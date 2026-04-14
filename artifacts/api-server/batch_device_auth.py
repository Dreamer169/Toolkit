#!/usr/bin/env python3
"""
批量设备码授权脚本（Device Code Flow）
为数据库中 token 为空的 Outlook 账号逐一申请授权，
获得 access_token + refresh_token 后写入 DB。

用法:
    python3 batch_device_auth.py
    python3 batch_device_auth.py --email jack@outlook.com   # 只授权单个
    python3 batch_device_auth.py --refresh                  # 刷新即将过期的 token
"""
import json, sys, time, argparse
import urllib.request, urllib.parse
import psycopg2

DATABASE_URL = 'postgresql://postgres:postgres@localhost/toolkit'
CLIENT_ID    = '9e5f94bc-e8a4-4e73-b8be-63364c29d753'
TENANT       = 'consumers'
SCOPES       = [
    'offline_access',
    'https://graph.microsoft.com/Mail.Read',
    'https://graph.microsoft.com/Mail.ReadWrite',
    'https://graph.microsoft.com/User.Read',
]
SCOPE = ' '.join(SCOPES)


# ── DB 工具 ──────────────────────────────────────────────────────────────────

def db_connect():
    return psycopg2.connect(DATABASE_URL)


def fetch_accounts_without_token(conn, email_filter=None):
    """返回 token 为空（或 NULL）的 outlook 账号列表。"""
    cur = conn.cursor()
    if email_filter:
        cur.execute(
            "SELECT email, password FROM accounts "
            "WHERE platform='outlook' AND email=%s",
            (email_filter,)
        )
    else:
        cur.execute(
            "SELECT email, password FROM accounts "
            "WHERE platform='outlook' "
            "  AND (token IS NULL OR token='' OR token='null') "
            "ORDER BY created_at"
        )
    rows = cur.fetchall()
    cur.close()
    return [{'email': r[0], 'password': r[1]} for r in rows]


def fetch_accounts_expiring_soon(conn, within_seconds=3600):
    """返回 token 即将在 within_seconds 秒内过期的账号（需要 DB 存 expires_at）。"""
    cur = conn.cursor()
    cur.execute(
        "SELECT email FROM accounts "
        "WHERE platform='outlook' "
        "  AND token IS NOT NULL AND token != '' "
        "  AND expires_at IS NOT NULL "
        "  AND expires_at < NOW() + interval '%s seconds'",
        (within_seconds,)
    )
    rows = cur.fetchall()
    cur.close()
    return [r[0] for r in rows]


def save_token(conn, email, access_token, refresh_token):
    cur = conn.cursor()
    cur.execute(
        "UPDATE accounts "
        "SET token=%s, refresh_token=%s, updated_at=NOW() "
        "WHERE email=%s AND platform='outlook'",
        (access_token, refresh_token, email)
    )
    conn.commit()
    cur.close()


# ── OAuth Device Code Flow ───────────────────────────────────────────────────

def _post(url, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req  = urllib.request.Request(
        url, data=body,
        headers={'Content-Type': 'application/x-www-form-urlencoded'}
    )
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def device_code_flow(email: str) -> dict:
    """申请设备码 → 等待用户扫码 → 返回 {'access_token', 'refresh_token'}。"""
    # Step 1: 申请 device_code
    resp = _post(
        f'https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/devicecode',
        {'client_id': CLIENT_ID, 'scope': SCOPE}
    )
    device_code = resp['device_code']
    interval    = resp.get('interval', 5)
    expires_in  = resp.get('expires_in', 900)

    print('\n' + '='*60)
    print(f'  账号: {email}')
    print('='*60)
    print(f'  📱 打开浏览器访问: {resp["verification_uri"]}')
    print(f'  🔑 输入代码: {resp["user_code"]}')
    print(f'  （用 {email} 账号登录，授权 Mail 权限）')
    print('='*60)
    print('  ⏳ 等待授权（最多 15 分钟）...', flush=True)

    # Step 2: 轮询
    deadline = time.time() + expires_in
    while time.time() < deadline:
        time.sleep(interval)
        try:
            tok = _post(
                f'https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token',
                {
                    'client_id':  CLIENT_ID,
                    'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
                    'device_code': device_code,
                }
            )
        except Exception as e:
            print(f'  轮询请求异常: {e}', flush=True)
            continue

        if 'access_token' in tok:
            print(f'  ✅ 授权成功！', flush=True)
            return {
                'access_token':  tok['access_token'],
                'refresh_token': tok.get('refresh_token', ''),
                'expires_in':    tok.get('expires_in', 3600),
            }
        elif tok.get('error') == 'authorization_pending':
            print('.', end='', flush=True)
        elif tok.get('error') == 'slow_down':
            interval += 5
            print('s', end='', flush=True)
        else:
            print(f'\n  ❌ 错误: {tok.get("error")} — {tok.get("error_description", "")[:100]}')
            return {}

    print('\n  ❌ 超时，跳过该账号')
    return {}


def refresh_token_flow(email: str, refresh_token: str) -> dict:
    """用 refresh_token 换新 access_token（token 过期时自动刷新）。"""
    try:
        tok = _post(
            f'https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token',
            {
                'client_id':     CLIENT_ID,
                'grant_type':    'refresh_token',
                'refresh_token': refresh_token,
                'scope':         SCOPE,
            }
        )
        if 'access_token' in tok:
            return {
                'access_token':  tok['access_token'],
                'refresh_token': tok.get('refresh_token', refresh_token),
            }
        else:
            print(f'  ❌ refresh 失败: {tok.get("error")} — {tok.get("error_description","")[:80]}')
            return {}
    except Exception as e:
        print(f'  ❌ refresh 请求异常: {e}')
        return {}


# ── 主逻辑 ───────────────────────────────────────────────────────────────────

def run_batch(email_filter=None, refresh_mode=False):
    conn = db_connect()
    print(f'[batch_device_auth] 已连接数据库')

    if refresh_mode:
        # 刷新即将过期的 token（Fix: access_token 过期自动刷新）
        emails = fetch_accounts_expiring_soon(conn, within_seconds=3600)
        if not emails:
            print('[batch_device_auth] 没有即将过期的 token，退出。')
            conn.close()
            return
        print(f'[batch_device_auth] 找到 {len(emails)} 个即将过期的账号，开始刷新...')
        cur = conn.cursor()
        for em in emails:
            cur.execute(
                "SELECT refresh_token FROM accounts WHERE email=%s AND platform='outlook'",
                (em,)
            )
            row = cur.fetchone()
            if not row or not row[0]:
                print(f'  [{em}] 无 refresh_token，跳过')
                continue
            print(f'  [{em}] 刷新中...', flush=True)
            result = refresh_token_flow(em, row[0])
            if result.get('access_token'):
                save_token(conn, em, result['access_token'], result['refresh_token'])
                print(f'  [{em}] ✅ 刷新成功')
            else:
                print(f'  [{em}] ❌ 刷新失败，可能需要重新授权')
        cur.close()
    else:
        # 设备码授权（无 token 账号）
        accounts = fetch_accounts_without_token(conn, email_filter)
        if not accounts:
            print('[batch_device_auth] 没有需要授权的账号，退出。')
            conn.close()
            return

        print(f'[batch_device_auth] 找到 {len(accounts)} 个无 token 账号，逐一申请设备码授权...')
        ok_count = fail_count = 0

        for i, acc in enumerate(accounts, 1):
            email = acc['email']
            print(f'\n[{i}/{len(accounts)}] 处理: {email}')
            result = device_code_flow(email)
            if result.get('access_token'):
                save_token(conn, email, result['access_token'], result['refresh_token'])
                print(f'  ✅ DB 已更新 — {email}')
                ok_count += 1
            else:
                print(f'  ❌ 未获取到 token，跳过 — {email}')
                fail_count += 1

        print(f'\n[batch_device_auth] 完成：成功 {ok_count}，失败 {fail_count}')

    conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='批量 Outlook Device Code 授权')
    parser.add_argument('--email',   help='只处理指定邮箱')
    parser.add_argument('--refresh', action='store_true', help='刷新即将过期的 token')
    args = parser.parse_args()
    run_batch(email_filter=args.email, refresh_mode=args.refresh)
