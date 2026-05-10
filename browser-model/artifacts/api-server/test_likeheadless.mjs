// 诊断 like-headless 每个 flag 的实际值
import { chromium } from 'playwright';
import { readFileSync } from 'fs';

const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY  = 'socks5://127.0.0.1:10854';
const UA     = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36';

// Load STEALTH_INIT from TS source
const tsSrc = readFileSync('/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts', 'utf8');
const stealthMatch = tsSrc.match(/export const STEALTH_INIT = `\n([\s\S]*?)\n`;/);
const STEALTH_INIT = stealthMatch ? stealthMatch[1] : '';
console.log('STEALTH_INIT:', STEALTH_INIT.length);

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
await ctx.addInitScript(STEALTH_INIT);
const page = await ctx.newPage();
await page.goto('about:blank');

const diag = await page.evaluate(async () => {
  const IS_BLINK = 'chrome' in window || CSS.supports('-webkit-app-region: initial');
  const mimeTypes = Object.keys({ ...navigator.mimeTypes });

  // hasPermissionsBug check
  let hasPermBug = false;
  try {
    if (IS_BLINK && 'permissions' in navigator) {
      const res = await navigator.permissions.query({ name: 'notifications' });
      hasPermBug = res.state == 'prompt' && 'Notification' in window && Notification.permission === 'denied';
    }
  } catch(_) {}

  // hasKnownBgColor
  let hasKnownBgColor = false;
  try {
    const el = document.createElement('div');
    document.body.appendChild(el);
    el.setAttribute('style', 'background-color: ActiveText');
    const { backgroundColor } = getComputedStyle(el);
    document.body.removeChild(el);
    hasKnownBgColor = backgroundColor === 'rgb(255, 0, 0)';
  } catch(_) {}

  // uaDataIsBlank
  let uaDataIsBlank = false;
  try {
    if ('userAgentData' in navigator) {
      const ud = navigator.userAgentData;
      const platform = ud?.platform;
      uaDataIsBlank = platform === '' || platform === null;
    }
  } catch(_) {}

  // noTaskbar  
  const noTaskbar = screen.height === screen.availHeight && screen.width === screen.availWidth;

  // hasVvpScreenRes
  let hasVvpScreenRes = (innerWidth === screen.width && outerHeight === screen.height);
  if (!hasVvpScreenRes && 'visualViewport' in window) {
    hasVvpScreenRes = visualViewport.width === screen.width && visualViewport.height === screen.height;
  }

  // noWebShare
  const noWebShare = IS_BLINK && CSS.supports('accent-color: initial') && (!('share' in navigator) || !('canShare' in navigator));

  return {
    noChrome: IS_BLINK && !('chrome' in window),
    hasPermissionsBug: hasPermBug,
    noPlugins: IS_BLINK && navigator.plugins.length === 0,
    noMimeTypes: IS_BLINK && mimeTypes.length === 0,
    notificationIsDenied: IS_BLINK && 'Notification' in window && Notification.permission == 'denied',
    hasKnownBgColor,
    prefersLightColor: matchMedia('(prefers-color-scheme: light)').matches,
    uaDataIsBlank,
    uaDataValue: 'userAgentData' in navigator ? String(navigator.userAgentData) : 'NOT_IN_NAV',
    uaDataPlatform: (() => { try { return navigator.userAgentData?.platform; } catch(_) { return 'err'; }})(),
    pdfIsDisabled: 'pdfViewerEnabled' in navigator && navigator.pdfViewerEnabled === false,
    noTaskbar,
    hasVvpScreenRes,
    noWebShare,
    // screen info
    screen_h: screen.height, screen_avH: screen.availHeight,
    screen_w: screen.width, screen_avW: screen.availWidth,
    innerW: innerWidth, outerH: outerHeight,
    pluginsLen: navigator.plugins.length,
    mimeTypesLen: mimeTypes.length,
  };
});

console.log('\n=== likeHeadless diagnostic ===');
const flags = ['noChrome','hasPermissionsBug','noPlugins','noMimeTypes','notificationIsDenied',
  'hasKnownBgColor','prefersLightColor','uaDataIsBlank','pdfIsDisabled','noTaskbar','hasVvpScreenRes','noWebShare'];
let trueCount = 0;
for (const k of flags) {
  const v = diag[k];
  if (v === true) trueCount++;
  console.log(`  ${v ? '❌' : '✅'} ${k}: ${v}`);
}
console.log(`\nlike-headless TRUE count: ${trueCount} / ${flags.length} = ${Math.round(trueCount/flags.length*100)}%`);
console.log('\n--- extra info ---');
console.log('  uaDataValue:', diag.uaDataValue, '  uaDataPlatform:', diag.uaDataPlatform);
console.log('  screen:', diag.screen_w, 'x', diag.screen_h, 'avail:', diag.screen_avW, 'x', diag.screen_avH);
console.log('  innerW:', diag.innerW, 'outerH:', diag.outerH);
console.log('  plugins:', diag.pluginsLen, 'mimeTypes:', diag.mimeTypesLen);

// Also check Worker WebGL renderer for hasSwiftShader
const page2 = await ctx.newPage();
await page2.goto('about:blank');
const workerRenderer = await page2.evaluate(async () => {
  return new Promise((resolve) => {
    const blob = new Blob([`
      const gl = new OffscreenCanvas(1,1).getContext('webgl');
      const ext = gl?.getExtension('WEBGL_debug_renderer_info');
      const renderer = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : 'no-ext';
      self.postMessage(renderer || 'null-renderer');
    `], {type:'application/javascript'});
    const url = URL.createObjectURL(blob);
    const w = new Worker(url);
    w.onmessage = e => { URL.revokeObjectURL(url); resolve(e.data); };
    w.onerror = e => resolve('error:'+e.message);
  });
});
console.log('\n  Worker WebGL renderer:', workerRenderer);
console.log('  hasSwiftShader (Worker):', /SwiftShader/.test(workerRenderer));

await browser.close();
