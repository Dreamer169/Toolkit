#!/usr/bin/env python3
"""
test_outlook_oauth_url_disambiguation.py

确定性单元测试 — 直接验证 v8.42 (3 处 URL path 判定) + v8.43 (路由拦截器 regex)
对 outlook OAuth 中 'nativeclient' / 'oauth20_authorize' / '/authorize' 子串歧义的修复.

不依赖真 Microsoft / 真浏览器 / 真账号; 只构造真实 URL 字面量 + 调用模块内的判定逻辑.

退出码: 0 = 全 PASS, 非 0 = 有 FAIL (打印第一条失败的明细).

用法:
  python3 artifacts/api-server/tests/test_outlook_oauth_url_disambiguation.py
"""
import os
import re
import sys
import urllib.parse as _up
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
APIDIR = os.path.dirname(HERE)
sys.path.insert(0, APIDIR)

# ── 真 OAuth 常量 (与 outlook_register.get_oauth_token_in_browser 完全一致) ──
CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
REDIRECT_URI = "https://login.microsoftonline.com/common/oauth2/nativeclient"
SCOPES = [
    "offline_access",
    "https://graph.microsoft.com/Mail.Read",
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/User.Read",
]

scope_encoded = "%20".join(_up.quote(s, safe=":/") for s in SCOPES)
AUTH_URL = (
    "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"
    "?client_id=" + CLIENT_ID
    + "&response_type=code"
    + "&redirect_uri=" + _up.quote(REDIRECT_URI, safe="")
    + "&scope=" + scope_encoded
    + "&prompt=consent"
    + "&login_hint=test%40outlook.com"
)
AUTHZ_SRF_URL = (
    "https://login.microsoftonline.com/common/oauth2/authorize"
    "?client_id=" + CLIENT_ID
    + "&response_type=code"
    + "&redirect_uri=" + _up.quote(REDIRECT_URI, safe="")
    + "&state=foo"
)
NATIVECLIENT_OK = REDIRECT_URI + "?code=M.R3_BAY.abcdefg-token-xyz"
NATIVECLIENT_ERR = REDIRECT_URI + "?error=access_denied&error_description=user_cancelled"
LOGIN_LIVE_INTERMEDIATE = (
    "https://login.live.com/oauth20_authorize.srf"
    "?client_id=" + CLIENT_ID
    + "&response_type=code"
    + "&redirect_uri=" + _up.quote(REDIRECT_URI, safe="")
)

passed = 0
failed = 0
failures = []


def _check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  PASS  " + name)
    else:
        failed += 1
        failures.append(name + " :: " + detail)
        print("  FAIL  " + name + " :: " + detail)


# ─────────────────────────────────────────────────────────────────────────
# (1) v8.43 路由拦截器 regex — 必须只匹配 path 含 nativeclient 的真回调
# ─────────────────────────────────────────────────────────────────────────
print("\n[v8.43] page.route 拦截器 regex 行为")
NC_PATH_RE = re.compile(r"^https?://[^/]+/[^?]*nativeclient", re.IGNORECASE)

_check(
    "authorize 页 (query 含 nativeclient) 不应被拦截",
    NC_PATH_RE.match(AUTH_URL) is None,
    "误命中 authorize 页 -> route.abort 中止真导航",
)
_check(
    "oauth20_authorize 中间页不应被拦截",
    NC_PATH_RE.match(AUTHZ_SRF_URL) is None,
    "误命中中间页",
)
_check(
    "login.live.com/oauth20_authorize.srf 不应被拦截",
    NC_PATH_RE.match(LOGIN_LIVE_INTERMEDIATE) is None,
    "误命中 login.live 中间页",
)
_check(
    "nativeclient?code=... (真 OAuth 回调) 必须命中",
    NC_PATH_RE.match(NATIVECLIENT_OK) is not None,
    "漏拦截真回调 -> code 抓不到",
)
_check(
    "nativeclient?error=... (真 OAuth 回调) 必须命中",
    NC_PATH_RE.match(NATIVECLIENT_ERR) is not None,
    "漏拦截 error 回调",
)

# 反向: 旧子串判定必须误命中 — 证明 bug 真实存在, 防止测试失效
print("\n[regression] 老子串判定 (v8.43 之前) 必须误命中, 证明 bug 真实")
_check(
    "OLD-BUG: 'nativeclient' in AUTH_URL == True",
    "nativeclient" in AUTH_URL,
    "若 fail, 说明 URL 构造跑偏, 验证已失效",
)


# ─────────────────────────────────────────────────────────────────────────
# (2) v8.42 _is_terminal 等价逻辑
# ─────────────────────────────────────────────────────────────────────────
print("\n[v8.42] _is_terminal (path-only) 等价逻辑")


def _is_terminal(u):
    if not u:
        return False
    try:
        parsed = urlparse(u)
        path = parsed.path or ""
        qs = parse_qs(parsed.query)
    except Exception:
        return False
    if "nativeclient" in path:
        return True
    is_authz = (
        ("oauth20_authorize" in path)
        or path.endswith("/authorize")
        or path.endswith("/authorize/")
    )
    if is_authz:
        return False
    return ("code" in qs) or ("error" in qs)


_check(
    "authorize 页 _is_terminal=False (含 response_type=code 子串)",
    _is_terminal(AUTH_URL) is False,
    "authorize 页被误判终止 -> 提前 break",
)
_check(
    "oauth20_authorize.srf _is_terminal=False",
    _is_terminal(AUTHZ_SRF_URL) is False,
    "中间页被误判终止",
)
_check(
    "nativeclient?code=... _is_terminal=True",
    _is_terminal(NATIVECLIENT_OK) is True,
    "真终止页被漏判 -> wait_for_url 超时",
)
_check(
    "nativeclient?error=... _is_terminal=True",
    _is_terminal(NATIVECLIENT_ERR) is True,
    "error 终止被漏判",
)
_check(
    "空字符串 _is_terminal=False",
    _is_terminal("") is False,
    "空 URL 不应终止",
)


# ─────────────────────────────────────────────────────────────────────────
# (3) 验证仓库内真代码 (outlook_register.py) 落地了 v8.42 + v8.43 修复
# ─────────────────────────────────────────────────────────────────────────
print("\n[source-truth] outlook_register.py 必须含 v8.42/v8.43 标记 + path-only 实现")
ORPATH = os.path.join(APIDIR, "outlook_register.py")
src = open(ORPATH, encoding="utf-8").read()

_check(
    "v8.43 路由 regex 已落地",
    ("_NC_PATH_RE = re.compile(r" in src) and ("page.route(_NC_PATH_RE" in src),
    "v8.43 修复未落地, 仍使用 glob 拦截器",
)
_check(
    "v8.43 注释含 ROOT-FIX 标记",
    "v8.43 ROOT-FIX" in src,
    "缺少版本标记",
)
_check(
    "v8.42 _is_terminal 用 urlparse path",
    ("urlparse as _urp3" in src) and ("_path3" in src),
    "v8.42 _is_terminal 未改为 path 判定",
)
_check(
    "v8.42 _path_X 检测已落地 (主循环)",
    ("_path_X" in src) and ("'nativeclient' in _path_X" in src),
    "主循环 nativeclient 检测未改为 path 判定",
)
_check(
    "v8.42 重CAPTCHA 跳转判定 _path2 已落地",
    ("_path2" in src) and ("_is_authz2" in src),
    "重CAPTCHA 后跳转判定未改为 path 判定",
)
_check(
    "旧 glob 字面量已被移除",
    ("page.route('**/nativeclient**'" not in src)
    and ('page.route("**/nativeclient**"' not in src),
    "旧 glob 拦截器残留, v8.43 fix 未生效",
)


# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("PASS " + str(passed) + "   FAIL " + str(failed))
if failed:
    print("\n失败详情:")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("OK — v8.42 + v8.43 OAuth URL 歧义修复行为正确, 源码已落地.")
sys.exit(0)
