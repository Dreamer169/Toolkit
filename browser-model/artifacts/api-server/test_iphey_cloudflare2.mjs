// IPHey + Cloudflare + DataDome benchmark tests
import { chromium } from 'playwright';
import { readFileSync } from 'fs';
const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY  = 'socks5://127.0.0.1:10854';
const tsSrc  = readFileSync('./src/lib/renderer.ts', 'utf8');
const STEALTH_INIT         = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/) || [])[1] || '';
const WORKER_STEALTH_PATCH = (tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/) || [])[1] || '';
console.log('SI:', STEALTH_INIT.length, 'WP:', WORKER_STEALTH_PATCH.length);

const mkBrowser = () => chromium.launch({
  headless: false, executablePath: BINARY,
  args: [
    '--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled',
    '--no-first-run','--no-default-browser-check','--mute-audio','--lang=en-US',
    '--use-gl=angle','--use-angle=swiftshader','--enable-webgl',
    '--proxy-server='+PROXY,'--disable-quic','--proxy-resolves-dns-locally',
    '--window-size=1920,1080',
    '--fingerprint='+Math.floor(Math.random()*0x7fffffff),
    '--fingerprint-platform=linux','--fingerprint-brand=Chrome',
    '--fingerprint-brand-version=144','--fingerprint-hardware-concurrency=4',
    '--timezone=America/Los_Angeles','--disable-non-proxied-udp',
  ],
  ignoreDefaultArgs: ['--enable-automation'],
  env: { ...process.env, DISPLAY: ':99' },
});
const mkCtx = async (b) => {
  const ctx = await b.newContext({
    userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
    viewport:{width:1920,height:1040},locale:'en-US',
    timezoneId:'America/Los_Angeles',screen:{width:1920,height:1080},
  });
  await ctx.addInitScript(STEALTH_INIT);
  ctx.on('page',p=>p.on('worker',w=>w.evaluate(WORKER_STEALTH_PATCH).catch(()=>{})));
  return ctx;
};

// ── IPHey ──────────────────────────────────────────────────────────────────
{
  const b = await mkBrowser(); const ctx = await mkCtx(b);
  const page = await ctx.newPage();
  // Capture API responses
  const responses = [];
  page.on('response', async r => {
    const ct = r.headers()['content-type']||'';
    if (!ct.includes('json')) return;
    try { const j = await r.json(); responses.push({url:r.url(), j}); } catch(_) {}
  });
  try {
    await page.goto('https://iphey.com/', {timeout:60000, waitUntil:'domcontentloaded'});
    await page.waitForTimeout(22000);
    const raw = await page.locator('body').textContent().catch(()=>'');
    console.log('\n=== IPHey body (first 80 lines) ===');
    raw.split('\n').map(l=>l.trim()).filter(Boolean).slice(0,80).forEach(l=>console.log(' ',l.slice(0,120)));
    console.log('\n=== IPHey API responses ===');
    responses.forEach(r => {
      const s = JSON.stringify(r.j);
      console.log('URL:', r.url.slice(0,100), '\n  ', s.slice(0,600));
    });
    await page.screenshot({path:'/tmp/iphey_result.png', fullPage:false});
    console.log('IPHey screenshot: /tmp/iphey_result.png');
  } catch(e) { console.log('IPHey error:', e.message); }
  await b.close();
}

// ── Cloudflare (nowsecure.nl) ───────────────────────────────────────────────
{
  const b = await mkBrowser(); const ctx = await mkCtx(b);
  const page = await ctx.newPage();
  try {
    await page.goto('https://nowsecure.nl/', {timeout:60000, waitUntil:'domcontentloaded'});
    await page.waitForTimeout(18000);
    const raw = await page.locator('body').textContent().catch(()=>'');
    const title = await page.title().catch(()=>'?');
    const url = page.url();
    console.log('\n=== Cloudflare nowsecure.nl ===');
    console.log('  Title:', title);
    console.log('  URL:', url);
    console.log('  Body (first 50 lines):');
    raw.split('\n').map(l=>l.trim()).filter(Boolean).slice(0,50).forEach(l=>console.log('   ',l.slice(0,120)));
    await page.screenshot({path:'/tmp/cf_result.png'});
  } catch(e) { console.log('CF error:', e.message); }
  await b.close();
}

// ── DataDome demo ───────────────────────────────────────────────────────────
{
  const b = await mkBrowser(); const ctx = await mkCtx(b);
  const page = await ctx.newPage();
  try {
    await page.goto('https://antoinevastel.com/bots/datadome', {timeout:60000, waitUntil:'domcontentloaded'});
    await page.waitForTimeout(15000);
    const raw = await page.locator('body').textContent().catch(()=>'');
    const title = await page.title().catch(()=>'?');
    console.log('\n=== DataDome (antoinevastel.com) ===');
    console.log('  Title:', title);
    console.log('  Body (first 40 lines):');
    raw.split('\n').map(l=>l.trim()).filter(Boolean).slice(0,40).forEach(l=>console.log('   ',l.slice(0,120)));
    await page.screenshot({path:'/tmp/datadome_result.png'});
  } catch(e) { console.log('DataDome error:', e.message); }
  await b.close();
}

console.log('\n=== ALL DONE ===');
