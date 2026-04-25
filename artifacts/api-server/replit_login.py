#!/usr/bin/env python3
"""
replit_login.py — 复用注册时保存的 storage_state, 实现 0-captcha 0-cf 复登。

调用:
    python3 replit_login.py '<json>'

JSON 字段 (按优先级):
    {
      "username":  "...",                # 必填: 用于定位 .state/replit/<username>.json
      "email":     "...",                # 可选: 兜底密码登录用
      "password":  "...",                # 可选: 兜底密码登录用
      "outlook_refresh_token": "...",    # 可选: 密码登录若触发 OTP 邮件验证时用
      "force_password": false,           # 可选: 强制走密码登录, 不读 storage
      "cdp_ws":    "http://127.0.0.1:9222"  # 可选: 密码登录走 CDP attach broker chromium
    }

返回 JSON:
    {
      "ok": true,
      "via": "storage" | "password",
      "username": "...",
      "cookies": "connect.sid=...; cf_clearance=...; ...",   # 兼容旧调用方
      "connect_sid": "...",
      "cf_clearance": "...",
      "state_path": "/root/Toolkit/.state/replit/<username>.json",
      "user": { ...current user JSON from /api/v1/users/current... }
    }

设计原则:
  1. 注册时已落 storage_state → load_state 进 context, 访问 /api/v1/users/current
     → 200 + {username:...} 视为复用成功. 全程无 captcha 无 CF 挑战.
  2. storage 缺失 / 过期 / 校验失败 → 走密码登录. 密码登录强烈建议 use CDP
     (attach broker chromium), 否则 reCAPTCHA Enterprise 评分会被 datacenter IP
     拖到 ban 线以下.
  3. 密码登录成功后必须重新落 storage_state, 这样下次又能走 fast path.
"""
import sys, json, os, time, asyncio
from pathlib import Path

STATE_DIR = "/root/Toolkit/.state/replit"


def _log(msg: str):
    print(f"[replit_login] {msg}", flush=True)


def _state_path(username: str) -> str:
    return os.path.join(STATE_DIR, f"{username}.json")


def _format_cookies(cookies: list) -> tuple[str, str, str]:
    """Return (cookie_header, connect_sid, cf_clearance)."""
    parts, sid, cfc = [], "", ""
    for c in cookies:
        n, v = c.get("name", ""), c.get("value", "")
        if not n:
            continue
        parts.append(f"{n}={v}")
        if n == "connect.sid":
            sid = v
        elif n == "cf_clearance":
            cfc = v
    return "; ".join(parts), sid, cfc


async def _try_storage(username: str) -> dict | None:
    """Fast path: load saved storage_state, verify by /api/v1/users/current."""
    path = _state_path(username)
    if not os.path.exists(path):
        _log(f"storage 不存在: {path}")
        return None
    try:
        st = json.loads(Path(path).read_text())
    except Exception as e:
        _log(f"storage 解析失败: {e}")
        return None

    cookies = st.get("cookies", []) or []
    if not any(c.get("name") == "connect.sid" for c in cookies):
        _log("storage 无 connect.sid, 视为无效")
        return None

    # 检查 cookie 过期
    now = int(time.time())
    sid = next(c for c in cookies if c.get("name") == "connect.sid")
    exp = sid.get("expires", -1)
    if exp > 0 and exp < now:
        _log(f"connect.sid 已过期 (exp={exp} now={now})")
        return None

    # 验证: 用 storage_state 起一个 context 访问 /api/v1/users/current
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        from rebrowser_playwright.async_api import async_playwright

    async with async_playwright() as pw:
        # 注意: storage_state 需要 dict 或 path. 直接传 dict.
        # 但 _meta 字段会被 Playwright 拒绝, 先剔除.
        st_clean = {k: v for k, v in st.items() if k in ("cookies", "origins")}
        # v7.85 — 走 WARP, 跟注册/复现路径出口 IP 一致 (104.28.x.x). 老逻辑裸
        # launch 让 chromium 直接从 VPS 公网 (45.205.27.69) 打 replit.com,
        # 跟 cf_clearance 绑定的 WARP IP 不匹配, 大概率被 CF 挑战.
        _warp = os.environ.get("REPLIT_BROWSER_PROXY") or os.environ.get("BROWSER_PROXY") or "socks5://127.0.0.1:40000"
        br = await pw.chromium.launch(
            headless=True,
            proxy={"server": _warp},
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                  "--disable-quic"],
        )
        _log(f"storage-replay launch: proxy={_warp} (v7.85 WARP)")
        ctx = await br.new_context(
            storage_state=st_clean,
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        )
        page = await ctx.new_page()
        try:
            r = await page.request.get(
                "https://replit.com/api/v1/users/current",
                headers={"Accept": "application/json"},
                timeout=15000,
            )
            status = r.status
            body = ""
            try:
                body = await r.text()
            except Exception:
                pass
            _log(f"GET /api/v1/users/current → {status} bodylen={len(body)}")
            if status != 200:
                await br.close()
                return None
            try:
                user = json.loads(body)
            except Exception:
                user = {}
            if not user.get("username"):
                _log("响应无 username, 视为未登录")
                await br.close()
                return None
            # 抓最新 cookies (可能有刷新)
            fresh = await ctx.cookies()
            await br.close()
            cookie_h, sid_v, cfc_v = _format_cookies(fresh)
            return {
                "ok": True,
                "via": "storage",
                "username": user.get("username"),
                "cookies": cookie_h,
                "connect_sid": sid_v,
                "cf_clearance": cfc_v,
                "state_path": path,
                "user": user,
            }
        except Exception as e:
            _log(f"verify 异常: {e}")
            try: await br.close()
            except: pass
            return None


async def _password_login(args: dict) -> dict | None:
    """Slow path: CDP attach broker chromium + stealth + reCAPTCHA Enterprise."""
    email = args.get("email", "")
    password = args.get("password", "")
    cdp_ws = args.get("cdp_ws", "http://127.0.0.1:9222")
    if not email or not password:
        _log("无 email/password, 无法密码登录")
        return None

    # 尝试 CDP attach broker (复用 cf_clearance + STEALTH)
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        from rebrowser_playwright.async_api import async_playwright

    # Ping broker /api/cf-warmup to ensure chromium is up
    try:
        import urllib.request
        urllib.request.urlopen("http://127.0.0.1:8092/api/cf-warmup", timeout=30).read()
    except Exception as e:
        _log(f"cf-warmup ping 失败: {e}")

    async with async_playwright() as pw:
        try:
            br = await pw.chromium.connect_over_cdp(cdp_ws, timeout=20000)
        except Exception as e:
            # v7.85 — 兜底 launch 走 WARP, 不走 VPS 公网. 老逻辑裸 launch 让密码
            # 登录直接从 VPS 出去, reCAPTCHA Enterprise 看到 datacenter IP 直接
            # 拖到 ban 线以下, 必挂.
            _warp = os.environ.get("REPLIT_BROWSER_PROXY") or os.environ.get("BROWSER_PROXY") or "socks5://127.0.0.1:40000"
            _log(f"CDP attach 失败 ({e}), 走 launch 兜底 (proxy={_warp} v7.85 WARP)")
            br = await pw.chromium.launch(
                headless=True,
                proxy={"server": _warp},
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                      "--disable-quic"],
            )
        try:
            ctx = br.contexts[0] if br.contexts else await br.new_context()
            page = await ctx.new_page()

            # 注入 google-route 和 STEALTH (与注册一致)
            try:
                sys.path.insert(0, "/root/Toolkit/artifacts/api-server")
                from google_proxy_route import attach_google_proxy_routing as _agr
                await _agr(ctx)
                _log("google-route attached")
            except Exception as e:
                _log(f"google-route 注入失败: {e}")

            await page.goto("https://replit.com/login",
                            wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(3000)

            await page.locator(
                'input[name="username"], input[type="email"]').first.fill(email, timeout=10000)
            await page.locator(
                'input[name="password"], input[type="password"]').first.fill(password, timeout=10000)

            # 等 reCAPTCHA Enterprise execute() 评分
            await page.wait_for_timeout(4000)
            try:
                await page.locator(
                    'button[type="submit"], button:has-text("Log in"), button:has-text("Sign in")'
                ).first.click(timeout=8000)
            except Exception:
                await page.keyboard.press("Enter")

            # 等 30s 等待登录完成
            for _ in range(30):
                await page.wait_for_timeout(1000)
                if "/login" not in page.url.lower() and "/signup" not in page.url.lower():
                    break

            cur_url = page.url
            _log(f"登录后 URL: {cur_url[:80]}")
            if "/login" in cur_url.lower():
                txt = await page.content()
                _log(f"仍在 login 页 (body 头 200 char): {txt[:200]}")
                await br.close() if not br.is_connected() else None
                return None

            # 抓 cookies + storage_state
            fresh = await ctx.cookies()
            cookie_h, sid_v, cfc_v = _format_cookies(fresh)
            if not sid_v:
                _log("无 connect.sid, 登录失败")
                return None

            # 落盘
            username = args.get("username", "")
            if not username:
                # 从 URL 提取
                import re as _re
                m = _re.search(r"replit\.com/@([^/?]+)", cur_url)
                if m: username = m.group(1)
            state_path = ""
            if username:
                try:
                    os.makedirs(STATE_DIR, exist_ok=True)
                    state_path = _state_path(username)
                    st = await ctx.storage_state()
                    st["_meta"] = {"username": username, "email": email,
                                   "saved_at": int(time.time()), "via": "password_login"}
                    with open(state_path, "w") as f:
                        json.dump(st, f, indent=2)
                    _log(f"✅ storage_state 已保存 → {state_path}")
                except Exception as e:
                    _log(f"storage_state 保存失败: {e}")

            return {
                "ok": True,
                "via": "password",
                "username": username,
                "cookies": cookie_h,
                "connect_sid": sid_v,
                "cf_clearance": cfc_v,
                "state_path": state_path,
            }
        finally:
            try:
                if br.is_connected():
                    await br.close()
            except Exception:
                pass


async def main():
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "缺少参数 JSON"}))
        return
    try:
        args = json.loads(sys.argv[1])
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"JSON 解析失败: {e}"}))
        return

    username = args.get("username", "")
    if not username and args.get("email"):
        # email 不带 username 时, 取 email 前缀作为状态文件 key (近似)
        username = args["email"].split("@")[0]

    force_pw = bool(args.get("force_password"))
    if not force_pw and username:
        res = await _try_storage(username)
        if res:
            print(json.dumps(res, ensure_ascii=False))
            return

    # 走密码登录
    _log("→ 密码登录 fallback")
    res = await _password_login(args)
    if res:
        print(json.dumps(res, ensure_ascii=False))
        return
    print(json.dumps({"ok": False, "error": "storage+password 均失败"}))


if __name__ == "__main__":
    asyncio.run(main())
