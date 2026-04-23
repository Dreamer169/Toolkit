import { Router, type IRouter, type Request, type Response } from "express";
import { renderWithBrowser, getStickyCookies, STEALTH_INIT } from "../lib/renderer.js";

const router: IRouter = Router();

// GET /api/cf-warmup?url=...
// Drives the headed-Chromium sticky context through the URL so the CF JS
// challenge auto-resolves; returns cookies[] (full Playwright shape with
// correct domain/path/secure/httpOnly/sameSite/expires) plus the same
// stealth init script the sticky context uses, so external CDP attachers
// can reproduce the JS-fingerprint env that CF originally validated.
router.get("/cf-warmup", async (req: Request, res: Response) => {
  const url = String(req.query.url || "");
  if (!/^https?:\/\//i.test(url)) {
    res.status(400).json({ error: "url query param required (http/https)" });
    return;
  }
  const timeoutMs = Math.min(Math.max(parseInt(String(req.query.timeoutMs ?? "60000"), 10) || 60000, 5000), 120000);
  const t0 = Date.now();
  try {
    let html = "";
    let warmedOk = false;
    try {
      html = await renderWithBrowser(url, timeoutMs);
      warmedOk = true;
    } catch (e) {
      warmedOk = false;
      html = String((e as Error).message || e);
    }
    // Full-attribute cookies (preserves SameSite=None / Secure / HttpOnly /
    // expires / true domain). The previous header-parse path lost all of
    // that and CF rejected the re-injected cf_clearance.
    const cookies = await getStickyCookies(url);
    // Legacy field for any old caller still parsing cookieHeader.
    const cookieHeader = cookies.map((c) => `${c.name}=${c.value}`).join("; ");
    const hasClearance = cookies.some((c) => /^cf_clearance$/i.test(c.name));
    res.json({
      ok: warmedOk && hasClearance,
      url,
      ms: Date.now() - t0,
      htmlBytes: html ? html.length : 0,
      cfClearance: hasClearance,
      cookies,
      cookieHeader,
      stealthInit: STEALTH_INIT,
    });
  } catch (e) {
    res.status(500).json({ ok: false, error: String((e as Error).message || e), ms: Date.now() - t0 });
  }
});

export default router;
