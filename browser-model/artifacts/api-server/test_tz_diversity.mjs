/**
 * test_tz_diversity.mjs
 * 测试 5 个代理端口 × 时区的指纹一致性
 * 每个端口只跑 IPHey (最能体现时区/IP 是否匹配)
 * 位置: /root/Toolkit/browser-model/artifacts/api-server/test_tz_diversity.mjs
 */
import { chromium } from "playwright";
import { readFileSync } from "fs";

const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const SRC    = "/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts";
const tsSrc  = readFileSync(SRC, "utf8");

const STEALTH_INIT     = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/)  ||[])[1]||"";
const BOOT_SUFFIX      = (tsSrc.match(/const _WORKER_BOOT_SUFFIX = `([\s\S]*?)`;/) ||[])[1]||"";
const LATE_FIX_PATCHES = (tsSrc.match(/const LATE_FIX_PATCHES = `([\s\S]*?)`;/)    ||[])[1]||"";
const WORKER_STEALTH   = (tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/)||[])[1]||"";
const STEALTH_FULL     = STEALTH_INIT + (BOOT_SUFFIX||"");

// 端口 → 时区映射 (来自 renderer.ts _PROXY_PORT_TZ)
const PORT_TZ = {
  "10857": "Asia/Hong_Kong",
  "10859": "Europe/Amsterdam",
  "10853": "America/Los_Angeles",
  "10855": "Europe/London",
  "10851": "America/New_York",
};

async function mkBrowser(port, tz) {
  const PROXY = `socks5://127.0.0.1:${port}`;
  const b = await chromium.launch({
    headless: false, executablePath: BINARY,
    args: [
      "--no-sandbox","--disable-dev-shm-usage",
      "--disable-blink-features=AutomationControlled",
      "--no-first-run","--no-default-browser-check","--mute-audio",
      "--lang=en-US","--use-gl=angle","--use-angle=swiftshader","--enable-webgl",
      "--window-size=1920,1080",
      `--fingerprint=${Math.floor(Math.random()*0x7fffffff)}`,
      "--fingerprint-platform=linux","--fingerprint-brand=Chrome",
      "--fingerprint-brand-version=144","--fingerprint-hardware-concurrency=8",
      `--timezone=${tz}`,
      `--proxy-server=${PROXY}`,"--disable-quic",
      "--proxy-resolves-dns-locally","--disable-non-proxied-udp",
    ],
    ignoreDefaultArgs: ["--enable-automation"],
    env: { ...process.env, DISPLAY: ":99", BROWSER_PROXY: PROXY },
  });
  const ctx = await b.newContext({
    userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    viewport: { width: 1920, height: 1040 }, screen: { width: 1920, height: 1080 },
    locale: "en-US", timezoneId: tz, colorScheme: "dark", ignoreHTTPSErrors: true,
  });
  await ctx.addInitScript(STEALTH_FULL);
  if (LATE_FIX_PATCHES) await ctx.addInitScript(LATE_FIX_PATCHES);
  ctx.on("page", p => p.on("worker", w => w.evaluate(WORKER_STEALTH).catch(()=>{})));
  return { b, ctx };
}

async function testIPHey(port, tz) {
  const label = `port=${port} tz=${tz}`;
  console.log(`\n[${new Date().toISOString().slice(11,19)}] === IPHey ${label} ===`);
  let b, ctx;
  try {
    ({ b, ctx } = await mkBrowser(port, tz));
    const page = await ctx.newPage();
    await page.goto("https://iphey.com/", { timeout: 90000, waitUntil: "domcontentloaded" });
    // 轮询最多 90s
    let text = "";
    for (let i = 0; i < 18; i++) {
      await page.waitForTimeout(5000);
      text = await page.evaluate(() => document.body?.innerText||"").catch(()=>"");
      if (!text.includes("Temporary value")) break;
    }
    await page.screenshot({ path: `/tmp/tz_${port}.png` });
    await b.close();
    const resultSection = text.split("How is this determined?")[0] || text;
    const trustworthy = /trustworthy|genuine/i.test(resultSection);
    const suspicious  = /suspicious/i.test(resultSection);
    const loading     = text.includes("Temporary value");
    const location    = (text.match(/LOCATION\n([^\n]+)/)||[])[1]?.trim() || "?";
    const ip          = (text.match(/IP ADDRESS\n([^\n]+)/)||[])[1]?.trim() || "?";
    let verdict, pass;
    if (loading)          { verdict = "FAIL still_loading"; pass = false; }
    else if (trustworthy) { verdict = `PASS Trustworthy (loc=${location} ip=${ip})`; pass = true; }
    else if (suspicious)  { verdict = `FAIL Suspicious (loc=${location} ip=${ip})`; pass = false; }
    else                  { verdict = `PASS? no_clear_result (loc=${location})`; pass = true; }
    console.log(`  ${pass?"✅":"❌"} ${label} → ${verdict}`);
    return { port, tz, pass, verdict };
  } catch(e) {
    try { b && await b.close(); } catch(_){}
    console.log(`  ❌ ${label} → ERROR: ${e.message?.slice(0,80)}`);
    return { port, tz, pass: false, verdict: `ERROR: ${e.message?.slice(0,60)}` };
  }
}

const results = [];
for (const [port, tz] of Object.entries(PORT_TZ)) {
  const r = await testIPHey(port, tz);
  results.push(r);
}

console.log(`\n${"=".repeat(60)}`);
console.log(`[TZ DIVERSITY RESULTS] ${new Date().toISOString()}`);
console.log("=".repeat(60));
results.forEach(r =>
  console.log(`  ${r.pass?"✅":"❌"} port=${r.port} ${r.tz.padEnd(22)} ${r.verdict}`)
);
const passed = results.filter(r=>r.pass).length;
console.log(`\n  Total: ${passed}/${results.length} PASSED`);
console.log("=".repeat(60));
