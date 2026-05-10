// PixelScan + Selenium Detector – checks DOM visibility to determine real result
import { chromium } from 'playwright';
import { readFileSync } from 'fs';
const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY  = 'socks5://127.0.0.1:10854';
const tsSrc  = readFileSync('./src/lib/renderer.ts', 'utf8');
const STEALTH_INIT         = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/) || [])[1] || '';
const WORKER_STEALTH_PATCH = (tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/) || [])[1] || '';
console.log('SI:', STEALTH_INIT.length, 'WP:', WORKER_STEALTH_PATCH.length);

const ARGS = [
  '--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled',
  '--no-first-run','--no-default-browser-check','--mute-audio','--lang=en-US',
  '--use-gl=angle','--use-angle=swiftshader','--enable-webgl',
  '--proxy-server='+PROXY,'--disable-quic','--proxy-resolves-dns-locally',
  '--window-size=1920,1080',
  '--fingerprint='+String(Math.floor(Math.random()*0x7fffffff)),
  '--fingerprint-platform=linux','--fingerprint-brand=Chrome',
  '--fingerprint-brand-version=144','--fingerprint-hardware-concurrency=8',
  '--timezone=America/Los_Angeles','--disable-non-proxied-udp',
];

const browser = await chromium.launch({
  headless: false, executablePath: BINARY, args: ARGS,
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

// ─── PixelScan bot-check with visibility detection ─────────────────────────
console.log('\n=== PixelScan /bot-check ===');
{
  const ctx = await mkCtx();
  const page = await ctx.newPage();
  await page.goto('https://pixelscan.net/bot-check', { timeout: 60000, waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(20000);

  const result = await page.evaluate(() => {
    // Check which summary H2 is visible
    const h2s = [...document.querySelectorAll('h2')].map(el => {
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      return {
        text: el.textContent.trim(),
        visible: rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0',
        display: style.display,
        rect: { w: Math.round(rect.width), h: Math.round(rect.height) },
      };
    });
    // Check accordion detected items
    const detected = [...document.querySelectorAll('[class*="status--a"],[class*="detected"],[class*="bot-detected"]')].map(el => ({
      text: el.textContent.trim(),
      class: el.className,
      visible: el.getBoundingClientRect().height > 0,
    }));
    // Summary section
    const summarySection = document.querySelector('[class*="summary-section"]');
    const summaryText = summarySection ? summarySection.textContent.trim().slice(0, 200) : 'not found';
    // All visible status spans
    const statusSpans = [...document.querySelectorAll('[class*="status"]')].filter(el => {
      const r = el.getBoundingClientRect();
      return r.height > 0 && r.width > 0;
    }).map(el => ({ cls: el.className.slice(0, 60), text: el.textContent.trim().slice(0, 50) }));

    return { h2s, detected, summaryText, statusSpans };
  });

  console.log('H2 visibility:');
  result.h2s.forEach(h => console.log('  vis='+h.visible, h.display, h.rect.w+'x'+h.rect.h, ':', JSON.stringify(h.text).slice(0,60)));
  console.log('\nSummary section:', result.summaryText.slice(0, 200));
  console.log('\nVisible status spans:');
  result.statusSpans.forEach(s => console.log('  ['+s.cls+']:', s.text));
  console.log('\nDetected elements:');
  result.detected.forEach(d => console.log('  vis='+d.visible, '['+d.class.slice(0,50)+']:', d.text.slice(0,50)));
  await ctx.close();
}

// ─── Selenium Detector ─────────────────────────────────────────────────────
console.log('\n=== Selenium Detector (hmaker.github.io) ===');
{
  const ctx = await mkCtx();
  const page = await ctx.newPage();
  await page.goto('https://hmaker.github.io/selenium-detector/', { timeout: 60000, waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(8000);
  const result = await page.evaluate(() => {
    const t = document.body ? document.body.innerText : '';
    const detected = [...document.querySelectorAll('[class*="detected"],[class*="selenium"],[id*="result"]')].map(el => ({
      id: el.id, cls: el.className.slice(0, 50), text: el.textContent.trim().slice(0, 100),
      visible: el.getBoundingClientRect().height > 0,
    }));
    return { lines: t.split('\n').filter(l => l.trim()).slice(0, 40), detected };
  });
  result.lines.forEach(l => console.log(' ', l));
  if (result.detected.length) {
    console.log('Detected elements:');
    result.detected.forEach(d => console.log('  vis='+d.visible, d.id, d.cls, ':', d.text));
  }
  await ctx.close();
}

// ─── IPHey bot-check specifically ─────────────────────────────────────────
console.log('\n=== IPHey /bot-check page ===');
{
  const ctx = await mkCtx();
  const page = await ctx.newPage();
  const apiResults = [];
  page.on('response', async r => {
    try {
      const ct = r.headers()['content-type'] || '';
      if (!ct.includes('json')) return;
      const j = await r.json().catch(() => null);
      if (!j) return;
      const s = JSON.stringify(j);
      if (/bot|trust|score|human|genuine|result|status/i.test(s) && s.length < 2000) {
        apiResults.push({ url: r.url().slice(0, 80), data: s.slice(0, 400) });
      }
    } catch (e) {}
  });
  await page.goto('https://iphey.com/', { timeout: 60000, waitUntil: 'networkidle' });
  await page.waitForTimeout(8000);
  // Try clicking "Restart Test" or wait for completion
  const result = await page.evaluate(() => {
    const t = document.body ? document.body.innerText : '';
    const statusEls = [...document.querySelectorAll('*')].filter(el => {
      const txt = (el.textContent || '').trim();
      const r = el.getBoundingClientRect();
      return txt.length > 0 && txt.length < 80 && el.children.length === 0
        && r.height > 0 && r.width > 0
        && /trustworthy|genuine|human|bot|suspicious|clean|risk|score|safe|unsafe|good|bad|pass|fail|\d+%/i.test(txt);
    }).map(el => ({
      tag: el.tagName,
      cls: el.className.slice(0, 50),
      text: el.textContent.trim(),
    })).slice(0, 20);
    const lines = t.split('\n').filter(l =>
      l.trim() && /trustworthy|genuine|human|bot|suspicious|clean|risk|score|safe|unsafe|good|bad|\d+%/i.test(l)
    ).slice(0, 15);
    return { statusEls, lines, raw: t.slice(0, 1000) };
  });
  console.log('Status elements:');
  result.statusEls.forEach(e => console.log('  ['+e.tag+'.'+e.cls+']:', e.text));
  console.log('Lines:', result.lines);
  if (!result.statusEls.length && !result.lines.length) console.log('raw:', result.raw.slice(0, 400));
  if (apiResults.length) apiResults.forEach(r => console.log('API:', r.url, r.data));
  await ctx.close();
}

await browser.close();
console.log('\n=== ALL DONE ===');
