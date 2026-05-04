#!/usr/bin/env python3
"""
统一数据库同步工具 v2
接受 JSON stdin，写入 /data/Toolkit/artifacts/api-server/data.db
支持 action: outlook | airforce | obvious | cursor | email | ai_account | profile
"""
import sys, json, sqlite3, datetime

DB = '/data/Toolkit/artifacts/api-server/data.db'
now = datetime.datetime.now().isoformat()

def get_conn():
    conn = sqlite3.connect(DB, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def upsert_email(d):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO emails (email,password,platform,status,tags,source,last_checked_at,notes,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(email) DO UPDATE SET
              password=COALESCE(NULLIF(excluded.password,''), password),
              status=CASE WHEN status IN ('suspended','banned') AND excluded.status='active'
                          THEN status ELSE excluded.status END,
              tags=COALESCE(NULLIF(excluded.tags,''), tags),
              last_checked_at=excluded.last_checked_at,
              updated_at=excluded.updated_at
        """, (
            d['email'], d.get('password'), d.get('platform','unknown'),
            d.get('status','active'), d.get('tags'), d.get('source','auto'),
            now, d.get('notes'), d.get('created_at', now), now
        ))
    return {"ok": True, "action": "email", "email": d['email']}

def upsert_ai_account(d):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO ai_accounts (service,username,password,email,api_key,status,validated,balance,tier,profile_id,notes,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT DO NOTHING
        """, (
            d['service'], d.get('username'), d.get('password'), d.get('email'),
            d.get('api_key'), d.get('status','active'), d.get('validated',0),
            d.get('balance'), d.get('tier'), d.get('profile_id'),
            d.get('notes'), d.get('created_at', now), now
        ))
    return {"ok": True, "action": "ai_account", "service": d['service']}

def upsert_profile(d):
    """完整档案：账密 + token + 代理 + 指纹 + cookies + 沙箱信息"""
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO profiles (
              label, service, platform, email, password, token, refresh_token,
              proxy, egress_ip, machine_id, sandbox_id,
              user_agent, fingerprint_json, cookies_json, local_storage_json,
              service_user_id, service_workspace_id, service_project_id, service_thread_id,
              exec_port, jupyter_port, reg_email, status, notes, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT DO NOTHING
        """, (
            d.get('label'), d.get('service'), d.get('platform'),
            d.get('email'), d.get('password'), d.get('token'), d.get('refresh_token'),
            d.get('proxy'), d.get('egress_ip'), d.get('machine_id'), d.get('sandbox_id'),
            d.get('user_agent'), d.get('fingerprint_json'), d.get('cookies_json'), d.get('local_storage_json'),
            d.get('service_user_id'), d.get('service_workspace_id'),
            d.get('service_project_id'), d.get('service_thread_id'),
            d.get('exec_port'), d.get('jupyter_port'), d.get('reg_email'),
            d.get('status','active'), d.get('notes'), d.get('created_at', now), now
        ))
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"ok": True, "action": "profile", "profile_id": pid}

def upsert_outlook(d):
    """Outlook 注册：写 emails + profiles（含账密/token/指纹/cookies/代理）"""
    r_email = upsert_email({
        'email': d['email'], 'password': d.get('password'),
        'platform': 'outlook', 'status': d.get('status','active'),
        'source': 'outlook_register'
    })
    r_profile = upsert_profile({
        'service': 'outlook', 'platform': 'outlook',
        'email': d['email'],
        'password': d.get('password'),
        'token': d.get('token'),
        'refresh_token': d.get('refresh_token'),
        'proxy': d.get('proxy'),
        'egress_ip': d.get('egress_ip'),
        'user_agent': d.get('user_agent'),
        'fingerprint_json': d.get('fingerprint_json'),
        'cookies_json': d.get('cookies_json'),
        'created_at': d.get('created_at', now)
    })
    return {"ok": True, "action": "outlook", "email": d['email'], "profile_id": r_profile.get('profile_id')}

def upsert_obvious(d):
    """Obvious 注册：写 emails(注册邮箱) + profiles(全档案) + ai_accounts"""
    email = d.get('email','')
    # 注册邮箱入邮箱库（mailtm 临时邮箱，标记 used）
    if email:
        upsert_email({
            'email': email, 'platform': 'mailtm',
            'status': 'used', 'source': 'obvious_register',
            'created_at': d.get('createdAt', d.get('created_at', now))
        })
    # 档案库：全量注册信息
    r_profile = upsert_profile({
        'label':   d.get('label'),
        'service': 'obvious', 'platform': 'replit',
        'email':   email,
        'password': d.get('password'),
        'token':   d.get('token'),
        'refresh_token': d.get('refresh_token'),
        'proxy':   d.get('proxy'),
        'egress_ip': d.get('egressIp', d.get('egress_ip')),
        'sandbox_id': d.get('sandboxId', d.get('sandbox_id')),
        'service_user_id':       d.get('userId', d.get('service_user_id')),
        'service_workspace_id':  d.get('workspaceId', d.get('service_workspace_id')),
        'service_project_id':    d.get('projectId', d.get('service_project_id')),
        'service_thread_id':     d.get('threadId', d.get('service_thread_id')),
        'exec_port':    d.get('execPort', d.get('exec_port')),
        'jupyter_port': d.get('jupyterPort', d.get('jupyter_port')),
        'created_at': d.get('createdAt', d.get('created_at', now)),
        'notes': d.get('name','') + (' @ ' + d.get('company','') if d.get('company') else '')
    })
    profile_id = r_profile.get('profile_id')
    # AI服务池：obvious Replit 账号
    upsert_ai_account({
        'service': 'obvious', 'platform': 'replit',
        'email': email, 'password': d.get('password'),
        'status': 'active', 'balance': d.get('creditBalance', d.get('credit_balance')),
        'tier': d.get('tier','free'), 'profile_id': profile_id,
        'created_at': d.get('createdAt', d.get('created_at', now))
    })
    return {"ok": True, "action": "obvious", "email": email, "profile_id": profile_id}

def upsert_cursor(d):
    """Cursor 注册：写 ai_accounts + profiles（若有注册 identity）"""
    r_profile = None
    if d.get('proxy') or d.get('egress_ip') or d.get('fingerprint_json'):
        r_profile = upsert_profile({
            'service': 'cursor', 'platform': 'cursor',
            'email': d.get('email'), 'password': d.get('password'),
            'token': d.get('token'), 'refresh_token': d.get('refresh_token'),
            'proxy': d.get('proxy'), 'egress_ip': d.get('egress_ip'),
            'user_agent': d.get('user_agent'),
            'fingerprint_json': d.get('fingerprint_json'),
            'cookies_json': d.get('cookies_json'),
        })
    pid = r_profile.get('profile_id') if r_profile else None
    upsert_ai_account({
        'service': 'cursor', 'email': d.get('email'), 'password': d.get('password'),
        'api_key': d.get('api_key'), 'status': d.get('status','active'),
        'profile_id': pid
    })
    return {"ok": True, "action": "cursor", "email": d.get('email')}

def upsert_airforce(d):
    """AirForce 注册：写 ai_accounts（+ 若有邮箱也写 emails）"""
    if d.get('email'):
        upsert_email({
            'email': d['email'], 'platform': 'proton',
            'status': 'used', 'source': 'airforce_register',
            'created_at': d.get('created_at', now)
        })
    upsert_ai_account({
        'service': 'airforce',
        'username': d.get('username'), 'password': d.get('password'),
        'email': d.get('email'),
        'api_key': d.get('api_key') or None,
        'status': 'active' if d.get('api_key') else 'unvalidated',
        'validated': 1 if d.get('validated') else 0,
        'created_at': d.get('created_at', now)
    })
    return {"ok": True, "action": "airforce", "username": d.get('username')}

DISPATCH = {
    'email':      upsert_email,
    'ai_account': upsert_ai_account,
    'profile':    upsert_profile,
    'outlook':    upsert_outlook,
    'obvious':    upsert_obvious,
    'cursor':     upsert_cursor,
    'airforce':   upsert_airforce,
}

if __name__ == '__main__':
    try:
        payload = json.load(sys.stdin)
        action  = payload.get('action','')
        fn = DISPATCH.get(action)
        if fn:
            result = fn(payload)
            print(json.dumps(result))
        else:
            print(json.dumps({"ok": False, "error": "unknown action: " + action}))
    except Exception as e:
        import traceback
        print(json.dumps({"ok": False, "error": str(e), "trace": traceback.format_exc()}), file=sys.stderr)
        sys.exit(1)
