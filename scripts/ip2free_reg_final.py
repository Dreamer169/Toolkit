#!/usr/bin/env python3
"""
ip2free 注册 final — Playwright + 有效 RESI 端口 (禁止直连)
"""
import sys, os, json, time, subprocess
sys.path.insert(0, "/root/Toolkit/scripts")

import ddddocr as _ddddocr
_ocr = _ddddocr.DdddOcr(show_ad=False)
try: _ocr2 = _ddddocr.DdddOcr(show_ad=False, beta=True)
except: _ocr2 = None

INVITE   = "pkGsSIjell"
BASE_API = "https://api.ip2free.com"
UA       = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.6778.85 Safari/537.36"

# 已创建的 wshu.net 邮箱 + 准备好的密码
ACCOUNTS = [
    {"email": "5pygn9r8bhlie7@wshu.net", "password": "JA%o#hCmBtA4$t", "port": 10851},
    {"email": "fd46qce8g3fm5m@wshu.net", "password": "AzPOjqy!htMXS8", "port": 10853},
    {"email": "bjd6c2ayft0zr1@wshu.net", "password": "Y23AbP%eR7Tey0", "port": 10854},
]

def ocr_png(png_bytes, label=""):
    results = []
    for lbl, ocr in [("std", _ocr), ("beta", _ocr2)]:
        if ocr is None: continue
        try:
            t = ocr.classification(png_bytes).strip()
            print("    OCR[%s]%s: %r" % (lbl, label, t))
            if t and t not in results: results.append(t)
        except Exception as e:
            print("    OCR[%s] err: %s" % (lbl, e))
    return results

def reg_one(email, pw, port):
    from patchright.sync_api import sync_playwright
    API_RESP = {}
    result = {"email": email, "password": pw, "status": "failed"}

    def on_resp(resp):
        if "api.ip2free.com/api" not in resp.url:
            return
        ep = resp.url.split("/api/")[-1].split("?")[0]
        try:
            ct = resp.headers.get("content-type", "")
            body = resp.body()
            if "image" in ct or (len(body) > 50 and body[:4] == b"\x89PNG"):
                API_RESP[ep] = body
            else:
                t = resp.text()
                API_RESP[ep] = t
                if ep not in ("ad/getList",):
                    print("    <- %s: %s" % (ep, t[:120]))
        except:
            pass

    try:
        subprocess.run(["pkill", "-9", "-f", "chrome-headless"],
                       capture_output=True, timeout=3)
    except:
        pass

    print("\n=== %s  port=%s ===" % (email, port))

    bkwargs = dict(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage",
              "--disable-blink-features=AutomationControlled"],
        proxy={"server": "socks5://127.0.0.1:%d" % port},
    )

    with sync_playwright() as p:
        br = p.chromium.launch(**bkwargs)
        ctx = br.new_context(locale="zh-CN", user_agent=UA)
        ctx.on("response", on_resp)
        page = ctx.new_page()

        try:
            reg_url = "https://www.ip2free.com/cn/register?inviteCode=" + INVITE
            print("  goto " + reg_url)
            page.goto(reg_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(5000)
            slug = email.split("@")[0]
            page.screenshot(path="/tmp/reg_%s_loaded.png" % slug)
            print("  url=" + page.url[:80])

            # Fill email
            for sel in ["#email", "input[type=email]", "input[name=email]"]:
                try:
                    loc = page.locator(sel)
                    if loc.count():
                        loc.first.click()
                        page.wait_for_timeout(300)
                        loc.first.fill(email)
                        print("  filled email via " + sel)
                        break
                except:
                    continue

            # Fill password
            for sel in ["#password", "input[type=password]", "input[name=password]"]:
                try:
                    loc = page.locator(sel)
                    if loc.count():
                        loc.first.click()
                        page.wait_for_timeout(200)
                        loc.first.fill(pw)
                        break
                except:
                    continue

            # Confirm password
            for sel in ["#confirmPassword", "input[name=confirmPassword]",
                        "input[placeholder*=确认]", "input[placeholder*=Confirm]"]:
                try:
                    loc = page.locator(sel)
                    if loc.count():
                        loc.first.fill(pw)
                        break
                except:
                    continue

            # Invite code
            for sel in ["input[name=inviteCode]", "#inviteCode", "input[placeholder*=邀请]"]:
                try:
                    loc = page.locator(sel)
                    if loc.count() and not loc.first.input_value():
                        loc.first.fill(INVITE)
                        break
                except:
                    continue

            # Checkbox
            try:
                cb = page.locator("input[type=checkbox]")
                if cb.count() and not cb.first.is_checked():
                    cb.first.click()
            except:
                pass

            page.wait_for_timeout(800)
            page.screenshot(path="/tmp/reg_%s_filled.png" % slug)

            # Submit loop
            for attempt in range(10):
                API_RESP.clear()

                for sel in ["button:has-text('注册')", "button:has-text('Register')",
                             "button[type=submit]", ".MuiButton-sizeLarge"]:
                    try:
                        loc = page.locator(sel)
                        if loc.count():
                            loc.first.click(timeout=3000)
                            print("  [a%d] clicked %s" % (attempt, sel))
                            break
                    except:
                        continue

                page.wait_for_timeout(4500)

                # Check API response
                reg_raw = API_RESP.get("account/register", "")
                if reg_raw:
                    try:
                        d = json.loads(reg_raw)
                        tok  = d.get("data", {}).get("token")
                        code = d.get("code", -1)
                        msg  = d.get("msg", "")
                        print("  [a%d] register: code=%s msg=%r" % (attempt, code, msg))
                        if tok or code == 0:
                            result.update({"status": "success", "token": tok})
                            break
                        if "已注册" in msg or "already" in msg.lower():
                            result["status"] = "already_registered"
                            break
                    except:
                        pass

                # Check redirect
                cur = page.url
                if "/login" in cur or "/dashboard" in cur or "/freeProxy" in cur:
                    print("  redirect OK: " + cur)
                    result["status"] = "success"
                    break

                # Captcha
                cap_png = API_RESP.get("account/captcha")
                if isinstance(cap_png, bytes) and len(cap_png) > 100:
                    print("  captcha %db" % len(cap_png))
                    with open("/tmp/reg_cap_%s_a%d.png" % (slug, attempt), "wb") as f:
                        f.write(cap_png)
                    cands = ocr_png(cap_png, "_a%d" % attempt)
                    if cands:
                        for isel in ["input[name=captcha]", "input[placeholder*=验]",
                                     "[role=dialog] input[type=text]"]:
                            try:
                                loc = page.locator(isel)
                                if loc.count():
                                    loc.first.fill(cands[0][:10])
                                    page.wait_for_timeout(400)
                                    for bsel in ["[role=dialog] button:has-text('确')",
                                                 "[role=dialog] button:has-text('提')",
                                                 "[role=dialog] button[type=submit]"]:
                                        try:
                                            page.locator(bsel).first.click(timeout=2000)
                                            break
                                        except:
                                            continue
                                    break
                            except:
                                continue
                        page.wait_for_timeout(3000)
                else:
                    page.screenshot(path="/tmp/reg_%s_a%d.png" % (slug, attempt))
                    if attempt >= 3:
                        # inspect page HTML for clues
                        html = page.content()[:2000]
                        print("  page snippet: " + html.replace("\n","")[:400])
                        break

        except Exception as e:
            import traceback
            print("  ERROR: " + str(e))
            traceback.print_exc()
        finally:
            try:
                page.screenshot(path="/tmp/reg_%s_final.png" % slug)
            except:
                pass
            br.close()

    return result


def main():
    print("=== ip2free 注册 (RESI only, 禁止直连) ===")
    results = []
    for acct in ACCOUNTS:
        r = reg_one(acct["email"], acct["password"], acct["port"])
        results.append(r)
        time.sleep(5)

    print("\n=== 最终结果 ===")
    ok = 0
    for r in results:
        st = r.get("status", "?")
        if st == "success":
            ok += 1
        print("  [%s] %s  pw=%s" % (st, r["email"], r["password"]))
    print("成功: %d/%d" % (ok, len(results)))

    with open("/tmp/ip2free_new_accounts.json", "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print("保存: /tmp/ip2free_new_accounts.json")

    # 如果有新账号成功，追加到 ip2free_get_proxies.py 的 ACCOUNTS 列表
    new_ok = [r for r in results if r.get("status") == "success"]
    if new_ok:
        try:
            gp = open("/root/Toolkit/scripts/ip2free_get_proxies.py").read()
            for r in new_ok:
                entry = '    {"email": "%s", "password": "%s"},\n' % (r["email"], r["password"])
                gp = gp.replace("    # 注册成功后追加\n", entry + "    # 注册成功后追加\n")
            open("/root/Toolkit/scripts/ip2free_get_proxies.py", "w").write(gp)
            print("已追加新账号到 ip2free_get_proxies.py")
        except Exception as e:
            print("追加失败: " + str(e))

if __name__ == "__main__":
    main()
