import { Router } from "express";
import { query, queryOne, execute } from "../db.js";

const router = Router();

// ─── 账号 CRUD ────────────────────────────────────────────
router.get("/data/accounts", async (req, res) => {
  try {
    const { platform, status, search } = req.query as Record<string, string>;
    let sql = "SELECT * FROM accounts WHERE 1=1";
    const params: unknown[] = [];
    if (platform) { params.push(platform); sql += ` AND platform=$${params.length}`; }
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
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING *`,
      [platform, email, password, username || null, token || null, refresh_token || null, status, notes || null, tags || null]
    );
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

// 批量导入账号（格式: email----password 每行一个）
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

// ─── 身份信息 CRUD ───────────────────────────────────────
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

// ─── 临时邮箱 CRUD ───────────────────────────────────────
router.get("/data/emails", async (req, res) => {
  try {
    const rows = await query("SELECT * FROM temp_emails ORDER BY created_at DESC LIMIT 500");
    res.json({ success: true, data: rows, total: rows.length });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

router.post("/data/emails", async (req, res) => {
  try {
    const { address, password, provider = "mailtm", token, status = "active", notes } = req.body;
    if (!address || !password) { res.status(400).json({ success: false, error: "address 和 password 必填" }); return; }
    const row = await queryOne(
      `INSERT INTO temp_emails (address,password,provider,token,status,notes)
       VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (address) DO UPDATE SET password=$2,token=$4,status=$5 RETURNING *`,
      [address, password, provider, token || null, status, notes || null]
    );
    res.json({ success: true, data: row });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

router.delete("/data/emails/:id", async (req, res) => {
  try {
    await execute("DELETE FROM temp_emails WHERE id=$1", [req.params.id]);
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

// ─── 统计 ───────────────────────────────────────────────
router.get("/data/stats", async (req, res) => {
  try {
    const [accts, idents, emails] = await Promise.all([
      queryOne<{ total: string; active: string }>(`SELECT COUNT(*) as total, COUNT(*) FILTER (WHERE status='active') as active FROM accounts`),
      queryOne<{ total: string }>(`SELECT COUNT(*) as total FROM identities`),
      queryOne<{ total: string }>(`SELECT COUNT(*) as total FROM temp_emails`),
    ]);
    const byPlatform = await query<{ platform: string; count: string }>(
      `SELECT platform, COUNT(*) as count FROM accounts GROUP BY platform ORDER BY count DESC`
    );
    res.json({
      success: true,
      accounts:   { total: Number(accts?.total ?? 0), active: Number(accts?.active ?? 0) },
      identities: { total: Number(idents?.total ?? 0) },
      emails:     { total: Number(emails?.total ?? 0) },
      byPlatform: byPlatform.map(r => ({ platform: r.platform, count: Number(r.count) })),
    });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

export default router;
