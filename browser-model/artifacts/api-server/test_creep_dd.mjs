import { chromium } from "playwright";
import { readFileSync } from "fs";

const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const tsSrc  = readFileSync("/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts","utf8");
const STEALTH_INIT   = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/)   ||[])[1]||"";
const WORKER_STEALTH = (tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/)  ||[])[1]||"";

// 10916 = 205.179.217.31 美国 Arisk Communications, hosting=false, proxy=false ← 最干净
// 10910 = 154.44.73.141  美国 Cogent, hosting=false, proxy=false ← datadome备用
const PROXY_US1 = "socks5://127.0.0.1:10916";
const PROXY_US2 = "socks5://127.0.0.1:10910";

console.log(`STEALTH_INIT:${STEALTH_INIT.length}  WORKER:${WORKER_STEALTH.length}`);
if (!STEALTH_INIT.length) { console.error("STEALTH_INIT empty!"); process.exit(1); }

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

async function testSite(name, proxy, url, waitMs, extractFn) {
  const proxyLabel = {
    [PROXY_US1]: "205.179.217.31 (US/Arisk)",
    [PROXY_US2]: "154.44.73.141 (US/Cogent)",
  }[proxy] || proxy;
  console.log(`\n${"=".repeat(62)}`);
  console.log(`=== ${name}  [${proxyLabel}] ===`);
  const browser = await launch(proxy);
  const ctx = await makeCtx(browser);
  const page = await ctx.newPage();
  page.on("crash", () => console.log("  ⚠️  page CRASHED"));
  const apiHits = [];
  page.on("response", async r => {
    try {
      const ct = r.headers()["content-type"] || "";
      if (ct.includes("json")) {
        const j = await r.json().catch(() => null);
        if (j) apiHits.push({ u: r.url().slice(0, 100), d: j });
      }
    } catch(e) {}
  });
  try {
    await page.goto(url, { timeout: 60000, waitUntil: "domcontentloaded" });
    console.log(`  页面已加载: ${page.url().slice(0,70)}`);
    await page.waitForTimeout(waitMs);
    const result = await page.evaluate(extractFn).catch(e => ({ err: e.message }));
    if (result.err) {
      console.log("  eval error:", result.err);
    } else {
      (result.lines||[]).forEach(l => console.log("  ", l));
      if (result.verdict) console.log(`\n  ▶ VERDICT: ${result.verdict}`);
    }
    // 打印相关API返回
    const rel = apiHits.filter(x => {
      const s = JSON.stringify(x.d);
      return /bot|headless|stealth|trust|score|visitor|risk|automat|detect|block/i.test(s);
    });
    rel.slice(0,2).forEach(x => console.log("  API:", x.u, JSON.stringify(x.d).slice(0,300)));
  } catch(e) {
    console.log("  ❌ Error:", e.message.slice(0,250));
  }
  await browser.close().catch(() => {});
}

// ─── 1. CreepJS ───
await testSite("CreepJS", PROXY_US1,
  "https://creepjs.com/", 35000,
  () => {
    const t = document.body?.innerText || "";
    // 找百分比、grade、headless标记
    const gradeEl = document.querySelector("[class*=grade],[class*=Grade]");
    const grade = gradeEl?.textContent?.trim() || "";
    const likeHeadless = t.match(/(\d+)\s*%\s*like.{0,8}headless/i)?.[1] || "?";
    const bots = t.match(/(\d+)\s*bot.{0,10}detected/i)?.[1] || "";
    const lines = t.split("\n").map(l=>l.trim()).filter(l =>
      l && /headless|stealth|like|%|grade|bot|chromium|worker|creep/i.test(l)
    ).slice(0, 30);
    return { lines, verdict: `like-headless=${likeHeadless}%  grade="${grade}"  bots="${bots}"` };
  }
);

// ─── 2. Datadome (US IP first) ───
await testSite("Datadome", PROXY_US1,
  "https://antoinevastel.com/bots/datadome", 18000,
  () => {
    const url = window.location.href;
    const t   = document.body?.innerText || "";
    const blocked = /blocked|captcha|datadome\.co/i.test(url) || /detected as a bot/i.test(t);
    const lines = t.split("\n").map(l=>l.trim()).filter(l=>l).slice(0, 12);
    return { lines, verdict: (blocked ? "❌ BLOCKED" : "✅ NOT BLOCKED") + "  url=" + url.slice(0,70) };
  }
);

// ─── 3. Datadome 备用端口 (如果上面仍被拦) ───
await testSite("Datadome(US/Cogent备用)", PROXY_US2,
  "https://antoinevastel.com/bots/datadome", 18000,
  () => {
    const url = window.location.href;
    const t   = document.body?.innerText || "";
    const blocked = /blocked|captcha|datadome\.co/i.test(url) || /detected as a bot/i.test(t);
    const lines = t.split("\n").map(l=>l.trim()).filter(l=>l).slice(0, 12);
    return { lines, verdict: (blocked ? "❌ BLOCKED" : "✅ NOT BLOCKED") + "  url=" + url.slice(0,70) };
  }
);

// ─── 4. IPHey (等待动态渲染完成) ───
await testSite("IPHey", PROXY_US1,
  "https://iphey.com/", 30000,
  () => {
    // 尝试等待结果元素出现后再提取
    const selectors = [
      ".trust-score","[class*=trust]","[class*=score]","[class*=rating]",
      "[class*=result]","[class*=verdict]","[class*=status]","[class*=grade]",
    ];
    const found = selectors.map(s => {
      const el = document.querySelector(s);
      return el ? `${s}: "${el.innerText?.trim()?.slice(0,80)}"` : null;
    }).filter(Boolean);
    const t = document.body?.innerText || "";
    const lines = t.split("\n").map(l=>l.trim()).filter(l => l && l.length < 120 && (
      /\d+\/100|\d+%|trustworthy|genuine|suspicious|good|bad|human|bot|clean|risk|\bscore\b|\btrust\b/i.test(l)
    )).slice(0, 20);
    return { lines, verdict: "selectors found: " + JSON.stringify(found) };
  }
);

console.log("\n" + "=".repeat(62));
console.log("=== 补测完成 ===");
