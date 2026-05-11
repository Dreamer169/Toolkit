
import { chromium } from "playwright";
import { readFileSync } from "fs";

const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const PROXY  = "socks5://127.0.0.1:10916";
const tsSrc = readFileSync("/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts","utf8");
const STEALTH_INIT     = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/)   ||[])[1]||"";
const BOOT_SUFFIX      = (tsSrc.match(/const _WORKER_BOOT_SUFFIX = `([\s\S]*?)`;/)   ||[])[1]||"";
const LATE_FIX_PATCHES = (tsSrc.match(/const LATE_FIX_PATCHES = `([\s\S]*?)`;/)      ||[])[1]||"";
const STEALTH_FULL = STEALTH_INIT + (BOOT_SUFFIX||"");

const browser = await chromium.launch({
  headless: false, executablePath: BINARY,
  args: [
    "--no-sandbox","--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--no-first-run","--no-default-browser-check","--mute-audio",
    "--lang=en-US","--use-gl=angle","--use-angle=swiftshader",
    "--enable-webgl","--window-size=1920,1080",
    `--fingerprint=${Math.floor(Math.random()*0x7fffffff)}`,
    "--fingerprint-platform=linux","--fingerprint-brand=Chrome",
    "--fingerprint-brand-version=144","--fingerprint-hardware-concurrency=4",
    "--timezone=America/Los_Angeles",
    `--proxy-server=${PROXY}`,"--disable-quic",
    "--proxy-resolves-dns-locally","--disable-non-proxied-udp",
  ],
  ignoreDefaultArgs: ["--enable-automation"],
  env: { ...process.env, DISPLAY: ":99" },
});
const ctx = await browser.newContext({
  userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
  viewport: {width:1920,height:1040}, screen:{width:1920,height:1080},
  locale:"en-US", timezoneId:"America/Los_Angeles", colorScheme:"light",
});
await ctx.addInitScript(STEALTH_FULL);
await ctx.addInitScript(LATE_FIX_PATCHES);

const page = await ctx.newPage();

// Navigate to blank page first to probe values
await page.goto("about:blank");
const probe = await page.evaluate(() => {
  const cv = document.createElement("canvas");
  cv.width = 200; cv.height = 50;
  const ctx2d = cv.getContext("2d");
  ctx2d.font = "11px arial";
  const mtWidth = ctx2d.measureText("Hello").width;

  // uaData check
  const uaData = navigator.userAgentData;
  const uaBrands = uaData ? JSON.stringify(uaData.brands) : "NO_UA_DATA";
  const uaPlatform = uaData ? uaData.platform : "NO_UA_DATA";
  const uaMobile = uaData ? uaData.mobile : "NO_UA_DATA";

  // screen checks
  const screen_ah  = screen.availHeight;
  const screen_aw  = screen.availWidth;
  const screen_h   = screen.height;
  const screen_w   = screen.width;

  // notification
  const notifPerm  = window.Notification ? Notification.permission : "no_notif";

  // share
  const hasShare   = "share" in navigator;
  const hasCanShare = "canShare" in navigator;

  // getComputedStyle / bg color for ActiveText
  const el = document.createElement("div");
  el.setAttribute("style", "color: ActiveText");
  document.body.appendChild(el);
  const cs = window.getComputedStyle(el);
  const activeTextColor = cs.color;
  document.body.removeChild(el);

  // downlinkMax
  const dlMax = navigator.connection ? navigator.connection.downlinkMax : "no_connection";

  // content index
  const hasContentIndex = "index" in (navigator.serviceWorker || {});

  // webdriver
  const wd = navigator.webdriver;

  // plugins
  const pluginCount = navigator.plugins.length;
  const mimeCount   = navigator.mimeTypes.length;

  // pdfViewerEnabled
  const pdfEnabled = navigator.pdfViewerEnabled;

  // chrome
  const hasChromeObj = "chrome" in window;

  // permissions check — need async, return marker
  return {
    mtWidth, uaBrands, uaPlatform, uaMobile,
    screen_ah, screen_aw, screen_h, screen_w,
    notifPerm, hasShare, hasCanShare, activeTextColor,
    dlMax, hasContentIndex, wd, pluginCount, mimeCount, pdfEnabled, hasChromeObj,
  };
});

// async permissions check
const permState = await page.evaluate(async () => {
  try {
    const r = await navigator.permissions.query({ name: "notifications" });
    return r.state;
  } catch(e) { return "error: " + e.message; }
});

console.log("=== FULL PROBE (with LATE_FIX_PATCHES) ===");
console.log("measureText width   :", probe.mtWidth, probe.mtWidth > 0 ? "OK" : "FAIL-negative");
console.log("uaData.platform     :", JSON.stringify(probe.uaPlatform), probe.uaPlatform ? "OK" : "FAIL-empty");
console.log("uaData.mobile       :", probe.uaMobile);
console.log("uaData.brands       :", probe.uaBrands);
console.log("screen ah/h         :", probe.screen_ah, "/", probe.screen_h, probe.screen_ah !== probe.screen_h ? "OK" : "FAIL-taskbar");
console.log("Notification.perm   :", probe.notifPerm, probe.notifPerm==="default"?"OK":"FAIL");
console.log("navigator.share     :", probe.hasShare, probe.hasShare?"OK":"FAIL");
console.log("navigator.canShare  :", probe.hasCanShare);
console.log("ActiveText color    :", probe.activeTextColor);
console.log("permissions.query   :", permState);
console.log("downlinkMax         :", probe.dlMax);
console.log("contentIndex        :", probe.hasContentIndex);
console.log("webdriver           :", probe.wd);
console.log("plugins             :", probe.pluginCount);
console.log("mimeTypes           :", probe.mimeCount);
console.log("pdfViewerEnabled    :", probe.pdfEnabled);
console.log("chrome in window    :", probe.hasChromeObj);

await browser.close();
console.log("Done.");
