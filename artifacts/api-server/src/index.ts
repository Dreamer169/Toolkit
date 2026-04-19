import app from "./app";
import { logger } from "./lib/logger";
import { selfRegister } from "./routes/tunnel";
import { startLiveVerifyPoller } from "./lib/live-verify-poller.js";
import { startAccountHealthcheck } from "./lib/account-healthcheck.js";
import { startCfPoolMaintainer } from "./lib/cf-pool-maintainer.js";

const rawPort = process.env["PORT"];
if (!rawPort) throw new Error("PORT environment variable is required but was not provided.");

const port = Number(rawPort);
if (Number.isNaN(port) || port <= 0) throw new Error(`Invalid PORT value: "${rawPort}"`);

const server = app.listen(port, (err) => {
  if (err) { logger.error({ err }, "Error listening on port"); process.exit(1); }
  logger.info({ port }, "Server listening");
  logger.info({ url: `http://localhost:${port}` }, "Stream relay URL");
  setTimeout(selfRegister, 3_000).unref();
  // 实时验证链接点击（每10秒扫描）
  startLiveVerifyPoller(10_000);
  // 账号健康检查：自动补 OAuth + 打标签（每5分钟）
  startAccountHealthcheck(5 * 60 * 1000);
  startCfPoolMaintainer();
});

server.on("error", (err) => { logger.error({ err }, "Server error"); });
