import { chromium } from "playwright";
import { readFileSync } from "fs";

const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const PROXY  = "socks5://127.0.0.1:10854";
// Read from TS source (most accurate, avoids dist regex issues)
const tsSrc = readFileSync("/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts", "utf8");
const m1 = tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/);
const STEALTH_INIT = m1 ? m1[1] : "";
const m2 = tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/);
const WORKER_STEALTH_PATCH = m2 ? m2[1] : "";
console.log("STEALTH_INIT:" + STEALTH_INIT.length + " WORKER:" + WORKER_STEALTH_PATCH.length);
if (!STEALTH_INIT) { console.error("STEALTH_INIT empty!"); process.exit(1); }

const browser = await chromium.launch({
  executablePath: BINARY, headless: true,
  proxy: { server: PROXY },
  args: [
    "--no-sandbox","--disable-setuid-sandbox","--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--fingerprint=" + (Math.random()*0x7fffffff|0),
    "--fingerprint-platform=linux","--fingerprint-brand=Chrome",
    "--fingerprint-brand-version=144","--fingerprint-hardware-concurrency=8",
    "--disable-spoofing=gpu",
    "--lang=en-US","--accept-lang=en-US,en",
    "--timezone=America/Los_Angeles","--window-size=1920,1080",
    "--disable-non-proxied-udp","--proxy-resolves-dns-locally",
  ],
  ignoreDefaultArgs: ["--enable-automation"],
  env: Object.assign({}, process.env, { DISPLAY: ":99" }),
});

async function makeCtx() {
  const ctx = await browser.newContext({
    userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    viewport: { width: 1920, height: 1040 }, locale: "en-US",
    timezoneId: "America/Los_Angeles", screen: { width: 1920, height: 1080 },
  });
  // Worker constructor bootstrap (GeekezBrowser approach):
  // Embed WORKER_STEALTH_PATCH as Blob URL FIRST so it runs before worker script.
  const workerBootSuffix = `;(function(){
  try {
    var _OW = self.Worker;
    var _OSW = self.SharedWorker;
    var _wp = ` + JSON.stringify(WORKER_STEALTH_PATCH) + `;
    function _hookW(Ctor, name) {
      if (typeof Ctor !== "function") return;
      function _H(url, opts) {
        try {
          var absUrl;
          try { absUrl = new URL(String(url), self.location.href).href; } catch(_e) { absUrl = String(url); }
          var code = _wp + ";importScripts(" + JSON.stringify(absUrl) + ");";
          var blob = new Blob([code], {type:"application/javascript"});
          var bu = URL.createObjectURL(blob);
          var w = new Ctor(bu, opts);
          setTimeout(function(){try{URL.revokeObjectURL(bu);}catch(_e){}}, 15000);
          return w;
        } catch(_e) { return new Ctor(url, opts); }
      }
      _H.prototype = Ctor.prototype;
      try { Object.defineProperty(self, name, {value:_H, configurable:true, writable:true}); }
      catch(_e) { try { self[name] = _H; } catch(_e2) {} }
    }
    _hookW(_OW, "Worker");
    _hookW(_OSW, "SharedWorker");
  } catch(_e) {}
})();`;
  const STEALTH_INIT_FULL = STEALTH_INIT + workerBootSuffix;
  await ctx.addInitScript(STEALTH_INIT_FULL);
  // Keep fallback page.on("worker") for any workers not caught by constructor hook
  ctx.on("page", function(p) { p.on("worker", function(w) { w.evaluate(WORKER_STEALTH_PATCH).catch(function(){}); }); });
  return ctx;
}

async function testSite(label, url, extractFn, waitMs) {
  waitMs = waitMs || 12000;
  console.log("\n" + "=".repeat(60));
  console.log("TEST: " + label + "  ->  " + url);
  const ctx = await makeCtx();
  const apiData = [];
  const page = await ctx.newPage();
  page.on("response", async function(r) {
    try {
      const ct = r.headers()["content-type"] || "";
      if (ct.includes("json") && !r.url().match(/\.(js|css|png|woff|ico)/)) {
        const j = await r.json().catch(function(){ return null; });
        if (j) apiData.push({ url: r.url().slice(0,120), data: j });
      }
    } catch(e) {}
  });
  try {
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForTimeout(waitMs);
    const result = await page.evaluate(extractFn);
    console.log(JSON.stringify(result, null, 2));
    if (apiData.length) {
      console.log("--- API JSON ---");
      apiData.slice(0,5).forEach(function(d) { console.log(d.url + "\n" + JSON.stringify(d.data).slice(0,800)); });
    }
  } catch(e) { console.log("ERROR: " + e.message.slice(0,200)); }
  await ctx.close();
}

// 1. BrowserScan
await testSite("BrowserScan", "https://www.browserscan.net/bot-detection", function() {
  var t = document.body ? document.body.innerText : "";
  var lines = t.split("\n").filter(function(l) { return l.trim() && /score|bot|human|pass|fail|result|detect|webdriver|headless|automation/i.test(l); }).slice(0,25);
  return { title: document.title, lines: lines, raw: t.slice(0,3000) };
}, 18000);

// 2. CreepJS
await testSite("CreepJS", "https://abrahamjuliot.github.io/creepjs/", function() {
  var t = document.body ? document.body.innerText : "";
  var gradeEl = document.querySelector && document.querySelector(".grade");
  var grade = gradeEl ? gradeEl.textContent.trim() : "";
  if (!grade) { var gm = t.match(/Grade[:\s]+([A-F][+-]?)/i); grade = gm ? gm[1] : ""; }
  var trust = t.match(/trust[^%]*?(\d+\.?\d*)%/i);
  var lines = t.split("\n").filter(function(l) { return l.trim() && /headless|stealth|like headless|Grade|Worker|SharedWorker|Chromium|bot|0%|lied|lie count/i.test(l); }).slice(0,35);
  return { title: document.title, grade: grade, trust: trust ? trust[0] : "", lines: lines };
}, 30000);

// 3. IPHey
await testSite("IPHey", "https://iphey.com/", function() {
  var t = document.body ? document.body.innerText : "";
  var lines = t.split("\n").filter(function(l) { return l.trim() && /trustworthy|bot|human|ip|score|detect|vpn|proxy|result|status|genuine|clean/i.test(l); }).slice(0,25);
  return { title: document.title, lines: lines, raw: t.slice(0,2500) };
}, 18000);

// 4. PixelScan
await testSite("PixelScan", "https://pixelscan.net/", function() {
  var t = document.body ? document.body.innerText : "";
  var lines = t.split("\n").filter(function(l) { return l.trim() && /consistent|inconsistent|normal|suspicious|bot|human|score|result|pass|fail|detect/i.test(l); }).slice(0,30);
  return { title: document.title, lines: lines, raw: t.slice(0,2500) };
}, 18000);

// 5. SannySoft
await testSite("SannySoft", "https://bot.sannysoft.com/", function() {
  var rows = [];
  var trs = document.querySelectorAll ? Array.from(document.querySelectorAll("table tr")) : [];
  trs.forEach(function(row) { var t = row.innerText ? row.innerText.replace(/\t/g," ").trim() : ""; if(t) rows.push(t); });
  return { title: document.title, rows: rows.slice(0,50) };
}, 12000);

// 6. Brotector
await testSite("Brotector", "https://kaliiiiiiiiii.github.io/brotector/", function() {
  var t = document.body ? document.body.innerText : "";
  var lines = t.split("\n").filter(function(l) { return l.trim() && /bot|human|detect|pass|fail|score|result|automation|webdriver|headless|true|false|status/i.test(l); }).slice(0,45);
  return { title: document.title, lines: lines, raw: t.slice(0,3500) };
}, 15000);

// 7. Fingerprint.com
await testSite("Fingerprint.com", "https://fingerprint.com/demo/", function() {
  var t = document.body ? document.body.innerText : "";
  var lines = t.split("\n").filter(function(l) { return l.trim() && /bot|human|visitor|confidence|score|detect|incognito|vpn|headless|automation|result|probability|request/i.test(l); }).slice(0,35);
  return { title: document.title, lines: lines, raw: t.slice(0,3000) };
}, 22000);

await browser.close();
console.log("\n=== ALL TESTS DONE ===");
