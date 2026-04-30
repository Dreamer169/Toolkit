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

            # 步骤5: 保持登录弹窗 (KMSI) — v8.74 改 wait_for_selector 给渲染时间
            kmsi_selectors = [
                'input[type="submit"][value="Yes"]',
                'input[type="submit"][value="是"]',
                'button:has-text("Yes")',
                'button:has-text("是")',
                '#idSIButton9',
            ]
            for _kmsi_sel in kmsi_selectors:
                try:
                    _kbtn = await page.query_selector(_kmsi_sel)  # v8.87 Bug L: 即时返回, 不 4s 阻塞
                    if _kbtn:
                        await _kbtn.click()
                        print(f"[{email}] ✅ KMSI 点 Yes: {_kmsi_sel}", flush=True)
                        await asyncio.sleep(3)
                        break
                except Exception:
                    continue

            # 步骤6: 授权确认页面（"XXX wants access..."） — v8.74 wait_for_selector 多重重试
            consent_selectors = [
                'input[type="submit"][value="Continue"]',
                'input[type="submit"][value="Accept"]',
                'input[type="submit"][value="Yes"]',
                'button:has-text("Continue")',
                'button:has-text("继续")',
                'button:has-text("Accept")',
                'button:has-text("接受")',
                'button:has-text("Allow")',
                'button:has-text("允许")',
                'button:has-text("Approve")',
                'button:has-text("Yes")',
                '#idSIButton9',
                '[data-testid="primaryButton"]',
            ]
            # v8.84 Bug F: 严格终态判定 = action=remoteConnectComplete OR 显式完成文案
            # oauth20_remoteconnect.srf 是 device-flow BASE URL, 包含 enterCode/login/consentApproval/complete 所有阶段,
            # 不能当终态指标. 真终态: ?action=remoteConnectComplete (服务端跳转) OR 页面显式说"已登录/可以关闭".
            def _is_real_done(_u: str, _c: str) -> bool:
                _ul = (_u or "").lower(); _cl = (_c or "").lower()
                if "action=remoteconnectcomplete" in _ul: return True
                if "/devicelogin/complete" in _ul: return True
                if any(t in _cl for t in ["device login is complete","you have signed in","you can now close this window","you can close this window"]): return True
                if any(t in _c for t in ["可以关闭此窗口","已经登录","登录成功","授权已完成"]): return True
                return False

            # v8.88 Bug M: consent 一旦点成功立刻 break, 不再过度点
            # (#idSIButton9 是 MS 通用 primary, 多次点会把"完成页"按钮也点了→跳回起始页).
            _consent_clicked_once = False
            _real_done = False
            for _retry in range(3):
                _round_clicked = False
                for _csel in consent_selectors:
                    try:
                        _btn_found = await page.query_selector(_csel)
                        if _btn_found:
                            _is_visible = await _btn_found.is_visible()
                            if _is_visible:
                                await _btn_found.click()
                                print(f"[{email}] ✅ Consent 点击 ({_retry+1}/3): {_csel}", flush=True)
                                await asyncio.sleep(6)
                                _consent_clicked_once = True
                                _round_clicked = True
                                break
                    except Exception:
                        continue
                # 真终态优先检查
                try:
                    _content_now = await page.content()
                except Exception:
                    _content_now = ""
                if _is_real_done(page.url or "", _content_now):
                    _real_done = True
                    print(f"[{email}] ✅ 真终态 round={_retry+1}", flush=True)
                    break
                # v8.88: 本轮成功 click → 立即 break, 让外层判定 (避免 round2/3 过度点)
                if _round_clicked:
                    print(f"[{email}] ✓ consent 已点, 跳出 loop 让后端 pollForToken 验证", flush=True)
                    break
                # 没找到按钮 → 等 2.5s 重试 (页面可能还在渲染)
                await asyncio.sleep(2.5)

            # v8.84: 循环结束后再 8s 兜底等待 + 二次读 URL/content (有时 MS 跳转延迟)
            if not _real_done:
                await asyncio.sleep(8)
                try:
                    _final_content = await page.content()
                except Exception:
                    _final_content = ""
                if _is_real_done(page.url or "", _final_content):
                    _real_done = True
                    print(f"[{email}] ✅ 兜底等待后检测到真终态 URL={(page.url or '')[:140]}", flush=True)
            print(f"[{email}] consent 阶段完成, real_done={_real_done} final={(page.url or '')[:140]}", flush=True)

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
                # v8.84 Bug F: 移除 "oauth20_remoteconnect.srf in _u" OR 短路漏洞.
                _has_done_action = "action=remoteconnectcomplete" in _u
                _has_devicelogin_complete = "/devicelogin/complete" in _u
                # v8.88 Bug M: _consent_clicked_once=True 也认 "done" (乐观)
                # 真终态 URL 检测在 SPA/重定向场景常 miss, 但 consent 已点 → 设备码大概率被消费,
                # 后端 pollForToken 90s 内会真校验 (拿到 token=真成功, 90s 超时=真失败再标 manual).
                if (_real_done or _has_done_action or _has_devicelogin_complete or _has_explicit_done_text
                    or _consent_clicked_once):
                    result["status"] = "done"
                    result["msg"] = "consent 已点 (待 pollForToken 校验)" if _consent_clicked_once and not _real_done else "授权成功"
                    print(f"[{email}] ✅ 返回 done (consent_clicked={_consent_clicked_once} real_done={_real_done}) URL={final_url[:140]}", flush=True)
                else:
                    result["status"] = "error"
                    result["msg"] = f"未检测到 consent 按钮: {final_url[:120]}"
                    print(f"[{email}] ⚠ 未点过 consent (final_url={final_url[:120]})", flush=True)

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
