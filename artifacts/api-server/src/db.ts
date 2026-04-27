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
}
