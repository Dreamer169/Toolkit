#!/usr/bin/env python3
"""
send_alert.py — 通用告警发件器
从数据库依次尝试有效 Outlook 账号发送邮件，任一成功即止。
用法: python3 send_alert.py <subject> <body_html_or_text>
"""
import sys, os, json, urllib.request, urllib.parse, urllib.error

TO = "rjrbaphnak@rommiui.com"
CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
TENANT    = "consumers"
SCOPE     = "offline_access https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/Mail.Send https://graph.microsoft.com/User.Read"

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/toolkit")

def get_accounts():
    try:
        import psycopg2, psycopg2.extras
        conn = psycopg2.connect(DATABASE_URL)
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT id, email, refresh_token FROM accounts "
            "WHERE platform='outlook' AND refresh_token IS NOT NULL AND refresh_token != '' "
            "ORDER BY updated_at DESC LIMIT 10"
        )
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[send_alert] DB error: {e}", file=sys.stderr)
        return []

def refresh_token(refresh_tok):
    data = urllib.parse.urlencode({
        "client_id": CLIENT_ID, "grant_type": "refresh_token",
        "refresh_token": refresh_tok, "scope": SCOPE,
    }).encode()
    req = urllib.request.Request(
        f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
        data=data, headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
    return resp.get("access_token")

def send_via_graph(access_token, subject, body):
    payload = json.dumps({
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body},
            "toRecipients": [{"emailAddress": {"address": TO}}],
        },
        "saveToSentItems": True,
    }).encode()
    req = urllib.request.Request(
        "https://graph.microsoft.com/v1.0/me/sendMail",
        data=payload,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=20)
        return True
    except urllib.error.HTTPError as e:
        print(f"[send_alert] Graph error {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
        return False

def main():
    if len(sys.argv) < 3:
        print("usage: send_alert.py <subject> <body>", file=sys.stderr); sys.exit(1)
    subject, body = sys.argv[1], sys.argv[2]
    accounts = get_accounts()
    if not accounts:
        print("[send_alert] ❌ 无可用 Outlook 账号，无法发信", file=sys.stderr); sys.exit(1)
    for acc in accounts:
        try:
            print(f"[send_alert] 尝试 {acc['email']} ...", file=sys.stderr)
            tok = refresh_token(acc["refresh_token"])
            if not tok:
                continue
            if send_via_graph(tok, subject, body):
                print(f"[send_alert] ✅ 发送成功 from={acc['email']} to={TO}")
                sys.exit(0)
        except Exception as e:
            print(f"[send_alert] 账号 {acc['email']} 失败: {e}", file=sys.stderr)
    print("[send_alert] ❌ 所有账号均发送失败", file=sys.stderr)
    sys.exit(1)

if __name__ == "__main__":
    main()
