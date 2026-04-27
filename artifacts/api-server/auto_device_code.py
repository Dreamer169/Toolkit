"""
自动完成 Microsoft 设备码授权流程
用法: python3 auto_device_code.py '[{"email":"...","password":"...","userCode":"...","accountId":44}]' [proxy]
输出: RESULTS:<json>，status 字段可为 done / suspended / error
"""
import asyncio, json, sys

MAX_CONCURRENCY = 1  # 同一出口代理串行授权，降低微软登录风控和页面超时

async def authorize_one(email: str, password: str, user_code: str, account_id: int, proxy: str = "", sem=None):
    from patchright.async_api import async_playwright
    result = {"accountId": account_id, "email": email, "status": "error", "msg": ""}

    launch_opts = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]}
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

            # 步骤1: 进入设备码授权页面
            await page.goto("https://www.microsoft.com/link", timeout=45000, wait_until="domcontentloaded")
            await asyncio.sleep(3)

            # 步骤2: 输入 user code
            code_input = await page.query_selector('input[name="otc"], input[placeholder*="code" i], input[id*="code" i], input[type="text"]')
            if not code_input:
                result["msg"] = "找不到验证码输入框"
                await browser.close()
                return result
            await code_input.fill(user_code)
            print(f"[{email}] 已输入 user code: {user_code}", flush=True)

            # 点击 Next/继续
            next_btn = await page.query_selector('button[type="submit"], input[type="submit"], button:has-text("Next"), button:has-text("下一步")')
            if next_btn:
                await next_btn.click()
            await asyncio.sleep(3)

            # 步骤3: 可能需要输入 email
            email_input = await page.query_selector('input[type="email"], input[name="loginfmt"]')
            if email_input:
                val = await email_input.input_value()
                if not val:
                    await email_input.fill(email)
                    print(f"[{email}] 填入邮箱", flush=True)
                btn = await page.query_selector('input[type="submit"], button[type="submit"]')
                if btn: await btn.click()
                await asyncio.sleep(4)

            # 步骤4: 输入密码
            pw_input = await page.query_selector('input[type="password"], input[name="passwd"]')
            if pw_input:
                await pw_input.fill(password)
                print(f"[{email}] 填入密码", flush=True)
                btn = await page.query_selector('input[type="submit"], button[type="submit"]')
                if btn: await btn.click()
                await asyncio.sleep(6)

            pw_input = await page.query_selector('input[type="password"], input[name="passwd"]')
            if pw_input:
                await pw_input.fill(password)
                btn = await page.query_selector('input[type="submit"], button[type="submit"]')
                if btn: await btn.click()
                await asyncio.sleep(6)

            # 步骤5: 保持登录弹窗
            stay_btn = await page.query_selector('button:has-text("Yes"), button:has-text("是"), input[value="Yes"]')
            if stay_btn:
                await stay_btn.click()
                await asyncio.sleep(2)

            # 步骤6: 授权确认页面（"XXX wants access..."）
            confirm_btn = await page.query_selector(
                'button:has-text("Continue"), button:has-text("继续"), '
                'button:has-text("Accept"), button:has-text("接受"), '
                'button:has-text("Allow"), button:has-text("允许"), '
                'input[value="Continue"], input[value="Accept"]'
            )
            if confirm_btn:
                await confirm_btn.click()
                print(f"[{email}] 点击授权确认", flush=True)
                await asyncio.sleep(3)

            final_url = page.url
            content = await page.content()

            # 检测账号封号/滥用页面
            abuse_signals = [
                "account.live.com/Abuse" in final_url,
                "account.live.com/recover" in final_url.lower(),
                "/Abuse" in final_url,
                "account has been suspended" in content.lower(),
                "account is suspended" in content.lower(),
                "account is temporarily locked" in content.lower(),
                "your account has been" in content.lower() and "locked" in content.lower(),
            ]
            if any(abuse_signals):
                result["status"] = "suspended"
                result["msg"] = f"账号已被微软封禁: {final_url[:100]}"
                print(f"[{email}] 🚫 账号已被封禁，URL: {final_url[:80]}", flush=True)
            else:
                # v8.38 ROOT-FIX: success_signals 太宽松, 字串 "signed in" 会被 microsoft.com
                # 通用页 chrome (导航/页脚/aria-label) 误命中 → 浏览器仅 8s 就回报 done
                # → device_code 真正 consent 未提交 → 后端 pollForToken 90s 全部 timeout
                # → 帐号永久卡在 needs_oauth_manual.
                # 改为: 仅当 URL 是真正的 device-flow 完成端点 + 内容含明确完成文案时才认 done.
                _u = (final_url or "").lower()
                _c = content.lower()
                _has_error_qs = ("error=" in _u) or ("error_description=" in _u)
                _is_done_endpoint = (
                    ("oauth20_remoteconnect.srf" in _u and not _has_error_qs)
                    or ("login.microsoftonline.com/common/oauth2/deviceauth" in _u and not _has_error_qs)
                    or ("/devicelogin/complete" in _u)
                )
                _has_explicit_done_text = (
                    "device login is complete" in _c
                    or "you have signed in" in _c
                    or "you can now close this window" in _c
                    or "you can close this window" in _c
                    or "可以关闭此窗口" in content
                    or "已经登录" in content
                    or "登录成功" in content
                    or "授权已完成" in content
                )
                # 同时满足 (终端 URL) 或 (终端 URL+ 完成文案), 才算真完成
                # 防止落到 microsoft.com 主页/marketing 页 chrome 含 "signed in" 误判
                if _is_done_endpoint and (_has_explicit_done_text or "oauth20_remoteconnect.srf" in _u):
                    result["status"] = "done"
                    result["msg"] = "授权成功"
                    print(f"[{email}] ✅ 授权成功！URL={final_url[:120]}", flush=True)
                else:
                    result["status"] = "error"
                    result["msg"] = f"最终页面非完成端点: {final_url[:120]}"
                    print(f"[{email}] ⚠ 授权未完成 (final_url={final_url[:120]} done_endpoint={_is_done_endpoint} done_text={_has_explicit_done_text})", flush=True)

            await browser.close()
    except Exception as e:
        result["msg"] = str(e)
        print(f"[{email}] ❌ 异常: {e}", flush=True)
    finally:
        if sem:
            sem.release()

    return result

async def main():
    accounts = json.loads(sys.argv[1])
    proxy = sys.argv[2] if len(sys.argv) > 2 else ""
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    tasks = [authorize_one(a["email"], a["password"], a["userCode"], a.get("accountId", 0), proxy, sem) for a in accounts]
    results = await asyncio.gather(*tasks)
    print("RESULTS:" + json.dumps(results, ensure_ascii=False), flush=True)

    # 统计
    done = sum(1 for r in results if r["status"] == "done")
    suspended = sum(1 for r in results if r["status"] == "suspended")
    errors = sum(1 for r in results if r["status"] == "error")
    print(f"[summary] 成功={done} 封禁={suspended} 错误={errors}", flush=True)

asyncio.run(main())
