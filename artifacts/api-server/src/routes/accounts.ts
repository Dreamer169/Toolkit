import { Router } from "express";
import { spawn } from "child_process";
import path from "path";
import { microsoftFetch } from "../lib/proxy-fetch.js";

const router = Router();
const WORKSPACE_DIR = process.cwd().endsWith("/artifacts/api-server") ? path.resolve(process.cwd(), "../..") : process.cwd();
const API_DIR = path.resolve(WORKSPACE_DIR, "artifacts/api-server");
const SCRIPTS_DIR = path.resolve(WORKSPACE_DIR, "scripts");
const PYTHON = process.env.PYTHON_BIN || "/usr/bin/python3";
const LOCAL_API_BASE = (process.env.LOCAL_API_BASE_URL || "http://127.0.0.1:" + (process.env.PORT || "8080")).replace(/\/$/, "");
const VPS_GATEWAY = process.env.VPS_GATEWAY_URL || "http://45.205.27.69:8080/api/gateway";
// Dead ports detected via connectivity scan (10827-10829, 10834-10841 offline)
// Only port 1090 (WS-proxy bridge: VPS → http_ws_bridge.py → Replit repl WS → internet, exit: Replit cloud IP) remains
// v7.51 — POOL SWAP (root cause of code:2 score-too-low):
//   旧 1090-1095 走 WARP/Cloudflare 出口至 34.96.x.x = GOOGLE-CLOUD-PLATFORM ASN,
//   reCAPTCHA Enterprise 给 GCP 的 datacenter IP 自动 ~0.1 score → server code:2 拒绝
//   (commit a21d345 已经诊断过这点)。新 10820-10845 是干净 residential/colo (DigitalOcean/
//   ColoCrossing/HostHatch 等), 实测大多 score >0.5 可过。旧注释已过时,
//   xray.json 已经把这 26 个端口配成 in-socks-N 入口, 实测探针 12/13 alive 且无 GCP IP.
const XRAY_PORTS_DEAD = new Set<number>([1090, 1091, 1092, 1094, 1095]);  // 友节点全死/或剩 GCP 出口
// v7.78b: WARP_PORT (40000) 不进 attempt-pool — chromium 主代理走 WARP 时 Replit
// 的 sign-up POST 会因 CF 拒绝自家 IP 直连 origin 而 36s 超时。改为只在
// google_proxy_route 端把 *.google 流量钉死走 WARP 提升 reCAPTCHA score, 而
// chromium 主代理保持 datacenter SOCKS 让 sign-up POST 走得通 (不对称代理).
const WARP_PORT   = 40000;  // 仅 google_proxy_route 用, 不进 attempt-pool
// v8.30 ROOT-FIX 2026-04-27 — DEEP-AUDIT REVELATION (paired with google_proxy_route v8.29):
//   The OLD pool [10822..10845] = xray in-socks-2..25 routes -> vless proxy-2..25 ALL
//   collapsed to single CF Worker IP 172.64.159.138 (AS13335) by v8.26 hot-fix.
//   broker chromium using these as outer proxy => /signup loaded via CF AS13335 =>
//   cf_clearance bound to CF IP => v8.27 sign-up POST forced through socks5:10859 =>
//   cf_clearance IP-mismatch => CF edge 403 "Just a moment..." challenge.
//   ALSO: even when not 403, reCAPTCHA Enterprise scored CF AS13335 origin as
//   datacenter -> code:1.
// The REAL clean-ASN pool was hiding at xray ss-in-0..9 (port 10850-10859),
// routing to shadowsocks ss-out-0..9 -> 9 distinct REAL upstreams. Per-port ASN
// audit on 2026-04-27:
//   10851 US AS60068 Datacamp Limited        (DC star)
//   10853 US AS27284 Fourplex Telecom        (small US telecom 2-star)
//   10854 KR AS20473 The Constant Co (Vultr) (DC star)
//   10855 GB AS9009  M247                    (DC star)
//   10857 TW AS3462  Chunghwa Telecom        (NATIONAL ISP 3-star residential)
//   10859 NL AS47172 Greenhost               (small hosting star)
//   10850/10852/10856/10858 DEAD             (drop)
// Order = ASN-quality descending so cf-banned-fallback walks toward real-ISP first.
// google_proxy_route v8.29 has parallel ASN-blocklist self-healer that filters this
// same pool at runtime, so the two layers stay in lockstep. CRITICAL: outer broker
// proxy + google_proxy_route + v8.27 sign-up forward MUST share the same exit IP
// to avoid CF edge IP-mismatch 403 + reCAPTCHA datacenter score.
const XRAY_PORTS  = [10857, 10855, 10853, 10854, 10859, 10851];  // v8.33 reorder by 04/27 实证: 10857(TW中华4✓) 10855(GB M247 3✓) 10853未测 10854未测 10859(NL Greenhost ✗) 10851(US Datacamp ✗)
const XRAY_PORTS_LEGACY_CF = [10822, 10824, 10826, 10828, 10830, 10832, 10834, 10836, 10838, 10840, 10842, 10845];  // ALL CF AS13335 — DO NOT USE
const DEAD_PORTS  = XRAY_PORTS_DEAD;
const TOR_SOCKS_PORT = 9050;  // Tor SOCKS5 (already running on VPS), exit = non-CF/non-GCP
const DIRECT_PORT    = 0;     // Direct VPS IP (AS8796 FASTNET DATA), exit = 45.205.27.69

/** Build proxy URL from port: 0=direct (no proxy), else SOCKS5 */
function portToProxy(port: number): string {
  if (port === DIRECT_PORT) return "";
  return `socks5://127.0.0.1:${port}`;
}

// Outlook OAuth (Thunderbird client_id)
const OUTLOOK_CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753";
const OUTLOOK_SCOPE     = "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/User.Read offline_access";
// CF-banned port cooldown: port → timestamp when cooldown expires (5 min)
const cfBannedUntil = new Map<number, number>();
// Dead ports: ERR_CONNECTION_CLOSED → skip for 10 min
const portDeadUntil = new Map<number, number>();
// Port reputation: last time port returned a real form response (not cf_ban/captcha_invalid)
const portLastGood = new Map<number, number>();

// ── 启动时从 xray.json 建立 port → CF IP 映射表 ─────────────────────────────
const xrayPortCfIp = new Map<number, string>();
const rotatingCfIps = new Set<string>();
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
  if (rotatingCfIps.has(bannedIp)) { console.warn(`[cf-rotate] ${bannedIp} rotation already running, skip duplicate`); return; }
  rotatingCfIps.add(bannedIp);
  try {
    let stdout = "";
    let stderr = "";
    const proc = spawn(PYTHON, [ROTATE_SCRIPT, "--banned-ip", bannedIp]);
    proc.stdout.on("data", (d: Buffer) => { stdout += d.toString(); });
    proc.stderr.on("data", (d: Buffer) => { stderr += d.toString(); });
    proc.on("close", () => {
      try {
        const result = stdout ? JSON.parse(stdout) : {};
        if (result.success) {
          console.log(`[cf-rotate] ${bannedIp} → ${result.new_ip} outbounds=${result.changed_outbounds} reload=${result.reload} remaining=${result.remaining ?? "?"}`);
          rebuildXrayPortMap().catch(() => {});
        } else {
          const error = String(result.error || stderr || "unknown");
          if (error.includes("not found in xray.json")) {
            console.warn(`[cf-rotate] stale banned IP ${bannedIp}, rebuild map and skip`);
            rebuildXrayPortMap().catch(() => {});
            return;
          }
          if (error.includes("pool_empty")) console.warn(`[cf-rotate] 失败: pool_empty，已触发后台补池`);
          else console.warn(`[cf-rotate] 失败: ${error}`);
        }
      } catch (e) { console.warn("[cf-rotate] parse error:", e); }
      finally { rotatingCfIps.delete(bannedIp); }
    });
    proc.on("error", (e: Error) => { rotatingCfIps.delete(bannedIp); console.warn("[cf-rotate] spawn error:", e.message); });
  } catch (e) { rotatingCfIps.delete(bannedIp); console.warn("[cf-rotate] exception:", e); }
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

// ── Dynamic per-attempt xray VLESS relay (fresh CF IP from pool each time) ────
const XRAY_BIN_PATH = (() => {
  const { existsSync } = require("fs");
  const candidates = [
    path.join(WORKSPACE_DIR, "artifacts/api-server/xray/xray"),
    "/root/Toolkit/artifacts/api-server/xray/xray",
  ];
  return candidates.find(existsSync) ?? "";
})();

const POP_ONLY_SCRIPT = ROTATE_SCRIPT; // same script, --pop-only flag

interface DynXray { port: number; cfIp: string; cleanup: () => void; }
const activeDynXrays = new Map<number, () => void>();

function _findFreeLocalPort(start = 20000, end = 29999): Promise<number | null> {
  return new Promise(resolve => {
    const net = require("net");
    let p = start + Math.floor(Math.random() * (end - start));
    const tryNext = (n: number) => {
      if (n > end) { resolve(null); return; }
      const s = net.createServer();
      s.once("error", () => tryNext(n + 1));
      s.once("listening", () => s.close(() => resolve(n)));
      s.listen(n, "127.0.0.1");
    };
    tryNext(p);
  });
}

function _waitForPort(port: number, timeoutMs = 8000): Promise<boolean> {
  return new Promise(resolve => {
    const net = require("net");
    const deadline = Date.now() + timeoutMs;
    const attempt = () => {
      if (Date.now() > deadline) { resolve(false); return; }
      const s = net.createConnection({ port, host: "127.0.0.1" });
      s.on("connect", () => { s.destroy(); resolve(true); });
      s.on("error", () => setTimeout(attempt, 300));
    };
    attempt();
  });
}

async function spawnDynamicXray(bannedExitIp?: string): Promise<DynXray | null> {
  if (!POP_ONLY_SCRIPT || !XRAY_BIN_PATH) {
    console.warn("[dyn-xray] missing rotate script or xray binary");
    return null;
  }
  // 1. Pop fresh CF IP from pool
  const cfIp: string = await new Promise(resolve => {
    const args = ["--pop-only"];
    if (bannedExitIp && bannedExitIp !== "127.0.0.1" && bannedExitIp.includes(".")) {
      args.push("--banned-ip", bannedExitIp);
    }
    let out = "";
    const p = spawn(PYTHON, [POP_ONLY_SCRIPT, ...args]);
    p.stdout.on("data", (d: Buffer) => { out += d; });
    p.on("close", () => {
      try { const j = JSON.parse(out); resolve(j.success ? j.new_ip : ""); }
      catch { resolve(""); }
    });
    p.on("error", () => resolve(""));
  });
  if (!cfIp) { console.warn("[dyn-xray] pool empty or error"); return null; }

  // 2. Find free port
  const port = await _findFreeLocalPort();
  if (!port) { console.warn("[dyn-xray] no free port"); return null; }

  // 3. Write temp config
  const { writeFileSync, unlinkSync } = require("fs");
  const cfgPath = `/tmp/xray_dyn_${port}.json`;
  const cfg = {
    log: { loglevel: "none" },
    inbounds: [{ port, listen: "127.0.0.1", protocol: "socks", settings: { auth: "noauth", udp: false } }],
    outbounds: [{
      protocol: "vless",
      settings: { vnext: [{ address: cfIp, port: 443, users: [{ id: "b3be1361-709c-4cad-824a-732e434ea06f", encryption: "none", flow: "" }] }] },
      streamSettings: {
        network: "ws", security: "tls",
        tlsSettings: { serverName: "iam.jimhacker.qzz.io", fingerprint: "chrome", alpn: ["h3", "h2", "http/1.1"], allowInsecure: false },
        wsSettings: { path: "/?ed=2048", headers: { Host: "iam.jimhacker.qzz.io" } },
      },
    }],
  };
  try { writeFileSync(cfgPath, JSON.stringify(cfg)); } catch { return null; }

  // 4. Spawn xray process
  const xrayProc = spawn(XRAY_BIN_PATH, ["run", "-config", cfgPath], { stdio: "ignore" });
  const cleanup = () => {
    try { xrayProc.kill("SIGTERM"); } catch {}
    try { unlinkSync(cfgPath); } catch {}
    activeDynXrays.delete(port);
  };
  activeDynXrays.set(port, cleanup);
  xrayProc.on("exit", () => { try { unlinkSync(cfgPath); } catch {} activeDynXrays.delete(port); });

  // 5. Wait for SOCKS5 port
  const ready = await _waitForPort(port, 8000);
  if (!ready) {
    console.warn(`[dyn-xray] port ${port} not ready in 8s (CF IP ${cfIp})`);
    cleanup();
    return null;
  }

  console.log(`[dyn-xray] ready: SOCKS5:${port} via CF IP ${cfIp}`);
  return { port, cfIp, cleanup };
}

function availablePorts(): number[] {
  const now = Date.now();
  const static_ = XRAY_PORTS.filter(p => !DEAD_PORTS.has(p) && (cfBannedUntil.get(p) ?? 0) < now && (portDeadUntil.get(p) ?? 0) < now);
  // Include active dynamic xray relay ports so they show up in livePorts check
  const dynamic = Array.from(activeDynXrays.keys());
  return [...static_, ...dynamic];
}
function sortedByReputation(ports: number[]): number[] {
  const priority = [1094, 1095, 1093, 1092, 1090].filter((p) => ports.includes(p));
  const rest = ports.filter((p) => !priority.includes(p));
  const good = rest.filter(p => portLastGood.has(p)).sort((a, b) => (portLastGood.get(b)!) - (portLastGood.get(a)!));
  // v8.34: 无信誉数据的 port 按 XRAY_PORTS 静态序 (实证战绩降序), 不做 shuffle.
  // 这样 process restart 后仍优先选历史最佳 (e.g. 10857 TW 中华电信), 不会随机退化.
  const xrayIdx = (p: number) => { const i = XRAY_PORTS.indexOf(p); return i < 0 ? 999 : i; };
  const other = rest.filter(p => !portLastGood.has(p)).sort((a, b) => xrayIdx(a) - xrayIdx(b));
  return [...priority, ...shuffled(good), ...other];
}
function shuffled(arr: number[]): number[] { return [...arr].sort(() => Math.random() - 0.5); }

const MIN_REPLIT_POOL = 1; // 账号池最低水位，低于此值自动触发补充

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
      "SELECT COUNT(*) AS count FROM accounts WHERE platform='replit' AND status IN ('active','registered')",
      []
    );
    const cur = parseInt(String(rows?.[0]?.count ?? "0"), 10);
    if (cur < MIN_REPLIT_POOL) {
      const need = MIN_REPLIT_POOL - cur;
      log(`[pool-check] replit 账号数 ${cur} < ${MIN_REPLIT_POOL}，自动触发补充 ${need} 个`);
      // [policy] 关闭自动补充：必须由用户从「完整工作流」→「邮件中心」→ 友节点注册手工触发
      log(`[pool-check] 已禁用自动补充（need=${need}），请走完整工作流人工触发`);
    } else {
      log(`[pool-check] replit 账号数 ${cur} ✅ (>= ${MIN_REPLIT_POOL})`);
    }
  } catch (e) { log(`[pool-check] 查询失败: ${e}`); }
}


interface Job {
  id: string;
  status: "running" | "done" | "error";
  started: number;
  finished?: number; // v7.94: 任务终态时间戳 — elapsed/UI 用，避免一直涨
  logs: string[];
  result: Record<string, unknown> | null;
}
const jobs = new Map<string, Job>();

// v7.93c — jobs persistence (file-backed, hydrate on boot, throttled writes)
const _jobsDir = '/root/Toolkit/.local/replit_jobs';
const _writeTimers = new Map<string, NodeJS.Timeout>();
async function _ensureJobsDir() {
  try {
    const fs = await import('fs/promises');
    await fs.mkdir(_jobsDir, { recursive: true });
  } catch {}
}
async function _flushJob(id: string) {
  const job = jobs.get(id);
  if (!job) return;
  try {
    const fs = await import('fs/promises');
    await _ensureJobsDir();
    await fs.writeFile(`${_jobsDir}/${id}.json`, JSON.stringify({
      id: job.id, status: job.status, started: job.started, finished: job.finished ?? null,
      logs: job.logs.slice(-2000), result: job.result, updated: Date.now(),
    }));
  } catch (e) { console.warn('[jobs-persist] flush fail', id, e); }
}
function persistJob(id: string, immediate = false) {
  if (immediate) {
    const t = _writeTimers.get(id);
    if (t) { clearTimeout(t); _writeTimers.delete(id); }
    _flushJob(id);
    return;
  }
  if (_writeTimers.has(id)) return;
  _writeTimers.set(id, setTimeout(() => {
    _writeTimers.delete(id);
    _flushJob(id);
  }, 5000));
}
(async () => {
  try {
    const fs = await import('fs/promises');
    await _ensureJobsDir();
    const files = await fs.readdir(_jobsDir);
    let n = 0;
    for (const f of files) {
      if (!f.endsWith('.json')) continue;
      try {
        const txt = await fs.readFile(`${_jobsDir}/${f}`, 'utf8');
        const obj = JSON.parse(txt) as Job & { updated?: number };
        if (!obj?.id) continue;
        // mark any 'running' as 'error' (process was killed mid-flight)
        if (obj.status === 'running') {
          obj.status = 'error';
          obj.logs = [...(obj.logs ?? []), `[${new Date().toISOString().slice(11,19)}] ⚠ pm2 重启截断 — 状态由 running 标记为 error`];
          (obj as Job & { finished?: number }).finished = (obj as Job & { updated?: number }).updated ?? Date.now();
        }
        // v7.94 retroactive heal: 旧版本 (okCount==0 → error) 错染红色的任务回收
        const _logsArr = (obj.logs ?? []) as string[];
        const _tail    = _logsArr.slice(-8).join("\n");
        if (obj.status === 'error'
            && /All done: \d+\/\d+ succeeded/.test(_tail)
            && !/FATAL|Unexpected error|pm2 重启截断/.test(_tail)) {
          obj.status = 'done';
        }
        jobs.set(obj.id, {
          id: obj.id, status: obj.status, started: obj.started,
          finished: (obj as Job & { finished?: number }).finished ?? ((obj as Job & { updated?: number }).updated ?? undefined),
          logs: obj.logs ?? [], result: obj.result ?? null,
        });
        n++;
      } catch (e) { console.warn('[jobs-persist] hydrate fail', f, e); }
    }
    console.log(`[jobs-persist] hydrated ${n} replit jobs from disk`);
  } catch (e) { console.warn('[jobs-persist] hydrate dir fail', e); }
})();


function makeJobId() { return `rpl_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`; }
function pick<T>(arr: T[]): T { return arr[Math.floor(Math.random() * arr.length)]; }

function classifyReplitJob(jobId: string) {
  if (jobId.startsWith("pipe_")) return { source: "replit", kind: "pipeline_full", title: "全自动流水线" };
  if (jobId.startsWith("rpl_")) return { source: "replit", kind: "replit_job", title: "Replit 注册/部署" };
  return { source: "replit", kind: "legacy_signup", title: "注册任务" };
}

function normalizeReplitStatus(status: Job["status"] | string) {
  return status === "error" ? "error" : status;
}

router.get("/replit/jobs", (_req, res) => {
  const list = Array.from(jobs.values()).map((job) => ({
    id: job.id,
    ...classifyReplitJob(job.id),
    status: normalizeReplitStatus(job.status),
    startedAt: job.started,
    logCount: job.logs.length,
    accountCount: Array.isArray(job.result?.results)
      ? (job.result.results as unknown[]).filter((r) => (r as Record<string, unknown>).ok).length
      : 0,
    finishedAt: job.finished ?? null,
    exitCode: null,
    lastLog: job.logs.length ? ((): { type: string; message: string } => {
      // v7.88: derive type from log content (same regex as GET /:jobId handler) instead
      // of job.status — fixes monitor showing successful tail line as red "error"
      // pill just because the job overall ended in error state.
      const line = job.logs.at(-1) ?? "";
      const t = /fatal|error|失败|✗|❌/i.test(line) ? "error" : /✓|✅|成功|done/i.test(line) ? "success" : "log";
      return { type: t, message: line };
    })() : null,
  })).sort((a, b) => b.startedAt - a.startedAt);
  res.json({ success: true, jobs: list });
});

router.get("/replit/jobs/:jobId", (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) { res.status(404).json({ success: false, error: "job not found" }); return; }
  const since = Number(req.query.since ?? 0);
  const logs = job.logs.slice(since).map((line) => ({
    type: /fatal|error|失败|✗|❌/i.test(line) ? "error" : /✓|✅|成功|done/i.test(line) ? "success" : "log",
    message: line,
  }));
  const _end = job.finished ?? Date.now();
  const _resultObj = (job.result ?? {}) as Record<string, unknown>;
  const _accountCount = Array.isArray(_resultObj.results)
    ? (_resultObj.results as unknown[]).filter((r) => (r as Record<string, unknown>).ok).length
    : 0;
  res.json({
    success: true,
    jobId: job.id,
    ...classifyReplitJob(job.id),
    status: normalizeReplitStatus(job.status),
    startedAt: job.started,
    finishedAt: job.finished ?? null,
    elapsed: Math.round((_end - job.started) / 1000),
    summary: typeof _resultObj.summary === "string" ? _resultObj.summary : null,
    accountCount: _accountCount,
    logs,
    nextSince: job.logs.length,
    result: job.result,
    exitCode: null,
  });
});

router.delete("/replit/jobs/:jobId", (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) { res.status(404).json({ success: false, error: "job not found" }); return; }
  if (job.status === "running") {
    job.status = "error";
    job.finished = Date.now();
    job.logs.push(`[${new Date().toISOString().slice(11,19)}] ⚠ 用户在监控中心标记停止（后台子进程如已启动可能继续到自然结束）`);
    persistJob(req.params.jobId, true);
  }
  res.json({ success: true });
});

// 批量清理所有已完成/出错的 jobs（清空内存中的 job 记录）
router.delete("/replit/jobs", (req, res) => {
  const killRunning = req.query.all === "1";
  let cleared = 0;
  for (const [id, job] of jobs.entries()) {
    if (job.status === "error" || job.status === "done") {
      jobs.delete(id);
      cleared++;
    } else if (killRunning && job.status === "running") {
      job.status = "error";
      job.logs.push(`[${new Date().toISOString().slice(11,19)}] ⚠ 强制停止`);
      jobs.delete(id);
      cleared++;
    }
  }
  res.json({ success: true, cleared });
});

const REPLIT_USERNAME_ADJS = [
  "amber","ancient","arctic","autumn","azure","binary","brave","bright","calm","cedar","clear","clever","cosmic","crimson","crystal","daily","deep","distant","drift","dusty","early","ember","fair","fast","forest","fresh","frost","gentle","golden","green","hidden","honest","ivory","jade","keen","kind","lively","lunar","maple","mellow","misty","modern","neon","nimble","noble","north","ocean","opal","polar","quiet","rapid","river","ruby","sage","silent","silver","solar","solid","spring","steady","stellar","stone","storm","summer","swift","tidal","true","urban","velvet","violet","warm","wild","winter","young","zen"
];
const REPLIT_USERNAME_NOUNS = [
  "acorn","anchor","atlas","badger","beacon","bear","brook","canyon","cedar","comet","coral","dawn","dove","eagle","ember","falcon","field","finch","forest","fox","glade","harbor","hawk","heron","island","ivy","jaguar","lake","lantern","leaf","lion","maple","meadow","meteor","moon","nova","oasis","otter","panda","pearl","phoenix","pine","planet","raven","reef","river","robin","rocket","sage","shadow","sparrow","star","stone","summit","sun","tiger","trail","valley","violet","willow","wolf","zephyr"
];
function makeReplitUsername(): string {
  const adj = pick(REPLIT_USERNAME_ADJS);
  const noun = pick(REPLIT_USERNAME_NOUNS);
  const num3 = Math.floor(Math.random() * 900) + 100;
  const num4 = Math.floor(Math.random() * 9000) + 1000;
  const tail = Math.random().toString(36).replace(/[^a-z]/g, "").slice(0, 2) || "xq";
  const patterns = [
    `${adj}${noun}${num3}`,
    `${adj}${noun}${tail}${num3}`,
    `${noun}${adj}${num3}`,
    `${adj}${tail}${noun}${num4}`,
    `${noun}${num4}${adj.slice(0, 3)}`,
  ];
  return pick(patterns).slice(0, 24);
}

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

function _readBrokerExitEnv(): Record<string, string> {
  // v7.98 — read /tmp/replit-broker/exit.json (written by start-browser-model.sh)
  // and inject BROWSER_PROXY / BROKER_EXIT_FAMILY / BROKER_EXIT_SOCKS_PORT into
  // python child process env. api-server pm2 process does not export these
  // (broker is a separate pm2 process), so without this injection python sees
  // empty env and v7.71-era fallback paths trigger SKIP wrongly.
  try {
    const fs = require("fs") as typeof import("fs");
    const raw = fs.readFileSync("/tmp/replit-broker/exit.json", "utf8");
    const j = JSON.parse(raw) as { family?: string; port?: string | number };
    const fam = String(j.family || "").trim().toLowerCase();
    const port = String(j.port || "").trim();
    const env: Record<string, string> = {};
    if (fam) env.BROKER_EXIT_FAMILY = fam;
    if (port) env.BROKER_EXIT_SOCKS_PORT = port;
    if (fam === "socks" && port) env.BROWSER_PROXY = `socks5://127.0.0.1:${port}`;
    else if (fam === "warp") env.BROWSER_PROXY = "socks5://127.0.0.1:40000";
    else env.BROWSER_PROXY = ""; // direct
    return env;
  } catch {
    return {};
  }
}

function runPython(script: string, arg: unknown, timeoutMs = 240_000, onLine?: (line: string) => void): Promise<{
  ok: boolean; raw: string; parsed: Record<string, unknown>;
}> {
  return new Promise((resolve) => {
    const _brokerEnv = _readBrokerExitEnv();
    const child = spawn(PYTHON, [script, JSON.stringify(arg)], {
      env: { ...process.env, PYTHONUNBUFFERED: "1", ..._brokerEnv },
      detached: true,
    });
    let out = "";
    // v7.88: line-buffered stdout/stderr forwarder so caller (e.g. /replit/register
    // handler) can pump python progress into job.logs → 实时监控中心 finally sees
    // [CDP] WARP exit_ip / [step2-miss] body_check / Canvas inject ✓ etc. previously
    // only visible in pm2 raw log. Without onLine: behavior is identical to before.
    let _outBuf = "";
    let _errBuf = "";
    const _emitLines = (which: "out" | "err", chunk: string) => {
      if (!onLine) return;
      const ref = which === "out" ? _outBuf + chunk : _errBuf + chunk;
      const lines = ref.split(/\r?\n/);
      const tail = lines.pop() ?? "";
      if (which === "out") _outBuf = tail; else _errBuf = tail;
      for (const ln of lines) { if (ln.trim()) onLine(ln); }
    };
    child.stdout.on("data", (d: Buffer) => { const t = d.toString(); out += t; process.stdout.write(d); _emitLines("out", t); });
    child.stderr.on("data", (d: Buffer) => { const t = d.toString(); process.stderr.write(d); _emitLines("err", t); });
    let settled = false;
    const finish = (result: { ok: boolean; raw: string; parsed: Record<string, unknown> }) => {
      if (settled) return;
      settled = true;
      resolve(result);
    };
    const timer = setTimeout(() => {
      try { process.kill(-child.pid!, "SIGTERM"); } catch { try { child.kill("SIGTERM"); } catch {} }
      setTimeout(() => { try { process.kill(-child.pid!, "SIGKILL"); } catch {} }, 3000).unref();
      finish({ ok: false, raw: out, parsed: { error: "timeout" } });
    }, timeoutMs);
    child.on("close", () => {
      clearTimeout(timer);
      const last = out.trim().split("\n").at(-1) ?? "{}";
      try { finish({ ok: true, raw: out, parsed: JSON.parse(last) }); }
      catch { finish({ ok: false, raw: out, parsed: { error: last.slice(0, 300) } }); }
    });
    child.on("error", (e) => { clearTimeout(timer); finish({ ok: false, raw: "", parsed: { error: e.message } }); });
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
      const r = await microsoftFetch(
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
      "UPDATE accounts SET tags = (SELECT NULLIF(array_to_string(ARRAY(SELECT DISTINCT trim(t) FROM unnest(string_to_array(COALESCE(tags,'') || ',token_invalid', ',')) AS t WHERE trim(t) <> ''), ','), ''), status='suspended', updated_at=NOW() WHERE id=$1",
      [acc.id]
    );
    return null;
  }

  // ── 3. OAuth refresh_token 换新 access_token ─────────────────────────────
  let newAccessToken = "";
  let newRefreshToken = rt;
  try {
    const tr = await microsoftFetch("https://login.microsoftonline.com/common/oauth2/v2.0/token", {
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
        "UPDATE accounts SET tags = (SELECT NULLIF(array_to_string(ARRAY(SELECT DISTINCT trim(t) FROM unnest(string_to_array(COALESCE(tags,'') || ',token_invalid', ',')) AS t WHERE trim(t) <> ''), ','), ''), status='suspended', updated_at=NOW() WHERE id=$1",
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
      "UPDATE accounts SET tags = (SELECT NULLIF(array_to_string(ARRAY(SELECT DISTINCT trim(t) FROM unnest(string_to_array(COALESCE(tags,'') || ',inbox_error', ',')) AS t WHERE trim(t) <> ''), ','), ''), updated_at=NOW() WHERE id=$1",
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
  const requestedEmail = typeof req.body?.email === "string" && req.body.email.trim() ? req.body.email.trim().toLowerCase() : null;
  const useCdp = req.body?.useCdp !== false && req.body?.use_cdp !== false; // default true: drive CDP+broker+warmup path
  const jobId    = makeJobId();
  const job: Job = { id: jobId, status: "running", started: Date.now(), logs: [], result: null };
  jobs.set(jobId, job);
  persistJob(jobId, true);

  function log(msg: string) {
    const line = `[${new Date().toISOString().slice(11, 19)}] ${msg}`;
    job.logs.push(line);
    console.log(`[replit-reg][${jobId}] ${msg}`);
    persistJob(jobId);
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
             AND COALESCE(tags, '') NOT LIKE '%abuse_mode%'
             AND ($1::text IS NULL OR LOWER(email) = $1)
             AND NOT EXISTS (
               SELECT 1 FROM accounts r
               WHERE r.platform = 'replit'
                 AND r.email = accounts.email
             )
             AND NOT EXISTS (
               SELECT 1 FROM archives ar
               WHERE ar.platform = 'replit'
                 AND ar.email = accounts.email
             )
           ORDER BY
             -- v7.80: prefer pre-scanned available emails (replit_avail), then unknown,
             -- defer pre-scanned taken/error emails to the very end so picker hits
             -- known-good candidates on the first try.
             CASE WHEN COALESCE(tags,'') LIKE '%replit_avail%' THEN 0
                  WHEN COALESCE(tags,'') LIKE '%replit_unknown%' THEN 2
                  ELSE 1 END,
             CASE WHEN COALESCE(tags,'') LIKE '%inbox_verified%' THEN 0 ELSE 1 END,
             RANDOM()
           LIMIT 10`,
          [requestedEmail]
        );

        if (!candidates.length) {
          log(requestedEmail ? `No available Outlook account for ${requestedEmail}` : "No available Outlook accounts — run FullWorkflow to generate more");
          results.push({ ok: false, error: "No Outlook accounts available" });
          continue;
        }

        // ── Step 2: 逐个 Outlook 账号尝试注册 ────────────────────────────
        let accountDone = false;

        for (const outlook of candidates) {
          const username = makeReplitUsername();
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
          let cfBlockedCount   = 0; // cf_api_blocked count → escalate Tor → direct
          let cfJsTimeoutCount = 0; // consecutive cf_js_challenge_timeout → inject Tor after threshold
          let torRateLimited   = false; // Tor IP also got account_rate_limited → never re-inject Tor
          const rateLimitedIps = new Set<string>(); // track unique IPs that rate-limited this email
          const regScript = path.join(API_DIR, "replit_register.py");
          let regOk  = false;
          let exitIp = "";
          let lastErr = "";
          const dynXrayCleanups: Array<() => void> = []; // cleanup fns for dynamic xray instances
          let parsed: Record<string, unknown> = {}; // v7.57 lifted out of attempt loop so post-loop verify_url access works

          // 每个Outlook账号：信誉好的端口优先，避免重复
          let portQueue = sortedByReputation(availablePorts());
          // v7.71: env override FORCE_REGISTER_PORTS="0" → only use DIRECT (VPS IP).
          //   When all xray subnodes are dead (iam.jimhacker.qzz.io down), regular SOCKS5
          //   ports hang on data probe → registration stuck. DIRECT bypasses xray entirely.
          const _forceRaw = process.env.FORCE_REGISTER_PORTS;
          if (_forceRaw && _forceRaw.trim()) {
            portQueue = _forceRaw.split(",").map((x) => parseInt(x.trim(), 10)).filter((n) => Number.isFinite(n));
            log(`    [force-ports] FORCE_REGISTER_PORTS=${_forceRaw} → portQueue=[${portQueue.join(",")}]`);
          }
          if (portQueue.length === 0) {
            log(`  [skip] No available ports for this Outlook, skipping`);
            continue; // skip to next Outlook
          }

          let lastUsedPort = -1; // v7.78p: 记录 success break 时实际用的端口, 供 Step4 INSERT
          for (let attempt = 1; attempt <= 3; attempt++) {
            const livePorts = availablePorts();
            if (livePorts.length === 0) {
              log(`    No live SOCKS ports available, stop retrying this Outlook`);
              break;
            }
            // Special ports (Tor=9050, direct=0) are always valid; skip livePorts check for them
            const SPECIAL_PORTS = new Set([TOR_SOCKS_PORT, DIRECT_PORT]);
            let tryPort = portQueue.find((p) => SPECIAL_PORTS.has(p) || livePorts.includes(p));
            if (tryPort === undefined) {  // use === undefined, not !tryPort (0 is falsy)
              portQueue = sortedByReputation(livePorts);
              tryPort = portQueue[0];
            }
            portQueue = portQueue.filter((p) => p !== tryPort);
            if (portQueue.length === 0) portQueue = sortedByReputation(availablePorts()).filter((p) => p !== tryPort);
            log(`    Attempt ${attempt}/3 via SOCKS5:${tryPort}`);

            ({ parsed } = await runPython(regScript, {
              email: outlook.email,
              username,
              password,
              proxy: portToProxy(tryPort),
              headless,
              max_wait: 90,
              outlook_refresh_token: outlook.refresh_token ?? "",
              use_cdp: useCdp,
            }, 240_000, (line) => log(`      [py] ${line}`)));

            // 记录代理使用（成功/失败均记录）
            await recordXrayProxyUsage(tryPort, dbE as unknown as (sql: string, params?: unknown[]) => Promise<unknown>);

            exitIp  = String(parsed.exit_ip ?? "");
            lastErr = String(parsed.error ?? "");

            // v7.81: broker-watchdog hook — feed every register attempt result so
            // N consecutive CF blocks within a window auto-rotate the broker (which
            // re-runs _pick_browser_proxy, picking a fresh WARP/SOCKS exit).
            recordBrokerSignal(parsed.ok === true, lastErr, log);

            if (parsed.ok) {
              portLastGood.set(tryPort, Date.now()); // successful registration
              lastUsedPort = tryPort; // v7.78p: 让 Step4 INSERT 能拿到真实端口
              log(`    ✅ Registered! phase=${parsed.phase} exit_ip=${exitIp}`);
              regOk = true;
              break;
            }

            log(`    ✗ Attempt ${attempt}: ${lastErr.slice(0, 100)}`);

            // 邮箱已被用 → 落 placeholder 保留 outlook→replit 映射关系，再标 replit_used
            if (lastErr.toLowerCase().includes("already") && lastErr.toLowerCase().includes("use")) {
              portLastGood.set(tryPort, Date.now()); // port got form response → mark good
              log(`    Email already on Replit → 写 placeholder + marking replit_used`);
              // v7.84: actual_proxy_port (broker WARP path) takes priority over xray hint
              const actualPortPh = Number(parsed.actual_proxy_port ?? 0);
              const portPh = actualPortPh > 0 ? actualPortPh : tryPort;
              await dbE(
                `INSERT INTO accounts (platform, email, password, username, status, notes, tags, exit_ip, proxy_port)
                 VALUES ('replit', $1, NULL, NULL, 'exists_no_password', 'email already on Replit, password not captured', 'replit,exists_no_password', $2, $3)
                 ON CONFLICT (platform, email) DO NOTHING`,
                [outlook.email, exitIp, portPh]
              ).catch(e => log(`    placeholder insert warn: ${e}`));
              await dbE(
                "UPDATE accounts SET tags = CASE WHEN string_to_array(COALESCE(tags,''), ',') @> ARRAY['replit_used'] THEN tags ELSE NULLIF(TRIM(BOTH ',' FROM COALESCE(tags,'') || ',replit_used'), ',') END, updated_at = NOW() WHERE id = $1",
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
            // signup_username_field_missing → step1可能已成功，或被限速 → 重试看结果
            if (lastErr.includes("signup_username_field_missing")) {
              // 如果是第6次 → 假设成功
              if (attempt >= 10) {
                log(`    username_field_missing on last attempt → assuming step1 succeeded`);
                regOk = true;
              } else {
                log(`    username_field_missing attempt ${attempt} → switch port + retry`);
                portLastGood.set(tryPort, Date.now());
              }
              break;
            }
            // captcha_token_invalid → Replit server拒绝token → 立即rate-limit该email → 跳下一个Outlook
            if (lastErr.includes("captcha_token_invalid")) {
              captchaFailCount++;
              // v8.33 ROOT-FIX: ss-pool 是多 ISP 真实节点 (TW Chunghwa / GB M247 / NL Greenhost ...).
              // captcha_token_invalid = 该节点出口 IP 被 reCAPTCHA Enterprise 评低分
              //   (实证 2026-04-27: 10859 NL Greenhost / 10851 US Datacamp 被拒, 10857/10855 通过).
              // 修复: 将失败 port 加入 15min 冷却 (reCAPTCHA score 短期翻不了盘),
              //       改用 portQueue 中下一个 port 重试; 阈值 1→2 (允许试 1 次换 port).
              cfBannedUntil.set(tryPort, Date.now() + 15 * 60 * 1000);
              const cfIpC = xrayPortCfIp.get(tryPort);
              const _cfNote = cfIpC ? ` (CF IP ${cfIpC})` : "";
              if (captchaFailCount >= 2) {
                log(`    captcha_token_invalid x${captchaFailCount} on port ${tryPort}${_cfNote} → 已换 port 重试仍失败, 跳 Outlook`);
                break;
              }
              log(`    captcha_token_invalid (${captchaFailCount}/2) on port ${tryPort}${_cfNote} → 冷却 port 15min + 立即换 port 重试`);
              if (cfIpC) {
                // 同时旋转该 port 在 xray.json 里绑定的 CF backend IP, 缓解未来再被路由到此 port 时仍是同一被封 IP
                try { rotateCfIpInXray(cfIpC); } catch {}
              }
              continue;
            }
            // account_rate_limited:
            //   - port 1090 (WS-proxy bridge → Replit cloud IP): Replit.com rate-limits its own cloud IPs → skip email immediately
            //   - other ports: track unique exit IPs; 2+ different IPs rate-limited same email → email-level, skip
            if (lastErr.includes("account_rate_limited")) {
              portLastGood.set(tryPort, Date.now()); // port got response
              rateLimitedIps.add(exitIp || String(tryPort));
              if (tryPort === 1090) {
                // WS bridge endpoint is rate-limited by Replit.com → skip this email (don't mark port dead)
                log(`    account_rate_limited on WS-proxy bridge (port ${tryPort}, exit: ${exitIp}) → Replit cloud IP rate-limited, skip email`);
                break;
              }
              const rateLimitThreshold = requestedEmail ? Math.max(4, XRAY_PORTS.length - 1) : 2;
              if (rateLimitedIps.size >= rateLimitThreshold) {
                log(`    account_rate_limited on ${rateLimitedIps.size} different IPs → email-level rate limit, skip Outlook`);
                break; // 换下一个 Outlook 账号
              }
              log(`    ⏳ account_rate_limited on port ${tryPort} (exit: ${exitIp}) → rotate CF IP + spawn dynamic xray`);
              const cfIpRl = xrayPortCfIp.get(tryPort);
              if (cfIpRl) rotateCfIpInXray(cfIpRl);
              // Track if Tor itself was rate-limited → mark it so we never re-inject
              if (tryPort === TOR_SOCKS_PORT) {
                torRateLimited = true;
                log(`    [tor] Tor IP also rate-limited → mark torRateLimited, skip Tor re-injection`);
              }
              // Inject Tor first as non-CF/non-GCP exit (free, diverse exit nodes)
              if (!torRateLimited && !portQueue.includes(TOR_SOCKS_PORT)) {
                log("      [policy] 已禁用 Tor 出口（仅友节点）");
                log(`    → pre-queued Tor SOCKS5:9050 (non-CF/non-GCP exit, tries before CF xray)`);
              }
              // Spawn a fresh xray VLESS relay with a new CF IP (different exit IP)
              const dynXray = await spawnDynamicXray(exitIp || undefined);
              if (dynXray) {
                log(`    [dyn-xray] spawned SOCKS5:${dynXray.port} via CF IP ${dynXray.cfIp} → queued after Tor`);
                dynXrayCleanups.push(dynXray.cleanup);
                // Insert dynXray AFTER Tor in queue so Tor is tried first
                const torIdx = portQueue.indexOf(TOR_SOCKS_PORT);
                if (torIdx >= 0) portQueue.splice(torIdx + 1, 0, dynXray.port);
                else portQueue.unshift(dynXray.port);
              } else {
                log(`    [dyn-xray] spawn failed → Tor will be tried`);
              }
              continue;
            }

            // cf_api_blocked: CF CDN exit IP blocked by Cloudflare WAF / Just-a-moment challenge.
            // v8.34: failing port → 10min cooldown so picker rotates to a different ss-pool exit.
            //        Tor/direct injection 已被策略禁用 (仅友节点), 改为纯 port-rotate.
            if (lastErr.includes("cf_api_blocked")) {
              cfBlockedCount++;
              cfBannedUntil.set(tryPort, Date.now() + 10 * 60 * 1000);
              const _cfIpB = xrayPortCfIp.get(tryPort);
              const _cfNoteB = _cfIpB ? ` (CF IP ${_cfIpB})` : "";
              if (cfBlockedCount >= 3) {
                log(`    ⛔ cf_api_blocked x${cfBlockedCount} on port ${tryPort}${_cfNoteB} → 已尝试 ${cfBlockedCount} 个 port 全被 CF 挑战, 跳 Outlook`);
                break;
              }
              log(`    ⛔ cf_api_blocked (${cfBlockedCount}/3) on port ${tryPort}${_cfNoteB} (exit: ${exitIp}) → 冷却该 port 10min + 立即换 port 重试`);
              if (_cfIpB) {
                try { rotateCfIpInXray(_cfIpB); } catch {}
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
              lastErr.includes("ERR_EMPTY_RESPONSE")       ||  // 空响应→换IP
              lastErr.includes("ERR_SOCKS_CONNECTION_FAILED");  // SOCKS5桥未就绪→立即换端口

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
              if (lastErr.includes("ERR_CONNECTION_CLOSED") || lastErr.includes("ERR_SOCKS_CONNECTION_FAILED")) {
                // port 1090 = WS-proxy bridge (Replit cloud IP): short 2min cooldown (repl may restart quickly)
                const deadMs = (tryPort === 1090 || tryPort === 1092) ? 2 * 60 * 1000 : 10 * 60 * 1000;
                portDeadUntil.set(tryPort, Date.now() + deadMs);
                log(`    [dead] port ${tryPort} dead ${deadMs/60000}min`);
              }
              const needsCfRotate =
                lastErr.includes("cf_ip_banned")             ||
                lastErr.includes("cf_hard_block")            ||
                lastErr.includes("cf_js_challenge_timeout")  ||  // 加回：JS challenge=IP信誉差→轮换IP
                lastErr.includes("ERR_CONNECTION_CLOSED")    ||
                lastErr.includes("ERR_EMPTY_RESPONSE")       ||
                lastErr.includes("ERR_CERT");
              if (needsCfRotate) {
                // 封禁冷却：永久封禁5min，JS challenge短暂1min（port 1090=WS-proxy bridge, Replit IP不走CF封禁）
                if (tryPort !== 1090 && tryPort !== 1092) {
                  if (lastErr.includes("cf_ip_banned") || lastErr.includes("cf_hard_block")) {
                    cfBannedUntil.set(tryPort, Date.now() + 5 * 60 * 1000);
                  } else if (lastErr.includes("cf_js_challenge_timeout")) {
                    cfBannedUntil.set(tryPort, Date.now() + 1 * 60 * 1000); // 1min cooldown
                    if (tryPort === TOR_SOCKS_PORT) {
                      // Tor itself got CF-challenged → disable Tor entirely
                      torRateLimited = true;
                      log(`    [tor] Tor IP also CF-challenged (${exitIp}) → mark torRateLimited`);
                    } else if (XRAY_PORTS.includes(tryPort)) {
                      // Only count static port CF timeouts (not dynamic, not Tor)
                      cfJsTimeoutCount++;
                    }
                    if (cfJsTimeoutCount >= 3 && !torRateLimited && !portQueue.includes(TOR_SOCKS_PORT)) {
                      log(`    [cf-js x${cfJsTimeoutCount}] All static ports CF-challenged → inject Tor SOCKS5:9050 (non-CF exit)`);
                      log("      [policy] 已禁用 Tor 出口（仅友节点）");
                    } else if (cfJsTimeoutCount >= 3 && torRateLimited && !portQueue.includes(DIRECT_PORT)) {
                      log(`    [cf-js x${cfJsTimeoutCount}] All static ports CF-challenged, Tor blocked → inject VPS direct (port 0)`);
                      log("      [policy] 已禁用直连 VPS 出口（仅友节点）");
                    } else if (torRateLimited && !portQueue.includes(DIRECT_PORT) && tryPort === TOR_SOCKS_PORT) {
                      // Tor just got blocked → inject VPS direct immediately
                      log(`    [tor] Tor CF-blocked → inject VPS direct (port 0)`);
                      log("      [policy] 已禁用直连 VPS 出口（仅友节点）");
                    }
                  }
                  const cfIp = xrayPortCfIp.get(tryPort);
                  if (cfIp) {
                    log(`    → rotate CF IP ${cfIp} in xray (pool → new IP)`);
                    rotateCfIpInXray(cfIp);
                    await new Promise(r => setTimeout(r, 2000));  // wait xray reload
                  }
                } else {
                  log(`    → port 1090 (WS-proxy bridge, Replit IP) CF issue → skip CF rotation, try next port`);
                  // WS bridge = Replit cloud IP, no CF rotation needed, handled above
                }
                // Dynamic xray port (>19999) with CF JS challenge → its CF CDN IP is fundamentally blocked
                // Inject Tor as non-CF, non-GCP exit IP alternative (once per email)
                if (lastErr.includes("cf_js_challenge_timeout") && tryPort > 19999 && !torRateLimited && !portQueue.includes(TOR_SOCKS_PORT)) {
                  log(`    [dyn-xray:${tryPort} cf-timeout] → inject Tor SOCKS5:9050 (non-CF exit)`);
                  log("      [policy] 已禁用 Tor 出口（仅友节点）");
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

          // Cleanup all dynamic xray processes used for this Outlook account
          for (const c of dynXrayCleanups) { try { c(); } catch {} }
          dynXrayCleanups.length = 0;

          if (!regOk) continue; // 换下一个 Outlook 账号

          // ── Step 3: 等待 Replit 发验证邮件 → 通过 Graph API 点击 ─────
          // Fast-path: python 已经从 Graph API 拿到 verify_url 并返回 → 直接 HTTP 点击,
          // 跳过容易扑空的收件箱轮询 (live-verify poller 也可能抢先点完导致永远找不到邮件)
          const pyVerifyUrl = typeof parsed.verify_url === "string" ? parsed.verify_url.trim() : "";
          let verified = false;

          if (pyVerifyUrl) {
            log(`  Step3: 直接点 python 已取的验证链接 (跳过收件箱轮询)`);
            const vr = await localPost("/api/tools/outlook/click-verify-link", {
              accountId: outlook.id,
              verifyUrl: pyVerifyUrl,
            }) as { success?: boolean; final_url?: string; error?: string };
            if (vr.success) {
              log(`    ✅ Verified! => ${vr.final_url?.slice(0, 70)}`);
              verified = true;
            } else {
              const errStr = String(vr.error ?? "").toLowerCase();
              // "invalid or has been used" = 链接已被消费 (python goto / live-verify) → 实际已激活
              if (errStr.includes("invalid") || errStr.includes("has been used") || errStr.includes("已使用")) {
                log(`    ✅ 链接已被消费 (视为已验证): ${String(vr.error).slice(0, 80)}`);
                verified = true;
              } else {
                log(`    ✗ 直接点击失败: ${String(vr.error).slice(0, 80)} → 退回收件箱轮询`);
              }
            }
          }

          if (!verified) {
            log("  Step3: Waiting for Replit verification email...");
            await new Promise(r => setTimeout(r, 12000));
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
              const errStr = String(vr.error ?? "no link yet").toLowerCase();
              if (errStr.includes("invalid") || errStr.includes("has been used") || errStr.includes("已使用")) {
                log(`    ✅ 链接已被消费 (视为已验证): ${String(vr.error).slice(0, 80)}`);
                verified = true;
                break;
              }
              log(`    Waiting... (${String(vr.error ?? "no link yet").slice(0, 60)})`);
              await new Promise(r => setTimeout(r, 15000));
            }
          }
          if (!verified) log("  Verification timed out (account may still be usable)");

          // ── Step 4: 写入 DB（必须成功，否则不允许标 outlook=replit_used，避免账号黑洞）
          log("  Step4: Saving to DB...");
          let dbInsertOk = false;
          try {
            await dbE(
              `INSERT INTO accounts (platform, email, password, username, status, notes, tags, exit_ip, proxy_port, user_agent, fingerprint_json)
               VALUES ('replit', $1, $2, $3, $4, $5, 'replit,subnode', $6, $7, $8, $9::jsonb)
               ON CONFLICT (platform, email) DO UPDATE
                 SET status = EXCLUDED.status,
                     username = EXCLUDED.username,
                     password = EXCLUDED.password,
                     exit_ip = EXCLUDED.exit_ip,
                     proxy_port = EXCLUDED.proxy_port,
                     user_agent = COALESCE(EXCLUDED.user_agent, accounts.user_agent),
                     fingerprint_json = COALESCE(EXCLUDED.fingerprint_json, accounts.fingerprint_json),
                     updated_at = NOW()`,
              [outlook.email, password, username,
               verified ? "registered" : "unverified",
               outlook.email, exitIp,
               // v7.84: actual_proxy_port (broker WARP=40000) overrides xray hint port
               // (lastUsedPort 是 xray pool 中尝试的端口, broker 实际可能切到 WARP)
               (() => {
                 const actualPort = Number(parsed.actual_proxy_port ?? 0);
                 if (actualPort > 0) return actualPort;
                 return lastUsedPort >= 0 ? lastUsedPort : pick(XRAY_PORTS);
               })(),
               String(parsed.user_agent ?? (parsed.fingerprint as Record<string, unknown> | undefined)?.user_agent ?? "") || null,
               parsed.fingerprint ? JSON.stringify(parsed.fingerprint) : null]
            );
            dbInsertOk = true;
            log(`  Step4 ✅ replit account saved (email=${outlook.email}, user=${username}, status=${verified ? 'registered' : 'unverified'})`);
          } catch (e) {
            log(`  Step4 ❌ DB INSERT 失败 (replit account NOT saved): ${String(e).slice(0,200)}`);
          }

          // 仅当 replit account 已落库时才标 outlook=replit_used，否则保持可用避免账号黑洞
          if (dbInsertOk) {
            await dbE(
              "UPDATE accounts SET tags = CASE WHEN string_to_array(COALESCE(tags,''), ',') @> ARRAY['replit_used'] THEN tags ELSE NULLIF(TRIM(BOTH ',' FROM COALESCE(tags,'') || ',replit_used'), ',') END, updated_at = NOW() WHERE id = $1",
              [outlook.id]
            ).catch(e => log(`  outlook tag update warn: ${e}`));
          } else {
            log(`  Outlook id=${outlook.id} 未标 replit_used — DB 落库失败，保持候选可重试`);
          }

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
    // v7.94: 脚本能跑到这里就是自然完成；okCount=0 不等于 error，把成败差异放在 result.summary
    job.status = "done";
    job.finished = Date.now();
    job.result = { results, summary: `${okCount}/${count} succeeded`, okCount, total: count };
    persistJob(jobId, true);
    log(`\nAll done: ${okCount}/${count} succeeded`);
    if (!requestedEmail) await checkAndRefillReplitPool(dbQ, log);
  })().catch(err => {
    job.status = "error";
    job.finished = Date.now();
    job.logs.push(`FATAL: ${String(err)}`);
    persistJob(jobId, true);
  });

  res.json({ success: true, jobId, message: `Replit registration started (${count} accounts)` });
});

// ── GET /api/replit/register/:jobId ──────────────────────────────────────────
router.get("/replit/register/:jobId", (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) { res.status(404).json({ success: false, error: "job not found" }); return; }
  res.json({
    jobId: job.id,
    status: job.status,
    startedAt: job.started,
    finishedAt: job.finished ?? null,
    elapsed: Math.round(((job.finished ?? Date.now()) - job.started) / 1000),
    logs: job.logs,
    result: job.result,
  });
});

// ── POST /api/replit/gateway-register ─────────────────────────────────────────
router.post("/replit/gateway-register", async (req, res) => {
  const { gatewayUrl, name } = req.body as { gatewayUrl: string; name?: string };
  if (!gatewayUrl) { res.status(400).json({ success: false, error: "missing gatewayUrl" }); return; }
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
          const tr = await microsoftFetch("https://login.microsoftonline.com/common/oauth2/v2.0/token", {
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
// 全自动流水线：Outlook 注册 → Reseek 注册 + 验证 → 友节点部署
// body: { target?: number, skipOutlook?: boolean, skipReseek?: boolean }
router.post("/pipeline/full", async (req, res) => {
  const {
    target = 3,           // 目标 Reseek 友节点数量
    skipOutlook  = false, // 跳过 Outlook 注册步骤（已有足够账号时）
    skipReseek   = false, // 跳过 Reseek 注册步骤
  } = req.body as { target?: number; skipOutlook?: boolean; skipReseek?: boolean };

  const jobId = `pipe_${Date.now().toString(36)}`;
  const job: Job = { id: jobId, status: "running", started: Date.now(), logs: [], result: null };
  jobs.set(jobId, job);
  persistJob(jobId, true);
  function log(msg: string) {
    const line = `[${new Date().toISOString().slice(11,19)}] ${msg}`;
    job.logs.push(line);
    console.log(`[pipeline][${jobId}] ${msg}`);
  }

  res.json({ success: true, jobId, message: `全自动流水线已启动 (目标${target}个友节点)` });

  (async () => {
    try {
      const { query: dbQ, execute: dbE } = await import("../db.js");

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
            count: need, headless: true, engine: "camoufox",
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
          "SELECT COUNT(*) AS count FROM accounts WHERE platform='replit' AND status IN ('active','registered')"
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

      // ── Step 4: 友节点登录部署 ─────────────────────────────────────────────
      log("=== Step4: 友节点登录部署 ===");
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
            gateway_url: "http://45.205.27.69:8080", headless: true, source_project: "https://replit.com/@skingsbp/gh-cli-install", deploy: true },
          300_000
        );
        if (deployR.parsed.ok) {
          const webUrl = String(deployR.parsed.webview_url ?? "");
          log(`  ✓ 部署成功 webview=${webUrl}`);
          // 标记已部署 + 保存 webview URL
          await dbE(
            "UPDATE accounts SET tags=(SELECT NULLIF(array_to_string(ARRAY(SELECT DISTINCT trim(t) FROM unnest(string_to_array(COALESCE(tags,'') || ',subnode_deployed', ',')) AS t WHERE trim(t) <> ''), ','), ''), notes=$1, updated_at=NOW() WHERE id=$2",
            [webUrl, acc.id]
          );
          // 注册为网关友节点
          if (webUrl) {
            try {
              await localPost("/api/gateway/self-register", {
                gatewayUrl: webUrl, name: acc.username || acc.email.split("@")[0],
              });
              log(`  ✓ 已注册友节点 ${webUrl}`);
            } catch (e) { log(`  友节点注册失败: ${e}`); }
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
      job.finished = Date.now();
      job.result = { total: parseInt(final[0]?.count ?? "0", 10), target };
      persistJob(jobId, true);
    } catch (e) {
      log(`FATAL: ${e}`);
      job.status = "error";
      job.finished = Date.now();
      persistJob(jobId, true);
    }
  })().catch((e) => {
    job.status = "error";
    job.finished = Date.now();
    job.logs.push(`FATAL: ${e}`);
    persistJob(jobId, true);
  });
});

// ── GET /api/pipeline/full/:jobId ─────────────────────────────────────────────
router.get("/pipeline/full/:jobId", (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) { res.status(404).json({ success: false, error: "job not found" }); return; }
  res.json({ jobId: job.id, status: job.status, startedAt: job.started, finishedAt: job.finished ?? null, elapsed: Math.round(((job.finished ?? Date.now()) - job.started) / 1000), logs: job.logs, result: job.result });
});


// ── POST /api/replit/deploy-subnode ──────────────────────────────────────────
// 对单个 Reseek 账号部署 agent 友节点 (浏览器自动化)
// body: { replitId?: number, email?: string }
router.post("/replit/deploy-subnode", async (req, res) => {
  const { replitId, email } = req.body as { replitId?: number; email?: string };
  const jobId = makeJobId();
  const job: Job = { id: jobId, status: "running", started: Date.now(), logs: [], result: null };
  jobs.set(jobId, job);
  persistJob(jobId, true);
  function log(msg: string) {
    const line = `[${new Date().toISOString().slice(11,19)}] ${msg}`;
    job.logs.push(line);
    console.log(`[deploy-sub][${jobId}] ${msg}`);
  }
  res.json({ success: true, jobId, message: "友节点部署任务已启动" });

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
      if (!acc) { job.status = "error"; job.finished = Date.now(); job.result = { error: "账号未找到" }; persistJob(jobId, true); return; }
      if (!acc.password) { job.status = "error"; job.finished = Date.now(); job.result = { error: "无密码，无法登录" }; persistJob(jobId, true); return; }
      log(`开始部署 ${acc.email} (id=${acc.id}, username=${acc.username})...`);

      // 获取 outlook token
      const ol = await dbQ<{ token: string | null }>(
        "SELECT token FROM accounts WHERE platform=\'outlook\' AND email=$1", [acc.email]
      );
      const outlookTok = ol[0]?.token ?? "";

      const deployR = await runPython(
        path.join(API_DIR, "replit_deploy_agent.py"),
        { email: acc.email, password: acc.password, outlook_token: outlookTok,
          gateway_url: "http://45.205.27.69:8080", headless: true, source_project: "https://replit.com/@skingsbp/gh-cli-install", deploy: true },
        300_000
      );
      log(`部署脚本输出: ${deployR.raw.slice(-400)}`);
      if (deployR.parsed.ok) {
        const webUrl = String(deployR.parsed.webview_url ?? "");
        const replUrl = String(deployR.parsed.repl_url ?? "");
        log(`✓ 部署成功 webview=${webUrl}`);
        await dbE(
          "UPDATE accounts SET tags=(SELECT NULLIF(array_to_string(ARRAY(SELECT DISTINCT trim(t) FROM unnest(string_to_array(COALESCE(tags,'') || ',subnode_deployed', ',')) AS t WHERE trim(t) <> ''), ','), ''), notes=$1, updated_at=NOW() WHERE id=$2",
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
        job.finished = Date.now();
        job.result = { ok: true, webview_url: webUrl, repl_url: replUrl, email: acc.email };
        persistJob(jobId, true);
      } else {
        const errMsg = String(deployR.parsed.error ?? deployR.raw.slice(-300) ?? "未知错误");
        log(`✗ 部署失败: ${errMsg.slice(0, 200)}`);
        job.status = "error";
        job.finished = Date.now();
        job.result = { ok: false, error: errMsg.slice(0, 200) };
        persistJob(jobId, true);
      }
    } catch (e) {
      log(`FATAL: ${e}`);
      job.status = "error";
      job.finished = Date.now();
      job.result = { error: String(e) };
      persistJob(jobId, true);
    }
  })();
});

// ── GET /api/replit/deploy-subnode/:jobId ─────────────────────────────────────
router.get("/replit/deploy-subnode/:jobId", (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) { res.status(404).json({ success: false, error: "job not found" }); return; }
  res.json({ jobId: job.id, status: job.status, startedAt: job.started, finishedAt: job.finished ?? null, elapsed: Math.round(((job.finished ?? Date.now()) - job.started) / 1000), logs: job.logs, result: job.result });
});


// ── POST /api/admin/replay-audit ──────────────────────────────────────────────
// v7.78p — 跑 replay_session.py 校验所有 active 账号真实登录态:
//   logged_in=true  → status='active', notes append "[audit ...] ok"
//   logged_in=false → status='stale',  notes append "[audit ...] stale: reason"
//   script error    → 不改 status, 仅在 summary 里报错
// body: { scope?: "active"|"all", ids?: number[], dryRun?: boolean,
//         concurrency?: 1..3 (default 1), timeoutMs?: 30000..300000 (default 180000) }
const REPLAY_SCRIPT = "/root/Toolkit/artifacts/api-server/replay_session.py";

function runReplay(idOrUser: string | number, timeoutMs = 180_000): Promise<{
  ok: boolean; raw: string; parsed: Record<string, unknown>;
}> {
  return new Promise((resolve) => {
    const child = spawn(PYTHON, [REPLAY_SCRIPT, String(idOrUser)], {
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
      detached: true,
    });
    let out = "";
    child.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    child.stderr.on("data", () => { /* swallow stderr (verbose patchright) */ });
    let settled = false;
    const finish = (r: { ok: boolean; raw: string; parsed: Record<string, unknown> }) => {
      if (settled) return; settled = true; resolve(r);
    };
    const timer = setTimeout(() => {
      try { process.kill(-child.pid!, "SIGTERM"); } catch { try { child.kill("SIGTERM"); } catch {} }
      setTimeout(() => { try { process.kill(-child.pid!, "SIGKILL"); } catch {} }, 3000).unref();
      finish({ ok: false, raw: out, parsed: { error: "replay_timeout" } });
    }, timeoutMs);
    child.on("close", () => {
      clearTimeout(timer);
      const last = out.trim().split("\n").at(-1) ?? "{}";
      try { finish({ ok: true, raw: out, parsed: JSON.parse(last) }); }
      catch { finish({ ok: false, raw: out, parsed: { error: "parse_failed", raw_tail: last.slice(-300) } }); }
    });
    child.on("error", (e) => { clearTimeout(timer); finish({ ok: false, raw: "", parsed: { error: e.message } }); });
  });
}

// v7.78r — 抽出 audit 核心 + lock + history 写入, cron 与 endpoint 共享
let _auditRunning = false;
let _lastAuditStartedAt = 0;
let _lastAuditFinishedAt = 0;

export type ReplayAuditOpts = {
  scope?: "active" | "all";
  ids?: number[];
  dryRun?: boolean;
  concurrency?: number;
  timeoutMs?: number;
};
export type ReplayAuditResult = {
  ok: boolean;
  source: string;
  scope: string;
  dryRun: boolean;
  concurrency: number;
  timeoutMs: number;
  total: number;
  scanned: number;
  active: number;
  stale: number;
  errors: number;
  duration_ms: number;
  details: Array<Record<string, unknown>>;
  history_id?: number;
  skipped?: string;
};

export function isReplayAuditRunning(): boolean { return _auditRunning; }
export function getReplayAuditState(): { running: boolean; lastStartedAt: number; lastFinishedAt: number } {
  return { running: _auditRunning, lastStartedAt: _lastAuditStartedAt, lastFinishedAt: _lastAuditFinishedAt };
}

export async function executeReplayAudit(opts: ReplayAuditOpts, source = "manual"): Promise<ReplayAuditResult> {
  const scope = opts.scope ?? "active";
  const dryRun = opts.dryRun === true;
  const concurrency = Math.max(1, Math.min(3, opts.concurrency ?? 1));
  const timeoutMs = Math.max(30_000, Math.min(300_000, opts.timeoutMs ?? 180_000));

  if (_auditRunning) {
    return {
      ok: false, source, scope, dryRun, concurrency, timeoutMs,
      total: 0, scanned: 0, active: 0, stale: 0, errors: 0, duration_ms: 0,
      details: [], skipped: "audit_already_running",
    };
  }
  _auditRunning = true;
  _lastAuditStartedAt = Date.now();

  const { query: dbQ, execute: dbE } = await import("../db.js");
  const t0 = Date.now();
  const startedAt = new Date(t0).toISOString();
  const details: Array<Record<string, unknown>> = [];
  let activeCnt = 0, staleCnt = 0, errCnt = 0;
  let rows: Array<{ id: number; username: string; status: string }> = [];

  try {
    if (Array.isArray(opts.ids) && opts.ids.length) {
      rows = await dbQ<{ id: number; username: string; status: string }>(
        "SELECT id, username, status FROM accounts WHERE platform='replit' AND id = ANY($1::int[]) AND username IS NOT NULL ORDER BY id",
        [opts.ids]
      );
      const found = new Set(rows.map((r) => r.id));
      for (const id of opts.ids) {
        if (!found.has(id)) {
          details.push({ id, ok: false, error: "id not in DB or username is NULL", action: "not-found" });
          errCnt++;
        }
      }
    } else if (scope === "all") {
      rows = await dbQ<{ id: number; username: string; status: string }>(
        "SELECT id, username, status FROM accounts WHERE platform='replit' AND username IS NOT NULL ORDER BY id"
      );
    } else {
      rows = await dbQ<{ id: number; username: string; status: string }>(
        "SELECT id, username, status FROM accounts WHERE platform='replit' AND status IN ('active','registered','unverified') AND username IS NOT NULL ORDER BY id"
      );
    }

    for (let i = 0; i < rows.length; i += concurrency) {
      const batch = rows.slice(i, i + concurrency);
      const results = await Promise.all(batch.map((acc) => runReplay(acc.id, timeoutMs)));
      for (let j = 0; j < batch.length; j++) {
        const acc = batch[j];
        const r = results[j];
        const pp = r.parsed as { logged_in?: boolean; http_status?: number; final_url?: string; error?: string };
        const loggedIn = pp.logged_in === true;
        const ts = new Date().toISOString();
        let action = "no-change";

        if (!r.ok || (pp.error && !loggedIn)) {
          errCnt++;
          action = "script-error";
        } else if (loggedIn) {
          activeCnt++;
          action = dryRun ? "would-keep-active" : "kept-active";
          if (!dryRun) {
            const note = `[audit ${ts}] ok status=${pp.http_status ?? ""} url=${(pp.final_url ?? "").slice(0,80)}`;
            await dbE(
              "UPDATE accounts SET status='active', updated_at=NOW(), notes=NULLIF(TRIM(BOTH E'\n' FROM COALESCE(notes,'') || E'\n' || $2),'') WHERE id=$1",
              [acc.id, note]
            ).catch((e: unknown) => { details.push({ id: acc.id, dbError: String(e).slice(0,200) }); });
          }
        } else {
          staleCnt++;
          action = dryRun ? "would-mark-stale" : "marked-stale";
          if (!dryRun) {
            const reason = pp.error
              ? pp.error
              : `not-logged-in status=${pp.http_status ?? ""} url=${(pp.final_url ?? "").slice(0,80)}`;
            const note = `[audit ${ts}] stale: ${String(reason).slice(0,200)}`;
            await dbE(
              "UPDATE accounts SET status='stale', updated_at=NOW(), notes=NULLIF(TRIM(BOTH E'\n' FROM COALESCE(notes,'') || E'\n' || $2),'') WHERE id=$1",
              [acc.id, note]
            ).catch((e: unknown) => { details.push({ id: acc.id, dbError: String(e).slice(0,200) }); });
          }
        }

        details.push({
          id: acc.id, username: acc.username, prev_status: acc.status,
          ok: r.ok, logged_in: loggedIn,
          http_status: pp.http_status, final_url: pp.final_url,
          error: pp.error, action,
        });
      }
    }
  } finally {
    _auditRunning = false;
    _lastAuditFinishedAt = Date.now();
  }

  const duration_ms = Date.now() - t0;
  const total = details.length;
  const scanned = rows.length;

  // v7.78r Bug N: 写 history 但 details 精简, 避免 jsonb 膨胀 (final_url 截断)
  const slimDetails = details.map((d) => ({
    id: d.id,
    username: d.username,
    action: d.action,
    prev_status: d.prev_status,
    logged_in: d.logged_in,
    http_status: d.http_status,
    error: d.error,
  }));
  let history_id: number | undefined;
  try {
    const ins = await dbQ<{ id: number }>(
      `INSERT INTO replit_audit_history(source, scope, dry_run, total, scanned, active, stale, errors, duration_ms, details, started_at, finished_at)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,NOW()) RETURNING id`,
      [source, scope, dryRun, total, scanned, activeCnt, staleCnt, errCnt, duration_ms, JSON.stringify(slimDetails), startedAt]
    );
    history_id = ins[0]?.id;
  } catch (e: unknown) {
    details.push({ history_write_failed: String(e).slice(0, 200) });
  }

  return {
    ok: true, source, scope, dryRun, concurrency, timeoutMs,
    total, scanned, active: activeCnt, stale: staleCnt, errors: errCnt,
    duration_ms, details, history_id,
  };
}

router.post("/admin/replay-audit", async (req, res) => {
  const body = (req.body ?? {}) as ReplayAuditOpts;
  const result = await executeReplayAudit(body, "manual");
  if (result.skipped) { res.status(409).json(result); return; }
  res.json(result);
});

// v7.78r — alias of replay-audit, semantic name + cron-friendly
router.post("/admin/audit-trigger", async (req, res) => {
  const body = (req.body ?? {}) as ReplayAuditOpts;
  const source = (req.body && typeof req.body.source === "string") ? req.body.source : "manual";
  const result = await executeReplayAudit(body, source);
  if (result.skipped) { res.status(409).json(result); return; }
  res.json(result);
});

// v7.78r — GET /admin/audit-history?limit=50 拿最近 N 次 run summary
router.get("/admin/audit-history", async (req, res) => {
  const limit = Math.max(1, Math.min(200, Number(req.query.limit ?? 50)));
  const { query: dbQ } = await import("../db.js");
  try {
    const rows = await dbQ<{
      id: number; source: string; scope: string; dry_run: boolean;
      total: number; scanned: number; active: number; stale: number; errors: number;
      duration_ms: number; started_at: string; finished_at: string;
    }>(
      `SELECT id, source, scope, dry_run, total, scanned, active, stale, errors,
              duration_ms, started_at, finished_at
         FROM replit_audit_history ORDER BY id DESC LIMIT $1`,
      [limit]
    );
    res.json({
      ok: true,
      runs: rows,
      state: getReplayAuditState(),
    });
  } catch (e: unknown) {
    res.status(500).json({ ok: false, error: String(e).slice(0, 200) });
  }
});


// ── v7.81 — Broker watchdog ──────────────────────────────────────────────────
// Tally CF block / 403 cf-mitigated / signup_cf_ip_banned / cf_js_challenge_timeout
// signals coming back from replit_register.py.  When N events accumulate inside
// a sliding window AND we're past the cooldown, spawn `pm2 restart browser-model`
// which re-runs start-browser-model.sh's _pick_browser_proxy → fresh WARP/SOCKS
// exit IP.  Picker will skip the now-bad CF-segment ports automatically.
//
// Why per-process counter (not python-side): the python register process is
// short-lived and per-attempt; only api-server has the cross-attempt view to
// know when "every recent attempt failed for the same CF reason".
const _CF_BLOCK_PATTERNS = [
  "cf_api_blocked",
  "cf_js_challenge_timeout",
  "signup_cf_ip_banned",
  "cf_mitigated",
  "cf-mitigated",
  "cloudflare challenge",
  // v7.97 — 移除 stealth/IP 一致性错误 (per v7.78q/r 模型):
  //   code:1 = stealth 指纹问题, code:2 = IP 一致性问题. 两者换 broker 出口 IP
  //   都无效, 只会无意义抖动. captcha_token_invalid / captcha_low_score /
  //   (code:1) / captcha token is invalid 不再纳入 broker rotation.
  //   保留真正的 CF block 信号 (cf_warmup_failed: cf_clearance 拿不到才算).
  "cf_warmup_failed",
  "signup_username_field_missing",
];
const _CF_WINDOW_MS    = 5 * 60_000;  // 5min sliding window
const _CF_THRESHOLD    = 3;            // N events within window → rotate
const _CF_COOLDOWN_MS  = 90_000;       // don't rotate again within 90s
let   _cfEvents: number[] = [];        // timestamps of recent CF block events
let   _lastBrokerRotateAt = 0;
let   _brokerRotatesTotal = 0;
let   _lastBrokerRotateReason = "";

function _isCfBlockErr(err: string): boolean {
  if (!err) return false;
  const e = err.toLowerCase();
  return _CF_BLOCK_PATTERNS.some((p) => e.includes(p));
}

function _spawnBrokerRotate(reason: string, log: (m: string) => void): void {
  _lastBrokerRotateAt = Date.now();
  _brokerRotatesTotal += 1;
  _lastBrokerRotateReason = reason;
  log(`[broker-watchdog] 🔄 rotating broker — reason=${reason} (rotate #${_brokerRotatesTotal})`);
  try {
    const child = spawn("pm2", ["restart", "browser-model", "--update-env"], {
      detached: true, stdio: "ignore",
    });
    child.unref();
  } catch (e: unknown) {
    log(`[broker-watchdog] spawn pm2 failed: ${String(e).slice(0, 200)}`);
  }
}

export function recordBrokerSignal(ok: boolean, err: string, log: (m: string) => void): void {
  if (ok) {
    // success clears the sliding window — broker is healthy, drop noise
    if (_cfEvents.length > 0) {
      log(`[broker-watchdog] ✓ success → clearing ${_cfEvents.length} pending CF events`);
      _cfEvents = [];
    }
    return;
  }
  if (!_isCfBlockErr(err)) return;
  const now = Date.now();
  _cfEvents.push(now);
  // prune out-of-window events
  _cfEvents = _cfEvents.filter((t) => now - t <= _CF_WINDOW_MS);
  log(`[broker-watchdog] CF signal #${_cfEvents.length}/${_CF_THRESHOLD} window — err=${err.slice(0, 80)}`);
  if (_cfEvents.length < _CF_THRESHOLD) return;
  if (now - _lastBrokerRotateAt < _CF_COOLDOWN_MS) {
    log(`[broker-watchdog] threshold hit but cooldown active (${Math.round((now - _lastBrokerRotateAt)/1000)}s/${_CF_COOLDOWN_MS/1000}s) — skip`);
    return;
  }
  _spawnBrokerRotate(`${_cfEvents.length} CF blocks in ${_CF_WINDOW_MS/60000}min: ${err.slice(0,60)}`, log);
  _cfEvents = []; // reset so we don't immediately re-trigger
}

export function getBrokerWatchdogState(): {
  cfEventsInWindow: number; threshold: number; windowMs: number; cooldownMs: number;
  lastRotateAt: number; lastRotateReason: string; rotatesTotal: number;
} {
  // prune before report
  const now = Date.now();
  _cfEvents = _cfEvents.filter((t) => now - t <= _CF_WINDOW_MS);
  return {
    cfEventsInWindow: _cfEvents.length,
    threshold: _CF_THRESHOLD,
    windowMs: _CF_WINDOW_MS,
    cooldownMs: _CF_COOLDOWN_MS,
    lastRotateAt: _lastBrokerRotateAt,
    lastRotateReason: _lastBrokerRotateReason,
    rotatesTotal: _brokerRotatesTotal,
  };
}

router.post("/admin/broker-rotate", (req, res) => {
  const reason = (req.body && typeof req.body.reason === "string")
    ? `manual: ${req.body.reason}` : "manual";
  const force = req.body && req.body.force === true;
  const now = Date.now();
  if (!force && now - _lastBrokerRotateAt < _CF_COOLDOWN_MS) {
    res.status(409).json({
      ok: false, skipped: "cooldown_active",
      cooldown_remaining_s: Math.round((_CF_COOLDOWN_MS - (now - _lastBrokerRotateAt)) / 1000),
      state: getBrokerWatchdogState(),
    });
    return;
  }
  _spawnBrokerRotate(reason, (m: string) => console.log(`[broker-watchdog] ${m}`));
  res.json({ ok: true, reason, state: getBrokerWatchdogState() });
});

router.get("/admin/broker-rotate/state", (_req, res) => {
  res.json({ ok: true, state: getBrokerWatchdogState() });
});


// ── v7.80 — Batch email pre-scan ─────────────────────────────────────────────
// Run replit_register.py with precheck_only=true on every eligible outlook in DB,
// tag each one as replit_avail / replit_used / replit_unknown so the registration
// picker hits known-good candidates on the first try.  Reuses replit_audit_history
// table (source='prescan').  Mutex-locked to one run at a time.
let _prescanRunning = false;
let _lastPrescanStartedAt = 0;
let _lastPrescanFinishedAt = 0;

export function isEmailPrescanRunning(): boolean { return _prescanRunning; }
export function getEmailPrescanState(): { running: boolean; lastStartedAt: number; lastFinishedAt: number } {
  return { running: _prescanRunning, lastStartedAt: _lastPrescanStartedAt, lastFinishedAt: _lastPrescanFinishedAt };
}

export type EmailPrescanOpts = {
  ids?: number[];
  rescan?: boolean;        // if true, ignore existing replit_avail/used/unknown tags
  limit?: number;          // cap how many emails this run touches (default 50)
  perEmailTimeoutMs?: number; // per-email python timeout (default 90s)
  precheckMaxS?: number;   // python-side probe budget seconds (default 8)
};
export type EmailPrescanResult = {
  ok: boolean;
  source: string;
  total: number;
  scanned: number;
  available: number;
  taken: number;
  unknown: number;
  errors: number;
  duration_ms: number;
  details: Array<Record<string, unknown>>;
  history_id?: number;
  skipped?: string;
};

export async function executeEmailPrescan(opts: EmailPrescanOpts, source = "manual"): Promise<EmailPrescanResult> {
  const limit = Math.max(1, Math.min(500, opts.limit ?? 50));
  const perEmailTimeoutMs = Math.max(30_000, Math.min(180_000, opts.perEmailTimeoutMs ?? 90_000));
  const precheckMaxS = Math.max(3, Math.min(20, opts.precheckMaxS ?? 8));
  const rescan = opts.rescan === true;

  if (_prescanRunning) {
    return {
      ok: false, source, total: 0, scanned: 0, available: 0, taken: 0,
      unknown: 0, errors: 0, duration_ms: 0, details: [], skipped: "prescan_already_running",
    };
  }
  _prescanRunning = true;
  _lastPrescanStartedAt = Date.now();

  const { query: dbQ, execute: dbE } = await import("../db.js");
  const t0 = Date.now();
  const startedAt = new Date(t0).toISOString();
  const details: Array<Record<string, unknown>> = [];
  let availCnt = 0, takenCnt = 0, unkCnt = 0, errCnt = 0;
  let rows: Array<{ id: number; email: string; tags: string | null }> = [];

  try {
    // Eligible outlook pool — same filters as registration picker.
    // When rescan=false, also skip emails already tagged with a prescan verdict.
    const tagFilter = rescan ? "" : `
        AND COALESCE(tags,'') NOT LIKE '%replit_avail%'
        AND COALESCE(tags,'') NOT LIKE '%replit_unknown%'`;
    if (Array.isArray(opts.ids) && opts.ids.length) {
      rows = await dbQ<{ id: number; email: string; tags: string | null }>(
        `SELECT id, email, tags FROM accounts
         WHERE platform='outlook' AND id = ANY($1::int[]) ORDER BY id LIMIT $2`,
        [opts.ids, limit]
      );
    } else {
      rows = await dbQ<{ id: number; email: string; tags: string | null }>(
        `SELECT id, email, tags FROM accounts
         WHERE platform='outlook'
           AND status='active'
           AND (token IS NOT NULL OR refresh_token IS NOT NULL)
           AND COALESCE(tags,'') NOT LIKE '%replit_used%'
           AND COALESCE(tags,'') NOT LIKE '%token_invalid%'
           AND COALESCE(tags,'') NOT LIKE '%inbox_error%'
           AND COALESCE(tags,'') NOT LIKE '%abuse_mode%'
           AND NOT EXISTS (
             SELECT 1 FROM accounts r
             WHERE r.platform='replit' AND r.email = accounts.email
           )
           AND NOT EXISTS (
             SELECT 1 FROM archives ar
             WHERE ar.platform='replit' AND ar.email = accounts.email
           )
           ${tagFilter}
         ORDER BY
           CASE WHEN COALESCE(tags,'') LIKE '%inbox_verified%' THEN 0 ELSE 1 END,
           id
         LIMIT $1`,
        [limit]
      );
    }

    if (rows.length === 0) {
      _prescanRunning = false;
      _lastPrescanFinishedAt = Date.now();
      return {
        ok: true, source, total: 0, scanned: 0, available: 0, taken: 0,
        unknown: 0, errors: 0, duration_ms: Date.now() - t0, details: [],
        skipped: "no_eligible_outlooks",
      };
    }

    const regScript = path.join(API_DIR, "replit_register.py");

    // Sequential — precheck holds a CDP browser instance and we don't want to
    // multiplex it.  ~10s per email × 50 = ~8min worst case, well within sane.
    for (const acc of rows) {
      const portList = sortedByReputation(availablePorts());
      const tryPort = portList[0];
      if (tryPort === undefined) {
        details.push({ id: acc.id, email: acc.email, decision: "error", error: "no_proxy_ports" });
        errCnt++;
        continue;
      }

      const { ok: pyOk, parsed } = await runPython(
        regScript,
        {
          email: acc.email,
          username: "_precheck_user_dummy",  // never submitted in precheck mode
          password: "_precheck_pwd_dummy",
          proxy: portToProxy(tryPort),
          headless: true,
          use_cdp: true,
          precheck_only: true,
          precheck_max_s: precheckMaxS,
        },
        perEmailTimeoutMs
      );

      const decisionRaw = String(
        (parsed && (parsed as { decision?: unknown }).decision) ?? ""
      ).toLowerCase();
      const decision: "available" | "taken" | "unknown" | "error" =
        decisionRaw === "available" ? "available" :
        decisionRaw === "taken" ? "taken" :
        decisionRaw === "unknown" ? "unknown" :
        (pyOk && parsed && (parsed as { ok?: unknown }).ok === true) ? "unknown" :
        "error";

      // Map decision → tag delta. tag added (idempotent), competing tags removed.
      const addTag =
        decision === "available" ? "replit_avail" :
        decision === "taken" ? "replit_used" :
        decision === "unknown" ? "replit_unknown" :
        null; // error → don't tag, retry later
      const removeTags =
        decision === "available" ? ["replit_used", "replit_unknown"] :
        decision === "taken" ? ["replit_avail", "replit_unknown"] :
        decision === "unknown" ? ["replit_avail", "replit_used"] :
        [];

      if (addTag) {
        try {
          // remove competing prescan tags first, then append the verdict tag (dedup via set logic)
          await dbE(
            `UPDATE accounts
                SET tags = (
                  SELECT NULLIF(
                    array_to_string(
                      ARRAY(
                        SELECT DISTINCT t FROM unnest(string_to_array(COALESCE(tags,''), ',')) AS t
                        WHERE t <> '' AND t <> ALL($2::text[])
                      ) || ARRAY[$3::text],
                    ','),
                  '')
                ),
                updated_at = NOW()
              WHERE id = $1`,
            [acc.id, removeTags, addTag]
          );
        } catch (e: unknown) {
          details.push({ id: acc.id, email: acc.email, dbError: String(e).slice(0, 200) });
        }
      }

      if (decision === "available") availCnt++;
      else if (decision === "taken") takenCnt++;
      else if (decision === "unknown") unkCnt++;
      else errCnt++;

      details.push({
        id: acc.id,
        email: acc.email,
        decision,
        port: tryPort,
        phase: parsed && (parsed as { phase?: unknown }).phase,
        raw_err: parsed && (parsed as { precheck_raw_err?: unknown }).precheck_raw_err,
        py_error: parsed && (parsed as { error?: unknown }).error,
      });
    }
  } finally {
    _prescanRunning = false;
    _lastPrescanFinishedAt = Date.now();
  }

  const duration_ms = Date.now() - t0;
  const total = details.length;
  const scanned = rows.length;

  // Slim details for jsonb storage (drop large fields if any)
  const slimDetails = details.map((d) => ({
    id: d.id, email: d.email, decision: d.decision,
    port: d.port, raw_err: d.raw_err, py_error: d.py_error,
  }));
  let history_id: number | undefined;
  try {
    const ins = await dbQ<{ id: number }>(
      `INSERT INTO replit_audit_history(source, scope, dry_run, total, scanned, active, stale, errors, duration_ms, details, started_at, finished_at)
       VALUES ($1,'email_prescan',false,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,NOW()) RETURNING id`,
      ["prescan:" + source, total, scanned, availCnt, takenCnt, errCnt + unkCnt,
       duration_ms, JSON.stringify(slimDetails), startedAt]
    );
    history_id = ins[0]?.id;
  } catch (e: unknown) {
    details.push({ history_write_failed: String(e).slice(0, 200) });
  }

  return {
    ok: true, source,
    total, scanned,
    available: availCnt, taken: takenCnt, unknown: unkCnt, errors: errCnt,
    duration_ms, details, history_id,
  };
}

router.post("/admin/email-prescan", async (req, res) => {
  const body = (req.body ?? {}) as EmailPrescanOpts & { source?: string };
  const source = (body && typeof body.source === "string") ? body.source : "manual";
  const result = await executeEmailPrescan(body, source);
  if (result.skipped === "prescan_already_running") { res.status(409).json(result); return; }
  res.json(result);
});

router.get("/admin/email-prescan/state", (_req, res) => {
  res.json({ ok: true, state: getEmailPrescanState() });
});


export default router;
