import { chromium } from 'playwright';
import { readFileSync } from 'fs';
const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY  = 'socks5://127.0.0.1:10854';
const tsSrc  = readFileSync('./src/lib/renderer.ts', 'utf8');
const STEALTH_INIT         = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/) || [])[1] || '';
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

// Capture all JSON API calls
const captured = [];
page.on('response', async r => {
  try {
    const ct = r.headers()['content-type'] || '';
    if (!ct.includes('json')) return;
    const j = await r.json().catch(() => null);
    if (!j) return;
    const s = JSON.stringify(j);
    if (s.length < 5000) captured.push({ url: r.url(), data: j });
  } catch (e) {}
});

await page.goto('https://iphey.com/', { timeout: 60000, waitUntil: 'domcontentloaded' });
await page.waitForTimeout(20000);

// Extract all visible text
const info = await page.evaluate(() => {
  const allText = document.body ? document.body.innerText : '';
  // Find status indicators in DOM
  const statusEls = [...document.querySelectorAll('*')].filter(el => {
    const txt = (el.textContent || '').trim();
    return txt.length > 0 && txt.length < 100 &&
      /trust|genuine|human|bot|suspicious|clean|risk|safe|unsafe|good|bad|score/i.test(txt) &&
      el.children.length === 0;  // leaf nodes only
  }).map(el => ({ tag: el.tagName, class: el.className, text: el.textContent.trim() })).slice(0, 30);
  return {
    statusEls,
    allLines: allText.split('\n').filter(l => l.trim() && l.trim().length < 200).slice(0, 60),
  };
});
console.log('\n=== IPHey leaf-node status elements ===');
info.statusEls.forEach(e => console.log(' ', e.tag, JSON.stringify(e.class).slice(0, 40), ':', e.text));
console.log('\n=== IPHey first 60 text lines ===');
info.allLines.forEach(l => console.log(' ', l));
console.log('\n=== IPHey API responses ===');
captured.forEach(c => {
  const s = JSON.stringify(c.data);
  if (/bot|trust|score|human|genuine|status|result/i.test(s)) {
    console.log('URL:', c.url.slice(0, 100));
    console.log('  ', s.slice(0, 500));
  }
});

await browser.close();
console.log('\n=== DONE ===');
