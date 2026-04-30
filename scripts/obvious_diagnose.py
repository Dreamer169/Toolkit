#!/usr/bin/env python3
"""
obvious_diagnose.py — 把失败的 Replit 注册任务 (rpl_xxx) 喂给 obvious 沙箱,
让它结合 web search + skill-builder 能力给出 4 段结构化诊断.

obvious 沙箱不直接当 Replit IP 池 (e2b 出口也是 datacenter), 但当**诊断助手**完美:
  * 读日志找根因, 引用具体行
  * 给可执行 patch (CHEAPEST FIX) 或 ASN 黑白名单 (INFRA RECOMMENDATION)
  * --tail N 自动并发分发到池中多个账号, N 大时显著加速

使用:
  python3 obvious_diagnose.py rpl_moj4wx0o_9kx2          # 单条, 池自动选最佳账号
  python3 obvious_diagnose.py --tail 5                   # 最近 5 条失败, 并发跑
  python3 obvious_diagnose.py --tail 10 --concurrent 2   # 限并发数
  python3 obvious_diagnose.py rpl_xxx --account cz-test1 # 强制用某个账号
  python3 obvious_diagnose.py --tail 5 --save /root/obvious-diagnoses
                                                          # 写文件而不是 stdout
"""
from __future__ import annotations
import argparse, glob, json, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from obvious_client import ObviousClient
from obvious_pool import ObviousPool, Account, DEFAULT_ACC_DIR

JOB_DIR = Path("/root/Toolkit/.local/replit_jobs")


def load_job(job_id: str) -> dict:
    p = JOB_DIR / f"{job_id}.json"
    if not p.exists(): sys.exit(f"job 不存在: {p}")
    return json.loads(p.read_text())


def summarize_job(j: dict) -> str:
    logs = j.get("logs") or []; res = j.get("result") or {}
    err = (res.get("results") or [{}])[0].get("error", "")
    head = "\n".join(logs[:6]); tail = "\n".join(logs[-25:])
    return (
        f"job_id: {j.get('id')}\n"
        f"status: {j.get('status')} duration: {(j.get('finished',0)-j.get('started',0))/1000:.1f}s\n"
        f"summary: {res.get('summary')}\nerror: {err}\n\n"
        f"--- head ---\n{head}\n\n"
        f"--- tail (last 25 lines) ---\n{tail}\n"
    )


PROMPT_TEMPLATE = """You're helping debug a Replit-account auto-registration pipeline.
Task ID `{job_id}` failed. Below is the full job summary + recent stdout from the
`replit_register.py` worker (camoufox + reCAPTCHA Enterprise v3 + outlook verify-mail).

```text
{summary}
```

Please analyze and answer in 4 sections:

1. **ROOT CAUSE** — single most likely cause, ≤2 sentences, citing log line numbers.
2. **EVIDENCE** — quote the 2-3 log fragments that prove it.
3. **CHEAPEST FIX** — code-level patch we can apply NOW (no infra change). Give a
   concrete file path + diff-style snippet if applicable. Skip if the cause is
   purely IP/network (in which case state "infra-only").
4. **INFRA RECOMMENDATION** — if the cause involves IP reputation / proxy ASN /
   captcha scoring, list 2-3 concrete external-IP pool actions (e.g. specific
   residential proxy provider categories, mobile 4G modem, known-good ASN
   keywords to whitelist). Skip if cause is pure code bug.

Be terse, no preamble. Use bullet points where natural.
"""


def collect_failed_jobs(n: int) -> list[str]:
    files = sorted(glob.glob(str(JOB_DIR / "rpl_*.json")), key=os.path.getmtime, reverse=True)
    ids = []
    for f in files:
        try:
            j = json.loads(open(f).read())
            if (j.get("result") or {}).get("okCount", 0) == 0:
                ids.append(j["id"])
            if len(ids) >= n: break
        except Exception: pass
    return ids


def diagnose_one(client: ObviousClient, job_id: str) -> str:
    summary = summarize_job(load_job(job_id))
    msgs = client.ask(PROMPT_TEMPLATE.format(job_id=job_id, summary=summary))
    return ObviousClient.extract_text(msgs)


def diagnose_via_pool(pool: ObviousPool, job_id: str, mode: str) -> dict:
    """Single-job dispatcher; acquires from pool."""
    summary = summarize_job(load_job(job_id))
    prompt = PROMPT_TEMPLATE.format(job_id=job_id, summary=summary)
    with pool.acquire(min_credits=0.5, mode=mode, wait_seconds=120) as cli:
        msgs = cli.ask(prompt)
        return {"job_id": job_id, "label": getattr(cli, "_account_label", "?"),
                "text": ObviousClient.extract_text(msgs)}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id", nargs="?")
    ap.add_argument("--account", help="强制用此 label (跳过池调度), 覆盖 --tail 也只用一个")
    ap.add_argument("--account-dir", default=str(DEFAULT_ACC_DIR))
    ap.add_argument("--mode", default="deep",
                    choices=["auto", "fast", "deep", "analyst", "skill-builder"])
    ap.add_argument("--tail", type=int, default=0,
                    help="批量诊断最近 N 条失败任务 (覆盖 job_id)")
    ap.add_argument("--concurrent", type=int, default=2,
                    help="--tail 时的并发数 (受池中健康账号数限制)")
    ap.add_argument("--save", default="",
                    help="把每条诊断写到此目录 <job_id>.md, 而不是 stdout")
    args = ap.parse_args(argv)

    pool = ObviousPool(Path(args.account_dir))

    # Determine job list
    if args.tail > 0:
        job_ids = collect_failed_jobs(args.tail)
        if not job_ids: sys.exit("no failed jobs found")
    elif args.job_id:
        job_ids = [args.job_id]
    else: sys.exit("需要 job_id 或 --tail N")

    save_dir = Path(args.save) if args.save else None
    if save_dir: save_dir.mkdir(parents=True, exist_ok=True)

    def _output(jid: str, label: str, text: str):
        if save_dir:
            path = save_dir / f"{jid}.md"
            path.write_text(f"# diagnosis for {jid} (via {label}, mode={args.mode})\n\n{text}\n")
            print(f"  → {path}", file=sys.stderr)
        else:
            print("=" * 80); print(f"# {jid}  (via {label})"); print("=" * 80)
            print(text); print()

    # --- single account override (manual pin) ---
    if args.account:
        acc_dir = Path(args.account_dir) / args.account
        if not acc_dir.exists(): sys.exit(f"account {args.account} 不存在")
        m = json.loads((acc_dir / "manifest.json").read_text())
        client = ObviousClient.from_storage_state(
            str(acc_dir / "storage_state.json"),
            thread_id=m["threadId"], project_id=m["projectId"], mode=args.mode,
        )
        for jid in job_ids:
            print(f"[{jid}] via {args.account} ...", file=sys.stderr)
            _output(jid, args.account, diagnose_one(client, jid))
        return 0

    # --- pool-driven (single or batch concurrent) ---
    pool.refresh_health()
    healthy = pool.healthy(min_credits=0.5)
    if not healthy:
        sys.exit("no healthy accounts in pool — run obvious_pool.py maintain --target N")
    print(f"[pool] {len(healthy)} healthy: {[a.label for a in healthy]}", file=sys.stderr)

    if len(job_ids) == 1:
        r = diagnose_via_pool(pool, job_ids[0], args.mode)
        _output(r["job_id"], r["label"], r["text"]); return 0

    # batch concurrent: each worker acquires its own account
    from concurrent.futures import ThreadPoolExecutor, as_completed
    n_workers = min(args.concurrent, len(healthy), len(job_ids))
    print(f"[batch] {len(job_ids)} jobs × {n_workers} workers", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        futs = {ex.submit(diagnose_via_pool, pool, jid, args.mode): jid for jid in job_ids}
        for f in as_completed(futs):
            jid = futs[f]
            try:
                r = f.result(); _output(r["job_id"], r["label"], r["text"])
            except Exception as e:
                print(f"[{jid}] FAIL: {type(e).__name__}: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
