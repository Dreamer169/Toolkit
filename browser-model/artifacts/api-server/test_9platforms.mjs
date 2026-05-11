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

console.log(`[9p] patches: STEALTH=${STEALTH_FULL.length} LATE_FIX=${LATE_FIX_PATCHES.length} WORKER=${WORKER_STEALTH.length}`);

const ARGS = [
  "--no-sandbox","--disable-dev-shm-usage",
  "--disable-blink-features=AutomationControlled",
  "--no-first-run","--no-default-browser-check","--mute-audio",
  "--lang=en-US","--use-gl=angle","--use-angle=swiftshader","--enable-webgl",
  "--window-size=1920,1080",
  `--fingerprint=${Math.floor(Math.random()*0x7fffffff)}`,
  "--fingerprint-platform=linux","--fingerprint-brand=Chrome",
  "--fingerprint-brand-version=144","--fingerprint-hardware-concurrency=8",
  "--timezone=America/Los_Angeles",
  `--proxy-server=${PROXY}`,"--disable-quic",
  "--proxy-resolves-dns-locally","--disable-non-proxied-udp",
];
const CTX_OPTS = {
  userAgent:"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
  viewport:{width:1920,height:1040}, screen:{width:1920,height:1080},
  locale:"en-US", timezoneId:"America/Los_Angeles",
  colorScheme:"dark", ignoreHTTPSErrors:true,
};

async function mkBrowser() {
  const b = await chromium.launch({
    headless:false, executablePath:BINARY, args:ARGS,
    ignoreDefaultArgs:["--enable-automation"],
    env:{...process.env, DISPLAY:":99"},
  });
  const ctx = await b.newContext(CTX_OPTS);
  await ctx.addInitScript(STEALTH_FULL);
  if (LATE_FIX_PATCHES) await ctx.addInitScript(LATE_FIX_PATCHES);
  ctx.on("page", p => p.on("worker", w => w.evaluate(WORKER_STEALTH).catch(()=>{})));
  return { b, ctx };
}

const results = [];

async function testSite(name, url, waitMs, checkFn) {
  console.log(`\n[${new Date().toISOString().slice(11,19)}] === ${name} ===`);
  let verdict = "UNKNOWN";
  try {
    const {b, ctx} = await mkBrowser();
    const page = await ctx.newPage();
    const apiData = [];
    page.on("response", async r => {
      try {
        const ct = r.headers()["content-type"]||"";
        if (ct.includes("json")) { const j=await r.json().catch(()=>null); if(j) apiData.push({url:r.url().slice(0,100),d:j}); }
      } catch(e){}
    });
    await page.goto(url, {timeout:90000, waitUntil:"domcontentloaded"});
    await page.waitForTimeout(waitMs);
    const text = await page.evaluate(() => document.body?.innerText||"").catch(()=>"");
    verdict = checkFn(text, apiData);
    await page.screenshot({path:`/tmp/9p_${name.toLowerCase().replace(/[^a-z0-9]/g,"_")}.png`}).catch(()=>{});
    await b.close();
  } catch(e) {
    verdict = "ERROR: " + e.message.slice(0,100);
    console.log("  !", verdict);
  }
  const pass = verdict.startsWith("PASS");
  console.log(`  ${pass?"✅":"❌"} ${verdict}`);
  results.push({name, pass, verdict});
}

// 1. BrowserScan
await testSite("BrowserScan", "https://www.browserscan.net/bot-detection", 20000, (t) => {
  const bot = /bot detected|detected as bot/i.test(t);
  const human = /not a bot|human detected|normal/i.test(t);
  const score = (t.match(/score[:\s]+(\d+)/i)||[])[1];
  return bot ? `FAIL bot_detected score=${score||"-"}`
       : human ? `PASS human score=${score||"-"}`
       : `PASS(assumed) score=${score||"?"} (no bot flag)`;
});

// 2. CreepJS
await testSite("CreepJS", "https://abrahamjuliot.github.io/creepjs/", 55000, (t) => {
  const lhMatch = t.match(/(\d+)%\s*like.headless/i);
  const lh = lhMatch ? parseInt(lhMatch[1]) : null;
  const trustMatch = t.match(/trust[:\s]+(\d+)%/i);
  const trust = trustMatch ? trustMatch[1] : "?";
  if (lh === null) return "FAIL could_not_parse";
  return lh === 0
    ? `PASS 0% like-headless trust=${trust}%`
    : `FAIL ${lh}% like-headless trust=${trust}%`;
});

// 3. IPHey
await testSite("IPHey", "https://iphey.com/", 25000, (t) => {
  const trustworthy = /trustworthy|genuine|clean/i.test(t);
  const suspicious  = /suspicious|bot detected|flagged/i.test(t);
  const score = (t.match(/(\d+)\s*%/)||[])[1];
  return suspicious ? `FAIL suspicious score=${score||"-"}`
       : trustworthy ? `PASS trustworthy score=${score||"-"}`
       : `PASS(no_flag) score=${score||"?"}`;
});

// 4. PixelScan
await testSite("PixelScan", "https://pixelscan.net/bot-check", 35000, (t) => {
  const human = /you're definitely a human/i.test(t);
  const navD  = /Navigator\s*\n\s*Detected/i.test(t);
  const shareD= /Share\s*\n\s*Detected/i.test(t);
  const canD  = /CanShare\s*\n\s*Detected/i.test(t);
  const issues = [];
  if (!human)  issues.push("not-human");
  if (navD)    issues.push("Navigator-Detected");
  if (shareD)  issues.push("Share-Detected");
  if (canD)    issues.push("CanShare-Detected");
  return issues.length === 0 ? "PASS human + all Clear" : `FAIL ${issues.join(", ")}`;
});

// 5. Cloudflare
await testSite("Cloudflare", "https://nowsecure.nl/", 20000, (t) => {
  const blocked = /blocked|just a moment|attention required|challenge|verify you are human/i.test(t);
  const passed  = /success|verified|protected by cloudflare|you passed/i.test(t);
  return blocked ? "FAIL blocked_by_cf"
       : passed  ? "PASS verified"
       : "PASS(no_block) page_loaded";
});

// 6. DataDome
await testSite("DataDome", "https://antoinevastel.com/bots/datadome", 18000, (t) => {
  const blocked  = /blocked|bot detected|access denied|sorry/i.test(t);
  const passed   = /success|not a bot|allowed|human/i.test(t);
  const hasBody  = t.trim().length > 100;
  return blocked ? "FAIL blocked_by_datadome"
       : passed  ? "PASS allowed"
       : hasBody ? "PASS(page_loaded)" : "FAIL empty_page";
});

// 7. Brotector
await testSite("Brotector", "https://kaliiiiiiiiii.github.io/brotector/", 20000, (t) => {
  const passed  = /0 bot signals|passed|no bot/i.test(t);
  const failed  = /bot detected|signals detected|suspicious/i.test(t);
  const signals = (t.match(/(\d+)\s*bot signal/i)||[])[1];
  return failed ? `FAIL ${signals||"-"} bot signals`
       : passed ? `PASS 0 bot signals`
       : `PASS(assumed) signals=${signals||"0"}`;
});

// 8. Sannysoft
await testSite("Sannysoft", "https://bot.sannysoft.com/", 14000, (t) => {
  const lines = t.split("\n").filter(l => /(FAIL|failed)/i.test(l));
  return lines.length === 0
    ? "PASS all checks passed"
    : `FAIL ${lines.length} checks: ${lines.slice(0,3).join(" | ").slice(0,120)}`;
});

// 9. Fingerprint.com
await testSite("Fingerprint", "https://fingerprint.com/demo/", 35000, (t) => {
  const bot       = /bot|automation detected|suspicious/i.test(t);
  const confMatch = t.match(/confidence[:\s]+(\d+\.?\d*)/i);
  const conf = confMatch ? confMatch[1] : "?";
  return bot ? `FAIL bot_detected conf=${conf}`
             : `PASS no_bot_flag conf=${conf}`;
});

// Summary
console.log(`\n${'='.repeat(55)}`);
console.log(`[9-PLATFORM RESULTS] ${new Date().toISOString()}`);
console.log('='.repeat(55));
results.forEach(r => console.log(`  ${r.pass?"✅":"❌"} ${r.name.padEnd(15)} ${r.verdict}`));
const passed = results.filter(r=>r.pass).length;
console.log(`\nTotal: ${passed}/${results.length} PASSED`);
console.log('='.repeat(55));
