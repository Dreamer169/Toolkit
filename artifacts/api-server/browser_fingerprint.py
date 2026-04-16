"""
browser_fingerprint.py — Outlook / Cursor 共享浏览器指纹生成器
==================================================================
Outlook 和 Cursor 注册使用同一套指纹策略，确保：
  - 同 IP / 同设备档案在 Cloudflare / WorkOS Radar 眼里是同一个真实用户
  - Turnstile signals 字段 (canvas / WebGL / Audio / nav) 与注册历史一致

用法:
    from browser_fingerprint import gen_profile, apply_fingerprint, context_kwargs

    profile = gen_profile()                          # 生成一次，整个会话复用
    ctx = await browser.new_context(**context_kwargs(profile))
    await apply_fingerprint(ctx, profile)            # add_init_script
"""

from __future__ import annotations

import random
import re
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Tuple


# ─── 指纹素材库（与 Outlook 原版保持一致）────────────────────────────────────

CHROME_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
]

US_TIMEZONES = [
    "America/New_York", "America/Chicago", "America/Denver",
    "America/Los_Angeles", "America/Phoenix", "America/Detroit",
    "America/Indiana/Indianapolis", "America/Anchorage", "America/Boise",
]

TIMEZONE_ALIASES = {
    "America/Boston": "America/New_York",
    "America/Miami": "America/New_York",
}

# locale → 对应时区池（保证指纹内部一致性）
LOCALE_TIMEZONES: dict[str, list[str]] = {
    "zh-CN": ["Asia/Shanghai", "Asia/Chongqing", "Asia/Harbin"],
    "zh-TW": ["Asia/Taipei"],
    "en-US": US_TIMEZONES,
    "en-GB": ["Europe/London"],
    "en-AU": ["Australia/Sydney", "Australia/Melbourne"],
    "en-SG": ["Asia/Singapore"],
    "ja-JP": ["Asia/Tokyo"],
    "ko-KR": ["Asia/Seoul"],
    "fr-FR": ["Europe/Paris"],
    "de-DE": ["Europe/Berlin"],
    "es-ES": ["Europe/Madrid"],
    "pt-BR": ["America/Sao_Paulo"],
    "ru-RU": ["Europe/Moscow"],
}


def normalize_timezone_id(timezone_id: str) -> str:
    """Return an ICU/IANA timezone accepted by Playwright."""
    return TIMEZONE_ALIASES.get(timezone_id, timezone_id)


def validate_timezone_id(timezone_id: str) -> str:
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(timezone_id)
    except Exception as exc:
        raise ValueError(f"Invalid timezone_id for Playwright/ICU: {timezone_id}") from exc
    return timezone_id

# 分辨率预设（真实用户分布）
SCREEN_PRESETS: list = [
    (1920, 1080, 1),
    (1440, 900,  1),
    (1536, 864,  1),
    (2560, 1440, 2),
    (1600, 900,  1),
    (1680, 1050, 1),
    (1920, 1200, 1),
    (2560, 1080, 1),
    (1280, 1024, 1),
]

WEBGL_VENDORS = [
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1080 Ti Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Intel)",  "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (AMD)",    "ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Apple Inc.",           "Apple M1"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3070 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Intel)",  "ANGLE (Intel, Intel(R) Iris Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0, D3D11)"),
]


# ─── 指纹档案 Dataclass ───────────────────────────────────────────────────────

@dataclass
class BrowserProfile:
    # 基础身份
    machine_id:     str   = field(default_factory=lambda: str(_uuid.uuid4()))
    user_agent:     str   = ""
    is_win:         bool  = True
    platform_str:   str   = "Win32"
    ch_ver:         str   = "131"

    # 时间 / 地区
    timezone_id:    str   = "America/New_York"
    locale:         str   = "en-US"

    # 屏幕 / 视口
    screen_w:       int   = 1920
    screen_h:       int   = 1080
    dpr:            int   = 1
    viewport_w:     int   = 1880
    viewport_h:     int   = 950

    # 硬件
    hw_concurrency: int   = 8
    device_memory:  int   = 8
    battery_level:  float = 0.95

    # 指纹噪点
    canvas_noise:   int   = 1234
    webgl_vendor:   str   = "Google Inc. (NVIDIA)"
    webgl_renderer: str   = "ANGLE (NVIDIA, NVIDIA GeForce GTX 1080 Ti Direct3D11 vs_5_0 ps_5_0, D3D11)"

    # 插件（伪装成真实 Chrome）
    plugins_js: str = (
        "[{name:'Chrome PDF Plugin'},{name:'Chrome PDF Viewer'},{name:'Native Client'}]"
    )


def gen_profile(locale: str = "en-US") -> BrowserProfile:
    """
    生成一个完整的浏览器指纹档案。
    时区自动匹配 locale，保证指纹内部一致性（zh-CN → Asia/Shanghai 等）。
    整个注册会话内应复用同一份 profile，保证指纹一致性。
    """
    ua = random.choice(CHROME_UAS)
    is_win = "Windows" in ua
    m = re.search(r"Chrome/(\d+)", ua)
    ch_ver = m.group(1) if m else "131"

    sw, sh, dpr = random.choice(SCREEN_PRESETS)
    vw = sw - random.randint(0, 40)
    vh = max(900, sh - random.randint(60, 130))

    webgl_vendor, webgl_renderer = random.choice(WEBGL_VENDORS)

    # 时区跟随 locale，未知 locale 回退到 US 时区池
    tz_pool = LOCALE_TIMEZONES.get(locale, US_TIMEZONES)
    tz = validate_timezone_id(normalize_timezone_id(random.choice(tz_pool)))

    return BrowserProfile(
        user_agent=ua,
        is_win=is_win,
        platform_str="Win32" if is_win else "MacIntel",
        ch_ver=ch_ver,
        timezone_id=tz,
        locale=locale,
        screen_w=sw, screen_h=sh, dpr=dpr,
        viewport_w=vw, viewport_h=vh,
        hw_concurrency=random.choice([4, 6, 8, 12, 16]),
        device_memory=random.choice([4, 8, 16]),
        battery_level=round(random.uniform(0.60, 1.0), 2),
        canvas_noise=random.randint(1, 9999),
        webgl_vendor=webgl_vendor,
        webgl_renderer=webgl_renderer,
    )


def context_kwargs(profile: BrowserProfile) -> dict:
    """
    返回 browser.new_context(**context_kwargs(profile)) 所需的完整参数字典。
    同时设置 sec-ch-ua headers 与 UA 保持一致（Outlook / Cursor 共用）。
    """
    os_str = "Windows" if profile.is_win else "macOS"
    # Accept-Language 跟随 locale，保证与浏览器语言设置完全一致
    _ACCEPT_LANG_MAP: dict[str, str] = {
        "zh-CN": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "zh-TW": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "ja-JP": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
        "ko-KR": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "fr-FR": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "de-DE": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
        "es-ES": "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7",
        "pt-BR": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "ru-RU": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "en-GB": "en-GB,en;q=0.9,en-US;q=0.8",
        "en-AU": "en-AU,en;q=0.9,en-US;q=0.8",
        "en-SG": "en-SG,en;q=0.9,en-US;q=0.8",
    }
    accept_lang = _ACCEPT_LANG_MAP.get(profile.locale, "en-US,en;q=0.9")

    return dict(
        locale=profile.locale,
        timezone_id=validate_timezone_id(normalize_timezone_id(profile.timezone_id)),
        viewport={"width": profile.viewport_w, "height": profile.viewport_h},
        screen={"width": profile.screen_w,    "height": profile.screen_h},
        device_scale_factor=profile.dpr,
        color_scheme="light",
        user_agent=profile.user_agent,
        java_script_enabled=True,
        accept_downloads=False,
        extra_http_headers={
            "Accept-Language": accept_lang,
            "sec-ch-ua": (
                f'"Chromium";v="{profile.ch_ver}", '
                f'"Google Chrome";v="{profile.ch_ver}", '
                '"Not_A Brand";v="24"'
            ),
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": f'"{os_str}"',
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "none",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
        },
    )


def fingerprint_script(profile: BrowserProfile) -> str:
    """
    返回完整的指纹伪装 JavaScript（IIFE）。
    通过 context.add_init_script() 注入，在每个页面加载前执行。

    覆盖范围（与 Outlook 注册完全一致）:
      navigator : hardwareConcurrency / deviceMemory / platform /
                  language / languages / webdriver / plugins
      screen    : colorDepth / pixelDepth
      canvas    : toDataURL + getImageData 噪点种子
      WebGL 1+2 : UNMASKED_VENDOR / UNMASKED_RENDERER
      Audio     : getChannelData 噪点
      Battery   : 伪造充电状态 + 电量水平
      Storage   : localStorage device_id / machine_id 持久化
    """
    p = profile
    return f"""(function() {{
    // ── navigator ───────────────────────────────────────────────────────────
    Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {p.hw_concurrency} }});
    Object.defineProperty(navigator, 'deviceMemory',        {{ get: () => {p.device_memory} }});
    Object.defineProperty(navigator, 'platform',            {{ get: () => '{p.platform_str}' }});
    Object.defineProperty(navigator, 'language',            {{ get: () => '{p.locale}' }});
    Object.defineProperty(navigator, 'languages',           {{
        get: () => ['{p.locale}', '{p.locale.split("-")[0]}', 'en-US', 'en']
    }});
    Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});
    Object.defineProperty(navigator, 'plugins', {{
        get: () => {{
            const arr = {p.plugins_js};
            arr.length = arr.length;
            return arr;
        }}
    }});

    // ── screen ──────────────────────────────────────────────────────────────
    Object.defineProperty(screen, 'colorDepth', {{ get: () => 24 }});
    Object.defineProperty(screen, 'pixelDepth', {{ get: () => 24 }});

    // ── canvas 指纹噪点（seed={p.canvas_noise}）──────────────────────────────
    (function() {{
        const NOISE = {p.canvas_noise};
        const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function(type) {{
            const ctx = this.getContext('2d');
            if (ctx) {{
                try {{
                    const px = ctx.getImageData(0, 0, 1, 1);
                    px.data[0] = (px.data[0] + NOISE) % 256;
                    ctx.putImageData(px, 0, 0);
                }} catch(e) {{}}
            }}
            return origToDataURL.apply(this, arguments);
        }};
        const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
        CanvasRenderingContext2D.prototype.getImageData = function(x, y, w, h) {{
            const data = origGetImageData.apply(this, arguments);
            for (let i = 0; i < data.data.length; i += 100) {{
                data.data[i] = (data.data[i] + NOISE % 3) % 256;
            }}
            return data;
        }};
    }})();

    // ── WebGL 供应商 & 渲染器 ────────────────────────────────────────────────
    (function() {{
        const V = '{p.webgl_vendor}';
        const R = '{p.webgl_renderer}';
        function patch(proto) {{
            const orig = proto.getParameter;
            proto.getParameter = function(param) {{
                if (param === 37445) return V;
                if (param === 37446) return R;
                return orig.apply(this, arguments);
            }};
        }}
        patch(WebGLRenderingContext.prototype);
        try {{ patch(WebGL2RenderingContext.prototype); }} catch(e) {{}}
    }})();

    // ── Audio 指纹噪点 ───────────────────────────────────────────────────────
    (function() {{
        const NOISE = {p.canvas_noise} * 1e-7;
        const orig = AudioBuffer.prototype.getChannelData;
        AudioBuffer.prototype.getChannelData = function(ch) {{
            const data = orig.apply(this, arguments);
            for (let i = 0; i < data.length; i += 200) {{ data[i] += NOISE; }}
            return data;
        }};
    }})();

    // ── 电池 API（服务器无电池 → 伪造充电中）────────────────────────────────
    try {{
        navigator.getBattery = async () => ({{
            charging: true, chargingTime: 0,
            dischargingTime: Infinity, level: {p.battery_level},
            addEventListener: () => {{}}, removeEventListener: () {{}}
        }});
    }} catch(e) {{}}

    // ── 机器 ID（localStorage 持久化，模拟真实设备）──────────────────────────
    try {{
        localStorage.setItem('device_id',  '{p.machine_id}');
        localStorage.setItem('machine_id', '{p.machine_id}');
    }} catch(e) {{}}
}})();
"""


async def apply_fingerprint(context, profile: BrowserProfile) -> None:
    """注入指纹脚本到 browser context（异步 patchright / playwright）。"""
    await context.add_init_script(fingerprint_script(profile))


def apply_fingerprint_sync(context, profile: BrowserProfile) -> None:
    """注入指纹脚本到 browser context（同步 playwright sync API）。"""
    context.add_init_script(fingerprint_script(profile))


def profile_summary(profile: BrowserProfile) -> str:
    """返回单行指纹摘要字符串（用于注册日志）。"""
    return (
        f"UA=...{profile.user_agent[25:55]} "
        f"WebGL={profile.webgl_vendor[:18]} "
        f"Screen={profile.screen_w}x{profile.screen_h} "
        f"TZ={profile.timezone_id} "
        f"MachineID={profile.machine_id[:8]}..."
    )
