#!/usr/bin/env python3
"""
test_outlook_mail.py — Outlook Graph API 收发件端到端测试

用法:
    python3 test_outlook_mail.py [--top N] [--send] [--to addr] [--accounts N]

    DATABASE_URL 优先从环境变量读取，若未设置则使用默认值
    postgresql://postgres:postgres@localhost/toolkit

选项:
    --top N        每个账号读取最近 N 封邮件（默认 3）
    --send         同时测试发件（向自身或 --to 地址发一封测试邮件）
    --to ADDR      指定发件目标地址（默认为账号自身）
    --accounts N   测试账号数量（默认 2）

退出码:
    0 — 所有测试通过
    1 — 至少一个测试失败
"""
import argparse
import os
import sys
import time

import psycopg2

# DATABASE_URL: 环境变量 → 默认值（无需手动 export，脚本自带 fallback）
DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost/toolkit",
)

# outlook_graph.py 与本脚本同目录
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from outlook_graph import (
    _refresh_access_token_raw,
    _graph_get_with_token,
    send_mail_graph,
)


def _get_accounts(n: int) -> list:
    """从 DB 获取最新 n 个有 refresh_token 的活跃 outlook 账号"""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, email, refresh_token
        FROM accounts
        WHERE platform = 'outlook'
          AND refresh_token IS NOT NULL
          AND refresh_token != ''
          AND status = 'active'
        ORDER BY updated_at DESC
        LIMIT %s
        """,
        (n,),
    )
    rows = cur.fetchall()
    conn.close()
    return [{"id": r[0], "email": r[1], "refresh_token": r[2]} for r in rows]


def test_read(acc: dict, top: int) -> bool:
    """收件测试：刷新 token → 读取收件箱前 N 封"""
    email = acc["email"]
    print(f"\n── 收件测试: {email} ──")
    try:
        resp = _refresh_access_token_raw(acc["refresh_token"])
        token = resp["access_token"]
        print("  token 刷新: OK")
    except Exception as e:
        print(f"  token 刷新: FAIL  {e}")
        return False

    try:
        msgs = _graph_get_with_token(
            f"/me/mailFolders/Inbox/messages?$top={top}"
            "&$orderby=receivedDateTime+desc"
            "&$select=subject,from,receivedDateTime",
            token,
        )
        items = msgs.get("value", [])
        print(f"  收件箱邮件: {len(items)} 封")
        for m in items:
            subj = m.get("subject", "(无主题)")
            sender = (
                m.get("from", {}).get("emailAddress", {}).get("address", "?")
            )
            recv = m.get("receivedDateTime", "")[:10]
            print(f"    [{recv}] {subj[:60]}  from={sender}")
        print("  收件读取: OK")
        return True
    except Exception as e:
        print(f"  收件读取: FAIL  {e}")
        return False


def test_send(acc: dict, to_addr: str = None) -> bool:
    """发件测试：向自身（或指定地址）发一封测试邮件"""
    email = acc["email"]
    target = to_addr or email
    print(f"\n── 发件测试: {email} → {target} ──")
    subj = f"[Graph API 发件测试] {time.strftime('%Y-%m-%d %H:%M:%S')}"
    body = (
        "<html><body>"
        "<p>这是由 <b>test_outlook_mail.py</b> 通过 Microsoft Graph API 发送的测试邮件。</p>"
        "<p>如果你能读到这封邮件，说明收发件链路均正常。</p>"
        "</body></html>"
    )
    result = send_mail_graph(acc["refresh_token"], target, subj, body)
    if result.get("success"):
        print(f"  发件: OK  主题={subj}")
        return True
    else:
        print(f"  发件: FAIL  {result.get('error', '未知错误')}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Outlook Graph API 收发件端到端测试")
    parser.add_argument("--top", type=int, default=3, help="每个账号读取最近 N 封邮件")
    parser.add_argument("--send", action="store_true", help="同时测试发件")
    parser.add_argument("--to", dest="to_addr", default=None, help="发件目标地址")
    parser.add_argument("--accounts", type=int, default=2, help="测试账号数量")
    args = parser.parse_args()

    print(f"DB: {DB_URL}")
    accounts = _get_accounts(args.accounts)
    if not accounts:
        print("FAIL: 数据库中没有可用的活跃 outlook 账号（需有 refresh_token）")
        sys.exit(1)

    print(f"测试账号: {len(accounts)} 个")
    passed = failed = 0

    for acc in accounts:
        ok = test_read(acc, args.top)
        if ok:
            passed += 1
        else:
            failed += 1

        if args.send:
            ok2 = test_send(acc, args.to_addr)
            if ok2:
                passed += 1
            else:
                failed += 1

    print(f"\n{'='*50}")
    print(f"结果: OK={passed}  FAIL={failed}")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
