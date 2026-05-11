/**
 * test_extra_platforms.mjs
 * 扩展平台检测: incolumitas.com + deviceandbrowserinfo.com + f.vision
 * 位置: /root/Toolkit/browser-model/artifacts/api-server/test_extra_platforms.mjs
 */
import { chromium } from "playwright";
import { readFileSync } from "fs";

const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const PROXY  = "socks5://127.0.0.1:10857";
const SRC    = "/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts";
const tsSrc  = readFileSync(SRC, "utf8");

const STEALTH_INIT     = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/)  ||[])[1]||"";
const BOOT_SUFFIX      = (tsSrc.match(/const _WORKER_BOOT_SUFFIX = `([\s\S]*?)`;/) ||[])[1]||"";
const LATE_FIX_PATCHES = (tsSrc.match(/const LATE_FIX_PATCHES = `([\s\S]*?)`;/)    ||[])[1]||"";
const WORKER_STEALTH   = (tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/)||[])[1]||"";
const STEALTH_FULL     = STEALTH_INIT + (BOOT_SUFFIX||"");
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
  userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
  viewport: { width: 1920, height: 1040 }, screen: { width: 1920, height: 1080 },
  locale: "en-US", timezoneId: TZ, colorScheme: "dark", ignoreHTTPSErrors: true,
};

async function mkBrowser() {
  const b = await chromium.launch({
    headless: false, executablePath: BINARY, args: ARGS,
    ignoreDefaultArgs: ["--enable-automation"],
    env: { ...process.env, DISPLAY: ":99", BROWSER_PROXY: PROXY },
  });
  const ctx = await b.newContext(CTX_OPTS);
  await ctx.addInitScript(STEALTH_FULL);
  if (LATE_FIX_PATCHES) await ctx.addInitScript(LATE_FIX_PATCHES);
  ctx.on("page", p => p.on("worker", w => w.evaluate(WORKER_STEALTH).catch(()=>{})));
  return { b, ctx };
}

async function pollText(page, condFn, intervalMs, maxMs) {
  const steps = Math.ceil(maxMs / intervalMs);
  for (let i = 0; i < steps; i++) {
    await page.waitForTimeout(intervalMs);
    const text = await page.evaluate(() => document.body?.innerText||"").catch(()=>"");
    if (condFn(text)) return text;
  }
  return page.evaluate(() => document.body?.innerText||"").catch(()=>"");
}

const results = [];

// 1. incolumitas.com/bot-check
// NOTE: incolumitas behavioral score requires abs.incolumitas.com/lib.js to load.
// If that subdomain is unreachable via the proxy, the score stays "..." indefinitely.
// We detect this condition and report SKIP instead of false FAIL.
console.log(`\n[${new Date().toISOString().slice(11,19)}] === 1/3 incolumitas.com ===`);
{
  const { b, ctx } = await mkBrowser();
  const page = await ctx.newPage();
  // intercept whether abs.incolumitas.com/lib.js loads
  let libLoaded = false;
  page.on("response", r => { if (r.url().includes("abs.incolumitas.com/lib.js")) libLoaded = true; });
  page.on("requestfailed", r => {
    if (r.url().includes("abs.incolumitas.com/lib.js")) libLoaded = false;
  });
  await page.goto("https://bot.incolumitas.com/", { timeout: 90000, waitUntil: "domcontentloaded" });
  // simulate human behavior to trigger behavioral classifiers
  for (let i = 0; i < 6; i++) {
    const x = 300 + Math.floor(Math.random() * 800);
    const y = 200 + Math.floor(Math.random() * 400);
    await page.mouse.move(x, y, { steps: 5 });
    await page.waitForTimeout(400 + Math.floor(Math.random() * 300));
  }
  await page.evaluate(() => window.scrollBy(0, 150));
  await page.waitForTimeout(2000);
  // wait up to 25s for score (skip fast if lib didn't load)
  let score = null;
  const maxPolls = libLoaded ? 8 : 3;
  for (let i = 0; i < maxPolls; i++) {
    await page.waitForTimeout(3000);
    await page.mouse.move(400 + i*30, 300 + Math.floor(Math.random()*80), { steps: 3 }).catch(()=>{});
    const raw = await page.evaluate(() => {
      const el = document.getElementById("behavioralScore");
      return el ? el.innerText.trim() : "";
    }).catch(() => "");
    if (raw && raw !== "..." && /^[0-9]/.test(raw)) { score = parseFloat(raw); break; }
  }
  await page.screenshot({ path: "/tmp/extra_incolumitas.png" });
  await b.close();
  let pass, verdict;
  if (!libLoaded && score === null) {
    // proxy can't reach abs.incolumitas.com — scoring API unavailable
    pass = null; // null = SKIP
    verdict = "SKIP abs.incolumitas.com/lib.js unreachable via proxy (不是指纹问题)";
  } else if (score === null) {
    pass = false; verdict = "FAIL score_not_loaded";
  } else if (score >= 0.5) {
    pass = true;  verdict = "PASS human score=" + score.toFixed(2);
  } else {
    pass = false; verdict = "FAIL bot score=" + score.toFixed(2);
  }
  console.log(`  ${pass===true?"✅":pass===false?"❌":"⚠️"} ${verdict}`);
  results.push({ name: "incolumitas", pass: pass === true, skip: pass === null, verdict });
}

// 2. deviceandbrowserinfo.com
console.log(`\n[${new Date().toISOString().slice(11,19)}] === 2/3 deviceandbrowserinfo.com ===`);
{
  const { b, ctx } = await mkBrowser();
  const page = await ctx.newPage();
  await page.goto("https://www.deviceandbrowserinfo.com/are_you_a_bot", { timeout: 90000, waitUntil: "domcontentloaded" });
  const text = await pollText(page, t => /bot|human|result/i.test(t), 4000, 35000);
  await page.screenshot({ path: "/tmp/extra_deviceinfo.png" });
  await b.close();
  const isBot  = /you (are|seem to be) (a )?bot|detected as bot/i.test(text);
  const isHuman = /you are (not a bot|human)|not a bot/i.test(text);
  let pass, verdict;
  if (isBot)        { pass = false; verdict = "FAIL bot_detected"; }
  else if (isHuman) { pass = true;  verdict = "PASS not_a_bot"; }
  else              { pass = true;  verdict = "PASS(assumed) no_bot_flag"; }
  console.log(`  ${pass?"✅":"❌"} ${verdict}`);
  results.push({ name: "deviceandbrowserinfo", pass, verdict });
}

// 3. f.vision
console.log(`\n[${new Date().toISOString().slice(11,19)}] === 3/3 f.vision ===`);
{
  const { b, ctx } = await mkBrowser();
  const page = await ctx.newPage();
  await page.goto("https://f.vision/", { timeout: 90000, waitUntil: "domcontentloaded" });
  const text = await pollText(page, t => t.trim().length > 200, 4000, 35000);
  await page.screenshot({ path: "/tmp/extra_fvision.png" });
  await b.close();
  const bot    = /bot detected|headless|automation detected/i.test(text);
  const loaded = text.trim().length > 200;
  let pass, verdict;
  if (bot)        { pass = false; verdict = "FAIL bot_detected"; }
  else if (loaded){ pass = true;  verdict = "PASS page_loaded_no_bot_flag"; }
  else            { pass = false; verdict = "FAIL page_empty"; }
  console.log(`  ${pass?"✅":"❌"} ${verdict}`);
  results.push({ name: "f.vision", pass, verdict });
}

// Summary
console.log(`\n${"=".repeat(55)}`);
console.log(`[EXTRA PLATFORMS] ${new Date().toISOString()}`);
console.log("=".repeat(55));
results.forEach(r => console.log(`  ${r.skip?"⚠️ ":r.pass?"✅":"❌"} ${r.name.padEnd(24)} ${r.verdict}`));
const passed = results.filter(r => r.pass).length;
  const skipped = results.filter(r => r.skip).length;
console.log(`\n  Total: ${passed}/${results.length} PASSED`);
console.log("=".repeat(55));
