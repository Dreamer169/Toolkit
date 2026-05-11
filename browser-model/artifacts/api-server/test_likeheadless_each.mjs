import { chromium } from "playwright";
import { readFileSync } from "fs";

const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const PROXY  = "socks5://127.0.0.1:10916";
const tsSrc  = readFileSync("/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts","utf8");
const STEALTH_INIT   = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/)  ||[])[1]||"";
const BOOT_SUFFIX    = (tsSrc.match(/const _WORKER_BOOT_SUFFIX = `([\s\S]*?)`;/)  ||[])[1]||"";
const STEALTH_FULL   = STEALTH_INIT + (BOOT_SUFFIX||"");
console.log(`STEALTH: ${STEALTH_INIT.length} chars`);

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
  locale:"en-US", timezoneId:"America/Los_Angeles",
  colorScheme:"dark",   // CHANGED: dark mode reduces prefersLightColor
  ignoreHTTPSErrors:true,
  extraHTTPHeaders: {
    "Accept-Language":"en-US,en;q=0.9",
    "sec-ch-ua":'"Chromium";v="144", "Not:A-Brand";v="99", "Google Chrome";v="144"',
    "sec-ch-ua-mobile":"?0","sec-ch-ua-platform":'"Linux"',
    "sec-ch-ua-platform-version":'"6.8.0"',
  },
});
await ctx.addInitScript(STEALTH_FULL);
ctx.on("page",async p=>{
  p.on("worker",w=>w.evaluate(STEALTH_FULL).catch(()=>{}));
});

const page = await ctx.newPage();

// Navigate to creepjs to get the actual worker scope filled in
// We need workerScope for hasSwiftShader check
// But first check independently on about:blank for the sync checks
await page.goto("about:blank");

const checks = await page.evaluate(async () => {
  const IS_BLINK = 'chrome' in window || /Chrome/.test(navigator.userAgent);
  const mimeTypes = Object.keys({...navigator.mimeTypes});
  
  // Get computed style check for ActiveText
  let activeTextColor = 'unknown';
  try {
    const el = document.createElement('div');
    document.body.appendChild(el);
    el.setAttribute('style', 'background-color: ActiveText');
    activeTextColor = getComputedStyle(el).backgroundColor;
    document.body.removeChild(el);
  } catch(_) {}
  
  // Check notification permission
  let notifPerm = 'unknown', permsBugState = 'unknown';
  try { notifPerm = Notification.permission; } catch(_) {}
  try {
    const res = await navigator.permissions.query({name:'notifications'});
    permsBugState = res.state;
  } catch(_) { permsBugState = 'blocked'; }
  
  // Check pdfViewerEnabled
  let pdfEnabled;
  try { pdfEnabled = navigator.pdfViewerEnabled; } catch(_) { pdfEnabled = 'error'; }
  
  // Check screen metrics
  const screenMetrics = {
    w: screen.width, h: screen.height,
    aw: screen.availWidth, ah: screen.availHeight,
    innerW: window.innerWidth, innerH: window.innerHeight,
    outerW: window.outerWidth, outerH: window.outerHeight,
  };
  
  // Check share
  const hasShare = 'share' in navigator;
  const hasCanShare = 'canShare' in navigator;
  
  // Check visualViewport
  let vvp = null;
  try {
    if ('visualViewport' in window) {
      vvp = { w: visualViewport.width, h: visualViewport.height };
    }
  } catch(_) {}
  
  // Check content index
  const hasContentIndex = 'ContentIndex' in window || 
    ('serviceWorker' in navigator && 'contentIndex' in ServiceWorkerRegistration.prototype);
  
  // Check downlinkMax
  let downlinkMax = 'N/A';
  try {
    const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    downlinkMax = conn ? conn.downlinkMax : 'no connection API';
  } catch(_) {}
  
  // matchMedia dark/light
  const prefersLight = matchMedia('(prefers-color-scheme: light)').matches;
  const prefersDark  = matchMedia('(prefers-color-scheme: dark)').matches;
  
  // Chrome in window
  const hasChromeObj = 'chrome' in window;
  
  // webdriver
  const webDriver = navigator.webdriver;
  
  // uaData
  let uaPlatform = 'N/A';
  try { uaPlatform = navigator.userAgentData?.platform || 'empty'; } catch(_) {}
  
  // measureText test
  let mtWidth = null;
  try {
    const cv = document.createElement('canvas');
    cv.width = 400; cv.height = 50;
    const c2 = cv.getContext('2d');
    if (c2) {
      c2.font = '16px Arial';
      mtWidth = c2.measureText('Hello').width;
    }
  } catch(_) {}
  
  // Now evaluate each likeHeadless check:
  const likeH = {
    "1_noChrome":           IS_BLINK && !hasChromeObj,
    "2_hasPermissionsBug":  IS_BLINK && permsBugState === 'prompt' && notifPerm === 'denied',
    "3_noPlugins":          IS_BLINK && navigator.plugins.length === 0,
    "4_noMimeTypes":        IS_BLINK && mimeTypes.length === 0,
    "5_notificationIsDenied": IS_BLINK && 'Notification' in window && notifPerm === 'denied',
    "6_hasKnownBgColor":    activeTextColor === 'rgb(255, 0, 0)',
    "7_prefersLightColor":  prefersLight,
    "9_pdfIsDisabled":      'pdfViewerEnabled' in navigator && pdfEnabled === false,
    "10_noTaskbar":         screen.height === screen.availHeight && screen.width === screen.availWidth,
    "11_hasVvpScreenRes":   (screenMetrics.innerW === screen.width && screenMetrics.outerH === screen.height) 
                             || !!(vvp && vvp.w === screen.width && vvp.h === screen.height),
    "13_noWebShare":        IS_BLINK && (!hasShare || !hasCanShare),
  };
  
  const trueKeys = Object.entries(likeH).filter(([,v])=>v).map(([k])=>k);
  const falseKeys = Object.entries(likeH).filter(([,v])=>!v).map(([k])=>k);
  
  return {
    likeH, trueKeys, falseKeys,
    screenMetrics, notifPerm, permsBugState,
    activeTextColor, pdfEnabled, hasShare, hasCanShare,
    prefersLight, prefersDark, hasChromeObj, webDriver,
    uaPlatform, downlinkMax, hasContentIndex, mtWidth, vvp,
  };
});

console.log("\n=== likeHeadless EVALUATION ===");
console.log("TRUE (headless) items:", checks.trueKeys.join(', ') || "NONE!");
console.log("FALSE (clean) items:", checks.falseKeys.join(', '));
console.log("\n--- Detail ---");
console.log("screen:", checks.screenMetrics);
console.log("Notification.permission:", checks.notifPerm);
console.log("permissions.query(notifications).state:", checks.permsBugState);
console.log("ActiveText color:", checks.activeTextColor, "(headless=rgb(255,0,0))");
console.log("pdfViewerEnabled:", checks.pdfEnabled);
console.log("navigator.share:", checks.hasShare, "| canShare:", checks.hasCanShare);
console.log("prefersDark:", checks.prefersDark, "| prefersLight:", checks.prefersLight);
console.log("chrome in window:", checks.hasChromeObj);
console.log("navigator.webdriver:", checks.webDriver);
console.log("uaData.platform:", checks.uaPlatform);
console.log("downlinkMax:", checks.downlinkMax);
console.log("contentIndex:", checks.hasContentIndex);
console.log("measureText('Hello') width:", checks.mtWidth);
console.log("visualViewport:", checks.vvp);
console.log("\n=== ESTIMATED like-headless % ===");
console.log(`${checks.trueKeys.length}/15 = ${Math.round(checks.trueKeys.length/15*100)}%`);

await browser.close();
console.log("\nDone.");
