// Get the actual innerHTML of PixelScan accordion to find check names
import { chromium } from 'playwright';
import { readFileSync } from 'fs';
const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY  = 'socks5://127.0.0.1:10854';
const tsSrc  = readFileSync('./src/lib/renderer.ts', 'utf8');
const STEALTH_INIT         = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/) || [])[1] || '';
const WORKER_STEALTH_PATCH = (tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/) || [])[1] || '';

const browser = await chromium.launch({
  headless: false, executablePath: BINARY,
  args: [
    '--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled',
    '--no-first-run','--no-default-browser-check','--mute-audio','--lang=en-US',
    '--use-gl=angle','--use-angle=swiftshader','--enable-webgl',
    '--proxy-server='+PROXY,'--disable-quic','--proxy-resolves-dns-locally',
    '--window-size=1920,1080',
    '--fingerprint='+String(Math.floor(Math.random()*0x7fffffff)),
    '--fingerprint-platform=linux','--fingerprint-brand=Chrome',
    '--fingerprint-brand-version=144','--fingerprint-hardware-concurrency=8',
    '--timezone=America/Los_Angeles','--disable-non-proxied-udp',
  ],
  ignoreDefaultArgs: ['--enable-automation'],
  env: { ...process.env, DISPLAY: ':99' },
});
const ctx = await browser.newContext({
  userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
  viewport: { width: 1920, height: 1040 }, locale: 'en-US',
  timezoneId: 'America/Los_Angeles', screen: { width: 1920, height: 1080 },
});
await ctx.addInitScript(STEALTH_INIT);
ctx.on('page', p => p.on('worker', w => w.evaluate(WORKER_STEALTH_PATCH).catch(() => {})));

const page = await ctx.newPage();
await page.goto('https://pixelscan.net/bot-check', { timeout: 60000, waitUntil: 'domcontentloaded' });
await page.waitForTimeout(22000);

// Get full HTML of the bot-check results section
const result = await page.evaluate(() => {
  // Find the section with the bot check results
  const section = document.querySelector('[class*="bot-check"],[class*="summary"],[class*="results"],[class*="accordion"]');
  if (section) return section.innerHTML.slice(0, 15000);
  // Fallback: get body HTML
  return (document.body ? document.body.innerHTML : '').slice(0, 15000);
});

// Print just the text content without all the HTML attributes (cleaner output)
// Extract text between > and < tags
const textParts = result.match(/>[^<]{2,100}</g);
const cleanTexts = textParts ? textParts.map(t => t.slice(1,-1).trim()).filter(t => t.length > 1) : [];

console.log('\n=== PixelScan accordion text content ===');
cleanTexts.forEach(t => console.log(t));

console.log('\n=== Raw HTML (first 5000 chars) ===');
console.log(result.slice(0, 5000));

await browser.close();
console.log('\n=== DONE ===');
