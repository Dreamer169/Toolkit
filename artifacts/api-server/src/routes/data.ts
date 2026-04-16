import { Router } from "express";
import { query, queryOne, execute } from "../db.js";

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
    const rows = await query(sql, params);
    res.json({ success: true, data: rows, total: rows.length });
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
    const rows = await query(sql, params);
    res.json({ success: true, data: rows, total: rows.length });
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
    const rows = await query(sql, params);
    res.json({ success: true, data: rows, total: rows.length });
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
router.get("/data/proxies", async (req, res) => {
  try {
    const rows = await query("SELECT id, formatted, host, port, status, used_count, last_used, created_at FROM proxies ORDER BY used_count ASC, id ASC LIMIT 200");
    res.json({ success: true, data: rows, total: rows.length });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

// 从代理池取一个最少使用的代理（供注册时自动选取）
router.get("/data/proxies/pick", async (req, res) => {
  try {
    const row = await queryOne<{ id: number; formatted: string }>(
      "SELECT id, formatted FROM proxies WHERE status != 'banned' ORDER BY used_count ASC, RANDOM() LIMIT 1"
    );
    if (!row) { res.json({ success: false, error: "代理池为空，请先导入代理" }); return; }
    await execute(
      "UPDATE proxies SET used_count = used_count + 1, last_used = NOW(), status = 'active' WHERE id = $1",
      [row.id]
    );
    res.json({ success: true, proxy: row.formatted, id: row.id });
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
          `INSERT INTO proxies (raw, formatted, host, port, username, password) VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (formatted) DO NOTHING`,
          [raw, formatted, host, port, user, pass]
        );
        inserted++;
      } catch {}
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
    res.json({
      success: true,
      accounts:    { total: Number(accts?.total ?? 0), active: Number(accts?.active ?? 0) },
      archives:    { total: Number(archives?.total ?? 0) },
      emails:      { total: Number(tempEmails?.total ?? 0) },
      long_term:   { total: Number(longTermEmails?.total ?? 0) },
      proxies:     { idle: Number(proxyStat?.idle ?? 0), active: Number(proxyStat?.active ?? 0), banned: Number(proxyStat?.banned ?? 0) },
      byPlatform:  byPlatform.map(r => ({ platform: r.platform, count: Number(r.count) })),
    });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

// ─── 打码服务配置 ────────────────────────────────────────────────────────────
router.get("/data/captcha-config", async (_req, res) => {
  try {
    const row = await queryOne<{ value: string }>(
      "SELECT value FROM configs WHERE key = 'captcha_config'"
    );
    const cfg = row ? JSON.parse(row.value) : { service: "", apiKey: "" };
    const masked = cfg.apiKey ? cfg.apiKey.slice(0, 6) + "****" + cfg.apiKey.slice(-4) : "";
    res.json({ success: true, service: cfg.service || "", apiKeyMasked: masked, hasKey: !!cfg.apiKey });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

router.post("/data/captcha-config", async (req, res) => {
  const { service = "", apiKey = "" } = req.body as { service?: string; apiKey?: string };
  try {
    let finalKey = apiKey;
    if (apiKey.includes("****")) {
      const existing = await queryOne<{ value: string }>(
        "SELECT value FROM configs WHERE key = 'captcha_config'"
      );
      if (existing) finalKey = (JSON.parse(existing.value) as { apiKey?: string }).apiKey || "";
    }
    const cfg = JSON.stringify({ service, apiKey: finalKey });
    await execute(
      `INSERT INTO configs(key, value) VALUES('captcha_config', $1)
       ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value`,
      [cfg]
    );
    res.json({ success: true });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

export default router;
