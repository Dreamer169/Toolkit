"""
Outlook Email Factory v5
- Engine: patchright (undetected Playwright fork)
- CAPTCHA: 1) Accessibility challenge button (primary) 2) Enter key + blob/collector detection (fallback)
- Selectors: copied verbatim from battle-tested outlook_register.py v8.22
- Output: RESULT: {"email":..., "password":..., "status":"ok"|"fail", "reason":...}
- Pushes to VPS queue at http://45.205.27.69:8084/emails/push on success
"""
import random
import re
import secrets
import string
import sys
import time
import json
import urllib.request

# ── Config ─────────────────────────────────────────────────────────────────────
VPS_PUSH_URL = "http://45.205.27.69:8084/emails/push"
REGISTER_URL = "https://outlook.live.com/mail/0/?prompt=create_account"
BOT_PROTECTION_WAIT = 11   # seconds
MAX_CAPTCHA_RETRIES = 2
POST_NAV_TIMEOUT = 60000   # ms

# ── Bilingual selectors (copied from outlook_register.py v8.22) ────────────────
TXT_AGREE_CONTINUE = re.compile(r"^\s*(同意并继续|Agree and continue|Continue|Next)\s*$")
TXT_USERNAME_TAKEN = re.compile(
    r"已被占用|该用户名不可用|username is taken|already (taken|exists|in use)"
    r"|is not available|cannot be used|Someone already has", re.IGNORECASE)
TXT_UNUSUAL_ACTIVITY = re.compile(r"一些异常活动|unusual activity|something went wrong", re.IGNORECASE)
SEL_EMAIL_INPUT = '[aria-label="新建电子邮件"], [aria-label="New email"], [aria-label="New email address"]'
SEL_IFRAME_CHALLENGE = 'iframe[title="验证质询"], iframe[title="Verification challenge"], iframe[title*="challenge" i]'
SEL_A11Y_CHALLENGE = '[aria-label="可访问性挑战"], [aria-label="Accessible challenge"], [aria-label="Accessibility challenge"], [aria-label="Audio challenge"]'
SEL_PRESS_AGAIN = '[aria-label="再次按下"], [aria-label="Press again"], [aria-label="Press and hold"]'
SUCCESS_URL_KEYWORDS = (
    "account.live.com", "account.microsoft.com",
    "outlook.live.com", "outlook.com/mail",
    "login.live.com/login.srf", "login.live.com/ppsecure",
    "consent.live.com", "/owa/", "hotmail.com/mail",
)
SUCCESS_COOKIE_NAMES = (
    "RPSAuth", "MSPAuth", "MSPProf", "ESTSAUTH", "ESTSAUTHPERSISTENT",
    "PPAuth", "WLSSC", "MSCC", "MUID", "ANON",
)
EN_MONTHS = ["", "January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December"]

ACCESSIBILITY_LABELS = [
    "可访问性挑战", "Accessible challenge", "Accessibility challenge",
    "Audio challenge", "轮椅",
]
JS_A11Y_SELECTORS = [
    'button[class*="audio"]', 'button[class*="accessible"]',
    'button[class*="accessibility"]',
    '[data-cy="accessibility-challenge-tab"]',
    '[data-cy="audio-challenge-tab"]',
    'button[id*="audio"]', 'button[id*="accessible"]',
    'button[aria-label*="Audio"]', 'button[aria-label*="audio"]',
    'button[aria-label*="Accessible"]', 'button[aria-label*="accessible"]',
]

# ── Helpers ────────────────────────────────────────────────────────────────────
def gen_password():
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        pw = "".join(secrets.choice(chars) for _ in range(random.randint(12, 16)))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw)
                and any(c in "!@#$%^&*" for c in pw)):
            return pw

def gen_email_username():
    FIRST = ["James","John","Robert","Michael","William","David","Richard","Joseph",
             "Thomas","Christopher","Daniel","Matthew","Anthony","Mark","Steven","Paul",
             "Andrew","Joshua","Benjamin","Samuel","Patrick","Jack","Tyler","Aaron",
             "Nathan","Kyle","Bryan","Eric","Mary","Patricia","Jennifer","Linda",
             "Elizabeth","Susan","Jessica","Sarah","Karen","Lisa","Nancy","Ashley",
             "Emily","Donna","Michelle","Amanda","Melissa","Rebecca","Laura",
             "Emma","Olivia","Liam","Noah","Ava","Sophia","Isabella","Lucas","Ethan"]
    LAST  = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis",
             "Rodriguez","Martinez","Hernandez","Lopez","Wilson","Anderson","Thomas",
             "Taylor","Moore","Jackson","Lee","Perez","Thompson","White","Harris",
             "Clark","Ramirez","Lewis","Robinson","Walker","Young","Allen","King",
             "Wright","Scott","Torres","Nguyen","Hill","Green","Adams","Nelson",
             "Baker","Campbell","Mitchell","Carter","Turner","Phillips","Evans",
             "Collins","Stewart","Morales","Murphy","Cook","Rogers","Bennett",
             "Gray","Hughes","Patel","Parker","Flores","Rivera","Gomez","Diaz"]
    fn = random.choice(FIRST)
    ln = random.choice(LAST)
    y2 = str(random.randint(70, 99))
    n3 = str(random.randint(100, 999))
    rc = ''.join(random.choices('abcdefghjkmnpqrstuvwxyz', k=3))
    patterns = [
        fn.lower() + "." + ln.lower() + y2,
        fn.lower() + "_" + ln.lower() + y2,
        fn[0].lower() + ln.lower() + y2,
        fn.lower() + ln.lower() + n3,
        fn[0].lower() + "." + ln.lower() + n3,
        fn.lower() + "." + ln[0].lower() + y2,
        fn.lower() + rc + n3,
        fn[0].lower() + ln.lower() + rc,
    ]
    return random.choice(patterns), fn, ln

def date_option_selector(value, zh_suffix, is_month=False):
    en_text = EN_MONTHS[int(value)] if is_month else value
    return f'[role="option"]:text-is("{value}{zh_suffix}"), [role="option"]:text-is("{en_text}")'

def push_to_vps(email_full, password):
    data = json.dumps({"email": email_full, "password": password}).encode()
    req = urllib.request.Request(
        VPS_PUSH_URL, data=data,
        headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}

# ── Human press-hold (copied from outlook_register.py v8.22) ──────────────────
def human_press_hold(page, cx, cy, hold_ms_min=4400, hold_ms_max=5300):
    _sx = cx + random.uniform(-130, -60) * random.choice([-1, 1])
    _sy = cy + random.uniform(-90, -40) * random.choice([-1, 1])
    page.mouse.move(_sx, _sy)
    _steps = random.randint(8, 14)
    for _i in range(1, _steps + 1):
        _t = _i / _steps
        _ix = _sx + (cx - _sx) * _t + random.uniform(-1.8, 1.8)
        _iy = _sy + (cy - _sy) * _t + random.uniform(-1.8, 1.8)
        try:
            page.mouse.move(_ix, _iy)
        except Exception:
            pass
        page.wait_for_timeout(random.randint(8, 24))
    page.wait_for_timeout(random.randint(80, 220))
    page.mouse.down()
    _hold_total = random.randint(hold_ms_min, hold_ms_max)
    _elapsed = 0
    while _elapsed < _hold_total:
        _wait = random.randint(180, 360)
        page.wait_for_timeout(_wait)
        _elapsed += _wait
        try:
            page.mouse.move(cx + random.uniform(-2.2, 2.2), cy + random.uniform(-2.2, 2.2))
        except Exception:
            pass
    page.mouse.up()
    return _hold_total

# ── Enter-key CAPTCHA bypass (copied from outlook_register.py v8.22) ──────────
def try_enter_challenge(page, blob_urls=None):
    """Enter-key CAPTCHA bypass.
    blob_urls: list pre-populated by a page.on('request') listener; if non-empty,
    blob URL was already seen and we skip the wait_for_event call.
    """
    print("[captcha] Trying Enter-key method...", flush=True)
    blob_already_seen = bool(blob_urls)
    if not blob_already_seen:
        print("[captcha] Waiting for blob URL (up to 22s)...", flush=True)
        try:
            page.wait_for_event(
                "request",
                lambda req: req.url.startswith("blob:https://iframe.hsprotect.net/"),
                timeout=22000,
            )
            blob_already_seen = True
        except Exception:
            print("[captcha] No blob URL in 22s, Enter-key skipped", flush=True)
            return False
    else:
        print(f"[captcha] Blob URL already captured ({len(blob_urls)}), proceeding...", flush=True)

    print("[captcha] blob URL detected, starting Enter-key method", flush=True)
    page.wait_for_timeout(800)

    for _t in range(MAX_CAPTCHA_RETRIES + 1):
        page.keyboard.press("Enter")
        page.wait_for_timeout(11500)
        page.keyboard.press("Enter")

        try:
            page.wait_for_event(
                "request",
                lambda req: req.url.startswith(
                    "https://browser.events.data.microsoft.com"),
                timeout=8000,
            )
            try:
                page.wait_for_event(
                    "request",
                    lambda req: req.url.startswith(
                        "https://collector-pxzc5j78di.hsprotect.net/assets/js/bundle"),
                    timeout=1700,
                )
                page.wait_for_timeout(2000)
                print(f"[captcha] Enter attempt {_t+1}: CAPTCHA reloaded, retry", flush=True)
                continue
            except Exception:
                if page.get_by_text(TXT_UNUSUAL_ACTIVITY).count():
                    return False
                print(f"[captcha] Enter-key PASSED on attempt {_t+1}!", flush=True)
                return True
        except Exception:
            page.wait_for_timeout(5000)
            page.keyboard.press("Enter")
            try:
                page.wait_for_event(
                    "request",
                    lambda req: req.url.startswith(
                        "https://browser.events.data.microsoft.com"),
                    timeout=10000,
                )
                try:
                    page.wait_for_event(
                        "request",
                        lambda req: req.url.startswith(
                            "https://collector-pxzc5j78di.hsprotect.net/assets/js/bundle"),
                        timeout=4000,
                    )
                except Exception:
                    print(f"[captcha] Enter-key secondary PASSED attempt {_t+1}!", flush=True)
                    return True
            except Exception:
                pass
        page.wait_for_timeout(500)
    return False

# ── Accessibility CAPTCHA bypass (copied from outlook_register.py v8.22) ───────
def try_accessibility_challenge(page):
    try:
        page.wait_for_selector(SEL_IFRAME_CHALLENGE, timeout=12000)
    except Exception:
        print("[captcha] No CAPTCHA iframe found, assuming passed", flush=True)
        return True

    for attempt in range(MAX_CAPTCHA_RETRIES + 1):
        page.wait_for_timeout(1000)
        print(f"[captcha] Accessibility attempt {attempt+1}/{MAX_CAPTCHA_RETRIES+1}...", flush=True)

        # Wait for CAPTCHA game to load (up to 25s)
        print("[captcha] Waiting for CAPTCHA game to load...", flush=True)
        page.wait_for_timeout(3000)
        for _wait in range(22):
            for _fr in page.frames:
                try:
                    enabled = _fr.evaluate("""
                        () => {
                            const btn = document.querySelector(
                                '[aria-label="可访问性挑战"], [aria-label="Accessible challenge"], [aria-label="Audio challenge"]'
                            );
                            if (!btn) return null;
                            return { disabled: btn.getAttribute('aria-disabled') };
                        }
                    """)
                    if enabled and enabled.get('disabled') != 'true':
                        break
                except Exception:
                    pass
            else:
                page.wait_for_timeout(1000)
                continue
            break

        # Scan all frames for a11y button
        all_frames = page.frames
        print(f"[captcha] Scanning {len(all_frames)} frames...", flush=True)
        frame2 = None
        best_frame = None
        for fr in all_frames:
            try:
                url = fr.url
                print(f"[captcha]   frame: {url[:80]}", flush=True)
                if "hsprotect.net" in url and best_frame is None:
                    best_frame = fr
                for lbl in ACCESSIBILITY_LABELS:
                    if fr.locator(f'[aria-label="{lbl}"]').count() > 0:
                        print(f"[captcha] Found a11y [{lbl}] in frame: {url[:60]}", flush=True)
                        frame2 = fr
                        break
                if frame2:
                    break
            except Exception:
                pass

        if frame2 is None:
            frame2 = best_frame
            if frame2:
                print("[captcha] Using best_frame (hsprotect.net)", flush=True)

        # frame_locator fallback when no frame found via page.frames
        if frame2 is None:
            print("[captcha] No frame via page.frames, trying frame_locator...", flush=True)
            frame1 = page.frame_locator(SEL_IFRAME_CHALLENGE)
            INNER_SELS = [
                'iframe[style*="display: block"]', 'iframe[style*="display:block"]',
                'iframe[tabindex="0"]', 'iframe:first-child', 'iframe',
            ]
            for _isel in INNER_SELS:
                try:
                    _f2 = frame1.frame_locator(_isel)
                    _a = _f2.locator(SEL_A11Y_CHALLENGE)
                    if _a.count() > 0:
                        print(f"[captcha] frame_locator found a11y: {_isel}", flush=True)
                        box = _a.first.bounding_box(timeout=5000)
                        if box:
                            page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
                            print("[captcha] Clicked a11y (frame_locator)", flush=True)
                            page.wait_for_timeout(2000)
                            # Try press-again
                            try:
                                _pa = _f2.locator(SEL_PRESS_AGAIN)
                                _pb = _pa.first.bounding_box(timeout=5000)
                                if _pb and _pb['width'] > 0:
                                    _hd = human_press_hold(page, _pb['x']+_pb['width']/2, _pb['y']+_pb['height']/2)
                                    print(f"[captcha] Press-hold {_hd}ms (frame_locator)", flush=True)
                            except Exception:
                                pass
                            return True
                except Exception:
                    pass
            continue

        # Click the a11y button
        clicked = False
        for lbl in ACCESSIBILITY_LABELS:
            try:
                loc = frame2.locator(f'[aria-label="{lbl}"]')
                if loc.count() == 0:
                    continue
                # Force-enable button
                frame2.evaluate(f"""
                    () => {{
                        const btn = document.querySelector('[aria-label="{lbl}"]');
                        if (btn) {{
                            btn.removeAttribute('aria-disabled');
                            btn.removeAttribute('disabled');
                            btn.style.opacity = '1';
                            btn.style.pointerEvents = 'auto';
                        }}
                    }}
                """)
                box = loc.bounding_box(timeout=5000)
                if box and box['width'] > 0:
                    cx = box['x'] + box['width']/2
                    cy = box['y'] + box['height']/2
                    page.mouse.move(cx-5, cy-3)
                    page.wait_for_timeout(200)
                    page.mouse.click(cx, cy)
                    print(f"[captcha] Mouse-clicked [{lbl}] at ({cx:.0f},{cy:.0f})", flush=True)
                    clicked = True
                    break
            except Exception as e:
                print(f"[captcha] Click [{lbl}] failed: {e}", flush=True)

        if not clicked:
            for sel in JS_A11Y_SELECTORS:
                try:
                    r = frame2.evaluate(
                        f'() => {{ const el = document.querySelector({repr(sel)}); '
                        f'if (el) {{ el.click(); return true; }} return false; }}'
                    )
                    if r:
                        print(f"[captcha] JS click: {sel}", flush=True)
                        clicked = True
                        break
                except Exception:
                    pass

        if not clicked:
            print("[captcha] A11y click failed, next attempt", flush=True)
            continue

        print("[captcha] A11y button clicked", flush=True)
        page.wait_for_timeout(2000)

        # Poll for hsprotect frames (up to 22s) then click press-again
        print("[captcha] Polling for hsprotect frames with press-again button...", flush=True)
        _poll_start = time.time()
        _hsp_frames = []
        while time.time() - _poll_start < 22:
            _tmp = []
            for _fr in page.frames:
                try:
                    _info = _fr.evaluate(
                        "() => ({ href: window.location.href, "
                        "bodyLen: document.body ? document.body.innerHTML.length : 0 })"
                    )
                    if 'hsprotect.net' in _info.get('href', '') and _info.get('bodyLen', 0) > 3000:
                        _tmp.append((_info['bodyLen'], _fr))
                except Exception:
                    pass
            if _tmp:
                _hsp_frames = _tmp
                print(f"[captcha] Found {len(_hsp_frames)} hsprotect frames", flush=True)
                break
            page.wait_for_timeout(1000)

        _press_clicked = False
        if _hsp_frames:
            _hsp_frames.sort(reverse=True)
            for _blen, _sfr in _hsp_frames:
                for lbl in ["再次按下", "Press again", "Press and hold"]:
                    try:
                        _loc = _sfr.locator(f'[aria-label="{lbl}"]')
                        if _loc.count() == 0:
                            continue
                        _box = _loc.first.bounding_box(timeout=3000)
                        if _box and _box['width'] > 0:
                            _hd = human_press_hold(page, _box['x']+_box['width']/2, _box['y']+_box['height']/2)
                            print(f"[captcha] Press-hold [{lbl}] {_hd}ms", flush=True)
                            _press_clicked = True
                            break
                    except Exception:
                        pass
                if _press_clicked:
                    break

        # frame_locator fallback for press-again
        if not _press_clicked:
            try:
                _f1 = page.frame_locator(SEL_IFRAME_CHALLENGE)
                for _isel in ['iframe[style*="display: block"]', 'iframe[style*="display:block"]', 'iframe']:
                    try:
                        _f2 = _f1.frame_locator(_isel)
                        _pa = _f2.locator(SEL_PRESS_AGAIN)
                        if _pa.count() > 0:
                            _pb = _pa.first.bounding_box(timeout=3000)
                            if _pb and _pb['width'] > 0:
                                _hd = human_press_hold(page, _pb['x']+_pb['width']/2, _pb['y']+_pb['height']/2)
                                print(f"[captcha] Press-hold (frame_locator) {_hd}ms", flush=True)
                                _press_clicked = True
                                break
                    except Exception:
                        pass
                if _press_clicked:
                    pass
            except Exception:
                pass

        page.wait_for_timeout(3000)
        if page.locator(SEL_IFRAME_CHALLENGE).count() == 0:
            print("[captcha] CAPTCHA iframe gone — accessibility succeeded!", flush=True)
            return True
        if any(k in page.url for k in SUCCESS_URL_KEYWORDS):
            print("[captcha] Success URL detected after a11y", flush=True)
            return True
        print(f"[captcha] Attempt {attempt+1} done, CAPTCHA still present", flush=True)

    return False

# ── Main registration function ──────────────────────────────────────────────────
def register_one():
    from patchright.sync_api import sync_playwright

    username, firstname, lastname = gen_email_username()
    password = gen_password()
    year = str(random.randint(1960, 2005))
    month = str(random.randint(1, 12))
    day = str(random.randint(1, 28))
    wait_time = BOT_PROTECTION_WAIT * 1000  # ms

    print(f"[factory] Attempting: {username}@outlook.com", flush=True)
    print(f"[factory] Name: {firstname} {lastname}, DOB: {year}-{month}-{day}", flush=True)

    # ── Start Xvfb virtual display for headed Chromium ────────────────────────
    # headed=True makes PerimeterX visual CAPTCHA fully render (blob URL appears)
    import subprocess as _sp, os as _os
    _xvfb_proc = None
    _display = ":99"
    try:
        _sp.run(["pkill", "-f", "Xvfb :99"], capture_output=True)
        import time as _t; _t.sleep(0.5)
        _xvfb_proc = _sp.Popen(
            ["Xvfb", _display, "-screen", "0", "1280x800x24", "-ac"],
            stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
        )
        _t.sleep(1.5)
        if _xvfb_proc.poll() is None:
            _os.environ["DISPLAY"] = _display
            print(f"[factory] Xvfb started on {_display}, using headed=False with DISPLAY", flush=True)
            _headless = False
        else:
            print("[factory] Xvfb failed to start, using headless=True", flush=True)
            _headless = True
    except Exception as _xe:
        print(f"[factory] Xvfb error: {_xe}, using headless=True", flush=True)
        _headless = True

    p = sync_playwright().start()
    b = p.chromium.launch(
        headless=_headless,
        args=[
            "--lang=en-US,en",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--disable-extensions",
            "--disable-gpu" if _headless else "--use-gl=egl",
            "--disable-software-rasterizer",
            "--disable-web-security",
            "--no-first-run",
            "--no-default-browser-check",
            "--ignore-certificate-errors",
            "--allow-running-insecure-content",
            "--mute-audio",
        ],
    )

    try:
        ctx = b.new_context(locale="en-US", viewport={"width": 1280, "height": 800})
        page = ctx.new_page()

        # ── Blob URL listener (installed early to catch visual CAPTCHA requests) ──
        blob_urls = []
        def _on_request(req):
            if req.url.startswith("blob:https://iframe.hsprotect.net/"):
                if req.url not in blob_urls:
                    blob_urls.append(req.url)
                    print(f"[captcha] blob URL captured: {req.url[:80]}", flush=True)
        page.on("request", _on_request)

        # ── Step 1: Open registration page ───────────────────────────────────
        print("[factory] Loading registration page...", flush=True)
        page.goto(REGISTER_URL, timeout=30000, wait_until="domcontentloaded")
        page.get_by_text(TXT_AGREE_CONTINUE).wait_for(timeout=30000)
        start_time = time.time()
        page.wait_for_timeout(0.1 * wait_time)
        page.get_by_text(TXT_AGREE_CONTINUE).click(timeout=30000)
        print("[factory] Clicked agree/continue", flush=True)

        # ── Step 2: Email username ────────────────────────────────────────────
        email_input = page.locator(SEL_EMAIL_INPUT)
        email_input.wait_for(timeout=20000)
        email_input.click()
        email_input.type(username, delay=max(20, 0.006 * wait_time), timeout=15000)
        page.keyboard.press("Tab")
        page.wait_for_timeout(0.02 * wait_time)
        page.locator('[data-testid="primaryButton"]').click(timeout=8000)
        page.wait_for_timeout(max(3000, 0.05 * wait_time))

        # Handle username taken (up to 8 retries)
        username_accepted = page.locator('[type="password"]').count() > 0
        for _attempt in range(8):
            if username_accepted:
                print(f"[factory] Username accepted: {username}", flush=True)
                break
            if page.locator('[type="password"]').count() > 0:
                username_accepted = True
                break
            taken = page.get_by_text(TXT_USERNAME_TAKEN).count() > 0
            if taken:
                username, _, _ = gen_email_username()
                print(f"[factory] Username taken, switching to: {username}", flush=True)
                page.wait_for_timeout(1500)
                email_input = page.locator(SEL_EMAIL_INPUT)
                email_input.click()
                page.keyboard.press("Control+a")
                page.keyboard.press("Delete")
                email_input.type(username, delay=max(30, 0.008 * wait_time))
                page.keyboard.press("Tab")
                page.wait_for_timeout(0.03 * wait_time)
                page.locator('[data-testid="primaryButton"]').click(timeout=8000)
                page.wait_for_timeout(max(4000, 0.06 * wait_time))
            else:
                page.wait_for_timeout(2000)
                if page.locator('[type="password"]').count() > 0:
                    username_accepted = True
        else:
            return False, "All usernames taken after 8 retries", username, password

        if not username_accepted:
            return False, "Username not accepted", username, password

        # ── Step 3: Password ──────────────────────────────────────────────────
        print("[factory] Filling password...", flush=True)
        pwd_loc = page.locator('[type="password"]')
        pwd_loc.wait_for(state="visible", timeout=35000)
        pwd_loc.click()
        pwd_loc.type(password, delay=0.004 * wait_time, timeout=35000)
        page.wait_for_timeout(0.02 * wait_time)
        page.locator('[data-testid="primaryButton"]').click(timeout=8000)

        # ── Step 4: Birthday ──────────────────────────────────────────────────
        print("[factory] Filling birthday...", flush=True)
        page.wait_for_timeout(0.03 * wait_time)
        page.locator('[name="BirthYear"]').fill(year, timeout=20000)
        try:
            page.wait_for_timeout(0.02 * wait_time)
            page.locator('[name="BirthMonth"]').select_option(value=month, timeout=1000)
            page.wait_for_timeout(0.05 * wait_time)
            page.locator('[name="BirthDay"]').select_option(value=day)
        except Exception:
            # FluentUI Dropdown: label intercepts pointer events — use JS click to bypass
            page.locator('[name="BirthMonth"]').wait_for(state="visible", timeout=10000)
            page.locator('[name="BirthMonth"]').evaluate('el => el.click()')
            page.wait_for_timeout(0.04 * wait_time)
            page.locator(date_option_selector(month, "月", is_month=True)).first.click()
            page.wait_for_timeout(0.04 * wait_time)
            page.locator('[name="BirthDay"]').evaluate('el => el.click()')
            page.wait_for_timeout(0.03 * wait_time)
            page.locator(date_option_selector(day, "日", is_month=False)).first.click()
            page.wait_for_timeout(0.02 * wait_time)
            page.locator('[data-testid="primaryButton"]').click(timeout=5000)

        # ── Step 5: Name ──────────────────────────────────────────────────────
        print("[factory] Filling name...", flush=True)
        page.locator('#lastNameInput').wait_for(state="visible", timeout=20000)
        page.locator('#lastNameInput').type(lastname, delay=0.002 * wait_time, timeout=20000)
        page.wait_for_timeout(0.02 * wait_time)
        page.locator('#firstNameInput').fill(firstname, timeout=20000)

        # Enforce bot_protection_wait
        elapsed = time.time() - start_time
        if elapsed < BOT_PROTECTION_WAIT:
            page.wait_for_timeout((BOT_PROTECTION_WAIT - elapsed) * 1000)

        page.locator('[data-testid="primaryButton"]').click(timeout=5000)
        print("[factory] Submitted form, waiting for CAPTCHA...", flush=True)

        # Wait for privacy link to detach (signals CAPTCHA is loading)
        try:
            page.locator(
                'span > [href="https://go.microsoft.com/fwlink/?LinkID=521839"]'
            ).wait_for(state="detached", timeout=22000)
        except Exception:
            pass

        page.wait_for_timeout(400)

        if page.get_by_text(TXT_UNUSUAL_ACTIVITY).count():
            return False, "IP flagged for unusual activity", username, password
        if page.locator("iframe#enforcementFrame").count() > 0:
            return False, "Wrong CAPTCHA type (enforcementFrame)", username, password

        # ── Step 6: CAPTCHA bypass ────────────────────────────────────────────
        print("[factory] Starting CAPTCHA bypass...", flush=True)
        # Enter-key method first (blob URL may have been seen during form submission)
        # blob_urls is pre-populated by the listener installed at page creation
        if blob_urls:
            print(f"[captcha] Pre-captured blob URL ({len(blob_urls)}), trying Enter-key first...", flush=True)
            captcha_ok = try_enter_challenge(page, blob_urls)
        else:
            # Try accessibility challenge first, Enter-key fallback
            captcha_ok = try_accessibility_challenge(page)
            if not captcha_ok:
                print("[captcha] A11y failed, falling back to Enter-key...", flush=True)
                captcha_ok = try_enter_challenge(page, blob_urls)
        if not captcha_ok:
            try:
                page.screenshot(path=f"/tmp/captcha_fail_{username}.png")
            except Exception:
                pass
            return False, "CAPTCHA failed (both methods)", username, password

        # ── Step 7: Verify registration success ───────────────────────────────
        def has_success_cookie():
            try:
                for c in page.context.cookies():
                    if (c.get("name") or "").strip() in SUCCESS_COOKIE_NAMES:
                        return True
            except Exception:
                pass
            return False

        def is_success():
            return (any(k in page.url for k in SUCCESS_URL_KEYWORDS)
                    or has_success_cookie())

        ok_signal = False
        try:
            page.wait_for_url(
                lambda u: any(x in u for x in SUCCESS_URL_KEYWORDS),
                timeout=POST_NAV_TIMEOUT,
            )
            print(f"[factory] Success URL: {page.url[:100]}", flush=True)
            ok_signal = True
        except Exception:
            if has_success_cookie():
                print("[factory] Success cookie detected", flush=True)
                ok_signal = True
            else:
                try:
                    page.wait_for_selector(
                        '[data-testid="ocid-login"], [aria-label="Outlook"], '
                        '.welcome-msg, #mectrl_headerPicture, '
                        '[data-testid="appConsentPrimaryButton"], #idSIButton9, '
                        '#KmsiCheckboxField, [data-task="consent"], #appName',
                        timeout=8000,
                    )
                    print("[factory] DOM success marker found", flush=True)
                    ok_signal = True
                except Exception:
                    pass
                if not ok_signal:
                    page.wait_for_timeout(2500)
                    if is_success():
                        print(f"[factory] Secondary poll success: {page.url[:100]}", flush=True)
                        ok_signal = True

        if not ok_signal:
            try:
                page.screenshot(path=f"/tmp/reg_fail_{username}.png")
            except Exception:
                pass
            return False, f"No success signal (URL: {page.url[:80]})", username, password

        return True, "success", username, password

    finally:
        try:
            b.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass
        if _xvfb_proc is not None:
            try:
                _xvfb_proc.terminate()
            except Exception:
                pass

# ── Entry point ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ok, reason, username, password = register_one()
    email_full = username + "@outlook.com"
    if ok:
        vps_result = push_to_vps(email_full, password)
        result = {
            "status": "ok",
            "email": email_full,
            "password": password,
            "reason": reason,
            "vps": vps_result,
        }
    else:
        result = {
            "status": "fail",
            "email": email_full,
            "password": password,
            "reason": reason,
        }
    print(f"RESULT: {json.dumps(result)}", flush=True)
