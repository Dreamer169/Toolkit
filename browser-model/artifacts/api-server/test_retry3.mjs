import { chromium } from "playwright";
import { readFileSync } from "fs";

const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const tsSrc  = readFileSync("/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts","utf8");
const STEALTH_INIT   = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/)   ||[])[1]||"";
const WORKER_STEALTH = (tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/)  ||[])[1]||"";

// 用最干净的IP逐平台测试
const PROXIES = {
  italy:   "socks5://127.0.0.1:10851",  // 185.49.57.133 Italy Wiplanet, hosting=false proxy=false
  turkey:  "socks5://127.0.0.1:10853",  // 5.180.32.18   Turkey, hosting=false proxy=false
  hk_hkt:  "socks5://127.0.0.1:10854",  // 112.120.48.16  HK HKT, hosting=false proxy=false
};

async function launch(proxy) {
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
      `--proxy-server=${proxy}`,"--disable-quic","--proxy-resolves-dns-locally","--disable-non-proxied-udp"],
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

async function testSite(name, proxy, url, waitMs, extractFn) {
  const proxyIP = {
    "socks5://127.0.0.1:10851":"185.49.57.133(意大利)",
    "socks5://127.0.0.1:10853":"5.180.32.18(土耳其)",
    "socks5://127.0.0.1:10854":"112.120.48.16(香港HKT)",
  }[proxy]||proxy;
  console.log(`\n${"=".repeat(60)}`);
  console.log(`=== ${name} [proxy: ${proxyIP}] ===`);
  const browser = await launch(proxy);
  const ctx = await makeCtx(browser);
  const page = await ctx.newPage();
  page.on("crash", ()=>console.log("  ⚠️  page crashed"));
  try {
    await page.goto(url,{timeout:60000,waitUntil:"domcontentloaded"});
    console.log(`  页面已加载: ${page.url().slice(0,80)}`);
    await page.waitForTimeout(waitMs);
    const result = await page.evaluate(extractFn).catch(e=>({err:e.message}));
    if (result.err) console.log("  eval error:", result.err);
    else {
      (result.lines||[]).forEach(l=>console.log("  ",l));
      if(result.verdict) console.log("  ▶ VERDICT:",result.verdict);
    }
  } catch(e) { console.log("  ❌ Error:",e.message.slice(0,200)); }
  await browser.close().catch(()=>{});
}

// === CreepJS - 用意大利IP ===
await testSite("CreepJS", PROXIES.italy, "https://creepjs.com/", 30000, () => {
  const t = document.body?.innerText||"";
  const pct = t.match(/(\d+)\s*%\s*like.{0,5}headless/i)?.[1]||"未找到";
  const grade = document.querySelector("[class*=grade]")?.textContent?.trim()||"";
  const lines = t.split("\n").filter(l=>l.trim()&&/headless|stealth|like|%|grade|bot/i.test(l)).slice(0,20);
  return {lines, verdict:`like-headless=${pct}%  grade=${grade}`};
});

// === Cloudflare - 用土耳其IP (最干净，不在CF灰名单) ===
await testSite("Cloudflare(nowsecure.nl)", PROXIES.turkey, "https://nowsecure.nl/", 28000, () => {
  const t = document.body?.innerText||"";
  const url = window.location.href;
  const challenged = /challenge|checking|just a moment|cloudflare/i.test(t);
  const lines = [
    `最终URL: ${url.slice(0,80)}`,
    `页面文本(前150字): ${t.replace(/\s+/g," ").slice(0,150)}`,
    challenged ? "❌ Cloudflare Challenge未通过" : "✅ 通过Cloudflare Challenge",
  ];
  return {lines};
});

// === Datadome - 用意大利IP ===
await testSite("Datadome", PROXIES.italy, "https://antoinevastel.com/bots/datadome", 18000, () => {
  const t = document.body?.innerText||"";
  const url = window.location.href;
  const blocked = /blocked|captcha|datadome/i.test(url)||/detected as bot/i.test(t);
  const lines = t.split("\n").filter(l=>l.trim()).slice(0,10);
  return {lines, verdict: blocked ? "❌ BLOCKED" : "✅ NOT BLOCKED  url="+url.slice(0,60)};
});

// === 同时补测: IPHey + PixelScan 提取器修复 ===
await testSite("IPHey(精确提取)", PROXIES.italy, "https://iphey.com/", 25000, () => {
  // 尝试找评分元素
  const scoreEl = document.querySelector(".trust-score,.score-value,.rating-value,[class*=score],[class*=trust],[class*=rating]");
  const statusEl = document.querySelector(".status,.verdict,.result,[class*=status],[class*=verdict],[class*=result]");
  const t = document.body?.innerText||"";
  const score = scoreEl?.innerText?.trim()||"";
  const status = statusEl?.innerText?.trim()||"";
  // 按关键词找行
  const lines = t.split("\n").map(l=>l.trim()).filter(l=>l&&(
    /\d+\/\d+|\d+%|trustworthy|genuine|suspicious|good|bad|human|bot|clean|risk|\bscore\b/i.test(l)
  )&&l.length<100).slice(0,15);
  return {lines, verdict:`scoreEl="${score}" statusEl="${status}"`};
});

await testSite("BrowserScan(精确提取)", PROXIES.italy, "https://www.browserscan.net/bot-detection", 25000, () => {
  // 找关键检测结果
  const resultEl = document.querySelector(".result,.detection-result,[class*=result],[class*=score],[class*=verdict]");
  const botEl = document.querySelector("[class*=bot],[class*=human],[class*=detect]");
  const t = document.body?.innerText||"";
  const lines = t.split("\n").map(l=>l.trim()).filter(l=>l&&(
    /passed|failed|human|bot|score|webdriver|headless|\byes\b|\bno\b|detected|clean|safe|risk/i.test(l)
  )&&l.length<120).slice(0,20);
  const verdict = resultEl?.innerText?.trim()||botEl?.innerText?.trim()||"";
  return {lines, verdict: verdict.slice(0,100)};
});

console.log("\n" + "=".repeat(60));
console.log("=== 重测完成 ===");
