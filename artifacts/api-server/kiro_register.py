#!/usr/bin/env python3
"""
kiro_register.py — Kiro/AWS Builder ID 单账号注册脚本
接入现有 Toolkit 基础设施:
  - Outlook 账号池 (accounts 表, platform='outlook', refresh_token)
  - Graph API OTP 读取 (outlook_graph.py)
  - kiro_core.KiroRegister (纯协议, 无浏览器, 低内存)
  - 结果存入 accounts 表 (platform='kiro')

用法:
  python3 kiro_register.py --account-id 1866 --proxy socks5://127.0.0.1:10854
  python3 kiro_register.py --auto  # 从 DB 自动选一个未用 Outlook 账号
"""
import sys, os, json, time, argparse, secrets, string
sys.path.insert(0, "/root/Toolkit/artifacts/api-server")

import psycopg2

# ── cyCronet shim (Chrome 144 TLS, HTTP/2) — replaces curl_cffi.Sessions ────
# Falls back to curl_cffi if cyCronet is not installed.
import sys as _sys, types as _types
_CYCRONET_ACTIVE = False
try:
    import cycronet_shim as _cyshim
    import curl_cffi as _cf
    _fake_requests = _types.SimpleNamespace(
        Session=_cyshim.Session,
        **{k: getattr(_cf.requests, k)
           for k in dir(_cf.requests)
           if k not in ("Session",) and not k.startswith("__")}
    )
    _sys.modules["curl_cffi.requests"] = _fake_requests  # type: ignore
    _cf.requests = _fake_requests
    _CYCRONET_ACTIVE = True
    print("[kiro_register] HTTP backend: cyCronet (Chrome 144 TLS)", flush=True)
except Exception as _ce:
    print(f"[kiro_register] HTTP backend: curl_cffi (cycronet unavailable: {_ce})", flush=True)

DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/toolkit")

# ── DB helpers ────────────────────────────────────────────────────────────────
def _db():
    return psycopg2.connect(DB_URL)

_GRAPH_CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"

def validate_refresh_token(refresh_token: str) -> bool:
    """Quick HTTP check: True=valid, False=expired(400)."""
    import urllib.request, urllib.parse
    data = urllib.parse.urlencode({
        "client_id": _GRAPH_CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": "offline_access https://graph.microsoft.com/Mail.Read",
    }).encode()
    try:
        resp = urllib.request.urlopen(
            urllib.request.Request(
                "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
                data=data, headers={"Content-Type": "application/x-www-form-urlencoded"},
            ), timeout=15)
        return "access_token" in json.loads(resp.read())
    except Exception as e:
        if "400" in str(e): return False
        print(f"[validate_rt] network err: {e}", flush=True); return True


def pick_outlook_account():
    """从 DB 中取一个未使用 Kiro 注册的 Outlook 账号"""
    conn = _db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, email, refresh_token, proxy_port
        FROM accounts
        WHERE platform = 'outlook'
          AND status = 'active'
          AND refresh_token IS NOT NULL
          AND (kiro_used IS NULL OR kiro_used = false)
        ORDER BY RANDOM()
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    """)
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        return None, None
    acc_id, email, refresh_token, proxy_port = row
    # 立刻标记为占用中，防止并发重复选取
    cur.execute("""
        UPDATE accounts SET kiro_used=true, kiro_used_at=NOW()
        WHERE id=%s
    """, (acc_id,))
    conn.commit()
    cur.close(); conn.close()
    return {"id": acc_id, "email": email, "refresh_token": refresh_token,
            "proxy_port": proxy_port}, None

def get_outlook_account(account_id: int):
    conn = _db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, email, refresh_token, proxy_port
        FROM accounts WHERE id=%s AND platform='outlook'
    """, (account_id,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        return None
    return {"id": row[0], "email": row[1], "refresh_token": row[2],
            "proxy_port": row[3]}

def save_kiro_account(outlook_id: int, email: str, password: str,
                      access_token: str, refresh_token: str,
                      client_id: str, client_secret: str,
                      session_token: str, proxy: str, exit_ip: str):
    conn = _db()
    cur = conn.cursor()
    notes = json.dumps({
        "clientId": client_id,
        "clientSecret": client_secret,
        "sessionToken": session_token,
        "source_outlook_id": outlook_id,
    })
    cur.execute("""
        INSERT INTO accounts (platform, email, password, token, refresh_token,
                              status, notes, proxy_formatted, exit_ip,
                              created_at, updated_at)
        VALUES ('kiro', %s, %s, %s, %s, 'active', %s, %s, %s, NOW(), NOW())
        RETURNING id
    """, (email, password, access_token, refresh_token, notes, proxy, exit_ip))
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close(); conn.close()
    return new_id

def update_kiro_notes(account_id: int, extra: dict):
    """Merge extra keys into the notes JSON of a kiro account."""
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT notes FROM accounts WHERE id=%s", (account_id,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        return
    try:
        notes = json.loads(row[0] or "{}")
    except Exception:
        notes = {}
    notes.update(extra)
    cur.execute("UPDATE accounts SET notes=%s, updated_at=NOW() WHERE id=%s",
                (json.dumps(notes), account_id))
    conn.commit()
    cur.close(); conn.close()


def update_sub_status(account_id: int, status: str, retry_hours: int = 0):
    """更新 kiro 账号的订阅状态列。"""
    conn = _db()
    cur  = conn.cursor()
    if retry_hours > 0:
        cur.execute("""
            UPDATE accounts
            SET    sub_status = %s,
                   sub_retry_after = NOW() + (%s || ' hours')::INTERVAL,
                   updated_at = NOW()
            WHERE  id = %s
        """, (status, str(retry_hours), account_id))
    else:
        cur.execute("""
            UPDATE accounts
            SET    sub_status = %s,
                   sub_retry_after = NULL,
                   updated_at = NOW()
            WHERE  id = %s
        """, (status, account_id))
    conn.commit(); cur.close(); conn.close()


def mark_outlook_kiro_done(account_id: int, success: bool, permanent: bool = False):
    conn = _db()
    cur = conn.cursor()
    if success:
        cur.execute("UPDATE accounts SET kiro_used=true, kiro_used_at=NOW() WHERE id=%s",
                    (account_id,))
    elif permanent:
        # 永久失败 (如 refresh_token 过期): 标记为已用, 不再重试
        cur.execute("UPDATE accounts SET kiro_used=true, kiro_used_at=NOW(), status='inactive' WHERE id=%s",
                    (account_id,))
    else:
        # 暂时失败时释放锁，允许下次重试
        cur.execute("UPDATE accounts SET kiro_used=false WHERE id=%s", (account_id,))
    conn.commit(); cur.close(); conn.close()

# ── Graph API OTP ─────────────────────────────────────────────────────────────
def wait_for_aws_otp(refresh_token: str, timeout: int = 120, tag: str = "") -> str | None:
    """用 Graph API 轮询 Outlook 收件箱，等待 AWS 验证码邮件"""
    import urllib.request, urllib.parse

    CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
    TENANT = "consumers"

    prefix = f"[{tag}] " if tag else ""
    print(f"{prefix}正在获取 Graph access_token...", flush=True)

    # 刷新 access_token
    data = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": "offline_access https://graph.microsoft.com/Mail.Read",
    }).encode()
    try:
        resp = urllib.request.urlopen(
            urllib.request.Request(
                f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ), timeout=20
        )
        tok = json.loads(resp.read())
        access_token = tok["access_token"]
        new_refresh = tok.get("refresh_token", refresh_token)
    except Exception as e:
        print(f"{prefix}❌ refresh token 失败: {e}", flush=True)
        # 400 = refresh_token 过期，向上抛以触发永久失败标记
        if "400" in str(e) or "400" in str(type(e)):
            # FIX: sentinel instead of raise, caller handles permanent mark
            return "TOKEN_EXPIRED"
        return None

    def graph_get(path):
        import urllib.parse as up
        # URL-encode spaces in query string
        url = "https://graph.microsoft.com/v1.0" + path.replace(" ", "%20")
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        })
        return json.loads(urllib.request.urlopen(req, timeout=20).read())

    deadline = time.time() + timeout
    seen_ids = set()
    start_ts = time.time() - 60  # 看最近60秒内的邮件

    print(f"{prefix}等待 AWS 验证码邮件 (最多 {timeout}s)...", flush=True)
    while time.time() < deadline:
        for folder in ["inbox", "JunkEmail"]:
            try:
                msgs = graph_get(
                    f"/me/mailFolders/{folder}/messages"
                    "?$select=id,subject,from,receivedDateTime,body"
                    "&$orderby=receivedDateTime desc&$top=20"
                )
                for m in msgs.get("value", []):
                    mid = m.get("id")
                    if not mid or mid in seen_ids:
                        continue
                    # Only process emails received after our start_ts (avoids stale OTPs)
                    recv_str = m.get("receivedDateTime", "")
                    if recv_str:
                        try:
                            import datetime as _dt
                            recv_dt = _dt.datetime.fromisoformat(recv_str.replace("Z", "+00:00"))
                            if recv_dt.timestamp() < start_ts:
                                seen_ids.add(mid)  # mark stale, skip
                                continue
                        except Exception:
                            pass
                    subj = (m.get("subject") or "").lower()
                    from_addr = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "").lower()
                    if "amazon" not in subj and "aws" not in subj and "verification" not in subj \
                       and "amazon" not in from_addr and "aws" not in from_addr:
                        continue
                    seen_ids.add(mid)
                    body = (m.get("body") or {}).get("content", "")
                    import re
                    for pat in [r"验证码[:：]\s*(\d{6})", r"verification code[^0-9]*(\d{6})",
                                r">\s*(\d{6})\s*<", r"\b([0-9]{6})\b"]:
                        match = re.search(pat, body, re.IGNORECASE)
                        if match:
                            code = match.group(1)
                            print(f"{prefix}✅ 收到 AWS OTP: {code}", flush=True)
                            return code
            except Exception as e:
                print(f"{prefix}⚠️ Graph 轮询异常({folder}): {e}", flush=True)
        elapsed = int(time.time() - (deadline - timeout))
        print(f"{prefix}  等待中... ({elapsed}s/{timeout}s)", flush=True)
        time.sleep(5)

    print(f"{prefix}❌ OTP 超时", flush=True)
    return None

# ── Kiro 注册入口 ─────────────────────────────────────────────────────────────
def _gen_password() -> str:
    chars = string.ascii_letters + string.digits + "!@#$"
    return secrets.token_hex(4).upper() + secrets.choice("!@#$") + secrets.token_hex(4)

def run_kiro_register(email: str, refresh_token: str, proxy: str | None,
                      tag: str = "KIRO") -> dict:
    """运行 Kiro 注册，返回 {"ok": bool, "email", "password", "accessToken", ...}"""
    # 动态猴子补丁: 替换 kiro_core 中的 OTP 等待函数
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "kiro_core", "/root/Toolkit/artifacts/api-server/kiro_core.py"
    )
    kiro_core = importlib.util.module_from_spec(spec)
    # Apply cycronet shim to this fresh kiro_core module before exec
    if _CYCRONET_ACTIVE:
        kiro_core.curl_requests = _fake_requests
    spec.loader.exec_module(kiro_core)
    # Belt-and-suspenders: re-apply after exec in case the module reset it
    if _CYCRONET_ACTIVE:
        kiro_core.curl_requests = _fake_requests

    # 替换 OTP 函数 (core.py 通过模块级 wait_for_otp 调用)
    def _our_otp(account_id=None, timeout=120, tag=tag):
        result = wait_for_aws_otp(refresh_token, timeout=timeout, tag=tag)
        if result == "TOKEN_EXPIRED":
            raise RuntimeError(f"refresh_token_expired:graph_400")
        return result
    kiro_core.wait_for_otp = _our_otp

    password = _gen_password()
    name = f"Kiro User {secrets.token_hex(3)}"

    reg = kiro_core.KiroRegister(proxy=proxy, tag=tag)
    try:
        ok, info = reg.register(email, pwd=password, name=name, mail_token=None)
    except RuntimeError as rte:
        err_str = str(rte)
        if "refresh_token_expired" in err_str:
            return {"ok": False, "email": email, "error": err_str, "permanent": True}
        raise

    if ok and info:
        info.setdefault("password", password)
        info["ok"] = True
        return info
    return {"ok": False, "email": email, "error": str(info)}

# ── 主程序 ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--account-id", type=int, help="指定 Outlook 账号 ID")
    parser.add_argument("--auto", action="store_true", help="自动从 DB 选账号")
    parser.add_argument("--proxy", help="代理地址, 如 socks5://127.0.0.1:10854")
    parser.add_argument("--port", type=int, help="代理端口 (自动生成 socks5://)")
    args = parser.parse_args()

    if args.account_id:
        account = get_outlook_account(args.account_id)
        if not account:
            print(f"❌ 找不到账号 ID={args.account_id}")
            sys.exit(1)
    elif args.auto:
        account, _ = pick_outlook_account()
        if not account:
            print("❌ 没有可用的 Outlook 账号")
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)

    proxy = args.proxy
    if not proxy and args.port:
        proxy = f"socks5://127.0.0.1:{args.port}"
    if not proxy and account.get("proxy_port"):
        proxy = f"socks5://127.0.0.1:{account['proxy_port']}"

    # FIX: 注册前快速验证 refresh_token
    print(f"[MAIN] 验证 refresh_token...", flush=True)
    if not validate_refresh_token(account["refresh_token"]):
        print(f"[MAIN] ❌ refresh_token 已过期 (400), 永久标记账号 {account['id']}", flush=True)
        mark_outlook_kiro_done(account["id"], success=False, permanent=True)
        sys.exit(2)

    print(f"[MAIN] 开始注册: {account['email']} proxy={proxy}", flush=True)
    start_t = time.time()

    result = run_kiro_register(
        email=account["email"],
        refresh_token=account["refresh_token"],
        proxy=proxy,
        tag=f"K-{account['id']}",
    )

    elapsed = int(time.time() - start_t)
    if result.get("ok"):
        kiro_id = save_kiro_account(
            outlook_id=account["id"],
            email=result["email"],
            password=result.get("password", ""),
            access_token=result.get("accessToken", ""),
            refresh_token=result.get("refreshToken", ""),
            client_id=result.get("clientId", ""),
            client_secret=result.get("clientSecret", ""),
            session_token=result.get("sessionToken", ""),
            proxy=proxy or "",
            exit_ip="",
        )
        mark_outlook_kiro_done(account["id"], success=True)
        print(f"✅ 注册成功! Kiro ID={kiro_id} email={result['email']} ({elapsed}s)", flush=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))

        # ── 预热 + 立即订阅（趁 token 新鲜，不等 24h）────────────────
        try:
            import importlib.util as _ilu
            _wspec = _ilu.spec_from_file_location(
                "kiro_warmup",
                "/root/Toolkit/artifacts/api-server/kiro_warmup.py",
            )
            _kwup = _ilu.module_from_spec(_wspec)
            _wspec.loader.exec_module(_kwup)

            def _sub_log(msg, level="info"):
                print(f"[SUB] [{level.upper():5s}] {msg}", flush=True)

            access_token_val = result.get("accessToken", "")
            if access_token_val:
                # A. 预热：模拟 Kiro IDE 首次启动，让账号在 AWS 侧建立使用记录
                print("[MAIN] 账号预热中 (模拟 IDE 首次启动)...", flush=True)
                try:
                    _kwup.warmup(access_token_val, proxy=proxy, log=_sub_log)
                except Exception as _wup_exc:
                    print(f"[MAIN] 预热异常(继续): {_wup_exc}", flush=True)

                # B. 立即发起订阅（趁 token 新鲜有效，不等 24h）
                print("[MAIN] 立即发起 Pro 订阅（token 刚拿到，此时有效）...", flush=True)
                try:
                    import importlib.util as _ilu2, os as _os2
                    _sspec = _ilu2.spec_from_file_location(
                        "kiro_subscribe",
                        "/data/Toolkit/artifacts/api-server/kiro_subscribe.py",
                    )
                    _ksub = _ilu2.module_from_spec(_sspec)
                    _sspec.loader.exec_module(_ksub)
                    sub_result = _ksub.subscribe_pro(access_token_val, proxy=proxy, log=_sub_log)
                    if sub_result and sub_result.get("ok") and sub_result.get("payment_url"):
                        payment_url = sub_result["payment_url"]
                        print(f"[MAIN] ✅ 获得支付 URL，启动 chkr.cc BIN 自动支付...", flush=True)
                        try:
                            import importlib.util as _ilu3
                            _spspec = _ilu3.spec_from_file_location(
                                "stripe_pay",
                                "/data/Toolkit/artifacts/api-server/stripe_pay.py",
                            )
                            _spay = _ilu3.module_from_spec(_spspec)
                            _spspec.loader.exec_module(_spay)
                            bins_raw = _os2.environ.get("CHKR_BINS", "")
                            bins = [b.strip() for b in bins_raw.split(",") if b.strip()] or _spay.DEFAULT_BINS
                            import asyncio as _asyncio
                            pay_ok = _asyncio.run(_spay.auto_pay_chkr(
                                payment_url, bins=bins, headless=True, log=_sub_log
                            ))
                            if isinstance(pay_ok, dict) and pay_ok.get("ok"):
                                update_sub_status(kiro_id, "active")
                                print("[MAIN] 🎉 订阅完成！sub_status → active", flush=True)
                            else:
                                update_sub_status(kiro_id, "pay_failed")
                                print("[MAIN] ⚠️  Stripe 支付失败 → pay_failed", flush=True)
                        except Exception as _pay_exc:
                            print(f"[MAIN] ⚠️  stripe_pay 异常: {_pay_exc}", flush=True)
                            update_sub_status(kiro_id, "pay_failed")
                    elif sub_result and not sub_result.get("ok"):
                        err2 = sub_result.get("error", "?")
                        print(f"[MAIN] ⚠️  订阅失败: {err2}，入队 6h 后重试", flush=True)
                        update_sub_status(kiro_id, "pending", retry_hours=6)
                    else:
                        print("[MAIN] ⚠️  订阅返回 None（403/token 问题），入队 6h 后重试", flush=True)
                        update_sub_status(kiro_id, "pending", retry_hours=6)
                except Exception as _sub_exc2:
                    print(f"[MAIN] ⚠️  订阅流程异常: {_sub_exc2}", flush=True)
                    update_sub_status(kiro_id, "pending", retry_hours=6)
            else:
                print("[MAIN] 无 accessToken，跳过预热/入队", flush=True)
        except Exception as _sub_exc:
            print(f"[MAIN] 预热/入队异常: {_sub_exc}", flush=True)

        sys.exit(0)
    else:
        permanent = result.get("permanent", False)
        mark_outlook_kiro_done(account["id"], success=False, permanent=permanent)
        print(f"❌ 注册失败: {result.get('error')} (permanent={permanent}) ({elapsed}s)", flush=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
