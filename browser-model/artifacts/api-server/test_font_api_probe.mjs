import { chromium } from "playwright";
import { readFileSync } from "fs";

const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const PROXY  = "socks5://127.0.0.1:10916";
const tsSrc  = readFileSync("/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts","utf8");
const STEALTH_INIT   = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/)  ||[])[1]||"";
const BOOT_SUFFIX    = (tsSrc.match(/const _WORKER_BOOT_SUFFIX = `([\s\S]*?)`;/)  ||[])[1]||"";
const STEALTH_FULL   = STEALTH_INIT + (BOOT_SUFFIX||"");

const browser = await chromium.launch({
  headless: false, executablePath: BINARY,
  args: [
    "--no-sandbox","--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--no-first-run","--no-default-browser-check","--mute-audio",
    "--lang=en-US","--use-gl=angle","--use-angle=swiftshader",
    "--enable-webgl","--window-size=1920,1080",
    `--fingerprint=${Math.floor(Math.random()*0x7fffffff)}`,
    "--fingerprint-platform=linux","--fingerprint-brand=Chrome",
    "--fingerprint-brand-version=144","--fingerprint-hardware-concurrency=4",
    "--timezone=America/Los_Angeles",
    `--proxy-server=${PROXY}`,"--disable-quic",
    "--proxy-resolves-dns-locally","--disable-non-proxied-udp",
  ],
  ignoreDefaultArgs: ["--enable-automation"],
  env: { ...process.env, DISPLAY: ":99" },
});
const ctx = await browser.newContext({
  userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
  viewport: {width:1920,height:1040}, screen:{width:1920,height:1080},
  locale:"en-US", timezoneId:"America/Los_Angeles",
  colorScheme:"light", ignoreHTTPSErrors:true,
});
await ctx.addInitScript(STEALTH_FULL);
const page = await ctx.newPage();
await page.goto("about:blank");

// Probe 1: Canvas font metrics (platform hints check)
const fontMetrics = await page.evaluate(() => {
  const results = {};
  const cv = document.createElement('canvas');
  cv.width = 500; cv.height = 100;
  const c = cv.getContext('2d');
  
  // Test multiple font sizes for Arial and other fonts (what CreepJS likely checks)
  const fonts = ['Arial', 'Courier New', 'Georgia', 'Times New Roman'];
  const sizes = [10, 11, 12, 13, 14, 15, 16, 20, 24];
  const testStr = 'mmmmmmmmmmlli';
  
  results.measureText = {};
  for (const font of fonts.slice(0,2)) {
    results.measureText[font] = {};
    for (const sz of sizes) {
      c.font = `${sz}px ${font}`;
      const m = c.measureText(testStr);
      results.measureText[font][sz] = Math.round(m.width * 100) / 100;
    }
  }
  
  // Test specific emoji + font combos like CreepJS does
  c.font = '16px Arial';
  results.arialWidth16_A = c.measureText('A').width;
  results.arialWidth16_abc = c.measureText('abcdefghijklmnopqrstuvwxyz').width;
  c.font = '10px Arial';
  results.arialWidth10_A = c.measureText('A').width;
  results.arialWidth10_abc = c.measureText('abcdefghijklmnopqrstuvwxyz').width;
  
  // The specific 0:73:100:91:91 pattern from CreepJS 
  // Likely measures at 5 sizes: 10, 20, 30, 40, 50 or similar
  const platformSizes = [10, 16, 20, 24, 32];
  const platformStr = 'Cwm fjordbank glyphs vext quiz';
  results.platformHints = {};
  for (const sz of platformSizes) {
    c.font = `${sz}px Arial`;
    results.platformHints[sz] = Math.round(c.measureText(platformStr).width);
  }
  
  // Simple single char at 10px
  c.font = '10px Arial';
  results.A_10px = c.measureText('A').width;
  
  return results;
});

// Probe 2: Load APIs - what's actually undefined
const loadAPIs = await page.evaluate(() => {
  // Full list of Chrome APIs that CreepJS checks in the "load" fingerprint
  // These are common ones that headless Chrome is missing
  const apiList = [
    // Window-level APIs
    'speechSynthesis', 'webkitSpeechRecognition', 'SpeechRecognition',
    'webkitSpeechGrammarList', 'SpeechGrammarList',
    'openDatabase', 'webkitRequestFileSystem', 'webkitResolveLocalFileSystemURL',
    'showOpenFilePicker', 'showSaveFilePicker', 'showDirectoryPicker',
    'launchQueue', 'documentPictureInPicture', 'userActivation',
    'scheduling', 'virtualKeyboard', 'windowControlsOverlay', 'ink',
    'credentialless', 'fence',
    // CSS-level APIs
    'CSS', 'CSS.registerProperty', 'CSS.paintWorklet',
    // Navigator APIs
    'navigator.bluetooth', 'navigator.usb', 'navigator.hid',
    'navigator.serial', 'navigator.nfc', 'navigator.gpu',
    'navigator.ink', 'navigator.mediaDevices',
    'navigator.wakeLock', 'navigator.share', 'navigator.clipboard',
    'navigator.getInstalledRelatedApps', 'navigator.setAppBadge',
    'navigator.clearAppBadge', 'navigator.userAgentData',
    'navigator.canShare', 'navigator.presentation',
    'navigator.virtualKeyboard',
    // Worker-accessible
    'ServiceWorker', 'CacheStorage', 'Cache',
    // WebXR
    'XR', 'XRSession', 'XRRigidTransform',
    // WebCodecs
    'VideoEncoder', 'VideoDecoder', 'AudioEncoder', 'AudioDecoder',
    'EncodedVideoChunk', 'EncodedAudioChunk', 'VideoFrame',
    // Payment
    'PaymentRequest', 'PaymentResponse',
    // Other common
    'EyeDropper', 'MathMLElement', 'ClipboardItem',
    'FileSystemDirectoryHandle', 'FileSystemFileHandle',
    'FileSystemHandle', 'FileSystemWritableFileStream',
    'AudioWorklet', 'AudioWorkletNode',
    'webkitOfflineAudioContext', 'OfflineAudioContext',
    'MediaRecorder', 'ImageCapture',
    'getScreenDetails', 'queryLocalFonts',
    'Notification', 'PushManager',
    'BackgroundFetchManager', 'SyncManager',
    'cookieStore', 'CookieStore', 'CookieChangeEvent',
    'TrustedTypes', 'trustedTypes',
    'Sanitizer', 'HTMLSanitizerElement',
    'ElementInternals', 'CustomStateSet',
    'NavigationPreloadManager', 'NavigateEvent',
    'CompressionStream', 'DecompressionStream',
    'WebTransport', 'WebTransportBidirectionalStream',
    'AuthenticatorAttestationResponse',
    'InputDeviceCapabilities', 'WakeLock', 'WakeLockSentinel',
    'ContentIndex', 'StorageBucket', 'StorageBucketManager',
    'DocumentTimeline', 'KeyframeEffect', 'AnimationEffect',
    'PerformanceObserverEntryList', 'PerformanceLongTaskTiming',
    'TaskController', 'TaskPriorityChangeEvent', 'TaskSignal',
    'CSSNumericValue', 'CSSUnitValue', 'CSS2Properties',
    'Highlight', 'HighlightRegistry',
    'ViewTransition', 'SharedStorageWorklet',
    'IdentityCredential', 'FederatedCredential', 'PasswordCredential',
    'OTPCredential', 'DigitalCredential',
    'NavigationDestination', 'NavigationHistoryEntry', 'Navigation',
  ];

  const missing = [];
  const present = [];
  for (const api of apiList) {
    let val;
    try {
      if (api.includes('.')) {
        const parts = api.split('.');
        val = parts.reduce((obj, k) => obj?.[k], window);
      } else {
        val = window[api];
      }
    } catch(e) { val = undefined; }
    if (val === undefined || val === null) {
      missing.push(api);
    } else {
      present.push(api + ':' + typeof val);
    }
  }
  
  return { missing: missing.slice(0, 60), presentCount: present.length };
});

console.log("\n=== CANVAS FONT METRICS (platform hints diagnosis) ===");
console.log("Arial measureText widths (px):");
console.log("  10px 'A':", fontMetrics.A_10px);
console.log("  Platform hints (Cwm fjordbank...):");
for (const [sz, w] of Object.entries(fontMetrics.platformHints)) {
  console.log(`    ${sz}px: ${w}`);
}
console.log("  16px 'A':", fontMetrics.arialWidth16_A);
console.log("  16px 'abcdefghijklmnopqrstuvwxyz':", fontMetrics.arialWidth16_abc);

console.log("\n=== MISSING LOAD APIs ===");
console.log(`Missing (${loadAPIs.missing.length}):`, loadAPIs.missing.join(', '));
console.log(`Present: ${loadAPIs.presentCount}`);

await browser.close();
console.log("\nDone.");
