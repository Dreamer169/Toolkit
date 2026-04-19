import * as http from "http";
import { WebSocketServer } from "ws";
import app from "./app";
import { logger } from "./lib/logger";
import { selfRegister, handleTunnelWs } from "./routes/tunnel";

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

const server = http.createServer(app);

const wss = new WebSocketServer({ noServer: true });

server.on("upgrade", (req, socket, head) => {
  const rawUrl = req.url ?? "/";
  let pathname = rawUrl;
  let searchStr = "";
  const qi = rawUrl.indexOf("?");
  if (qi !== -1) { pathname = rawUrl.slice(0, qi); searchStr = rawUrl.slice(qi + 1); }

  if (pathname === "/api/tunnel/ws") {
    wss.handleUpgrade(req, socket, head, (ws) => {
      const params = new URLSearchParams(searchStr);
      handleTunnelWs(ws, params);
    });
  } else {
    socket.destroy();
  }
});

server.listen(port, (err?: Error) => {
  if (err) {
    logger.error({ err }, "Error listening on port");
    process.exit(1);
  }

  logger.info({ port }, "Server listening");

  const myUrl = process.env.MY_URL ||
    (process.env.REPLIT_DEV_DOMAIN
      ? `https://${process.env.REPLIT_DEV_DOMAIN}`
      : `http://localhost:${port}`);
  logger.info({ url: myUrl }, "Tunnel sub-node URL (WebSocket at /api/tunnel/ws)");

  setTimeout(() => {
    selfRegister();
    setInterval(selfRegister, 5 * 60 * 1000);
  }, 3000);
});
