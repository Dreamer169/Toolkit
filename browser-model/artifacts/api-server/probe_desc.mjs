import { chromium } from "playwright";
const exe = "/opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome";
const browser = await chromium.launch({
  headless: false, executablePath: exe,
  args: ["--no-sandbox","--use-gl=angle","--use-angle=swiftshader","--enable-webgl"],
  ignoreDefaultArgs: ["--enable-automation"],
  env: { ...process.env, DISPLAY:":99", LANG:"en_US.UTF-8" },
});
const ctx = await browser.newContext();
const page = await ctx.newPage();
await page.goto("about:blank");
const r = await page.evaluate(() => {
  const chromeProp = Object.getOwnPropertyDescriptor(window, "chrome");
  const runtimeProp = window.chrome ? Object.getOwnPropertyDescriptor(window.chrome, "runtime") : null;
  const langProto = Object.getOwnPropertyDescriptor(Navigator.prototype, "languages");
  const langInst  = Object.getOwnPropertyDescriptor(navigator, "languages");
  // Try to assign runtime property directly
  let assignOk = false, definePropOk = false, assignMergeOk = false;
  if (window.chrome) {
    try { window.chrome._testProp = 123; assignOk = window.chrome._testProp === 123; delete window.chrome._testProp; } catch(e){}
    if (window.chrome.runtime) {
      try { window.chrome.runtime._testProp = 456; assignMergeOk = window.chrome.runtime._testProp===456; delete window.chrome.runtime._testProp; } catch(e){}
    }
    try {
      Object.defineProperty(window.chrome, "_testDef", {value:789,writable:true,enumerable:true,configurable:true});
      definePropOk = window.chrome._testDef===789; delete window.chrome._testDef;
    } catch(e){}
  }
  return {
    chrome: { writable:chromeProp?.writable, configurable:chromeProp?.configurable, enumerable:chromeProp?.enumerable },
    runtime: runtimeProp ? { writable:runtimeProp.writable, configurable:runtimeProp.configurable, enumerable:runtimeProp.enumerable } : null,
    runtime_keys: window.chrome?.runtime ? Object.keys(window.chrome.runtime) : [],
    langProto: langProto ? { configurable:langProto.configurable, writable:langProto.writable } : null,
    langInst: langInst ? { configurable:langInst.configurable, writable:langInst.writable } : null,
    assignOk, definePropOk, assignMergeOk,
    chrome_keys_before: Object.keys(window.chrome||{}),
  };
});
console.log(JSON.stringify(r, null, 2));
await browser.close();
