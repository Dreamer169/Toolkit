// Test WITH screen:{} option to understand what Playwright does to window.screen
import { chromium } from "playwright";
const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const browser = await chromium.launch({
  headless: false, executablePath: BINARY,
  args: ["--no-sandbox","--disable-dev-shm-usage"],
  ignoreDefaultArgs: ["--enable-automation"],
  env: { ...process.env, DISPLAY: ":99" },
});

// === Context WITH screen:{} emulation (current approach — likely failing) ===
const ctxWith = await browser.newContext({
  viewport: {width:1920, height:1040},
  screen: {width:1920, height:1080},   // <-- Playwright CDP screen emulation
});
await ctxWith.addInitScript(`(function(){
  var d = Object.getOwnPropertyDescriptor(window, 'screen') || {};
  window.__sd1 = JSON.stringify({ cfg: d.configurable, writable: d.writable, hasGet: typeof d.get==='function', hasValue: 'value' in d });
  // Try overriding after Playwright applies screen:{}
  try {
    var _rs = window.screen;
    Object.defineProperty(window, 'screen', {
      get: ()=>new Proxy(_rs, {get:(t,p)=>p==='availHeight'?1040:t[p]}), configurable:true
    });
    window.__sdOK = 'defineProperty:ok -> ah=' + screen.availHeight;
  } catch(e) {
    window.__sdOK = 'defineProperty:fail -> ' + e.message;
    try {
      window.screen = new Proxy(window.screen, {get:(t,p)=>p==='availHeight'?1040:t[p]});
      window.__sdOK += ' | directAssign:ok -> ah=' + screen.availHeight;
    } catch(e2) {
      window.__sdOK += ' | directAssign:fail -> ' + e2.message;
      // Try Screen.prototype
      try {
        Object.defineProperty(Screen.prototype, 'availHeight', {get:()=>1040, configurable:true});
        window.__sdOK += ' | protoODP:ok -> ah=' + screen.availHeight;
      } catch(e3) {
        window.__sdOK += ' | protoODP:fail -> ' + e3.message;
      }
    }
  }
  // Check Notification
  try {
    var _RN = Notification;
    window.Notification = new Proxy(_RN, {get:(t,p)=>p==='permission'?'default':t[p], construct:(t,a)=>new t(...a)});
    window.__notifOK = 'direct:ok perm=' + window.Notification.permission;
  } catch(e) {
    window.__notifOK = 'direct:fail ' + e.message;
  }
  // Check navigator.share
  try {
    navigator.share = function share(){return Promise.reject()};
    window.__shareOK = 'instance:ok in=' + ('share' in navigator);
  } catch(e) {
    window.__shareOK = 'instance:fail ' + e.message;
  }
})();`);
const pg = await ctxWith.newPage();
await pg.goto('about:blank');
const r = await pg.evaluate(()=>({
  sd: window.__sd1, sdOK: window.__sdOK, notif: window.__notifOK, share: window.__shareOK,
  actualAH: screen.availHeight, Nperm: Notification.permission, hasShare: 'share' in navigator
}));
console.log("=== WITH screen:{} option ===");
console.log("window.screen descriptor:", r.sd);
console.log("screen override result:", r.sdOK);
console.log("actual availHeight:", r.actualAH);
console.log("Notification.permission:", r.Nperm, "| notifOK:", r.notif);
console.log("navigator.share in:", r.hasShare, "| shareOK:", r.share);
await browser.close(); console.log("Done.");
