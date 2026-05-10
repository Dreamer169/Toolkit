// PixelScan detailed: get every accordion item name + status
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

// Capture the scan results API
const scanData = [];
page.on('response', async r => {
  try {
    const ct = r.headers()['content-type'] || '';
    if (!ct.includes('json')) return;
    const j = await r.json().catch(() => null);
    if (!j) return;
    const s = JSON.stringify(j);
    if (r.url().includes('pixelscan') && s.length > 100) {
      scanData.push({ url: r.url().slice(0, 100), data: j });
    }
  } catch (e) {}
});

await page.goto('https://pixelscan.net/bot-check', { timeout: 60000, waitUntil: 'domcontentloaded' });
await page.waitForTimeout(22000);

const result = await page.evaluate(() => {
  // Get all accordion rows: pair label + status
  const rows = [];
  // Try various selector patterns for accordion items
  const accordionItems = document.querySelectorAll('[class*="accordion__item"],[class*="check-item"],[class*="bot-check-row"],[class*="detection-row"]');
  accordionItems.forEach(item => {
    const label = item.querySelector('[class*="label"],[class*="title"],[class*="name"],[class*="heading"]');
    const status = item.querySelector('[class*="status"],[class*="result"],[class*="value"]');
    if (label || status) {
      rows.push({
        label: label ? label.textContent.trim() : '',
        status: status ? status.textContent.trim() : '',
        statusClass: status ? status.className : '',
        fullText: item.textContent.trim().slice(0, 100),
      });
    }
  });

  // Fallback: look for any element containing "Detected" that has a sibling with a label
  if (rows.length === 0) {
    const allDetected = [...document.querySelectorAll('[class*="status--ale"],[class*="status--alert"],[class*="status--a"]')];
    allDetected.forEach(el => {
      const parent = el.closest('[class*="accordion"],[class*="item"],[class*="row"],[class*="check"]');
      if (parent) {
        rows.push({
          status: el.textContent.trim(),
          statusClass: el.className,
          fullText: parent.textContent.trim().slice(0, 120),
          label: parent.querySelector('p,span,h3,h4,div')?.textContent?.trim()?.slice(0, 80) || '',
        });
      }
    });
  }

  // Also grab the main summary
  const summary = document.querySelector('[class*="summary"]');
  const mainStatus = document.querySelector('[class*="summary-section__status"]');

  return {
    rows,
    summaryText: summary ? summary.textContent.trim().slice(0, 300) : 'not found',
    mainStatus: mainStatus ? { cls: mainStatus.className, text: mainStatus.textContent.trim() } : null,
    // Full page accordion text for manual parsing
    pageInnerText: (document.body ? document.body.innerText : '').slice(0, 8000),
  };
});

console.log('\n=== PixelScan /bot-check DETAILED ===');
console.log('Main status:', JSON.stringify(result.mainStatus));
console.log('Summary:', result.summaryText);
console.log('\nAccordion rows found:', result.rows.length);
result.rows.forEach((r, i) => {
  const isAlert = r.statusClass && r.statusClass.includes('ale');
  console.log((isAlert ? '❌' : '✅'), `[${r.status}]`, r.label || r.fullText.slice(0, 70));
});

if (result.rows.length === 0) {
  // Print full page text for manual analysis
  console.log('\nFull page text (for manual analysis):');
  console.log(result.pageInnerText);
}

// Print any scan API data
console.log('\nScan API responses:');
scanData.forEach(d => {
  const s = JSON.stringify(d.data);
  if (s.length < 5000) console.log('URL:', d.url, '\n', s.slice(0, 1000));
});

await browser.close();
console.log('\n=== DONE ===');
