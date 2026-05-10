import { chromium } from 'playwright';
import { readFileSync } from 'fs';
const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY = 'socks5://127.0.0.1:10854';
const tsSrc = readFileSync('./src/lib/renderer.ts', 'utf8');
const STEALTH_INIT = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/) || [])[1] || '';
const browser = await chromium.launch({
  headless: false, executablePath: BINARY,
  args: ['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled',
    '--use-gl=angle','--use-angle=swiftshader','--enable-webgl','--lang=en-US',
    '--proxy-server='+PROXY,'--window-size=1920,1080',
    '--fingerprint='+String(Math.floor(Math.random()*0x7fffffff)),
    '--fingerprint-platform=linux','--fingerprint-brand=Chrome','--fingerprint-brand-version=144',
    '--fingerprint-hardware-concurrency=8','--timezone=America/Los_Angeles',
  ],
  ignoreDefaultArgs: ['--enable-automation'],
  env: { ...process.env, DISPLAY: ':99' },
});
const ctx = await browser.newContext({
  userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
  viewport: { width: 1920, height: 1040 }, locale: 'en-US',
  timezoneId: 'America/Los_Angeles', screen: { width: 1920, height: 1080 },
  extraHTTPHeaders: { 'Accept-Language': 'en-US,en;q=0.9' },
});
await ctx.addInitScript(STEALTH_INIT);
const page = await ctx.newPage();
await page.goto('about:blank');
const nav = await page.evaluate(() => {
  const plugins = [];
  for (let i=0;i<navigator.plugins.length;i++){
    const p=navigator.plugins[i];
    plugins.push({name:p.name,desc:p.description.slice(0,40),fn:p.filename});
  }
  const hcDesc = Object.getOwnPropertyDescriptor(Navigator.prototype,'hardwareConcurrency');
  const langDesc = Object.getOwnPropertyDescriptor(Navigator.prototype,'language');
  const langsDesc = Object.getOwnPropertyDescriptor(Navigator.prototype,'languages');
  return {
    language: navigator.language,
    languages: Array.from(navigator.languages),
    hardwareConcurrency: navigator.hardwareConcurrency,
    deviceMemory: navigator.deviceMemory,
    plugins, pluginCount: navigator.plugins.length,
    pdfEnabled: navigator.pdfViewerEnabled,
    doNotTrack: navigator.doNotTrack,
    // descriptor info (detectable by PixelScan)
    hcDesc_configurable: hcDesc && hcDesc.configurable,
    hcDesc_enumerable: hcDesc && hcDesc.enumerable,
    hcDesc_native: hcDesc && hcDesc.get && String(hcDesc.get).includes('[native code]'),
    langDesc_native: langDesc && langDesc.get && String(langDesc.get).includes('[native code]'),
    langsDesc_native: langsDesc && langsDesc.get && String(langsDesc.get).includes('[native code]'),
    // own-property checks
    langOwnProp: Object.prototype.hasOwnProperty.call(navigator,'languages'),
    hcOwnProp: Object.prototype.hasOwnProperty.call(navigator,'hardwareConcurrency'),
    plugOwnProp: Object.prototype.hasOwnProperty.call(navigator,'plugins'),
  };
});
console.log(JSON.stringify(nav, null, 2));
await browser.close();
