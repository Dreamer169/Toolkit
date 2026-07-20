#!/usr/bin/env python3
"""
obvious_pool.py — 多账号 obvious 沙箱池, 健康监控 + 并发分发 + 自动补给.

为什么需要：
  * 单账号 25 credits ≈ 100 prompt, 大批量诊断 / 沙箱 offload 一会就烧光
  * cookie 会过期 / 邮箱被风控 → 需要主动健康检查
  * 多任务想并发 → 每个账号一个 thread, 必须分发到不同账号
  * obvious_provision.py 只管开号, 不管之后状态

API（库用法）：
    pool = ObviousPool()
    pool.refresh_health()                  # 并发 ping 所有账号
    pool.print_status()                    # 表格
    with pool.acquire(min_credits=2.0) as client:
        out = client.ask("...")
    results = pool.dispatch_batch([
        ("prompt1", "label-hint or None"),
        ("prompt2", None),
    ], max_concurrent=3)
    pool.provision_one("auto-1", proxy="socks5://127.0.0.1:10854")

CLI：
    obvious_pool.py status                 # 表格
    obvious_pool.py refresh                # 强制刷新
    obvious_pool.py ask "prompt"           # 用最佳账号问一个
    obvious_pool.py maintain --target 5    # 缺多少自动开多少 (用 replit_ip_probe --pick)
    obvious_pool.py prune --min-credits 1  # 标死 credits<1 的, 不删 (人工 review)
"""
from __future__ import annotations
import argparse, contextlib, json, os, sys, threading, time, subprocess, fcntl
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from obvious_client import ObviousClient

DEFAULT_ACC_DIR = Path("/root/obvious-accounts")
INDEX_FILE = DEFAULT_ACC_DIR / "index.json"
LOCK_DIR = DEFAULT_ACC_DIR / ".locks"
HEALTH_FILE_NAME = "health.json"
PROVISION_SCRIPT = Path("/root/Toolkit/scripts/obvious_provision.py")
IP_PROBE_SCRIPT = Path("/root/Toolkit/scripts/replit_ip_probe.py")
HEALTH_TTL_SEC = 300  # 5 min cache; force refresh past TTL


def _now() -> str: return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content)
    tmp.replace(path)


# ─────────────────────────────────────────────────────────────────────────────
# Account: thin wrapper around manifest + storage_state + health sidecar
# ─────────────────────────────────────────────────────────────────────────────
class Account:
    def __init__(self, dir_path: Path):
        self.dir = dir_path
        self.label = dir_path.name
        self.manifest_path = dir_path / "manifest.json"
        self.storage_path = dir_path / "storage_state.json"
        self.health_path = dir_path / HEALTH_FILE_NAME
        self.lock_path = LOCK_DIR / f"{self.label}.lock"

    @property
    def manifest(self) -> dict:
        return json.loads(self.manifest_path.read_text())

    def write_manifest(self, m: dict) -> None:
        _atomic_write(self.manifest_path, json.dumps(m, indent=2))

    @property
    def health(self) -> dict:
        if not self.health_path.exists():
            return {"alive": None, "credits": None, "checkedAt": None, "error": None}
        try: return json.loads(self.health_path.read_text())
        except Exception: return {"alive": None, "credits": None, "checkedAt": None, "error": "corrupt"}

    def write_health(self, h: dict) -> None:
        _atomic_write(self.health_path, json.dumps(h, indent=2))

    def _make_client(self, mode: str = "auto") -> ObviousClient:
        m = self.manifest
        return ObviousClient.from_storage_state(
            str(self.storage_path),
            thread_id=m["threadId"], project_id=m["projectId"],
            mode=mode,
        )

    def probe(self) -> dict:
        """One-shot health probe: validates cookie + fetches credits + backfills IDs.
        Pure HTTP via the storage_state cookies (no Playwright)."""
        import urllib.request, urllib.error
        h = {"checkedAt": _now(), "alive": False, "credits": None, "tier": None,
             "userId": None, "workspaceId": None, "error": None, "latencyMs": None}
        try:
            t0 = time.time()
            state = json.loads(self.storage_path.read_text())
            cookies = "; ".join(c["name"] + "=" + c["value"]
                                for c in state["cookies"] if c["domain"].endswith("obvious.ai"))
            hdr = {"Cookie": cookies, "Accept": "application/json", "User-Agent": "obvious_pool/1",
                   "Origin": "https://app.obvious.ai", "Referer": "https://app.obvious.ai/"}

            def get(url):
                r = urllib.request.Request(url, headers=hdr)
                try:
                    with urllib.request.urlopen(r, timeout=8) as resp: return resp.status, resp.read().decode()
                except urllib.error.HTTPError as e: return e.code, e.read().decode()

            s_sess, b_sess = get("https://api.app.obvious.ai/prepare/auth/get-session")
            if s_sess != 200 or not b_sess.strip().startswith("{"):
                h["error"] = f"session_invalid:{s_sess}"; return h
            j_sess = json.loads(b_sess)
            user = (j_sess.get("user") or {})
            h["userId"] = user.get("providerId")
            h["alive"] = True

            s_wks, b_wks = get("https://api.app.obvious.ai/prepare/workspaces")
            if s_wks == 200:
                wks = (json.loads(b_wks).get("workspaces") or [None])[0]
                if wks:
                    h["workspaceId"] = wks.get("id")
                    h["credits"] = float(wks.get("creditBalance") or 0)
                    h["tier"] = wks.get("subscriptionTier")
            h["latencyMs"] = int((time.time() - t0) * 1000)

            # Backfill missing IDs into manifest (older provision runs left these null)
            m = self.manifest; changed = False
            for k in ("userId", "workspaceId"):
                if not m.get(k) and h.get(k):
                    m[k] = h[k]; changed = True
            if changed: self.write_manifest(m)
            return h
        except Exception as e:
            h["error"] = f"{type(e).__name__}:{str(e)[:120]}"; return h

    @contextlib.contextmanager
    def lock(self, timeout: float = 0.0):
        """Filesystem lock so two callers never use same thread concurrently
        (obvious accounts have a single agent; concurrent ask = corrupt thread)."""
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        f = open(self.lock_path, "w")
        try:
            mode = fcntl.LOCK_EX | (fcntl.LOCK_NB if timeout == 0 else 0)
            t0 = time.time()
            while True:
                try: fcntl.flock(f.fileno(), mode); break
                except BlockingIOError:
                    if timeout > 0 and (time.time() - t0) < timeout:
                        time.sleep(0.2); continue
                    raise
            yield
        finally:
            try: fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception: pass
            f.close()


# ─────────────────────────────────────────────────────────────────────────────
# Pool
# ─────────────────────────────────────────────────────────────────────────────
class ObviousPool:
    def __init__(self, acc_dir: Path = DEFAULT_ACC_DIR):
        self.acc_dir = Path(acc_dir)
        self.index_file = self.acc_dir / "index.json"

    def discover(self) -> list[Account]:
        accs = []
        if not self.acc_dir.exists(): return accs
        for d in sorted(self.acc_dir.iterdir()):
            if not d.is_dir() or d.name.startswith("."): continue
            if (d / "manifest.json").exists() and (d / "storage_state.json").exists():
                accs.append(Account(d))
        return accs

    def refresh_health(self, max_workers: int = 8, force: bool = False) -> list[tuple[Account, dict]]:
        accs = self.discover(); results = []
        def _do(a: Account):
            cur = a.health
            if not force and cur.get("checkedAt"):
                try:
                    age = time.time() - datetime.fromisoformat(cur["checkedAt"]).timestamp()
                    if age < HEALTH_TTL_SEC and cur.get("alive") is not None:
                        return a, cur
                except Exception: pass
            h = a.probe(); a.write_health(h); return a, h
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for f in as_completed([ex.submit(_do, a) for a in accs]):
                results.append(f.result())
        return results

    def healthy(self, min_credits: float = 0.5) -> list[Account]:
        out = []
        for a in self.discover():
            h = a.health
            if h.get("alive") and (h.get("credits") or 0) >= min_credits:
                out.append((a, h["credits"]))
        out.sort(key=lambda x: -x[1])  # credits desc
        return [a for a, _ in out]

    @contextlib.contextmanager
    def acquire(self, min_credits: float = 0.5, mode: str = "auto",
                wait_seconds: float = 30.0):
        """Acquire least-loaded healthy account; lock its thread; yield client."""
        deadline = time.time() + wait_seconds
        last_err = "no healthy accounts"
        while time.time() < deadline:
            for a in self.healthy(min_credits):
                try:
                    with a.lock(timeout=0):  # non-blocking; try next if locked
                        client = a._make_client(mode=mode)
                        client._account_label = a.label  # type: ignore
                        yield client
                        return
                except BlockingIOError:
                    last_err = f"all healthy accounts in use ({a.label} locked)"
                    continue
            time.sleep(1.0)
        raise RuntimeError(f"acquire timeout: {last_err}")

    def dispatch_batch(self, prompts: list[str], max_concurrent: int = 3,
                       mode: str = "auto", min_credits: float = 0.5,
                       wait_per_acquire: float = 60.0) -> list[dict]:
        """Run N prompts across the pool concurrently. Returns list of
        {prompt, label, ok, text|error, durationMs} in input order."""
        results: list[Optional[dict]] = [None] * len(prompts)
        def _worker(i: int, prompt: str):
            t0 = time.time()
            try:
                with self.acquire(min_credits=min_credits, mode=mode,
                                  wait_seconds=wait_per_acquire) as cli:
                    msgs = cli.ask(prompt)
                    text = ObviousClient.extract_text(msgs)
                    return i, {"prompt": prompt[:80], "label": getattr(cli, "_account_label", "?"),
                               "ok": True, "text": text, "durationMs": int((time.time()-t0)*1000)}
            except Exception as e:
                return i, {"prompt": prompt[:80], "label": "-", "ok": False,
                           "error": f"{type(e).__name__}:{str(e)[:200]}",
                           "durationMs": int((time.time()-t0)*1000)}
        with ThreadPoolExecutor(max_workers=max_concurrent) as ex:
            for f in as_completed([ex.submit(_worker, i, p) for i, p in enumerate(prompts)]):
                i, r = f.result(); results[i] = r
        return [r for r in results if r is not None]

    # ─── auto-provisioning ──────────────────────────────────────────────────
    def pick_proxy(self) -> Optional[str]:
        """Use replit_ip_probe.py --pick to pick highest-score live socks port."""
        if not IP_PROBE_SCRIPT.exists(): return None
        try:
            out = subprocess.run(
                ["python3", str(IP_PROBE_SCRIPT), "--pick", "--respect-cooldown"],
                capture_output=True, text=True, timeout=120,
            )
            url = (out.stdout or "").strip()
            return url if url.startswith("socks5://") else None
        except Exception: return None

    def provision_one(self, label: str, proxy: Optional[str] = None,
                      headless: bool = True) -> dict:
        """Wraps obvious_provision.py to add one new account."""
        if not PROVISION_SCRIPT.exists():
            raise RuntimeError(f"provisioner not at {PROVISION_SCRIPT}")
        proxy = proxy or self.pick_proxy()
        if not proxy:
            raise RuntimeError("no usable proxy from replit_ip_probe --pick")
        env = os.environ.copy(); env.setdefault("DISPLAY", ":99")
        cmd = ["python3", str(PROVISION_SCRIPT), "--proxy", proxy,
               "--label", label, "--check-ip"]
        if headless:
            cmd.append("--headless")
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=480, env=env)
        ok = "\u2705 provisioned" in (out.stdout or "")
        return {"label": label, "proxy": proxy, "ok": ok,
                "stdout_tail": (out.stdout or "")[-1500:],
                "stderr_tail": (out.stderr or "")[-400:]}

    def maintain(self, target: int, min_credits: float = 5.0,
                 max_provision: int = 1) -> dict:
        """Refresh health; if healthy<target, provision up to max_provision new
        accounts (one at a time to avoid IP burst). Returns action report."""
        self.refresh_health(force=True)
        h = self.healthy(min_credits=min_credits)
        deficit = max(0, target - len(h))
        report = {"target": target, "healthyNow": len(h), "deficit": deficit,
                  "provisioned": []}
        for i in range(min(deficit, max_provision)):
            label = f"auto-{int(time.time())}-{i}"
            r = self.provision_one(label)
            report["provisioned"].append(r)
            if not r["ok"]: break  # stop on first failure
        return report

    def print_status(self) -> None:
        accs = self.discover()
        print(f"{'label':<22} {'alive':<6} {'credits':>8} {'tier':<6} "
              f"{'lat':>5} {'checked':<22} note")
        print("─" * 110)
        for a in accs:
            h = a.health; m = a.manifest
            alive = "✅" if h.get("alive") else ("❌" if h.get("alive") is False else "?")
            cred = f"{h.get('credits'):.2f}" if isinstance(h.get("credits"), (int, float)) else "?"
            checked = (h.get("checkedAt") or "")[:19].replace("T", " ")
            note = (h.get("error") or "")[:40] or m.get("egressIp", "")
            print(f"{a.label:<22} {alive:<6} {cred:>8} {(h.get('tier') or '-'):<6} "
                  f"{h.get('latencyMs','?'):>5} {checked:<22} {note}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(DEFAULT_ACC_DIR), help="accounts root dir")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="print health table (uses cached health if fresh)")
    sub.add_parser("refresh", help="force re-probe all accounts")
    p_ask = sub.add_parser("ask", help="single ask via best healthy account")
    p_ask.add_argument("prompt"); p_ask.add_argument("--mode", default="auto")
    p_ask.add_argument("--min-credits", type=float, default=0.5)
    p_batch = sub.add_parser("batch", help="run prompts from file (one/line) concurrently")
    p_batch.add_argument("file"); p_batch.add_argument("--mode", default="auto")
    p_batch.add_argument("--concurrent", type=int, default=3)
    p_maint = sub.add_parser("maintain", help="provision new accounts up to target")
    p_maint.add_argument("--target", type=int, default=3)
    p_maint.add_argument("--max-provision", type=int, default=1)
    p_prov = sub.add_parser("provision", help="provision one new account")
    p_prov.add_argument("--label", required=True); p_prov.add_argument("--proxy")
    args = ap.parse_args(argv)
    pool = ObviousPool(Path(args.dir))

    if args.cmd == "status":
        pool.refresh_health(); pool.print_status(); return 0
    if args.cmd == "refresh":
        pool.refresh_health(force=True); pool.print_status(); return 0
    if args.cmd == "ask":
        with pool.acquire(min_credits=args.min_credits, mode=args.mode) as cli:
            msgs = cli.ask(args.prompt)
            print(f"# via account: {getattr(cli,'_account_label','?')}", file=sys.stderr)
            print(ObviousClient.extract_text(msgs))
        return 0
    if args.cmd == "batch":
        prompts = [l.rstrip("\n") for l in open(args.file) if l.strip()]
        results = pool.dispatch_batch(prompts, max_concurrent=args.concurrent, mode=args.mode)
        print(json.dumps(results, indent=2, ensure_ascii=False)); return 0
    if args.cmd == "maintain":
        r = pool.maintain(args.target, max_provision=args.max_provision)
        print(json.dumps(r, indent=2)); return 0
    if args.cmd == "provision":
        r = pool.provision_one(args.label, proxy=args.proxy)
        print(json.dumps(r, indent=2)); return (0 if r["ok"] else 1)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
