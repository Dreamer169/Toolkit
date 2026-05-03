#!/usr/bin/env python3
"""
ip2free solve v4 - smart: stop on 0-byte PNG, max 3 captcha attempts/session
"""
import sys, json, time, os, base64, datetime, re
sys.path.insert(0, "/root/Toolkit/artifacts/api-server")
import warnings; warnings.filterwarnings("ignore")
import urllib3; urllib3.disable_warnings()
import requests as _req
import ddddocr as _ddddocr

_ocr_std = _ddddocr.DdddOcr(show_ad=False)
try:
    _ocr_beta = _ddddocr.DdddOcr(show_ad=False, beta=True)
except:
    _ocr_beta = None

SS_DIR = "/tmp/ip2free_ss_v4"
os.makedirs(SS_DIR, exist_ok=True)
LOG_F = open("/tmp/ip2free_solve_v4.log", "a", buffering=1)

def log(m):
    ts = datetime.datetime.now().strftime("[%H:%M:%S]")
    line = ts + " " + str(m)
    print(line, flush=True)
    LOG_F.write(line + "\n")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.6778.85 Safari/537.36"
BASE_API = "https://api.ip2free.com"
H = {
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

FETCH_BLOB_JS = """
async (blobUrl) => {
    try {
        const resp = await fetch(blobUrl);
        const ab = await resp.arrayBuffer();
        const bytes = new Uint8Array(ab);
        if (bytes.length === 0) return null;
        let b64 = "";
        const chunk = 8192;
        for (let i = 0; i < bytes.length; i += chunk) {
            b64 += btoa(String.fromCharCode(...bytes.subarray(i, i+chunk)));
        }
        return b64;
    } catch(e) { return null; }
}
"""

CLICK_CLAIM_JS = (
    "(function(){"
    "var bs=Array.from(document.querySelectorAll('button')).filter(function(b){return b.offsetParent!==null&&!b.disabled;});"
    "var t=bs.find(function(b){var tx=b.innerText.trim();"
    "return tx.indexOf('\u7acb\u5373\u9886\u53d6')>=0||tx.indexOf('\u70b9\u51fb\u9886\u53d6')>=0||tx.indexOf('\u9886\u53d6')>=0;});"
    "if(t){t.click();return t.innerText.trim();}return null;})()"
)

GET_BLOB_JS = (
    "(function(){"
    "var d=Array.from(document.querySelectorAll('[role=\"dialog\"]')).filter(function(x){return x.offsetParent!==null;})[0];"
    "if(!d)return null;"
    "var img=d.querySelector('img.captcha-img')||d.querySelector('img');"
    "return img?img.src:null;})()"
)

MODAL_VIS_JS = (
    "(function(){return Array.from(document.querySelectorAll('[role=\"dialog\"]'))"
    ".filter(function(x){return x.offsetParent!==null;}).length;})()"
)

ESC_JS = (
    "(function(){document.dispatchEvent("
    "new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));})()"
)

SUBMIT_JS = (
    "(function(){"
    "var d=Array.from(document.querySelectorAll('[role=\"dialog\"]')).filter(function(x){return x.offsetParent!==null;})[0];"
    "if(!d)return null;"
    "var bs=Array.from(d.querySelectorAll('button')).filter(function(b){return b.offsetParent!==null;});"
    "var tgts=['\u63d0\u4ea4','\u786e\u8ba4','OK','\u9a8c\u8bc1'];"
    "var ok=bs.find(function(b){var t=b.innerText.trim();return tgts.some(function(tg){return t.indexOf(tg)>=0;});})||bs[0];"
    "if(ok){ok.click();return ok.innerText.trim();}return null;})()"
)


def make_fill_js(cap_value):
    escaped = cap_value.replace("\\", "\\\\").replace("'", "\\'")
    return (
        "(function(){"
        "var d=Array.from(document.querySelectorAll('[role=\"dialog\"]')).filter(function(x){return x.offsetParent!==null;})[0];"
        "if(!d)return null;"
        "var inp=d.querySelector('input[name=\"captcha\"]')||d.querySelector('input[type=\"text\"]');"
        "if(!inp)return null;"
        "var setter=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;"
        "setter.call(inp,'" + escaped + "');"
        "inp.dispatchEvent(new Event('input',{bubbles:true}));"
        "inp.dispatchEvent(new Event('change',{bubbles:true}));"
        "return inp.value;"
        "})()"
    )


def blob_to_png(page, blob_url):
    try:
        result = page.evaluate(FETCH_BLOB_JS, blob_url)
        if result:
            raw = base64.b64decode(result)
            return raw if len(raw) > 50 else None
    except Exception as e:
        log("blob_to_png err: " + str(e))
    return None


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
            log("  OCR " + label + ": " + repr(txt))
            if txt and txt not in results:
                results.append(txt)
        except Exception as e:
            log("  OCR " + label + " err: " + str(e))
    return results


def api_pre_check(email, pw, port):
    """Returns (captcha_size, task_done) via requests API"""
    s = _req.Session()
    s.verify = False
    s.headers.update(H)
    s.proxies = {
        "http":  "socks5h://127.0.0.1:" + str(port),
        "https": "socks5h://127.0.0.1:" + str(port),
    }
    try:
        lr = s.post(BASE_API + "/api/account/login?",
            data=json.dumps({"email": email, "password": pw}),
            headers={"Content-Type": "text/plain;charset=UTF-8"}, timeout=15)
        tok = lr.json().get("data", {}).get("token")
        if not tok:
            return -1, False
        s.headers["x-token"] = tok
        cap = s.get(BASE_API + "/api/account/captcha?", timeout=10)
        sz = len(cap.content)
        tl = s.post(BASE_API + "/api/account/taskList?",
            data="{}", headers={"Content-Type": "text/plain;charset=UTF-8"}, timeout=12)
        tasks = tl.json().get("data", {}).get("list", [])
        t6 = next((t for t in tasks if t.get("task_id") == 6), None)
        done = False
        if t6:
            fa = t6.get("finished_at")
            done = bool(fa and fa != "null" and fa is not None)
        return sz, done
    except Exception as e:
        log("pre_check err " + email + ": " + str(e)[:60])
        return -1, False


def solve_account(email, pw, port):
    """
    Returns: 'success', 'already_done', 'rate_limited', 'login_failed', 'failed'
    Max 3 captcha attempts. Stops immediately on 0-byte PNG.
    """
    from patchright.sync_api import sync_playwright
    log("\n=== SOLVE " + email + " ===")
    LAST = {}
    FT_OK = [False]
    MAX_CAP_ATTEMPTS = 3  # stop after 3 wrong captchas to preserve rate limit budget

    with sync_playwright() as p:
        br = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"],
            proxy={"server": "socks5://127.0.0.1:" + str(port)},
        )
        ctx = br.new_context(locale="zh-CN", timezone_id="Asia/Shanghai", user_agent=UA)

        def on_r(resp):
            if "api.ip2free.com/api" in resp.url:
                n = resp.url.split("/api/")[-1].split("?")[0]
                try:
                    b = resp.body()
                    if b and b[:4] == b"\x89PNG":
                        with open(SS_DIR + "/cap_" + email.split("@")[0] + ".png", "wb") as f:
                            f.write(b)
                        return
                    txt = resp.text()
                    LAST[n] = txt
                    if n not in ("ad/getList", "account/profile", "wallet/balance",
                                 "ip/freeList", "coupon/my"):
                        log("  <- " + n + ": " + txt[:100])
                    if n == "account/finishTask" and '"code":0' in txt:
                        FT_OK[0] = True
                except:
                    pass

        ctx.on("response", on_r)
        page = ctx.new_page()
        result = "failed"
        captcha_attempts = 0

        try:
            # ── Login ──────────────────────────────────────────
            page.goto("https://www.ip2free.com/cn/login",
                      wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(3000)
            page.wait_for_selector("#email", timeout=12000)

            el = page.locator("#email")
            el.click()
            page.wait_for_timeout(200)
            for ch in email:
                el.press_sequentially(ch, delay=50)

            pl = page.locator("#password")
            pl.click()
            page.wait_for_timeout(200)
            for ch in pw:
                pl.press_sequentially(ch, delay=50)

            page.wait_for_timeout(300)
            page.evaluate(
                "(function(){var c=document.querySelector(\"input[type='checkbox']\");"
                "if(c&&!c.checked)c.click();})()"
            )
            page.wait_for_timeout(200)
            page.evaluate(
                "(function(){var b=document.querySelector('button.MuiButton-sizeLarge');"
                "if(b)b.click();})()"
            )
            page.wait_for_timeout(8000)

            if "/login" in page.url:
                log("  login failed")
                br.close()
                return "login_failed"
            log("  logged in: " + page.url)

            # ── Activity page ──────────────────────────────────
            LAST.clear()
            page.goto("https://www.ip2free.com/cn/freeProxy?tab=activity",
                      wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(6000)

            tl = LAST.get("account/taskList", "")
            if tl:
                m = re.search(r'"task_id"\s*:\s*6.*?"finished_at"\s*:\s*"([^"]+)"', tl, re.S)
                if m and m.group(1) not in ("null",):
                    log("  ALREADY DONE: " + m.group(1))
                    br.close()
                    return "already_done"

            for attempt in range(15):
                log("  -- attempt " + str(attempt + 1) + " (cap_tries=" + str(captcha_attempts) + ") --")

                if captcha_attempts >= MAX_CAP_ATTEMPTS:
                    log("  hit MAX_CAP_ATTEMPTS, stopping to preserve rate limit")
                    result = "rate_limited"
                    break

                LAST.clear()
                FT_OK[0] = False

                page.evaluate("window.scrollTo(0,400)")
                page.wait_for_timeout(400)

                btn = page.evaluate(CLICK_CLAIM_JS)
                log("  btn: " + repr(btn))

                if not btn:
                    page.wait_for_timeout(3000)
                    tl2 = LAST.get("account/taskList", "")
                    if tl2:
                        m = re.search(r'"task_id"\s*:\s*6.*?"finished_at"\s*:\s*"([^"]+)"',
                                      tl2, re.S)
                        if m and m.group(1) not in ("null",):
                            log("  done: " + m.group(1))
                            result = "already_done"
                            break
                    if attempt == 0:
                        page.reload(wait_until="domcontentloaded", timeout=15000)
                        page.wait_for_timeout(6000)
                    continue

                page.wait_for_timeout(3500)

                if FT_OK[0] or '"code":0' in LAST.get("account/finishTask", ""):
                    result = "success"
                    break

                modal_n = page.evaluate(MODAL_VIS_JS)
                if not modal_n:
                    log("  no modal")
                    page.wait_for_timeout(2000)
                    continue

                blob_url = page.evaluate(GET_BLOB_JS)
                log("  blob: " + str(blob_url))

                if not blob_url or not blob_url.startswith("blob:"):
                    page.evaluate(ESC_JS)
                    page.wait_for_timeout(2000)
                    continue

                png = blob_to_png(page, blob_url)
                log("  PNG: " + str(len(png) if png else 0) + "b")

                # CRITICAL: 0-byte PNG = rate limited NOW, stop immediately
                if not png or len(png) < 100:
                    log("  PNG empty = re-rate-limited! Stopping this session.")
                    result = "rate_limited"
                    break

                candidates = ocr_png(png,
                    SS_DIR + "/" + email.split("@")[0] + "_a" + str(attempt) + ".png")

                if not candidates:
                    page.evaluate(ESC_JS)
                    page.wait_for_timeout(1500)
                    continue

                solved = False
                cur_blob = blob_url
                cur_cands = list(candidates)

                for cand in cur_cands:
                    cap = cand.strip()[:12]
                    log("  trying: " + repr(cap))
                    captcha_attempts += 1

                    fill_js = make_fill_js(cap)
                    filled = page.evaluate(fill_js)
                    log("  filled: " + repr(filled))
                    page.wait_for_timeout(500)

                    LAST.clear()
                    FT_OK[0] = False

                    sub = page.evaluate(SUBMIT_JS)
                    log("  sub: " + repr(sub))
                    page.wait_for_timeout(5000)

                    cc = LAST.get("account/checkCaptcha", "")
                    ft = LAST.get("account/finishTask", "")
                    log("  cc: " + cc[:80])
                    log("  ft: " + ft[:80])

                    if '"code":0' in cc or FT_OK[0]:
                        log("  captcha OK!")
                        if '"code":0' in ft or FT_OK[0]:
                            result = "success"
                            solved = True
                            break
                        # Re-trigger
                        page.evaluate(ESC_JS)
                        page.wait_for_timeout(2000)
                        LAST.clear()
                        FT_OK[0] = False
                        btn2 = page.evaluate(CLICK_CLAIM_JS)
                        log("  re-trigger btn: " + repr(btn2))
                        page.wait_for_timeout(4000)
                        ft2 = LAST.get("account/finishTask", "")
                        if '"code":0' in ft2 or FT_OK[0]:
                            log("  SUCCESS re-trigger!")
                            result = "success"
                            solved = True
                            break
                        log("  re-trigger failed: " + ft2[:60])
                    else:
                        # Check for new blob
                        still = page.evaluate(MODAL_VIS_JS)
                        if still:
                            page.wait_for_timeout(1000)
                            nb = page.evaluate(GET_BLOB_JS)
                            if nb and nb != cur_blob:
                                np2 = blob_to_png(page, nb)
                                if np2 and len(np2) > 100:
                                    nc = ocr_png(np2,
                                        SS_DIR + "/" + email.split("@")[0] + "_r" + str(attempt) + ".png")
                                    if nc:
                                        cur_cands = nc
                                        cur_blob = nb
                                        log("  new cands: " + str(nc))
                                elif np2 is None or len(np2 or b"") < 100:
                                    log("  new PNG empty = re-rate-limited!")
                                    result = "rate_limited"
                                    solved = True
                                    break
                                continue

                    if captcha_attempts >= MAX_CAP_ATTEMPTS and result != "success":
                        log("  MAX_CAP_ATTEMPTS reached after cand loop")
                        if result != "rate_limited":
                            result = "rate_limited"
                        solved = True
                        break

                if solved or result in ("success", "rate_limited"):
                    break

                page.evaluate(ESC_JS)
                page.wait_for_timeout(2000)

        except Exception as e:
            log("  ERR: " + str(e))
            import traceback
            traceback.print_exc(file=LOG_F)
        finally:
            try:
                page.screenshot(path=SS_DIR + "/" + email.split("@")[0] + "_final.png")
            except:
                pass
            br.close()

    return result


def main():
    log("\n" + "=" * 60)
    log("ip2free solve v4 | " + datetime.datetime.now().strftime("%H:%M:%S"))
    log("Max 3 captcha attempts/session, stop on 0-byte PNG")
    log("=" * 60)

    RESULTS = {}
    DONE = set()
    MAX_ROUNDS = 40
    CHECK_INTERVAL = 300  # 5 min between rounds

    for round_n in range(1, MAX_ROUNDS + 1):
        if len(DONE) >= 4:
            break
        log("\n" + "=" * 50)
        log("ROUND " + str(round_n) + " | " + datetime.datetime.now().strftime("%H:%M:%S") + " | done=" + str(len(DONE)) + "/4")
        log("=" * 50)

        any_available = False

        for email, pw, port in ACCOUNTS:
            if email in DONE:
                continue

            cap_sz, task_done = api_pre_check(email, pw, port)
            log(email + ": cap=" + str(cap_sz) + "b  done=" + str(task_done))

            if task_done:
                log("  -> already done!")
                DONE.add(email)
                RESULTS[email] = "already_done"
                continue

            if cap_sz <= 100:
                log("  -> blocked")
                continue

            any_available = True
            res = solve_account(email, pw, port)
            log("RESULT: " + email + " -> " + res)
            RESULTS[email] = res

            if res in ("success", "already_done"):
                DONE.add(email)
            elif res == "rate_limited":
                log("  rate limited again, will retry in " + str(CHECK_INTERVAL) + "s")

        pending = [a[0] for a in ACCOUNTS if a[0] not in DONE]
        log("Done: " + str(len(DONE)) + "/4  Pending: " + str(pending))

        if len(DONE) >= 4:
            log("ALL DONE!")
            break

        if pending:
            log("Sleeping " + str(CHECK_INTERVAL) + "s...")
            time.sleep(CHECK_INTERVAL)

    log("\nFINAL RESULTS:")
    for e, r in RESULTS.items():
        log("  " + str(r) + "  " + e)

    with open("/tmp/ip2free_solve_v4_result.json", "w") as f:
        json.dump(RESULTS, f, indent=2)
    LOG_F.close()


main()
