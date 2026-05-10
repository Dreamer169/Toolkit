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

async function launch(headlessMode) {
  return chromium.launch({
    headless: headlessMode, executablePath: BINARY,
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
}
async function makeCtx(browser) {
  const ctx = await browser.newContext({
    userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
    viewport:{width:1920,height:1040}, locale:'en-US', timezoneId:'America/Los_Angeles', screen:{width:1920,height:1080},
  });
  await ctx.addInitScript(STEALTH_INIT);
  ctx.on('page', p => p.on('worker', w => w.evaluate(WORKER_STEALTH_PATCH).catch(()=>{})));
  return ctx;
}

// --- Fingerprint.com headless:false ---
console.log('\n=== Fingerprint.com [headless:false] ===');
{
  const browser = await launch(false);
  const ctx = await makeCtx(browser);
  const page = await ctx.newPage();
  let botResult = null;
  page.on('response', async r => {
    try {
      if (/fingerprint\.com\/(r4a|BFF|pB7|dKz|[A-Za-z0-9]{4,8})\/?(\?|$)/.test(r.url()) ||
          r.url().includes('fpjs.pro') || r.url().includes('/api/')) {
        const ct = r.headers()['content-type']||'';
        if (ct.includes('json')) {
          const j = await r.json().catch(()=>null);
          if (j && (j.bot !== undefined || j.visitor_id || j.bot_type)) {
            botResult = j;
            console.log('CAPTURED:', r.url().slice(0,80));
            console.log('bot:', j.bot, 'bot_type:', j.bot_type, 'suspect_score:', j.suspect_score);
            if (j.identification) console.log('confidence:', JSON.stringify(j.identification.confidence));
          }
        }
      }
    } catch(e) {}
  });
  await page.goto('https://fingerprint.com/demo/', {timeout:60000, waitUntil:'domcontentloaded'});
  await page.waitForTimeout(25000);
  if (!botResult) {
    // fallback: extract from page
    const txt = await page.evaluate(() => document.body?.innerText||'');
    const botLine = txt.split('\n').filter(l=>/bot_type|bot.*bad|puppeteer|suspect_score/i.test(l));
    console.log('page lines:', botLine.slice(0,5));
  }
  await browser.close();
}
