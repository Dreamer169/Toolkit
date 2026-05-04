import { Router } from "express";
import { execFile } from "child_process";
import { promisify } from "util";
import path from "path";
import { fileURLToPath } from "url";

const router = Router();
const execFileAsync = promisify(execFile);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DB_PATH = "/data/Toolkit/artifacts/api-server/data.db";

// ── SQLite 查询工具（用 python3 执行，无需额外依赖）──────────────────────────
async function sqlQuery<T = Record<string, unknown>>(
  sql: string,
  params: (string | number | null)[] = []
): Promise<T[]> {
  const script = `
import sqlite3, json, sys
db = sqlite3.connect(${JSON.stringify(DB_PATH)}, timeout=5)
db.row_factory = sqlite3.Row
params = json.loads(sys.argv[1])
rows = db.execute(${JSON.stringify(sql)}, params).fetchall()
print(json.dumps([dict(r) for r in rows]))
`;
  const { stdout } = await execFileAsync(
    "python3", ["-c", script, JSON.stringify(params)],
    { timeout: 8000 }
  );
  return JSON.parse(stdout.trim()) as T[];
}

async function sqlOne<T = Record<string, unknown>>(
  sql: string,
  params: (string | number | null)[] = []
): Promise<T | null> {
  const rows = await sqlQuery<T>(sql, params);
  return rows[0] ?? null;
}

// ── GET /api/unified-db/stats ─────────────────────────────────────────────────
router.get("/unified-db/stats", async (_req, res) => {
  try {
    const [emails, ai, profiles] = await Promise.all([
      sqlOne<{ total: number; active: number; suspended: number; today: number }>(
        `SELECT
          COUNT(*) as total,
          SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) as active,
          SUM(CASE WHEN status IN ('suspended','banned') THEN 1 ELSE 0 END) as suspended,
          SUM(CASE WHEN date(created_at)=date('now') THEN 1 ELSE 0 END) as today
        FROM emails`
      ),
      sqlOne<{ total: number; active: number; validated: number; today: number }>(
        `SELECT
          COUNT(*) as total,
          SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) as active,
          SUM(CASE WHEN validated=1 THEN 1 ELSE 0 END) as validated,
          SUM(CASE WHEN date(created_at)=date('now') THEN 1 ELSE 0 END) as today
        FROM ai_accounts`
      ),
      sqlOne<{ total: number; today: number }>(
        `SELECT COUNT(*) as total,
          SUM(CASE WHEN date(created_at)=date('now') THEN 1 ELSE 0 END) as today
        FROM profiles`
      ),
    ]);

    // 按服务分组统计
    const byService = await sqlQuery<{ service: string; count: number }>(
      `SELECT service, COUNT(*) as count FROM ai_accounts GROUP BY service ORDER BY count DESC`
    );
    const byPlatform = await sqlQuery<{ platform: string; count: number }>(
      `SELECT platform, COUNT(*) as count FROM emails GROUP BY platform ORDER BY count DESC`
    );

    res.json({
      success: true,
      stats: {
        emails:     emails,
        ai_accounts: ai,
        profiles:   profiles,
        by_service: byService,
        by_platform: byPlatform,
      },
      generated_at: new Date().toISOString(),
    });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// ── GET /api/unified-db/recent?limit=20&table=emails|ai_accounts|profiles ─────
router.get("/unified-db/recent", async (req, res) => {
  const table  = String(req.query.table  || "ai_accounts");
  const limit  = Math.min(Number(req.query.limit  || 20), 100);
  const offset = Number(req.query.offset || 0);
  const service = req.query.service ? String(req.query.service) : null;

  const VALID_TABLES = ["emails", "ai_accounts", "profiles"];
  if (!VALID_TABLES.includes(table)) {
    res.status(400).json({ success: false, error: "invalid table" });
    return;
  }

  try {
    let where = service ? `WHERE service=? OR platform=?` : "";
    const params: (string | number | null)[] = service
      ? [service, service, limit, offset]
      : [limit, offset];

    const rows = await sqlQuery(
      `SELECT * FROM ${table} ${where} ORDER BY created_at DESC LIMIT ? OFFSET ?`,
      params
    );
    const total = await sqlOne<{ n: number }>(
      `SELECT COUNT(*) as n FROM ${table} ${where}`,
      service ? [service, service] : []
    );
    res.json({ success: true, table, rows, total: total?.n ?? 0, limit, offset });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// ── GET /api/unified-db/search?q=xxx&table=... ───────────────────────────────
router.get("/unified-db/search", async (req, res) => {
  const q     = String(req.query.q || "").trim();
  const table = String(req.query.table || "emails");
  const limit = Math.min(Number(req.query.limit || 20), 100);

  const VALID_TABLES = ["emails", "ai_accounts", "profiles"];
  if (!VALID_TABLES.includes(table) || !q) {
    res.status(400).json({ success: false, error: "invalid table or empty q" });
    return;
  }

  const like = `%${q}%`;
  try {
    let sql = "";
    let params: (string | number | null)[];
    if (table === "emails") {
      sql = `SELECT * FROM emails WHERE email LIKE ? OR platform LIKE ? ORDER BY created_at DESC LIMIT ?`;
      params = [like, like, limit];
    } else if (table === "ai_accounts") {
      sql = `SELECT * FROM ai_accounts WHERE service LIKE ? OR email LIKE ? OR username LIKE ? OR api_key LIKE ? ORDER BY created_at DESC LIMIT ?`;
      params = [like, like, like, like, limit];
    } else {
      sql = `SELECT * FROM profiles WHERE label LIKE ? OR email LIKE ? OR service LIKE ? ORDER BY created_at DESC LIMIT ?`;
      params = [like, like, like, limit];
    }
    const rows = await sqlQuery(sql, params);
    res.json({ success: true, table, q, rows });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// ── POST /api/unified-db/write ────────────────────────────────────────────────
// 通用写入（透传给 sync_unified_db.py）
router.post("/unified-db/write", async (req, res) => {
  const payload = req.body as Record<string, unknown>;
  if (!payload?.action) {
    res.status(400).json({ success: false, error: "action required" });
    return;
  }
  try {
    const { spawn } = await import("child_process");
    const cp = spawn("python3", ["/data/Toolkit/artifacts/api-server/sync_unified_db.py"]);
    let out = "";
    cp.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    cp.stdin.write(JSON.stringify(payload));
    cp.stdin.end();
    await new Promise<void>((resolve, reject) => {
      cp.on("close", (code) => code === 0 ? resolve() : reject(new Error(`exit ${code}`)));
    });
    res.json({ success: true, result: JSON.parse(out.trim() || "{}") });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

export default router;
