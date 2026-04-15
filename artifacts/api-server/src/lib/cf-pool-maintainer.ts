import { logger } from "./logger.js";
import { spawnSync } from "child_process";
import { existsSync } from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const CF_POOL_SCRIPT =
  ["/workspaces/Toolkit/artifacts/api-server/cf_pool_api.py",
   "/home/runner/workspace/artifacts/api-server/cf_pool_api.py",
   path.resolve(__dirname, "../../../artifacts/api-server/cf_pool_api.py"),
  ].find((p) => existsSync(p))
  ?? "/workspaces/Toolkit/artifacts/api-server/cf_pool_api.py";

const MIN_POOL_SIZE   = 40;   // 低于此值触发补充
const TARGET_POOL_SIZE = 50;  // 每次补充目标数量
const GENERATE_COUNT   = 150;  // 每次生成候选 IP 数量
const CHECK_INTERVAL_MS = 10 * 60 * 1000; // 每10分钟检查一次

let _intervalId: ReturnType<typeof setInterval> | null = null;

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

function refreshPool(): { newIps: number; total: number } {
  logger.info({ target: TARGET_POOL_SIZE, generate: GENERATE_COUNT }, "[cf-pool] 开始补充 IP 池");
  const { ok, data } = runPython([
    "refresh",
    "--count", String(GENERATE_COUNT),
    "--target", String(TARGET_POOL_SIZE),
    "--threads", "10",
    "--port", "443",
    "--max-latency", "800",
  ], 90_000);
  if (!ok) return { newIps: 0, total: 0 };
  const newIps = (data["new_ips"] as number) ?? 0;
  const total  = (data["total_available"] as number) ?? 0;
  logger.info({ newIps, total }, "[cf-pool] 补充完成");
  return { newIps, total };
}

async function runCheck() {
  const { available } = getPoolStatus();
  logger.info({ available, min: MIN_POOL_SIZE }, "[cf-pool] 维护检查");

  if (available < MIN_POOL_SIZE) {
    logger.info({ available, min: MIN_POOL_SIZE }, "[cf-pool] IP 池不足，触发补充");
    refreshPool();
  } else {
    logger.info({ available }, "[cf-pool] IP 池健康，无需补充");
  }
}

export function startCfPoolMaintainer() {
  logger.info({ script: CF_POOL_SCRIPT, minSize: MIN_POOL_SIZE, intervalMin: CHECK_INTERVAL_MS / 60_000 },
    "[cf-pool] IP 池动态维护器启动");

  // 启动时立即检查一次（稍延迟，避免阻塞启动）
  setTimeout(() => { runCheck().catch((e) => logger.error({ err: String(e) }, "[cf-pool] 初始检查出错")); }, 5_000);

  if (_intervalId) clearInterval(_intervalId);
  _intervalId = setInterval(() => {
    runCheck().catch((e) => logger.error({ err: String(e) }, "[cf-pool] 定期检查出错"));
  }, CHECK_INTERVAL_MS);
}
