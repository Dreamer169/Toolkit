import { logger } from "./logger.js";
import { spawn, spawnSync } from "child_process";
import { existsSync } from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const CF_POOL_SCRIPT =
  ["/root/Toolkit/artifacts/api-server/cf_pool_api.py",
   "/workspaces/Toolkit/artifacts/api-server/cf_pool_api.py",
   "/home/runner/workspace/artifacts/api-server/cf_pool_api.py",
   path.resolve(process.cwd(), "artifacts/api-server/cf_pool_api.py"),
   path.resolve(__dirname, "../../cf_pool_api.py"),
   path.resolve(__dirname, "../../../artifacts/api-server/cf_pool_api.py"),
  ].find((p) => existsSync(p))
  ?? "/root/Toolkit/artifacts/api-server/cf_pool_api.py";

const MIN_POOL_SIZE = 80;
const TARGET_POOL_SIZE = 100;
const GENERATE_COUNT = 300;
const CHECK_INTERVAL_MS = 60 * 1000;

let _intervalId: ReturnType<typeof setInterval> | null = null;
let _refreshRunning = false;

function runPython(args: string[], timeoutMs = 60_000): { ok: boolean; data: Record<string, unknown> } {
  const r = spawnSync("python3", [CF_POOL_SCRIPT, ...args], {
    timeout: timeoutMs,
    encoding: "utf8",
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
  });
  if (r.error || r.status !== 0) {
    logger.warn({ args, stderr: r.stderr?.slice(0, 200), err: r.error?.message }, "[cf-pool] python3 调用失败");
    return { ok: false, data: {} };
  }
  try {
    return { ok: true, data: JSON.parse(r.stdout ?? "{}") as Record<string, unknown> };
  } catch {
    return { ok: true, data: {} };
  }
}

function getPoolStatus(): { available: number; pool: Array<{ ip: string; latency: number }> } {
  const { data } = runPython(["status"], 10_000);
  return {
    available: (data["available"] as number) ?? 0,
    pool: (data["pool"] as Array<{ ip: string; latency: number }>) ?? [],
  };
}

function refreshPool(): Promise<{ newIps: number; total: number }> {
  if (_refreshRunning) return Promise.resolve({ newIps: 0, total: 0 });
  _refreshRunning = true;
  logger.info({ target: TARGET_POOL_SIZE, generate: GENERATE_COUNT }, "[cf-pool] 开始后台补充 IP 池");
  return new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    const child = spawn("python3", [
      CF_POOL_SCRIPT,
      "refresh",
      "--count", String(GENERATE_COUNT),
      "--target", String(TARGET_POOL_SIZE),
      "--threads", "12",
      "--port", "443",
      "--max-latency", "800",
    ], { env: { ...process.env, PYTHONUNBUFFERED: "1" } });
    const timer = setTimeout(() => {
      child.kill("SIGTERM");
    }, 120_000);
    child.stdout.on("data", (d: Buffer) => { stdout += d.toString(); });
    child.stderr.on("data", (d: Buffer) => { stderr += d.toString(); });
    child.on("close", () => {
      clearTimeout(timer);
      _refreshRunning = false;
      try {
        const data = JSON.parse(stdout || "{}");
        const newIps = (data["new_ips"] as number) ?? 0;
        const total = (data["total_available"] as number) ?? 0;
        logger.info({ newIps, total }, "[cf-pool] 后台补充完成");
        resolve({ newIps, total });
      } catch {
        logger.warn({ stderr: stderr.slice(0, 300) }, "[cf-pool] 后台补充解析失败");
        resolve({ newIps: 0, total: 0 });
      }
    });
    child.on("error", (err: Error) => {
      clearTimeout(timer);
      _refreshRunning = false;
      logger.warn({ err: err.message }, "[cf-pool] 后台补充启动失败");
      resolve({ newIps: 0, total: 0 });
    });
  });
}

async function runCheck() {
  const { available } = getPoolStatus();
  logger.info({ available, min: MIN_POOL_SIZE }, "[cf-pool] 维护检查");

  if (available < MIN_POOL_SIZE) {
    logger.info({ available, min: MIN_POOL_SIZE }, "[cf-pool] IP 池不足，触发后台补充");
    await refreshPool();
  } else {
    logger.info({ available }, "[cf-pool] IP 池健康，无需补充");
  }
}

export function startCfPoolMaintainer() {
  logger.info({ script: CF_POOL_SCRIPT, minSize: MIN_POOL_SIZE, intervalMin: CHECK_INTERVAL_MS / 60_000 },
    "[cf-pool] IP 池动态维护器启动");

  setTimeout(() => { runCheck().catch((e) => logger.error({ err: String(e) }, "[cf-pool] 初始检查出错")); }, 5_000);

  if (_intervalId) clearInterval(_intervalId);
  _intervalId = setInterval(() => {
    runCheck().catch((e) => logger.error({ err: String(e) }, "[cf-pool] 定期检查出错"));
  }, CHECK_INTERVAL_MS);
}
