// Fingerprint real test — uses our fingerprint-chromium binary + proxy
// Run: DISPLAY=:99 node /tmp/fp_test.mjs
import { chromium } from "playwright";
import fs from "fs";

const FP_BIN = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const STD_BIN = "/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome";
const exe = fs.existsSync(FP_BIN) ? FP_BIN : STD_BIN;
const PROXY = "socks5://127.0.0.1:10854";
const UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36";

const args = [
  "--no-sandbox","--disable-dev-shm-usage",
  "--disable-blink-features=AutomationControlled",
  "--disable-features=AutomationControlled",
  "--no-first-run","--no-default-browser-check",
  "--lang=en-US","--accept-lang=en-US,en;q=0.9",
  "--use-fake-ui-for-media-stream","--use-fake-device-for-media-stream",
  "--password-store=basic","--use-mock-keychain",
  "--use-gl=angle","--use-angle=swiftshader","--enable-webgl",
  `--proxy-server=${PROXY}`,
  "--disable-quic",
  "--proxy-resolves-dns-locally",
  "--enable-features=AsyncDns,DnsOverHttps",
  "--dns-over-https-mode=secure",
  "--dns-over-https-templates=https://1.1.1.1/dns-query,https://dns.google/dns-query",
  "--window-size=1920,1080",
];
if (exe === FP_BIN) {
  args.push(
    `--fingerprint=${Math.floor(Math.random()*0x7fffffff)}`,
    "--fingerprint-platform=linux",
    
    "--fingerprint-brand=Chrome",
    "--fingerprint-brand-version=144",
    "--fingerprint-hardware-concurrency=8",
    "--disable-spoofing=gpu",
    "--timezone=America/Los_Angeles",
    "--disable-non-proxied-udp",
  );
}

console.log("=== Fingerprint Test ===");
console.log("Binary:", exe === FP_BIN ? "fingerprint-chromium 144 ✓" : "playwright chromium (fallback)");
console.log("Proxy:", PROXY);
console.log("");

const browser = await chromium.launch({
  headless: false,
  executablePath: exe,
  args,
  ignoreDefaultArgs: ["--enable-automation"],
  env: { ...process.env, LANG:"en_US.UTF-8", LC_ALL:"en_US.UTF-8", LANGUAGE:"en_US:en", DISPLAY: process.env.DISPLAY||":99" },
});

const ctx = await browser.newContext({
  userAgent: UA,
  viewport: { width:1920, height:1040 },
  locale: "en-US",
  timezoneId: "America/Los_Angeles",
  screen: { width:1920, height:1080 },
  deviceScaleFactor: 1,
});

// ── Quick JS env snapshot ──────────────────────────────────────────────────
const page0 = await ctx.newPage();
await page0.goto("about:blank");
const env = await page0.evaluate(() => ({
  ua:            navigator.userAgent,
  lang:          navigator.language,
  langs:         navigator.languages,
  platform:      navigator.platform,
  hwConcurrency: navigator.hardwareConcurrency,
  mem:           navigator.deviceMemory,
  pdfViewer:     navigator.pdfViewerEnabled,
  webdriver:     navigator.webdriver,
  connection:    navigator.connection ? { type: navigator.connection.type, effectiveType: navigator.connection.effectiveType } : null,
  uaData:        navigator.userAgentData ? navigator.userAgentData.brands : null,
  chrome:        !!window.chrome,
  chromeKeys:    Object.keys(window.chrome||{}).join(","),
  chromeRuntime: !!(window.chrome && window.chrome.runtime),
  speechVoices:  typeof speechSynthesis!=="undefined" ? speechSynthesis.getVoices().length : -1,
  outerW:        outerWidth,
  outerH:        outerHeight,
}));
console.log("── JS environment snapshot ─────────────────────────────────");
for (const [k,v] of Object.entries(env)) console.log(`  ${k}:`, JSON.stringify(v));
await page0.close();

// ── BrowserScan ────────────────────────────────────────────────────────────
console.log("\n── BrowserScan (full-scan) ─────────────────────────────────");
const bsPage = await ctx.newPage();
try {
  await bsPage.goto("https://www.browserscan.net/bot-detection", { timeout: 60000, waitUntil:"domcontentloaded" });
  await bsPage.waitForTimeout(8000);
  const bsText = await bsPage.evaluate(() => {
    const rows = [...document.querySelectorAll("table tr, .result-row, [class*='result'], [class*='check']")];
    return rows.map(r => r.innerText?.trim()).filter(Boolean).join("\n");
  });
  if (bsText) { console.log(bsText.slice(0,2000)); }
  else {
    const body = await bsPage.evaluate(() => document.body?.innerText?.slice(0,3000));
    console.log("(raw body):\n", body);
  }
} catch(e) { console.log("BrowserScan error:", e.message); }
await bsPage.close();

// ── PixelScan ──────────────────────────────────────────────────────────────
console.log("\n── PixelScan ────────────────────────────────────────────────");
const psPage = await ctx.newPage();
try {
  await psPage.goto("https://pixelscan.net", { timeout: 60000, waitUntil:"domcontentloaded" });
  await psPage.waitForTimeout(10000);
  const psText = await psPage.evaluate(() => {
    const items = [...document.querySelectorAll("[class*='result'], [class*='check'], [class*='pass'], [class*='fail'], [class*='status'], .item")];
    return items.map(el => el.innerText?.trim()).filter(Boolean).join("\n") || document.body?.innerText?.slice(0,3000);
  });
  console.log(psText.slice(0,2000));
} catch(e) { console.log("PixelScan error:", e.message); }
await psPage.close();

// ── CreepJS ────────────────────────────────────────────────────────────────
console.log("\n── CreepJS ──────────────────────────────────────────────────");
const cjPage = await ctx.newPage();
try {
  await cjPage.goto("https://abrahamjuliot.github.io/creepjs/", { timeout: 90000, waitUntil:"domcontentloaded" });
  await cjPage.waitForTimeout(15000);
  const cjScore = await cjPage.evaluate(() => {
    const pct = [...document.querySelectorAll("*")].find(el =>
      el.innerText && /^\d+(\.\d+)?%$/.test(el.innerText.trim()) &&
      parseFloat(el.innerText) > 30
    );
    const grade = [...document.querySelectorAll("[class*='grade'], [class*='score'], [class*='trust']")]
      .map(el => el.innerText?.trim()).filter(Boolean);
    return { pct: pct?.innerText?.trim(), grade, summary: document.body?.innerText?.slice(0,1500) };
  });
  if (cjScore.pct) console.log("Score:", cjScore.pct);
  if (cjScore.grade.length) console.log("Grade:", cjScore.grade);
  console.log("\n--- summary ---");
  console.log(cjScore.summary?.slice(0,1500));
} catch(e) { console.log("CreepJS error:", e.message); }
await cjPage.close();

await browser.close();
console.log("\n=== Test complete ===");
