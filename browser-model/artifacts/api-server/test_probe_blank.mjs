
import { chromium } from "playwright";

const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";

const PROBE = `
(() => {
  var res = {};
  // 1. Screen.prototype
  try {
    Object.defineProperty(Screen.prototype, "availHeight", {
      get: function() { return 999; }, configurable: true, enumerable: true
    });
    res.screen_proto = screen.availHeight;
  } catch(e) { res.screen_err = e.message; }
  // 2. Notification.permission
  try {
    var RN = window.Notification;
    if (RN) {
      Object.defineProperty(RN, "permission", {
        get: function() { return "default"; }, configurable: true, enumerable: true
      });
      res.notif = window.Notification.permission;
    } else { res.notif = "NO_NOTIF"; }
  } catch(e) { res.notif_err = e.message; }
  // 3. navigator.share
  try {
    var stub = async function share() { return Promise.reject(new DOMException("AbortError")); };
    Object.defineProperty(Navigator.prototype, "share", {
      value: stub, configurable: true, writable: true, enumerable: false
    });
    res.share = "share" in navigator;
  } catch(e) { res.share_err = e.message; }
  // 4. getComputedStyle
  try {
    var _origGCS = window.getComputedStyle;
    window.getComputedStyle = function(el, ps) { return _origGCS.call(window, el, ps); };
    res.gcs_reassigned = (window.getComputedStyle !== _origGCS);
    var testEl = document.createElement("div");
    document.body && document.body.appendChild(testEl);
    testEl.setAttribute("style", "background-color: ActiveText");
    res.at_orig = _origGCS.call(window, testEl).backgroundColor;
    res.at_patched = window.getComputedStyle(testEl).backgroundColor;
    document.body && document.body.removeChild(testEl);
  } catch(e) { res.gcs_err = e.message; }
  window.__probeBlank = res;
})();
`;

const br = await chromium.launch({
  headless: false, executablePath: BINARY,
  args: ["--no-sandbox","--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--no-first-run","--mute-audio","--use-gl=angle","--use-angle=swiftshader",
    "--enable-webgl","--window-size=1920,1080",
    "--fingerprint=11223344","--fingerprint-platform=linux",
    "--fingerprint-brand=Chrome","--fingerprint-brand-version=144"],
  ignoreDefaultArgs:["--enable-automation"],
  env:{...process.env, DISPLAY:":99"},
});
const ctx = await br.newContext({
  userAgent:"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
  viewport:{width:1920,height:1040}, screen:{width:1920,height:1080},
  locale:"en-US", ignoreHTTPSErrors:true,
});
await ctx.addInitScript(PROBE);
const pg = await ctx.newPage();
await pg.goto("about:blank");
await pg.waitForTimeout(300);
const r = await pg.evaluate(() => window.__probeBlank || {error:"no_probe"});
console.log(JSON.stringify(r, null, 2));
await br.close();
