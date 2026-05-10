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

const cfEvents = [];
page.on('response', async (resp) => {
  const url = resp.url();
  const status = resp.status();
  if (url.match(/challenge|turnstile|__cf_|cdn-cgi\/challenge/i)) {
    cfEvents.push({ url: url.slice(0, 120), status });
  }
});

console.log('Navigating to unitool.ai/en ...');
const t0 = Date.now();
try {
  await page.goto('https://unitool.ai/en', { waitUntil: 'domcontentloaded', timeout: 45000 });
} catch(e) {
  console.log('goto error:', e.message);
}
await page.waitForTimeout(10000);

const elapsed = Date.now() - t0;
const finalUrl = page.url();
const title = await page.title().catch(() => '?');
const bodyText = await page.evaluate(() => {
  const b = document.body?.innerText || '';
  return b.slice(0, 2000);
});

// Check if CF challenge is present
const hasCFChallenge = await page.evaluate(() => {
  return !!(
    document.querySelector('iframe[src*="challenge"]') ||
    document.querySelector('[class*="cf-"]') ||
    document.querySelector('#challenge-form') ||
    document.title?.includes('Just a moment') ||
    document.body?.innerText?.includes('Checking if the site connection is secure')
  );
});

console.log(`=== unitool.ai (${elapsed}ms) ===`);
console.log('Final URL:', finalUrl);
console.log('Title:', title);
console.log('CF Challenge detected:', hasCFChallenge);
console.log('CF Events:', JSON.stringify(cfEvents));
console.log('\nBody text:');
console.log(bodyText);

await page.screenshot({ path: '/tmp/unitool_shot.png', fullPage: false });
console.log('\nScreenshot: /tmp/unitool_shot.png');

await browser.close();
