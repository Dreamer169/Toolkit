import { chromium } from "playwright";
const exe = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const browser = await chromium.launch({
  headless: false, executablePath: exe,
  args: ["--no-sandbox","--use-gl=angle","--use-angle=swiftshader","--enable-webgl","--lang=en-US"],
  ignoreDefaultArgs: ["--enable-automation"],
  env: { ...process.env, DISPLAY:":99", LANG:"en_US.UTF-8" },
});
const ctx = await browser.newContext({ userAgent:"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36" });

// Direct-assign approach (updated)
await ctx.addInitScript(`
(function(){
  try {
    if (!window.chrome) window.chrome = {};
    const _c = window.chrome;
    const _mk = () => ({ addListener(){}, removeListener(){}, hasListener(){ return false; }, hasListeners(){ return false; } });
    const _rt = {
      id: undefined, lastError: null,
      onConnect: _mk(), onConnectExternal: _mk(),
      onMessage: _mk(), onMessageExternal: _mk(),
      onInstalled: _mk(), onStartup: _mk(),
      onSuspend: _mk(), onSuspendCanceled: _mk(),
      onUpdateAvailable: _mk(), onRestartRequired: _mk(),
      connect(){ throw new Error("Extension context not available."); },
      sendMessage(){ throw new Error("Extension context not available."); },
      getManifest(){ return undefined; },
      getURL(p){ return "chrome-extension://undefined/" + (p||""); },
      getPlatformInfo(cb){ const i={os:"linux",arch:"x86-64",nacl_arch:"x86-64"}; if(cb) cb(i); return Promise.resolve(i); },
      PlatformOs: {ANDROID:"android",CROS:"cros",LINUX:"linux",MAC:"mac",WIN:"win"},
      PlatformArch: {ARM:"arm",ARM64:"arm64",X86_32:"x86-32",X86_64:"x86-64"},
      OnInstalledReason: {CHROME_UPDATE:"chrome_update",INSTALL:"install",UPDATE:"update"},
      RequestUpdateCheckStatus: {NO_UPDATE:"no_update",THROTTLED:"throttled",UPDATE_AVAILABLE:"update_available"},
    };
    // Direct assignment
    _c.runtime = _rt;
    // webstore
    _c.webstore = { onInstallStageChanged:_mk(), onDownloadProgress:_mk(),
      install(){ return Promise.reject(new Error("Webstore not available")); },
      ErrorCode:{ABORTED:"ABORTED",BLACKLISTED:"BLACKLISTED"},
      InstallStage:{DOWNLOADING:"downloading",INSTALLING:"installing"} };
    // dom
    _c.dom = { openOrClosedShadowRoot(el){ try{ return el.openOrClosedShadowRoot||null; }catch(e){ return null; } } };
    _c.action = _c.action || { onClicked: _mk() };
    _c.scripting = _c.scripting || { executeScript(){ return Promise.resolve([]); }, insertCSS(){ return Promise.resolve(); } };
    // loadTimes / csi
    const _t0 = Date.now()/1000-(Math.random()*0.3+0.1);
    _c.loadTimes = function(){ return {requestTime:_t0,startLoadTime:_t0,commitLoadTime:_t0+0.05,finishDocumentLoadTime:_t0+0.4,finishLoadTime:_t0+0.5,firstPaintTime:_t0+0.15,firstPaintAfterLoadTime:0,navigationType:"Other",wasFetchedViaSpdy:true,wasNpnNegotiated:true,npnNegotiatedProtocol:"h2",wasAlternateProtocolAvailable:false,connectionInfo:"h2"}; };
    _c.csi = function(){ return {startE:Date.now(),onloadT:Date.now(),pageT:Math.random()*800+200,tran:15}; };
  } catch(_e) {}
  try { Object.defineProperty(Navigator.prototype,"languages",{get:()=>["en-US","en"],configurable:true,enumerable:true}); } catch(_) {}
  try { Object.defineProperty(navigator,"languages",{get:()=>["en-US","en"],configurable:true,enumerable:true}); } catch(_) {}
})();
`);

const page = await ctx.newPage();
await page.goto("about:blank");
const snap = await page.evaluate(() => ({
  langs: navigator.languages,
  chrome_keys: Object.keys(window.chrome||{}),
  runtime_exists: !!window.chrome?.runtime,
  runtime_onConnect: typeof window.chrome?.runtime?.onConnect,
  runtime_id_type: typeof window.chrome?.runtime?.id,
  webstore: typeof window.chrome?.webstore,
  dom: typeof window.chrome?.dom,
  webdriver: navigator.webdriver,
  csi_type: typeof window.chrome?.csi,
  loadTimes_type: typeof window.chrome?.loadTimes,
}));
console.log("=== After direct-assign initScript ===");
console.log(JSON.stringify(snap, null, 2));
await browser.close();
