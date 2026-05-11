import { chromium } from "playwright";
import { readFileSync } from "fs";

const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const tsSrc  = readFileSync("/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts","utf8");
const STEALTH_INIT   = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/)   ||[])[1]||"";
const WORKER_STEALTH = (tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/)  ||[])[1]||"";

const PROXY_US   = "socks5://127.0.0.1:10916";  // 205.179.217.31 US/Arisk clean
const PROXY_IT   = "socks5://127.0.0.1:10851";  // 185.49.57.133  Italy/Wiplanet clean

console.log(`STEALTH:${STEALTH_INIT.length} WORKER:${WORKER_STEALTH.length}`);

async function launch(proxy) {
  return chromium.launch({
    headless: false, executablePath: BINARY,
    args: [
      "--no-sandbox","--disable-dev-shm-usage",
      "--disable-blink-features=AutomationControlled",
      "--no-first-run","--no-default-browser-check","--mute-audio","--lang=en-US",
      "--use-gl=angle","--use-angle=swiftshader","--enable-webgl",
      "--window-size=1920,1080",
      `--fingerprint=${Math.floor(Math.random()*0x7fffffff)}`,
      "--fingerprint-platform=linux","--fingerprint-brand=Chrome",
      "--fingerprint-brand-version=144","--fingerprint-hardware-concurrency=8",
      "--timezone=America/Los_Angeles",
      `--proxy-server=${proxy}`,
      "--disable-quic","--proxy-resolves-dns-locally","--disable-non-proxied-udp",
    ],
    ignoreDefaultArgs: ["--enable-automation"],
    env: { ...process.env, DISPLAY: ":99" },
  });
}
async function makeCtx(browser) {
  const ctx = await browser.newContext({
    userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    viewport: { width:1920, height:1040 }, locale: "en-US",
    timezoneId: "America/Los_Angeles", screen: { width:1920, height:1080 },
  });
  await ctx.addInitScript(STEALTH_INIT);
  ctx.on("page", p => p.on("worker", w => w.evaluate(WORKER_STEALTH).catch(()=>{})));
  return ctx;
}

async function run(name, proxy, url, waitMs, fn) {
  const ip = proxy.includes("10916") ? "205.179.217.31(US)" : "185.49.57.133(IT)";
  console.log(`\n${"=".repeat(60)}\n=== ${name}  [${ip}] ===`);
  const browser = await launch(proxy);
  const ctx = await makeCtx(browser);
  const page = await ctx.newPage();
  page.on("crash", ()=>console.log("  ⚠️ crash"));
  const apiHits = [];
  page.on("response", async r=>{
    try {
      if ((r.headers()["content-type"]||"").includes("json")) {
        const j = await r.json().catch(()=>null);
        if (j) apiHits.push({u:r.url().slice(0,100),d:j});
      }
    } catch(e){}
  });
  try {
    await page.goto(url,{timeout:65000,waitUntil:"domcontentloaded"});
    console.log("  loaded:", page.url().slice(0,70));
    await page.waitForTimeout(waitMs);
    const res = await page.evaluate(fn).catch(e=>({err:e.message}));
    if (res.err) { console.log("  eval err:", res.err); }
    else {
      (res.lines||[]).forEach(l=>console.log(" ",l));
      if (res.verdict) console.log(`\n  ▶ VERDICT: ${res.verdict}`);
    }
    const rel = apiHits.filter(x=>/bot|headless|stealth|trust|score|visitor|risk|automat|block/i.test(JSON.stringify(x.d)));
    rel.slice(0,2).forEach(x=>console.log("  API:",x.u,"\n    ",JSON.stringify(x.d).slice(0,300)));
  } catch(e){ console.log("  ❌",e.message.slice(0,200)); }
  await browser.close().catch(()=>{});
}

// ── 1. CreepJS ── 正确地址 github pages
await run("CreepJS", PROXY_US,
  "https://abrahamjuliot.github.io/creepjs/", 38000,
  () => {
    const t = document.body?.innerText || "";
    const grade = [...document.querySelectorAll("*")].find(el=>/^[A-F][+-]?$/.test(el.textContent?.trim()))?.textContent?.trim() || "";
    const likeHL  = t.match(/(\d+\.?\d*)\s*%\s*like.{0,10}headless/i)?.[1] || "?";
    const trustPct= t.match(/trust\D{0,10}(\d+\.?\d*)\s*%/i)?.[1] || "";
    const lines = t.split("\n").map(l=>l.trim()).filter(l=>
      l && /headless|stealth|like|%|grade|bot|worker|creep|trust/i.test(l) && l.length<120
    ).slice(0,30);
    return {lines, verdict:`like-headless=${likeHL}%  grade="${grade}"  trust="${trustPct}%"`};
  }
);

// ── 2. IPHey ── 等渲染完再抓
await run("IPHey", PROXY_US,
  "https://iphey.com/", 35000,
  () => {
    const t = document.body?.innerText || "";
    // IPHey动态渲染：扫描所有文字节点找分数/状态
    const scoreMatch = t.match(/(?:trust|score)[^\d]*(\d+(?:\.\d+)?)\s*(?:\/\s*100|%)?/i);
    const verdictMatch = t.match(/\b(Trustworthy|Genuine|Human|Bot|Suspicious|Good|Bad|Risk|Clean)\b/i);
    // 取所有包含数字+关键词的行
    const lines = t.split("\n").map(l=>l.trim()).filter(l=>
      l && l.length<150 && (
        /\d+\s*\/\s*100|\d+%|trustworthy|genuine|human|bot|suspicious|risk|clean|good|bad|score|trust|flag/i.test(l)
      )
    ).slice(0,20);
    // DOM选择器全扫
    const allText = [...document.querySelectorAll("*")]
      .filter(el=>el.children.length===0 && el.innerText?.trim())
      .map(el=>({tag:el.tagName, cls:el.className?.toString?.()?.slice(0,40)||"", txt:el.innerText.trim().slice(0,80)}))
      .filter(x=>/score|trust|grade|result|verdict|human|bot|suspicious|genuine|trustworthy/i.test(x.cls+x.txt))
      .slice(0,15);
    return {lines, verdict:`score="${scoreMatch?.[1]||"?"}"  verdict="${verdictMatch?.[1]||"?"}"  domHits=${JSON.stringify(allText)}`};
  }
);

// ── 3. PixelScan ── 精确等结果
await run("PixelScan", PROXY_US,
  "https://pixelscan.net/fingerprint-check", 28000,
  () => {
    const t = document.body?.innerText || "";
    // PixelScan用table/list展示结果
    const rows = [...document.querySelectorAll("tr,li,[class*=item],[class*=row],[class*=check]")].map(el=>{
      const txt = el.innerText?.trim();
      return txt && txt.length<200 ? txt : null;
    }).filter(Boolean);
    const lines = rows.length > 0 ? rows.slice(0,25) : 
      t.split("\n").map(l=>l.trim()).filter(l=>l&&/consistent|inconsistent|normal|suspicious|pass|fail|bot|score|detect|risk|ok/i.test(l)&&l.length<150).slice(0,20);
    const verdict = t.match(/(?:consistent|inconsistent|suspicious|normal|bot detected|no bot)/i)?.[0] || "?";
    return {lines, verdict};
  }
);

console.log("\n"+"=".repeat(60)+"\n=== 三平台补测完成 ===");
