#!/usr/bin/env python3
"""
replit_signup_v2.py — Replit 账号全自动注册 + 子节点接入 v2
============================================================
改进:
  • mail.tm 收件 (deltajohnsons.com，Replit 不封)
  • playwright-stealth 绕过 Cloudflare integrity check
  • xray SOCKS5 轮转端口 (10820-10845)，不同出口 IP
  • 完整身份档案（UA/IP/cookies/指纹）写入 accounts 表
  • integrity 失败自动重试（最多 MAX_RETRIES 次）
  • 支持 HTTP API 模式 (--serve) 供 api-server 调用

用法:
  python3 replit_signup_v2.py                   # 注册1个账号
  python3 replit_signup_v2.py --count 3          # 批量注册3个
  python3 replit_signup_v2.py --serve 7070       # HTTP 服务模式
"""
import argparse, asyncio, json, os, random, re, secrets, string
import subprocess, sys, time, urllib.request, urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

# ── 配置 ──────────────────────────────────────────────────────────────────────
GATEWAY_API      = os.environ.get("GATEWAY_API",    "http://localhost:8080/api/gateway")
VPS_GATEWAY_URL  = os.environ.get("VPS_GATEWAY_URL","http://45.205.27.69:8080/api/gateway")
TOOLKIT_FORK     = os.environ.get("TOOLKIT_FORK",   "Dreamer169/Toolkit")
SIGNUP_URL       = "https://replit.com/signup"
DB_URL           = os.environ.get("DATABASE_URL","postgresql://toolkit:toolkit@localhost/toolkit")

# xray SOCKS5 轮转池（对应 proxy-0 … proxy-25）
XRAY_SOCKS_PORTS = list(range(10820, 10846))   # 26 个出口
POLL_BRIDGE_PORTS = [1092, 1093, 1094, 1095]  # Replit GCP friend-node IPs
ALL_SOCKS_PORTS = POLL_BRIDGE_PORTS + XRAY_SOCKS_PORTS  # poll-bridge 优先
MAX_RETRIES      = 5   # integrity check 失败最多重试次数

# 常见 UA
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

FIRST = ["alex","blake","casey","dakota","eden","finley","gray","harper","jordan",
         "kennedy","lane","morgan","parker","quinn","reese","sage","taylor","winter",
         "james","liam","noah","emma","olivia","lucas","ethan","ava","mia","zoe"]
LAST  = ["smith","jones","chen","kim","lee","wang","patel","garcia","miller","davis",
         "taylor","wilson","moore","anderson","jackson","white","harris","clark"]

G="\033[92m"; R="\033[91m"; Y="\033[93m"; C="\033[96m"; B="\033[1m"; X="\033[0m"
def ok(m):  print(f"{G}✓{X} {m}", flush=True)
def er(m):  print(f"{R}✗{X} {m}", flush=True)
def inf(m): print(f"{C}…{X} {m}", flush=True)
def warn(m):print(f"{Y}!{X} {m}", flush=True)
def hdr(m): print(f"\n{B}{'─'*55}\n{m}\n{'─'*55}{X}", flush=True)

# ── 身份生成 ──────────────────────────────────────────────────────────────────
def gen_identity(mailtm_domain="deltajohnsons.com"):
    first = random.choice(FIRST)
    last  = random.choice(LAST)
    num   = random.randint(10, 9999)
    login = f"{first}{last[:4]}{num}".lower()[:20]
    uname = f"{first.capitalize()}{last[:3].capitalize()}{num}"[:20]
    pw    = secrets.token_hex(4).upper()[:3] + secrets.token_hex(3) + "!A1"
    return {
        "username": uname,
        "login":    login,
        "email":    f"{login}@{mailtm_domain}",
        "mailtm_domain": mailtm_domain,
        "password": pw,
        "first_name": first.capitalize(),
        "last_name":  last.capitalize(),
    }

# ── mail.tm ───────────────────────────────────────────────────────────────────
def _mreq(method, path, data=None, token=None, timeout=20):
    url = "https://api.mail.tm" + path
    body = json.dumps(data).encode() if data else None
    h = {"Content-Type":"application/json","Accept":"application/json"}
    if token: h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:    return e.code, json.loads(e.read())
        except: return e.code, {}
    except Exception as exc:
        return 0, {"error": str(exc)}

def mailtm_create(email, password):
    code, body = _mreq("POST","/accounts",{"address":email,"password":password})
    if code not in (200,201):
        raise RuntimeError(f"mail.tm 建号失败 {code}: {body}")
    return body.get("id","")

def mailtm_token(email, password):
    code, body = _mreq("POST","/token",{"address":email,"password":password})
    if code != 200:
        raise RuntimeError(f"mail.tm token 失败 {code}: {body}")
    return body["token"]

def mailtm_poll(token, timeout=220, kws=("verify","confirm","replit","activate","email")):
    deadline = time.time() + timeout
    inf(f"等待 mail.tm 验证邮件 (最多 {timeout}s)…")
    while time.time() < deadline:
        code, body = _mreq("GET","/messages",token=token)
        if code == 200:
            # mail.tm 返回 hydra:Collection dict 或直接 list
            members = (body.get("hydra:member",[]) if isinstance(body, dict)
                       else body if isinstance(body, list) else [])
            for msg in members:
                if not isinstance(msg, dict): continue
                s = msg.get("subject","").lower()
                i = msg.get("intro","").lower()
                if any(k in s or k in i for k in kws):
                    c2, full = _mreq("GET",f"/messages/{msg['id']}",token=token)
                    if c2 == 200 and isinstance(full, dict):
                        ok(f"收到邮件: {msg.get('subject','')[:55]}")
                        return full.get("html") or full.get("text","")
        time.sleep(7)
    return None

def extract_verify_url(html):
    candidates = re.findall(r'https?://[^\s"\'<>]+', html or "")
    kws = ("verify","confirm","activate","email-action","auth","replit.com")
    for u in candidates:
        if any(k in u.lower() for k in kws):
            return u
    return None

# ── HTTP 工具 ─────────────────────────────────────────────────────────────────
def http_get(url, timeout=12):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:    return e.code, json.loads(e.read())
        except: return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}

def http_post(url, data, timeout=15, headers=None):
    body = json.dumps(data).encode()
    h = {"Content-Type":"application/json","Accept":"application/json",**(headers or {})}
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:    return e.code, json.loads(e.read())
        except: return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}

# ── 数据库 ────────────────────────────────────────────────────────────────────
def db_save(identity, status="registered", node_id="", extra_json="{}"):
    q = lambda v: "'" + str(v).replace("'","''") + "'"
    sql = f"""
        INSERT INTO accounts
          (platform,email,password,username,status,notes,tags,token)
        VALUES (
          'replit',{q(identity['email'])},{q(identity['password'])},
          {q(identity['username'])},{q(status)},{q(extra_json)},
          'replit,subnode',{q(node_id)}
        )
        ON CONFLICT (email) DO UPDATE
          SET status=EXCLUDED.status, notes=EXCLUDED.notes,
              token=EXCLUDED.token, updated_at=now();
    """
    try:
        r = subprocess.run(["psql",DB_URL,"-t","-A","-c",sql],
                           capture_output=True, text=True, timeout=8)
        return r.returncode == 0
    except Exception as e:
        warn(f"DB 写入失败: {e}")
        return False

# ── Playwright 注册核心 ───────────────────────────────────────────────────────
async def _do_signup(identity, ua, proxy_port, mailtm_tok, headless=True):
    """
    实际执行一次注册尝试。
    返回 dict: {"ok":bool, "phase":str, "error":str, "cookies":list, "exit_ip":str}
    """
    try:
        from playwright.async_api import async_playwright
        from playwright_stealth import Stealth
    except ImportError:
        return {"ok":False,"phase":"import","error":"playwright_stealth 未安装"}

    result = {"ok":False,"phase":"init","error":"","cookies":[],"exit_ip":""}
    proxy_cfg = {"server": f"socks5://127.0.0.1:{proxy_port}"}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            proxy=proxy_cfg,
            args=["--no-sandbox","--disable-dev-shm-usage",
                  "--disable-gpu","--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            viewport={"width":1280,"height":800},
            locale="en-US",
            user_agent=ua,
            java_script_enabled=True,
        )
        page = await ctx.new_page()
        await Stealth().apply_stealth_async(page)

        try:
            # 0. 获取出口 IP
            result["phase"] = "get_exit_ip"
            try:
                await page.goto("https://api.ipify.org?format=json",
                                wait_until="domcontentloaded", timeout=60000)
                ip_text = await page.locator("body").inner_text()
                result["exit_ip"] = json.loads(ip_text).get("ip","")
                inf(f"  出口 IP: {result['exit_ip']}")
            except:
                pass

            # 1. 打开 signup
            result["phase"] = "navigate"
            inf("  打开 replit.com/signup …")
            await page.goto(SIGNUP_URL, wait_until="load", timeout=65000)
            await page.wait_for_timeout(2500)

            # 检查 integrity 错误
            body_text = await page.locator("body").inner_text()
            if "failed to evaluate" in body_text.lower() or "browser integrity" in body_text.lower():
                result["error"] = "integrity_check_failed"
                await browser.close()
                return result

            # 2. 点 "Email & password" 按钮
            result["phase"] = "click_email_btn"
            for sel in ['button:has-text("Email")', 'button:has-text("Continue with email")',
                        '[data-cy="email-signup"]', 'a:has-text("Email")']:
                btn = page.locator(sel)
                if await btn.count() > 0:
                    await btn.first.click()
                    await page.wait_for_timeout(1500)
                    break

            # 3. 填写表单
            result["phase"] = "fill_form"
            inf(f"  填表: {identity['username']} / {identity['email']}")

            for sel in ['input[name="username"]','input[placeholder*="username" i]','input[id*="username" i]']:
                f = page.locator(sel)
                if await f.count():
                    await f.first.click(); await page.wait_for_timeout(300)
                    await f.first.type(identity["username"], delay=75)
                    break

            await page.wait_for_timeout(600)

            for sel in ['input[type="email"]','input[name="email"]','input[placeholder*="email" i]']:
                f = page.locator(sel)
                if await f.count():
                    await f.first.click(); await page.wait_for_timeout(250)
                    await f.first.type(identity["email"], delay=65)
                    break

            await page.wait_for_timeout(500)

            for sel in ['input[type="password"]','input[name="password"]']:
                f = page.locator(sel)
                if await f.count():
                    await f.first.click(); await page.wait_for_timeout(250)
                    await f.first.type(identity["password"], delay=55)
                    break

            await page.wait_for_timeout(1200)
            await page.screenshot(path=f"/tmp/signup_{identity['username']}_form.png")

            # 4. 提交
            result["phase"] = "submit"
            for sel in ['button[type="submit"]','button:has-text("Create Account")',
                        'button:has-text("Sign up")','button:has-text("Continue")']:
                btn = page.locator(sel)
                if await btn.count():
                    await btn.first.click(); break
            else:
                await page.keyboard.press("Enter")

            await page.wait_for_timeout(6000)
            cur = page.url
            inf(f"  提交后 URL: {cur[:70]}")

            # integrity 再次检查
            body_text2 = await page.locator("body").inner_text()
            if "failed to evaluate" in body_text2.lower():
                result["error"] = "integrity_check_failed_after_submit"
                await browser.close()
                return result

            await page.screenshot(path=f"/tmp/signup_{identity['username']}_after.png")

            # 5. 等待邮件验证
            result["phase"] = "email_verify"
            html = mailtm_poll(mailtm_tok, timeout=220)
            if not html:
                warn("  未收到验证邮件，尝试继续…")
            else:
                vurl = extract_verify_url(html)
                if vurl:
                    inf(f"  验证链接: {vurl[:70]}…")
                    try:
                        await page.goto(vurl, wait_until="load", timeout=30000)
                        await page.wait_for_timeout(5000)
                        ok(f"  邮件验证完成 → {page.url[:60]}")
                    except Exception as ve:
                        warn(f"  验证链接访问失败: {ve}")
                else:
                    warn("  无法提取验证链接")

            # 6. 收集 cookies（含 session token）
            result["phase"] = "collect_cookies"
            cookies = await ctx.cookies("https://replit.com")
            result["cookies"] = cookies
            session_tok = next((c["value"] for c in cookies
                                if "session" in c["name"].lower() or "auth" in c["name"].lower()), "")
            inf(f"  Session cookie: {'已获取' if session_tok else '未找到'}")

            # 7. 导入 Fork（通过 Replit GraphQL API）
            result["phase"] = "import_fork"
            node_id = await _import_fork(page, ctx, identity, cookies)
            result["node_id"] = node_id or ""

            result["ok"]    = True
            result["phase"] = "done"

        except Exception as exc:
            er(f"  浏览器异常: {exc}")
            result["error"] = str(exc)
            try:
                await page.screenshot(path=f"/tmp/signup_{identity['username']}_error.png")
            except: pass

        await browser.close()
    return result


async def _import_fork(page, ctx, identity, cookies):
    """
    通过 GraphQL 克隆 Toolkit fork 到新账号，设置 SELF_REGISTER_URL secret。
    返回 repl_id 或 None。
    """
    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    headers    = {"Cookie": cookie_str, "Content-Type":"application/json",
                  "X-Requested-With":"XMLHttpRequest"}

    # 查用户 ID
    gql_me = '{"query":"{ currentUser { id username } }"}'
    try:
        req = urllib.request.Request("https://replit.com/graphql",
                                     data=gql_me.encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read())
            uid = d.get("data",{}).get("currentUser",{}).get("id","")
            inf(f"  已登录 Replit 用户 ID: {uid}")
    except Exception as e:
        warn(f"  获取用户 ID 失败: {e}")
        uid = ""

    # 克隆 fork (createRepl mutation)
    gql_clone = json.dumps({
        "query": """mutation cloneFork($url: String!) {
            createRepl(input: {cloneUrl: $url, isPrivate: true}) {
                ... on Repl { id url }
                ... on UserError { message }
            }
        }""",
        "variables": {"url": f"https://github.com/{TOOLKIT_FORK}"}
    })
    repl_id, repl_url = "", ""
    try:
        req = urllib.request.Request("https://replit.com/graphql",
                                     data=gql_clone.encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read())
            repl = d.get("data",{}).get("createRepl",{})
            repl_id  = repl.get("id","")
            repl_url = repl.get("url","")
            if repl_id:
                ok(f"  Fork 克隆成功: {repl_url}")
            else:
                warn(f"  Fork 克隆失败: {repl}")
    except Exception as e:
        warn(f"  Fork 克隆异常: {e}")

    if not repl_id:
        return None

    # 设置 SELF_REGISTER_URL secret
    self_reg_url = f"{VPS_GATEWAY_URL}/nodes/register"
    gql_secret = json.dumps({
        "query": """mutation addSecret($replId: String!, $key: String!, $value: String!) {
            addSecret(input: {replId: $replId, key: $key, value: $value}) {
                ... on UserError { message }
            }
        }""",
        "variables": {"replId": repl_id,
                       "key": "SELF_REGISTER_URL",
                       "value": self_reg_url}
    })
    try:
        req = urllib.request.Request("https://replit.com/graphql",
                                     data=gql_secret.encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read())
            ok(f"  SELF_REGISTER_URL secret 已设置")
    except Exception as e:
        warn(f"  设置 secret 失败: {e}")

    return repl_id


# ── 主注册流程（含重试）────────────────────────────────────────────────────────
async def signup_one(identity, headless=True):
    hdr(f"账号: {identity['username']}  <{identity['email']}>")

    # 建 mail.tm 邮箱
    inf("建立 mail.tm 收件箱…")
    try:
        mailtm_create(identity["email"], identity["password"])
        mailtm_tok = mailtm_token(identity["email"], identity["password"])
        ok(f"mail.tm 就绪: {identity['email']}")
    except Exception as e:
        er(f"mail.tm 初始化失败: {e}")
        return {"email": identity["email"], "ok": False, "error": str(e)}

    ua = random.choice(USER_AGENTS)
    proxy_ports = random.sample(ALL_SOCKS_PORTS, min(MAX_RETRIES, len(ALL_SOCKS_PORTS)))

    for attempt, port in enumerate(proxy_ports, 1):
        inf(f"  第 {attempt}/{MAX_RETRIES} 次尝试 (SOCKS:{port}, UA:{ua[30:60]}…)")
        res = await _do_signup(identity, ua, port, mailtm_tok, headless=headless)

        if any(x in res.get("error","") for x in
               ("integrity_check_failed","Timeout","timeout","TimeoutError","net::ERR")):
            warn(f"  可重试错误 ({res.get('error','')[:60]})，换代理重试 ({attempt}/{MAX_RETRIES})…")
            await asyncio.sleep(random.uniform(4,10))
            ua = random.choice(USER_AGENTS)   # 也换 UA
            continue

        if res.get("ok"):
            ok(f"注册成功！节点: {res.get('node_id','')}")
            extra = json.dumps({
                "exit_ip": res.get("exit_ip",""),
                "ua": ua,
                "proxy_port": port,
                "node_id": res.get("node_id",""),
            })
            db_save(identity, status="registered",
                    node_id=res.get("node_id",""), extra_json=extra)

            # 等待子节点自注册（30s）
            if res.get("node_id"):
                inf("等待子节点自注册 (30s)…")
                await asyncio.sleep(30)
                s, b = http_get(f"{GATEWAY_API}/nodes")
                for n in b.get("nodes",[]):
                    if identity["username"].lower() in n.get("name","").lower():
                        ok(f"子节点已上线: {n['name']} [{n['status']}]")
            return res

        er(f"  注册失败: {res.get('error','未知')}")
        break

    # 所有重试失败
    db_save(identity, status="failed")
    return {"email": identity["email"], "ok": False, "error": "所有重试失败"}


# ── HTTP 服务模式 ─────────────────────────────────────────────────────────────
class SignupHandler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        if self.path != "/signup":
            self.send_response(404); self.end_headers(); return
        length = int(self.headers.get("Content-Length",0))
        body   = json.loads(self.rfile.read(length) or b"{}") if length else {}
        count  = int(body.get("count",1))
        results = []
        for _ in range(count):
            ident = gen_identity()
            res   = asyncio.run(signup_one(ident))
            results.append(res)
        resp = json.dumps({"results": results}).encode()
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.end_headers()
        self.wfile.write(resp)

    def do_GET(self):
        if self.path == "/health":
            resp = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type","application/json")
            self.end_headers()
            self.wfile.write(resp)
        else:
            self.send_response(404); self.end_headers()


# ── CLI ───────────────────────────────────────────────────────────────────────
async def main_async(args):
    for i in range(args.count):
        ident = gen_identity()
        print(f"\n{'='*60}\n账号 {i+1}/{args.count}\n{'='*60}")
        result = await signup_one(ident, headless=args.headless)
        print(f"结果: {'成功' if result.get('ok') else '失败'}")
        if args.count > 1 and i < args.count - 1:
            delay = random.uniform(8, 20)
            inf(f"等待 {delay:.0f}s 后继续…")
            await asyncio.sleep(delay)

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--count",    type=int, default=1,     help="注册账号数量")
    p.add_argument("--no-headless", dest="headless", action="store_false",
                   default=True, help="显示浏览器窗口")
    p.add_argument("--serve",    type=int, default=0,     help="HTTP 服务端口 (0=禁用)")
    args = p.parse_args()

    if args.serve:
        print(f"[signup-server] 监听 0.0.0.0:{args.serve}")
        HTTPServer(("0.0.0.0", args.serve), SignupHandler).serve_forever()
    else:
        asyncio.run(main_async(args))
