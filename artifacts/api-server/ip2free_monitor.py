#!/usr/bin/env python3
"""
ip2free 监控 + 自动求解器 v1.0
- 每 5 分钟检查待处理账号的验证码状态
- 验证码可用时立即启动 patchright 浏览器求解
- ddddocr beta + std 双模型 OCR
- 成功后记录完成状态
"""
import sys, json, time, os, base64, io, datetime
sys.path.insert(0, "/root/Toolkit/artifacts/api-server")
import warnings; warnings.filterwarnings("ignore")
import urllib3; urllib3.disable_warnings()
import requests as _req

LOG_FILE = "/tmp/ip2free_monitor.log"
LF = open(LOG_FILE, "w", buffering=1)
def log(msg):
    ts = datetime.datetime.now().strftime("[%H:%M:%S]")
    line = f"{ts} {msg}"
    print(line, flush=True)
    LF.write(line + "\n")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.6778.85 Safari/537.36"
BASE_API = "https://api.ip2free.com"
COMMON_H = {
    "User-Agent": UA, "Accept": "*/*", "Accept-Language": "zh-CN",
    "Origin": "https://www.ip2free.com", "Referer": "https://www.ip2free.com/",
    "domain": "www.ip2free.com", "lang": "cn", "webname": "IP2FREE",
    "affid": "", "invitecode": "", "serviceid": "",
}

ACCOUNTS = [
    # (email, pw, proxy_port)  — alewisazs already done
    ("emily_gomez98@outlook.com",    "inAyy$X87Uj^",     10820),
    ("sophiagray574@outlook.com",    "8nQDovHvbR@%mWL$", 10822),
    ("e.lewis904@outlook.com",       "Aa123456",          10840),
    ("rylan_rivera98@outlook.com",   "AWgpis7xb0",        10825),
]

# OCR setup
import ddddocr as _ddddocr
_ocr_std = _ddddocr.DdddOcr(show_ad=False)
try:
    _ocr_beta = _ddddocr.DdddOcr(show_ad=False, beta=True)
except:
    _ocr_beta = None
    log("[warn] beta OCR not available")

SS_DIR = "/tmp/ip2free_monitor_ss"
os.makedirs(SS_DIR, exist_ok=True)

# Track done accounts
DONE = set(["alewisazs@outlook.com"])
RESULTS = {"alewisazs@outlook.com": "pre-done"}

def api_session(port: int) -> _req.Session:
    s = _req.Session()
    s.verify = False
    s.headers.update(COMMON_H)
    s.proxies = {
        "http":  f"socks5h://127.0.0.1:{port}",
        "https": f"socks5h://127.0.0.1:{port}",
    }
    return s

def check_captcha_size(email: str, pw: str, port: int) -> int:
    """Return captcha response size (>100 = available)"""
    try:
        s = api_session(port)
        lr = s.post(f"{BASE_API}/api/account/login?",
            data=json.dumps({"email": email, "password": pw}),
            headers={"Content-Type": "text/plain;charset=UTF-8"}, timeout=15)
        tok = lr.json().get("data", {}).get("token")
        if not tok:
            return -1
        s.headers["x-token"] = tok
        cap = s.get(f"{BASE_API}/api/account/captcha?", timeout=10)
        return len(cap.content)
    except Exception as e:
        log(f"  check_captcha_size err {email}: {e}")
        return -1

def check_task_done(email: str, pw: str, port: int) -> bool:
    """Return True if task 6 is already finished today"""
    try:
        s = api_session(port)
        lr = s.post(f"{BASE_API}/api/account/login?",
            data=json.dumps({"email": email, "password": pw}),
            headers={"Content-Type": "text/plain;charset=UTF-8"}, timeout=15)
        tok = lr.json().get("data", {}).get("token")
        if not tok:
            return False
        s.headers["x-token"] = tok
        tl = s.post(f"{BASE_API}/api/account/taskList?",
            data="{}", headers={"Content-Type": "text/plain;charset=UTF-8"}, timeout=12)
        tasks = tl.json().get("data", {}).get("list", [])
        t6 = next((t for t in tasks if t.get("task_id") == 6), None)
        if t6:
            fa = t6.get("finished_at")
            return bool(fa and fa != "null" and fa is not None)
        return False
    except:
        return False

def ocr_png(png_bytes: bytes, save_path: str | None = None) -> list[str]:
    if save_path:
        with open(save_path, "wb") as f:
            f.write(png_bytes)
    results = []
    for label, ocr in [("beta", _ocr_beta), ("std", _ocr_std)]:
        if ocr is None:
            continue
        try:
            txt = ocr.classification(png_bytes).strip()
            log(f"    OCR {label}: {txt!r}")
            if txt and txt not in results:
                results.append(txt)
        except Exception as e:
            log(f"    OCR {label} err: {e}")
    return results

def blob_to_png(page, blob_url: str) -> bytes | None:
    try:
        result = page.evaluate("""
        async (blobUrl) => {
            try {
                const resp = await fetch(blobUrl);
                const ab = await resp.arrayBuffer();
                const bytes = new Uint8Array(ab);
                if (bytes.length === 0) return null;
                let b64 = '';
                const chunk = 8192;
                for (let i = 0; i < bytes.length; i += chunk) {
                    b64 += btoa(String.fromCharCode(...bytes.subarray(i, i+chunk)));
                }
                return b64;
            } catch(e) { return null; }
        }
        """, blob_url)
        if result:
            raw = base64.b64decode(result)
            return raw if len(raw) > 50 else None
    except Exception as e:
        log(f"    blob_to_png err: {e}")
    return None

CLICK_BTN_JS = (
    "(function(){"
    "var bs=Array.from(document.querySelectorAll('button'))"
    ".filter(function(b){return b.offsetParent!==null&&!b.disabled;});"
    "var t=bs.find(function(b){"
    "var tx=b.innerText.trim();"
    "return tx.indexOf('\u7acb\u5373\u9886\u53d6')>=0"
    "||tx.indexOf('\u70b9\u51fb\u9886\u53d6')>=0"
    "||tx.indexOf('\u9886\u53d6')>=0;"
    "});"
    "if(t){t.click();return t.innerText.trim();}"
    "return null;})()"
)

FILL_CAPTCHA_JS_TPL = (
    "(function(val){{"
    "var inp=document.querySelector('[role=\"dialog\"] input[name=\"captcha\"]');"
    "if(!inp)inp=document.querySelector('[role=\"dialog\"] input[type=\"text\"]');"
    "if(!inp)return null;"
    "var setter=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;"
    "setter.call(inp,val);"
    "inp.dispatchEvent(new Event('input',{{bubbles:true}}));"
    "inp.dispatchEvent(new Event('change',{{bubbles:true}}));"
    "return inp.value;"
    "}})('{val}')"
)

CLICK_SUBMIT_JS = (
    "(function(){"
    "var dlg=Array.from(document.querySelectorAll('[role=\"dialog\"]'))"
    ".filter(function(x){return x.offsetParent!==null;})[0];"
    "if(!dlg)return null;"
    "var bs=Array.from(dlg.querySelectorAll('button'))"
    ".filter(function(b){return b.offsetParent!==null;});"
    "var ok=bs.find(function(b){"
    "var t=b.innerText.trim();"
    "return t==='\u63d0\u4ea4'||t==='\u786e\u8ba4'||t==='OK'||t.indexOf('\u9a8c\u8bc1')>=0;"
    "});"
    "if(!ok&&bs.length>0)ok=bs[0];"
    "if(ok){ok.click();return ok.innerText.trim();}"
    "return null;})()"
)

GET_BLOB_URL_JS = (
    "(function(){"
    "var d=Array.from(document.querySelectorAll('[role=\"dialog\"]'))"
    ".filter(function(x){return x.offsetParent!==null;})[0];"
    "if(!d)return null;"
    "var img=d.querySelector('img.captcha-img');"
    "if(!img)img=d.querySelector('img');"
    "return img?img.src:null;})()"
)

MODAL_VIS_JS = (
    "(function(){return Array.from(document.querySelectorAll('[role=\"dialog\"]'))"
    ".filter(function(x){return x.offsetParent!==null;}).length;})()"
)


def solve_one(email: str, pw: str, port: int) -> str:
    """Run full solver. Return 'success', 'already_done', or 'failed'"""
    from patchright.sync_api import sync_playwright

    log(f"\n  → SOLVING {email} port={port}")
    LAST_API = {}
    CAPTCHA_VALID = [False]

    with sync_playwright() as p:
        br = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"],
            proxy={"server": f"socks5://127.0.0.1:{port}"},
        )
        ctx = br.new_context(locale="zh-CN", timezone_id="Asia/Shanghai",
                              user_agent=UA, java_script_enabled=True)
        ctx.add_init_script(
            "Object.defineProperty(navigator,'platform',{get:()=>'Win32'});"
        )

        def on_response(resp):
            if "api.ip2free.com/api" in resp.url:
                name = resp.url.split("/api/")[-1].split("?")[0]
                try:
                    b = resp.body()
                    if b and b[:4] == b"\x89PNG":
                        with open(f"{SS_DIR}/cap_{email.split('@')[0]}.png","wb") as f:
                            f.write(b)
                        log(f"    ← {name} PNG {len(b)}b")
                        return
                    txt = resp.text()
                    LAST_API[name] = txt[:800]
                    if name not in ("ad/getList","account/profile","wallet/balance",
                                    "ip/freeList","coupon/my"):
                        log(f"    ← {name} ({len(b)}b): {txt[:100]}")
                    if name == "account/checkCaptcha" and '"code":0' in txt:
                        CAPTCHA_VALID[0] = True
                        log("    ✅ checkCaptcha VALID!")
                    if name == "account/finishTask" and '"code":0' in txt:
                        log("    🎉 finishTask SUCCESS!")
                except:
                    pass

        ctx.on("response", on_response)
        page = ctx.new_page()
        result = "failed"

        try:
            # ── Login ──────────────────────────────────────────────────
            page.goto("https://www.ip2free.com/cn/login",
                      wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(3000)
            page.wait_for_selector("#email", timeout=12000)

            el = page.locator("#email"); el.click(); page.wait_for_timeout(150)
            for ch in email: el.press_sequentially(ch, delay=50)
            pl = page.locator("#password"); pl.click(); page.wait_for_timeout(150)
            for ch in pw: pl.press_sequentially(ch, delay=50)
            page.wait_for_timeout(300)
            page.evaluate(
                "(function(){var c=document.querySelector(\"input[type='checkbox']\");"
                "if(c&&!c.checked)c.click();})()"
            )
            page.wait_for_timeout(300)
            page.evaluate(
                "(function(){var b=document.querySelector('button.MuiButton-sizeLarge');"
                "if(b)b.click();})()"
            )
            page.wait_for_timeout(8000)
            if "/login" in page.url:
                log("    ❌ login failed")
                br.close()
                return "login_failed"
            log("    ✅ logged in")

            # ── Activity page ──────────────────────────────────────────
            page.goto("https://www.ip2free.com/cn/freeProxy?tab=activity",
                      wait_until="domcontentloaded", timeout=15000)
            # Wait for React to render content
            page.wait_for_timeout(6000)

            # Check task already done?
            tl_rsp = LAST_API.get("account/taskList", "")
            if tl_rsp:
                import re as _re
                m = _re.search(r'"task_id"\s*:\s*6.*?"finished_at"\s*:\s*"([^"]+)"', tl_rsp, _re.S)
                if m and m.group(1) not in ("null",):
                    log(f"    task already done: {m.group(1)}")
                    br.close()
                    return "already_done"

            for attempt in range(10):
                log(f"    ── attempt {attempt+1}/10 ──")
                LAST_API.clear()
                CAPTCHA_VALID[0] = False

                # Scroll page to find button
                page.evaluate("window.scrollTo(0, 500)")
                page.wait_for_timeout(500)

                btn_txt = page.evaluate(CLICK_BTN_JS)
                log(f"    btn: {btn_txt!r}")

                if not btn_txt:
                    # Maybe page needs more time, or task done
                    page.wait_for_timeout(3000)
                    # Try to load fresh task list
                    tl2 = LAST_API.get("account/taskList","")
                    if tl2:
                        import re as _re
                        m = _re.search(r'"task_id"\s*:\s*6.*?"finished_at"\s*:\s*"([^"]+)"', tl2, _re.S)
                        if m and m.group(1) not in ("null",):
                            log(f"    task done: {m.group(1)}")
                            result = "already_done"
                            break
                    if attempt == 0:
                        # Reload page
                        page.reload(wait_until="domcontentloaded", timeout=15000)
                        page.wait_for_timeout(6000)
                    continue

                page.wait_for_timeout(3500)

                # Check direct success
                ft = LAST_API.get("account/finishTask","")
                if '"code":0' in ft:
                    log("    🎉 direct success!")
                    result = "success"
                    break

                # Check modal
                modal_n = page.evaluate(MODAL_VIS_JS)
                if not modal_n:
                    log("    modal not visible")
                    page.wait_for_timeout(2000)
                    continue

                # Get captcha blob
                blob_url = page.evaluate(GET_BLOB_URL_JS)
                log(f"    blob: {blob_url}")

                if not blob_url or not blob_url.startswith("blob:"):
                    log("    no valid blob url")
                    page.evaluate("(function(){var e=new KeyboardEvent('keydown',{key:'Escape',bubbles:true});document.dispatchEvent(e);})()")
                    page.wait_for_timeout(1500)
                    continue

                png_bytes = blob_to_png(page, blob_url)
                log(f"    PNG: {len(png_bytes) if png_bytes else 0}b")

                if not png_bytes or len(png_bytes) < 100:
                    log("    PNG empty, closing modal")
                    page.evaluate("(function(){var e=new KeyboardEvent('keydown',{key:'Escape',bubbles:true});document.dispatchEvent(e);})()")
                    page.wait_for_timeout(2000)
                    continue

                candidates = ocr_png(png_bytes,
                    f"{SS_DIR}/{email.split('@')[0]}_cap{attempt}.png")

                if not candidates:
                    log("    no OCR candidates")
                    page.evaluate("(function(){var e=new KeyboardEvent('keydown',{key:'Escape',bubbles:true});document.dispatchEvent(e);})()")
                    page.wait_for_timeout(1500)
                    continue

                solved_captcha = False
                for cand in candidates:
                    cap_clean = cand.strip()[:12].replace("'", "")
                    log(f"    trying: {cap_clean!r}")

                    fill_js = FILL_CAPTCHA_JS_TPL.replace("{val}", cap_clean)
                    filled = page.evaluate(fill_js)
                    log(f"    filled: {filled!r}")
                    page.wait_for_timeout(500)

                    LAST_API.clear()
                    CAPTCHA_VALID[0] = False

                    submit_txt = page.evaluate(CLICK_SUBMIT_JS)
                    log(f"    submit: {submit_txt!r}")
                    page.wait_for_timeout(5000)

                    cc = LAST_API.get("account/checkCaptcha","")
                    ft = LAST_API.get("account/finishTask","")
                    log(f"    checkCaptcha: {cc[:80]}")
                    log(f"    finishTask:   {ft[:80]}")

                    if CAPTCHA_VALID[0] or '"code":0' in cc:
                        log(f"    ✅ captcha accepted!")
                        if '"code":0' in ft:
                            log("    🎉 finishTask code=0 too!")
                            result = "success"
                            solved_captcha = True
                            break
                        else:
                            # Re-click "立即领取" to trigger finishTask again
                            page.evaluate("(function(){var e=new KeyboardEvent('keydown',{key:'Escape',bubbles:true});document.dispatchEvent(e);})()")
                            page.wait_for_timeout(2000)
                            LAST_API.clear()
                            btn2 = page.evaluate(CLICK_BTN_JS)
                            page.wait_for_timeout(4000)
                            ft2 = LAST_API.get("account/finishTask","")
                            log(f"    re-trigger finishTask: {ft2[:100]}")
                            if '"code":0' in ft2:
                                log("    🎉 SUCCESS on re-trigger!")
                                result = "success"
                                solved_captcha = True
                                break
                            else:
                                log(f"    re-trigger failed: {ft2[:60]}")
                    else:
                        # Wrong captcha - check if modal still open with new image
                        still_open = page.evaluate(MODAL_VIS_JS)
                        if still_open:
                            new_blob = page.evaluate(GET_BLOB_URL_JS)
                            if new_blob and new_blob != blob_url:
                                # Got new captcha in same attempt
                                new_png = blob_to_png(page, new_blob)
                                if new_png and len(new_png) > 100:
                                    candidates = ocr_png(new_png,
                                        f"{SS_DIR}/{email.split('@')[0]}_retry{attempt}.png")
                                    log(f"    new captcha candidates: {candidates}")
                                    blob_url = new_blob
                                continue

                if solved_captcha or result == "success":
                    break

                # Close modal
                page.evaluate("(function(){var e=new KeyboardEvent('keydown',{key:'Escape',bubbles:true});document.dispatchEvent(e);})()")
                page.wait_for_timeout(2000)

        except Exception as e:
            log(f"    ERROR: {e}")
            import traceback; traceback.print_exc(file=LF)
        finally:
            try:
                page.screenshot(path=f"{SS_DIR}/{email.split('@')[0]}_final.png")
            except:
                pass
            br.close()

    return result


def main():
    log("=" * 60)
    log("ip2free Monitor + Auto Solver v1.0")
    log("=" * 60)
    log(f"Accounts: {[a[0] for a in ACCOUNTS]}")
    log(f"Already done: {list(DONE)}")

    CHECK_INTERVAL = 300  # 5 minutes
    MAX_ROUNDS = 48        # max 4 hours total
    round_n = 0

    while len(DONE) < 5 and round_n < MAX_ROUNDS:
        round_n += 1
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        log(f"\n{'='*50}")
        log(f"ROUND {round_n} | {ts} | done={len(DONE)}/5")
        log(f"{'='*50}")

        for email, pw, port in ACCOUNTS:
            if email in DONE:
                continue

            log(f"\n  Checking {email}...")

            # First check if already done
            if check_task_done(email, pw, port):
                log(f"  ✅ TASK ALREADY DONE: {email}")
                DONE.add(email)
                RESULTS[email] = "already_done"
                continue

            # Check captcha availability
            cap_sz = check_captcha_size(email, pw, port)
            log(f"  captcha: {cap_sz} bytes")

            if cap_sz <= 0:
                log(f"  → captcha blocked, skip")
                continue

            # Solve!
            log(f"  → captcha available! Running solver...")
            res = solve_one(email, pw, port)
            log(f"  RESULT: {email} → {res}")
            RESULTS[email] = res

            if res in ("success", "already_done"):
                DONE.add(email)

        pending = [a[0] for a in ACCOUNTS if a[0] not in DONE]
        log(f"\nDone: {len(DONE)}/5  Pending: {pending}")

        if len(DONE) >= 5:
            log("🎉 ALL ACCOUNTS COMPLETE!")
            break

        if pending:
            log(f"Waiting {CHECK_INTERVAL}s before next check...")
            time.sleep(CHECK_INTERVAL)

    log("\n" + "=" * 60)
    log("FINAL RESULTS:")
    for email, r in RESULTS.items():
        log(f"  {r:15s} {email}")

    with open("/tmp/ip2free_monitor_result.json","w") as f:
        json.dump(RESULTS, f, indent=2)
    log("Saved /tmp/ip2free_monitor_result.json")
    LF.close()


main()
