/**
 * routes/sync.ts — 多窗口 CDP 动作同步器 HTTP API
 *
 * POST /api/browser/sync/start    { masterSessionId, followerSessionIds[], options? }
 * POST /api/browser/sync/stop
 * GET  /api/browser/sync/status
 * POST /api/browser/sync/navigate { url, includeMaster? }
 * GET  /api/browser/sync/sessions  列出当前 registry 中所有活跃 sessionId
 */
import { Router, type IRouter } from "express";
import { synchronizer } from "../lib/cdp-synchronizer.js";
import { sessionRegistry } from "../lib/cdp-broker.js";

const router: IRouter = Router();

router.post("/browser/sync/start", (req, res) => {
  try {
    const { masterSessionId, followerSessionIds, options } = req.body as {
      masterSessionId: string;
      followerSessionIds: string[];
      options?: Record<string, unknown>;
    };
    if (!masterSessionId || !Array.isArray(followerSessionIds) || followerSessionIds.length === 0) {
      res.status(400).json({ ok: false, error: "masterSessionId and non-empty followerSessionIds[] required" });
      return;
    }
    const status = synchronizer.start(masterSessionId, followerSessionIds, options ?? {});
    res.json({ ok: true, ...status });
  } catch (e) {
    res.status(400).json({ ok: false, error: String((e as Error).message ?? e) });
  }
});

router.post("/browser/sync/stop", (_req, res) => {
  const status = synchronizer.stop();
  res.json({ ok: true, ...status });
});

router.get("/browser/sync/status", (_req, res) => {
  res.json({ ok: true, ...synchronizer.status() });
});

router.post("/browser/sync/navigate", (req, res) => {
  try {
    const { url, includeMaster } = req.body as { url: string; includeMaster?: boolean };
    if (!url) { res.status(400).json({ ok: false, error: "url required" }); return; }
    const result = synchronizer.navigate(url, includeMaster !== false);
    res.json({ ok: true, ...result });
  } catch (e) {
    res.status(400).json({ ok: false, error: String((e as Error).message ?? e) });
  }
});

router.get("/browser/sync/sessions", (_req, res) => {
  const sessions = Array.from(sessionRegistry.entries()).map(([id, sess]) => ({
    sessionId: id,
    hasPage:   !!sess.getPage(),
    hasCdp:    !!sess.getCdp(),
  }));
  res.json({ ok: true, count: sessions.length, sessions });
});

export default router;
