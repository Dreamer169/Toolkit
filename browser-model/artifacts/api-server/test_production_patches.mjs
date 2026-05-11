import { chromium } from "playwright";
import { readFileSync } from "fs";
const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const tsSrc  = readFileSync("/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts","utf8");
const STEALTH_INIT    = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/) ||[])[1]||"";
const LATE_FIX_PATCHES = (tsSrc.match(/const LATE_FIX_PATCHES = `([\s\S]*?)`;/) ||[])[1]||"";
console.log("STEALTH_INIT:", STEALTH_INIT.length, "chars");
console.log("LATE_FIX_PATCHES:", LATE_FIX_PATCHES.length, "chars");
if (!LATE_FIX_PATCHES) { console.error("LATE_FIX_PATCHES not extracted!"); process.exit(1); }

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
// Production code path: STEALTH_INIT_FULL first, LATE_FIX_PATCHES second
await ctx.addInitScript(STEALTH_INIT);    // approximates STEALTH_INIT_FULL
await ctx.addInitScript(LATE_FIX_PATCHES);
const page = await ctx.newPage();
await page.goto("about:blank");

const r = await page.evaluate(async () => {
  const screenH  = screen.height;
  const screenAH = screen.availHeight;
  const noTaskbar = screenH === screenAH;
  let notifPerm = "NA";
  try { notifPerm = Notification.permission; } catch(_) {}
  const hasShare = "share" in navigator;
  const hasCanShare = "canShare" in navigator;
  let bgColor = "err";
  try {
    const el = document.createElement("div");
    document.body.appendChild(el);
    el.setAttribute("style","background-color: ActiveText");
    bgColor = getComputedStyle(el).backgroundColor;
    document.body.removeChild(el);
  } catch(e) { bgColor = "ex:"+e.message; }
  return { screenH, screenAH, noTaskbar, notifPerm, hasShare, hasCanShare, bgColor };
});
console.log("=== Production patch test (STEALTH_INIT + LATE_FIX_PATCHES) ===");
console.log(JSON.stringify(r, null, 2));
const pass = (r.noTaskbar === false) && (r.notifPerm === "default") && (r.hasShare === true) && (r.bgColor !== "rgb(255, 0, 0)");
console.log("ALL 4 PASS:", pass ? "YES ✓" : "NO ✗");
if (!pass) {
  console.log("  noTaskbar FAIL?", r.noTaskbar, "(want false)");
  console.log("  notifPerm FAIL?", r.notifPerm, "(want default)");
  console.log("  hasShare  FAIL?", r.hasShare, "(want true)");
  console.log("  bgColor   FAIL?", r.bgColor, "(want NOT rgb(255,0,0))");
}
await browser.close();
