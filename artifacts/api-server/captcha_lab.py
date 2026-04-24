#!/usr/bin/env python3
"""
captcha_lab.py — CAPTCHA 绕过方法实验室
用法：python3 captcha_lab.py [--method accessibility|token|audio-stt|enter] [--headless false]

此脚本独立于主注册流程，专门用于测试不同 CAPTCHA 绕过策略。
不会注册真实账号，不写数据库。失败日志保存到 /tmp/captcha_lab_*.png。
"""
import argparse, asyncio, sys, os, time, subprocess, glob

# ── 参数 ──────────────────────────────────────────────────────────────────────
ap = argparse.ArgumentParser()
ap.add_argument("--method", default="accessibility",
                choices=["accessibility", "audio-stt", "enter", "all"])
ap.add_argument("--proxy",    default="")
ap.add_argument("--headless", default="true")
ap.add_argument("--wait",     type=int, default=11)
args = ap.parse_args()

HEADLESS  = args.headless.lower() in ("true", "1", "yes")
PROXY     = args.proxy
BOT_WAIT  = args.wait
TARGET    = "https://signup.live.com/signup?mkt=zh-CN&lic=1"
REPORT    = {}

print(f"[lab] 方法={args.method}  headless={HEADLESS}  proxy={PROXY or '无'}", flush=True)

# ── 找 ffmpeg ─────────────────────────────────────────────────────────────────
def find_ffmpeg():
    for path in ["/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg"]:
        if os.path.isfile(path):
            return path
    # Nix store
    hits = glob.glob("/nix/store/*ffmpeg*/bin/ffmpeg")
    if hits:
        return hits[0]
    r = subprocess.run(["which", "ffmpeg"], capture_output=True, text=True, timeout=5)
    fp = r.stdout.strip()
    return fp if fp and os.path.isfile(fp) else None

FFMPEG = find_ffmpeg()
print(f"[lab] ffmpeg: {FFMPEG or '未找到'}", flush=True)

# ── 测试方法1：三步无障碍点击（LainsNL 方法）─────────────────────────────────
def test_accessibility(page):
    print("[lab] ── 方法1: 三步无障碍点击 ──", flush=True)
    try:
        page.wait_for_selector('iframe[title="验证质询"]', timeout=15000)
    except Exception:
        print("[lab] ✅ 无 CAPTCHA iframe，可能已通过", flush=True)
        return True, "no_captcha"

    print("[lab] 发现 CAPTCHA iframe，开始无障碍挑战…", flush=True)
    page.wait_for_timeout(3000)

    LABELS = ["可访问性挑战", "Accessible challenge", "Accessibility challenge", "Audio challenge"]
    INNER  = ['iframe[style*="display: block"]', 'iframe[style*="display:block"]',
              'iframe[tabindex="0"]', 'iframe[id*="game"]', 'iframe:first-child', 'iframe']

    frame1 = page.frame_locator('iframe[title="验证质询"]')
    frame2 = None

    # 等按钮可用（最多20s）
    for _ in range(20):
        for fr in page.frames:
            try:
                info = fr.evaluate("""() => {
                    const btn = document.querySelector(
                        '[aria-label="可访问性挑战"],[aria-label="Accessible challenge"],[aria-label="Accessibility challenge"]');
                    return btn ? {disabled: btn.getAttribute('aria-disabled')} : null;
                }""")
                if info and info.get('disabled') != 'true':
                    print(f"[lab] 无障碍按钮已启用 frame={fr.url[:40]}", flush=True)
                    break
            except Exception:
                pass
        else:
            page.wait_for_timeout(1000)
            continue
        break

    # 找内层 frame
    for sel in INNER:
        try:
            candidate = frame1.frame_locator(sel)
            for lbl in LABELS:
                if candidate.locator(f'[aria-label="{lbl}"]').count() > 0:
                    frame2 = candidate
                    print(f"[lab] 内层 frame: {sel}", flush=True)
                    break
            if frame2:
                break
        except Exception:
            pass

    if not frame2:
        print("[lab] ⚠ 内层 frame 未找到", flush=True)
        return False, "inner_frame_not_found"

    # 第一次点击
    clicked = False
    for lbl in LABELS:
        try:
            btn = frame2.locator(f'[aria-label="{lbl}"]')
            if btn.count() > 0:
                btn.first.click(timeout=5000)
                print(f"[lab] ✅ 第一次点击: [{lbl}]", flush=True)
                clicked = True
                break
        except Exception as e:
            print(f"[lab]   点击 [{lbl}] 失败: {e}", flush=True)

    if not clicked:
        return False, "first_click_failed"

    page.wait_for_timeout(2000)

    # 第二次点击（同一按钮，新 frame 内）
    for lbl in LABELS:
        try:
            btn2 = frame2.locator(f'[aria-label="{lbl}"]')
            if btn2.count() > 0:
                btn2.first.click(timeout=5000)
                print(f"[lab] ✅ 第二次点击: [{lbl}]", flush=True)
                break
        except Exception:
            pass

    page.wait_for_timeout(1500)

    # 再次按下
    try:
        press_again = frame2.locator('[aria-label="再次按下"]')
        if press_again.count() == 0:
            press_again = page.locator('[aria-label="再次按下"]')
        if press_again.count() > 0:
            press_again.first.click(timeout=8000)
            print("[lab] ✅ 再次按下 已点击！", flush=True)
        else:
            print("[lab] ⚠ 再次按下 按钮未找到", flush=True)
    except Exception as e:
        print(f"[lab] ⚠ 再次按下 异常: {e}", flush=True)

    page.wait_for_timeout(5000)
    page.screenshot(path="/tmp/captcha_lab_after_a11y.png")
    print("[lab] 截图: /tmp/captcha_lab_after_a11y.png", flush=True)

    # 判断是否通过
    try:
        page.wait_for_selector('iframe[title="验证质询"]', timeout=3000)
        print("[lab] ❌ CAPTCHA 仍然存在（需要音频解题）", flush=True)
        return False, "captcha_still_present_need_audio"
    except Exception:
        print("[lab] ✅ CAPTCHA 已消失！三步点击法通过", flush=True)
        return True, "passed"

# ── 测试方法2：音频 STT（Google 免费）──────────────────────────────────────────
def test_audio_stt(page):
    print("[lab] ── 方法2: 音频挑战 + Google STT ──", flush=True)
    # 先执行三步点击（进入音频模式）
    ok, reason = test_accessibility(page)
    if ok:
        return True, reason
    if reason != "captcha_still_present_need_audio":
        return False, reason

    print("[lab] 音频模式已进入，搜索音频URL…", flush=True)
    page.wait_for_timeout(5000)

    audio_url = None
    audio_frame = None
    for fr in page.frames:
        try:
            info = fr.evaluate("""() => {
                const a = document.querySelector('audio[src],audio source');
                return a ? (a.src || a.getAttribute('src') || '') : '';
            }""")
            if info:
                audio_url = info
                audio_frame = fr
                print(f"[lab] 找到音频URL: {audio_url[:80]}", flush=True)
                break
        except Exception:
            pass

    if not audio_url:
        print("[lab] ❌ 未找到音频元素", flush=True)
        return False, "no_audio_element"

    # 下载音频
    import tempfile, urllib.request, base64
    tmp_fd, tmp_mp3 = tempfile.mkstemp(suffix=".mp3")
    os.close(tmp_fd)
    try:
        if audio_url.startswith("blob:"):
            data_b64 = audio_frame.evaluate(f"""async () => {{
                const resp = await fetch({repr(audio_url)});
                const buf = await resp.arrayBuffer();
                const bytes = new Uint8Array(buf);
                let s = ''; for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
                return btoa(s);
            }}""")
            with open(tmp_mp3, 'wb') as f:
                f.write(base64.b64decode(data_b64))
        else:
            req = urllib.request.Request(audio_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                with open(tmp_mp3, 'wb') as f:
                    f.write(resp.read())
        print(f"[lab] 音频下载成功 ({os.path.getsize(tmp_mp3)} bytes)", flush=True)
    except Exception as e:
        print(f"[lab] ❌ 音频下载失败: {e}", flush=True)
        return False, f"audio_download_failed: {e}"

    # 转换为 wav
    tmp_wav = tmp_mp3 + ".wav"
    if FFMPEG:
        subprocess.run([FFMPEG, "-y", "-i", tmp_mp3, "-ar", "16000", "-ac", "1",
                        "-acodec", "pcm_s16le", tmp_wav], capture_output=True, timeout=20)
        print(f"[lab] ffmpeg 转换: {tmp_wav}", flush=True)
    else:
        tmp_wav = tmp_mp3

    # Google STT
    transcript = ""
    try:
        import speech_recognition as sr
        recognizer = sr.Recognizer()
        with sr.AudioFile(tmp_wav) as src:
            audio_data = recognizer.record(src)
        transcript = recognizer.recognize_google(audio_data, language="en-US")
        print(f"[lab] ✅ STT 识别: '{transcript}'", flush=True)
    except Exception as e:
        print(f"[lab] ❌ STT 失败: {e}", flush=True)
        return False, f"stt_failed: {e}"

    if not transcript:
        return False, "empty_transcript"

    # 提交答案
    try:
        submitted = audio_frame.evaluate(f"""() => {{
            const input = document.querySelector('input[type="text"],input[type="tel"],input[placeholder]');
            if (!input) return false;
            input.value = {repr(transcript)};
            input.dispatchEvent(new Event('input', {{bubbles: true}}));
            input.dispatchEvent(new Event('change', {{bubbles: true}}));
            const btn = document.querySelector('button[type="submit"],button[class*="submit"]');
            if (btn) {{ btn.click(); return true; }}
            input.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter', bubbles: true}}));
            return true;
        }}""")
        print(f"[lab] 提交结果: {submitted}", flush=True)
    except Exception as e:
        print(f"[lab] ⚠ 提交失败: {e}", flush=True)

    page.wait_for_timeout(4000)
    try:
        page.wait_for_selector('iframe[title="验证质询"]', timeout=3000)
        print("[lab] ❌ 提交后 CAPTCHA 仍在", flush=True)
        return False, "wrong_answer"
    except Exception:
        print("[lab] ✅ CAPTCHA 已消失，音频法通过！", flush=True)
        return True, "passed"

# ── 主流程 ──────────────────────────────────────────────────────────────────
def run():
    from patchright.sync_api import sync_playwright

    launch_args = {
        "headless": HEADLESS,
        "args": ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
    }
    if PROXY:
        launch_args["proxy"] = {"server": PROXY}

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_args)
        ctx = browser.new_context(locale="zh-CN", timezone_id="America/New_York")
        page = ctx.new_page()

        print(f"[lab] 打开注册页…", flush=True)
        page.goto(TARGET, timeout=30000)
        page.wait_for_timeout(BOT_WAIT * 1000)
        page.screenshot(path="/tmp/captcha_lab_start.png")
        print("[lab] 截图: /tmp/captcha_lab_start.png", flush=True)

        result, reason = False, "not_run"

        if args.method in ("accessibility", "all"):
            result, reason = test_accessibility(page)
            REPORT["accessibility"] = (result, reason)
            print(f"[lab] 无障碍方法: {'✅ 通过' if result else '❌ 失败'} ({reason})", flush=True)

        if args.method in ("audio-stt", "all") and not result:
            # 重新打开页面（避免上次点击影响）
            if args.method == "all":
                page.goto(TARGET, timeout=30000)
                page.wait_for_timeout(BOT_WAIT * 1000)
            result, reason = test_audio_stt(page)
            REPORT["audio-stt"] = (result, reason)
            print(f"[lab] 音频STT方法: {'✅ 通过' if result else '❌ 失败'} ({reason})", flush=True)

        page.screenshot(path="/tmp/captcha_lab_end.png")
        print("[lab] 最终截图: /tmp/captcha_lab_end.png", flush=True)
        browser.close()

    print("\n══ 实验结果汇总 ══", flush=True)
    for method, (ok, rsn) in REPORT.items():
        print(f"  {method}: {'✅' if ok else '❌'} — {rsn}", flush=True)

    return 0 if any(ok for ok, _ in REPORT.values()) else 1

if __name__ == "__main__":
    sys.exit(run())
