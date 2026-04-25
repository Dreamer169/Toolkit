/**
 * v7.78r — Replit 账号 replay-audit 后台调度器
 * 沿用 account-healthcheck.ts pattern (setInterval + 延迟首跑)
 *
 * env REPLAY_AUDIT_INTERVAL_HOURS:
 *   undefined / 不设置  -> 默认 6 小时
 *   "0"                  -> 禁用 (不启动)
 *   小数 (e.g. "0.0083") -> 30s, 测试用
 *
 * 触发的 audit 一律 source="cron", scope="active", dryRun=false,
 * 共享 accounts.ts 的 _auditRunning 锁 (executeReplayAudit 自己处理).
 */
import { logger } from "./logger.js";
import { executeReplayAudit, isReplayAuditRunning } from "../routes/accounts.js";

let _intervalId: ReturnType<typeof setInterval> | null = null;

async function tick() {
  if (isReplayAuditRunning()) {
    logger.info({ tag: "replay-audit-cron" }, "前次 audit 仍运行中, 本轮跳过");
    return;
  }
  try {
    const r = await executeReplayAudit({ scope: "active", dryRun: false }, "cron");
    if (r.skipped) {
      logger.info({ skipped: r.skipped }, "[replay-audit-cron] 已被锁跳过");
    } else {
      logger.info({
        history_id: r.history_id, total: r.total, scanned: r.scanned,
        active: r.active, stale: r.stale, errors: r.errors,
        duration_ms: r.duration_ms,
      }, "[replay-audit-cron] tick 完成");
    }
  } catch (e) {
    logger.error({ err: String(e) }, "[replay-audit-cron] tick 抛异常");
  }
}

export function startReplitReplayAudit() {
  const raw = process.env["REPLAY_AUDIT_INTERVAL_HOURS"];
  const hours = raw === undefined ? 6 : Number(raw);
  if (!Number.isFinite(hours) || hours <= 0) {
    logger.info({ raw }, "[replay-audit-cron] 已禁用 (REPLAY_AUDIT_INTERVAL_HOURS<=0 或非法)");
    return;
  }
  const intervalMs = Math.max(30_000, Math.floor(hours * 3600 * 1000));
  if (_intervalId) clearInterval(_intervalId);
  _intervalId = setInterval(() => { void tick(); }, intervalMs);
  // 启动后延迟 60s 首跑, 等 api-server / xray / patchright 都就绪
  setTimeout(() => { void tick(); }, 60_000).unref();
  logger.info({ intervalMs, intervalHours: hours }, "[replay-audit-cron] 已启动");
}

export function stopReplitReplayAudit() {
  if (_intervalId) { clearInterval(_intervalId); _intervalId = null; }
}
