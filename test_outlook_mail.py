#!/usr/bin/env python3
"""
test_outlook_mail.py — 实际测试 Outlook 收邮件
从数据库取第一个有 token 的 Outlook 账号，通过 Graph API 读取收件箱最新邮件

用法:
  python3 test_outlook_mail.py              # 自动选第一个有 token 的账号
  python3 test_outlook_mail.py --id 3       # 指定账号 ID
  python3 test_outlook_mail.py --top 10     # 读最新10封
  python3 test_outlook_mail.py --folder sentitems
"""

import argparse, json, os, sys, time, urllib.request, urllib.parse, urllib.error

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/toolkit")

CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
TENANT    = "consumers"
SCOPE     = "offline_access https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/User.Read"


def db_get_account(acc_id):
    import psycopg2, psycopg2.extras
    conn = psycopg2.connect(DATABASE_URL)
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if acc_id:
        cur.execute(
            "SELECT id, email, token, refresh_token FROM accounts WHERE id=%s AND platform='outlook'",
            (acc_id,),
        )
    else:
        cur.execute(
            "SELECT id, email, token, refresh_token FROM accounts "
            "WHERE platform='outlook' AND token IS NOT NULL AND token != '' "
            "ORDER BY updated_at DESC LIMIT 1"
        )
    row = cur.fetchone()
    conn.close()
    if not row:
        sys.exit("❌ 找不到有 token 的 Outlook 账号，请先完成授权")
    return dict(row)


def do_refresh(refresh_tok):
    data = urllib.parse.urlencode({
        "client_id":     CLIENT_ID,
        "grant_type":    "refresh_token",
        "refresh_token": refresh_tok,
        "scope":         SCOPE,
    }).encode()
    req = urllib.request.Request(
        f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
    if "access_token" not in resp:
        sys.exit(f"❌ 刷新 token 失败: {resp.get('error_description', resp)}")
    return resp


def graph_get(path, token):
    req = urllib.request.Request(
        f"https://graph.microsoft.com/v1.0{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=15).read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        sys.exit(f"❌ Graph API 失败 {e.code}: {body[:400]}")


def db_update_token(acc_id, access_token, refresh_token):
    try:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        cur  = conn.cursor()
        cur.execute(
            "UPDATE accounts SET token=%s, refresh_token=%s, updated_at=NOW() WHERE id=%s",
            (access_token, refresh_token, acc_id),
        )
        conn.commit()
        conn.close()
        print("  ✅ DB token 已更新")
    except Exception as e:
        print(f"  ⚠️  DB 更新失败（不影响测试）: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id",     type=int, default=None)
    parser.add_argument("--top",    type=int, default=5)
    parser.add_argument("--folder", default="inbox")
    args = parser.parse_args()

    print("=" * 60)
    print("Outlook Graph API 邮件读取测试")
    print("=" * 60)

    acc = db_get_account(args.id)
    print(f"\n📧 账号: {acc['email']}  (id={acc['id']})")

    access_token = acc.get("token", "") or ""
    if acc.get("refresh_token"):
        print("🔄 刷新 access_token ...")
        try:
            tok = do_refresh(acc["refresh_token"])
            access_token = tok["access_token"]
            db_update_token(acc["id"], access_token, tok.get("refresh_token", acc["refresh_token"]))
            print("  ✅ Token 刷新成功")
        except SystemExit as e:
            print(f"  ⚠️  {e} — 尝试用现有 DB token")

    if not access_token:
        sys.exit("❌ 无可用 access_token，请先完成 retoken")

    # 用户信息
    print("\n👤 获取用户信息 ...")
    me = graph_get("/me?$select=displayName,mail,userPrincipalName", access_token)
    print(f"  名称: {me.get('displayName', 'N/A')}")
    print(f"  邮箱: {me.get('mail') or me.get('userPrincipalName', 'N/A')}")

    # 读邮件
    top    = min(args.top, 50)
    folder = args.folder
    print(f"\n📬 读取 {folder} 最新 {top} 封邮件 ...")
    url = (
        f"/me/mailFolders/{folder}/messages"
        f"?$top={top}"
        f"&$select=id,subject,from,receivedDateTime,bodyPreview,isRead"
        f"&$orderby=receivedDateTime%20desc"
    )
    data     = graph_get(url, access_token)
    messages = data.get("value", [])

    if not messages:
        print("  ⚠️  收件箱为空")
        return

    print(f"  共 {len(messages)} 封:\n")
    for i, msg in enumerate(messages, 1):
        sender  = msg.get("from", {}).get("emailAddress", {})
        read    = "已读" if msg.get("isRead") else "未读"
        subject = msg.get("subject") or "(无主题)"
        preview = (msg.get("bodyPreview") or "").replace("\n", " ")[:80]
        recv    = msg.get("receivedDateTime", "")[:19].replace("T", " ")
        print(f"  [{i}] {read} | {recv}")
        print(f"       From: {sender.get('address', '?')}  ({sender.get('name', '')})")
        print(f"       主题: {subject}")
        print(f"       预览: {preview}")
        print()

    print("=" * 60)
    print(f"✅ 测试完成！成功读取 {len(messages)} 封邮件，Graph API 正常。")
    print("=" * 60)


if __name__ == "__main__":
    main()
