#!/usr/bin/env python3
"""
Oxylabs.io 注册脚本 v5 - pydoll 版本 (CF auto-bypass)
"""
import asyncio, json, time, random, argparse

def log(msg: str):
    print(msg, flush=True)

def js_val(r) -> object:
    """Extract value from pydoll execute_script result dict."""
    if isinstance(r, dict):
        inner = r.get("result", r)
        if isinstance(inner, dict):
            inner2 = inner.get("result", inner)
            if isinstance(inner2, dict):
                return inner2.get("value")
    return r

FIRST_NAMES = ["James","John","Robert","Michael","William","David","Richard","Joseph",
               "Thomas","Charles","Christopher","Daniel","Matthew","Anthony","Mark"]
LAST_NAMES  = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis",
               "Rodriguez","Martinez","Hernandez","Lopez","Gonzalez","Wilson","Anderson"]

async def register_oxylabs(
    email: str, password: str,
    first_name: str = "", last_name: str = "",
    proxy: str = "", headless: bool = True
) -> dict:
    t0 = time.time()
    result = {
        "success": False, "email": email, "password": password,
        "first_name": first_name, "last_name": last_name,
        "username": "", "error": "", "elapsed": ""
    }
    if not first_name:
        first_name = random.choice(FIRST_NAMES); result["first_name"] = first_name
    if not last_name:
        last_name  = random.choice(LAST_NAMES);  result["last_name"]  = last_name

    from pydoll.browser import Chrome
    from pydoll.browser.options import ChromiumOptions

    log(f"✅ pydoll 已加载")
    log(f"📧 邮箱: {email}")
    log(f"👤 姓名: {first_name} {last_name}")
    if proxy: log(f"🌐 代理: {proxy[:60]}")
    log("🚀 启动 Chrome (pydoll CF bypass)...")

    options = ChromiumOptions()
    options.headless = headless
    options.binary_location = "/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome"
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1366,768")
    options.add_argument("--disable-gpu")
    fake_time = int(time.time()) - random.randint(7, 21) * 86400
    options.browser_preferences = {
        "profile": {
            "last_engagement_time": fake_time,
            "exit_type": "Normal",
            "exited_cleanly": True,
        },
        "intl": {"accept_languages": "en-US,en"},
    }
    options.webrtc_leak_protection = True
    if proxy:
        options.add_argument(f"--proxy-server={proxy}")

    async with Chrome(options=options) as browser:
        tab = await browser.start()

        # Enable CF auto-solve
        await tab.enable_auto_solve_cloudflare_captcha()
        log("✅ CF 自动绕过已启用")

        # Enable network events for API response capture
        await tab.enable_network_events()
        api_event   = asyncio.Event()
        api_data    = {}

        async def on_net_response(event):
            url    = event.get("params", {}).get("response", {}).get("url", "")
            if "/api/v1/users" in url:
                status = event.get("params", {}).get("response", {}).get("status", 0)
                req_id  = event.get("params", {}).get("requestId", "")
                api_data["status"]    = status
                api_data["requestId"] = req_id
                log(f"📡 /api/v1/users → HTTP {status}")
                api_event.set()

        await tab.on("Network.responseReceived", on_net_response)

        # Navigate + CF bypass
        log("🌐 打开注册页 (CF bypass)...")
        try:
            async with tab.expect_and_bypass_cloudflare_captcha():
                await tab.go_to("https://dashboard.oxylabs.io/registration", timeout=60)
            log("✅ 页面加载 + CF 绕过完成")
        except Exception as e:
            log(f"⚠ CF bypass 提示: {e} — 继续...")

        await asyncio.sleep(2)

        # Wait for React form
        log("⏳ 等待表单渲染...")
        form_ready = False
        for i in range(25):
            await asyncio.sleep(1)
            count_r = await tab.execute_script("document.querySelectorAll('input').length")
            count = js_val(count_r)
            if count and int(count) >= 3:
                log(f"✅ 表单就绪 t={i+1}s, inputs={count}")
                form_ready = True
                break
            if i % 5 == 4:
                log(f"  等待... t={i+1}s inputs={count}")

        if not form_ready:
            result["error"] = "表单未渲染"
            result["elapsed"] = f"{time.time()-t0:.1f}s"
            return result

        # Fill via React-compatible JS (setNativeValue + dispatch events)
        log("✏️ 填写表单 (React native value setter)...")
        fill_js = """
        (function(name, surname, email, password) {
            function reactSet(el, val) {
                var niv = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
                niv.call(el, val);
                el.dispatchEvent(new Event('input',  {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                el.dispatchEvent(new Event('blur',   {bubbles:true}));
            }
            var f = {};
            document.querySelectorAll('input').forEach(function(e){ if(e.name) f[e.name]=e; });
            var ok = [];
            if(f['name'])     { reactSet(f['name'],     name);     ok.push('name'); }
            if(f['surname'])  { reactSet(f['surname'],  surname);  ok.push('surname'); }
            if(f['email'])    { reactSet(f['email'],    email);    ok.push('email'); }
            if(f['password']) { reactSet(f['password'], password); ok.push('password'); }
            return ok.join(',');
        })
        """
        # pydoll execute_script with arguments
        filled_r = await tab.execute_script(
            f"({fill_js})({json.dumps(first_name)},{json.dumps(last_name)},{json.dumps(email)},{json.dumps(password)})"
        )
        filled = js_val(filled_r)
        log(f"  ✓ 已填写: {filled}")

        # Verify
        await asyncio.sleep(0.5)
        name_r  = await tab.execute_script("document.querySelector(\"input[name='name']\")?.value||''")
        email_r = await tab.execute_script("document.querySelector(\"input[name='email']\")?.value||''")
        name_v  = js_val(name_r)  or ""
        email_v = js_val(email_r) or ""
        log(f"  验证 name={name_v!r}, email={str(email_v)[:30]!r}")

        if not name_v or not email_v:
            log("⚠ 字段未持久化，尝试 keyboard 输入...")
            for fname, fval in [("name", first_name),("surname",last_name),
                                  ("email",email),("password",password)]:
                try:
                    click_r = await tab.execute_script(
                        f"var el=document.querySelector(\"input[name='{fname}']\"); if(el)el.focus(); el?'ok':'no'"
                    )
                    if js_val(click_r) == "ok":
                        await asyncio.sleep(0.2)
                        # Select all + type
                        await tab.keyboard.type(fval, interval=0.06)
                        await asyncio.sleep(0.3)
                        log(f"  ✓ {fname} via keyboard")
                except Exception as ex:
                    log(f"  ⚠ {fname}: {ex}")

        # Wait for SEON fingerprint
        log("⏳ 等待 SEON 指纹 (5s)...")
        await asyncio.sleep(5)

        # Screenshot
        try:
            await tab.take_screenshot("/tmp/oxylabs_pydoll_before.png")
            log("📸 提交前截图已保存")
        except Exception:
            pass

        # Submit
        log("🖱️ 提交表单...")
        submit_r = await tab.execute_script("""
        (function(){
            var btn = document.querySelector("button[type='submit']");
            if (!btn) btn = document.querySelector("button");
            if (btn) { btn.click(); return 'clicked:' + btn.textContent.trim().slice(0,20); }
            return 'no-btn';
        })()
        """)
        log(f"  ✓ {js_val(submit_r)}")

        # After submit, enable CF bypass for the resulting challenge
        log("⏳ 等待 /api/v1/users 响应 + CF 处理 (最多 90s)...")

        # While waiting for API event, also try to bypass any CF challenge that appears
        async def watchdog():
            for _ in range(45):
                await asyncio.sleep(2)
                # Check if CF challenge is active
                try:
                    txt_r = await tab.execute_script(
                        "document.body?.innerText?.substring(0,200)||''"
                    )
                    txt = (js_val(txt_r) or "").lower()
                    if any(s in txt for s in ["performing security","just a moment","checking your browser"]):
                        # Try pydoll CF bypass
                        try:
                            async with tab.expect_and_bypass_cloudflare_captcha():
                                pass  # just try to solve
                        except Exception:
                            pass
                except Exception:
                    pass

        watchdog_task = asyncio.create_task(watchdog())

        try:
            await asyncio.wait_for(api_event.wait(), timeout=90)
        except asyncio.TimeoutError:
            log("  ⚠ API 响应超时 90s")
        finally:
            watchdog_task.cancel()

        # Get API response body
        status = api_data.get("status", 0)
        req_id = api_data.get("requestId", "")

        if status:
            body_str = ""
            if req_id:
                try:
                    body = await tab.get_network_response_body(req_id)
                    body_str = json.dumps(body) if isinstance(body, dict) else str(body)
                    log(f"  响应体: {body_str[:200]}")
                except Exception as e:
                    log(f"  ⚠ 获取响应体失败: {e}")

            bl = body_str.lower()
            if status in (200, 201):
                result["success"] = True
                result["username"] = email.split("@")[0]
                log("✅ 注册成功!")
            elif status in (400, 422):
                if "already" in bl or "exist" in bl or "taken" in bl:
                    result["error"] = "邮箱已被注册"
                elif "password" in bl:
                    result["error"] = f"密码格式问题: {body_str[:100]}"
                else:
                    result["error"] = f"API {status}: {body_str[:150]}"
            elif status == 429:
                result["error"] = "请求频率限制 (429)"
            elif status == 403:
                result["error"] = f"CF 403 (Managed Challenge 未通过)"
            else:
                result["error"] = f"API HTTP {status}: {body_str[:100]}"
        else:
            # Fallback: check page state
            log("  ⚠ 未捕获 API 响应，检查页面内容...")
            await asyncio.sleep(3)
            try:
                cur_url = await tab.current_url
                txt_r   = await tab.execute_script("document.body?.innerText||''")
                txt     = js_val(txt_r) or ""
                bl      = txt.lower()
                log(f"  URL: {cur_url[-60:]}")
                log(f"  页面: {txt[:150]}")

                if any(s in bl for s in ["check your email","verify","sent","confirm","successfully"]):
                    result["success"] = True; result["username"] = email.split("@")[0]
                    log("✅ 成功消息检测")
                elif "/registration" not in cur_url and "/register" not in cur_url:
                    result["success"] = True; result["username"] = email.split("@")[0]
                    log(f"✅ URL 跳转: {cur_url}")
                elif "already" in bl:
                    result["error"] = "邮箱已被注册"
                else:
                    result["error"] = f"未检测到结果 | {txt[:120]}"
            except Exception as e:
                result["error"] = str(e)

        try:
            result["final_url"] = await tab.current_url
        except Exception:
            result["final_url"] = ""

    result["elapsed"] = f"{time.time()-t0:.1f}s"
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--email",    required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--first",    default="")
    ap.add_argument("--last",     default="")
    ap.add_argument("--proxy",    default="")
    ap.add_argument("--headless", default="true")
    args = ap.parse_args()
    headless = args.headless.lower() not in ("false","0","no")
    r = asyncio.run(register_oxylabs(
        args.email, args.password, args.first, args.last,
        args.proxy, headless
    ))
    print("\n── JSON 结果 ──")
    print(json.dumps(r, ensure_ascii=False, indent=2))
