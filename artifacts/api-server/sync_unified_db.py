#!/usr/bin/env python3
"""
统一数据库同步工具
接受 JSON stdin，写入 /data/Toolkit/artifacts/api-server/data.db
用法: echo '{"action":"email","email":"...","platform":"outlook","status":"active"}' | python3 sync_unified_db.py
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
    print(json.dumps({"ok":True,"action":"email","email":d['email']}))

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
    print(json.dumps({"ok":True,"action":"ai_account","service":d['service']}))

def upsert_profile(d):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO profiles (label,service,email,proxy,egress_ip,machine_id,sandbox_id,
              user_agent,fingerprint_json,cookies_json,local_storage_json,
              service_user_id,service_workspace_id,service_project_id,service_thread_id,
              exec_port,jupyter_port,status,notes,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            d.get('label'), d.get('service'), d.get('email'),
            d.get('proxy'), d.get('egress_ip'), d.get('machine_id'), d.get('sandbox_id'),
            d.get('user_agent'), d.get('fingerprint_json'), d.get('cookies_json'),
            d.get('local_storage_json'),
            d.get('service_user_id'), d.get('service_workspace_id'),
            d.get('service_project_id'), d.get('service_thread_id'),
            d.get('exec_port'), d.get('jupyter_port'),
            d.get('status','active'), d.get('notes'),
            d.get('created_at', now), now
        ))
        profile_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    print(json.dumps({"ok":True,"action":"profile","profile_id":profile_id}))

def upsert_outlook(d):
    """Outlook注册：同时写 emails + profiles"""
    upsert_email({
        'email': d['email'], 'password': d.get('password'),
        'platform': 'outlook', 'status': d.get('status','active'),
        'source': 'outlook_register'
    })
    if d.get('proxy') or d.get('egress_ip') or d.get('cookies_json') or d.get('fingerprint_json'):
        upsert_profile({
            'service': 'outlook', 'email': d['email'],
            'proxy': d.get('proxy'),
            'egress_ip': d.get('egress_ip'),
            'user_agent': d.get('user_agent'),
            'fingerprint_json': d.get('fingerprint_json'),
            'cookies_json': d.get('cookies_json'),
        })

def upsert_obvious(d):
    """Obvious注册：写 emails + profiles + ai_accounts"""
    email = d.get('email','')
    if email:
        upsert_email({
            'email': email, 'platform': 'mailtm',
            'status': 'used', 'source': 'obvious_register',
            'created_at': d.get('createdAt', now)
        })
    conn = get_conn()
    conn.execute("""
        INSERT INTO profiles (label,service,email,proxy,egress_ip,sandbox_id,
          service_user_id,service_workspace_id,service_project_id,service_thread_id,
          exec_port,jupyter_port,status,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        d.get('label'), 'obvious', email,
        d.get('proxy'), d.get('egressIp'), d.get('sandboxId'),
        d.get('userId'), d.get('workspaceId'), d.get('projectId'), d.get('threadId'),
        d.get('execPort'), d.get('jupyterPort'),
        'active', d.get('createdAt', now), now
    ))
    profile_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    upsert_ai_account({
        'service': 'obvious', 'email': email,
        'status': 'active', 'balance': d.get('creditBalance'),
        'tier': d.get('tier','free'), 'profile_id': profile_id,
        'created_at': d.get('createdAt', now)
    })
    print(json.dumps({"ok":True,"action":"obvious","email":email,"profile_id":profile_id}))

def upsert_airforce(d):
    """AirForce注册：写 ai_accounts，若有邮箱也写 emails"""
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

if __name__ == '__main__':
    try:
        payload = json.load(sys.stdin)
        action = payload.get('action','')
        if action == 'email':       upsert_email(payload)
        elif action == 'ai_account': upsert_ai_account(payload)
        elif action == 'profile':   upsert_profile(payload)
        elif action == 'outlook':   upsert_outlook(payload)
        elif action == 'obvious':   upsert_obvious(payload)
        elif action == 'airforce':  upsert_airforce(payload)
        else: print(json.dumps({"ok":False,"error":"unknown action: "+action}))
    except Exception as e:
        print(json.dumps({"ok":False,"error":str(e)}), file=sys.stderr)
        sys.exit(1)
