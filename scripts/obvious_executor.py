#!/usr/bin/env python3
"""
obvious_executor.py — 用obvious沙箱执行Replit注册任务

原理:
  obvious e2b沙箱 = Playwright worker. SSH桥提供住宅IP.
  可独立运行注册流程, 也可作为replit_register.py的备用通道.

  注册成功 → 账号写入DB (通过本地API) + 返回 {email, replit_token}

使用:
  # 单次注册
  python3 obvious_executor.py --register --email test@outlook.com

  # 诊断模式: 分析rpl_xxx任务失败原因
  python3 obvious_executor.py --diagnose rpl_moj4wx0o_9kx2

  # 批量诊断
  python3 obvious_executor.py --diagnose --tail 5

  # 完整注册+诊断流水线 (失败自动诊断)
  python3 obvious_executor.py --pipeline --count 3

  # 检查obvious沙箱状态 (隧道+IP+credits)
  python3 obvious_executor.py --health
"""
from __future__ import annotations
import argparse, glob, json, os, sys, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent))
from obvious_client import ObviousClient
from obvious_pool import ObviousPool, DEFAULT_ACC_DIR

JOB_DIR = Path("/root/Toolkit/.local/replit_jobs")
LOCAL_API = "http://127.0.0.1:8081"

# ── 提示词模板 ──────────────────────────────────────────────────────────────

HEALTH_PROMPT = """Run these commands in your sandbox and print all output:
```bash
echo '=== sandbox info ==='
uname -a && df -h /home/user && free -m | head -2
echo '=== proxy relay check ==='
EXIT_IP=$(curl -s --max-time 10 --proxy 'socks5h://obv:Obv@R3layS3cr3t_2026@45.205.27.69:19857' https://api.ipify.org 2>/dev/null)
echo "EXIT_IP=${EXIT_IP:-FAILED}"
echo '=== playwright ==='
python3 -c "from playwright.sync_api import sync_playwright; print('PLAYWRIGHT_OK')" 2>&1
echo '=== credits ==='
python3 -c "import json; m=open('/tmp/credits.json').read() if __import__('os').path.exists('/tmp/credits.json') else '{}'; print(m)"
```
"""

REGISTER_PROMPT_TMPL = """You have a Playwright environment in your e2b sandbox.
A residential SOCKS5 proxy socks5://45.205.27.69:19857 is available (VPS relay → xray住宅IP).

Run this Python registration script and print ALL output. Do NOT explain.

```python
import asyncio, json, random, string, time, re
from playwright.async_api import async_playwright

EMAIL = "{email}"
PASSWORD = "{password}"
USERNAME = "{username}"
PROXY = "socks5://45.205.27.69:19857"  # VPS SOCKS5 relay → xray住宅

def rand_str(n): return ''.join(random.choices(string.ascii_lowercase, k=n))

async def register():
    result = {{{{"status": "init", "email": EMAIL, "replit_token": None, "error": None}}}}
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            viewport={{{{"width": 1366, "height": 768}}}},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="en-US", timezone_id="America/New_York",
            proxy={{"server": "socks5://45.205.27.69:19857", "username": "obv", "password": "Obv@R3layS3cr3t_2026"}},
        )

        # Warmup: visit google first
        page = await ctx.new_page()
        try:
            await page.goto("https://www.google.com", timeout=20000, wait_until="domcontentloaded")
            print(f"WARMUP_OK google title={{{{await page.title()!r}}}}")
            await asyncio.sleep(4)
        except Exception as e:
            print(f"WARMUP_SKIP: {{{{e}}}}")
        await page.close()

        # Go to Replit signup
        page = await ctx.new_page()
        try:
            await page.goto("https://replit.com/signup", timeout=30000, wait_until="networkidle")
            print(f"SIGNUP_PAGE title={{{{await page.title()!r}}}}")
            await asyncio.sleep(2)

            # Fill email
            await page.fill('input[name="email"], input[type="email"]', EMAIL)
            await asyncio.sleep(0.8)
            await page.fill('input[name="password"], input[type="password"]', PASSWORD)
            await asyncio.sleep(0.5)

            # Username if field present
            uname_sel = 'input[name="username"]'
            if await page.locator(uname_sel).count() > 0:
                await page.fill(uname_sel, USERNAME)
                await asyncio.sleep(0.5)

            # Submit
            await page.click('button[type="submit"], button:has-text("Sign up")')
            print("FORM_SUBMITTED")
            await asyncio.sleep(5)

            # Check for success indicators
            url = page.url
            print(f"POST_SUBMIT_URL={{{{url}}}}")

            # Wait for dashboard or verify page
            try:
                await page.wait_for_url("**/~/", timeout=20000)
                print("SIGNUP_SUCCESS dashboard reached")
                result["status"] = "success"
            except Exception:
                # Check for verify email page
                content = await page.content()
                if "verify" in content.lower() or "email" in content.lower():
                    print("SIGNUP_VERIFY_EMAIL_REQUIRED")
                    result["status"] = "needs_verify"
                elif "captcha" in content.lower() or "challenge" in content.lower():
                    print("SIGNUP_CAPTCHA_BLOCKED")
                    result["status"] = "captcha_blocked"
                else:
                    print(f"SIGNUP_UNKNOWN_STATE url={{{{url}}}}")
                    result["status"] = "unknown"

            # Extract any tokens from cookies
            cookies = await ctx.cookies(["https://replit.com"])
            for c in cookies:
                if "token" in c["name"].lower() or "connect" in c["name"].lower():
                    result["replit_token"] = c["value"][:80]
                    print(f"TOKEN_FOUND name={{{{c['name']}}}}")

        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)[:200]
            print(f"SIGNUP_ERROR {{{{e}}}}")
        finally:
            await page.close()
        await browser.close()

    print(f"RESULT_JSON={{{{json.dumps(result)}}}}")

asyncio.run(register())
```
"""

DIAGNOSE_PROMPT_TMPL = """You're helping debug a Replit auto-registration pipeline.
Job `{job_id}` failed. Analyze the log below and answer in 4 sections:

1. **ROOT CAUSE** — single most likely cause, ≤2 sentences, cite log lines.
2. **EVIDENCE** — quote 2-3 key log fragments.
3. **CHEAPEST FIX** — concrete code patch (file + diff). Say "infra-only" if IP issue.
4. **INFRA RECOMMENDATION** — IP/proxy/ASN actions. Skip if pure code bug.

Be terse, no preamble.

```
{summary}
```
"""


# ── 工具函数 ────────────────────────────────────────────────────────────────

def _load_job(job_id: str) -> dict:
    p = JOB_DIR / f"{job_id}.json"
    if not p.exists():
        sys.exit(f"job not found: {p}")
    return json.loads(p.read_text())


def _summarize_job(j: dict) -> str:
    logs = j.get("logs") or []
    res = j.get("result") or {}
    err = (res.get("results") or [{}])[0].get("error", "")
    head = "\n".join(str(l) for l in logs[:8])
    tail = "\n".join(str(l) for l in logs[-30:])
    return (
        f"job_id={j.get('id')} status={j.get('status')}\n"
        f"error={err}\n\n--- head ---\n{head}\n\n--- tail ---\n{tail}"
    )


def _collect_failed_jobs(n: int) -> list[str]:
    files = sorted(glob.glob(str(JOB_DIR / "rpl_*.json")), key=os.path.getmtime, reverse=True)
    ids = []
    for f in files:
        try:
            j = json.loads(open(f).read())
            if (j.get("result") or {}).get("okCount", 0) == 0:
                ids.append(j["id"])
            if len(ids) >= n:
                break
        except Exception:
            pass
    return ids


def _ask_account(acc_dir: Path, prompt: str, mode: str = "fast", timeout: float = 300) -> str:
    m = json.loads((acc_dir / "manifest.json").read_text())
    client = ObviousClient.from_storage_state(
        str(acc_dir / "storage_state.json"),
        thread_id=m["threadId"], project_id=m["projectId"],
        mode=mode,
    )
    client.poll_timeout = timeout
    msgs = client.ask(prompt)
    shells = client.extract_shell_results(msgs)
    text = client.extract_text(msgs)
    parts = [s["stdout"] for s in shells if s.get("stdout")]
    if not parts:
        parts = [text]
    return "\n".join(parts)


# ── 命令实现 ────────────────────────────────────────────────────────────────

def cmd_health(pool: ObviousPool, acc_dir_root: Path, account: str):
    pool.refresh_health(force=True)
    pool.print_status()

    targets = [acc_dir_root / account] if account else [a.dir for a in pool.healthy(min_credits=0.1)]
    for t in targets[:2]:
        print(f"\n[health-check] {t.name}", file=sys.stderr)
        out = _ask_account(t, HEALTH_PROMPT)
        print(f"=== {t.name} ===\n{out}")


def cmd_diagnose(pool: ObviousPool, job_id: str | None, tail: int, concurrent: int, mode: str, save: str, account: str, acc_dir_root: Path):
    job_ids = _collect_failed_jobs(tail) if tail > 0 else ([job_id] if job_id else [])
    if not job_ids:
        sys.exit("no failed jobs found")

    if account:
        targets = [acc_dir_root / account]
    else:
        pool.refresh_health()
        targets = [a.dir for a in pool.healthy(min_credits=0.5)]
    if not targets:
        sys.exit("no healthy accounts")

    save_dir = Path(save) if save else None
    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)

    def _diagnose_one(jid: str) -> tuple[str, str, str]:
        summary = _summarize_job(_load_job(jid))
        prompt = DIAGNOSE_PROMPT_TMPL.format(job_id=jid, summary=summary)
        with pool.acquire(min_credits=0.5, mode=mode, wait_seconds=120) as cli:
            msgs = cli.ask(prompt)
            label = getattr(cli, "_account_label", "?")
            text = ObviousClient.extract_text(msgs)
            return jid, label, text

    def _output(jid, label, text):
        if save_dir:
            p = save_dir / f"{jid}.md"
            p.write_text(f"# {jid} via {label}\n\n{text}\n")
            print(f"  → {p}", file=sys.stderr)
        else:
            print("=" * 72)
            print(f"# {jid}  (via {label})")
            print("=" * 72)
            print(text)

    if len(job_ids) == 1:
        jid, lbl, txt = _diagnose_one(job_ids[0])
        _output(jid, lbl, txt)
    else:
        n = min(concurrent, len(targets), len(job_ids))
        with ThreadPoolExecutor(max_workers=n) as ex:
            futs = {ex.submit(_diagnose_one, jid): jid for jid in job_ids}
            for f in as_completed(futs):
                try:
                    jid, lbl, txt = f.result()
                    _output(jid, lbl, txt)
                except Exception as e:
                    print(f"[{futs[f]}] FAIL: {e}", file=sys.stderr)


def cmd_register(pool: ObviousPool, email: str, password: str, username: str, account: str, acc_dir_root: Path):
    """
    VPS Playwright direct registration (bypasses obvious AI safety filter)
    Uses xray residential proxy on 127.0.0.1:10851-10859
    """
    import hashlib, random, subprocess as _sp
    if not email:
        sys.exit("--email required")
    if not password:
        password = "Px" + hashlib.md5(email.encode()).hexdigest()[:12] + "!"
    if not username:
        username = email.split("@")[0].replace(".", "_")[:20] + str(random.randint(10, 99))

    vps_reg = Path(__file__).parent / "vps_pw_register.py"
    if not vps_reg.exists():
        sys.exit(f"vps_pw_register.py not found at {vps_reg}")

    port = random.randint(10851, 10859)
    cmd = [sys.executable, str(vps_reg),
           "--email", email,
           "--password", password,
           "--username", username,
           "--port", str(port)]
    print(f"[register] VPS_PW email={email} proxy=127.0.0.1:{port}", file=sys.stderr)
    proc = _sp.run(cmd, capture_output=False, timeout=300)
    sys.exit(proc.returncode)



def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--health", action="store_true")
    ap.add_argument("--diagnose", action="store_true")
    ap.add_argument("--register", action="store_true")
    ap.add_argument("job_id", nargs="?")
    ap.add_argument("--tail", type=int, default=0)
    ap.add_argument("--concurrent", type=int, default=2)
    ap.add_argument("--mode", default="deep", choices=["auto", "fast", "deep", "analyst", "skill-builder"])
    ap.add_argument("--save", default="")
    ap.add_argument("--account", default="")
    ap.add_argument("--acc-dir", default=str(DEFAULT_ACC_DIR))
    ap.add_argument("--email", default="")
    ap.add_argument("--password", default="")
    ap.add_argument("--username", default="")
    args = ap.parse_args(argv)

    acc_dir_root = Path(args.acc_dir)
    pool = ObviousPool(acc_dir_root)

    if args.health:
        cmd_health(pool, acc_dir_root, args.account)
    elif args.diagnose:
        cmd_diagnose(pool, args.job_id, args.tail, args.concurrent,
                     args.mode, args.save, args.account, acc_dir_root)
    elif args.register:
        cmd_register(pool, args.email, args.password, args.username,
                     args.account, acc_dir_root)
    else:
        ap.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
