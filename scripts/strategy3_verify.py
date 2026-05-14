#!/usr/bin/env python3
"""
strategy3_verify.py v1.0 — 策略3完整链路验证
================================================================
目标：验证"用同一个 IP 完成 Chrome 注册 + ref_code 创建"是否可行。

背景（截至 2026-05-14）：
  - Webshare 9 个 IP 全部 ip-already-existed（已耗尽）
  - RESI 端口 10851-10859 全部 ip-already-existed
  - proxyscrape SOCKS5 能创建 ref_code（curl 已实测）
  - 但 Chrome 能否通过远程 SOCKS5 完成注册 → 从未实测，本脚本验证

测试流程：
  Phase 0: 从 proxyscrape 拉 60 个新鲜 SOCKS5 代理
  Phase 1: 双重筛选 —— HTTPS 可达 AND unitool 未记录
  Phase 2: 用筛选出的代理通过 Chrome 注册测试账号
  Phase 3: 用同一代理立刻调 POST /api/ref-codes
  Phase 4: 输出明确结论

结论判定：
  ✅ 策略3可行  = Phase2 注册成功 AND Phase3 ref_code 创建成功
  ⚠️ 部分可行  = Phase2 成功 但 Phase3 ip-already-existed（注册消耗了IP额度）
  ❌ Chrome无法注册 = Phase2 注册失败（SOCKS5无法用于Chrome注册）
  ❌ 代理耗尽  = Phase1 无代理通过双重筛选
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from typing import Optional

sys.path.insert(0, "/data/Toolkit/scripts")

LOG_FILE = "/tmp/strategy3_verify.log"

def log(msg: str):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def run_cmd(cmd: list, timeout: int = 20) -> tuple[str, str, int]:
    """运行命令，返回 (stdout, stderr, returncode)"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", "TIMEOUT", -1
    except Exception as e:
        return "", str(e), -1

# ─────────────────────────────────────────────
# DB：取一个未注册的测试账号
# ─────────────────────────────────────────────
def get_test_account() -> Optional[dict]:
    """从 DB 取一个未注册且有密码的测试账号"""
    import psycopg2
    try:
        conn = psycopg2.connect("postgresql://postgres:postgres@localhost/toolkit")
        cur = conn.cursor()
        cur.execute("""
            SELECT id, email, notes FROM accounts
            WHERE tags::text LIKE '%unitool%'
              AND tags::text NOT LIKE '%unitool_registered%'
              AND notes::text LIKE '%password%'
              AND tags::text NOT LIKE '%unitool_fail%'
            ORDER BY RANDOM()
            LIMIT 1
        """)
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row:
            return None
        account_id, email, notes = row
        # 从 notes 提取密码
        pwd_m = re.search(r"password=([^\n\s,]+)", notes or "")
        password = pwd_m.group(1) if pwd_m else ""
        return {"id": account_id, "email": email, "password": password}
    except Exception as e:
        log(f"[DB] 取账号失败: {e}")
        return None

# ─────────────────────────────────────────────
# Phase 0：从 proxyscrape 拉新鲜代理
# ─────────────────────────────────────────────
def fetch_fresh_proxies(n: int = 80) -> list[str]:
    """从 proxyscrape 拉 SOCKS5 代理列表"""
    urls = [
        "https://api.proxyscrape.com/v4/free-proxy-list/get"
        "?request=display_proxies&protocol=socks5"
        "&proxy_format=protocolipport&format=text&timeout=1000"
        "&country=us,gb,de,fr,nl,ca",
        "https://api.proxyscrape.com/v4/free-proxy-list/get"
        "?request=display_proxies&protocol=socks5"
        "&proxy_format=protocolipport&format=text&timeout=1500",
    ]
    proxies = []
    for url in urls:
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                text = resp.read().decode("utf-8", errors="ignore")
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("socks5://") and line not in proxies:
                    proxies.append(line)
            if len(proxies) >= n:
                break
        except Exception as e:
            log(f"[fetch] proxyscrape 拉取失败: {e}")
    log(f"[Phase0] 拉取到 {len(proxies)} 个 SOCKS5 代理")
    return proxies[:n]

# ─────────────────────────────────────────────
# Phase 1：双重筛选
# ─────────────────────────────────────────────
def check_https_capable(proxy_url: str) -> tuple[bool, str]:
    """
    验证代理是否支持 HTTPS（curl --socks5-hostname → https://api.ipify.org）
    返回: (ok, exit_ip)
    """
    # 解析 socks5://ip:port
    m = re.match(r"socks5://([^:]+):(\d+)", proxy_url)
    if not m:
        return False, ""
    ip, port = m.group(1), m.group(2)
    out, _, rc = run_cmd([
        "curl", "-s", "--max-time", "10",
        "--socks5-hostname", f"{ip}:{port}",
        "https://api.ipify.org"
    ], timeout=15)
    if rc == 0 and re.match(r"^\d+\.\d+\.\d+\.\d+$", out):
        return True, out
    return False, ""

def check_unitool_fresh(proxy_url: str, test_ssid: str) -> tuple[bool, str]:
    """
    验证该 IP 是否被 unitool 记录（POST /api/ref-codes）
    返回: (is_fresh, raw_response)
    新鲜 = 不是 ip-already-existed（可能返回 code 或其他错误）
    """
    m = re.match(r"socks5://([^:]+):(\d+)", proxy_url)
    if not m:
        return False, "parse_fail"
    ip, port = m.group(1), m.group(2)
    out, _, rc = run_cmd([
        "curl", "-s", "--max-time", "12",
        "--socks5-hostname", f"{ip}:{port}",
        "-b", f"__Secure-unitool-ssid={test_ssid}",
        "-X", "POST",
        "-H", "Content-Type: application/json",
        "-H", "Accept: application/json",
        "https://unitool.ai/api/ref-codes"
    ], timeout=18)
    if not out:
        return False, "empty/timeout"
    if "ip-already-existed" in out:
        return False, "ip-already-existed"
    # 任何其他响应（含 code 或 auth 错误）= IP 未被记录
    return True, out

def phase1_find_fresh_proxy(proxies: list[str], test_ssid: str, max_test: int = 40) -> Optional[dict]:
    """
    双重筛选：HTTPS 可达 + unitool 未记录
    返回第一个通过的代理信息
    """
    log(f"[Phase1] 开始双重筛选，测试上限 {min(len(proxies), max_test)} 个")
    https_ok = 0
    fresh_ok = 0

    for i, proxy in enumerate(proxies[:max_test]):
        m = re.match(r"socks5://([^:]+):(\d+)", proxy)
        if not m:
            continue
        ip, port = m.group(1), m.group(2)

        # Step A: HTTPS 可达
        ok_https, exit_ip = check_https_capable(proxy)
        if not ok_https:
            log(f"[Phase1] [{i+1:02d}] {ip}:{port} → HTTPS ❌")
            continue
        https_ok += 1
        log(f"[Phase1] [{i+1:02d}] {ip}:{port} → HTTPS ✅ exit_ip={exit_ip}")

        # Step B: unitool 未记录
        is_fresh, raw = check_unitool_fresh(proxy, test_ssid)
        if not is_fresh:
            log(f"[Phase1] [{i+1:02d}] {ip}:{port} → unitool {raw} ❌")
            continue
        fresh_ok += 1
        log(f"[Phase1] [{i+1:02d}] {ip}:{port} → unitool ✅ (raw={raw[:60]})")
        log(f"[Phase1] 找到可用代理！proxy={proxy} exit_ip={exit_ip}")
        return {"proxy": proxy, "ip": ip, "port": int(port), "exit_ip": exit_ip}

    log(f"[Phase1] 筛选完成：HTTPS可达 {https_ok} 个，unitool新鲜 {fresh_ok} 个，无通过")
    return None

# ─────────────────────────────────────────────
# Phase 2：Chrome 注册（远程 SOCKS5）
# ─────────────────────────────────────────────
async def phase2_chrome_register(
    email: str, password: str, proxy_ip: str, proxy_port: int,
    ref_code: str = "xjfjk"
) -> dict:
    """
    用远程 SOCKS5 代理启动 Chrome，注册 unitool 账号。
    关键：--proxy-server=socks5://REMOTE_IP:PORT（不走本地 xray 端口）
    """
    try:
        from pydoll.browser import Chrome
        from pydoll.browser.options import ChromiumOptions
    except ImportError:
        return {"ok": False, "error": "pydoll_not_installed"}

    CHROME = None
    for _p in [
        "/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
        "/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome",
        "/data/cache/ms-playwright/chromium-1169/chrome-linux64/chrome",
    ]:
        if os.path.exists(_p):
            CHROME = _p
            break

    # 找空闲 CDP 端口
    import socket
    def _free_port():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    # SIGNUP_NA（如果变更用 --probe 更新）
    SIGNUP_NA = "602b5c42ffedec9865ca902b033d188b22c575dfd5"

    os.environ.setdefault("DISPLAY", ":99")
    opt = ChromiumOptions()
    if CHROME:
        opt.binary_location = CHROME

    # 关键：直接指向远程 SOCKS5（不是本地 127.0.0.1）
    proxy_str = f"socks5://{proxy_ip}:{proxy_port}"
    for arg in [
        "--no-sandbox", "--disable-dev-shm-usage",
        "--window-size=1440,900", "--disable-gpu", "--lang=en-US",
        "--disable-blink-features=AutomationControlled",
        f"--proxy-server={proxy_str}",
    ]:
        opt.add_argument(arg)

    log(f"[Phase2] Chrome 启动 proxy={proxy_str} email={email}")
    result = {"ok": False, "email": email, "proxy": proxy_str, "raw": "", "ssid": ""}
    t0 = time.time()

    try:
        cdp_port = _free_port()
        from pydoll.browser.chromium import ChromiumBrowser
        opt.add_argument(f"--remote-debugging-port={cdp_port}")

        async with Chrome(options=opt, connection_port=cdp_port) as browser:
            tab = await browser.start()

            # fingerprint
            FINGERPRINT_JS = """(function(){
              try{Object.defineProperty(navigator,'webdriver',{get:()=>undefined,configurable:true});}catch(e){}
              try{Object.defineProperty(navigator,'languages',{get:()=>['en-US','en'],configurable:true});}catch(e){}
              try{if(!window.chrome){Object.defineProperty(window,'chrome',{value:{runtime:{}},configurable:true,writable:true});}}catch(e){}
            })();"""
            await tab.execute_script(FINGERPRINT_JS)

            # 访问 ref 页（带 ref_code cookie）
            log(f"[Phase2] 访问 /ref/{ref_code}")
            await tab.go_to(f"https://unitool.ai/ref/{ref_code}", timeout=30)
            await asyncio.sleep(2)

            # 访问注册页并 bypass Turnstile
            log("[Phase2] 访问 /en/entry")
            await tab.go_to("https://unitool.ai/en/entry", timeout=30)

            log("[Phase2] bypass_cloudflare...")
            from pydoll.browser.mixins import FindElementsMixin
            token = await browser.bypass_cloudflare(tab)
            token = token or ""
            log(f"[Phase2] Turnstile token len={len(token)}")

            if not token or len(token) < 100:
                result["error"] = f"turnstile_failed token_len={len(token)}"
                log(f"[Phase2] ❌ Turnstile 失败")
                return result

            # JS fetch() 提交注册
            log("[Phase2] JS fetch() POST 注册...")
            js_register = f"""
(async () => {{
  const fd = new FormData();
  fd.append('1_email', '{email}');
  fd.append('1_password', '{password}');
  fd.append('1_action', 'register');
  fd.append('0', '{{"emailAddress":"{email}","password":"{password}","action":"register"}}');

  const r = await fetch('/en/entry', {{
    method: 'POST',
    headers: {{
      'Next-Action': '{SIGNUP_NA}',
      'x-cf-turnstile-token': '{token}',
    }},
    body: fd,
    credentials: 'include',
  }});
  const text = await r.text();
  return text.substring(0, 1000);
}})()
"""
            raw = await tab.execute_script(js_register)
            result["raw"] = str(raw)[:500]
            log(f"[Phase2] 响应: {result['raw'][:200]}")

            # 判断注册结果
            if "email_sent" in str(raw) or "verify" in str(raw).lower():
                result["ok"] = True
                log("[Phase2] ✅ 注册成功（email_sent）")

                # 提取 ssid
                cookies = await tab.get_cookies()
                for c in (cookies or []):
                    if "unitool-ssid" in c.get("name", ""):
                        result["ssid"] = c["value"]
                        log(f"[Phase2] ssid 长度={len(result['ssid'])}")
                        break
            elif "already" in str(raw).lower() or "exist" in str(raw).lower():
                result["error"] = "already_registered"
                log("[Phase2] ❌ 账号已注册")
            else:
                result["error"] = f"unknown_response"
                log(f"[Phase2] ❌ 未知响应")

    except Exception as e:
        result["error"] = str(e)
        log(f"[Phase2] ❌ 异常: {e}")

    elapsed = time.time() - t0
    log(f"[Phase2] 完成 {elapsed:.1f}s ok={result['ok']}")
    return result

# ─────────────────────────────────────────────
# Phase 3：同 IP 创建 ref_code
# ─────────────────────────────────────────────
def phase3_create_ref_code(proxy_ip: str, proxy_port: int, ssid: str) -> dict:
    """
    用与注册完全相同的 SOCKS5 代理，立刻调 POST /api/ref-codes
    """
    log(f"[Phase3] 创建 ref_code via socks5://{proxy_ip}:{proxy_port}")
    out, _, rc = run_cmd([
        "curl", "-s", "--max-time", "15",
        "--socks5-hostname", f"{proxy_ip}:{proxy_port}",
        "-b", f"__Secure-unitool-ssid={ssid}",
        "-X", "POST",
        "-H", "Content-Type: application/json",
        "-H", "Accept: application/json",
        "https://unitool.ai/api/ref-codes"
    ], timeout=20)

    log(f"[Phase3] 响应: {out[:200] if out else '(空)'}")

    if not out:
        return {"ok": False, "error": "empty/timeout", "raw": ""}
    try:
        d = json.loads(out)
    except Exception:
        return {"ok": False, "error": "json_parse_fail", "raw": out[:200]}

    if "code" in d:
        log(f"[Phase3] ✅ ref_code 创建成功: {d['code']}")
        return {"ok": True, "code": d["code"], "raw": out}
    else:
        err = d.get("error", "unknown")
        log(f"[Phase3] ❌ 失败: {err}")
        return {"ok": False, "error": err, "raw": out}

# ─────────────────────────────────────────────
# Phase 4：结论输出
# ─────────────────────────────────────────────
def phase4_verdict(p1_proxy, p2_reg, p3_ref):
    print("\n" + "="*60)
    print("  策略3验证结论")
    print("="*60)

    if p1_proxy is None:
        print("❌ Phase1 失败：未找到双重筛选通过的代理")
        print("   原因：proxyscrape SOCKS5 代理全部 HTTPS 不可达 或 已被 unitool 记录")
        print("   推论：无法继续验证，结论未知")
        print("="*60)
        return

    print(f"✅ Phase1 通过：{p1_proxy['proxy']}  出口IP={p1_proxy['exit_ip']}")

    if p2_reg is None or not p2_reg.get("ok"):
        err = (p2_reg or {}).get("error", "未执行")
        print(f"❌ Phase2 失败：Chrome 注册未成功 ({err})")
        print()
        if "turnstile" in err.lower():
            print("📌 结论：Turnstile bypass 失败（CF 对该 IP 触发了更严格校验）")
        elif "proxy" in err.lower() or "socks" in err.lower():
            print("📌 结论：Chrome 无法通过远程 SOCKS5 建立 HTTPS 连接")
            print("   → 策略3不可行（Chrome+proxyscrape SOCKS5 技术障碍）")
        else:
            print(f"📌 结论：Chrome 注册失败，原因待查（{err}）")
        print("="*60)
        return

    print(f"✅ Phase2 通过：账号注册成功  ssid_len={len(p2_reg.get('ssid',''))}")

    if p3_ref is None or not p3_ref.get("ok"):
        err = (p3_ref or {}).get("error", "未执行")
        print(f"❌ Phase3 失败：ref_code 创建失败 ({err})")
        print()
        if "ip-already-existed" in err:
            print("📌 结论：⚠️ 注册动作本身消耗了该 IP 的 unitool 额度")
            print("   → 策略3部分可行：注册和ref_code创建不能用同一IP")
            print("   → 正确做法：注册用IP-A，ref_code用未注册过的IP-B")
        else:
            print(f"📌 结论：ref_code 创建失败（{err}），原因待查")
        print("="*60)
        return

    print(f"✅ Phase3 通过：ref_code 创建成功  code={p3_ref.get('code')}")
    print()
    print("🎉 结论：策略3完全可行！")
    print("   同一个 proxyscrape SOCKS5 IP 可以：")
    print("   1. 通过 Chrome --proxy-server=socks5://ip:port 完成注册")
    print("   2. 立刻用同一 IP 通过 curl 创建 ref_code")
    print("   → 建议：批量启用此策略，每次注册前先确认 IP 未被记录")
    print("="*60)

# ─────────────────────────────────────────────
# main
# ─────────────────────────────────────────────
async def main():
    log("=" * 60)
    log("strategy3_verify.py v1.0 启动")
    log("=" * 60)

    # 获取测试账号
    account = get_test_account()
    if not account:
        log("❌ 无法取到测试账号，退出")
        sys.exit(1)
    log(f"[准备] 测试账号: {account['email']} (id={account['id']})")

    # 取一个有效 ssid 用于 Phase1 探测
    test_ssid = ""
    ssid_files = os.listdir("/data/unitool_ssids")
    if ssid_files:
        with open(f"/data/unitool_ssids/{ssid_files[0]}") as f:
            test_ssid = f.read().strip()
    if not test_ssid:
        log("❌ 无法取到 ssid 用于 Phase1 探测，退出")
        sys.exit(1)
    log(f"[准备] 探测用 ssid 长度={len(test_ssid)}")

    # Phase 0
    proxies = fetch_fresh_proxies(n=80)
    if not proxies:
        log("❌ 无法获取代理列表，退出")
        sys.exit(1)

    # Phase 1
    chosen = phase1_find_fresh_proxy(proxies, test_ssid, max_test=50)

    if not chosen:
        phase4_verdict(None, None, None)
        sys.exit(2)

    # Phase 2
    reg_result = await phase2_chrome_register(
        email=account["email"],
        password=account["password"],
        proxy_ip=chosen["ip"],
        proxy_port=chosen["port"],
        ref_code="xjfjk"
    )

    # Phase 3（只在注册成功且有 ssid 时执行）
    ref_result = None
    if reg_result.get("ok") and reg_result.get("ssid"):
        # 注意：立刻用同一 IP 创建，不切换代理
        ref_result = phase3_create_ref_code(
            proxy_ip=chosen["ip"],
            proxy_port=chosen["port"],
            ssid=reg_result["ssid"]
        )
    elif reg_result.get("ok"):
        log("[Phase3] 注册成功但未获取 ssid，跳过 ref_code 创建")

    # Phase 4
    phase4_verdict(chosen, reg_result, ref_result)

    log(f"[完成] 日志保存在 {LOG_FILE}")

if __name__ == "__main__":
    asyncio.run(main())
