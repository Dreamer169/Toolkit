import { chromium } from 'playwright';


const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY  = 'socks5://127.0.0.1:10854';

// Read STEALTH_INIT from built dist

const { STEALTH_INIT, WORKER_STEALTH_PATCH } = await import("/tmp/read_stealth.mjs");
const STEALTH_INIT = m ? m[1] : '';



const WORKER_STEALTH_PATCH = WORKER_PATCH_M ? WORKER_PATCH_M[1] : '';


const browser = await chromium.launch({
  executablePath: BINARY,
  headless: true,
  proxy: { server: PROXY },
  args: [
    '--no-sandbox','--disable-setuid-sandbox','--disable-dev-shm-usage',
    '--disable-blink-features=AutomationControlled',
    `--fingerprint=${Math.random()*0x7fffffff|0}`,
    '--fingerprint-platform=linux','--fingerprint-brand=Chrome',
    '--fingerprint-brand-version=144','--fingerprint-hardware-concurrency=8',
    '--disable-spoofing=gpu',
    '--lang=en-US','--accept-lang=en-US,en',
    '--timezone=America/Los_Angeles',
  ],
  env: { ...process.env, DISPLAY: ':99' },
});

const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 } });
await ctx.addInitScript(STEALTH_INIT);
ctx.on('page', (p) => { p.on('worker', (w) => { w.evaluate(WORKER_STEALTH_PATCH).catch(() => {}); }); });

const page = await ctx.newPage();
console.log('Navigating to CreepJS...');
await page.goto('https://abrahamjuliot.github.io/creepjs/', { waitUntil: 'networkidle', timeout: 90000 });
await page.waitForTimeout(12000);

// Read summary badges
const results = await page.evaluate(() => {
  const badges = {};
  document.querySelectorAll('[id]').forEach(el => {
    const id = el.id;
    const txt = el.textContent?.trim();
    if (id && txt) badges[id] = txt;
  });
  // Also grab headings/spans with % scores
  const scores = {};
  document.querySelectorAll('strong, .bold, [class*="grade"], [class*="score"]').forEach(el => {
    const txt = el.textContent?.trim();
    if (txt && (txt.includes('%') || txt.match(/^[A-F][+-]?$/))) {
      scores[el.className || el.tagName] = txt;
    }
  });
  // Get the raw page text for key terms
  const body = document.body?.innerText || '';
  const lines = body.split('\n').filter(l => 
    l.includes('headless') || l.includes('stealth') || l.includes('like') || 
    l.includes('Grade') || l.includes('%')
  );
  return { lines: lines.slice(0, 30) };
});

console.log('=== CreepJS Results ===');
results.lines.forEach(l => console.log(l.trim()));

await browser.close();
