// Test fingerprint.com with headless:false to see if headless is the issue
import { chromium } from 'playwright';
import { readFileSync } from 'fs';
const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY  = 'socks5://127.0.0.1:10854';
const tsSrc = readFileSync('/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts', 'utf8');
const m1 = tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/);
const STEALTH_INIT = m1 ? m1[1] : '';
const m2 = tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/);
const WORKER_STEALTH_PATCH = m2 ? m2[1] : '';
console.log('STEALTH_INIT:', STEALTH_INIT.length, 'WORKER:', WORKER_STEALTH_PATCH.length);
const browser = await chromium.launch({
  headless: false, executablePath: BINARY,
  args: ['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled',
    '--no-first-run','--no-default-browser-check','--mute-audio','--lang=en-US',
    '--use-gl=angle','--use-angle=swiftshader','--enable-webgl',
    '--proxy-server='+PROXY,'--disable-quic','--proxy-resolves-dns-locally',
    '--window-size=1920,1080',
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
const apiData = [];
const page = await ctx.newPage();
page.on('response', async r => {
  try {
    const ct = r.headers()['content-type']||'';
    if (ct.includes('json') && r.url().includes('fingerprint')) {
      const j = await r.json().catch(()=>null);
      if (j) { console.log('API:', r.url().slice(0,100)); console.log(JSON.stringify(j).slice(0,600)); }
    }
  } catch(e) {}
});
await page.goto('https://fingerprint.com/demo/', {timeout:60000, waitUntil:'domcontentloaded'});
await page.waitForTimeout(22000);
const r = await page.evaluate(() => {
  const t = document.body?.innerText||'';
  return t.split('\n').filter(l=>l.trim() && /bot|confidence|suspect|visitor_id|puppeteer|headless|vpn|proxy/i.test(l)).slice(0,20);
});
console.log('=== Fingerprint.com headless:false ===');
r.forEach(l=>console.log(l));
await browser.close();
