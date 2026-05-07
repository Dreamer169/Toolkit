#!/usr/bin/env python3
"""
yahoo_email_factory_v2.py  -- Sandbox-A: Yahoo Mail account generator
======================================================================
* Routes Playwright through obvious.ai sandbox socks5 proxy (local port)
  → bypasses Yahoo's datacenter E500 block
* Phone verification via SMS-Activate (service=ya)
* Pushes {email, password} to VPS relay queue (port 8084)

Environment:
  PROXY_PORT       — local socks5 port (e.g. 10837 for us-auto-5) REQUIRED
  SMS_ACTIVATE_KEY — sms-activate.org API key (required for phone step)
  VPS_API          — default http://45.205.27.69:8084
  SMS_COUNTRY      — int country code (0=any, 12=US, 6=Indonesia) default 0
  SANDBOX_LABEL    — label for logging (e.g. us-auto-5)
"""

import json, os, random, re, secrets, string, time, urllib.request

PROXY_PORT   = os.environ.get("PROXY_PORT", "")
SMS_API_BASE = "https://api.sms-activate.org/stubs/handler_api.php"
SMS_KEY      = os.environ.get("SMS_ACTIVATE_KEY", "")
SMS_COUNTRY  = int(os.environ.get("SMS_COUNTRY", "0"))
VPS_API      = os.environ.get("VPS_API", "http://45.205.27.69:8084")
VPS_PUSH     = f"{VPS_API}/emails/push"
SANDBOX      = os.environ.get("SANDBOX_LABEL", f"proxy:{PROXY_PORT}")

PHONE_POLL_INTERVAL = 5
PHONE_POLL_MAX      = 90   # seconds to wait for SMS code

FIRST_NAMES = [
    "James","John","Robert","Michael","William","David","Richard","Joseph",
    "Thomas","Christopher","Daniel","Matthew","Anthony","Mark","Steven",
    "Andrew","Joshua","Benjamin","Samuel","Patrick","Jack","Tyler","Aaron",
    "Brian","Kevin","Jason","Jeffrey","Ryan","Gary","Larry","Scott","Eric",
]
LAST_NAMES = [
    "Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis",
    "Rodriguez","Martinez","Wilson","Anderson","Thomas","Taylor","Moore",
    "Jackson","Lee","Thompson","White","Harris","Clark","Ramirez","Lewis",
    "Robinson","Walker","Perez","Hall","Young","Allen","King","Wright","Scott",
]


def gen_username(fn, ln):
    ts  = str(int(time.time()))[-6:]
    n4  = str(random.randint(1000, 9999))
    rc3 = "".join(random.choices("abcdefghjkmnpqrstvwxyz", k=3))
    pat = random.choice([
        fn.lower() + ln.lower() + ts,
        fn[0].lower() + ln.lower() + ts,
        fn.lower() + rc3 + n4,
        rc3 + ts + "z",
    ])
    return pat[:30]


def gen_password():
    chars = string.ascii_letters + string.digits + "!@#$%"
    while True:
        pw = "".join(secrets.choice(chars) for _ in range(random.randint(13, 16)))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw) and any(c in "!@#$%" for c in pw)):
            return pw


def sms_call(params):
    qs  = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{SMS_API_BASE}?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return r.read().decode().strip()
    except Exception as e:
        return f"ERROR:{e}"


def sms_get_number():
    resp = sms_call({"api_key": SMS_KEY, "action": "getNumber",
                     "service": "ya", "country": SMS_COUNTRY})
    if not resp.startswith("ACCESS_NUMBER:"):
        raise RuntimeError(f"sms-activate getNumber: {resp}")
    parts = resp.split(":")
    return parts[1], parts[2]


def sms_poll_code(act_id):
    deadline = time.time() + PHONE_POLL_MAX
    while time.time() < deadline:
        resp = sms_call({"api_key": SMS_KEY, "action": "getStatus", "id": act_id})
        if resp.startswith("STATUS_OK:"):
            return resp.split(":", 1)[1]
        if resp in ("STATUS_WAIT_CODE", "STATUS_WAIT_RETRY"):
            time.sleep(PHONE_POLL_INTERVAL)
            continue
        if resp == "STATUS_CANCEL":
            return None
        time.sleep(PHONE_POLL_INTERVAL)
    return None


def sms_finish(act_id):
    sms_call({"api_key": SMS_KEY, "action": "setStatus", "status": "6", "id": act_id})


def sms_cancel(act_id):
    sms_call({"api_key": SMS_KEY, "action": "setStatus", "status": "8", "id": act_id})


def vps_push(email, password, username_hint=None):
    try:
        payload = {"email": email, "password": password,
                   "platform": "yahoo", "sandbox": SANDBOX}
        if username_hint:
            payload["username"] = username_hint
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(VPS_PUSH, data=data,
                                      headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


TAKEN_RE = re.compile(r"not available|already taken|try something else", re.I)


def run_factory():
    if not PROXY_PORT:
        print("[yahoo] ERROR: PROXY_PORT not set — set to sandbox socks5 local port", flush=True)
        return {"status": "fail", "reason": "PROXY_PORT_missing"}

    from patchright.sync_api import sync_playwright

    fn       = random.choice(FIRST_NAMES)
    ln       = random.choice(LAST_NAMES)
    username = gen_username(fn, ln)
    password = gen_password()
    year     = str(random.randint(1972, 1999))
    month    = str(random.randint(1, 12)).zfill(2)
    day      = str(random.randint(1, 28)).zfill(2)
    act_id   = None

    result = {"status": "fail", "email": f"{username}@yahoo.com",
              "password": password, "reason": "unknown", "sandbox": SANDBOX}

    proxy_url = f"socks5://127.0.0.1:{PROXY_PORT}"
    print(f"[yahoo:{SANDBOX}] {username}@yahoo.com  {fn} {ln}  DOB={year}-{month}-{day}", flush=True)
    print(f"[yahoo:{SANDBOX}] proxy={proxy_url}", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            proxy={"server": proxy_url},
            args=["--no-sandbox", "--disable-dev-shm-usage", "--lang=en-US",
                  "--disable-blink-features=AutomationControlled"],
        )
        try:
            ctx  = browser.new_context(
                locale="en-US",
                viewport={"width": 1280, "height": 800},
                proxy={"server": proxy_url},
            )
            page = ctx.new_page()

            # Load Yahoo create page
            print(f"[yahoo:{SANDBOX}] Loading Yahoo signup...", flush=True)
            page.goto("https://login.yahoo.com/account/create",
                      timeout=45000, wait_until="domcontentloaded")
            time.sleep(random.uniform(2.5, 4.0))

            current_url = page.url
            print(f"[yahoo:{SANDBOX}] URL: {current_url[:100]}", flush=True)

            if "error" in current_url or "E500" in current_url:
                result["reason"] = f"yahoo_blocked: {current_url}"
                print(f"[yahoo:{SANDBOX}] BLOCKED: {current_url}", flush=True)
                page.screenshot(path=f"/tmp/yahoo_blocked_{SANDBOX}.png")
                return result

            # Fill form fields
            FIELDS = [
                ("input#firstName,input[name=firstName]",      fn,       "firstname"),
                ("input#lastName,input[name=lastName]",        ln,       "lastname"),
                ("input#usernameInput,input[name=userId]",     username, "username"),
                ("input[type=password],input[name=password]",  password, "password"),
                ("input[name=mm],input#mm",                    month,    "month"),
                ("input[name=dd],input#dd",                    day,      "day"),
                ("input[name=yyyy],input#yyyy",                year,     "year"),
            ]
            filled = 0
            for sel, val, label in FIELDS:
                try:
                    loc = page.locator(sel).first
                    loc.wait_for(state="visible", timeout=10000)
                    loc.click()
                    time.sleep(random.uniform(0.2, 0.5))
                    loc.fill(val)
                    print(f"[yahoo:{SANDBOX}]   {label}: {val}", flush=True)
                    time.sleep(random.uniform(0.3, 0.7))
                    filled += 1
                except Exception as e:
                    print(f"[yahoo:{SANDBOX}]   {label} err: {str(e)[:80]}", flush=True)

            if filled == 0:
                page.screenshot(path=f"/tmp/yahoo_form_fail_{SANDBOX}.png")
                result["reason"] = "form_fields_not_found"
                return result

            page.screenshot(path=f"/tmp/yahoo_form_{SANDBOX}.png")
            print(f"[yahoo:{SANDBOX}] Form filled ({filled}/7)", flush=True)

            # Handle username taken
            time.sleep(1.5)
            for _retry in range(6):
                if page.get_by_text(TAKEN_RE).count() == 0:
                    break
                username = gen_username(fn, ln)
                result["email"] = f"{username}@yahoo.com"
                try:
                    em = page.locator("input#usernameInput,input[name=userId]").first
                    em.fill(username)
                    print(f"[yahoo:{SANDBOX}]   username retry #{_retry+1}: {username}", flush=True)
                    time.sleep(1.5)
                except Exception:
                    pass

            # Submit form
            submitted = False
            for sel in ["button#reg-submit-btn", "button[type=submit]", "input[type=submit]"]:
                try:
                    btn = page.locator(sel).first
                    if btn.count() > 0:
                        btn.wait_for(state="visible", timeout=5000)
                        btn.click()
                        print(f"[yahoo:{SANDBOX}] Submitted via: {sel}", flush=True)
                        submitted = True
                        break
                except Exception:
                    pass
            if not submitted:
                page.keyboard.press("Enter")
                print(f"[yahoo:{SANDBOX}] Submitted via Enter", flush=True)

            time.sleep(7)
            page.screenshot(path=f"/tmp/yahoo_after_submit_{SANDBOX}.png")
            url_now = page.url
            print(f"[yahoo:{SANDBOX}] URL after submit: {url_now[:100]}", flush=True)

            # Direct mailbox (phone skipped for some accounts)
            if "yahoo.com/mail" in url_now or "mail.yahoo.com" in url_now:
                email_full = f"{username}@yahoo.com"
                push_resp  = vps_push(email_full, password, username)
                result.update({"status": "ok", "email": email_full, "password": password,
                               "reason": "success_no_phone", "vps": push_resp})
                print(f"[yahoo:{SANDBOX}] SUCCESS (no phone): {email_full}", flush=True)
                return result

            # Phone verification screen
            phone_loc = page.locator("input[name=phone],input#reg-phone,input[type=tel]")
            phone_visible = False
            try:
                phone_loc.first.wait_for(state="visible", timeout=8000)
                phone_visible = True
            except Exception:
                pass

            if not phone_visible:
                result["reason"] = f"unexpected_page: {url_now[:80]}"
                print(f"[yahoo:{SANDBOX}] Unexpected page: {result['reason']}", flush=True)
                return result

            print(f"[yahoo:{SANDBOX}] Phone verification screen reached", flush=True)

            if not SMS_KEY:
                result["reason"] = "needs_phone_SMS_ACTIVATE_KEY_missing"
                result["status"] = "partial"
                print(f"[yahoo:{SANDBOX}] Partial: form OK, needs SMS_ACTIVATE_KEY to complete", flush=True)
                return result

            # Get phone from SMS-Activate
            try:
                act_id, phone_raw = sms_get_number()
                digits      = re.sub(r"\D", "", phone_raw)
                phone_input = digits[1:] if (digits.startswith("1") and len(digits) == 11) else digits
                print(f"[yahoo:{SANDBOX}] SMS number id={act_id} input={phone_input}", flush=True)
            except Exception as e:
                result["reason"] = f"sms_get_number: {e}"
                return result

            # Enter phone
            try:
                ph = phone_loc.first
                ph.fill(phone_input)
                time.sleep(random.uniform(0.8, 1.5))
            except Exception as e:
                sms_cancel(act_id); act_id = None
                result["reason"] = f"phone_fill: {e}"
                return result

            # Click "Send code" button
            try:
                send_btn = page.locator(
                    "button:has-text('Get code'), button:has-text('Send code'), "
                    "button:has-text('Text me'), button[type=submit]"
                ).first
                send_btn.wait_for(state="visible", timeout=5000)
                send_btn.click()
                print(f"[yahoo:{SANDBOX}] Requested SMS code", flush=True)
            except Exception as e:
                sms_cancel(act_id); act_id = None
                result["reason"] = f"send_sms_btn: {e}"
                return result

            # Poll for code
            time.sleep(3)
            code = sms_poll_code(act_id)
            if not code:
                sms_cancel(act_id); act_id = None
                result["reason"] = "sms_code_timeout"
                print(f"[yahoo:{SANDBOX}] SMS code timeout", flush=True)
                return result
            print(f"[yahoo:{SANDBOX}] SMS code: {code}", flush=True)

            # Enter code
            time.sleep(4)
            page.screenshot(path=f"/tmp/yahoo_code_{SANDBOX}.png")
            code_loc = page.locator(
                "input[name=code],input[name=challenge_response],"
                "input[placeholder*=code i],input[aria-label*=code i],"
                "input[name=verification_code]"
            ).first
            try:
                code_loc.wait_for(state="visible", timeout=12000)
                code_loc.fill(code)
                time.sleep(0.7)
            except Exception as e:
                sms_finish(act_id); act_id = None
                result["reason"] = f"code_field: {e}"
                return result

            # Submit code
            for sel in ["button[type=submit]", "button#reg-submit-btn"]:
                try:
                    btn = page.locator(sel).first
                    if btn.count() > 0:
                        btn.wait_for(state="visible", timeout=5000)
                        btn.click()
                        break
                except Exception:
                    pass

            sms_finish(act_id); act_id = None

            # Verify success
            time.sleep(8)
            page.screenshot(path=f"/tmp/yahoo_final_{SANDBOX}.png")
            final_url = page.url
            print(f"[yahoo:{SANDBOX}] Final URL: {final_url[:100]}", flush=True)

            success = (
                "yahoo.com/mail" in final_url
                or "mail.yahoo.com" in final_url
                or "account/create" not in final_url
                or page.locator("[data-ylk*=mailbox],[aria-label*=Inbox i]").count() > 0
            )
            if success:
                email_full = f"{username}@yahoo.com"
                push_resp  = vps_push(email_full, password, username)
                result.update({"status": "ok", "email": email_full, "password": password,
                               "reason": "success", "vps": push_resp})
                print(f"[yahoo:{SANDBOX}] SUCCESS: {email_full}", flush=True)
            else:
                result["reason"] = f"no_success_signal url={final_url[:60]}"
                print(f"[yahoo:{SANDBOX}] FAIL: {result['reason']}", flush=True)

        finally:
            if act_id:
                sms_cancel(act_id)
            browser.close()

    return result


if __name__ == "__main__":
    res = run_factory()
    print("RESULT:", json.dumps(res), flush=True)
