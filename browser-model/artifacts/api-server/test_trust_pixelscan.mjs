// trust score from CreepJS DOM + PixelScan accordion detail
import { chromium } from 'playwright';
import { readFileSync } from 'fs';

const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY  = 'socks5://127.0.0.1:10854';

const tsSrc = readFileSync('./src/lib/renderer.ts', 'utf8');
const STEALTH_INIT = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/) || [])[1] || '';
const WORKER_STEALTH_PATCH = (tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/) || [])[1] || '';
console.log('STEALTH_INIT:', STEALTH_INIT.length, 'WORKER:', WORKER_STEALTH_PATCH.length);

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
const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36';

async function makeBrowser() {
  const browser = await chromium.launch({
    headless: false, executablePath: BINARY, args: ARGS,
    ignoreDefaultArgs: ['--enable-automation'],
    env: { ...process.env, DISPLAY: ':99', LANG: 'en_US.UTF-8' },
  });
  const ctx = await browser.newContext({
    userAgent: UA, viewport: { width: 1920, height: 1040 }, locale: 'en-US',
    timezoneId: 'America/Los_Angeles', screen: { width: 1920, height: 1080 },
  });
  await ctx.addInitScript(STEALTH_INIT);
  ctx.on('page', p => p.on('worker', w => w.evaluate(WORKER_STEALTH_PATCH).catch(() => {})));
  return { browser, ctx };
}

// ─── 1) CreepJS: full DOM extraction for trust score ────────────────────────
{
  console.log('\n[CreepJS] loading...');
  const { browser, ctx } = await makeBrowser();
  const page = await ctx.newPage();
  await page.goto('https://abrahamjuliot.github.io/creepjs/', { timeout: 90000, waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(30000);

  const r = await page.evaluate(() => {
    const allText = document.body ? document.body.innerText : '';
    const lines = allText.split('\n').map(l => l.trim()).filter(l => l);
    const pcts = [...allText.matchAll(/(\d+(?:\.\d+)?)\s*%/g)].map(m => m[0]);

    // Leaf numeric elements (trust score is often a bare number)
    const leafNums = [];
    document.querySelectorAll('*').forEach(el => {
      if (el.children.length > 0) return;
      const t = (el.textContent || '').trim();
      if (/^\d{1,3}$/.test(t) || /^\d{1,3}%$/.test(t)) {
        leafNums.push({ tag: el.tagName, cls: String(el.className || '').slice(0,60), text: t });
      }
    });

    // Section/article headings with associated values
    const sections = [];
    document.querySelectorAll('section, article, [id], [class*="section"], [class*="result"]').forEach(el => {
      const t = (el.textContent || '').trim().slice(0, 200);
      if (t && t.length > 3) sections.push({ id: el.id || '', cls: String(el.className||'').slice(0,50), t });
    });

    return {
      lines: lines.slice(0, 120),
      pcts: [...new Set(pcts)],
      leafNums: leafNums.slice(0, 40),
      sections: sections.slice(0, 30),
    };
  });

  console.log('=== CreepJS ===');
  console.log('All % values:', r.pcts);
  console.log('Leaf numeric elements:');
  r.leafNums.forEach(e => console.log('  ', e.tag, e.cls.slice(0,40), '=', e.text));
  console.log('\nAll page lines:');
  r.lines.forEach(l => console.log('  ', l.slice(0, 130)));
  await browser.close();
}

// ─── 2) PixelScan /bot-check: expanded accordion + API capture ───────────────
{
  console.log('\n[PixelScan bot-check] loading...');
  const { browser, ctx } = await makeBrowser();
  const page = await ctx.newPage();

  const scanAPI = [];
  page.on('response', async res => {
    try {
      const url = res.url();
      const ct = res.headers()['content-type'] || '';
      const skip = ['unleash','s/api/m','assets','static','font','ico','png','svg','woff'];
      if (!ct.includes('json')) return;
      if (skip.some(s => url.includes(s))) return;
      const j = await res.json().catch(() => null);
      if (j && url.includes('pixelscan')) scanAPI.push({ url: url.slice(0,150), j });
    } catch(_){}
  });

  await page.goto('https://pixelscan.net/bot-check', { timeout: 60000, waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(20000);

  // expand every possible accordion
  const triggerSels = [
    '[class*="accordion__trigger"]',
    '[class*="accordion-trigger"]',
    '[class*="item__header"]',
    '[class*="check__header"]',
    '[class*="bot-check"] button',
    '[class*="checker"] button',
  ];
  let clicked = 0;
  for (const sel of triggerSels) {
    try {
      const els = page.locator(sel);
      const count = await els.count();
      for (let i = 0; i < count; i++) {
        await els.nth(i).click({ timeout: 1500 }).catch(() => {});
        clicked++;
      }
    } catch(_){}
  }
  console.log('expanded', clicked, 'accordion items');
  await page.waitForTimeout(8000);

  const r = await page.evaluate(() => {
    const body = document.body ? document.body.innerText : '';
    const lines = body.split('\n').map(l => l.trim()).filter(l => l && l.length > 1);

    // Collect all elements with status/result/badge/label class
    const statusEls = [];
    document.querySelectorAll('[class*="status"],[class*="result"],[class*="badge"],[class*="alert"],[class*="clear"]').forEach(el => {
      const text = (el.textContent || '').trim().slice(0, 150);
      if (text && text.length < 200) {
        statusEls.push({ cls: String(el.className||'').slice(0,80), text });
      }
    });

    // Accordion items with their full content
    const accordionItems = [];
    document.querySelectorAll('[class*="accordion__item"],[class*="accordion-item"],[class*="check-item"],[class*="bot-check__item"]').forEach(el => {
      accordionItems.push((el.textContent || '').trim().slice(0, 300));
    });

    return { lines: lines.slice(0, 300), statusEls: statusEls.slice(0, 80), accordionItems };
  });

  console.log('\n=== PixelScan bot-check: detection-related lines ===');
  r.lines.filter(l =>
    /detect|clear|bot|pass|fail|alert|navigator|webdriver|cdp|agent|plugin|hardware|canvas|webgl|font|audio|screen|timezone|language|automat|parameter|human|ok\b/i.test(l)
  ).forEach(l => console.log('  ', l.slice(0, 150)));

  console.log('\n=== Accordion items ===');
  r.accordionItems.forEach((item, i) => console.log(`[${i}]`, item.slice(0, 200)));

  console.log('\n=== Status class elements ===');
  r.statusEls.forEach(e => console.log(e.cls.slice(0,50), '|', e.text.slice(0,100)));

  console.log('\n=== Scan API JSON responses ===');
  scanAPI.forEach(d => {
    console.log('URL:', d.url);
    const s = JSON.stringify(d.j, null, 2);
    console.log(s.slice(0, 5000));
    console.log('---');
  });

  await browser.close();
}
console.log('\n=== ALL DONE ===');
