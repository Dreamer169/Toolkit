
import { chromium } from "playwright";
import { readFileSync } from "fs";
const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const tsSrc  = readFileSync("/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts","utf8");
const STEALTH_INIT = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/) ||[])[1]||"";

const LATE_PATCHES = `
(function() {
  try {
    Object.defineProperty(Screen.prototype, "availHeight", {
      get: function() { return window.innerHeight || 1040; },
      configurable: true, enumerable: true,
    });
    Object.defineProperty(Screen.prototype, "availWidth", {
      get: function() { return window.innerWidth || 1920; },
      configurable: true, enumerable: true,
    });
  } catch(_) {}
  try {
    var _RN = window.Notification;
    if (_RN) {
      Object.defineProperty(_RN, "permission", {
        get: function() { return "default"; },
        configurable: true, enumerable: true,
      });
    }
  } catch(_) {}
  try {
    var _shareStub = async function share() { return Promise.reject(new DOMException("AbortError")); };
    Object.defineProperty(Navigator.prototype, "share", {
      value: _shareStub, configurable: true, writable: true, enumerable: false,
    });
  } catch(_) {}
  try {
    var _gCS = window.getComputedStyle;
    var _p = function getComputedStyle(el, ps) {
      var r = _gCS.call(window, el, ps);
      try {
        var at = /ActiveText/i.test(el.getAttribute ? (el.getAttribute("style") || "") : "");
        if (at) return new Proxy(r, { get: function(t, p) {
          if (p === "backgroundColor" || p === "color") return "rgb(0, 120, 212)";
          var v = t[p]; return typeof v === "function" ? v.bind(t) : v;
        }});
      } catch(_) {}
      return r;
    };
    try { Object.defineProperty(window, "getComputedStyle", { value: _p, writable: true, configurable: true }); }
    catch(_) { window.getComputedStyle = _p; }
  } catch(_) {}
})();
`;

const browser = await chromium.launch({
  headless: false, executablePath: BINARY,
  args: ["--no-sandbox","--disable-dev-shm-usage","--disable-blink-features=AutomationControlled",
    "--no-first-run","--mute-audio","--use-gl=angle","--use-angle=swiftshader",
    "--enable-webgl","--window-size=1920,1080","--fingerprint=11223344",
    "--fingerprint-platform=linux","--fingerprint-brand=Chrome","--fingerprint-brand-version=144"],
  ignoreDefaultArgs: ["--enable-automation"],
  env: { ...process.env, DISPLAY: ":99" },
});
const ctx = await browser.newContext({
  userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
  viewport:{width:1920,height:1040}, screen:{width:1920,height:1080},
  locale:"en-US", colorScheme:"dark", ignoreHTTPSErrors:true,
});
await ctx.addInitScript(STEALTH_INIT);
await ctx.addInitScript(LATE_PATCHES);
const page = await ctx.newPage();
await page.goto("about:blank");
const r = await page.evaluate(async () => {
  const screenAH = screen.availHeight;
  const screenH  = screen.height;
  let notifPerm = "NA"; try { notifPerm = Notification.permission; } catch(_) {}
  const hasShare = "share" in navigator;
  let bgColor = "err";
  try {
    const el = document.createElement("div"); document.body.appendChild(el);
    el.setAttribute("style","background-color: ActiveText");
    bgColor = getComputedStyle(el).backgroundColor;
    document.body.removeChild(el);
  } catch(e) { bgColor = "ex:" + e.message; }
  return { screenH, screenAH, noTaskbar: screenH===screenAH, notifPerm, hasShare, bgColor };
});
console.log(JSON.stringify(r, null, 2));
await browser.close();
