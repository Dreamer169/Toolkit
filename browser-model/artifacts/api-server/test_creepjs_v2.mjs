import { chromium } from "playwright";
import { readFileSync } from "fs";

const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const PROXY  = "socks5://127.0.0.1:10916";

const tsSrc = readFileSync("/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts","utf8");
const STEALTH_INIT   = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/)   ||[])[1]||"";
const WORKER_STEALTH = (tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/)  ||[])[1]||"";
const BOOT_SUFFIX    = (tsSrc.match(/const _WORKER_BOOT_SUFFIX = `([\s\S]*?)`;/)   ||[])[1]||"";
const STEALTH_FULL = STEALTH_INIT + (BOOT_SUFFIX||"");

console.log(`Stealth init: ${STEALTH_INIT.length} chars | Worker: ${WORKER_STEALTH.length} chars`);

const SEED = Math.floor(Math.random() * 0x7fffffff);
const browser = await chromium.launch({
  headless: false, executablePath: BINARY,
  args: [
    "--no-sandbox","--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--no-first-run","--no-default-browser-check","--mute-audio",
    "--lang=en-US","--use-gl=angle","--use-angle=swiftshader",
    "--enable-webgl","--window-size=1920,1080",
    `--fingerprint=${SEED}`,
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
  colorScheme:"light", ignoreHTTPSErrors:true,
  extraHTTPHeaders:{
    "Accept-Language":"en-US,en;q=0.9",
    "sec-ch-ua":'"Chromium";v="144", "Not:A-Brand";v="99", "Google Chrome";v="144"',
    "sec-ch-ua-mobile":"?0","sec-ch-ua-platform":'"Linux"',
    "sec-ch-ua-platform-version":'"6.8.0"',
  },
});
await ctx.addInitScript(STEALTH_FULL);
ctx.on("page", p => p.on("worker", w => w.evaluate(WORKER_STEALTH).catch(()=>{})));

const page = await ctx.newPage();
console.log("Navigating to CreepJS...");
await page.goto("https://abrahamjuliot.github.io/creepjs/",{timeout:70000,waitUntil:"domcontentloaded"});
console.log("Waiting 45s for CreepJS full analysis...");
await page.waitForTimeout(45000);

const result = await page.evaluate(() => {
  const body = document.body?.innerText || "";
  const lines = body.split("\n").map(l=>l.trim()).filter(Boolean);
  const hlLines = lines.filter(l=>/like.{0,15}headless|headless.*%|%.*headless/i.test(l)).slice(0,10);
  const issueLines = lines.filter(l=>
    /rgba.*noise|noise.*rgba|load.*\d+|like.*undef|\bload\b.*like|\d+%.*headless/i.test(l)
  ).slice(0,20);
  const apiCheck = {
    showOpenFilePicker: typeof window.showOpenFilePicker,
    showSaveFilePicker: typeof window.showSaveFilePicker,
    showDirectoryPicker: typeof window.showDirectoryPicker,
    launchQueue: typeof window.launchQueue,
    documentPictureInPicture: typeof window.documentPictureInPicture,
    userActivation: typeof navigator.userActivation,
    scheduling: typeof navigator.scheduling,
    virtualKeyboard: typeof navigator.virtualKeyboard,
    windowControlsOverlay: typeof navigator.windowControlsOverlay,
    ink: typeof navigator.ink,
  };
  const cv = document.createElement("canvas");
  const gl = cv.getContext("webgl");
  const dbg = gl?.getExtension("WEBGL_debug_renderer_info");
  const glRenderer = dbg ? gl?.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : "no-ext";
  const glVendor   = dbg ? gl?.getParameter(dbg.UNMASKED_VENDOR_WEBGL)   : "no-ext";
  // canvas noise check: fill gray, check if RGB channels modified
  const cnv = document.createElement("canvas"); cnv.width=20;cnv.height=20;
  const c2d = cnv.getContext("2d");
  c2d.fillStyle="rgba(200,200,200,0.9)"; c2d.fillRect(0,0,20,20);
  const id = c2d.getImageData(0,0,10,1);
  let rgbMods=0;
  for(let i=0;i<id.data.length;i+=4){
    if(id.data[i+3]>0){
      if(Math.abs(id.data[i]-200)>1||Math.abs(id.data[i+1]-200)>1||Math.abs(id.data[i+2]-200)>1) rgbMods++;
    }
  }
  return {hlLines,issueLines,apiCheck,glRenderer,glVendor,rgbMods};
});

console.log("\n╔═════════════════════════════════════════╗");
console.log("║   CreepJS v8.90 POST-PATCH RESULTS     ║");
console.log("╚═════════════════════════════════════════╝");
console.log("\n📊 Like-headless lines:");
result.hlLines.forEach(l=>console.log("  ",l));
console.log("\n⚠️  Issue lines (rgba/load/%):");
result.issueLines.forEach(l=>console.log("  ",l));
console.log("\n🔍 WebGL:");
console.log("   renderer:", result.glRenderer);
console.log("   vendor:  ", result.glVendor);
console.log("\n🎨 Canvas RGB mods on gray fill (should=0):", result.rgbMods, result.rgbMods===0?"✓ CLEAN":"✗ NOISE DETECTED");
console.log("\n✅ API Stubs:");
Object.entries(result.apiCheck).forEach(([k,v])=>{
  console.log(`   ${v!=="undefined"?"✓":"✗"} ${k}: ${v}`);
});

await browser.close();
console.log("\nDone.");
