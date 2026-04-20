import { Router, type IRouter } from "express";
import * as net from "node:net";
import * as http from "node:http";
import * as https from "node:https";
import { logger } from "../lib/logger";

const router: IRouter = Router();

const TUNNEL_TOKEN =
  process.env["TUNNEL_TOKEN"] ??
  "1NnCcQJcNgwlTDPEnDIkWEKzWIdmZ/4+BmsOp1/jLP6ojCWsv8+xTwcLj34Mu2viWy0q5SEoDP0q2qE5xHaRRg==";
const VPS_GATEWAY = process.env["VPS_GATEWAY"] ?? "http://45.205.27.69:8080";
const NODE_NAME = process.env["REPLIT_USER"] ?? "replit-subnode";

interface Session {
  socket: net.Socket;
  readBuffer: Buffer[];
  waiters: Array<(data: Buffer | null) => void>;
  closed: boolean;
}

const sessions = new Map<string, Session>();

function checkToken(token: unknown): boolean {
  return typeof token === "string" && token === TUNNEL_TOKEN;
}

function createId(): string {
  return `${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
}

function cleanupSession(id: string): void {
  const session = sessions.get(id);
  if (!session) return;
  session.closed = true;
  for (const waiter of session.waiters.splice(0)) waiter(null);
  try {
    session.socket.destroy();
  } catch {
  }
  sessions.delete(id);
}

function createTunnelRouter(prefix: "stream" | "tunnel") {
  router.post(`/${prefix}/open`, (req, res) => {
    if (!checkToken(req.query["token"] ?? req.query["tok"])) {
      res.status(403).json({ error: "forbidden" });
      return;
    }

    const host = String(req.query["host"] ?? "");
    const port = Number.parseInt(String(req.query["port"] ?? ""), 10);
    if (!host || !Number.isInteger(port) || port <= 0 || port > 65535) {
      res.status(400).json({ error: "host/port required" });
      return;
    }

    const id = createId();
    const socket = net.createConnection({ host, port }, () => {
      const session: Session = {
        socket,
        readBuffer: [],
        waiters: [],
        closed: false,
      };
      sessions.set(id, session);

      socket.on("data", (chunk: Buffer) => {
        const current = sessions.get(id);
        if (!current) return;
        const waiter = current.waiters.shift();
        if (waiter) waiter(chunk);
        else current.readBuffer.push(chunk);
      });

      const closeSession = () => {
        const current = sessions.get(id);
        if (!current) return;
        current.closed = true;
        for (const waiter of current.waiters.splice(0)) waiter(null);
      };

      socket.on("close", closeSession);
      socket.on("error", closeSession);
      res.json({ ok: true, id });
    });

    socket.on("error", (error: Error) => {
      if (!sessions.has(id) && !res.headersSent) {
        res.status(502).json({ error: error.message });
      }
    });

    setTimeout(() => {
      if (!sessions.has(id)) socket.destroy();
    }, 10_000);
  });

  router.get(`/${prefix}/read/:id`, async (req, res) => {
    if (!checkToken(req.query["token"] ?? req.query["tok"])) {
      res.status(403).json({ error: "forbidden" });
      return;
    }

    const session = sessions.get(req.params["id"]);
    if (!session) {
      res.status(404).json({ error: "no session" });
      return;
    }

    res.set({
      "Content-Type": "application/octet-stream",
      "Cache-Control": "no-store, no-cache",
      "X-Accel-Buffering": "no",
      Connection: "close",
    });

    if (session.readBuffer.length > 0) {
      res.end(Buffer.concat(session.readBuffer.splice(0)));
      return;
    }

    if (session.closed) {
      cleanupSession(req.params["id"]);
      res.status(410).json({ error: "session closed" });
      return;
    }

    const data = await new Promise<Buffer | null>((resolve) => {
      let waiter: (chunk: Buffer | null) => void;
      const timer = setTimeout(() => {
        const index = session.waiters.indexOf(waiter);
        if (index >= 0) session.waiters.splice(index, 1);
        resolve(Buffer.alloc(0));
      }, 25_000);
      waiter = (chunk) => {
        clearTimeout(timer);
        resolve(chunk);
      };
      session.waiters.push(waiter);
    });

    if (!data) {
      cleanupSession(req.params["id"]);
      res.status(410).json({ error: "session closed" });
      return;
    }

    res.end(data);
  });

  router.post(`/${prefix}/write/:id`, (req, res) => {
    if (!checkToken(req.query["token"] ?? req.query["tok"])) {
      res.status(403).json({ error: "forbidden" });
      return;
    }

    const session = sessions.get(req.params["id"]);
    if (!session) {
      res.status(404).json({ error: "no session" });
      return;
    }

    const chunks: Buffer[] = [];
    req.on("data", (chunk: Buffer) => chunks.push(chunk));
    req.on("end", () => {
      session.socket.write(Buffer.concat(chunks));
      res.json({ ok: true });
    });
  });

  router.delete(`/${prefix}/:id`, (req, res) => {
    if (!checkToken(req.query["token"] ?? req.query["tok"])) {
      res.status(403).json({ error: "forbidden" });
      return;
    }
    cleanupSession(req.params["id"]);
    res.json({ ok: true });
  });

  router.get(`/${prefix}/health`, (_req, res) => {
    res.json({
      ok: true,
      sessions: sessions.size,
      name: NODE_NAME,
      time: new Date().toISOString(),
    });
  });
}

router.get(["/health", "/nodes", "/stats"], (_req, res) => {
  res.json({ ok: true, sessions: sessions.size, name: NODE_NAME });
});

router.get("/v1/models", (_req, res) => {
  res.json({
    object: "list",
    data: [{ id: "tunnel-proxy", object: "model", owned_by: "subnode" }],
  });
});

createTunnelRouter("stream");
createTunnelRouter("tunnel");

setInterval(() => {
  for (const [id, session] of sessions.entries()) {
    if (session.closed) cleanupSession(id);
  }
}, 30_000).unref();

// 友节点自注册：启动后把本实例公开 URL 注册到 VPS 网关，最多重试 6 次
export function selfRegister(attempt = 0): void {
  const domain =
    process.env["MY_URL"] ??
    (process.env["REPLIT_DOMAINS"]
      ? `https://${process.env["REPLIT_DOMAINS"].split(",")[0]?.trim()}`
      : process.env["REPLIT_DEV_DOMAIN"]
        ? `https://${process.env["REPLIT_DEV_DOMAIN"]}`
        : undefined);

  if (!domain) {
    logger.debug("FriendNode self-register skipped: no public domain");
    return;
  }

  const gatewayUrl = `${domain.replace(/\/$/, "")}/api`;
  const body = JSON.stringify({
    gatewayUrl,
    name: NODE_NAME,
    token: TUNNEL_TOKEN,
    source: "runtime",
    model: "gpt-5-mini",
    priority: 2,
  });

  const target = new URL("/api/gateway/self-register", VPS_GATEWAY);
  const client = target.protocol === "https:" ? https : http;
  const request = client.request(
    target,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body),
        "x-register-source": "replit-friend-node",
      },
    },
    (response) => {
      let data = "";
      response.on("data", (chunk) => { data += String(chunk); });
      response.on("end", () => {
        const ok = response.statusCode === 200 || response.statusCode === 201;
        logger.info(
          { statusCode: response.statusCode, url: gatewayUrl, attempt },
          ok ? "FriendNode registered ok" : "FriendNode register non-2xx",
        );
        if (!ok && attempt < 5) {
          setTimeout(() => selfRegister(attempt + 1), 30_000).unref();
        }
      });
    },
  );

  request.on("error", (error) => {
    logger.warn({ err: error, attempt }, "FriendNode self-register error");
    if (attempt < 5) {
      setTimeout(() => selfRegister(attempt + 1), 30_000).unref();
    }
  });
  request.write(body);
  request.end();
}

export default router;
