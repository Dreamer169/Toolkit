#!/usr/bin/env python3
"""
kiro_subscribe.py — Kiro Pro 订阅模块
注册成功后自动获取 $0 Pro 试用的 Stripe 支付 URL。

API 流程:
  1. POST /listAvailableSubscriptions → 获取可用套餐（含 subscriptionType）
  2. POST /CreateSubscriptionToken   → 获取一次性 Stripe 支付 URL

无需 Playwright，纯 HTTP 调用。
"""
import json
import uuid
import urllib.request
import urllib.error
from datetime import datetime

CODEWHISPERER_ENDPOINT = "https://q.us-east-1.amazonaws.com"

FIXED_PROFILE_ARNS = {
    "BuilderId": "arn:aws:codewhisperer:us-east-1:638616132270:profile/AAAACCCCXXXX",
    "Github":    "arn:aws:codewhisperer:us-east-1:699475941385:profile/EHGA3GRVQMUK",
    "Google":    "arn:aws:codewhisperer:us-east-1:699475941385:profile/EHGA3GRVQMUK",
}


def _post(url, payload, access_token, timeout=30):
    """POST JSON, return (status_code, dict)."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
            "x-amz-target": "com.amazonaws.codewhisperer",
        },
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read()
        except Exception:
            pass
        return e.code, {"_error_body": body.decode(errors="replace")[:500]}
    except Exception as exc:
        return 0, {"_exception": str(exc)}


def list_available_subscriptions(access_token, profile_arn, log=print):
    """
    返回 {"ok": True, "plans": [...], "data": {...}}
         {"ok": False, "error": ...}
    """
    url = f"{CODEWHISPERER_ENDPOINT}/listAvailableSubscriptions"
    status, data = _post(url, {"profileArn": profile_arn}, access_token)

    if status == 200:
        plans = data.get("subscriptionPlans", [])
        log(f"获取到 {len(plans)} 个套餐", "ok")
        for p in plans:
            title   = p.get("description", {}).get("title", "Unknown")
            pricing = p.get("pricing", {})
            amount  = pricing.get("amount", -1)
            currency= pricing.get("currency", "")
            sub_type= p.get("qSubscriptionType", "")
            log(f"  [{sub_type}] {title} - {amount} {currency}", "dbg")
        return {"ok": True, "plans": plans, "data": data,
                "disclaimer": data.get("disclaimer", [])}
    else:
        log(f"查询套餐失败: HTTP {status} - {data}", "error")
        return {"ok": False, "error": {"status": status, "body": data}}


def create_subscription_token(access_token, profile_arn, subscription_type,
                              success_url=None, cancel_url=None, log=print):
    """
    返回 {"ok": True, "url": "...", "token": "...", "status": "..."}
         {"ok": False, "error": ...}
    """
    url = f"{CODEWHISPERER_ENDPOINT}/CreateSubscriptionToken"
    payload = {
        "provider": "STRIPE",
        "subscriptionType": subscription_type,
        "profileArn": profile_arn,
        "clientToken": str(uuid.uuid4()),
    }
    if success_url:
        payload["successUrl"] = success_url
    if cancel_url:
        payload["cancelUrl"] = cancel_url

    log(f"创建订阅 Token (type={subscription_type})...", "info")
    status, data = _post(url, payload, access_token)

    if status == 200:
        encoded_url = data.get("encodedVerificationUrl", "")
        tok_status  = data.get("status", "")
        token       = data.get("token", "")
        if encoded_url:
            log(f"获取支付 URL 成功 (status={tok_status})", "ok")
            log(f"[重要] 支付 URL (一次性): {encoded_url[:80]}...", "warn")
        else:
            log(f"响应中无 encodedVerificationUrl, status={tok_status}", "warn")
        return {"ok": True, "url": encoded_url, "token": token,
                "status": tok_status, "raw": data}
    else:
        log(f"创建订阅 Token 失败: HTTP {status} - {data}", "error")
        return {"ok": False, "error": {"status": status, "body": data}}


def subscribe_pro(access_token, profile_arn=None, provider="BuilderId",
                  subscription_type=None, log=print):
    """
    完整 Pro 订阅流程: 查询套餐 → 选择 Pro → 获取支付 URL。

    Returns:
        dict with ok, payment_url, subscription_type, timestamp  — 或 None
    """
    if not profile_arn:
        profile_arn = FIXED_PROFILE_ARNS.get(provider, FIXED_PROFILE_ARNS["BuilderId"])

    log("=" * 50, "ok")
    log("开始 Pro 订阅流程", "info")
    log(f"  Provider: {provider}  ProfileArn: {profile_arn}", "info")
    log("=" * 50, "ok")

    # Step 1: 查询可用套餐
    plans_result = list_available_subscriptions(access_token, profile_arn, log)
    if not plans_result["ok"]:
        log("无法获取套餐列表，流程中止", "error")
        return None

    plans = plans_result["plans"]

    # Step 2: 选择 Pro 套餐（优先选含 PRO 但非 PLUS/POWER 的）
    if not subscription_type:
        for plan in plans:
            st = plan.get("qSubscriptionType", "").upper()
            if "PRO" in st and "PLUS" not in st and "POWER" not in st:
                subscription_type = plan.get("qSubscriptionType")
                break
        if not subscription_type:
            for plan in plans:
                st = plan.get("qSubscriptionType", "").upper()
                if "FREE" not in st:
                    subscription_type = plan.get("qSubscriptionType")
                    break

    if not subscription_type:
        log("未找到可用的 Pro 套餐", "error")
        return {"ok": False, "plans": plans, "error": "no_pro_plan"}

    log(f"选择套餐: {subscription_type}", "ok")

    # Step 3: 获取一次性 Stripe 支付 URL
    token_result = create_subscription_token(
        access_token, profile_arn, subscription_type, log=log
    )
    if not token_result["ok"]:
        log("获取支付 URL 失败", "error")
        return {"ok": False, "plans": plans, "error": token_result.get("error")}

    payment_url = token_result["url"]

    log("=" * 50, "ok")
    log("Pro 订阅流程完成", "ok")
    log(f"  套餐: {subscription_type}", "info")
    log(f"  [警告] 此 URL 为一次性链接，关闭后无法再获得 $0 试用", "warn")
    log("=" * 50, "ok")

    return {
        "ok": bool(payment_url),
        "payment_url": payment_url,
        "subscription_type": subscription_type,
        "token": token_result.get("token"),
        "status": token_result.get("status"),
        "profile_arn": profile_arn,
        "plans": plans,
        "disclaimer": plans_result.get("disclaimer", []),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ── 便捷入口 ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    def _log(msg, level="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] [{level.upper():5s}] {msg}")

    if len(sys.argv) < 2:
        print("用法: python3 kiro_subscribe.py <access_token> [profile_arn]")
        sys.exit(1)

    token_arg = sys.argv[1]
    pa_arg    = sys.argv[2] if len(sys.argv) > 2 else None

    result = subscribe_pro(token_arg, profile_arn=pa_arg, log=_log)
    if result and result.get("ok"):
        print("\n" + "=" * 60)
        print(f"支付 URL: {result['payment_url']}")
        print("=" * 60)
    else:
        print("订阅流程失败:", result)
