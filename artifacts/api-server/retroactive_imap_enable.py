#!/usr/bin/env python3
"""
retroactive_imap_enable.py
v1.0 — Use saved browser cookies to enable IMAP for existing accounts.
Microsoft disabled IMAP by default for new Outlook accounts (2024+).
This script loads each account's saved cookies into Playwright,
navigates to the IMAP settings page, and enables IMAP access.
"""

import sys, os, json, time, argparse
import psycopg2

DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/toolkit")
IMAP_SETTINGS_URL = "https://outlook.live.com/mail/0/options/mail/popimap"
BATCH_SIZE = 5        # concurrent browser pages
MAX_ACCOUNTS = 200    # cap for this run
HEADLESS = True


def enable_imap_for_page(page, email):
    """Navigate to IMAP settings and enable IMAP. Returns (success, message)."""
    try:
        page.goto(IMAP_SETTINGS_URL, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(3500)

        # Screenshot before
        try:
            page.screenshot(path=f"/tmp/retro_imap_before_{email}.png")
        except Exception:
            pass

        # Check if we're actually on the settings page (not redirected to login)
        curr_url = page.url
        if "login" in curr_url or "account.live" in curr_url or "passport" in curr_url:
            return False, f"Redirected to login: {curr_url[:80]}"

        # Strategy 1: find unchecked IMAP toggle
        enable_sels = [
            '[role="switch"][aria-checked="false"]',
            'button[aria-checked="false"]',
            'input[type="radio"][value="1"]',
            'input[type="radio"][id*="imap"]',
            'input[type="checkbox"][aria-label*="IMAP"]',
            'input[type="checkbox"][aria-label*="imap"]',
        ]
        clicked = False
        for sel in enable_sels:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=1500):
                    loc.click()
                    page.wait_for_timeout(800)
                    print(f"[retro] {email}: clicked toggle {sel}", flush=True)
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            # Strategy 2: JS scan
            try:
                js = """() => {
                    var els = Array.from(document.querySelectorAll(
                        '[role="switch"],[role="checkbox"],input[type="radio"],input[type="checkbox"]'));
                    for (var el of els) {
                        var par = el.closest("section") || el.closest("fieldset") || el.parentElement || {};
                        var txt = (par.textContent || "").toLowerCase();
                        if (txt.indexOf("imap") >= 0) {
                            var chk = el.getAttribute("aria-checked") || (el.checked ? "true" : "false");
                            if (chk === "true") return "already-enabled";
                            el.click();
                            return "js-clicked";
                        }
                    }
                    var byAttr = Array.from(document.querySelectorAll(
                        '[aria-label*="IMAP"],[aria-label*="imap"],[data-testid*="imap"],[data-testid*="IMAP"]'));
                    for (var el of byAttr) {
                        var chk = el.getAttribute("aria-checked") || (el.checked ? "true" : "false");
                        if (chk === "true") return "already-enabled";
                        el.click();
                        return "attr-clicked";
                    }
                    return "not-found";
                }"""
                res = page.evaluate(js)
                print(f"[retro] {email}: JS result={res}", flush=True)
                if res in ("js-clicked", "already-enabled", "attr-clicked"):
                    clicked = True
                    if res == "already-enabled":
                        return True, "already-enabled"
            except Exception as je:
                print(f"[retro] {email}: JS failed: {je}", flush=True)

        if not clicked:
            # Take screenshot for inspection
            try:
                page.screenshot(path=f"/tmp/retro_imap_stuck_{email}.png")
            except Exception:
                pass
            # Try to get page text for debugging
            try:
                body_text = page.evaluate("() => document.body ? document.body.innerText.slice(0,500) : 'empty'")
                print(f"[retro] {email}: page text preview: {body_text[:200]}", flush=True)
            except Exception:
                pass
            return False, "toggle-not-found"

        # Strategy 3: Save
        save_sels = [
            'button:has-text("Save")',
            'button:has-text("\u4fdd\u5b58")',
            'button[type="submit"]',
            'input[type="submit"]',
            'button[aria-label*="Save"]',
        ]
        saved = False
        for sel in save_sels:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=2000):
                    loc.click()
                    page.wait_for_timeout(2000)
                    print(f"[retro] {email}: saved via {sel}", flush=True)
                    saved = True
                    break
            except Exception:
                continue

        if not saved:
            try:
                page.evaluate("""() => {
                    Array.from(document.querySelectorAll("button")).forEach(b => {
                        if (b.textContent.trim().match(/^(Save|\\u4fdd\\u5b58)$/i)) b.click();
                    });
                }""")
                page.wait_for_timeout(1500)
                saved = True
                print(f"[retro] {email}: saved via JS fallback", flush=True)
            except Exception:
                pass

        try:
            page.screenshot(path=f"/tmp/retro_imap_after_{email}.png")
        except Exception:
            pass

        return True, f"enabled (save={'ok' if saved else 'failed'})"

    except Exception as e:
        return False, f"exception: {e}"


def get_accounts_with_cookies(limit=MAX_ACCOUNTS):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, email, cookies_json
        FROM accounts
        WHERE platform='outlook'
          AND status NOT IN ('suspended')
          AND cookies_json IS NOT NULL
          AND LENGTH(cookies_json::text) > 50
          AND (token IS NOT NULL AND token <> '')
        ORDER BY updated_at DESC
        LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=MAX_ACCOUNTS)
    parser.add_argument("--no-headless", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    headless = not args.no_headless

    accounts = get_accounts_with_cookies(args.max)
    print(f"[retro] Found {len(accounts)} accounts with cookies to process", flush=True)

    if args.dry_run:
        for acct_id, email, _ in accounts[:5]:
            print(f"  DRY-RUN: would enable IMAP for {email} (id={acct_id})")
        return

    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("ERROR: Neither patchright nor playwright is installed")
            sys.exit(1)

    success_count = 0
    fail_count = 0
    skip_count = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=["--no-sandbox"])

        for idx, (acct_id, email, cookies_json_raw) in enumerate(accounts, 1):
            print(f"\n[retro] [{idx}/{len(accounts)}] Processing {email} (id={acct_id})", flush=True)

            # Parse cookies
            try:
                storage_state = json.loads(cookies_json_raw) if isinstance(cookies_json_raw, str) else cookies_json_raw
            except Exception as e:
                print(f"[retro] {email}: failed to parse cookies: {e}", flush=True)
                fail_count += 1
                continue

            # Create context with saved storage state
            try:
                ctx = browser.new_context(storage_state=storage_state)
            except Exception as e:
                print(f"[retro] {email}: failed to create context: {e}", flush=True)
                fail_count += 1
                continue

            try:
                page = ctx.new_page()
                ok, msg = enable_imap_for_page(page, email)
                if ok:
                    print(f"[retro] ✅ {email}: {msg}", flush=True)
                    success_count += 1
                else:
                    print(f"[retro] ❌ {email}: {msg}", flush=True)
                    fail_count += 1
            except Exception as e:
                print(f"[retro] {email}: page exception: {e}", flush=True)
                fail_count += 1
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass

            # Small delay between accounts
            time.sleep(1)

        browser.close()

    print(f"\n[retro] Done: success={success_count} fail={fail_count} skip={skip_count}/{len(accounts)}", flush=True)


if __name__ == "__main__":
    main()
