#!/usr/bin/env python3
"""
replit_onboard.py — Replit 子节点自动上线脚本
用法:
  单个接入:  python3 replit_onboard.py https://your-app.replit.app/api/gateway
  批量接入:  python3 replit_onboard.py --file urls.txt
  只探测:    python3 replit_onboard.py --probe-only https://...
  帮助:      python3 replit_onboard.py --help

urls.txt 格式（每行一个）:
  https://abc.replit.app/api/gateway
  https://def.replit.app/api/gateway  my-api-key
  # 注释行被忽略
"""

import asyncio
import argparse
import json
import sys
import time
from typing import Optional
import urllib.request
import urllib.error

GATEWAY_API = "http://localhost:8080/api/gateway"   # 本地 gateway 管理 API
PROBE_TIMEOUT = 10      # 探测超时(s)
CONCURRENCY = 8         # 并发探测数

# ── ANSI 颜色 ────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"
OK     = f"{GREEN}✅{RESET}"
FAIL   = f"{RED}❌{RESET}"
WARN   = f"{YELLOW}⚠️ {RESET}"

# ── 工具函数 ─────────────────────────────────────────────────────────────────
def http_json(url: str, method: str = "GET", data=None, timeout: int = 10):
    body = json.dumps(data).encode() if data else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}

async def probe_url(session_url: str, api_key: Optional[str] = None) -> dict:
    """探测一个 Replit 子节点 URL，返回探测结果"""
    loop = asyncio.get_event_loop()
    t0 = time.time()
    base = session_url.rstrip("/")

    def _probe():
        for path in ["/health", "/v1/models", "/nodes", "/stats"]:
            headers = {"Accept": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            req = urllib.request.Request(
                f"{base}{path}", headers=headers, method="GET"
            )
            try:
                with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as resp:
                    data = json.loads(resp.read())
                    models = []
                    if isinstance(data.get("data"), list):
                        models = [m.get("id","") for m in data["data"][:5]]
                    return {
                        "ok": True,
                        "latencyMs": int((time.time() - t0) * 1000),
                        "path": path,
                        "models": models,
                        "status": resp.status,
                    }
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    return {"ok": False, "error": f"HTTP {e.code} 认证失败", "latencyMs": int((time.time()-t0)*1000)}
            except Exception:
                pass
        return {"ok": False, "error": "所有路径均无响应", "latencyMs": int((time.time()-t0)*1000)}

    result = await loop.run_in_executor(None, _probe)
    return {"url": base, "apiKey": api_key, **result}

async def register_node(probe_result: dict, model: str = "gpt-5-mini", priority: int = 3) -> dict:
    """将探测成功的节点注册到本地 gateway"""
    if not probe_result.get("ok"):
        return {"registered": False, "error": probe_result.get("error")}

    loop = asyncio.get_event_loop()
    base_url = probe_result["url"]
    hostname = base_url.split("//")[-1].split(".")[0]

    def _register():
        return http_json(f"{GATEWAY_API}/nodes", method="POST", data={
            "baseUrl": base_url,
            "apiKey": probe_result.get("apiKey"),
            "name": f"Replit({hostname})",
            "model": model,
            "priority": priority,
        })

    result = await loop.run_in_executor(None, _register)
    added = result.get("added", [])
    if added:
        return {"registered": True, "nodeId": added[0].get("id"), "name": added[0].get("name")}
    elif result.get("success") is False and "去重" in str(result.get("error","")) :
        return {"registered": True, "nodeId": None, "name": hostname, "note": "已存在"}
    else:
        return {"registered": False, "error": result.get("error", str(result))}

async def health_check(node_id: str) -> dict:
    """调用 gateway 对指定节点做 chat 测试"""
    loop = asyncio.get_event_loop()
    def _test():
        return http_json(f"{GATEWAY_API}/nodes/{node_id}/test", method="POST", data={})
    result = await loop.run_in_executor(None, _test)
    return result

async def onboard_one(url: str, api_key: Optional[str] = None,
                      model: str = "gpt-5-mini", priority: int = 3,
                      probe_only: bool = False) -> dict:
    """全流程：探测 → 注册 → 健康检查"""
    # 1. 探测
    probe = await probe_url(url, api_key)
    if not probe["ok"]:
        return {
            "url": url, "phase": "probe", "ok": False,
            "error": probe.get("error"), "latencyMs": probe.get("latencyMs"),
        }

    if probe_only:
        return {"url": url, "phase": "probe", "ok": True,
                "latencyMs": probe.get("latencyMs"), "models": probe.get("models", [])}

    # 2. 注册
    reg = await register_node(probe, model=model, priority=priority)
    if not reg.get("registered"):
        return {
            "url": url, "phase": "register", "ok": False,
            "error": reg.get("error"), "latencyMs": probe.get("latencyMs"),
        }

    # 3. 健康检查
    node_id = reg.get("nodeId")
    health = {}
    if node_id:
        health = await health_check(node_id)

    return {
        "url": url, "phase": "done", "ok": True,
        "latencyMs": probe.get("latencyMs"),
        "models": probe.get("models", []),
        "nodeId": node_id,
        "nodeName": reg.get("name"),
        "healthOk": health.get("success", False),
        "healthContent": health.get("content", ""),
    }

async def batch_onboard(entries: list[tuple[str, Optional[str]]],
                        model: str, priority: int, probe_only: bool):
    """并发批量处理（最多 CONCURRENCY 个）"""
    sem = asyncio.Semaphore(CONCURRENCY)

    async def _run(url, key):
        async with sem:
            return await onboard_one(url, key, model=model, priority=priority, probe_only=probe_only)

    tasks = [_run(url, key) for url, key in entries]
    return await asyncio.gather(*tasks, return_exceptions=True)

def print_table(results):
    """打印汇总表格"""
    W = 60
    print(f"\n{BOLD}{'─'*W}{RESET}")
    print(f"{BOLD}{'URL':<42} {'延迟':>6} {'状态':>8} {'健康'}{RESET}")
    print(f"{'─'*W}{RESET}")
    ok_count = 0
    for r in results:
        if isinstance(r, Exception):
            print(f"{'[异常]':<42} {'—':>6} {FAIL:>8}")
            continue
        url = str(r.get("url", ""))[:40]
        latency = f"{r.get('latencyMs',0)}ms"
        ok = r.get("ok", False)
        health = "✅" if r.get("healthOk") else ("—" if r.get("phase")=="probe" else "❌")
        status = OK if ok else FAIL
        if ok:
            ok_count += 1
        print(f"{url:<42} {latency:>6} {status} {health}")
    print(f"{'─'*W}")
    total = len(results)
    print(f"{BOLD}汇总: {ok_count}/{total} 成功{RESET}")
    print()

def load_url_file(path: str) -> list[tuple[str, Optional[str]]]:
    """读取 URL 文件，格式：URL [可选 API_KEY]"""
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            url = parts[0]
            key = parts[1] if len(parts) > 1 else None
            entries.append((url, key))
    return entries

async def main():
    parser = argparse.ArgumentParser(
        description="Replit 子节点自动上线脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("urls", nargs="*", help="Replit 子节点 URL（可多个）")
    parser.add_argument("--file", "-f", help="从文件批量读取 URL（每行一个）")
    parser.add_argument("--model", default="gpt-5-mini", help="注册为节点的模型名 (默认: gpt-5-mini)")
    parser.add_argument("--priority", type=int, default=3, help="节点优先级 (默认: 3)")
    parser.add_argument("--probe-only", action="store_true", help="只探测，不注册")
    parser.add_argument("--api-key", help="所有节点使用的统一 API Key")
    parser.add_argument("--gateway", help="本地 gateway API URL (默认: http://localhost:8080/api/gateway)")
    args = parser.parse_args()

    global GATEWAY_API
    if args.gateway:
        GATEWAY_API = args.gateway.rstrip("/")

    entries: list[tuple[str, Optional[str]]] = []
    if args.file:
        entries = load_url_file(args.file)
    for url in args.urls:
        entries.append((url, args.api_key))

    if not entries:
        parser.print_help()
        sys.exit(1)

    action = "探测" if args.probe_only else "探测 + 注册 + 健康检查"
    print(f"\n{CYAN}{BOLD}Replit 子节点批量上线 — {action}{RESET}")
    print(f"共 {len(entries)} 个节点，并发数 {CONCURRENCY}\n")

    results = await batch_onboard(entries, args.model, args.priority, args.probe_only)
    print_table(results)

    # 打印详情（失败项）
    failed = [r for r in results if isinstance(r, Exception) or not r.get("ok")]
    if failed:
        print(f"{YELLOW}失败详情:{RESET}")
        for r in failed:
            if isinstance(r, Exception):
                print(f"  [异常] {r}")
            else:
                print(f"  {r.get('url','')} → {r.get('error','未知错误')} (phase={r.get('phase','')})")
        print()

if __name__ == "__main__":
    asyncio.run(main())
