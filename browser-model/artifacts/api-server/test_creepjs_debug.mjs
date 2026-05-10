// 抓取 CreepJS 输出的完整 headless 检测项（包括细分项）
import { chromium } from 'playwright';
const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY  = 'socks5://127.0.0.1:10854';
const UA     = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36';
const browser = await chromium.launch({
  headless: false, executablePath: BINARY,
  args: ['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled',
    '--no-first-run','--no-default-browser-check','--mute-audio','--lang=en-US','--use-fake-ui-for-media-stream',
    '--use-gl=angle','--use-angle=swiftshader','--enable-webgl',
    `--proxy-server=${PROXY}`,'--disable-quic','--proxy-resolves-dns-locally',
    '--dns-over-https-mode=secure','--dns-over-https-templates=https://1.1.1.1/dns-query','--window-size=1920,1080',
    `--fingerprint=${Math.floor(Math.random()*0x7fffffff)}`,
    '--fingerprint-platform=linux','--fingerprint-brand=Chrome','--fingerprint-brand-version=144',
    '--fingerprint-hardware-concurrency=8','--timezone=America/Los_Angeles','--disable-non-proxied-udp',
  ],
  ignoreDefaultArgs: ['--enable-automation'],
  env: { ...process.env, DISPLAY: ':99', LANG: 'en_US.UTF-8' },
});
const ctx = await browser.newContext({
  userAgent: UA, viewport:{width:1920,height:1040}, locale:'en-US',
  timezoneId:'America/Los_Angeles', screen:{width:1920,height:1080},
});
// Minimal init only
await ctx.addInitScript(`(function(){
  try { Object.defineProperty(Navigator.prototype,"webdriver",{get:()=>false,configurable:true}); } catch(_){}
  try { Object.defineProperty(Navigator.prototype,"hardwareConcurrency",{get:()=>8,configurable:true}); } catch(_){}
})();`);
const page = await ctx.newPage();
await page.goto('https://abrahamjuliot.github.io/creepjs/', {timeout:90000, waitUntil:'domcontentloaded'});
await page.waitForTimeout(30000);
// 提取所有 headless 相关文字（比之前正则更宽）
const full = await page.evaluate(() => {
  const items = [];
  document.querySelectorAll('*').forEach(el => {
    const t = el.childNodes;
    t.forEach(n => {
      if (n.nodeType === 3) {
        const txt = n.textContent.trim();
        if (txt.length > 3 && txt.length < 200) items.push(txt);
      }
    });
  });
  return [...new Set(items)];
});
const headlessLines = full.filter(l => /headless|stealth|Worker|worker|grade|Grade|GPU|webgl|renderer|vendor|angle|swift|mesa/i.test(l));
console.log('=== headless-related lines ===');
headlessLines.forEach(l => console.log(' ', l));
await browser.close();
