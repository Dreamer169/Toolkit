import { Router, type IRouter, type Request, type Response } from "express";
import { spawn } from "node:child_process";
import path from "node:path";
import { logger } from "../lib/logger";

const router: IRouter = Router();

// 复用 VPS 上 Python 工具集合
const PY_DIR = "/root/Toolkit/artifacts/api-server";
const PY_BIN = process.env["PYTHON_BIN"] || "python3";

function runPy(
  script: string,
  payload: Record<string, unknown>,
  timeoutMs: number,
): Promise<{ ok: boolean; data?: unknown; raw?: string; error?: string }> {
  return new Promise((resolve) => {
    const scriptPath = path.join(PY_DIR, script);
    const proc = spawn(PY_BIN, [scriptPath, JSON.stringify(payload)], {
      cwd: PY_DIR,
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
    });

    let out = "";
    let err = "";
    let done = false;

    const timer = setTimeout(() => {
      if (done) return;
      done = true;
      try { proc.kill("SIGKILL"); } catch { /* noop */ }
      resolve({ ok: false, error: `timeout after ${timeoutMs}ms`, raw: out + err });
    }, timeoutMs);

    proc.stdout.on("data", (b: Buffer) => { out += b.toString(); });
    proc.stderr.on("data", (b: Buffer) => { err += b.toString(); });

    proc.on("close", (code) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      // python 脚本约定: 最后一行是 JSON 结果, 前面是日志
      const lines = out.trim().split(/\r?\n/);
      for (let i = lines.length - 1; i >= 0; i--) {
        const line = lines[i]?.trim();
        if (!line) continue;
        if (line.startsWith("{") && line.endsWith("}")) {
          try {
            const parsed = JSON.parse(line);
            return resolve({ ok: true, data: parsed, raw: out });
          } catch { /* keep scanning earlier lines */ }
        }
      }
      resolve({
        ok: false,
        error: `exit ${code}, no JSON in stdout`,
        raw: (out + "\n----STDERR----\n" + err).slice(-4000),
      });
    });

    proc.on("error", (e) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve({ ok: false, error: `spawn failed: ${(e as Error).message}` });
    });
  });
}

// POST /replit/register
//   body: { email, password, outlook_refresh_token?, proxy?, username? }
router.post("/replit/register", async (req: Request, res: Response) => {
  const body = (req.body || {}) as Record<string, unknown>;
  if (!body["email"] || !body["password"]) {
    return res.status(400).json({ ok: false, error: "email/password required" });
  }
  const timeout = Number(body["timeout_ms"] || 300_000);
  logger.info({ email: body["email"] }, "[replit/register] dispatch");
  const r = await runPy("replit_register.py", body, timeout);
  if (!r.ok) {
    logger.warn({ err: r.error }, "[replit/register] failed");
    return res.status(500).json({ ok: false, error: r.error, raw: r.raw });
  }
  return res.json(r.data);
});

// POST /replit/login
//   body: { username?, email?, password?, force_password? }
router.post("/replit/login", async (req: Request, res: Response) => {
  const body = (req.body || {}) as Record<string, unknown>;
  if (!body["username"] && !body["email"]) {
    return res.status(400).json({ ok: false, error: "username or email required" });
  }
  const timeout = Number(body["timeout_ms"] || 120_000);
  logger.info({ username: body["username"] || body["email"] }, "[replit/login] dispatch");
  const r = await runPy("replit_login.py", body, timeout);
  if (!r.ok) {
    return res.status(500).json({ ok: false, error: r.error, raw: r.raw });
  }
  return res.json(r.data);
});

// GET /replit/sessions  — list saved storage_state files
router.get("/replit/sessions", async (_req: Request, res: Response) => {
  const fs = await import("node:fs/promises");
  const dir = "/root/Toolkit/.state/replit";
  try {
    const files = await fs.readdir(dir);
    const items = await Promise.all(
      files.filter((f) => f.endsWith(".json")).map(async (f) => {
        const stat = await fs.stat(path.join(dir, f));
        return {
          username: f.replace(/\.json$/, ""),
          mtime: stat.mtimeMs,
          size: stat.size,
        };
      }),
    );
    return res.json({ ok: true, count: items.length, sessions: items });
  } catch (e) {
    return res.json({ ok: true, count: 0, sessions: [], note: (e as Error).message });
  }
});

export default router;
