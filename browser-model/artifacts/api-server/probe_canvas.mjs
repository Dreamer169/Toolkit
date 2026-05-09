import { chromium } from "playwright";
const exe = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const browser = await chromium.launch({
  headless: false, executablePath: exe,
  args: ["--no-sandbox","--use-gl=angle","--use-angle=swiftshader","--enable-webgl"],
  ignoreDefaultArgs: ["--enable-automation"],
  env: { ...process.env, DISPLAY:":99" },
});
const ctx = await browser.newContext();
const page = await ctx.newPage();
await page.goto("about:blank");
const r = await page.evaluate(() => {
  const c1 = document.createElement("canvas");
  c1.width=200; c1.height=50;
  const cx = c1.getContext("2d");
  cx.fillStyle="#ff0000"; cx.fillRect(0,0,200,50);
  cx.font="18px Arial"; cx.fillStyle="#000"; cx.fillText("FingerprintJS",10,30);
  const canvasData = c1.toDataURL().substring(0,80);

  const ctxDesc = Object.getOwnPropertyDescriptor(CanvasRenderingContext2D.prototype, "getImageData");
  const toDUDesc = Object.getOwnPropertyDescriptor(HTMLCanvasElement.prototype, "toDataURL");
  const toBlobDesc = Object.getOwnPropertyDescriptor(HTMLCanvasElement.prototype, "toBlob");
  const audioGcdDesc = typeof AudioBuffer !== "undefined" ?
    Object.getOwnPropertyDescriptor(AudioBuffer.prototype, "getChannelData") : null;

  let gidDefOk = false, toDuDefOk = false, audioChdDefOk = false;
  try { const o=CanvasRenderingContext2D.prototype.getImageData; Object.defineProperty(CanvasRenderingContext2D.prototype,"getImageData",{value:o,writable:true,configurable:true}); gidDefOk=true; } catch(e){}
  try { const o=HTMLCanvasElement.prototype.toDataURL; Object.defineProperty(HTMLCanvasElement.prototype,"toDataURL",{value:o,writable:true,configurable:true}); toDuDefOk=true; } catch(e){}
  try { const o=AudioBuffer.prototype.getChannelData; Object.defineProperty(AudioBuffer.prototype,"getChannelData",{value:o,writable:true,configurable:true}); audioChdDefOk=true; } catch(e){}

  const speechVoices = typeof speechSynthesis !== "undefined" ? speechSynthesis.getVoices().length : -1;
  const plugins = Array.from(navigator.plugins||[]).map(p=>p.name);
  const mimeTypes = Array.from(navigator.mimeTypes||[]).map(m=>m.type);

  return {
    canvasData,
    gid: { configurable:ctxDesc?.configurable, writable:ctxDesc?.writable, gidDefOk },
    toDu: { configurable:toDUDesc?.configurable, writable:toDUDesc?.writable, toDuDefOk },
    toBlob: { configurable:toBlobDesc?.configurable },
    audioGcd: audioGcdDesc ? { configurable:audioGcdDesc.configurable, gcdDefOk: audioChdDefOk } : null,
    speechVoices, plugins, mimeTypes,
  };
});
console.log(JSON.stringify(r, null, 2));
await browser.close();
