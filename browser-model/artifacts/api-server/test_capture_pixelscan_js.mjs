// Capture PixelScan JS bundles to find Navigator check logic
import { chromium } from 'playwright';
import { readFileSync, writeFileSync } from 'fs';
const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY  = 'socks5://127.0.0.1:10854';
const tsSrc  = readFileSync('./src/lib/renderer.ts', 'utf8');
const STEALTH_INIT = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/) || [])[1] || '';

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

const page = await ctx.newPage();

// Capture JS bundles
const jsBundles = [];
page.on('response', async r => {
  const url = r.url();
  if (!url.includes('pixelscan')) return;
  const ct = r.headers()['content-type'] || '';
  if (ct.includes('javascript') || url.endsWith('.js')) {
    try {
      const body = await r.text();
      jsBundles.push({ url: url.slice(-100), size: body.length, body });
    } catch (_) {}
  }
});

await page.goto('https://pixelscan.net/bot-check', { timeout: 60000, waitUntil: 'networkidle' });
await page.waitForTimeout(5000);

// Find the largest JS bundle (main app bundle)
const sorted = jsBundles.sort((a, b) => b.size - a.size);
console.log('Captured JS bundles:');
sorted.slice(0, 5).forEach(b => console.log(' ', b.size, 'bytes:', b.url.slice(-60)));

// Save the largest one
if (sorted.length > 0) {
  const main = sorted[0];
  writeFileSync('/tmp/pixelscan_main.js', main.body);
  console.log('\nSaved main bundle:', main.url.slice(-80), '(', main.size, 'bytes)');
  
  // Search for navigator-related checks in the bundle
  const body = main.body;
  
  // Find navigator property references
  const navProps = body.match(/navigator\s*\.\s*(\w+)/g) || [];
  const propCounts = {};
  navProps.forEach(p => {
    const name = p.replace(/navigator\s*\.\s*/, '');
    propCounts[name] = (propCounts[name] || 0) + 1;
  });
  console.log('\nNavigator properties accessed (sorted by count):');
  Object.entries(propCounts)
    .sort(([,a],[,b]) => b - a)
    .slice(0, 30)
    .forEach(([k, v]) => console.log(`  ${k}: ${v}`));
  
  // Find "Detected" patterns with nearby context
  const detectedPattern = /["']Detected["']|['"]detected["']|isDetected|detected\s*:/gi;
  let m;
  const detectedContexts = [];
  while ((m = detectedPattern.exec(body)) !== null && detectedContexts.length < 10) {
    const ctx = body.slice(Math.max(0, m.index - 100), m.index + 100);
    detectedContexts.push(ctx);
  }
  console.log('\n"Detected" contexts in JS bundle:');
  detectedContexts.forEach(c => console.log(' ---\n  ', c.replace(/\n/g,' ').slice(0, 150)));
  
  // Also search for hasOwnProperty checks
  const ownPropMatches = body.match(/hasOwnProperty[^;]{0,60}/g) || [];
  console.log('\nhasOwnProperty checks:');
  ownPropMatches.slice(0, 15).forEach(m => console.log(' ', m.slice(0, 100)));
}

await browser.close();
console.log('\n=== DONE ===');
