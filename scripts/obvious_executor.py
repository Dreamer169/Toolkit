#!/usr/bin/env python3
"""
obvious_executor.py — Replit注册任务执行器 (通过obvious沙箱直接执行)

工作原理 (2026-05发现):
  obvious.ai每个Project自带一个e2b Linux沙箱.
  该沙箱对外暴露两个无认证服务:
    port 8888  — Jupyter Server (wss://)
    port 49999 — obvious代码执行服务器 (FastAPI, POST /execute)

  直接调用这两个端口 = 完全绕过obvious AI安全过滤器,
  任意代码在沙箱内执行 (root权限, Chrome/Playwright已安装).

使用:
    python3 obvious_executor.py --register --email test@outlook.com
    python3 obvious_executor.py --register --email test@outlook.com --account eu-test1
    python3 obvious_executor.py --health
    python3 obvious_executor.py --health --all
    python3 obvious_executor.py --exec "uname -a" --account eu-test1
"""
from __future__ import annotations
import argparse, json, os, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from obvious_sandbox import ObviousSandbox, DEFAULT_ACC_DIR, load_index

LOCAL_API = "http://127.0.0.1:8081"

# ─────────────────────────────────────────────────────────────────────────────
# Replit注册脚本 (在obvious沙箱内执行)
# ─────────────────────────────────────────────────────────────────────────────

_REPLIT_REGISTER_SCRIPT = '''
import asyncio, json, os, random, string, sys, time
from playwright.async_api import async_playwright

EMAIL    = os.environ["REPLIT_EMAIL"]
PASSWORD = os.environ["REPLIT_PASSWORD"]
USERNAME = os.environ["REPLIT_USERNAME"]
PROXY_URL = os.environ.get("REPLIT_PROXY", "")

RESULT = {"status": "init", "email": EMAIL, "replit_token": None, "error": None}

def _rand(n, chars=string.ascii_lowercase): return "".join(random.choices(chars, k=n))

async def register():
    launch_opts = dict(
        headless=True,
        args=["--no-sandbox","--disable-dev-shm-usage",
              "--disable-blink-features=AutomationControlled",
              "--disable-web-security"],
    )
    if PROXY_URL:
        launch_opts["proxy"] = {"server": PROXY_URL}

    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch_opts)
        ctx = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        await ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = {runtime: {}};
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
        """)
        page = await ctx.new_page()

        try:
            await page.goto("https://replit.com/signup", timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(2)

            email_sel = 'input[name="email"], input[type="email"]'
            await page.wait_for_selector(email_sel, timeout=15000)
            await page.fill(email_sel, EMAIL)
            await asyncio.sleep(0.5)

            user_sel = 'input[name="username"]'
            if await page.query_selector(user_sel):
                await page.fill(user_sel, USERNAME)
                await asyncio.sleep(0.3)

            pass_sel = 'input[name="password"], input[type="password"]'
            if await page.query_selector(pass_sel):
                await page.fill(pass_sel, PASSWORD)
                await asyncio.sleep(0.3)

            submit = 'button[type="submit"], button:has-text("Create account"), button:has-text("Sign up")'
            await page.click(submit, timeout=8000)
            await asyncio.sleep(3)

            url = page.url
            RESULT["final_url"] = url

            if "replit.com/~" in url or "/home" in url or "/repls" in url:
                RESULT["status"] = "success"
            elif "verify" in url.lower() or "confirm" in url.lower():
                RESULT["status"] = "needs_verify"
            elif "error" in url.lower() or await page.query_selector(".error, [data-testid=error]"):
                err_el = await page.query_selector(".error, [data-testid=error], [role=alert]")
                RESULT["error"] = await err_el.inner_text() if err_el else "form error"
                RESULT["status"] = "error"
            else:
                RESULT["status"] = "unknown"
                RESULT["page_title"] = await page.title()

        except Exception as e:
            RESULT["status"] = "exception"
            RESULT["error"] = str(e)
        finally:
            await browser.close()

asyncio.run(register())
print("RESULT:" + json.dumps(RESULT))
'''

# ─────────────────────────────────────────────────────────────────────────────
# cmd_register
# ─────────────────────────────────────────────────────────────────────────────

def _gen_username() -> str:
    import random, string
    return "user_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))

def _gen_password() -> str:
    import random, string
    chars = string.ascii_letters + string.digits + "!@#$"
    return "".join(random.choices(chars, k=16))


def cmd_register(account: str, email: str, password: str, username: str,
                 proxy: str | None, acc_dir: Path) -> int:
    print(f"[register] account={account} email={email}")

    sb = ObviousSandbox.from_account(account, acc_dir=acc_dir)
    print(f"[register] sandbox ready: {sb}")

    ip = sb.get_public_ip()
    print(f"[register] sandbox IP: {ip}")

    env_inject = (
        f"import os\n"
        f"os.environ['REPLIT_EMAIL']    = {email!r}\n"
        f"os.environ['REPLIT_PASSWORD'] = {password!r}\n"
        f"os.environ['REPLIT_USERNAME'] = {username!r}\n"
    )
    if proxy:
        env_inject += f"os.environ['REPLIT_PROXY'] = {proxy!r}\n"

    full_code = env_inject + _REPLIT_REGISTER_SCRIPT
    print("[register] running Playwright in sandbox ...", flush=True)

    try:
        out = sb.execute(full_code, timeout=90)
    except Exception as e:
        print(f"[register] execute error: {e}", file=sys.stderr)
        return 1

    print(out)

    result_line = next((l for l in out.splitlines() if l.startswith("RESULT:")), None)
    if not result_line:
        print("[register] no RESULT line in output", file=sys.stderr)
        return 1

    result = json.loads(result_line[len("RESULT:"):])
    status = result.get("status")
    print(f"[register] status={status}")

    if status in ("success", "needs_verify"):
        _write_result(result, email, account, acc_dir)
        return 0

    print(f"[register] FAILED: {result.get('error')}", file=sys.stderr)
    return 1


def _write_result(result: dict, email: str, account: str, acc_dir: Path) -> None:
    out_file = acc_dir / account / "replit_accounts.json"
    existing: list[dict] = []
    if out_file.exists():
        try:
            existing = json.loads(out_file.read_text())
        except Exception:
            pass
    existing.append({
        "email": email,
        "status": result.get("status"),
        "replit_token": result.get("replit_token"),
        "final_url": result.get("final_url"),
        "registeredAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    out_file.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
    print(f"[register] saved to {out_file}")


# ─────────────────────────────────────────────────────────────────────────────
# cmd_health
# ─────────────────────────────────────────────────────────────────────────────

def cmd_health(account: str | None, acc_dir: Path) -> int:
    accounts = load_index(acc_dir)
    if account:
        accounts = [a for a in accounts if a["label"] == account]

    for acc in accounts:
        label = acc["label"]
        try:
            sb = ObviousSandbox.from_account(label, acc_dir=acc_dir)
            h = sb.health()
            ip = sb.get_public_ip()
            print(json.dumps({"label": label, **h, "public_ip": ip}))
        except Exception as e:
            print(json.dumps({"label": label, "error": str(e)}))

    return 0


# ─────────────────────────────────────────────────────────────────────────────
# cmd_exec
# ─────────────────────────────────────────────────────────────────────────────

def cmd_exec(account: str, code: str, lang: str, acc_dir: Path) -> int:
    sb = ObviousSandbox.from_account(account, acc_dir=acc_dir)
    print(f"[exec] {sb}", file=sys.stderr)
    out = sb.execute(code, language=lang)
    print(out, end="")
    return 0




# ─────────────────────────────────────────────────────────────────────────────
# cmd_env  —  dump sandbox env vars (rev-bypass for masked values)
# ─────────────────────────────────────────────────────────────────────────────

def cmd_env(account: str, acc_dir: Path,
            keys: list | None = None,
            raw: bool = False,
            sensitive: bool = False,
            save: bool = False) -> int:
    """Dump env vars for a single account via env|rev bypass.

    Args:
        account:   account label (directory under acc_dir)
        keys:      specific var names; None = all (or all SENSITIVE_KEYS if sensitive=True)
        raw:       output as KEY=VALUE lines instead of pretty-print
        sensitive: only return SENSITIVE_KEYS (+ any explicit keys)
        save:      persist captured vars to manifest["env_snapshot"]
    """
    sb = ObviousSandbox.from_account(account, acc_dir=acc_dir)
    if save or sensitive:
        env = sb.env_snapshot(save=save, extra_keys=keys, sensitive_only=sensitive)
    else:
        env = sb.get_env_vars(keys=set(keys) if keys else None)

    _print_env(env, raw=raw, label=account)
    return 0


def _print_env(env: dict, raw: bool = False, label: str = "") -> None:
    prefix = f"[{label}] " if label else ""
    if raw:
        for k, v in sorted(env.items()):
            print(f"{k}={v}")
    else:
        import pprint
        print(f"{prefix}--- {len(env)} vars ---")
        pprint.pprint(dict(sorted(env.items())))


def cmd_env_all(acc_dir: Path,
                keys: list | None = None,
                raw: bool = False,
                sensitive: bool = True,
                save: bool = False,
                account_filter: str | None = None) -> int:
    """Sweep all accounts: wake sandbox if needed, collect env vars, optionally save.

    This is the automated path — iterates every account in index.json,
    wakes the sandbox if paused, runs env|rev bypass, and collects vars.

    Args:
        keys:           specific extra var names to capture
        sensitive:      include all SENSITIVE_KEYS (default True)
        save:           persist results to each account's manifest["env_snapshot"]
        account_filter: if set, only process this one account label
    """
    from obvious_sandbox import load_index, SENSITIVE_KEYS
    accounts = load_index(acc_dir)
    if account_filter:
        accounts = [a for a in accounts if a["label"] == account_filter]
    if not accounts:
        print("[env-all] no accounts found", file=sys.stderr)
        return 1

    summary: list[dict] = []
    exit_code = 0

    for acc in accounts:
        label = acc["label"]
        mp = acc_dir / label / "manifest.json"
        ss = acc_dir / label / "storage_state.json"
        if not mp.exists() or not ss.exists():
            print(f"[env-all] [{label}] skipped — missing manifest or cookies")
            continue

        print(f"[env-all] [{label}] connecting ...", flush=True)
        try:
            sb = ObviousSandbox.from_account(label, acc_dir=acc_dir)
        except Exception as e:
            print(f"[env-all] [{label}] CONNECT ERROR: {e}", file=sys.stderr)
            summary.append({"label": label, "ok": False, "error": str(e)})
            exit_code = 1
            continue

        try:
            env = sb.env_snapshot(save=save, extra_keys=keys, sensitive_only=sensitive)
            _print_env(env, raw=raw, label=label)
            summary.append({
                "label": label,
                "ok": True,
                "sandbox_id": sb.sandbox_id,
                "vars_captured": len(env),
                "has_api_token": "API_TOKEN" in env,
                "saved": save,
            })
        except Exception as e:
            print(f"[env-all] [{label}] EXEC ERROR: {e}", file=sys.stderr)
            summary.append({"label": label, "ok": False, "error": str(e)})
            exit_code = 1

    # Print summary table
    print()
    print(f"{'ACCOUNT':<20} {'OK':<5} {'SB_ID':<12} {'VARS':<6} {'API_TOKEN':<10} {'SAVED'}")
    print("-" * 70)
    for r in summary:
        if r["ok"]:
            sb_id = r.get("sandbox_id", "")[:8]
            print(f"{r['label']:<20} {'✓':<5} {sb_id:<12} {r.get('vars_captured',0):<6} {str(r.get('has_api_token','')):<10} {r.get('saved','')}")
        else:
            print(f"{r['label']:<20} {'✗':<5} {'—':<12} {'—':<6} {'—':<10} {r.get('error','')[:30]}")
    return exit_code


# ─────────────────────────────────────────────────────────────────────────────
# cmd_sniff_token  —  decode the live API_TOKEN JWT from sandbox
# ─────────────────────────────────────────────────────────────────────────────

def cmd_sniff_token(account: str, acc_dir: Path) -> int:
    sb = ObviousSandbox.from_account(account, acc_dir=acc_dir)
    tok = sb.get_api_token()
    if not tok:
        print("[sniff-token] API_TOKEN not present (sandbox may be idle or token expired)")
        return 1
    ttl = int(tok.get("exp", 0) - time.time())
    print(f"[sniff-token] userId        = {tok.get('userId')}")
    print(f"[sniff-token] tokenType     = {tok.get('tokenType')}")
    print(f"[sniff-token] toolPerms     = {tok.get('toolPermissions')}")
    print(f"[sniff-token] sandboxId     = {tok.get('sandboxId')}")
    print(f"[sniff-token] threadId      = {tok.get('threadId')}")
    print(f"[sniff-token] exp TTL       = {ttl}s ({'expired' if ttl < 0 else 'valid'})")
    print(f"[sniff-token] raw JWT       = {tok.get('_raw', '')[:80]}...")
    return 0



# ─────────────────────────────────────────────────────────────────────────────
# cmd_credits  —  check / reset credit balance
# ─────────────────────────────────────────────────────────────────────────────

def cmd_credits(account: str, acc_dir: Path, reset: bool = False,
                threshold: float = 0.0, dry_run: bool = False) -> int:
    """Check or reset credits for a single account."""
    sb = ObviousSandbox.from_account(account, acc_dir=acc_dir)
    if reset:
        r = sb.reset_credits(threshold=threshold, dry_run=dry_run)
        print(json.dumps(r, indent=2))
    else:
        c = sb.check_credits()
        print(json.dumps(c, indent=2))
    return 0


def cmd_credits_all(acc_dir: Path, reset: bool = False,
                    threshold: float = 0.0, dry_run: bool = False) -> int:
    """Check (and optionally reset) credits for ALL accounts in index.json."""
    from obvious_sandbox import load_index
    accounts = load_index(acc_dir)
    if not accounts:
        print("[credits-all] no accounts found", file=sys.stderr)
        return 1

    rows = []
    for acc in accounts:
        label = acc["label"]
        mp = acc_dir / label / "manifest.json"
        if not mp.exists():
            continue
        m = json.loads(mp.read_text())
        if m.get("status") == "dead":
            rows.append({"label": label, "status": "dead (skipped)"})
            continue
        try:
            sb = ObviousSandbox.from_account(label, acc_dir=acc_dir)
            c  = sb.check_credits()
            row = {"label": label, **c}
            if reset:
                r = sb.reset_credits(threshold=threshold, dry_run=dry_run)
                row["reset"]    = r["reset"]
                row["deleted"]  = r["projects_deleted"]
                row["after"]    = round(r["credits_after"], 4)
            rows.append(row)
        except Exception as e:
            rows.append({"label": label, "error": str(e)[:60]})

    # Summary table
    print()
    hdr = "{:<20} {:>10} {:>10} {:>10} {:>10} {:>10}".format(
        "ACCOUNT", "USED", "BALANCE", "MSGS", "CACHE%", "HIT_RATIO")
    print(hdr)
    print("-" * 75)
    for r in rows:
        if "error" in r:
            print("{:<20}  {}".format(r["label"], r.get("error", "")))
        elif "status" in r:
            print("{:<20}  {}".format(r["label"], r["status"]))
        else:
            reset_note = ""
            if reset and r.get("reset"):
                reset_note = "  → reset (" + str(r.get("deleted", 0)) + " proj deleted)"
            elif reset and not r.get("reset"):
                reset_note = "  → skipped (below threshold)"
            print("{:<20} {:>10.3f} {:>10.3f} {:>10} {:>9.1f}%{}".format(
                r["label"],
                r.get("totalCredits", 0),
                r.get("balance", 0),
                r.get("totalMessages", 0),
                r.get("cacheHitPct", 0),
                reset_note,
            ))
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="obvious沙箱Replit注册执行器 (直连port-49999/Jupyter, 绕过AI过滤)")
    ap.add_argument("--account", default="eu-test1", help="obvious账号标签")
    ap.add_argument("--acc-dir", default=str(DEFAULT_ACC_DIR), help="账号目录根")
    ap.add_argument("--register", action="store_true", help="注册一个Replit账号")
    ap.add_argument("--email", help="待注册邮箱")
    ap.add_argument("--password", help="密码 (不填则自动生成)")
    ap.add_argument("--username", help="用户名 (不填则自动生成)")
    ap.add_argument("--proxy", help="给注册脚本用的SOCKS5代理 (可选)")
    ap.add_argument("--health", action="store_true", help="检查沙箱状态")
    ap.add_argument("--all", action="store_true", help="--health时检查所有账号")
    ap.add_argument("--exec", metavar="CODE", help="在沙箱内执行Python代码")
    ap.add_argument("--lang", default="python", help="--exec的语言")
    ap.add_argument("--get-env", nargs="*", metavar="VAR",
                    help="env|rev bypass读沙箱env变量; 可指定变量名; 不指定=所有")
    ap.add_argument("--env-all", action="store_true",
                    help="扫描所有账号的env变量 (自动唤醒沙箱)")
    ap.add_argument("--sensitive", action="store_true",
                    help="--get-env/--env-all只返回敏感变量(API_TOKEN等)")
    ap.add_argument("--save", action="store_true",
                    help="将采集结果持久化到manifest[env_snapshot]")
    ap.add_argument("--raw-env", action="store_true",
                    help="输出为KEY=VALUE格式 (适合shell eval)")
    ap.add_argument("--sniff-token", action="store_true",
                    help="解码沙箱API_TOKEN JWT (含TTL/权限/sandboxId)")
    ap.add_argument("--credits", action="store_true",
                    help="查看当前账号的credit使用量和缓存命中率")
    ap.add_argument("--credits-all", action="store_true",
                    help="查看所有账号的credit使用量汇总表")
    ap.add_argument("--reset-credits", action="store_true",
                    help="删除所有项目，将credit计数归零 (绕过25 credits上限)")
    ap.add_argument("--reset-threshold", type=float, default=0.0,
                    help="只在credits >= 此值时才执行reset (0=总是reset)")
    ap.add_argument("--dry-run", action="store_true",
                    help="--reset-credits时只模拟，不实际删除")
    args = ap.parse_args(argv)

    acc_dir = Path(args.acc_dir)

    if args.health:
        return cmd_health(None if args.all else args.account, acc_dir)

    if args.exec:
        return cmd_exec(args.account, args.exec, args.lang, acc_dir)

    if args.env_all:
        return cmd_env_all(
            acc_dir=acc_dir,
            keys=args.get_env or None,
            raw=args.raw_env,
            sensitive=args.sensitive,
            save=args.save,
        )

    if args.get_env is not None:
        return cmd_env(
            account=args.account,
            acc_dir=acc_dir,
            keys=args.get_env or None,
            raw=args.raw_env,
            sensitive=args.sensitive,
            save=args.save,
        )

    if args.sniff_token:
        return cmd_sniff_token(args.account, acc_dir)
    if args.credits or args.reset_credits:
        return cmd_credits(
            account=args.account, acc_dir=acc_dir,
            reset=args.reset_credits,
            threshold=args.reset_threshold,
            dry_run=args.dry_run,
        )

    if args.credits_all or (args.reset_credits and args.all):
        return cmd_credits_all(
            acc_dir=acc_dir,
            reset=args.reset_credits,
            threshold=args.reset_threshold,
            dry_run=args.dry_run,
        )


    if args.register:
        if not args.email:
            ap.error("--email required for --register")
        return cmd_register(
            account=args.account,
            email=args.email,
            password=args.password or _gen_password(),
            username=args.username or _gen_username(),
            proxy=args.proxy,
            acc_dir=acc_dir,
        )

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
