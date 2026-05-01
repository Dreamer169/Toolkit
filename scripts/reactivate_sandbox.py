import asyncio, json, re, time
from pathlib import Path
import requests

ACC_DIR = Path("/root/obvious-accounts/eu-test1")
MANIFEST_PATH = ACC_DIR / "manifest.json"
STATE_PATH = ACC_DIR / "storage_state.json"
NEW_PID = "prj_G9RjU6Jj"
PROXY_SOCKS = "socks5://127.0.0.1:10822"
API_BASE = "https://api.app.obvious.ai/prepare"

def make_session():
    s = requests.Session()
    socks5h = PROXY_SOCKS.replace("socks5://", "socks5h://")
    s.proxies = {"http": socks5h, "https": socks5h}
    s.headers["User-Agent"] = "Mozilla/5.0 obvious-sandbox/1.0"
    return s

async def main():
    from playwright.async_api import async_playwright

    mf = json.loads(MANIFEST_PATH.read_text())
    ss = json.loads(STATE_PATH.read_text())
    print("[reactivate] email=" + mf["email"] + " newProject=" + NEW_PID)

    cookies_str = "; ".join(
        c["name"] + "=" + c["value"]
        for c in ss["cookies"] if "obvious.ai" in c.get("domain", "")
    )
    http_headers = {
        "Cookie": cookies_str,
        "Content-Type": "application/json",
        "Origin": "https://app.obvious.ai",
        "Referer": "https://app.obvious.ai/",
        "User-Agent": "Mozilla/5.0 obvious-sandbox/1.0",
    }
    session = make_session()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            proxy={"server": PROXY_SOCKS},
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = await browser.new_context(
            storage_state=str(STATE_PATH),
            viewport={"width": 1280, "height": 800},
        )

        api_calls = []
        ctx.on("request", lambda req: api_calls.append(req.url))

        page = await ctx.new_page()
        target_url = "https://app.obvious.ai/p/" + NEW_PID
        print("[reactivate] navigating to " + target_url)
        await page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(12)
        print("[reactivate] landed: " + page.url)

        if "sign" in page.url or "login" in page.url:
            print("[reactivate] FAIL: not logged in")
            await browser.close()
            return

        # Wait up to 20s for any input to appear
        try:
            await page.wait_for_selector('[contenteditable="true"], textarea, [role=textbox]', timeout=20000)
        except Exception:
            pass

        ci = None
        for sel in ['[contenteditable="true"]', "textarea", "[role=textbox]"]:
            ci = await page.query_selector(sel)
            if ci:
                print("[reactivate] input found via: " + sel)
                break

        if not ci:
            content = await page.content()
            print("[reactivate] no input, page snippet: " + content[200:600])
            await browser.close()
            return

        await ci.focus()
        await asyncio.sleep(0.5)
        await page.keyboard.type("run: print(1)", delay=20)
        await asyncio.sleep(0.5)
        await page.keyboard.press("Enter")
        print("[reactivate] message sent, capturing thread_id...")

        thread_id = None
        for i in range(60):
            await asyncio.sleep(2)
            for url in api_calls:
                for pat in [r"/threads/(th_[A-Za-z0-9]+)", r"/agent/chat/(th_[A-Za-z0-9]+)"]:
                    m2 = re.search(pat, url)
                    if m2:
                        thread_id = m2.group(1)
            if thread_id:
                print("[reactivate] thread_id=" + thread_id)
                break
            if i % 5 == 0:
                print("[reactivate] " + str(i * 2) + "s, api_calls=" + str(len(api_calls)))

        new_state = await ctx.storage_state()
        STATE_PATH.write_text(json.dumps(new_state, indent=2))
        await browser.close()

    if not thread_id:
        print("[reactivate] FAIL: no thread_id captured")
        return

    print("[reactivate] polling messages API for sandbox_id...")
    sandbox_id = None
    for i in range(30):
        time.sleep(3)
        try:
            r = session.get(
                API_BASE + "/threads/" + thread_id + "/messages",
                headers=http_headers, timeout=10
            )
            if r.status_code == 200:
                pat = '"sandboxId"' + r'\s*:\s*"([a-z0-9]+)"'
                mat = re.search(pat, r.text)
                if mat:
                    sandbox_id = mat.group(1)
                    print("[reactivate] sandbox_id=" + sandbox_id)
                    break
            if i % 3 == 0:
                print("[reactivate] messages HTTP=" + str(r.status_code) + " " + str(i * 3) + "s")
        except Exception as e:
            print("[reactivate] err: " + str(e))

    mf2 = json.loads(MANIFEST_PATH.read_text())
    mf2["prevProjectId"] = mf2.get("projectId")
    mf2["prevThreadId"] = mf2.get("threadId")
    mf2["projectId"] = NEW_PID
    mf2["threadId"] = thread_id
    mf2["sandboxId"] = sandbox_id
    mf2["status"] = "active" if sandbox_id else "thread_only"
    mf2["deadReason"] = None
    mf2["deadAt"] = None
    MANIFEST_PATH.write_text(json.dumps(mf2, indent=2))

    print("[reactivate] DONE tid=" + str(thread_id) + " sb=" + str(sandbox_id))

asyncio.run(main())
