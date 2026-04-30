#!/usr/bin/env python3
"""
obvious_provision.py — end-to-end obvious.ai account provisioner
================================================================

注册一个新的 obvious.ai 账号、跑完 onboarding、捕获 storage_state 和 thread/project
ID，写入磁盘的 manifest 文件，给 obvious_client.py 直接复用。

⚠️ IP 隔离：obvious 用 better-auth + 风控，**每次新注册必须换出口 IP**，
否则容易被关联封号。本脚本要求 --proxy 参数（socks5/http），建议把请求经
Toolkit xray 的 sub-node socks 端口（10820–10825…）轮换，每个端口对应一条
不同的 vmess/vless 出站。

依赖
----
* mailtm_client.py（同目录）— 临时邮箱
* playwright python（pip install playwright; playwright install chromium）

使用
----
  # 用 sub-node #1 的 socks 出口注册
  python3 obvious_provision.py --proxy socks5://127.0.0.1:10820 \\
      --out-dir /root/obvious-accounts \\
      --label sub-node-1

  # 手动指定邮箱（绕开 mailtm 被屏蔽的情况）
  python3 obvious_provision.py --proxy socks5://127.0.0.1:10821 \\
      --email me+x@outlook.com --password 'somePass!' \\
      --out-dir /root/obvious-accounts

输出（每个账号一个目录）
------------------------
  <out-dir>/<label-or-email-slug>/
    ├── manifest.json     # email/pwd/userId/workspaceId/projectId/threadId/proxy/...
    ├── storage_state.json # Playwright cookie jar，给 obvious_client.py 用
    └── shots/             # 注册过程截图（出问题排错用）
  <out-dir>/index.json    # 所有账号的 registry，新账号 append 到末尾
"""
from __future__ import annotations
import argparse, asyncio, json, os, random, re, secrets, string, sys, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

# 兼容直接运行（同目录 import）
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import mailtm_client  # type: ignore
except Exception:
    mailtm_client = None

from playwright.async_api import async_playwright

# ---- 假身份池 ---------------------------------------------------------------

FIRST_NAMES = ["Alex","Jordan","Taylor","Morgan","Casey","Riley","Avery","Quinn",
               "Sam","Cameron","Drew","Hayden","Reese","Dakota","Skyler","Emerson",
               "Finley","Harper","Kendall","Logan","Marlow","Parker","Rowan","Sage"]
LAST_NAMES  = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis",
               "Rodriguez","Martinez","Hernandez","Lopez","Wilson","Anderson","Thomas",
               "Taylor","Moore","Jackson","Martin","Lee","Perez","Thompson","White","Harris"]
COMPANIES   = ["Acme Co","Initech","Globex","Hooli","Vandelay","Stark Industries",
               "Massive Dynamic","Soylent","Pied Piper","Bluth Holdings","Wayne Enterprises",
               "Cyberdyne","Tyrell","Cogswell Cogs","Spacely Sprockets","Mom Corp"]
ROLES       = ["Software Engineer","Product Manager","Data Analyst","DevOps Engineer",
               "Solutions Architect","Technical Writer","QA Engineer","SRE",
               "Backend Engineer","Frontend Engineer","ML Engineer","Platform Engineer"]

def rand_password() -> str:
    """符合常见复杂度规则：大小写+数字+符号，14 位"""
    pool = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        p = "".join(secrets.choice(pool) for _ in range(14))
        if (any(c.isupper() for c in p) and any(c.islower() for c in p)
                and any(c.isdigit() for c in p) and any(c in "!@#$%^&*" for c in p)):
            return p

def rand_identity():
    return {
        "first": random.choice(FIRST_NAMES),
        "last":  random.choice(LAST_NAMES),
        "company": random.choice(COMPANIES),
        "role":    random.choice(ROLES),
    }

# ---- 代理解析 ---------------------------------------------------------------

def parse_proxy(p: str) -> dict:
    """socks5://[user:pass@]host:port  或  http://...  →  Playwright proxy 参数"""
    m = re.match(r"^(?P<scheme>socks5|http|https)://(?:(?P<u>[^:@]+):(?P<pw>[^@]+)@)?(?P<host>[^:]+):(?P<port>\d+)$", p)
    if not m:
        raise ValueError(f"invalid --proxy: {p!r} (want socks5://host:port or http://...)")
    server = f"{m.group('scheme')}://{m.group('host')}:{m.group('port')}"
    out = {"server": server}
    if m.group("u"): out["username"] = m.group("u"); out["password"] = m.group("pw")
    return out

def egress_ip_via_proxy(proxy_url: str, timeout: float = 12.0) -> str:
    """简单查一下出口 IP（仅 http/socks5h via curl 命令）"""
    import subprocess
    try:
        scheme = proxy_url.split("://",1)[0]
        flag = "--socks5-hostname" if scheme.startswith("socks5") else "-x"
        out = subprocess.run(["curl","-s","--max-time",str(int(timeout)),flag,proxy_url.split("://",1)[1] if scheme.startswith("socks5") else proxy_url, "https://api.ipify.org"],
                             capture_output=True, text=True, timeout=timeout+2)
        return (out.stdout or "").strip() or "?"
    except Exception as e:
        return f"err:{e.__class__.__name__}"

# ---- 主流程 -----------------------------------------------------------------

async def shot(page, shots_dir: Path, name: str):
    try:
        await page.screenshot(path=str(shots_dir / f"{name}.png"), full_page=True)
    except Exception: pass

async def dismiss_credit_modal(page, max_tries: int = 8):
    for _ in range(max_tries):
        gone = await page.evaluate("""
          () => {
            const m = document.querySelector('div.fixed.inset-0.bg-black\\\\/50');
            if (!m) return true;
            for (const b of document.querySelectorAll('button')) {
              const t = (b.innerText||'').toLowerCase();
              if (t.includes('continue with free') || t === 'continue') { b.click(); return false; }
            }
            return false;
          }
        """)
        if gone: return True
        await asyncio.sleep(1.5)
    return False

async def provision(args) -> dict:
    out_root = Path(args.out_dir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    label = args.label or f"acc-{int(time.time())}"
    acc_dir = out_root / label
    shots_dir = acc_dir / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    # ---- 1. 邮箱 ----
    if args.email and args.password:
        email = args.email; password = args.password; mail_token = None
        print(f"[mail] 使用手动邮箱 {email}", flush=True)
    else:
        if mailtm_client is None:
            raise RuntimeError("无 mailtm_client.py，且没提供 --email/--password")
        email, password, _ = mailtm_client.create_account()
        mail_token = mailtm_client.get_token(email, password)
        print(f"[mail] mailtm 邮箱: {email}", flush=True)

    ident = rand_identity()
    full_name = f"{ident['first']} {ident['last']}"
    print(f"[id] {full_name} @ {ident['company']} ({ident['role']})", flush=True)

    proxy = parse_proxy(args.proxy)
    egress = egress_ip_via_proxy(args.proxy) if args.check_ip else "(skipped)"
    print(f"[proxy] {proxy['server']}  egress={egress}", flush=True)

    # ---- 2. Playwright ----
    api_calls: list[dict] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=args.headless, args=["--no-sandbox"], proxy=proxy,
        )
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            locale="en-US",
        )
        page = await ctx.new_page()

        def on_req(r):
            u = r.url
            if "obvious.ai" in u and "/api/" in u:
                api_calls.append({"m": r.method, "u": u})
        page.on("request", on_req)

        try:
            print("[step] /signup", flush=True)
            await page.goto("https://app.obvious.ai/signup", wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(4)
            await shot(page, shots_dir, "01_signup")

            # 填写表单 — placeholder 'Enter your name', email, password
            inputs = await page.query_selector_all("input")
            print(f"  inputs found: {len(inputs)}", flush=True)
            for el in inputs:
                ph = (await el.get_attribute("placeholder")) or ""
                typ = (await el.get_attribute("type")) or "text"
                ph_l = ph.lower()
                if "name" in ph_l:
                    await el.click(); await el.type(full_name, delay=20)
                elif typ == "email" or "email" in ph_l:
                    await el.click(); await el.type(email, delay=20)
                elif typ == "password" or "password" in ph_l:
                    await el.click(); await el.type(password, delay=20)
            await asyncio.sleep(0.5)
            await shot(page, shots_dir, "02_form_filled")

            # 点 Sign Up（不要点 'Continue with Google'）
            clicked = await page.evaluate("""
              () => {
                for (const b of document.querySelectorAll('button')) {
                  const t = (b.innerText||'').trim().toLowerCase();
                  if (t === 'sign up' || t === 'create account' || t === 'sign up free') {
                    b.click(); return t;
                  }
                }
                return null;
              }
            """)
            print(f"  signup click: {clicked}", flush=True)
            if not clicked:
                raise RuntimeError("找不到 Sign Up 按钮（页面结构可能变了）")
            await asyncio.sleep(8)
            await shot(page, shots_dir, "03_after_signup")

            # 检查是否要邮箱验证
            url = page.url
            if "verify" in url.lower() or "confirm" in url.lower():
                if not mail_token:
                    raise RuntimeError(f"obvious 要邮箱验证 ({url})，但是手动邮箱无法自动收信")
                print("[verify] 等待 obvious 验证邮件…", flush=True)
                html = mailtm_client.poll_inbox(mail_token, timeout=180,
                                                keywords=("verify","confirm","obvious","email","activate"))
                if not html:
                    raise RuntimeError("180s 内没收到 obvious 验证邮件")
                vurl = mailtm_client.extract_verify_url(html)
                if not vurl:
                    raise RuntimeError("邮件里没找到验证链接")
                print(f"[verify] 点击 {vurl[:80]}…", flush=True)
                await page.goto(vurl, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(6)
                await shot(page, shots_dir, "04_verified")

            # ---- 3. onboarding ----
            # 此时应已到 /onboarding 或自动跳转
            for attempt in range(3):
                if "/onboarding" in page.url: break
                await page.goto("https://app.obvious.ai/onboarding", wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(4)
            await shot(page, shots_dir, "05_onboarding")

            inputs = await page.query_selector_all("input")
            if len(inputs) < 2:
                raise RuntimeError(f"onboarding 期望 2 个输入框，实际 {len(inputs)}")
            await inputs[0].click(); await inputs[0].type(ident["company"], delay=20)
            await inputs[1].click(); await inputs[1].type(ident["role"], delay=20)
            await asyncio.sleep(0.5)
            await shot(page, shots_dir, "06_onboard_filled")

            done_clicked = await page.evaluate("""
              () => {
                for (const b of document.querySelectorAll('button')) {
                  const t = (b.innerText||'').trim().toLowerCase();
                  if (t.includes('get to work')) { b.click(); return t; }
                }
                return null;
              }
            """)
            print(f"  onboard submit: {done_clicked}", flush=True)
            await asyncio.sleep(8)
            await shot(page, shots_dir, "07_after_onboard")

            # ---- 4. 关 25 credits modal ----
            await dismiss_credit_modal(page)
            await asyncio.sleep(3)
            await shot(page, shots_dir, "08_landing")

            # ---- 5. 抓基础 IDs (auth/get-session + workspaces) ----
            workspace_id = None; user_id = None; credit_balance = None; tier = None
            try:
                sess = await page.evaluate("""async () => {
                    const r = await fetch('https://api.app.obvious.ai/prepare/auth/get-session', {credentials:'include'});
                    return {s:r.status, b:await r.text()};
                }""")
                if sess.get("s") == 200:
                    j = json.loads(sess["b"])
                    user_id = (j.get("user") or {}).get("providerId")
            except Exception as e: print(f"  auth/get-session err: {e}", flush=True)
            try:
                wks = await page.evaluate("""async () => {
                    const r = await fetch('https://api.app.obvious.ai/prepare/workspaces', {credentials:'include'});
                    return {s:r.status, b:await r.text()};
                }""")
                if wks.get("s") == 200:
                    j = json.loads(wks["b"])
                    if j.get("workspaces"):
                        w0 = j["workspaces"][0]
                        workspace_id = w0.get("id")
                        credit_balance = w0.get("creditBalance")
                        tier = w0.get("subscriptionTier")
            except Exception as e: print(f"  workspaces err: {e}", flush=True)

            # ---- 6. 创建一个 thread：发一条短消息，URL 会带 prj_/th_ ----
            ci = await page.query_selector('[contenteditable="true"]')
            if not ci:
                raise RuntimeError("找不到 chat 输入框")
            await page.evaluate("() => document.querySelector('[contenteditable=\"true\"]').focus()")
            await asyncio.sleep(0.5)
            await page.keyboard.type("Hello — confirm your sandbox is alive (run: uname -n && date -u)", delay=10)
            await asyncio.sleep(0.5)
            await page.keyboard.press("Enter")
            print("  ↩ first prompt sent", flush=True)

            project_id = None; thread_id = None
            for _ in range(40):
                await asyncio.sleep(1.5)
                m = re.search(r"/p/([a-z0-9-]+-)?([A-Za-z0-9]{6,})", page.url)
                if m: project_id = "prj_" + m.group(2)
                # thread_id 来自 messages API 调用
                for c in api_calls:
                    m2 = re.search(r"/threads/(th_[A-Za-z0-9]+)/", c["u"]) or re.search(r"/agent/chat/(th_[A-Za-z0-9]+)", c["u"])
                    if m2: thread_id = m2.group(1)
                if project_id and thread_id: break

            await shot(page, shots_dir, "09_first_thread")
            print(f"  → projectId={project_id}  threadId={thread_id}", flush=True)

            # 等 agent 完成 + 从 messages tool-result 拿 sandboxId
            sandbox_id = None
            if thread_id:
                for _ in range(20):
                    await asyncio.sleep(3)
                    try:
                        msgs = await page.evaluate("""async (tid) => {
                            const r = await fetch('https://api.app.obvious.ai/prepare/threads/'+tid+'/messages', {credentials:'include'});
                            return {s:r.status, b:await r.text()};
                        }""", thread_id)
                        if msgs.get("s") == 200:
                            m = re.search(r'"sandboxId"\s*:\s*"([a-z0-9]+)"', msgs["b"])
                            if m: sandbox_id = m.group(1); break
                    except: pass
            print(f"  → sandboxId={sandbox_id}", flush=True)
            await shot(page, shots_dir, "10_sandbox_alive")

            # ---- 7. 保存 ----
            state = await ctx.storage_state()
            state_path = acc_dir / "storage_state.json"
            with open(state_path, "w") as f: json.dump(state, f)

            manifest = {
                "label": label,
                "email": email, "password": password,
                "name": full_name, "company": ident["company"], "role": ident["role"],
                "userId": user_id, "workspaceId": workspace_id,
                "projectId": project_id, "threadId": thread_id,
                "sandboxId": sandbox_id,
                "creditBalance": credit_balance, "tier": tier,
                "proxy": args.proxy, "egressIp": egress,
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "storageState": str(state_path),
            }
            with open(acc_dir / "manifest.json", "w") as f:
                json.dump(manifest, f, indent=2)

            # registry
            registry = out_root / "index.json"
            try:
                idx = json.load(open(registry)) if registry.exists() else []
            except Exception: idx = []
            idx.append({k: manifest[k] for k in ("label","email","userId","workspaceId","projectId","threadId","proxy","egressIp","createdAt")})
            with open(registry, "w") as f: json.dump(idx, f, indent=2)

            return manifest
        except Exception as e:
            await shot(page, shots_dir, "ERR")
            raise
        finally:
            await browser.close()


def main():
    ap = argparse.ArgumentParser(description="obvious.ai 自动注册 + onboarding")
    ap.add_argument("--proxy", required=True, help="socks5://host:port 或 http://...")
    ap.add_argument("--out-dir", default="/root/obvious-accounts", help="账号根目录")
    ap.add_argument("--label", help="账号短标签（默认时间戳）")
    ap.add_argument("--email", help="手动邮箱（不写则用 mailtm）")
    ap.add_argument("--password", help="手动密码")
    ap.add_argument("--headless", action="store_true", help="无头模式（默认有头，方便排错）")
    ap.add_argument("--check-ip", action="store_true", help="启动前用 curl 查代理出口 IP")
    args = ap.parse_args()

    if (args.email and not args.password) or (args.password and not args.email):
        ap.error("--email 和 --password 必须一起给")

    try:
        m = asyncio.run(provision(args))
        print("\n=== ✅ provisioned ===")
        print(json.dumps(m, indent=2))
    except Exception as e:
        print(f"\n=== ❌ FAILED: {e} ===", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
