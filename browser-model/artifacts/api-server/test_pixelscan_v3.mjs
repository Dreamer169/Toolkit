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

console.log(`STEALTH:${STEALTH_FULL.length} LATE_FIX:${LATE_FIX_PATCHES.length} WORKER:${WORKER_STEALTH.length}`);
if (!LATE_FIX_PATCHES.length) { console.error("LATE_FIX_PATCHES missing!"); process.exit(1); }

const SEED = Math.floor(Math.random() * 0x7fffffff);
const browser = await chromium.launch({
  headless: false, executablePath: BINARY,
  args: [
    "--no-sandbox","--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--no-first-run","--no-default-browser-check","--mute-audio",
    "--lang=en-US","--use-gl=angle","--use-angle=swiftshader",
    "--enable-webgl","--window-size=1920,1080",
    `--fingerprint=${SEED}`,
    "--fingerprint-platform=linux","--fingerprint-brand=Chrome",
    "--fingerprint-brand-version=144","--fingerprint-hardware-concurrency=4",
    "--timezone=America/Los_Angeles",
    `--proxy-server=${PROXY}`,"--disable-quic",
    "--proxy-resolves-dns-locally","--disable-non-proxied-udp",
  ],
  ignoreDefaultArgs: ["--enable-automation"],
  env: { ...process.env, DISPLAY: ":99" },
});

const ctx = await browser.newContext({
  userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
  viewport: {width:1920, height:1040}, screen:{width:1920, height:1080},
  locale:"en-US", timezoneId:"America/Los_Angeles",
  colorScheme:"dark", ignoreHTTPSErrors:true,
});

await ctx.addInitScript(STEALTH_FULL);
await ctx.addInitScript(LATE_FIX_PATCHES);
ctx.on("page", p => p.on("worker", w => w.evaluate(WORKER_STEALTH).catch(()=>{})));

const page = await ctx.newPage();
const apiResponses = [];
page.on("response", async r => {
  try {
    const ct = r.headers()["content-type"]||"";
    if (!ct.includes("json")) return;
    const j = await r.json().catch(()=>null);
    if (j) apiResponses.push({ url: r.url().slice(-100), data: j });
  } catch(_) {}
});

console.log("Navigating to pixelscan.net/bot-check...");
await page.goto("https://pixelscan.net/bot-check", {timeout:90000, waitUntil:"domcontentloaded"});
console.log("Waiting 35s for full scan...");
await page.waitForTimeout(35000);

const result = await page.evaluate(async () => {
  const toggles = document.querySelectorAll(
    "mat-expansion-panel-header, [class*='accordion'] button, [class*='toggle'], [class*='expand']"
  );
  toggles.forEach(el => { try { el.click(); } catch(_){} });
  await new Promise(r => setTimeout(r, 1500));

  const rows = [];
  document.querySelectorAll("mat-expansion-panel, [class*='accordion__item'], [class*='check-item']").forEach(el => {
    const t = el.innerText.trim().replace(/\s+/g," ").slice(0,250);
    if (t.length > 5) rows.push(t);
  });

  const badges = [];
  document.querySelectorAll("[class*='status'], [class*='badge'], [class*='chip'], [class*='alert'], [class*='result']").forEach(el => {
    const t = el.textContent.trim();
    const par = el.closest("mat-expansion-panel,[class*='item'],[class*='row'],[class*='check'],[class*='panel']");
    if (t && t.length < 50) badges.push({ status: t, ctx: par ? par.innerText.replace(/\s+/g," ").trim().slice(0,150) : "" });
  });

  const bodyText = document.body.innerText;
  return { rows, badges, bodyText: bodyText.slice(0,12000) };
});

console.log("\n=== PIXELSCAN BOT-CHECK RESULTS (with LATE_FIX_PATCHES) ===\n");

if (result.rows.length > 0) {
  console.log("--- Expanded panel rows ---");
  result.rows.forEach(r => {
    const f = /detected|bot|fail/i.test(r)?"[FAIL]":/clear|pass/i.test(r)?"[ok  ]":"[    ]";
    console.log(f, r.slice(0,160));
  });
}

if (result.badges.length > 0) {
  console.log("\n--- Status badges ---");
  const seen = new Set();
  result.badges.forEach(b => {
    const key = b.status+"|"+b.ctx.slice(0,40);
    if (seen.has(key)) return; seen.add(key);
    const f = /detected|bot|fail/i.test(b.status)?"[FAIL]":/clear|pass/i.test(b.status)?"[ok  ]":"[    ]";
    console.log(f, b.status.padEnd(15), "|", b.ctx.slice(0,120));
  });
}

if (apiResponses.length > 0) {
  console.log("\n--- API JSON ---");
  apiResponses.forEach(r => console.log("URL:", r.url, "\n   ", JSON.stringify(r.data).slice(0,600)));
}

console.log("\n--- Raw page text ---");
const lines = result.bodyText.split("\n").map(l=>l.trim()).filter(l=>l.length>2);
lines.forEach(l => {
  const f = /detected/i.test(l)?"[FAIL]":/clear/i.test(l)?"[ok  ]":"      ";
  console.log(f, l.slice(0,150));
});

await browser.close();
console.log("\n=== DONE ===");
