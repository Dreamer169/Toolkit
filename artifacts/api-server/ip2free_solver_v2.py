#!/usr/bin/env python3
"""
ip2free 验证码 Solver v2.0
改进:
  1. ddddocr beta 模型 (更准确)
  2. 同时尝试 std + beta 两个模型
  3. 完整 3-步流程: finishTask → checkCaptcha → finishTask(成功)
  4. 正确的 React modal 提交 (browser session)
  5. 精确监听 checkCaptcha 响应来判断是否成功
"""
import sys, json, time, os, base64, io
sys.path.insert(0, "/root/Toolkit/artifacts/api-server")
from patchright.sync_api import sync_playwright
import warnings; warnings.filterwarnings("ignore")

SS_DIR = "/tmp/ip2free_v2_ss"
LOG_FILE = "/tmp/ip2free_v2.log"
os.makedirs(SS_DIR, exist_ok=True)
LF = open(LOG_FILE, "w", buffering=1)
def log(msg):
    ts = time.strftime("[%H:%M:%S]")
    line = f"{ts} {msg}"
    print(line, flush=True)
    LF.write(line + "\n")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.6778.85 Safari/537.36"

# All 5 ip2free accounts
ACCOUNTS = [
    ("alewisazs@outlook.com",       "XYO8nJy3%MGZjm%E", 10839),
    ("emily_gomez98@outlook.com",    "inAyy$X87Uj^",     10835),
    ("sophiagray574@outlook.com",    "8nQDovHvbR@%mWL$", 10836),
    ("e.lewis904@outlook.com",       "Aa123456",          10837),
    ("rylan_rivera98@outlook.com",   "AWgpis7xb0",        10838),
]

# ── OCR ──────────────────────────────────────────────────────────────────────
import ddddocr as _ddddocr_mod
_ocr_std  = _ddddocr_mod.DdddOcr(show_ad=False)
try:
    _ocr_beta = _ddddocr_mod.DdddOcr(show_ad=False, beta=True)
except Exception as _e:
    log(f"[warn] beta model unavailable: {_e}")
    _ocr_beta = None

def ocr_all(png_bytes: bytes) -> list[str]:
    """Return list of candidate OCR results (deduped, non-empty)"""
    results = []
    for label, ocr in [("std", _ocr_std), ("beta", _ocr_beta)]:
        if ocr is None:
            continue
        try:
            txt = ocr.classification(png_bytes)
            txt = txt.strip() if txt else ""
            log(f"    OCR {label}: {txt!r}")
            if txt and txt not in results:
                results.append(txt)
        except Exception as e:
            log(f"    OCR {label} err: {e}")
    return results

def blob_to_png(page, blob_url: str) -> bytes | None:
    """Fetch blob: URL from browser, return raw PNG bytes"""
    try:
        result = page.evaluate("""
        async (blobUrl) => {
            try {
                const resp = await fetch(blobUrl);
                const ab = await resp.arrayBuffer();
                const bytes = new Uint8Array(ab);
                if (bytes.length === 0) return null;
                let b64 = '';
                const chunk = 8192;
                for (let i = 0; i < bytes.length; i += chunk) {
                    b64 += btoa(String.fromCharCode(...bytes.subarray(i, i+chunk)));
                }
                return b64;
            } catch(e) {
                return null;
            }
        }
        """, blob_url)
        if result:
            raw = base64.b64decode(result)
            return raw if len(raw) > 50 else None
    except Exception as e:
        log(f"    blob_to_png err: {e}")
    return None

# ── Main solver ───────────────────────────────────────────────────────────────
def solve_account(email: str, pw: str, port: int) -> dict:
    log(f"\n{'='*58}")
    log(f"ACCOUNT: {email}  port={port}")
    log(f"{'='*58}")

    LAST_API: dict[str, str] = {}
    CAPTCHA_OK = [False]   # mutable flag

    with sync_playwright() as p:
        br = p.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"],
            proxy={"server": f"socks5://127.0.0.1:{port}"},
        )
        ctx = br.new_context(
            locale="zh-CN", timezone_id="Asia/Shanghai",
            user_agent=UA, java_script_enabled=True,
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator,'platform',{get:()=>'Win32'});"
        )

        def on_response(resp):
            if "api.ip2free.com/api" in resp.url:
                name = resp.url.split("/api/")[-1].split("?")[0]
                try:
                    b = resp.body()
                    if b and b[:4] == b"\x89PNG":
                        log(f"  ← {name} PNG {len(b)} bytes")
                        with open(f"{SS_DIR}/cap_{email.split('@')[0]}.png","wb") as f:
                            f.write(b)
                    else:
                        txt = resp.text()
                        LAST_API[name] = txt[:800]
                        if name not in ("ad/getList","account/profile","wallet/balance",
                                        "account/taskList","ip/freeList","coupon/my"):
                            log(f"  ← {name} ({len(b)}b): {txt[:150]}")
                        # Mark captcha success
                        if name == "account/checkCaptcha" and '"code":0' in txt:
                            CAPTCHA_OK[0] = True
                            log(f"  ✅✅ checkCaptcha VALID!")
                except Exception:
                    pass

        ctx.on("response", on_response)
        page = ctx.new_page()

        try:
            # ── Login ──────────────────────────────────────────────────────
            page.goto("https://www.ip2free.com/cn/login",
                      wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(3000)
            page.wait_for_selector("#email", timeout=12000)

            el = page.locator("#email"); el.click(); page.wait_for_timeout(150)
            for ch in email: el.press_sequentially(ch, delay=50)
            pl = page.locator("#password"); pl.click(); page.wait_for_timeout(150)
            for ch in pw: pl.press_sequentially(ch, delay=50)
            page.wait_for_timeout(300)
            page.evaluate(
                "(function(){var cb=document.querySelector('input[type=\"checkbox\"]');"
                "if(cb&&!cb.checked)cb.click();})()"
            )
            page.wait_for_timeout(400)
            page.evaluate(
                "(function(){var b=document.querySelector('button.MuiButton-sizeLarge');"
                "if(b)b.click();})()"
            )
            page.wait_for_timeout(8000)
            if "/login" in page.url:
                log("  ❌ 登录失败")
                br.close()
                return {"status": "login_failed"}
            log("  ✅ 登录成功")

            # ── Activity page ──────────────────────────────────────────────
            page.goto("https://www.ip2free.com/cn/freeProxy?tab=activity",
                      wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(5000)

            for attempt in range(8):
                log(f"\n  ── attempt {attempt+1}/8 ──")
                LAST_API.clear()
                CAPTCHA_OK[0] = False

                # ── Click "立即领取" ──────────────────────────────────────
                clicked = page.evaluate(
                    "(function(){var b=Array.from(document.querySelectorAll('button'))"
                    ".filter(function(b){return b.offsetParent!==null;})"
                    ".find(function(b){return b.innerText.indexOf('\u7acb\u5373\u9886\u53d6')>=0;});"
                    "if(b){b.click();return true;}return false;})()"
                )
                if not clicked:
                    log("  button not found - checking if task already done...")
                    tl = LAST_API.get("account/taskList","")
                    if tl and '"finished_at"' in tl:
                        import re as _re
                        m = _re.search(r'"finished_at"\s*:\s*"([^"]+)"', tl)
                        if m and m.group(1) != "null":
                            log(f"  🎉 task already completed: {m.group(1)}")
                            br.close()
                            return {"status": "already_done", "finished_at": m.group(1)}
                    page.wait_for_timeout(2000)
                    continue

                page.wait_for_timeout(3500)

                # Check if task completed without captcha (rare)
                ft = LAST_API.get("account/finishTask","")
                if '"code":0' in ft:
                    log("  🎉 直接完成 (无验证码)!")
                    br.close()
                    return {"status": "success_no_captcha"}

                # ── Find captcha modal ─────────────────────────────────────
                modal_vis = page.evaluate(
                    "(function(){var d=Array.from(document.querySelectorAll('[role=\"dialog\"]'))"
                    ".filter(function(x){return x.offsetParent!==null;});return d.length;})()"
                )
                if not modal_vis:
                    log("  modal not visible, retry")
                    page.wait_for_timeout(1500)
                    continue

                # ── Get blob URL ───────────────────────────────────────────
                blob_url = page.evaluate(
                    "(function(){var d=Array.from(document.querySelectorAll('[role=\"dialog\"]'))"
                    ".filter(function(x){return x.offsetParent!==null;})[0];"
                    "if(!d) return null;"
                    "var img=d.querySelector('img.captcha-img');"
                    "if(!img) img=d.querySelector('img');"
                    "return img?img.src:null;})()"
                )
                log(f"  blob_url: {blob_url}")

                cap_candidates = []

                if blob_url and blob_url.startswith("blob:"):
                    png_bytes = blob_to_png(page, blob_url)
                    log(f"  PNG bytes: {len(png_bytes) if png_bytes else 0}")
                    if png_bytes and len(png_bytes) > 100:
                        save_path = f"{SS_DIR}/{email.split('@')[0]}_cap{attempt}.png"
                        with open(save_path, "wb") as f:
                            f.write(png_bytes)
                        cap_candidates = ocr_all(png_bytes)

                if not cap_candidates:
                    log("  no candidates, skip attempt")
                    page.evaluate(
                        "(function(){var esc=new KeyboardEvent('keydown',{key:'Escape',bubbles:true});"
                        "document.dispatchEvent(esc);})()"
                    )
                    page.wait_for_timeout(1500)
                    continue

                # ── Try each OCR candidate ────────────────────────────────
                solved = False
                for cand in cap_candidates:
                    cap_clean = cand.strip()[:12]
                    log(f"  trying captcha: {cap_clean!r}")

                    # Fill input via React-compatible event dispatch
                    filled = page.evaluate(
                        "(function(val){"
                        "var inp=document.querySelector('[role=\"dialog\"] input[name=\"captcha\"]');"
                        "if(!inp)inp=document.querySelector('[role=\"dialog\"] input[type=\"text\"]');"
                        "if(!inp)return null;"
                        "var nativeInputValueSetter=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;"
                        "nativeInputValueSetter.call(inp,val);"
                        "inp.dispatchEvent(new Event('input',{bubbles:true}));"
                        "inp.dispatchEvent(new Event('change',{bubbles:true}));"
                        "return inp.value;"
                        "})('" + cap_clean.replace("'", "\\'") + "')"
                    )
                    log(f"  filled input: {filled!r}")
                    page.wait_for_timeout(500)

                    # Click 提交 button
                    LAST_API.clear()
                    CAPTCHA_OK[0] = False
                    btn_clicked = page.evaluate("""
                    (function(){
                        var dlg=Array.from(document.querySelectorAll('[role="dialog"]'))
                                     .filter(function(x){return x.offsetParent!==null;})[0];
                        if(!dlg)return null;
                        var btns=Array.from(dlg.querySelectorAll('button'))
                                     .filter(function(b){return b.offsetParent!==null;});
                        var sub=btns.find(function(b){
                            var t=b.innerText.trim();
                            return t==='\u63d0\u4ea4'||t==='\u786e\u8ba4'||t==='OK'||t.includes('\u9a8c\u8bc1');
                        });
                        if(!sub&&btns.length>0)sub=btns[0];
                        if(sub){sub.click();return sub.innerText.trim();}
                        return null;
                    })()
                    """)
                    log(f"  submit btn: {btn_clicked!r}")
                    page.wait_for_timeout(5000)

                    # Check checkCaptcha response
                    cc_rsp = LAST_API.get("account/checkCaptcha","")
                    ft_rsp = LAST_API.get("account/finishTask","")
                    log(f"  checkCaptcha: {cc_rsp[:80]}")
                    log(f"  finishTask:   {ft_rsp[:80]}")

                    if CAPTCHA_OK[0] or '"code":0' in cc_rsp:
                        log(f"  ✅ captcha accepted: {cap_clean!r}")
                        # Wait for finishTask to also complete (React may call it automatically)
                        if '"code":0' in ft_rsp:
                            log("  🎉 finishTask also code=0!")
                            page.screenshot(path=f"{SS_DIR}/{email.split('@')[0]}_SUCCESS.png")
                            br.close()
                            return {"status": "success", "captcha": cap_clean}
                        else:
                            # Manually trigger finishTask via button or navigation
                            log("  captcha ok but finishTask not done - need to re-trigger")
                            # Close modal and re-click "立即领取"
                            page.evaluate(
                                "(function(){var esc=new KeyboardEvent('keydown',"
                                "{key:'Escape',bubbles:true});document.dispatchEvent(esc);})()"
                            )
                            page.wait_for_timeout(1500)
                            LAST_API.clear()
                            clicked2 = page.evaluate(
                                "(function(){var b=Array.from(document.querySelectorAll('button'))"
                                ".filter(function(b){return b.offsetParent!==null;})"
                                ".find(function(b){return b.innerText.indexOf('\u7acb\u5373\u9886\u53d6')>=0;});"
                                "if(b){b.click();return true;}return false;})()"
                            )
                            page.wait_for_timeout(4000)
                            ft2 = LAST_API.get("account/finishTask","")
                            log(f"  re-click finishTask: {ft2[:120]}")
                            if '"code":0' in ft2:
                                log("  🎉 SUCCESS on re-trigger!")
                                page.screenshot(path=f"{SS_DIR}/{email.split('@')[0]}_SUCCESS.png")
                                br.close()
                                return {"status": "success", "captcha": cap_clean}
                            else:
                                log(f"  still failed: {ft2[:80]}")
                                solved = True  # captcha was right, but other issue
                                break
                    else:
                        # Captcha wrong - modal may reset with new captcha
                        log(f"  ❌ captcha wrong: {cc_rsp[:60]}")
                        # Check if modal is still open (new captcha loaded)
                        page.wait_for_timeout(1000)
                        still_open = page.evaluate(
                            "(function(){return Array.from(document.querySelectorAll('[role=\"dialog\"]'))"
                            ".filter(function(x){return x.offsetParent!==null;}).length;})()"
                        )
                        if still_open:
                            log("  modal still open with new captcha, retrying in same attempt")
                            # Get new blob URL
                            new_blob = page.evaluate(
                                "(function(){var d=Array.from(document.querySelectorAll('[role=\"dialog\"]'))"
                                ".filter(function(x){return x.offsetParent!==null;})[0];"
                                "if(!d)return null;"
                                "var img=d.querySelector('img.captcha-img');"
                                "if(!img)img=d.querySelector('img');"
                                "return img?img.src:null;})()"
                            )
                            if new_blob and new_blob != blob_url:
                                log(f"  new blob: {new_blob}")
                                # OCR new captcha and add to candidates
                                new_png = blob_to_png(page, new_blob)
                                if new_png and len(new_png) > 100:
                                    new_cands = ocr_all(new_png)
                                    log(f"  new candidates: {new_cands}")
                                    cap_candidates = new_cands
                            break  # re-do outer loop

                if solved:
                    break

                # Close modal, wait a bit before retry
                page.evaluate(
                    "(function(){var esc=new KeyboardEvent('keydown',{key:'Escape',bubbles:true});"
                    "document.dispatchEvent(esc);})()"
                )
                page.wait_for_timeout(2000)

        except Exception as e:
            log(f"  ERROR: {e}")
            import traceback; traceback.print_exc()
        finally:
            try:
                page.screenshot(path=f"{SS_DIR}/{email.split('@')[0]}_final.png")
            except Exception:
                pass
            br.close()

    return {"status": "failed"}


def main():
    log("=" * 60)
    log("ip2free Solver v2.0  (ddddocr beta + browser submit)")
    log("=" * 60)

    results = {}
    for email, pw, port in ACCOUNTS:
        # Quick check: captcha available?
        import requests as _req, json as _json
        import urllib3; urllib3.disable_warnings()
        H = {
            "User-Agent": UA, "Accept": "*/*", "Accept-Language": "zh-CN",
            "Origin": "https://www.ip2free.com",
            "Referer": "https://www.ip2free.com/",
            "domain": "www.ip2free.com", "lang": "cn", "webname": "IP2FREE",
            "affid": "", "invitecode": "", "serviceid": "",
        }
        try:
            s = _req.Session(); s.verify = False; s.headers.update(H)
            s.proxies = {"http": f"socks5h://127.0.0.1:{port}",
                         "https": f"socks5h://127.0.0.1:{port}"}
            lr = s.post("https://api.ip2free.com/api/account/login?",
                data=_json.dumps({"email": email, "password": pw}),
                headers={"Content-Type": "text/plain;charset=UTF-8"}, timeout=15)
            tok = lr.json().get("data", {}).get("token")
            if tok:
                s.headers["x-token"] = tok
                cap_r = s.get("https://api.ip2free.com/api/account/captcha?", timeout=10)
                cap_sz = len(cap_r.content)
            else:
                cap_sz = 0
        except Exception as e:
            cap_sz = -1
            log(f"  pre-check err {email}: {e}")

        log(f"\nPRE-CHECK {email}: captcha={cap_sz} bytes")
        if cap_sz <= 0:
            log(f"  → SKIP (captcha still blocked)")
            results[email] = {"status": "skipped_rate_limited"}
            continue

        r = solve_account(email, pw, port)
        results[email] = r
        time.sleep(3)

    log("\n" + "=" * 60)
    log("FINAL RESULTS:")
    for email, r in results.items():
        log(f"  {r.get('status','?'):25s} {email}")

    with open("/tmp/ip2free_v2_result.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    log("\nSaved /tmp/ip2free_v2_result.json")
    LF.close()


main()
