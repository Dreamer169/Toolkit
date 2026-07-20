#!/usr/bin/env python3
"""
auto_device_code.py — Device Code flow browser automation for Microsoft account OAuth.

Args:
  sys.argv[1]: JSON list [{email, password, userCode, verificationUri, accountId}]
  sys.argv[2]: proxy string (empty string = auto-select residential proxy)

Output (stdout):
  RESULTS:[{"status":"done"|"error"|"timeout","msg":"..."}]
"""
import json
import socket
import sys
import time


def _pick_residential_proxy():
    ports = [10851, 10853, 10854, 10857, 10859]
    for port in ports:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=1)
            s.close()
            return "socks5://127.0.0.1:" + str(port)
        except Exception:
            pass
    return ""


def _handle_device_code_page(page, email, password, user_code):
    wait = 1200

    # Step 1: Enter user code
    try:
        code_sel = page.locator(
            "#idTxtBx_SAOTCC_OTC, [name='otc'], input[autocomplete='one-time-code'], "
            "input[placeholder*='code' i], input[type='text']"
        )
        code_sel.first.wait_for(state="visible", timeout=20000)
        code_sel.first.fill(user_code, timeout=5000)
        page.wait_for_timeout(wait)

        next_btn = page.locator(
            "#idSIButton9, [data-testid='primaryButton'], "
            "[value='Next'], [type='submit']"
        )
        next_btn.first.click(timeout=7000)
        page.wait_for_timeout(wait * 2)
    except Exception as e:
        print("[auto_dc] code-entry error: " + str(e), flush=True)

    # Step 2: Email login form (if session not active)
    try:
        login_input = page.locator("[name=loginfmt]")
        if login_input.is_visible(timeout=4000):
            login_input.fill(email, timeout=5000)
            page.wait_for_timeout(wait)
            page.locator("#idSIButton9, [data-testid='primaryButton']").first.click(timeout=5000)
            page.wait_for_timeout(wait * 2)
    except Exception:
        pass

    # Step 3: Password
    try:
        pwd_input = page.locator("[name=passwd], [type='password']")
        if pwd_input.is_visible(timeout=8000):
            pwd_input.first.fill(password, timeout=5000)
            page.wait_for_timeout(wait)
            page.locator(
                "#idSIButton9, [data-testid='primaryButton'], [value='Sign in']"
            ).first.click(timeout=5000)
            page.wait_for_timeout(wait * 2)
    except Exception:
        pass

    # Step 4: Stay signed in? → No
    try:
        no_btn = page.locator("#idBtn_Back, [data-value='no'], [value='No']")
        if no_btn.is_visible(timeout=3000):
            no_btn.first.click(timeout=3000)
            page.wait_for_timeout(wait)
    except Exception:
        pass

    # Step 5: Consent / Accept
    for attempt in range(3):
        try:
            accept_sel = page.locator(
                "[data-testid='appConsentPrimaryButton'], "
                "[id='idSIButton9'][value='Accept'], "
                "input[value='Accept'], button:has-text('Accept')"
            )
            if accept_sel.first.is_visible(timeout=8000):
                accept_sel.first.click(timeout=7000)
                page.wait_for_timeout(wait * 2)
                break
        except Exception:
            pass
        time.sleep(1)

    # Step 6: Verify success (look for confirmation text or URL)
    try:
        confirmed = (
            page.get_by_text("signed in", case_insensitive=True).count() > 0
            or page.get_by_text("You have signed in", case_insensitive=True).count() > 0
            or "deviceauth" not in page.url.lower()
        )
        return confirmed
    except Exception:
        return True  # optimistic — let token polling decide


def process_one(item, proxy):
    email = item.get("email", "")
    password = item.get("password", "")
    user_code = item.get("userCode", "")
    verification_uri = item.get("verificationUri", "https://www.microsoft.com/link")

    print("[auto_dc] start email=" + email + " code=" + user_code, flush=True)
    print("[auto_dc] proxy=" + (proxy or "(none)"), flush=True)

    try:
        from patchright.sync_api import sync_playwright

        proxy_settings = None
        if proxy:
            proxy_settings = {"server": proxy, "bypass": "localhost"}

        with sync_playwright() as pw:
            b = pw.chromium.launch(
                headless=True,
                args=["--lang=en-US", "--no-sandbox", "--disable-dev-shm-usage"],
                proxy=proxy_settings,
            )
            try:
                ctx = b.new_context()
                page = ctx.new_page()
                page.goto(verification_uri, timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
                ok = _handle_device_code_page(page, email, password, user_code)
                print("[auto_dc] result ok=" + str(ok), flush=True)
                return {"status": "done" if ok else "error", "msg": "flow completed" if ok else "flow failed"}
            finally:
                try:
                    b.close()
                except Exception:
                    pass
    except Exception as e:
        msg = str(e)[:300]
        print("[auto_dc] exception: " + msg, flush=True)
        return {"status": "error", "msg": msg}


def main():
    if len(sys.argv) < 2:
        print("RESULTS:" + json.dumps([{"status": "error", "msg": "no payload"}]))
        return

    try:
        payload = json.loads(sys.argv[1])
    except Exception as e:
        print("RESULTS:" + json.dumps([{"status": "error", "msg": "json parse: " + str(e)}]))
        return

    proxy_arg = sys.argv[2] if len(sys.argv) > 2 else ""
    proxy = proxy_arg.strip() if proxy_arg else _pick_residential_proxy()

    results = []
    for item in payload:
        r = process_one(item, proxy)
        results.append(r)

    print("RESULTS:" + json.dumps(results))


if __name__ == "__main__":
    main()
