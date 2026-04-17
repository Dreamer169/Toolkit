/**
 * CDP WebSocket Relay — 会话管理器
 * 用途：让 VPS 上的 cursor_register.py 通过 VPS 本地端口访问用户本地真实 Chrome 的 CDP
 *
 * 架构：
 *   本地 Chrome CDP(:9222) ←→ [cdp_bridge_client.py] ←WS→ VPS:8080/api/cdp-relay/client
 *                                                               ↕ (VPS 内部会话)
 *   cursor_register.py ← --cdp-url http://localhost:8080/api/cdp-relay/<sessionId>
 */
import type { IncomingMessage } from "http";
import type { WebSocket as WSType } from "ws";

export interface CdpSession {
  sessionId: string;
  clientWs: WSType;                   // 本地桥接客户端的 WS 连接
  playwrightWs: WSType | null;        // Playwright/cursor_register 的 CDP WS 连接
  jsonVersion: Record<string, unknown>; // Chrome 的 /json/version 响应
  createdAt: number;
}

const sessions = new Map<string, CdpSession>();

function genId(): string {
  return `cdp_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

/** 注册本地桥接客户端连接 */
export function handleClientConnection(ws: WSType, _req: IncomingMessage): void {
  let sessionId: string | null = null;

  const keepAlive = setInterval(() => {
    if (ws.readyState === 1) ws.send(JSON.stringify({ type: "ping" }));
  }, 20_000);

  ws.on("message", (raw) => {
    try {
      const msg = JSON.parse(raw.toString()) as Record<string, unknown>;

      if (msg["type"] === "register") {
        // 客户端注册，携带 Chrome /json/version 数据
        sessionId = genId();
        const jsonVersion = (msg["jsonVersion"] as Record<string, unknown>) ?? {};
        const session: CdpSession = {
          sessionId,
          clientWs: ws,
          playwrightWs: null,
          jsonVersion,
          createdAt: Date.now(),
        };
        sessions.set(sessionId, session);

        ws.send(JSON.stringify({
          type: "session",
          sessionId,
          relayUrl: `http://localhost:${process.env["PORT"] ?? 8080}/api/cdp-relay/${sessionId}`,
          // 对外 URL 供用户复制到注册请求里
          publicRelayUrl: `http://45.205.27.69:${process.env["PORT"] ?? 8080}/api/cdp-relay/${sessionId}`,
        }));
        console.log(`[cdp-relay] 新会话 ${sessionId} 已注册`);

      } else if (msg["type"] === "cdp-frame") {
        // 来自本地 Chrome 的 CDP 消息 → 转发给 Playwright
        if (sessionId) {
          const session = sessions.get(sessionId);
          if (session?.playwrightWs?.readyState === 1) {
            const data = msg["data"];
            session.playwrightWs.send(typeof data === "string" ? data : JSON.stringify(data));
          }
        }

      } else if (msg["type"] === "pong") {
        // 心跳回应，忽略
      }
    } catch {
      // ignore parse errors
    }
  });

  ws.on("close", () => {
    clearInterval(keepAlive);
    if (sessionId) {
      const session = sessions.get(sessionId);
      session?.playwrightWs?.close();
      sessions.delete(sessionId);
      console.log(`[cdp-relay] 会话 ${sessionId} 已关闭`);
    }
  });
}

/** Playwright/cursor_register 通过 CDP WebSocket 连接 */
export function handlePlaywrightConnection(ws: WSType, sessionId: string): void {
  const session = sessions.get(sessionId);
  if (!session) {
    ws.close(1008, "Session not found");
    return;
  }

  session.playwrightWs = ws;

  // 通知本地桥接客户端：开始连接 Chrome CDP
  session.clientWs.send(JSON.stringify({ type: "cdp-connect" }));
  console.log(`[cdp-relay] Playwright 已连接会话 ${sessionId}`);

  ws.on("message", (raw) => {
    // Playwright → Chrome（经过本地桥接）
    if (session.clientWs.readyState === 1) {
      session.clientWs.send(JSON.stringify({ type: "cdp-frame", data: raw.toString() }));
    }
  });

  ws.on("close", () => {
    session.playwrightWs = null;
    if (session.clientWs.readyState === 1) {
      session.clientWs.send(JSON.stringify({ type: "cdp-disconnect" }));
    }
    console.log(`[cdp-relay] Playwright 断开会话 ${sessionId}`);
  });
}

export function getSession(sessionId: string): CdpSession | undefined {
  return sessions.get(sessionId);
}

export function listSessions(): Array<{ sessionId: string; age: number; connected: boolean }> {
  return [...sessions.values()].map((s) => ({
    sessionId: s.sessionId,
    age: Date.now() - s.createdAt,
    connected: s.playwrightWs !== null,
  }));
}

// 定期清理超时会话（30分钟）
setInterval(() => {
  const now = Date.now();
  for (const [id, session] of sessions) {
    if (now - session.createdAt > 30 * 60 * 1000) {
      session.clientWs.close();
      session.playwrightWs?.close();
      sessions.delete(id);
    }
  }
}, 5 * 60 * 1000);
