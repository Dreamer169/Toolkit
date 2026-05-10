import { chromium } from 'playwright';
import { readFileSync } from 'fs';
const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY  = 'socks5://127.0.0.1:10854';
const tsSrc = readFileSync('./src/lib/renderer.ts', 'utf8');
const STEALTH_INIT = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/) || [])[1] || '';
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

async function mkCtx() {
  const ctx = await browser.newContext({
    userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
    viewport: { width: 1920, height: 1040 }, locale: 'en-US',
    timezoneId: 'America/Los_Angeles', screen: { width: 1920, height: 1080 },
  });
  await ctx.addInitScript(STEALTH_INIT);
  ctx.on('page', p => p.on('worker', w => w.evaluate(WORKER_STEALTH_PATCH).catch(() => {})));
  return ctx;
}

// ─── Fingerprint.com ───────────────────────────────────────────────────────
console.log('\n=== Fingerprint.com [headless:false] ===');
{
  const ctx = await mkCtx();
  const page = await ctx.newPage();
  page.on('response', async r => {
    try {
      if (!/fingerprint\.com/.test(r.url())) return;
      const ct = r.headers()['content-type'] || '';
      if (!ct.includes('json')) return;
      const j = await r.json().catch(() => null);
      if (!j) return;
      const s = JSON.stringify(j);
      if (!s.includes('"bot"') && !s.includes('visitor_id') && !s.includes('suspect')) return;
      console.log('URL:', r.url().slice(0, 80));
      // safe extraction without regex groups that confuse bash
      const keys = ['bot', 'bot_type', 'suspect_score', 'visitor_id', 'proxy_confidence'];
      keys.forEach(k => {
        if (j[k] !== undefined) console.log(' ', k + ':', JSON.stringify(j[k]));
      });
      // nested confidence score
      if (j.identification && j.identification.confidence) {
        console.log('  confidence.score:', j.identification.confidence.score);
      }
    } catch (e) {}
  });
  await page.goto('https://fingerprint.com/demo/', { timeout: 60000, waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(28000);
  await ctx.close();
}

// ─── IPHey ─────────────────────────────────────────────────────────────────
console.log('\n=== IPHey ===');
{
  const ctx = await mkCtx();
  const page = await ctx.newPage();
  page.on('response', async r => {
    try {
      const ct = r.headers()['content-type'] || '';
      if (!ct.includes('json')) return;
      const j = await r.json().catch(() => null);
      const s = JSON.stringify(j || {});
      if (/bot|trust|score|human|genuine/i.test(s) && s.length < 2000) {
        console.log('API:', r.url().slice(0, 80));
        console.log(' ', s.slice(0, 400));
      }
    } catch (e) {}
  });
  await page.goto('https://iphey.com/', { timeout: 60000, waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(25000);
  const result = await page.evaluate(() => {
    const t = document.body ? document.body.innerText : '';
    const lines = t.split('\n').filter(l =>
      l.trim() && /trust|genuine|human|bot|score|suspicious|clean|risk|pass|fail|good|bad/i.test(l)
    );
    return { lines: lines.slice(0, 15), raw: t.slice(0, 1500) };
  });
  result.lines.forEach(l => console.log(' ', l));
  if (!result.lines.length) console.log('raw sample:', result.raw.slice(0, 600));
  await ctx.close();
}

// ─── PixelScan ─────────────────────────────────────────────────────────────
console.log('\n=== PixelScan ===');
{
  const ctx = await mkCtx();
  const page = await ctx.newPage();
  const apiData = [];
  page.on('response', async r => {
    try {
      const ct = r.headers()['content-type'] || '';
      if (ct.includes('json') && r.url().includes('pixelscan')) {
        const j = await r.json().catch(() => null);
        if (j) apiData.push(r.url().slice(0, 80) + '\n' + JSON.stringify(j).slice(0, 500));
      }
    } catch (e) {}
  });
  await page.goto('https://pixelscan.net/fingerprint-check', { timeout: 60000, waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(25000);
  const result = await page.evaluate(() => {
    const t = document.body ? document.body.innerText : '';
    return {
      lines: t.split('\n').filter(l =>
        l.trim() && /consistent|inconsistent|normal|suspicious|bot|human|score|pass|fail|detect|risk|status|ok|warn/i.test(l)
      ).slice(0, 20),
    };
  });
  result.lines.forEach(l => console.log(' ', l));
  apiData.forEach(d => console.log('API:', d));
  await ctx.close();
}

// ─── Selenium Detector (Brotector alternative) ─────────────────────────────
console.log('\n=== Selenium Detector ===');
{
  const ctx = await mkCtx();
  const page = await ctx.newPage();
  await page.goto('https://hmaker.github.io/selenium-detector/', { timeout: 60000, waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(8000);
  const result = await page.evaluate(() => ({
    lines: (document.body ? document.body.innerText : '').split('\n').filter(l => l.trim()).slice(0, 40),
  }));
  result.lines.forEach(l => console.log(' ', l));
  await ctx.close();
}

// ─── Datadome ──────────────────────────────────────────────────────────────
console.log('\n=== Datadome ===');
{
  const ctx = await mkCtx();
  const page = await ctx.newPage();
  await page.goto('https://antoinevastel.com/bots/datadome', { timeout: 60000, waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(10000);
  const result = await page.evaluate(() => {
    const t = document.body ? document.body.innerText : '';
    return { lines: t.split('\n').filter(l => l.trim()).slice(0, 30), title: document.title };
  });
  console.log('Title:', result.title);
  result.lines.forEach(l => console.log(' ', l));
  await ctx.close();
}

await browser.close();
console.log('\n=== ALL DONE ===');
