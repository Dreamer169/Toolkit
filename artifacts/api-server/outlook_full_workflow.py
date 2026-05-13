#!/usr/bin/env python3
"""
outlook_full_workflow.py — 完整 Outlook 工作流（v1.0）

流程:
  1. 从 CF pool 获取唯一 CF IP，启动 XrayRelay VLESS tunnel
  2. 用该 IP 注册 Outlook 账号 + 完成 in-browser OAuth 授权（access/refresh token）
  3. 停止 CF tunnel，写账号到 DB（含 cookies_json / fingerprint_json / exit_ip）
  4. 用 ISP 代理（tp-in/ss-in 端口）复用账号密码开启 IMAP+POP
  5. DB 追加 imap_enabled + pop_enabled tag

IP 一致性：
  - 注册 + OAuth：CF VLESS（force_dynamic=True），唯一 IP 保证 Microsoft 不判断 abuse
  - IMAP 开启：ISP/tp-in 端口（enable_imap_v5._find_isp_proxy 内部自动选），
    CF VLESS 无法渲染 MS React SPA settings panel，必须用真实 ISP IP

用法:
  python3 outlook_full_workflow.py
  python3 outlook_full_workflow.py --count 3
  python3 outlook_full_workflow.py --count 1 --headless false
  python3 outlook_full_workflow.py --email myuser --password MyPass123!
  python3 outlook_full_workflow.py --skip-imap          # 只注册+授权，不开启IMAP
"""

import argparse
import json
import os
import random
import sys
import time
import traceback

import psycopg2
import psycopg2.extras

# ── 路径设置 ────────────────────────────────────────────────────────────────
_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)

DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/toolkit")

MAX_CF_RETRIES = 3


def log(msg: str):
    print(msg, flush=True)


# ── DB 工具 ─────────────────────────────────────────────────────────────────

def db_conn():
    return psycopg2.connect(DB_URL)


def save_account_to_db(result: dict) -> int:
    """
    将注册结果写入 accounts 表。
    ON CONFLICT (platform, email) 时 UPDATE（支持重跑）。
    返回 account id。
    """
    conn = db_conn()
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO accounts
            (platform, email, password, username,
             token, refresh_token, status,
             cookies_json, fingerprint_json, user_agent,
             exit_ip, proxy_port, proxy_formatted,
             tags, created_at, updated_at)
        VALUES
            ('outlook', %(email)s, %(password)s, %(username)s,
             %(token)s, %(refresh_token)s, 'active',
             %(cookies_json)s, %(fingerprint_json)s, %(user_agent)s,
             %(exit_ip)s, %(proxy_port)s, %(proxy_formatted)s,
             '', NOW(), NOW())
        ON CONFLICT (platform, email) DO UPDATE SET
            password         = EXCLUDED.password,
            token            = EXCLUDED.token,
            refresh_token    = EXCLUDED.refresh_token,
            status           = 'active',
            cookies_json     = CASE WHEN EXCLUDED.cookies_json  <> '' THEN EXCLUDED.cookies_json  ELSE accounts.cookies_json  END,
            fingerprint_json = CASE WHEN EXCLUDED.fingerprint_json <> '' THEN EXCLUDED.fingerprint_json ELSE accounts.fingerprint_json END,
            user_agent       = CASE WHEN EXCLUDED.user_agent    <> '' THEN EXCLUDED.user_agent    ELSE accounts.user_agent    END,
            exit_ip          = CASE WHEN EXCLUDED.exit_ip       <> '' THEN EXCLUDED.exit_ip       ELSE accounts.exit_ip       END,
            proxy_port       = CASE WHEN EXCLUDED.proxy_port    > 0   THEN EXCLUDED.proxy_port    ELSE accounts.proxy_port    END,
            proxy_formatted  = CASE WHEN EXCLUDED.proxy_formatted <> '' THEN EXCLUDED.proxy_formatted ELSE accounts.proxy_formatted END,
            updated_at       = NOW()
        RETURNING id
    """, {
        "email":           result.get("email", ""),
        "password":        result.get("password", ""),
        "username":        result.get("username", result.get("email", "").split("@")[0]),
        "token":           result.get("access_token", ""),
        "refresh_token":   result.get("refresh_token", ""),
        "cookies_json":    result.get("cookies_json", ""),
        "fingerprint_json":result.get("fingerprint_json", ""),
        "user_agent":      result.get("user_agent", ""),
        "exit_ip":         result.get("exit_ip", ""),
        "proxy_port":      int(result.get("proxy_port") or 0),
        "proxy_formatted": result.get("proxy_formatted", ""),
    })
    acc_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    log(f"  [db] 账号写入完成 id={acc_id} email={result.get('email')}")
    return acc_id


def db_add_tags(account_id: int, *tags: str):
    """给账号追加 tag（去重，逗号分隔，利用 normalize trigger）"""
    conn = db_conn()
    cur  = conn.cursor()
    # 先读现有 tags
    cur.execute("SELECT tags FROM accounts WHERE id=%s", (account_id,))
    row = cur.fetchone()
    existing = set(t.strip() for t in (row[0] or "").split(",") if t.strip()) if row else set()
    new_tags = existing | set(tags)
    cur.execute(
        "UPDATE accounts SET tags=%s, updated_at=NOW() WHERE id=%s",
        (",".join(sorted(new_tags)), account_id),
    )
    conn.commit()
    cur.close()
    conn.close()
    log(f"  [db] tags 更新: id={account_id} tags={','.join(sorted(new_tags))}")


# ── CF IP 池 ─────────────────────────────────────────────────────────────────

def _get_cf_ip(job_id: str):
    """从 CF pool 取一个 IP，返回 ip_info dict 或 None。"""
    import cf_ip_pool
    ip_info = cf_ip_pool.acquire_ip(job_id, auto_refresh=True,
                                    log_cb=lambda m: log(f"   [cf_pool] {m}"))
    return ip_info


def _release_cf_ip(job_id: str):
    import cf_ip_pool
    cf_ip_pool.release_ip(job_id)


def _ban_cf_ip(ip: str):
    import cf_ip_pool
    cf_ip_pool.ban_ip(ip)


# ── 主注册函数 ───────────────────────────────────────────────────────────────

def run_register_and_oauth(
    planned_email: str = "",
    planned_password: str = "",
    proxy: str = "",
    exit_ip: str = "",
    proxy_port: int = 0,
    headless: bool = True,
) -> dict:
    """
    调用 outlook_register.register_one() 完成注册 + in-browser OAuth。
    返回 result dict（含 success, email, password, access_token, refresh_token,
    cookies_json, fingerprint_json, user_agent, exit_ip, proxy_port, proxy_formatted）。
    """
    from outlook_register import PatchrightController, register_one

    ctrl = PatchrightController(
        proxy=proxy,
        wait_ms=1500,
        max_captcha_retries=3,
    )
    result = register_one(
        ctrl=ctrl,
        engine_name="patchright",
        headless=headless,
        planned_username=planned_email,
        planned_password=planned_password,
        exit_ip=exit_ip,
        proxy_port=proxy_port,
        proxy_formatted=proxy,
    )
    return result


# ── IMAP 开启 ────────────────────────────────────────────────────────────────

def run_enable_imap(
    email: str,
    password: str,
    account_id: int,
    cookies_json: str = "",
    fingerprint_json: str = "",
    headless: bool = True,
    max_retries: int = 2,
) -> bool:
    """
    调用 enable_imap_v5.enable_imap()。
    内部会自动选 ISP/tp-in 代理（_find_isp_proxy），不依赖注册时的 CF VLESS 隧道。
    """
    from enable_imap_v5 import enable_imap

    for attempt in range(1, max_retries + 1):
        log(f"  [imap] 尝试 {attempt}/{max_retries}: {email}")
        try:
            ok = enable_imap(
                email=email,
                password=password,
                account_id=account_id,
                cookies_json=cookies_json,
                fingerprint_json=fingerprint_json,
                proxy="",           # enable_imap 内部自动选 ISP proxy
                headless=headless,
                xray_relay_inst=None,
            )
            if ok:
                return True
            log(f"  [imap] ⚠ 第 {attempt} 次失败，{'重试' if attempt < max_retries else '放弃'}")
        except Exception as e:
            log(f"  [imap] ⚠ 异常 attempt={attempt}: {e}")
            log(traceback.format_exc()[:400])
        if attempt < max_retries:
            time.sleep(5)
    return False


# ── 单账号完整流程 ────────────────────────────────────────────────────────────

def run_one(
    idx: int,
    total: int,
    planned_email: str = "",
    planned_password: str = "",
    manual_proxy: str = "",
    headless: bool = True,
    skip_imap: bool = False,
    cf_retry: int = 0,
) -> dict:
    log(f"\n{'='*64}")
    log(f"[{idx+1}/{total}] 开始完整工作流{'（重试CF IP）' if cf_retry else ''}")

    xray_inst  = None
    ip_info    = None
    job_id     = f"full_{idx}_{int(time.time())}"
    cur_proxy  = manual_proxy
    cur_exit   = ""
    cur_port   = 0

    # ── Step 1: 获取 CF IP ──────────────────────────────────────────────────
    if not manual_proxy:
        ip_info = _get_cf_ip(job_id)
        if not ip_info:
            log("  ❌ CF pool 无可用 IP，跳过此账号")
            return {"success": False, "error": "cf_pool_empty", "email": "", "password": ""}

        from xray_relay import XrayRelay
        xray_inst = XrayRelay(ip_info["ip"], force_dynamic=True)
        if not xray_inst.start(timeout=12.0):
            log(f"  ❌ XrayRelay 启动超时 IP={ip_info['ip']}，ban 并跳过")
            _ban_cf_ip(ip_info["ip"])
            _release_cf_ip(job_id)
            return {"success": False, "error": "xray_start_timeout", "email": "", "password": ""}

        cur_proxy = xray_inst.socks5_url
        cur_exit  = ip_info["ip"]
        cur_port  = xray_inst.socks_port
        log(f"  [proxy] CF IP={cur_exit} latency={ip_info.get('latency',0)}ms SOCKS5={cur_proxy}")

    # ── Step 2: 注册 + OAuth ────────────────────────────────────────────────
    log(f"  [register] 开始注册 + OAuth...")
    result = {}
    try:
        result = run_register_and_oauth(
            planned_email=planned_email,
            planned_password=planned_password,
            proxy=cur_proxy,
            exit_ip=cur_exit,
            proxy_port=cur_port,
            headless=headless,
        )
    except Exception as e:
        log(f"  ❌ register_one 异常: {e}")
        log(traceback.format_exc()[:600])
        result = {"success": False, "error": str(e)[:200], "email": "", "password": ""}

    # ── Step 3: 停止 CF tunnel ──────────────────────────────────────────────
    if xray_inst:
        try:
            xray_inst.stop()
        except Exception:
            pass
        xray_inst = None
    if ip_info:
        _release_cf_ip(job_id)

    if not result.get("success"):
        err = result.get("error", "?")
        log(f"  ❌ 注册失败: {err}")
        # 特定错误 ban CF IP 并建议调用方重试
        if ip_info and any(kw in err for kw in (
            "验证码", "CAPTCHA", "IP质量", "频率过快", "ERR_TUNNEL", "ERR_CONNECTION",
            "Timeout", "等待同意按钮", "Consent", "net::ERR",
        )):
            _ban_cf_ip(cur_exit)
            result["_should_retry"] = True
        return result

    email    = result.get("email", "")
    password = result.get("password", "")
    log(f"  ✅ 注册成功: {email} | access_token={'✓' if result.get('access_token') else '✗'}")

    # ── Step 4: 写 DB ────────────────────────────────────────────────────────
    acc_id = 0
    try:
        acc_id = save_account_to_db(result)
        result["account_id"] = acc_id
    except Exception as e:
        log(f"  ⚠ DB 写入失败: {e}")
        # 即使 DB 失败也继续 IMAP 步骤（IMAP 会尝试写 db tag）

    # ── Step 5: 开启 IMAP+POP ────────────────────────────────────────────────
    imap_ok = False
    if skip_imap:
        log(f"  [imap] skip_imap=True，跳过 IMAP 开启")
    elif not password:
        log(f"  [imap] 无密码，跳过 IMAP 开启")
    else:
        imap_ok = run_enable_imap(
            email=email,
            password=password,
            account_id=acc_id,
            cookies_json=result.get("cookies_json", ""),
            fingerprint_json=result.get("fingerprint_json", ""),
            headless=headless,
        )
        result["imap_enabled"] = imap_ok
        if imap_ok:
            log(f"  ✅ IMAP+POP 开启成功")
            if acc_id:
                try:
                    db_add_tags(acc_id, "imap_enabled", "pop_enabled")
                except Exception as te:
                    log(f"  ⚠ tags 更新失败: {te}")
        else:
            log(f"  ⚠ IMAP+POP 开启失败（账号已注册并授权，后续可单独跑 enable_imap_v5.py）")

    result["imap_enabled"] = imap_ok
    return result


# ── 批量入口 ─────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Outlook 完整注册+OAuth+IMAP 工作流")
    ap.add_argument("--count",      type=int,  default=1,     help="注册账号数量")
    ap.add_argument("--email",      default="",               help="指定邮箱前缀（仅第1个账号）")
    ap.add_argument("--password",   default="",               help="指定密码（仅第1个账号）")
    ap.add_argument("--headless",   default="true",           help="true/false")
    ap.add_argument("--proxy",      default="",               help="手动指定代理（不用CF pool）")
    ap.add_argument("--delay",      type=int,  default=8,     help="账号间等待秒数")
    ap.add_argument("--skip-imap",  action="store_true",      help="跳过 IMAP+POP 开启步骤")
    ap.add_argument("--output",     default="",               help="成功账号输出到文件")
    args = ap.parse_args()

    headless = args.headless.lower() not in ("false", "0", "no")

    results  = []
    ok_list  = []
    fail_list = []

    for i in range(args.count):
        pe = args.email    if i == 0 else ""
        pp = args.password if i == 0 else ""

        # CF IP 重试循环
        r = None
        for cf_attempt in range(MAX_CF_RETRIES):
            r = run_one(
                idx=i, total=args.count,
                planned_email=pe,
                planned_password=pp,
                manual_proxy=args.proxy,
                headless=headless,
                skip_imap=args.skip_imap,
                cf_retry=cf_attempt,
            )
            if r.get("success"):
                break
            if not r.get("_should_retry"):
                break
            log(f"  ↺ CF IP 重试 ({cf_attempt+1}/{MAX_CF_RETRIES})...")
            time.sleep(3)

        results.append(r)
        if r and r.get("success"):
            ok_list.append(r)
        else:
            fail_list.append(r)

        if i < args.count - 1:
            delay = args.delay + random.randint(0, 5)
            log(f"\n  ⏱ 下一账号等待 {delay}s...")
            time.sleep(delay)

    # ── 汇总 ────────────────────────────────────────────────────────────────
    log(f"\n{'='*64}")
    log(f"完成！成功注册: {len(ok_list)} / 总计: {len(results)}")
    imap_ok = [r for r in ok_list if r.get("imap_enabled")]
    log(f"IMAP+POP 开启成功: {len(imap_ok)} / {len(ok_list)}")

    for r in ok_list:
        imap_str = "✓ imap" if r.get("imap_enabled") else "✗ imap"
        token_str = "✓ token" if r.get("access_token") else "✗ token"
        log(f"  ✅ {r['email']} | pw: {r['password']} | {token_str} | {imap_str} | db_id={r.get('account_id',0)}")

    if fail_list:
        log(f"失败: {len(fail_list)}")
        for r in fail_list:
            log(f"  ❌ {r.get('email','?')}: {r.get('error','?')}")

    if args.output and ok_list:
        from pathlib import Path
        Path(args.output).write_text(
            "\n".join(f"{r['email']}----{r['password']}" for r in ok_list)
        )
        log(f"\n💾 已保存 {len(ok_list)} 条到 {args.output}")

    log("\n── JSON 结果 ──")
    # 过滤掉长字段，只输出关键信息
    summary = []
    for r in results:
        summary.append({
            "email":        r.get("email", ""),
            "password":     r.get("password", ""),
            "success":      r.get("success", False),
            "error":        r.get("error", ""),
            "account_id":   r.get("account_id", 0),
            "has_token":    bool(r.get("access_token")),
            "imap_enabled": r.get("imap_enabled", False),
            "exit_ip":      r.get("exit_ip", ""),
            "elapsed":      r.get("elapsed", ""),
        })
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
