import { chromium } from "playwright";
import fs from "fs";
const exe = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const browser = await chromium.launch({
  headless: false, executablePath: exe,
  args: ["--no-sandbox","--use-gl=angle","--use-angle=swiftshader","--enable-webgl","--lang=en-US"],
  ignoreDefaultArgs: ["--enable-automation"],
  env: { ...process.env, DISPLAY:":99", LANG:"en_US.UTF-8", LC_ALL:"en_US.UTF-8" },
});
const ctx = await browser.newContext({ userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36" });
const page = await ctx.newPage();
await page.goto("about:blank");
const result = await page.evaluate(() => {
  const c = window.chrome || {};
  return {
    chrome_exists: !!window.chrome,
    app: typeof c.app,
    webstore: typeof c.webstore,
    runtime_id: c.runtime?.id,
    runtime_onConnect: typeof c.runtime?.onConnect,
    runtime_onMessage: typeof c.runtime?.onMessage,
    runtime_getManifest: typeof c.runtime?.getManifest,
    runtime_sendMessage: typeof c.runtime?.sendMessage,
    loadTimes: typeof c.loadTimes,
    csi: typeof c.csi,
    dom: typeof c.dom,
    cast: typeof c.cast,
    accessibilityFeatures: typeof c.accessibilityFeatures,
    fontSettings: typeof c.fontSettings,
    power: typeof c.power,
    privacy: typeof c.privacy,
    storage: typeof c.storage,
    tabCapture: typeof c.tabCapture,
    tabs: typeof c.tabs,
    windows: typeof c.windows,
    // Plugin check
    plugins_length: navigator.plugins.length,
    plugin_0: navigator.plugins[0]?.name,
    // fonts
    fonts_tostring: document.fonts?.toString(),
    // headless hints
    chrome_keys: Object.keys(window.chrome || {}),
    productSub: navigator.productSub,
    vendor: navigator.vendor,
    vendorSub: navigator.vendorSub,
  };
});
console.log(JSON.stringify(result, null, 2));
await browser.close();
