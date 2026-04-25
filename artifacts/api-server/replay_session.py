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
import asyncio, json, os, subprocess, sys, traceback
import psycopg2

STATE_DIR = "/root/Toolkit/.state/replit"
XRAY_POOL = list(range(10820, 10846))
VPS_IP_CACHE: dict = {}


def vps_public_ip() -> str:
    if "ip" in VPS_IP_CACHE:
        return VPS_IP_CACHE["ip"]
    for url in ("https://ifconfig.me", "https://api.ipify.org", "https://icanhazip.com"):
        try:
            r = subprocess.run(["curl", "-s", "-4", "--max-time", "5", url],
                               capture_output=True, text=True, timeout=8)
            ip = r.stdout.strip()
            if ip and ip.count(".") == 3:
                VPS_IP_CACHE["ip"] = ip; return ip
        except Exception:
            pass
    return ""


def probe_socks_exit_ip(port: int, timeout: int = 6) -> str:
    """通过本地 SOCKS port 请求 ifconfig.me 拿当前出口公网 IP"""
    try:
        r = subprocess.run(
            ["curl", "-s", "-4", "--max-time", str(timeout),
             "--socks5-hostname", "127.0.0.1:" + str(port), "https://ifconfig.me"],
            capture_output=True, text=True, timeout=timeout + 2,
        )
        ip = (r.stdout or "").strip()
        return ip if ip.count(".") == 3 else ""
    except Exception:
        return ""


def resolve_proxy_for_target(target_ip: str, hint_port: int) -> dict:
    """按 register 时落库 exit_ip 找当前能落到同一 IP 的 SOCKS 端口；
    若 target == VPS 自身 IP, 直接 bypass SOCKS 走直连 (最稳)。"""
    out = {"target_ip": target_ip, "candidates_tried": []}
    vps = vps_public_ip()

    if target_ip and vps and target_ip == vps:
        out["mode"] = "direct"; out["probed_ip"] = vps; return out

    if hint_port:
        ip = probe_socks_exit_ip(hint_port)
        out["candidates_tried"].append({"port": hint_port, "ip": ip})
        if ip and ip == target_ip:
            out["mode"] = "socks"; out["port"] = hint_port
            out["server"] = "socks5://127.0.0.1:" + str(hint_port)
            out["probed_ip"] = ip; return out

    for pp in XRAY_POOL:
        if pp == hint_port: continue
        ip = probe_socks_exit_ip(pp, timeout=4)
        out["candidates_tried"].append({"port": pp, "ip": ip})
        if ip and ip == target_ip:
            out["mode"] = "socks_alt"; out["port"] = pp
            out["server"] = "socks5://127.0.0.1:" + str(pp)
            out["probed_ip"] = ip; return out

    out["mode"] = "unmatched"; out["probed_ip"] = ""
    return out
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

    # v7.78l: IP 一致性校验 — exit_ip 是 register 实测出口 IP, 但同一个
    # SOCKS 端口在不同时间可能走不同上游 outbound (xray pool / WARP 漂移),
    # 不一定每次都落同 IP. Replit + Cloudflare 的 cf_clearance / connect.sid
    # 都做 IP 校验, IP 漂移会立刻被拒. 先 probe 当前出口 IP, 不一致就遍历
    # xray 池或 bypass 走 VPS 直连.
    target_ip = (acc.get("exit_ip") or "").strip()
    hint_port = acc.get("proxy_port") or 0
    resolved = resolve_proxy_for_target(target_ip, int(hint_port) if hint_port else 0)
    out["resolved_proxy"] = {k: v for k, v in resolved.items() if k != "candidates_tried"}
    proxy_cfg = None
    if resolved["mode"] in ("socks", "socks_alt"):
        proxy_cfg = {"server": resolved["server"]}
    elif resolved["mode"] == "direct":
        proxy_cfg = None
    else:
        out["error"] = ("exit_ip 漂移: register 时=" + target_ip + ", 当前所有 xray "
                        "端口都不落到该 IP. 上游池子 outbound 配置可能已变, "
                        "建议把账号标 needs_relink 重新登录刷新 cf_clearance.")
        out["candidates_tried"] = resolved["candidates_tried"]
        return out

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
            out["proxy_used"] = ((proxy_cfg["server"] if proxy_cfg else "direct") + " (mode=" + resolved["mode"] + ")")
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
