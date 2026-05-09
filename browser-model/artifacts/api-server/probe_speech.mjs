import { chromium } from "playwright";
const exe = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const browser = await chromium.launch({
  headless: false, executablePath: exe,
  args: ["--no-sandbox","--use-gl=angle","--use-angle=swiftshader"],
  ignoreDefaultArgs: ["--enable-automation"],
  env: { ...process.env, DISPLAY:":99" },
});
const ctx = await browser.newContext({ locale:"en-US" });
await ctx.addInitScript(`(function(){
  try {
    var _fakeVoices = [
      { voiceURI:"Google US English", name:"Google US English", lang:"en-US", localService:false, default:true },
      { voiceURI:"Google UK English Female", name:"Google UK English Female", lang:"en-GB", localService:false, default:false },
      { voiceURI:"Google Deutsch", name:"Google Deutsch", lang:"de-DE", localService:false, default:false },
    ];
    if (typeof SpeechSynthesis !== "undefined") {
      Object.defineProperty(SpeechSynthesis.prototype, "getVoices", {value:function(){return _fakeVoices;},writable:true,configurable:true});
    }
    if (typeof speechSynthesis !== "undefined") {
      Object.defineProperty(speechSynthesis, "getVoices", {value:function(){return _fakeVoices;},writable:true,configurable:true});
    }
  } catch(e) { console.error("speech err:", e); }
})();`);
const page = await ctx.newPage();
await page.goto("about:blank");
const r = await page.evaluate(() => {
  const voices = speechSynthesis.getVoices();
  const perms = typeof navigator.permissions;
  return {
    voiceCount: voices.length,
    firstVoice: voices[0]?.name,
    speechSynthExists: typeof speechSynthesis,
    permissionsType: perms,
  };
});
console.log(JSON.stringify(r, null, 2));
await browser.close();
