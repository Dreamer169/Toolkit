
import { chromium } from "playwright";
import { readFileSync } from "fs";

const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const tsSrc  = readFileSync("/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts","utf8");
const STEALTH_INIT = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/) ||[])[1]||"";
console.log("STEALTH_INIT chars:", STEALTH_INIT.length);
// DO NOT append broken BOOT_SUFFIX — only use STEALTH_INIT
const STEALTH_FULL = STEALTH_INIT;

const browser = await chromium.launch({
  headless: false, executablePath: BINARY,
  args: [
    "--no-sandbox","--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--no-first-run","--no-default-browser-check","--mute-audio",
    "--lang=en-US","--use-gl=angle","--use-angle=swiftshader",
    "--enable-webgl","--window-size=1920,1080",
    "--fingerprint=77665544",
    "--fingerprint-platform=linux","--fingerprint-brand=Chrome",
    "--fingerprint-brand-version=144","--fingerprint-hardware-concurrency=4",
  ],
  ignoreDefaultArgs: ["--enable-automation"],
  env: { ...process.env, DISPLAY: ":99" },
});
const ctx = await browser.newContext({
  userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
  viewport: {width:1920,height:1040}, screen:{width:1920,height:1080},
  locale:"en-US", timezoneId:"America/Los_Angeles",
  colorScheme:"dark",
  ignoreHTTPSErrors:true,
});
await ctx.addInitScript(STEALTH_FULL);

const page = await ctx.newPage();
await page.goto("about:blank");

const r = await page.evaluate(async () => {
  const screen_ah = screen.availHeight;
  const screen_h  = screen.height;
  const noTaskbar = screen.height === screen.availHeight;
  let notifPerm = "NO_NOTIF";
  try { notifPerm = Notification.permission; } catch(_) {}
  const hasShare = "share" in navigator;
  const hasCanShare = "canShare" in navigator;

  let bgColor = "no_body";
  try {
    const el = document.createElement("div");
    document.body.appendChild(el);
    el.setAttribute("style","background-color: ActiveText");
    bgColor = getComputedStyle(el).backgroundColor;
    document.body.removeChild(el);
  } catch(e) { bgColor = "err:" + e.message; }

  return { screen_h, screen_ah, noTaskbar, notifPerm, hasShare, hasCanShare, bgColor };
});

console.log("=== STEALTH_INIT-only results ===");
console.log("screen:", r.screen_h, "/", r.screen_ah, "noTaskbar:", r.noTaskbar);
console.log("Notification.permission:", r.notifPerm);
console.log("share:", r.hasShare, "canShare:", r.hasCanShare);
console.log("bgColor:", r.bgColor);
await browser.close();
