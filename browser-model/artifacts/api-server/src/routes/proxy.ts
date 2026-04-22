import { Router, type IRouter, type Request, type Response } from "express";
import { needsJsRendering, renderWithBrowser, getStickyCookieHeader, storeStickyCookies, looksLikeCfChallengeHtml } from "../lib/renderer.js";
import type { Dispatcher } from "undici";

const router: IRouter = Router();

let proxyDispatcher: Dispatcher | undefined;
async function getProxyDispatcher(): Promise<Dispatcher | undefined> {
  const url = process.env.BROWSER_PROXY;
  if (!url) return undefined;
  if (proxyDispatcher) return proxyDispatcher;
  const m = url.match(/^socks5h?:\/\/(?:([^@]+)@)?([^:]+):(\d+)\/?$/i);
  if (!m) {
    console.warn("[proxy] BROWSER_PROXY must be socks5://host:port, got:", url);
    return undefined;
  }
  const [, , host, portStr] = m;
  const port = Number(portStr);
  const { Agent } = await import("undici");
  const { SocksClient } = await import("socks");
  const tls = await import("node:tls");
  const dns = await import("node:dns");

  // GFW poisons UDP DNS responses. Use TCP-mode resolver against a clean
  // public upstream so we get the real IP, then hand the IP (not hostname)
  // to SOCKS5 — this also bypasses the vless server's polluted resolver.
  const cleanResolver = new dns.promises.Resolver({ timeout: 4000, tries: 2 });
  cleanResolver.setServers(["1.1.1.1", "8.8.8.8", "9.9.9.9"]);

  const dnsCache = new Map<string, { ip: string; expires: number }>();
  async function resolveCleanIPv4(host: string): Promise<string> {
    // Already an IP literal?
    if (/^\d+\.\d+\.\d+\.\d+$/.test(host)) return host;
    if (/^[\[]?[0-9a-f:]+[\]]?$/i.test(host) && host.includes(":")) return host;
    const cached = dnsCache.get(host);
    if (cached && cached.expires > Date.now()) return cached.ip;
    try {
      const ips = await cleanResolver.resolve4(host);
      if (ips && ips.length) {
        const ip = ips[Math.floor(Math.random() * ips.length)];
        dnsCache.set(host, { ip, expires: Date.now() + 5 * 60 * 1000 });
        return ip;
      }
    } catch (e) {
      console.warn("[proxy] clean DNS failed for", host, (e as Error).message);
    }
    // Fallback: let SOCKS resolve remotely (may fail on poisoned domains)
    return host;
  }
  proxyDispatcher = new Agent({
    connect: async (opts: Record<string, unknown>, cb: (err: Error | null, sock?: unknown) => void) => {
      try {
        const dstHost = String(opts.hostname);
        const isTls = opts.protocol === "https:";
        const rawPort = opts.port;
        const dstPort = rawPort && Number(rawPort) > 0 ? Number(rawPort) : (isTls ? 443 : 80);
        const dstIp = await resolveCleanIPv4(dstHost);
        const { socket } = await SocksClient.createConnection({
          proxy: { host, port, type: 5 },
          command: "connect",
          destination: { host: dstIp, port: dstPort },
        });
        if (isTls) {
          const tlsSocket = tls.connect({
            socket,
            servername: (opts.servername as string) || dstHost,  // SNI = real hostname, not IP
            ALPNProtocols: opts.ALPNProtocols as string[] | undefined,
          });
          tlsSocket.once("secureConnect", () => cb(null, tlsSocket));
          tlsSocket.once("error", (err: Error) => cb(err));
        } else {
          cb(null, socket);
        }
      } catch (err) {
        cb(err as Error);
      }
    },
  });
  console.log("[proxy] outbound fetch routed via", url);
  return proxyDispatcher;
}

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailers",
  "transfer-encoding",
  "upgrade",
  "content-encoding",
  "content-length",
  "content-security-policy",
  "content-security-policy-report-only",
  "x-frame-options",
  "x-content-type-options",
  "strict-transport-security",
  "permissions-policy",
  "cross-origin-opener-policy",
  "cross-origin-embedder-policy",
  "cross-origin-resource-policy",
  "report-to",
  "nel",
]);

function absolutize(target: URL, ref: string): string {
  try {
    return new URL(ref, target).toString();
  } catch {
    return ref;
  }
}

function rewriteHtml(html: string, target: URL, proxyBase: string): string {
  const proxify = (raw: string): string => {
    const trimmed = raw.trim();
    if (!trimmed) return raw;
    if (trimmed.startsWith("data:") || trimmed.startsWith("blob:") || trimmed.startsWith("javascript:") || trimmed.startsWith("mailto:") || trimmed.startsWith("tel:") || trimmed.startsWith("#")) {
      return raw;
    }
    const abs = absolutize(target, trimmed);
    return `${proxyBase}?url=${encodeURIComponent(abs)}`;
  };

  let out = html;

  // Strip CSP meta tags + X-Frame-Options meta tags
  out = out.replace(/<meta[^>]+http-equiv=["']?content-security-policy["']?[^>]*>/gi, "");
  out = out.replace(/<meta[^>]+http-equiv=["']?x-frame-options["']?[^>]*>/gi, "");

  // Strip any existing <base> tags so we control resolution
  out = out.replace(/<base\b[^>]*>/gi, "");

  // Rewrite href/src/action attributes
  out = out.replace(/\b(href|src|action|formaction|poster|data-src)\s*=\s*"([^"]*)"/gi, (_m, attr, val) => `${attr}="${proxify(val)}"`);
  out = out.replace(/\b(href|src|action|formaction|poster|data-src)\s*=\s*'([^']*)'/gi, (_m, attr, val) => `${attr}='${proxify(val)}'`);

  // Inject hidden __upstream__ input into every <form>. Browsers drop the action
  // URL's query string on GET-submit, so the url= we put in the action is lost.
  // The hidden field becomes part of the submitted form fields and thus survives.
  out = out.replace(/<form\b([^>]*)>/gi, (full, attrs) => {
    const actionMatch = /\baction\s*=\s*["']([^"']*)["']/i.exec(attrs);
    let upstream: string;
    if (actionMatch && actionMatch[1]) {
      const actionVal = actionMatch[1];
      // Action was already proxified above; unwrap to recover the upstream URL.
      try {
        const a = new URL(actionVal, target.toString());
        upstream = a.searchParams.get("url") || a.toString();
      } catch {
        upstream = target.toString();
      }
    } else {
      upstream = target.toString();
    }
    const hidden = `<input type="hidden" name="__upstream__" value="${upstream.replace(/"/g, "&quot;")}">`;
    return `<form${attrs}>${hidden}`;
  });

  // srcset (multiple URLs)
  out = out.replace(/\bsrcset\s*=\s*"([^"]*)"/gi, (_m, val) => {
    const rewritten = val
      .split(",")
      .map((part: string) => {
        const trimmed = part.trim();
        const [url, size] = trimmed.split(/\s+/, 2);
        return size ? `${proxify(url)} ${size}` : proxify(url);
      })
      .join(", ");
    return `srcset="${rewritten}"`;
  });

  // Inject navigation interceptor: rewrites runtime location/href changes,
  // window.open calls, and form submissions to route through the proxy.
  // Without this, JS like `window.location = "https://example.com"` makes
  // the iframe navigate directly to the original URL (which then fails
  // due to X-Frame-Options).
  const interceptor = `<script>(function(){
    var PROXY = ${JSON.stringify(proxyBase)};
    var BASE = ${JSON.stringify(target.toString())};
    try { window.parent && window.parent.postMessage({ type: 'browser-model:navigated', url: BASE }, '*'); } catch(_){}
    function abs(u){ try{ return new URL(u, BASE).toString(); }catch(_){ return u; } }
    var PROXY_PATH = (function(){ try { return new URL(PROXY).pathname; } catch(_){ return '/api/proxy'; } })();
    function px(u){
      if(!u) return u;
      var s = String(u);
      if(/^(data:|blob:|javascript:|mailto:|tel:|about:|#)/i.test(s)) return s;
      var a;
      try { a = abs(s); } catch(_) { return s; }
      // Already wrapped with OUR proxy origin → pass through.
      if (a.indexOf(PROXY) === 0) return a;
      // Wrapped with proxy path on the upstream origin (happens when the
      // server-side rewriter emitted a relative href like "/api/proxy?url=ENC"
      // and abs() resolved it against the upstream BASE). Unwrap and re-wrap
      // with our actual PROXY origin so we don\'t hit replit.com/api/proxy.
      try {
        var u2 = new URL(a);
        if (u2.pathname === PROXY_PATH && u2.searchParams.has("url")) {
          return PROXY + "?url=" + encodeURIComponent(u2.searchParams.get("url"));
        }
      } catch(_){}
      return PROXY + "?url=" + encodeURIComponent(a);
    }
    // NOTE: Do NOT override Location.prototype.{assign,replace,href}.
    // In Chromium these accessors check internal slots on \`this\` and only
    // trigger navigation when invoked via the real Location IDL slot, not via
    // a JS-level prototype redefinition. Wrapping them here ALSO breaks them
    // (origAssign/origReplace are undefined on Location.prototype in modern
    // Chromium because the methods live on the instance). The click/form/
    // fetch/XHR hooks below already wrap URLs through px() before any user
    // code touches location, so we get the same effect without breaking nav.
    try {
      var origOpen = window.open;
      window.open = function(u, n, f){ return origOpen.call(this, px(u), n, f); };
    } catch(_){}
    document.addEventListener('click', function(e){
      var a = e.target && e.target.closest && e.target.closest('a[href]');
      if(!a) return;
      var h = a.getAttribute('href');
      if(!h || /^(javascript:|mailto:|tel:|#)/i.test(h)) return;
      // If link already points at our proxy (absolute or relative path), let
      // browser handle it natively — px() would no-op anyway.
      e.preventDefault();
      window.location.href = px(h);
    }, true);
    document.addEventListener('submit', function(e){
      var f = e.target;
      if(!f || f.tagName !== 'FORM') return;
      // Resolve the real upstream target (strip proxy wrapping if already wrapped)
      var rawAction = f.getAttribute('action') || BASE;
      var target;
      try {
        var aUrl = new URL(rawAction, BASE);
        var wrapped = aUrl.searchParams.get('url');
        target = new URL(wrapped || aUrl.toString(), BASE);
      } catch(_) { return; }
      var method = (f.method || 'GET').toUpperCase();
      if (method === 'GET') {
        // Browsers DROP existing query string of action on GET submit and
        // rebuild it from form fields. So we must preventDefault, merge
        // form fields into the upstream target URL, and navigate manually.
        e.preventDefault();
        try {
          var fd = new FormData(f);
          var sp = new URLSearchParams();
          fd.forEach(function(v,k){ if(typeof v === 'string') sp.append(k, v); });
          target.search = sp.toString();
        } catch(_){}
        window.location.href = px(target.href);
      } else {
        // POST: action's query is preserved; safe to wrap.
        if (String(f.action).indexOf(PROXY) !== 0) f.action = px(target.href);
      }
    }, true);
    // Override fetch — SPAs call fetch('/api/...') which would otherwise
    // resolve against our localhost origin instead of the upstream site.
    try {
      var origFetch = window.fetch;
      window.fetch = function(input, init){
        try {
          if (typeof input === 'string') {
            input = px(input);
          } else if (input && typeof input === 'object' && 'url' in input) {
            // Request object — rebuild with rewritten URL
            input = new Request(px(input.url), input);
          }
        } catch(_){}
        return origFetch.call(this, input, init);
      };
    } catch(_){}
    // Override XHR
    try {
      var origXhrOpen = XMLHttpRequest.prototype.open;
      XMLHttpRequest.prototype.open = function(m, u){
        arguments[1] = px(u);
        return origXhrOpen.apply(this, arguments);
      };
    } catch(_){}
    // Override sendBeacon (analytics, telemetry)
    try {
      if (navigator.sendBeacon) {
        var origBeacon = navigator.sendBeacon.bind(navigator);
        navigator.sendBeacon = function(u, d){ return origBeacon(px(u), d); };
      }
    } catch(_){}
    // Notify parent of SPA route changes (pushState / replaceState / popstate)
    function notifyParent(){
      try {
        // Reverse the proxy URL back to the upstream URL we're really on
        var here = window.location.href;
        var m = here.match(/[?&]url=([^&]+)/);
        var upstream = m ? decodeURIComponent(m[1]) : BASE;
        window.parent && window.parent.postMessage({ type: 'browser-model:navigated', url: upstream }, '*');
      } catch(_){}
    }
    try {
      var origPush = history.pushState;
      var origReplace = history.replaceState;
      history.pushState = function(){ var r = origPush.apply(this, arguments); setTimeout(notifyParent, 0); return r; };
      history.replaceState = function(){ var r = origReplace.apply(this, arguments); setTimeout(notifyParent, 0); return r; };
      window.addEventListener('popstate', function(){ setTimeout(notifyParent, 0); });
      window.addEventListener('hashchange', function(){ setTimeout(notifyParent, 0); });
    } catch(_){}
    // Block service worker registration — wrong origin/scope causes hard errors
    try {
      if (navigator.serviceWorker) {
        Object.defineProperty(navigator, 'serviceWorker', {
          configurable: true,
          get: function(){
            return {
              register: function(){ return Promise.reject(new Error('blocked')); },
              getRegistration: function(){ return Promise.resolve(undefined); },
              getRegistrations: function(){ return Promise.resolve([]); },
              addEventListener: function(){},
              removeEventListener: function(){},
              ready: new Promise(function(){})
            };
          }
        });
      }
    } catch(_){}
  })();</script>`;
  if (/<head[^>]*>/i.test(out)) {
    out = out.replace(/<head([^>]*)>/i, (m) => `${m}${interceptor}`);
  } else {
    out = interceptor + out;
  }

  return out;
}

function rewriteCss(css: string, target: URL, proxyBase: string): string {
  return css.replace(/url\(\s*(['"]?)([^'")]+)\1\s*\)/gi, (_m, q, ref) => {
    const trimmed = ref.trim();
    if (!trimmed || trimmed.startsWith("data:") || trimmed.startsWith("blob:") || trimmed.startsWith("#")) {
      return `url(${q}${ref}${q})`;
    }
    const abs = absolutize(target, trimmed);
    return `url(${q}${proxyBase}?url=${encodeURIComponent(abs)}${q})`;
  });
}

const RESERVED_PARAMS = new Set(["url", "__upstream__", "render", "skipRender", "forceRender"]);

router.all("/proxy", async (req: Request, res: Response) => {
  // Prefer __upstream__ (injected into forms as hidden input — survives browser's
  // GET-form behaviour of dropping the action URL's query string). Fall back to url=.
  const rawUrl =
    (typeof req.query["__upstream__"] === "string" ? req.query["__upstream__"] : "") ||
    (typeof req.query["url"] === "string" ? req.query["url"] : "");
  if (!rawUrl) {
    res.status(400).type("text/plain").send("Missing ?url=");
    return;
  }

  let target: URL;
  try {
    target = new URL(rawUrl);
  } catch {
    res.status(400).type("text/plain").send("Invalid url");
    return;
  }

  // Merge any extra query parameters from the proxy request into the upstream URL
  // (form GET submits land here as /api/proxy?__upstream__=...&q=trump&...).
  for (const [k, v] of Object.entries(req.query)) {
    if (RESERVED_PARAMS.has(k)) continue;
    if (Array.isArray(v)) {
      for (const vv of v) target.searchParams.append(k, String(vv));
    } else if (v != null) {
      target.searchParams.append(k, String(v));
    }
  }

  if (target.protocol !== "http:" && target.protocol !== "https:") {
    res.status(400).type("text/plain").send("Only http(s) supported");
    return;
  }

  // Block private/internal addresses
  const host = target.hostname;
  if (
    host === "localhost" ||
    host.endsWith(".local") ||
    host.startsWith("127.") ||
    host.startsWith("10.") ||
    host.startsWith("192.168.") ||
    /^169\.254\./.test(host) ||
    /^172\.(1[6-9]|2\d|3[0-1])\./.test(host) ||
    host === "0.0.0.0" ||
    host === "::1"
  ) {
    res.status(403).type("text/plain").send("Blocked");
    return;
  }

  const proxyBase = `${req.baseUrl || ""}/proxy`.replace(/\/+/g, "/");
  const forceRender = req.query.render === "1";
  const skipRender = req.query.render === "0";

  // Detect static asset requests (CSS/JS/images/fonts/etc) — these don't need a browser
  const pathname = target.pathname.toLowerCase();
  const isAsset = /\.(css|js|mjs|cjs|json|xml|rss|ico|png|jpe?g|gif|webp|avif|svg|bmp|woff2?|ttf|eot|otf|mp4|webm|ogg|mp3|wav|pdf|zip|wasm|map)(\?|$)/.test(pathname);
  const acceptsHtml = (req.headers.accept || "").toLowerCase().includes("text/html");
  const fetchDest = (req.headers["sec-fetch-dest"] as string | undefined) || "";
  // A real top-level navigation OR our first request from a fresh tab (no sec-fetch-dest at all).
  // Sub-iframes (sec-fetch-dest=iframe), workers, fetch() etc. must NEVER be Playwright-rendered —
  // doing so snapshots interactive widgets (reCAPTCHA, embeds) into broken static HTML.
  const isTopLevelDoc = !isAsset && (fetchDest === "document" || (!fetchDest && acceptsHtml));

  const isGet = req.method === "GET" || req.method === "HEAD";

  // Use real browser for sites that block server-side fetches, or for any top-level document.
  // Only GET can be rendered by a browser (Playwright's goto is GET).
  const shouldRender = isGet && !skipRender && (forceRender || (isTopLevelDoc && needsJsRendering(target.hostname)) || isTopLevelDoc);

  if (shouldRender) {
    try {
      const { html, finalUrl: finalUrlStr, status } = await renderWithBrowser(target.toString());
      const finalUrl = new URL(finalUrlStr);
      const rewritten = rewriteHtml(html, finalUrl, proxyBase);
      res.status(status);
      res.setHeader("content-type", "text/html; charset=utf-8");
      res.setHeader("access-control-allow-origin", "*");
      res.send(rewritten);
      return;
    } catch (err) {
      console.error("[renderer] Browser render failed:", target.toString(), err);
      req.log.error({ err, url: target.toString() }, "Browser render failed");
      // fall through to plain fetch as backup
    }
  }

  try {
    const fetchHeaders: Record<string, string> = {
      "User-Agent":
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
      "Accept":
        (req.headers.accept as string) ||
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
      "Accept-Language": "en-US,en;q=0.9",
      "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not.A/Brand";v="24"',
      "sec-ch-ua-mobile": "?0",
      "sec-ch-ua-platform": '"macOS"',
      "Upgrade-Insecure-Requests": "1",
      "Referer": `https://${target.hostname}/`,
    };
    if (req.headers["content-type"] && !isGet) {
      fetchHeaders["Content-Type"] = req.headers["content-type"] as string;
    }
    // Forward client cookies + per-site sticky jar (carries CF clearance set by the
    // top-level Playwright render, so XHR/fetch sub-requests don't get re-challenged).
    {
      const sticky = await getStickyCookieHeader(target.toString());
      const client = (req.headers.cookie as string) || "";
      const merged = [sticky, client].filter(Boolean).join("; ");
      if (merged) fetchHeaders["Cookie"] = merged;
    }

    let body: BodyInit | undefined;
    if (!isGet) {
      const chunks: Buffer[] = [];
      await new Promise<void>((resolve, reject) => {
        req.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
        req.on("end", () => resolve());
        req.on("error", reject);
      });
      body = Buffer.concat(chunks);
    }

    const dispatcher = await getProxyDispatcher();
    const upstream = await fetch(target.toString(), {
      method: req.method,
      redirect: "follow",
      headers: fetchHeaders,
      body,
      ...(dispatcher ? { dispatcher } : {}),
    } as RequestInit & { dispatcher?: Dispatcher });

    const finalUrl = new URL(upstream.url);
    const contentType = upstream.headers.get("content-type") || "application/octet-stream";

    // Forward safe headers. For Set-Cookie, strip the upstream Domain= and SameSite=None;Secure
    // so the user's browser actually accepts cookies under our proxy origin.
    const setCookies: string[] = [];
    upstream.headers.forEach((value, key) => {
      const lk = key.toLowerCase();
      if (HOP_BY_HOP.has(lk)) return;
      if (lk === "set-cookie") {
        // undici's Headers folds multiple Set-Cookie via getSetCookie; fall back to value split.
        const parts = typeof (upstream.headers as unknown as { getSetCookie?: () => string[] }).getSetCookie === "function"
          ? (upstream.headers as unknown as { getSetCookie: () => string[] }).getSetCookie()
          : value.split(/,(?=[^;]+=[^;]+)/);
        for (const c of parts) {
          const cleaned = c
            .replace(/;\s*Domain=[^;]+/gi, "")
            .replace(/;\s*Secure/gi, "")
            .replace(/;\s*SameSite=[^;]+/gi, "; SameSite=Lax");
          setCookies.push(cleaned);
        }
        return;
      }
      res.setHeader(key, value);
    });
    if (setCookies.length > 0) {
      res.setHeader("Set-Cookie", setCookies);
      // Also persist into sticky jar so future sub-requests reuse them.
      void storeStickyCookies(target.toString(), setCookies);
    }
    res.removeHeader("X-Frame-Options");
    res.removeHeader("Content-Security-Policy");

    res.status(upstream.status);

    if (/text\/html/i.test(contentType)) {
      let text = await upstream.text();
      let effectiveFinal = finalUrl;
      let effectiveStatus = upstream.status;
      // If CF (or similar) served a challenge page, re-fetch via Playwright
      // (sticky context already holds cf_clearance from prior renders).
      if (isGet && looksLikeCfChallengeHtml(text, finalUrl.toString())) {
        try {
          const r = await renderWithBrowser(target.toString());
          text = r.html;
          effectiveFinal = new URL(r.finalUrl);
          effectiveStatus = r.status;
          res.status(effectiveStatus);
          console.log("[proxy] CF challenge bypassed via browser:", target.toString());
        } catch (e) {
          console.error("[proxy] CF retry failed:", (e as Error).message);
        }
      }
      const rewritten = rewriteHtml(text, effectiveFinal, proxyBase);
      res.setHeader("content-type", "text/html; charset=utf-8");
      res.send(rewritten);
      return;
    }

    if (/text\/css/i.test(contentType)) {
      const text = await upstream.text();
      const rewritten = rewriteCss(text, finalUrl, proxyBase);
      res.setHeader("content-type", "text/css; charset=utf-8");
      res.send(rewritten);
      return;
    }

    const buf = Buffer.from(await upstream.arrayBuffer());
    res.setHeader("content-type", contentType);
    res.send(buf);
  } catch (err) {
    req.log.error({ err, url: target.toString() }, "Proxy fetch failed");
    res.status(502).type("text/html").send(
      `<!doctype html><meta charset="utf-8"><body style="font-family:system-ui;padding:40px;color:#444"><h2>Proxy fetch failed</h2><p>Could not reach <code>${target.toString()}</code>.</p></body>`,
    );
  }
});

export default router;
