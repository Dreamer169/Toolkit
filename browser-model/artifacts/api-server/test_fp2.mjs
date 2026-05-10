import { chromium } from 'playwright';
import { readFileSync } from 'fs';
const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY  = 'socks5://127.0.0.1:10854';
const tsSrc = readFileSync('/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts', 'utf8');
const STEALTH_INIT = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/) || [])[1] || '';
const WORKER_STEALTH_PATCH = (tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/) || [])[1] || '';
console.log('SI:', STEALTH_INIT.length, 'WP:', WORKER_STEALTH_PATCH.length);

const browser = await chromium.launch({
  headless: false, executablePath: BINARY,
  args: ['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled',
    '--no-first-run','--no-default-browser-check','--mute-audio','--lang=en-US',
    '--use-gl=angle','--use-angle=swiftshader','--enable-webgl',
    '--proxy-server='+PROXY,'--disable-quic','--proxy-resolves-dns-locally','--window-size=1920,1080',
    '--fingerprint='+Math.floor(Math.random()*0x7fffffff),
    '--fingerprint-platform=linux','--fingerprint-brand=Chrome','--fingerprint-brand-version=144',
    '--fingerprint-hardware-concurrency=8','--timezone=America/Los_Angeles','--disable-non-proxied-udp',
  ],
  ignoreDefaultArgs: ['--enable-automation'],
  env: { ...process.env, DISPLAY: ':99' },
});
const ctx = await browser.newContext({
  userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
  viewport:{width:1920,height:1040}, locale:'en-US', timezoneId:'America/Los_Angeles', screen:{width:1920,height:1080},
});
await ctx.addInitScript(STEALTH_INIT);
ctx.on('page', p => p.on('worker', w => w.evaluate(WORKER_STEALTH_PATCH).catch(()=>{})));

const page = await ctx.newPage();
// capture ALL JSON responses
page.on('response', async r => {
  try {
    const ct = r.headers()['content-type']||'';
    if (!ct.includes('json')) return;
    const j = await r.json().catch(()=>null);
    if (!j) return;
    const s = JSON.stringify(j);
    if (s.includes('bot') || s.includes('suspect') || s.includes('visitor_id') || s.includes('confidence')) {
      console.log('=API=', r.url().slice(0,120));
      console.log(s.slice(0,1000));
    }
  } catch(e) {}
});
await page.goto('https://fingerprint.com/demo/', {timeout:60000, waitUntil:'domcontentloaded'});
await page.waitForTimeout(28000);
console.log('Done waiting');
await browser.close();
