/**
 * routes/sync.ts — 多窗口 CDP 动作同步器 HTTP API
 *
 * POST   /api/browser/sync/start              { masterSessionId, followerSessionIds[], options? }
 * POST   /api/browser/sync/stop
 * GET    /api/browser/sync/status
 * POST   /api/browser/sync/navigate            { url, includeMaster? }
 * GET    /api/browser/sync/sessions            list all active sessions in registry
 * POST   /api/browser/sync/replay              { events[], sessionIds?, includeMaster? }
 * POST   /api/browser/sync/recording/start     { maxEvents?, filterTypes?, clearFirst? }
 * POST   /api/browser/sync/recording/stop
 * GET    /api/browser/sync/recording           ?types=click,navigate&maxEvents=100&asReplay=true
 * DELETE /api/browser/sync/recording           clear buffer
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

/**
 * POST /browser/sync/replay
 *
 * Replay a pre-recorded sequence of CDP/DOM events to the target follower sessions.
 * Useful for scripted automation, regression testing, and multi-window sync validation.
 *
 * Body:
 *   events[]         — array of { type, payload, delayMs? }
 *   sessionIds?      — target session IDs (default: all active followers)
 *   includeMaster?   — also apply to master (default: false)
 *
 * Supported event types: navigate / click / input / change / wheel / scroll / keydown / mouse_move
 *
 * Example:
 *   { "events": [
 *       { "type": "navigate", "payload": { "url": "https://example.com" } },
 *       { "type": "click",    "payload": { "selector": "#btn" }, "delayMs": 500 },
 *       { "type": "input",    "payload": { "selector": "#q", "value": "hello" }, "delayMs": 200 }
 *   ]}
 */
router.post("/browser/sync/replay", async (req, res) => {
  try {
    const { events, sessionIds, includeMaster } = req.body as {
      events: Array<{ type: string; payload: Record<string, unknown>; delayMs?: number }>;
      sessionIds?: string[];
      includeMaster?: boolean;
    };
    if (!Array.isArray(events)) {
      res.status(400).json({ ok: false, error: "events[] array is required" });
      return;
    }
    const result = await synchronizer.replay(events, { sessionIds, includeMaster });
    res.json({ ok: true, ...result });
  } catch (e) {
    res.status(500).json({ ok: false, error: String((e as Error).message ?? e) });
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// Recording endpoints
// ─────────────────────────────────────────────────────────────────────────────

/**
 * POST /browser/sync/recording/start
 *
 * Start buffering all master events server-side.
 * Every event type that passes through _record() is captured with an inter-event
 * delayMs, producing a buffer that can be passed directly to /replay.
 *
 * Body (all optional):
 *   maxEvents   — hard cap on buffer size (default 5000)
 *   filterTypes — array of event type strings to record; empty = record all
 *   clearFirst  — clear existing buffer before starting (default true)
 */
router.post("/browser/sync/recording/start", (req, res) => {
  try {
    const { maxEvents, filterTypes, clearFirst } = req.body as {
      maxEvents?:   number;
      filterTypes?: string[];
      clearFirst?:  boolean;
    };
    const status = synchronizer.startRecording({ maxEvents, filterTypes, clearFirst });
    res.json({ ok: true, recording: status });
  } catch (e) {
    res.status(500).json({ ok: false, error: String((e as Error).message ?? e) });
  }
});

/**
 * POST /browser/sync/recording/stop
 *
 * Stop recording. Buffer is preserved; fetch it with GET /recording.
 */
router.post("/browser/sync/recording/stop", (req, res) => {
  try {
    const status = synchronizer.stopRecording();
    res.json({ ok: true, recording: status });
  } catch (e) {
    res.status(500).json({ ok: false, error: String((e as Error).message ?? e) });
  }
});

/**
 * GET /browser/sync/recording
 *
 * Return the recorded event buffer.
 *
 * Query params:
 *   types     — comma-separated event types to include (default: all)
 *   maxEvents — max number of events returned (most recent, default: all)
 *   asReplay  — if "true", strip ts field so the response events[]
 *               can be passed directly to POST /replay
 */
router.get("/browser/sync/recording", (req, res) => {
  try {
    const types     = req.query.types     ? String(req.query.types).split(",").map(s => s.trim()).filter(Boolean) : undefined;
    const maxEvents = req.query.maxEvents ? parseInt(String(req.query.maxEvents), 10) : undefined;
    const asReplay  = req.query.asReplay  === "true" || req.query.asReplay === "1";
    const result = synchronizer.getRecording({ types, maxEvents, asReplay });
    res.json({ ok: true, ...result });
  } catch (e) {
    res.status(500).json({ ok: false, error: String((e as Error).message ?? e) });
  }
});

/**
 * DELETE /browser/sync/recording
 *
 * Clear the recording buffer without stopping an active recording session.
 */
router.delete("/browser/sync/recording", (req, res) => {
  try {
    const status = synchronizer.clearRecording();
    res.json({ ok: true, recording: status });
  } catch (e) {
    res.status(500).json({ ok: false, error: String((e as Error).message ?? e) });
  }
});

export default router;
