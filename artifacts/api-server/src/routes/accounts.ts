import { Router } from "express";
import { spawn } from "child_process";
import path from "path";

const router = Router();
const SCRIPTS_DIR = path.resolve(process.cwd(), "scripts");
const SIGNUP_SCRIPT = path.join(SCRIPTS_DIR, "replit_signup_v2.py");
const PYTHON = "/usr/bin/python3";

interface Job {
  id: string;
  status: "running" | "done" | "error";
  count: number;
  started: number;
  output: string[];
  results: unknown[];
}
const jobs = new Map<string, Job>();

// POST /api/signup — 触发 Replit 账号注册
router.post("/signup", (req, res) => {
  const count = Math.min(parseInt(String(req.body?.count ?? "1")), 5);
  const jobId = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);

  const job: Job = {
    id: jobId, status: "running", count,
    started: Date.now(), output: [], results: [],
  };
  jobs.set(jobId, job);

  const proc = spawn(PYTHON, [SIGNUP_SCRIPT, "--count", String(count)], {
    cwd: SCRIPTS_DIR,
    env: {
      ...process.env,
      GATEWAY_API: "http://localhost:8080/api/gateway",
      VPS_GATEWAY_URL: "http://45.205.27.69:8080/api/gateway",
    },
  });

  proc.stdout.on("data", (d: Buffer) => {
    const lines = d.toString().split("\n").filter(Boolean);
    job.output.push(...lines);
    for (const line of lines) {
      if (line.startsWith("{") || line.startsWith("[")) {
        try { job.results.push(JSON.parse(line)); } catch {}
      }
    }
  });
  proc.stderr.on("data", (d: Buffer) => {
    job.output.push("ERR: " + d.toString().trim());
  });
  proc.on("close", (code) => {
    job.status = code === 0 ? "done" : "error";
  });

  res.json({ jobId, count, status: "running" });
});

// GET /api/signup/status/:jobId
router.get("/signup/status/:jobId", (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) return res.status(404).json({ error: "job not found" });
  res.json({
    jobId: job.id, status: job.status, count: job.count,
    elapsed: Math.round((Date.now() - job.started) / 1000),
    lastLines: job.output.slice(-30),
    results: job.results,
  });
});

// DELETE /api/signup/:jobId
router.delete("/signup/:jobId", (req, res) => {
  jobs.delete(req.params.jobId);
  res.json({ ok: true });
});

// GET /api/replit-accounts — 列出已注册账号
router.get("/replit-accounts", async (req, res) => {
  const { Pool } = await import("pg");
  const pool = new Pool({ connectionString: process.env.DATABASE_URL });
  try {
    const { rows } = await pool.query(`
      SELECT id, platform, email, username, status, notes, tags, created_at, updated_at
      FROM accounts WHERE platform='replit'
      ORDER BY created_at DESC LIMIT 100
    `);
    res.json({ accounts: rows, total: rows.length });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    // 表不存在时友好返回
    res.json({ accounts: [], total: 0, note: msg });
  } finally {
    await pool.end().catch(() => {});
  }
});

export default router;
