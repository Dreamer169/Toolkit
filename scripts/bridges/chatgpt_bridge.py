#!/usr/bin/env python3
"""
桥接脚本：把 MasterAlanLab/Register 下 openai-register 产出的 token JSON
自动导入到 chatgpt2api 的账号池 (POST /api/accounts)。

幂等：处理过的 token_*.json 文件会被移动到 tokens/imported/，不会重复导入。
"""
import json
import os
import shutil
import sys
import urllib.request

TOKENS_DIR = "/data/Toolkit/reference-tools/Register/openai-register/tokens"
IMPORTED_DIR = os.path.join(TOKENS_DIR, "imported")
CHATGPT2API_URL = os.environ.get("CHATGPT2API_URL", "http://127.0.0.1:9103")
CHATGPT2API_AUTH_KEY = os.environ.get("CHATGPT2API_AUTH_KEY", "chatgpt2api")


def main() -> int:
    if not os.path.isdir(TOKENS_DIR):
        print(f"[chatgpt_bridge] 尚无注册产出目录: {TOKENS_DIR}，跳过本次导入")
        return 0

    os.makedirs(IMPORTED_DIR, exist_ok=True)

    candidates = [
        fn for fn in os.listdir(TOKENS_DIR)
        if fn.startswith("token_") and fn.endswith(".json")
        and os.path.isfile(os.path.join(TOKENS_DIR, fn))
    ]

    if not candidates:
        print("[chatgpt_bridge] 没有新账号需要导入")
        return 0

    accounts = []
    valid_files = []
    for fn in candidates:
        path = os.path.join(TOKENS_DIR, fn)
        try:
            data = json.loads(open(path).read())
        except Exception as exc:
            print(f"[chatgpt_bridge] 跳过无法解析的文件 {fn}: {exc}", file=sys.stderr)
            continue
        if not data.get("access_token"):
            print(f"[chatgpt_bridge] 跳过缺少 access_token 的文件 {fn}")
            continue
        accounts.append({
            "access_token": data.get("access_token", ""),
            "refresh_token": data.get("refresh_token", ""),
            "id_token": data.get("id_token", ""),
            "source_type": "register-tool-auto-import",
        })
        valid_files.append(fn)

    if not accounts:
        print("[chatgpt_bridge] 没有有效账号需要导入")
        return 0

    payload = json.dumps({"accounts": accounts}).encode()
    req = urllib.request.Request(
        f"{CHATGPT2API_URL}/api/accounts",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {CHATGPT2API_AUTH_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode()
            print(f"[chatgpt_bridge] 导入结果: {body}")
    except Exception as exc:
        print(f"[chatgpt_bridge] 导入失败，本次不移动文件，下次重试: {exc}", file=sys.stderr)
        return 1

    for fn in valid_files:
        shutil.move(os.path.join(TOKENS_DIR, fn), os.path.join(IMPORTED_DIR, fn))

    print(f"[chatgpt_bridge] 已导入 {len(valid_files)} 个新账号到 chatgpt2api")
    return 0


if __name__ == "__main__":
    sys.exit(main())
