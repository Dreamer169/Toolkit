#!/usr/bin/env python3
"""
桥接脚本：把 MasterAlanLab/Register 下 grok-register 产出的 SSO token
自动导入到 grok2api 的账号池 (POST /admin/api/tokens/add)。

幂等：用 state 文件记录已处理的行号，每次只导入新增账号，可反复由 systemd timer 触发。
"""
import json
import os
import sys
import urllib.request

KEYS_FILE = "/data/Toolkit/reference-tools/Register/grok-register/keys/grok.txt"
STATE_FILE = "/data/Toolkit/state/grok_bridge.state"
GROK2API_URL = os.environ.get("GROK2API_URL", "http://127.0.0.1:9102")
GROK2API_APP_KEY = os.environ.get("GROK2API_APP_KEY", "grok2api")
POOL = os.environ.get("GROK2API_POOL", "basic")


def load_state() -> int:
    if os.path.exists(STATE_FILE):
        try:
            return int(open(STATE_FILE).read().strip() or "0")
        except ValueError:
            return 0
    return 0


def save_state(n: int) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        f.write(str(n))


def main() -> int:
    if not os.path.exists(KEYS_FILE):
        print(f"[grok_bridge] 尚无注册产出文件: {KEYS_FILE}，跳过本次导入")
        return 0

    with open(KEYS_FILE) as f:
        lines = [ln.strip() for ln in f.readlines()]

    processed = load_state()
    new_tokens = [ln for ln in lines[processed:] if ln]

    if not new_tokens:
        print("[grok_bridge] 没有新账号需要导入")
        return 0

    payload = json.dumps({"tokens": new_tokens, "pool": POOL, "tags": ["auto-import", "register-tool"]}).encode()
    req = urllib.request.Request(
        f"{GROK2API_URL}/admin/api/tokens/add",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {GROK2API_APP_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode()
            print(f"[grok_bridge] 导入结果: {body}")
    except Exception as exc:
        print(f"[grok_bridge] 导入失败，本次不推进游标，下次重试: {exc}", file=sys.stderr)
        return 1

    save_state(len(lines))
    print(f"[grok_bridge] 已导入 {len(new_tokens)} 个新账号到 grok2api pool={POOL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
