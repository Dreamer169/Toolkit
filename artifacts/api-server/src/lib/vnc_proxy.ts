/**
 * VNC WebSocket Proxy
 * Forwards /api/tools/oxylabs/vnc-ws → ws://127.0.0.1:6080 (websockify)
 * Enables noVNC browser client to connect to x11vnc on the VPS
 */
import type { IncomingMessage, Server as HttpServer } from "http";
import type { Socket } from "net";
import { createConnection } from "net";

const VNC_WS_PATH = "/api/tools/oxylabs/vnc-ws";
const WEBSOCKIFY_PORT = 6080;
const WEBSOCKIFY_HOST = "127.0.0.1";

/** Raw TCP proxy: forwards the entire HTTP upgrade + WebSocket frames transparently */
function handleVncUpgrade(req: IncomingMessage, socket: Socket, head: Buffer): void {
  const target = createConnection({ port: WEBSOCKIFY_PORT, host: WEBSOCKIFY_HOST });

  target.on("connect", () => {
    // Re-send the HTTP upgrade request to websockify
    // Build request line + headers
    const url = req.url ?? "/";
    const headers = Object.entries(req.headers)
      .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : v}`)
      .join("\r\n");
    const upgradeRequest = `GET ${url} HTTP/1.1\r\n${headers}\r\n\r\n`;
    target.write(upgradeRequest);

    // Forward any leading bytes
    if (head && head.length > 0) target.write(head);

    // Bidirectional pipe
    socket.pipe(target);
    target.pipe(socket);
  });

  target.on("error", (err) => {
    console.error("[vnc-proxy] target error:", err.message);
    try { socket.destroy(); } catch {}
  });

  socket.on("error", () => {
    try { target.destroy(); } catch {}
  });

  socket.on("close", () => {
    try { target.destroy(); } catch {}
  });
}

let _attached = false;

export function attachVncWebSocket(server: HttpServer): void {
  if (_attached) return;
  _attached = true;

  server.on("upgrade", (req: IncomingMessage, socket: Socket, head: Buffer) => {
    if (!req.url) return;
    try {
      const url = new URL(req.url, "http://x");
      if (url.pathname === VNC_WS_PATH) {
        handleVncUpgrade(req, socket, head);
      }
    } catch {}
  });

  console.log(`[vnc-proxy] WS proxy attached: ${VNC_WS_PATH} → websockify:${WEBSOCKIFY_PORT}`);
}
