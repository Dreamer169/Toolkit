import { chromium } from "playwright";
const exe = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";

const WORKER_BODY = `
try {
  var brands = [{brand:"Chromium",version:"144"},{brand:"Not:A-Brand",version:"99"},{brand:"Google Chrome",version:"144"}];
  var fullList = [{brand:"Chromium",version:"144.0.7559.132"},{brand:"Not:A-Brand",version:"99.0.0.0"},{brand:"Google Chrome",version:"144.0.7559.132"}];
  var high = { architecture:"x86", bitness:"64", model:"", mobile:false, platform:"Linux", platformVersion:"6.8.0", uaFullVersion:"144.0.7559.132", wow64:false, formFactors:["Desktop"], fullVersionList:fullList, brands:brands };
  var uaData = { brands: brands, mobile: false, platform: "Linux",
    getHighEntropyValues: function(hints){ var o={brands:brands, mobile:false, platform:"Linux"}; (hints||[]).forEach(function(h){ if(h in high) o[h]=high[h]; }); return Promise.resolve(o); },
    toJSON: function(){ return {brands:brands, mobile:false, platform:"Linux"}; }
  };
  Object.defineProperty(WorkerNavigator.prototype, "userAgentData", { get: function(){return uaData;}, configurable: true });
} catch(e) { self.postMessage("uaData err: "+e); }
self.onmessage = async function(e) {
  if (!navigator.userAgentData) { self.postMessage("no uaData"); return; }
  const r = await navigator.userAgentData.getHighEntropyValues(["platformVersion"]);
  self.postMessage(JSON.stringify(r));
};
`;

const workerBlob = `data:application/javascript;base64,${Buffer.from(WORKER_BODY).toString("base64")}`;

const browser = await chromium.launch({
  headless: false, executablePath: exe,
  args: ["--no-sandbox","--use-gl=angle","--use-angle=swiftshader"],
  ignoreDefaultArgs: ["--enable-automation"],
  env: { ...process.env, DISPLAY:":99" },
});
const ctx = await browser.newContext({ locale:"en-US" });
const page = await ctx.newPage();
await page.goto("about:blank");

const result = await page.evaluate(async (blobUrl) => {
  const w = new Worker(blobUrl);
  return new Promise((res) => {
    w.onmessage = (e) => res(e.data);
    w.postMessage("go");
    setTimeout(() => res("timeout"), 5000);
  });
}, workerBlob);

console.log("Worker uaData platformVersion result:", result);

// Also test page-level
const pv = await page.evaluate(async () => {
  if (!navigator.userAgentData) return "no uaData on page";
  const r = await navigator.userAgentData.getHighEntropyValues(["platformVersion"]);
  return r.platformVersion;
});
console.log("Page platformVersion:", pv);

await browser.close();
