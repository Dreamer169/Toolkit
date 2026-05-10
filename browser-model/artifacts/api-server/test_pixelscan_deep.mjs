// Deep PixelScan test: expand ALL accordion items + run exhaustive navigator diagnostics
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

// Intercept all JSON API responses
const apiData = [];
page.on('response', async r => {
  try {
    const ct = r.headers()['content-type'] || '';
    if (!ct.includes('json')) return;
    const url = r.url();
    if (!url.includes('pixelscan')) return;
    const j = await r.json().catch(() => null);
    if (j && JSON.stringify(j).length > 50) {
      apiData.push({ url: url.slice(-80), data: j });
    }
  } catch (_) {}
});

// Also intercept JS assets URLs 
const jsUrls = [];
page.on('response', r => {
  if (r.url().includes('pixelscan') && r.url().endsWith('.js')) jsUrls.push(r.url().slice(-100));
});

await page.goto('https://pixelscan.net/bot-check', { timeout: 60000, waitUntil: 'domcontentloaded' });
await page.waitForTimeout(24000);

// ─── Step 1: Try clicking all accordion headers to expand them ───
const expanded = await page.evaluate(async () => {
  // Click any collapsed accordion headers
  const togglable = [...document.querySelectorAll('[class*="accordion"] [class*="head"],[class*="accordion"] [class*="toggle"],[class*="accordion"] [class*="trigger"]')];
  togglable.forEach(el => { try { el.click(); } catch (_) {} });
  await new Promise(r => setTimeout(r, 800));

  // Now grab everything
  const rows = [];
  // Strategy A: all elements that look like check rows
  const checks = document.querySelectorAll('[class*="accordion__item"],[class*="param-row"],[class*="detection__item"],[class*="bot-check__item"],[class*="row--check"]');
  checks.forEach(el => {
    const txt = el.innerText.trim().replace(/\n/g,' | ').slice(0,120);
    if (txt) rows.push({ src:'A', txt });
  });

  // Strategy B: find all "status" spans and get their parent row text
  const statuses = document.querySelectorAll('[class*="status"],[class*="badge"],[class*="result"]');
  statuses.forEach(el => {
    const val = el.textContent.trim();
    if (!val || val.length > 30) return;
    const par = el.closest('[class*="item"],[class*="row"],[class*="check"],[class*="param"]') || el.parentElement;
    if (par) {
      const label = par.innerText.trim().replace(/\n/g,' | ').slice(0,120);
      if (label && label !== val) rows.push({ src:'B', val, label });
    }
  });

  // Strategy C: Angular component data via __ngContext__
  const ngData = [];
  const allEls = document.querySelectorAll('[_nghost-serverapp-c36] *,[_nghost-serverapp-c37] *');
  const seen = new Set();
  allEls.forEach(el => {
    const ctx = el.__ngContext__ || el.__ngContest__;
    if (ctx && !seen.has(el)) {
      seen.add(el);
      try {
        const s = JSON.stringify(ctx, (k,v) => typeof v === 'function' ? '[fn]' : v, 0).slice(0,300);
        if (s.includes('Detected') || s.includes('Clear') || s.includes('param')) ngData.push(s.slice(0,200));
      } catch (_) {}
    }
  });

  return { rows: rows.slice(0,40), ngData: ngData.slice(0,10) };
});

console.log('\n=== Accordion rows expanded ===');
expanded.rows.forEach(r => {
  const marker = r.val === 'Detected' || r.label?.includes('Detected') ? '❌' : (r.val === 'Clear' ? '✅' : '  ');
  console.log(marker, r.src, r.label || r.txt || '');
});

console.log('\n=== Angular context data ===');
expanded.ngData.forEach(d => console.log(' ', d));

console.log('\n=== API responses ===');
apiData.forEach(d => console.log(' URL:', d.url, '\n JSON:', JSON.stringify(d.data).slice(0,300)));

// ─── Step 2: Exhaustive navigator property diagnostics ───
const navDiag = await page.evaluate(async () => {
  const report = {};

  // Own property checks
  const ownProps = ['languages','language','userAgent','platform','hardwareConcurrency','deviceMemory',
    'maxTouchPoints','appName','appCodeName','appVersion','product','productSub','vendor','vendorSub',
    'onLine','cookieEnabled','doNotTrack','webdriver','getBattery','connection','storage','permissions',
    'mediaDevices','keyboard','scheduling','userActivation','geolocation','clipboard','credentials',
    'locks','wakeLock','serviceWorker','mimeTypes','plugins','presentation'];
  report.navOwnProps = {};
  ownProps.forEach(k => {
    try { report.navOwnProps[k] = navigator.hasOwnProperty(k); } catch (_) {}
  });

  // Prototype chain checks
  report.protoOwnProps = {};
  ownProps.forEach(k => {
    try {
      const np = Object.getPrototypeOf(navigator);
      report.protoOwnProps[k] = np && np.hasOwnProperty(k);
    } catch (_) {}
  });

  // Value checks
  report.values = {
    languages: JSON.stringify(navigator.languages),
    language: navigator.language,
    platform: navigator.platform,
    hardwareConcurrency: navigator.hardwareConcurrency,
    deviceMemory: navigator.deviceMemory,
    maxTouchPoints: navigator.maxTouchPoints,
    onLine: navigator.onLine,
    cookieEnabled: navigator.cookieEnabled,
    doNotTrack: navigator.doNotTrack,
    webdriver: navigator.webdriver,
    plugins: navigator.plugins.length,
    mimeTypes: navigator.mimeTypes.length,
  };

  // MediaDevices checks
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    report.mediaDevices = devices.map(d => ({ kind: d.kind, label: d.label, id: d.deviceId.slice(0,8) }));
    report.mediaDevicesOwnEnum = navigator.mediaDevices.hasOwnProperty('enumerateDevices');
    report.mediaDevicesOwnGUM = navigator.mediaDevices.hasOwnProperty('getUserMedia');
  } catch (e) { report.mediaDevicesErr = String(e); }

  // Permissions checks
  try {
    const perm = await navigator.permissions.query({ name: 'notifications' });
    report.permsNotify = { state: perm.state, hasOnChange: 'onchange' in perm };
    report.permsQueryOwnProp = navigator.permissions.hasOwnProperty('query');
  } catch (e) { report.permsErr = String(e); }

  // Storage checks
  try {
    const est = await navigator.storage.estimate();
    report.storageEst = { quota: est.quota, usage: est.usage };
    report.storageEstOwnProp = navigator.storage.hasOwnProperty('estimate');
  } catch (e) { report.storageEstErr = String(e); }

  // Connection checks
  try {
    const c = navigator.connection;
    report.connection = {
      effectiveType: c.effectiveType,
      rtt: c.rtt,
      downlink: c.downlink,
      type: c.type,
      ownProp: navigator.hasOwnProperty('connection'),
      protoName: c ? Object.getPrototypeOf(c).constructor.name : null,
      saveData: c.saveData,
    };
  } catch (e) { report.connectionErr = String(e); }

  // getBattery checks
  try {
    const bat = await navigator.getBattery();
    report.battery = {
      charging: bat.charging,
      level: bat.level,
      chargingTime: bat.chargingTime,
      dischargingTime: bat.dischargingTime,
      ownProp: navigator.hasOwnProperty('getBattery'),
      protoHasIt: Object.getPrototypeOf(navigator).hasOwnProperty('getBattery'),
    };
  } catch (e) { report.batteryErr = String(e); }

  // CSS media query checks
  report.mediaQueries = {
    prefersColorScheme: window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light',
    prefersColorSchemeLight: window.matchMedia('(prefers-color-scheme: light)').matches,
    pointer: window.matchMedia('(pointer: fine)').matches ? 'fine' : (window.matchMedia('(pointer: coarse)').matches ? 'coarse' : 'none'),
    hover: window.matchMedia('(hover: hover)').matches ? 'hover' : 'none',
    anyPointer: window.matchMedia('(any-pointer: fine)').matches ? 'fine' : (window.matchMedia('(any-pointer: coarse)').matches ? 'coarse' : 'none'),
    colorDepth: screen.colorDepth,
    pixelDepth: screen.pixelDepth,
  };

  // WebGL diagnostics
  try {
    const cvs = document.createElement('canvas');
    const gl = cvs.getContext('webgl') || cvs.getContext('experimental-webgl');
    const ext = gl && gl.getExtension('WEBGL_debug_renderer_info');
    report.webgl = {
      vendor: ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : gl ? gl.getParameter(gl.VENDOR) : 'none',
      renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl ? gl.getParameter(gl.RENDERER) : 'none',
      version: gl ? gl.getParameter(gl.VERSION) : 'none',
    };
  } catch (e) { report.webglErr = String(e); }

  // Timezone
  report.timezone = {
    jsTimezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    offset: new Date().getTimezoneOffset(),
  };

  // screen
  report.screen = {
    width: screen.width, height: screen.height,
    colorDepth: screen.colorDepth,
    orientation: screen.orientation ? screen.orientation.type : 'unknown',
  };

  return report;
});

console.log('\n=== Nav own properties (should all be false for real Chrome) ===');
const ownProbs = Object.entries(navDiag.navOwnProps).filter(([,v]) => v === true);
if (ownProbs.length === 0) console.log('  ✅ No own properties detected on navigator');
else ownProbs.forEach(([k,v]) => console.log(`  ❌ navigator.hasOwnProperty('${k}') = ${v}`));

console.log('\n=== Prototype own properties ===');
const protoHas = Object.entries(navDiag.protoOwnProps).filter(([,v]) => v === true);
protoHas.slice(0,10).forEach(([k]) => console.log(`  ✅ Navigator.prototype.hasOwnProperty('${k}')`));

console.log('\n=== Values ===');
Object.entries(navDiag.values).forEach(([k,v]) => console.log(' ', k+':', v));

console.log('\n=== MediaDevices ===');
console.log('  enumerateDevices own prop:', navDiag.mediaDevicesOwnEnum, '(should be false)');
console.log('  getUserMedia own prop:', navDiag.mediaDevicesOwnGUM, '(should be false)');
if (navDiag.mediaDevices) navDiag.mediaDevices.forEach(d => console.log(' ', JSON.stringify(d)));

console.log('\n=== Permissions ===');
console.log('  query own prop:', navDiag.permsQueryOwnProp, '(should be false)');
console.log('  notifications:', JSON.stringify(navDiag.permsNotify));

console.log('\n=== Storage ===');
console.log('  estimate own prop:', navDiag.storageEstOwnProp, '(should be false)');
console.log('  estimate result:', JSON.stringify(navDiag.storageEst));

console.log('\n=== Battery ===');
console.log(JSON.stringify(navDiag.battery || navDiag.batteryErr));

console.log('\n=== Connection ===');
console.log(JSON.stringify(navDiag.connection || navDiag.connectionErr));

console.log('\n=== CSS Media Queries ===');
Object.entries(navDiag.mediaQueries).forEach(([k,v]) => console.log(' ', k+':', v));

console.log('\n=== WebGL ===');
console.log(JSON.stringify(navDiag.webgl || navDiag.webglErr));

console.log('\n=== Timezone ===', JSON.stringify(navDiag.timezone));
console.log('=== Screen ===', JSON.stringify(navDiag.screen));

await browser.close();
console.log('\n=== DONE ===');
