import { chromium } from "playwright";
import { readFileSync } from "fs";

const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const PROXY  = "socks5://127.0.0.1:10916";
const tsSrc  = readFileSync("/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts","utf8");
const STEALTH_INIT   = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/)  ||[])[1]||"";
const WORKER_STEALTH = (tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/) ||[])[1]||"";
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
  colorScheme:"light", ignoreHTTPSErrors:true,
  extraHTTPHeaders: {
    "Accept-Language":"en-US,en;q=0.9",
    "sec-ch-ua":'"Chromium";v="144", "Not:A-Brand";v="99", "Google Chrome";v="144"',
    "sec-ch-ua-mobile":"?0","sec-ch-ua-platform":'"Linux"',
    "sec-ch-ua-platform-version":'"6.8.0"',
  },
});
await ctx.addInitScript(STEALTH_FULL);
ctx.on("page",p=>p.on("worker",w=>w.evaluate(WORKER_STEALTH).catch(()=>{})));

const page = await ctx.newPage();
await page.goto("https://abrahamjuliot.github.io/creepjs/",{timeout:70000,waitUntil:"domcontentloaded"});
console.log("Waiting 50s...");
await page.waitForTimeout(50000);

const r = await page.evaluate(() => {
  const body = document.body?.innerText || "";
  const lines = body.split("\n").map(l=>l.trim()).filter(l=>l.length>2&&l.length<300);
  // Grab every line that has "like headless", %, undefined, load, rendering, etc.
  const keyLines = lines.filter(l=>
    /like.{0,20}headless|headless.*%|%.*headless|like.*undef|undef.*like|rgba|noise|load.*\d|rendering|canvas|audio|worker|webgl|gpu|fingerprint|grade|score|trust/i.test(l)
  );
  // Full text extract (first 8000 chars)
  const fullText = body.slice(0,8000);
  // Check native toString for our stubs
  const nativeCheck = {
    showOpenFilePicker: typeof window.showOpenFilePicker === 'function' ? Function.prototype.toString.call(window.showOpenFilePicker).includes('[native code]') : 'missing',
    showSaveFilePicker: typeof window.showSaveFilePicker === 'function' ? Function.prototype.toString.call(window.showSaveFilePicker).includes('[native code]') : 'missing',
    showDirectoryPicker: typeof window.showDirectoryPicker === 'function' ? Function.prototype.toString.call(window.showDirectoryPicker).includes('[native code]') : 'missing',
  };
  return {keyLines: keyLines.slice(0,60), fullText, nativeCheck};
});

console.log("\n=== KEY LINES FROM CREEPJS ===");
r.keyLines.forEach(l=>console.log(" ",l));
console.log("\n=== NATIVE CHECK (should be true for native APIs) ===");
console.log(JSON.stringify(r.nativeCheck, null, 2));
console.log("\n=== FULL TEXT (first 4000 chars) ===");
console.log(r.fullText.slice(0, 4000));

await browser.close();
console.log("\nDone.");
