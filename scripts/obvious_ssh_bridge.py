#!/usr/bin/env python3
"""
obvious_ssh_bridge.py — 把VPS住宅代理穿透进obvious沙箱

原理:
  obvious e2b沙箱出口是datacenter IP, reCAPTCHA Enterprise必拒.
  通过SSH本地端口转发, 给沙箱注入住宅代理:

    sandbox:1080 --SSH--> VPS:127.0.0.1:10857 --xray--> TW中华电信(住宅)

  SSH key限制为只能端口转发(no-pty, permitopen), 无法执行VPS命令.
  e2b沙箱在会话间持久存在, 隧道建立一次即可复用.

使用:
  python3 obvious_ssh_bridge.py --setup              # 在最佳账号沙箱建立隧道
  python3 obvious_ssh_bridge.py --status             # 检查所有账号隧道状态+出口IP
  python3 obvious_ssh_bridge.py --teardown           # 关闭隧道
  python3 obvious_ssh_bridge.py --setup --account eu-test1
  python3 obvious_ssh_bridge.py --setup --port 10857 # 指定住宅端口
"""
from __future__ import annotations
import argparse, base64, json, sys, textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from obvious_client import ObviousClient
from obvious_pool import ObviousPool, Account, DEFAULT_ACC_DIR

VPS_HOST = "45.205.27.69"
VPS_USER = "root"
PROXY_PORTS = [10857, 10855, 10853, 10854, 10859, 10851]
SSH_LOCAL_PORT = 1080

# 专用 SSH 私钥 (no-pty, permitopen 限制)
# base64 of /root/.ssh/obvious_bridge_key
SSH_PRIVKEY_B64 = (
    "LS0tLS1CRUdJTiBPUEVOU1NIIFBSSVZBVEUgS0VZLS0tLS0KYjNCbGJuTnphQzFyWlhrdGRqRUFBQUFBQkc1dmJtVUFBQUFFYm05dVpRQUFBQUFBQUFBQkFBQUFNd0FBQUF0emMyZ3RaVwpReU5UVXhPUUFBQUNBTEpZRHQ3RTBlSGJabzlVVThCU3VwTzQ0SXdQNkFBZVBZWXFxdkFFV3hjQUFBQUtESkt4NWN5U3NlClhBQUFBQXR6YzJndFpXUXlOVFV4T1FBQUFDQUxKWUR0N0UwZUhiWm85VVU4QlN1cE80NEl3UDZBQWVQWVlxcXZBRVd4Y0EKQUFBRURGQ1VLM1B3Y3pSaVVLZ0FFM1dCOHZ3RGhrUGVFVGpLWEtoM3hmaUZMbzFEMzV5U2N6NDZCeUxFQy9FY1FBQUFBRm0KOWlkbWx2ZFhNdGMyRnVaR2hoWXpBdFluSnBaR2RsQVFJREJBVUdCdz09Ci0tLS0tRU5EIE9QRU5TU0ggUFJJVkFURSBLRVktLS0tLQo="
)


def _build_bridge_prompt(port: int) -> str:
    privkey = base64.b64decode(SSH_PRIVKEY_B64).decode()
    other_ports = " ".join(str(p) for p in PROXY_PORTS if p != port)
    return (
        "Run these shell commands IN YOUR E2B SANDBOX and report ALL output verbatim.\n"
        "Do NOT explain. Just execute and print each command's output.\n\n"
        "```bash\n"
        "# 1. Setup SSH key\n"
        "mkdir -p /root/.ssh && chmod 700 /root/.ssh\n"
        f"printf '%s' '{privkey}' > /root/.ssh/obvious_bridge_key\n"
        "chmod 600 /root/.ssh/obvious_bridge_key\n"
        "echo KEY_WRITTEN\n\n"
        "# 2. Kill existing tunnel\n"
        f"pkill -f 'ssh.*-L {SSH_LOCAL_PORT}' 2>/dev/null; sleep 1; echo KILLED_OLD\n\n"
        "# 3. Install ssh client if missing\n"
        "command -v ssh >/dev/null || (apt-get update -qq && apt-get install -y -qq openssh-client 2>/dev/null)\n"
        "echo SSH_READY\n\n"
        "# 4. Establish tunnel, trying each proxy port\n"
        "TUNNEL_PORT=0\n"
        f"for P in {port} {other_ports}; do\n"
        f"  ssh -f -N -i /root/.ssh/obvious_bridge_key -o StrictHostKeyChecking=no -o ConnectTimeout=12 -o ServerAliveInterval=25 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -L {SSH_LOCAL_PORT}:127.0.0.1:$P {VPS_USER}@{VPS_HOST} && TUNNEL_PORT=$P && echo \"TUNNEL_OK port=$P\" && break || echo \"TUNNEL_FAIL port=$P\"\n"
        "done\n\n"
        "# 5. Verify exit IP\n"
        "sleep 2\n"
        f"EXIT_IP=$(curl -s --max-time 10 -x socks5h://127.0.0.1:{SSH_LOCAL_PORT} https://api.ipify.org 2>/dev/null)\n"
        'echo "EXIT_IP=${EXIT_IP:-FAILED}"\n'
        "# 6. ASN check\n"
        f"curl -s --max-time 8 -x socks5h://127.0.0.1:{SSH_LOCAL_PORT} 'https://ip-api.com/json?fields=query,country,isp,org,as,mobile,proxy,hosting' 2>/dev/null\n"
        "```\n"
    )


def _build_status_prompt() -> str:
    return (
        "Run these commands in your e2b sandbox and print all output:\n"
        "```bash\n"
        f"echo '=== tunnel ==='; pgrep -a ssh | grep 'L {SSH_LOCAL_PORT}' || echo NO_TUNNEL\n"
        f"echo '=== exit IP ==='; curl -s --max-time 8 -x socks5h://127.0.0.1:{SSH_LOCAL_PORT} https://api.ipify.org && echo || echo UNREACHABLE\n"
        f"echo '=== asn ==='; curl -s --max-time 8 -x socks5h://127.0.0.1:{SSH_LOCAL_PORT} 'https://ip-api.com/json?fields=query,country,isp,org,as,mobile,proxy,hosting'\n"
        "```\n"
    )


def _build_teardown_prompt() -> str:
    return (
        "Run in your sandbox:\n"
        "```bash\n"
        f"pkill -f 'ssh.*-L {SSH_LOCAL_PORT}' && echo TEARDOWN_OK || echo TEARDOWN_NOTHING\n"
        "```\n"
    )


def _run_on_account(acc_dir: Path, prompt: str, mode: str = "fast") -> str:
    m = json.loads((acc_dir / "manifest.json").read_text())
    client = ObviousClient.from_storage_state(
        str(acc_dir / "storage_state.json"),
        thread_id=m["threadId"], project_id=m["projectId"],
        mode=mode,
    )
    client.poll_timeout = 200
    msgs = client.ask(prompt)
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
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--teardown", action="store_true")
    ap.add_argument("--account", default="")
    ap.add_argument("--port", type=int, default=10857)
    ap.add_argument("--acc-dir", default=str(DEFAULT_ACC_DIR))
    args = ap.parse_args(argv)

    acc_dir_root = Path(args.acc_dir)

    if args.account:
        targets = [acc_dir_root / args.account]
    else:
        pool = ObviousPool(acc_dir_root)
        pool.refresh_health()
        healthy = pool.healthy(min_credits=0.3)
        if not healthy:
            sys.exit("no healthy obvious accounts")
        targets = [h.dir for h in healthy[:2]]

    if args.setup:
        prompt = _build_bridge_prompt(args.port)
        action = "SETUP"
    elif args.status:
        prompt = _build_status_prompt()
        action = "STATUS"
    elif args.teardown:
        prompt = _build_teardown_prompt()
        action = "TEARDOWN"
    else:
        ap.print_help()
        return 1

    for acc_dir in targets:
        print(f"[{action}] account={acc_dir.name}", file=sys.stderr)
        try:
            out = _run_on_account(acc_dir, prompt)
            print(f"\n=== {acc_dir.name} ===\n{out}")
        except Exception as e:
            print(f"[{acc_dir.name}] FAIL: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
