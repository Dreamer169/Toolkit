"""
ProxyScrape 动态代理管理器
- 定期从 proxyscrape 拉新代理
- 测连通 + IP质量筛选
- 动态更新 Xray ps-* 端口 (10870-10899)
- 更新 /root/AirForce/core/turnstile.py RESIDENTIAL_PORTS
"""
import urllib.request, json, subprocess, concurrent.futures, time, random, sys

XRAY_JSON  = '/root/Toolkit/xray.json'
TURNSTILE  = '/root/AirForce/core/turnstile.py'
PS_BASE_PORT = 10870
PS_MAX_SLOTS = 30    # 10870-10899
PROXY_API  = 'https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=10000&country=all&simplified=true'

def fetch_proxies(limit=400):
    resp = urllib.request.urlopen(PROXY_API, timeout=15)
    all_p = [l.strip() for l in resp.read().decode().splitlines() if ':' in l.strip()]
    random.shuffle(all_p)
    return all_p[:limit]

def test_connectivity(proxy, timeout=8):
    host, port = proxy.rsplit(':', 1)
    try:
        r = subprocess.run(['curl','-s','--max-time',str(timeout),
                            '--socks5-hostname',f'{host}:{port}',
                            'https://api.ipify.org'],
                           capture_output=True,text=True,timeout=timeout+2)
        ip = r.stdout.strip()
        return ip if (ip and '.' in ip and len(ip)<20) else None
    except:
        return None

def check_ip_quality(ip):
    try:
        req = urllib.request.Request(
            f'http://ip-api.com/json/{ip}?fields=status,hosting,proxy,mobile,isp,country',
            headers={'User-Agent':'curl/7.88'})
        d = json.loads(urllib.request.urlopen(req,timeout=8).read())
        if d.get('mobile'): return 100
        if d.get('hosting'): return -15
        if d.get('proxy'):   return 20
        isp = d.get('isp','').lower()
        if any(k in isp for k in ['comcast','at&t','charter','verizon','cox','spectrum',
                                   'cable','broadband','telekom','telecom','cellcom','hkbn','pccw']): return 85
        return 50
    except:
        return 0

def find_best_proxies(n=PS_MAX_SLOTS):
    """拉代理→测连通→测IP质量，返回最多n个 (proxy_str, exit_ip, score)"""
    print(f'[PS-Manager] 拉取代理列表...', flush=True)
    proxies = fetch_proxies(400)
    print(f'[PS-Manager] 测试 {len(proxies)} 个代理连通性 (workers=50)...', flush=True)
    
    live = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as ex:
        futs = {ex.submit(test_connectivity, p): p for p in proxies}
        for f in concurrent.futures.as_completed(futs):
            p = futs[f]
            ip = f.result()
            if ip:
                live.append((p, ip))
    
    print(f'[PS-Manager] {len(live)} 个通, 检测IP质量...', flush=True)
    scored = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(check_ip_quality, ip): (proxy, ip) for proxy, ip in live}
        for f in concurrent.futures.as_completed(futs):
            proxy, ip = futs[f]
            score = f.result()
            scored.append((score, proxy, ip))
    
    scored.sort(reverse=True)
    best = [(p, ip, s) for s, p, ip in scored if s >= 0][:n]
    print(f'[PS-Manager] 选出 {len(best)} 个代理 (score>=0, max {n})', flush=True)
    for p, ip, s in best[:10]:
        print(f'  score={s:4d} {p} -> {ip}', flush=True)
    return best

def update_xray(best_proxies):
    """更新 xray.json: 移除旧 ps-* 端口, 插入新的"""
    with open(XRAY_JSON) as f:
        cfg = json.load(f)
    
    # 清除旧 ps-* 
    cfg['inbounds']  = [i for i in cfg['inbounds']  if not i.get('tag','').startswith('ps-')]
    cfg['outbounds'] = [o for o in cfg['outbounds'] if not o.get('tag','').startswith('ps-')]
    cfg['routing']['rules'] = [r for r in cfg['routing']['rules']
                                if not any(t.startswith('ps-') for t in (r.get('inboundTag') or []))]
    
    # 插入新的
    for n, (proxy, exit_ip, score) in enumerate(best_proxies):
        host, port = proxy.rsplit(':',1)
        lport   = PS_BASE_PORT + n
        in_tag  = f'ps-in-{n}'
        out_tag = f'ps-out-{n}'
        cfg['inbounds'].append({'tag':in_tag,'port':lport,'listen':'127.0.0.1',
                                 'protocol':'socks','settings':{'auth':'noauth','udp':False}})
        cfg['outbounds'].append({'tag':out_tag,'protocol':'socks',
                                  'settings':{'servers':[{'address':host,'port':int(port)}]}})
        cfg['routing']['rules'].insert(0,{'type':'field','inboundTag':[in_tag],'outboundTag':out_tag})
    
    with open(XRAY_JSON,'w') as f:
        json.dump(cfg, f, indent=2)
    print(f'[PS-Manager] xray.json 更新: {len(best_proxies)} 个 ps-* 端口', flush=True)

def verify_ports(best_proxies):
    """验证实际连通后的端口, 返回真正可用的本地端口列表"""
    ok_ports = []
    def check(item):
        n, (proxy, exit_ip, score) = item
        lport = PS_BASE_PORT + n
        try:
            r = subprocess.run(['curl','-s','--max-time','8',
                               '--socks5',f'127.0.0.1:{lport}','https://api.ipify.org'],
                              capture_output=True,text=True,timeout=10)
            actual_ip = r.stdout.strip()
            if actual_ip and '.' in actual_ip:
                return lport, actual_ip, score
        except:
            pass
        return None
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
        for result in ex.map(check, enumerate(best_proxies)):
            if result:
                ok_ports.append(result)
    
    print(f'[PS-Manager] 验证: {len(ok_ports)}/{len(best_proxies)} 端口实际可用', flush=True)
    return ok_ports

def update_turnstile(working_ports):
    """更新 turnstile.py RESIDENTIAL_PORTS 加入 ps-* 端口"""
    port_nums = [p for p,ip,s in working_ports]
    
    with open(TURNSTILE) as f:
        src = f.read()
    
    import re
    # 找到 RESIDENTIAL_PORTS 定义行
    pattern = r'(RESIDENTIAL_PORTS\s*=\s*\[)[^\]]*(\])'
    
    def replacer(m):
        # 读出现有端口
        existing = re.findall(r'\d+', m.group(0))
        # 去掉旧的 ps-* 范围端口 (10870-10899)
        keep = [int(x) for x in existing if not (10870 <= int(x) <= 10899)]
        # 合并新端口
        combined = sorted(set(keep + port_nums))
        return m.group(1) + ', '.join(str(x) for x in combined) + m.group(2)
    
    new_src = re.sub(pattern, replacer, src)
    with open(TURNSTILE,'w') as f:
        f.write(new_src)
    print(f'[PS-Manager] turnstile.py RESIDENTIAL_PORTS 更新: {port_nums}', flush=True)

def run_refresh():
    print(f'[PS-Manager] === 开始刷新 proxyscrape 代理池 ===', flush=True)
    best = find_best_proxies(PS_MAX_SLOTS)
    if not best:
        print('[PS-Manager] 没有找到可用代理!', flush=True)
        return []
    update_xray(best)
    # 重启 xray
    subprocess.run(['pm2','restart','xray'], capture_output=True)
    time.sleep(5)
    working = verify_ports(best)
    if working:
        update_turnstile(working)
        print(f'[PS-Manager] 完成! {len(working)} 个可用端口: {[p for p,ip,s in working]}', flush=True)
    return working

if __name__ == '__main__':
    working = run_refresh()
    print(f'\n最终可用端口: {[p for p,ip,s in working]}')
