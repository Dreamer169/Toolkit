// 精确复制 fp_quick.mjs 的 launch 参数 — 已知在该机器上可工作
// 区别：去掉 fp_quick 的 minimal init, 改用完整 STEALTH_INIT + WORKER patch
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

const browser = await chromium.launch({
  headless: false,
  executablePath: BINARY,
  args: [
    '--no-sandbox', '--disable-dev-shm-usage',
    '--disable-blink-features=AutomationControlled',
    '--no-first-run', '--no-default-browser-check', '--mute-audio',
    '--lang=en-US', '--use-fake-ui-for-media-stream',
    '--use-gl=angle', '--use-angle=swiftshader', '--enable-webgl',
    `--proxy-server=${PROXY}`, '--disable-quic', '--proxy-resolves-dns-locally',
    '--dns-over-https-mode=secure',
    '--dns-over-https-templates=https://1.1.1.1/dns-query',
    '--window-size=1920,1080',
    `--fingerprint=${Math.floor(Math.random()*0x7fffffff)}`,
    '--fingerprint-platform=linux', '--fingerprint-brand=Chrome',
    '--fingerprint-brand-version=144', '--fingerprint-hardware-concurrency=8',
    '--timezone=America/Los_Angeles', '--disable-non-proxied-udp',
  ],
  ignoreDefaultArgs: ['--enable-automation'],
  env: { ...process.env, DISPLAY: ':99', LANG: 'en_US.UTF-8' },
});

const ctx = await browser.newContext({
  userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
  viewport: { width: 1920, height: 1040 },
  locale: 'en-US',
  timezoneId: 'America/Los_Angeles',
  screen: { width: 1920, height: 1080 },
});
await ctx.addInitScript(STEALTH_INIT);
ctx.on('page', (p) => { p.on('worker', (w) => { w.evaluate(WORKER_STEALTH_PATCH).catch(() => {}); }); });

const page = await ctx.newPage();
console.log('Going to CreepJS...');
await page.goto('https://abrahamjuliot.github.io/creepjs/', {
  timeout: 90000, waitUntil: 'domcontentloaded',
});
await page.waitForTimeout(25000);

const result = await page.evaluate(() => {
  const body = document.body?.innerText || '';
  const lines = body.split('\n').map(l => l.trim()).filter(l =>
    l && /headless|stealth|like headless|^0%|Grade|chromium:|Worker|SharedWorker/i.test(l)
  );
  const grade = document.querySelector('[class*="grade"]')?.textContent?.trim() || '';
  return { lines: lines.slice(0, 35), grade };
});

console.log('=== CreepJS (headless:false + STEALTH_INIT) ===');
result.lines.forEach(l => console.log(' ', l));
if (result.grade) console.log('Grade:', result.grade);

await browser.close();
