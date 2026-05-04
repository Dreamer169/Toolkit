#!/usr/bin/env python3
"""
oxylabs_vnc_session.py — noVNC interactive Oxylabs registration
Architecture: user sees Chrome via noVNC, interacts to bypass CF, automation fills form
"""
import os, sys, time, json, subprocess, argparse, signal
os.environ["DISPLAY"] = ":99"

VNC_PORT  = 5900
WS_PORT   = 6080
NOVNC_DIR = "/usr/share/novnc"
REAL_CHROME = "/opt/google/chrome/google-chrome"

t0 = time.time()
_procs = []

def log(msg): print(msg, flush=True)

def kill_services():
    for s in ["x11vnc", "websockify"]:
        subprocess.run(["pkill", "-9", s], capture_output=True)
    subprocess.run(["pkill", "-9", "-f", "remote-debugging-port=9300"], capture_output=True)
    time.sleep(0.8)

def start_vnc():
    p1 = subprocess.Popen([
        "x11vnc", "-display", ":99", "-nopw",
        "-listen", "127.0.0.1", "-rfbport", str(VNC_PORT),
        "-forever", "-shared", "-bg", "-quiet",
        "-o", "/tmp/x11vnc_oxy.log"
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _procs.append(p1)
    time.sleep(1.0)

    p2 = subprocess.Popen([
        "python3", "-m", "websockify",
        "--web", NOVNC_DIR,
        str(WS_PORT), f"127.0.0.1:{VNC_PORT}"
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _procs.append(p2)
    time.sleep(1.0)
    log(f"[VNC] x11vnc:{VNC_PORT} + websockify:{WS_PORT} ready")

def cleanup(sig=None, frame=None):
    for p in _procs:
        try: p.terminate()
        except: pass
    subprocess.run(["pkill", "-9", "x11vnc"], capture_output=True)
    subprocess.run(["pkill", "-9", "websockify"], capture_output=True)

signal.signal(signal.SIGTERM, cleanup)
signal.signal(signal.SIGINT, cleanup)

def register(email, password, first_name, last_name, proxy, cf_clearance_val, timeout_s):
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-restore-session-state")
    options.add_argument("--window-size=1280,720")
    options.add_argument("--lang=en-US,en")
    if proxy:
        # Chrome does not support socks5h:// - normalize to socks5://
        chrome_proxy = proxy.replace("socks5h://", "socks5://")
        options.add_argument(f"--proxy-server={chrome_proxy}")

    driver = uc.Chrome(
        browser_executable_path=REAL_CHROME,
        options=options,
        headless=False,
        use_subprocess=True,
        version_main=147,
    )

    result = {"success": False, "error": "timeout", "email": email}
    try:
        driver.get("https://dashboard.oxylabs.io/en/registration")

        # Inject cf_clearance if provided
        if cf_clearance_val:
            log(f"[CF] Injecting manual cf_clearance (len={len(cf_clearance_val)})")
            driver.execute_cdp_cmd("Network.setCookie", {
                "name": "cf_clearance", "value": cf_clearance_val,
                "domain": ".oxylabs.io", "path": "/", "httpOnly": False, "secure": True
            })
            driver.execute_cdp_cmd("Network.setCookie", {
                "name": "cf_clearance", "value": cf_clearance_val,
                "domain": "dashboard.oxylabs.io", "path": "/", "httpOnly": False, "secure": True
            })
            driver.refresh()
            log("[CF] Cookie injected + page refreshed")

        log("⏳ Waiting for CF bypass (user can interact via noVNC)...")

        cf_passed = False
        for i in range(timeout_s):
            try:
                title = driver.title
                tl = title.lower() if title else ""
                elapsed = int(time.time() - t0)
                if i % 15 == 0:
                    log(f"[{elapsed}s] {driver.current_url[:60]} | {title[:50]}")

                if title and "moment" not in tl and "cierpliw" not in tl and "cloudflare" not in tl and len(title) > 3:
                    log(f"✅ CF bypassed at {elapsed}s! Title: {title}")
                    cf_passed = True
                    break

                # CDP-based CF Turnstile click every 4s (only when iframe found!)
                if i % 4 == 0 and i >= 4:
                    try:
                        # Search CF Turnstile iframe with multiple selectors
                        _cf_rect = driver.execute_script("""
                            const sels = [
                                'iframe[src*="challenges.cloudflare.com"]',
                                'iframe[src*="turnstile"]',
                                'iframe[src*="challenge-platform"]',
                                'iframe[title*="Widget"]',
                                '.cf-turnstile iframe',
                                '[id*="cf-chl"] iframe',
                                '[class*="cf-challenge"] iframe'
                            ];
                            for (const sel of sels) {
                                try {
                                    const frames = document.querySelectorAll(sel);
                                    for (const f of frames) {
                                        const r = f.getBoundingClientRect();
                                        if (r.width > 20 && r.height > 20)
                                            return {left:r.left,top:r.top,w:r.width,h:r.height,m:sel};
                                    }
                                } catch(e) {}
                            }
                            // Also check all iframes by src content
                            for (const f of document.querySelectorAll('iframe')) {
                                const src = (f.src || '').toLowerCase();
                                if (src.includes('cloudflare') || src.includes('challenge')) {
                                    const r = f.getBoundingClientRect();
                                    if (r.width > 20) return {left:r.left,top:r.top,w:r.width,h:r.height,m:'any-iframe'};
                                }
                            }
                            return null;
                        """)
                        if _cf_rect:
                            # CF Turnstile checkbox is in left portion of widget at ~28% x, 50% y
                            _cx = _cf_rect["left"] + _cf_rect["w"] * 0.28
                            _cy = _cf_rect["top"] + _cf_rect["h"] * 0.50
                            # Human-like approach: move first then click
                            driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
                                "type":"mouseMoved","x":_cx,"y":_cy,"modifiers":0,"button":"none","buttons":0
                            })
                            time.sleep(0.15)
                            driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
                                "type":"mousePressed","x":_cx,"y":_cy,"button":"left","buttons":1,"clickCount":1
                            })
                            time.sleep(0.08)
                            driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
                                "type":"mouseReleased","x":_cx,"y":_cy,"button":"left","buttons":0,"clickCount":1
                            })
                            log(f"🖱️ CDP clicked CF at ({_cx:.0f},{_cy:.0f}) [{_cf_rect.get('m','?')}]")
                        else:
                            if i % 20 == 0:
                                log(f"ℹ️ CF iframe not in DOM yet (i={i}), waiting...")
                    except Exception as _ce:
                        if i % 20 == 0:
                            log(f"⚠️ CDP click err: {str(_ce)[:80]}")
                    # Also try xdotool as secondary (may work if Chrome is on :99)
                    try:
                        import subprocess as _sp
                        _env2 = {**os.environ, "DISPLAY": ":99"}
                        _r2 = _sp.run(["xdotool","search","--name","Just a moment"],
                                     capture_output=True, text=True, env=_env2, timeout=2)
                        _wids2 = _r2.stdout.strip().split()
                        if _wids2:
                            _wid2 = _wids2[-1]
                            _g2 = _sp.run(["xdotool","getwindowgeometry",_wid2],
                                         capture_output=True, text=True, env=_env2, timeout=2).stdout
                            import re as _re2
                            _pm2 = _re2.search(r'Position: (\d+),(\d+)', _g2)
                            _sm2 = _re2.search(r'Geometry: (\d+)x(\d+)', _g2)
                            if _pm2 and _sm2:
                                _wx2,_wy2 = int(_pm2.group(1)),int(_pm2.group(2))
                                _ww2,_wh2 = int(_sm2.group(1)),int(_sm2.group(2))
                                _cx2 = _wx2 + int(_ww2 * 0.284)
                                _cy2 = _wy2 + int(_wh2 * 0.403)
                                _sp.run(["xdotool","windowfocus",_wid2], env=_env2, timeout=1)
                                _sp.run(["xdotool","mousemove","--sync",str(_cx2),str(_cy2)], env=_env2, timeout=1)
                                _sp.run(["xdotool","click","1"], env=_env2, timeout=1)
                                log(f"🖱️ xdotool backup clicked CF at ({_cx2},{_cy2})")
                    except: pass
            except Exception as e:
                if "invalid session" in str(e).lower():
                    break
            time.sleep(1)

        if not cf_passed:
            result["error"] = f"CF not bypassed after {timeout_s}s. Open noVNC and solve the challenge manually, or provide cf_clearance cookie."
            return result

        # CF passed — wait for React SPA form to fully render (can take 5-20s)
        fn = first_name or "Alex"
        ln = last_name or "Morgan"
        # --- IFRAME-AWARE FORM DETECTION AND FILL ---
        fn = first_name or "Alex"
        ln = last_name or "Morgan"
        log("⏳ Waiting for registration form (checking main + iframes)...")

        def find_inputs_in_context(drv):
            """Return (inputs_list, in_iframe, iframe_index)"""
            # 1) Main frame
            main_inps = drv.find_elements(By.TAG_NAME, "input")
            visible = [i for i in main_inps if i.is_displayed()]
            if len(visible) >= 2:
                return visible, False, -1
            if len(main_inps) >= 2:
                return main_inps, False, -1
            # 2) Scan iframes
            iframes = drv.find_elements(By.TAG_NAME, "iframe")
            for idx, fr in enumerate(iframes):
                try:
                    drv.switch_to.frame(fr)
                    fi = drv.find_elements(By.TAG_NAME, "input")
                    if len(fi) >= 2:
                        return fi, True, idx
                    drv.switch_to.default_content()
                except Exception:
                    try: drv.switch_to.default_content()
                    except: pass
            drv.switch_to.default_content()
            return [], False, -1

        inputs = []
        in_iframe = False
        frame_idx = -1
        for wait_j in range(45):
            try:
                inputs, in_iframe, frame_idx = find_inputs_in_context(driver)
                if len(inputs) >= 2:
                    log(f"✅ Form found at wait_j={wait_j}s (iframe={in_iframe}, frame={frame_idx}): {len(inputs)} inputs")
                    break
                if wait_j % 5 == 0:
                    cur_url = driver.current_url
                    cur_title = driver.title
                    ifc = len(driver.find_elements(By.TAG_NAME, "iframe"))
                    inp0 = len(driver.find_elements(By.TAG_NAME, "input"))
                    log(f"  [form-wait {wait_j}s] url={cur_url[:60]} iframes={ifc} main_inputs={inp0}")
                    if "chrome-error" in cur_url or "err_no" in cur_url.lower():
                        log("⚠️ Chrome error page detected — proxy may have failed")
                        break
                    if "dashboard" in cur_url and "registration" not in cur_url:
                        log("⚠️ Redirected away from registration page")
                        break
            except Exception as we:
                log(f"  [form-wait {wait_j}s] err: {str(we)[:80]}")
            time.sleep(1)

        log(f"📝 Found {len(inputs)} inputs (iframe={in_iframe}, frame_idx={frame_idx})")

        if len(inputs) < 2:
            # Get body text for debugging
            try:
                body = driver.execute_script("return document.body ? document.body.innerText.slice(0,200) : 'no body'")
                log(f"  Page body: {body[:100]}")
            except: pass
            result["error"] = f"Form not found (inputs={len(inputs)}). " +                 "If proxy failed, try: socks5://127.0.0.1:10851. " +                 "Or connect via noVNC and fill manually."
            return result

        # React-aware field value setter
        def react_set(el, value):
            try:
                driver.execute_script("""
                    var el = arguments[0]; var val = arguments[1];
                    var nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    nativeSetter.call(el, val);
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                """, el, value)
            except Exception:
                el.clear()
                el.send_keys(value)

        filled = 0
        for inp in inputs:
            try:
                ph = " ".join(filter(None, [
                    inp.get_attribute("placeholder"),
                    inp.get_attribute("name"),
                    inp.get_attribute("id"),
                    inp.get_attribute("type"),
                    inp.get_attribute("autocomplete"),
                ])).lower()
                val = None
                if "email" in ph or inp.get_attribute("type") == "email":
                    val = email
                elif "password" in ph or "pass" in ph or inp.get_attribute("type") == "password":
                    val = password
                elif "first" in ph or ("name" in ph and "last" not in ph and "user" not in ph and filled == 0):
                    val = fn
                elif "last" in ph or "surname" in ph:
                    val = ln
                elif filled == 1:  # fallback: second unknown text field = last name
                    val = ln

                if val and not (inp.get_attribute("value") or "").strip():
                    driver.execute_script("arguments[0].scrollIntoView(true)", inp)
                    react_set(inp, val)
                    filled += 1
                    log(f"  ✏️ [{ph[:30]}] = {val[:25]}")
                    time.sleep(0.3)
            except Exception as fe:
                log(f"  fill err: {str(fe)[:60]}")

        log(f"📝 Filled {filled} fields")

        if filled < 2:
            result["error"] = f"Could not fill form — only {filled} fields filled. Use noVNC for manual interaction."
            return result

        time.sleep(1)

        # Click submit
        submitted = False
        for sel in ['button[type="submit"]', 'button.btn-primary', 'button.btn', 'form button']:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, sel)
                driver.execute_script("arguments[0].click()", btn)
                log(f"🖱️ Clicked submit: {sel}")
                submitted = True
                break
            except:
                pass
        if not submitted:
            try:
                btns = driver.find_elements(By.TAG_NAME, "button")
                for btn in btns:
                    text = (btn.text or "").strip().lower()
                    if any(w in text for w in ["create", "register", "sign up", "start"]):
                        driver.execute_script("arguments[0].click()", btn)
                        log(f"🖱️ Clicked button: {btn.text[:30]}")
                        submitted = True
                        break
            except:
                pass
        if not submitted:
            log("⚠️ No submit button found — user may need to submit manually via noVNC")

        # Wait for response
        time.sleep(5)
        final_url = driver.current_url
        final_title = driver.title
        log(f"📍 Final URL: {final_url}")
        log(f"📍 Final Title: {final_title}")

        if "dashboard" in final_url or "success" in final_title.lower() or "/app/" in final_url:
            result = {"success": True, "email": email, "username": email.split("@")[0]}
            log("✅ REGISTRATION SUCCESS!")
        else:
            # Check for inline error message
            try:
                err_els = driver.find_elements(By.CSS_SELECTOR,
                    '.error, .alert, [role="alert"], p[class*="error"], [class*="message"]')
                for el in err_els:
                    txt = (el.text or "").strip()
                    if txt:
                        result["error"] = f"Form error: {txt[:200]}"
                        log(f"❌ Form error: {txt[:100]}")
                        break
                else:
                    result["error"] = f"Unknown result: {final_url[:100]}"
            except:
                result["error"] = f"Unknown result: {final_url[:100]}"

    except Exception as ex:
        result["error"] = str(ex)[:200]
    finally:
        try: driver.quit()
        except: pass

    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Oxylabs VNC Registration Session")
    ap.add_argument("--email",        required=True)
    ap.add_argument("--password",     required=True)
    ap.add_argument("--first",        default="")
    ap.add_argument("--last",         default="")
    ap.add_argument("--proxy",        default="")
    ap.add_argument("--cf-clearance", default="", dest="cf_clearance")
    ap.add_argument("--timeout",      type=int, default=300)
    args = ap.parse_args()

    kill_services()
    start_vnc()

    log(f"📺 noVNC ready — connect to see Chrome")
    log(f"🔑 Email: {args.email}")

    r = register(
        args.email, args.password,
        args.first, args.last,
        args.proxy, args.cf_clearance,
        args.timeout,
    )
    log(json.dumps(r))
    cleanup()
