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
  return XRAY_PORTS.filter(p => (cfBannedUntil.get(p) ?? 0) < now);
}
function sortedByReputation(ports: number[]): number[] {
  // Good ports (recently had form response) first, then others
  const good = ports.filter(p => portLastGood.has(p)).sort((a, b) => (portLastGood.get(b)!) - (portLastGood.get(a)!));
  const other = ports.filter(p => !portLastGood.has(p));
  return [...shuffled(good), ...shuffled(other)];
}
function shuffled(arr: number[]): number[] { return [...arr].sort(() => Math.random() - 0.5); }

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
                lastErr.includes("cf_js_challenge_timeout")  ||
                lastErr.includes("ERR_CONNECTION_CLOSED")    ||
                lastErr.includes("ERR_EMPTY_RESPONSE")       ||
                lastErr.includes("ERR_CERT");
              if (needsCfRotate) {
                // CF超时/封禁类 → 额外记录5分钟冷却
                if (lastErr.includes("cf_ip_banned") || lastErr.includes("cf_hard_block") || lastErr.includes("cf_js_challenge_timeout")) {
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

export default router;
