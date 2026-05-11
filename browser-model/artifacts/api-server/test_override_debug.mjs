import { chromium } from "playwright";

const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";

const browser = await chromium.launch({
  headless: false, executablePath: BINARY,
  args: ["--no-sandbox","--disable-dev-shm-usage","--no-first-run","--window-size=1920,1080"],
  ignoreDefaultArgs: ["--enable-automation"],
  env: { ...process.env, DISPLAY: ":99" },
});

// Test with MINIMAL context (no screen emulation) first
const ctx = await browser.newContext({ viewport: {width:1920,height:1040} });

// Inject our test code as initScript
await ctx.addInitScript(`(function() {
  // Test 1: Can we modify window.screen?
  var _rs = window.screen;
  var screenDefineError = 'none';
  var screenProxy = null;
  try {
    Object.defineProperty(window, 'screen', {
      get: function() { return screenProxy; },
      configurable: true
    });
    screenProxy = new Proxy(_rs, {
      get: function(t,p) { if(p==='availHeight') return 1040; var v=t[p]; return typeof v==='function'?v.bind(t):v; }
    });
    window.__screenDefineOK = 'yes';
  } catch(e) {
    window.__screenDefineOK = 'no: ' + e.message;
  }

  // Test 2: Direct assignment
  try {
    var _rs2 = window.screen;
    window.screen = new Proxy(_rs2, { get: (t,p)=>p==='availWidth'?999:t[p] });
    window.__screenDirectOK = 'yes, availWidth=' + screen.availWidth;
  } catch(e) {
    window.__screenDirectOK = 'no: ' + e.message;
  }

  // Test 3: What does getOwnPropertyDescriptor say about window.screen?
  try {
    var d = Object.getOwnPropertyDescriptor(window, 'screen');
    window.__screenDescriptor = JSON.stringify({
      configurable: d && d.configurable,
      writable: d && d.writable,
      hasValue: d && 'value' in d,
      hasGet: d && typeof d.get === 'function'
    });
  } catch(e) {
    window.__screenDescriptor = 'error: ' + e.message;
  }

  // Test 4: Can we override Notification.permission?
  try {
    var _RN = window.Notification;
    var notifPerm1 = _RN && _RN.permission;
    var _NP = new Proxy(_RN, {
      get: function(t,p) { if(p==='permission') return 'default'; var v=t[p]; return typeof v==='function'?v.bind(t):v; },
      construct: function(t,args) { return new (Function.prototype.bind.apply(t, [null].concat(args))); }
    });
    window.Notification = _NP;
    window.__notifPermOK = 'yes: was=' + notifPerm1 + ' now=' + window.Notification.permission;
  } catch(e) {
    window.__notifPermOK = 'no: ' + e.message;
  }

  // Test 5: Can we override navigator.share via instance assignment?
  var shareOK = 'not_tried';
  try {
    navigator.share = function share() { return Promise.resolve(); };
    shareOK = 'instance_assign: ' + ('share' in navigator) + ' typeof=' + typeof navigator.share;
  } catch(e) {
    shareOK = 'instance_assign_err: ' + e.message;
    try {
      Object.defineProperty(navigator, 'share', { value: function share() {}, writable:true, configurable:true, enumerable:true });
      shareOK = 'ODP_instance: ' + ('share' in navigator);
    } catch(e2) {
      shareOK = 'ODP_instance_err: ' + e2.message;
    }
  }
  window.__shareOK = shareOK;

  // Test 6: Navigator descriptor
  try {
    var nd = Object.getOwnPropertyDescriptor(window, 'navigator');
    window.__navDescriptor = JSON.stringify({ configurable: nd&&nd.configurable, writable: nd&&nd.writable, hasGet: nd&&typeof nd.get==='function' });
  } catch(e) { window.__navDescriptor = 'error: '+e.message; }

  // Test 7: Screen descriptor
  try {
    var sd2 = Object.getOwnPropertyDescriptor(window, 'screen');
    window.__screenDescriptor2 = JSON.stringify({ configurable: sd2&&sd2.configurable, writable: sd2&&sd2.writable, hasGet: sd2&&typeof sd2.get==='function' });
  } catch(e) { window.__screenDescriptor2 = 'error: '+e.message; }

})();`);

const page = await ctx.newPage();
await page.goto("about:blank");

const results = await page.evaluate(() => ({
  screenDefineOK: window.__screenDefineOK,
  screenDirectOK: window.__screenDirectOK,
  screenDescriptor: window.__screenDescriptor,
  screenDescriptor2: window.__screenDescriptor2,
  actualAvailH: screen.availHeight,
  actualAvailW: screen.availWidth,
  notifPermOK: window.__notifPermOK,
  shareOK: window.__shareOK,
  navDescriptor: window.__navDescriptor,
  hasShare: 'share' in navigator,
}));

console.log("\n=== OVERRIDE DEBUG RESULTS ===");
console.log("screen.defineProperty result:", results.screenDefineOK);
console.log("screen.directAssign result:", results.screenDirectOK);
console.log("screen descriptor (before override):", results.screenDescriptor);
console.log("screen descriptor (after override):", results.screenDescriptor2);
console.log("actual screen.availHeight:", results.actualAvailH);
console.log("actual screen.availWidth:", results.actualAvailW);
console.log("Notification.permission override:", results.notifPermOK);
console.log("navigator.share override:", results.shareOK);
console.log("navigator descriptor:", results.navDescriptor);
console.log("'share' in navigator:", results.hasShare);

await browser.close();
console.log("\nDone.");
