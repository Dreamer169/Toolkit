// 诊断：webDriverIsOn 的各个分量实际值
import { chromium } from 'playwright';
const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY  = 'socks5://127.0.0.1:10854';
const UA     = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36';

for (const patchWebdriver of [false, true]) {
  const browser = await chromium.launch({
    headless: false, executablePath: BINARY,
    args: ['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled',
      '--no-first-run','--no-default-browser-check','--use-gl=angle','--use-angle=swiftshader',
      `--proxy-server=${PROXY}`,'--window-size=1920,1080',
      `--fingerprint=${Math.floor(Math.random()*0x7fffffff)}`,
      '--fingerprint-platform=linux','--fingerprint-brand=Chrome','--fingerprint-brand-version=144',
    ],
    ignoreDefaultArgs: ['--enable-automation'],
    env: { ...process.env, DISPLAY: ':99', LANG: 'en_US.UTF-8' },
  });
  const ctx = await browser.newContext({ userAgent: UA });
  if (patchWebdriver) {
    await ctx.addInitScript(`Object.defineProperty(Navigator.prototype,"webdriver",{get:()=>false,configurable:true});`);
  }
  const page = await ctx.newPage();
  await page.goto('about:blank');
  const diag = await page.evaluate(() => ({
    webdriver: navigator.webdriver,
    webdriverType: typeof navigator.webdriver,
    webdriverInNav: 'webdriver' in navigator,
    cssSupports: CSS.supports('border-end-end-radius: initial'),
    getterStr: (() => { try { const desc = Object.getOwnPropertyDescriptor(Navigator.prototype,'webdriver'); return desc?.get?.toString() || 'no-getter'; } catch(e) { return 'err:'+e; } })(),
  }));
  console.log(`\n=== patchWebdriver=${patchWebdriver} ===`);
  console.log(JSON.stringify(diag, null, 2));
  await browser.close();
}
