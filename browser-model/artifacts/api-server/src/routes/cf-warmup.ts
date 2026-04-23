import { Router, type IRouter, type Request, type Response } from "express";
import { renderWithBrowser, getStickyCookies, getStickyAllCookies, readCachedGoogleCookies, STEALTH_INIT, warmupGoogleSession } from "../lib/renderer.js";

const router: IRouter = Router();

// GET /api/cf-warmup?url=...&googleWarmup=1
// Drives the headed-Chromium sticky context through the URL so the CF JS
// challenge auto-resolves; returns cookies[] (full Playwright shape with
// correct domain/path/secure/httpOnly/sameSite/expires) plus the same
// stealth init script the sticky context uses, so external CDP attachers
// can reproduce the JS-fingerprint env that CF originally validated.
//
// When googleWarmup=1 (or target host is replit.com), the sticky context
// first visits google.com / search / reCAPTCHA Enterprise demo / youtube
// to seed NID/AEC/SOCS/LOGIN_INFO cookies + register a recent reCAPTCHA
// client token in the same session — significantly lifts subsequent
// reCAPTCHA Enterprise score (free, ~6s).
router.get("/cf-warmup", async (req: Request, res: Response) => {
  const url = String(req.query.url || "");
  if (!/^https?:\/\//i.test(url)) {
    res.status(400).json({ error: "url query param required (http/https)" });
    return;
  }
  const timeoutMs = Math.min(Math.max(parseInt(String(req.query.timeoutMs ?? "60000"), 10) || 60000, 5000), 120000);
  const reqGoogleWarmup = String(req.query.googleWarmup ?? "") === "1";
  let host = "";
  try { host = new URL(url).hostname; } catch { /* */ }
  // Auto-warmup Google for replit.com (the only path that needs reCAPTCHA score).
  const doGoogleWarmup = reqGoogleWarmup || /(^|\.)replit\.com$/i.test(host);

  const t0 = Date.now();
  let googleInfo: { visited: string[]; durationMs: number; cookieCount: number } | null = null;
  try {
    if (doGoogleWarmup) {
      try {
        googleInfo = await warmupGoogleSession(host);
      } catch (e) {
        console.error("[cf-warmup] google warmup failed:", (e as Error).message);
      }
    }
    let html = "";
    let warmedOk = false;
    try {
      html = await renderWithBrowser(url, timeoutMs);
      warmedOk = true;
    } catch (e) {
      warmedOk = false;
      html = String((e as Error).message || e);
    }
    const cookies = await getStickyCookies(url);
    const cookieHeader = cookies.map((c) => `${c.name}=${c.value}`).join("; ");
    const hasClearance = cookies.some((c) => /^cf_clearance$/i.test(c.name));
    // v7.49 — 额外回传 sticky context 中所有 .google/.gstatic/.youtube/.recaptcha 域 cookies
    // 这些是 warmupGoogleSession() harvest 后注入到 sticky 的 NID/AEC/SOCS/LOGIN_INFO 信任 cookies
    // getStickyCookies(url) 只返回 url 域 (replit.com) 可用的 cookies, 跨域 google cookies 必须单独导出
    // v7.50 — sticky context 注入 google cookies 静默失败 (Playwright addCookies 对 __Secure- 前缀
    // + 跨域 cookie 的 sameSite/secure 校验严格)。直接读 readCachedGoogleCookies() 返回 harvest cache,
    // 这是 warmupGoogleSession() 已经 harvest 过的 NID/AEC/SOCS 等信任 cookies, 给外部 CDP attacher
    // 自己注入到 patchright ctx 里 (patchright addCookies 容忍度更高)
    let googleCookies: typeof cookies = [];
    try {
      const cached = readCachedGoogleCookies();
      if (cached && cached.length > 0) {
        googleCookies = cached.map((c) => ({
          name: c.name, value: c.value, domain: c.domain, path: c.path,
          expires: c.expires, httpOnly: c.httpOnly, secure: c.secure, sameSite: c.sameSite,
        }));
      } else {
        // fallback: try sticky context (in case patches lifted addCookies issue)
        const allCks = await getStickyAllCookies(url);
        googleCookies = allCks.filter((c) =>
          /(^|\.)google\.com$/i.test(c.domain) ||
          /(^|\.)gstatic\.com$/i.test(c.domain) ||
          /(^|\.)youtube\.com$/i.test(c.domain) ||
          /(^|\.)recaptcha\.net$/i.test(c.domain) ||
          /(^|\.)googleapis\.com$/i.test(c.domain)
        );
      }
    } catch (_e) { /* best effort */ }
    res.json({
      ok: warmedOk && hasClearance,
      url,
      ms: Date.now() - t0,
      htmlBytes: html ? html.length : 0,
      cfClearance: hasClearance,
      cookies,
      googleCookies,
      cookieHeader,
      stealthInit: STEALTH_INIT,
      googleWarmup: googleInfo,
    });
  } catch (e) {
    res.status(500).json({ ok: false, error: String((e as Error).message || e), ms: Date.now() - t0 });
  }
});

export default router;
