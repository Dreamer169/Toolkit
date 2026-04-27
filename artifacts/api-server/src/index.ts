import app from "./app";
import { initDatabase } from "./db.js";
import { logger } from "./lib/logger";
import { selfRegister } from "./routes/tunnel";
import { startLiveVerifyPoller } from "./lib/live-verify-poller.js";
import { startAccountHealthcheck } from "./lib/account-healthcheck.js";
import { startReplitReplayAudit } from "./lib/replit-replay-audit.js";
import { startCfPoolMaintainer } from "./lib/cf-pool-maintainer.js";
import { startProxyMaintenance } from "./routes/data.js";
import { attachCdpRelayWebSocket } from "./lib/cdp_relay_ws.js";
import { PersistenceManager } from "./lib/persistence-manager.js";

const rawPort = process.env["PORT"];
if (!rawPort) throw new Error("PORT environment variable is required but was not provided.");

const port = Number(rawPort);
if (Number.isNaN(port) || port <= 0) throw new Error(`Invalid PORT value: "${rawPort}"`);

// v7.78r Bug O: 启动前确保所有 CREATE TABLE IF NOT EXISTS 跑过
await initDatabase().catch((e) => { logger.error({ err: String(e) }, "initDatabase failed"); process.exit(1); });

const server = app.listen(port, (err) => {
  if (err) { logger.error({ err }, "Error listening on port"); process.exit(1); }
  logger.info({ port }, "Server listening");
  logger.info({ url: `http://localhost:${port}` }, "Stream relay URL");
  setTimeout(selfRegister, 3_000).unref();
  // 实时验证链接点击（每10秒扫描）
  startLiveVerifyPoller(3_000);
  // 账号健康检查：自动补 OAuth + 打标签（每5分钟）
  startAccountHealthcheck(5 * 60 * 1000);
  // v7.78r — Replit 账号 replay-audit 周期校验 (默认 6h, env REPLAY_AUDIT_INTERVAL_HOURS)
  startReplitReplayAudit();
  startCfPoolMaintainer();
  startProxyMaintenance();
  attachCdpRelayWebSocket(server);
  // v1: 启动时回收上次 api-server 退出后留下的 'running' 僵尸 job
  PersistenceManager.reapOrphans()
    .then((n) => { if (n > 0) logger.warn({ reaped: n }, "reaped orphan running jobs"); })
    .catch((err) => logger.error({ err }, "reapOrphans failed"));
});

server.on("error", (err) => { logger.error({ err }, "Server error"); });
