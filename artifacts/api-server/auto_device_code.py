"""
自动完成 Microsoft 设备码授权流程
v8.96 修复:
  - [根因修复] token 兑换 + DB 写入移进 Python 同一 CF proxy 环境。
    之前 close handler 在 Node.js（VPS 直连 45.205.27.69）兑换 token，
    MS 检测到 auth IP != token IP → 触发「New app(s) connected」安全邮件。
    现在浏览器授权 + token POST + DB INSERT 全在同一个 CF proxy 出口 IP 上。
  - 兑换逻辑: httpx AsyncClient + socks5 proxy → MS /oauth2/v2.0/token
  - DB 写入: psycopg2 写 localhost PostgreSQL（DB 在本地，无需走代理）
  - payload 每项新增可选字段 deviceCode / dbUrl（不传则跳过 token 兑换）
  - React-safe 邮箱填写: nativeInputValueSetter + dispatchEvent
  - 自动 CF IP 代理: 无 proxy 参数时从 /tmp/cf_pool_state.json 随机取
"""
import asyncio, json, sys, os, random

MAX_CONCURRENCY = 8
CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
TOKEN_ENDPOINT = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
DB_URL = "postgresql://postgres:postgres@localhost/toolkit"

SKIP_SELECTORS = [
    'button:has-text("Skip for now")',
    'button:has-text("Maybe later")',
    'button:has-text("Not now")',
    'a:has-text("Skip for now")',
    'a:has-text("Maybe later")',
    'input[type="submit"][value="Skip for now"]',
    'input[type="submit"][value="Maybe later"]',
    'input[type="submit"][value="Not now"]',
    'button:has-text("Skip setup")',
    'button:has-text("Set up later")',
    'a:has-text("Skip setup")',
]


def _pick_cf_proxy() -> str:
    try:
        import json as _j
        _ps = _j.load(open('/tmp/cf_pool_state.json'))
        _avail = [x['ip'] for x in _ps.get('available', []) if isinstance(x, dict) and x.get('ip')]
        if not _avail:
            return ""
        _ip = random.choice(_avail[:30])
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from xray_relay import XrayRelay as _XR
        _relay = _XR(_ip)
        if _relay.start(timeout=8.0):
            _ACTIVE_RELAYS.append(_relay)
            print(f"[cf-proxy] CF IP={_ip} SOCKS5 port={_relay.socks_port}", flush=True)
            return _relay.socks5_url
        _relay.stop()
    except Exception as _e:
        print(f"[cf-proxy] 启动失败: {_e}", flush=True)
    return ""


_ACTIVE_RELAYS: list = []


async def _exchange_token_and_save(
    device_code: str,
    account_id: int,
    email: str,
    proxy: str,
    db_url: str = DB_URL,
    remove_tag: str = "",
) -> bool:
    """
    v8.96: 通过同一个 CF proxy 向 MS 兑换 access_token，再写入本地 DB。
    返回 True 表示成功入库。
    """
    import httpx

    # 构造 proxy_url (httpx 格式)
    proxy_url = proxy if proxy else None

    access_token = ""
    refresh_token_str = ""
    last_err = ""
    deadline = asyncio.get_event_loop().time() + 120  # 最多等 120s

    print(f"[{email}] v8.96 token exchange via proxy={proxy_url or 'DIRECT'}", flush=True)

    transport_kwargs: dict = {}
    if proxy_url:
        try:
            transport_kwargs["transport"] = httpx.AsyncHTTPTransport(
                proxy=proxy_url
            )
        except Exception:
            # httpx 旧版 API
            transport_kwargs["proxies"] = {"all://": proxy_url}

    async with httpx.AsyncClient(timeout=20, **transport_kwargs) as client:
        while asyncio.get_event_loop().time() < deadline:
            try:
                resp = await client.post(
                    TOKEN_ENDPOINT,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "client_id": CLIENT_ID,
                        "device_code": device_code,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                data = resp.json()
                if data.get("access_token"):
                    access_token = data["access_token"]
                    refresh_token_str = data.get("refresh_token", "")
                    print(f"[{email}] ✅ token 兑换成功 (via CF proxy) access={len(access_token)}B", flush=True)
                    break
                err = data.get("error", "no_token")
                last_err = f"{err}: {data.get('error_description','')[:80]}"
                if err not in ("authorization_pending", "slow_down"):
                    print(f"[{email}] ❌ token 兑换终止: {last_err}", flush=True)
                    return False
            except Exception as e:
                last_err = str(e)[:100]
            await asyncio.sleep(2)

    if not access_token:
        print(f"[{email}] ❌ token 120s 内未拿到: {last_err}", flush=True)
        return False

    # 写入 DB（本地 psycopg2，无需走代理）
    try:
        import psycopg2  # type: ignore
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        if remove_tag:
            cur.execute(
                """UPDATE accounts
                      SET token=%s, refresh_token=%s, status='active', updated_at=NOW(),
                          tags = NULLIF(TRIM(BOTH ',' FROM
                            REGEXP_REPLACE(COALESCE(tags,''), '(^|,?)" + remove_tag + "(,|$)', ',', 'g')
                          ), ',')
                    WHERE id=%s""",
                (access_token, refresh_token_str, account_id),
            )
        else:
            cur.execute(
                """UPDATE accounts
                      SET token=%s, refresh_token=%s, status='active', updated_at=NOW()
                    WHERE id=%s""",
                (access_token, refresh_token_str, account_id),
            )

        # 同步写 archives
        cur.execute(
            """UPDATE archives
                  SET token=%s, refresh_token=%s, status='active', updated_at=NOW()
                WHERE platform='outlook' AND email=%s""",
            (access_token, refresh_token_str, email),
        )
        conn.commit()
        cur.close()
        conn.close()
        print(f"[{email}] ✅ token 已写入 DB (id={account_id})", flush=True)
        return True
    except Exception as e:
        print(f"[{email}] ⚠ DB 写入失败: {e}", flush=True)
        return False


async def _safe_shot(page, email: str, step: str):
    try:
        fn = f"/tmp/dc_{email.split('@')[0]}_{step}.png"
        await page.screenshot(path=fn, full_page=False)
    except Exception:
        pass


async def _skip_if_security_page(page, email: str) -> bool:
    for sel in SKIP_SELECTORS:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.click()
                print(f"[{email}] skip security: {sel}", flush=True)
                await asyncio.sleep(3)
                return True
        except Exception:
            pass
    return False


def _is_real_done(_u: str, _c: str) -> bool:
    if "action=remoteconnectcomplete" in _u: return True
    if "/devicelogin/complete" in _u: return True
    if "remoteconnect.srf" in _u and "remoteconnectcomplete" in _u: return True
    if any(t in _c for t in [
        "device login is complete", "you have signed in",
        "you can now close this window", "you can close this window",
        "you are now signed in", "signed in to", "you signed in to",
        "access was granted", "authorization complete",
    ]): return True
    if any(t in _c for t in ["可以关闭此窗口", "已经登录", "登录成功", "授权已完成",
        "你已登录", "已授权", "登录成功"]): return True
    return False


async def _poll_real_done(page, email: str, max_wait: int = 30) -> bool:
    for i in range(max_wait // 2):
        await asyncio.sleep(2)
        try:
            _u = (page.url or "").lower()
            _c = (await page.content()).lower()
        except Exception:
            continue
        if _is_real_done(_u, _c):
            print(f"[{email}] real_done tick={i} url={page.url[:100]}", flush=True)
            return True
        await _skip_if_security_page(page, email)
    return False


async def _react_safe_fill_email(page, email: str) -> bool:
    try:
        filled = await page.evaluate(r"""(email) => {
            const inp = document.querySelector('input[name="loginfmt"], input[type="email"]');
            if (!inp) return false;
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(inp, email);
            inp.dispatchEvent(new Event('input',  {bubbles: true}));
            inp.dispatchEvent(new Event('change', {bubbles: true}));
            return true;
        }""", email)
        return bool(filled)
    except Exception:
        return False


async def authorize_one(email: str, password: str, user_code: str, account_id: int,
                        proxy: str = "", sem=None,
                        device_code: str = "", db_url: str = DB_URL,
                        remove_tag: str = ""):
    from patchright.async_api import async_playwright
    result = {"accountId": account_id, "email": email, "status": "error", "msg": ""}

    _auto_proxy = ""
    if not proxy:
        _auto_proxy = _pick_cf_proxy()
        if _auto_proxy:
            proxy = _auto_proxy
            print(f"[{email}] 使用 CF pool 代理: {_auto_proxy}", flush=True)
        else:
            print(f"[{email}] ⚠ 无 CF proxy，VPS 直连（风险高）", flush=True)

    launch_opts = {
        "headless": True,
        "args": ["--no-sandbox", "--disable-dev-shm-usage",
                 "--disable-blink-features=AutomationControlled"],
    }
    ctx_opts = {}
    if proxy:
        ctx_opts["proxy"] = {"server": proxy}

    if sem:
        await sem.acquire()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(**launch_opts)
            ctx = await browser.new_context(**ctx_opts)
            page = await ctx.new_page()

            await page.goto("https://microsoft.com/devicelogin",
                            timeout=45000, wait_until="domcontentloaded")
            await asyncio.sleep(3)
            await _safe_shot(page, email, "01_start")
            print(f"[{email}] 01 url={page.url[:100]}", flush=True)

            # Step 1: 输入 user_code
            code_input = await page.query_selector(
                'input[name="otc"], input[placeholder*="code" i], '
                'input[id*="code" i], input[type="text"]'
            )
            if not code_input:
                result["msg"] = "no code input"
                await browser.close()
                return result
            await code_input.fill(user_code)
            print(f"[{email}] user_code={user_code}", flush=True)
            next_btn = await page.query_selector(
                'button[type="submit"], input[type="submit"], '
                'button:has-text("Next"), button:has-text("下一步")'
            )
            if next_btn:
                await next_btn.click()
            await asyncio.sleep(3)
            await _safe_shot(page, email, "02_code")

            # v8.90: detect expired/invalid user_code
            try:
                _body02 = await page.inner_text("body")
                _bad = ["That code didn work", "code didn work",
                        "Check the code and try again", "该代码无效", "此代码无效"]
                if any(x in _body02 for x in _bad):
                    print(f"[{email}] code_invalid user_code={user_code}", flush=True)
                    result["msg"] = "code_invalid_or_expired"
                    await browser.close()
                    return result
            except Exception:
                pass

            # Step 2: 填入邮箱 (React-safe)
            _email_el = await page.query_selector('input[type="email"], input[name="loginfmt"]')
            if _email_el:
                _cur_val = ""
                try:
                    _cur_val = await _email_el.input_value()
                except Exception:
                    pass
                if not _cur_val:
                    _filled = await _react_safe_fill_email(page, email)
                    if not _filled:
                        await _email_el.fill(email)
                    await asyncio.sleep(0.4)
                    print(f"[{email}] 📧 填入邮箱(React-safe={_filled}): {email}", flush=True)

                _submitted = False
                for _sel in [
                    'button[data-testid="primaryButton"]',
                    'input[id="idSIButton9"]',
                    'input[type="submit"]',
                    'button[type="submit"]',
                ]:
                    try:
                        _b = await page.query_selector(_sel)
                        if _b and await _b.is_visible():
                            await _b.click()
                            _submitted = True
                            print(f"[{email}] 📤 邮箱提交: {_sel}", flush=True)
                            break
                    except Exception:
                        continue
                if not _submitted:
                    await _email_el.press('Enter')
                    print(f"[{email}] 📤 邮箱提交: Enter键", flush=True)

                try:
                    await page.wait_for_load_state('networkidle', timeout=12000)
                except Exception:
                    await asyncio.sleep(5)

                try:
                    _still_email = await page.locator(
                        'input[name="loginfmt"], input[type="email"]'
                    ).first.is_visible(timeout=2000)
                except Exception:
                    _still_email = False

                if _still_email:
                    print(f"[{email}] ⚠ 邮箱提交后页面未跳转 URL={page.url[:100]} — 可能IP被MS拒", flush=True)
                    await _react_safe_fill_email(page, email)
                    await asyncio.sleep(0.3)
                    for _sel2 in ['button[data-testid="primaryButton"]', 'input[type="submit"]',
                                  'button[type="submit"]']:
                        try:
                            _b2 = await page.query_selector(_sel2)
                            if _b2 and await _b2.is_visible():
                                await _b2.click()
                                print(f"[{email}] 📤 邮箱二次提交: {_sel2}", flush=True)
                                break
                        except Exception:
                            continue
                    try:
                        await page.wait_for_load_state('networkidle', timeout=12000)
                    except Exception:
                        await asyncio.sleep(5)

            await _safe_shot(page, email, "03_email")

            # Step 3: 密码
            for _pw in range(3):
                pw_input = await page.query_selector(
                    'input[type="password"], input[name="passwd"]')
                if pw_input and await pw_input.is_visible():
                    await pw_input.fill(password)
                    print(f"[{email}] 🔒 密码 attempt {_pw+1}", flush=True)
                    btn = await page.query_selector(
                        'input[type="submit"], button[type="submit"]')
                    if btn:
                        await btn.click()
                    try:
                        await page.wait_for_load_state('networkidle', timeout=12000)
                    except Exception:
                        await asyncio.sleep(6)
                    break
                elif _pw < 2:
                    await asyncio.sleep(4)
                    await _skip_if_security_page(page, email)

            await _safe_shot(page, email, "04_password")
            print(f"[{email}] 04 url={page.url[:120]}", flush=True)

            # Step 4: Skip 安全页 / KMSI
            for _ in range(4):
                if not await _skip_if_security_page(page, email):
                    break

            kmsi_sels = [
                'input[type="submit"][value="Yes"]',
                'input[type="submit"][value="是"]',
                'button:has-text("Yes")',
                'button:has-text("是")',
                '#idSIButton9',
            ]
            for sel in kmsi_sels:
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        _txt = ""
                        try:
                            _txt = (await btn.inner_text() or "").lower()
                        except Exception:
                            pass
                        if "password" not in _txt and "forgot" not in _txt:
                            await btn.click()
                            print(f"[{email}] KMSI {sel}", flush=True)
                            await asyncio.sleep(3)
                            break
                except Exception:
                    continue
            await _safe_shot(page, email, "05_kmsi")
            print(f"[{email}] 05 url={page.url[:120]}", flush=True)

            for _ in range(4):
                if not await _skip_if_security_page(page, email):
                    break

            # Step 5: Device-confirm
            for _dc_i in range(3):
                try:
                    _dc_body = (await page.inner_text("body")).lower()
                    _dc_url = (page.url or "").lower()
                    _is_dc = (
                        ("signing in to" in _dc_body or "on another device" in _dc_body
                         or "signing in on" in _dc_body)
                        and "deviceauth" in _dc_url
                    )
                    if not _is_dc:
                        break
                    _confirmed = False
                    for _cs in [
                        'input[type="submit"]', 'button[type="submit"]',
                        '#idSIButton9', '[data-testid="primaryButton"]',
                        'form button',
                    ]:
                        try:
                            _cb = await page.query_selector(_cs)
                            if _cb and await _cb.is_visible():
                                await _cb.click()
                                print(f"[{email}] device-confirm {_cs}", flush=True)
                                await asyncio.sleep(5)
                                _confirmed = True
                                break
                        except Exception:
                            continue
                    if not _confirmed:
                        break
                except Exception:
                    break

            # Step 6: Consent / Continue
            consent_sels = [
                'input[type="submit"][value="Continue"]',
                'input[type="submit"][value="Accept"]',
                'button:has-text("Continue")',
                'button:has-text("继续")',
                'button:has-text("Accept")',
                'button:has-text("接受")',
                'button:has-text("Allow")',
                'button:has-text("允许")',
                'button:has-text("Approve")',
                '[data-testid="primaryButton"]',
            ]

            _real_done = False
            _consent_clicked = False

            try:
                if _is_real_done((page.url or "").lower(),
                                 (await page.content()).lower()):
                    _real_done = True
                    print(f"[{email}] done before consent loop", flush=True)
            except Exception:
                pass

            if not _real_done:
                for _retry in range(3):
                    await _skip_if_security_page(page, email)
                    cur_url = page.url or ""
                    print(f"[{email}] consent r={_retry} url={cur_url[:100]}", flush=True)

                    _on_device = any(x in cur_url.lower() for x in [
                        "remoteconnect", "deviceauth", "microsoft.com/link", "microsoft.com/devicelogin",
                        "login.microsoftonline.com", "account.live.com/abuse",
                    ]) or cur_url == ""

                    _clicked = False
                    if _on_device:
                        for sel in consent_sels:
                            try:
                                btn = await page.query_selector(sel)
                                if btn and await btn.is_visible():
                                    await btn.click()
                                    print(f"[{email}] consent click r={_retry} {sel}", flush=True)
                                    try:
                                        await page.wait_for_load_state("networkidle", timeout=12000)
                                    except Exception:
                                        await asyncio.sleep(10)
                                    _consent_clicked = True
                                    _clicked = True
                                    await _safe_shot(page, email, f"06_consent_{_retry}")
                                    for _pwstep in range(3):
                                        _pw2 = await page.query_selector(
                                            'input[type="password"], input[name="passwd"]')
                                        if _pw2 and await _pw2.is_visible():
                                            await _pw2.fill(password)
                                            print(f"[{email}] pw after consent", flush=True)
                                            _sbtn = await page.query_selector(
                                                'input[type="submit"], button[type="submit"]')
                                            if _sbtn: await _sbtn.click()
                                            try:
                                                await page.wait_for_load_state("networkidle", timeout=10000)
                                            except Exception:
                                                await asyncio.sleep(6)
                                            for _ksel in ['#idSIButton9', 'input[type="submit"][value="Yes"]',
                                                          '[data-testid="primaryButton"]']:
                                                try:
                                                    _kb = await page.query_selector(_ksel)
                                                    if _kb and await _kb.is_visible():
                                                        _kbt = (await _kb.inner_text())[:30]
                                                        if 'password' not in _kbt.lower() and 'forgot' not in _kbt.lower():
                                                            await _kb.click()
                                                            print(f"[{email}] kmsi2 {_ksel}", flush=True)
                                                            await asyncio.sleep(4)
                                                            break
                                                except Exception:
                                                    pass
                                        else:
                                            break
                                    break
                            except Exception:
                                continue
                    else:
                        print(f"[{email}] not device url skip consent", flush=True)

                    _real_done = await _poll_real_done(page, email, max_wait=20)
                    if _real_done:
                        break

                    if not _clicked:
                        await asyncio.sleep(4)
                        try:
                            if _is_real_done((page.url or "").lower(),
                                            (await page.content()).lower()):
                                _real_done = True
                                break
                        except Exception:
                            pass

                if not _real_done:
                    await asyncio.sleep(10)
                    try:
                        if _is_real_done((page.url or "").lower(),
                                        (await page.content()).lower()):
                            _real_done = True
                            print(f"[{email}] done fallback 10s", flush=True)
                    except Exception:
                        pass

            await _safe_shot(page, email, "07_final")
            final_url = page.url
            print(f"[{email}] final url={final_url[:140]} real_done={_real_done} "
                  f"consent={_consent_clicked}", flush=True)
            try:
                body = await page.inner_text("body")
                print(f"[{email}] body(400): {body[:400].replace(chr(10),' ')}", flush=True)
            except Exception:
                pass

            try:
                content = await page.content()
            except Exception:
                content = ""
            _on_abuse_page = ("account.live.com/Abuse" in final_url or "/Abuse" in final_url)
            _body_low = content.lower()
            _real_suspended_body = (
                "account has been suspended" in _body_low
                or "account is suspended" in _body_low
                or "account is temporarily locked" in _body_low
                or "account has been locked" in _body_low
                or "your account has been blocked" in _body_low
            )
            if _on_abuse_page and not _real_suspended_body:
                _captcha_handled = False
                try:
                    _hold_btn = await page.query_selector(
                        "#hold-button, .px-captcha-button, [id*=hold], button[class*=captcha]"
                    )
                    if _hold_btn and await _hold_btn.is_visible():
                        print(f"[{email}] abuse_captcha: attempting press-hold", flush=True)
                        _box = await _hold_btn.bounding_box()
                        if _box:
                            _cx = _box["x"] + _box["width"] / 2
                            _cy = _box["y"] + _box["height"] / 2
                            await page.mouse.move(_cx, _cy)
                            await page.mouse.down()
                            await asyncio.sleep(3.5)
                            await page.mouse.up()
                            await asyncio.sleep(4)
                            _after_url = page.url
                            print(f"[{email}] after press-hold url={_after_url[:100]}", flush=True)
                            if "/Abuse" not in _after_url and "account.live.com/Abuse" not in _after_url:
                                _captcha_handled = True
                                _real_done = await _poll_real_done(page, email, max_wait=20)
                except Exception as _ce:
                    print(f"[{email}] abuse_captcha press-hold failed: {_ce}", flush=True)
                if not _captcha_handled:
                    result["status"] = "error"
                    result["msg"] = f"abuse_captcha_required (retryable): {final_url[:100]}"
                    print(f"[{email}] Abuse page=CAPTCHA not suspension -> error(retryable)", flush=True)
            elif _real_suspended_body:
                result["status"] = "suspended"
                result["msg"] = f"suspended: {final_url[:100]}"
            elif _real_done or _consent_clicked:
                result["status"] = "done"
                result["msg"] = "authorized"
                print(f"[{email}] 浏览器授权完成，开始 token 兑换 (同 CF proxy)...", flush=True)

                # v8.96 核心修复: token 兑换在同一 CF proxy 出口 IP 进行
                if device_code:
                    _tok_saved = await _exchange_token_and_save(
                        device_code=device_code,
                        account_id=account_id,
                        email=email,
                        proxy=proxy,
                        db_url=db_url or DB_URL,
                        remove_tag=remove_tag,
                    )
                    result["token_saved"] = _tok_saved
                    if not _tok_saved:
                        result["msg"] += " (token exchange failed, see log)"
                else:
                    print(f"[{email}] ⚠ 无 deviceCode，跳过 token 兑换（由 Node.js 兑换）", flush=True)
            else:
                result["status"] = "error"
                result["msg"] = f"no consent reached: {final_url[:120]}"

            await browser.close()

    except Exception as e:
        result["msg"] = str(e)
        print(f"[{email}] exception: {e}", flush=True)
    finally:
        if sem:
            sem.release()

    return result


async def main():
    accounts = json.loads(sys.argv[1])
    proxy = sys.argv[2] if len(sys.argv) > 2 else ""
    db_url = sys.argv[3] if len(sys.argv) > 3 else DB_URL
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    tasks = [
        authorize_one(
            a["email"], a["password"], a["userCode"],
            a.get("accountId", 0), proxy, sem,
            device_code=a.get("deviceCode", ""),
            db_url=a.get("dbUrl", db_url),
            remove_tag=a.get("removeTag", ""),
        )
        for a in accounts
    ]
    results = await asyncio.gather(*tasks)
    print("RESULTS:" + json.dumps(results, ensure_ascii=False), flush=True)
    done = sum(1 for r in results if r["status"] == "done")
    suspended = sum(1 for r in results if r["status"] == "suspended")
    errors = sum(1 for r in results if r["status"] == "error")
    print(f"[summary] done={done} suspended={suspended} errors={errors}", flush=True)


asyncio.run(main())
