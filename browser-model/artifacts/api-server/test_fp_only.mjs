// Fingerprint.com precise test – headless:false, captures all JSON responses
import { chromium } from 'playwright';
import { readFileSync } from 'fs';
const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY  = 'socks5://127.0.0.1:10854';
const tsSrc  = readFileSync('./src/lib/renderer.ts', 'utf8');
const STEALTH_INIT        = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/) || [])[1] || '';
const WORKER_STEALTH_PATCH = (tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/) || [])[1] || '';
console.log('SI:', STEALTH_INIT.length, 'WP:', WORKER_STEALTH_PATCH.length);

const browser = await chromium.launch({
  headless: false, executablePath: BINARY,
  args: [
    '--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled',
    '--no-first-run','--no-default-browser-check','--mute-audio','--lang=en-US',
    '--use-gl=angle','--use-angle=swiftshader','--enable-webgl',
    '--proxy-server='+PROXY,'--disable-quic','--proxy-resolves-dns-locally',
    '--window-size=1920,1080',
    '--fingerprint='+Math.floor(Math.random()*0x7fffffff),
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

// Capture ALL JSON responses to/from fingerprint.com
page.on('response', async r => {
  try {
    const ct = r.headers()['content-type'] || '';
    if (!ct.includes('json')) return;
    const j = await r.json().catch(() => null);
    if (!j) return;
    const s = JSON.stringify(j);
    // Only print if it has interesting fields
    const interesting = s.includes('bot') || s.includes('visitor_id') || s.includes('suspect')
      || s.includes('confidence') || s.includes('automation') || s.includes('headless');
    if (!interesting) return;
    console.log('=== RESPONSE:', r.url().slice(0, 100));
    // Print key fields
    const fields = ['bot','bot_type','suspect_score','visitor_id','proxy_confidence','automation','headless'];
    fields.forEach(k => { if (j[k] !== undefined) console.log('  '+k+':', JSON.stringify(j[k])); });
    if (j.identification) {
      const id = j.identification;
      console.log('  identification.visitor_id:', id.visitor_id);
      if (id.confidence) console.log('  identification.confidence.score:', id.confidence.score);
    }
    // raw snippet for diagnosis
    console.log('  raw:', s.slice(0, 600));
  } catch (e) {}
});

await page.goto('https://fingerprint.com/demo/', { timeout: 60000, waitUntil: 'domcontentloaded' });
await page.waitForTimeout(35000);  // generous wait

// Also check page text for any bot indication
const pageText = await page.evaluate(() => {
  const t = document.body ? document.body.innerText : '';
  return t.split('\n').filter(l => l.trim() && /bot_type|bot.*bad|bot.*good|puppeteer|suspect_score|confidence/i.test(l)).slice(0, 10);
}).catch(() => []);
console.log('\nPage text matches:', pageText);

await browser.close();
console.log('\n=== DONE ===');
