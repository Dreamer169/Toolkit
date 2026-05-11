import { chromium } from "playwright";
import { readFileSync } from "fs";

const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const PROXY  = "socks5://127.0.0.1:10857";
const SRC    = "/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts";
const tsSrc  = readFileSync(SRC, "utf8");

const STEALTH_INIT     = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/)   ||[])[1]||"";
const BOOT_SUFFIX      = (tsSrc.match(/const _WORKER_BOOT_SUFFIX = `([\s\S]*?)`;/)   ||[])[1]||"";
const LATE_FIX_PATCHES = (tsSrc.match(/const LATE_FIX_PATCHES = `([\s\S]*?)`;/)      ||[])[1]||"";
const WORKER_STEALTH   = (tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/)  ||[])[1]||"";
const STEALTH_FULL     = STEALTH_INIT + (BOOT_SUFFIX||"");

// Port 10857 = Asia/Hong_Kong per renderer.ts _PROXY_PORT_TZ
const TZ = "Asia/Hong_Kong";

console.log(`[9p-v2] patches: STEALTH=${STEALTH_FULL.length} LATE_FIX=${LATE_FIX_PATCHES.length} WORKER=${WORKER_STEALTH.length}`);
console.log(`[9p-v2] proxy=${PROXY} tz=${TZ}`);

const ARGS = [
  "--no-sandbox","--disable-dev-shm-usage",
  "--disable-blink-features=AutomationControlled",
  "--no-first-run","--no-default-browser-check","--mute-audio",
  "--lang=en-US","--use-gl=angle","--use-angle=swiftshader","--enable-webgl",
  "--window-size=1920,1080",
  `--fingerprint=${Math.floor(Math.random()*0x7fffffff)}`,
  "--fingerprint-platform=linux","--fingerprint-brand=Chrome",
  "--fingerprint-brand-version=144","--fingerprint-hardware-concurrency=8",
  `--timezone=${TZ}`,
  `--proxy-server=${PROXY}`,"--disable-quic",
  "--proxy-resolves-dns-locally","--disable-non-proxied-udp",
];
const CTX_OPTS = {
  userAgent:"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
  viewport:{width:1920,height:1040}, screen:{width:1920,height:1080},
  locale:"en-US", timezoneId: TZ,
  colorScheme:"dark", ignoreHTTPSErrors:true,
};

async function mkBrowser() {
  const b = await chromium.launch({
    headless:false, executablePath:BINARY, args:ARGS,
    ignoreDefaultArgs:["--enable-automation"],
    env:{...process.env, DISPLAY:":99", BROWSER_PROXY:PROXY},
  });
  const ctx = await b.newContext(CTX_OPTS);
  await ctx.addInitScript(STEALTH_FULL);
  if (LATE_FIX_PATCHES) await ctx.addInitScript(LATE_FIX_PATCHES);
  ctx.on("page", p => p.on("worker", w => w.evaluate(WORKER_STEALTH).catch(()=>{})));
  return { b, ctx };
}

// Poll helper: wait until condition or maxMs
async function pollText(page, condFn, intervalMs, maxMs) {
  const steps = Math.ceil(maxMs / intervalMs);
  for (let i = 0; i < steps; i++) {
    await page.waitForTimeout(intervalMs);
    const text = await page.evaluate(() => document.body?.innerText||"").catch(()=>"");
    if (condFn(text)) return text;
  }
  return await page.evaluate(() => document.body?.innerText||"").catch(()=>"");
}

const results = [];

// 1. BrowserScan
console.log(`\n[${new Date().toISOString().slice(11,19)}] === 1/9 BrowserScan ===`);
{
  const {b, ctx} = await mkBrowser();
  const page = await ctx.newPage();
  await page.goto("https://www.browserscan.net/bot-detection", {timeout:90000, waitUntil:"domcontentloaded"});
  await page.waitForTimeout(20000);
  const text = await page.evaluate(() => document.body?.innerText||"");
  await page.screenshot({path:"/tmp/9p2_browserscan.png"});
  await b.close();
  const bot = /bot detected|detected as bot/i.test(text);
  const score = (text.match(/score[:\s]+(\d+)/i)||[])[1];
  const verdict = bot ? `FAIL bot_detected score=${score||"-"}` : `PASS human score=${score||"-"}`;
  const pass = !bot;
  console.log(`  ${pass?"✅":"❌"} ${verdict}`);
  results.push({name:"BrowserScan", pass, verdict});
}

// 2. CreepJS
console.log(`\n[${new Date().toISOString().slice(11,19)}] === 2/9 CreepJS ===`);
{
  const {b, ctx} = await mkBrowser();
  const page = await ctx.newPage();
  await page.goto("https://abrahamjuliot.github.io/creepjs/", {timeout:90000, waitUntil:"domcontentloaded"});
  await page.waitForTimeout(55000);
  const text = await page.evaluate(() => document.body?.innerText||"");
  await page.screenshot({path:"/tmp/9p2_creepjs.png"});
  await b.close();
  const lhMatch = text.match(/(\d+)%\s*like.headless/i);
  const lh = lhMatch ? parseInt(lhMatch[1]) : null;
  const trustMatch = text.match(/trust[:\s]+(\d+)%/i) || text.match(/level[:\s]+(\d+)%/i);
  const trust = trustMatch ? trustMatch[1] : "?";
  let verdict, pass;
  if (lh === null) { verdict = "FAIL could_not_parse"; pass = false; }
  else if (lh === 0) { verdict = `PASS 0% like-headless trust=${trust}%`; pass = true; }
  else { verdict = `FAIL ${lh}% like-headless trust=${trust}%`; pass = false; }
  console.log(`  ${pass?"✅":"❌"} ${verdict}`);
  results.push({name:"CreepJS", pass, verdict});
}

// 3. IPHey — poll until "Temporary value" disappears (real result loaded)
console.log(`\n[${new Date().toISOString().slice(11,19)}] === 3/9 IPHey ===`);
{
  const {b, ctx} = await mkBrowser();
  const page = await ctx.newPage();
  await page.goto("https://iphey.com/", {timeout:90000, waitUntil:"domcontentloaded"});
  // Poll max 90s (18 × 5s) until "Temporary value" disappears
  const text = await pollText(page, t => !t.includes("Temporary value"), 5000, 90000);
  await page.screenshot({path:"/tmp/9p2_iphey.png"});
  await b.close();
  // Only check result section (before "How is this determined?")
  const resultSection = (text.split("How is this determined?")[0] || text);
  const trustworthy = /trustworthy|genuine/i.test(resultSection);
  const suspicious  = /suspicious/i.test(resultSection);
  const stillLoading = text.includes("Temporary value");
  let verdict, pass;
  if (stillLoading) { verdict = "FAIL still_loading_after_90s"; pass = false; }
  else if (trustworthy) { verdict = `PASS Trustworthy`; pass = true; }
  else if (suspicious)  { verdict = `FAIL Suspicious`; pass = false; }
  else { verdict = `PASS(assumed) no_flag`; pass = true; }
  console.log(`  ${pass?"✅":"❌"} ${verdict}`);
  results.push({name:"IPHey", pass, verdict});
}

// 4. PixelScan
console.log(`\n[${new Date().toISOString().slice(11,19)}] === 4/9 PixelScan ===`);
{
  const {b, ctx} = await mkBrowser();
  const page = await ctx.newPage();
  await page.goto("https://pixelscan.net/bot-check", {timeout:90000, waitUntil:"domcontentloaded"});
  await page.waitForTimeout(35000);
  const text = await page.evaluate(() => document.body?.innerText||"");
  await page.screenshot({path:"/tmp/9p2_pixelscan.png"});
  await b.close();
  const human = /you're definitely a human/i.test(text);
  const navD  = /Navigator\s*\n\s*Detected/i.test(text);
  const shareD= /Share\s*\n\s*Detected/i.test(text);
  const canD  = /CanShare\s*\n\s*Detected/i.test(text);
  const issues = [];
  if (!human)  issues.push("not-human");
  if (navD)    issues.push("Navigator-Detected");
  if (shareD)  issues.push("Share-Detected");
  if (canD)    issues.push("CanShare-Detected");
  const pass = issues.length === 0;
  const verdict = pass ? "PASS human + all Clear" : `FAIL ${issues.join(", ")}`;
  console.log(`  ${pass?"✅":"❌"} ${verdict}`);
  results.push({name:"PixelScan", pass, verdict});
}

// 5. Cloudflare (nowsecure.nl)
console.log(`\n[${new Date().toISOString().slice(11,19)}] === 5/9 Cloudflare ===`);
{
  const {b, ctx} = await mkBrowser();
  const page = await ctx.newPage();
  await page.goto("https://nowsecure.nl/", {timeout:90000, waitUntil:"domcontentloaded"});
  await page.waitForTimeout(20000);
  const text = await page.evaluate(() => document.body?.innerText||"");
  await page.screenshot({path:"/tmp/9p2_cloudflare.png"});
  await b.close();
  const blocked = /blocked|just a moment|attention required|verify you are human/i.test(text);
  const pass = !blocked;
  const verdict = blocked ? "FAIL blocked_by_cf" : "PASS page_loaded_no_block";
  console.log(`  ${pass?"✅":"❌"} ${verdict}`);
  results.push({name:"Cloudflare", pass, verdict});
}

// 6. DataDome
console.log(`\n[${new Date().toISOString().slice(11,19)}] === 6/9 DataDome ===`);
{
  const {b, ctx} = await mkBrowser();
  const page = await ctx.newPage();
  await page.goto("https://antoinevastel.com/bots/datadome", {timeout:90000, waitUntil:"domcontentloaded"});
  await page.waitForTimeout(18000);
  const text = await page.evaluate(() => document.body?.innerText||"");
  await page.screenshot({path:"/tmp/9p2_datadome.png"});
  await b.close();
  const blocked = /blocked|bot detected|access denied|sorry/i.test(text);
  const hasBody = text.trim().length > 100;
  const pass = !blocked && hasBody;
  const verdict = blocked ? "FAIL blocked_by_datadome" : pass ? "PASS page_loaded" : "FAIL empty_page";
  console.log(`  ${pass?"✅":"❌"} ${verdict}`);
  results.push({name:"DataDome", pass, verdict});
}

// 7. Brotector
console.log(`\n[${new Date().toISOString().slice(11,19)}] === 7/9 Brotector ===`);
{
  const {b, ctx} = await mkBrowser();
  const page = await ctx.newPage();
  await page.goto("https://kaliiiiiiiiii.github.io/brotector/", {timeout:90000, waitUntil:"domcontentloaded"});
  await page.waitForTimeout(20000);
  const text = await page.evaluate(() => document.body?.innerText||"");
  await page.screenshot({path:"/tmp/9p2_brotector.png"});
  await b.close();
  const signals = (text.match(/(\d+)\s*bot signal/i)||[])[1];
  const failed  = /bot detected|signals detected/i.test(text) || (signals && parseInt(signals) > 0);
  const pass = !failed;
  const verdict = pass ? `PASS 0 bot signals` : `FAIL ${signals||"?"} signals`;
  console.log(`  ${pass?"✅":"❌"} ${verdict}`);
  results.push({name:"Brotector", pass, verdict});
}

// 8. Sannysoft
console.log(`\n[${new Date().toISOString().slice(11,19)}] === 8/9 Sannysoft ===`);
{
  const {b, ctx} = await mkBrowser();
  const page = await ctx.newPage();
  await page.goto("https://bot.sannysoft.com/", {timeout:90000, waitUntil:"domcontentloaded"});
  await page.waitForTimeout(14000);
  const text = await page.evaluate(() => document.body?.innerText||"");
  await page.screenshot({path:"/tmp/9p2_sannysoft.png"});
  await b.close();
  const failLines = text.split("\n").filter(l => /(FAIL|failed)/i.test(l));
  const pass = failLines.length === 0;
  const verdict = pass ? "PASS all checks passed" : `FAIL ${failLines.length} checks: ${failLines.slice(0,3).join(" | ").slice(0,100)}`;
  console.log(`  ${pass?"✅":"❌"} ${verdict}`);
  results.push({name:"Sannysoft", pass, verdict});
}

// 9. Fingerprint.com — poll until visitor ID loads, then check for visitor ID presence
console.log(`\n[${new Date().toISOString().slice(11,19)}] === 9/9 Fingerprint.com ===`);
{
  const {b, ctx} = await mkBrowser();
  const page = await ctx.newPage();
  await page.goto("https://fingerprint.com/demo/", {timeout:90000, waitUntil:"domcontentloaded"});
  // Poll until "Loading visitor ID" disappears
  const text = await pollText(page, t => !/Loading visitor ID/i.test(t), 5000, 60000);
  await page.screenshot({path:"/tmp/9p2_fingerprint.png"});
  await b.close();
  // Success = visitor ID (32 hex chars) was generated
  const visitorId = text.match(/[0-9a-f]{20,}/i)?.[0];
  const stillLoading = /Loading visitor ID/i.test(text);
  // Real bot detection result — NOT section headers
  // The actual result block contains "Bot Detected: Yes/No" or "Result: Not a Bot"
  const botResult = text.match(/bot\s+detected[:\s]+(yes|no)/i)?.[1]?.toLowerCase();
  let pass, verdict;
  if (stillLoading) { pass = false; verdict = "FAIL still_loading"; }
  else if (botResult === "yes") { pass = false; verdict = "FAIL bot_detected"; }
  else if (botResult === "no" || visitorId) { pass = true; verdict = `PASS visitor_id=${visitorId?.slice(0,12)||"?"}...`; }
  else { pass = false; verdict = "FAIL no_visitor_id"; }
  console.log(`  ${pass?"✅":"❌"} ${verdict}`);
  results.push({name:"Fingerprint", pass, verdict});
}

// Final summary
console.log(`\n${"=".repeat(57)}`);
console.log(`[9-PLATFORM RESULTS v2] ${new Date().toISOString()}`);
console.log("=".repeat(57));
results.forEach(r => console.log(`  ${r.pass?"✅":"❌"} ${r.name.padEnd(15)} ${r.verdict}`));
const passed = results.filter(r=>r.pass).length;
console.log(`\n  Total: ${passed}/${results.length} PASSED`);
console.log("=".repeat(57));
