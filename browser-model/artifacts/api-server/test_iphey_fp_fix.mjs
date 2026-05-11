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

// === IPHey ===
const ts1 = new Date().toISOString().slice(11,19);
console.log(`\n[${ts1}] === IPHey (TZ=${TZ}) ===`);
{
  const {b, ctx} = await mkBrowser();
  const page = await ctx.newPage();
  await page.goto("https://iphey.com/", {timeout:90000, waitUntil:"domcontentloaded"});
  await page.waitForTimeout(28000);
  const text = await page.evaluate(() => document.body?.innerText||"");
  await page.screenshot({path:"/tmp/iphey_fix.png"});
  await b.close();
  const lines = text.split("\n").map(l=>l.trim()).filter(l=>l.length>1);
  lines.slice(0, 60).forEach(l => console.log(" ", l.slice(0,120)));
  const trustworthy = /trustworthy|genuine|clean/i.test(text);
  const suspicious  = /suspicious|bot detected|flagged/i.test(text);
  console.log("--- trustworthy:", trustworthy, "| suspicious:", suspicious);
}

// === Fingerprint.com ===
const ts2 = new Date().toISOString().slice(11,19);
console.log(`\n[${ts2}] === Fingerprint.com ===`);
{
  const {b, ctx} = await mkBrowser();
  const page = await ctx.newPage();
  const apiData = [];
  page.on("response", async r => {
    try {
      const url = r.url();
      if (/api\.fpjs\.io|fpcdn|fpjscdn|visitor-identification/i.test(url)) {
        const ct = r.headers()["content-type"]||"";
        if (ct.includes("json")) {
          const j = await r.json().catch(()=>null);
          if (j) console.log(" API:", url.slice(0,80), JSON.stringify(j).slice(0,500));
        }
      }
    } catch(e){}
  });
  await page.goto("https://fingerprint.com/demo/", {timeout:90000, waitUntil:"domcontentloaded"});
  await page.waitForTimeout(38000);
  const text = await page.evaluate(() => document.body?.innerText||"");
  await page.screenshot({path:"/tmp/fp_fix.png"});
  await b.close();
  const lines = text.split("\n").map(l=>l.trim()).filter(l=>l.length>2);
  const relevant = lines.filter(l=>/bot|automat|suspect|human|visitor|confidence|score|result|identify|risk/i.test(l));
  if (relevant.length) relevant.slice(0,25).forEach(l=>console.log(" ",l.slice(0,120)));
  else { console.log("(no relevant lines - first 40:)"); lines.slice(0,40).forEach(l=>console.log(" ",l.slice(0,100))); }
}

console.log("\n=== DIAG DONE ===");
