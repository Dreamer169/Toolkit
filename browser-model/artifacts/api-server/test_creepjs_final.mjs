
import { chromium } from "playwright";
import { readFileSync } from "fs";

const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const PROXY  = "socks5://127.0.0.1:10916";

const tsSrc = readFileSync("/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts","utf8");
const STEALTH_INIT     = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/)   ||[])[1]||"";
const BOOT_SUFFIX      = (tsSrc.match(/const _WORKER_BOOT_SUFFIX = `([\s\S]*?)`;/)   ||[])[1]||"";
const LATE_FIX_PATCHES = (tsSrc.match(/const LATE_FIX_PATCHES = `([\s\S]*?)`;/)      ||[])[1]||"";
const WORKER_STEALTH   = (tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/)  ||[])[1]||"";
const STEALTH_FULL = STEALTH_INIT + (BOOT_SUFFIX||"");

console.log(`STEALTH_FULL: ${STEALTH_FULL.length} | LATE_FIX: ${LATE_FIX_PATCHES.length} | WORKER: ${WORKER_STEALTH.length}`);
if (!LATE_FIX_PATCHES.length) { console.error("LATE_FIX_PATCHES not found!"); process.exit(1); }

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
});

// CRITICAL: two initScripts — second overrides fingerprint-chromium native resets
await ctx.addInitScript(STEALTH_FULL);
await ctx.addInitScript(LATE_FIX_PATCHES);
ctx.on("page", p => p.on("worker", w => w.evaluate(WORKER_STEALTH).catch(()=>{})));

const page = await ctx.newPage();
console.log("Navigating to CreepJS...");
await page.goto("https://abrahamjuliot.github.io/creepjs/",{timeout:90000,waitUntil:"domcontentloaded"});
console.log("Waiting 50s for full analysis...");
await page.waitForTimeout(50000);

const result = await page.evaluate(() => {
  const body = document.body?.innerText || "";
  const lines = body.split("\n").map(l=>l.trim()).filter(Boolean);
  const hlLines    = lines.filter(l => /like.{0,20}headless|headless.*%/i.test(l)).slice(0,10);
  const trustLines = lines.filter(l => /trust|grade|\d+%/i.test(l)).slice(0,25);
  const pctLines   = lines.filter(l => /^\d+(\.\d+)?%/.test(l)).slice(0,15);
  return { hlLines, trustLines, pctLines };
});

console.log("\n=== CREEPJS WITH LATE_FIX_PATCHES ===");
console.log("\nlike-headless lines:");
result.hlLines.forEach(l => console.log("  " + l));
console.log("\ntrust/grade/% lines:");
result.trustLines.forEach(l => console.log("  " + l));
console.log("\nall % lines:");
result.pctLines.forEach(l => console.log("  " + l));

await browser.close();
console.log("\nDone.");
