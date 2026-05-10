import { chromium } from 'playwright';
import { readFileSync } from 'fs';
const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY  = 'socks5://127.0.0.1:10854';
const tsSrc = readFileSync('/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts', 'utf8');
const m1 = tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/);
const STEALTH_INIT = m1 ? m1[1] : '';
const m2 = tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/);
const WORKER_STEALTH_PATCH = m2 ? m2[1] : '';

async function launch() {
  return chromium.launch({
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

// IPHey
console.log('\n=== IPHey ===');
{
  const browser = await launch();
  const ctx = await makeCtx(browser);
  const page = await ctx.newPage();
  const apiResults = [];
  page.on('response', async r => {
    try {
      const ct = r.headers()['content-type']||'';
      if (ct.includes('json') && /iphey|ipapi|ipinfo|check|result/i.test(r.url())) {
        const j = await r.json().catch(()=>null);
        if (j) apiResults.push({url: r.url().slice(0,100), data: j});
      }
    } catch(e) {}
  });
  await page.goto('https://iphey.com/', {timeout:60000, waitUntil:'domcontentloaded'});
  await page.waitForTimeout(20000);
  const result = await page.evaluate(() => {
    const t = document.body?.innerText||'';
    // Look for the actual result indicator
    const statusEl = document.querySelector('[class*=status],[class*=result],[class*=score],[class*=trust],[class*=genuine]');
    return {
      status: statusEl?.textContent?.trim() || '',
      lines: t.split('\n').filter(l=>l.trim() && /trustworthy|genuine|human|bot|suspicious|score|good|bad|clean|risk|result|status/i.test(l)).slice(0,20),
      raw: t.slice(0,1500)
    };
  });
  console.log('Status:', result.status);
  result.lines.forEach(l=>console.log(' ', l));
  if (apiResults.length) apiResults.forEach(r=>console.log('API:', r.url, JSON.stringify(r.data).slice(0,300)));
  await browser.close();
}

// PixelScan fingerprint check
console.log('\n=== PixelScan /fingerprint-check ===');
{
  const browser = await launch();
  const ctx = await makeCtx(browser);
  const page = await ctx.newPage();
  const apiResults = [];
  page.on('response', async r => {
    try {
      const ct = r.headers()['content-type']||'';
      if (ct.includes('json') && /pixelscan|scan|check/i.test(r.url())) {
        const j = await r.json().catch(()=>null);
        if (j) apiResults.push({url: r.url().slice(0,100), data: j});
      }
    } catch(e) {}
  });
  await page.goto('https://pixelscan.net/fingerprint-check', {timeout:60000, waitUntil:'domcontentloaded'});
  await page.waitForTimeout(20000);
  const result = await page.evaluate(() => {
    const t = document.body?.innerText||'';
    return {
      lines: t.split('\n').filter(l=>l.trim() && /consistent|inconsistent|normal|suspicious|bot|human|score|pass|fail|detect|result|status|risk/i.test(l)).slice(0,25),
      raw: t.slice(0,2000)
    };
  });
  result.lines.forEach(l=>console.log(' ', l));
  if (apiResults.length) apiResults.slice(0,3).forEach(r=>console.log('API:', r.url, JSON.stringify(r.data).slice(0,400)));
  await browser.close();
}

// Brotector (new location)
console.log('\n=== Brotector / Selenium Detector ===');
{
  const browser = await launch();
  const ctx = await makeCtx(browser);
  const page = await ctx.newPage();
  await page.goto('https://hmaker.github.io/selenium-detector/', {timeout:60000, waitUntil:'domcontentloaded'});
  await page.waitForTimeout(8000);
  const result = await page.evaluate(() => {
    const t = document.body?.innerText||'';
    return { lines: t.split('\n').filter(l=>l.trim()).slice(0,40) };
  });
  result.lines.forEach(l=>console.log(' ', l));
  await browser.close();
}
