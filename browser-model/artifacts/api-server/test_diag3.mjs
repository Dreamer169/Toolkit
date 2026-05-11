
import { chromium } from "playwright";
import { readFileSync } from "fs";
const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const tsSrc  = readFileSync("/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts","utf8");
const STEALTH_INIT = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/) ||[])[1]||"";
const browser = await chromium.launch({
  headless: false, executablePath: BINARY,
  args: ["--no-sandbox","--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--no-first-run","--mute-audio","--use-gl=angle","--use-angle=swiftshader",
    "--enable-webgl","--window-size=1920,1080","--fingerprint=11223344",
    "--fingerprint-platform=linux","--fingerprint-brand=Chrome","--fingerprint-brand-version=144"],
  ignoreDefaultArgs: ["--enable-automation"],
  env: { ...process.env, DISPLAY: ":99" },
});
const ctx = await browser.newContext({
  userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
  viewport:{width:1920,height:1040}, screen:{width:1920,height:1080},
  locale:"en-US", colorScheme:"dark", ignoreHTTPSErrors:true,
});
await ctx.addInitScript(STEALTH_INIT);
const page = await ctx.newPage();
await page.goto("about:blank");
const r = await page.evaluate(() => {
  // 1. Check Screen.prototype own descriptor  
  const desc = Object.getOwnPropertyDescriptor(Screen.prototype, "availHeight");
  const screenProtoDesc = desc ? {
    hasGet: typeof desc.get === "function",
    getIsNative: desc.get ? desc.get.toString().includes("[native code]") : null,
    configurable: desc.configurable,
    enumerable: desc.enumerable,
    getSource: desc.get ? desc.get.toString().slice(0,80) : null,
  } : null;
  
  // 2. Check if our getter actually runs when called
  const screenAH_direct = screen.availHeight;
  
  // 3. Check Notification own property
  const notifDesc = Object.getOwnPropertyDescriptor(window.Notification || {}, "permission");
  const notifDescInfo = notifDesc ? {
    hasGet: typeof notifDesc.get === "function",
    getIsNative: notifDesc.get ? notifDesc.get.toString().includes("[native code]") : null,
  } : "no_own_descriptor_on_Notification";
  
  // 4. Check Navigator.prototype share descriptor
  const shareDesc = Object.getOwnPropertyDescriptor(Navigator.prototype, "share");
  const shareDescInfo = shareDesc ? {
    type: typeof shareDesc.value || typeof shareDesc.get,
    isNative: shareDesc.value ? shareDesc.value.toString().includes("[native code]") : null,
  } : "no_share_on_Navigator.prototype";

  // 5. Check window.getComputedStyle descriptor
  const gcsDesc = Object.getOwnPropertyDescriptor(window, "getComputedStyle") ||
                  Object.getOwnPropertyDescriptor(Window.prototype, "getComputedStyle");
  const gcsDescInfo = gcsDesc ? {
    isNative: (gcsDesc.value || gcsDesc.get || "").toString().includes("[native code]"),
    configurable: gcsDesc.configurable,
    where: Object.getOwnPropertyDescriptor(window, "getComputedStyle") ? "own" : "Window.prototype",
  } : "not_found";
  
  return { screenProtoDesc, screenAH_direct, notifDescInfo, shareDescInfo, gcsDescInfo };
});
console.log(JSON.stringify(r, null, 2));
await browser.close();
