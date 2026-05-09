#!/usr/bin/env python3
"""
ip2free 监控 + 自动求解器 v2.0 — 修复 JS 双大括号 + 使用 locator fill
"""
import sys, json, time, os, base64, datetime
sys.path.insert(0, "/root/Toolkit/artifacts/api-server")
sys.path.insert(0, "/root/Toolkit/scripts")
try:
    import resi_pool as _rpool
    _HAS_RPOOL = True
except ImportError:
    _HAS_RPOOL = False

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
    """Fast OCR using only ddddocr (easyocr is too slow and inaccurate for these captchas).
    Tries: beta-orig, std-orig, beta-upscale3x, std-upscale3x.
    RGBA→RGB conversion with white background before processing.
    """
    if save_path:
        with open(save_path, "wb") as f:
            f.write(png_bytes)
    results = []
    import io, tempfile, os
    from PIL import Image, ImageEnhance
    # RGBA→RGB with white background (captcha uses transparency)
    try:
        base_img = Image.open(io.BytesIO(png_bytes))
        if base_img.mode == "RGBA":
            bg = Image.new("RGB", base_img.size, (255, 255, 255))
            bg.paste(base_img, mask=base_img.split()[3])
            base_img = bg
        else:
            base_img = base_img.convert("RGB")
        w, h = base_img.size
        # 3x upscale — helps ddddocr read small 120×40 Chinese characters
        upscale = base_img.resize((w * 3, h * 3), Image.LANCZOS)
        _tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        upscale.save(_tmp.name)
        _tmp.close()
        up_bytes = open(_tmp.name, "rb").read()
        os.unlink(_tmp.name)
    except Exception as e:
        log(f"    OCR preprocess err: {e}")
        up_bytes = png_bytes
    # ddddocr: try beta then std, on orig + upscaled
    for label, ocr in [("beta", _ocr_beta), ("std", _ocr_std)]:
        if ocr is None:
            continue
        for lbl2, bts in [("orig", png_bytes), ("up3x", up_bytes)]:
            try:
                txt = ocr.classification(bts).strip()
                log(f"    OCR dddd[{label}/{lbl2}]: {txt!r}")
                if txt and txt not in results:
                    results.append(txt)
            except Exception as e:
                log(f"    OCR dddd[{label}/{lbl2}] err: {e}")
    return results

def blob_to_png(page, blob_url):
    # Capture captcha via getBoundingClientRect clip screenshot
    import time as _t2
    _dlg_sel = '[role="dialog"]'
    GET_RECT_JS = ("(function(blobUrl) {"
        "var dialogs=Array.from(document.querySelectorAll('" + _dlg_sel + "')).filter(function(x){return x.offsetParent!==null;});"
        "var d=dialogs[0];if(!d){return null;}"
        "var img=d.querySelector('img.captcha-img');"
        "if(!img){var all=Array.from(d.querySelectorAll('img'));"
        "img=all.find(function(i){return i.naturalWidth>60;})"
        "||all.find(function(i){return i.src===blobUrl;})"
        "||null;}"
        "if(!img){return null;}"
        "var r=img.getBoundingClientRect();"
        "return {x:r.left,y:r.top,width:r.width,height:r.height,"
        "nw:img.naturalWidth,nh:img.naturalHeight};})(arguments[0])")
    for _w in range(10):
        try:
            rect = page.evaluate(GET_RECT_JS, blob_url)
            if rect and rect.get('width', 0) > 30 and rect.get('height', 0) > 15:
                clip = {k: rect[k] for k in ('x', 'y', 'width', 'height')}
                png_bytes = page.screenshot(clip=clip)
                if png_bytes and len(png_bytes) > 300:
                    log(f"    captcha rect: {rect['nw']}x{rect['nh']} {len(png_bytes)}b")
                    return png_bytes
            else:
                log(f"    rect not ready ({rect}), retrying...")
        except Exception as _e:
            log(f"    rect err {_w}: {_e}")
        _t2.sleep(0.4)
    return None

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
    var img = d.querySelector('img.captcha-img');
    if (!img) {
        var allImgs = Array.from(d.querySelectorAll('img'));
        img = allImgs.find(function(i) { return i.naturalWidth > 60; })
             || allImgs.find(function(i) { return i.src && i.src.startsWith('blob:'); })
             || allImgs[0];
    }
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


def _kill_zombie_chrome():
    """Kill leaked chrome-headless-shell processes and stale /tmp profile dirs."""
    import subprocess, glob, shutil, time
    try:
        subprocess.run(["pkill", "-f", "chrome-headless-shell"], capture_output=True, timeout=5)
        time.sleep(0.5)
        subprocess.run(["pkill", "-9", "-f", "chrome-headless-shell"], capture_output=True, timeout=5)
    except Exception:
        pass
    # Clean up stale profile dirs older than 10 minutes
    import os, time as _t
    cutoff = _t.time() - 600
    for d in glob.glob("/tmp/playwright_chromiumdev_profile-*"):
        try:
            if os.path.getmtime(d) < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass



def _safe_wait(page, ms: int) -> bool:
    """wait_for_timeout that survives TargetClosedError (browser crash/close)."""
    try:
        page.wait_for_timeout(ms)
        return True
    except Exception:
        return False


def _navigate_spa(page, url: str, timeout_ms: int = 15000) -> bool:
    """
    SPA-safe navigation: use JS window.location for React Router pages.
    Falls back to page.goto() if JS navigation doesn't change URL.
    Returns True if navigation succeeded.
    """
    import time as _t
    try:
        current = page.url
        page.evaluate(f"window.location.href = {repr(url)}")
        deadline = _t.time() + timeout_ms / 1000
        while _t.time() < deadline:
            try:
                if page.url != current:
                    return True
                page.wait_for_timeout(300)
            except Exception:
                return False
        # fallback: hard goto
        page.goto(url, wait_until="commit", timeout=timeout_ms)
        return True
    except Exception as e:
        log(f"    _navigate_spa err: {e}")
        return False



def solve_one(email, pw, port):
    from patchright.sync_api import sync_playwright
    log(f"\n  → SOLVING {email} port={port}")

    LAST_API = {}
    FINISH_SUCCESS = [False]

    _kill_zombie_chrome()
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
                        # Capture captcha code from URL query param (?code=UUID)
                        # needed for checkCaptcha POST body
                        try:
                            from urllib.parse import urlparse, parse_qs
                            _qp = parse_qs(urlparse(resp.url).query)
                            _cap_code = (_qp.get("code") or [None])[0]
                            if _cap_code:
                                LAST_API["captcha_code"] = _cap_code
                                log(f"    <- captcha PNG code={_cap_code}")
                            else:
                                log(f"    <- captcha PNG (no code in URL: {resp.url})")
                        except Exception as _ce:
                            log(f"    <- captcha PNG (url parse err: {_ce})")
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
                    # Extract captcha UUID from JSON account/captcha response
                    # Server may return {"data":"UUID","code":0} or {"data":"java.lang.Object@...","code":-202}
                    if name == "account/captcha":
                        try:
                            # Log response headers — UUID may be in a custom header
                            _hdrs = dict(resp.headers) if resp.headers else {}
                            _hdr_log = {k: v for k, v in _hdrs.items()
                                        if k.lower() not in ("content-type","content-length","date",
                                                              "server","vary","access-control-allow-origin")}
                            if _hdr_log:
                                log(f"    <- captcha resp extra-headers: {str(_hdr_log)[:200]}")
                        except:
                            pass
                        try:
                            import json as _jj
                            _cj = _jj.loads(txt)
                            _cdata = (_cj.get("data") or "")
                            if _cdata and isinstance(_cdata, str) and not _cdata.startswith("java.lang.Object"):
                                LAST_API["captcha_code"] = _cdata
                                log(f"    captcha UUID from JSON: {_cdata}")
                        except:
                            pass
                except:
                    pass

        def on_request(req):
            if "api.ip2free.com/api" in req.url:
                rname = req.url.split("/api/")[-1].split("?")[0]
                _skip = ("ad/getList", "account/profile", "wallet/balance",
                         "ip/freeList", "coupon/my", "website/link", "account/taskList")
                if rname not in _skip:
                    try:
                        pd = req.post_data or ""
                        _url_tail = req.url.split("api.ip2free.com")[-1][-90:]
                        log(f"    -> {rname} REQ url={_url_tail} body={pd[:120]}")
                    except:
                        pass

        ctx.on("response", on_response)
        ctx.on("request", on_request)
        page = ctx.new_page()
        result = "failed"

        try:
            # ── Login ──────────────────────────────────────────────
            page.goto("https://www.ip2free.com/cn/login",
                      wait_until="domcontentloaded", timeout=25000)
            _safe_wait(page, 3000)
            page.wait_for_selector("#email", timeout=12000)

            # ip2free MUI input starts as readOnly — click triggers React onFocus to make editable
            page.locator("#email").click()
            _safe_wait(page, 800)
            # Force-remove readOnly via JS if React hasn't cleared it yet
            page.evaluate("""
                (function() {
                    var inp = document.querySelector('#email');
                    if (inp && inp.readOnly) {
                        Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
                        inp.removeAttribute('readonly');
                        inp.readOnly = false;
                        inp.dispatchEvent(new Event('focus', {bubbles: true}));
                        inp.dispatchEvent(new Event('click', {bubbles: true}));
                    }
                })()
            """)
            _safe_wait(page, 300)
            page.locator("#email").fill(email)
            page.locator("#password").click()
            page.wait_for_timeout(300)
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
            _safe_wait(page, 8000)

            if "/login" in page.url:
                log("    ❌ login failed — URL still login")
                br.close()
                return "login_failed"
            log(f"    ✅ logged in: {page.url}")

            # ── Activity page (SPA-safe navigation) ─────────────────
            # Save login token BEFORE clearing LAST_API — linkClick needs it below
            _saved_login_raw = LAST_API.get("account/login", "")
            LAST_API.clear()
            log("    navigating to freeProxy activity page...")
            _navigate_spa(page, "https://www.ip2free.com/cn/freeProxy?tab=activity", 15000)
            _safe_wait(page, 6000)

            # Check if task already done
            tl = LAST_API.get("account/taskList", "")
            import re
            if tl:
                m = re.search(r'"task_id"\s*:\s*6.*?"finished_at"\s*:\s*"([^"]+)"', tl, re.S)
                if m and m.group(1) not in ("null",):
                    log(f"    already done: {m.group(1)}")
                    br.close()
                    return "already_done"

            # linkClick: ip2free requires visiting ad-link before captcha image is served
            try:
                # Token from login API response saved BEFORE LAST_API.clear()
                import json as _json
                _login_raw = _saved_login_raw
                _tok = ""
                if _login_raw:
                    try: _tok = (_json.loads(_login_raw).get("data") or {}).get("token", "")
                    except: pass
                if not _tok:
                    _tok = page.evaluate("window.localStorage.getItem('Mall-token') || ''")
                log(f"    linkClick tok: {(_tok or 'EMPTY')[:20]}...")
                # Fetch link list
                _wl = page.request.get(
                    "https://api.ip2free.com/api/website/link",
                    headers={
                        "X-Token": _tok, "Domain": "www.ip2free.com",
                        "WebName": "IP2FREE", "Lang": "cn",
                        "AffId": "", "InviteCode": "", "ServiceId": "",
                    },
                )
                _wl_j = _wl.json()
                _cats = (_wl_j.get("data") or {}).get("categories") or []
                # Real path: cats[0].subCategories[0].links[0].id
                _lid = None
                for _cat in _cats:
                    for _sub in (_cat.get("subCategories") or []):
                        _links = _sub.get("links") or []
                        if _links:
                            _lid = _links[0]["id"]
                            break
                    if _lid: break
                if _lid:
                    # linkClick = plain GET (no auth), like window.open
                    _lc = page.request.get(
                        f"https://api.ip2free.com/api/website/linkClick?id={_lid}",
                    )
                    log(f"    linkClick id={_lid} status={_lc.status}")
                    _safe_wait(page, 2500)
                else:
                    log(f"    linkClick: no links found in {str(_wl_j)[:80]}")
            except Exception as _lce:
                log(f"    linkClick err: {_lce}")

            _current_cap_uuid = [""]
            for attempt in range(12):
                log(f"    ── attempt {attempt+1}/12 ──")
                _saved_cap = LAST_API.get("captcha_code", "") or _current_cap_uuid[0]
                LAST_API.clear()
                FINISH_SUCCESS[0] = False
                if _saved_cap:
                    LAST_API["captcha_code"] = _saved_cap

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

                # --- Get captcha PNG via blob URL ---
                blob_url = page.evaluate(GET_BLOB_URL_JS)
                log(f"    blob: {blob_url}")

                if not blob_url or not blob_url.startswith("blob:"):
                    log("    no blob url, closing modal")
                    page.evaluate(CLOSE_MODAL_JS)
                    page.wait_for_timeout(2000)
                    continue

                # Extract UUID from blob URL: blob:https://domain/UUID
                _blob_uuid = blob_url.split("/")[-1] if blob_url else ""
                if _blob_uuid:
                    LAST_API["captcha_code"] = _blob_uuid
                    _current_cap_uuid[0] = _blob_uuid
                    log(f"    captcha UUID from blob: {_blob_uuid}")

                # on_response already saves PNG to file; blob fetch returns 0b due to browser security
                import os as _os, time as _tm2
                _cap_file = f"{SS_DIR}/cap_{email.split(chr(64))[0]}.png"
                png_bytes = None
                if _os.path.exists(_cap_file) and _tm2.time() - _os.path.getmtime(_cap_file) < 30:
                    with open(_cap_file, "rb") as _cf:
                        png_bytes = _cf.read()
                    log(f"    PNG from file: {len(png_bytes)}b")
                if not png_bytes or len(png_bytes) < 100:
                    png_bytes = blob_to_png(page, blob_url)
                    log(f"    PNG from blob fallback: {len(png_bytes) if png_bytes else 0}b")

                if not png_bytes or len(png_bytes) < 100:
                    _empty_png_count = _empty_png_count + 1 if "_empty_png_count" in dir() else 1
                    log(f"    PNG empty (#{_empty_png_count}), closing modal")
                    page.evaluate(CLOSE_MODAL_JS)
                    page.wait_for_timeout(2000)
                    if _empty_png_count >= 2:
                        log("    2x empty PNG → rate-limited; doing fresh login to reset session")
                        result = "captcha_blocked"
                        break
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
                    _saved_cap2 = LAST_API.get("captcha_code", "") or _current_cap_uuid[0]
                    LAST_API.clear()
                    FINISH_SUCCESS[0] = False
                    if _saved_cap2:
                        LAST_API["captcha_code"] = _saved_cap2

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

                    # Direct API fallback: if browser didn't return checkCaptcha code=0,
                    # call it manually using the captured captcha UUID
                    # API format: {"captcha": UUID_session_code, "code": OCR_text_typed}
                    if not cc or '"code":0' not in cc:
                        _cap_code = LAST_API.get("captcha_code", "")
                        if _cap_code and cap_clean:
                            log(f"    checkCaptcha fallback: captcha(UUID)={_cap_code[:20]} code(text)={cap_clean!r}")
                            try:
                                _cc_resp = page.request.post(
                                    "https://api.ip2free.com/api/account/checkCaptcha",
                                    headers={
                                        "X-Token": _tok, "Domain": "www.ip2free.com",
                                        "WebName": "IP2FREE", "Lang": "cn",
                                        "Content-Type": "application/json",
                                    },
                                    data=json.dumps({"captcha": _cap_code, "code": cap_clean}),
                                )
                                cc = _cc_resp.text()
                                LAST_API["account/checkCaptcha"] = cc
                                log(f"    checkCaptcha fallback resp: {cc[:120]}")
                            except Exception as _ccfe:
                                log(f"    checkCaptcha fallback err: {_ccfe}")
                        else:
                            log(f"    checkCaptcha fallback skipped: uuid={_cap_code!r} text={cap_clean!r}")

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
                                # Try saved file first (fresh write by on_response), else blob
                                import os as _os2, time as _tm3
                                _ncap_file = f"{SS_DIR}/cap_{email.split(chr(64))[0]}.png"
                                new_png = None
                                if _os2.path.exists(_ncap_file) and _tm3.time() - _os2.path.getmtime(_ncap_file) < 30:
                                    with open(_ncap_file, "rb") as _ncf:
                                        new_png = _ncf.read()
                                    log(f"    new PNG from file: {len(new_png)}b")
                                if not new_png or len(new_png) < 100:
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



def fetch_and_inject_proxies(email, pw, port):
    """After successful ip2free task, fetch free proxy list and inject into resi_pool."""
    if not _HAS_RPOOL:
        log("  [inject] resi_pool unavailable, skipping")
        return 0
    try:
        s = api_session(port)
        lr = s.post(f"{BASE_API}/api/account/login?",
            data=json.dumps({"email": email, "password": pw}),
            headers={"Content-Type": "text/plain;charset=UTF-8"}, timeout=15)
        tok = lr.json().get("data", {}).get("token")
        if not tok:
            log(f"  [inject] login failed for {email}")
            return 0
        s.headers["x-token"] = tok
        resp = s.get(f"{BASE_API}/api/ip/freeList", timeout=12)
        data = resp.json()
        raw = data.get("data", {})
        proxies = (raw.get("free_ip_list") if isinstance(raw, dict) else raw) or []
        injected = 0
        for item in proxies:
            h = (item.get("ip") or item.get("host") or item.get("addr") or "").strip()
            p_raw = item.get("port", 0)
            try:
                p = int(str(p_raw).split(":")[0])
            except (ValueError, TypeError):
                continue
            if not h or not p:
                continue
            if _rpool.add_external(h, p, probe=False):
                injected += 1
                log(f"  [inject] + {h}:{p}")
        log(f"  [inject] {injected}/{len(proxies)} proxies from {email} injected into resi_pool")
        return injected
    except Exception as e:
        log(f"  [inject] error: {e}")
        return 0


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

            # NOTE: Cloudflare blocks direct API captcha fetch → always use patchright browser
            log(f"  → launching patchright to solve captcha...")
            res = solve_one(email, pw, port)
            log(f"  RESULT: {email} → {res}")
            RESULTS[email] = res

            if res == "success":
                log(f"  SUCCESS {email}, fetching free proxies for resi_pool...")
                fetch_and_inject_proxies(email, pw, port)
                DONE.add(email)
            elif res == "already_done":
                DONE.add(email)

        pending = [a[0] for a in ACCOUNTS if a[0] not in DONE]
        log(f"\nDone: {len(DONE)}/5  Pending: {pending}")

        if len(DONE) >= 5:
            log("🎉 ALL ACCOUNTS COMPLETE!")
            break

        if pending:
            # If ALL pending accounts returned captcha_blocked, sleep until UTC+8 midnight reset
            pending_results = [RESULTS.get(e, "") for e in pending]
            all_blocked = all(r == "captcha_blocked" for r in pending_results if r)
            if all_blocked and len(pending_results) > 0:
                import datetime as _dt
                _now = _dt.datetime.utcnow()
                _reset = _now.replace(hour=16, minute=5, second=0, microsecond=0)
                if _now.hour >= 16:
                    _reset += _dt.timedelta(days=1)
                _wait = max((_reset - _now).total_seconds(), 300)
                log(f"All accounts captcha-blocked; sleeping {_wait/3600:.1f}h until {_reset.strftime('%Y-%m-%d %H:%M UTC')} (UTC+8 reset)")
                time.sleep(_wait)
            else:
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
