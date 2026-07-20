#!/usr/bin/env python3
"""
outlook_retoken.py — Outlook 账号自动浏览器重新授权
用 patchright 打开浏览器，登录账号，完成 device code OAuth 授权。
不需要人工介入。

用法:
  python3 outlook_retoken.py --ids 2,3,4     # 指定账号 ID
  python3 outlook_retoken.py --all-error      # 所有 error 状态账号
  python3 outlook_retoken.py --all-error --headless false  # 可视化模式
"""

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.parse
import urllib.request

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/toolkit")

CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
TENANT    = "consumers"
SCOPE     = "offline_access https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/User.Read"

# URL 关键词 → 账号已被 Microsoft 封锁/冻结
LOCKED_URL_KEYWORDS = (
    "abuse", "accountfrozen", "accountcompromised", "accountblocked",
    "account/cancel", "account/recover", "identityprotection",
    "recover?", "isblocked", "suspensioncenter",
)

# 页面正文中出现这些词 → 封号
LOCKED_TEXT_KEYWORDS = [
    "account has been locked", "your account has been suspended",
    "账号已被锁定", "账户已暂停", "帐户已锁定",
    "unusual sign-in activity", "we've detected suspicious activity",
    "your account has been blocked",
]


def log(msg: str):
    print(msg, flush=True)


def db_conn():
    return psycopg2.connect(DATABASE_URL)


def get_accounts(ids: list[int] | None, all_error: bool) -> list[dict]:
    conn = db_conn()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if ids:
        # v8.20: 扩展 SELECT, 取 cookies/fingerprint/UA/IP/port 用于 retoken 复用注册时的浏览器指纹
        cur.execute(
            "SELECT id, email, password, cookies_json, fingerprint_json, user_agent, exit_ip, proxy_port FROM accounts WHERE platform='outlook' AND id = ANY(%s) AND password IS NOT NULL AND password != ''",
            (ids,),
        )
    elif all_error:
        cur.execute(
            "SELECT id, email, password, cookies_json, fingerprint_json, user_agent, exit_ip, proxy_port FROM accounts WHERE platform='outlook' AND status='error' AND password IS NOT NULL AND password != '' ORDER BY id"
        )
    else:
        cur.execute(
            "SELECT id, email, password, cookies_json, fingerprint_json, user_agent, exit_ip, proxy_port FROM accounts WHERE platform='outlook' AND (token IS NULL OR token='') AND password IS NOT NULL AND password != '' ORDER BY id"
        )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def save_tokens(account_id: int, access_token: str, refresh_token: str):
    conn = db_conn()
    cur  = conn.cursor()
    cur.execute(
        "UPDATE accounts SET token=%s, refresh_token=%s, status='active', updated_at=NOW() WHERE id=%s",
        (access_token, refresh_token, account_id),
    )
    conn.commit()
    conn.close()


def mark_locked(account_id: int):
    conn = db_conn()
    cur  = conn.cursor()
    cur.execute("UPDATE accounts SET status='locked', updated_at=NOW() WHERE id=%s", (account_id,))
    conn.commit()
    conn.close()


def mark_failed(account_id: int):
    conn = db_conn()
    cur  = conn.cursor()
    cur.execute("UPDATE accounts SET status='error', updated_at=NOW() WHERE id=%s", (account_id,))
    conn.commit()
    conn.close()


def url_is_locked(url: str) -> bool:
    """URL 中包含 Microsoft 封号相关关键词。"""
    low = url.lower()
    return any(kw in low for kw in LOCKED_URL_KEYWORDS)


def request_device_code() -> dict:
    data = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "scope":     SCOPE,
    }).encode()
    req = urllib.request.Request(
        f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/devicecode",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
    return resp


def poll_token(device_code: str, interval: int = 5, expires_in: int = 900) -> dict | None:
    deadline = time.time() + expires_in
    data = urllib.parse.urlencode({
        "client_id":    CLIENT_ID,
        "grant_type":   "urn:ietf:params:oauth:grant-type:device_code",
        "device_code":  device_code,
    }).encode()
    while time.time() < deadline:
        time.sleep(interval)
        req = urllib.request.Request(
            f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
        except Exception:
            continue
        if resp.get("access_token"):
            return resp
        err = resp.get("error", "")
        if err in ("authorization_pending", "slow_down"):
            if err == "slow_down":
                time.sleep(interval)
            continue
        log(f"  ❌ token 轮询失败: {resp.get('error_description', err)[:120]}")
        return None
    log("  ❌ 设备码已过期")
    return None


async def retoken_account(account: dict, headless: bool, proxy: str = "") -> bool:
    email    = account["email"]
    password = account["password"]
    acc_id   = account["id"]
    # v8.20: 提取注册时持久化的 identity bundle, 复用避免微软风控 abuse
    saved_cookies_json     = (account.get("cookies_json")     or "").strip()
    saved_fingerprint_json = (account.get("fingerprint_json") or "").strip()
    saved_user_agent       = (account.get("user_agent")       or "").strip()
    saved_exit_ip          = (account.get("exit_ip")          or "").strip()
    saved_proxy_port       = int(account.get("proxy_port") or 0)
    # v8.21: 二级 fallback — accounts 表字段空(老账号或被清空), 从 archives 表恢复
    if not (saved_cookies_json or saved_fingerprint_json):
        try:
            _conn = db_conn()
            _cur  = _conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            _cur.execute(
                "SELECT cookies, fingerprint, identity_data, proxy_used FROM archives "
                "WHERE platform='outlook' AND email=%s",
                (email,),
            )
            _arc = _cur.fetchone()
            _conn.close()
            if _arc:
                if _arc.get("cookies") and not saved_cookies_json:
                    saved_cookies_json = json.dumps(_arc["cookies"], ensure_ascii=False)
                if _arc.get("fingerprint") and not saved_fingerprint_json:
                    saved_fingerprint_json = json.dumps(_arc["fingerprint"], ensure_ascii=False)
                _idn = _arc.get("identity_data") or {}
                if isinstance(_idn, dict):
                    if _idn.get("user_agent") and not saved_user_agent:
                        saved_user_agent = _idn["user_agent"]
                    if _idn.get("exit_ip") and not saved_exit_ip:
                        saved_exit_ip = str(_idn["exit_ip"])
                    if _idn.get("proxy_port") and not saved_proxy_port:
                        saved_proxy_port = int(_idn["proxy_port"] or 0)
                if saved_cookies_json or saved_fingerprint_json:
                    log(f"  📚 从 archives 恢复 identity: cookies={len(saved_cookies_json)}B fp={len(saved_fingerprint_json)}B")
        except Exception as _afe:
            log(f"  ⚠ archives fallback 查询失败: {_afe}")

    log(f"\n{'='*60}")
    log(f"[{acc_id}] 开始处理: {email}")

    # 1. 申请设备码
    try:
        dc = request_device_code()
    except Exception as e:
        log(f"  ❌ 申请设备码失败: {e}")
        mark_failed(acc_id)
        return False

    user_code        = dc["user_code"]
    device_code_val  = dc["device_code"]
    verification_uri = dc.get("verification_uri", "https://microsoft.com/devicelogin")
    interval         = dc.get("interval", 5)
    expires_in       = dc.get("expires_in", 900)
    log(f"  🔑 设备码: {user_code}  URL: {verification_uri}")

    # 2. 启动 patchright 浏览器
    try:
        from patchright.async_api import async_playwright
    except ImportError:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            log("  ❌ 未安装 patchright/playwright")
            mark_failed(acc_id)
            return False

    # v8.21: IP 一致性 — 若有 saved_exit_ip, 启动专属 XrayRelay 让 patchright 走同一个 CF IP 出口,
    # 避免微软风控因为 "注册时美东IP, 登录时VPS马来IP" 触发 abuse_mode 封号
    xray_relay_inst = None
    saved_proxy_url  = ""
    if saved_exit_ip and not proxy:  # proxy 是手动传入参数, 优先级更高
        try:
            from xray_relay import XrayRelay as _XrayRelay
            xray_relay_inst = _XrayRelay(saved_exit_ip)
            if xray_relay_inst.start(timeout=8.0):
                saved_proxy_url = xray_relay_inst.socks5_url
                proxy = saved_proxy_url  # 注入到下面 launch_args 用的 proxy 变量
                log(f"  🌐 IP 一致性: 复用注册时 CF IP {saved_exit_ip} → SOCKS5:{xray_relay_inst.socks_port}")
            else:
                log(f"  ⚠ XrayRelay({saved_exit_ip}) 启动失败 — 退化为 VPS 直连 (IP 不一致风险)")
                xray_relay_inst = None
        except Exception as _xre:
            log(f"  ⚠ XrayRelay 启动异常: {_xre} — 退化为 VPS 直连")
            xray_relay_inst = None
    elif not saved_exit_ip:
        log(f"  ⚠ 无 saved_exit_ip (老账号), 用 VPS 直连 — IP 不一致风险高")

    # 在后台线程中轮询 token（与浏览器操作并行）
    token_result: list[dict | None] = [None]
    token_done = asyncio.Event()

    async def poll_background():
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, poll_token, device_code_val, interval, expires_in)
        token_result[0] = result
        token_done.set()

    poll_task = asyncio.create_task(poll_background())

    async def check_locked(page) -> bool:
        """检查当前页面是否为 Microsoft 封号页面，URL 或正文均检测。"""
        if url_is_locked(page.url):
            log(f"  🔒 URL 含封号关键词: {page.url[:100]}")
            return True
        try:
            body = await page.inner_text("body", timeout=2000)
            body_low = body.lower()
            for kw in LOCKED_TEXT_KEYWORDS:
                if kw.lower() in body_low:
                    log(f"  🔒 页面正文检测到封号标志: '{kw}'")
                    return True
        except Exception:
            pass
        return False

    async with async_playwright() as p:
        launch_args: dict = {
            "headless": headless,
            "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        }
        if proxy:
            launch_args["proxy"] = {"server": proxy}

        browser = await p.chromium.launch(**launch_args)
        # v8.20: 优先复用注册时持久化的 fingerprint + cookies + UA, 否则回退默认
        # 关键修复: 之前每次 retoken 都用全新随机指纹 + 新 IP, 微软风控判定 abuse
        # 导致大批量账号注册 1-3 天内全部 suspended
        _ctx_kwargs = {
            "locale": "en-US",
            "timezone_id": "America/Los_Angeles",
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        }
        _saved_fp = None
        if saved_fingerprint_json:
            try:
                _saved_fp = json.loads(saved_fingerprint_json)
                # 复用注册时 UA / 时区 / 屏幕等
                if _saved_fp.get("user_agent"):
                    _ctx_kwargs["user_agent"] = _saved_fp["user_agent"]
                if _saved_fp.get("locale"):
                    _ctx_kwargs["locale"] = _saved_fp["locale"]
                if _saved_fp.get("timezone_id"):
                    _ctx_kwargs["timezone_id"] = _saved_fp["timezone_id"]
                if _saved_fp.get("viewport"):
                    _ctx_kwargs["viewport"] = _saved_fp["viewport"]
                if _saved_fp.get("screen"):
                    _ctx_kwargs["screen"] = _saved_fp["screen"]
                log(f"  📦 复用注册指纹: ua={_ctx_kwargs['user_agent'][:40]}... tz={_ctx_kwargs['timezone_id']}")
            except Exception as _fpe:
                log(f"  ⚠ fingerprint_json 解析失败, 用默认: {_fpe}")
        elif saved_user_agent:
            _ctx_kwargs["user_agent"] = saved_user_agent
            log(f"  📦 复用注册 UA (无完整 fp): {saved_user_agent[:40]}...")
        else:
            log(f"  ⚠ 账号无持久化 identity (老账号), 用默认 context — 风控风险高")

        context = await browser.new_context(**_ctx_kwargs)

        # v8.20: 注入注册时的 cookies (含 storage_state)
        if saved_cookies_json:
            try:
                _state = json.loads(saved_cookies_json)
                if isinstance(_state, dict) and _state.get("cookies"):
                    await context.add_cookies(_state["cookies"])
                    log(f"  🍪 已注入 {len(_state['cookies'])} 个注册时 cookies")
            except Exception as _ce:
                log(f"  ⚠ cookies_json 解析失败: {_ce}")

        page = await context.new_page()

        try:
            # 3. 导航到 devicelogin 页面
            log(f"  🌐 打开: {verification_uri}")
            await page.goto(verification_uri, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)

            # 4. 输入 user_code
            code_input = page.locator('input[name="otc"], input[id="otc"], input[type="text"]').first
            await code_input.wait_for(timeout=10000)
            await code_input.fill(user_code)
            log(f"  ✍️  输入用户码: {user_code}")
            await asyncio.sleep(1)

            next_btn = page.locator('input[type="submit"], button[type="submit"]').first
            await next_btn.click()
            await asyncio.sleep(3)

            # 5. 输入 email
            email_input = page.locator('input[type="email"], input[name="loginfmt"]').first
            try:
                _email_visible = await email_input.is_visible(timeout=5000)
            except Exception:
                _email_visible = False
            if _email_visible:
                await email_input.fill(email)
                log(f"  📧 输入邮箱: {email}")
                await asyncio.sleep(1)
                next_btn2 = page.locator('input[type="submit"], button[type="submit"]').first
                await next_btn2.click()
                await asyncio.sleep(3)

            # ── 邮箱提交后检测封号 ──
            if await check_locked(page):
                poll_task.cancel()
                await browser.close()
                mark_locked(acc_id)
                log(f"  🔒 [写库 locked] 邮箱提交后封号: {email}")
                return False

            # 6. 输入密码
            pw_input = page.locator('input[type="password"], input[name="passwd"]').first
            pw_visible = False
            try:
                pw_visible = await pw_input.is_visible(timeout=8000)
            except Exception:
                pass

            if pw_visible:
                await pw_input.fill(password)
                log("  🔒 输入密码")
                await asyncio.sleep(1)
                sign_btn = page.locator('input[type="submit"], button[type="submit"]').first
                await sign_btn.click()
                await asyncio.sleep(4)
            else:
                # 密码框未出现 → 先检测封号
                if await check_locked(page):
                    poll_task.cancel()
                    await browser.close()
                    mark_locked(acc_id)
                    log(f"  🔒 [写库 locked] 密码框未出现且封号: {email}")
                    return False
                log("  ⚠️  密码框未出现，可能已登录或被拦截，继续等待 token…")

            # ── 密码提交后检测封号 ──
            if await check_locked(page):
                poll_task.cancel()
                await browser.close()
                mark_locked(acc_id)
                log(f"  🔒 [写库 locked] 密码提交后封号: {email}")
                return False

            # 7. KMSI ("Stay signed in?")
            kmsi = page.locator('input[type="submit"][value*="Yes"], button:has-text("Yes"), button:has-text("是")').first
            if await kmsi.is_visible(timeout=4000):
                await kmsi.click()
                log("  ✅ 点击了 '保持登录'")
                await asyncio.sleep(2)

            # 8. 截图 + consent
            try:
                await page.screenshot(path=f"/tmp/retoken_{acc_id}_consent.png")
                log(f"  📸 截图: /tmp/retoken_{acc_id}_consent.png | URL: {page.url[:80]}")
            except Exception:
                pass

            # ── consent 前检测封号 ──
            if await check_locked(page):
                poll_task.cancel()
                await browser.close()
                mark_locked(acc_id)
                log(f"  🔒 [写库 locked] consent 前封号: {email}")
                return False

            try:
                await page.locator('[data-testid="appConsentPrimaryButton"]').click(timeout=12000)
                log("  ✅ 点击 appConsentPrimaryButton 同意授权")
                await asyncio.sleep(3)
            except Exception:
                log(f"  ℹ️  未检测到同意按钮，URL: {page.url[:80]}")

            # 9. 旧版 error div 兼容检测
            error_div = page.locator('[id*="error"], .alert-error, [aria-live="assertive"]').first
            if await error_div.is_visible(timeout=2000):
                err_text = await error_div.inner_text()
                log(f"  ⚠️  页面提示: {err_text[:120]}")
                if url_is_locked(page.url) or "锁定" in err_text or "Abuse" in err_text:
                    poll_task.cancel()
                    await browser.close()
                    mark_locked(acc_id)
                    log(f"  🔒 [写库 locked] error div 检测到封号: {email}")
                    return False

            log("  ⏳ 等待 token 轮询结果…")
            try:
                await asyncio.wait_for(token_done.wait(), timeout=120)
            except asyncio.TimeoutError:
                log("  ❌ 等待 token 超时")

        except Exception as e:
            log(f"  ❌ 浏览器操作异常: {e}")
        finally:
            # v8.22: token 已到手时, 捕获 storage_state + identity 写回 accounts
            # 解决 "老账号 retoken 永远不写 cookies/fp, 第二轮风控仍被锁" 隐患
            try:
                if token_done.is_set() and token_result[0] and token_result[0].get("access_token"):
                    _new_cookies_json = ""
                    try:
                        _state = await context.storage_state()
                        _new_cookies_json = json.dumps(_state, ensure_ascii=False)
                    except Exception as _se:
                        log(f"  ⚠ storage_state 抓取失败: {_se}")
                    # fingerprint: 优先保留 saved_fp, 否则用当前 _ctx_kwargs 合成最小指纹
                    if _saved_fp:
                        _fp_dict = _saved_fp
                    else:
                        _fp_dict = {
                            "user_agent": _ctx_kwargs.get("user_agent", ""),
                            "locale":     _ctx_kwargs.get("locale", "en-US"),
                            "timezone_id":_ctx_kwargs.get("timezone_id", "America/Los_Angeles"),
                            "_synthesized_at_retoken": True,
                        }
                        if _ctx_kwargs.get("viewport"): _fp_dict["viewport"] = _ctx_kwargs["viewport"]
                        if _ctx_kwargs.get("screen"):   _fp_dict["screen"]   = _ctx_kwargs["screen"]
                    _new_fp_json = json.dumps(_fp_dict, ensure_ascii=False)
                    _new_ua = _ctx_kwargs.get("user_agent", "") or saved_user_agent
                    try:
                        _uconn = db_conn()
                        _ucur  = _uconn.cursor()
                        _ucur.execute(
                            "UPDATE accounts SET "
                            "cookies_json    = CASE WHEN %s <> '' THEN %s ELSE cookies_json END, "
                            "fingerprint_json= CASE WHEN %s <> '' THEN %s ELSE fingerprint_json END, "
                            "user_agent      = CASE WHEN %s <> '' THEN %s ELSE user_agent END, "
                            "exit_ip         = CASE WHEN %s <> '' THEN %s ELSE exit_ip END, "
                            "proxy_port      = CASE WHEN %s > 0 THEN %s ELSE proxy_port END, "
                            "updated_at      = NOW() "
                            "WHERE id=%s",
                            (
                                _new_cookies_json, _new_cookies_json,
                                _new_fp_json, _new_fp_json,
                                _new_ua, _new_ua,
                                saved_exit_ip, saved_exit_ip,
                                saved_proxy_port, saved_proxy_port,
                                acc_id,
                            ),
                        )
                        _uconn.commit(); _uconn.close()
                        log(f"  💾 retoken 后写回 identity: cookies={len(_new_cookies_json)}B fp={len(_new_fp_json)}B ip={saved_exit_ip or 'vps_direct'}")
                    except Exception as _ue:
                        log(f"  ⚠ identity 写回 accounts 失败: {_ue}")
            except Exception as _ce:
                log(f"  ⚠ identity capture 异常: {_ce}")
            await browser.close()
            # v8.21: 关闭 IP 一致性专属 xray relay (避免端口/进程泄漏)
            if xray_relay_inst:
                try: xray_relay_inst.stop()
                except Exception: pass

    if not poll_task.done():
        poll_task.cancel()

    tok = token_result[0]
    if tok and tok.get("access_token"):
        save_tokens(acc_id, tok["access_token"], tok.get("refresh_token", ""))
        log(f"  ✅ Token 已保存！email={email}")
        return True
    else:
        log(f"  ❌ 未能获取 token")
        mark_failed(acc_id)
        return False


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids",         default="",   help="逗号分隔的账号 ID 列表")
    parser.add_argument("--all-error",   action="store_true", help="处理所有 status=error 的账号")
    parser.add_argument("--headless",    default="true", help="true/false")
    parser.add_argument("--proxy",       default="",   help="代理地址")
    parser.add_argument("--concurrency", type=int, default=1, help="并发数（建议1-3）")
    args = parser.parse_args()

    headless = args.headless.lower() != "false"
    ids_list = [int(x) for x in args.ids.split(",") if x.strip()] if args.ids else None

    accounts = get_accounts(ids_list, args.all_error)
    if not accounts:
        log("没有找到需要处理的账号（确保账号有密码）")
        return

    log(f"共 {len(accounts)} 个账号需要重新授权，并发={args.concurrency}")

    ok_count   = 0
    fail_count = 0
    sem = asyncio.Semaphore(args.concurrency)

    async def process_one(acc):
        nonlocal ok_count, fail_count
        async with sem:
            success = await retoken_account(acc, headless, args.proxy)
            if success:
                ok_count += 1
            else:
                fail_count += 1

    await asyncio.gather(*[process_one(a) for a in accounts])

    log(f"\n{'='*60}")
    log(f"完成！成功: {ok_count} / 失败: {fail_count} / 总计: {len(accounts)}")


if __name__ == "__main__":
    asyncio.run(main())
