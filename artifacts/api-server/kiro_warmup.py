#!/usr/bin/env python3
"""
kiro_warmup.py — 账号预热模块

注册成功后、发起 Pro 订阅前，模拟 Kiro IDE 首次启动的 API 调用序列。
目的：让 AWS 反欺诈系统识别为真实客户端，降低 CreateSubscriptionToken 403 概率。
"""
import json
import random
import time
import urllib.error
import urllib.request

CW_ENDPOINT   = "https://q.us-east-1.amazonaws.com"
KIRO_API_BASE = "https://api.kiro.dev"

_FIXED_PROFILE = "arn:aws:codewhisperer:us-east-1:638616132270:profile/AAAACCCCXXXX"


def _cw_post(path: str, payload: dict, token: str, timeout: int = 20):
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        CW_ENDPOINT + path, data=data,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent":    "aws-toolkit-jetbrains/2.0",
        },
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = b""
        try: body = e.read()
        except Exception: pass
        return e.code, {"_err": body.decode(errors="replace")[:200]}
    except Exception as exc:
        return 0, {"_exc": str(exc)}


def _kiro_get(path: str, token: str, timeout: int = 15):
    req = urllib.request.Request(
        KIRO_API_BASE + path,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept":        "application/json",
            "User-Agent":    "Kiro/1.0",
        },
        method="GET",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception as exc:
        return 0, {"_exc": str(exc)}


def warmup(access_token: str, profile_arn: str = "", log=print) -> None:
    """
    模拟 Kiro IDE 首次启动行为，共约 15-30 秒。
    调用方在 warmup() 返回后再调 subscribe_pro()。
    """
    if not profile_arn:
        profile_arn = _FIXED_PROFILE

    log("[WARMUP] ═══ 账号预热开始 (模拟 Kiro IDE 首次启动) ═══")

    # ── Step 1: 查询可用订阅套餐（只读，模拟 IDE 启动时检查订阅状态）
    t = time.time()
    s, d = _cw_post("/listAvailableSubscriptions", {"profileArn": profile_arn}, access_token)
    plans_n = len((d.get("subscriptionPlans") or []))
    log(f"[WARMUP] listAvailableSubscriptions → HTTP {s}, {plans_n} 个套餐 ({int((time.time()-t)*1000)}ms)")
    time.sleep(random.uniform(1.5, 3.0))

    # ── Step 2: 尝试访问 Kiro 用户信息接口（模拟 IDE 读取 profile）
    t = time.time()
    s2, _ = _kiro_get("/user/profile", access_token)
    log(f"[WARMUP] GET /user/profile → HTTP {s2} ({int((time.time()-t)*1000)}ms)")
    time.sleep(random.uniform(1.0, 2.5))

    # ── Step 3: 再次查询（模拟 IDE 后台刷新）
    t = time.time()
    s3, _ = _cw_post("/listAvailableSubscriptions", {"profileArn": profile_arn}, access_token)
    log(f"[WARMUP] listAvailableSubscriptions(2) → HTTP {s3} ({int((time.time()-t)*1000)}ms)")

    # ── Step 4: 随机等待（模拟用户浏览欢迎页/配置向导）
    pause = random.uniform(8, 18)
    log(f"[WARMUP] 模拟用户操作停顿 {pause:.1f}s ...")
    time.sleep(pause)

    log("[WARMUP] ✅ 预热完成，准备发起订阅")
