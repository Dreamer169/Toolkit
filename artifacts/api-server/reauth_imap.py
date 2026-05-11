#!/usr/bin/env python3
"""
IMAP 重授权脚本 — 用新 Azure AD App client_id 重新获取含 IMAP scope 的 token。

使用设备码流程（Device Code Flow），无需浏览器自动化，用户在手机/任意浏览器完成授权。

用法:
  OUTLOOK_CLIENT_ID=<你的app_client_id> python3 reauth_imap.py [--limit N] [--email xxx@outlook.com]

选项:
  --limit N       只处理前 N 个账号（默认全部）
  --email ADDR    只处理指定邮箱
  --dry-run       只打印账号列表，不执行授权

需要先:
  1. Azure Portal 注册 App（见 imap_idle_daemon.py 顶部注释）
  2. 将 App client_id 设置到 OUTLOOK_CLIENT_ID 环境变量
"""
import argparse, json, os, sys, time, urllib.request, urllib.parse

CLIENT_ID = os.environ.get("OUTLOOK_CLIENT_ID", "")
DB_URL    = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/toolkit")

DEVICE_CODE_ENDPOINT = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
TOKEN_ENDPOINT       = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"

SCOPE = " ".join([
    "offline_access",
    "https://graph.microsoft.com/Mail.Read",
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.Send",
    "https://graph.microsoft.com/User.Read",
    "https://graph.microsoft.com/IMAP.AccessAsUser.All",
    "https://graph.microsoft.com/SMTP.Send",
    "https://outlook.office.com/IMAP.AccessAsUser.All",
    "https://outlook.office.com/SMTP.Send",
])


def _post(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return json.loads(resp.read())
    except urllib.request.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            raise RuntimeError(f"HTTP {e.code}")


def _get_accounts(limit: int | None, email_filter: str | None) -> list[dict]:
    import psycopg2
    conn = psycopg2.connect(DB_URL)
    try:
        cur = conn.cursor()
        if email_filter:
            cur.execute(
                "SELECT id, email FROM accounts WHERE platform='outlook' AND email=%s",
                (email_filter,)
            )
        else:
            sql = """
                SELECT id, email FROM accounts
                WHERE platform='outlook' AND status NOT IN ('suspended')
                ORDER BY updated_at DESC
            """
            if limit:
                sql += f" LIMIT {int(limit)}"
            cur.execute(sql)
        return [{"id": r[0], "email": r[1]} for r in cur.fetchall()]
    finally:
        conn.close()


def _save_tokens(acct_id: int, email: str, access_token: str, refresh_token: str):
    import psycopg2
    conn = psycopg2.connect(DB_URL)
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE accounts SET token=%s, refresh_token=%s, status='active', updated_at=NOW() WHERE id=%s",
            (access_token, refresh_token, acct_id)
        )
        conn.commit()
        print(f"  ✅ token 已写入 DB (id={acct_id})", flush=True)
    finally:
        conn.close()


def reauth_account(acct: dict) -> bool:
    email = acct["email"]
    print(f"\n{'─'*60}", flush=True)
    print(f"账号: {email}", flush=True)

    # 1. 申请设备码
    dc_resp = _post(DEVICE_CODE_ENDPOINT, {
        "client_id": CLIENT_ID,
        "scope":     SCOPE,
    })
    if "error" in dc_resp:
        print(f"  ❌ 设备码申请失败: {dc_resp['error']}: {dc_resp.get('error_description','')[:100]}", flush=True)
        return False

    user_code  = dc_resp["user_code"]
    device_code = dc_resp["device_code"]
    verify_uri  = dc_resp.get("verification_uri", "https://microsoft.com/devicelogin")
    interval    = dc_resp.get("interval", 5)
    expires_in  = dc_resp.get("expires_in", 900)

    print(f"  👉 打开浏览器: {verify_uri}", flush=True)
    print(f"  👉 输入代码:   {user_code}", flush=True)
    print(f"  ⏳ 等待授权（{expires_in}秒内完成）...", flush=True)

    # 2. 轮询 token endpoint
    deadline = time.time() + expires_in
    while time.time() < deadline:
        time.sleep(interval)
        tok_resp = _post(TOKEN_ENDPOINT, {
            "grant_type":  "urn:ietf:params:oauth:grant-type:device_code",
            "client_id":   CLIENT_ID,
            "device_code": device_code,
        })
        if tok_resp.get("access_token"):
            at = tok_resp["access_token"]
            rt = tok_resp.get("refresh_token", "")
            print(f"  ✅ 授权成功！access_token={len(at)}B, refresh_token={len(rt)}B", flush=True)
            _save_tokens(acct["id"], email, at, rt)
            return True

        err = tok_resp.get("error", "")
        if err == "authorization_pending":
            print("  ⏳ 等待用户授权...", end="\r", flush=True)
        elif err == "slow_down":
            interval += 5
        elif err in ("authorization_declined", "expired_token", "bad_verification_code"):
            print(f"  ❌ 授权终止: {err}", flush=True)
            return False
        else:
            print(f"  ⚠ {err}: {tok_resp.get('error_description','')[:80]}", flush=True)

    print("  ❌ 超时", flush=True)
    return False


def main():
    if not CLIENT_ID:
        print("❌ 未设置 OUTLOOK_CLIENT_ID 环境变量！", flush=True)
        print("   先在 Azure Portal 注册 App，然后:", flush=True)
        print("   export OUTLOOK_CLIENT_ID=<your_client_id>", flush=True)
        print("   python3 reauth_imap.py", flush=True)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="IMAP 重授权")
    parser.add_argument("--limit",   type=int, default=None,  help="最多处理N个账号")
    parser.add_argument("--email",   default=None,             help="只处理指定邮箱")
    parser.add_argument("--dry-run", action="store_true",      help="只列出账号，不执行")
    args = parser.parse_args()

    print(f"IMAP 重授权脚本", flush=True)
    print(f"CLIENT_ID: {CLIENT_ID}", flush=True)
    print(f"SCOPE: {SCOPE}", flush=True)

    accounts = _get_accounts(args.limit, args.email)
    print(f"\n找到 {len(accounts)} 个账号待处理", flush=True)

    if args.dry_run:
        for a in accounts:
            print(f"  {a['id']:5d}  {a['email']}", flush=True)
        return

    ok, fail = 0, 0
    for i, acct in enumerate(accounts, 1):
        print(f"\n[{i}/{len(accounts)}]", end="", flush=True)
        if reauth_account(acct):
            ok += 1
        else:
            fail += 1
        if i < len(accounts):
            ans = input(f"\n继续下一个账号？[Y/n]: ").strip().lower()
            if ans == "n":
                break

    print(f"\n{'='*60}", flush=True)
    print(f"完成: 成功 {ok} / 失败 {fail}", flush=True)


if __name__ == "__main__":
    main()
