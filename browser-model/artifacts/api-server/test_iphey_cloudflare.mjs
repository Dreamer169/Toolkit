// Test IPHey + Cloudflare status + get proxy IP from pixelscan perspective
import { chromium } from 'playwright';
import { readFileSync } from 'fs';
const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY  = 'socks5://127.0.0.1:10854';
const tsSrc  = readFileSync('./src/lib/renderer.ts', 'utf8');
const m1 = tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/);
const m2 = tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/);
const STEALTH_INIT         = m1 ? m1[1] : '';
const WORKER_STEALTH_PATCH = m2 ? m2[1] : '';
console.log('STEALTH_INIT len:', STEALTH_INIT.length, 'WORKER len:', WORKER_STEALTH_PATCH.length);

const launchBrowser = async () => chromium.launch({
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

const makeCtx = async (browser) => {
  const ctx = await browser.newContext({
    userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
    viewport: { width: 1920, height: 1040 }, locale: 'en-US',
    timezoneId: 'America/Los_Angeles', screen: { width: 1920, height: 1080 },
  });
  await ctx.addInitScript(STEALTH_INIT);
  ctx.on('page', p => p.on('worker', w => w.evaluate(WORKER_STEALTH_PATCH).catch(() => {})));
  return ctx;
};

// --- Test 1: IP check via a simple page ---
{
  const browser = await launchBrowser();
  const ctx = await makeCtx(browser);
  const page = await ctx.newPage();
  try {
    await page.goto('https://httpbin.org/ip', { timeout: 30000, waitUntil: 'domcontentloaded' });
    const txt = await page.locator('body').textContent().catch(() => '');
    console.log('\n=== Proxy exit IP (httpbin.org) ===', txt.trim());
  } catch(e) { console.log('IP check failed:', e.message); }

  // Also check navigator.battery inside a page to verify Proxy works
  const page2 = await ctx.newPage();
  try {
    await page2.goto('about:blank');
    await ctx.addInitScript(STEALTH_INIT); // re-ensure
    const result = await page2.evaluate(async () => {
      try {
        const bat = await navigator.getBattery();
        // Return as string to avoid Infinity->null serialization
        return {
          charging: bat.charging, level: bat.level,
          chargingTime: bat.chargingTime,
          dischargingTime: String(bat.dischargingTime),  // avoid JSON null for Infinity
          hasProxy: typeof bat === 'object',
        };
      } catch(e) { return { err: String(e) }; }
    });
    console.log('\n=== Battery (blank page) ===', JSON.stringify(result));
  } catch(e) { console.log('Battery check failed:', e.message); }

  await browser.close();
}

// --- Test 2: IPHey ---
{
  const browser = await launchBrowser();
  const ctx = await makeCtx(browser);
  const page = await ctx.newPage();
  try {
    await page.goto('https://iphey.com/', { timeout: 60000, waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(18000);
    const body = await page.locator('body').textContent().catch(() => '');
    // IPHey shows trust score + IP + browser info
    const lines = body.split('\n').map(l=>l.trim()).filter(Boolean).slice(0,60);
    console.log('\n=== IPHey ===');
    lines.forEach(l => {
      if (/trust|score|browser|result|ip|vpn|proxy|datacenter|suspicious|safe|detected|clean|passed|check|bot|flag|normal|fake|real|firefox|chrome|linux|windows/i.test(l)) {
        console.log(' ', l.slice(0,100));
      }
    });
    // Screenshot for reference  
    await page.screenshot({ path: '/tmp/iphey.png', fullPage: false });
    console.log('IPHey screenshot saved');
  } catch(e) { console.log('IPHey failed:', e.message); }
  await browser.close();
}

// --- Test 3: Cloudflare bot detection ---
{
  const browser = await launchBrowser();
  const ctx = await makeCtx(browser);
  const page = await ctx.newPage();
  try {
    await page.goto('https://nowsecure.nl/', { timeout: 60000, waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(15000);
    const body = await page.locator('body').textContent().catch(() => '');
    const isBlocked = /blocked|cloudflare|just a moment|attention required|challenge/i.test(body);
    console.log('\n=== Cloudflare (nowsecure.nl) ===');
    console.log('Blocked:', isBlocked);
    const lines = body.split('\n').map(l=>l.trim()).filter(Boolean).slice(0,30);
    lines.forEach(l => {
      if (/success|fail|block|result|pass|check|bot|human|verify|challenge|recaptcha/i.test(l)) {
        console.log(' ', l.slice(0,120));
      }
    });
  } catch(e) { console.log('Cloudflare failed:', e.message); }
  await browser.close();
}

console.log('\n=== ALL TESTS DONE ===');
