import { Pool } from "pg";

let pool: Pool | null = null;

function getPool(): Pool {
  const dbUrl = process.env["DATABASE_URL"];
  if (!dbUrl) throw new Error("[db] DATABASE_URL not configured, skipping DB operation");
  if (!pool) {
    try {
      const url = new URL(dbUrl);
      const isLocal = url.hostname === "localhost" || url.hostname === "127.0.0.1";
      const p = new Pool({
        host: url.hostname,
        port: url.port ? parseInt(url.port) : 5432,
        database: url.pathname.replace(/^\//, ""),
        user: decodeURIComponent(url.username),
        password: decodeURIComponent(url.password),
        ssl: isLocal ? false : { rejectUnauthorized: false },
        max: 10,
      });
      p.on("error", () => { pool = null; });
      pool = p;
    } catch {
      const p = new Pool({
        connectionString: dbUrl,
        ssl: dbUrl.includes("localhost") ? false : { rejectUnauthorized: false },
        max: 10,
      });
      p.on("error", () => { pool = null; });
      pool = p;
    }
  }
  return pool;
}

export async function query<T = Record<string, unknown>>(
  sql: string,
  params: unknown[] = []
): Promise<T[]> {
  const p = getPool();
  try {
    const result = await p.query(sql, params);
    return result.rows as T[];
  } catch (e: unknown) {
    const msg = (e instanceof Error ? e.message : String(e));
    if (msg.includes("SASL") || msg.includes("password must be") || msg.includes("auth")) {
      pool = null;
    }
    throw e;
  }
}

export async function queryOne<T = Record<string, unknown>>(
  sql: string,
  params: unknown[] = []
): Promise<T | null> {
  const rows = await query<T>(sql, params);
  return rows[0] ?? null;
}

export async function execute(sql: string, params: unknown[] = []): Promise<{ rowCount: number }> {
  const p = getPool();
  try {
    const result = await p.query(sql, params);
    return { rowCount: result.rowCount ?? 0 };
  } catch (e: unknown) {
    const msg = (e instanceof Error ? e.message : String(e));
    if (msg.includes("SASL") || msg.includes("password must be") || msg.includes("auth")) {
      pool = null;
    }
    throw e;
  }
}

export async function initDatabase(): Promise<void> {
  await execute(`CREATE TABLE IF NOT EXISTS accounts (
    id SERIAL PRIMARY KEY, platform VARCHAR(64) NOT NULL DEFAULT 'outlook',
    email VARCHAR(255), password VARCHAR(255), username VARCHAR(255),
    token TEXT, refresh_token TEXT, status VARCHAR(64) NOT NULL DEFAULT 'active',
    notes TEXT, tags TEXT, exit_ip VARCHAR(255), proxy_port INTEGER,
    name VARCHAR(255), type VARCHAR(64),
    credentials JSONB NOT NULL DEFAULT '{}'::jsonb,
    extra JSONB NOT NULL DEFAULT '{}'::jsonb,
    concurrency INTEGER NOT NULL DEFAULT 1, priority INTEGER NOT NULL DEFAULT 50,
    schedulable BOOLEAN NOT NULL DEFAULT true, auto_pause_on_expired BOOLEAN NOT NULL DEFAULT true,
    rate_multiplier NUMERIC NOT NULL DEFAULT 1.0,
    deleted_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())`);
  await execute(`ALTER TABLE accounts ADD COLUMN IF NOT EXISTS exit_ip VARCHAR(255)`);
  await execute(`ALTER TABLE accounts ADD COLUMN IF NOT EXISTS proxy_port INTEGER`);
  await execute(`CREATE UNIQUE INDEX IF NOT EXISTS accounts_platform_email_unique ON accounts(platform, email)`);
  await execute(`CREATE INDEX IF NOT EXISTS accounts_platform_status_idx ON accounts(platform, status)`);
  await execute(`CREATE TABLE IF NOT EXISTS temp_emails (
    id SERIAL PRIMARY KEY, address VARCHAR(255) NOT NULL UNIQUE,
    password VARCHAR(255), provider VARCHAR(64) NOT NULL DEFAULT 'mailtm',
    token TEXT, status VARCHAR(64) NOT NULL DEFAULT 'active',
    notes TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())`);
  await execute(`CREATE INDEX IF NOT EXISTS temp_emails_provider_status_idx ON temp_emails(provider, status)`);
  await execute(`CREATE TABLE IF NOT EXISTS proxies (
    id SERIAL PRIMARY KEY, formatted VARCHAR(500) NOT NULL UNIQUE,
    host VARCHAR(255) NOT NULL, port INTEGER NOT NULL,
    username VARCHAR(255), password VARCHAR(255),
    status VARCHAR(64) NOT NULL DEFAULT 'idle',
    used_count INTEGER NOT NULL DEFAULT 0, last_used TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())`);
  await execute(`CREATE INDEX IF NOT EXISTS proxies_status_used_idx ON proxies(status, used_count)`);
  await execute(`CREATE TABLE IF NOT EXISTS configs (
    key VARCHAR(255) PRIMARY KEY, value TEXT NOT NULL, description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())`);
  await execute(`CREATE TABLE IF NOT EXISTS identities (
    id SERIAL PRIMARY KEY, first_name VARCHAR(255), last_name VARCHAR(255),
    full_name VARCHAR(255), gender VARCHAR(64), birthday DATE,
    phone VARCHAR(255), email VARCHAR(255), address TEXT,
    city VARCHAR(255), state VARCHAR(255), zip VARCHAR(64),
    country VARCHAR(255) NOT NULL DEFAULT 'United States',
    username VARCHAR(255), password VARCHAR(255), notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())`);
  await execute(`CREATE INDEX IF NOT EXISTS identities_search_idx ON identities(full_name, email, username)`);
  await execute(`CREATE TABLE IF NOT EXISTS archives (
    id SERIAL PRIMARY KEY, platform VARCHAR(64) NOT NULL DEFAULT 'unknown',
    email VARCHAR(255) NOT NULL, password VARCHAR(255), username VARCHAR(255),
    token TEXT, refresh_token TEXT, machine_id VARCHAR(255),
    fingerprint JSONB, proxy_used VARCHAR(500), identity_data JSONB,
    cookies JSONB, registration_email VARCHAR(255),
    status VARCHAR(32) NOT NULL DEFAULT 'active', notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())`);
  // v8.32 ROOT-FIX: archives 必须 UNIQUE(platform,email) 否则 INSERT ... ON CONFLICT(platform,email) DO UPDATE 全部抛 "no unique or exclusion constraint matching the ON CONFLICT specification" → 档案库静默丢失
  await execute(`DROP INDEX IF EXISTS archives_platform_email_idx`);
  await execute(`DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'archives_platform_email_unique') THEN
      BEGIN
        ALTER TABLE archives ADD CONSTRAINT archives_platform_email_unique UNIQUE (platform, email);
      EXCEPTION WHEN unique_violation THEN
        RAISE NOTICE 'archives has duplicate (platform,email) — leaving without unique constraint, manual dedupe required';
      END;
    END IF;
  END $$;`);
  await execute(`CREATE TABLE IF NOT EXISTS replit_audit_history (
    id SERIAL PRIMARY KEY,
    source VARCHAR(32) NOT NULL DEFAULT 'manual',
    scope VARCHAR(32) NOT NULL DEFAULT 'active',
    dry_run BOOLEAN NOT NULL DEFAULT false,
    total INTEGER NOT NULL DEFAULT 0,
    scanned INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 0,
    stale INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    details JSONB NOT NULL DEFAULT '[]'::jsonb,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ NOT NULL DEFAULT NOW())`);
  await execute(`CREATE INDEX IF NOT EXISTS replit_audit_history_finished_idx ON replit_audit_history(finished_at DESC)`);

  // v8.80 Bug M ROOT-FIX: PG trigger 自动同步 accounts.token/refresh_token/status → archives.
  // 历史问题: ts/python 12+ 处 UPDATE accounts SET token=... 路径只有 1 处 (设备码 fallback) 同步 archives,
  // 其余 (in-browser OAuth, refresh, retoken, batch refresh, force_refresh, ...) 全部遗漏 → 档案库 token
  // 永远过期/为空 → 邮件中心从档案库派生的下游路径全部失败. 改在 DB 层一次性兜底, 永久解决.
  // v8.81 Bug R ROOT-FIX: 升级为 UPSERT — drift endpoint 实测 18 个账号 archives 行根本不存在
  // (注册路径 INSERT INTO accounts 时无对应 INSERT INTO archives, 老 trigger 只 UPDATE 不会建行).
  // 1) 给 archives 加 UNIQUE(platform,email) 支持 ON CONFLICT
  // 2) trigger 改 INSERT ... ON CONFLICT DO UPDATE — 自动建缺失行
  // 3) 加 AFTER INSERT trigger 同样路径 (新注册账号即时建档案)
  await execute(`CREATE UNIQUE INDEX IF NOT EXISTS archives_platform_email_uniq ON archives(platform, email)`);
  // v8.82 Bug S ROOT-FIX: 把 trigger 平台白名单从 'outlook' 扩为 ('outlook','replit').
  // 实测 DB: 106 个 replit 账号 archives 表里 0 行 — 整个下游档案库缺失 replit 历史.
  // 5 处 INSERT INTO accounts (platform='replit', ...) 路径 + 多处 UPDATE replit status 全部不触发.
  // archives schema 通用 (platform/email/password/username/token/refresh_token/status), 兼容 replit.
  // status 白名单也补 replit 专用值 (registered/unverified/exists_no_password/stale).
  await execute(`
    CREATE OR REPLACE FUNCTION sync_account_token_to_archives() RETURNS trigger AS $func$
    BEGIN
      IF NEW.platform IN ('outlook','replit') AND (
           TG_OP = 'INSERT'
        OR NEW.token         IS DISTINCT FROM OLD.token
        OR NEW.refresh_token IS DISTINCT FROM OLD.refresh_token
        OR NEW.status        IS DISTINCT FROM OLD.status
      ) THEN
        INSERT INTO archives (platform, email, password, username, token, refresh_token, status, updated_at)
        VALUES (NEW.platform, NEW.email, NEW.password, NEW.username, NEW.token, NEW.refresh_token,
                CASE WHEN NEW.status IN ('active','suspended','token_invalid','needs_oauth','needs_oauth_pending','done','error',
                                         'registered','unverified','exists_no_password','stale')
                     THEN NEW.status ELSE 'active' END,
                NOW())
        ON CONFLICT (platform, email) DO UPDATE
          SET token         = COALESCE(NULLIF(EXCLUDED.token, ''),         archives.token),
              refresh_token = COALESCE(NULLIF(EXCLUDED.refresh_token, ''), archives.refresh_token),
              status        = CASE WHEN EXCLUDED.status IN ('active','suspended','token_invalid','needs_oauth','needs_oauth_pending','done','error',
                                                            'registered','unverified','exists_no_password','stale')
                                   THEN EXCLUDED.status ELSE archives.status END,
              updated_at    = NOW();
      END IF;
      RETURN NEW;
    END;
    $func$ LANGUAGE plpgsql;
  `);
  await execute(`DROP TRIGGER IF EXISTS trg_sync_account_token ON accounts`);
  await execute(`DROP TRIGGER IF EXISTS trg_sync_account_token_ins ON accounts`);
  await execute(`
    CREATE TRIGGER trg_sync_account_token
    AFTER UPDATE OF token, refresh_token, status ON accounts
    FOR EACH ROW
    EXECUTE FUNCTION sync_account_token_to_archives()
  `);
  await execute(`
    CREATE TRIGGER trg_sync_account_token_ins
    AFTER INSERT ON accounts
    FOR EACH ROW
    EXECUTE FUNCTION sync_account_token_to_archives()
  `);
  // v8.81 升级 backfill 为 UPSERT — 修 archive_missing (历史上 INSERT INTO accounts 漏写 archives)
  // v8.82 扩到 replit (实测 106 个 replit 账号 archives 缺失). 同时回填存量字段差异.
  const _bf = await execute(`
    INSERT INTO archives (platform, email, password, username, token, refresh_token, status, updated_at)
    SELECT ac.platform, ac.email, ac.password, ac.username, ac.token, ac.refresh_token,
           CASE WHEN ac.status IN ('active','suspended','token_invalid','needs_oauth','needs_oauth_pending','done','error',
                                   'registered','unverified','exists_no_password','stale')
                THEN ac.status ELSE 'active' END,
           NOW()
      FROM accounts ac
     WHERE ac.platform IN ('outlook','replit')
    ON CONFLICT (platform, email) DO UPDATE
      SET token         = COALESCE(NULLIF(EXCLUDED.token, ''),         archives.token),
          refresh_token = COALESCE(NULLIF(EXCLUDED.refresh_token, ''), archives.refresh_token),
          status        = CASE WHEN EXCLUDED.status IN ('active','suspended','token_invalid','needs_oauth','needs_oauth_pending','done','error',
                                                        'registered','unverified','exists_no_password','stale')
                               THEN EXCLUDED.status ELSE archives.status END,
          updated_at    = NOW()
     WHERE archives.token         IS DISTINCT FROM EXCLUDED.token
        OR archives.refresh_token IS DISTINCT FROM EXCLUDED.refresh_token
        OR archives.status        IS DISTINCT FROM EXCLUDED.status
  `);
  if (_bf.rowCount > 0) {
    console.log(`[db.init] v8.82 archives upsert backfill: ${_bf.rowCount} rows synced/created (outlook+replit) from accounts → archives\n`);
  }

  // v8.81 Bug Q ROOT-FIX: 6 处 DELETE FROM accounts 路径全部漏写 DELETE FROM archives → 历史 orphan
  // 档案库残留无主数据. 加 AFTER DELETE trigger 自动级联, 永久解决 (用户意图 "彻底移除").
  // v8.82 cascade delete 也扩到 replit 平台.
  await execute(`
    CREATE OR REPLACE FUNCTION cascade_delete_archives() RETURNS trigger AS $func$
    BEGIN
      IF OLD.platform IN ('outlook','replit') THEN
        DELETE FROM archives WHERE platform = OLD.platform AND email = OLD.email;
      END IF;
      RETURN OLD;
    END;
    $func$ LANGUAGE plpgsql;
  `);
  await execute(`DROP TRIGGER IF EXISTS trg_cascade_delete_archives ON accounts`);
  await execute(`
    CREATE TRIGGER trg_cascade_delete_archives
    AFTER DELETE ON accounts
    FOR EACH ROW
    EXECUTE FUNCTION cascade_delete_archives()
  `);
  // 启动时一次性清理已 orphan 的存量数据 (历史已删除账号的档案残留, outlook+replit)
  const _orphan = await execute(`
    DELETE FROM archives ar
     WHERE ar.platform IN ('outlook','replit')
       AND NOT EXISTS (SELECT 1 FROM accounts a WHERE a.platform = ar.platform AND a.email = ar.email)
  `);
  if (_orphan.rowCount > 0) {
    console.log(`[db.init] v8.82 orphan cleanup: removed ${_orphan.rowCount} orphaned archives rows (outlook+replit)\n`);
  }
}
