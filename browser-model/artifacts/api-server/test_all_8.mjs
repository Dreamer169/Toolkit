import { chromium } from "playwright";
import { readFileSync } from "fs";

const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const PROXY  = "socks5://127.0.0.1:10857";
const tsSrc  = readFileSync("/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts","utf8");
const STEALTH_INIT   = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/)   ||[])[1]||"";
const WORKER_STEALTH = (tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/)  ||[])[1]||"";
console.log(`STEALTH_INIT:${STEALTH_INIT.length}  WORKER:${WORKER_STEALTH.length}`);

async function launch() {
  return chromium.launch({
    headless: false, executablePath: BINARY,
    args: ["--no-sandbox","--disable-dev-shm-usage",
      "--disable-blink-features=AutomationControlled","--no-first-run",
      "--no-default-browser-check","--mute-audio","--lang=en-US",
      "--use-gl=angle","--use-angle=swiftshader","--enable-webgl",
      "--window-size=1920,1080",
      `--fingerprint=${Math.floor(Math.random()*0x7fffffff)}`,
      "--fingerprint-platform=linux","--fingerprint-brand=Chrome",
      "--fingerprint-brand-version=144","--fingerprint-hardware-concurrency=8",
      "--timezone=America/Los_Angeles",
      `--proxy-server=${PROXY}`,"--disable-quic","--proxy-resolves-dns-locally","--disable-non-proxied-udp"],
    ignoreDefaultArgs:["--enable-automation"],
    env:{...process.env, DISPLAY:":99"},
  });
}
async function makeCtx(browser) {
  const ctx = await browser.newContext({
    userAgent:"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    viewport:{width:1920,height:1040},locale:"en-US",
    timezoneId:"America/Los_Angeles",screen:{width:1920,height:1080},
  });
  await ctx.addInitScript(STEALTH_INIT);
  ctx.on("page",p=>p.on("worker",w=>w.evaluate(WORKER_STEALTH).catch(()=>{})));
  return ctx;
}

async function testSite(name, url, waitMs, extractFn) {
  console.log(`\n${"=".repeat(55)}`);
  console.log(`=== ${name} ===`);
  const browser = await launch();
  const ctx = await makeCtx(browser);
  const page = await ctx.newPage();
  const apiData = [];
  page.on("response", async r => {
    try {
      const ct = r.headers()["content-type"]||"";
      if (ct.includes("json")) { const j=await r.json().catch(()=>null); if(j) apiData.push({url:r.url().slice(0,120),d:j}); }
    } catch(e){}
  });
  try {
    await page.goto(url,{timeout:60000,waitUntil:"domcontentloaded"});
    await page.waitForTimeout(waitMs);
    const result = await page.evaluate(extractFn).catch(e=>({err:e.message}));
    if (result.err) console.log("  eval error:", result.err);
    else { (result.lines||[]).forEach(l=>console.log(" ",l)); if(result.verdict) console.log("  VERDICT:",result.verdict); }
    const rel = apiData.filter(x=>{const s=JSON.stringify(x.d); return /bot|like|headless|stealth|trust|score|visitor|risk|automati/i.test(s);});
    rel.slice(0,2).forEach(x=>console.log("  API:",x.url.slice(0,80),JSON.stringify(x.d).slice(0,300)));
  } catch(e) { console.log("  Error:",e.message.slice(0,200)); }
  await browser.close();
}

// 1. BrowserScan
await testSite("BrowserScan", "https://www.browserscan.net/bot-detection", 22000, () => {
  const t = document.body?.innerText||"";
  const lines = t.split("\n").filter(l=>l.trim()&&/bot|human|score|risk|pass|fail|webdriver|headless|automation|result/i.test(l)).slice(0,20);
  const verdict = t.match(/(?:Result|Status|Detection)[:\s]+([^\n]{1,50})/i)?.[1]?.trim()||"";
  return {lines, verdict};
});

// 2. CreepJS
await testSite("CreepJS", "https://creepjs.com/", 32000, () => {
  const t = document.body?.innerText||"";
  const lines = t.split("\n").filter(l=>l.trim()&&/headless|stealth|like headless|\d+%|grade|bot|chromium/i.test(l)).slice(0,25);
  const pct = t.match(/(\d+)\s*%\s*like.{0,5}headless/i)?.[1]||"?";
  return {lines, verdict:`like-headless=${pct}%`};
});

// 3. IPHey
await testSite("IPHey", "https://iphey.com/", 22000, () => {
  const t = document.body?.innerText||"";
  const lines = t.split("\n").filter(l=>l.trim()&&/trustworthy|genuine|human|bot|suspicious|score|trust|status|result|risk|clean|good|bad/i.test(l)).slice(0,20);
  const verdict = t.match(/(Trustworthy|Genuine|Human|Bot|Suspicious|Good|Bad)[^\n]*/i)?.[0]?.trim()||"";
  return {lines, verdict};
});

// 4. PixelScan
await testSite("PixelScan", "https://pixelscan.net/fingerprint-check", 22000, () => {
  const t = document.body?.innerText||"";
  const lines = t.split("\n").filter(l=>l.trim()&&/consistent|inconsistent|normal|suspicious|bot|score|pass|fail|detect|clear|risk/i.test(l)).slice(0,25);
  return {lines};
});

// 5. Cloudflare (challenge page)
await testSite("Cloudflare", "https://nowsecure.nl/", 25000, () => {
  const t = document.body?.innerText||"";
  const url = window.location.href;
  const passed = !/challenge|checking|just a moment/i.test(t)&&!url.includes("challenge");
  const lines = [
    `URL: ${url.slice(0,80)}`,
    `Page text (120c): ${t.slice(0,120).replace(/\n/g," ")}`,
    passed ? "✅ Challenge PASSED" : "❌ Still on challenge / blocked",
  ];
  return {lines};
});

// 6. Datadome
await testSite("Datadome", "https://antoinevastel.com/bots/datadome", 18000, () => {
  const t = document.body?.innerText||"";
  const url = window.location.href;
  const blocked = /blocked|captcha|datadome/i.test(url)||/you.{0,20}bot|detected/i.test(t);
  const lines = t.split("\n").filter(l=>l.trim()).slice(0,15);
  lines.unshift(blocked?"❌ BLOCKED":"✅ NOT BLOCKED — URL: "+url.slice(0,80));
  return {lines};
});

// 7. Brotector
await testSite("Brotector", "https://kaliiiiiiiiii.github.io/brotector/", 18000, () => {
  const t = document.body?.innerText||"";
  const lines = t.split("\n").filter(l=>l.trim()&&/bot|human|pass|fail|detect|webdriver|CDP|automation|score|true|false/i.test(l)).slice(0,25);
  const failed = t.split("\n").filter(l=>/FAIL|true/i.test(l)&&!/false/i.test(l)).length;
  return {lines, verdict:`FAIL items: ${failed}`};
});

// 8. Sannysoft
await testSite("Sannysoft", "https://bot.sannysoft.com/", 14000, () => {
  const rows = [...document.querySelectorAll("table tr")];
  const lines = rows.map(r=>{
    const cells = [...r.querySelectorAll("td,th")].map(c=>c.innerText.trim());
    return cells.join(" | ");
  }).filter(l=>l).slice(0,30);
  const fails = rows.filter(r=>r.innerText.includes("FAIL")||r.querySelector(".failed")||r.style.backgroundColor==="red").length;
  return {lines, verdict:`FAIL rows: ${fails}`};
});

// 9. Fingerprint.com
await testSite("Fingerprint.com", "https://fingerprint.com/demo/", 35000, () => {
  const t = document.body?.innerText||"";
  const lines = t.split("\n").filter(l=>l.trim()&&/bot|automation|score|confidence|visitor|suspect|human|identified/i.test(l)).slice(0,20);
  const score = t.match(/confidence[:\s]+([0-9.]+%?)/i)?.[1]||t.match(/([0-9]{2,3}%?)\s+confidence/i)?.[1]||"?";
  return {lines, verdict:`confidence=${score}`};
});

console.log("\n" + "=".repeat(55));
console.log("=== ALL 9 TESTS DONE ===");
