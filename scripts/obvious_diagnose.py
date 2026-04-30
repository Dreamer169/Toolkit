#!/usr/bin/env python3
"""
obvious_diagnose.py — 把一条失败的 Replit 注册任务 (rpl_xxx) 喂给 obvious 沙箱,
让它结合自身 web search + skill-builder 能力分析根因 + 给可执行建议.

obvious 不直接当 IP 池 (它的 e2b 出口也是 datacenter), 但当**诊断助手**很合适:
  - 能 web search 当前 reCAPTCHA Enterprise 评分规则
  - 能跑 Python/JS 分析 IP, 解析 Replit signup HTML
  - 能 skill-builder 模式产出可重用的检测流程

使用:
  python3 obvious_diagnose.py rpl_moj4wx0o_9kx2
  python3 obvious_diagnose.py rpl_moj4wx0o_9kx2 --account /root/obvious-accounts/cz-test1
  python3 obvious_diagnose.py --tail 5            # 诊断最近 5 条任务批量分析
"""
from __future__ import annotations
import argparse, glob, json, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from obvious_client import ObviousClient

JOB_DIR = "/root/Toolkit/.local/replit_jobs"
DEFAULT_ACC = "/root/obvious-accounts/cz-test1"


def load_job(job_id: str) -> dict:
    p = Path(JOB_DIR) / f"{job_id}.json"
    if not p.exists():
        sys.exit(f"job 不存在: {p}")
    return json.loads(p.read_text())


def summarize_job(j: dict) -> str:
    logs = j.get("logs") or []
    res = j.get("result") or {}
    err = (res.get("results") or [{}])[0].get("error", "")
    head = "\n".join(logs[:6])
    tail = "\n".join(logs[-25:])
    return (
        f"job_id: {j.get('id')}\n"
        f"status: {j.get('status')} duration: {(j.get('finished',0)-j.get('started',0))/1000:.1f}s\n"
        f"summary: {res.get('summary')}\n"
        f"error: {err}\n\n"
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


def diagnose(client: ObviousClient, job_id: str) -> str:
    j = load_job(job_id)
    summary = summarize_job(j)
    prompt = PROMPT_TEMPLATE.format(job_id=job_id, summary=summary)
    print(f"[obvious] sending {len(prompt)} chars about {job_id} (mode={client.mode}) …", file=sys.stderr)
    msgs = client.ask(prompt)
    return client.extract_text(msgs)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id", nargs="?", help="rpl_xxx 任务 id")
    ap.add_argument("--account", default=DEFAULT_ACC, help="obvious 账号目录 (含 manifest.json + storage_state.json)")
    ap.add_argument("--mode", default="deep", choices=["auto", "fast", "deep", "analyst", "skill-builder"])
    ap.add_argument("--tail", type=int, default=0, help="批量诊断最近 N 条 (忽略 job_id)")
    args = ap.parse_args(argv)

    acc = Path(args.account)
    manifest = json.loads((acc / "manifest.json").read_text())
    client = ObviousClient.from_storage_state(
        str(acc / "storage_state.json"),
        thread_id=manifest["threadId"], project_id=manifest["projectId"],
        mode=args.mode,
    )

    if args.tail > 0:
        files = sorted(glob.glob(f"{JOB_DIR}/rpl_*.json"), key=os.path.getmtime, reverse=True)
        ids = []
        for f in files[: args.tail * 4]:
            try:
                j = json.loads(open(f).read())
                if (j.get("result") or {}).get("okCount", 0) == 0:
                    ids.append(j["id"])
                if len(ids) >= args.tail: break
            except Exception: pass
        for jid in ids:
            print("=" * 80); print(f"# {jid}"); print("=" * 80)
            print(diagnose(client, jid)); print()
        return 0

    if not args.job_id: sys.exit("需要 job_id 或 --tail N")
    print(diagnose(client, args.job_id))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
