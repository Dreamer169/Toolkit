// 模拟生产环境：headless:false + DISPLAY=:99 (Xvfb), 同 renderer.ts spawn 模式
import { chromium } from 'playwright';
import { readFileSync } from 'fs';

const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY  = 'socks5://127.0.0.1:10854';

const tsSrc = readFileSync('/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts', 'utf8');
const m1 = tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/);
const STEALTH_INIT = m1 ? m1[1] : '';
const m2 = tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/);
const WORKER_STEALTH_PATCH = m2 ? m2[1] : '';
console.log(`STEALTH_INIT:${STEALTH_INIT.length}  WORKER:${WORKER_STEALTH_PATCH.length}`);
if (!STEALTH_INIT.length) { console.error('STEALTH_INIT empty! abort'); process.exit(1); }

// headless: false = Xvfb visible mode, matches production renderer.ts (spawn + DISPLAY=:99)
const browser = await chromium.launch({
  executablePath: BINARY,
  headless: false,
  proxy: { server: PROXY },
  args: [
    '--no-sandbox','--disable-setuid-sandbox','--disable-dev-shm-usage',
    '--disable-blink-features=AutomationControlled',
    '--no-first-run','--no-default-browser-check',
    '--window-size=1920,1080',
    '--use-gl=angle','--use-angle=swiftshader','--enable-webgl',
    `--fingerprint=${Math.random()*0x7fffffff|0}`,
    '--fingerprint-platform=linux','--fingerprint-brand=Chrome',
    '--fingerprint-brand-version=144','--fingerprint-hardware-concurrency=8',
    '--lang=en-US','--accept-lang=en-US,en',
    '--timezone=America/Los_Angeles',
    '--disable-non-proxied-udp',
  ],
  env: { ...process.env, DISPLAY: ':99', LANG: 'en_US.UTF-8' },
  ignoreDefaultArgs: ['--enable-automation'],
});

const ctx = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  locale: 'en-US',
  timezoneId: 'America/Los_Angeles',
});
await ctx.addInitScript(STEALTH_INIT);
ctx.on('page', (p) => { p.on('worker', (w) => { w.evaluate(WORKER_STEALTH_PATCH).catch(() => {}); }); });

const page = await ctx.newPage();
console.log('Navigating to CreepJS...');
await page.goto('https://abrahamjuliot.github.io/creepjs/', { waitUntil: 'networkidle', timeout: 90000 });
await page.waitForTimeout(20000);

const result = await page.evaluate(() => {
  const body = document.body?.innerText || '';
  const lines = body.split('\n').map(l => l.trim()).filter(l =>
    l && (l.includes('headless') || l.includes('stealth') || l.includes('like') ||
          l.includes('Grade') || l.includes('%') || l.match(/^[A-F][+-]?\s*$/))
  );
  const grade = document.querySelector('[class*="grade"]')?.textContent?.trim() || '';
  return { lines: lines.slice(0, 40), grade };
});

console.log('=== CreepJS Results (headless:false / Xvfb) ===');
result.lines.forEach(l => console.log(' ', l));
if (result.grade) console.log('Grade:', result.grade);

await browser.close();
