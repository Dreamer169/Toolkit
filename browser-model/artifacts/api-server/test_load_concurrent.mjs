/**
 * test_load_concurrent.mjs — browser-model 生产负载压测
 * 用法: CONCURRENCY=3 node test_load_concurrent.mjs
 *       CONCURRENCY=5 ROUNDS=2 node test_load_concurrent.mjs
 */
import { chromium } from "playwright";
import { readFileSync } from "fs";
import { execSync } from "child_process";

const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const SRC    = "/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts";
const tsSrc  = readFileSync(SRC, "utf8");
const STEALTH_INIT     = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/)  ||[])[1]||"";
const BOOT_SUFFIX      = (tsSrc.match(/const _WORKER_BOOT_SUFFIX = `([\s\S]*?)`;/) ||[])[1]||"";
const LATE_FIX_PATCHES = (tsSrc.match(/const LATE_FIX_PATCHES = `([\s\S]*?)`;/)    ||[])[1]||"";
const WORKER_STEALTH   = (tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/)||[])[1]||"";
const STEALTH_FULL = STEALTH_INIT + (BOOT_SUFFIX||"");

const CONCURRENCY = parseInt(process.env.CONCURRENCY || "3");
const ROUNDS      = parseInt(process.env.ROUNDS      || "1");
const PROXY_PORTS = [10857, 10910, 10911, 10912, 10916];
const PORT_TZ = {
  10857: "Asia/Hong_Kong",
  10910: "America/Los_Angeles",
  10911: "America/Los_Angeles",
  10912: "America/Los_Angeles",
  10916: "America/Los_Angeles",
};

function getSysStats() {
  try {
    const memRaw  = execSync("free -m", {encoding:"utf8"}).trim().split("\n")[1].trim().split(/\s+/);
    const mem     = memRaw[2]+"/"+memRaw[1]+" MB";
    const chromN  = execSync("ps aux | grep -i chrom | grep -v grep | wc -l", {encoding:"utf8"}).trim();
    const chromKB = execSync(
      "ps aux | grep -i chrom | grep -v grep | awk '{s+=$6}END{printf \"%.0f\",s/1024}'",
      {encoding:"utf8"}
    ).trim();
    const loadAvg = execSync("cat /proc/loadavg", {encoding:"utf8"}).trim().split(" ").slice(0,3).join("/");
    return { mem, chromN, chromMB: (chromKB||"0")+"MB", loadAvg };
  } catch(e) { return { mem:"?", chromN:"?", chromMB:"?", loadAvg:"?" }; }
}

async function runWorker(id, port) {
  const tz    = PORT_TZ[port] || "America/Los_Angeles";
  const PROXY = "socks5://127.0.0.1:"+port;
  const label = "worker#"+id+"(port="+port+")";
  const t0 = Date.now();
  let b;
  try {
    b = await chromium.launch({
      headless: false, executablePath: BINARY,
      args: [
        "--no-sandbox","--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
        "--no-first-run","--no-default-browser-check","--mute-audio",
        "--lang=en-US","--use-gl=angle","--use-angle=swiftshader","--enable-webgl",
        "--window-size=1920,1080",
        "--fingerprint="+Math.floor(Math.random()*0x7fffffff),
        "--fingerprint-platform=linux","--fingerprint-brand=Chrome",
        "--fingerprint-brand-version=144","--fingerprint-hardware-concurrency=8",
        "--timezone="+tz,
        "--proxy-server="+PROXY,"--disable-quic",
        "--proxy-resolves-dns-locally","--disable-non-proxied-udp",
      ],
      ignoreDefaultArgs: ["--enable-automation"],
      env: { ...process.env, DISPLAY: ":99", BROWSER_PROXY: PROXY },
    });
    const ctx = await b.newContext({
      userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
      viewport: { width:1920, height:1040 }, screen: { width:1920, height:1080 },
      locale: "en-US", timezoneId: tz, colorScheme: "dark", ignoreHTTPSErrors: true,
    });
    await ctx.addInitScript(STEALTH_FULL);
    if (LATE_FIX_PATCHES) await ctx.addInitScript(LATE_FIX_PATCHES);
    ctx.on("page", p => p.on("worker", w => w.evaluate(WORKER_STEALTH).catch(()=>{})));

    const tasks = [
      { url:"https://nowsecure.nl/",  re:/protected|verify/i,                name:"CF"       },
      { url:"https://pixelscan.net/", re:/human|bot/i,                        name:"PixelScan"},
      { url:"https://iphey.com/",     re:/trustworthy|suspicious|temporary/i, name:"IPHey"   },
    ];
    const res = [];
    for (const t of tasks) {
      const page = await ctx.newPage();
      await page.goto(t.url, { timeout:120000, waitUntil:"domcontentloaded" });
      let txt = "";
      for (let i=0; i<6; i++) {
        await page.waitForTimeout(5000);
        txt = await page.evaluate(()=>document.body?.innerText||"").catch(()=>"");
        if (t.re.test(txt) && !txt.includes("Temporary value")) break;
      }
      await page.close();
      const pass = !/bot detected|automated|suspicious/i.test(txt);
      res.push({ name:t.name, pass });
    }
    await b.close();
    const elapsed = ((Date.now()-t0)/1000).toFixed(1);
    const ok = res.every(r=>r.pass);
    console.log("  ["+label+"] "+(ok?"✅":"❌")+" "+res.map(r=>(r.pass?"✅":"❌")+r.name).join(" ")+" — "+elapsed+"s");
    return { id, port, pass:ok, elapsed:parseFloat(elapsed), res };
  } catch(e) {
    try { b && await b.close(); } catch(_) {}
    const elapsed = ((Date.now()-t0)/1000).toFixed(1);
    console.log("  ["+label+"] ❌ ERROR: "+e.message?.slice(0,80)+" — "+elapsed+"s");
    return { id, port, pass:false, elapsed:parseFloat(elapsed), error:e.message };
  }
}

console.log("\n"+"=".repeat(65));
console.log("[LOAD TEST] CONCURRENCY="+CONCURRENCY+" ROUNDS="+ROUNDS);
console.log("  Started: "+new Date().toISOString());
console.log("=".repeat(65));

const allResults = [];
for (let round=1; round<=ROUNDS; round++) {
  console.log("\n--- Round "+round+"/"+ROUNDS+" ---");
  const sb = getSysStats();
  console.log("  [sys-before] load="+sb.loadAvg+" mem="+sb.mem+" chromium="+sb.chromN+"procs/"+sb.chromMB);
  const workers = Array.from({length:CONCURRENCY}, (_,i) => runWorker(i+1, PROXY_PORTS[i%PROXY_PORTS.length]));
  const rr = await Promise.all(workers);
  allResults.push(...rr);
  await new Promise(r=>setTimeout(r,3000));
  const sa = getSysStats();
  console.log("  [sys-after]  load="+sa.loadAvg+" mem="+sa.mem+" chromium="+sa.chromN+"procs/"+sa.chromMB);
  const passed = rr.filter(r=>r.pass).length;
  const avg    = (rr.reduce((s,r)=>s+r.elapsed,0)/rr.length).toFixed(1);
  console.log("  Round: "+passed+"/"+CONCURRENCY+" passed  avg="+avg+"s");
}

const tp = allResults.filter(r=>r.pass).length;
const avg = (allResults.reduce((s,r)=>s+r.elapsed,0)/allResults.length).toFixed(1);
const mx  = Math.max(...allResults.map(r=>r.elapsed)).toFixed(1);
const fs  = getSysStats();
const chromMB = parseInt(fs.chromMB)||0;
console.log("\n"+"=".repeat(65));
console.log("[LOAD TEST SUMMARY] "+new Date().toISOString());
console.log("=".repeat(65));
console.log("  Concurrency : "+CONCURRENCY+" workers/round");
console.log("  Rounds      : "+ROUNDS);
console.log("  Total runs  : "+allResults.length);
console.log("  Passed      : "+tp+"/"+allResults.length);
console.log("  Avg elapsed : "+avg+"s  Max: "+mx+"s");
console.log("  Load avg    : "+fs.loadAvg);
console.log("  Memory      : "+fs.mem);
console.log("  Chromium    : "+fs.chromN+" procs / "+fs.chromMB);
console.log(chromMB>2000
  ? "\n  ⚠️  WARNING: Chromium 内存>2GB，建议减少并发或增加 swap"
  : "\n  ✅ 内存正常 ("+fs.chromMB+")");
console.log("=".repeat(65));
