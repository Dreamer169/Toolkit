import { chromium } from "playwright";
const exe = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const PROXY = "socks5://127.0.0.1:10854";
const UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36";

const browser = await chromium.launch({
  headless: false, executablePath: exe,
  args: [
    "--no-sandbox","--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--disable-features=AutomationControlled,IsolateOrigins,site-per-process",
    "--no-first-run","--no-default-browser-check","--mute-audio",
    "--lang=en-US","--accept-lang=en-US,en;q=0.9",
    "--use-fake-ui-for-media-stream","--use-fake-device-for-media-stream",
    "--password-store=basic","--use-mock-keychain",
    "--use-gl=angle","--use-angle=swiftshader","--enable-webgl",
    `--proxy-server=${PROXY}`,"--disable-quic",
    "--proxy-resolves-dns-locally",
    "--enable-features=AsyncDns,DnsOverHttps",
    "--dns-over-https-mode=secure",
    "--dns-over-https-templates=https://1.1.1.1/dns-query,https://dns.google/dns-query",
    "--window-size=1920,1080","--start-maximized",
    `--fingerprint=${Math.floor(Math.random()*0x7fffffff)}`,
    "--fingerprint-platform=linux","--fingerprint-brand=Chrome",
    "--fingerprint-brand-version=144","--fingerprint-hardware-concurrency=8",
    "--timezone=America/Los_Angeles","--disable-non-proxied-udp",
  ],
  ignoreDefaultArgs: ["--enable-automation"],
  env: { ...process.env, DISPLAY:":99", LANG:"en_US.UTF-8", LC_ALL:"en_US.UTF-8", LANGUAGE:"en_US:en" },
});

const ctx = await browser.newContext({
  userAgent: UA, viewport:{width:1920,height:1040},
  locale:"en-US", timezoneId:"America/Los_Angeles",
  screen:{width:1920,height:1080},
});

// Full direct-assign initScript (mirrors updated renderer.ts)
await ctx.addInitScript(`(function(){
  try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array; delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise; delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol; } catch(_){}
  try { Object.defineProperty(Navigator.prototype,"languages",{get:()=>["en-US","en"],configurable:true,enumerable:true}); } catch(_){}
  try { Object.defineProperty(navigator,"languages",{get:()=>["en-US","en"],configurable:true,enumerable:true}); } catch(_){}
  try { Object.defineProperty(Navigator.prototype,"platform",{get:()=>"Linux x86_64",configurable:true}); } catch(_){}
  try { Object.defineProperty(Navigator.prototype,"hardwareConcurrency",{get:()=>8,configurable:true}); } catch(_){}
  try { Object.defineProperty(Navigator.prototype,"deviceMemory",{get:()=>8,configurable:true}); } catch(_){}
  try { Object.defineProperty(Navigator.prototype,"maxTouchPoints",{get:()=>0,configurable:true}); } catch(_){}
  try { Object.defineProperty(Navigator.prototype,"pdfViewerEnabled",{get:()=>true,configurable:true}); } catch(_){}
  try { Object.defineProperty(Navigator.prototype,"webdriver",{get:()=>false,configurable:true}); } catch(_){}
  // chrome stubs — direct assignment
  try {
    if(!window.chrome) window.chrome={};
    const _c=window.chrome;
    const _mk=()=>({addListener(){},removeListener(){},hasListener(){return false;},hasListeners(){return false;}});
    _c.runtime={
      id:undefined,lastError:null,
      onConnect:_mk(),onConnectExternal:_mk(),onMessage:_mk(),onMessageExternal:_mk(),
      onInstalled:_mk(),onStartup:_mk(),onSuspend:_mk(),onSuspendCanceled:_mk(),
      onUpdateAvailable:_mk(),onRestartRequired:_mk(),
      connect(){throw new Error("Extension context not available.");},
      sendMessage(){throw new Error("Extension context not available.");},
      getManifest(){return undefined;},
      getURL(p){return "chrome-extension://undefined/"+(p||"");},
      getPlatformInfo(cb){const i={os:"linux",arch:"x86-64",nacl_arch:"x86-64"};if(cb)cb(i);return Promise.resolve(i);},
      PlatformOs:{ANDROID:"android",CROS:"cros",LINUX:"linux",MAC:"mac",WIN:"win"},
      PlatformArch:{ARM:"arm",ARM64:"arm64",X86_32:"x86-32",X86_64:"x86-64"},
      OnInstalledReason:{CHROME_UPDATE:"chrome_update",INSTALL:"install",UPDATE:"update"},
      RequestUpdateCheckStatus:{NO_UPDATE:"no_update",THROTTLED:"throttled",UPDATE_AVAILABLE:"update_available"},
    };
    const _t0=Date.now()/1000-(Math.random()*0.3+0.1);
    _c.loadTimes=function(){return{requestTime:_t0,startLoadTime:_t0,commitLoadTime:_t0+0.05,finishDocumentLoadTime:_t0+0.4,finishLoadTime:_t0+0.5,firstPaintTime:_t0+0.15,firstPaintAfterLoadTime:0,navigationType:"Other",wasFetchedViaSpdy:true,wasNpnNegotiated:true,npnNegotiatedProtocol:"h2",wasAlternateProtocolAvailable:false,connectionInfo:"h2"};};
    _c.csi=function(){return{startE:Date.now(),onloadT:Date.now(),pageT:Math.random()*800+200,tran:15};};
    _c.app=_c.app||{isInstalled:false,getDetails(){return null;},getIsInstalled(){return false;},installState(cb){if(cb)cb("not_installed");},runningState(){return "cannot_run";},InstallState:{DISABLED:"disabled",INSTALLED:"installed",NOT_INSTALLED:"not_installed"},RunningState:{CANNOT_RUN:"cannot_run",READY_TO_RUN:"ready_to_run",RUNNING:"running"}};
    _c.webstore={onInstallStageChanged:_mk(),onDownloadProgress:_mk(),install(){return Promise.reject(new Error("Webstore not available"));},ErrorCode:{ABORTED:"ABORTED",BLACKLISTED:"BLACKLISTED"},InstallStage:{DOWNLOADING:"downloading",INSTALLING:"installing"}};
    _c.dom={openOrClosedShadowRoot(el){try{return el.openOrClosedShadowRoot||null;}catch(e){return null;}}};
    _c.action=_c.action||{onClicked:_mk()};
    _c.scripting=_c.scripting||{executeScript(){return Promise.resolve([]);},insertCSS(){return Promise.resolve();}};
  } catch(_e){}
  // navigator.connection
  try { Object.defineProperty(Navigator.prototype,"connection",{get:()=>({effectiveType:"4g",rtt:50,downlink:10,saveData:false,type:"wifi",addEventListener(){},removeEventListener(){}}),configurable:true}); } catch(_){}
  // outerWidth/outerHeight
  try { if(!window.outerWidth) Object.defineProperty(window,"outerWidth",{get:()=>window.innerWidth}); } catch(_){}
  try { if(!window.outerHeight) Object.defineProperty(window,"outerHeight",{get:()=>window.innerHeight+88}); } catch(_){}
})();`);

const page = await ctx.newPage();
await page.goto("about:blank");
const snap = await page.evaluate(() => ({
  langs: navigator.languages,
  chrome_keys: Object.keys(window.chrome||{}),
  runtime_onConnect: typeof window.chrome?.runtime?.onConnect,
  webstore: typeof window.chrome?.webstore,
  dom: typeof window.chrome?.dom,
}));
console.log("JS snapshot:", JSON.stringify(snap));
await page.close();

console.log("\nLoading CreepJS...");
const cjPage = await ctx.newPage();
await cjPage.goto("https://abrahamjuliot.github.io/creepjs/", {timeout:90000,waitUntil:"domcontentloaded"});
await cjPage.waitForTimeout(20000);
const cj = await cjPage.evaluate(() => {
  const all = document.body?.innerText || "";
  const lines = all.split("\n").map(l=>l.trim()).filter(l=>
    /headless|stealth|chromium|like headless|chrome_keys|webstore|dom.*object|runtime.*object|44%|0%|Worker|worker|SharedWorker/i.test(l)
  );
  // find percentage
  const pct = all.match(/(\d+(?:\.\d+)?)%\s*(?:trust|like|bot|human)?/g);
  return { pct, headless_lines: lines.slice(0,30) };
});
console.log("\n=== CreepJS headless section ===");
cj.headless_lines?.forEach(l=>console.log(" ",l));
console.log("\nAll %:", cj.pct?.join(", "));
await cjPage.close();
await browser.close();
