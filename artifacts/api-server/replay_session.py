#!/usr/bin/env python3
"""
v7.78k — replay_session.py

从 DB 取 Replit 账号的链路指纹 (exit_ip / proxy_port / user_agent /
fingerprint_json) + 持久化的 storage_state 文件 (.state/replit/<username>.json),
用 patchright 重建一致上下文 (UA / viewport / locale / timezone / SOCKS 出口),
访问 https://replit.com/account 校验 server 端是否仍认可登录态。

链路一致性的所有维度都用 register 时落库的快照,确保:
  - cf_clearance 的 IP+UA 绑定不漂移 (Cloudflare 不二次 challenge)
  - connect.sid + __Host-session-sig 的设备指纹一致 (Replit 风控不顶号)
  - statsig/amplitude device id 持久化 (前端 fingerprintjs 不告警)

用法:
  python3 replay_session.py <account_id_or_username>
  python3 replay_session.py 338
  python3 replay_session.py zephyrcosmic897

输出: 单行 JSON {ok, logged_in, username, email, user_id, final_url, error?}
"""
import asyncio, json, os, sys, traceback
import psycopg2

STATE_DIR = "/root/Toolkit/.state/replit"
DB_DSN = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/toolkit")


def fetch_account(key: str) -> dict:
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    if key.isdigit():
        cur.execute("SELECT id,email,username,status,exit_ip,proxy_port,user_agent,fingerprint_json "
                    "FROM accounts WHERE platform=%s AND id=%s", ("replit", int(key)))
    else:
        cur.execute("SELECT id,email,username,status,exit_ip,proxy_port,user_agent,fingerprint_json "
                    "FROM accounts WHERE platform=%s AND username=%s", ("replit", key))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        raise RuntimeError(f"account not found: {key}")
    cols = ["id","email","username","status","exit_ip","proxy_port","user_agent","fingerprint_json"]
    return dict(zip(cols, row))


async def replay(acc: dict) -> dict:
    out = {"ok": False, "logged_in": False, "account_id": acc["id"],
           "username": acc["username"], "email": acc["email"]}
    state_path = os.path.join(STATE_DIR, str(acc["username"]) + ".json")
    if not os.path.exists(state_path):
        out["error"] = f"state file missing: {state_path}"
        return out

    fp = acc.get("fingerprint_json") or {}
    if isinstance(fp, str):
        fp = json.loads(fp)
    ua = acc.get("user_agent") or fp.get("user_agent")
    if not ua:
        out["error"] = "no user_agent in DB or fingerprint"
        return out

    # SOCKS 出口: register 时落库的 proxy_port → 复用同一条出口 (xray 池里同
    # 一个 outbound,多数情况映射回 VPS 自己 IP — 与 register 时 exit_ip 一致)
    proxy_cfg = None
    pp = acc.get("proxy_port")
    if pp:
        proxy_cfg = {"server": f"socks5://127.0.0.1:{pp}"}

    try:
        from patchright.async_api import async_playwright
    except Exception:
        from playwright.async_api import async_playwright

    # v7.78k: 注入 register 同款 stealth — 链路一致性不仅是 IP/UA/timezone,
    # 还包括浏览器侧 fingerprint (navigator.webdriver / canvas / webgl / chrome.runtime).
    # patchright 默认 launch 会被 Cloudflare 识别成自动化 → 弹 challenge → 403.
    stealth_fn = None
    try:
        from playwright_stealth import Stealth
        stealth_fn = Stealth(chrome_runtime=True, webgl_vendor=True).apply_stealth_async
    except Exception:
        pass

    canvas_noise_js = ""
    try:
        # 复用 register 的 _CANVAS_WEBGL_NOISE_JS 常量，确保两侧指纹完全一致
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location("replit_register",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "replit_register.py"))
        # 不实际 exec 全模块（开销大），用文本提取常量更轻
        _txt = open(_spec.origin).read()
        _m = _txt.split("_CANVAS_WEBGL_NOISE_JS = \"\"\"", 1)
        if len(_m) == 2:
            canvas_noise_js = _m[1].split("\"\"\"", 1)[0]
    except Exception:
        canvas_noise_js = ""

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx_kw = dict(
            storage_state=state_path,
            user_agent=ua,
            viewport=fp.get("viewport") or {"width": 1920, "height": 1040},
            screen=fp.get("screen") or {"width": 1920, "height": 1080},
            device_scale_factor=fp.get("device_scale_factor", 1),
            is_mobile=fp.get("is_mobile", False),
            has_touch=fp.get("has_touch", False),
            locale=fp.get("locale", "en-US"),
            timezone_id=fp.get("timezone_id", "America/Los_Angeles"),
            color_scheme=fp.get("color_scheme", "light"),
            ignore_https_errors=True,
            extra_http_headers=fp.get("extra_http_headers") or {
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        if proxy_cfg:
            ctx_kw["proxy"] = proxy_cfg
        ctx = await browser.new_context(**ctx_kw)
        page = await ctx.new_page()
        if stealth_fn:
            try: await stealth_fn(page)
            except Exception: pass
        if canvas_noise_js:
            try: await page.add_init_script(canvas_noise_js)
            except Exception: pass

        try:
            resp = await page.goto("https://replit.com/account", timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            final_url = page.url
            title = await page.title()
            body = await page.evaluate("() => document.body ? document.body.innerText : \"\"") or ""
            html = await page.content()

            # 登录判定: 已登录页 URL 仍在 /account 不被跳到 /login,
            # 且 HTML/body 含本人 username 或 user_id (注: HTML 里有 hydrated 数据)
            redirected = ("/login" in final_url) or ("/signup" in final_url)
            uname = acc["username"] or ""
            email = acc["email"] or ""
            id_marker_hit = (uname and uname.lower() in html.lower()) or \
                            (email and email.lower() in html.lower())
            out["logged_in"] = (not redirected) and id_marker_hit and (resp.status == 200 if resp else False)
            out["http_status"] = resp.status if resp else 0
            out["final_url"] = final_url
            out["title"] = title
            out["body_snippet"] = body[:200].replace("\n", " ")
            out["proxy_used"] = proxy_cfg["server"] if proxy_cfg else "direct"
            out["ok"] = out["logged_in"]
        except Exception as e:
            out["error"] = f"navigate failed: {e}"
        finally:
            await ctx.close(); await browser.close()
    return out


async def main():
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "usage: replay_session.py <account_id_or_username>"}))
        sys.exit(1)
    try:
        acc = fetch_account(sys.argv[1])
        result = await replay(acc)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e), "trace": traceback.format_exc()[-400:]}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
