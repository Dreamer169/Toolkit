import { createServer } from "http";
import { WebSocketServer } from "ws";
import type { IncomingMessage } from "http";
import app from "./app";
import { initNotifier } from "./lib/notifier.js";
import { startLiveVerifyPoller } from "./lib/live-verify-poller.js";
import { startCfPoolMaintainer } from "./lib/cf-pool-maintainer.js";
import { handleClientConnection, handlePlaywrightConnection } from "./lib/cdp_relay_ws.js";
import { logger } from "./lib/logger";

const rawPort = process.env["PORT"];
if (!rawPort) throw new Error("PORT environment variable is required but was not provided.");
const port = Number(rawPort);
if (Number.isNaN(port) || port <= 0) throw new Error(`Invalid PORT value: "${rawPort}"`);

initNotifier();
startLiveVerifyPoller(10_000);
startCfPoolMaintainer();

// 创建 HTTP server（用于挂载 WebSocket）
const server = createServer(app);

// WebSocket 服务器（不自动绑定路由，通过 upgrade 事件手动路由）
const wss = new WebSocketServer({ noServer: true });

server.on("upgrade", (req: IncomingMessage, socket, head) => {
  const url = req.url ?? "";

  if (url === "/api/cdp-relay/client") {
    // 本地桥接客户端连接
    wss.handleUpgrade(req, socket, head, (ws) => {
      handleClientConnection(ws, req);
    });
  } else {
    // Playwright CDP WebSocket: /api/cdp-relay/:sessionId/playwright
    const m = url.match(/^\/api\/cdp-relay\/([^/]+)\/playwright$/);
    if (m && m[1]) {
      wss.handleUpgrade(req, socket, head, (ws) => {
        handlePlaywrightConnection(ws, m[1]!);
      });
    } else {
      socket.destroy();
    }
  }
});

server.listen(port, (err?: Error) => {
  if (err) {
    logger.error({ err }, "Error listening on port");
    process.exit(1);
  }
  logger.info({ port }, "Server listening (with CDP relay WS)");
});
