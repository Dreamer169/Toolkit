import { Router } from "express";
import { spawn } from "child_process";
import path from "path";

const router = Router();
const WORKSPACE_DIR = process.cwd().endsWith("/artifacts/api-server") ? path.resolve(process.cwd(), "../..") : process.cwd();
const API_DIR = path.resolve(WORKSPACE_DIR, "artifacts/api-server");
const SCRIPTS_DIR = path.resolve(WORKSPACE_DIR, "scripts");
const PYTHON = process.env.PYTHON_BIN || "/usr/bin/python3";
const LOCAL_API_BASE = (process.env.LOCAL_API_BASE_URL || "http://127.0.0.1:" + (process.env.PORT || "8080")).replace(/\/$/, "");
const VPS_GATEWAY = process.env.VPS_GATEWAY_URL || "http://45.205.27.69:8080/api/gateway";
const XRAY_PORTS  = Array.from({ length: 26 }, (_, i) => 10820 + i);
// Dead ports detected via connectivity scan (10827-10829, 10834-10841 offline)
const DEAD_PORTS  = new Set([10827, 10828, 10829, 10834, 10835, 10836, 10837, 10838, 10839, 10840, 10841]);

// Outlook OAuth (Thunderbird client_id)
const OUTLOOK_CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753";
const OUTLOOK_SCOPE     = "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/User.Read offline_access";
// CF-banned port cooldown: port → timestamp when cooldown expires (5 min)
const cfBannedUntil = new Map<number, number>();
// Port reputation: last time port returned a real form response (not cf_ban/captcha_invalid)
const portLastGood = new Map<number, number>();

// ── 启动时从 xray.json 建立 port → CF IP 映射表 ─────────────────────────────
const xrayPortCfIp = new Map<number, string>();
(async () => {
  try {
    const { readFileSync, existsSync } = await import("fs");
    const candidates = [
      path.join(WORKSPACE_DIR, "xray.json"),
      "/root/Toolkit/xray.json",
    ];
    const xrayPath = candidates.find(existsSync);
    if (!xrayPath) return;
    const xray = JSON.parse(readFileSync(xrayPath, "utf8")) as {
      inbounds:  Array<{ tag: string; port: number }>;
      outbounds: Array<{ tag: string; settings?: { vnext?: Array<{ address: string }> } }>;
      routing:   { rules: Array<{ inboundTag?: string[]; outboundTag?: string }> };
    };
    const obMap = new Map<string, string>(); // outbound tag → CF IP
    for (const ob of xray.outbounds ?? []) {
      const ip = ob.settings?.vnext?.[0]?.address;
      if (ip) obMap.set(ob.tag, ip);
    }
    for (const rule of xray.routing?.rules ?? []) {
      if (!rule.inboundTag || !rule.outboundTag) continue;
      const cfIp = obMap.get(rule.outboundTag);
      if (!cfIp) continue;
      for (const tag of rule.inboundTag) {
        const m = tag.match(/in-socks-(\d+)/);
        if (m) xrayPortCfIp.set(10820 + Number(m[1]), cfIp);
      }
    }
    console.log(`[accounts] xray port→CF IP map: ${xrayPortCfIp.size} entries`);
  } catch { /* 静默 */ }
})();

// 从 CF IP 池取新 IP 替换 xray.json 中被封禁的 IP，然后 reload xray（fire-and-forget）
const ROTATE_SCRIPT = [
  path.join(WORKSPACE_DIR, "artifacts/api-server/rotate_xray_ip.py"),
  "/root/Toolkit/artifacts/api-server/rotate_xray_ip.py",
].find((p) => { try { require("fs").accessSync(p); return true; } catch { return false; } }) ?? "";

function rotateCfIpInXray(bannedIp: string) {
  if (!ROTATE_SCRIPT) { console.warn("[cf-rotate] rotate_xray_ip.py 未找到"); return; }
  try {
    const { spawnSync } = require("child_process");
    const r = spawnSync("python3", [ROTATE_SCRIPT, "--banned-ip", bannedIp],
      { timeout: 12000, encoding: "utf8" });
    const result = r.stdout ? JSON.parse(r.stdout) : {};
    if (result.success) {
      console.log(`[cf-rotate] ${bannedIp} → ${result.new_ip}  outbounds=${result.changed_outbounds} reload=${result.reload}`);
      // xray 已 reload，重建 port→IP 映射
      rebuildXrayPortMap().catch(() => {});
    } else {
      console.warn(`[cf-rotate] 失败: ${result.error}`);
    }
  } catch (e) { console.warn("[cf-rotate] exception:", e); }
}

async function rebuildXrayPortMap() {
  try {
    const { readFileSync, existsSync } = await import("fs");
    const candidates = [
      path.join(WORKSPACE_DIR, "xray.json"),
      "/root/Toolkit/xray.json",
    ];
    const xrayPath = candidates.find(existsSync);
    if (!xrayPath) return;
    const xray = JSON.parse(readFileSync(xrayPath, "utf8")) as {
      inbounds:  Array<{ tag: string; port: number }>;
      outbounds: Array<{ tag: string; settings?: { vnext?: Array<{ address: string }> } }>;
      routing:   { rules: Array<{ inboundTag?: string[]; outboundTag?: string }> };
    };
    const obMap = new Map<string, string>();
    for (const ob of xray.outbounds ?? []) {
      const ip = ob.settings?.vnext?.[0]?.address;
      if (ip) obMap.set(ob.tag, ip);
    }
    xrayPortCfIp.clear();
    for (const rule of xray.routing?.rules ?? []) {
      if (!rule.inboundTag || !rule.outboundTag) continue;
      const cfIp = obMap.get(rule.outboundTag);
      if (!cfIp) continue;
      for (const tag of rule.inboundTag) {
        const m = tag.match(/in-socks-(\d+)/);
        if (m) xrayPortCfIp.set(10820 + Number(m[1]), cfIp);
      }
    }
    console.log(`[cf-rotate] xray port→IP 映射已重建: ${xrayPortCfIp.size} 条`);
  } catch { /* 静默 */ }
}
function availablePorts(): number[] {
  const now = Date.now();
  return XRAY_PORTS.filter(p => !DEAD_PORTS.has(p) && (cfBannedUntil.get(p) ?? 0) < now);
}
function sortedByReputation(ports: number[]): number[] {
  // Good ports (recently had form response) first, then others
  const good = ports.filter(p => portLastGood.has(p)).sort((a, b) => (portLastGood.get(b)!) - (portLastGood.get(a)!));
  const other = ports.filter(p => !portLastGood.has(p));
  return [...shuffled(good), ...shuffled(other)];
}
function shuffled(arr: number[]): number[] { return [...arr].sort(() => Math.random() - 0.5); }

const MIN_REPLIT_POOL = 5; // 账号池最低水位，低于此值自动触发补充

let autoRefillRunning = false;

// 记录 XRAY CF IP 代理使用情况到 proxies 表（used_count + last_used）
async function recordXrayProxyUsage(port: number, dbE: (sql: string, params?: unknown[]) => Promise<unknown>) {
  try {
    const cfIp = xrayPortCfIp.get(port) ?? "127.0.0.1";
    const formatted = `socks5://127.0.0.1:${port}`;
    await dbE(
      `INSERT INTO proxies (formatted, host, port, status, used_count, last_used)
       VALUES ($1, $2, $3, 'active', 1, NOW())
       ON CONFLICT (formatted) DO UPDATE
         SET used_count = proxies.used_count + 1,
             host       = EXCLUDED.host,
             last_used  = NOW()`,
      [formatted, cfIp, port]
    );
  } catch { /* 静默，不影响主流程 */ }
}

// 检查 replit 账号池水位，若不足则自动触发补充注册
async function checkAndRefillReplitPool(
  dbQ: (sql: string, params?: unknown[]) => Promise<Array<Record<string,unknown>>>,
  log: (s: string) => void
) {
  if (autoRefillRunning) return;
  try {
    const rows = await dbQ(
      "SELECT COUNT(*) AS count FROM accounts WHERE platform='replit' AND status IN ('active','registered','unverified')",
      []
    );
    const cur = parseInt(String(rows?.[0]?.count ?? "0"), 10);
    if (cur < MIN_REPLIT_POOL) {
      const need = MIN_REPLIT_POOL - cur;
      log(`[pool-check] replit 账号数 ${cur} < ${MIN_REPLIT_POOL}，自动触发补充 ${need} 个`);
      autoRefillRunning = true;
      const port = process.env.PORT ?? "8080";
      fetch(`http://127.0.0.1:${port}/api/replit/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ count: need, headless: true }),
      }).catch(() => {}).finally(() => { autoRefillRunning = false; });
    } else {
      log(`[pool-check] replit 账号数 ${cur} ✅ (>= ${MIN_REPLIT_POOL})`);
    }
  } catch (e) { log(`[pool-check] 查询失败: ${e}`); }
}


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
  const r = await fetch(`${LOCAL_API_BASE}${p}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json() as Promise<Record<string, unknown>>;
}

async function localGet(p: string) {
  const r = await fetch(`${LOCAL_API_BASE}${p}`);
  return r.json() as Promise<Record<string, unknown>>;
}

function normalizeUrls(input: unknown): string[] {
  if (Array.isArray(input)) return input.map(String).map((s) => s.trim()).filter(Boolean);
  if (typeof input === "string") return input.split(/[\n,;\s]+/).map((s) => s.trim()).filter(Boolean);
  return [];
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
// ── 注册前预检：验证 Outlook 收件箱可用性（优先复用现有 token，只在过期时才刷新）──
async function verifyOutlookInbox(
  acc: { id: number; email: string; token: string | null; refresh_token: string | null },
  dbE: (sql: string, params?: unknown[]) => Promise<unknown>,
  log: (msg: string) => void
): Promise<string | null> {

  // ── 辅助：用 access_token 测试收件箱 ─────────────────────────────────────
  async function tryInbox(tok: string): Promise<{ ok: boolean; count?: number; status?: number }> {
    try {
      const r = await fetch(
        "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$top=1&$select=id,receivedDateTime",
        { headers: { Authorization: `Bearer ${tok}` } }
      );
      if (r.ok) {
        const d = await r.json() as { value?: unknown[] };
        return { ok: true, count: d.value?.length ?? 0 };
      }
      return { ok: false, status: r.status };
    } catch { return { ok: false, status: 0 }; }
  }

  // ── 1. 如果 DB 里已有实际 access token（非占位符），先直接验证 ──────────────
  const stored = acc.token && acc.token.length > 50 && acc.token !== "ok" ? acc.token : null;
  if (stored) {
    const res = await tryInbox(stored);
    if (res.ok) {
      log(`    [inbox✓] id=${acc.id} ${acc.email} 现有token有效，收件箱可读 (${res.count}封)`);
      return stored;
    }
    // 401 → 过期，继续走刷新流程；其它错误同样走刷新
    log(`    [inbox] id=${acc.id} 现有token无效(${res.status}) → 尝试刷新`);
  }

  // ── 2. 无可用 refresh_token 时直接放弃 ────────────────────────────────────
  const rt = acc.refresh_token && acc.refresh_token.length > 20 && acc.refresh_token !== "ok"
    ? acc.refresh_token : null;
  if (!rt) {
    log(`    [inbox✗] id=${acc.id} 无可用refresh_token → 标token_invalid`);
    await dbE(
      "UPDATE accounts SET tags = COALESCE(tags || ',', '') || ',token_invalid', status='suspended', updated_at=NOW() WHERE id=$1",
      [acc.id]
    );
    return null;
  }

  // ── 3. OAuth refresh_token 换新 access_token ─────────────────────────────
  let newAccessToken = "";
  let newRefreshToken = rt;
  try {
    const tr = await fetch("https://login.microsoftonline.com/common/oauth2/v2.0/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "refresh_token",
        client_id: OUTLOOK_CLIENT_ID,
        refresh_token: rt,
        scope: OUTLOOK_SCOPE,
      }).toString(),
    });
    const td = await tr.json() as {
      access_token?: string; refresh_token?: string;
      error?: string; error_description?: string;
    };
    if (!td.access_token) {
      const errMsg = (td.error_description ?? td.error ?? "刷新失败").slice(0, 120);
      log(`    [inbox✗] id=${acc.id} token刷新失败: ${errMsg} → 标token_invalid`);
      await dbE(
        "UPDATE accounts SET tags = COALESCE(tags || ',', '') || ',token_invalid', status='suspended', updated_at=NOW() WHERE id=$1",
        [acc.id]
      );
      return null;
    }
    newAccessToken  = td.access_token;
    newRefreshToken = td.refresh_token ?? rt;   // 微软可能轮换 refresh_token
    // 仅当 refresh_token 被轮换时才更新 DB（保持"最小写入"原则）
    if (newRefreshToken !== rt) {
      await dbE(
        "UPDATE accounts SET token=$1, refresh_token=$2, updated_at=NOW() WHERE id=$3",
        [newAccessToken, newRefreshToken, acc.id]
      );
    } else {
      // refresh_token 未变，只更新 access_token
      await dbE(
        "UPDATE accounts SET token=$1, updated_at=NOW() WHERE id=$2",
        [newAccessToken, acc.id]
      );
    }
  } catch (e) {
    log(`    [inbox✗] id=${acc.id} token刷新异常: ${String(e).slice(0, 80)} → skip`);
    return null;
  }

  // ── 4. 用新 token 验证收件箱 ──────────────────────────────────────────────
  const res2 = await tryInbox(newAccessToken);
  if (!res2.ok) {
    const errMsg = `收件箱访问失败 HTTP ${res2.status}`;
    log(`    [inbox✗] id=${acc.id} ${errMsg} → 标inbox_error`);
    await dbE(
      "UPDATE accounts SET tags = COALESCE(tags || ',', '') || ',inbox_error', updated_at=NOW() WHERE id=$1",
      [acc.id]
    );
    return null;
  }

  log(`    [inbox✓] id=${acc.id} ${acc.email} token刷新成功，收件箱可读 (${res2.count}封)`);
  // 清除旧的 inbox_error 标记
  await dbE(
    "UPDATE accounts SET tags = NULLIF(TRIM(BOTH ',' FROM REPLACE(COALESCE(tags,''), 'inbox_error', '')), ''), updated_at=NOW() WHERE id=$1",
    [acc.id]
  );
  return newAccessToken;
}

router.post("/replit/register", (req, res) => {
  const parsedCount = Number.parseInt(String(req.body?.count ?? "1"), 10);
  const count = Math.min(Math.max(Number.isFinite(parsedCount) ? parsedCount : 1, 1), 3);
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
    const { query: dbQ, execute: dbE } = await import("../db.js");

    for (let i = 0; i < count; i++) {
      log(`\n== Account ${i + 1}/${count} ==`);

      try {
        // ── Step 1: 取最多 5 个可用 Outlook 账号候选 ─────────────────────
        log("Step1: Fetching Outlook candidates from DB...");

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
             AND COALESCE(tags, '') NOT LIKE '%token_invalid%'
             AND COALESCE(tags, '') NOT LIKE '%inbox_error%'
           ORDER BY
             CASE WHEN COALESCE(tags,'') LIKE '%inbox_verified%' THEN 0 ELSE 1 END,
             RANDOM()
           LIMIT 10`
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

          // ── 预检：确认 Outlook 账号 token 有效且能收件 ────────────────────────
          const freshToken = await verifyOutlookInbox(
            { id: outlook.id, email: outlook.email, token: outlook.token, refresh_token: outlook.refresh_token },
            dbE, log
          );
          if (!freshToken) {
            log(`  [skip] Outlook id=${outlook.id} 无法收件 → 换下一个候选`);
            continue;
          }

          log(`  Trying Outlook id=${outlook.id} email=${outlook.email} => Replit user=${username}`);

          // ── Step 2a: 最多 6 次不同代理端口重试（shuffle不重复）───────
          let captchaFailCount = 0; // 同一 Outlook 账号的 captcha_token_invalid 次数
          const regScript = path.join(API_DIR, "replit_register.py");
          let regOk  = false;
          let exitIp = "";
          let lastErr = "";

          // 每个Outlook账号：信誉好的端口优先，避免重复
          const portQueue = sortedByReputation(availablePorts());
          if (portQueue.length < 6) portQueue.push(...shuffled(XRAY_PORTS)); // 兜底

          for (let attempt = 1; attempt <= 6; attempt++) {
            const tryPort = portQueue[(attempt - 1) % portQueue.length];
            log(`    Attempt ${attempt}/6 via SOCKS5:${tryPort}`);

            const { parsed } = await runPython(regScript, {
              email: outlook.email,
              username,
              password,
              proxy: `socks5://127.0.0.1:${tryPort}`,
              headless,
              max_wait: 90,
              capsolver_key: process.env.CAPSOLVER_KEY ?? "",
            });

            // 记录代理使用（成功/失败均记录）
            await recordXrayProxyUsage(tryPort, dbE as unknown as (sql: string, params?: unknown[]) => Promise<unknown>);

            exitIp  = String(parsed.exit_ip ?? "");
            lastErr = String(parsed.error ?? "");

            if (parsed.ok) {
              portLastGood.set(tryPort, Date.now()); // successful registration
              log(`    ✅ Registered! phase=${parsed.phase} exit_ip=${exitIp}`);
              regOk = true;
              break;
            }

            log(`    ✗ Attempt ${attempt}: ${lastErr.slice(0, 100)}`);

            // 邮箱已被用 → 标记并换下一个 Outlook 账号
            if (lastErr.toLowerCase().includes("already") && lastErr.toLowerCase().includes("use")) {
              portLastGood.set(tryPort, Date.now()); // port got form response → mark good
              log(`    Email already on Replit → marking replit_used`);
              await dbE(
                "UPDATE accounts SET tags = COALESCE(tags || ',', '') || 'replit_used', updated_at = NOW() WHERE id = $1",
                [outlook.id]
              ).catch(() => {});
              break; // 跳出 attempt 循环，尝试下一个 Outlook
            }

            // 立即换端口：CF封禁 / Turnstile超时 / captcha token失效
            // "too quickly" → 该邮箱/用户名被限速，非可重试（直接下一个Outlook）
            if (lastErr.toLowerCase().includes("too quickly") || lastErr.toLowerCase().includes("doing this too")) {
              portLastGood.set(tryPort, Date.now()); // port got form response → mark good
              log(`    Rate-limited (too quickly) → skip this Outlook`);
              break;
            }
            // signup_username_field_missing → step1可能已成功（账号已建），当作成功处理
            if (lastErr.includes("signup_username_field_missing")) {
              log(`    username_field_missing → assuming step1 succeeded, proceeding to verify`);
              regOk = true;
              break;
            }
            // captcha_token_invalid → Replit server拒绝token → 立即rate-limit该email → 跳下一个Outlook
            if (lastErr.includes("captcha_token_invalid")) {
              captchaFailCount++;
              if (captchaFailCount >= 3) {
                log(`    captcha_token_invalid x${captchaFailCount} → skip Outlook (rate-limit risk)`);
                break;
              }
              // 换个 CF CDN IP 再试——captcha 失败通常是 IP 信誉问题
              const cfIpC = xrayPortCfIp.get(tryPort);
              if (cfIpC) {
                log(`    captcha_token_invalid (${captchaFailCount}/2) → rotate CF IP ${cfIpC} + retry`);
                rotateCfIpInXray(cfIpC);
                await new Promise(r => setTimeout(r, 2000));
              } else {
                log(`    captcha_token_invalid (${captchaFailCount}/2) → instant port switch`);
              }
              continue;
            }
            // v7.31b: account_rate_limited = IP被限速，换端口重试（最多6次）
            if (lastErr.includes("account_rate_limited")) {
              portLastGood.set(tryPort, Date.now()); // port got response
              log();
              await new Promise(r => setTimeout(r, 4000));
              continue;
            }

            const isInstantSwitch =
              lastErr.includes("cf_ip_banned")              ||
              lastErr.includes("cf_hard_block")             ||
              lastErr.includes("cf_js_challenge_timeout")   ||  // v7.3 新增
              lastErr.includes("turnstile_unsolved")       ||
              lastErr.includes("ERR_CERT")               ||  // SSL证书损坏端口立即跳
              lastErr.includes("ERR_CONNECTION_RESET")    ||
              lastErr.includes("ERR_CONNECTION_CLOSED")  ||  // 连接被关闭→立即换IP
              lastErr.includes("ERR_EMPTY_RESPONSE");    // 空响应→换IP

            const retryable =
              isInstantSwitch ||
              lastErr.includes("integrity") ||
              lastErr.includes("timeout")   ||
              lastErr.includes("Timeout")   ||
              lastErr.includes("signup")    ||
              lastErr.includes("moment");
            if (!retryable) break;

            if (isInstantSwitch) {
              // CF封禁/连接失败 → 轮换该端口的 CF CDN IP
              const needsCfRotate =
                lastErr.includes("cf_ip_banned")             ||
                lastErr.includes("cf_hard_block")            ||
                // cf_js_challenge_timeout = 超时(非永久封禁), 不轮换IP避免xray频繁重启
                lastErr.includes("ERR_CONNECTION_CLOSED")    ||
                lastErr.includes("ERR_EMPTY_RESPONSE")       ||
                lastErr.includes("ERR_CERT");
              if (needsCfRotate) {
                // 只有真实封禁才记录冷却 + 轮换IP
                if (lastErr.includes("cf_ip_banned") || lastErr.includes("cf_hard_block")) {
                  cfBannedUntil.set(tryPort, Date.now() + 5 * 60 * 1000);
                }
                const cfIp = xrayPortCfIp.get(tryPort);
                if (cfIp) {
                  log(`    → rotate CF IP ${cfIp} in xray (pool → new IP)`);
                  rotateCfIpInXray(cfIp);
                  await new Promise(r => setTimeout(r, 2000));  // wait xray reload
                }
              }
              log(`    → instant port switch`);
              // 从queue剩余中找下一个未用端口
              continue;
            }
            const delayMs = lastErr.includes("integrity") ? 1000 :
                            lastErr.includes("signup_form_input_missing") ? 2000 :
                            3000 + attempt * 1000;
            await new Promise(r => setTimeout(r, delayMs));
          }

          if (!regOk) continue; // 换下一个 Outlook 账号

          // ── Step 3: 等待 Replit 发验证邮件 → 通过 Graph API 点击 ─────
          log("  Step3: Waiting for Replit verification email...");
          await new Promise(r => setTimeout(r, 12000));

          let verified = false;
          for (let t = 0; t < 30; t++) {
            log(`    Poll ${t + 1}/30 (accountId=${outlook.id})...`);
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
    await checkAndRefillReplitPool(dbQ, log);
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

// ── POST /api/replit/subnodes/register ─────────────────────────────────────────
router.post("/replit/subnodes/register", async (req, res) => {
  const urls = normalizeUrls(req.body?.urls ?? req.body?.url ?? req.body?.gatewayUrl);
  if (!urls.length) {
    res.status(400).json({ success: false, error: "urls 不能为空，可传数组或按行/逗号分隔的字符串" });
    return;
  }
  const model = String(req.body?.model || "gpt-5-mini");
  const priority = Math.min(Math.max(Number(req.body?.priority || 3), 1), 20);
  const apiKey = req.body?.apiKey ? String(req.body.apiKey) : undefined;
  try {
    const result = await localPost("/api/gateway/nodes/batch-probe", {
      urls,
      apiKey,
      model,
      priority,
      autoRegister: true,
    });
    res.json(result);
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// ── GET /api/replit/subnodes ──────────────────────────────────────────────────
router.get("/replit/subnodes", async (_req, res) => {
  try {
    const result = await localGet("/api/gateway/nodes") as {
      success?: boolean;
      nodes?: Array<Record<string, unknown>>;
      totals?: Record<string, unknown>;
    };
    const nodes = (result.nodes || []).filter((n) => {
      const source = String(n.source || "");
      const type = String(n.type || "");
      return type === "friend-openai" || source === "register" || source === "runtime" || source === "env";
    });
    res.json({ success: result.success !== false, total: nodes.length, totals: result.totals, nodes });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  }
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
  const parsedCount = Number.parseInt(String(req.body?.count ?? "1"), 10);
  const count = Math.min(Math.max(Number.isFinite(parsedCount) ? parsedCount : 1, 1), 5);
  const jobId = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  const job: Job = { id: jobId, status: "running", started: Date.now(), logs: [], result: null };
  jobs.set(jobId, job);
  const proc = spawn(PYTHON, [
    path.join(SCRIPTS_DIR, "replit_signup_v2.py"),
    "--count", String(count),
  ], {
    cwd: SCRIPTS_DIR,
    env: { ...process.env, GATEWAY_API: LOCAL_API_BASE + "/api/gateway", VPS_GATEWAY_URL: VPS_GATEWAY },
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

// ── POST /api/replit/retry-verify ─────────────────────────────────────────────
// 对所有 unverified 的 Reseek 账号重试邮箱验证（用对应 Outlook 账号的 token）
router.post("/replit/retry-verify", async (_req, res) => {
  try {
    const { query: dbQ, execute: dbE } = await import("../db.js");
    const unverified = await dbQ<{ id: number; email: string; username: string }>(
      "SELECT id, email, username FROM accounts WHERE platform='replit' AND status='unverified'"
    );
    if (!unverified.length) { res.json({ success: true, message: "没有待验证账号", results: [] }); return; }
    const results: Array<{ replitId: number; email: string; status: string; error?: string }> = [];
    for (const acc of unverified) {
      // 找对应 Outlook 账号
      const ol = await dbQ<{ id: number; email: string; token: string | null; refresh_token: string | null }>(
        "SELECT id, email, token, refresh_token FROM accounts WHERE platform='outlook' AND email=$1 AND status='active'",
        [acc.email]
      );
      if (!ol.length) {
        results.push({ replitId: acc.id, email: acc.email, status: "no_outlook", error: "无对应 Outlook 账号" });
        continue;
      }
      const outlook = ol[0];
      // 尝试点击验证链接
      let accessToken = outlook.token || "";
      if (outlook.refresh_token) {
        try {
          const tr = await fetch("https://login.microsoftonline.com/common/oauth2/v2.0/token", {
            method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: new URLSearchParams({
              grant_type: "refresh_token",
              client_id: "9e5f94bc-e8a4-4e73-b8be-63364c29d753",
              refresh_token: outlook.refresh_token,
              scope: "https://graph.microsoft.com/Mail.Read offline_access",
            }).toString(),
          });
          const td = await tr.json() as { access_token?: string; refresh_token?: string };
          if (td.access_token) {
            accessToken = td.access_token;
            await dbE("UPDATE accounts SET token=$1, updated_at=NOW() WHERE id=$2", [accessToken, outlook.id]);
          }
        } catch { /* 忽略刷新错误 */ }
      }
      if (!accessToken) {
        results.push({ replitId: acc.id, email: acc.email, status: "no_token", error: "无 access token" });
        continue;
      }
      // 调用 click-verify-link
      const verifyResult = await localPost("/api/tools/outlook/click-verify-link", {
        accountId: outlook.id,
      }) as { success?: boolean; final_url?: string; error?: string };
      if (verifyResult.success) {
        await dbE("UPDATE accounts SET status='registered', updated_at=NOW() WHERE id=$1", [acc.id]);
        results.push({ replitId: acc.id, email: acc.email, status: "verified" });
      } else {
        results.push({ replitId: acc.id, email: acc.email, status: "failed", error: String(verifyResult.error ?? "未找到验证链接") });
      }
    }
    res.json({ success: true, results });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

// ── POST /api/pipeline/full ────────────────────────────────────────────────────
// 全自动流水线：Outlook 注册 → Reseek 注册 + 验证 → 子节点部署
// body: { target?: number, skipOutlook?: boolean, skipReseek?: boolean }
router.post("/pipeline/full", async (req, res) => {
  const {
    target = 3,           // 目标 Reseek 子节点数量
    skipOutlook  = false, // 跳过 Outlook 注册步骤（已有足够账号时）
    skipReseek   = false, // 跳过 Reseek 注册步骤
  } = req.body as { target?: number; skipOutlook?: boolean; skipReseek?: boolean };

  const jobId = `pipe_${Date.now().toString(36)}`;
  const job: Job = { id: jobId, status: "running", started: Date.now(), logs: [], result: null };
  jobs.set(jobId, job);
  function log(msg: string) {
    const line = `[${new Date().toISOString().slice(11,19)}] ${msg}`;
    job.logs.push(line);
    console.log(`[pipeline][${jobId}] ${msg}`);
  }

  res.json({ success: true, jobId, message: `全自动流水线已启动 (目标${target}个子节点)` });

  (async () => {
    try {
      const { query: dbQ } = await import("../db.js");

      // ── Step 1: 检查 Outlook 账号供应 ────────────────────────────────────
      log("=== Step1: 检查 Outlook 账号池 ===");
      const avail = await dbQ<{ count: string }>(
        "SELECT COUNT(*) AS count FROM accounts WHERE platform='outlook' AND status='active' AND (token IS NOT NULL OR refresh_token IS NOT NULL) AND COALESCE(tags,'') NOT LIKE '%replit_used%'"
      );
      const availCount = parseInt(avail[0]?.count ?? "0", 10);
      log(`  可用 Outlook 账号: ${availCount}`);

      if (!skipOutlook && availCount < target) {
        const need = target - availCount;
        log(`  不足 ${target} 个，启动 Outlook 注册 ${need} 个...`);
        try {
          const r = await localPost("/api/tools/outlook/register", {
            count: need, headless: true, engine: "patchright",
            wait: 11, retries: 2, proxyMode: "cf", cfPort: 443, delay: 3,
          }) as { jobId?: string };
          if (r.jobId) {
            // 等待最多 30 分钟
            for (let w = 0; w < 180; w++) {
              await new Promise(r => setTimeout(r, 10000));
              const status = await localGet(`/api/tools/outlook/register/${r.jobId}`) as { status?: string };
              log(`  Outlook注册状态: ${status.status ?? "?"}`);
              if (status.status === "done" || status.status === "stopped") break;
            }
          }
        } catch (e) { log(`  Outlook注册失败: ${e}`); }
      }

      // ── Step 2: Reseek 注册 ───────────────────────────────────────────────
      if (!skipReseek) {
        log("=== Step2: 启动 Reseek 注册 ===");
        const existing = await dbQ<{ count: string }>(
          "SELECT COUNT(*) AS count FROM accounts WHERE platform='replit' AND status IN ('active','registered','unverified')"
        );
        const existCount = parseInt(existing[0]?.count ?? "0", 10);
        const toReg = Math.max(0, target - existCount);
        if (toReg > 0) {
          log(`  当前 Reseek 账号 ${existCount}，需再注册 ${toReg} 个`);
          const r = await localPost("/api/replit/register", { count: toReg, headless: true }) as { jobId?: string };
          if (r.jobId) {
            for (let w = 0; w < 120; w++) {
              await new Promise(r => setTimeout(r, 10000));
              const status = await localGet(`/api/replit/register/${r.jobId}`) as { status?: string; result?: Record<string,unknown> };
              log(`  Reseek注册状态: ${status.status ?? "?"}`);
              if (status.status === "done" || status.status === "error") {
                log(`  注册结果: ${JSON.stringify(status.result ?? {}).slice(0, 100)}`);
                break;
              }
            }
          }
        } else {
          log(`  已有 ${existCount} 个 Reseek 账号，跳过注册`);
        }
      }

      // ── Step 3: 重试验证 unverified 账号 ─────────────────────────────────
      log("=== Step3: 重试 unverified 账号验证 ===");
      try {
        const vr = await localPost("/api/replit/retry-verify", {}) as { results?: Array<Record<string,unknown>> };
        log(`  验证结果: ${JSON.stringify(vr.results ?? []).slice(0, 200)}`);
      } catch (e) { log(`  重试验证失败: ${e}`); }

      // ── Step 4: 子节点登录部署 ─────────────────────────────────────────────
      log("=== Step4: 子节点登录部署 ===");
      const toDeploy = await dbQ<{ id: number; email: string; password: string; username: string }>(
        "SELECT id, email, password, COALESCE(username,'') AS username FROM accounts WHERE platform='replit' AND status IN ('active','registered') AND COALESCE(tags,'') NOT LIKE '%subnode_deployed%' LIMIT 3"
      );
      log(`  待部署账号: ${toDeploy.length}`);
      for (const acc of toDeploy) {
        if (!acc.password) { log(`  ${acc.email} 无密码，跳过`); continue; }
        // 找对应 outlook token
        const ol = await dbQ<{ token: string | null; refresh_token: string | null }>(
          "SELECT token, refresh_token FROM accounts WHERE platform='outlook' AND email=$1", [acc.email]
        );
        const outlookTok = ol[0]?.token ?? "";
        log(`  部署 ${acc.email} (${acc.username})...`);
        const deployR = await runPython(
          path.join(API_DIR, "replit_deploy_agent.py"),
          { email: acc.email, password: acc.password, outlook_token: outlookTok,
            gateway_url: "http://45.205.27.69:8080", headless: true },
          300_000
        );
        if (deployR.parsed.ok) {
          const webUrl = String(deployR.parsed.webview_url ?? "");
          log(`  ✓ 部署成功 webview=${webUrl}`);
          // 标记已部署 + 保存 webview URL
          await dbE(
            "UPDATE accounts SET tags=COALESCE(tags||\',\',\'\') || \'subnode_deployed\', notes=$1, updated_at=NOW() WHERE id=$2",
            [webUrl, acc.id]
          );
          // 注册为网关子节点
          if (webUrl) {
            try {
              await localPost("/api/gateway/self-register", {
                gatewayUrl: webUrl, name: acc.username || acc.email.split("@")[0],
              });
              log(`  ✓ 已注册子节点 ${webUrl}`);
            } catch (e) { log(`  子节点注册失败: ${e}`); }
          }
        } else {
          log(`  ✗ 部署失败: ${String(deployR.parsed.error ?? "").slice(0, 120)}`);
        }
      }

      const final = await dbQ<{ count: string }>(
        "SELECT COUNT(*) AS count FROM accounts WHERE platform='replit' AND status IN ('active','registered','unverified')"
      );
      log(`\n=== 流水线完成 === 总 Reseek 账号: ${final[0]?.count ?? 0}/${target}`);
      job.status = "done";
      job.result = { total: parseInt(final[0]?.count ?? "0", 10), target };
    } catch (e) {
      log(`FATAL: ${e}`);
      job.status = "error";
    }
  })().catch((e) => { job.status = "error"; job.logs.push(`FATAL: ${e}`); });
});

// ── GET /api/pipeline/full/:jobId ─────────────────────────────────────────────
router.get("/pipeline/full/:jobId", (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) return res.status(404).json({ success: false, error: "job not found" });
  res.json({ jobId: job.id, status: job.status, elapsed: Math.round((Date.now() - job.started) / 1000), logs: job.logs, result: job.result });
});


// ── POST /api/replit/deploy-subnode ──────────────────────────────────────────
// 对单个 Reseek 账号部署 agent 子节点 (浏览器自动化)
// body: { replitId?: number, email?: string }
router.post("/replit/deploy-subnode", async (req, res) => {
  const { replitId, email } = req.body as { replitId?: number; email?: string };
  const jobId = makeJobId();
  const job: Job = { id: jobId, status: "running", started: Date.now(), logs: [], result: null };
  jobs.set(jobId, job);
  function log(msg: string) {
    const line = `[${new Date().toISOString().slice(11,19)}] ${msg}`;
    job.logs.push(line);
    console.log(`[deploy-sub][${jobId}] ${msg}`);
  }
  res.json({ success: true, jobId, message: "子节点部署任务已启动" });

  (async () => {
    try {
      const { query: dbQ, execute: dbE } = await import("../db.js");
      let acc: { id: number; email: string; password: string; username: string } | undefined;
      if (replitId) {
        const rows = await dbQ<typeof acc>("SELECT id,email,COALESCE(password,\'\') AS password,COALESCE(username,\'\') AS username FROM accounts WHERE id=$1 AND platform=\'replit\'", [replitId]);
        acc = rows[0];
      } else if (email) {
        const rows = await dbQ<typeof acc>("SELECT id,email,COALESCE(password,\'\') AS password,COALESCE(username,\'\') AS username FROM accounts WHERE email=$1 AND platform=\'replit\'", [email]);
        acc = rows[0];
      } else {
        // 找第一个未部署的 registered/active 账号
        const rows = await dbQ<typeof acc>(
          "SELECT id,email,COALESCE(password,\'\') AS password,COALESCE(username,\'\') AS username FROM accounts WHERE platform=\'replit\' AND status IN (\'active\',\'registered\') AND COALESCE(tags,\'\') NOT LIKE \'%subnode_deployed%\' LIMIT 1"
        );
        acc = rows[0];
      }
      if (!acc) { job.status = "error"; job.result = { error: "账号未找到" }; return; }
      if (!acc.password) { job.status = "error"; job.result = { error: "无密码，无法登录" }; return; }
      log(`开始部署 ${acc.email} (id=${acc.id}, username=${acc.username})...`);

      // 获取 outlook token
      const ol = await dbQ<{ token: string | null }>(
        "SELECT token FROM accounts WHERE platform=\'outlook\' AND email=$1", [acc.email]
      );
      const outlookTok = ol[0]?.token ?? "";

      const deployR = await runPython(
        path.join(API_DIR, "replit_deploy_agent.py"),
        { email: acc.email, password: acc.password, outlook_token: outlookTok,
          gateway_url: "http://45.205.27.69:8080", headless: true },
        300_000
      );
      log(`部署脚本输出: ${deployR.raw.slice(-400)}`);
      if (deployR.parsed.ok) {
        const webUrl = String(deployR.parsed.webview_url ?? "");
        const replUrl = String(deployR.parsed.repl_url ?? "");
        log(`✓ 部署成功 webview=${webUrl}`);
        await dbE(
          "UPDATE accounts SET tags=NULLIF(TRIM(BOTH \',\' FROM COALESCE(tags,\'\') || \',subnode_deployed\'),\',\'), notes=$1, updated_at=NOW() WHERE id=$2",
          [webUrl || replUrl, acc.id]
        );
        if (webUrl) {
          try {
            const sr = await localPost("/api/gateway/self-register",
              { gatewayUrl: webUrl, name: acc.username || acc.email.split("@")[0] });
            log(`✓ 网关注册: ${JSON.stringify(sr).slice(0,80)}`);
          } catch (e) { log(`网关注册异常: ${e}`); }
        }
        job.status = "done";
        job.result = { ok: true, webview_url: webUrl, repl_url: replUrl, email: acc.email };
      } else {
        const errMsg = String(deployR.parsed.error ?? deployR.raw.slice(-300) ?? "未知错误");
        log(`✗ 部署失败: ${errMsg.slice(0, 200)}`);
        job.status = "error";
        job.result = { ok: false, error: errMsg.slice(0, 200) };
      }
    } catch (e) {
      log(`FATAL: ${e}`);
      job.status = "error";
      job.result = { error: String(e) };
    }
  })();
});

// ── GET /api/replit/deploy-subnode/:jobId ─────────────────────────────────────
router.get("/replit/deploy-subnode/:jobId", (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) return res.status(404).json({ success: false, error: "job not found" });
  res.json({ jobId: job.id, status: job.status, elapsed: Math.round((Date.now() - job.started) / 1000), logs: job.logs, result: job.result });
});


export default router;
