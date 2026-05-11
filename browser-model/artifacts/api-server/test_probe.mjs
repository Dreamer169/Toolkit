
import { chromium } from "playwright";

const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const PROXY  = "";

const PROBE = `
(() => {
  var res = {};

  // Test 1: Can we assign to Object.defineProperty?
  try {
    var orig = Object.defineProperty;
    Object.defineProperty = function(o,p,d){ return orig(o,p,d); };
    res.odp_writable = (Object.defineProperty !== orig);
    Object.defineProperty = orig;
  } catch(e) { res.odp_writable = false; res.odp_err = e.message; }

  // Test 2: Screen.prototype ODP
  try {
    Object.defineProperty(Screen.prototype, 'availHeight', {
      get: function() { return 999; }, configurable: true, enumerable: true
    });
    res.screen_proto = screen.availHeight;
    res.screen_proto_ok = (screen.availHeight === 999);
  } catch(e) { res.screen_proto_err = e.message; }

  // Test 3: window.screen ODP
  try {
    var realScreen = window.screen;
    Object.defineProperty(window, 'screen', {
      get: function(){ return realScreen; }, configurable: true
    });
    res.screen_win_ok = true;
  } catch(e) { res.screen_win_err = e.message; }

  // Test 4: Notification.permission direct ODP on constructor
  try {
    var RN = window.Notification;
    if (RN) {
      Object.defineProperty(RN, 'permission', {
        get: function() { return 'denied'; }, configurable: true, enumerable: true
      });
      res.notif_direct = window.Notification.permission;
      res.notif_direct_ok = (window.Notification.permission === 'denied');
    } else { res.notif_direct_ok = 'NO_NOTIF'; }
  } catch(e) { res.notif_direct_err = e.message; }

  // Test 5: navigator.share via Navigator.prototype
  try {
    Object.defineProperty(Navigator.prototype, 'share', {
      value: async function() { return Promise.reject(new DOMException('AbortError')); },
      configurable: true, writable: true, enumerable: false,
    });
    res.share_proto_ok = ('share' in navigator);
  } catch(e) { res.share_proto_err = e.message; }

  // Test 6: canShare via Navigator.prototype
  try {
    Object.defineProperty(Navigator.prototype, 'canShare', {
      value: function() { return false; },
      configurable: true, writable: true, enumerable: false,
    });
    res.canShare_proto_ok = ('canShare' in navigator);
  } catch(e) { res.canShare_proto_err = e.message; }

  window.__probeRes = res;
})();
`;

const br = await chromium.launch({
  headless: false, executablePath: BINARY,
  args: [
    "--no-sandbox","--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--no-first-run","--mute-audio","--use-gl=angle","--use-angle=swiftshader",
    "--enable-webgl","--window-size=1920,1080",
    "--fingerprint=11223344","--fingerprint-platform=linux",
    "--fingerprint-brand=Chrome","--fingerprint-brand-version=144",
    ``,"--disable-quic",
  ],
  ignoreDefaultArgs: ["--enable-automation"],
  env: { ...process.env, DISPLAY: ":99" },
});
const ctx = await br.newContext({
  userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
  viewport: { width: 1920, height: 1040 },
  screen: { width: 1920, height: 1080 },
  locale: "en-US", timezoneId: "America/Los_Angeles",
  colorScheme: "light", ignoreHTTPSErrors: true,
});
await ctx.addInitScript(PROBE);
const pg = await ctx.newPage();
await pg.goto("about:blank
await pg.waitForTimeout(500);
const r = await pg.evaluate(() => window.__probeRes || { error: "no_probe" });
console.log("=== PROBE RESULTS ===");
console.log(JSON.stringify(r, null, 2));
await br.close();
