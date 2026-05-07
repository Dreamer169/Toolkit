#!/usr/bin/env python3
"""
sandbox_pydoll_factory.py v5
KEY ADDITION: Comprehensive stealth patches including WebGL vendor/renderer
(critical for CF Turnstile — headless Chrome shows SwiftShader = bot)
"""
import asyncio, glob, json, os, re, subprocess, sys, time, urllib.request

VPS_API       = os.environ.get("VPS_API",       "http://45.205.27.69:8084")
SANDBOX_LABEL = os.environ.get("SANDBOX_LABEL", "sandbox")
SIGNUP_URL    = "https://api.airforce/signup"
REAL_UA       = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def _log(msg): print(f"[pydoll:{SANDBOX_LABEL}] {msg}", flush=True)

try:
    from pydoll.browser import Chrome
    from pydoll.browser.options import ChromiumOptions
    _log("pydoll ok")
except ImportError:
    subprocess.run([sys.executable,"-m","pip","install","-q","pydoll-python"], capture_output=True, timeout=120)
    from pydoll.browser import Chrome
    from pydoll.browser.options import ChromiumOptions

try:
    import nest_asyncio; nest_asyncio.apply()
except ImportError: pass

def _find_chromium():
    for pat in [os.path.expanduser("~/.cache/ms-playwright/chromium*/chrome-linux64/chrome"),
                "/usr/bin/chromium-browser", "/usr/bin/chromium"]:
        hits = sorted(glob.glob(pat))
        if hits: return hits[-1]
    return None

CHROMIUM_BIN = _find_chromium()
_log(f"chromium: {CHROMIUM_BIN}")
if not CHROMIUM_BIN: _log("FATAL: no chromium"); sys.exit(0)

def vps_get(path):
    try:
        with urllib.request.urlopen(f"{VPS_API}{path}", timeout=10) as r:
            return json.loads(r.read())
    except Exception as e: return {"error": str(e)}

def vps_post(path, payload):
    try:
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(f"{VPS_API}{path}", data=data, headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(req, timeout=10) as r: return json.loads(r.read())
    except Exception as e: return {"error": str(e)}

def gen_username(hint):
    import random, string
    base = re.sub(r"[^a-z0-9]","",hint.lower())[:12]
    sfx  = "".join(random.choices(string.ascii_lowercase+string.digits, k=4))
    return f"af_{base}{sfx}"

def gen_password():
    import secrets, string
    chars = string.ascii_letters + string.digits + "!@#$%"
    while True:
        pw = "".join(secrets.choice(chars) for _ in range(13))
        if (any(c.isupper() for c in pw) and any(c.islower() for c in pw)
                and any(c.isdigit() for c in pw) and any(c in "!@#$%" for c in pw)):
            return pw

RESULT = {"success":False,"username":None,"email":None,"password":None,
          "api_key":None,"error":None,"sandbox":SANDBOX_LABEL}

def _ex(raw):
    if raw is None: return None
    if isinstance(raw, (bool, int, float)): return raw
    if isinstance(raw, str): return None if raw.strip() in ("null","undefined","") else raw
    if isinstance(raw, dict):
        x = raw.get("result", {})
        if isinstance(x, dict): x = x.get("result", x)
        if isinstance(x, dict):
            if x.get("subtype") in ("null","undefined","error"): return None
            return x.get("value")
        return x
    return None

async def _js(tab, script):
    try: return _ex(await tab.execute_script(script))
    except Exception as e: _log(f"_js err: {str(e)[:50]}"); return None

# COMPREHENSIVE stealth — injected via Page.addScriptToEvaluateOnNewDocument
# Patches ALL known CF Turnstile detection vectors
STEALTH_EARLY = r"""
(function() {
  'use strict';
  
  // 1. Remove webdriver
  Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
  
  // 2. Fix userAgent (remove HeadlessChrome)
  const REAL_UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';
  Object.defineProperty(navigator, 'userAgent', {get: () => REAL_UA});
  Object.defineProperty(navigator, 'appVersion', {get: () => REAL_UA.replace('Mozilla/','')});
  
  // 3. Fix vendor
  Object.defineProperty(navigator, 'vendor', {get: () => 'Google Inc.'});
  
  // 4. Fix plugins
  function makeFakePlugin(name, fname, desc, mimeTypes) {
    return {name, filename:fname, description:desc, length:mimeTypes.length,
      item:function(i){return mimeTypes[i]||null},
      namedItem:function(n){return mimeTypes.find(m=>m.type===n)||null},
      [Symbol.iterator]:function*(){for(let m of mimeTypes) yield m;}};
  }
  var pdfMt = {type:'application/pdf',suffixes:'pdf',description:'',enabledPlugin:null};
  var fakePlugins = [
    makeFakePlugin('Chrome PDF Plugin','internal-pdf-viewer','Portable Document Format',[pdfMt]),
    makeFakePlugin('Chrome PDF Viewer','mhjfbmdgcfjbbpaeojofohoefgiehjai','',[pdfMt]),
    makeFakePlugin('Native Client','internal-nacl-plugin','',
      [{type:'application/x-nacl',suffixes:'',description:'Native Client Executable',enabledPlugin:null},
       {type:'application/x-pnacl',suffixes:'',description:'Portable Native Client Executable',enabledPlugin:null}])
  ];
  fakePlugins.item = i => fakePlugins[i]||null;
  fakePlugins.namedItem = n => fakePlugins.find(p=>p.name===n)||null;
  fakePlugins.refresh = function(){};
  fakePlugins.length = 3;
  Object.defineProperty(navigator, 'plugins', {get: () => fakePlugins});
  
  // 5. Fix mimeTypes
  Object.defineProperty(navigator, 'mimeTypes', {get: () => ({
    length:2, item:i=>[pdfMt][i]||null, namedItem:n=>n==='application/pdf'?pdfMt:null
  })});
  
  // 6. Fix languages
  Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
  Object.defineProperty(navigator, 'language',  {get: () => 'en-US'});
  
  // 7. Fix hardware
  Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
  Object.defineProperty(navigator, 'deviceMemory',        {get: () => 8});
  
  // 8. Fix connection
  if (!navigator.connection) {
    Object.defineProperty(navigator, 'connection', {
      get: () => ({rtt:50,type:'wifi',saveData:false,downlink:10,
        downlinkMax:Infinity,effectiveType:'4g'})
    });
  }
  
  // 9. Fix chrome runtime (CRITICAL)
  window.chrome = {
    runtime: {
      id: undefined,
      connect: function(){return {postMessage:function(){},disconnect:function(){}};},
      sendMessage: function(){},
      onMessage: {addListener:function(){}},
    },
    loadTimes: function() { return {
      requestTime: Date.now()/1000, startLoadTime: Date.now()/1000,
      commitLoadTime: Date.now()/1000, finishDocumentLoadTime: Date.now()/1000,
      finishLoadTime: Date.now()/1000, firstPaintTime: Date.now()/1000,
      firstPaintAfterLoadTime: 0, navigationType: 'Other',
      wasFetchedViaSpdy: false, wasNpnNegotiated: false, npnNegotiatedProtocol: 'http/1.1',
      wasAlternateProtocolAvailable: false, connectionInfo: 'http/1.1'
    };},
    csi: function() { return {startE:Date.now(),onloadT:Date.now(),pageT:1,tran:15}; },
    app: {isInstalled: false, getDetails: function(){return null;}, getIsInstalled: function(){return false;}}
  };
  
  // 10. Fix permissions
  if (navigator.permissions && navigator.permissions.query) {
    const origQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = function(params) {
      return params && params.name === 'notifications'
        ? Promise.resolve({state: Notification && Notification.permission || 'prompt'})
        : origQuery(params);
    };
  }
  
  // 11. Fix screen
  Object.defineProperty(window, 'outerWidth',  {get: () => 1280});
  Object.defineProperty(window, 'outerHeight', {get: () => 900});
  Object.defineProperty(screen, 'availWidth',  {get: () => 1280});
  Object.defineProperty(screen, 'availHeight', {get: () => 900});
  Object.defineProperty(screen, 'colorDepth',  {get: () => 24});
  Object.defineProperty(screen, 'pixelDepth',  {get: () => 24});
  
  // 12. CRITICAL: Override WebGL fingerprint (headless shows "SwiftShader" = bot!)
  function patchWebGL(proto) {
    if (!proto) return;
    const origGetParam = proto.getParameter;
    proto.getParameter = function(parameter) {
      // UNMASKED_VENDOR_WEBGL = 37445
      if (parameter === 37445) return 'Intel Inc.';
      // UNMASKED_RENDERER_WEBGL = 37446
      if (parameter === 37446) return 'ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)';
      return origGetParam.call(this, parameter);
    };
  }
  try { patchWebGL(WebGLRenderingContext.prototype); } catch(e) {}
  try { patchWebGL(WebGL2RenderingContext.prototype); } catch(e) {}
  
  // 13. Fix AudioContext (headless may return sample rate 0)
  try {
    const origAC = window.AudioContext || window.webkitAudioContext;
    if (origAC) {
      const OrigCtx = origAC;
      window.AudioContext = window.webkitAudioContext = function(...args) {
        const ctx = new OrigCtx(...args);
        Object.defineProperty(ctx, 'sampleRate', {get: () => 44100});
        return ctx;
      };
    }
  } catch(e) {}
  
  // 14. Turnstile hook (both window + globalThis)
  window.__ts_token = '';
  function _patchTS() {
    var ts = globalThis.turnstile || window.turnstile;
    if (ts && ts.render && !ts.__patched) {
      var orig = ts.render.bind(ts);
      ts.render = function(container, params) {
        if (params && typeof params.callback === 'function') {
          var cb = params.callback;
          params.callback = function(token) {
            window.__ts_token = token;
            if (cb) cb(token);
          };
        }
        return orig(container, params);
      };
      ts.__patched = true;
    }
  }
  setInterval(_patchTS, 50);
  
  // 15. Listen for postMessage from CF iframe
  window.addEventListener('message', function(ev) {
    if (!ev || !ev.data) return;
    var d = ev.data;
    if (typeof d === 'object' && d !== null) {
      if (d.token && typeof d.source === 'string' && d.source.indexOf('turnstile') >= 0) {
        window.__ts_token = d.token;
      }
    }
    if (typeof d === 'string') {
      try {
        var o = JSON.parse(d);
        if (o && o.token && o.source && o.source.indexOf('turnstile') >= 0) {
          window.__ts_token = o.token;
        }
      } catch(e) {}
    }
  }, false);
  
  console.log('[stealth] injected via addScriptToEvaluateOnNewDocument');
})();
"""

JS_TOKEN_CHECK = "window.__ts_token||document.querySelector('[name=cf-turnstile-response]')&&document.querySelector('[name=cf-turnstile-response]').value||''"
JS_INPUT_COUNT = "document.querySelectorAll('input').length"
JS_BTN_COUNT   = "document.querySelectorAll('button').length"
JS_GET_URL     = "location.href"
JS_GET_BODY    = "document.body.innerText"

def js_set_field(fid, fval):
    jfid  = json.dumps(fid); jfval = json.dumps(fval)
    return (
        "var _el=document.getElementById("+jfid+");"
        "var _r='notfound';"
        "if(_el){"
        "var _s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;"
        "_s.call(_el,"+jfval+");"
        "_el.dispatchEvent(new Event('input',{bubbles:true}));"
        "_el.dispatchEvent(new Event('change',{bubbles:true}));"
        "_r='ok';}"
        "_r"
    )
def js_btn_text(i):  return f"document.querySelectorAll('button')[{i}]&&document.querySelectorAll('button')[{i}].textContent||''"
def js_btn_click(i): return f"document.querySelectorAll('button')[{i}]&&document.querySelectorAll('button')[{i}].click()"

async def register():
    t0 = time.time()

    claim = vps_get("/emails/pop")
    if claim.get("error") or not claim.get("email"):
        RESULT["error"] = f"no email: {claim}"; return
    email    = claim["email"]
    username = gen_username(claim.get("username", email.split("@")[0]))
    password = gen_password()
    RESULT.update({"email":email,"username":username,"password":password})
    _log(f"email={email} user={username}")

    opts = ChromiumOptions()
    opts.binary_location = CHROMIUM_BIN
    opts.headless = True
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--lang=en-US")
    opts.add_argument("--headless=new")
    opts.add_argument(f"--user-agent={REAL_UA}")
    opts.add_argument("--enable-webgl")
    opts.add_argument("--ignore-gpu-blocklist")

    async with Chrome(options=opts) as browser:
        tab = await browser.start()

        # Inject EARLY stealth via Page.addScriptToEvaluateOnNewDocument
        conn = getattr(tab, "_connection_handler", None)
        if conn and hasattr(conn, "execute_command"):
            try:
                result = await conn.execute_command({
                    "method": "Page.addScriptToEvaluateOnNewDocument",
                    "params": {"source": STEALTH_EARLY}
                })
                _log(f"STEALTH v5 EARLY INJECTED: {result}")
            except Exception as e:
                _log(f"early inject warn: {str(e)[:80]}")

        _log(f"navigating to {SIGNUP_URL}")
        try:
            await tab.go_to(SIGNUP_URL, timeout=60)
        except Exception as e:
            _log(f"goto warn: {str(e)[:60]}")

        # Verify stealth worked
        wd   = await _js(tab, "navigator.webdriver")
        ua   = await _js(tab, "navigator.userAgent")
        wgl  = await _js(tab, "(function(){try{var c=document.createElement('canvas');var gl=c.getContext('webgl');return gl.getExtension('WEBGL_debug_renderer_info')&&gl.getParameter(37446)||'no_ext';}catch(e){return 'err:'+e;}})()")
        _log(f"webdriver={wd} ua={str(ua or '')[:50]} webgl_renderer={wgl}")

        # Wait for form
        for _i in range(30):
            _n = await _js(tab, JS_INPUT_COUNT)
            try:
                if _n is not None and int(str(_n)) >= 3:
                    _log(f"form ready: {_n} inputs at {_i}s")
                    break
            except: pass
            await asyncio.sleep(1)

        # Enable auto-solve
        await tab.enable_auto_solve_cloudflare_captcha()
        _log("enable_auto_solve_cloudflare_captcha() called")

        # Re-inject stealth on current page context (belt+suspenders)
        await tab.execute_script(STEALTH_EARLY)

        # Poll 90s for token
        token = None
        for _i in range(90):
            await asyncio.sleep(1)
            tok = await _js(tab, JS_TOKEN_CHECK)
            if tok and len(str(tok)) > 20:
                token = str(tok)
                _log(f"TOKEN at t={time.time()-t0:.1f}s len={len(token)}")
                break
            if _i % 15 == 0 and _i > 0:
                _log(f"t={_i}s no_token yet")
                _txt = await _js(tab, JS_GET_BODY) or ""
                if "Too many" in _txt: RESULT["error"] = "RATE_LIMITED"; return

        if not token:
            RESULT["error"] = "no_token_90s"
            _log("FAIL: no token after 90s"); return

        _log(f"token len={len(token)}, filling form...")
        for fid, fval in [("username",username),("email",email),
                           ("password",password),("confirmPassword",password)]:
            r = await _js(tab, js_set_field(fid, fval))
            _log(f"field {fid}: {r}")

        await asyncio.sleep(1)
        n_btn = await _js(tab, JS_BTN_COUNT) or 0
        sub_r = "none"
        for _bi in range(int(str(n_btn))):
            _bt = await _js(tab, js_btn_text(_bi))
            if _bt and "create" in str(_bt).lower():
                await _js(tab, js_btn_click(_bi)); sub_r = f"clicked:{str(_bt).strip()[:30]}"; break
        if sub_r == "none" and int(str(n_btn)) > 0:
            await _js(tab, js_btn_click(0)); sub_r = "fallback:btn[0]"
        _log(f"submit: {sub_r}")

        dashboard = False
        for _i in range(90):
            await asyncio.sleep(1)
            _url = await _js(tab, JS_GET_URL)
            if "dashboard" in str(_url or ""):
                _log(f"DASHBOARD t={time.time()-t0:.1f}s"); dashboard = True; break
            if _i in (5, 15, 30, 60):
                _txt = await _js(tab, JS_GET_BODY) or ""
                _log(f"t={_i}s url={str(_url or '')[:60]}")
                if "Too many" in _txt: RESULT["error"] = "RATE_LIMITED_submit"; return
                if "already" in _txt.lower(): RESULT["error"] = "duplicate"; return

        if not dashboard:
            _url = await _js(tab, JS_GET_URL); _txt = await _js(tab, JS_GET_BODY) or ""
            RESULT["error"] = f"no_dashboard url={_url} text={str(_txt)[:150]}"
            _log(f"FAIL: {RESULT['error'][:100]}"); return

        await asyncio.sleep(1)
        xhr_js = (
            "var _r='';"
            "var _x=new XMLHttpRequest();"
            "try{_x.open('GET','/api/me',false);_x.withCredentials=true;_x.send();_r=_x.responseText;}"
            "catch(_e){_r='err:'+_e;}"
            "_r"
        )
        me_str = await _js(tab, xhr_js)
        _log(f"/api/me: {str(me_str or '')[:200]}")
        api_key = None
        if me_str and not str(me_str).startswith("err:"):
            try:
                me = json.loads(me_str)
                api_key = me.get("api_key") or me.get("apiKey") or me.get("key")
            except: pass

        if api_key:
            push = vps_post("/accounts/push",{"username":username,"email":email,
                "password":password,"api_key":api_key,"sandbox":SANDBOX_LABEL})
            RESULT.update({"success":True,"api_key":api_key,"elapsed":round(time.time()-t0,1)})
            _log(f"SUCCESS key={api_key[:28]}... push={push}")
        else:
            RESULT["error"] = "dashboard_but_no_api_key"
            _log(f"PARTIAL: {RESULT['error']}")

asyncio.get_event_loop().run_until_complete(register())
print("RESULT:"+json.dumps(RESULT), flush=True)
