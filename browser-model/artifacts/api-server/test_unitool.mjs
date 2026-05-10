import { chromium } from 'playwright';
import { execSync } from 'child_process';

const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY  = 'socks5://127.0.0.1:10854';

const dist = execSync('cat /root/Toolkit/browser-model/artifacts/api-server/dist/index.mjs').toString();
const m = dist.match(/const STEALTH_INIT = `([\s\S]*?)`;/);
const STEALTH_INIT = m ? m[1] : '';
const WM = dist.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/);
const WORKER_STEALTH_PATCH = WM ? WM[1] : '';

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

// Track CF Turnstile / challenge events
let cfEvents = [];
page.on('response', async (resp) => {
  const url = resp.url();
  const status = resp.status();
  if (url.includes('challenge') || url.includes('turnstile') || url.includes('cf-') || url.includes('__cf')) {
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
await page.waitForTimeout(8000);

const elapsed = Date.now() - t0;
const finalUrl = page.url();
const title = await page.title().catch(() => '?');
const bodyText = await page.evaluate(() => (document.body?.innerText || '').slice(0, 1500));

console.log(`=== unitool.ai Results (${elapsed}ms) ===`);
console.log('Final URL:', finalUrl);
console.log('Title:', title);
console.log('CF events:', JSON.stringify(cfEvents));
console.log('Body (first 1000 chars):');
console.log(bodyText.slice(0, 1000));

// Screenshot
await page.screenshot({ path: '/tmp/unitool_screenshot.png' });
console.log('Screenshot saved: /tmp/unitool_screenshot.png');

await browser.close();
