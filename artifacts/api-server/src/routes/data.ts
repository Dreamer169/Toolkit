import { Router } from "express";
import { query, queryOne, execute } from "../db.js";
import { Socket } from "net";
import { execFile } from "child_process";

const DB_PATH = "/data/Toolkit/artifacts/api-server/data.db";

/** Run a SQLite SQL query via python3 sqlite3. Returns array of row objects. */
function sqliteQuery(sql: string, params: (string | number)[] = []): Promise<Record<string, unknown>[]> {
  return new Promise((resolve) => {
    const script = `\nimport sqlite3, json, sys\ndb = sqlite3.connect(sys.argv[1])\ndb.row_factory = sqlite3.Row\ncur = db.execute(sys.argv[2], json.loads(sys.argv[3]))\nrows = [dict(r) for r in cur.fetchall()]\ndb.close()\nprint(json.dumps(rows))\n`;
    execFile("python3", ["-c", script, DB_PATH, sql, JSON.stringify(params)], { timeout: 8000 }, (err, stdout) => {
      if (err) { resolve([]); return; }
      try { resolve(JSON.parse(stdout.trim())); } catch { resolve([]); }
    });
  });
}

const router = Router();

// ─── 档案库初始化（首次请求时自动建表）────────────────────────────────────────
let archivesTableReady = false;
async function ensureArchivesTable() {
  if (archivesTableReady) return;
  await execute(`
    CREATE TABLE IF NOT EXISTS archives (
      id               SERIAL PRIMARY KEY,
      platform         VARCHAR(64)  NOT NULL DEFAULT 'unknown',
      email            VARCHAR(255) NOT NULL,
      password         VARCHAR(255),
      username         VARCHAR(255),
      token            TEXT,
      refresh_token    TEXT,
      machine_id       VARCHAR(255),
      fingerprint      JSONB,
      proxy_used       VARCHAR(500),
      identity_data    JSONB,
      cookies          JSONB,
      registration_email VARCHAR(255),
      status           VARCHAR(32)  NOT NULL DEFAULT 'active',
      notes            TEXT,
      created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
      updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    )
  `);
  await execute(`CREATE INDEX IF NOT EXISTS archives_platform_email_idx ON archives(platform, email)`);
  archivesTableReady = true;
}

// v8.81 ── 漂移检测 endpoint: accounts vs archives 字段不一致即立刻告警 ───────
// 即便有 trigger 兜底, 这里也作为只读审计入口供前端/CI 定期 ping. 前端可显示红色 banner.
router.get("/data/archives/drift", async (_req, res) => {
  try {
    await ensureArchivesTable();
    type DriftRow = {
      email: string; acc_status: string | null; arc_status: string | null;
      acc_token_len: number; arc_token_len: number;
      acc_refresh_len: number; arc_refresh_len: number;
      drift_kind: string;
    };
    // v8.81 区分真 drift vs informational:
    // - archive_missing: accounts 有但 archives 没有 → 严重, 后端有 trigger 已自动建 (剩余报告即历史 race)
    // - status_mismatch: status 不一致 → 严重 (trigger 应 100% 同步)
    // - token_mismatch: accounts 与 archives token 都非空且不一致 → 真同步问题
    // - archive_legacy_token (info): accounts.token 已清空 (失效/needs_oauth/error) 但 archives 保留旧 token →
    //   设计意图 (COALESCE 保护历史 token 在 archives 不被空值覆盖, 供未来参考). 不算 unhealthy.
    // v8.82 drift endpoint 扩到 outlook+replit, 反映 trigger 平台白名单同步范围
    const rows = await query<DriftRow & { platform: string }>(`
      SELECT a.platform, a.email,
             a.status AS acc_status, ar.status AS arc_status,
             COALESCE(LENGTH(a.token),0)         AS acc_token_len,
             COALESCE(LENGTH(ar.token),0)        AS arc_token_len,
             COALESCE(LENGTH(a.refresh_token),0) AS acc_refresh_len,
             COALESCE(LENGTH(ar.refresh_token),0) AS arc_refresh_len,
             CASE
               WHEN ar.email IS NULL THEN 'archive_missing'
               WHEN a.status IS DISTINCT FROM ar.status THEN 'status_mismatch'
               WHEN COALESCE(a.token,'') = '' AND COALESCE(ar.token,'') <> ''
                    AND a.status IN ('needs_oauth','needs_oauth_pending','token_invalid','error','suspended','stale','exists_no_password')
                 THEN 'archive_legacy_token'
               WHEN COALESCE(a.token,'') <> '' AND COALESCE(ar.token,'') <> ''
                    AND a.token IS DISTINCT FROM ar.token
                 THEN 'token_mismatch'
               WHEN COALESCE(a.refresh_token,'') <> '' AND COALESCE(ar.refresh_token,'') <> ''
                    AND a.refresh_token IS DISTINCT FROM ar.refresh_token
                 THEN 'refresh_mismatch'
               ELSE 'minor'
             END AS drift_kind
        FROM accounts a
        LEFT JOIN archives ar ON ar.platform = a.platform AND ar.email = a.email
       WHERE a.platform IN ('outlook','replit')
         AND ( ar.email IS NULL
            OR a.status IS DISTINCT FROM ar.status
            OR COALESCE(a.token,'')         IS DISTINCT FROM COALESCE(ar.token,'')
            OR COALESCE(a.refresh_token,'') IS DISTINCT FROM COALESCE(ar.refresh_token,'') )
       ORDER BY a.platform, a.email
       LIMIT 500
    `);
    const seriousKinds = new Set(['archive_missing','status_mismatch','token_mismatch','refresh_mismatch']);
    const serious = rows.filter(r => seriousKinds.has(r.drift_kind));
    const informational = rows.filter(r => !seriousKinds.has(r.drift_kind));
    const orphans = await query<{ platform: string; email: string; status: string | null }>(`
      SELECT ar.platform, ar.email, ar.status
        FROM archives ar
       WHERE ar.platform IN ('outlook','replit')
         AND NOT EXISTS (SELECT 1 FROM accounts a WHERE a.platform = ar.platform AND a.email = ar.email)
       ORDER BY ar.platform, ar.email
       LIMIT 500
    `);
    res.json({
      success: true,
      drift_count: rows.length,
      serious_drift_count: serious.length,
      informational_drift_count: informational.length,
      orphan_archive_count: orphans.length,
      drift: rows,
      serious,
      informational,
      orphan_archives: orphans,
      healthy: serious.length === 0 && orphans.length === 0,
    });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

// ─── 档案库 CRUD ─────────────────────────────────────────────────────────────
router.get("/data/archives", async (req, res) => {
  try {
    await ensureArchivesTable();
    const { platform, status, search } = req.query as Record<string, string>;
    let sql = "SELECT * FROM archives WHERE 1=1";
    const params: unknown[] = [];
    if (platform) { params.push(platform); sql += ` AND platform=$${params.length}`; }
    if (status)   { params.push(status);   sql += ` AND status=$${params.length}`; }
    if (search)   { params.push(`%${search}%`); sql += ` AND (email ILIKE $${params.length} OR username ILIKE $${params.length} OR notes ILIKE $${params.length})`; }
    sql += " ORDER BY created_at DESC LIMIT 500";
    const [pgRows, sqliteRows] = await Promise.all([
      query(sql, params),
      sqliteQuery(
        `SELECT id, label as username, service as platform, email, password, token, refresh_token,
                sandbox_id as machine_id, proxy as proxy_used, reg_email as registration_email,
                status, notes, created_at,
                service_user_id, service_workspace_id, service_project_id
         FROM profiles WHERE 1=1` +
        (platform ? ` AND service='${platform.replace(/'/g,"''")}'` : "") +
        (status   ? ` AND status='${status.replace(/'/g,"''")}'`   : "") +
        (search   ? ` AND (email LIKE '%${search.replace(/'/g,"''")}%' OR label LIKE '%${search.replace(/'/g,"''")}%' OR notes LIKE '%${search.replace(/'/g,"''")}%')` : "") +
        ` ORDER BY created_at DESC LIMIT 200`
      ),
    ]);
    const pgEmails = new Set((pgRows as Array<{email: string; platform: string}>).map(r => `${r.platform}:${r.email?.toLowerCase()}`));
    const mergedSqlite = (sqliteRows as Array<Record<string,unknown>>)
      .filter(r => {
        const key = `${r.platform}:${String(r.email ?? "").toLowerCase()}`;
        return r.email && !pgEmails.has(key);
      })
      .map((r, i) => {
        const suid = r.service_user_id;
        const swid = r.service_workspace_id;
        const spid = r.service_project_id;
        const identity_data = (suid || swid || spid) ? { userId: suid, workspaceId: swid, projectId: spid } : null;
        return { ...r, id: 4000000 + Number(r.id ?? i), identity_data, _source: "sqlite" };
      });
    const data = [...(pgRows as Record<string,unknown>[]), ...mergedSqlite];
    res.json({ success: true, data, total: data.length });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

router.get("/data/archives/by-email", async (req, res) => {
  try {
    await ensureArchivesTable();
    const { email, platform } = req.query as Record<string, string>;
    if (!email) { res.status(400).json({ success: false, error: "email 必填" }); return; }
    let sql = "SELECT * FROM archives WHERE email=$1";
    const params: unknown[] = [email];
    if (platform) { params.push(platform); sql += ` AND platform=$${params.length}`; }
    sql += " ORDER BY created_at DESC LIMIT 1";
    const row = await queryOne(sql, params);
    res.json({ success: true, data: row });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

router.get("/data/archives/:id", async (req, res) => {
  try {
    await ensureArchivesTable();
    const row = await queryOne("SELECT * FROM archives WHERE id=$1", [req.params.id]);
    if (!row) { res.status(404).json({ success: false, error: "未找到" }); return; }
    res.json({ success: true, data: row });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

router.post("/data/archives", async (req, res) => {
  try {
    await ensureArchivesTable();
    const {
      platform = "unknown", email, password, username, token, refresh_token,
      machine_id, fingerprint, proxy_used, identity_data, cookies,
      registration_email, status = "active", notes,
    } = req.body;
    if (!email) { res.status(400).json({ success: false, error: "email 必填" }); return; }
    const row = await queryOne(
      `INSERT INTO archives
         (platform,email,password,username,token,refresh_token,machine_id,fingerprint,proxy_used,identity_data,cookies,registration_email,status,notes)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
       ON CONFLICT DO NOTHING
       RETURNING *`,
      [
        platform, email, password || null, username || null, token || null, refresh_token || null,
        machine_id || null,
        fingerprint ? JSON.stringify(fingerprint) : null,
        proxy_used || null,
        identity_data ? JSON.stringify(identity_data) : null,
        cookies ? JSON.stringify(cookies) : null,
        registration_email || null, status, notes || null,
      ]
    );
    res.json({ success: true, data: row });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

router.put("/data/archives/:id", async (req, res) => {
  try {
    await ensureArchivesTable();
    const {
      platform, email, password, username, token, refresh_token,
      machine_id, fingerprint, proxy_used, identity_data, cookies,
      registration_email, status, notes,
    } = req.body;
    const row = await queryOne(
      `UPDATE archives SET
         platform=$1,email=$2,password=$3,username=$4,token=$5,refresh_token=$6,
         machine_id=$7,fingerprint=$8,proxy_used=$9,identity_data=$10,cookies=$11,
         registration_email=$12,status=$13,notes=$14,updated_at=NOW()
       WHERE id=$15 RETURNING *`,
      [
        platform, email, password || null, username || null, token || null, refresh_token || null,
        machine_id || null,
        fingerprint ? JSON.stringify(fingerprint) : null,
        proxy_used || null,
        identity_data ? JSON.stringify(identity_data) : null,
        cookies ? JSON.stringify(cookies) : null,
        registration_email || null, status, notes || null,
        req.params.id,
      ]
    );
    if (!row) { res.status(404).json({ success: false, error: "未找到" }); return; }
    res.json({ success: true, data: row });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

router.delete("/data/archives/:id", async (req, res) => {
  try {
    await ensureArchivesTable();
    await execute("DELETE FROM archives WHERE id=$1", [req.params.id]);
    res.json({ success: true });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

// 批量保存档案（注册成功后由 tools.ts 调用）
router.post("/data/archives/bulk-upsert", async (req, res) => {
  try {
    await ensureArchivesTable();
    const { records } = req.body as { records: Record<string, unknown>[] };
    let saved = 0;
    for (const r of records) {
      try {
        await execute(
          `INSERT INTO archives
             (platform,email,password,username,token,refresh_token,machine_id,fingerprint,proxy_used,identity_data,cookies,registration_email,status,notes)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
           ON CONFLICT DO NOTHING`,
          [
            r.platform || "unknown", r.email, r.password || null, r.username || null,
            r.token || null, r.refresh_token || null, r.machine_id || null,
            r.fingerprint ? JSON.stringify(r.fingerprint) : null,
            r.proxy_used || null,
            r.identity_data ? JSON.stringify(r.identity_data) : null,
            r.cookies ? JSON.stringify(r.cookies) : null,
            r.registration_email || null, r.status || "active", r.notes || null,
          ]
        );
        saved++;
      } catch {}
    }
    res.json({ success: true, saved });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

// ─── 账号 CRUD (AI服务池) ─────────────────────────────────────────────────────
router.get("/data/accounts", async (req, res) => {
  try {
    const { platform, status, search, exclude_platform } = req.query as Record<string, string>;
    let sql = "SELECT * FROM accounts WHERE 1=1";
    const params: unknown[] = [];
    if (platform) { params.push(platform); sql += ` AND platform=$${params.length}`; }
    if (exclude_platform) { params.push(exclude_platform); sql += ` AND platform!=$${params.length}`; }
    if (status)   { params.push(status);   sql += ` AND status=$${params.length}`; }
    if (search)   { params.push(`%${search}%`); sql += ` AND (email ILIKE $${params.length} OR username ILIKE $${params.length} OR notes ILIKE $${params.length})`; }
    sql += " ORDER BY created_at DESC LIMIT 500";
    const [pgRows, sqliteRows] = await Promise.all([
      query(sql, params),
      sqliteQuery(
        `SELECT id, service as platform, email, password, api_key as token, status, notes, created_at FROM ai_accounts WHERE 1=1` +
        (platform ? ` AND service='${platform.replace(/'/g,"''")}'` : "") +
        (status   ? ` AND status='${status.replace(/'/g,"''")}'`   : "") +
        (search   ? ` AND (email LIKE '%${search.replace(/'/g,"''")}%' OR notes LIKE '%${search.replace(/'/g,"''")}%')` : "") +
        ` ORDER BY created_at DESC LIMIT 300`
      ),
    ]);
    const pgEmails = new Set((pgRows as Array<{email: string}>).map(r => r.email?.toLowerCase()));
    const mergedSqlite = (sqliteRows as Array<Record<string,unknown>>)
      .filter(r => {
        const em = String(r.email ?? "").toLowerCase();
        // Allow records with no email (e.g. airforce api-key-only entries); only skip if email is non-empty AND already in PG
        if (em && pgEmails.has(em)) return false;
        return true;
      })
      .map((r, i) => ({ ...r, id: 2000000 + Number(r.id ?? i), _source: "sqlite" }));
    const data = [...(pgRows as Record<string,unknown>[]), ...mergedSqlite];
    res.json({ success: true, data, total: data.length });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

router.post("/data/accounts", async (req, res) => {
  try {
    const { platform = "outlook", email, password, username, token, refresh_token, status = "active", notes, tags } = req.body;
    if (!email || !password) { res.status(400).json({ success: false, error: "email 和 password 必填" }); return; }
    const row = await queryOne(
      `INSERT INTO accounts (platform,email,password,username,token,refresh_token,status,notes,tags)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
       ON CONFLICT (platform,email) DO UPDATE SET password=EXCLUDED.password,username=EXCLUDED.username,token=COALESCE(EXCLUDED.token,accounts.token),refresh_token=COALESCE(EXCLUDED.refresh_token,accounts.refresh_token),status=EXCLUDED.status,notes=COALESCE(EXCLUDED.notes,accounts.notes),tags=COALESCE(EXCLUDED.tags,accounts.tags),updated_at=NOW()
       RETURNING *`,
      [platform, email, password, username || null, token || null, refresh_token || null, status, notes || null, tags || null]
    );
    if (platform === "outlook") {
      await execute(
        `INSERT INTO temp_emails (address,password,provider,status,notes)
         VALUES ($1,$2,$3,$4,$5)
         ON CONFLICT (address) DO UPDATE SET password=EXCLUDED.password,status=EXCLUDED.status,notes=COALESCE(EXCLUDED.notes,temp_emails.notes)`,
        [email, password, "outlook", status, notes || "Outlook 账号库同步"],
      );
    }
    // 同步到档案库（仅有基础信息）
    try {
      await ensureArchivesTable();
      await execute(
        `INSERT INTO archives (platform,email,password,username,token,refresh_token,status,notes)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT DO NOTHING`,
        [platform, email, password, username || null, token || null, refresh_token || null, status, notes || "手动添加"]
      );
    } catch {}
    res.json({ success: true, data: row });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

router.put("/data/accounts/:id", async (req, res) => {
  try {
    const { platform, email, password, username, token, refresh_token, status, notes, tags } = req.body;
    const row = await queryOne(
      `UPDATE accounts SET platform=$1,email=$2,password=$3,username=$4,token=$5,refresh_token=$6,
       status=$7,notes=$8,tags=$9,updated_at=NOW() WHERE id=$10 RETURNING *`,
      [platform, email, password, username || null, token || null, refresh_token || null, status, notes || null, tags || null, req.params.id]
    );
    if (!row) { res.status(404).json({ success: false, error: "未找到" }); return; }
    res.json({ success: true, data: row });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

router.delete("/data/accounts/:id", async (req, res) => {
  try {
    await execute("DELETE FROM accounts WHERE id=$1", [req.params.id]);
    res.json({ success: true });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

// 批量导入账号
router.post("/data/accounts/import", async (req, res) => {
  try {
    const { text, platform = "outlook", delimiter = "----" } = req.body as { text: string; platform?: string; delimiter?: string };
    const lines = text.split("\n").map((l: string) => l.trim()).filter(Boolean);
    let inserted = 0;
    for (const line of lines) {
      const parts = line.split(delimiter);
      if (parts.length < 2) continue;
      const [email, password, ...rest] = parts;
      const token = rest[0] || null;
      try {
        await execute(
          `INSERT INTO accounts (platform,email,password,token,status) VALUES ($1,$2,$3,$4,'active')
           ON CONFLICT DO NOTHING`,
          [platform, email.trim(), password.trim(), token]
        );
        inserted++;
      } catch {}
    }
    res.json({ success: true, inserted, total: lines.length });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

// 导出账号
router.get("/data/accounts/export", async (req, res) => {
  try {
    const { format = "txt", platform } = req.query as Record<string, string>;
    let sql = "SELECT * FROM accounts WHERE status='active'";
    const params: unknown[] = [];
    if (platform) { params.push(platform); sql += ` AND platform=$${params.length}`; }
    sql += " ORDER BY created_at DESC";
    const rows = await query(sql, params);

    if (format === "json") {
      res.setHeader("Content-Type", "application/json");
      res.setHeader("Content-Disposition", "attachment; filename=accounts.json");
      res.send(JSON.stringify(rows, null, 2));
    } else if (format === "csv") {
      res.setHeader("Content-Type", "text/csv");
      res.setHeader("Content-Disposition", "attachment; filename=accounts.csv");
      res.send(["platform,email,password,token,status", ...rows.map((r: Record<string, unknown>) => `${r.platform},${r.email},${r.password},${r.token || ""},${r.status}`)].join("\n"));
    } else {
      res.setHeader("Content-Type", "text/plain");
      res.setHeader("Content-Disposition", "attachment; filename=accounts.txt");
      res.send(rows.map((r: Record<string, unknown>) => `${r.email}----${r.password}${r.token ? "----" + r.token : ""}`).join("\n"));
    }
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

// ─── 临时邮箱 CRUD ───────────────────────────────────────
router.get("/data/emails", async (req, res) => {
  try {
    const { search, provider } = req.query as Record<string, string>;
    let sql = "SELECT * FROM temp_emails WHERE 1=1";
    const params: unknown[] = [];
    if (provider) { params.push(provider); sql += ` AND provider=$${params.length}`; }
    if (search) { params.push(`%${search}%`); sql += ` AND (address ILIKE $${params.length} OR notes ILIKE $${params.length})`; }
    sql += " ORDER BY created_at DESC LIMIT 500";
    const [pgRows, sqliteRows] = await Promise.all([
      query(sql, params),
      sqliteQuery(
        `SELECT id, email as address, password, platform as provider, status, notes, created_at FROM emails WHERE 1=1` +
        (provider ? ` AND platform='${provider.replace(/'/g,"''")}'` : "") +
        (search   ? ` AND (email LIKE '%${search.replace(/'/g,"''")}%' OR notes LIKE '%${search.replace(/'/g,"''")}%')` : "") +
        ` ORDER BY created_at DESC LIMIT 600`
      ),
    ]);
    const pgAddrs = new Set((pgRows as Array<{address: string}>).map(r => r.address?.toLowerCase()));
    const mergedSqlite = (sqliteRows as Array<Record<string,unknown>>)
      .filter(r => {
        const addr = String(r.address ?? "").toLowerCase();
        return addr && !pgAddrs.has(addr);
      })
      .map((r, i) => ({ ...r, id: 3000000 + Number(r.id ?? i), _source: "sqlite" }));
    const data = [...(pgRows as Record<string,unknown>[]), ...mergedSqlite];
    res.json({ success: true, data, total: data.length });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

router.post("/data/emails", async (req, res) => {
  try {
    const { address, password, provider = "mailtm", token, status = "active", notes } = req.body;
    if (!address) { res.status(400).json({ success: false, error: "address 必填" }); return; }
    const row = await queryOne(
      `INSERT INTO temp_emails (address,password,provider,token,status,notes)
       VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (address) DO UPDATE SET password=$2,token=$4,status=$5,notes=COALESCE($6,temp_emails.notes) RETURNING *`,
      [address, password || null, provider, token || null, status, notes || null]
    );
    // 同步到档案库
    if (provider === "outlook" || provider === "hotmail") {
      try {
        await ensureArchivesTable();
        await execute(
          `INSERT INTO archives (platform,email,password,token,status,notes)
           VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING`,
          ["outlook", address, password || null, token || null, status, notes || "邮箱库同步"]
        );
      } catch {}
    }
    res.json({ success: true, data: row });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

router.delete("/data/emails/:id", async (req, res) => {
  try {
    await execute("DELETE FROM temp_emails WHERE id=$1", [req.params.id]);
    res.json({ success: true });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

// ─── 身份信息 CRUD（保留兼容）───────────────────────────────────────
router.get("/data/identities", async (req, res) => {
  try {
    const { search } = req.query as Record<string, string>;
    let sql = "SELECT * FROM identities WHERE 1=1";
    const params: unknown[] = [];
    if (search) { params.push(`%${search}%`); sql += ` AND (full_name ILIKE $1 OR email ILIKE $1 OR username ILIKE $1)`; }
    sql += " ORDER BY created_at DESC LIMIT 500";
    const rows = await query(sql, params);
    res.json({ success: true, data: rows, total: rows.length });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

router.post("/data/identities", async (req, res) => {
  try {
    const { first_name, last_name, full_name, gender, birthday, phone, email, address, city, state, zip, country, username, password, notes } = req.body;
    const row = await queryOne(
      `INSERT INTO identities (first_name,last_name,full_name,gender,birthday,phone,email,address,city,state,zip,country,username,password,notes)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15) RETURNING *`,
      [first_name, last_name, full_name || `${first_name} ${last_name}`, gender, birthday || null, phone, email, address, city, state, zip, country || "United States", username, password, notes]
    );
    res.json({ success: true, data: row });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

router.post("/data/identities/bulk", async (req, res) => {
  try {
    const { identities } = req.body as { identities: Record<string, string>[] };
    let inserted = 0;
    for (const id of identities) {
      try {
        await execute(
          `INSERT INTO identities (first_name,last_name,full_name,gender,birthday,phone,email,address,city,state,zip,country,username,password)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)`,
          [id.first_name, id.last_name, id.full_name || `${id.first_name} ${id.last_name}`, id.gender, id.birthday || null, id.phone, id.email, id.address, id.city, id.state, id.zip, id.country || "United States", id.username, id.password]
        );
        inserted++;
      } catch {}
    }
    res.json({ success: true, inserted });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

router.delete("/data/identities/:id", async (req, res) => {
  try {
    await execute("DELETE FROM identities WHERE id=$1", [req.params.id]);
    res.json({ success: true });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

// ─── 配置 CRUD ──────────────────────────────────────────
router.get("/data/configs", async (req, res) => {
  try {
    const rows = await query("SELECT * FROM configs ORDER BY key");
    const map: Record<string, string> = {};
    for (const r of rows as Array<{ key: string; value: string }>) map[r.key] = r.value;
    res.json({ success: true, data: rows, map });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

router.put("/data/configs/:key", async (req, res) => {
  try {
    const { value, description } = req.body;
    const row = await queryOne(
      `INSERT INTO configs (key,value,description) VALUES ($1,$2,$3)
       ON CONFLICT (key) DO UPDATE SET value=$2,description=COALESCE($3,configs.description),updated_at=NOW() RETURNING *`,
      [req.params.key, value, description || null]
    );
    res.json({ success: true, data: row });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

router.post("/data/configs/batch", async (req, res) => {
  try {
    const { configs } = req.body as { configs: Record<string, string> };
    for (const [key, value] of Object.entries(configs)) {
      await execute(
        `INSERT INTO configs (key,value) VALUES ($1,$2) ON CONFLICT (key) DO UPDATE SET value=$2,updated_at=NOW()`,
        [key, value]
      );
    }
    res.json({ success: true });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

// ─── 代理池 CRUD ─────────────────────────────────────────
const SUBNODE_BRIDGE_MIN_PORT = Number(process.env["SUBNODE_BRIDGE_MIN_PORT"] || 1089);
const SUBNODE_BRIDGE_MAX_PORT = Number(process.env["SUBNODE_BRIDGE_MAX_PORT"] || 1199);
const SUBNODE_BRIDGE_SQL = `
  (
    (host = '127.0.0.1' AND port BETWEEN ${SUBNODE_BRIDGE_MIN_PORT} AND ${SUBNODE_BRIDGE_MAX_PORT})
    OR formatted = 'socks5://127.0.0.1:1089'
    OR formatted ILIKE 'socks5://127.0.0.1:109%'
    OR formatted ILIKE 'socks5://127.0.0.1:11%'
  )
`;
let lastSubnodeBridgeSync = 0;

// 真实外网连通性探测：SOCKS5 握手 + CONNECT 到 Outlook，250ms 内握手 + 5s 内 CONNECT 成功才算可用
const SOCKS5_PROBE_HOST = "login.live.com";
const SOCKS5_PROBE_PORT = 443;
const SOCKS5_HANDSHAKE_TIMEOUT_MS = 600;
const SOCKS5_CONNECT_TIMEOUT_MS   = 5000;

function testSocks5Connectivity(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = new Socket();
    let step = 0;   // 0=handshake, 1=connect-req
    let done = false;
    let timer: ReturnType<typeof setTimeout>;

    const finish = (ok: boolean) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      socket.destroy();
      resolve(ok);
    };

    // 阶段超时：握手阶段先用短超时，CONNECT 阶段用长超时
    const armTimer = (ms: number) => {
      clearTimeout(timer);
      timer = setTimeout(() => finish(false), ms);
    };

    socket.once("error", () => finish(false));

    socket.on("data", (buf) => {
      if (step === 0) {
        // 期望握手响应 0x05 0x00
        if (buf.length < 2 || buf[0] !== 0x05 || buf[1] !== 0x00) return finish(false);
        step = 1;
        // 发送 CONNECT 到探测目标
        const hostBuf = Buffer.from(SOCKS5_PROBE_HOST, "ascii");
        const req = Buffer.alloc(7 + hostBuf.length);
        req[0] = 0x05; req[1] = 0x01; req[2] = 0x00; req[3] = 0x03;
        req[4] = hostBuf.length;
        hostBuf.copy(req, 5);
        req.writeUInt16BE(SOCKS5_PROBE_PORT, 5 + hostBuf.length);
        armTimer(SOCKS5_CONNECT_TIMEOUT_MS);
        socket.write(req);
      } else if (step === 1) {
        // 期望 CONNECT 响应 0x05 0x00
        finish(buf.length >= 2 && buf[0] === 0x05 && buf[1] === 0x00);
      }
    });

    armTimer(SOCKS5_HANDSHAKE_TIMEOUT_MS);
    socket.connect(port, "127.0.0.1", () => {
      socket.write(Buffer.from([0x05, 0x01, 0x00]));
    });
  });
}

async function syncLocalSubnodeBridgeProxies(force = false) {
  const now = Date.now();
  if (!force && now - lastSubnodeBridgeSync < 30000) return;
  lastSubnodeBridgeSync = now;

  const ports = Array.from(
    { length: SUBNODE_BRIDGE_MAX_PORT - SUBNODE_BRIDGE_MIN_PORT + 1 },
    (_, i) => SUBNODE_BRIDGE_MIN_PORT + i
  );

  // 并发限制：分批探测，每批 8 个（避免大量 TCP 超时时互相干扰）
  const BATCH = 8;
  const results: { port: number; ok: boolean }[] = [];
  for (let i = 0; i < ports.length; i += BATCH) {
    const batch = ports.slice(i, i + BATCH);
    const batchResults = await Promise.all(
      batch.map(async (port) => ({ port, ok: await testSocks5Connectivity(port) }))
    );
    results.push(...batchResults);
  }

  const good = results.filter((r) => r.ok).map((r) => r.port);
  const bad  = results.filter((r) => !r.ok).map((r) => r.port);

  if (good.length || bad.length)
    console.log(`[subnode-bridge] 探测完成: 可用=${good.length} 失败=${bad.length} 端口:`, good.join(",") || "无");

  // 可用桥：写入/恢复为 idle
  for (const port of good) {
    await execute(
      `INSERT INTO proxies (formatted, host, port, status, used_count, last_used)
       VALUES ($1, '127.0.0.1', $2, 'idle', 0, NULL)
       ON CONFLICT (formatted) DO UPDATE SET
         host='127.0.0.1',
         port=$2,
         status=CASE WHEN proxies.status='banned' THEN 'idle' ELSE proxies.status END`,
      [`socks5://127.0.0.1:${port}`, port]
    );
  }

  // 失败桥：若已在库中则标记 banned，防止被分配
  if (bad.length > 0) {
    const badFormatted = bad.map((p) => `socks5://127.0.0.1:${p}`);
    // 用单条 SQL 批量更新，避免 N 次往返
    await execute(
      `UPDATE proxies SET status='banned', last_used=NOW()
       WHERE formatted = ANY($1::text[]) AND status != 'banned'`,
      [badFormatted]
    );
  }
}

const ELIGIBLE_PROXY_SQL = `
  status != 'banned'
  AND NOT (host = '127.0.0.1' AND port BETWEEN 10820 AND 10845)
  AND NOT (formatted ILIKE 'socks5://127.0.0.1:1082%' OR formatted ILIKE 'socks5://127.0.0.1:1083%' OR formatted ILIKE 'socks5://127.0.0.1:1084%')
`;

const PROXY_SOURCE_CASE = `
  CASE
    WHEN ${SUBNODE_BRIDGE_SQL} THEN 'subnode_bridge'
    WHEN host = '127.0.0.1' THEN 'local_proxy'
    ELSE 'external'
  END
`;

router.get("/data/proxies", async (req, res) => {
  try {
    await syncLocalSubnodeBridgeProxies();
    const rows = await query(`
      SELECT id, formatted, host, port, status, used_count, last_used, created_at,
             ${PROXY_SOURCE_CASE} AS source,
             (${ELIGIBLE_PROXY_SQL}) AS eligible
      FROM proxies
      ORDER BY used_count ASC, id ASC
      LIMIT 300
    `);
    const stats = await queryOne<{
      total: string; eligible: string; subnode_bridge: string; residential: string; external: string; local_proxy: string;
    }>(`
      SELECT
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE ${ELIGIBLE_PROXY_SQL}) AS eligible,
        COUNT(*) FILTER (WHERE ${ELIGIBLE_PROXY_SQL} AND ${SUBNODE_BRIDGE_SQL}) AS subnode_bridge,
        COUNT(*) FILTER (WHERE ${ELIGIBLE_PROXY_SQL} AND host <> '127.0.0.1') AS external,
        COUNT(*) FILTER (WHERE ${ELIGIBLE_PROXY_SQL} AND host = '127.0.0.1' AND NOT (${SUBNODE_BRIDGE_SQL})) AS local_proxy
      FROM proxies
    `);
    res.json({
      success: true,
      data: rows,
      total: Number(stats?.total ?? rows.length),
      eligibleTotal: Number(stats?.eligible ?? 0),
      sources: {
        subnodeBridge: Number(stats?.subnode_bridge ?? 0),
        external: Number(stats?.external ?? 0),
        localProxy: Number(stats?.local_proxy ?? 0),
      },
    });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

// 从共享代理池取一个最少使用的代理（子节点桥/住宅/外部代理优先，排除已知死亡的 CF 本地端口）
router.get("/data/proxies/pick", async (req, res) => {
  try {
    await syncLocalSubnodeBridgeProxies();
    const row = await queryOne<{ id: number; formatted: string; source: string }>(`
      SELECT id, formatted, ${PROXY_SOURCE_CASE} AS source
      FROM proxies
      WHERE ${ELIGIBLE_PROXY_SQL}
      ORDER BY
        CASE
          WHEN ${SUBNODE_BRIDGE_SQL} THEN 0
          WHEN host <> '127.0.0.1' THEN 2
          ELSE 3
        END,
        used_count ASC,
        RANDOM()
      LIMIT 1
    `);
    if (!row) { res.json({ success: false, error: "共享代理池为空，请先导入代理或部署子节点" }); return; }
    await execute(
      "UPDATE proxies SET used_count = used_count + 1, last_used = NOW(), status = 'active' WHERE id = $1",
      [row.id]
    );
    res.json({ success: true, proxy: row.formatted, id: row.id, source: row.source });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

// 归还代理（使用完毕标记 idle）
router.put("/data/proxies/:id/release", async (req, res) => {
  try {
    await execute("UPDATE proxies SET status='idle' WHERE id=$1 AND status='active'", [req.params.id]);
    res.json({ success: true });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

// 标记代理失效
router.put("/data/proxies/:id/ban", async (req, res) => {
  try {
    await execute("UPDATE proxies SET status='banned' WHERE id=$1", [req.params.id]);
    res.json({ success: true });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

// 重置所有代理状态
router.post("/data/proxies/reset", async (req, res) => {
  try {
    await execute("UPDATE proxies SET status='idle', used_count=0, last_used=NULL");
    res.json({ success: true });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

// 批量导入代理

// ── 清除代理（按 pattern）─────────────────────────────────────────────────
router.delete("/data/proxies/purge", async (req, res) => {
  try {
    const { pattern } = req.query as { pattern?: string };
    if (!pattern) { res.status(400).json({ success: false, error: "需要 pattern 参数" }); return; }
    const result = await query<{ id: number }>(
      `SELECT id FROM proxies WHERE formatted ILIKE $1`, [`%${pattern}%`]
    );
    if (result.length === 0) { res.json({ success: true, deleted: 0 }); return; }
    await execute(`DELETE FROM proxies WHERE id = ANY($1::int[])`, [result.map(r => r.id)]);
    console.log(`[proxies/purge] 删除 ${result.length} 个匹配 "${pattern}" 的代理`);
    res.json({ success: true, deleted: result.length });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});
router.post("/data/proxies/import", async (req, res) => {
  try {
    const { text } = req.body as { text: string };
    const lines = text.split("\n").map((l: string) => l.trim()).filter(Boolean);
    let inserted = 0;
    for (const raw of lines) {
      let formatted = raw;
      let host = "", port = 0, user = "", pass = "";
      const noProto = raw.replace(/^socks5:\/\/|^http:\/\//, "");
      const parts = noProto.split(":");
      if (parts.length === 4 && !noProto.includes("@")) {
        [host, , user, pass] = parts; port = parseInt(parts[1]);
        formatted = `socks5://${user}:${pass}@${host}:${port}`;
      } else if (raw.includes("@")) {
        const m = raw.match(/socks5:\/\/([^:]+):([^@]+)@([^:]+):(\d+)/);
        if (m) { [, user, pass, host] = m; port = parseInt(m[4]); formatted = raw; }
      }
      if (!host) continue;
      try {
        await execute(
          `INSERT INTO proxies (formatted, host, port, username, password, status) VALUES ($1,$2,$3,$4,$5,'idle') ON CONFLICT (formatted) DO NOTHING`,
          [formatted, host, port, user, pass]
        );
        inserted++;
      } catch (err) { console.error("proxy insert err:", String(err)); }
    }
    res.json({ success: true, inserted, total: lines.length });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

// ─── 统计 ───────────────────────────────────────────────
router.get("/data/stats", async (req, res) => {
  try {
    await ensureArchivesTable();
    const EMAIL_PLATFORMS = ["outlook", "gmail", "yahoo", "hotmail", "163", "qq"];
    const [accts, archives, tempEmails, longTermEmails, proxyStat] = await Promise.all([
      queryOne<{ total: string; active: string }>(`SELECT COUNT(*) as total, COUNT(*) FILTER (WHERE status='active') as active FROM accounts`),
      queryOne<{ total: string }>(`SELECT COUNT(*) as total FROM archives`),
      queryOne<{ total: string }>(`SELECT COUNT(*) as total FROM temp_emails`),
      queryOne<{ total: string }>(`SELECT COUNT(*) as total FROM accounts WHERE platform = ANY($1)`, [EMAIL_PLATFORMS]),
      queryOne<{ idle: string; active: string; banned: string }>(`SELECT COUNT(*) FILTER (WHERE status='idle') as idle, COUNT(*) FILTER (WHERE status='active') as active, COUNT(*) FILTER (WHERE status='banned') as banned FROM proxies`),
    ]);
    const byPlatform = await query<{ platform: string; count: string }>(
      `SELECT platform, COUNT(*) as count FROM accounts GROUP BY platform ORDER BY count DESC`
    );
    const [sqliteAccountCount, sqliteEmailCount, sqliteProfileCount] = await Promise.all([
      sqliteQuery("SELECT COUNT(*) as cnt FROM ai_accounts").then(r => Number((r[0] as Record<string,unknown>)?.cnt ?? 0)),
      Promise.resolve(0), // emails in SQLite mirror PG temp_emails (same source); avoid double-count
      sqliteQuery("SELECT COUNT(*) as cnt FROM profiles").then(r => Number((r[0] as Record<string,unknown>)?.cnt ?? 0)),
    ]);
    const sqliteByPlatform = await sqliteQuery("SELECT service as platform, COUNT(*) as count FROM ai_accounts GROUP BY service ORDER BY count DESC");
    const pgByPlatformMap = new Map(byPlatform.map(r => [r.platform, Number(r.count)]));
    for (const r of sqliteByPlatform as Array<{platform: string; count: number}>) {
      const plat = String(r.platform);
      pgByPlatformMap.set(plat, (pgByPlatformMap.get(plat) ?? 0) + Number(r.count));
    }
    const mergedByPlatform = Array.from(pgByPlatformMap.entries())
      .map(([platform, count]) => ({ platform, count }))
      .sort((a, b) => b.count - a.count);
    res.json({
      success: true,
      accounts:    { total: Number(accts?.total ?? 0) + sqliteAccountCount, active: Number(accts?.active ?? 0) },
      archives:    { total: Number(archives?.total ?? 0) + sqliteProfileCount },
      emails:      { total: Number(tempEmails?.total ?? 0) + sqliteEmailCount },
      long_term:   { total: Number(longTermEmails?.total ?? 0) },
      proxies:     { idle: Number(proxyStat?.idle ?? 0), active: Number(proxyStat?.active ?? 0), banned: Number(proxyStat?.banned ?? 0) },
      byPlatform:  mergedByPlatform,
    });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

// ── 代理池后台维护 ─────────────────────────────────────────────────────────────
const PROXY_MAINTAIN_INTERVAL_MS = 2 * 60 * 1000;   // 2 分钟跑一次
const STUCK_ACTIVE_TIMEOUT_MS    = 8 * 60 * 1000;   // active 超 8 分钟算卡死
const REPLENISH_THRESHOLD        = 50;               // idle 低于此数触发补充
const REPLENISH_TARGET           = 100;              // 补充目标数量

interface MaintenanceResult {
  ts: number; checked: number; banned: number; recycled: number; replenished: number;
}
let lastMaintenanceResult: MaintenanceResult | null = null;

/** 对任意 SOCKS5 代理 URL 做真实连通性探测：握手 + CONNECT login.live.com:443 */
function testProxyConnectivity(proxyUrl: string): Promise<boolean> {
  return new Promise((resolve) => {
    if (!proxyUrl.startsWith("socks5://")) { resolve(true); return; }
    const m = proxyUrl.match(/socks5:\/\/(?:[^@]+@)?([^:]+):(\d+)/);
    if (!m) { resolve(false); return; }
    const [, proxyHost, proxyPortStr] = m;
    const proxyPort = Number(proxyPortStr);
    const socket = new Socket();
    let step = 0, done = false;
    let timer: ReturnType<typeof setTimeout>;
    const finish = (ok: boolean) => {
      if (done) return; done = true;
      clearTimeout(timer); socket.destroy(); resolve(ok);
    };
    const armTimer = (ms: number) => { clearTimeout(timer); timer = setTimeout(() => finish(false), ms); };
    socket.once("error", () => finish(false));
    socket.on("data", (buf) => {
      if (step === 0) {
        if (buf.length < 2 || buf[0] !== 0x05 || buf[1] !== 0x00) return finish(false);
        step = 1;
        const hostBuf = Buffer.from(SOCKS5_PROBE_HOST, "ascii");
        const req = Buffer.alloc(7 + hostBuf.length);
        req[0]=0x05; req[1]=0x01; req[2]=0x00; req[3]=0x03; req[4]=hostBuf.length;
        hostBuf.copy(req, 5); req.writeUInt16BE(SOCKS5_PROBE_PORT, 5 + hostBuf.length);
        armTimer(SOCKS5_CONNECT_TIMEOUT_MS); socket.write(req);
      } else if (step === 1) {
        finish(buf.length >= 2 && buf[0] === 0x05 && buf[1] === 0x00);
      }
    });
    armTimer(SOCKS5_HANDSHAKE_TIMEOUT_MS);
    socket.connect(proxyPort, proxyHost, () => socket.write(Buffer.from([0x05, 0x01, 0x00])));
  });
}

/** eligible < REPLENISH_THRESHOLD 时从 CF IP 状态文件取全量 IP 补充到 REPLENISH_TARGET */
async function replenishFromCfPool(currentEligible: number): Promise<number> {
  const needed = REPLENISH_TARGET - currentEligible;
  if (needed <= 0) return 0;
  console.log(`[proxy-maintain] eligible=${currentEligible} < ${REPLENISH_THRESHOLD}，从 CF IP 池补充 ${needed} 个...`);
  try {
    // 直接读 CF 池状态文件，包含全量可用 IP（不受 status 命令 top-20 限制）
    const { readFileSync } = await import("fs");
    const CF_STATE_FILE = process.env["CF_POOL_STATE_FILE"] || "/tmp/cf_pool_state.json";
    let cfState: { available?: Array<{ ip: string; latency: number }> } = {};
    try { cfState = JSON.parse(readFileSync(CF_STATE_FILE, "utf8")); } catch {}
    const available = cfState.available || [];
    if (available.length === 0) { console.log("[proxy-maintain] CF 状态文件为空，无法补充"); return 0; }
    // 过滤掉已在 proxies 表中的 IP
    const allIps = available.map(x => x.ip);
    const existing = await query<{ host: string }>(`SELECT host FROM proxies WHERE host = ANY($1::text[])`, [allIps]);
    const existingSet = new Set(existing.map(row => row.host));
    const candidates = available.filter(x => !existingSet.has(x.ip)).slice(0, needed + 5);
    if (candidates.length === 0) { console.log("[proxy-maintain] CF IP 已全部在代理表中，无新 IP 可补"); return 0; }
    let added = 0;
    for (const cf of candidates) {
      if (added >= needed) break;
      const proxyUrl = `http://${cf.ip}:443`;
      try {
        await execute(
          `INSERT INTO proxies (formatted, host, port, status, used_count, last_used)
           VALUES ($1,$2,$3,'idle',0,NULL) ON CONFLICT (formatted) DO NOTHING`,
          [proxyUrl, cf.ip, 443]
        );
        added++;
      } catch {}
    }
    console.log(`[proxy-maintain] CF 池补充完成 +${added} 个（CF 全量: ${available.length}，eligible 目标 ${REPLENISH_TARGET}）`);
    return added;
  } catch (e) {
    console.error("[proxy-maintain] CF 池补充出错:", e);
    return 0;
  }
}

async function runProxyMaintenance() {
  if (!process.env["DATABASE_URL"]) return; // no DB configured
  const t0 = Date.now();
  let recycled = 0, banned = 0, checked = 0, replenished = 0;
  try {
    // 1. 回收卡 active 超 8 分钟的代理 → idle，重置 used_count
    const stuckCutoff = new Date(Date.now() - STUCK_ACTIVE_TIMEOUT_MS).toISOString();
    const stuck = await query<{ id: number }>(
      `SELECT id FROM proxies WHERE status='active' AND (last_used IS NULL OR last_used < $1)`,
      [stuckCutoff]
    );
    if (stuck.length > 0) {
      await execute(`UPDATE proxies SET status='idle', used_count=0 WHERE id=ANY($1::int[])`, [stuck.map(r => r.id)]);
      recycled = stuck.length;
    }

    // 2. 普通代理：used_count>=1 且 idle 且非桥 → banned（单次消耗型，含 CF IP）
    const usedRows = await query<{ id: number }>(
      `SELECT id FROM proxies
       WHERE status='idle' AND used_count >= 1
         AND NOT (host='127.0.0.1' AND port BETWEEN ${SUBNODE_BRIDGE_MIN_PORT} AND ${SUBNODE_BRIDGE_MAX_PORT})`,
      []
    );
    if (usedRows.length > 0) {
      await execute(`UPDATE proxies SET status='banned', last_used=NOW() WHERE id=ANY($1::int[])`, [usedRows.map(r => r.id)]);
      banned += usedRows.length;
    }

    // 4. 连通性检测：随机抽 30 个 idle SOCKS5 代理，验证真实出网
    const toCheck = await query<{ id: number; formatted: string }>(
      `SELECT id, formatted FROM proxies
       WHERE status='idle' AND formatted ILIKE 'socks5://%'
         AND NOT (host='127.0.0.1' AND port BETWEEN ${SUBNODE_BRIDGE_MIN_PORT} AND ${SUBNODE_BRIDGE_MAX_PORT})
       ORDER BY RANDOM() LIMIT 30`, []
    );
    checked = toCheck.length;
    const deadIds: number[] = [];
    for (let i = 0; i < toCheck.length; i += 5) {
      const batch = toCheck.slice(i, i + 5);
      const results = await Promise.all(batch.map(async p => ({ id: p.id, ok: await testProxyConnectivity(p.formatted) })));
      for (const r of results) if (!r.ok) deadIds.push(r.id);
    }
    if (deadIds.length > 0) {
      await execute(`UPDATE proxies SET status='banned', last_used=NOW() WHERE id=ANY($1::int[])`, [deadIds]);
      banned += deadIds.length;
    }

    // 5. 如果有效可用（eligible）代理不足阈值，自动补充
    //    eligible = 非banned + 非子节点桥 + 非本地代理，和前端显示口径一致
    const eligibleCount = await queryOne<{ cnt: string }>(
      `SELECT COUNT(*) AS cnt FROM proxies
       WHERE status != 'banned'
         AND NOT (host='127.0.0.1' AND port BETWEEN ${SUBNODE_BRIDGE_MIN_PORT} AND ${SUBNODE_BRIDGE_MAX_PORT})
         AND NOT (formatted ILIKE 'socks5://127.0.0.1:1082%'
               OR formatted ILIKE 'socks5://127.0.0.1:1083%'
               OR formatted ILIKE 'socks5://127.0.0.1:1084%')`, []
    );
    const currentEligible = Number(eligibleCount?.cnt ?? 0);
    if (currentEligible < REPLENISH_THRESHOLD) {
      replenished = await replenishFromCfPool(currentEligible);
    }

    lastMaintenanceResult = { ts: Date.now(), checked, banned, recycled, replenished };
    console.log(`[proxy-maintain] 完成 recycled=${recycled} banned=${banned} checked=${checked} replenished=${replenished} elapsed=${Date.now() - t0}ms`);
  } catch (e) {
    console.error("[proxy-maintain] 出错:", e);
  }
}

export function startProxyMaintenance() {
  console.log("[proxy-maintain] 启动代理池后台维护，每 2 分钟运行");
  setTimeout(() => { runProxyMaintenance(); setInterval(runProxyMaintenance, PROXY_MAINTAIN_INTERVAL_MS); }, 20_000);
}

router.get("/data/proxies/maintenance/status", (_req, res) => {
  res.json({ success: true, lastRun: lastMaintenanceResult });
});


// ══ unitool 链路实时统计 ════════════════════════════════════════════════════════
router.get("/data/unitool-stats", async (req, res) => {
  try {
    // 1. Outlook 账号分布
    const ol = await queryOne<{ fresh:string; registered:string; fail:string; processing:string; total:string }>(
      `SELECT
        COUNT(*) FILTER(WHERE tags IS NULL OR (
          tags NOT LIKE '%unitool_registered%'
          AND tags NOT LIKE '%unitool_fail%'
          AND tags NOT LIKE '%unitool_processing%'
          AND tags NOT LIKE '%unitool_already%'
          AND tags NOT LIKE '%unitool_rescue_dead%'
          AND tags NOT LIKE '%unitool_verify_pending%'
          AND (tags NOT LIKE '%unitool_reg_retry%' OR updated_at < NOW() - INTERVAL '4 hours')
        )) as fresh,
        COUNT(*) FILTER(WHERE tags LIKE '%unitool_registered%') as registered,
        COUNT(*) FILTER(WHERE tags LIKE '%unitool_fail%') as fail,
        COUNT(*) FILTER(WHERE tags LIKE '%unitool_processing%') as processing,
        COUNT(*) as total
       FROM accounts WHERE platform='outlook' AND status='active'
         AND refresh_token IS NOT NULL AND refresh_token != ''
         AND LENGTH(COALESCE(password,''))>=8`
    );

    // 2. ref_code 信息
    const refRow = await queryOne<{ email:string; notes:string }>(
      `SELECT email, notes FROM accounts
       WHERE platform='outlook' AND (tags LIKE '%unitool_ref_master%' OR notes LIKE '%unitool_ref_code=%')
       ORDER BY updated_at DESC LIMIT 1`
    );
    let refCode = "", refMasterEmail = "";
    if (refRow?.notes) {
      refCode        = (refRow.notes.match(/unitool_ref_code=([a-z0-9]+)/) ?? [])[1] ?? "";
      refMasterEmail = refRow.email ?? "";
    }
    // refUsed: count ref_registered= occurrences in master account notes (authoritative)
    const refUsed = refRow?.notes
      ? (refRow.notes.match(/ref_registered=/g) ?? []).length
      : 0;

    // 3. proxy 池状态
    let pool = { total:0, live:0, dead:0, ssid_len:0 };
    try {
      const { get } = await import("http");
      const poolRaw = await new Promise<string>((resolve, reject) => {
        const r2 = get({ host:"127.0.0.1", port:8089, path:"/pool-status", timeout:3000 }, (r) => {
          let d = ""; r.on("data", (c:Buffer) => { d += c; }); r.on("end", () => resolve(d));
        });
        r2.on("error", reject);
      });
      const pd = JSON.parse(poolRaw) as { pool_size:number; live:number; accounts?:unknown[] };
      // ssid_len: 读取实际 ssid 文件字节数（所有文件固定 264 chars）
      let ssid_len = 0;
      try {
        const { readdirSync, readFileSync } = await import("fs");
        const ssidFiles = readdirSync("/data/unitool_ssids").filter((f:string) => f.endsWith(".txt"));
        if (ssidFiles.length > 0) {
          ssid_len = readFileSync(`/data/unitool_ssids/${ssidFiles[0]}`).length;
        }
      } catch {}
      pool = { total: pd.pool_size, live: pd.live, dead: pd.pool_size - pd.live, ssid_len };
    } catch {}

    // 4. 最近5条成功注册
    const recent = await query<{ id:number; email:string; notes:string; updated_at:string }>(
      `SELECT id, email, notes, updated_at FROM accounts
       WHERE platform='outlook' AND tags LIKE '%unitool_registered%'
       ORDER BY updated_at DESC LIMIT 5`
    );
    // ssid_len 权威源: unitool_ssids 表 (比 notes regex 准确)
    const ssidLenMap: Record<string, number> = {};
    if (recent.length > 0) {
      const emails = recent.map(r => r.email);
      try {
        const ssidRows = await query<{ source_email:string; ssid_len:string }>(
          `SELECT DISTINCT ON (source_email) source_email, LENGTH(ssid) as ssid_len
           FROM unitool_ssids WHERE source_email = ANY($1)
           ORDER BY source_email, collected_at DESC`,
          [emails]
        );
        for (const s of ssidRows) ssidLenMap[s.source_email] = Number(s.ssid_len);
      } catch {}
    }

    // 5. chain_v3 日志摘要
    let chainStatus = "unknown", chainLastRun = "", chainBrief = "";
    try {
      const { readFileSync } = await import("fs");
      const logLines = readFileSync("/tmp/unitool_chain_v3_out.log", "utf8").split("\n").filter(Boolean);
      const startLines = logLines.filter(l => l.includes("=== unitool_chain_v3 start ==="));
      if (startLines.length > 0) {
        const m = startLines[startLines.length - 1].match(/\[(\d{2}:\d{2}:\d{2})\]/);
        chainLastRun = m?.[1] ?? "";
      }
      const last20 = logLines.slice(-20);
      if (last20.some(l => l.includes("✅") && l.includes("ssid"))) chainStatus = "success";
      else if (last20.some(l => l.includes("sleep"))) chainStatus = "waiting";
      else if (last20.some(l => l.includes("[main]"))) chainStatus = "running";
      chainBrief = last20
        .filter(l => l.includes("[main]") || l.includes("✅") || l.includes("[ref]") || l.includes("[watermark]"))
        .slice(-4).map(l => l.replace(/^\[\d{2}:\d{2}:\d{2}\]\s*/, "")).join(" | ");
    } catch {}

    // 6. 最近24h 失败原因分布
    const failReasons = await query<{ reason:string; cnt:string }>(
      `SELECT
        CASE
          WHEN notes LIKE '%no_redirect_no_ssid%' THEN 'no_redirect_no_ssid'
          WHEN notes LIKE '%fill_failed%' THEN 'fill_failed'
          WHEN notes LIKE '%no_verify_email%' THEN 'no_verify_email'
          WHEN notes LIKE '%ref_reg_fail%' THEN 'ref_reg_fail'
          WHEN notes LIKE '%already_registered%' THEN 'already_registered'
          ELSE 'other'
        END as reason,
        COUNT(*) as cnt
       FROM accounts WHERE platform='outlook' AND tags LIKE '%unitool_fail%'
         AND updated_at > NOW()-INTERVAL '24 hours'
       GROUP BY 1 ORDER BY 2 DESC`
    );

    res.json({
      success: true,
      outlook: {
        fresh:      Number(ol?.fresh ?? 0),
        registered: Number(ol?.registered ?? 0),
        fail:       Number(ol?.fail ?? 0),
        processing: Number(ol?.processing ?? 0),
        total:      Number(ol?.total ?? 0),
      },
      ref: { master: refMasterEmail, ref_code: refCode, used: refUsed, limit: 10 },
      pool,
      recent: recent.map(r => {
        const m2 = r.notes?.match(/unitool_ssid=([a-f0-9]+)/);
        // 优先从 unitool_ssids 表取 ssid_len（权威），回退 notes regex
        const ssidLen = ssidLenMap[r.email] ?? (m2?.[1]?.length ?? 0);
        return {
          id:          r.id,
          email:       r.email,
          ssid_prefix: m2?.[1]?.slice(0, 16) ?? "",
          ssid_len:    ssidLen,
          updated_at:  r.updated_at,
        };
      }),
      chain:        { status: chainStatus, last_run: chainLastRun, brief: chainBrief },
      fail_reasons: failReasons.map(r => ({ reason: r.reason, count: Number(r.cnt) })),
      ts: new Date().toISOString(),
    });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

export default router;
