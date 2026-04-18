import { Router } from "express";
import { spawn } from "child_process";
import path from "path";

const router = Router();
const API_DIR     = path.resolve(process.cwd(), "artifacts/api-server");
const SCRIPTS_DIR = path.resolve(process.cwd(), "scripts");
const PYTHON      = "/usr/bin/python3";
const VPS_GATEWAY = process.env.VPS_GATEWAY_URL || "http://45.205.27.69:8080/api/gateway";
const XRAY_PORTS  = Array.from({ length: 26 }, (_, i) => 10820 + i);

interface Job {
  id: string;
  status: "running" | "done" | "error";
  started: number;
  logs: string[];
  result: Record<string, unknown> | null;
}
const jobs = new Map<string, Job>();

function makeJobId() { return `rpl_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`; }
function pick<T>(arr: T[]): T { return arr[Math.floor(Math.random() * arr.length)]; }

async function localPost(p: string, body: unknown) {
  const r = await fetch(`http://localhost:8080${p}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json() as Promise<Record<string, unknown>>;
}

function runPython(script: string, arg: unknown, timeoutMs = 130_000): Promise<{
  ok: boolean; raw: string; parsed: Record<string, unknown>;
}> {
  return new Promise((resolve) => {
    const child = spawn(PYTHON, [script, JSON.stringify(arg)], {
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
    });
    let out = "";
    child.stdout.on("data", (d: Buffer) => { out += d.toString(); process.stdout.write(d); });
    child.stderr.on("data", (d: Buffer) => { process.stderr.write(d); });
    const timer = setTimeout(() => { child.kill(); resolve({ ok: false, raw: out, parsed: { error: "timeout" } }); }, timeoutMs);
    child.on("close", () => {
      clearTimeout(timer);
      const last = out.trim().split("\n").at(-1) ?? "{}";
      try { resolve({ ok: true, raw: out, parsed: JSON.parse(last) }); }
      catch { resolve({ ok: false, raw: out, parsed: { error: last.slice(0, 300) } }); }
    });
    child.on("error", (e) => { clearTimeout(timer); resolve({ ok: false, raw: "", parsed: { error: e.message } }); });
  });
}

// ── POST /api/replit/register ─────────────────────────────────────────────────
router.post("/replit/register", (req, res) => {
  const count    = Math.min(parseInt(String(req.body?.count ?? "1")), 3);
  const headless = req.body?.headless !== false;
  const jobId    = makeJobId();
  const job: Job = { id: jobId, status: "running", started: Date.now(), logs: [], result: null };
  jobs.set(jobId, job);

  function log(msg: string) {
    const line = `[${new Date().toISOString().slice(11, 19)}] ${msg}`;
    job.logs.push(line);
    console.log(`[replit-reg][${jobId}] ${msg}`);
  }

  (async () => {
    const results: unknown[] = [];

    for (let i = 0; i < count; i++) {
      log(`\n== Account ${i + 1}/${count} ==`);

      try {
        // ── Step 1: 取最多 5 个可用 Outlook 账号候选 ─────────────────────
        log("Step1: Fetching Outlook candidates from DB...");
        const { query: dbQ, execute: dbE } = await import("../db.js");

        const candidates = await dbQ<{
          id: number; email: string; password: string; username: string;
          token: string | null; refresh_token: string | null;
        }>(
          `SELECT id, email, password, username, token, refresh_token
           FROM accounts
           WHERE platform = 'outlook'
             AND status = 'active'
             AND refresh_token IS NOT NULL
             AND COALESCE(tags, '') NOT LIKE '%replit_used%'
           ORDER BY RANDOM()
           LIMIT 5`
        );

        if (!candidates.length) {
          log("No available Outlook accounts — run FullWorkflow to generate more");
          results.push({ ok: false, error: "No Outlook accounts available" });
          continue;
        }

        // ── Step 2: 逐个 Outlook 账号尝试注册 ────────────────────────────
        let accountDone = false;

        for (const outlook of candidates) {
          const ADJS = ["cool","fast","bright","swift","calm","bold","clear","keen"];
          const NONS = ["bear","fox","wolf","hawk","dove","lion","star","moon"];
          const username = `${pick(ADJS)}${pick(NONS)}${Math.floor(Math.random() * 900) + 100}`;
          const password = outlook.password || `Rpl${Math.random().toString(36).slice(2, 8)}!A1`;

          log(`  Trying Outlook id=${outlook.id} email=${outlook.email} => Replit user=${username}`);

          // ── Step 2a: 最多 4 次不同代理端口重试 ───────────────────────
          const regScript = path.join(API_DIR, "replit_register.py");
          let regOk  = false;
          let exitIp = "";
          let lastErr = "";

          for (let attempt = 1; attempt <= 4; attempt++) {
            const tryPort = pick(XRAY_PORTS);
            log(`    Attempt ${attempt}/4 via SOCKS5:${tryPort}`);

            const { parsed } = await runPython(regScript, {
              email: outlook.email,
              username,
              password,
              proxy: `socks5://127.0.0.1:${tryPort}`,
              headless,
              max_wait: 90,
            });

            exitIp  = String(parsed.exit_ip ?? "");
            lastErr = String(parsed.error ?? "");

            if (parsed.ok) {
              log(`    ✅ Registered! phase=${parsed.phase} exit_ip=${exitIp}`);
              regOk = true;
              break;
            }

            log(`    ✗ Attempt ${attempt}: ${lastErr.slice(0, 100)}`);

            // 邮箱已被用 → 标记并换下一个 Outlook 账号
            if (lastErr.toLowerCase().includes("already") && lastErr.toLowerCase().includes("use")) {
              log(`    Email already on Replit → marking replit_used`);
              await dbE(
                "UPDATE accounts SET tags = COALESCE(tags || ',', '') || 'replit_used', updated_at = NOW() WHERE id = $1",
                [outlook.id]
              ).catch(() => {});
              break; // 跳出 attempt 循环，尝试下一个 Outlook
            }

            // 可重试错误：Turnstile("signup"/"moment")、integrity、timeout → 换端口继续
            const retryable =
              lastErr.includes("integrity") ||
              lastErr.includes("timeout")   ||
              lastErr.includes("Timeout")   ||
              lastErr.includes("signup")    ||
              lastErr.includes("moment")    ||
              lastErr.includes("Turnstile") || lastErr.includes("cf_ip_banned");
            if (!retryable) break;

            await new Promise(r => setTimeout(r, 5000 + attempt * 2000));
          }

          if (!regOk) continue; // 换下一个 Outlook 账号

          // ── Step 3: 等待 Replit 发验证邮件 → 通过 Graph API 点击 ─────
          log("  Step3: Waiting for Replit verification email...");
          await new Promise(r => setTimeout(r, 12000));

          let verified = false;
          for (let t = 0; t < 15; t++) {
            log(`    Poll ${t + 1}/15 (accountId=${outlook.id})...`);
            const vr = await localPost("/api/tools/outlook/click-verify-link", {
              accountId: outlook.id,
            }) as { success?: boolean; final_url?: string; error?: string };

            if (vr.success) {
              log(`    ✅ Verified! => ${vr.final_url?.slice(0, 70)}`);
              verified = true;
              break;
            }
            log(`    Waiting... (${String(vr.error ?? "no link yet").slice(0, 60)})`);
            await new Promise(r => setTimeout(r, 15000));
          }
          if (!verified) log("  Verification timed out (account may still be usable)");

          // ── Step 4: 写入 DB ──────────────────────────────────────────
          log("  Step4: Saving to DB...");
          await dbE(
            `INSERT INTO accounts (platform, email, password, username, status, notes, tags, exit_ip, proxy_port)
             VALUES ('replit', $1, $2, $3, $4, $5, 'replit,subnode', $6, $7)
             ON CONFLICT (platform, email) DO UPDATE
               SET status = EXCLUDED.status, username = EXCLUDED.username, updated_at = NOW()`,
            [outlook.email, password, username,
             verified ? "registered" : "unverified",
             outlook.email, exitIp, pick(XRAY_PORTS)]
          ).catch(e => log(`  DB warn: ${e}`));

          // 标记 Outlook 已使用
          await dbE(
            "UPDATE accounts SET tags = COALESCE(tags || ',', '') || 'replit_used', updated_at = NOW() WHERE id = $1",
            [outlook.id]
          ).catch(() => {});

          log(`  ✅ Account ${i + 1} done: ${outlook.email} verified=${verified}`);
          results.push({ ok: true, email: outlook.email, username, verified, exit_ip: exitIp });
          accountDone = true;
          break; // 成功，退出 Outlook 候选循环
        }

        if (!accountDone) {
          log(`  All ${candidates.length} Outlook candidates failed for account ${i + 1}`);
          results.push({ ok: false, error: `All ${candidates.length} candidates failed (already-used or Turnstile)` });
        }

      } catch (err) {
        log(`Unexpected error: ${String(err).slice(0, 200)}`);
        results.push({ ok: false, error: String(err) });
      }

      if (i < count - 1) await new Promise(r => setTimeout(r, 10000));
    }

    const okCount = results.filter((r: unknown) => (r as Record<string, unknown>).ok).length;
    job.status = okCount > 0 ? "done" : "error";
    job.result = { results, summary: `${okCount}/${count} succeeded` };
    log(`\nAll done: ${okCount}/${count} succeeded`);
  })().catch(err => {
    job.status = "error";
    job.logs.push(`FATAL: ${String(err)}`);
  });

  res.json({ success: true, jobId, message: `Replit registration started (${count} accounts)` });
});

// ── GET /api/replit/register/:jobId ──────────────────────────────────────────
router.get("/replit/register/:jobId", (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) return res.status(404).json({ success: false, error: "job not found" });
  res.json({
    jobId: job.id,
    status: job.status,
    elapsed: Math.round((Date.now() - job.started) / 1000),
    logs: job.logs,
    result: job.result,
  });
});

// ── POST /api/replit/gateway-register ─────────────────────────────────────────
router.post("/replit/gateway-register", async (req, res) => {
  const { gatewayUrl, name } = req.body as { gatewayUrl: string; name?: string };
  if (!gatewayUrl) return res.status(400).json({ success: false, error: "missing gatewayUrl" });
  try {
    const r = await localPost("/api/gateway/self-register", { gatewayUrl, name });
    res.json(r);
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

// ── GET /api/replit/accounts ──────────────────────────────────────────────────
router.get("/replit/accounts", async (_req, res) => {
  try {
    const { query: dbQ } = await import("../db.js");
    const rows = await dbQ(
      `SELECT id, email, username, status, exit_ip, proxy_port, created_at
       FROM accounts WHERE platform = 'replit' ORDER BY created_at DESC LIMIT 100`
    );
    res.json({ accounts: rows, total: rows.length });
  } catch (e) { res.json({ accounts: [], total: 0, error: String(e) }); }
});

// ── GET /api/replit-accounts (旧路由兼容) ────────────────────────────────────
router.get("/replit-accounts", async (_req, res) => {
  try {
    const { query: dbQ } = await import("../db.js");
    const rows = await dbQ(
      `SELECT id, email, username, status, created_at FROM accounts WHERE platform = 'replit' ORDER BY created_at DESC LIMIT 100`
    );
    res.json({ accounts: rows, total: rows.length });
  } catch (e) { res.json({ accounts: [], total: 0, error: String(e) }); }
});

// ── POST /api/signup (旧接口) ─────────────────────────────────────────────────
router.post("/signup", (req, res) => {
  const count = Math.min(parseInt(String(req.body?.count ?? "1")), 5);
  const jobId = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  const job: Job = { id: jobId, status: "running", started: Date.now(), logs: [], result: null };
  jobs.set(jobId, job);
  const proc = spawn(PYTHON, [
    path.join(SCRIPTS_DIR, "replit_signup_v2.py"),
    "--count", String(count),
  ], {
    cwd: SCRIPTS_DIR,
    env: { ...process.env, GATEWAY_API: "http://localhost:8080/api/gateway", VPS_GATEWAY_URL },
  });
  proc.stdout.on("data", (d: Buffer) => { job.logs.push(...d.toString().split("\n").filter(Boolean)); });
  proc.stderr.on("data", (d: Buffer) => { job.logs.push("ERR: " + d.toString().trim()); });
  proc.on("close", (code) => { job.status = code === 0 ? "done" : "error"; });
  res.json({ jobId, count, status: "running" });
});

router.get("/signup/status/:jobId", (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) return res.status(404).json({ error: "job not found" });
  res.json({
    jobId: job.id, status: job.status,
    elapsed: Math.round((Date.now() - job.started) / 1000),
    lastLines: job.logs.slice(-30),
    result: job.result,
  });
});

export default router;
