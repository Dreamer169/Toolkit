#!/usr/bin/env python3
"""
ip2free 监控 + 自动求解器 v2.0 — 修复 JS 双大括号 + 使用 locator fill
"""
import sys, json, time, os, base64, datetime
sys.path.insert(0, "/root/Toolkit/artifacts/api-server")
import warnings; warnings.filterwarnings("ignore")
import urllib3; urllib3.disable_warnings()
import requests as _req

LOG_FILE = "/tmp/ip2free_monitor.log"
LF = open(LOG_FILE, "a", buffering=1)
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
    ("emily_gomez98@outlook.com",    "inAyy$X87Uj^",     10820),
    ("sophiagray574@outlook.com",    "8nQDovHvbR@%mWL$", 10822),
    ("e.lewis904@outlook.com",       "Aa123456",          10840),
    ("rylan_rivera98@outlook.com",   "AWgpis7xb0",        10825),
]

import ddddocr as _ddddocr
_ocr_std = _ddddocr.DdddOcr(show_ad=False)
try:
    _ocr_beta = _ddddocr.DdddOcr(show_ad=False, beta=True)
except:
    _ocr_beta = None

SS_DIR = "/tmp/ip2free_monitor_ss"
os.makedirs(SS_DIR, exist_ok=True)

DONE = set(["alewisazs@outlook.com"])
RESULTS = {"alewisazs@outlook.com": "pre-done"}

def api_session(port):
    s = _req.Session()
    s.verify = False
    s.headers.update(COMMON_H)
    s.proxies = {"http": f"socks5h://127.0.0.1:{port}", "https": f"socks5h://127.0.0.1:{port}"}
    return s

def check_captcha_size(email, pw, port):
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

def check_task_done_api(email, pw, port):
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

def ocr_png(png_bytes, save_path=None):
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

def blob_to_png(page, blob_url):
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

# --- Pure JS snippets (no Python format braces) ---
CLICK_CLAIM_BTN_JS = """
(function() {
    var bs = Array.from(document.querySelectorAll('button')).filter(function(b) {
        return b.offsetParent !== null && !b.disabled;
    });
    var labels = ['\u7acb\u5373\u9886\u53d6', '\u70b9\u51fb\u9886\u53d6', '\u9886\u53d6'];
    var t = bs.find(function(b) {
        var tx = b.innerText.trim();
        return labels.some(function(l) { return tx.indexOf(l) >= 0; });
    });
    if (t) { t.click(); return t.innerText.trim(); }
    return null;
})()
"""

GET_BLOB_URL_JS = """
(function() {
    var dialogs = Array.from(document.querySelectorAll('[role="dialog"]')).filter(function(x) {
        return x.offsetParent !== null;
    });
    if (!dialogs.length) return null;
    var d = dialogs[0];
    var img = d.querySelector('img.captcha-img') || d.querySelector('img');
    return img ? img.src : null;
})()
"""

MODAL_VISIBLE_JS = """
(function() {
    return Array.from(document.querySelectorAll('[role="dialog"]')).filter(function(x) {
        return x.offsetParent !== null;
    }).length;
})()
"""

CLOSE_MODAL_JS = """
(function() {
    var e = new KeyboardEvent('keydown', {key: 'Escape', bubbles: true});
    document.dispatchEvent(e);
})()
"""

FINISH_TASK_CODE_JS = """
(function() {
    var t = window.__lastFinishTask || '';
    return t;
})()
"""


def solve_one(email, pw, port):
    from patchright.sync_api import sync_playwright
    log(f"\n  → SOLVING {email} port={port}")

    LAST_API = {}
    FINISH_SUCCESS = [False]

    with sync_playwright() as p:
        br = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"],
            proxy={"server": f"socks5://127.0.0.1:{port}"},
        )
        ctx = br.new_context(locale="zh-CN", timezone_id="Asia/Shanghai",
                              user_agent=UA, java_script_enabled=True)

        def on_response(resp):
            if "api.ip2free.com/api" in resp.url:
                name = resp.url.split("/api/")[-1].split("?")[0]
                try:
                    b = resp.body()
                    if b and b[:4] == b"\x89PNG":
                        with open(f"{SS_DIR}/cap_{email.split('@')[0]}.png", "wb") as f:
                            f.write(b)
                        return
                    txt = resp.text()
                    LAST_API[name] = txt
                    if name not in ("ad/getList", "account/profile", "wallet/balance",
                                    "ip/freeList", "coupon/my"):
                        log(f"    <- {name}: {txt[:100]}")
                    if name == "account/finishTask" and '"code":0' in txt:
                        FINISH_SUCCESS[0] = True
                        log("    🎉 finishTask code=0!")
                except:
                    pass

        ctx.on("response", on_response)
        page = ctx.new_page()
        result = "failed"

        try:
            # ── Login ──────────────────────────────────────────────
            page.goto("https://www.ip2free.com/cn/login",
                      wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(3000)
            page.wait_for_selector("#email", timeout=12000)

            page.locator("#email").fill(email)
            page.locator("#password").fill(pw)
            page.wait_for_timeout(300)
            # Accept terms checkbox if present
            try:
                cb = page.locator("input[type='checkbox']")
                if cb.count() > 0 and not cb.first.is_checked():
                    cb.first.click()
            except:
                pass
            page.wait_for_timeout(200)
            # Click login button
            try:
                page.locator("button.MuiButton-sizeLarge").click(timeout=5000)
            except:
                page.locator("button[type='submit']").first.click(timeout=3000)
            page.wait_for_timeout(8000)

            if "/login" in page.url:
                log("    ❌ login failed — URL still login")
                br.close()
                return "login_failed"
            log(f"    ✅ logged in: {page.url}")

            # ── Activity page ──────────────────────────────────────
            LAST_API.clear()
            page.goto("https://www.ip2free.com/cn/freeProxy?tab=activity",
                      wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(6000)

            # Check if task already done
            tl = LAST_API.get("account/taskList", "")
            import re
            if tl:
                m = re.search(r'"task_id"\s*:\s*6.*?"finished_at"\s*:\s*"([^"]+)"', tl, re.S)
                if m and m.group(1) not in ("null",):
                    log(f"    already done: {m.group(1)}")
                    br.close()
                    return "already_done"

            for attempt in range(12):
                log(f"    ── attempt {attempt+1}/12 ──")
                LAST_API.clear()
                FINISH_SUCCESS[0] = False

                page.evaluate("window.scrollTo(0, 400)")
                page.wait_for_timeout(400)

                btn_txt = page.evaluate(CLICK_CLAIM_BTN_JS)
                log(f"    btn: {btn_txt!r}")

                if not btn_txt:
                    page.wait_for_timeout(3000)
                    tl2 = LAST_API.get("account/taskList", "")
                    if tl2:
                        m = re.search(r'"task_id"\s*:\s*6.*?"finished_at"\s*:\s*"([^"]+)"', tl2, re.S)
                        if m and m.group(1) not in ("null",):
                            log(f"    task done via tl2: {m.group(1)}")
                            result = "already_done"
                            break
                    if attempt == 0:
                        page.reload(wait_until="domcontentloaded", timeout=15000)
                        page.wait_for_timeout(6000)
                    continue

                page.wait_for_timeout(3500)

                # Direct success?
                if FINISH_SUCCESS[0]:
                    log("    🎉 direct success!")
                    result = "success"
                    break

                ft = LAST_API.get("account/finishTask", "")
                if '"code":0' in ft:
                    result = "success"
                    break

                # Check modal
                modal_n = page.evaluate(MODAL_VISIBLE_JS)
                if not modal_n:
                    log("    modal not visible, retry")
                    page.wait_for_timeout(2000)
                    continue

                # Get blob URL
                blob_url = page.evaluate(GET_BLOB_URL_JS)
                log(f"    blob: {blob_url}")

                if not blob_url or not blob_url.startswith("blob:"):
                    log("    no blob url, closing modal")
                    page.evaluate(CLOSE_MODAL_JS)
                    page.wait_for_timeout(2000)
                    continue

                png_bytes = blob_to_png(page, blob_url)
                log(f"    PNG: {len(png_bytes) if png_bytes else 0}b")

                if not png_bytes or len(png_bytes) < 100:
                    log("    PNG empty, closing modal")
                    page.evaluate(CLOSE_MODAL_JS)
                    page.wait_for_timeout(2000)
                    continue

                candidates = ocr_png(png_bytes,
                    f"{SS_DIR}/{email.split('@')[0]}_a{attempt}.png")

                if not candidates:
                    log("    no OCR candidates")
                    page.evaluate(CLOSE_MODAL_JS)
                    page.wait_for_timeout(1500)
                    continue

                solved = False
                for cand in candidates:
                    cap_clean = cand.strip()[:12]
                    log(f"    trying: {cap_clean!r}")

                    # Use patchright locator to fill captcha input (no JS braces issue)
                    try:
                        inp_loc = page.locator('[role="dialog"] input[name="captcha"]')
                        if inp_loc.count() == 0:
                            inp_loc = page.locator('[role="dialog"] input[type="text"]')
                        inp_loc.first.fill(cap_clean, timeout=5000)
                        filled_val = inp_loc.first.input_value(timeout=3000)
                        log(f"    filled: {filled_val!r}")
                    except Exception as fe:
                        log(f"    fill err: {fe}")
                        # Fallback: JS with no braces issue
                        clean_escaped = cap_clean.replace("'", "\\'")
                        fill_js = (
                            "(function() {"
                            "var d = Array.from(document.querySelectorAll('[role=\"dialog\"]'))"
                            ".filter(function(x) { return x.offsetParent !== null; })[0];"
                            "if (!d) return null;"
                            "var inp = d.querySelector('input[name=\"captcha\"]') || d.querySelector('input[type=\"text\"]');"
                            "if (!inp) return null;"
                            "var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;"
                            "setter.call(inp, '" + clean_escaped + "');"
                            "inp.dispatchEvent(new Event('input', {bubbles: true}));"
                            "inp.dispatchEvent(new Event('change', {bubbles: true}));"
                            "return inp.value;"
                            "})()"
                        )
                        try:
                            filled_val = page.evaluate(fill_js)
                            log(f"    filled (js): {filled_val!r}")
                        except Exception as fe2:
                            log(f"    fill js err: {fe2}")
                            continue

                    page.wait_for_timeout(500)
                    LAST_API.clear()
                    FINISH_SUCCESS[0] = False

                    # Click submit button in dialog
                    try:
                        # Try locator approach first
                        submit_txt = None
                        for sel in [
                            '[role="dialog"] button:has-text("\u63d0\u4ea4")',
                            '[role="dialog"] button:has-text("\u786e\u8ba4")',
                            '[role="dialog"] button:has-text("OK")',
                        ]:
                            try:
                                loc = page.locator(sel)
                                if loc.count() > 0:
                                    submit_txt = loc.first.inner_text(timeout=2000)
                                    loc.first.click(timeout=3000)
                                    break
                            except:
                                pass
                        if not submit_txt:
                            # JS fallback for submit
                            submit_js = (
                                "(function() {"
                                "var dlg = Array.from(document.querySelectorAll('[role=\"dialog\"]'))"
                                ".filter(function(x) { return x.offsetParent !== null; })[0];"
                                "if (!dlg) return null;"
                                "var bs = Array.from(dlg.querySelectorAll('button'))"
                                ".filter(function(b) { return b.offsetParent !== null; });"
                                "var targets = ['\u63d0\u4ea4', '\u786e\u8ba4', 'OK', '\u9a8c\u8bc1'];"
                                "var ok = bs.find(function(b) {"
                                "var t = b.innerText.trim();"
                                "return targets.some(function(tg) { return t.indexOf(tg) >= 0; });"
                                "}) || bs[0];"
                                "if (ok) { ok.click(); return ok.innerText.trim(); }"
                                "return null;"
                                "})()"
                            )
                            submit_txt = page.evaluate(submit_js)
                        log(f"    submit: {submit_txt!r}")
                    except Exception as se:
                        log(f"    submit err: {se}")

                    page.wait_for_timeout(5000)

                    cc = LAST_API.get("account/checkCaptcha", "")
                    ft = LAST_API.get("account/finishTask", "")
                    log(f"    checkCaptcha: {cc[:100]}")
                    log(f"    finishTask:   {ft[:100]}")

                    if '"code":0' in cc or FINISH_SUCCESS[0]:
                        log("    ✅ captcha accepted!")
                        if '"code":0' in ft or FINISH_SUCCESS[0]:
                            result = "success"
                            solved = True
                            break
                        # Re-trigger finishTask
                        page.evaluate(CLOSE_MODAL_JS)
                        page.wait_for_timeout(2000)
                        LAST_API.clear()
                        FINISH_SUCCESS[0] = False
                        btn2 = page.evaluate(CLICK_CLAIM_BTN_JS)
                        log(f"    re-trigger btn: {btn2!r}")
                        page.wait_for_timeout(4000)
                        ft2 = LAST_API.get("account/finishTask", "")
                        if '"code":0' in ft2 or FINISH_SUCCESS[0]:
                            log("    🎉 SUCCESS on re-trigger!")
                            result = "success"
                            solved = True
                            break
                        else:
                            log(f"    re-trigger failed: {ft2[:80]}")
                    else:
                        log("    captcha wrong, checking for new image...")
                        still_open = page.evaluate(MODAL_VISIBLE_JS)
                        if still_open:
                            page.wait_for_timeout(1500)
                            new_blob = page.evaluate(GET_BLOB_URL_JS)
                            if new_blob and new_blob != blob_url:
                                new_png = blob_to_png(page, new_blob)
                                if new_png and len(new_png) > 100:
                                    new_cands = ocr_png(new_png,
                                        f"{SS_DIR}/{email.split('@')[0]}_r{attempt}.png")
                                    log(f"    new captcha cands: {new_cands}")
                                    if new_cands:
                                        candidates = new_cands
                                        blob_url = new_blob
                                    continue

                if solved or result == "success":
                    break

                page.evaluate(CLOSE_MODAL_JS)
                page.wait_for_timeout(2000)

        except Exception as e:
            log(f"    ERROR: {e}")
            import traceback
            traceback.print_exc(file=LF)
        finally:
            try:
                page.screenshot(path=f"{SS_DIR}/{email.split('@')[0]}_final.png")
            except:
                pass
            br.close()

    return result


def main():
    log("\n" + "=" * 60)
    log("ip2free Monitor + Auto Solver v2.0 (locator-fill fix)")
    log("=" * 60)

    CHECK_INTERVAL = 240  # 4 minutes between rounds
    MAX_ROUNDS = 60
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

            if check_task_done_api(email, pw, port):
                log(f"  ✅ ALREADY DONE: {email}")
                DONE.add(email)
                RESULTS[email] = "already_done"
                continue

            cap_sz = check_captcha_size(email, pw, port)
            log(f"  captcha: {cap_sz} bytes")

            if cap_sz <= 100:
                log(f"  → blocked, skip")
                continue

            log(f"  → captcha available ({cap_sz}b)! Solving...")
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
            log(f"Sleeping {CHECK_INTERVAL}s...")
            time.sleep(CHECK_INTERVAL)

    log("\n" + "=" * 60)
    log("FINAL RESULTS:")
    for email, r in RESULTS.items():
        log(f"  {r:15s} {email}")
    with open("/tmp/ip2free_monitor_result.json", "w") as f:
        json.dump(RESULTS, f, indent=2)
    LF.close()


main()
