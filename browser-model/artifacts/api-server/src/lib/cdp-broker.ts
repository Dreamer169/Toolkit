/**
 * CDP screencast + remote input broker.
 *
 * 替代 URL-rewrite 代理（routes/proxy.ts）。
 * 思路：服务端跑真浏览器（Playwright + 系统 Chromium），通过 CDP
 *   - Page.startScreencast 把 viewport 以 JPEG 帧推给前端
 *   - Input.dispatchMouseEvent / dispatchKeyEvent / dispatchMouseWheelEvent
 *     接收前端坐标/键盘事件并转发给真浏览器
 * 前端只负责把 JPEG 画到 <canvas>，捕获鼠标键盘事件回传。
 *
 * 这样彻底绕过：
 *   - URL 重写代理对 Next.js / TurboPack 懒加载 chunk 的破坏
 *     （__turbopack_load_page_chunks__ is not defined）
 *   - 第三方脚本（googleads/recaptcha）走代理后 MIME 错乱被 Chrome 拒绝
 *   - X-Frame-Options / CSP frame-ancestors 阻止 iframe 嵌入
 */

import { chromium, type Browser, type BrowserContext, type Page } from "playwright";
import type { WebSocket } from "ws";
import { logger } from "./logger.js";

type CDPSession = Awaited<ReturnType<BrowserContext["newCDPSession"]>>;

/**
 * 反指纹 init script —— 在每个页面 JS 之前注入。
 * 移植自旧 renderer.ts，覆盖 reCAPTCHA / hCaptcha / Cloudflare BM / Datadome /
 * Akamai BM / PerimeterX 这些反爬常查的所有点：navigator.* / WebGL / chrome.*
 * / WebRTC IP 泄漏 / mediaDevices / Intl 时区一致性 / Function.toString 泄漏。
 */
const STEALTH_INIT = `
(() => {
  // navigator.webdriver
  try { Object.defineProperty(Navigator.prototype, 'webdriver', { get: () => false, configurable: true }); } catch (_) {}
  // 删 CDP 注入的全局变量（Selenium/Playwright 检测套路）
  try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array; } catch(_) {}
  try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise; } catch(_) {}
  try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol; } catch(_) {}

  try { Object.defineProperty(Navigator.prototype, 'languages', { get: () => ['en-US', 'en'], configurable: true }); } catch (_) {}
  try { Object.defineProperty(Navigator.prototype, 'language',  { get: () => 'en-US', configurable: true }); } catch (_) {}
  try { Object.defineProperty(Navigator.prototype, 'platform', { get: () => 'Linux x86_64', configurable: true }); } catch (_) {}
  try { Object.defineProperty(Navigator.prototype, 'hardwareConcurrency', { get: () => 8, configurable: true }); } catch (_) {}
  try { Object.defineProperty(Navigator.prototype, 'deviceMemory', { get: () => 8, configurable: true }); } catch (_) {}
  try { Object.defineProperty(Navigator.prototype, 'maxTouchPoints', { get: () => 0, configurable: true }); } catch (_) {}

  // PluginArray —— 真 Chrome 至少 5 个 PDF Viewer plugin，没有就是 headless 信号
  try {
    const makePlugin = (name, filename, desc) => {
      const p = Object.create(Plugin.prototype);
      Object.defineProperties(p, {
        name: { value: name }, filename: { value: filename },
        description: { value: desc }, length: { value: 1 },
      });
      return p;
    };
    const plugins = [
      makePlugin('PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      makePlugin('Chrome PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      makePlugin('Chromium PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      makePlugin('Microsoft Edge PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      makePlugin('WebKit built-in PDF', 'internal-pdf-viewer', 'Portable Document Format'),
    ];
    const arr = Object.create(PluginArray.prototype);
    plugins.forEach((p, i) => { arr[i] = p; arr[p.name] = p; });
    Object.defineProperty(arr, 'length', { value: plugins.length });
    Object.defineProperty(Navigator.prototype, 'plugins', { get: () => arr, configurable: true });
  } catch (_) {}

  // chrome.* —— 没有 chrome.runtime/app/csi/loadTimes 是经典 headless 指纹
  try {
    if (!window.chrome) window.chrome = {};
    if (!window.chrome.runtime) {
      window.chrome.runtime = {
        OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' },
        OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
        PlatformArch: { ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
        PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
        PlatformNaclArch: { ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
        RequestUpdateCheckStatus: { NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' },
        // 注意：未装扩展时 chrome.runtime.id 是 undefined（不是 null），sendMessage/connect 调用会抛
        id: undefined,
        sendMessage: function(){ throw new Error('Cannot read properties of undefined'); },
        connect: function(){ throw new Error('Cannot read properties of undefined'); },
      };
    }
    window.chrome.app = window.chrome.app || { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }, RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } };
    window.chrome.csi = window.chrome.csi || function(){return{};};
    window.chrome.loadTimes = window.chrome.loadTimes || function(){return{requestTime: Date.now()/1000, startLoadTime: Date.now()/1000, commitLoadTime: Date.now()/1000, finishDocumentLoadTime: 0, finishLoadTime: 0, firstPaintTime: 0, firstPaintAfterLoadTime: 0, navigationType: 'Other', wasFetchedViaSpdy: false, wasNpnNegotiated: true, npnNegotiatedProtocol: 'h2', wasAlternateProtocolAvailable: false, connectionInfo: 'h2'};};
  } catch (_) {}

  // === navigator.userAgentData (UA-CH High Entropy) ===
  // UA 串说 Chrome 145，但 Sec-CH-UA / userAgentData 还报老版本就一眼穿帮。
  // 真 Chrome 145 + Linux 应回这套品牌串和高熵字段。
  try {
    const brands = [
      { brand: 'Chromium',           version: '145' },
      { brand: 'Not:A-Brand',        version: '99'  },
      { brand: 'Google Chrome',      version: '145' },
    ];
    const fullVerList = [
      { brand: 'Chromium',           version: '145.0.7375.0' },
      { brand: 'Not:A-Brand',        version: '99.0.0.0' },
      { brand: 'Google Chrome',      version: '145.0.7375.0' },
    ];
    const high = {
      architecture: 'x86', bitness: '64', model: '', mobile: false,
      platform: 'Linux', platformVersion: '6.5.0', uaFullVersion: '145.0.7375.0',
      wow64: false, formFactors: ['Desktop'], fullVersionList: fullVerList,
      brands: brands,
    };
    const uaData = {
      brands: brands, mobile: false, platform: 'Linux',
      getHighEntropyValues: function(hints) {
        const out = { brands: brands, mobile: false, platform: 'Linux' };
        (hints || []).forEach((h) => { if (h in high) out[h] = high[h]; });
        return Promise.resolve(out);
      },
      toJSON: function(){ return { brands: brands, mobile: false, platform: 'Linux' }; },
    };
    Object.defineProperty(Navigator.prototype, 'userAgentData', { get: () => uaData, configurable: true });
    try { wrap(uaData.getHighEntropyValues); wrap(uaData.toJSON); } catch (_) {}
  } catch (_) {}

  // === navigator.gpu (WebGPU) ===
  // Chrome 113+ 有 navigator.gpu。Linux + SwiftShader 实际能 requestAdapter 但 adapter.info
  // 直接报 'Google SwiftShader' → 一望识破是无头/容器。让它返回 null（Linux 桌面 Chrome
  // 在很多发行版上 WebGPU 也是默认禁的，null 不可疑）。
  try {
    Object.defineProperty(Navigator.prototype, 'gpu', { get: () => null, configurable: true });
  } catch (_) {}

  // permissions: notifications quirk
  try {
    const origQuery = window.navigator.permissions && window.navigator.permissions.query;
    if (origQuery) {
      window.navigator.permissions.query = (params) =>
        params && params.name === 'notifications'
          ? Promise.resolve({ state: Notification.permission, name: 'notifications', onchange: null })
          : origQuery.call(window.navigator.permissions, params);
    }
  } catch (_) {}

  // WebGL vendor/renderer —— 默认 SwiftShader 太典型，伪装成 Mesa Intel Iris
  try {
    const getParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function (p) {
      if (p === 37445) return 'Google Inc. (Intel)';
      if (p === 37446) return 'ANGLE (Intel, Mesa Intel(R) UHD Graphics 630 (CFL GT2), OpenGL 4.6)';
      return getParam.apply(this, arguments);
    };
    if (typeof WebGL2RenderingContext !== 'undefined') {
      const getParam2 = WebGL2RenderingContext.prototype.getParameter;
      WebGL2RenderingContext.prototype.getParameter = function (p) {
        if (p === 37445) return 'Google Inc. (Intel)';
        if (p === 37446) return 'ANGLE (Intel, Mesa Intel(R) UHD Graphics 630 (CFL GT2), OpenGL 4.6)';
        return getParam2.apply(this, arguments);
      };
    }
  } catch (_) {}

  // headless 经典泄漏：window.outer{Width,Height} 为 0
  try {
    if (!window.outerWidth)  Object.defineProperty(window, 'outerWidth',  { get: () => window.innerWidth });
    if (!window.outerHeight) Object.defineProperty(window, 'outerHeight', { get: () => window.innerHeight });
  } catch (_) {}

  // screen 属性
  try {
    Object.defineProperty(screen, 'availWidth',  { get: () => 1920, configurable: true });
    Object.defineProperty(screen, 'availHeight', { get: () => 1040, configurable: true });
    Object.defineProperty(screen, 'width',  { get: () => 1920, configurable: true });
    Object.defineProperty(screen, 'height', { get: () => 1080, configurable: true });
    Object.defineProperty(screen, 'colorDepth', { get: () => 24, configurable: true });
    Object.defineProperty(screen, 'pixelDepth', { get: () => 24, configurable: true });
  } catch (_) {}

  // 电池
  try {
    if (navigator.getBattery) {
      const _gb = navigator.getBattery.bind(navigator);
      navigator.getBattery = () => _gb().then((b) => b).catch(() => ({
        charging: true, chargingTime: 0, dischargingTime: Infinity, level: 0.99,
        addEventListener(){}, removeEventListener(){}, dispatchEvent(){return true;},
      }));
    }
  } catch (_) {}

  // 网络连接（NetworkInformation）
  try {
    Object.defineProperty(Navigator.prototype, 'connection', {
      get: () => ({ effectiveType: '4g', rtt: 50, downlink: 10, saveData: false, addEventListener(){}, removeEventListener(){} }),
      configurable: true,
    });
  } catch (_) {}

  try { if (window.Notification && Notification.permission === 'denied') Object.defineProperty(Notification, 'permission', { get: () => 'default' }); } catch (_) {}

  // toString 泄漏：被改写过的函数 toString 必须仍返回 '[native code]'
  const nativeToString = Function.prototype.toString;
  const fakeFns = new WeakSet();
  const wrap = (fn) => { fakeFns.add(fn); return fn; };
  Function.prototype.toString = function () {
    if (fakeFns.has(this)) return 'function ' + (this.name || '') + '() { [native code] }';
    return nativeToString.call(this);
  };
  try { wrap(WebGLRenderingContext.prototype.getParameter); } catch (_) {}
  try { wrap(window.navigator.permissions.query); } catch (_) {}
  try { wrap(Function.prototype.toString); } catch (_) {}

  // WebRTC IP 泄漏防护 —— 通过 ICE candidate 把 VPS 真实 IP / 内网 IP 上报给反爬
  try {
    const PC = window.RTCPeerConnection || window.webkitRTCPeerConnection || window.mozRTCPeerConnection;
    if (PC) {
      const origAddIceCandidate = PC.prototype.addIceCandidate;
      const origSetLocalDescription = PC.prototype.setLocalDescription;
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

  // mediaDevices —— 没设备列表本身就可疑，给典型笔记本配置
  try {
    if (navigator.mediaDevices) {
      const fakeDevices = [
        { deviceId: "default", kind: "audioinput",  label: "", groupId: "g1", toJSON(){return this;} },
        { deviceId: "8a1bcf",  kind: "audioinput",  label: "", groupId: "g1", toJSON(){return this;} },
        { deviceId: "default", kind: "audiooutput", label: "", groupId: "g1", toJSON(){return this;} },
        { deviceId: "cam1xy",  kind: "videoinput",  label: "", groupId: "g2", toJSON(){return this;} },
      ];
      const fakeEnum = function enumerateDevices() { return Promise.resolve(fakeDevices); };
      // 真实"没插设备"状态 = NotFoundError；NotAllowedError 跟 enumerateDevices 返回设备列表
      // 自相矛盾（既然有摄像头为何 user 没拒绝过就 not allowed？）
      const fakeGUM  = function getUserMedia() { return Promise.reject(new DOMException("Requested device not found", "NotFoundError")); };
      const fakeGDM  = function getDisplayMedia() { return Promise.reject(new DOMException("Permission denied by system", "NotAllowedError")); };
      try { wrap(fakeEnum); wrap(fakeGUM); wrap(fakeGDM); } catch (_) {}
      try {
        const proto = Object.getPrototypeOf(navigator.mediaDevices);
        Object.defineProperty(proto, "enumerateDevices", { value: fakeEnum, configurable: true, writable: true });
        Object.defineProperty(proto, "getUserMedia",     { value: fakeGUM,  configurable: true, writable: true });
        Object.defineProperty(proto, "getDisplayMedia",  { value: fakeGDM,  configurable: true, writable: true });
      } catch (_) {}
      try {
        Object.defineProperty(navigator.mediaDevices, "enumerateDevices", { value: fakeEnum, configurable: true, writable: true });
        Object.defineProperty(navigator.mediaDevices, "getUserMedia",     { value: fakeGUM,  configurable: true, writable: true });
        Object.defineProperty(navigator.mediaDevices, "getDisplayMedia",  { value: fakeGDM,  configurable: true, writable: true });
      } catch (_) {}
    }
  } catch (_) {}

  // Intl/timezone 一致性 —— 上下文 pin 了 LA 时区，加固 SPA bundles 在 init 之前的 Date 调用
  try {
    const origRO = Intl.DateTimeFormat.prototype.resolvedOptions;
    Intl.DateTimeFormat.prototype.resolvedOptions = function resolvedOptions() {
      const r = origRO.apply(this, arguments);
      if (!r.timeZone || r.timeZone === "UTC") r.timeZone = "America/Los_Angeles";
      if (!r.locale || /^(zh|en-GB|de|fr|ja|ru|ko)/.test(r.locale)) r.locale = "en-US";
      return r;
    };
    try { wrap(Intl.DateTimeFormat.prototype.resolvedOptions); } catch (_) {}
  } catch (_) {}
  try {
    const origGTO = Date.prototype.getTimezoneOffset;
    Date.prototype.getTimezoneOffset = function getTimezoneOffset() {
      const v = origGTO.call(this);
      if (v === 0) {
        const month = this.getUTCMonth();
        return (month >= 2 && month <= 10) ? 420 : 480;
      }
      return v;
    };
    try { wrap(Date.prototype.getTimezoneOffset); } catch (_) {}
  } catch (_) {}

  // === Canvas 指纹防护 ===
  // FingerprintJS / CreepJS 通过 toDataURL/getImageData 拿到画布像素哈希。
  // 给每帧像素加 ±1 微噪音（per-session 固定种子），既能破坏哈希一致性，
  // 又不影响视觉。注意必须 in-place 改 ImageData，因为 toDataURL 内部直读。
  try {
    const seed = (Math.random() * 0xffffffff) >>> 0;
    let s = seed || 1;
    const rng = () => { s = (s * 1664525 + 1013904223) >>> 0; return s; };
    const noisify = (canvas) => {
      try {
        const ctx = canvas.getContext && canvas.getContext('2d');
        if (!ctx) return;
        const w = canvas.width, h = canvas.height;
        if (!w || !h) return;
        const img = ctx.getImageData(0, 0, w, h);
        const d = img.data;
        // 只动 RGB，alpha 不动；每像素 1/32 概率 ±1
        for (let i = 0; i < d.length; i += 4) {
          const r = rng();
          if ((r & 31) === 0) d[i]   = (d[i]   + ((r >> 5) & 1 ? 1 : -1)) & 255;
          if ((r & 31) === 1) d[i+1] = (d[i+1] + ((r >> 5) & 1 ? 1 : -1)) & 255;
          if ((r & 31) === 2) d[i+2] = (d[i+2] + ((r >> 5) & 1 ? 1 : -1)) & 255;
        }
        ctx.putImageData(img, 0, 0);
      } catch (_) {}
    };
    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function toDataURL() {
      noisify(this);
      return origToDataURL.apply(this, arguments);
    };
    const origToBlob = HTMLCanvasElement.prototype.toBlob;
    if (origToBlob) {
      HTMLCanvasElement.prototype.toBlob = function toBlob() {
        noisify(this);
        return origToBlob.apply(this, arguments);
      };
    }
    const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function getImageData() {
      const r = origGetImageData.apply(this, arguments);
      try {
        const d = r.data;
        for (let i = 0; i < d.length; i += 4) {
          const x = rng();
          if ((x & 31) === 0) d[i]   = (d[i]   + ((x >> 5) & 1 ? 1 : -1)) & 255;
          if ((x & 31) === 1) d[i+1] = (d[i+1] + ((x >> 5) & 1 ? 1 : -1)) & 255;
          if ((x & 31) === 2) d[i+2] = (d[i+2] + ((x >> 5) & 1 ? 1 : -1)) & 255;
        }
      } catch (_) {}
      return r;
    };
    try {
      wrap(HTMLCanvasElement.prototype.toDataURL);
      if (origToBlob) wrap(HTMLCanvasElement.prototype.toBlob);
      wrap(CanvasRenderingContext2D.prototype.getImageData);
    } catch (_) {}
  } catch (_) {}

  // === AudioContext 指纹防护 ===
  // CreepJS 用 OfflineAudioContext 渲染正弦波 → getChannelData 哈希。
  // 给每个采样加 ±1e-7 噪声，破坏哈希但听不出来。
  try {
    const arrNoise = (arr) => {
      for (let i = 0; i < arr.length; i++) arr[i] = arr[i] + (Math.random() - 0.5) * 1e-7;
      return arr;
    };
    if (typeof AnalyserNode !== 'undefined') {
      const o1 = AnalyserNode.prototype.getFloatFrequencyData;
      AnalyserNode.prototype.getFloatFrequencyData = function () {
        const r = o1.apply(this, arguments);
        try { arrNoise(arguments[0]); } catch (_) {}
        return r;
      };
      try { wrap(AnalyserNode.prototype.getFloatFrequencyData); } catch (_) {}
    }
    if (typeof AudioBuffer !== 'undefined') {
      const o2 = AudioBuffer.prototype.getChannelData;
      AudioBuffer.prototype.getChannelData = function () {
        const r = o2.apply(this, arguments);
        try { arrNoise(r); } catch (_) {}
        return r;
      };
      try { wrap(AudioBuffer.prototype.getChannelData); } catch (_) {}
    }
  } catch (_) {}

  // === ClientRects 指纹防护 ===
  // 子像素布局测量在不同 GPU/字体 hinting 下不一样 → 给宽度加纳米级噪声
  try {
    const noise = () => (Math.random() - 0.5) * 1e-4;
    const wrapRect = (r) => {
      try {
        Object.defineProperty(r, 'x',     { value: r.x     + noise(), configurable: true });
        Object.defineProperty(r, 'y',     { value: r.y     + noise(), configurable: true });
        Object.defineProperty(r, 'width', { value: r.width + noise(), configurable: true });
        Object.defineProperty(r, 'height',{ value: r.height+ noise(), configurable: true });
      } catch (_) {}
      return r;
    };
    const oG = Element.prototype.getBoundingClientRect;
    Element.prototype.getBoundingClientRect = function () {
      return wrapRect(oG.apply(this, arguments));
    };
    const oR = Range.prototype.getBoundingClientRect;
    Range.prototype.getBoundingClientRect = function () {
      return wrapRect(oR.apply(this, arguments));
    };
    try { wrap(Element.prototype.getBoundingClientRect); } catch (_) {}
    try { wrap(Range.prototype.getBoundingClientRect); } catch (_) {}
  } catch (_) {}

  // === 字体指纹防护 ===
  // CanvasRenderingContext2D.measureText 能用宽度差检测某字体是否安装。
  // 加亚像素噪声让"是/否安装"的判定不再稳定。
  try {
    const oMT = CanvasRenderingContext2D.prototype.measureText;
    CanvasRenderingContext2D.prototype.measureText = function measureText() {
      const m = oMT.apply(this, arguments);
      try {
        const n = (Math.random() - 0.5) * 1e-3;
        Object.defineProperty(m, 'width', { value: m.width + n, configurable: true });
      } catch (_) {}
      return m;
    };
    try { wrap(CanvasRenderingContext2D.prototype.measureText); } catch (_) {}
  } catch (_) {}

  // === 语音合成指纹 ===
  // speechSynthesis.getVoices() 暴露 OS 安装的 TTS 语音 → Linux/Win/Mac 一望可知
  try {
    if (typeof speechSynthesis !== 'undefined') {
      const fakeVoices = [
        { voiceURI: 'Google US English', name: 'Google US English', lang: 'en-US', localService: false, default: true },
        { voiceURI: 'Google UK English Female', name: 'Google UK English Female', lang: 'en-GB', localService: false, default: false },
        { voiceURI: 'Google UK English Male', name: 'Google UK English Male', lang: 'en-GB', localService: false, default: false },
      ];
      Object.defineProperty(speechSynthesis, 'getVoices', { value: () => fakeVoices, configurable: true });
      try { wrap(speechSynthesis.getVoices); } catch (_) {}
    }
  } catch (_) {}

  // === navigator.pdfViewerEnabled ===
  // 现代 Chrome 默认 true；headless/某些容器里是 false
  try { Object.defineProperty(Navigator.prototype, 'pdfViewerEnabled', { get: () => true, configurable: true }); } catch (_) {}

  // === WebGL 额外参数 ===
  // 除 vendor/renderer 外，反爬还会查 MAX_TEXTURE_SIZE / 着色器精度等
  try {
    const patchExt = (Proto) => {
      const oExts = Proto.prototype.getSupportedExtensions;
      Proto.prototype.getSupportedExtensions = function () {
        // 真 Chrome / Intel Iris 典型扩展集
        return [
          'ANGLE_instanced_arrays','EXT_blend_minmax','EXT_color_buffer_half_float','EXT_disjoint_timer_query',
          'EXT_float_blend','EXT_frag_depth','EXT_shader_texture_lod','EXT_texture_compression_bptc',
          'EXT_texture_compression_rgtc','EXT_texture_filter_anisotropic','EXT_sRGB','OES_element_index_uint',
          'OES_fbo_render_mipmap','OES_standard_derivatives','OES_texture_float','OES_texture_float_linear',
          'OES_texture_half_float','OES_texture_half_float_linear','OES_vertex_array_object',
          'WEBGL_color_buffer_float','WEBGL_compressed_texture_s3tc','WEBGL_compressed_texture_s3tc_srgb',
          'WEBGL_debug_renderer_info','WEBGL_debug_shaders','WEBGL_depth_texture','WEBGL_draw_buffers',
          'WEBGL_lose_context','WEBGL_multi_draw',
        ];
      };
      try { wrap(Proto.prototype.getSupportedExtensions); } catch (_) {}
    };
    patchExt(WebGLRenderingContext);
    if (typeof WebGL2RenderingContext !== 'undefined') patchExt(WebGL2RenderingContext);
  } catch (_) {}
})();
`;

/**
 * 把地址栏里随便丢进来的字符串规范成可以 page.goto 的 URL：
 *   "google.com"          -> "https://google.com"
 *   "localhost:3000"      -> "http://localhost:3000"
 *   "what is rust"        -> "https://www.google.com/search?q=what%20is%20rust"
 *   "https://x.com/y"     -> 原样
 */
function normalizeUrl(raw: string): string {
  const s = raw.trim();
  if (!s) return "about:blank";
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) return s;
  if (/^about:|^chrome:|^data:|^javascript:|^file:/i.test(s)) return s;
  // host[:port][/path] 形如 "x.y", "localhost", "10.0.0.1:8080"
  const looksLikeHost = /^[\w-]+(\.[\w-]+)+(:\d+)?(\/.*)?$/.test(s)
    || /^localhost(:\d+)?(\/.*)?$/.test(s)
    || /^(\d{1,3}\.){3}\d{1,3}(:\d+)?(\/.*)?$/.test(s);
  if (looksLikeHost) {
    const proto = /^localhost|^127\.|^10\.|^192\.168\./.test(s) ? "http" : "https";
    return `${proto}://${s}`;
  }
  // 没空格也不像 URL → 兜底当域名加 https
  if (!/\s/.test(s) && /^[\w-]+\.[a-z]{2,}/i.test(s)) return `https://${s}`;
  // 含空格 → 走 Google 搜索
  return `https://www.google.com/search?q=${encodeURIComponent(s)}`;
}

interface ClientMsg {
  type:
    | "navigate"
    | "back"
    | "forward"
    | "reload"
    | "mouse"
    | "wheel"
    | "key"
    | "type"
    | "resize"
    | "evaluate"
    | "ack";
  // navigate
  url?: string;
  // mouse
  x?: number;
  y?: number;
  button?: "left" | "middle" | "right" | "none";
  action?: "down" | "up" | "move";
  clickCount?: number;
  buttons?: number;
  modifiers?: number;
  // wheel
  deltaX?: number;
  deltaY?: number;
  // key
  keyCode?: number;
  key?: string;
  code?: string;
  text?: string;
  unmodifiedText?: string;
  isKeypad?: boolean;
  isSystemKey?: boolean;
  location?: number;
  keyAction?: "keyDown" | "keyUp" | "rawKeyDown" | "char";
  // type
  textBlock?: string;
  // evaluate
  expression?: string;
  // resize
  width?: number;
  height?: number;
  deviceScaleFactor?: number;
  // ack
  sessionId?: number;
}

interface SessionOpts {
  width: number;
  height: number;
  proxy?: string;
  userAgent?: string;
}

let _browserPromise: Promise<Browser> | null = null;

async function getBrowser(): Promise<Browser> {
  // 健康检查：浏览器进程死了/崩了，重置 promise 让下个 session 重新启
  if (_browserPromise) {
    try {
      const b = await _browserPromise;
      if (!b.isConnected()) { _browserPromise = null; }
    } catch { _browserPromise = null; }
  }
  if (_browserPromise) return _browserPromise;
  const exe = process.env.REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE
    || process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
    || undefined;
  const proxyEnv = process.env.BROWSER_PROXY;
  // 如果有 DISPLAY（生产环境的 Xvfb :99），就跑 headed Chromium —— 真 X 显示器
  // 上的 Chrome 比 headless Chromium 难被反爬检测（Cloudflare / hCaptcha 等）
  const display = process.env.DISPLAY;
  const useHeaded = !!display && process.platform === "linux";

  const args = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process,AutomationControlled,Translate",
    "--no-default-browser-check",
    "--no-first-run",
    "--mute-audio",
    // 强制 UI/系统 locale 为 en-US，否则 Linux 上 LANG=zh_CN.UTF-8 会让 navigator.language
    // 之外的部分（HTTP Accept-Language 协商内核回退、字体回退、Date toString）漏出中文
    "--lang=en-US",
    "--disable-extensions-except",
    "--disable-component-extensions-with-background-pages",
    // Linux 上不带 --password-store=basic 会触发 keyring 报错
    "--password-store=basic",
    "--use-mock-keychain",
    // 窗口尺寸跟 Xvfb 屏一致 + 真实定位
    "--window-size=1920,1080",
    "--window-position=0,0",
    "--start-maximized",
    "--disable-infobars",
  ];
  // 代理：用命令行 --proxy-server 传，不能用 Playwright launch.proxy 选项！
  // 后者会自动注入 --host-resolver-rules="MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"，
  // 把所有域名解析废掉，导致 DoH bootstrap 和业务 URL 全 NOTFOUND → chrome-error://chromewebdata/
  if (proxyEnv) {
    args.push("--proxy-server=" + proxyEnv);
    // QUIC 走 UDP，--proxy-server 只代理 TCP！google/cloudfront 默认走 QUIC →
    // 直接从 VPS 真实 IP 出去 → 反爬看到的 IP 跟 SOCKS5 出口不一致 → 立即识破 + IP 泄漏
    args.push("--disable-quic");
    // WebRTC：addIceCandidate 拦截只过滤 SDP 字符串，但 STUN 探测包已经通过 UDP 发出去
    // 暴露真实/内网 IP。强制 ICE 必须走代理，无代理时禁用非代理 UDP
    args.push("--force-webrtc-ip-handling-policy=disable_non_proxied_udp");
    args.push("--webrtc-ip-handling-policy=disable_non_proxied_udp");
  }
  // DNS防污染：无论 headed/headless 都必须注入，避免系统 DNS 被 GFW UDP 污染
  // SOCKS proxy 出去的请求 DNS 也走代理内 DoH (secure 强制模式)
  args.push(
    "--proxy-resolves-dns-locally",
    "--enable-features=AsyncDns,DnsOverHttps",
    "--dns-over-https-mode=secure",
    "--dns-over-https-templates=https://1.1.1.1/dns-query,https://dns.google/dns-query",
  );
  if (useHeaded) {
    // GPU 走 ANGLE+SwiftShader：headed Chromium 在 Xvfb 上需要软件 GL 后端，
    //   否则 WebGL 直接关闭 → fingerprint 上一眼识破
    args.push("--use-gl=angle", "--use-angle=swiftshader", "--enable-webgl");
  } else {
    args.push("--disable-gpu");
  }

  _browserPromise = chromium.launch({
    headless: !useHeaded,
    executablePath: exe,
    args,
    // 砍掉默认会带的 --enable-automation 开关
    ignoreDefaultArgs: ["--enable-automation"],
    // LANG/LC_ALL 强制 en_US.UTF-8：容器默认 zh_CN.UTF-8 会让 Chrome 字体回退/Intl.format
    // 实际渲染中文 fallback、Date.toString() 输出"二〇二六年" → 一眼识破不是美国机
    env: {
      ...process.env,
      LANG: "en_US.UTF-8",
      LC_ALL: "en_US.UTF-8",
      LANGUAGE: "en_US:en",
      ...(useHeaded ? { DISPLAY: display } : {}),
    } as Record<string, string>,
  }).then((b) => {
    logger.info({ exe, proxy: !!proxyEnv, headed: useHeaded, display }, "[cdp-broker] browser launched");
    return b;
  }).catch((err) => {
    _browserPromise = null;
    throw err;
  });
  return _browserPromise;
}

export class CdpSession {
  private ctx: BrowserContext | null = null;
  private page: Page | null = null;
  private cdp: CDPSession | null = null;
  private closed = false;
  private currentUrl = "about:blank";
  private viewport = { w: 1280, h: 800 };
  private lastStatus = 0;

  constructor(private ws: WebSocket) {}

  send(obj: Record<string, unknown>) {
    if (this.ws.readyState === 1) {
      try { this.ws.send(JSON.stringify(obj)); } catch { /* ignore */ }
    }
  }

  async start(opts: SessionOpts) {
    const browser = await getBrowser();
    this.viewport = { w: opts.width, h: opts.height };
    // UA 必须 (a) Linux 平台 (b) Chrome 145（playwright 1.59 / chromium-1208 实际版本）
    // 否则 sec-ch-ua 客户端提示和 UA 不一致 → 现代反爬 (Cloudflare BM, Akamai BMP) 立刻识破
    const ua = opts.userAgent || (
      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      + "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    );
    this.ctx = await browser.newContext({
      viewport: { width: opts.width, height: opts.height },
      // 真物理屏 1920x1080，window.screen.* 和这里要对齐
      screen: { width: 1920, height: 1080 },
      deviceScaleFactor: opts.deviceScaleFactor ?? 1,
      isMobile: false,
      hasTouch: false,
      userAgent: ua,
      locale: "en-US",
      timezoneId: "America/Los_Angeles",
      colorScheme: "light",
      ignoreHTTPSErrors: true,
      // Client Hints —— 现代反爬必查项，必须跟 UA 串自洽
      extraHTTPHeaders: {
        "Accept-Language": "en-US,en;q=0.9",
        "sec-ch-ua": "\"Chromium\";v=\"145\", \"Not:A-Brand\";v=\"99\", \"Google Chrome\";v=\"145\"",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "\"Linux\"",
      },
      // 跟时区一致：洛杉矶（Mission District 附近），地理位置/时区/locale 三者自洽
      // 否则反爬看到 timezone=LA 但 geolocation=null 立马起疑
      geolocation: { latitude: 37.7749, longitude: -122.4194, accuracy: 50 },
      permissions: ["geolocation", "clipboard-read", "clipboard-write", "notifications"],
    });
    await this.ctx.addInitScript({ content: STEALTH_INIT });
    this.page = await this.ctx.newPage();
    this.cdp = await this.ctx.newCDPSession(this.page);

    // 监听导航变化 → 推给前端更新地址栏
    // 主帧 document 响应状态码追踪 —— CF 第一次返回 403 + JS 挑战，挑战通过后
    // 同一主帧再次拿到 200，UI 上想显示状态码就得拿后者
    this.page.on("response", (r) => {
      try {
        if (!this.page || r.frame() !== this.page.mainFrame()) return;
        const rt = (r.request().resourceType?.() || "").toString();
        if (rt && rt !== "document") return;
        this.lastStatus = r.status();
      } catch { /* ignore */ }
    });

    this.page.on("framenavigated", (frame) => {
      if (frame === this.page!.mainFrame()) {
        this.currentUrl = frame.url();
        this.send({ type: "url", url: this.currentUrl, title: "" });
        this.page!.title().then((t) => this.send({ type: "title", title: t })).catch(() => {});
      }
    });
    this.page.on("close", () => this.close().catch(() => {}));
    // alert / confirm / prompt / beforeunload —— 不处理会挂住主线程，
    //   表现为：触发任意 JS 弹窗后整个页面冻住、点哪都没反应。
    //   策略：默认全部 dismiss（confirm/prompt 视为取消），并把内容上报给前端
    //   以便将来弹原生模态。
    this.page.on("dialog", (dialog) => {
      this.send({ type: "dialog", kind: dialog.type(), message: dialog.message() });
      dialog.dismiss().catch(() => {});
    });
    // window.open() 弹窗页面不会画到当前 canvas —— 把它的 URL 拿出来在主页面跳转，
    //   行为类似按住 Ctrl 点链接的反向：把"新窗口"重定向回当前 tab。
    this.ctx.on("page", (p) => {
      if (p === this.page) return;
      const url = p.url();
      p.close().catch(() => {});
      if (url && url !== "about:blank") {
        this.page?.goto(url, { waitUntil: "domcontentloaded", timeout: 60_000 }).catch(() => {});
      }
    });

    // 启动 CDP screencast，每帧推到前端
    await this.cdp.send("Page.enable");
    this.cdp.on("Page.screencastFrame", (params: { data: string; sessionId: number; metadata: unknown }) => {
      this.send({ type: "frame", data: params.data, sid: params.sessionId });
      // 默认服务端立即 ack；如果前端要求节流（resize 时）再以 ack 替代
      this.cdp!.send("Page.screencastFrameAck", { sessionId: params.sessionId }).catch(() => {});
    });
    await this.cdp.send("Page.startScreencast", {
      format: "jpeg",
      quality: 60,
      maxWidth: opts.width,
      maxHeight: opts.height,
      everyNthFrame: 1,
    });

    this.send({ type: "ready", width: opts.width, height: opts.height });
    logger.info({ w: opts.width, h: opts.height }, "[cdp-broker] session started");
  }

  async handleMessage(raw: string | Buffer) {
    if (this.closed || !this.page || !this.cdp) return;
    let msg: ClientMsg;
    try { msg = JSON.parse(raw.toString()) as ClientMsg; } catch { return; }
    try {
      switch (msg.type) {
        case "navigate":
          if (msg.url) {
            const target = normalizeUrl(msg.url);
            this.lastStatus = 0;
            const page = this.page;
            // 1) goto —— SPA 在 load 里 location.replace 会把当前 goto 抛错（NS_BINDING_ABORTED
            //    / net::ERR_ABORTED），但页面 DOM 实际已经在新 URL 上。只要还有 document
            //    就当成功，避免 Outlook/Teams/M365 这类站打不开
            try {
              await page.goto(target, { waitUntil: "domcontentloaded", timeout: 60_000 });
            } catch (err) {
              const hasDoc = await page
                .evaluate(() => !!document && !!document.documentElement)
                .catch(() => false);
              if (!hasDoc) {
                this.send({ type: "navError", url: target, error: String((err as Error)?.message ?? err) });
                break;
              }
            }
            // 2) 等 SPA 完全 idle（软超时，等不到也无所谓）
            await page.waitForLoadState("networkidle", { timeout: 6000 }).catch(() => {});
            // 3) Cloudflare "Just a moment..." 五秒挑战 —— 它的 JS 通过后会自己跳到真页
            //    现在我们带了完整指纹/headed Chrome/sec-ch-ua/WebGL，绝大多数能自动通过
            try {
              const probe = await page.content().catch(() => "");
              if (/<title>Just a moment|cf-browser-verification|id="challenge-form"|cdn-cgi\/challenge-platform|name="cf-turnstile-response"/i.test(probe)) {
                this.send({ type: "cfChallenge", state: "waiting" });
                await page
                  .waitForFunction(
                    () => !/Just a moment|challenge-form|cf-browser-verification/i.test(document.documentElement.outerHTML),
                    { timeout: 15_000 },
                  )
                  .catch(() => {});
                await page.waitForLoadState("networkidle", { timeout: 4000 }).catch(() => {});
                this.send({ type: "cfChallenge", state: "done" });
              }
            } catch { /* ignore */ }
            this.send({ type: "httpStatus", status: this.lastStatus });
          }
          break;
        case "back":   await this.page.goBack({ waitUntil: "domcontentloaded" }).catch(() => {}); break;
        case "forward":await this.page.goForward({ waitUntil: "domcontentloaded" }).catch(() => {}); break;
        case "reload": await this.page.reload({ waitUntil: "domcontentloaded" }).catch(() => {}); break;
        case "mouse": {
          const action = msg.action ?? "move";
          const cdpType = action === "down" ? "mousePressed" : action === "up" ? "mouseReleased" : "mouseMoved";
          await this.cdp.send("Input.dispatchMouseEvent", {
            type: cdpType,
            x: msg.x ?? 0,
            y: msg.y ?? 0,
            button: msg.button ?? "none",
            buttons: msg.buttons ?? 0,
            clickCount: msg.clickCount ?? (action === "down" ? 1 : 0),
            modifiers: msg.modifiers ?? 0,
          });
          break;
        }
        case "wheel":
          await this.cdp.send("Input.dispatchMouseEvent", {
            type: "mouseWheel",
            x: msg.x ?? 0,
            y: msg.y ?? 0,
            deltaX: msg.deltaX ?? 0,
            deltaY: msg.deltaY ?? 0,
            modifiers: msg.modifiers ?? 0,
          });
          break;
        case "key":
          await this.cdp.send("Input.dispatchKeyEvent", {
            type: msg.keyAction ?? "keyDown",
            modifiers: msg.modifiers ?? 0,
            text: msg.text,
            unmodifiedText: msg.unmodifiedText ?? msg.text,
            key: msg.key,
            code: msg.code,
            windowsVirtualKeyCode: msg.keyCode,
            nativeVirtualKeyCode: msg.keyCode,
            location: msg.location ?? 0,
            isKeypad: msg.isKeypad ?? false,
            isSystemKey: msg.isSystemKey ?? false,
          });
          break;
        case "evaluate":
          if (msg.expression) {
            try {
              const val = await this.page.evaluate(msg.expression as string);
              this.send({ type: "evaluateResult", result: val });
            } catch (e) {
              this.send({ type: "evaluateResult", error: String(e) });
            }
          }
          break;
        case "type":
          if (msg.textBlock) {
            await this.page.keyboard.insertText(msg.textBlock);
          }
          break;
        case "resize":
          if (msg.width && msg.height) {
            this.viewport = { w: msg.width, h: msg.height };
            await this.page.setViewportSize({ width: msg.width, height: msg.height });
            // 重启 screencast 以套用新尺寸
            await this.cdp.send("Page.stopScreencast").catch(() => {});
            await this.cdp.send("Page.startScreencast", {
              format: "jpeg",
              quality: 60,
              maxWidth: msg.width,
              maxHeight: msg.height,
              everyNthFrame: 1,
            });
          }
          break;
        case "ack":
          // 客户端要求显式 ack 控流：默认我们已 ack，所以这里 no-op
          break;
      }
    } catch (e) {
      logger.warn({ msgType: msg.type, err: String(e) }, "[cdp-broker] handleMessage error");
    }
  }

  async close() {
    if (this.closed) return;
    this.closed = true;
    try { await this.cdp?.send("Page.stopScreencast"); } catch {}
    try { await this.cdp?.detach(); } catch {}
    try { await this.page?.close({ runBeforeUnload: false }); } catch {}
    try { await this.ctx?.close(); } catch {}
    this.cdp = null;
    this.page = null;
    this.ctx = null;
    logger.info("[cdp-broker] session closed");
  }
}

export async function shutdownBrowser() {
  if (!_browserPromise) return;
  try {
    const b = await _browserPromise;
    await b.close();
  } catch {}
  _browserPromise = null;
}
