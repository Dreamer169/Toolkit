import { chromium, Browser, BrowserContext } from "playwright";

let browserPromise: Promise<Browser> | null = null;

const STEALTH_INIT = `
// === Anti-fingerprint init script (runs before any page JS) ===
(() => {
  // navigator.webdriver
  try { Object.defineProperty(Navigator.prototype, 'webdriver', { get: () => undefined, configurable: true }); } catch (_) {}
  // delete CDP-injected globals
  try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array; } catch(_) {}
  try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise; } catch(_) {}
  try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol; } catch(_) {}

  // languages
  try { Object.defineProperty(Navigator.prototype, 'languages', { get: () => ['en-US', 'en'], configurable: true }); } catch (_) {}

  // platform / hardwareConcurrency / deviceMemory
  try { Object.defineProperty(Navigator.prototype, 'platform', { get: () => 'Linux x86_64', configurable: true }); } catch (_) {}
  try { Object.defineProperty(Navigator.prototype, 'hardwareConcurrency', { get: () => 8, configurable: true }); } catch (_) {}
  try { Object.defineProperty(Navigator.prototype, 'deviceMemory', { get: () => 8, configurable: true }); } catch (_) {}
  try { Object.defineProperty(Navigator.prototype, 'maxTouchPoints', { get: () => 0, configurable: true }); } catch (_) {}

  // plugins / mimeTypes — PluginArray with realistic entries
  try {
    const makePlugin = (name, filename, desc) => {
      const p = Object.create(Plugin.prototype);
      Object.defineProperties(p, {
        name: { value: name }, filename: { value: filename },
        description: { value: desc }, length: { value: 1 },
      });
      return p;
    };
    const plugins = [
      makePlugin('PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      makePlugin('Chrome PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      makePlugin('Chromium PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      makePlugin('Microsoft Edge PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      makePlugin('WebKit built-in PDF', 'internal-pdf-viewer', 'Portable Document Format'),
    ];
    const arr = Object.create(PluginArray.prototype);
    plugins.forEach((p, i) => { arr[i] = p; arr[p.name] = p; });
    Object.defineProperty(arr, 'length', { value: plugins.length });
    Object.defineProperty(Navigator.prototype, 'plugins', { get: () => arr, configurable: true });
  } catch (_) {}

  // chrome.runtime stub
  try {
    if (!window.chrome) window.chrome = {};
    window.chrome.runtime = window.chrome.runtime || { OnInstalledReason: {}, OnRestartRequiredReason: {}, PlatformArch: {}, PlatformOs: {}, RequestUpdateCheckStatus: {} };
    window.chrome.app = window.chrome.app || { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }, RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } };
    window.chrome.csi = window.chrome.csi || function(){return{};};
    window.chrome.loadTimes = window.chrome.loadTimes || function(){return{requestTime: Date.now()/1000, startLoadTime: Date.now()/1000, commitLoadTime: Date.now()/1000, finishDocumentLoadTime: 0, finishLoadTime: 0, firstPaintTime: 0, firstPaintAfterLoadTime: 0, navigationType: 'Other', wasFetchedViaSpdy: false, wasNpnNegotiated: true, npnNegotiatedProtocol: 'h2', wasAlternateProtocolAvailable: false, connectionInfo: 'h2'};};
  } catch (_) {}

  // permissions: notifications quirk
  try {
    const origQuery = window.navigator.permissions && window.navigator.permissions.query;
    if (origQuery) {
      window.navigator.permissions.query = (params) =>
        params && params.name === 'notifications'
          ? Promise.resolve({ state: Notification.permission, name: 'notifications', onchange: null })
          : origQuery.call(window.navigator.permissions, params);
    }
  } catch (_) {}

  // WebGL vendor / renderer (Mesa Intel — typical Linux Chrome)
  try {
    const getParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function (p) {
      if (p === 37445) return 'Intel Inc.';            // UNMASKED_VENDOR_WEBGL
      if (p === 37446) return 'Intel Iris OpenGL Engine'; // UNMASKED_RENDERER_WEBGL
      return getParam.apply(this, arguments);
    };
    if (typeof WebGL2RenderingContext !== 'undefined') {
      const getParam2 = WebGL2RenderingContext.prototype.getParameter;
      WebGL2RenderingContext.prototype.getParameter = function (p) {
        if (p === 37445) return 'Intel Inc.';
        if (p === 37446) return 'Intel Iris OpenGL Engine';
        return getParam2.apply(this, arguments);
      };
    }
  } catch (_) {}

  // window.outerWidth/Height = innerWidth/Height when 0 (headless leak)
  try {
    if (!window.outerWidth) Object.defineProperty(window, 'outerWidth', { get: () => window.innerWidth });
    if (!window.outerHeight) Object.defineProperty(window, 'outerHeight', { get: () => window.innerHeight });
  } catch (_) {}

  // Realistic screen properties
  try {
    Object.defineProperty(screen, 'availWidth',  { get: () => 1920, configurable: true });
    Object.defineProperty(screen, 'availHeight', { get: () => 1040, configurable: true });
    Object.defineProperty(screen, 'width',  { get: () => 1920, configurable: true });
    Object.defineProperty(screen, 'height', { get: () => 1080, configurable: true });
    Object.defineProperty(screen, 'colorDepth', { get: () => 24, configurable: true });
    Object.defineProperty(screen, 'pixelDepth', { get: () => 24, configurable: true });
  } catch (_) {}

  // Battery (some sites probe it)
  try {
    if (navigator.getBattery) {
      const _gb = navigator.getBattery.bind(navigator);
      navigator.getBattery = () => _gb().then((b) => b).catch(() => ({
        charging: true, chargingTime: 0, dischargingTime: Infinity, level: 0.99,
        addEventListener(){}, removeEventListener(){}, dispatchEvent(){return true;},
      }));
    }
  } catch (_) {}

  // Connection
  try {
    Object.defineProperty(Navigator.prototype, 'connection', {
      get: () => ({ effectiveType: '4g', rtt: 50, downlink: 10, saveData: false, addEventListener(){}, removeEventListener(){} }),
      configurable: true,
    });
  } catch (_) {}

  // Notification permission default
  try { if (window.Notification && Notification.permission === 'denied') Object.defineProperty(Notification, 'permission', { get: () => 'default' }); } catch (_) {}

  // toString-leak: hide our patches by overriding fn.toString to native pattern
  const nativeToString = Function.prototype.toString;
  const fakeFns = new WeakSet();
  const wrap = (fn) => { fakeFns.add(fn); return fn; };
  Function.prototype.toString = function () {
    if (fakeFns.has(this)) return 'function ' + (this.name || '') + '() { [native code] }';
    return nativeToString.call(this);
  };
  // mark our overrides
  try { wrap(WebGLRenderingContext.prototype.getParameter); } catch (_) {}
  try { wrap(window.navigator.permissions.query); } catch (_) {}
  try { wrap(Function.prototype.toString); } catch (_) {}

  // === WebRTC IP leak protection ===
  // Sites probe local/public IP via RTCPeerConnection ICE candidates. Strip
  // host/srflx candidates that would expose the real network identity.
  try {
    const PC = window.RTCPeerConnection || window.webkitRTCPeerConnection || window.mozRTCPeerConnection;
    if (PC) {
      const origCreateOffer = PC.prototype.createOffer;
      const origAddIceCandidate = PC.prototype.addIceCandidate;
      const origSetLocalDescription = PC.prototype.setLocalDescription;
      // Filter out candidate lines exposing private IPs in SDP
      function sanitizeSdp(sdp) {
        if (!sdp || typeof sdp !== 'string') return sdp;
        return sdp.split('\r\n').filter((l) => !/^a=candidate:/i.test(l)).join('\r\n');
      }
      PC.prototype.setLocalDescription = function setLocalDescription(desc) {
        if (desc && desc.sdp) desc.sdp = sanitizeSdp(desc.sdp);
        return origSetLocalDescription.apply(this, arguments);
      };
      PC.prototype.addIceCandidate = function addIceCandidate(cand) {
        try {
          const c = (cand && (cand.candidate || (typeof cand === "string" ? cand : ""))) || "";
          if (/^candidate:/i.test(c)) return Promise.resolve();
        } catch (_) {}
        return origAddIceCandidate.apply(this, arguments);
      };
      try { wrap(PC.prototype.setLocalDescription); wrap(PC.prototype.addIceCandidate); } catch (_) {}
    }
  } catch (_) {}

  // === Mock mediaDevices.enumerateDevices / getUserMedia ===
  try {
    if (navigator.mediaDevices) {
      const fakeDevices = [
        { deviceId: "default", kind: "audioinput",  label: "", groupId: "g1", toJSON(){return this;} },
        { deviceId: "8a1bcf",  kind: "audioinput",  label: "", groupId: "g1", toJSON(){return this;} },
        { deviceId: "default", kind: "audiooutput", label: "", groupId: "g1", toJSON(){return this;} },
        { deviceId: "cam1xy",  kind: "videoinput",  label: "", groupId: "g2", toJSON(){return this;} },
      ];
      const fakeEnum = function enumerateDevices() { return Promise.resolve(fakeDevices); };
      const fakeGUM  = function getUserMedia() { return Promise.reject(new DOMException("Permission denied", "NotAllowedError")); };
      const fakeGDM  = function getDisplayMedia() { return Promise.reject(new DOMException("Permission denied", "NotAllowedError")); };
      try { wrap(fakeEnum); wrap(fakeGUM); wrap(fakeGDM); } catch (_) {}
      // Try prototype first
      try {
        const proto = Object.getPrototypeOf(navigator.mediaDevices);
        Object.defineProperty(proto, "enumerateDevices", { value: fakeEnum, configurable: true, writable: true });
        Object.defineProperty(proto, "getUserMedia",     { value: fakeGUM,  configurable: true, writable: true });
        Object.defineProperty(proto, "getDisplayMedia",  { value: fakeGDM,  configurable: true, writable: true });
        } catch (_) {}
      // Always also define on instance (proto may be locked)
      try {
        Object.defineProperty(navigator.mediaDevices, "enumerateDevices", { value: fakeEnum, configurable: true, writable: true });
        Object.defineProperty(navigator.mediaDevices, "getUserMedia",     { value: fakeGUM,  configurable: true, writable: true });
        Object.defineProperty(navigator.mediaDevices, "getDisplayMedia",  { value: fakeGDM,  configurable: true, writable: true });
        } catch (_) {}
    }
  } catch (_) {}

  // === navigator.language matches languages[0] ===
  try { Object.defineProperty(Navigator.prototype, 'language', { get: () => 'en-US', configurable: true }); } catch (_) {}

  // === Intl/timezone consistency check ===
  // Context already pins timezoneId, but some libs read DateTimeFormat directly.
  // Ensure reported timezone matches context (America/Los_Angeles).
  try {
    const origRO = Intl.DateTimeFormat.prototype.resolvedOptions;
    Intl.DateTimeFormat.prototype.resolvedOptions = function resolvedOptions() {
      const r = origRO.apply(this, arguments);
      if (!r.timeZone || r.timeZone === "UTC") r.timeZone = "America/Los_Angeles";
      if (!r.locale || r.locale === "en-GB") r.locale = "en-US";
      return r;
    };
    try { wrap(Intl.DateTimeFormat.prototype.resolvedOptions); } catch (_) {}
  } catch (_) {}

  // === Date.prototype.getTimezoneOffset → PST/PDT ===
  // (Playwright's timezoneId already handles this for new pages; reinforced
  // here so SPA code that runs before our init sees correct offset on Node-driven
  // bundles.) America/Los_Angeles offset is +480 (PST) or +420 (PDT).
  try {
    const origGTO = Date.prototype.getTimezoneOffset;
    Date.prototype.getTimezoneOffset = function getTimezoneOffset() {
      const v = origGTO.call(this);
      if (v === 0) {
        const month = this.getUTCMonth();
        return (month >= 2 && month <= 10) ? 420 : 480;
      }
      return v;
    };
    try { wrap(Date.prototype.getTimezoneOffset); } catch (_) {}
  } catch (_) {}
})();
`;

async function getBrowser(): Promise<Browser> {
  // If browser exists but is no longer connected (process died), reset.
  if (browserPromise) {
    try {
      const b = await browserPromise;
      if (!b.isConnected()) {
        browserPromise = null;
        for (const k of stickyContexts.keys()) stickyContexts.delete(k);
        stickyExpiry.clear();
      }
    } catch {
      browserPromise = null;
    }
  }
  if (!browserPromise) {
    const executablePath = process.env.REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined;
    const proxyServer = process.env.BROWSER_PROXY || undefined;
    browserPromise = chromium
      .launch({
        // Headed mode over Xvfb :99 (1920x1080x24). DISPLAY env is set by
        // start-browser-model.sh. This gives a real GPU stack, real fonts,
        // real window manager surface — anti-bot / WebGL / fingerprint gets
        // a vastly more realistic profile than headless.
        headless: false,
        executablePath,
        proxy: proxyServer ? { server: proxyServer } : undefined,
        args: [
          "--no-sandbox",
          "--disable-blink-features=AutomationControlled",
          "--disable-features=IsolateOrigins,site-per-process,AutomationControlled,Translate",
          "--disable-dev-shm-usage",
          "--disable-extensions-except",
          "--disable-component-extensions-with-background-pages",
          "--no-default-browser-check",
          "--no-first-run",
          "--password-store=basic",
          "--use-mock-keychain",
          // Window/screen
          "--window-size=1920,1080",
          "--window-position=0,0",
          "--start-maximized",
          // GPU on Xvfb — software GL via SwiftShader/ANGLE works headed too
          "--use-gl=angle",
          "--use-angle=swiftshader",
          "--enable-webgl",
          // DNS via DoH directly inside chromium (bypasses GFW UDP poisoning)
          "--proxy-resolves-dns-locally",
          "--enable-features=AsyncDns,DnsOverHttpsUpgrade,NetworkServiceInProcess",
          "--dns-over-https-templates=https://1.1.1.1/dns-query,https://dns.google/dns-query",
        ],
        ignoreDefaultArgs: ["--enable-automation", "--disable-component-extensions-with-background-pages"],
      })
      .catch((err) => {
        browserPromise = null;
        console.error("[renderer] chromium.launch failed:", err);
        throw err;
      });
  }
  return browserPromise;
}

async function newFreshContext(): Promise<BrowserContext> {
  // Per-request incognito context. Sharing one context across requests pools
  // cookies (e.g. Google's NID / GOOGLE_ABUSE_EXEMPTION) — once any request
  // gets captcha'd, every subsequent request inherits the poisoned state.
  const browser = await getBrowser();
  const ctx = await browser.newContext({
    userAgent:
      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    viewport: { width: 1920, height: 1040 },
    screen: { width: 1920, height: 1080 },
    deviceScaleFactor: 1,
    isMobile: false,
    hasTouch: false,
    locale: "en-US",
    timezoneId: "America/Los_Angeles",
    colorScheme: "light",
    ignoreHTTPSErrors: true,
    extraHTTPHeaders: {
      "Accept-Language": "en-US,en;q=0.9",
      "sec-ch-ua": "\"Chromium\";v=\"145\", \"Not:A-Brand\";v=\"99\", \"Google Chrome\";v=\"145\"",
      "sec-ch-ua-mobile": "?0",
      "sec-ch-ua-platform": "\"Linux\"",
    },
  });
  ctx.on("close", () => { closedContexts.add(ctx); });
  await ctx.addInitScript(STEALTH_INIT);
  return ctx;
}

// eTLD+1 (cheap heuristic: last two labels). Good enough for sticky keying.
function siteKey(hostname: string): string {
  const parts = hostname.toLowerCase().split(".");
  if (parts.length <= 2) return hostname.toLowerCase();
  // Handle a few common 2-label TLDs
  const last2 = parts.slice(-2).join(".");
  const last3 = parts.slice(-3).join(".");
  if (/^(co|com|net|org|gov|edu|ac)\.[a-z]{2}$/.test(last2)) return last3;
  return last2;
}

// Sites whose cookies must NOT be reused across requests (they ban faster when
// the same identity reappears: search engines, anti-bot endpoints).
function shouldUseFreshContext(hostname: string): boolean {
  return needsJsRendering(hostname);
}

const stickyContexts = new Map<string, Promise<BrowserContext>>();
const STICKY_TTL_MS = 30 * 60 * 1000;
const stickyExpiry = new Map<string, number>();
// Track contexts whose 'close' event has fired so we never hand out a dead one.
const closedContexts = new WeakSet<BrowserContext>();

async function getStickyContext(hostname: string): Promise<BrowserContext> {
  const key = siteKey(hostname);
  const now = Date.now();
  const exp = stickyExpiry.get(key) ?? 0;
  let cached = stickyContexts.get(key);
  if (cached && now < exp) {
    try {
      const c = await cached;
      const alive = !closedContexts.has(c) && (c.browser()?.isConnected() ?? false);
      if (alive) return cached;
    } catch { /* fall through and rebuild */ }
    stickyContexts.delete(key);
    stickyExpiry.delete(key);
  }

  // Expire old one
  if (cached) {
    cached.then((c) => c.close().catch(() => {})).catch(() => {});
    stickyContexts.delete(key);
  }
  const browser = await getBrowser();
  const p = browser.newContext({
    userAgent:
      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    viewport: { width: 1920, height: 1040 },
    screen: { width: 1920, height: 1080 },
    deviceScaleFactor: 1,
    isMobile: false,
    hasTouch: false,
    locale: "en-US",
    timezoneId: "America/Los_Angeles",
    colorScheme: "light",
    ignoreHTTPSErrors: true,
    extraHTTPHeaders: {
      "Accept-Language": "en-US,en;q=0.9",
      "sec-ch-ua": "\"Chromium\";v=\"145\", \"Not:A-Brand\";v=\"99\", \"Google Chrome\";v=\"145\"",
      "sec-ch-ua-mobile": "?0",
      "sec-ch-ua-platform": "\"Linux\"",
    },
  }).then(async (c) => {
    c.on("close", () => { closedContexts.add(c); });
    await c.addInitScript(STEALTH_INIT);
    return c;
  });
  stickyContexts.set(key, p);
  stickyExpiry.set(key, now + STICKY_TTL_MS);
  return p;
}

function dropStickyContext(hostname: string): void {
  const key = siteKey(hostname);
  const cached = stickyContexts.get(key);
  if (cached) cached.then((c) => c.close().catch(() => {})).catch(() => {});
  stickyContexts.delete(key);
  stickyExpiry.delete(key);
}

async function getPageContent(page: import("playwright").Page): Promise<string> {
  // page.content() throws if the page is mid-navigation. Retry, then fall
  // back to evaluating outerHTML directly which tolerates navigation races.
  for (let i = 0; i < 4; i++) {
    try {
      return await page.content();
    } catch {
      await page.waitForTimeout(500);
    }
  }
  try {
    return await page.evaluate(
      () => "<!DOCTYPE html>" + document.documentElement.outerHTML
    );
  } catch {
    return "<!DOCTYPE html><html><body>(content unavailable: page navigating)</body></html>";
  }
}

function looksLikeCaptcha(html: string, finalUrl: string): boolean {
  if (/\/sorry\/index|\/recaptcha\/api2\/bframe|\/cdn-cgi\/challenge-platform|challenges\.cloudflare\.com\/turnstile/i.test(finalUrl)) return true;
  return /Our systems have detected unusual traffic|sending requests, and not a robot|<title>Attention Required|<title>Just a moment|Performing security verification|Unable to connect to the website|cf-browser-verification|cf_chl_opt|__cf_chl_|cdn-cgi\/challenge-platform|id="challenge-form"|name="cf-turnstile-response"/i.test(
    html
  );
}

export async function renderWithBrowser(
  url: string,
  timeoutMs = 30000,
  attempt = 0
): Promise<{ html: string; finalUrl: string; status: number }> {
  const targetHost = (() => { try { return new URL(url).hostname; } catch { return ""; } })();
  const useFresh = attempt > 0 || shouldUseFreshContext(targetHost);
  const ctx = useFresh ? await newFreshContext() : await getStickyContext(targetHost);
  const page = await ctx.newPage();
  let lastResponseStatus = 200;
  try {
    // Capture status of every main-frame document response (CF challenge first
    // returns 403, the JS challenge then navigates to the real page with 200 —
    // we want the latter, not the former).
    page.on("response", (r) => {
      try {
        if (r.frame() !== page.mainFrame()) return;
        const rt = (r.request().resourceType?.() || "").toString();
        if (rt && rt !== "document") return;
        lastResponseStatus = r.status();
      } catch {
        /* ignore */
      }
    });

    try {
      await page.goto(url, { waitUntil: "load", timeout: timeoutMs });
    } catch (err) {
      // Some SPAs (Outlook, Teams) trigger an immediate location.replace
      // inside load handlers. The aborted goto still leaves us on a real
      // page that we can extract — only re-throw if we have no document.
      const hasDoc = await page
        .evaluate(() => !!document && !!document.documentElement)
        .catch(() => false);
      if (!hasDoc) throw err;
    }

    // Let any in-flight client navigation / hydration settle.
    // Outlook/Office immediately location.replace inside the load handler;
    // wait for DOMContentLoaded first (always reachable) then attempt
    // networkidle as a soft wait.
    await page
      .waitForLoadState("domcontentloaded", { timeout: 8000 })
      .catch(() => {});
    await page
      .waitForLoadState("networkidle", { timeout: 6000 })
      .catch(() => {});
    // If we landed on a CF interstitial, give its JS challenge time to auto-resolve
    // and navigate to the real page.
    try {
      const probe = await page.content().catch(() => "");
      if (/<title>Just a moment|cf-browser-verification|id="challenge-form"|cdn-cgi\/challenge-platform/i.test(probe)) {
        await page
          .waitForFunction(
            () => !/Just a moment|challenge-form|cf-browser-verification/i.test(document.documentElement.outerHTML),
            { timeout: 12000 }
          )
          .catch(() => {});
        await page.waitForLoadState("networkidle", { timeout: 4000 }).catch(() => {});
      }
    } catch {
      /* ignore */
    }

    const html = await getPageContent(page);
    const finalUrl = page.url();
    if (attempt < 2 && looksLikeCaptcha(html, finalUrl)) {
      console.log(
        `[renderer] captcha detected on ${finalUrl} (attempt ${attempt + 1}), retrying with new context/connection`
      );
      if (!useFresh) dropStickyContext(targetHost);
      await page.close().catch(() => {});
      if (useFresh) await ctx.close().catch(() => {});
      // Brief delay so the SOCKS5 wrapper picks up a new upstream connection
      await new Promise((r) => setTimeout(r, 300));
      return renderWithBrowser(url, timeoutMs, attempt + 1);
    }
    return { html, finalUrl, status: lastResponseStatus };
  } finally {
    await page.close().catch(() => {});
    if (useFresh) await ctx.close().catch(() => {});
  }
}

// Hosts that require JS execution / anti-bot evasion
const JS_REQUIRED = [
  /(^|\.)google\.[a-z.]+$/i,
  /(^|\.)bing\.com$/i,
  /(^|\.)search\.brave\.com$/i,
  /(^|\.)yandex\.[a-z.]+$/i,
  /(^|\.)baidu\.com$/i,
];

export function needsJsRendering(hostname: string): boolean {
  return JS_REQUIRED.some((re) => re.test(hostname));
}

export async function shutdownBrowser(): Promise<void> {
  for (const p of stickyContexts.values()) {
    try { (await p).close().catch(() => {}); } catch {}
  }
  stickyContexts.clear();
  stickyExpiry.clear();
  if (browserPromise) {
    const b = await browserPromise;
    await b.close().catch(() => {});
    browserPromise = null;
  }
}


export { siteKey };

export async function getStickyCookieHeader(url: string): Promise<string> {
  try {
    const u = new URL(url);
    const key = siteKey(u.hostname);
    const cached = stickyContexts.get(key);
    if (!cached) return "";
    const ctx = await cached;
    const cookies = await ctx.cookies(url);
    return cookies.map((c) => `${c.name}=${c.value}`).join("; ");
  } catch {
    return "";
  }
}

async function ensureStickyContext(hostname: string): Promise<BrowserContext> {
  return getStickyContext(hostname);
}

export async function storeStickyCookies(url: string, setCookieHeaders: string[]): Promise<void> {
  if (!setCookieHeaders.length) return;
  try {
    const u = new URL(url);
    if (shouldUseFreshContext(u.hostname)) return; // don't pollute search-engine sites
    const ctx = await ensureStickyContext(u.hostname);
    const cookies: Array<{
      name: string; value: string; domain: string; path: string;
      expires?: number; httpOnly?: boolean; secure?: boolean;
      sameSite?: "Lax" | "Strict" | "None";
    }> = [];
    for (const sc of setCookieHeaders) {
      const parts = sc.split(";").map((p) => p.trim());
      if (!parts.length) continue;
      const nv = parts.shift()!;
      const eq = nv.indexOf("=");
      if (eq < 0) continue;
      const name = nv.slice(0, eq).trim();
      const value = nv.slice(eq + 1).trim();
      let domain = u.hostname;
      let path = "/";
      let expires: number | undefined;
      let httpOnly = false;
      let secure = true; // upstream is https — keep secure for CF cookies to work
      let sameSite: "Lax" | "Strict" | "None" = "Lax";
      for (const a of parts) {
        const ai = a.indexOf("=");
        const ak = (ai < 0 ? a : a.slice(0, ai)).toLowerCase();
        const av = ai < 0 ? "" : a.slice(ai + 1);
        if (ak === "domain") domain = av.replace(/^\./, "");
        else if (ak === "path") path = av || "/";
        else if (ak === "expires") {
          const t = Date.parse(av);
          if (!isNaN(t)) expires = Math.floor(t / 1000);
        } else if (ak === "max-age") {
          const n = parseInt(av, 10);
          if (!isNaN(n)) expires = Math.floor(Date.now() / 1000) + n;
        } else if (ak === "httponly") httpOnly = true;
        else if (ak === "secure") secure = true;
        else if (ak === "samesite") {
          const v = av.toLowerCase();
          sameSite = v === "strict" ? "Strict" : v === "none" ? "None" : "Lax";
        }
      }
      cookies.push({ name, value, domain, path, expires, httpOnly, secure, sameSite });
    }
    if (cookies.length) await ctx.addCookies(cookies);
  } catch (e) {
    const m=(e as Error).message;
    if (!/has been closed|Target (page|context)/.test(m)) console.error("[renderer] storeStickyCookies:", m);
  }
}

export function looksLikeCfChallengeHtml(html: string, finalUrl: string): boolean {
  return looksLikeCaptcha(html, finalUrl);
}
