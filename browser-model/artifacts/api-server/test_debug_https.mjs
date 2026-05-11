import { chromium } from "playwright";
import { readFileSync } from "fs";

const BINARY = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const PROXY  = "socks5://127.0.0.1:10851";
const tsSrc = readFileSync("/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts","utf8");
const STEALTH_INIT = (tsSrc.match(/export const STEALTH_INIT = `([\s\S]*?)`;/) ||[])[1]||"";
console.log("STEALTH chars:", STEALTH_INIT.length);

// Debug wrapper: intercept ODP calls and log successes/failures
const DEBUG = `
var __L=[];
var __O=Object.defineProperty;
Object.defineProperty=function(o,p,d){
  var lbl=(o===window?'win':o===Navigator.prototype?'NavP':o===Screen.prototype?'ScrP':'?');
  try{var r=__O(o,p,d);__L.push('OK:'+lbl+'.'+p);return r;}
  catch(e){__L.push('FAIL:'+lbl+'.'+p+':'+e.message);throw e;}
};
`;
const FULL = DEBUG + STEALTH_INIT;

const br = await chromium.launch({
  headless:false, executablePath:BINARY,
  args:["--no-sandbox","--disable-dev-shm-usage","--disable-blink-features=AutomationControlled",
    "--no-first-run","--mute-audio","--lang=en-US","--use-gl=angle","--use-angle=swiftshader",
    "--enable-webgl","--window-size=1920,1080","--fingerprint=99887766",
    "--fingerprint-platform=linux","--fingerprint-brand=Chrome","--fingerprint-brand-version=144",
    `--proxy-server=${PROXY}`,"--disable-quic","--proxy-resolves-dns-locally","--disable-non-proxied-udp"],
  ignoreDefaultArgs:["--enable-automation"],
  env:{...process.env,DISPLAY:":99"},
});
const ctx = await br.newContext({
  userAgent:"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
  viewport:{width:1920,height:1040}, screen:{width:1920,height:1080},
  locale:"en-US", timezoneId:"America/Los_Angeles",
  colorScheme:"light", ignoreHTTPSErrors:true,
});
await ctx.addInitScript(FULL);
const pg = await ctx.newPage();
await pg.goto("https://example.com",{timeout:30000,waitUntil:"load"});
await pg.waitForTimeout(800);

const r = await pg.evaluate(()=>({
  scrH:screen.height, scrAH:screen.availHeight, scrAW:screen.availWidth,
  noTaskbar: screen.height===screen.availHeight,
  notifPerm: window.Notification?window.Notification.permission:"NONE",
  hasShare:"share" in navigator, hasCanShare:"canShare" in navigator,
  log: window.__L||[],
}));

console.log("screen h/ah/aw:", r.scrH, r.scrAH, r.scrAW, "noTaskbar:", r.noTaskbar);
console.log("Notification.permission:", r.notifPerm);
console.log("share:", r.hasShare, "canShare:", r.hasCanShare);
console.log("--- ODP LOG ---");
r.log.forEach(l=>console.log(" ",l));
await br.close();
