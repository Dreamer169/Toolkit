#!/usr/bin/env python3
"""
ip2free.com API 模块 v2.0
=========================
完整 API 文档 (经过实际浏览器/网络抓包验证)

BASE URL: https://api.ip2free.com
页面 URL: https://www.ip2free.com
  免费代理页:   /cn/freeProxy
  活动页:       /cn/freeProxy?tab=activity
  仪表盘:       /cn/dashboard
  登录页:       /cn/login

认证:
  - 登录: POST /api/account/login?  body: {"email":..., "password":...}  Content-Type: text/plain;charset=UTF-8
  - token 存在: response.data.token
  - 后续请求头: x-token: <token>

必需自定义请求头:
  x-token: <登录后token>
  domain: www.ip2free.com
  lang: cn
  webname: IP2FREE
  affid: (空)
  invitecode: (空)
  serviceid: (空)
  Origin: https://www.ip2free.com
  Referer: https://www.ip2free.com/

=== 已确认 API 端点 ===

账号相关:
  POST /api/account/login?       → 登录, 返回 token + profile
  GET  /api/account/profile      → 用户信息 (id, email, invite_code)
  GET  /api/wallet/balance       → 余额 (balance, todaySpend, todayIncome)
  GET  /api/coupon/my            → 优惠券 (coupons_unused, coupons_used, coupons_expired)

任务系统:
  GET/POST /api/account/taskList → 任务列表
    body (可选): {"page":1, "page_size":20}
    返回: data.list (任务数组), data.order_count
    
  任务数据结构:
    task_id: 6 (每日点击任务)
    id: 73636 (用户任务记录ID, 每账号不同)
    task_code: "client_click"
    task_name_cn: "每天一次丨点击就送1天不限流量住宅代理"
    is_finished: 0/1
    items: [{country_code:"US3", quantity:1}, {country_code:"US7", quantity:1}]

  任务完成流程 (三步):
    1. POST /api/account/finishTask {"id": <记录ID>}
       → code=-1 "task finish invalid" (正常, 触发验证码弹窗)
    2. GET  /api/account/captcha → PNG 图片 (120x40px, 汉字验证码)
    3. POST /api/account/checkCaptcha {"captcha": "<识别文字>"}
       → code=0 表示验证成功 → 再次调用 finishTask → 解锁代理

免费代理:
  GET/POST /api/ip/freeList → 免费代理列表
    body (可选): {"keyword":"","country":"","city":"","page":1,"page_size":100}
    返回: data.free_ip_list (最多23个)
    
  代理格式:
    {ip, port, username, password, protocol:"socks5",
     country_code, city, status:1, expires_at:null}
  
  示例:
    socks5://vpdutlfd:4s7a8tfgqz1r@104.239.107.47:5699  [US/New York]

静态代理:
  GET /api/staticProxy/list → 已购静态代理列表

其他:
  GET  /api/option/orderCategoryList → 产品分类
  GET  /api/payment/channel          → 支付渠道

=== 5个账号数据 (2026-05-03) ===

emily_gomez98@outlook.com  | pw: inAyy$X87Uj^       | id=363768 | invite=I3qD20OQyg | task_record_id=73636
sophiagray574@outlook.com  | pw: 8nQDovHvbR@%mWL$   | id=363769 | invite=9A8a27QSKi | task_record_id=73642
e.lewis904@outlook.com     | pw: Aa123456            | id=363761 | invite=x9ZmE6Y4Ia | task_record_id=73599
rylan_rivera98@outlook.com | pw: AWgpis7xb0          | id=363763 | invite=6b9e4jo42S | task_record_id=?
alewisazs@outlook.com      | pw: XYO8nJy3%MGZjm%E   | id=?      | invite=?          | task_record_id=?

=== 23个免费代理 (公共池, 所有账号共享) ===

socks5://vpdutlfd:4s7a8tfgqz1r@104.239.107.47:5699  [US/New York]
socks5://patehqmp:xws5kt55g1g8@104.164.49.38:7693   [US/Santa Clara]
socks5://nkrciqgj:zgei6c2kjrfa@64.137.10.153:5803   [DE/Frankfurt Am Main]
socks5://qzsuulnh:spdtie155rj4@198.46.161.42:5092   [US/Los Angeles]
socks5://jwllsppf:1tq8la36efsk@31.58.9.4:6077       [DE/Frankfurt Am Main]
socks5://jwllsppf:1tq8la36efsk@191.96.254.138:6185  [US/Los Angeles]
socks5://pwweyyrb:huike5u2pkn7@194.39.32.164:6461   [DE/Frankfurt Am Main]
socks5://apvobxpe:hzrc86u13f1u@23.26.53.37:6003     [JP/Tokyo]
socks5://jttenuuv:g3yjdzrq5jbz@2.57.20.2:5994       [US/Abbeville]
socks5://jwllsppf:1tq8la36efsk@23.229.19.94:8689    [US/Los Angeles]
socks5://jwllsppf:1tq8la36efsk@23.27.208.120:5830   [US/Reston]
socks5://cokwgcrz:2banm65hbp8j@23.26.71.145:5628    [US/Orem]
socks5://jluhwjdg:set84cppefi0@84.247.60.125:6095   [PL/Warsaw]
socks5://jwllsppf:1tq8la36efsk@198.105.121.200:6462 [GB/City Of London]
socks5://jwllsppf:1tq8la36efsk@142.111.67.146:5611  [JP/Tokyo]
socks5://jwllsppf:1tq8la36efsk@216.10.27.159:6837   [US/Dallas]
socks5://zqbzmbqq:9m8moeohfbyf@64.137.96.74:6641    [ES/Madrid]
socks5://jwllsppf:1tq8la36efsk@107.172.163.27:6543  [US/Bloomingdale]
socks5://jwllsppf:1tq8la36efsk@45.38.107.97:6014    [GB/London]
socks5://jwllsppf:1tq8la36efsk@198.23.239.134:6540  [US/Buffalo]
socks5://vycvzakb:4s9r8c5o1w45@23.95.150.145:6114   [US/Buffalo]
socks5://jwllsppf:1tq8la36efsk@31.59.20.176:6754    [GB/London]
socks5://srrwunkn:g6mhp407z3jm@142.111.48.253:7030  [US/Los Angeles]
"""

import json, time, random

try:
    import requests
except ImportError:
    import subprocess, sys
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "requests", "PySocks"], check=True)
    import requests

BASE_API = "https://api.ip2free.com"
BASE_WWW = "https://www.ip2free.com"

COMMON_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.85 Safari/537.36",
    "Accept":          "*/*",
    "Accept-Language": "zh-CN",
    "Origin":          BASE_WWW,
    "Referer":         BASE_WWW + "/",
    "domain":          "www.ip2free.com",
    "lang":            "cn",
    "webname":         "IP2FREE",
    "affid":           "",
    "invitecode":      "",
    "serviceid":       "",
}

CF_POOL_PORTS = list(range(10820, 10846))  # 26 SOCKS5 ports

class Ip2FreeClient:
    """ip2free.com API 客户端"""

    def __init__(self, proxy_port: int | None = None, verify_ssl: bool = True):
        self.session = requests.Session()
        self.session.verify = verify_ssl
        port = proxy_port or random.choice(CF_POOL_PORTS)
        self.session.proxies = {
            "http":  f"socks5h://127.0.0.1:{port}",
            "https": f"socks5h://127.0.0.1:{port}",
        }
        self.session.headers.update(COMMON_HEADERS)
        self.token:   str | None = None
        self.profile: dict       = {}
        self._proxy_port         = port

    # ── Auth ──────────────────────────────────────────────────────────────

    def login(self, email: str, password: str) -> dict:
        """登录并获取 token"""
        r = self.session.post(
            f"{BASE_API}/api/account/login?",
            data=json.dumps({"email": email, "password": password}),
            headers={"Content-Type": "text/plain;charset=UTF-8"},
            timeout=15,
        )
        d = r.json()
        if d.get("code") == 0:
            data = d.get("data", {})
            self.token   = data.get("token")
            self.profile = data.get("profile", {})
            if self.token:
                self.session.headers["x-token"] = self.token
        return d

    # ── Core calls ────────────────────────────────────────────────────────

    def _call(self, method: str, path: str, body=None, timeout=12):
        url = f"{BASE_API}{path}?"
        if method == "GET":
            r = self.session.get(url, timeout=timeout)
        else:
            r = self.session.post(
                url,
                data=json.dumps(body or {}),
                headers={"Content-Type": "text/plain;charset=UTF-8"},
                timeout=timeout,
            )
        return r.json()

    # ── Account ───────────────────────────────────────────────────────────

    def get_profile(self) -> dict:
        return self._call("GET", "/api/account/profile")

    def get_balance(self) -> dict:
        return self._call("GET", "/api/wallet/balance")

    def get_coupons(self) -> dict:
        return self._call("GET", "/api/coupon/my")

    # ── Tasks ─────────────────────────────────────────────────────────────

    def get_task_list(self) -> list[dict]:
        d = self._call("POST", "/api/account/taskList", {})
        return d.get("data", {}).get("list", []) if d.get("code") == 0 else []

    def get_task6_record_id(self) -> int | None:
        """获取每日点击任务的记录 ID (task_id=6)"""
        tasks = self.get_task_list()
        t = next((t for t in tasks if t.get("task_id") == 6), None)
        return t.get("id") if t else None

    def finish_task_step1(self, record_id: int) -> dict:
        """步骤1: 触发验证码, code=-1 是正常的"""
        return self._call("POST", "/api/account/finishTask", {"id": record_id})

    def get_captcha_png(self) -> bytes:
        """获取验证码 PNG 图片 (120x40px, 汉字)"""
        r = self.session.get(f"{BASE_API}/api/account/captcha?", timeout=10)
        return r.content

    def check_captcha(self, captcha_text: str) -> dict:
        """步骤3: 验证验证码"""
        return self._call("POST", "/api/account/checkCaptcha", {"captcha": captcha_text})

    # ── Free Proxies ──────────────────────────────────────────────────────

    def get_free_proxies(self, country: str = "", city: str = "",
                         page: int = 1, page_size: int = 100) -> list[dict]:
        """获取免费代理列表 (最多23个, 所有账号共享同一公共池)"""
        d = self._call("POST", "/api/ip/freeList", {
            "keyword": "", "country": country, "city": city,
            "page": page, "page_size": page_size,
        })
        return d.get("data", {}).get("free_ip_list", []) if d.get("code") == 0 else []

    def get_free_proxy_strings(self) -> list[str]:
        """返回 socks5://user:pass@ip:port 格式的代理字符串列表"""
        proxies = self.get_free_proxies()
        result  = []
        for p in proxies:
            proto = p.get("protocol", "socks5")
            user  = p.get("username", "")
            pw    = p.get("password", "")
            ip    = p.get("ip", "")
            port  = p.get("port", "")
            if ip and port:
                result.append(f"{proto}://{user}:{pw}@{ip}:{port}")
        return result

    # ── Static Proxies ───────────────────────────────────────────────────

    def get_static_proxy_list(self) -> dict:
        return self._call("GET", "/api/staticProxy/list")


def login_all_accounts(proxy_port=None) -> list[dict]:
    """登录所有 ip2free 账号并返回客户端列表"""
    ACCOUNTS = [
        ("emily_gomez98@outlook.com",  "inAyy$X87Uj^"),
        ("sophiagray574@outlook.com",  "8nQDovHvbR@%mWL$"),
        ("e.lewis904@outlook.com",     "Aa123456"),
        ("rylan_rivera98@outlook.com", "AWgpis7xb0"),
        ("alewisazs@outlook.com",      "XYO8nJy3%MGZjm%E"),
    ]
    clients = []
    for email, pw in ACCOUNTS:
        try:
            port = proxy_port or random.choice(CF_POOL_PORTS)
            c = Ip2FreeClient(proxy_port=port)
            r = c.login(email, pw)
            if r.get("code") == 0:
                clients.append({"client": c, "email": email, "profile": c.profile})
            else:
                print(f"[ip2free] login failed {email}: {r.get(msg)}")
        except Exception as e:
            print(f"[ip2free] error {email}: {e}")
        time.sleep(0.5)
    return clients


if __name__ == "__main__":
    import sys
    print("ip2free API 模块测试")
    port = int(sys.argv[1]) if len(sys.argv) > 1 else random.choice(CF_POOL_PORTS)
    print(f"使用代理端口: {port}")

    c = Ip2FreeClient(proxy_port=port)
    r = c.login("emily_gomez98@outlook.com", "inAyy$X87Uj^")
    print(f"Login: code={r.get(code)} id={c.profile.get(id)} invite={c.profile.get(invite_code)}")

    proxies = c.get_free_proxies()
    print(f"\n免费代理: {len(proxies)} 个")
    for p in proxies[:5]:
        print(f"  {p.get(protocol)}://{p.get(username)}:{p.get(password)}@{p.get(ip)}:{p.get(port)}  [{p.get(country_code)}/{p.get(city)}]")

    tasks = c.get_task_list()
    print(f"\n任务: {len(tasks)} 个")
    for t in tasks:
        print(f"  [{t.get(task_id)}] {t.get(task_name_cn)}  finished={t.get(is_finished)}")
