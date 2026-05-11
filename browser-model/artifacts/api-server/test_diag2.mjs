
import { chromium } from "playwright";
import { readFileSync } from "fs";
const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const tsSrc  = readFileSync("/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts","utf8");
const STEALTH_INIT = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/) ||[])[1]||"";
const MARKER = `(function(){ window.__marker_early = ran; })();`;
const browser = await chromium.launch({
  headless: false, executablePath: BINARY,
  args: ["--no-sandbox","--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
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
// Add MARKER first (separate initScript), then STEALTH_INIT
await ctx.addInitScript(MARKER);
await ctx.addInitScript(STEALTH_INIT);
const page = await ctx.newPage();
await page.goto("about:blank");
const r = await page.evaluate(() => {
  return {
    markerRan: window.__marker_early,
    hasChromeObj: "chrome" in window,
    webdriver: navigator.webdriver,
    screenAH: screen.availHeight,
    notifPerm: (() => { try { return Notification.permission; } catch(_) { return "err"; } })(),
    hasShare: "share" in navigator,
    gcsIsNative: window.getComputedStyle.toString().includes("[native code]"),
  };
});
console.log(JSON.stringify(r, null, 2));
await browser.close();
