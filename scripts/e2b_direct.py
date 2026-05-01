#!/usr/bin/env python3
"""
e2b_direct.py v2 — 直连obvious e2b沙箱（绕过AI层）

修复的Bug:
  1. obvious API base错误: app.obvious.ai/api → api.app.obvious.ai/prepare
  2. auth_token提取错误: localStorage auth-user JSON !== Bearer token
     → obvious使用cookie鉴权，无Bearer token，直接用cookie头
  3. e2b envd URL格式错误: {id}.e2b.app → {id}-{port}.e2b.dev (需API key)
     → 需先通过obvious API拿到sandbox access credentials
  4. 沙箱可能paused → 需先通过obvious chat API唤醒

直连方案:
  A) obvious prepare API /projects/{pid} 拿 sandbox host/metadata
  B) 唤醒沙箱 (发送chat消息触发)
  C) 用 ObviousClient (cookie auth) + mode=auto 直接执行命令 (无AI层干预的最优路径)
  D) 探测envd直连 (需e2b API key, 通过sniff获取)
"""
import sys, json, time, uuid, urllib.request, urllib.error, ssl
from pathlib import Path

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else "cz-test1"
ACC_DIR = Path(f"/root/obvious-accounts/{ACCOUNT}")
manifest = json.loads((ACC_DIR / "manifest.json").read_text())
state    = json.loads((ACC_DIR / "storage_state.json").read_text())

SANDBOX_ID  = manifest["sandboxId"]
PROJECT_ID  = manifest["projectId"]
THREAD_ID   = manifest["threadId"]
WORKSPACE_ID = manifest["workspaceId"]

# ── 正确的鉴权: obvious使用cookie，不是Bearer ──────────────────────────────
cookies = "; ".join(
    c["name"] + "=" + c["value"]
    for c in state["cookies"]
    if c["domain"].endswith("obvious.ai")
)
HDR = {
    "Cookie": cookies,
    "Accept": "application/json",
    "User-Agent": "obvious-direct/2.0",
    "Origin": "https://app.obvious.ai",
    "Referer": "https://app.obvious.ai/",
}

# ── 正确的API base ───────────────────────────────────────────────────────
BASE = "https://api.app.obvious.ai/prepare"

def fetch(url, method="GET", body=None, extra_hdrs=None):
    h = dict(HDR)
    if extra_hdrs:
        h.update(extra_hdrs)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=12, context=ctx) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return -1, str(e)


print(f"[*] Account={ACCOUNT} sandbox={SANDBOX_ID}", flush=True)

# ── Step 1: 检查沙箱状态 ──────────────────────────────────────────────────
print("\n=== Step 1: sandbox metadata ===", flush=True)
s, b = fetch(f"{BASE}/projects/{PROJECT_ID}")
if s == 200:
    j = json.loads(b)
    meta = j.get("metadata", {}).get("sandbox", {})
    host = meta.get("host", "")
    paused = meta.get("isPaused", "?")
    template = meta.get("templateId", "?")
    print(f"  host={host} isPaused={paused} templateId={template}", flush=True)
else:
    print(f"  project fetch failed: {s}", flush=True)
    meta = {}
    paused = None
    host = f"{SANDBOX_ID}.e2b.app"

# ── Step 2: 唤醒沙箱 (若paused) ──────────────────────────────────────────
if paused:
    print("\n=== Step 2: wake sandbox via chat ping ===", flush=True)
    # 先获取当前消息数baseline
    s2, b2 = fetch(f"{BASE}/threads/{THREAD_ID}/messages")
    baseline = len(json.loads(b2).get("messages", [])) if s2 == 200 else 0

    wake_body = {
        "message": "run: echo SANDBOX_WAKE && date -u",
        "messageId": uuid.uuid4().hex,
        "projectId": PROJECT_ID,
        "fileIds": [],
        "modeId": "auto",  # auto模式支持run-shell
        "timezone": "UTC",
    }
    sw, bw = fetch(f"{BASE}/api/v2/agent/chat/{THREAD_ID}", method="POST", body=wake_body)
    print(f"  wake POST => {sw}", flush=True)
    if sw == 200:
        # 等待完成
        for _ in range(20):
            time.sleep(4)
            sh, bh = fetch(f"{BASE}/hydrate/project/{PROJECT_ID}")
            for line in (bh or "").splitlines():
                try:
                    obj = json.loads(line.strip())
                    data = obj.get("data", {})
                    status = data.get("agentStatus")
                    if status in ("completed", "idle", "error"):
                        print(f"  agentStatus={status}", flush=True)
                        break
                except Exception:
                    pass
            else:
                continue
            break

# ── Step 3: 验证run-shell可用 (检查最新tool-result) ─────────────────────
print("\n=== Step 3: check shell tool output ===", flush=True)
s3, b3 = fetch(f"{BASE}/threads/{THREAD_ID}/messages")
if s3 == 200:
    msgs = json.loads(b3).get("messages", [])
    for m in reversed(msgs[-10:]):
        for c in (m.get("content") or []):
            if c.get("type") == "tool-result":
                out = c.get("output") or {}
                otype = out.get("type", "?")
                if otype == "json":
                    val = (out.get("value") or {}).get("data") or {}
                    print(f"  tool-result OK: stdout={val.get('stdout','')[:80]!r}", flush=True)
                elif otype == "error-text":
                    print(f"  tool-result ERROR: {str(out.get('value',''))[:100]}", flush=True)
                break

# ── Step 4: 探测envd直连 ────────────────────────────────────────────────
# e2b envd正确URL格式: {sandboxId}-{port}.e2b.dev (需API key)
# 若无API key, 返回401而非404 (说明地址正确)
print("\n=== Step 4: e2b envd直连探测 ===", flush=True)
ctx_noverify = ssl.create_default_context()
ctx_noverify.check_hostname = False
ctx_noverify.verify_mode = ssl.CERT_NONE

# 尝试不同的URL格式
for url_tmpl in [
    f"https://{SANDBOX_ID}-49983.e2b.dev/health",
    f"https://{SANDBOX_ID}-49982.e2b.dev/",
    f"https://{host}:49983/health" if host else "",
]:
    if not url_tmpl:
        continue
    try:
        req = urllib.request.Request(url_tmpl, headers={"User-Agent": "test/1"})
        with urllib.request.urlopen(req, timeout=8, context=ctx_noverify) as r:
            print(f"  OK {url_tmpl} => {r.status} {r.read()[:80]}", flush=True)
    except urllib.error.HTTPError as e:
        body = e.read()[:80]
        print(f"  HTTP {e.code} {url_tmpl} => {body}", flush=True)
    except Exception as e:
        print(f"  ERR {url_tmpl} => {type(e).__name__}: {str(e)[:60]}", flush=True)

print("\n[*] Done. 如需e2b直连需获取API key (运行 capture_e2b_token.py 捕获).", flush=True)
