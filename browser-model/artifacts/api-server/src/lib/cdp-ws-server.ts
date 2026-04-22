/**
 * 把 WebSocket 升级 hook 装到 Node http.Server 上。
 * 路径 /api/cdp/ws 走 cdp-broker；其他路径放行。
 */
import type { Server as HttpServer } from "node:http";
import { WebSocketServer, type WebSocket } from "ws";
import { CdpSession } from "./cdp-broker.js";
import { logger } from "./logger.js";

const WS_PATH = "/api/cdp/ws";

export function attachCdpWebSocket(server: HttpServer): void {
  const wss = new WebSocketServer({ noServer: true });

  server.on("upgrade", (req, socket, head) => {
    if (!req.url) return;
    const url = new URL(req.url, "http://x");
    if (url.pathname !== WS_PATH) return;
    wss.handleUpgrade(req, socket, head, (ws) => wss.emit("connection", ws, req));
  });

  wss.on("connection", async (ws: WebSocket, req) => {
    const url = new URL(req.url ?? "/", "http://x");
    const w = Math.max(320, Math.min(2560, Number(url.searchParams.get("w") || 1280)));
    const h = Math.max(240, Math.min(1600, Number(url.searchParams.get("h") || 800)));
    const initialUrl = url.searchParams.get("url");

    const session = new CdpSession(ws);
    ws.on("message", (data) => session.handleMessage(data as Buffer));
    ws.on("close", () => session.close().catch(() => {}));
    ws.on("error", (e) => logger.warn({ err: String(e) }, "[cdp-ws] socket error"));

    try {
      await session.start({ width: w, height: h });
      if (initialUrl) {
        await session.handleMessage(JSON.stringify({ type: "navigate", url: initialUrl }));
      }
    } catch (e) {
      logger.error({ err: String(e) }, "[cdp-ws] session start failed");
      try { ws.send(JSON.stringify({ type: "error", message: String(e) })); } catch {}
      try { ws.close(); } catch {}
    }
  });

  logger.info({ path: WS_PATH }, "[cdp-ws] WebSocket attached");
}
