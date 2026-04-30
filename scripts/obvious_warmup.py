#!/usr/bin/env python3
"""
obvious_warmup.py — 用obvious沙箱+SSH桥住宅IP做Replit注册前cookie预热

session_freshness_v2研究发现: 在打开signup.replit.com前, 先访问
google.com / youtube.com / github.com 能显著提升reCAPTCHA Enterprise score
(从0.3-0.5提升到0.7-0.9), 原因是建立了真实的浏览器会话历史.

流程:
  1. 确认obvious沙箱SSH桥已建立(有住宅IP)
  2. 用Playwright访问热身站点(google, youtube, github), 停留5-10s
  3. 访问replit.com主页, 等待CF clearance cookie
  4. 返回已热身的cookie状态, 供replit_register.py直接使用

使用:
  python3 obvious_warmup.py --account eu-test1          # 热身一个账号
  python3 obvious_warmup.py --save /tmp/warmed_cookies  # 保存热身cookie
  python3 obvious_warmup.py --check-ip                  # 仅检查出口IP
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from obvious_client import ObviousClient
from obvious_pool import ObviousPool, DEFAULT_ACC_DIR

WARMUP_SITES = [
    ("https://www.google.com", 6),
    ("https://www.youtube.com", 5),
    ("https://github.com", 4),
    ("https://replit.com", 8),
]

WARMUP_PROMPT = '''Run this Python script in your e2b sandbox using Playwright.
Report ALL print() output. Do NOT add explanations.

```python
import asyncio, json, time
from playwright.async_api import async_playwright

PROXY = "socks5://45.205.27.69:19857"  # SSH bridge to VPS residential

async def warmup():
    results = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  f"--proxy-server=socks5://45.205.27.69:19857"],
        )
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="America/New_York",
        )

        # Check exit IP first
        page = await ctx.new_page()
        try:
            await page.goto("https://api.ipify.org?format=json", timeout=15000)
            ip_data = json.loads(await page.inner_text("body"))
            results["exit_ip"] = ip_data.get("ip", "UNKNOWN")
            print(f"EXIT_IP={results['exit_ip']}")
        except Exception as e:
            results["exit_ip"] = f"ERROR:{e}"
            print(f"EXIT_IP=ERROR: {e}")
        await page.close()

        # Warmup visits
        sites = [
            ("https://www.google.com", 7),
            ("https://www.youtube.com", 6),
            ("https://github.com", 5),
            ("https://replit.com", 9),
        ]
        for url, wait_sec in sites:
            page = await ctx.new_page()
            try:
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                print(f"WARMUP_OK url={url} title={await page.title()!r}")
                # Simulate human: scroll, wait
                await page.evaluate("window.scrollTo(0, 300)")
                await asyncio.sleep(wait_sec)
                await page.evaluate("window.scrollTo(0, 600)")
                await asyncio.sleep(2)
            except Exception as e:
                print(f"WARMUP_FAIL url={url} err={e}")
            finally:
                await page.close()

        # Extract cookies (especially CF clearance from replit.com)
        cookies = await ctx.cookies(["https://replit.com"])
        cf_cookies = [c for c in cookies if "clearance" in c["name"] or "cf_" in c["name"]]
        all_replit_cookies = {c["name"]: c["value"][:40] for c in cookies}
        print(f"REPLIT_COOKIES={json.dumps(all_replit_cookies)}")
        print(f"CF_COOKIES_COUNT={len(cf_cookies)}")

        # Save full cookie state
        all_cookies = await ctx.cookies()
        import pathlib
        out_dir = pathlib.Path("/tmp/warmup_state")
        out_dir.mkdir(exist_ok=True)
        state_file = out_dir / "cookies.json"
        state_file.write_text(json.dumps({"cookies": all_cookies}, indent=2))
        print(f"COOKIES_SAVED={state_file}")
        print("WARMUP_COMPLETE")

        await browser.close()

asyncio.run(warmup())
```
'''


def _run_warmup(acc_dir: Path) -> str:
    m = json.loads((acc_dir / "manifest.json").read_text())
    client = ObviousClient.from_storage_state(
        str(acc_dir / "storage_state.json"),
        thread_id=m["threadId"], project_id=m["projectId"],
        mode="fast",
    )
    client.poll_timeout = 300
    msgs = client.ask(WARMUP_PROMPT)
    shells = client.extract_shell_results(msgs)
    text = client.extract_text(msgs)
    out = []
    for s in shells:
        if s.get("stdout"):
            out.append(s["stdout"])
    if not out and text:
        out.append(text)
    return "\n".join(out)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="", help="obvious账号label")
    ap.add_argument("--acc-dir", default=str(DEFAULT_ACC_DIR))
    ap.add_argument("--check-ip", action="store_true", help="仅检查出口IP")
    args = ap.parse_args(argv)

    acc_dir_root = Path(args.acc_dir)

    if args.account:
        acc_dir = acc_dir_root / args.account
    else:
        pool = ObviousPool(acc_dir_root)
        pool.refresh_health()
        healthy = pool.healthy(min_credits=0.5)
        if not healthy:
            sys.exit("no healthy obvious accounts")
        acc_dir = healthy[0].dir

    print(f"[warmup] account={acc_dir.name}", file=sys.stderr)
    out = _run_warmup(acc_dir)
    print(out)

    # Parse key values
    exit_ip = next((l.split("=", 1)[1] for l in out.splitlines() if l.startswith("EXIT_IP=")), "?")
    complete = "WARMUP_COMPLETE" in out
    print(f"\n[result] exit_ip={exit_ip} complete={complete}", file=sys.stderr)
    return 0 if complete else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
