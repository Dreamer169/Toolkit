import { Router, type IRouter, type Request, type Response } from "express";
import { renderWithBrowser, getStickyCookieHeader } from "../lib/renderer.js";

const router: IRouter = Router();

// GET /api/cf-warmup?url=https://replit.com/signup
// Drives the headed-Chromium sticky context through the URL so the CF JS
// challenge auto-resolves; returns the resulting cookie jar (cf_clearance et al)
// in a Playwright-compatible cookies[] shape so external tools (Camoufox /
// chromium / Puppeteer) can inject and skip CF on subsequent visits.
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
    const cookieHeader = await getStickyCookieHeader(url);
    const cookies = cookieHeader
      .split(/;\s*/)
      .filter(Boolean)
      .map((p) => {
        const i = p.indexOf("=");
        if (i < 0) return null;
        const name = p.slice(0, i).trim();
        const value = p.slice(i + 1).trim();
        let domain: string;
        try { domain = new URL(url).hostname; } catch { domain = ""; }
        return { name, value, domain, path: "/", secure: true, httpOnly: false, sameSite: "Lax" as const };
      })
      .filter(Boolean);
    const hasClearance = cookies.some((c) => c && /^cf_clearance$/i.test(c.name));
    res.json({
      ok: warmedOk && hasClearance,
      url,
      ms: Date.now() - t0,
      htmlBytes: html ? html.length : 0,
      cfClearance: hasClearance,
      cookies,
      cookieHeader,
    });
  } catch (e) {
    res.status(500).json({ ok: false, error: String((e as Error).message || e), ms: Date.now() - t0 });
  }
});

export default router;
