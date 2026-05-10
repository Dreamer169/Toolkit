import { chromium } from 'playwright';
import { readFileSync } from 'fs';
const BINARY = '/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome';
const PROXY  = 'socks5://127.0.0.1:10857';
const tsSrc = readFileSync('/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts', 'utf8');
const STEALTH_INIT = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/) || [])[1] || '';
const WORKER_STEALTH = (tsSrc.match(/const WORKER_STEALTH_PATCH = `([\s\S]*?)`;/) || [])[1] || '';
console.log('STEALTH_INIT len:', STEALTH_INIT.length, ' WORKER len:', WORKER_STEALTH.length);

async function launch() {
  return chromium.launch({
    headless: false, executablePath: BINARY,
    args: ['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled',
      '--no-first-run','--no-default-browser-check','--mute-audio','--lang=en-US',
      '--use-gl=angle','--use-angle=swiftshader','--enable-webgl','--window-size=1920,1080',
      '--fingerprint='+Math.floor(Math.random()*0x7fffffff),
      '--fingerprint-platform=linux','--fingerprint-brand=Chrome','--fingerprint-brand-version=144',
      '--fingerprint-hardware-concurrency=8','--timezone=America/Los_Angeles',
      '--proxy-server='+PROXY,'--disable-quic','--proxy-resolves-dns-locally','--disable-non-proxied-udp'],
    ignoreDefaultArgs: ['--enable-automation'],
    env: { ...process.env, DISPLAY: ':99' },
  });
}
async function makeCtx(browser) {
  const ctx = await browser.newContext({
    userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
    viewport:{width:1920,height:1040}, locale:'en-US', timezoneId:'America/Los_Angeles', screen:{width:1920,height:1080},
  });
  await ctx.addInitScript(STEALTH_INIT);
  ctx.on('page', p => p.on('worker', w => w.evaluate(WORKER_STEALTH).catch(()=>{})));
  return ctx;
}
async function testSite(name, url, waitMs, extractFn) {
  console.log('\n=== '+name+' ===');
  const browser = await launch();
  const ctx = await makeCtx(browser);
  const page = await ctx.newPage();
  const apiData = [];
  page.on('response', async r => {
    try {
      const ct = r.headers()['content-type']||'';
      if (ct.includes('json')) { const j = await r.json().catch(()=>null); if (j) apiData.push({url:r.url().slice(0,120),d:j}); }
    } catch(e) {}
  });
  try {
    await page.goto(url, {timeout:60000, waitUntil:'domcontentloaded'});
    await page.waitForTimeout(waitMs);
    const result = await page.evaluate(extractFn).catch(e=>({err:e.message}));
    if (result.err) console.log('eval error:', result.err);
    else { (result.lines||[]).forEach(l=>console.log(' '+l)); if (result.extra) console.log(' extra:', result.extra); }
    const rel = apiData.filter(x=>{const s=JSON.stringify(x.d); return s.includes('bot')||s.includes('like')||s.includes('headless')||s.includes('stealth')||s.includes('trust')||s.includes('score')||s.includes('visitor');});
    rel.slice(0,3).forEach(x=>console.log(' API:',x.url,JSON.stringify(x.d).slice(0,500)));
  } catch(e) { console.log('Error:', e.message.slice(0,200)); }
  await browser.close();
}

await testSite('CreepJS', 'https://creepjs.com/', 30000, () => {
  const t = document.body?.innerText||'';
  const lines = t.split('\n').filter(l=>l.trim()&&/%|like.headless|headless|stealth|bot/i.test(l)).slice(0,30);
  const flags = ['noContentIndex','noContactsManager','noDownlinkMax','hasKnownBgColor','noWebShare','prefersLightColor','matchMedia']
    .filter(f=>new RegExp(f,'i').test(t));
  return {lines, extra:'remaining flags: '+JSON.stringify(flags)};
});

await testSite('PixelScan', 'https://pixelscan.net/fingerprint-check', 22000, () => {
  const t = document.body?.innerText||'';
  return {lines: t.split('\n').filter(l=>l.trim()&&/consistent|inconsistent|normal|suspicious|bot|score|pass|fail|detect|clear|risk/i.test(l)).slice(0,25)};
});

await testSite('IPHey', 'https://iphey.com/', 22000, () => {
  const t = document.body?.innerText||'';
  return {lines: t.split('\n').filter(l=>l.trim()&&/trustworthy|genuine|human|bot|suspicious|score|good|bad|clean|risk|result|status|trust/i.test(l)).slice(0,20)};
});

await testSite('BrowserScan', 'https://www.browserscan.net/bot-detection', 20000, () => {
  const t = document.body?.innerText||'';
  return {lines: t.split('\n').filter(l=>l.trim()&&/bot|human|score|risk|pass|fail|detect|normal|suspicious|headless|webdriver/i.test(l)).slice(0,25)};
});

await testSite('Sannysoft', 'https://bot.sannysoft.com/', 12000, () => {
  const t = document.body?.innerText||'';
  return {lines: t.split('\n').filter(l=>l.trim()&&/(PASS|FAIL|true|false)/i.test(l)).slice(0,30)};
});

await testSite('Fingerprint', 'https://fingerprint.com/demo/', 32000, () => {
  const t = document.body?.innerText||'';
  return {lines: t.split('\n').filter(l=>l.trim()&&/bot|automation|score|confidence|visitor|suspect|human/i.test(l)).slice(0,20)};
});

await testSite('Datadome', 'https://antoinevastel.com/bots/datadome', 15000, () => {
  const t = document.body?.innerText||'';
  return {lines: t.split('\n').filter(l=>l.trim()).slice(0,20)};
});

console.log('\n=== ALL TESTS DONE ===');
