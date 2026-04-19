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
      "Transfer-Encoding": "chunked",
      "Content-Type": "application/octet-stream",
      "Cache-Control": "no-store, no-cache",
      "X-Accel-Buffering": "no",
      Connection: "keep-alive",
    });
    res.flushHeaders();

    const writeChunk = (data: Buffer) => {
      res.write(data);
      const flushable = res as typeof res & { flush?: () => void };
      flushable.flush?.();
    };

    while (!res.destroyed) {
      if (session.readBuffer.length > 0) {
        writeChunk(Buffer.concat(session.readBuffer.splice(0)));
      } else if (session.closed) {
        res.end();
        return;
      } else {
        const data = await new Promise<Buffer | null>((resolve) =>
          session.waiters.push(resolve),
        );
        if (!data) {
          res.end();
          return;
        }
        writeChunk(data);
      }
    }
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

export function selfRegister(): void {
  const domain =
    process.env["MY_URL"] ??
    (process.env["REPLIT_DOMAINS"]
      ? `https://${process.env["REPLIT_DOMAINS"].split(",")[0]?.trim()}`
      : process.env["REPLIT_DEV_DOMAIN"]
        ? `https://${process.env["REPLIT_DEV_DOMAIN"]}`
        : undefined);

  if (!domain) {
    logger.warn("Subnode self-register skipped: no public domain");
    return;
  }

  const body = JSON.stringify({
    gatewayUrl: `${domain.replace(/\/$/, "")}/api`,
    name: NODE_NAME,
    token: TUNNEL_TOKEN,
    source: "runtime",
    model: "gpt-5-mini",
    priority: 5,
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
      },
    },
    (response) => {
      let data = "";
      response.on("data", (chunk) => {
        data += String(chunk);
      });
      response.on("end", () => {
        logger.info(
          { statusCode: response.statusCode, response: data.slice(0, 200) },
          "Subnode self-register completed",
        );
      });
    },
  );

  request.on("error", (error) => {
    logger.warn({ err: error }, "Subnode self-register failed");
  });
  request.write(body);
  request.end();
}

export default router;
