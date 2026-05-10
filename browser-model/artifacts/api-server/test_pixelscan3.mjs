import { chromium } from 'playwright';
import { readFileSync } from 'fs';

const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY  = 'socks5://127.0.0.1:10854';

const browser = await chromium.launch({
  executablePath: BINARY,
  headless: true,
  proxy: { server: PROXY },
  args: [
    '--no-sandbox','--disable-setuid-sandbox','--disable-dev-shm-usage',
    '--disable-blink-features=AutomationControlled',
    `--fingerprint=${Math.random()*0x7fffffff|0}`,
    '--fingerprint-platform=linux','--fingerprint-brand=Chrome',
    '--fingerprint-brand-version=144','--fingerprint-hardware-concurrency=8',
    '--disable-spoofing=gpu',
    '--lang=en-US','--accept-lang=en-US,en',
    '--timezone=America/Los_Angeles',
  ],
  env: { ...process.env, DISPLAY: ':99' },
});

const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 } });

// Extract STEALTH_INIT / WORKER_STEALTH_PATCH from source (not minified dist)
const tsSrc = readFileSync('/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts', 'utf8');
const m1 = tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/);
const STEALTH_INIT = m1 ? m1[1] : '';
const m2 = tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/);
const WORKER_STEALTH_PATCH = m2 ? m2[1] : '';
console.log(`STEALTH_INIT:${STEALTH_INIT.length} WORKER:${WORKER_STEALTH_PATCH.length}`);

await ctx.addInitScript(STEALTH_INIT);
ctx.on('page', (p) => { p.on('worker', (w) => { w.evaluate(WORKER_STEALTH_PATCH).catch(() => {}); }); });

const page = await ctx.newPage();
const apiData = [];
page.on('response', async (resp) => {
  const url = resp.url();
  if (url.includes('pixelscan.net') && url.match(/\/api\/|\/scan\/|\/result/)) {
    try {
      const ct = resp.headers()['content-type'] || '';
      if (ct.includes('json')) {
        const j = await resp.json();
        apiData.push({ url: url.slice(0, 120), data: j });
      }
    } catch(_) {}
  }
});

console.log('Navigating to pixelscan.net/fingerprint-check ...');
await page.goto('https://pixelscan.net/fingerprint-check', { waitUntil: 'networkidle', timeout: 60000 });
// Wait for scan to complete
await page.waitForTimeout(15000);

// Try clicking scan button if present
try {
  const btn = await page.$('button:has-text("Scan"), button:has-text("Check"), button:has-text("Start")');
  if (btn) { await btn.click(); await page.waitForTimeout(10000); }
} catch(_) {}

const result = await page.evaluate(() => {
  const body = document.body?.innerText || '';
  const all = body.split('\n').map(l => l.trim()).filter(l => l.length > 2);
  // Specifically look for result lines
  const resultLines = all.filter(l =>
    l.match(/consistent|inconsistent|normal|suspicious|detected|pass|fail|real|fake|bot|human|ip|vpn|proxy|score|fingerprint|headless|automated|webdriver|cdp|selenium/i)
  );
  return {
    title: document.title,
    url: location.href,
    resultLines: resultLines.slice(0, 50),
    allLines: all.slice(0, 80),
  };
});

console.log('=== PixelScan /fingerprint-check ===');
console.log('URL:', result.url, '| Title:', result.title);
if (result.resultLines.length) {
  console.log('--- Result lines ---');
  result.resultLines.forEach(l => console.log(' ', l));
} else {
  console.log('--- All page text (no result keywords found) ---');
  result.allLines.forEach(l => console.log(' ', l));
}
if (apiData.length) {
  console.log('\n--- API JSON ---');
  apiData.forEach(r => { console.log(r.url); console.log(JSON.stringify(r.data, null, 2).slice(0, 2000)); });
}

await browser.close();
