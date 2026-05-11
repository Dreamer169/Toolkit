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

// === IPHey: poll until "Temporary value" is gone, up to 90s ===
const ts1 = new Date().toISOString().slice(11,19);
console.log(`\n[${ts1}] === IPHey (TZ=${TZ}, polling for real result) ===`);
{
  const {b, ctx} = await mkBrowser();
  const page = await ctx.newPage();
  const apiResponses = [];
  page.on("response", async r => {
    try {
      const ct = r.headers()["content-type"]||"";
      if (ct.includes("json") && /iphey/i.test(r.url())) {
        const j = await r.json().catch(()=>null);
        if (j) apiResponses.push({url:r.url().slice(0,100), d:j});
      }
    } catch(e){}
  });
  await page.goto("https://iphey.com/", {timeout:90000, waitUntil:"domcontentloaded"});
  // Poll until "Temporary value" disappears (result loaded) or timeout 90s
  let text = "";
  for (let i = 0; i < 18; i++) {
    await page.waitForTimeout(5000);
    text = await page.evaluate(() => document.body?.innerText||"");
    if (!text.includes("Temporary value")) {
      console.log(`  [poll] result loaded after ${(i+1)*5}s`);
      break;
    }
    console.log(`  [poll] ${(i+1)*5}s: still loading...`);
  }
  await page.screenshot({path:"/tmp/iphey_v2.png"});
  await b.close();

  // Extract the actual verdict (appears before "How is this determined?")
  const beforeHow = text.split("How is this determined?")[0] || text;
  const resultLines = beforeHow.split("\n").map(l=>l.trim()).filter(l=>l.length>1);
  console.log("--- Result section ---");
  resultLines.slice(0,15).forEach(l => console.log(" ", l.slice(0,120)));

  const trustworthy = /trustworthy|genuine/i.test(beforeHow);
  const suspicious  = /suspicious/i.test(beforeHow);
  const hasTemp = text.includes("Temporary value");
  console.log(`--- trustworthy=${trustworthy} suspicious=${suspicious} stillLoading=${hasTemp}`);

  // API responses
  apiResponses.forEach(r => console.log(" API:", r.url, JSON.stringify(r.d).slice(0,300)));
}

// === Fingerprint.com: wait for visitor ID + check API for botDetected ===
const ts2 = new Date().toISOString().slice(11,19);
console.log(`\n[${ts2}] === Fingerprint.com (polling for visitor ID) ===`);
{
  const {b, ctx} = await mkBrowser();
  const page = await ctx.newPage();
  let fpApiResult = null;
  page.on("response", async r => {
    try {
      const url = r.url();
      if (/api\.fpjs\.io|fpcdn\.io|fpjscdn|visitor-identification/i.test(url)) {
        const j = await r.json().catch(()=>null);
        if (j) {
          console.log(" FP API:", url.slice(0,80));
          console.log(" DATA:", JSON.stringify(j).slice(0,600));
          fpApiResult = j;
        }
      }
    } catch(e){}
  });
  await page.goto("https://fingerprint.com/demo/", {timeout:90000, waitUntil:"domcontentloaded"});
  // Poll until visitor ID loads (not "Loading visitor ID")
  let text = "";
  for (let i = 0; i < 12; i++) {
    await page.waitForTimeout(5000);
    text = await page.evaluate(() => document.body?.innerText||"");
    const loading = /Loading visitor ID/i.test(text);
    if (!loading && fpApiResult) {
      console.log(`  [poll] FP result loaded after ${(i+1)*5}s`);
      break;
    }
    console.log(`  [poll] ${(i+1)*5}s: loading=${loading} apiResult=${!!fpApiResult}`);
  }
  await page.screenshot({path:"/tmp/fp_v2.png"});
  await b.close();

  // Check API result for bot detection
  if (fpApiResult) {
    const botD = fpApiResult?.products?.botd?.data;
    const botDetected = botD?.bot?.result;
    const requestId = fpApiResult?.requestId;
    console.log(`  botd.result: ${botDetected} requestId: ${requestId}`);
    if (botDetected === "notDetected") console.log("  ✅ PASS not a bot");
    else console.log(`  ❌ FAIL botd=${botDetected}`);
  } else {
    // Fallback: look for visitor ID in page text
    const visitorId = text.match(/[0-9a-f]{32}/i)?.[0];
    const loadFail = /Loading visitor ID/i.test(text);
    console.log(`  visitorId=${visitorId||"none"} loadFail=${loadFail}`);
  }
}

console.log("\n=== DIAG V2 DONE ===");
