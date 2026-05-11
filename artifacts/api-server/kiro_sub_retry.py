#!/usr/bin/env python3
"""
kiro_sub_retry.py — Pro 订阅延迟重试守护进程

扫描 accounts 表中满足以下条件的 kiro 账号，调用 subscribe_pro() 并更新结果：
  - sub_status = 'pending'   (注册后等待首次订阅)
  - sub_status = 'suspended' (上次订阅失败，按退避周期重试)
  - sub_retry_after <= NOW()

关键修复: 订阅时传入账号注册时使用的 proxy_formatted (IP 一致性)
          避免 Kiro 检测到 IP 变化触发安全锁定。

运行方式:
  python3 kiro_sub_retry.py            # 单次扫描后退出
  python3 kiro_sub_retry.py --daemon   # 无限循环，每 1h 扫描一次
"""
import argparse
import importlib.util
import json
import os
import sys
import time
from datetime import datetime

import psycopg2

DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/toolkit")
LOG_PREFIX = "[RETRY]"

# 脚本目录（/data/Toolkit/artifacts/api-server）
_API_DIR = "/data/Toolkit/artifacts/api-server"


def _db():
    return psycopg2.connect(DB_URL)


def _log(msg: str, level: str = "info"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{LOG_PREFIX} [{ts}] [{level.upper():5s}] {msg}", flush=True)


def _load_subscribe():
    spec = importlib.util.spec_from_file_location(
        "kiro_subscribe", f"{_API_DIR}/kiro_subscribe.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_warmup():
    spec = importlib.util.spec_from_file_location(
        "kiro_warmup", f"{_API_DIR}/kiro_warmup.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def pending_accounts() -> list[dict]:
    """查询待处理账号：pending(首次) 和 suspended(重试) 均纳入"""
    conn = _db()
    cur  = conn.cursor()
    cur.execute("""
        SELECT id, email, token, notes, sub_status, proxy_formatted
        FROM   accounts
        WHERE  platform = 'kiro'
          AND  sub_status IN ('pending', 'suspended')
          AND  sub_retry_after <= NOW()
        ORDER  BY sub_retry_after
        LIMIT  20
    """)
    rows = cur.fetchall()
    cur.close(); conn.close()
    result = []
    for row in rows:
        acc_id, email, access_token, notes_raw, sub_status, proxy_fmt = row
        try:
            notes = json.loads(notes_raw or "{}")
        except Exception:
            notes = {}
        result.append({
            "id":           acc_id,
            "email":        email,
            "access_token": access_token or "",
            "profile_arn":  notes.get("profileArn", ""),
            "sub_status":   sub_status,
            "proxy":        proxy_fmt or "",   # 账号注册时使用的代理，用于 IP 一致性
        })
    return result


def _update_status(acc_id: int, status: str, payment_url: str = "",
                   retry_after_hours: int = 0, extra_notes: dict | None = None):
    conn = _db()
    cur  = conn.cursor()
    if retry_after_hours > 0:
        cur.execute("""
            UPDATE accounts
            SET    sub_status = %s,
                   sub_retry_after = NOW() + (%s || ' hours')::INTERVAL,
                   updated_at = NOW()
            WHERE  id = %s
        """, (status, str(retry_after_hours), acc_id))
    else:
        cur.execute("""
            UPDATE accounts
            SET    sub_status = %s,
                   sub_retry_after = NULL,
                   updated_at = NOW()
            WHERE  id = %s
        """, (status, acc_id))

    if extra_notes or payment_url:
        cur.execute("SELECT notes FROM accounts WHERE id=%s", (acc_id,))
        row = cur.fetchone()
        try:
            notes = json.loads((row[0] if row else None) or "{}")
        except Exception:
            notes = {}
        if payment_url:
            notes["paymentUrl"] = payment_url
        if extra_notes:
            notes.update(extra_notes)
        cur.execute("UPDATE accounts SET notes=%s WHERE id=%s",
                    (json.dumps(notes, ensure_ascii=False), acc_id))

    conn.commit(); cur.close(); conn.close()


def process_account(acc: dict, ksub, kwarmup) -> str:
    acc_id       = acc["id"]
    email        = acc["email"]
    access_token = acc["access_token"]
    profile_arn  = acc["profile_arn"]
    prev_status  = acc["sub_status"]
    proxy        = acc["proxy"]  # 注册时的代理，维持 IP 一致性

    _log(f"处理账号 [{acc_id}] {email}  (前状态: {prev_status}, proxy={proxy or 'NONE'})")

    if not access_token:
        _log(f"  ❌ 无 access_token，标记 failed", "warn")
        _update_status(acc_id, "failed")
        return "failed"

    # pending = 首次订阅；suspended = 重试，先预热
    if prev_status == "suspended":
        _log(f"  预热中 (重试前再次模拟 IDE 行为)...")
        try:
            kwarmup.warmup(access_token, profile_arn=profile_arn, log=_log)
        except Exception as e:
            _log(f"  ⚠ 预热异常: {e}", "warn")
    else:
        _log(f"  pending 首次订阅，跳过预热")

    # 发起订阅 — 传入 proxy 保持 IP 一致性
    def _sub_log(msg, level="info"):
        _log(f"  {msg}", level)

    try:
        result = ksub.subscribe_pro(
            access_token,
            profile_arn=profile_arn or None,
            proxy=proxy or None,
            log=_sub_log,
        )
    except Exception as e:
        _log(f"  ❌ subscribe_pro 异常: {e}", "error")
        _update_status(acc_id, "suspended", retry_after_hours=24)
        return "suspended"

    if not result:
        _log("  ❌ 返回 None，24h 后重试", "error")
        _update_status(acc_id, "suspended", retry_after_hours=24)
        return "suspended"

    if result.get("ok") and result.get("payment_url"):
        pay_url  = result["payment_url"]
        sub_type = result.get("subscription_type", "")
        _log(f"  ✅ 订阅 URL 获取成功 (type={sub_type})")

        # ── 自动支付 (chkr.cc BIN 生成 Live 卡 → 填写 Stripe 表单) ──────────
        chkr_bins_env = os.environ.get("CHKR_BINS", "")
        chkr_bins = [b.strip() for b in chkr_bins_env.split(",") if b.strip()]
        pay_status = "url_only"
        if chkr_bins and pay_url:
            _log(f"  [chkr] 启动自动支付 (BINs: {chkr_bins[:3]}...)")
            try:
                import asyncio as _aio
                _spec2 = importlib.util.spec_from_file_location(
                    "stripe_pay", f"{_API_DIR}/stripe_pay.py",
                )
                _spmod = importlib.util.module_from_spec(_spec2)
                _spec2.loader.exec_module(_spmod)

                def _plog(msg, level="info"):
                    _log(f"    {msg}", level)

                pay_result = _aio.run(
                    _spmod.auto_pay_chkr(pay_url, bins=chkr_bins,
                                         headless=True, log=_plog,
                                         proxy=acc["proxy"])
                )
                if pay_result and pay_result.get("ok"):
                    _log(f"  ✅ 自动支付成功!")
                    pay_status = "paid"
                else:
                    stat = (pay_result or {}).get("status", "unknown")
                    _log(f"  ⚠ 自动支付失败 (status={stat})，保留 URL", "warn")
            except Exception as _pe:
                _log(f"  ⚠ 自动支付异常: {_pe}", "warn")
        else:
            _log("  [chkr] CHKR_BINS 未配置，仅保存支付 URL")

        _update_status(acc_id, "ok", payment_url=pay_url,
                       extra_notes={"subscriptionType": sub_type,
                                    "profileArn": result.get("profile_arn", ""),
                                    "payStatus": pay_status})
        return "ok"

    # 判断错误类型
    err_body = ""
    try:
        err_obj = result.get("error", {})
        if isinstance(err_obj, dict):
            raw = err_obj.get("body", {})
            err_body = json.dumps(raw) if isinstance(raw, dict) else str(raw)
    except Exception:
        pass

    if "suspended" in err_body.lower():
        backoff = 48 if prev_status == "suspended" else 24
        _log(f"  ⏳ Kiro 安全锁定 (IP/行为触发)，{backoff}h 后重试", "warn")
        _update_status(acc_id, "suspended", retry_after_hours=backoff,
                       extra_notes={"subError": "temporarily_suspended"})
        return "suspended"
    elif "invalid" in err_body.lower() and "token" in err_body.lower():
        _log(f"  ❌ Token 无效或已过期 (需重新登录)，标记 failed", "error")
        _update_status(acc_id, "failed",
                       extra_notes={"subError": "token_invalid"})
        return "failed"
    else:
        _log(f"  ❌ 其他错误: {err_body[:120]}，标记 failed", "error")
        _update_status(acc_id, "failed")
        return "failed"


def run_once():
    accs = pending_accounts()
    if not accs:
        _log("暂无待处理账号 (pending/suspended)")
        return 0

    pending_n   = sum(1 for a in accs if a["sub_status"] == "pending")
    suspended_n = sum(1 for a in accs if a["sub_status"] == "suspended")
    _log(f"发现 {len(accs)} 个账号待处理 (首次pending={pending_n} 重试suspended={suspended_n})")

    ksub    = _load_subscribe()
    kwarmup = _load_warmup()

    ok_n = fail_n = still_n = 0
    for acc in accs:
        st = process_account(acc, ksub, kwarmup)
        if st == "ok":       ok_n    += 1
        elif st == "failed": fail_n  += 1
        else:                still_n += 1
        time.sleep(3)

    _log(f"本轮结果: ✅ok={ok_n}  ❌failed={fail_n}  ⏳retry={still_n}")
    return ok_n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daemon",   action="store_true", help="持续运行，每隔 interval 秒扫描一次")
    ap.add_argument("--interval", type=int, default=3600, help="守护间隔(秒)，默认 3600")
    args = ap.parse_args()

    if args.daemon:
        _log(f"守护模式启动，扫描间隔 {args.interval}s")
        while True:
            try:
                run_once()
            except Exception as e:
                _log(f"扫描异常: {e}", "error")
            _log(f"下次扫描: {args.interval}s 后")
            time.sleep(args.interval)
    else:
        run_once()


if __name__ == "__main__":
    main()
