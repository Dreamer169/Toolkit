
import { chromium } from "playwright";
import { readFileSync } from "fs";

const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const PROXY  = "socks5://127.0.0.1:10916";
const tsSrc  = readFileSync("/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts","utf8");

const STEALTH_INIT     = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/)  ||[])[1]||"";
const BOOT_SUFFIX      = (tsSrc.match(/const _WORKER_BOOT_SUFFIX = `([\s\S]*?)`;/)  ||[])[1]||"";
const LATE_FIX_PATCHES = (tsSrc.match(/const LATE_FIX_PATCHES = `([\s\S]*?)`;/)     ||[])[1]||"";
const WORKER_STEALTH   = (tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/) ||[])[1]||"";
const STEALTH_FULL     = STEALTH_INIT + (BOOT_SUFFIX||"");

console.log(`[smoke] STEALTH:${STEALTH_FULL.length} LATE_FIX:${LATE_FIX_PATCHES.length} WORKER:${WORKER_STEALTH.length}`);
if (!LATE_FIX_PATCHES.length) { console.error("[smoke] LATE_FIX_PATCHES missing"); process.exit(1); }

const ARGS = [
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
];
const CTX_OPTS = {
  userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
  viewport:{width:1920,height:1040}, screen:{width:1920,height:1080},
  locale:"en-US", timezoneId:"America/Los_Angeles",
  colorScheme:"dark", ignoreHTTPSErrors:true,
};

const fails = [];
let passed = 0;

async function mkBrowser() {
  const b = await chromium.launch({
    headless:false, executablePath:BINARY, args:ARGS,
    ignoreDefaultArgs:["--enable-automation"],
    env:{...process.env, DISPLAY:":99"},
  });
  const ctx = await b.newContext(CTX_OPTS);
  await ctx.addInitScript(STEALTH_FULL);
  await ctx.addInitScript(LATE_FIX_PATCHES);
  ctx.on("page", p => p.on("worker", w => w.evaluate(WORKER_STEALTH).catch(()=>{})));
  return { b, ctx };
}

// Test 1: CreepJS like-headless
console.log("\n[smoke] Test 1: CreepJS...");
{
  const {b, ctx} = await mkBrowser();
  const page = await ctx.newPage();
  await page.goto("https://abrahamjuliot.github.io/creepjs/", {timeout:90000, waitUntil:"domcontentloaded"});
  await page.waitForTimeout(50000);
  const text = await page.evaluate(() => document.body ? document.body.innerText : "");
  await b.close();
  const likeHeadless = (text.match(/(\d+)%\s+like\s+headless/i)||[])[1];
  const lhVal = parseInt(likeHeadless||"999");
  const trustMatch = text.match(/level:\s*(\d+)%/i);
  if (lhVal === 0) {
    console.log(`[smoke] PASS CreepJS: ${likeHeadless}% like-headless | trust: ${trustMatch ? trustMatch[1]+"%" : "?"}`);
    passed++;
  } else {
    console.log(`[smoke] FAIL CreepJS: ${likeHeadless||"?"}% like-headless (expected 0)`);
    fails.push(`CreepJS like-headless: ${likeHeadless||"?"}%`);
  }
}

// Test 2: Pixelscan
console.log("\n[smoke] Test 2: Pixelscan...");
{
  const {b, ctx} = await mkBrowser();
  const page = await ctx.newPage();
  await page.goto("https://pixelscan.net/bot-check", {timeout:90000, waitUntil:"domcontentloaded"});
  await page.waitForTimeout(35000);
  const bodyText = await page.evaluate(() => document.body ? document.body.innerText : "");
  await b.close();
  const isHuman = /you're definitely a human/i.test(bodyText);
  const navDetected = /Navigator\s*\n\s*Detected/i.test(bodyText);
  const shareDetected = /Share\s*\n\s*Detected/i.test(bodyText);
  const canShareDetected = /CanShare\s*\n\s*Detected/i.test(bodyText);
  if (isHuman && !navDetected && !shareDetected && !canShareDetected) {
    console.log("[smoke] PASS Pixelscan: Human + Navigator/Share/CanShare = Clear");
    passed++;
  } else {
    const issues = [];
    if (!isHuman) issues.push("not-human");
    if (navDetected) issues.push("Navigator-Detected");
    if (shareDetected) issues.push("Share-Detected");
    if (canShareDetected) issues.push("CanShare-Detected");
    console.log(`[smoke] FAIL Pixelscan: ${issues.join(", ")}`);
    fails.push(`Pixelscan: ${issues.join(", ")}`);
  }
}

// Summary
console.log(`\n[smoke] ==========================================`);
console.log(`[smoke] Passed: ${passed}/2`);
if (fails.length > 0) {
  console.log(`[smoke] FAILURES:`);
  fails.forEach(f => console.log(`  - ${f}`));
  process.exit(1);
} else {
  console.log(`[smoke] ALL TESTS PASSED`);
  process.exit(0);
}
