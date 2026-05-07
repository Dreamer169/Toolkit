"""
core/turnstile.py -- Cloudflare Turnstile token (pure open-source)

Verified flow (2026-05-02 live test):
  1. replit_ip_probe.py --pick -> best residential/mobile SOCKS5 port
  2. pydoll Chrome through that proxy -> api.airforce/signup
  3. Wait 10s for Turnstile widget shadow DOM to fully render
  4. find_shadow_roots() -> outer shadow root with challenges.cloudflare.com
  5. shadow_root.query(iframe) -> CF challenge iframe
  6. iframe.find(body) -> iframe body
  7. body.get_shadow_root() -> inner shadow root (contains span.cb-i checkbox)
  8. inner_shadow.query(span.cb-i) -> click checkbox -> token appears in main page

Key ports (replit_ip_probe 2026-05-02):
  10854: HKT Mobile HK  score=100  <- best
  10857: HKBN Broadband  score=85
  10820/10823/10828/10829/10833/10840: PCCW IMS HK  score=85
"""
import asyncio
import os
import subprocess
import time

SITEKEY     = "0x4AAAAAACY9xSVz3RBFYucU"
PAGE_URL    = "https://api.airforce/signup"
CHROME_PATH = "/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome"
IP_PROBE    = "/root/Toolkit/scripts/replit_ip_probe.py"

RESIDENTIAL_PORTS = [10851, 10853, 10854, 10857, 10859, 10870, 10871, 10872, 10873, 10877, 10884, 10888, 10889]
# Ports permanently excluded (DC/cloud IPs - Cloudflare blocks these)
DC_PORTS_SKIP = [10808, 10809, 10820, 10821, 10822, 10824, 10825, 10826, 10828, 10829, 10831, 10832, 10833, 10834, 10837, 10838, 10839, 10840, 10841, 10842, 10843, 10844, 10850, 10852, 10855, 10856, 10858]
# Port -> ISP mapping for reference
PORT_ISP = {
    10854: "HKT Mobile HK score=100",
    10857: "HKBN Broadband HK score=85",
    10823: "PCCW IMS HK score=85",
    10830: "PCCW IMS HK score=85",
    10835: "PCCW IMS HK score=85",
    10853: "Turkey Telekom score=80",
    10836: "SG Aceville score=50",
    10845: "HK ACE score=50",
    10851: "IT Wiplanet score=50",
    10859: "HK HGC score=50",
    10827: "CN 263 Global score=50",
}
_CF_DOMAIN     = "challenges.cloudflare.com"
_CF_IFRAME_SEL = 'iframe[src*="challenges.cloudflare.com"]'
_CHECKBOX_SEL  = "span.cb-i"

# Exact same JS as the proven working test script
_TOKEN_JS = 'document.querySelector(\'[name="cf-turnstile-response"]\').value'


def _cdp(raw):
    """Parse pydoll execute_script result. Handles both raw string and CDP dict."""
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw if len(raw) > 20 else None
    if not isinstance(raw, dict):
        return None
    inner = raw.get("result", {}).get("result", raw.get("result", {}))
    if isinstance(inner, dict):
        if inner.get("subtype") == "null" or inner.get("type") == "undefined":
            return None
        v = inner.get("value")
        return v if isinstance(v, str) and len(v) > 20 else None
    if isinstance(inner, str) and len(inner) > 20:
        return inner
    return None


def _options(proxy_port=None):
    from pydoll.browser.options import ChromiumOptions
    opt = ChromiumOptions()
    opt.headless = False
    opt.binary_location = CHROME_PATH
    opt.add_argument("--no-sandbox")
    opt.add_argument("--disable-dev-shm-usage")
    opt.add_argument("--disable-gpu")
    opt.add_argument("--window-size=1280,900")
    opt.start_timeout = 30
    if proxy_port:
        opt.add_argument(f"--proxy-server=socks5://127.0.0.1:{proxy_port}")
    opt.add_argument("--proxy-bypass-list=<-loopback>")
    return opt


def get_best_proxy_port() -> int:
    """Run replit_ip_probe.py --pick to get the current best residential port."""
    try:
        r = subprocess.run(
            ["python3", IP_PROBE, "--pick",
             "--ports", ",".join(str(p) for p in RESIDENTIAL_PORTS)],
            capture_output=True, text=True, timeout=30,
        )
        url = (r.stdout or "").strip()
        if url.startswith("socks5://127.0.0.1:"):
            port = int(url.split(":")[-1])
            print(f"[turnstile] probe picked port {port}", flush=True)
            return port
    except Exception as e:
        print(f"[turnstile] probe err: {e}", flush=True)
    print(f"[turnstile] probe fallback -> port {RESIDENTIAL_PORTS[0]}", flush=True)
    return RESIDENTIAL_PORTS[0]


async def _get_token_via_proxy(proxy_port: int, timeout: float = 90.0) -> str:
    """
    Get Turnstile token through residential IP proxy.
    Key: wait 10s for shadow DOM to render, then manual shadow bypass.
    Token appears in main page's [name=cf-turnstile-response] after checkbox click.
    """
    os.environ.setdefault("DISPLAY", ":99")
    from pydoll.browser import Chrome

    label = f"proxy:{proxy_port}"
    t0 = time.time()
    print(f"[turnstile:{label}] launching Chrome...", flush=True)

    opt = _options(proxy_port)
    async with Chrome(options=opt) as browser:
        tab = await browser.start()

        print(f"[turnstile:{label}] navigating...", flush=True)
        try:
            await tab.go_to(PAGE_URL, timeout=35)
        except Exception as e:
            print(f"[turnstile:{label}] go_to: {e}", flush=True)

        # Critical: wait 10s for Turnstile shadow DOM to fully render
        print(f"[turnstile:{label}] waiting 10s for Turnstile render...", flush=True)
        await asyncio.sleep(10)

        # Step 1: find outer CF shadow root
        print(f"[turnstile:{label}] Step1: finding CF shadow root...", flush=True)
        cf_sr = None
        t_search = time.time()
        while time.time() - t_search < 30:
            roots = await tab.find_shadow_roots(deep=False)
            for sr in roots:
                html = await sr.inner_html
                if _CF_DOMAIN in (html or ""):
                    cf_sr = sr
                    print(f"[turnstile:{label}] Step1 OK html_len={len(html)}", flush=True)
                    break
            if cf_sr:
                break
            await asyncio.sleep(2)

        if not cf_sr:
            raise TimeoutError(f"[turnstile:{label}] CF shadow root not found in 30s")

        # Step 2: find CF challenge iframe inside shadow root
        print(f"[turnstile:{label}] Step2: querying CF iframe...", flush=True)
        iframe_el = await cf_sr.query(_CF_IFRAME_SEL, timeout=15)
        print(f"[turnstile:{label}] Step2 OK", flush=True)

        # Step 3: get iframe body
        print(f"[turnstile:{label}] Step3: getting iframe body...", flush=True)
        body_el = await iframe_el.find(tag_name="body", timeout=20)
        print(f"[turnstile:{label}] Step3 OK", flush=True)

        # Step 4: get inner shadow root inside CF iframe body
        print(f"[turnstile:{label}] Step4: getting inner shadow root...", flush=True)
        inner_sr = await body_el.get_shadow_root(timeout=20)
        print(f"[turnstile:{label}] Step4 OK", flush=True)

        # Step 5: click checkbox (span.cb-i) - same as working test
        print(f"[turnstile:{label}] Step5: clicking checkbox...", flush=True)
        try:
            checkbox = await inner_sr.query(_CHECKBOX_SEL, timeout=10)
            await checkbox.click()
            print(f"[turnstile:{label}] Step5 clicked OK", flush=True)
        except Exception as e:
            print(f"[turnstile:{label}] Step5 no checkbox ({e}), invisible mode", flush=True)

        # Step 6: monitor cf-turnstile-response using EXACT same JS as proven working test
        # Monitor for 60s from NOW (not relative to function start)
        print(f"[turnstile:{label}] Step6: monitoring token (60s window from click)...", flush=True)
        t_click = time.time()
        elapsed_total = t_click - t0
        print(f"[turnstile:{label}] elapsed before Step6: {elapsed_total:.1f}s", flush=True)

        deadline = t_click + 60  # 60s from click time
        while time.time() < deadline:
            remaining = deadline - time.time()
            try:
                raw = await tab.execute_script(_TOKEN_JS)
                token = _cdp(raw)
                if token:
                    print(f"[turnstile:{label}] TOKEN len={len(token)} t={time.time()-t0:.1f}s", flush=True)
                    return token
                # Every 10s log progress
                if int(time.time() - t_click) % 10 == 0:
                    print(f"[turnstile:{label}] Step6 waiting... {remaining:.0f}s left", flush=True)
            except Exception as e:
                err_s = str(e)
                # Exception means element exists but has empty value, or element not found
                if "Cannot read" not in err_s and int(time.time() - t_click) % 15 == 0:
                    print(f"[turnstile:{label}] Step6 script err: {err_s[:80]}", flush=True)
            await asyncio.sleep(0.8)

        try:
            await tab.take_screenshot(f"/tmp/turnstile_fail_{proxy_port}.png")
        except Exception:
            pass
        raise TimeoutError(f"[turnstile:{label}] no token after 60s monitoring")


async def _get_token_direct(timeout: float = 90.0) -> str:
    """Fallback: VPS direct connection, pydoll auto-solve invisible Turnstile."""
    os.environ.setdefault("DISPLAY", ":99")
    from pydoll.browser import Chrome

    opt = _options(proxy_port=None)
    async with Chrome(options=opt) as browser:
        tab = await browser.start()
        await tab.enable_auto_solve_cloudflare_captcha(time_to_wait_captcha=60)
        try:
            await tab.go_to(PAGE_URL, timeout=35)
        except Exception as e:
            print(f"[turnstile:direct] go_to: {e}", flush=True)
        await asyncio.sleep(2)

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = await tab.execute_script(_TOKEN_JS)
                token = _cdp(raw)
                if token:
                    print(f"[turnstile:direct] TOKEN len={len(token)}", flush=True)
                    return token
            except Exception:
                pass
            await asyncio.sleep(0.8)

        raise TimeoutError(f"[turnstile:direct] no token after {timeout:.0f}s")


def get_turnstile_token(timeout_s: float = 120.0, use_proxy: bool = True,
                        proxy_port: int = None) -> str:
    """
    Get Cloudflare Turnstile token for api.airforce/signup.

    Strategy (verified 2026-05-02):
      1. Residential IP proxy (port 10854 HKT Mobile score=100):
         10s wait -> shadow bypass -> click -> token ~794 chars
      2. Fallback: VPS direct (invisible Turnstile auto-pass)

    Args:
      timeout_s:   total timeout in seconds (default 120 to allow render+bypass+monitor)
      use_proxy:   use residential IP proxy (default True)
      proxy_port:  specific proxy port, None = auto-probe best
    Returns:
      CF Turnstile token string (~700-800 chars)
    """
    os.environ.setdefault("DISPLAY", ":99")

    if use_proxy and proxy_port is None:
        proxy_port = get_best_proxy_port()

    errors = []

    if use_proxy and proxy_port:
        try:
            return asyncio.run(_get_token_via_proxy(proxy_port, timeout=timeout_s))
        except Exception as e:
            print(f"[turnstile] proxy:{proxy_port} failed: {e}", flush=True)
            errors.append(str(e))
            # try one fallback residential port
            for fb in RESIDENTIAL_PORTS:
                if fb != proxy_port:
                    try:
                        return asyncio.run(
                            _get_token_via_proxy(fb, timeout=min(timeout_s, 100))
                        )
                    except Exception as e2:
                        errors.append(str(e2))
                    break

    # final fallback: direct VPS
    try:
        return asyncio.run(_get_token_direct(timeout=min(timeout_s, 80)))
    except Exception as e:
        errors.append(str(e))

    raise RuntimeError(f"Turnstile failed on all attempts: {'; '.join(errors[-3:])}")


if __name__ == "__main__":
    print("[turnstile] standalone test (proxy mode)...", flush=True)
    tok = get_turnstile_token(timeout_s=120.0, use_proxy=True)
    print(f"[turnstile] OK token={tok[:60]}... len={len(tok)}", flush=True)
