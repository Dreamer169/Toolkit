import { chromium } from 'playwright';
import { readFileSync } from 'fs';

const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY  = 'socks5://127.0.0.1:10854';
const DIST   = '/root/Toolkit/browser-model/artifacts/api-server/dist/index.mjs';

const src = readFileSync(DIST, 'utf8');
const m1 = src.match(/const STEALTH_INIT = `([\s\S]*?)`;/);
const STEALTH_INIT = m1 ? m1[1] : '';
const m2 = src.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/);
const WORKER_STEALTH_PATCH = m2 ? m2[1] : '';
console.log(`STEALTH_INIT:${STEALTH_INIT.length} WORKER:${WORKER_STEALTH_PATCH.length}`);

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
await ctx.addInitScript(STEALTH_INIT);
ctx.on('page', (p) => { p.on('worker', (w) => { w.evaluate(WORKER_STEALTH_PATCH).catch(() => {}); }); });

const page = await ctx.newPage();

// Capture API/XHR responses from pixelscan
const apiResponses = [];
page.on('response', async (resp) => {
  const url = resp.url();
  if (url.includes('pixelscan.net') && !url.match(/\.(css|js|png|ico|woff)/)) {
    try {
      const ct = resp.headers()['content-type'] || '';
      if (ct.includes('json')) {
        const j = await resp.json();
        apiResponses.push({ url: url.slice(0, 100), data: j });
      }
    } catch(_) {}
  }
});

console.log('Navigating to PixelScan...');
await page.goto('https://pixelscan.net/', { waitUntil: 'networkidle', timeout: 60000 });
await page.waitForTimeout(10000);

const result = await page.evaluate(() => {
  const body = document.body?.innerText || '';
  // Filter for result-like lines
  const lines = body.split('\n').map(l => l.trim()).filter(l =>
    l && l.match(/consistent|inconsistent|normal|suspicious|detected|pass|fail|bot|human|real|fake|ip|vpn|proxy|tor|browser|os|platform|fingerprint|score|result/i)
  );
  return {
    url: location.href,
    title: document.title,
    lines: lines.slice(0, 50),
    rawText: body.slice(0, 4000),
  };
});

console.log('=== PixelScan Results ===');
console.log('URL:', result.url, '| Title:', result.title);
if (result.lines.length) {
  result.lines.forEach(l => console.log(' ', l));
} else {
  console.log('No keyword lines found. Raw text:');
  console.log(result.rawText.slice(0, 2000));
}
if (apiResponses.length) {
  console.log('\n--- API responses ---');
  apiResponses.forEach(r => {
    console.log(r.url);
    console.log(JSON.stringify(r.data, null, 2).slice(0, 1000));
  });
}

await browser.close();
