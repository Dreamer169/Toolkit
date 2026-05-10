// Aggressively expand ALL PixelScan accordion items to see all failing checks
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

// Wait for check to complete
await page.waitForTimeout(25000);

// Strategy: use shadow DOM piercing + evaluate to get all text from the page
const result = await page.evaluate(() => {
  // Walk DOM tree to find all text nodes inside accordion-like structures
  function getAllText(el, depth = 0) {
    if (depth > 6) return [];
    const results = [];
    const text = el.textContent.trim();
    if (!text) return results;
    
    // Look for status indicators
    const classes = el.className || '';
    const tag = el.tagName || '';
    const isStatusEl = classes.includes('status') || classes.includes('badge') || classes.includes('pill') || classes.includes('label');
    const isContainerEl = classes.includes('accordion') || classes.includes('check') || classes.includes('param') || classes.includes('row') || classes.includes('item');
    
    if (isStatusEl && (text === 'Detected' || text === 'Clear')) {
      // Find sibling/parent label
      const par = el.closest('[class*="accordion"],[class*="check"],[class*="param"],[class*="row"],[class*="item"]') || el.parentElement;
      const label = par ? par.innerText.replace(text,'').trim().split('\n')[0].trim() : '';
      results.push({ status: text, label: label.slice(0, 60) });
    }
    
    for (const child of el.children) {
      results.push(...getAllText(child, depth + 1));
    }
    return results;
  }
  
  // Get ALL text statuses
  const statuses = getAllText(document.body);
  
  // Also try: get all elements with specific Angular component attributes and walk their children
  // Look for the bot-check component
  const checkEls = document.querySelectorAll('[_nghost-serverapp-c36],[_nghost-serverapp-c37],[_nghost-serverapp-c38]');
  const checkTexts = [];
  checkEls.forEach(el => {
    // Get all text in accordion-like rows
    const rows = el.querySelectorAll('[class*="accordion__item"],[class*="accordion--item"],[class*="row"],[class*="param"]');
    rows.forEach(row => {
      const txt = row.innerText.trim().replace(/\n+/g, ' | ').slice(0, 120);
      if (txt && (txt.includes('Detected') || txt.includes('Clear'))) {
        checkTexts.push(txt);
      }
    });
  });
  
  // Also: dump ALL text from the page structured
  const allSections = document.querySelectorAll('[class*="section"],[class*="container"],[class*="wrapper"],[class*="checker"]');
  const sectionTexts = [];
  allSections.forEach(el => {
    const txt = el.innerText.trim().replace(/\n+/g, '\n').slice(0, 300);
    if (txt.includes('Detected') || txt.includes('Clear')) {
      sectionTexts.push(txt);
    }
  });
  
  // Get ALL spans/divs with "Detected" or "Clear" text
  const allStatusSpans = [...document.querySelectorAll('*')].filter(el => {
    const t = el.textContent.trim();
    return (t === 'Detected' || t === 'Clear') && el.children.length === 0;
  });
  
  const spanContexts = allStatusSpans.map(el => {
    const parent3 = el.parentElement && el.parentElement.parentElement && el.parentElement.parentElement.parentElement;
    return {
      status: el.textContent.trim(),
      context: (parent3 ? parent3.innerText : (el.parentElement ? el.parentElement.innerText : '')).trim().replace(/\n+/g, ' | ').slice(0, 150)
    };
  });
  
  return { statuses, checkTexts, sectionTexts: sectionTexts.slice(0, 5), spanContexts };
});

console.log('\n=== All status entries (walking DOM) ===');
result.statuses.forEach(s => {
  const marker = s.status === 'Detected' ? '❌' : '✅';
  console.log(marker, s.status.padEnd(10), '|', s.label);
});

console.log('\n=== Angular component rows ===');
result.checkTexts.forEach(t => {
  const marker = t.includes('Detected') ? '❌' : '✅';
  console.log(marker, t);
});

console.log('\n=== ALL Status spans with context ===');
result.spanContexts.forEach(s => {
  const marker = s.status === 'Detected' ? '❌' : '✅';
  console.log(marker, s.status.padEnd(10), '|', s.context.slice(0, 120));
});

// Also run detailed navigator checks to see what might be problematic
const navDiag2 = await page.evaluate(async () => {
  const r = {};
  // Plugins own property check
  r.pluginsOwnProp = navigator.hasOwnProperty('plugins');
  r.mimeTypesOwnProp = navigator.hasOwnProperty('mimeTypes');
  r.pdfViewerEnabledOwnProp = navigator.hasOwnProperty('pdfViewerEnabled');
  
  // Webdriver descriptor
  const wdDesc = Object.getOwnPropertyDescriptor(navigator, 'webdriver');
  r.webdriverOwnDescriptor = wdDesc ? JSON.stringify(wdDesc) : 'null (not own)';
  
  // getBattery result
  try {
    const bat = await navigator.getBattery();
    r.batteryResult = { charging: bat.charging, level: bat.level, chargingTime: bat.chargingTime, dischargingTime: bat.dischargingTime };
  } catch (e) { r.batteryErr = String(e); }
  
  // connection details
  try {
    const c = navigator.connection;
    r.connectionDetail = { effectiveType: c.effectiveType, rtt: c.rtt, downlink: c.downlink, type: c.type, saveData: c.saveData };
    // Check if rtt/downlink are "too round"
    r.rttModulo = c.rtt % 25;
    r.downlinkModulo = (c.downlink * 10) % 1;
  } catch (e) { r.connectionErr = String(e); }
  
  // enumerateDevices - now native
  try {
    const devs = await navigator.mediaDevices.enumerateDevices();
    r.devices = devs.map(d => ({ kind: d.kind, label: d.label.slice(0,20), id: d.deviceId.slice(0,16) }));
  } catch (e) { r.devicesErr = String(e); }
  
  // Worker navigator check - post a task to a Worker and get its navigator properties  
  // Check if Worker exists
  r.workerExists = typeof Worker !== 'undefined';
  
  // window.chrome check
  r.chromeExists = !!window.chrome;
  r.chromeCsi = !!(window.chrome && window.chrome.csi);
  r.chromeLoadTimes = !!(window.chrome && window.chrome.loadTimes);
  r.chromeApp = !!(window.chrome && window.chrome.app);
  
  // Check Notification.permission
  try { r.notificationPerm = Notification.permission; } catch (_) {}
  
  return r;
});

console.log('\n=== Additional navigator diagnostics ===');
Object.entries(navDiag2).forEach(([k,v]) => console.log(' ', k+':', typeof v === 'object' ? JSON.stringify(v) : v));

await browser.close();
console.log('\n=== DONE ===');
