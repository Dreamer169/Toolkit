import app from "./app";
import { initNotifier } from "./lib/notifier.js";
import { startLiveVerifyPoller } from "./lib/live-verify-poller.js";
import { startCfPoolMaintainer } from "./lib/cf-pool-maintainer.js";
import { logger } from "./lib/logger";

const rawPort = process.env["PORT"];

if (!rawPort) {
  throw new Error(
    "PORT environment variable is required but was not provided.",
  );
}

const port = Number(rawPort);

if (Number.isNaN(port) || port <= 0) {
  throw new Error(`Invalid PORT value: "${rawPort}"`);
}

initNotifier();
startLiveVerifyPoller(10_000);
startCfPoolMaintainer();
app.listen(port, (err) => {
  if (err) {
    logger.error({ err }, "Error listening on port");
    process.exit(1);
  }

  logger.info({ port }, "Server listening");
});
