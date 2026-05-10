// 综合测试: CreepJS trust% + PixelScan Navigator detail
import { chromium } from 'playwright';
import { readFileSync } from 'fs';

const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY  = 'socks5://127.0.0.1:10854';

const tsSrc = readFileSync('./src/lib/renderer.ts', 'utf8');
const STEALTH_INIT = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/) || [])[1] || '';
const WORKER_STEALTH_PATCH = (tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/) || [])[1] || '';
console.log('STEALTH_INIT:', STEALTH_INIT.length, ' WORKER:', WORKER_STEALTH_PATCH.length);

const ARGS = [
  '--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled',
  '--no-first-run','--no-default-browser-check','--mute-audio','--lang=en-US',
  '--use-fake-ui-for-media-stream','--use-gl=angle','--use-angle=swiftshader','--enable-webgl',
  '--proxy-server='+PROXY,'--disable-quic','--proxy-resolves-dns-locally',
  '--window-size=1920,1080',
  '--fingerprint='+String(Math.floor(Math.random()*0x7fffffff)),
  '--fingerprint-platform=linux','--fingerprint-brand=Chrome',
  '--fingerprint-brand-version=144','--fingerprint-hardware-concurrency=8',
  '--timezone=America/Los_Angeles','--disable-non-proxied-udp',
];

// ─── 1) CreepJS trust score ───────────────────────────────────────────────────
{
  const browser = await chromium.launch({
    headless: false, executablePath: BINARY, args: ARGS,
    ignoreDefaultArgs: ['--enable-automation'],
    env: { ...process.env, DISPLAY: ':99', LANG: 'en_US.UTF-8' },
  });
  const ctx = await browser.newContext({
    userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
    viewport: { width: 1920, height: 1040 }, locale: 'en-US',
    timezoneId: 'America/Los_Angeles', screen: { width: 1920, height: 1080 },
  });
  await ctx.addInitScript(STEALTH_INIT);
  ctx.on('page', p => p.on('worker', w => w.evaluate(WORKER_STEALTH_PATCH).catch(() => {})));
  const page = await ctx.newPage();
  console.log('\n[CreepJS] loading...');
  await page.goto('https://abrahamjuliot.github.io/creepjs/', { timeout: 90000, waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(28000);
  const r = await page.evaluate(() => {
    const txt = document.body?.innerText || '';
    // trust score: usually a line like 78% or near trust text
    const trustMatch = txt.match(/trust[:\s\n]*(\d+(?:\.\d+)?%)/i);
    // like-headless line
    const lhMatch = txt.match(/(\d+(?:\.\d+)?)%\s*like[\s-]headless/i);
    const headlessMatch = txt.match(/(\d+(?:\.\d+)?)%\s*headless/i);
    const stealthMatch = txt.match(/(\d+(?:\.\d+)?)%\s*stealth/i);
    // grade
    const grade = document.querySelector('[class*=grade]')?.textContent?.trim();
    // all % lines
    const pctLines = txt.split('\n').filter(l => l.includes('%')).slice(0, 30);
    return { trustMatch, lhMatch, headlessMatch, stealthMatch, grade, pctLines, rawSnippet: txt.slice(0, 3000) };
  });
  console.log('=== CreepJS ===');
  console.log('trust:', r.trustMatch);
  console.log('like-headless:', r.lhMatch);
  console.log('headless:', r.headlessMatch);
  console.log('stealth:', r.stealthMatch);
  console.log('grade:', r.grade);
  console.log('--- % lines ---');
  r.pctLines.forEach(l => console.log(' ', l.trim().slice(0,120)));
  await browser.close();
}

// ─── 2) PixelScan fingerprint-check Navigator details ────────────────────────
{
  const browser = await chromium.launch({
    headless: false, executablePath: BINARY, args: ARGS,
    ignoreDefaultArgs: ['--enable-automation'],
    env: { ...process.env, DISPLAY: ':99', LANG: 'en_US.UTF-8' },
  });
  const ctx = await browser.newContext({
    userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
    viewport: { width: 1920, height: 1040 }, locale: 'en-US',
    timezoneId: 'America/Los_Angeles', screen: { width: 1920, height: 1080 },
  });
  await ctx.addInitScript(STEALTH_INIT);
  ctx.on('page', p => p.on('worker', w => w.evaluate(WORKER_STEALTH_PATCH).catch(() => {})));
  const page = await ctx.newPage();
  // capture JSON API
  const apiData = [];
  page.on('response', async res => {
    try {
      if ((res.headers()['content-type']||'').includes('json')) {
        const j = await res.json().catch(() => null);
        if (j && res.url().includes('pixelscan')) apiData.push({ url: res.url().slice(0,120), j });
      }
    } catch(_) {}
  });
  console.log('\n[PixelScan] loading fingerprint-check...');
  await page.goto('https://pixelscan.net/fingerprint-check', { timeout: 60000, waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(20000);
  const r = await page.evaluate(() => {
    const body = document.body?.innerText || '';
    // Look for Navigator section and everything under it
    const navIdx = body.indexOf('Navigator');
    const snippet = navIdx >= 0 ? body.slice(navIdx, navIdx + 4000) : body.slice(0, 4000);
    return { snippet, full: body.slice(0, 8000) };
  });
  console.log('\n=== PixelScan fingerprint-check ===');
  console.log(r.snippet);
  console.log('\n--- API responses ---');
  apiData.slice(0,3).forEach(d => {
    const s = JSON.stringify(d.j);
    if (s.length > 50 && s.length < 8000) console.log('URL:', d.url, '\n', s.slice(0, 2000));
  });
  await browser.close();
}
console.log('\n=== DONE ===');
