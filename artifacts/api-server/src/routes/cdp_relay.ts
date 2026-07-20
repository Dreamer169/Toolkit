/**
 * CDP Relay HTTP 路由
 * GET /api/cdp-relay/sessions        → 列出所有活跃会话
 * GET /api/cdp-relay/:sessionId      → 会话信息
 * GET /api/cdp-relay/:sessionId/json/version → 供 Playwright connect_over_cdp 使用
 * GET /api/cdp-relay/:sessionId/json → 供 Playwright 列出 target
 *
 * WebSocket 路由（在 index.ts 的 upgrade 事件中处理）：
 * WS /api/cdp-relay/client           → 本地桥接客户端连接
 * WS /api/cdp-relay/:sessionId/playwright → Playwright CDP WS
 */
import { Router } from "express";
import { getSession, listSessions } from "../lib/cdp_relay_ws.js";

const router = Router();

/** 列出活跃会话 */
router.get("/cdp-relay/sessions", (_req, res) => {
  res.json({ success: true, sessions: listSessions() });
});

/** 会话基本信息 */
router.get("/cdp-relay/:sessionId", (req, res) => {
  const session = getSession(req.params["sessionId"]!);
  if (!session) return void res.status(404).json({ success: false, error: "会话不存在" });
  res.json({
    success: true,
    sessionId: session.sessionId,
    relayUrl: `http://localhost:${process.env["PORT"] ?? 8080}/api/cdp-relay/${session.sessionId}`,
    connected: session.playwrightWs !== null,
    age: Date.now() - session.createdAt,
  });
});

/** Playwright connect_over_cdp 第一步：GET /json/version */
router.get("/cdp-relay/:sessionId/json/version", (req, res) => {
  const session = getSession(req.params["sessionId"]!);
  if (!session) return void res.status(404).json({ error: "会话不存在" });

  const port = process.env["PORT"] ?? 8080;
  const data = {
    ...session.jsonVersion,
    // Playwright 会连接此 WebSocket
    webSocketDebuggerUrl: `ws://localhost:${port}/api/cdp-relay/${session.sessionId}/playwright`,
  };
  res.json(data);
});

/** Playwright 有时也会访问 /json */
router.get("/cdp-relay/:sessionId/json", (req, res) => {
  const session = getSession(req.params["sessionId"]!);
  if (!session) return void res.status(404).json([]);
  const port = process.env["PORT"] ?? 8080;
  res.json([{
    ...session.jsonVersion,
    webSocketDebuggerUrl: `ws://localhost:${port}/api/cdp-relay/${session.sessionId}/playwright`,
  }]);
});

export default router;
