import { chromium } from 'playwright';
import { execSync } from 'child_process';

const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY  = 'socks5://127.0.0.1:10854';

const dist = execSync('cat /root/Toolkit/browser-model/artifacts/api-server/dist/index.mjs').toString();
const m = dist.match(/const STEALTH_INIT = `([\s\S]*?)`;/);
const STEALTH_INIT = m ? m[1] : '';
const WORKER_PATCH_M = dist.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/);
const WORKER_STEALTH_PATCH = WORKER_PATCH_M ? WORKER_PATCH_M[1] : '';

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
// Intercept the PixelScan API response
let scanData = null;
page.on('response', async (resp) => {
  const url = resp.url();
  if (url.includes('pixelscan') && (url.includes('/api') || url.includes('result') || url.includes('scan'))) {
    try {
      const j = await resp.json();
      scanData = j;
      console.log('API response captured:', url);
    } catch(_) {}
  }
});

console.log('Navigating to PixelScan...');
await page.goto('https://pixelscan.net/', { waitUntil: 'networkidle', timeout: 60000 });
await page.waitForTimeout(8000);

// Try to find results in the page
const results = await page.evaluate(() => {
  const body = document.body?.innerText || '';
  const lines = body.split('\n').filter(l => 
    l.match(/consistent|inconsistent|normal|suspicious|detected|passed|failed|bot|human|ip|browser|os|platform/i) ||
    l.match(/\d+\/\d+/)
  );
  return {
    url: location.href,
    title: document.title,
    lines: lines.slice(0, 40),
    fullText: body.slice(0, 3000),
  };
});

console.log('=== PixelScan Results ===');
console.log('URL:', results.url);
console.log('Title:', results.title);
results.lines.forEach(l => console.log(' ', l.trim()));
if (!results.lines.length) {
  console.log('No filtered lines found. Full text (first 2000 chars):');
  console.log(results.fullText.slice(0, 2000));
}
if (scanData) {
  console.log('\nAPI JSON:', JSON.stringify(scanData, null, 2).slice(0, 2000));
}

await browser.close();
