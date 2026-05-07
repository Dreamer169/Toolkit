"""
Outlook Email Factory v6
- Engine: patchright (undetected Playwright fork) + Xvfb headed mode
- CAPTCHA: called after EACH step (email, password, birthday, name)
  Primary:   Audio-STT (Google free STT via speech_recognition)
  Secondary: Keyboard down+hold+up (Enter/Space held in focused CAPTCHA iframe)
  Tertiary:  Mouse press-hold on press-again button
- speech_recognition must be installed: pip install SpeechRecognition
- ffmpeg must be on PATH
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
import os
import subprocess
import urllib.request
import base64
import tempfile

# ── Config ─────────────────────────────────────────────────────────────────────
VPS_PUSH_URL     = "http://45.205.27.69:8084/emails/push"
REGISTER_URL     = "https://outlook.live.com/mail/0/?prompt=create_account"
BOT_PROTECTION_WAIT = 11   # seconds before final submit
MAX_CAPTCHA_RETRIES = 2
POST_NAV_TIMEOUT = 60000   # ms
CAPTCHA_TIMEOUT  = 30000   # ms to wait for CAPTCHA to appear

# ── Bilingual selectors ─────────────────────────────────────────────────────────
TXT_AGREE_CONTINUE = re.compile(r"^\s*(同意并继续|Agree and continue|Continue|Next)\s*$")
TXT_USERNAME_TAKEN = re.compile(
    r"已被占用|该用户名不可用|username is taken|already (taken|exists|in use)"
    r"|is not available|cannot be used|Someone already has", re.IGNORECASE)
TXT_UNUSUAL_ACTIVITY = re.compile(r"一些异常活动|unusual activity|something went wrong", re.IGNORECASE)
SEL_EMAIL_INPUT   = '[aria-label="新建电子邮件"], [aria-label="New email"], [aria-label="New email address"]'
SEL_PASSWORD      = '[type="password"]'
SEL_PRIMARY_BTN   = '[data-testid="primaryButton"]'
SEL_IFRAME_CHALLENGE = ('iframe[title="验证质询"], iframe[title="Verification challenge"], '
                        'iframe[title*="challenge" i]')
ACCESSIBILITY_LABELS = [
    "可访问性挑战", "Accessible challenge", "Accessibility challenge",
    "Audio challenge", "轮椅",
]
SUCCESS_URL_KEYWORDS = (
    "account.live.com", "account.microsoft.com",
    "outlook.live.com", "outlook.com/mail",
    "login.live.com/login.srf", "login.live.com/ppsecure",
    "consent.live.com", "/owa/", "hotmail.com/mail",
)
SUCCESS_COOKIE_NAMES = (
    "RPSAuth", "MSPAuth", "MSPProf", "ESTSAUTH", "ESTSAUTHPERSISTENT",
    "PPAuth", "WLSSC", "MSCC", "MUID",
)
EN_MONTHS = ["","January","February","March","April","May","June",
             "July","August","September","October","November","December"]

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
             "Thomas","Christopher","Daniel","Matthew","Anthony","Mark","Steven",
             "Andrew","Joshua","Benjamin","Samuel","Patrick","Jack","Tyler","Aaron",
             "Nathan","Kyle","Bryan","Eric","Mary","Patricia","Jennifer","Linda",
             "Elizabeth","Susan","Jessica","Sarah","Karen","Lisa","Nancy","Ashley",
             "Emily","Donna","Michelle","Amanda","Melissa","Rebecca","Laura","Emma"]
    LAST  = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis",
             "Rodriguez","Martinez","Hernandez","Lopez","Wilson","Anderson","Thomas",
             "Taylor","Moore","Jackson","Lee","Perez","Thompson","White","Harris",
             "Clark","Ramirez","Lewis","Robinson","Walker","Young","Allen","King",
             "Wright","Scott","Torres","Nguyen","Hill","Green","Adams","Nelson",
             "Baker","Campbell","Mitchell","Carter","Turner","Phillips","Evans",
             "Collins","Stewart","Morales","Murphy","Cook","Rogers","Bennett"]
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

def find_ffmpeg():
    for path in ["/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg"]:
        if os.path.isfile(path):
            return path
    import glob as _g
    hits = _g.glob("/nix/store/*ffmpeg*/bin/ffmpeg")
    if hits:
        return hits[0]
    r = subprocess.run(["which", "ffmpeg"], capture_output=True, text=True, timeout=5)
    fp = r.stdout.strip()
    return fp if fp and os.path.isfile(fp) else None

FFMPEG = find_ffmpeg()

# ── CAPTCHA: check if CAPTCHA is actually visible on screen ────────────────────
def captcha_visible(page):
    """
    Returns True only if a PerimeterX CAPTCHA iframe is VISIBLY on screen.
    PerimeterX always loads a hidden/off-screen tracking iframe (x<0 or y<0).
    We only want to fire when the user-facing challenge is actually rendered.
    Checks: bounding box x>=0, y>=0, width>50, height>50.
    """
    try:
        sels = [
            # Prefer titled challenge iframes (most reliable signal)
            'iframe[title="验证质询"]',
            'iframe[title="Verification challenge"]',
            'iframe[title*="challenge" i]',
            # Fall back to src-based, but ONLY if on-screen
            'iframe[src*="hsprotect.net"]',
        ]
        for sel in sels:
            locs = page.locator(sel)
            n = locs.count()
            for i in range(n):
                try:
                    box = locs.nth(i).bounding_box(timeout=1000)
                    if box and box.get("x", -1) >= 0 and box.get("y", -1) >= 0 \
                            and box.get("width", 0) > 50 and box.get("height", 0) > 50:
                        print(f"[captcha] Visible challenge iframe ({sel}) "
                              f"at ({box['x']:.0f},{box['y']:.0f}) "
                              f"{box['width']:.0f}x{box['height']:.0f}", flush=True)
                        return True
                except Exception:
                    pass
    except Exception:
        pass
    return False

# ── CAPTCHA method 1: Audio-STT (Google free) ──────────────────────────────────
def try_audio_stt(page, step_name=""):
    """
    Click the accessibility/audio button, then capture the audio challenge,
    download it, convert with ffmpeg, transcribe with Google STT, submit.
    Returns True on success.
    """
    print(f"[captcha][{step_name}] Audio-STT attempt...", flush=True)
    if not captcha_visible(page):
        print(f"[captcha][{step_name}] No CAPTCHA visible, skipping Audio-STT", flush=True)
        return True  # no captcha = already passed

    # Step 1: Find and click accessibility button
    clicked = False
    for attempt in range(3):
        page.wait_for_timeout(2000)
        for fr in page.frames:
            try:
                for lbl in ACCESSIBILITY_LABELS:
                    loc = fr.locator(f'[aria-label="{lbl}"]')
                    if loc.count() > 0:
                        box = loc.first.bounding_box(timeout=2000)
                        if box:
                            cx = box["x"] + box["width"] / 2
                            cy = box["y"] + box["height"] / 2
                            page.mouse.click(cx, cy)
                            print(f"[captcha][{step_name}] Clicked [{lbl}] at ({cx:.0f},{cy:.0f})", flush=True)
                            clicked = True
                            break
            except Exception:
                pass
            if clicked:
                break
        if clicked:
            break
        print(f"[captcha][{step_name}] A11y button not found (attempt {attempt+1}/3)", flush=True)
        page.wait_for_timeout(2000)

    if not clicked:
        print(f"[captcha][{step_name}] Audio-STT: no accessibility button found", flush=True)
        return False

    # Step 2a: Screenshot + frame dump right after a11y click
    page.wait_for_timeout(2500)
    try:
        page.screenshot(path=f"/tmp/captcha_a11y_after_{step_name}.png")
        print(f"[captcha][{step_name}] Screenshot: /tmp/captcha_a11y_after_{step_name}.png", flush=True)
    except Exception:
        pass

    # Step 2b: Dump all non-empty frame contents (buttons, audio, iframes)
    print(f"[captcha][{step_name}] --- Frame dump after a11y click ---", flush=True)
    for _fi, fr in enumerate(page.frames):
        try:
            info = fr.evaluate("""() => ({
                url: window.location.href,
                bodyLen: document.body ? document.body.innerHTML.length : 0,
                buttons: Array.from(document.querySelectorAll(
                    'button,[role="button"],[role="tab"]')).slice(0,6).map(b=>({
                    text: b.textContent.trim().substring(0,30),
                    aria: b.getAttribute('aria-label')||'',
                    id: b.id||'', cls: b.className.substring(0,30)||''
                })),
                audios: Array.from(document.querySelectorAll('audio,audio source')).map(
                    a => a.src||a.currentSrc||a.getAttribute('src')||'').filter(x=>x),
                iframes: Array.from(document.querySelectorAll('iframe')).slice(0,4).map(
                    f=>({src:f.src||'',style:f.style.cssText.substring(0,40),title:f.title||''}))
            })""")
            if info.get("bodyLen", 0) > 300:
                url_s = info.get("url","")[:70]
                print(f"  Frame[{_fi}] {url_s} bodyLen={info.get('bodyLen',0)}", flush=True)
                if info.get("buttons"):
                    print(f"    buttons: {info['buttons']}", flush=True)
                if info.get("audios"):
                    print(f"    *** AUDIO: {info['audios']}", flush=True)
                if info.get("iframes"):
                    print(f"    iframes: {info['iframes']}", flush=True)
        except Exception:
            pass
    print(f"[captcha][{step_name}] --- End frame dump ---", flush=True)

    # Step 2c: Look for audio-tab button and click it (switch to audio mode)
    AUDIO_TAB_LABELS = [
        "音频", "Audio", "audio", "Sound", "Speaker",
        "听", "Play audio", "听觉", "Audio challenge",
    ]
    AUDIO_TAB_SELECTORS = [
        '[aria-label*="Audio" i]', '[aria-label*="audio" i]',
        '[aria-label*="Sound" i]', '[class*="audio"]',
        '[data-cy*="audio"]', '[id*="audio"]',
        '[role="tab"]:nth-child(2)',  # often the 2nd tab is audio
        'button[title*="audio" i]',
    ]
    audio_tab_clicked = False
    for fr in page.frames:
        try:
            for sel in AUDIO_TAB_SELECTORS:
                locs = fr.locator(sel)
                if locs.count() > 0:
                    box = locs.first.bounding_box(timeout=1000)
                    if box and box.get("x", -1) >= -100:  # allow slightly off
                        cx = box["x"] + box["width"] / 2
                        cy = box["y"] + box["height"] / 2
                        page.mouse.click(max(0, cx), max(0, cy))
                        print(f"[captcha][{step_name}] Audio-tab clicked ({sel}) at ({cx:.0f},{cy:.0f})", flush=True)
                        audio_tab_clicked = True
                        break
        except Exception:
            pass
        if audio_tab_clicked:
            break

    if audio_tab_clicked:
        page.wait_for_timeout(2000)

    # Step 2d: Try clicking "再次按下" / "Press again" to trigger audio fallback
    press_labels = ["再次按下", "Press again", "Press and hold", "Hold"]
    for fr in page.frames:
        try:
            for lbl in press_labels:
                loc = fr.locator(f'[aria-label="{lbl}"]')
                if loc.count() > 0:
                    box = loc.first.bounding_box(timeout=1000)
                    if box and box.get("x", -1) >= 0:
                        page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                        print(f"[captcha][{step_name}] Clicked [{lbl}] to trigger audio mode", flush=True)
                        break
        except Exception:
            pass

    page.wait_for_timeout(2000)

    # Step 2: Wait for audio element to appear in any frame (20s total)
    audio_url = None
    audio_frame = None
    print(f"[captcha][{step_name}] Waiting for audio element (20s)...", flush=True)
    for _wait_round in range(10):
        page.wait_for_timeout(2000)
        for fr in page.frames:
            try:
                info = fr.evaluate("""() => {
                    const a = document.querySelector('audio');
                    if (a) {
                        const src = a.src || a.currentSrc || '';
                        const children = Array.from(a.querySelectorAll('source'))
                                             .map(s => s.src || s.getAttribute('src') || '')
                                             .filter(x => x.length > 4);
                        return {src: src, children: children};
                    }
                    return null;
                }""")
                if info:
                    url = info.get("src") or (info.get("children") or [""])[0] or ""
                    if url and len(url) > 10:
                        audio_url = url
                        audio_frame = fr
                        print(f"[captcha][{step_name}] Audio URL: {audio_url[:80]}", flush=True)
                        break
            except Exception:
                pass
        if audio_url:
            break
        print(f"[captcha][{step_name}] No audio yet (round {_wait_round+1}/10)...", flush=True)

    if not audio_url:
        print(f"[captcha][{step_name}] Audio-STT: no audio element found after waiting", flush=True)
        return False

    # Step 3: Download audio (handle blob: URLs via in-frame fetch)
    tmp_mp3 = tempfile.mktemp(suffix=".mp3")
    try:
        if audio_url.startswith("blob:"):
            print(f"[captcha][{step_name}] Fetching blob URL via in-frame JS...", flush=True)
            data_b64 = audio_frame.evaluate(f"""async () => {{
                const resp = await fetch({json.dumps(audio_url)});
                const buf  = await resp.arrayBuffer();
                const bytes = new Uint8Array(buf);
                let s = '';
                for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
                return btoa(s);
            }}""")
            with open(tmp_mp3, "wb") as f:
                f.write(base64.b64decode(data_b64))
        else:
            print(f"[captcha][{step_name}] Downloading audio via HTTP...", flush=True)
            req = urllib.request.Request(audio_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                with open(tmp_mp3, "wb") as f:
                    f.write(resp.read())
        print(f"[captcha][{step_name}] Audio downloaded: {os.path.getsize(tmp_mp3)} bytes", flush=True)
    except Exception as e:
        print(f"[captcha][{step_name}] Audio download failed: {e}", flush=True)
        return False

    # Step 4: Convert to WAV with ffmpeg
    tmp_wav = tmp_mp3 + ".wav"
    if FFMPEG:
        try:
            subprocess.run(
                [FFMPEG, "-y", "-i", tmp_mp3, "-ar", "16000", "-ac", "1",
                 "-acodec", "pcm_s16le", tmp_wav],
                capture_output=True, timeout=20
            )
            print(f"[captcha][{step_name}] ffmpeg conversion done", flush=True)
        except Exception as e:
            print(f"[captcha][{step_name}] ffmpeg error: {e}, using original", flush=True)
            tmp_wav = tmp_mp3
    else:
        print(f"[captcha][{step_name}] ffmpeg not found, using original mp3", flush=True)
        tmp_wav = tmp_mp3

    # Step 5: Google STT (free, via speech_recognition)
    transcript = ""
    try:
        import speech_recognition as sr
        recognizer = sr.Recognizer()
        with sr.AudioFile(tmp_wav) as src:
            audio_data = recognizer.record(src)
        transcript = recognizer.recognize_google(audio_data, language="en-US")
        print(f"[captcha][{step_name}] STT result: '{transcript}'", flush=True)
    except Exception as e:
        print(f"[captcha][{step_name}] STT failed: {e}", flush=True)
        # Cleanup
        for f in [tmp_mp3, tmp_wav]:
            try: os.remove(f)
            except Exception: pass
        return False

    if not transcript:
        print(f"[captcha][{step_name}] Empty transcript", flush=True)
        return False

    # Cleanup temp files
    for f in [tmp_mp3, tmp_wav]:
        try: os.remove(f)
        except Exception: pass

    # Step 6: Submit transcript in CAPTCHA frame
    try:
        submitted = audio_frame.evaluate(f"""() => {{
            const input = document.querySelector(
                'input[type="text"], input[type="tel"], input[type="number"], '
                'input[placeholder], input:not([type="hidden"])');
            if (!input) return 'no_input';
            input.value = {json.dumps(transcript)};
            input.dispatchEvent(new Event('input', {{bubbles: true}}));
            input.dispatchEvent(new Event('change', {{bubbles: true}}));
            const btn = document.querySelector(
                'button[type="submit"], button[class*="submit"], '
                'button[class*="verify"], button[class*="confirm"]');
            if (btn) {{ btn.click(); return 'clicked_btn'; }}
            input.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter', keyCode: 13, bubbles: true}}));
            input.dispatchEvent(new KeyboardEvent('keyup',   {{key: 'Enter', keyCode: 13, bubbles: true}}));
            return 'enter_pressed';
        }}""")
        print(f"[captcha][{step_name}] Submit result: {submitted}", flush=True)
    except Exception as e:
        print(f"[captcha][{step_name}] Submit error: {e}", flush=True)
        return False

    page.wait_for_timeout(4000)
    if not captcha_visible(page):
        print(f"[captcha][{step_name}] Audio-STT PASSED ✓", flush=True)
        return True
    print(f"[captcha][{step_name}] CAPTCHA still visible after submission", flush=True)
    return False

# ── CAPTCHA method 2: Keyboard down+hold+up ────────────────────────────────────
def try_keyboard_hold(page, step_name=""):
    """
    Click into the CAPTCHA iframe to give it focus, then keyboard.down("Enter"),
    hold for 6 seconds, keyboard.up("Enter"). Works when PerimeterX is in
    accessibility keyboard mode.
    Returns True if CAPTCHA disappears.
    """
    print(f"[captcha][{step_name}] Keyboard hold attempt...", flush=True)
    if not captcha_visible(page):
        return True

    # First click accessibility button to enter keyboard mode
    clicked = False
    for fr in page.frames:
        try:
            for lbl in ACCESSIBILITY_LABELS:
                loc = fr.locator(f'[aria-label="{lbl}"]')
                if loc.count() > 0:
                    box = loc.first.bounding_box(timeout=2000)
                    if box:
                        cx = box["x"] + box["width"] / 2
                        cy = box["y"] + box["height"] / 2
                        page.mouse.click(cx, cy)
                        print(f"[captcha][{step_name}] Keyhold: clicked [{lbl}] at ({cx:.0f},{cy:.0f})", flush=True)
                        clicked = True
                        break
        except Exception:
            pass
        if clicked:
            break

    page.wait_for_timeout(2000)

    # Try to focus the hsprotect iframe and use keyboard hold
    focused = False
    try:
        iframe_loc = page.locator('iframe[src*="hsprotect.net"]').first
        box = iframe_loc.bounding_box(timeout=3000)
        if box:
            cx = box["x"] + box["width"] / 2
            cy = box["y"] + box["height"] / 2
            page.mouse.click(cx, cy)
            print(f"[captcha][{step_name}] Focused hsprotect iframe at ({cx:.0f},{cy:.0f})", flush=True)
            focused = True
    except Exception as e:
        print(f"[captcha][{step_name}] iframe focus failed: {e}", flush=True)

    page.wait_for_timeout(500)

    # Also try: find press-again/hold button in any frame and click it for focus
    press_labels = ["再次按下", "Press again", "Press and hold", "Hold"]
    for fr in page.frames:
        try:
            for lbl in press_labels:
                loc = fr.locator(f'[aria-label="{lbl}"]')
                if loc.count() > 0:
                    box = loc.first.bounding_box(timeout=2000)
                    if box:
                        cx = box["x"] + box["width"] / 2
                        cy = box["y"] + box["height"] / 2
                        page.mouse.move(cx, cy)
                        page.wait_for_timeout(300)
                        page.mouse.click(cx, cy)
                        print(f"[captcha][{step_name}] Keyhold: focused [{lbl}]", flush=True)
                        focused = True
                        break
        except Exception:
            pass
        if focused:
            break

    page.wait_for_timeout(500)

    # Keyboard hold: down for 6s
    for _key in ["Enter", " ", "Space"]:
        try:
            print(f"[captcha][{step_name}] keyboard.down('{_key}') for 6s...", flush=True)
            page.keyboard.down(_key)
            page.wait_for_timeout(6000)
            page.keyboard.up(_key)
            print(f"[captcha][{step_name}] keyboard.up('{_key}')", flush=True)
            page.wait_for_timeout(3000)
            if not captcha_visible(page):
                print(f"[captcha][{step_name}] Keyboard hold PASSED ✓", flush=True)
                return True
            break  # only try Enter
        except Exception as e:
            print(f"[captcha][{step_name}] keyboard hold error: {e}", flush=True)

    print(f"[captcha][{step_name}] Keyboard hold: CAPTCHA still visible", flush=True)
    return False

# ── CAPTCHA method 3: Mouse press-hold (existing approach) ─────────────────────
def try_mouse_hold(page, step_name=""):
    """Mouse press-hold on press-again button (works on residential IPs)."""
    print(f"[captcha][{step_name}] Mouse hold attempt...", flush=True)
    if not captcha_visible(page):
        return True

    press_labels = ["再次按下", "Press again", "Press and hold", "Hold"]
    HOLD_MS = random.randint(5000, 6000)

    for attempt in range(MAX_CAPTCHA_RETRIES + 1):
        # First click accessibility button
        for fr in page.frames:
            try:
                for lbl in ACCESSIBILITY_LABELS:
                    loc = fr.locator(f'[aria-label="{lbl}"]')
                    if loc.count() > 0:
                        box = loc.first.bounding_box(timeout=2000)
                        if box:
                            page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                            break
            except Exception:
                pass

        page.wait_for_timeout(2000)

        # Find and press-hold the "Press again" button
        held = False
        for fr in page.frames:
            try:
                for lbl in press_labels:
                    loc = fr.locator(f'[aria-label="{lbl}"]')
                    if loc.count() > 0:
                        box = loc.first.bounding_box(timeout=2000)
                        if box:
                            cx = box["x"] + box["width"] / 2
                            cy = box["y"] + box["height"] / 2
                            # Human-like approach
                            start_x = cx + random.uniform(-100, -50)
                            start_y = cy + random.uniform(-80, -40)
                            page.mouse.move(start_x, start_y)
                            for step in range(1, 10):
                                t = step / 9
                                page.mouse.move(
                                    start_x + (cx - start_x) * t + random.uniform(-2, 2),
                                    start_y + (cy - start_y) * t + random.uniform(-2, 2)
                                )
                                page.wait_for_timeout(random.randint(15, 30))
                            page.wait_for_timeout(random.randint(100, 200))
                            page.mouse.down()
                            elapsed = 0
                            while elapsed < HOLD_MS:
                                wait = random.randint(150, 300)
                                page.wait_for_timeout(wait)
                                elapsed += wait
                                page.mouse.move(
                                    cx + random.uniform(-2, 2),
                                    cy + random.uniform(-2, 2)
                                )
                            page.mouse.up()
                            print(f"[captcha][{step_name}] Mouse hold {elapsed}ms on [{lbl}]", flush=True)
                            held = True
                            break
            except Exception:
                pass
            if held:
                break

        page.wait_for_timeout(2500)
        if not captcha_visible(page):
            print(f"[captcha][{step_name}] Mouse hold PASSED ✓", flush=True)
            return True
        print(f"[captcha][{step_name}] Mouse hold attempt {attempt+1}: CAPTCHA still visible", flush=True)

    return False

# ── Master CAPTCHA bypass (tries all methods) ──────────────────────────────────
def bypass_captcha(page, step_name=""):
    """
    Called after each registration step button click.
    Returns True if no CAPTCHA or CAPTCHA was bypassed.
    """
    if not captcha_visible(page):
        return True  # no captcha, already good

    print(f"[captcha] CAPTCHA detected at step '{step_name}'", flush=True)

    # Method 1: Audio-STT
    try:
        if try_audio_stt(page, step_name):
            return True
    except Exception as e:
        print(f"[captcha][{step_name}] Audio-STT exception: {e}", flush=True)

    # Method 2: Keyboard hold
    try:
        if try_keyboard_hold(page, step_name):
            return True
    except Exception as e:
        print(f"[captcha][{step_name}] Keyboard hold exception: {e}", flush=True)

    # Method 3: Mouse press-hold
    try:
        if try_mouse_hold(page, step_name):
            return True
    except Exception as e:
        print(f"[captcha][{step_name}] Mouse hold exception: {e}", flush=True)

    print(f"[captcha] ALL methods failed at step '{step_name}'", flush=True)
    return False

# ── Main registration ──────────────────────────────────────────────────────────
def register_one():
    from patchright.sync_api import sync_playwright

    username, firstname, lastname = gen_email_username()
    password  = gen_password()
    year  = str(random.randint(1960, 2005))
    month = str(random.randint(1, 12))
    day   = str(random.randint(1, 28))
    wait_time = BOT_PROTECTION_WAIT * 1000  # ms

    print(f"[factory] Attempting: {username}@outlook.com", flush=True)
    print(f"[factory] Name: {firstname} {lastname}, DOB: {year}-{month}-{day}", flush=True)

    # ── Start Xvfb ────────────────────────────────────────────────────────────
    _xvfb_proc = None
    _headless   = True
    try:
        subprocess.run(["pkill", "-f", "Xvfb :99"], capture_output=True)
        time.sleep(0.5)
        _xvfb_proc = subprocess.Popen(
            ["Xvfb", ":99", "-screen", "0", "1280x800x24", "-ac"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(1.5)
        if _xvfb_proc.poll() is None:
            os.environ["DISPLAY"] = ":99"
            print("[factory] Xvfb started on :99, headless=False", flush=True)
            _headless = False
        else:
            print("[factory] Xvfb failed, headless=True", flush=True)
    except Exception as e:
        print(f"[factory] Xvfb error: {e}, headless=True", flush=True)

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
        ctx  = b.new_context(locale="en-US", viewport={"width": 1280, "height": 800})
        page = ctx.new_page()

        # ── Step 1: Open registration page ───────────────────────────────────
        print("[factory] Loading registration page...", flush=True)
        page.goto(REGISTER_URL, timeout=30000, wait_until="domcontentloaded")
        page.get_by_text(TXT_AGREE_CONTINUE).wait_for(timeout=30000)
        start_time = time.time()
        page.wait_for_timeout(int(0.1 * wait_time))
        page.get_by_text(TXT_AGREE_CONTINUE).click(timeout=30000)
        print("[factory] Clicked agree/continue", flush=True)

        # ── Step 2: Email username ─────────────────────────────────────────────
        email_input = page.locator(SEL_EMAIL_INPUT)
        email_input.wait_for(timeout=20000)
        email_input.click()
        email_input.type(username, delay=max(20, int(0.006 * wait_time)), timeout=15000)
        page.keyboard.press("Tab")
        page.wait_for_timeout(int(0.02 * wait_time))
        page.locator(SEL_PRIMARY_BTN).click(timeout=8000)
        page.wait_for_timeout(max(3000, int(0.05 * wait_time)))

        # Check for CAPTCHA at email step (early trigger)
        if captcha_visible(page):
            print("[factory] CAPTCHA at email step!", flush=True)
            if not bypass_captcha(page, "email"):
                return False, "CAPTCHA failed at email step", username, password

        # Handle username taken (up to 8 retries)
        for _attempt in range(8):
            if page.locator(SEL_PASSWORD).count() > 0:
                break
            if page.get_by_text(TXT_USERNAME_TAKEN).count() > 0:
                username, _, _ = gen_email_username()
                print(f"[factory] Username taken, switching to: {username}", flush=True)
                page.wait_for_timeout(1500)
                email_input = page.locator(SEL_EMAIL_INPUT)
                email_input.click()
                page.keyboard.press("Control+a")
                page.keyboard.press("Delete")
                email_input.type(username, delay=max(30, int(0.008 * wait_time)))
                page.keyboard.press("Tab")
                page.wait_for_timeout(int(0.03 * wait_time))
                page.locator(SEL_PRIMARY_BTN).click(timeout=8000)
                page.wait_for_timeout(max(4000, int(0.06 * wait_time)))
                if captcha_visible(page):
                    if not bypass_captcha(page, "email-retry"):
                        return False, "CAPTCHA failed at email-retry step", username, password
            else:
                page.wait_for_timeout(2000)
        else:
            if page.locator(SEL_PASSWORD).count() == 0:
                return False, "All usernames taken after 8 retries", username, password

        # ── Step 3: Password ──────────────────────────────────────────────────
        print("[factory] Filling password...", flush=True)
        pwd_loc = page.locator(SEL_PASSWORD)
        try:
            pwd_loc.wait_for(state="visible", timeout=20000)
        except Exception:
            # CAPTCHA might be blocking; handle it first
            if captcha_visible(page):
                if not bypass_captcha(page, "pre-password"):
                    return False, "CAPTCHA failed before password step", username, password
                pwd_loc.wait_for(state="visible", timeout=15000)
            else:
                return False, "Password field not found", username, password

        pwd_loc.click()
        pwd_loc.type(password, delay=int(0.004 * wait_time), timeout=35000)
        page.wait_for_timeout(int(0.02 * wait_time))
        page.locator(SEL_PRIMARY_BTN).click(timeout=8000)
        page.wait_for_timeout(2000)

        if captcha_visible(page):
            if not bypass_captcha(page, "password"):
                return False, "CAPTCHA failed at password step", username, password

        # ── Step 4: Birthday ──────────────────────────────────────────────────
        print("[factory] Filling birthday...", flush=True)
        page.wait_for_timeout(int(0.03 * wait_time))
        try:
            page.locator('[name="BirthYear"]').fill(year, timeout=20000)
        except Exception:
            if captcha_visible(page):
                if not bypass_captcha(page, "pre-birthday"):
                    return False, "CAPTCHA failed before birthday step", username, password
                page.locator('[name="BirthYear"]').fill(year, timeout=15000)
            else:
                return False, "Birthday year field not found", username, password

        try:
            page.wait_for_timeout(int(0.02 * wait_time))
            page.locator('[name="BirthMonth"]').select_option(value=month, timeout=1000)
            page.wait_for_timeout(int(0.05 * wait_time))
            page.locator('[name="BirthDay"]').select_option(value=day)
        except Exception:
            page.locator('[name="BirthMonth"]').wait_for(state="visible", timeout=10000)
            page.locator('[name="BirthMonth"]').evaluate('el => el.click()')
            page.wait_for_timeout(int(0.04 * wait_time))
            page.locator(date_option_selector(month, "月", is_month=True)).first.click()
            page.wait_for_timeout(int(0.04 * wait_time))
            page.locator('[name="BirthDay"]').evaluate('el => el.click()')
            page.wait_for_timeout(int(0.03 * wait_time))
            page.locator(date_option_selector(day, "日", is_month=False)).first.click()
            page.wait_for_timeout(int(0.02 * wait_time))
            page.locator(SEL_PRIMARY_BTN).click(timeout=5000)

        page.wait_for_timeout(2000)
        if captcha_visible(page):
            if not bypass_captcha(page, "birthday"):
                return False, "CAPTCHA failed at birthday step", username, password

        # ── Step 5: Name ──────────────────────────────────────────────────────
        print("[factory] Filling name...", flush=True)
        try:
            page.locator('#lastNameInput').wait_for(state="visible", timeout=20000)
        except Exception:
            if captcha_visible(page):
                if not bypass_captcha(page, "pre-name"):
                    return False, "CAPTCHA failed before name step", username, password
                page.locator('#lastNameInput').wait_for(state="visible", timeout=15000)
            else:
                return False, "Name field not found", username, password

        page.locator('#lastNameInput').type(lastname, delay=int(0.002 * wait_time), timeout=20000)
        page.wait_for_timeout(int(0.02 * wait_time))
        page.locator('#firstNameInput').fill(firstname, timeout=20000)

        # Enforce bot_protection_wait
        elapsed = time.time() - start_time
        if elapsed < BOT_PROTECTION_WAIT:
            page.wait_for_timeout(int((BOT_PROTECTION_WAIT - elapsed) * 1000))

        page.locator(SEL_PRIMARY_BTN).click(timeout=5000)
        print("[factory] Submitted name form, waiting...", flush=True)

        # Wait for privacy link to detach
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

        if captcha_visible(page):
            if not bypass_captcha(page, "post-name"):
                try:
                    page.screenshot(path=f"/tmp/captcha_fail_{username}.png")
                except Exception:
                    pass
                return False, "CAPTCHA failed at post-name step", username, password

        # ── Step 7: Verify success ─────────────────────────────────────────────
        def has_success_cookie():
            try:
                return any(
                    (c.get("name") or "").strip() in SUCCESS_COOKIE_NAMES
                    for c in page.context.cookies()
                )
            except Exception:
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
