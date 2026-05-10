// Click on each PixelScan accordion item to see full check names and details
import { chromium } from 'playwright';
import { readFileSync } from 'fs';
const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY  = 'socks5://127.0.0.1:10854';
const tsSrc  = readFileSync('./src/lib/renderer.ts', 'utf8');
const STEALTH_INIT         = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/) || [])[1] || '';
const WORKER_STEALTH_PATCH = (tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/) || [])[1] || '';

const browser = await chromium.launch({
  headless: false, executablePath: BINARY,
  args: [
    '--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled',
    '--no-first-run','--no-default-browser-check','--mute-audio','--lang=en-US',
    '--use-gl=angle','--use-angle=swiftshader','--enable-webgl',
    '--proxy-server='+PROXY,'--disable-quic','--proxy-resolves-dns-locally',
    '--window-size=1920,1080',
    '--fingerprint='+String(Math.floor(Math.random()*0x7fffffff)),
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
await page.goto('https://pixelscan.net/bot-check', { timeout: 60000, waitUntil: 'domcontentloaded' });
await page.waitForTimeout(22000);

// Try to get check names by evaluating Angular component data
const result = await page.evaluate(() => {
  // Strategy 1: Use ng-reflect attributes or data attributes
  const items = [...document.querySelectorAll('[class*="accordion"]')];
  const itemData = items.map(el => {
    const title = el.querySelector('[class*="title"],[class*="label"],[class*="name"],[class*="heading"],[class*="param"]');
    const status = el.querySelector('[class*="status"]');
    const info = el.querySelector('[class*="info"],[class*="description"],[class*="value"],[class*="detail"]');
    return {
      outerHtml: el.outerHTML.slice(0, 500),
      title: title ? title.textContent.trim() : '',
      status: status ? status.textContent.trim() : '',
      info: info ? info.textContent.trim() : '',
      fullText: el.innerText ? el.innerText.trim().slice(0, 200) : '',
      ngAttrs: [...el.attributes].filter(a => a.name.startsWith('ng-') || a.name.startsWith('_ng')).map(a => a.name+'='+a.value).slice(0, 5),
    };
  }).filter(d => d.fullText);
  
  // Strategy 2: Get all text nodes adjacent to status spans
  const alertSpans = [...document.querySelectorAll('[class*="status--ale"]')];
  const alertData = alertSpans.map(el => {
    const parent = el.closest('[class*="accordion__item"],[class*="check-row"],[class*="bot-check"]') || el.parentElement;
    const sibling = parent ? parent.innerText : '';
    return { status: el.textContent.trim(), context: sibling.trim().slice(0, 150) };
  });

  // Strategy 3: Look at the navigator properties PixelScan is checking
  // by checking what the page has in its component data
  const ngElems = [...document.querySelectorAll('[ng-reflect-params],[ng-reflect-name],[data-param],[data-check]')];
  const ngData = ngElems.map(el => ({
    attrs: [...el.attributes].map(a => a.name+'='+a.value.slice(0,50)).join('; '),
    text: el.textContent.trim().slice(0, 80),
  })).slice(0, 20);

  return { itemData: itemData.slice(0, 30), alertData, ngData };
});

console.log('\n=== Accordion items with fullText ===');
result.itemData.forEach((item, i) => {
  if (item.status.includes('Detected') || item.status.includes('Clear')) {
    const marker = item.status.includes('Detected') ? '❌' : '✅';
    console.log(marker, `[${item.status}]`, item.title || item.fullText.replace(/\n/g,' ').slice(0,80));
  }
});

console.log('\n=== Alert spans with context ===');
result.alertData.forEach(d => {
  console.log('❌', d.context.replace(/\n/g, ' | '));
});

console.log('\n=== NG attrs ===');
result.ngData.forEach(d => console.log(d.attrs, ':', d.text));

// Also try to check navigator properties directly to see what's unusual
const navCheck = await page.evaluate(() => {
  return {
    userAgentData: typeof navigator.userAgentData,
    userAgentDataBrands: navigator.userAgentData ? JSON.stringify(navigator.userAgentData.brands) : 'null',
    getBattery: typeof navigator.getBattery,
    getBatteryOwnProp: navigator.hasOwnProperty('getBattery'),
    connectionOwnProp: navigator.hasOwnProperty('connection'),
    connection: navigator.connection ? JSON.stringify({
      effectiveType: navigator.connection.effectiveType,
      rtt: navigator.connection.rtt,
      downlink: navigator.connection.downlink,
      type: navigator.connection.type,
      proto: Object.getPrototypeOf(navigator.connection).constructor.name,
    }) : 'null',
    mediaDevicesProto: navigator.mediaDevices ? Object.getPrototypeOf(navigator.mediaDevices).constructor.name : 'null',
    permissionsQueryOwnProp: navigator.permissions && navigator.permissions.hasOwnProperty('query'),
    storageEstimateOwnProp: navigator.storage && navigator.storage.hasOwnProperty('estimate'),
    keyboardProto: navigator.keyboard ? Object.getPrototypeOf(navigator.keyboard).constructor.name : 'null',
    webkitGetUserMediaType: typeof navigator.webkitGetUserMedia,
    schedulingProto: navigator.scheduling ? Object.getPrototypeOf(navigator.scheduling).constructor.name : 'null',
  };
});
console.log('\n=== Navigator property diagnostics ===');
Object.entries(navCheck).forEach(([k, v]) => console.log(' ', k+':', v));

await browser.close();
console.log('\n=== DONE ===');
