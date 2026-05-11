import { chromium, Browser, BrowserContext } from "playwright";
import { attachGoogleProxyRouting, googleProxyPoolInfo } from "./google-route.js";
import { spawn, type ChildProcess } from "node:child_process";
import * as net from "node:net";
import * as fs from "node:fs";

let browserPromise: Promise<Browser> | null = null;
let chromiumProc: ChildProcess | null = null;

async function waitForCdpHttp(port: number, host: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastErr: unknown = null;
  while (Date.now() < deadline) {
    const sockOk = await new Promise<boolean>((resolve) => {
      const s = net.connect(port, host);
      let done = false;
      const finish = (v: boolean, e?: unknown) => {
        if (done) return;
        done = true;
        if (e) lastErr = e;
        try { s.destroy(); } catch { /* ignore */ }
        resolve(v);
      };
      s.once("connect", () => finish(true));
      s.once("error", (e) => finish(false, e));
      setTimeout(() => finish(false, new Error("connect timeout")), 1500);
    });
    if (sockOk) {
      // chromium with --remote-debugging-pipe will LISTEN on the port but
      // /json/version returns ECONNREFUSED at the HTTP layer. Probe HTTP.
      try {
        const ctl = new AbortController();
        const t = setTimeout(() => ctl.abort(), 2500);
        const r = await fetch(`http://${host}:${port}/json/version`, { signal: ctl.signal });
        clearTimeout(t);
        if (r.ok) return;
      } catch (e) { lastErr = e; }
    }
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error(`CDP HTTP at ${host}:${port} not ready in ${timeoutMs}ms (last=${String(lastErr)})`);
}

function killChromiumProc(): void {
  if (chromiumProc && chromiumProc.exitCode === null) {
    try { chromiumProc.kill("SIGTERM"); } catch { /* ignore */ }
  }
  chromiumProc = null;
}
process.once("exit",   () => { killChromiumProc(); });
process.once("SIGTERM", () => { killChromiumProc(); process.exit(0); });
process.once("SIGINT",  () => { killChromiumProc(); process.exit(0); });


// -- Proxy-aware timezone resolution (added to fix IPHey Unreliable verdict) --
// Maps SOCKS proxy port to exit-IP geographic timezone so that Playwright
// timezoneId, Chrome --timezone flag, and JS Intl patch all stay consistent.
// (c20544f only patched browser_fingerprint.py; this file was missed.)
const _PROXY_PORT_TZ: Record<string, { tz: string; std: number; dst: number; hasDst: boolean }> = {
  "10857": { tz: "Asia/Hong_Kong",       std: -480, dst: -480, hasDst: false },
  "10859": { tz: "Europe/Amsterdam",    std:  -60, dst: -120, hasDst: true  },
  "10853": { tz: "America/Los_Angeles", std:  480, dst:  420, hasDst: true  },
  "10855": { tz: "Europe/London",       std:    0, dst:  -60, hasDst: true  },
  "10851": { tz: "America/New_York",    std:  300, dst:  240, hasDst: true  },
  "10854": { tz: "Asia/Seoul",          std: -540, dst: -540, hasDst: false },
  "10910": { tz: "America/Los_Angeles", std:  480, dst:  420, hasDst: true  },
  "10911": { tz: "America/Los_Angeles", std:  480, dst:  420, hasDst: true  },
  "10912": { tz: "America/Los_Angeles", std:  480, dst:  420, hasDst: true  },
  "10914": { tz: "Europe/London",       std:    0, dst:  -60, hasDst: true  },
  "10915": { tz: "America/Mexico_City", std:  360, dst:  300, hasDst: true  },
  "10916": { tz: "America/Los_Angeles", std:  480, dst:  420, hasDst: true  },
};
function _resolveProxyTz() {
  const m = (process.env.BROWSER_PROXY || "").match(/:(\d+)$/);
  return _PROXY_PORT_TZ[m?.[1] ?? ""] ?? { tz: "America/Los_Angeles", std: 480, dst: 420, hasDst: true };
}
const _ptz = _resolveProxyTz();
const BROWSER_TIMEZONE = _ptz.tz;
const _TZ_STD = _ptz.std;
const _TZ_DST = _ptz.dst;
const _TZ_HAS_DST = _ptz.hasDst;
export const STEALTH_INIT = `
// === Anti-fingerprint init script (runs before any page JS) ===
(() => {

  // === Native-code spoofing: per-function toString (NO global override) ========
  // PixelScan: String(getter) / getter+"" calls fn.toString() → hits own-property → "[native code]"
  // CreepJS: Function.prototype.toString is untouched → no antidetect stealth detection
  // WeakSet global approach was detected by CreepJS cross-realm iframe toString check.
  function _mkN(fn, name) {
    // .bind(null) creates a V8 bound function: toString() returns "function () { [native code] }"
    // unconditionally, even when called via cross-realm native Function.prototype.toString.
    // Closures are preserved. No global Function.prototype.toString override needed.
    // This satisfies both PixelScan (uses toString.call(getter)) and CreepJS (no global change).
    try {
      var bound = fn.bind(null);
      if (name) { try { Object.defineProperty(bound, "name", { value: name }); } catch(_) {} }
      return bound;
    } catch(_) { return fn; }
  }

  // navigator.webdriver REMOVED: binary natively returns false with [native code] getter.
  // Patching to undefined triggers CSS check + webdriver===undefined -> webDriverIsOn=true -> 33% headless.
  // delete CDP-injected globals
  try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array; } catch(_) {}
  try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise; } catch(_) {}
  try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol; } catch(_) {}

  // languages
  // languages — patch prototype first, then instance (handles ungoogled-chromium non-configurable)
  // Cache the frozen array so identity checks pass: navigator.languages === navigator.languages → true
  try {
    const _cachedLangs = Object.freeze(['en-US', 'en']);
    Object.defineProperty(Navigator.prototype, 'languages', { get: _mkN(function languages() { return _cachedLangs; }, 'get languages'), configurable: true, enumerable: true });
  } catch (_) {}
  // navigator instance-level languages REMOVED: makes hasOwnProperty('languages')=true. Prototype override above is sufficient.

  // platform / hardwareConcurrency / deviceMemory
  try { Object.defineProperty(Navigator.prototype, 'platform', { get: _mkN(function platform() { return 'Linux x86_64'; }, 'get platform'), configurable: true }); } catch (_) {}
  try { Object.defineProperty(Navigator.prototype, 'hardwareConcurrency', { get: _mkN(function hardwareConcurrency() { return 4; }, 'get hardwareConcurrency'), configurable: true }); } catch (_) {}
  try { Object.defineProperty(Navigator.prototype, 'deviceMemory', { get: _mkN(function deviceMemory() { return 4; }, 'get deviceMemory'), configurable: true }); } catch (_) {}
  // maxTouchPoints REMOVED: native Chrome/Linux returns 0. Overriding changes configurable flag, PixelScan detects it.

  // plugins / mimeTypes — PluginArray + MimeTypeArray with full bidirectional cross-references.
  // PixelScan checks: plugin.item(0) ↔ mimeTypes["application/pdf"].enabledPlugin consistency.
  try {
    // Create MimeType objects with proper properties
    const makeMimeType = (type, desc, suffixes) => {
      const mt = Object.create(MimeType.prototype);
      Object.defineProperties(mt, {
        type:        { value: type,     enumerable: true, configurable: true },
        description: { value: desc,     enumerable: true, configurable: true },
        suffixes:    { value: suffixes, enumerable: true, configurable: true },
        enabledPlugin: { value: null, writable: true, enumerable: true, configurable: true },
      });
      return mt;
    };

    // Two MimeTypes for PDF
    const mtPdf  = makeMimeType('application/pdf', 'Portable Document Format', 'pdf');
    const mtTxt  = makeMimeType('text/pdf',        'Portable Document Format', 'pdf');

    // Create Plugin objects with item()/namedItem() + MimeType back-reference
    const makePlugin = (name, filename, desc, mimeTypes) => {
      const p = Object.create(Plugin.prototype);
      const mts = mimeTypes || [];
      mts.forEach((mt, i) => { p[i] = mt; p[mt.type] = mt; });
      Object.defineProperties(p, {
        name:        { value: name,       enumerable: true, configurable: true },
        filename:    { value: filename,   enumerable: true, configurable: true },
        description: { value: desc,       enumerable: true, configurable: true },
        length:      { value: mts.length, enumerable: true, configurable: true },
      });
      return p;
    };

    const plugins = [
      makePlugin('PDF Viewer',                'internal-pdf-viewer', 'Portable Document Format', [mtPdf, mtTxt]),
      makePlugin('Chrome PDF Viewer',         'internal-pdf-viewer', 'Portable Document Format', [mtPdf, mtTxt]),
      makePlugin('Chromium PDF Viewer',       'internal-pdf-viewer', 'Portable Document Format', [mtPdf, mtTxt]),
      makePlugin('Microsoft Edge PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format', [mtPdf, mtTxt]),
      makePlugin('WebKit built-in PDF',       'internal-pdf-viewer', 'Portable Document Format', [mtPdf, mtTxt]),
    ];

    // Set enabledPlugin back-reference on each MimeType
    mtPdf.enabledPlugin = plugins[0];
    mtTxt.enabledPlugin = plugins[0];

    // Build PluginArray
    const plugArr = Object.create(PluginArray.prototype);
    plugins.forEach((p, i) => { plugArr[i] = p; plugArr[p.name] = p; });
    Object.defineProperty(plugArr, 'length', { value: plugins.length, enumerable: true, configurable: true });

    Object.defineProperty(Navigator.prototype, 'plugins', { get: _mkN(function plugins() { return plugArr; }, 'get plugins'), configurable: true });
    // mimeTypes NOT overridden: native ungoogled-chromium values passed PixelScan (Clear).
  } catch (_) {}

  // ── chrome.* stubs (comprehensive — direct assignment to bypass configurable:false) ──
  try {
    if (!window.chrome) window.chrome = {};
    const _c = window.chrome;
    const _mk = () => ({
      addListener()    { /* no-op */ },
      removeListener() { /* no-op */ },
      hasListener()    { return false; },
      hasListeners()   { return false; },
    });

    // chrome.runtime — direct assign (ungoogled-chromium has no runtime natively)
    _c.runtime = {
      id: undefined,
      lastError: null,
      onConnect:              _mk(), onConnectExternal:    _mk(),
      onMessage:              _mk(), onMessageExternal:    _mk(),
      onInstalled:            _mk(), onStartup:            _mk(),
      onSuspend:              _mk(), onSuspendCanceled:    _mk(),
      onUpdateAvailable:      _mk(), onRestartRequired:    _mk(),
      connect()         { throw new Error('Extension context not available.'); },
      connectNative()   { throw new Error('Extension context not available.'); },
      sendMessage()     { throw new Error('Extension context not available.'); },
      sendNativeMessage(){ throw new Error('Extension context not available.'); },
      getManifest()     { return undefined; },
      getURL(p)         { return 'chrome-extension://undefined/' + (p || ''); },
      reload()          { location.reload(); },
      setUninstallURL() { /* no-op */ },
      openOptionsPage() { /* no-op */ },
      getContexts()     { return Promise.resolve([]); },
      getPlatformInfo(cb) {
        const info = { os: 'linux', arch: 'x86-64', nacl_arch: 'x86-64' };
        if (cb) cb(info); return Promise.resolve(info);
      },
      requestUpdateCheck(cb) { if (cb) cb('no_update', {}); },
      PlatformOs:  { ANDROID:'android', CROS:'cros', LINUX:'linux', MAC:'mac', OPENBSD:'openbsd', WIN:'win' },
      PlatformArch:{ ARM:'arm', ARM64:'arm64', MIPS:'mips', MIPS64:'mips64', X86_32:'x86-32', X86_64:'x86-64' },
      PlatformNaclArch: { ARM:'arm', MIPS:'mips', MIPS64:'mips64', X86_32:'x86-32', X86_64:'x86-64' },
      OnInstalledReason:        { CHROME_UPDATE:'chrome_update', INSTALL:'install', SHARED_MODULE_UPDATE:'shared_module_update', UPDATE:'update' },
      OnRestartRequiredReason:  { APP_UPDATE:'app_update', OS_UPDATE:'os_update', PERIODIC:'periodic' },
      RequestUpdateCheckStatus: { NO_UPDATE:'no_update', THROTTLED:'throttled', UPDATE_AVAILABLE:'update_available' },
    };

    // chrome.loadTimes — realistic timing
    const _t0 = Date.now() / 1000 - (Math.random() * 0.3 + 0.1);
    _c.loadTimes = function() {
      return {
        requestTime: _t0, startLoadTime: _t0, commitLoadTime: _t0 + 0.05,
        finishDocumentLoadTime: _t0 + 0.4, finishLoadTime: _t0 + 0.5,
        firstPaintTime: _t0 + 0.15, firstPaintAfterLoadTime: 0,
        navigationType: 'Other',
        wasFetchedViaSpdy: true, wasNpnNegotiated: true,
        npnNegotiatedProtocol: 'h2', wasAlternateProtocolAvailable: false,
        connectionInfo: 'h2',
      };
    };

    // chrome.csi
    _c.csi = function() {
      return { startE: Date.now(), onloadT: Date.now(), pageT: Math.random() * 800 + 200, tran: 15 };
    };

    // chrome.app
    _c.app = _c.app || {
      isInstalled: false,
      getDetails()    { return null; },
      getIsInstalled(){ return false; },
      installState(cb){ if (cb) cb('not_installed'); },
      runningState()  { return 'cannot_run'; },
      InstallState: { DISABLED:'disabled', INSTALLED:'installed', NOT_INSTALLED:'not_installed' },
      RunningState:  { CANNOT_RUN:'cannot_run', READY_TO_RUN:'ready_to_run', RUNNING:'running' },
    };

    // chrome.webstore — stub (checked by some fp tools)
    _c.webstore = {
      onInstallStageChanged: _mk(),
      onDownloadProgress:    _mk(),
      install()  { return Promise.reject(new Error('Webstore not available')); },
      ErrorCode: { ABORTED:'ABORTED', BLACKLISTED:'BLACKLISTED', BLOCKED_BY_POLICY:'BLOCKED_BY_POLICY' },
      InstallStage: { DOWNLOADING:'downloading', INSTALLING:'installing' },
    };

    // chrome.dom
    _c.dom = {
      openOrClosedShadowRoot(el) {
        try { return el.openOrClosedShadowRoot || null; } catch(e) { return null; }
      },
    };

    // chrome.action / chrome.scripting stubs
    _c.action    = _c.action    || { onClicked: _mk() };
    _c.scripting = _c.scripting || { executeScript(){ return Promise.resolve([]); }, insertCSS(){ return Promise.resolve(); } };

  } catch (_e) { /* silent */ }

  // permissions.query: patched on Permissions.prototype below — see "Permissions API" block.
  // Instance-level assignment REMOVED: navigator.permissions.hasOwnProperty('query')=true triggers PixelScan.

  // WebGL getParameter: not patched — binary handles GPU spoofing consistently
  // in both main page and Worker contexts via fingerprint-chromium --fingerprint seed.

  // === DOMRect / getBoundingClientRect noise (fixes fontFaceLoadPolicy / rect fingerprint) =
  // GeekezBrowser --rect-seed concept: add deterministic sub-pixel jitter so
  // rect-based font fingerprints differ across sessions but are stable within one session.
  try {
    var _rectSeed = (function() {
      try {
        var k = '__rect_ns__';
        var v = sessionStorage.getItem(k);
        if (!v) { v = String((Math.random() * 1e9) >>> 0); sessionStorage.setItem(k, v); }
        return (parseInt(v, 10) || 1) >>> 0;
      } catch(_) { return (Math.random() * 1e9) >>> 0; }
    })();
    var _rrs = _rectSeed || 1;
    var _rrng = function() { _rrs = (Math.imul(_rrs, 1664525) + 1013904223) >>> 0; return _rrs / 4294967296; };
    var _rectJitter = function(v) { return typeof v === 'number' ? v + (_rrng() - 0.5) * 0.02 : v; };

    var _origGBCR = Element.prototype.getBoundingClientRect;
    Element.prototype.getBoundingClientRect = function getBoundingClientRect() {
      var r = _origGBCR.apply(this, arguments);
      try {
        // Only jitter for text-rendering elements to avoid layout breakage
        var tag = (this.tagName || '').toUpperCase();
        if (tag === 'SPAN' || tag === 'DIV' || tag === 'P' || tag === 'CANVAS') {
          return {
            x: _rectJitter(r.x), y: _rectJitter(r.y),
            width: r.width, height: r.height,
            top: _rectJitter(r.top), right: _rectJitter(r.right),
            bottom: _rectJitter(r.bottom), left: _rectJitter(r.left),
            toJSON: function() { return this; }
          };
        }
      } catch(_) {}
      return r;
    };
  } catch(_) {}

  // === StorageEstimate quota spoof (YSbrowser --quota-seed) ====================
  // Headless chromium reports quota=0 or minimal; real Chrome shows ~120GB+
  try {
    // Use prototype so navigator.storage.hasOwnProperty('estimate') stays false (PixelScan detects instance override)
    if (navigator.storage && navigator.storage.estimate) {
      var _storProto = Object.getPrototypeOf(navigator.storage);
      var _origEst = navigator.storage.estimate.bind(navigator.storage);
      if (_storProto && !_storProto._estPatched) {
        Object.defineProperty(_storProto, 'estimate', {
          value: function estimate() {
            return _origEst().then(function(r) {
              if (!r || !r.quota || r.quota < 1e9) {
                return { quota: 128849018880, usage: Math.floor(Math.random() * 5e7 + 1e7) };
              }
              return r;
            });
          },
          configurable: true, writable: true,
        });
        _storProto._estPatched = true;
      }
    }
  } catch(_) {}

  // window.outerWidth/Height = innerWidth/Height when 0 (headless leak)
  try {
    if (!window.outerWidth) Object.defineProperty(window, 'outerWidth', { get: () => window.innerWidth });
    if (!window.outerHeight) Object.defineProperty(window, 'outerHeight', { get: () => window.innerHeight });
  } catch (_) {}

  // Screen properties via Proxy — ODP may fail on non-configurable window.screen.
  // Fallback: direct assignment window.screen = proxy (works when ODP throws).
  try {
    var _rs = window.screen;
    var _screenProxy = new Proxy(_rs, {
      get: function(t, p) {
        if (p === 'availWidth')  return 1920;
        if (p === 'availHeight') return 1040;
        if (p === 'width')  return 1920;
        if (p === 'height') return 1080;
        if (p === 'colorDepth') return 24;
        if (p === 'pixelDepth') return 24;
        var v = t[p]; return typeof v === 'function' ? v.bind(t) : v;
      },
    });
    try {
      Object.defineProperty(window, 'screen', { get: function() { return _screenProxy; }, configurable: true });
    } catch (_odp) {
      try { Object.getPrototypeOf(window).screen; window['screen'] = _screenProxy; } catch(_wa) {}
    }
  } catch (_) {}

  // Battery — override on prototype so it is NOT an own property of navigator.
  // PixelScan checks navigator.hasOwnProperty('getBattery') → direct assignment = detected.
  try {
    const _gbOrig = Navigator.prototype.getBattery;
    if (_gbOrig) {
      Object.defineProperty(Navigator.prototype, 'getBattery', {
        value: function getBattery() {
          return _gbOrig.call(this).then(function(bat) {
            // BatteryManager.dischargingTime is non-configurable — use Proxy to intercept reads
            if (!bat) return bat;
            if (bat.dischargingTime !== null && bat.dischargingTime !== undefined) return bat;
            // dischargingTime is null (server has no battery): wrap in Proxy returning Infinity
            return new Proxy(bat, {
              get: function(target, prop) {
                if (prop === 'dischargingTime') return Infinity;
                if (prop === 'chargingTime') return 0;
                var v = target[prop];
                return typeof v === 'function' ? v.bind(target) : v;
              },
            });
          }).catch(() => ({
            charging: true, chargingTime: 0, dischargingTime: Infinity, level: 0.99,
            addEventListener(){}, removeEventListener(){}, dispatchEvent(){return true;},
          }));
        },
        writable: true, configurable: true,
      });
    }
  } catch (_) {}

  // Connection — let real Chrome expose the native NetworkInformation.
  // Returning a plain object breaks instanceof checks; PixelScan detects it.
  // No override needed: Chrome on Linux already returns a proper NetworkInformation with effectiveType '4g'.


  // === NetworkInformation.downlinkMax (fixes noDownlinkMax like-headless flag) ============
  // Chrome deprecated/removed downlinkMax ~M110 for privacy, but it existed on ALL prior
  // desktop Chrome returning Infinity for wired connections. fingerprint-chromium strips it.
  // Override on prototype (not instance) so navigator.connection.hasOwnProperty('downlinkMax')
  // stays false — PixelScan-safe. CreepJS probes 'downlinkMax' in navigator.connection.
  try {
    var _conn = navigator.connection;
    if (_conn && !('downlinkMax' in _conn)) {
      var _connProto = Object.getPrototypeOf(_conn);
      if (_connProto && !_connProto._dlMaxPatched) {
        Object.defineProperty(_connProto, 'downlinkMax', {
          get: function() { return Infinity; },
          configurable: true, enumerable: true,
        });
        _connProto._dlMaxPatched = true;
      }
    }
    // Also expose window.NetworkInformation globally (CreepJS checks window.NetworkInformation?.prototype)
    // If ungoogled-chromium strips the global constructor, the fallback {} makes noDownlinkMax=true.
    try {
      if (typeof window.NetworkInformation === 'undefined' && _conn && _conn.constructor && _conn.constructor !== Object) {
        Object.defineProperty(window, 'NetworkInformation', {
          value: _conn.constructor,
          configurable: true, writable: true, enumerable: false,
        });
      }
    } catch(_n) {}
  } catch (_) {}

  // Notification.permission always 'default'.
  // window.Notification is a getter — direct assignment silently fails.
  // ODP with get: () => proxy ensures the Proxy is always returned.
  try {
    if (window.Notification) {
      var _RealNotif = window.Notification;
      var _notifProxy = new Proxy(_RealNotif, {
        get: function(t, p) {
          if (p === 'permission') return 'default';
          var v = t[p]; return typeof v === 'function' ? v.bind(t) : v;
        },
        construct: function(t, args) { return new t(...args); },
      });
      try {
        Object.defineProperty(window, 'Notification', {
          get: function() { return _notifProxy; },
          configurable: true, enumerable: true,
        });
      } catch (_odp) {
        try { window.Notification = _notifProxy; } catch (_) {}
      }
    }
  } catch (_) {}

  // wrap() is a no-op — existing call sites compile without error, no toString spoofing.
  // fakeFns WeakSet REMOVED — it is a known puppeteer-extra-plugin-stealth v2 signature
  // detected by Fingerprint.com as bot_type:"puppeteer_stealth". Use plain array instead.
  var _cslRefs = []; // replaces fakeFns WeakSet for Kasada console protection
  var wrap = function(fn) { return fn; }; // no-op: does NOT modify toString

  // === WebRTC IP leak protection ===
  // Sites probe local/public IP via RTCPeerConnection ICE candidates. Strip
  // host/srflx candidates that would expose the real network identity.
  try {
    const PC = window.RTCPeerConnection || window.webkitRTCPeerConnection || window.mozRTCPeerConnection;
    if (PC) {
      const origCreateOffer = PC.prototype.createOffer;
      const origAddIceCandidate = PC.prototype.addIceCandidate;
      const origSetLocalDescription = PC.prototype.setLocalDescription;
      // Filter out candidate lines exposing private IPs in SDP
      function sanitizeSdp(sdp) {
        if (!sdp || typeof sdp !== 'string') return sdp;
        return sdp.split('\\r\\n').filter((l) => !/^a=candidate:/i.test(l)).join('\\r\\n');
      }
      PC.prototype.setLocalDescription = function setLocalDescription(desc) {
        if (desc && desc.sdp) desc.sdp = sanitizeSdp(desc.sdp);
        return origSetLocalDescription.apply(this, arguments);
      };
      PC.prototype.addIceCandidate = function addIceCandidate(cand) {
        try {
          const c = (cand && (cand.candidate || (typeof cand === "string" ? cand : ""))) || "";
          if (/^candidate:/i.test(c)) return Promise.resolve();
        } catch (_) {}
        return origAddIceCandidate.apply(this, arguments);
      };
      try { wrap(PC.prototype.setLocalDescription); wrap(PC.prototype.addIceCandidate); } catch (_) {}
    }
  } catch (_) {}

  // === mediaDevices: remove fake enumerateDevices/getUserMedia mocks ===
  // Fake device IDs ("cam1xy" etc.) are obviously synthetic and detected by PixelScan.
  // Let Chrome enumerate real/native devices — on this server that returns [] or PulseAudio virtual
  // devices, both of which are natural. WebRTC IP leak is handled by RTCPeerConnection SDP above.
  // Only keep getDisplayMedia rejection (not suspicious — real desktop Chrome needs screen-share perm).
  try {
    if (navigator.mediaDevices) {
      const fakeGDM = function getDisplayMedia() { return Promise.reject(new DOMException("Permission denied", "NotAllowedError")); };
      try { wrap(fakeGDM); } catch (_) {}
      try {
        const proto = Object.getPrototypeOf(navigator.mediaDevices);
        Object.defineProperty(proto, "getDisplayMedia", { value: fakeGDM, configurable: true, writable: true });
      } catch (_) {}
    }
  } catch (_) {}

  // === navigator.language matches languages[0] ===
  try { Object.defineProperty(Navigator.prototype, 'language', { get: _mkN(function language() { return 'en-US'; }, 'get language'), configurable: true }); } catch (_) {}

  // === Intl/timezone consistency check ===
  // Context already pins timezoneId, but some libs read DateTimeFormat directly.
  // Ensure reported timezone matches context (America/Los_Angeles).
  try {
    const origRO = Intl.DateTimeFormat.prototype.resolvedOptions;
    Intl.DateTimeFormat.prototype.resolvedOptions = function resolvedOptions() {
      const r = origRO.apply(this, arguments);
      if (!r.timeZone || r.timeZone === "UTC") r.timeZone = "${BROWSER_TIMEZONE}";
      if (!r.locale || r.locale === "en-GB") r.locale = "en-US";
      return r;
    };
    try { wrap(Intl.DateTimeFormat.prototype.resolvedOptions); } catch (_) {}
  } catch (_) {}

  // === Date.prototype.getTimezoneOffset → PST/PDT ===
  // (Playwright's timezoneId already handles this for new pages; reinforced
  // here so SPA code that runs before our init sees correct offset on Node-driven
  // bundles.) America/Los_Angeles offset is +480 (PST) or +420 (PDT).
  try {
    const origGTO = Date.prototype.getTimezoneOffset;
    Date.prototype.getTimezoneOffset = function getTimezoneOffset() {
      const v = origGTO.call(this);
      if (v === 0 && ${_TZ_STD} !== 0) {
        const month = this.getUTCMonth();
        return ${_TZ_HAS_DST} ? ((month >= 2 && month <= 10) ? ${_TZ_DST} : ${_TZ_STD}) : ${_TZ_STD};
      }
      return v;
    };
    try { wrap(Date.prototype.getTimezoneOffset); } catch (_) {}
  } catch (_) {}

  // userAgentData JS patch REMOVED.
  // fingerprint-chromium (ungoogled) provides null userAgentData in BOTH main page
  // and DedicatedWorker → consistent → 0% headless.
  // CF uses HTTP sec-ch-ua-* headers (set in newFreshContext/getStickyContext), not JS API.

  // === Canvas / Audio / WebGL fingerprint noise ===
  // v7.79 — CF/CreepJS canvas hash 黑名单已收录 stock playwright + Mesa-SwiftShader
  // 的确定性指纹. 注入 deterministic-per-session 微噪声: 同会话内多次读返回一致
  // (避免 sanity check 失败), 不同会话产生不同 hash → 黑名单失效.
  try {
    // sessionStorage seed: 同一 ctx 一致, 跨 ctx 不同
    var _noiseSeed;
    try {
      var _k = "__cnv_noise_seed__";
      var _v = sessionStorage.getItem(_k);
      if (!_v) { _v = String(Math.floor(Math.random() * 1e9)); sessionStorage.setItem(_k, _v); }
      _noiseSeed = (parseInt(_v, 10) || 1) >>> 0;
    } catch (_) { _noiseSeed = (Math.random() * 1e9) >>> 0; }
    var _rngState = _noiseSeed || 1;
    var _rand = function () {
      _rngState = (Math.imul(_rngState, 1664525) + 1013904223) >>> 0;
      return _rngState / 4294967296;
    };

    // -- CanvasRenderingContext2D.getImageData: GeekezBrowser alpha-only sparse noise --
    // v8.90: REPLACED RGB-LSB XOR (creates detectable 'rgba noise' on all 3 channels)
    // with alpha-only perturbation at every 53rd pixel (prime stride avoids grid patterns).
    // CreepJS rgba-noise check compares R/G/B channel statistics — alpha-only is invisible
    // to that check while still breaking cross-session canvas hashing. No putImageData
    // permanently mutating the canvas (which caused double-noise via toDataURL path).
    var _origGID = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function () {
      var img = _origGID.apply(this, arguments);
      try {
        var d = img.data;
        var noiseVal = ((_noiseSeed & 1) === 0) ? 1 : -1;
        for (var i = 0; i < d.length; i += 4) {
          if ((i / 4 + _noiseSeed) % 53 === 0 && d[i + 3] > 0 && d[i + 3] < 255) {
            d[i + 3] = Math.max(1, Math.min(254, d[i + 3] + noiseVal));
          }
        }
      } catch (_) {}
      return img;
    };
    try { wrap(CanvasRenderingContext2D.prototype.getImageData); } catch (_) {}

    // -- AudioBuffer.getChannelData: 1 ± 5e-8 微抖, 听觉零差异 --
    try {
      if (typeof AudioBuffer !== 'undefined' && AudioBuffer.prototype.getChannelData) {
        var _origGCD = AudioBuffer.prototype.getChannelData;
        AudioBuffer.prototype.getChannelData = function () {
          var d = _origGCD.apply(this, arguments);
          try {
            var factor = 1 + (_rand() - 0.5) * 1e-7;
            var n = Math.min(d.length, 1024);
            for (var i = 0; i < n; i++) d[i] = d[i] * factor;
          } catch (_) {}
          return d;
        };
        try { wrap(AudioBuffer.prototype.getChannelData); } catch (_) {}
      }
    } catch (_) {}

    // -- WebGL readPixels: noise REMOVED (v8.90) --
    // RGB-XOR on readPixels created detectable 'rgba noise' pattern.
    // fingerprint-chromium --fingerprint seed handles WebGL natively.
  } catch (_) {}


  // === Speech Synthesis voices (headless = 0 voices) =======================
  try {
    var _fv = [
      { voiceURI:'Google US English',        name:'Google US English',        lang:'en-US', localService:false, default:true  },
      { voiceURI:'Google UK English Female', name:'Google UK English Female', lang:'en-GB', localService:false, default:false },
      { voiceURI:'Google UK English Male',   name:'Google UK English Male',   lang:'en-GB', localService:false, default:false },
      { voiceURI:'Google Deutsch',           name:'Google Deutsch',           lang:'de-DE', localService:false, default:false },
      { voiceURI:'Google espanol',           name:'Google espanol',           lang:'es-ES', localService:false, default:false },
      { voiceURI:'Google francais',          name:'Google francais',          lang:'fr-FR', localService:false, default:false },
      { voiceURI:'Google italiano',          name:'Google italiano',          lang:'it-IT', localService:false, default:false },
      { voiceURI:'Google nihongo',           name:'Google nihongo',           lang:'ja-JP', localService:false, default:false },
      { voiceURI:'Google hangugeo',          name:'Google hangugeo',          lang:'ko-KR', localService:false, default:false },
      { voiceURI:'Google portugues',         name:'Google portugues',         lang:'pt-BR', localService:false, default:false },
      { voiceURI:'Google russkiy',           name:'Google russkiy',           lang:'ru-RU', localService:false, default:false },
      { voiceURI:'Google putonghua',         name:'Google putonghua',         lang:'zh-CN', localService:false, default:false },
    ];
    _fv.forEach(function(v) { try { Object.setPrototypeOf(v, SpeechSynthesisVoice.prototype); } catch(_){} });
    if (typeof speechSynthesis !== 'undefined') {
      Object.defineProperty(speechSynthesis, 'getVoices', { value: function(){ return _fv; }, writable:true, configurable:true });
      try { speechSynthesis.dispatchEvent(new Event('voiceschanged')); } catch(_) {}
    }
    if (typeof SpeechSynthesis !== 'undefined') {
      Object.defineProperty(SpeechSynthesis.prototype, 'getVoices', { value: function(){ return _fv; }, writable:true, configurable:true });
    }
  } catch (_) {}

  // === Kasada/CF console detection (b field) — mark via existing fakeFns ===
  try {
    ["log","warn","error","info","debug","trace","dir","table","count","countReset",
     "group","groupCollapsed","groupEnd","time","timeEnd","timeLog","assert","clear"
    ].forEach(function(m) {
      try { if (typeof console[m] === "function") _cslRefs.push(console[m]); } catch(_) {}
    });
  } catch(_) {}

  // === Missing Chrome 144 API stubs — fix "load: Like undefined" (v8.90) ====
  // Real Chrome 144 on Linux desktop exposes these APIs; absence is flagged by
  // CreepJS as 'Like undefined' which inflates the like-headless percentage.

  // File System Access API
  try {
    if (typeof window.showOpenFilePicker === 'undefined') {
      window.showOpenFilePicker = function showOpenFilePicker() {
        return Promise.reject(new DOMException('The user aborted a request.', 'AbortError'));
      };
    }
    if (typeof window.showSaveFilePicker === 'undefined') {
      window.showSaveFilePicker = function showSaveFilePicker() {
        return Promise.reject(new DOMException('The user aborted a request.', 'AbortError'));
      };
    }
    if (typeof window.showDirectoryPicker === 'undefined') {
      window.showDirectoryPicker = function showDirectoryPicker() {
        return Promise.reject(new DOMException('The user aborted a request.', 'AbortError'));
      };
    }
  } catch (_) {}

  // File Handling API — launchQueue
  try {
    if (typeof window.launchQueue === 'undefined') {
      var _lq = { _handlers: [], setConsumer: function(cb) { this._handlers.push(cb); } };
      Object.defineProperty(window, 'launchQueue', { value: _lq, configurable: true, writable: true });
    }
  } catch (_) {}

  // Document Picture-in-Picture API
  try {
    if (typeof window.documentPictureInPicture === 'undefined') {
      Object.defineProperty(window, 'documentPictureInPicture', {
        value: { requestWindow: function() { return Promise.reject(new DOMException('Not supported', 'NotSupportedError')); } },
        configurable: true, writable: true,
      });
    }
  } catch (_) {}

  // navigator.userActivation
  try {
    if (typeof navigator.userActivation === 'undefined') {
      Object.defineProperty(Navigator.prototype, 'userActivation', {
        get: _mkN(function userActivation() { return { hasBeenActive: true, isActive: false }; }, 'get userActivation'),
        configurable: true,
      });
    }
  } catch (_) {}

  // navigator.scheduling (Scheduler API — Chrome 94+)
  try {
    if (typeof navigator.scheduling === 'undefined') {
      Object.defineProperty(Navigator.prototype, 'scheduling', {
        get: function() { return { isInputPending: function() { return false; } }; },
        configurable: true,
      });
    }
  } catch (_) {}

  // navigator.virtualKeyboard (Virtual Keyboard API — Chrome 94+)
  try {
    if (typeof navigator.virtualKeyboard === 'undefined') {
      Object.defineProperty(Navigator.prototype, 'virtualKeyboard', {
        get: function() {
          return { show: function(){}, hide: function(){}, overlaysContent: false,
                   boundingRect: { x:0, y:0, width:0, height:0, top:0, right:0, bottom:0, left:0 },
                   addEventListener: function(){}, removeEventListener: function(){} };
        },
        configurable: true,
      });
    }
  } catch (_) {}

  // navigator.windowControlsOverlay (Window Controls Overlay — Chrome 92+)
  try {
    if (typeof navigator.windowControlsOverlay === 'undefined') {
      Object.defineProperty(Navigator.prototype, 'windowControlsOverlay', {
        get: function() {
          return { visible: false,
                   getTitlebarAreaRect: function() { return { x:0,y:0,width:0,height:0 }; },
                   addEventListener: function(){}, removeEventListener: function(){} };
        },
        configurable: true,
      });
    }
  } catch (_) {}

  // navigator.ink (Ink API — Chrome 94+)
  try {
    if (typeof navigator.ink === 'undefined') {
      Object.defineProperty(Navigator.prototype, 'ink', {
        get: function() {
          return { requestPresenter: function() {
            return Promise.reject(new DOMException('Not supported', 'NotSupportedError'));
          }};
        },
        configurable: true,
      });
    }
  } catch (_) {}

  // === Keyboard API presence (headless leak) ================================
  try {
    if (typeof navigator.keyboard === 'undefined') {
      Object.defineProperty(Navigator.prototype, 'keyboard', {
        get: function() { return { getLayoutMap: function() { return Promise.resolve(new Map()); } }; },
        configurable: true,
      });
    }
  } catch (_) {}

  // === Permissions API — override headless "denied" defaults ================
  try {
    // Use prototype so navigator.permissions.hasOwnProperty('query') stays false (PixelScan detects instance override)
    var _permProto = navigator.permissions && Object.getPrototypeOf(navigator.permissions);
    var _oPQ = navigator.permissions && navigator.permissions.query && navigator.permissions.query.bind(navigator.permissions);
    if (_permProto && _oPQ && !_permProto._pqPatched) {
      Object.defineProperty(_permProto, 'query', {
        value: function query(desc) {
          var n = desc && desc.name;
          if (n === 'notifications') return Promise.resolve({ state: 'default', onchange: null });
          if (n === 'clipboard-read' || n === 'clipboard-write') return Promise.resolve({ state: 'prompt', onchange: null });
          return _oPQ(desc);
        },
        writable: true, configurable: true,
      });
      _permProto._pqPatched = true;
    }
  } catch (_) {}

  // === pdfViewerEnabled (Chrome ≥ 105) ===
  try {
    Object.defineProperty(Navigator.prototype, 'pdfViewerEnabled', { get: () => true, configurable: true });
  } catch (_) {}
  // === Web Share API stub (noWebShare fix) ===
  // ODP(Navigator.prototype) and direct assignment both silently fail on Linux Chrome builds.
  // Robust fix: try ODP on prototype first, then Proxy window.navigator as fallback.
  try {
    var _shareStub = function share() { return Promise.reject(new DOMException('Share canceled','AbortError')); };
    var _canShareStub = function canShare() { return true; };
    try {
      if (!('share' in Navigator.prototype)) {
        Object.defineProperty(Navigator.prototype, 'share', { value: _shareStub, writable: true, configurable: true, enumerable: false });
      }
    } catch(_p1) {}
    try {
      if (!('canShare' in Navigator.prototype)) {
        Object.defineProperty(Navigator.prototype, 'canShare', { value: _canShareStub, writable: true, configurable: true, enumerable: false });
      }
    } catch(_p2) {}
    // Fallback: if still absent, wrap window.navigator in a Proxy
    if (!('share' in navigator)) {
      var _realNav = window.navigator;
      var _navProxy = new Proxy(_realNav, {
        get: function(t, p) {
          if (p === 'share') return _shareStub;
          if (p === 'canShare') return _canShareStub;
          var v = t[p]; return typeof v === 'function' ? v.bind(t) : v;
        },
        has: function(t, p) {
          if (p === 'share' || p === 'canShare') return true;
          return p in t;
        },
      });
      try {
        Object.defineProperty(window, 'navigator', { get: function() { return _navProxy; }, configurable: true });
      } catch(_no) {}
    }
  } catch (_) {}

  // === prefers-color-scheme: dark (fixes prefersLightColor like-headless flag) =========
  // Xvfb returns "light" by default; real desktop Chrome on Linux typically follows system
  // dark/light setting. Override matchMedia so (prefers-color-scheme: light).matches = false.
  try {
    var _origMQL = window.matchMedia.bind(window);
    Object.defineProperty(window, "matchMedia", {
      value: function matchMedia(query) {
        var mql = _origMQL(query);
        if (typeof query === "string" && query.indexOf("prefers-color-scheme") !== -1) {
          var isLight = query.indexOf("light") !== -1;
          var isDark = query.indexOf("dark") !== -1;
          if (isLight || isDark) {
            // Return a MediaQueryList-like object that reports dark mode preference
            return Object.defineProperties(Object.create(mql), {
              matches: { get: function() { return isDark; }, configurable: true },
              media:   { get: function() { return mql.media;  }, configurable: true },
              onchange:{ value: null, writable: true, configurable: true },
              addListener:    { value: mql.addListener.bind(mql), configurable: true },
              removeListener: { value: mql.removeListener.bind(mql), configurable: true },
              addEventListener:    { value: mql.addEventListener.bind(mql), configurable: true },
              removeEventListener: { value: mql.removeEventListener.bind(mql), configurable: true },
              dispatchEvent: { value: mql.dispatchEvent.bind(mql), configurable: true },
            });
          }
        }
        return mql;
      },
      writable: true, configurable: true,
    });
  } catch (_) {}

  // === getComputedStyle: spoof ActiveText system color (fixes hasKnownBgColor) ============
  // Xvfb default X11 theme returns rgb(255,0,0) for CSS system color ActiveText.
  // CreepJS: if background-color:ActiveText resolves to red → hasKnownBgColor=true → like-headless.
  // Real desktop Chrome on Linux follows GTK theme and rarely returns pure red.
  // Patch: intercept getComputedStyle only when el has explicit inline ActiveText bg-color.
  try {
    var _gCS = window.getComputedStyle;
    window.getComputedStyle = function getComputedStyle(el, pseudo) {
        var result = _gCS(el, pseudo);
        try {
          if (el && el.getAttribute) {
            var inl = el.getAttribute("style") || "";
            if (/background-color\s*:\s*ActiveText/i.test(inl)) {
              return new Proxy(result, {
                get: function(t, p) {
                  if (p === "backgroundColor") return "rgb(0, 120, 212)";
                  var v = t[p];
                  return typeof v === "function" ? v.bind(t) : v;
                }
              });
            }
          }
        } catch(_2) {}
        return result;
      },
    };
  } catch (_) {}


  // === ContentIndex stub (noContentIndex fix): getPlatformEstimate checks 'ContentIndex' in window ===
  try {
    if (typeof window.ContentIndex === 'undefined') {
      window.ContentIndex = function ContentIndex() { throw new TypeError('Illegal constructor'); };
    }
  } catch(_) {}

  // === userAgentData.platform fix (uaDataIsBlank fix) ===
  // fingerprint-chromium may leave navigator.userAgentData.platform='' → uaDataIsBlank=true.
  try {
    var _uad = navigator.userAgentData;
    if (_uad && !_uad.platform) {
      try { Object.defineProperty(_uad, 'platform', { get: function() { return 'Linux'; }, configurable: true }); } catch(_up) {}
    }
  } catch(_) {}

})();


`;


// WORKER_STEALTH_PATCH: injected into DedicatedWorkers via page.on('worker').
// addInitScript() does NOT reach Workers. getHighEntropyValues() is Promise-
// based, so this patch wins the race against the worker's early async calls.
const WORKER_STEALTH_PATCH = `(function() {

  // === Worker native-code spoofing: per-function (no global override) ===========
  // Same rationale as main page: avoids CreepJS cross-realm iframe toString detection.
  function _wMkN(fn, name) {
    // Same bind() trick as main page _mkN: bound functions are natively [native code].
    try {
      var bound = fn.bind(null);
      if (name) { try { Object.defineProperty(bound, "name", { value: name }); } catch(_) {} }
      return bound;
    } catch(_) { return fn; }
  }
  // Worker context: no window, use self. navigator = WorkerNavigator.
  // Mirrors the three STEALTH_INIT patches that CreepJS checks in SharedWorker probes.

  // === Web Share API stub (noWebShare) ===
  // WorkerNavigator lacks share/canShare on Linux Chrome — same absence as main page pre-patch.
  try {
    var _nav = self.navigator;
    if (_nav && !("share" in _nav)) {
      Object.defineProperty(_nav.constructor.prototype, "share", {
        value: function share() { return Promise.reject(new DOMException("Share canceled", "AbortError")); },
        writable: true, configurable: true, enumerable: true,
      });
    }
    if (_nav && !("canShare" in _nav)) {
      Object.defineProperty(_nav.constructor.prototype, "canShare", {
        value: function canShare() { return false; },
        writable: true, configurable: true, enumerable: true,
      });
    }
  } catch (_) {}

  // === hardwareConcurrency + deviceMemory consistency with main page ===
  // PixelScan checks Worker.hardwareConcurrency vs navigator.hardwareConcurrency.
  // If we override main page to 8 but Worker returns native (e.g. 2), mismatch = Detected.
  try {
    var _wNav = self.navigator;
    var _wNavProto = _wNav && _wNav.constructor && _wNav.constructor.prototype;
    if (_wNavProto) {
      if (!('_hcPatched' in _wNavProto)) {
        Object.defineProperty(_wNavProto, 'hardwareConcurrency', { get: _wMkN(function hardwareConcurrency() { return 4; }, 'get hardwareConcurrency'), configurable: true });
        Object.defineProperty(_wNavProto, 'deviceMemory', { get: function() { return 4; }, configurable: true });
        Object.defineProperty(_wNavProto, 'platform', { get: function() { return 'Linux x86_64'; }, configurable: true });
        _wNavProto._hcPatched = true;
      }
    }
  } catch (_) {}

  // === language/languages consistency with main page ===
  // Navigator.prototype overrides don't reach WorkerNavigator.prototype.
  // Add explicit language overrides so PixelScan cross-frame check passes.
  try {
    var _wNav2 = self.navigator;
    var _wNavProto2 = _wNav2 && _wNav2.constructor && _wNav2.constructor.prototype;
    if (_wNavProto2 && !('_langPatched' in _wNavProto2)) {
      var _wCachedLangs = Object.freeze(['en-US', 'en']);
      Object.defineProperty(_wNavProto2, 'language', { get: _wMkN(function language() { return 'en-US'; }, 'get language'), configurable: true });
      Object.defineProperty(_wNavProto2, 'languages', { get: _wMkN(function languages() { return _wCachedLangs; }, 'get languages'), configurable: true });
      _wNavProto2._langPatched = true;
    }
  } catch (_) {}

  // === matchMedia stub (prefersLightColor) ===
  // matchMedia is not natively available in Worker scope.
  // Stub it to report dark-mode preference, consistent with main page patch.
  try {
    if (typeof self.matchMedia !== "function") {
      self.matchMedia = function matchMedia(query) {
        var q = String(query || "");
        var isDark = q.indexOf("prefers-color-scheme") !== -1 && q.indexOf("dark") !== -1;
        return {
          matches: isDark,
          media: q,
          onchange: null,
          addListener: function() {},
          removeListener: function() {},
          addEventListener: function() {},
          removeEventListener: function() {},
          dispatchEvent: function() { return false; },
        };
      };
    }
  } catch (_) {}

  // === getComputedStyle stub (hasKnownBgColor / ActiveText) ===
  // getComputedStyle is not natively available in Worker scope.
  // Stub returns a proxy that reports the same spoofed ActiveText value as main page.
  try {
    if (typeof self.getComputedStyle !== "function") {
      self.getComputedStyle = function getComputedStyle() {
        return new Proxy({}, {
          get: function(t, p) {
            if (p === "backgroundColor") return "rgb(0, 120, 212)";
            if (p === "getPropertyValue") return function() { return ""; };
            return "";
          },
        });
      };
    }
  } catch (_) {}

  // === StorageEstimate in Worker ===============================================
  // Use prototype so hasOwnProperty('estimate') stays false (PixelScan detects instance override)
  try {
    if (self.navigator && self.navigator.storage && self.navigator.storage.estimate) {
      var _wStorProto = Object.getPrototypeOf(self.navigator.storage);
      var _wOrigEst = self.navigator.storage.estimate.bind(self.navigator.storage);
      if (_wStorProto && !_wStorProto._wEstPatched) {
        Object.defineProperty(_wStorProto, 'estimate', {
          value: function estimate() {
            return _wOrigEst().then(function(r) {
              if (!r || !r.quota || r.quota < 1e9) {
                return { quota: 128849018880, usage: 15728640 };
              }
              return r;
            });
          },
          configurable: true, writable: true,
        });
        _wStorProto._wEstPatched = true;
      }
    }
  } catch(_) {}

  // === Worker WebGL renderer (hasSwiftShader fix) ==============================
  // CreepJS probes workerScope.webglRenderer via OffscreenCanvas in a DedicatedWorker.
  // fingerprint-chromium spoofs main-page GPU via --fingerprint but NOT OffscreenCanvas
  // in workers -> SwiftShader leaks -> hasSwiftShader = TRUE (extra like-headless flag).
  // GeekezBrowser-style: hook getParameter on prototype, not instance (PixelScan-safe).
  try {
    var _W_RENDERER = "ANGLE (NVIDIA Corporation, NVIDIA GeForce RTX 4080/PCIe/SSE2, OpenGL 4.5.0)";
    var _W_VENDOR   = "Google Inc. (NVIDIA Corporation)";
    var _W_GL_KEY   = "__wGlSpoofed__";
    var _W_DBG_EXT  = { UNMASKED_VENDOR_WEBGL: 37445, UNMASKED_RENDERER_WEBGL: 37446 };

    function _wHookGlProto(proto) {
      if (!proto || proto[_W_GL_KEY]) return;
      try {
        var _oGP = proto.getParameter;
        var _oGE = proto.getExtension;
        proto.getParameter = function getParameter(param) {
          if (param === 37445) return _W_VENDOR;
          if (param === 37446) return _W_RENDERER;
          if (param === 7936)  return _W_VENDOR;
          if (param === 7937)  return _W_RENDERER;
          return _oGP.apply(this, arguments);
        };
        proto.getExtension = function getExtension(name) {
          if (name === "WEBGL_debug_renderer_info") return _W_DBG_EXT;
          return _oGE.apply(this, arguments);
        };
        proto[_W_GL_KEY] = true;
      } catch(_) {}
    }

    _wHookGlProto(self.WebGLRenderingContext  && self.WebGLRenderingContext.prototype);
    _wHookGlProto(self.WebGL2RenderingContext && self.WebGL2RenderingContext.prototype);

    if (self.OffscreenCanvas && self.OffscreenCanvas.prototype && self.OffscreenCanvas.prototype.getContext) {
      var _oOCGC = self.OffscreenCanvas.prototype.getContext;
      self.OffscreenCanvas.prototype.getContext = function getContext(type) {
        var ctx = _oOCGC.apply(this, arguments);
        if (ctx) {
          var t = String(type || "").toLowerCase();
          if (t === "webgl" || t === "experimental-webgl" || t === "webgl2") {
            try { _wHookGlProto(Object.getPrototypeOf(ctx)); } catch(_) {}
          }
        }
        return ctx;
      };
    }
  } catch(_) {}
})();`;

// Worker constructor bootstrap (GeekezBrowser approach):
// Hook window.Worker/SharedWorker to inject WORKER_STEALTH_PATCH as Blob URL FIRST.
// This guarantees our patch code runs before the actual worker script — fixes the
// CreepJS Worker stealth race condition that page.on("worker") w.evaluate() loses.
const _WORKER_BOOT_SUFFIX = `;(function(){
  try {
    var _OW = self.Worker;
    var _OSW = self.SharedWorker;
    var _wp = ` + JSON.stringify(WORKER_STEALTH_PATCH) + `;
    function _hookW(Ctor, name) {
      if (typeof Ctor !== "function") return;
      function _Hooked(url, opts) {
        try {
          var absUrl;
          try { absUrl = new URL(String(url), self.location.href).href; } catch(_e) { absUrl = String(url); }
          var code = _wp + "\n;importScripts(" + JSON.stringify(absUrl) + ");";
          var blob = new Blob([code], {type:"application/javascript"});
          var bu = URL.createObjectURL(blob);
          var w = new Ctor(bu, opts);
          setTimeout(function(){try{URL.revokeObjectURL(bu);}catch(_e){}}, 15000);
          return w;
        } catch(_e) { return new Ctor(url, opts); }
      }
      _Hooked.prototype = Ctor.prototype;
      try { Object.defineProperty(self, name, {value:_Hooked, configurable:true, writable:true}); }
      catch(_e) { try { self[name] = _Hooked; } catch(_e2) {} }
    }
    _hookW(_OW, "Worker");
    _hookW(_OSW, "SharedWorker");
  } catch(_e) {}
})();`;
const STEALTH_INIT_FULL = STEALTH_INIT + _WORKER_BOOT_SUFFIX;

async function getBrowser(): Promise<Browser> {
  // If browser exists but is no longer connected (process died), reset.
  if (browserPromise) {
    try {
      const b = await browserPromise;
      if (!b.isConnected()) {
        browserPromise = null;
        for (const k of stickyContexts.keys()) stickyContexts.delete(k);
        stickyExpiry.clear();
      }
    } catch {
      browserPromise = null;
    }
  }
  if (!browserPromise) {
    const _fpChromeBin = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
    const _useFpChrome = fs.existsSync(_fpChromeBin);
    const executablePath = process.env.REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE
      || (_useFpChrome ? _fpChromeBin : "/data/cache/ms-playwright/chromium-1208/chrome-linux64/chrome");
    const proxyServer = process.env.BROWSER_PROXY || undefined;
    const userDataDir = "/tmp/broker-chromium-profile";
    try { fs.mkdirSync(userDataDir, { recursive: true }); } catch { /* ignore */ }
    // We must spawn chromium directly (not chromium.launch) because Playwright
    // forces --remote-debugging-pipe transport, which suppresses the HTTP
    // DevTools server on :9222 — making external CDP attach (replit_register.py
    // connect_over_cdp) impossible. By spawning ourselves with TCP CDP only
    // and then calling connectOverCDP from this process, we share the same
    // chromium instance + user-data-dir + CF cookies between broker & external
    // attach clients.
    const args: string[] = [
      "--no-sandbox",
      "--disable-blink-features=AutomationControlled",
      "--disable-features=IsolateOrigins,site-per-process,AutomationControlled,Translate",
      "--disable-dev-shm-usage",
      "--no-default-browser-check",
      "--no-first-run",
      "--password-store=basic",
      "--use-mock-keychain",
      "--remote-debugging-port=9222",
      "--remote-debugging-address=127.0.0.1",
      `--user-data-dir=${userDataDir}`,
      "--window-size=1920,1080",
      "--window-position=0,0",
      "--start-maximized",
      "--use-gl=angle",
      "--use-angle=swiftshader",
      "--enable-webgl",
      "--proxy-resolves-dns-locally",
      "--enable-features=AsyncDns,DnsOverHttpsUpgrade,NetworkServiceInProcess",
      "--dns-over-https-templates=https://1.1.1.1/dns-query,https://dns.google/dns-query",
      ...(proxyServer ? [`--proxy-server=${proxyServer}`, "--disable-quic"] : []),
      // fingerprint-chromium kernel-level spoofing (Chrome 144 AppImage)
      ...(_useFpChrome ? [
        `--fingerprint=${(Math.random() * 0x7fffffff | 0)}`,
        "--fingerprint-platform=linux",
        "--fingerprint-brand=Chrome",
        "--fingerprint-brand-version=144",
        "--fingerprint-hardware-concurrency=4",
        "--lang=en-US",
        "--accept-lang=en-US,en",
        `--timezone=${BROWSER_TIMEZONE}`,
        "--disable-non-proxied-udp",
      ] : []),
      "about:blank",
    ];
    killChromiumProc();
    chromiumProc = spawn(executablePath, args, {
      env: {
        ...process.env,
        LANG: "en_US.UTF-8",
        LC_ALL: "en_US.UTF-8",
        LANGUAGE: "en_US:en",
        DISPLAY: process.env.DISPLAY ?? ":99",
      } as NodeJS.ProcessEnv,
      stdio: ["ignore", "pipe", "pipe"],
      detached: false,
    });
    chromiumProc.on("exit", (code, signal) => {
      console.error(`[renderer] chromium exited code=${code} signal=${signal}`);
      browserPromise = null;
      chromiumProc = null;
    });
    chromiumProc.stderr?.on("data", (d: Buffer) => {
      const line = d.toString();
      if (/error|fatal|fail/i.test(line)) process.stderr.write(`[chromium] ${line}`);
    });
    browserPromise = waitForCdpHttp(9222, "127.0.0.1", 30000)
      .then(() => chromium.connectOverCDP("http://127.0.0.1:9222"))
      .then((b) => {
        // v8.10 — fire-and-forget Google trust bootstrap (~40s deep visit, once
        // per chromium lifetime). Lifts reCAPTCHA Enterprise score by seeding
        // a real user-like browsing history (youtube + google + search) into
        // the persistent broker-chromium-profile + shared google-cookies cache.
        void _bootstrapGoogleTrust(b);
        return b;
      })
      .catch((err) => {
        browserPromise = null;
        killChromiumProc();
        console.error("[renderer] spawn chromium / connectOverCDP failed:", err);
        throw err;
      });
  }
  return browserPromise;
}

async function newFreshContext(): Promise<BrowserContext> {
  // Per-request incognito context. Sharing one context across requests pools
  // cookies (e.g. Google's NID / GOOGLE_ABUSE_EXEMPTION) — once any request
  // gets captcha'd, every subsequent request inherits the poisoned state.
  const browser = await getBrowser();
  const ctx = await browser.newContext({
    userAgent:
      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    viewport: { width: 1920, height: 1040 },
    screen: { width: 1920, height: 1080 },
    deviceScaleFactor: 1,
    isMobile: false,
    hasTouch: false,
    locale: "en-US",
    timezoneId: BROWSER_TIMEZONE,
    colorScheme: "light",
    ignoreHTTPSErrors: true,
    extraHTTPHeaders: {
      "Accept-Language": "en-US,en;q=0.9",
      "sec-ch-ua": "\"Chromium\";v=\"144\", \"Not:A-Brand\";v=\"99\", \"Google Chrome\";v=\"144\"",
      "sec-ch-ua-mobile": "?0",
      "sec-ch-ua-platform": "\"Linux\"",
      "sec-ch-ua-bitness": "\"64\"",
      "sec-ch-ua-arch": "\"x86\"",
      "sec-ch-ua-full-version": "\"144.0.7559.132\"",
      "sec-ch-ua-platform-version": "\"6.8.0\"",
      "sec-ch-ua-full-version-list": "\"Chromium\";v=\"144.0.7559.132\", \"Not:A-Brand\";v=\"99.0.0.0\", \"Google Chrome\";v=\"144.0.7559.132\"",
      "sec-ch-ua-model": "\"\"",
      "sec-ch-ua-wow64": "?0",
    },
  });
  ctx.on("close", () => { closedContexts.add(ctx); });
  await ctx.addInitScript(STEALTH_INIT_FULL);
  // Inject userAgentData patch into DedicatedWorkers (addInitScript doesn't reach them)
  ctx.on("page", (p) => { p.on("worker", (w) => { w.evaluate(WORKER_STEALTH_PATCH).catch(() => {}); }); });
  return ctx;
}

// eTLD+1 (cheap heuristic: last two labels). Good enough for sticky keying.
function siteKey(hostname: string): string {
  const parts = hostname.toLowerCase().split(".");
  if (parts.length <= 2) return hostname.toLowerCase();
  // Handle a few common 2-label TLDs
  const last2 = parts.slice(-2).join(".");
  const last3 = parts.slice(-3).join(".");
  if (/^(co|com|net|org|gov|edu|ac)\.[a-z]{2}$/.test(last2)) return last3;
  return last2;
}

// Sites whose cookies must NOT be reused across requests (they ban faster when
// the same identity reappears: search engines, anti-bot endpoints).
function shouldUseFreshContext(hostname: string): boolean {
  return needsJsRendering(hostname);
}

const stickyContexts = new Map<string, Promise<BrowserContext>>();
const STICKY_TTL_MS = 30 * 60 * 1000;
const stickyExpiry = new Map<string, number>();
// Track contexts whose 'close' event has fired so we never hand out a dead one.
const closedContexts = new WeakSet<BrowserContext>();

async function getStickyContext(hostname: string): Promise<BrowserContext> {
  const key = siteKey(hostname);
  const now = Date.now();
  const exp = stickyExpiry.get(key) ?? 0;
  let cached = stickyContexts.get(key);
  if (cached && now < exp) {
    try {
      const c = await cached;
      const alive = !closedContexts.has(c) && (c.browser()?.isConnected() ?? false);
      if (alive) return cached;
    } catch { /* fall through and rebuild */ }
    stickyContexts.delete(key);
    stickyExpiry.delete(key);
  }

  // Expire old one
  if (cached) {
    cached.then((c) => c.close().catch(() => {})).catch(() => {});
    stickyContexts.delete(key);
  }
  const browser = await getBrowser();
  const p = browser.newContext({
    userAgent:
      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    viewport: { width: 1920, height: 1040 },
    screen: { width: 1920, height: 1080 },
    deviceScaleFactor: 1,
    isMobile: false,
    hasTouch: false,
    locale: "en-US",
    timezoneId: BROWSER_TIMEZONE,
    colorScheme: "light",
    ignoreHTTPSErrors: true,
    extraHTTPHeaders: {
      "Accept-Language": "en-US,en;q=0.9",
      "sec-ch-ua": "\"Chromium\";v=\"144\", \"Not:A-Brand\";v=\"99\", \"Google Chrome\";v=\"144\"",
      "sec-ch-ua-mobile": "?0",
      "sec-ch-ua-platform": "\"Linux\"",
      "sec-ch-ua-bitness": "\"64\"",
      "sec-ch-ua-arch": "\"x86\"",
      "sec-ch-ua-full-version": "\"144.0.7559.132\"",
      "sec-ch-ua-platform-version": "\"6.8.0\"",
      "sec-ch-ua-full-version-list": "\"Chromium\";v=\"144.0.7559.132\", \"Not:A-Brand\";v=\"99.0.0.0\", \"Google Chrome\";v=\"144.0.7559.132\"",
      "sec-ch-ua-model": "\"\"",
      "sec-ch-ua-wow64": "?0",
    },
  }).then(async (c) => {
    c.on("close", () => { closedContexts.add(c); });
    await c.addInitScript(STEALTH_INIT_FULL);
    c.on("page", (p) => { p.on("worker", (w) => { w.evaluate(WORKER_STEALTH_PATCH).catch(() => {}); }); });
    try { await attachGoogleProxyRouting(c); } catch (e) { console.error("[google-route] attach failed:", (e as Error).message); }
    return c;
  });
  stickyContexts.set(key, p);
  stickyExpiry.set(key, now + STICKY_TTL_MS);
  return p;
}

function dropStickyContext(hostname: string): void {
  const key = siteKey(hostname);
  const cached = stickyContexts.get(key);
  if (cached) cached.then((c) => c.close().catch(() => {})).catch(() => {});
  stickyContexts.delete(key);
  stickyExpiry.delete(key);
}

async function getPageContent(page: import("playwright").Page): Promise<string> {
  // page.content() throws if the page is mid-navigation. Retry, then fall
  // back to evaluating outerHTML directly which tolerates navigation races.
  for (let i = 0; i < 4; i++) {
    try {
      return await page.content();
    } catch {
      await page.waitForTimeout(500);
    }
  }
  try {
    return await page.evaluate(
      () => "<!DOCTYPE html>" + document.documentElement.outerHTML
    );
  } catch {
    return "<!DOCTYPE html><html><body>(content unavailable: page navigating)</body></html>";
  }
}

function looksLikeCaptcha(html: string, finalUrl: string): boolean {
  if (/\/sorry\/index|\/recaptcha\/api2\/bframe|\/cdn-cgi\/challenge-platform|challenges\.cloudflare\.com\/turnstile/i.test(finalUrl)) return true;
  return /Our systems have detected unusual traffic|sending requests, and not a robot|<title>Attention Required|<title>Just a moment|Performing security verification|Unable to connect to the website|cf-browser-verification|cf_chl_opt|__cf_chl_|cdn-cgi\/challenge-platform|id="challenge-form"|name="cf-turnstile-response"/i.test(
    html
  );
}

function _classifyCaptchaHit(html: string, finalUrl: string): string {
  const tags: string[] = [];
  if (/\/sorry\/index/i.test(finalUrl)) tags.push("google-sorry");
  if (/\/recaptcha\/api2\/bframe/i.test(finalUrl)) tags.push("recaptcha-v2-visible");
  if (/\/cdn-cgi\/challenge-platform/i.test(finalUrl)) tags.push("cf-cp-url");
  if (/challenges\.cloudflare\.com\/turnstile/i.test(finalUrl)) tags.push("cf-turnstile-iframe-url");
  if (/<title>Just a moment/i.test(html)) tags.push("cf-just-a-moment");
  if (/cf-browser-verification/i.test(html)) tags.push("cf-browser-verification");
  if (/id="challenge-form"/i.test(html)) tags.push("cf-managed-challenge-form");
  if (/cf_chl_opt|__cf_chl_/i.test(html)) tags.push("cf-chl-opt-script");
  if (/name="cf-turnstile-response"/i.test(html)) tags.push("cf-turnstile-widget");
  if (/<title>Attention Required/i.test(html)) tags.push("cf-attention-required");
  if (/Performing security verification/i.test(html)) tags.push("cf-security-verification");
  if (/Our systems have detected unusual traffic|sending requests, and not a robot/i.test(html)) tags.push("google-unusual-traffic");
  if (/Unable to connect to the website/i.test(html)) tags.push("cf-unable-connect");
  if (/cdn-cgi\/challenge-platform/i.test(html)) tags.push("cf-cp-html");
  return tags.length ? tags.join(",") : "unknown";
}

async function _dumpCaptcha(html: string, finalUrl: string, status: number, attempt: number, tag: string): Promise<string> {
  try {
    const dir = "/tmp/captcha-dumps";
    await fs.promises.mkdir(dir, { recursive: true });
    const ts = new Date().toISOString().replace(/[:.]/g, "-");
    const slug = finalUrl.replace(/[^a-zA-Z0-9]+/g, "_").slice(0, 60);
    const file = `${dir}/${ts}_a${attempt}_${slug}.json`;
    await fs.promises.writeFile(file, JSON.stringify({
      ts, finalUrl, status, attempt, tag,
      html_length: html.length,
      html_first_8kb: html.slice(0, 8192),
      html_last_2kb: html.length > 8192 ? html.slice(-2048) : "",
    }, null, 2));
    return file;
  } catch { return "(dump-failed)"; }
}



// ── Human-behavior simulation layer ─────────────────────────────────────────
// Datadome + similar ML-based detectors collect: mouse event count, scroll
// events, time-on-page, event timing entropy, and interaction "naturalness".
// This layer runs AFTER page load and BEFORE HTML extraction to seed those
// signals with realistic values.

// Domains that require behavioral warm-up (Datadome / DataDome-adjacent).
// Extend this list as new protected sites are encountered.
const _BEHAVIOR_SIM_HOSTS = [
  /datadome\.co$/i,
  /(^|\.)antoinelouis\.co$/i,
  // add more Datadome-protected e-commerce/news domains here:
  /(^|\.)foot\.fr$/i,
  /(^|\.)lemonde\.fr$/i,
  /(^|\.)leboncoin\.fr$/i,
  /(^|\.)cdiscount\.com$/i,
  /(^|\.)fnac\.com$/i,
];

function _needsBehaviorSim(hostname: string): boolean {
  return _BEHAVIOR_SIM_HOSTS.some((re) => re.test(hostname));
}

// Cubic Bezier interpolation for mouse trajectories.
// Returns N evenly-spaced points along the curve.
function _bezierPoints(
  x0: number, y0: number,
  x1: number, y1: number,
  x2: number, y2: number,
  x3: number, y3: number,
  steps: number
): Array<[number, number]> {
  const pts: Array<[number, number]> = [];
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const u = 1 - t;
    const x = u*u*u*x0 + 3*u*u*t*x1 + 3*u*t*t*x2 + t*t*t*x3;
    const y = u*u*u*y0 + 3*u*u*t*y1 + 3*u*t*t*y2 + t*t*t*y3;
    pts.push([Math.round(x), Math.round(y)]);
  }
  return pts;
}

async function _behaviorSim(page: import("playwright").Page, budgetMs = 6000): Promise<void> {
  const t0 = Date.now();
  const elapsed = () => Date.now() - t0;
  const ri = (a: number, b: number) => Math.floor(Math.random() * (b - a + 1)) + a;
  const sleep = (ms: number) => page.waitForTimeout(ms).catch(() => {});

  // 1) Inject JS-side mouse/scroll event counters so Datadome sees real DOM
  //    events flowing (not just CDP synthetic events which some detectors flag).
  await page.evaluate(() => {
    const fire = (type: string, x: number, y: number) => {
      try {
        document.dispatchEvent(new MouseEvent(type, {
          bubbles: true, cancelable: true, view: window,
          clientX: x, clientY: y, screenX: x + 10, screenY: y + 80,
          movementX: Math.round(Math.random() * 6 - 3),
          movementY: Math.round(Math.random() * 6 - 3),
        }));
      } catch { /* ignore */ }
    };
    // seed initial mouse position near center
    fire("mousemove", 800, 400);
    fire("mouseover", 800, 400);
  }).catch(() => {});

  // 2) Bezier mouse movement sequences
  let cx = ri(300, 1200), cy = ri(200, 700);
  const moveCount = ri(3, 5);
  for (let m = 0; m < moveCount && elapsed() < budgetMs - 1200; m++) {
    const tx = ri(150, 1750), ty = ri(120, 900);
    // control points with natural "overshoot" bias
    const cp1x = cx + ri(-200, 200), cp1y = cy + ri(-150, 150);
    const cp2x = tx + ri(-150, 150), cp2y = ty + ri(-100, 100);
    const steps = ri(18, 35);
    const pts = _bezierPoints(cx, cy, cp1x, cp1y, cp2x, cp2y, tx, ty, steps);
    for (const [px, py] of pts) {
      await page.mouse.move(px, py).catch(() => {});
      // variable inter-step delay (fast in middle, slow at start/end)
      await sleep(ri(8, 28));
    }
    // micro-jitter pause (human hand tremor)
    for (let j = 0; j < ri(2, 4); j++) {
      await page.mouse.move(tx + ri(-3, 3), ty + ri(-3, 3)).catch(() => {});
      await sleep(ri(30, 80));
    }
    cx = tx; cy = ty;
    await sleep(ri(180, 500));
  }

  if (elapsed() >= budgetMs - 800) return;

  // 3) Realistic scroll with ease-in/ease-out
  const scrollSections = ri(2, 4);
  for (let s = 0; s < scrollSections && elapsed() < budgetMs - 600; s++) {
    const totalDy = ri(200, 600) * (Math.random() < 0.85 ? 1 : -1);
    const chunks = ri(6, 14);
    for (let c = 0; c < chunks; c++) {
      // ease curve: sin(π * c/chunks) * factor
      const factor = Math.sin(Math.PI * c / chunks);
      const dy = Math.round((totalDy / chunks) * (0.5 + factor * 0.8));
      await page.evaluate((d: number) => window.scrollBy({ top: d, behavior: "instant" }), dy).catch(() => {});
      await sleep(ri(35, 90));
    }
    await sleep(ri(400, 1100));
    // occasional mouse move after scroll (simulates reading)
    if (Math.random() < 0.6 && elapsed() < budgetMs - 800) {
      await page.mouse.move(ri(200, 1600), ri(150, 850), { steps: ri(6, 12) }).catch(() => {});
      await sleep(ri(200, 600));
    }
  }

  if (elapsed() >= budgetMs - 400) return;

  // 4) Tab-key focus navigation (signals keyboard presence to Datadome)
  const tabPresses = ri(1, 3);
  for (let t = 0; t < tabPresses && elapsed() < budgetMs - 300; t++) {
    await page.keyboard.press("Tab").catch(() => {});
    await sleep(ri(120, 350));
  }

  // 5) Final idle dwell (time-on-page signal)
  const remainBudget = budgetMs - elapsed();
  if (remainBudget > 200) await sleep(Math.min(remainBudget, 800));
}

export async function renderWithBrowser(
  url: string,
  timeoutMs = 30000,
  attempt = 0
): Promise<{ html: string; finalUrl: string; status: number }> {
  const targetHost = (() => { try { return new URL(url).hostname; } catch { return ""; } })();
  const useFresh = attempt > 0 || shouldUseFreshContext(targetHost);
  const ctx = useFresh ? await newFreshContext() : await getStickyContext(targetHost);
  const page = await ctx.newPage();
  let lastResponseStatus = 200;
  try {
    // Capture status of every main-frame document response (CF challenge first
    // returns 403, the JS challenge then navigates to the real page with 200 —
    // we want the latter, not the former).
    page.on("response", (r) => {
      try {
        if (r.frame() !== page.mainFrame()) return;
        const rt = (r.request().resourceType?.() || "").toString();
        if (rt && rt !== "document") return;
        lastResponseStatus = r.status();
      } catch {
        /* ignore */
      }
    });

    const _navStart = Date.now();
    try {
      await page.goto(url, { waitUntil: "load", timeout: timeoutMs });
    } catch (err) {
      // Some SPAs (Outlook, Teams) trigger an immediate location.replace
      // inside load handlers. The aborted goto still leaves us on a real
      // page that we can extract — only re-throw if we have no document.
      const hasDoc = await page
        .evaluate(() => !!document && !!document.documentElement)
        .catch(() => false);
      if (!hasDoc) throw err;
    }

    // Let any in-flight client navigation / hydration settle.
    // Outlook/Office immediately location.replace inside the load handler;
    // wait for DOMContentLoaded first (always reachable) then attempt
    // networkidle as a soft wait.
    await page
      .waitForLoadState("domcontentloaded", { timeout: 8000 })
      .catch(() => {});
    await page
      .waitForLoadState("networkidle", { timeout: 6000 })
      .catch(() => {});
    // If we landed on a CF interstitial, give its JS challenge time to auto-resolve
    // and navigate to the real page.
    try {
      const probe = await page.content().catch(() => "");
      if (/<title>Just a moment|cf-browser-verification|id="challenge-form"|cdn-cgi\/challenge-platform/i.test(probe)) {
        await page
          .waitForFunction(
            () => !/Just a moment|challenge-form|cf-browser-verification/i.test(document.documentElement.outerHTML),
            { timeout: 12000 }
          )
          .catch(() => {});
        await page.waitForLoadState("networkidle", { timeout: 4000 }).catch(() => {});
      }
    } catch {
      /* ignore */
    }

    // Behavioral warm-up for Datadome / ML-based detectors.
    // Runs after page load to seed genuine mouse/scroll/keyboard signals.
    if (_needsBehaviorSim(targetHost)) {
      const _behaviorBudget = Math.max(0, timeoutMs - (Date.now() - _navStart) - 3000);
      await _behaviorSim(page, Math.min(_behaviorBudget, 7000)).catch(() => {});
    }

    const html = await getPageContent(page);
    const finalUrl = page.url();
    if (attempt < 2 && looksLikeCaptcha(html, finalUrl)) {
      const _tag = _classifyCaptchaHit(html, finalUrl);
      const _df = await _dumpCaptcha(html, finalUrl, lastResponseStatus, attempt + 1, _tag);
      console.log(
        `[renderer][D1] captcha on ${finalUrl} status=${lastResponseStatus} attempt=${attempt + 1} tag=${_tag} dump=${_df} — retrying`
      );
      if (!useFresh) dropStickyContext(targetHost);
      await page.close().catch(() => {});
      if (useFresh) await ctx.close().catch(() => {});
      // Brief delay so the SOCKS5 wrapper picks up a new upstream connection
      await new Promise((r) => setTimeout(r, 300));
      return renderWithBrowser(url, timeoutMs, attempt + 1);
    }
    if (attempt >= 2 && looksLikeCaptcha(html, finalUrl)) {
      const _tag = _classifyCaptchaHit(html, finalUrl);
      const _df = await _dumpCaptcha(html, finalUrl, lastResponseStatus, attempt + 1, _tag);
      console.log(`[renderer][D1] captcha PERSISTED after ${attempt + 1} on ${finalUrl} status=${lastResponseStatus} tag=${_tag} dump=${_df}`);
    }
    return { html, finalUrl, status: lastResponseStatus };
  } finally {
    await page.close().catch(() => {});
    if (useFresh) await ctx.close().catch(() => {});
  }
}

// Hosts that require JS execution / anti-bot evasion
const JS_REQUIRED = [
  /(^|\.)google\.[a-z.]+$/i,
  /(^|\.)bing\.com$/i,
  /(^|\.)search\.brave\.com$/i,
  /(^|\.)yandex\.[a-z.]+$/i,
  /(^|\.)baidu\.com$/i,
];

export function needsJsRendering(hostname: string): boolean {
  return JS_REQUIRED.some((re) => re.test(hostname));
}

export async function shutdownBrowser(): Promise<void> {
  for (const p of stickyContexts.values()) {
    try { (await p).close().catch(() => {}); } catch {}
  }
  stickyContexts.clear();
  stickyExpiry.clear();
  if (browserPromise) {
    const b = await browserPromise;
    await b.close().catch(() => {});
    browserPromise = null;
  }
}


export { siteKey };

export async function getStickyCookieHeader(url: string): Promise<string> {
  try {
    const u = new URL(url);
    const key = siteKey(u.hostname);
    const cached = stickyContexts.get(key);
    if (!cached) return "";
    const ctx = await cached;
    const cookies = await ctx.cookies(url);
    return cookies.map((c) => `${c.name}=${c.value}`).join("; ");
  } catch {
    return "";
  }
}

// v7.49 — 返回 sticky context 中所有域的 cookies (无 url 域过滤)
// 用于把 warmupGoogleSession() harvest 的 .google.com NID/AEC/SOCS 等跨域信任 cookie 导出给外部 CDP attacher
export async function getStickyAllCookies(url: string): Promise<Array<{name:string;value:string;domain:string;path:string;expires?:number;httpOnly?:boolean;secure?:boolean;sameSite?:"Lax"|"Strict"|"None"}>> {
  try {
    const u = new URL(url);
    const key = siteKey(u.hostname);
    const cached = stickyContexts.get(key);
    if (!cached) return [];
    const ctx = await cached;
    return await ctx.cookies(); // no url filter -> all cookies in context
  } catch {
    return [];
  }
}

export async function getStickyCookies(url: string): Promise<Array<{name:string;value:string;domain:string;path:string;expires?:number;httpOnly?:boolean;secure?:boolean;sameSite?:"Lax"|"Strict"|"None"}>> {
  try {
    const u = new URL(url);
    const key = siteKey(u.hostname);
    const cached = stickyContexts.get(key);
    if (!cached) return [];
    const ctx = await cached;
    return await ctx.cookies(url);
  } catch {
    return [];
  }
}

async function ensureStickyContext(hostname: string): Promise<BrowserContext> {
  return getStickyContext(hostname);
}

export async function storeStickyCookies(url: string, setCookieHeaders: string[]): Promise<void> {
  if (!setCookieHeaders.length) return;
  try {
    const u = new URL(url);
    if (shouldUseFreshContext(u.hostname)) return; // don't pollute search-engine sites
    const ctx = await ensureStickyContext(u.hostname);
    const cookies: Array<{
      name: string; value: string; domain: string; path: string;
      expires?: number; httpOnly?: boolean; secure?: boolean;
      sameSite?: "Lax" | "Strict" | "None";
    }> = [];
    for (const sc of setCookieHeaders) {
      const parts = sc.split(";").map((p) => p.trim());
      if (!parts.length) continue;
      const nv = parts.shift()!;
      const eq = nv.indexOf("=");
      if (eq < 0) continue;
      const name = nv.slice(0, eq).trim();
      const value = nv.slice(eq + 1).trim();
      let domain = u.hostname;
      let path = "/";
      let expires: number | undefined;
      let httpOnly = false;
      let secure = true; // upstream is https — keep secure for CF cookies to work
      let sameSite: "Lax" | "Strict" | "None" = "Lax";
      for (const a of parts) {
        const ai = a.indexOf("=");
        const ak = (ai < 0 ? a : a.slice(0, ai)).toLowerCase();
        const av = ai < 0 ? "" : a.slice(ai + 1);
        if (ak === "domain") domain = av.replace(/^\./, "");
        else if (ak === "path") path = av || "/";
        else if (ak === "expires") {
          const t = Date.parse(av);
          if (!isNaN(t)) expires = Math.floor(t / 1000);
        } else if (ak === "max-age") {
          const n = parseInt(av, 10);
          if (!isNaN(n)) expires = Math.floor(Date.now() / 1000) + n;
        } else if (ak === "httponly") httpOnly = true;
        else if (ak === "secure") secure = true;
        else if (ak === "samesite") {
          const v = av.toLowerCase();
          sameSite = v === "strict" ? "Strict" : v === "none" ? "None" : "Lax";
        }
      }
      cookies.push({ name, value, domain, path, expires, httpOnly, secure, sameSite });
    }
    if (cookies.length) await ctx.addCookies(cookies);
  } catch (e) {
    const m=(e as Error).message;
    if (!/has been closed|Target (page|context)/.test(m)) console.error("[renderer] storeStickyCookies:", m);
  }
}

export function looksLikeCfChallengeHtml(html: string, finalUrl: string): boolean {
  return looksLikeCaptcha(html, finalUrl);
}
// Appended to renderer.ts: pre-warm Google session in the sticky context so
// reCAPTCHA Enterprise sees NID/AEC/SOCS cookies + prior Google iframe load
// when scoring the next token. Free, ~5s, lifts score from ~0.1 to ~0.5+.
// === Google reCAPTCHA score booster ===
// WARP (the broker's default proxy) blocks google.com apex but allows
// /recaptcha/*. So we cannot visit google.com through the sticky context.
// Strategy: harvest .google.com / .youtube.com cookies via a TEMPORARY
// context routed through a non-WARP SOCKS5 (cf-pool 1093 → GCP exit),
// cache them on disk for 24h, then inject into the sticky context. The
// target site's reCAPTCHA iframe (which loads via WARP) will send those
// cookies → Google sees a known session → score lifts from ~0.1 to ~0.5+.
import * as fs from "node:fs";
import * as path from "node:path";

const GOOGLE_COOKIE_CACHE = process.env.GOOGLE_COOKIE_CACHE || "/root/.google-cookies.json";
const GOOGLE_COOKIE_TTL_MS = 24 * 3600 * 1000;
// Free non-WARP SOCKS5 (cf-pool xray) — google reachable.
const GOOGLE_HARVEST_PROXY = process.env.GOOGLE_HARVEST_PROXY || "socks5://127.0.0.1:10831";

type CK = {
  name: string; value: string; domain: string; path: string;
  expires: number; httpOnly: boolean; secure: boolean; sameSite: "Strict"|"Lax"|"None";
};

export function readCachedGoogleCookies(): CK[] | null {
  try {
    if (!fs.existsSync(GOOGLE_COOKIE_CACHE)) return null;
    const raw = JSON.parse(fs.readFileSync(GOOGLE_COOKIE_CACHE, "utf8"));
    if (!raw || typeof raw.savedAt !== "number" || !Array.isArray(raw.cookies)) return null;
    if (Date.now() - raw.savedAt > GOOGLE_COOKIE_TTL_MS) return null;
    if (!raw.cookies.some((c: CK) => /^NID$/.test(c.name))) return null;
    return raw.cookies;
  } catch { return null; }
}
export function writeCachedGoogleCookies(cookies: CK[]): void {
  try {
    fs.mkdirSync(path.dirname(GOOGLE_COOKIE_CACHE), { recursive: true });
    fs.writeFileSync(GOOGLE_COOKIE_CACHE, JSON.stringify({ savedAt: Date.now(), cookies }));
  } catch (e) {
    console.error("[google-warmup] cache write failed:", (e as Error).message);
  }
}

async function harvestGoogleCookiesFresh(): Promise<CK[]> {
  const browser = await getBrowser();
  const ctx = await browser.newContext({
    userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    viewport: { width: 1920, height: 1040 },
    locale: "en-US",
    timezoneId: BROWSER_TIMEZONE,
    proxy: { server: GOOGLE_HARVEST_PROXY },
  });
  try {
    await ctx.addInitScript(STEALTH_INIT_FULL);
    try { await attachGoogleProxyRouting(ctx); } catch {}
    const page = await ctx.newPage();
    const visit = async (u: string, dwell: number) => {
      try {
        await page.goto(u, { waitUntil: "domcontentloaded", timeout: 20000 });
        for (let i = 0; i < 5; i++) {
          await page.mouse.move(
            120 + Math.floor(Math.random() * 1600),
            120 + Math.floor(Math.random() * 800),
            { steps: 8 + Math.floor(Math.random() * 12) }
          ).catch(() => {});
          await page.waitForTimeout(150 + Math.floor(Math.random() * 250));
        }
        await page.evaluate((d) => window.scrollBy(0, d), 200 + Math.floor(Math.random() * 600)).catch(() => {});
        await page.waitForTimeout(dwell);
      } catch (e) {
        const _wm = (e as Error).message;
        if (!/SOCKS|ERR_PROXY|chrome-error|timeout|interrupted by another navigation/i.test(_wm))
          console.error(`[google-warmup] visit ${u} failed:`, _wm);
      }
    };
    await visit("https://www.google.com/", 1500);
    await visit("https://www.google.com/search?q=replit+features&hl=en", 1800);
    await visit("https://www.google.com/recaptcha/api2/demo", 1500);
    await visit("https://consent.youtube.com/m?continue=https%3A%2F%2Fwww.youtube.com%2F&hl=en", 800);
    await visit("https://www.youtube.com/", 1200);
    await page.close().catch(() => {});
    const all = await ctx.cookies();
    return all.filter((c) =>
      /(^|\.)google\.com$/i.test(c.domain) ||
      /(^|\.)youtube\.com$/i.test(c.domain) ||
      /(^|\.)gstatic\.com$/i.test(c.domain)
    ) as CK[];
  } finally {
    await ctx.close().catch(() => {});
  }
}

export async function warmupGoogleSession(hostnameForKey: string): Promise<{
  visited: string[]; durationMs: number; cookieCount: number; source: "cache" | "fresh" | "none";
}> {
  const t0 = Date.now();
  let cookies = readCachedGoogleCookies();
  let source: "cache" | "fresh" | "none" = cookies ? "cache" : "none";
  if (!cookies) {
    try {
      cookies = await harvestGoogleCookiesFresh();
      if (cookies.length > 0) {
        writeCachedGoogleCookies(cookies);
        source = "fresh";
      }
    } catch (e) {
      console.error("[google-warmup] harvest failed:", (e as Error).message);
      cookies = [];
    }
  }
  if (!cookies || cookies.length === 0) {
    return { visited: [], durationMs: Date.now() - t0, cookieCount: 0, source: "none" };
  }
  // Inject into sticky context for the target host.
  try {
    const ctx = await getStickyContext(hostnameForKey);
    await ctx.addCookies(cookies);
    // Activate by hitting the reCAPTCHA anchor endpoint inside the sticky
    // context — this is on /recaptcha/* which IS reachable via WARP, and
    // forces Google to issue any per-session refresh cookies tied to the
    // sticky context's WARP exit IP.
    const page = await ctx.newPage();
    try {
      await page.goto("https://www.google.com/recaptcha/api2/anchor?ar=1&k=6Le-wvkSAAAAAPBMRTvw0Q4Muexq9bi0DJwx_mJ-&co=aHR0cHM6Ly93d3cuZ29vZ2xlLmNvbTo0NDM.&hl=en&v=v1700000000000", {
        waitUntil: "domcontentloaded", timeout: 12000,
      });
      await page.waitForTimeout(800);
    } catch { /* best effort */ }
    await page.close().catch(() => {});
  } catch (e) {
    console.error("[google-warmup] inject failed:", (e as Error).message);
  }
  return {
    visited: cookies.map((c) => `${c.domain}:${c.name}`).slice(0, 12),
    durationMs: Date.now() - t0,
    cookieCount: cookies.length,
    source,
  };
}


// ── v8.23 — Google trust-cookie REAL-human bootstrap ──────────────────────────
// Runs ONCE per chromium lifetime. Vs v8.10 (固定坐标 / 固定滚动 / search "hello world"
// / 从不点击 replit), v8.23 模拟真实用户:
//   1. 从词表随机抽 1 个真实意图搜索词 (不是 hello+world)
//   2. youtube + google.com 用随机化坐标和滚动距离
//   3. google search 后真正点击 replit.com 结果链接 (注入 domain familiarity 信号)
//   4. 在 Replit 首页停 8-15s 自然滚动 (告诉 reCAPTCHA Enterprise: 此用户最近确实访问过目标域)
// 这是 datacenter ASN VPS 出口下唯一能拉高 reCAPTCHA Enterprise score 的合法手段.
// v8.66 — refresh trust cookies every 25min (cookies stale by hour 2-3 → score collapse)
let _googleTrustAt = 0;
const _GOOGLE_TRUST_TTL_MS = 25 * 60 * 1000;
let _googleTrustInFlight = false;

function _ri(min: number, max: number): number {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function _pick<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

const _SEARCH_QUERIES = [
  "replit online ide",
  "replit features",
  "replit vs codespaces",
  "replit pricing",
  "best browser based code editor",
  "online python interpreter free",
  "collaborative coding playground",
  "free web hosting tutorial",
];

async function _humanScroll(page: import("playwright-core").Page): Promise<void> {
  const steps = _ri(2, 4);
  for (let i = 0; i < steps; i++) {
    const dy = _ri(180, 520) * (Math.random() < 0.85 ? 1 : -1);
    await page.evaluate(`window.scrollBy(0, ${dy})`);
    await page.waitForTimeout(_ri(900, 2200));
  }
}

async function _humanMouse(page: import("playwright-core").Page): Promise<void> {
  const moves = _ri(2, 4);
  for (let i = 0; i < moves; i++) {
    await page.mouse.move(_ri(200, 1700), _ri(150, 900), { steps: _ri(8, 18) });
    await page.waitForTimeout(_ri(400, 1100));
  }
}

async function _bootstrapGoogleTrust(browser: Browser): Promise<void> {
  if (_googleTrustInFlight) return;
  if (Date.now() - _googleTrustAt < _GOOGLE_TRUST_TTL_MS) return; // v8.66 TTL
  _googleTrustInFlight = true;
  const t0 = Date.now();
  let ctx: BrowserContext | null = null;
  try {
    console.log("[v8.23] google-trust REAL-human bootstrap START (~50s, will click replit.com)");
    ctx = await browser.newContext({
      userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
      viewport: { width: 1920, height: 1040 },
      screen: { width: 1920, height: 1080 },
      locale: "en-US",
      timezoneId: BROWSER_TIMEZONE,
      ignoreHTTPSErrors: true,
      extraHTTPHeaders: {
        "Accept-Language": "en-US,en;q=0.9",
        "sec-ch-ua": '"Chromium";v="144", "Not:A-Brand";v="99", "Google Chrome";v="144"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Linux"',
      },
    });
    try { await ctx.addInitScript(STEALTH_INIT_FULL); } catch (_) { /* */ }
    const page = await ctx.newPage();

    // Phase 1: youtube.com (~10-13s) — 随机化滚动 + 鼠标
    try {
      await page.goto("https://www.youtube.com/", { waitUntil: "domcontentloaded", timeout: 18000 });
      await page.waitForTimeout(_ri(1500, 2800));
      await _humanMouse(page);
      await _humanScroll(page);
      console.log(`[v8.23] phase-1 youtube.com OK (${Date.now() - t0}ms)`);
    } catch (e) {
      console.warn("[v8.23] phase-1 youtube.com failed:", (e as Error).message);
    }

    // Phase 2: google.com (~6-9s) — 随机化, 准备搜索
    try {
      await page.goto("https://www.google.com/", { waitUntil: "domcontentloaded", timeout: 15000 });
      await page.waitForTimeout(_ri(1200, 2400));
      await _humanMouse(page);
      await page.waitForTimeout(_ri(800, 1500));
      console.log(`[v8.23] phase-2 google.com OK (${Date.now() - t0}ms)`);
    } catch (e) {
      console.warn("[v8.23] phase-2 google.com failed:", (e as Error).message);
    }

    // Phase 3 v8.66: 真键盘 type 进 google.com 搜索框 (不是 URL?q=, 触发真 input fingerprint)
    const query = _pick(_SEARCH_QUERIES);
    try {
      // 已经在 phase-2 google.com 上 → 找 [name=q] 输入
      const qInput = page.locator("textarea[name=q], input[name=q]").first();
      await qInput.click({ timeout: 5000 });
      await page.waitForTimeout(_ri(300, 700));
      // 逐字符变频率 type (60-180ms/char) — 真人键入节奏
      for (const ch of query) {
        await page.keyboard.type(ch, { delay: _ri(60, 180) });
      }
      await page.waitForTimeout(_ri(500, 1200));
      await page.keyboard.press("Enter");
      await page.waitForLoadState("domcontentloaded", { timeout: 15000 });
      await page.waitForTimeout(_ri(1500, 2800));
      await _humanScroll(page);
      console.log(`[v8.66] phase-3 REAL-keystroke search "${query}" OK (${Date.now() - t0}ms)`);

      // 找第一条 replit.com 结果 → click → 跳到 Replit 官网
      let clicked = false;
      try {
        const replitLinks = await page.locator("a[href*='replit.com']:visible").all();
        for (const link of replitLinks) {
          try {
            const href = await link.getAttribute("href");
            if (!href || !/replit\.com/.test(href)) continue;
            // 只点击实际指向 replit.com (跳过 google 内部 /url? 重定向之外的)
            await link.scrollIntoViewIfNeeded({ timeout: 3000 });
            await page.waitForTimeout(_ri(600, 1200));
            await link.click({ timeout: 5000 });
            clicked = true;
            console.log(`[v8.23] phase-4 clicked replit.com link href=${href.slice(0, 80)} (${Date.now() - t0}ms)`);
            break;
          } catch (_) { /* try next */ }
        }
      } catch (e) {
        console.warn("[v8.23] phase-4 replit link locate failed:", (e as Error).message);
      }

      // Phase 5: 在 Replit 首页 (或重定向后任意 replit 页) 停 8-12s 自然滚动
      if (clicked) {
        try {
          await page.waitForLoadState("domcontentloaded", { timeout: 12000 });
          await page.waitForTimeout(_ri(2000, 3500));
          await _humanScroll(page);
          await _humanMouse(page);
          await page.waitForTimeout(_ri(1500, 2500));
          await _humanScroll(page);
          const finalUrl = page.url();
          console.log(`[v8.23] phase-5 Replit landing dwell complete url=${finalUrl.slice(0, 80)} (${Date.now() - t0}ms)`);
        } catch (e) {
          console.warn("[v8.23] phase-5 Replit dwell failed:", (e as Error).message);
        }
      } else {
        console.warn("[v8.23] phase-4 no replit.com link clicked — domain-familiarity signal MISSING");
      }
    } catch (e) {
      console.warn("[v8.23] phase-3 google search failed:", (e as Error).message);
    }

    // Phase 6 v8.66: maps.google.com — 跨服务 NID 活跃信号 (compose google-family trust)
    try {
      await page.goto("https://www.google.com/maps", { waitUntil: "domcontentloaded", timeout: 15000 });
      await page.waitForTimeout(_ri(1800, 3200));
      await _humanMouse(page);
      await _humanScroll(page);
      console.log(`[v8.66] phase-6 maps.google.com OK (${Date.now() - t0}ms)`);
    } catch (e) {
      console.warn("[v8.66] phase-6 maps failed:", (e as Error).message);
    }

    // Phase 7 v8.66: news.google.com — 第三个 google 服务, 强化 cross-service trust
    try {
      await page.goto("https://news.google.com/", { waitUntil: "domcontentloaded", timeout: 15000 });
      await page.waitForTimeout(_ri(1500, 2800));
      await _humanScroll(page);
      await _humanMouse(page);
      await page.waitForTimeout(_ri(1200, 2200));
      console.log(`[v8.66] phase-7 news.google.com OK (${Date.now() - t0}ms)`);
    } catch (e) {
      console.warn("[v8.66] phase-7 news failed:", (e as Error).message);
    }

    // Harvest google-family cookies into shared cache (v8.66: 5 services, TTL refresh)
    try {
      const allCks = await ctx.cookies();
      const googleCks = allCks.filter((c) =>
        /(^|\.)(google\.com|gstatic\.com|youtube\.com|recaptcha\.net|googleapis\.com|googleusercontent\.com|googletagmanager\.com)$/i.test(c.domain)
      );
      console.log(`[v8.23] google-trust cookies harvested: ${googleCks.length} (${Date.now() - t0}ms)`);
      try {
        writeCachedGoogleCookies(googleCks as Parameters<typeof writeCachedGoogleCookies>[0]);
        console.log("[v8.23] google-trust cookies written to shared cache");
      } catch (e) {
        console.warn("[v8.23] writeCachedGoogleCookies failed:", (e as Error).message);
      }
    } catch (e) {
      console.warn("[v8.23] cookie harvest failed:", (e as Error).message);
    }
    _googleTrustAt = Date.now(); // v8.66
    console.log(`[v8.66] google-trust REAL-human bootstrap COMPLETE (${Date.now() - t0}ms)`);
  } catch (e) {
    console.error("[v8.23] google-trust bootstrap fatal:", (e as Error).message);
  } finally {
    _googleTrustInFlight = false;
    if (ctx) try { await ctx.close(); } catch (_) { /* */ }
  }
}
