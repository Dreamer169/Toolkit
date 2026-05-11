
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
    "--enable-webgl","--window-size=1920,1080",
    "--fingerprint=11223344","--fingerprint-platform=linux",
    "--fingerprint-brand=Chrome","--fingerprint-brand-version=144"],
  ignoreDefaultArgs: ["--enable-automation"],
  env: { ...process.env, DISPLAY: ":99" },
});
const ctx = await browser.newContext({
  userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
  viewport: {width:1920,height:1040}, screen:{width:1920,height:1080},
  locale:"en-US", colorScheme:"dark", ignoreHTTPSErrors:true,
});
await ctx.addInitScript(STEALTH_INIT);

const page = await ctx.newPage();
await page.goto("about:blank");

const r = await page.evaluate(() => {
  // Test gcs ODP status
  const gcsIsPatched = window.getComputedStyle.toString().includes("_gCS_patched") ||
                       window.getComputedStyle.name === "getComputedStyle";
  const gcsName = window.getComputedStyle.name;
  const gcsStr  = window.getComputedStyle.toString().slice(0, 80);

  // Test screen
  const innerH = window.innerHeight;
  const screenAH = screen.availHeight;
  const screenH  = screen.height;

  // Test notification
  let notifPerm = "NA";
  try { notifPerm = Notification.permission; } catch(_) {}

  // Test share
  const hasShare = "share" in navigator;

  // Test gcs bgColor
  const el = document.createElement("div");
  document.body.appendChild(el);
  el.setAttribute("style","background-color: ActiveText");
  const rawAttr = el.getAttribute("style");
  const gcsResult = window.getComputedStyle(el).backgroundColor;
  document.body.removeChild(el);

  return { gcsName, gcsStr, innerH, screenAH, screenH, notifPerm, hasShare, rawAttr, gcsResult };
});
console.log(JSON.stringify(r, null, 2));
await browser.close();
