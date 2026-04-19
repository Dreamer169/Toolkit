import { Router } from "express";
import * as net from "net";
import * as https from "https";
import * as http from "http";
import { WebSocket } from "ws";

const router = Router();

const TUNNEL_TOKEN = process.env.TUNNEL_TOKEN || "1NnCcQJcNgwlTDPEnDIkWEKzWIdmZ/4+BmsOp1/jLP6ojCWsv8+xTwcLj34Mu2viWy0q5SEoDP0q2qE5xHaRRg==";
const VPS_GATEWAY  = process.env.VPS_GATEWAY || "http://45.205.27.69:8080";
const MY_URL       = process.env.MY_URL || `https://${process.env.REPLIT_DEV_DOMAIN || "localhost"}`;
const NODE_NAME    = process.env.REPLIT_USER || "replit-subnode";

interface Session {
  sk: net.Socket;
  rb: Buffer[];
  rw: Array<(d: Buffer | null) => void>;
  cl: boolean;
}

const sessions = new Map<string, Session>();
let wsSessionCount = 0;

function chkTok(tok: string | undefined): boolean {
  return tok === TUNNEL_TOKEN;
}

function makeId(): string {
  return Math.random().toString(36).slice(2, 10) + Math.random().toString(36).slice(2, 10);
}

function cleanSession(id: string): void {
  const ss = sessions.get(id);
  if (ss) {
    try { ss.sk.destroy(); } catch (_) {}
    sessions.delete(id);
  }
}

setInterval(() => {
  for (const [id, ss] of sessions.entries()) {
    if (ss.cl) cleanSession(id);
  }
}, 30_000);

export function handleTunnelWs(ws: WebSocket, params: URLSearchParams): void {
  const tok = params.get("token") ?? params.get("tok") ?? undefined;
  if (!chkTok(tok)) { ws.close(4003, "forbidden"); return; }

  const host = params.get("host") ?? "";
  const port = parseInt(params.get("port") ?? "0", 10);
  if (!host || !port) { ws.close(4000, "host/port required"); return; }

  wsSessionCount++;
  const label = `ws#${wsSessionCount}(${host}:${port})`;
  console.log(`[tunnel] ${label} open`);

  const sk = net.createConnection({ host, port });

  sk.on("connect", () => {
    console.log(`[tunnel] ${label} tcp connected`);
    ws.send(JSON.stringify({ ok: true }));
  });

  sk.on("data", (d: Buffer) => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(d, { binary: true });
    }
  });

  sk.on("close", () => {
    console.log(`[tunnel] ${label} tcp closed`);
    if (ws.readyState === WebSocket.OPEN) ws.close(1000, "tcp_closed");
  });

  sk.on("error", (e: Error) => {
    console.warn(`[tunnel] ${label} tcp error: ${e.message}`);
    if (ws.readyState === WebSocket.OPEN) ws.close(1011, e.message);
  });

  ws.on("message", (data: Buffer | ArrayBuffer | Buffer[], isBinary: boolean) => {
    if (!isBinary) return;
    const buf = Buffer.isBuffer(data) ? data : Buffer.from(data as ArrayBuffer);
    sk.write(buf);
  });

  ws.on("close", () => {
    console.log(`[tunnel] ${label} ws closed`);
    try { sk.destroy(); } catch (_) {}
  });

  ws.on("error", (e: Error) => {
    console.warn(`[tunnel] ${label} ws error: ${e.message}`);
    try { sk.destroy(); } catch (_) {}
  });

  setTimeout(() => {
    if (ws.readyState === WebSocket.CONNECTING) {
      ws.close(1001, "connect_timeout");
      sk.destroy();
    }
  }, 15_000);
}

router.post("/tunnel/open", (req, res) => {
  const tok  = (req.query.token ?? req.query.tok) as string | undefined;
  if (!chkTok(tok)) { res.status(403).json({ error: "forbidden" }); return; }

  const host = String(req.query.host ?? "");
  const port = parseInt(String(req.query.port ?? "80"), 10);
  if (!host || !port) { res.status(400).json({ error: "host/port required" }); return; }

  const id = makeId();
  const sk  = net.createConnection({ host, port }, () => {
    const session: Session = { sk, rb: [], rw: [], cl: false };
    sessions.set(id, session);
    sk.on("data", (d: Buffer) => {
      const ss = sessions.get(id);
      if (!ss) return;
      if (ss.rw.length) {
        ss.rw.shift()!(d);
      } else {
        ss.rb.push(d);
      }
    });
    sk.on("close", () => {
      const ss = sessions.get(id);
      if (ss) { ss.cl = true; ss.rw.forEach(r => r(null)); }
    });
    sk.on("error", () => {
      const ss = sessions.get(id);
      if (ss) { ss.cl = true; ss.rw.forEach(r => r(null)); }
    });
    res.json({ ok: true, id });
  });
  sk.on("error", (e: Error) => {
    if (!sessions.has(id)) { res.status(502).json({ error: e.message }); }
  });
  setTimeout(() => { if (!sessions.has(id)) sk.destroy(); }, 10_000);
});

router.get("/tunnel/read/:id", async (req, res) => {
  const tok = (req.query.token ?? req.query.tok) as string | undefined;
  if (!chkTok(tok)) { res.status(403).json({ error: "forbidden" }); return; }

  const id = req.params.id;
  const ss = sessions.get(id);
  if (!ss) { res.status(404).json({ error: "no session" }); return; }

  res.set({
    "Transfer-Encoding": "chunked",
    "Content-Type": "application/octet-stream",
    "Cache-Control": "no-store, no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
  });
  try { (res as unknown as { socket?: { setNoDelay?: (v: boolean) => void } }).socket?.setNoDelay?.(true); } catch (_) {}
  res.flushHeaders();

  const write = (d: Buffer) => {
    const hex = d.length.toString(16);
    res.write(`${hex}\r\n`);
    res.write(d);
    res.write("\r\n");
    try { (res as unknown as { flush?: () => void }).flush?.(); } catch (_) {}
  };

  while (true) {
    if (ss.rb.length) {
      const d = Buffer.concat(ss.rb.splice(0));
      write(d);
    } else if (ss.cl) {
      res.write("0\r\n\r\n");
      res.end();
      return;
    } else {
      const d = await new Promise<Buffer | null>(r => ss.rw.push(r));
      if (!d) { res.write("0\r\n\r\n"); res.end(); return; }
      write(d);
    }
  }
});

router.post("/tunnel/write/:id", (req, res) => {
  const tok = (req.query.token ?? req.query.tok) as string | undefined;
  if (!chkTok(tok)) { res.status(403).json({ error: "forbidden" }); return; }

  const id = req.params.id;
  const ss = sessions.get(id);
  if (!ss) { res.status(404).json({ error: "no session" }); return; }

  const chunks: Buffer[] = [];
  req.on("data", (d: Buffer) => chunks.push(d));
  req.on("end", () => {
    const buf = Buffer.concat(chunks);
    ss.sk.write(buf);
    res.json({ ok: true });
  });
});

router.delete("/tunnel/:id", (req, res) => {
  const tok = (req.query.token ?? req.query.tok) as string | undefined;
  if (!chkTok(tok)) { res.status(403).json({ error: "forbidden" }); return; }
  cleanSession(req.params.id);
  res.json({ ok: true });
});

router.get("/tunnel/health", (_req, res) => {
  res.json({ ok: true, sessions: sessions.size, wsSessions: wsSessionCount, name: NODE_NAME, time: new Date().toISOString() });
});

function selfRegister(): void {
  const body = JSON.stringify({
    gatewayUrl: MY_URL,
    name: NODE_NAME,
    token: TUNNEL_TOKEN,
    source: "runtime",
    model: "gpt-5-mini",
    priority: 5,
  });

  const isHttps = VPS_GATEWAY.startsWith("https");
  const urlObj  = new URL(VPS_GATEWAY + "/api/gateway/self-register");
  const opts = {
    hostname: urlObj.hostname,
    port:     Number(urlObj.port || (isHttps ? 443 : 80)),
    path:     urlObj.pathname,
    method:   "POST",
    headers:  { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) },
  };

  const lib = isHttps ? https : http;
  const req = lib.request(opts, (r) => {
    let d = "";
    r.on("data", c => d += c);
    r.on("end", () => console.log(`[tunnel] self-register → ${r.statusCode}: ${d.slice(0, 120)}`));
  });
  req.on("error", e => console.warn("[tunnel] self-register failed:", e.message));
  req.write(body);
  req.end();
}

export { selfRegister };
export default router;
