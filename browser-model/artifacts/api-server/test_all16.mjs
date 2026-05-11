
import { chromium } from "playwright";
import { readFileSync } from "fs";

const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const PROXY  = "socks5://127.0.0.1:10916";
const tsSrc = readFileSync("/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts","utf8");
const STEALTH_FULL     = ((tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/)   ||[])[1]||"") +
                          ((tsSrc.match(/const _WORKER_BOOT_SUFFIX = `([\s\S]*?)`;/)   ||[])[1]||"");
const LATE_FIX_PATCHES = (tsSrc.match(/const LATE_FIX_PATCHES = `([\s\S]*?)`;/)      ||[])[1]||"";
const WORKER_STEALTH   = (tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/)  ||[])[1]||"";

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
    `--proxy-server=${PROXY}`,"--disable-quic","--proxy-resolves-dns-locally","--disable-non-proxied-udp",
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
ctx.on("page", p => p.on("worker", w => w.evaluate(WORKER_STEALTH).catch(()=>{})));

const page = await ctx.newPage();
await page.goto("about:blank");

// Check all 16 like-headless items + worker WebGL
const probe = await page.evaluate(() => {
  const mimeTypes = Object.keys({ ...navigator.mimeTypes });
  return {
    // 1 noChrome
    noChrome: !("chrome" in window),
    // 3 noPlugins
    noPlugins: navigator.plugins.length === 0,
    // 4 noMimeTypes
    noMimeTypes: mimeTypes.length === 0,
    // 5 notificationIsDenied
    notificationIsDenied: "Notification" in window && Notification.permission === "denied",
    // 6 hasKnownBgColor
    hasKnownBgColor: (() => {
      const d = document.createElement("div");
      document.body.appendChild(d);
      d.setAttribute("style","background-color: ActiveText");
      const c = getComputedStyle(d).backgroundColor;
      document.body.removeChild(d);
      return c === "rgb(255, 0, 0)";
    })(),
    // 7 prefersLightColor
    prefersLightColor: matchMedia("(prefers-color-scheme: light)").matches,
    // 8 uaDataIsBlank
    uaDataIsBlank: "userAgentData" in navigator && (navigator.userAgentData?.platform === ""),
    uaDataPlatform: navigator.userAgentData ? navigator.userAgentData.platform : "NOT_IN_NAVIGATOR",
    // 9 pdfIsDisabled
    pdfIsDisabled: "pdfViewerEnabled" in navigator && navigator.pdfViewerEnabled === false,
    // 10 noTaskbar
    noTaskbar: screen.height === screen.availHeight && screen.width === screen.availWidth,
    // 11 hasVvpScreenRes
    hasVvpScreenRes: (innerWidth===screen.width && outerHeight===screen.height) ||
      ("visualViewport" in window && window.visualViewport.width===screen.width && window.visualViewport.height===screen.height),
    // 13 noWebShare
    noWebShare: CSS.supports("accent-color: initial") && (!("share" in navigator) || !("canShare" in navigator)),
    // 14-16 headlessEstimate
    noContentIndex: CSS.supports("appearance: initial") && !("ContentIndex" in window),
    noContactsManager: !("ContactsManager" in window),
    noDownlinkMax: !("downlinkMax" in ((window.NetworkInformation?.prototype) || {})),
    // canvas measureText
    measureText: (() => { const c=document.createElement("canvas"); c.getContext("2d").font="11px arial"; return c.getContext("2d").measureText("Hello").width; })(),
    // hasSwiftShader needs workerScope — check main thread WebGL
    mainGLRenderer: (() => {
      const c=document.createElement("canvas"); const gl=c.getContext("webgl");
      const d=gl&&gl.getExtension("WEBGL_debug_renderer_info");
      return d ? gl.getParameter(d.UNMASKED_RENDERER_WEBGL) : "no-ext";
    })(),
  };
});

// Check permissions bug async
const permsBug = await page.evaluate(async () => {
  if (!("permissions" in navigator)) return false;
  const res = await navigator.permissions.query({ name: "notifications" });
  return res.state === "prompt" && "Notification" in window && Notification.permission === "denied";
});

// Check worker WebGL via a shared worker
const workerGLRenderer = await page.evaluate(() => {
  return new Promise((resolve) => {
    const blob = new Blob([`
      const c = new OffscreenCanvas(1,1);
      const gl = c.getContext("webgl");
      const d = gl && gl.getExtension("WEBGL_debug_renderer_info");
      const r = d ? gl.getParameter(d.UNMASKED_RENDERER_WEBGL) : "no-ext";
      self.postMessage(r);
    `], {type:"application/javascript"});
    const url = URL.createObjectURL(blob);
    const w = new Worker(url);
    w.onmessage = e => resolve(e.data);
    w.onerror = () => resolve("worker-error");
    setTimeout(() => resolve("timeout"), 5000);
  });
});

console.log("=== ALL 16 likeHeadless CHECKS ===");
console.log("1  noChrome             :", probe.noChrome,           probe.noChrome?"FAIL":"ok");
console.log("2  hasPermissionsBug    :", permsBug,                  permsBug?"FAIL":"ok");
console.log("3  noPlugins            :", probe.noPlugins,           probe.noPlugins?"FAIL":"ok");
console.log("4  noMimeTypes          :", probe.noMimeTypes,         probe.noMimeTypes?"FAIL":"ok");
console.log("5  notificationIsDenied :", probe.notificationIsDenied,probe.notificationIsDenied?"FAIL":"ok");
console.log("6  hasKnownBgColor      :", probe.hasKnownBgColor,     probe.hasKnownBgColor?"FAIL":"ok");
console.log("7  prefersLightColor    :", probe.prefersLightColor,   probe.prefersLightColor?"FAIL":"ok");
console.log("8  uaDataIsBlank        :", probe.uaDataIsBlank,       probe.uaDataIsBlank?"FAIL":"ok", "  [platform="+probe.uaDataPlatform+"]");
console.log("9  pdfIsDisabled        :", probe.pdfIsDisabled,       probe.pdfIsDisabled?"FAIL":"ok");
console.log("10 noTaskbar            :", probe.noTaskbar,            probe.noTaskbar?"FAIL":"ok");
console.log("11 hasVvpScreenRes      :", probe.hasVvpScreenRes,     probe.hasVvpScreenRes?"FAIL":"ok");
console.log("12 hasSwiftShader       : [workerScope]  mainGL=", probe.mainGLRenderer);
console.log("   worker WebGL renderer:", workerGLRenderer,          /SwiftShader/i.test(workerGLRenderer)?"FAIL":"ok");
console.log("13 noWebShare           :", probe.noWebShare,          probe.noWebShare?"FAIL":"ok");
console.log("14 noContentIndex       :", probe.noContentIndex,      probe.noContentIndex?"FAIL(expected for Linux)":"ok");
console.log("15 noContactsManager    :", probe.noContactsManager,   probe.noContactsManager?"FAIL(expected for Linux)":"ok");
console.log("16 noDownlinkMax        :", probe.noDownlinkMax,       probe.noDownlinkMax?"FAIL(expected for Linux)":"ok");
console.log("\nmeasureText(Hello):", probe.measureText);

await browser.close();
console.log("Done.");
