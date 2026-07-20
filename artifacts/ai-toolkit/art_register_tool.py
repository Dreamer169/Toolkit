#!/usr/bin/env python3
"""
Art-Register-Tool v3.0 — arting.ai 专属全自动注册反代服务

路由前缀 [ar]:
  POST /ar/v1/chat/completions        — OpenAI 兼容聊天（流式 / 非流式）
  GET  /ar/v1/models                  — 模型列表
  GET  /ar/admin/status               — 账号池状态（Bearer 鉴权）
  GET  /ar/admin/sched                — 调度器状态（Bearer 鉴权）
  POST /ar/admin/register             — 手动触发注册（Bearer 鉴权）
  GET  /ar/admin/accounts             — 账号详情（Bearer 鉴权）
  POST /ar/admin/accounts/<id>/disable
  POST /ar/admin/accounts/<id>/enable
  GET  /health  /ar/health            — 健康检查
  GET  /v1/models                     — 别名
  POST /v1/chat/completions           — 别名

arting.ai 协议（实测确认，2026-07-18）：
  注册: POST /api/wp/user/register → 邮件验证码 → verify → login → JWT token
  聊天: Authorization = JWT token（裸值，无 Bearer 前缀）
        X-Identity-Id  = 本地生成的 UUID（账号指纹，固定不变，不随重登改变）
        X-Timestamp + X-Signature = HMAC-SHA256(
            key  = HMAC_SECRET（UTF-8 编码），
            msg  = "{unix_ts}\\n{METHOD}\\n{path}\\n{sha256(body)}\\n{uuid}"
        )
  额度: 60 次 / 账号（FREE_USAGE_CAP=60，可通过 /admin/settings 热改）
  token 过期或单会话冲突 → code 100001 / 100005 / 100012 → 重新登录刷新 JWT

高并发部署（推荐，gevent 已安装时自动启用）：
  python art_register_tool.py  ← 自动检测 gevent 并使用 gevent worker

  gevent worker（当前默认）:
    gunicorn -w 1 -k gevent --worker-connections 1000 --bind 0.0.0.0:9097 art_register_tool:app
    # 单线程 + greenlet，500-1000 并发 RSS≈40MB；Condition/httpx 在 I/O 时 cooperative yield

  fallback（gevent 不可用时）:
    gunicorn -w 1 -k gthread --threads 300 --bind 0.0.0.0:9097 art_register_tool:app
    # 300 OS线程，I/O密集 GIL等待时释放，适合 <300 并发
"""
from __future__ import annotations

# gevent monkey-patch 必须在所有其他 import 之前执行
# gunicorn gevent worker 启动时会自动 patch，但 _StandaloneApp 内嵌模式下
# app 模块在 gunicorn 初始化前已 import，所以必须在此处主动 patch
try:
    from gevent import monkey as _gm
    _gm.patch_all()
    _GEVENT = True
except ImportError:
    _GEVENT = False

import hashlib
import hmac as _hmac
import json
import os
import random
import secrets
import string
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import httpx
from flask import Flask, Response, jsonify, request, stream_with_context

# ── 临时邮件模块（注册用）────────────────────────────────────────────
_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))
sys.path.insert(0, "/root")
try:
    from all_temp_mail import create_normal_manager
    _HAS_MAIL_MGR = True
except ImportError:
    _HAS_MAIL_MGR = False

# ═══════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════
ARTING_BASE     = "https://arting.ai"
CHAT_PATH       = "/api/aigc/comprehensive/chat/create-task"
ARTING_CHAT_URL = f"{ARTING_BASE}{CHAT_PATH}"

HMAC_SECRET     = "de05b62d7eb7cc3d93db4fb168054035fc147d6f89a013b528a1e6ab16cd08c1"
BOOTSTRAP_TOKEN = "4xH7LpQ8KjF2aR9cVbN3mW6yT1uE5iA0sD8fG7hJ9kL2zXArting"

FREE_USAGE_CAP   = 60     # 每账号每日上限（arting 实际 60 次，与服务侧一致）
POOL_CAP         = 300    # 阶段切换阈值（超过后切低速注册，不阻止注册）

# ── 运行时热改参数（可通过 /ar/admin/settings PATCH 修改，无需重启）──
_runtime_cfg: dict = {
    "rate_limit_cooldown": 60.0,   # 账号收到 100099 后冷却秒数
    "proxy_cooldown_ttl":  600,    # 代理端口失效后冷却秒数
    "reg_phase1_min":      80,     # 阶段一每日最少注册
    "reg_phase1_max":      120,    # 阶段一每日最多注册
    "reg_phase2_min":      24,     # 阶段二每日最少注册
    "reg_phase2_max":      36,     # 阶段二每日最多注册
    "pool_cap":            300,    # 阶段切换阈值（总量超过后切换为低速注册）
    "chat_proxy":          "",     # 聊天代理 socks5://… 留空=直连
    "release_cooldown":    30,     # 账号释放后的冷却秒数，期间不再被优先选取
    "proactive_rpm":       2,      # 每账号每窗口最多发送请求数（主动限速），0=禁用
    "proactive_rpm_window":60,     # 主动限速滑动窗口（秒）
    "free_usage_cap":      60,     # 每账号每日对话上限基准（实际=cap-随机余量）
    "usage_reserve_min":   2,      # 每账号随机余量下限（次），防止触及 arting 硬限
    "usage_reserve_max":   5,      # 每账号随机余量上限（次），增加行为随机性
}
_runtime_cfg_lock = threading.Lock()

# ── 日志 ring-buffer ─────────────────────────────────────────
_log_ring: "collections.deque[str]" = None  # 延迟初始化（collections 尚未导入）
_log_ring_lock = threading.Lock()

SAVE_INTERVAL    = 30     # 账号池持久化间隔（秒），避免每次请求都写磁盘

ACCOUNTS_FILE    = Path("/root/art-register-tool/accounts.json")
DAILY_STATE_FILE = Path("/root/art-register-tool/daily_state.json")
ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)

# xray 住宅代理端口（注册使用，按需自动选取）
RESI_PORTS = (
    list(range(10820, 10850)) +   # in-socks-0~29 (订阅轮换节点)
    list(range(10851, 10861)) +   # ss-in-1~9
    list(range(10870, 10891)) +   # ps-in-0~19
    list(range(10950, 10996))     # sub-in-0~45 (订阅链接节点，HK/JP/US 多 IP)
)

# 节点失效冷却表：{port: cooldown_until_epoch}，pick_resi_proxy 自动跳过
PROXY_COOLDOWN_TTL = 600   # 秒，10 分钟后自动恢复
_proxy_cooldown: dict = {}
_proxy_cooldown_lock = threading.Lock()

# 已实探验证可用的代理缓存：{port: valid_until_epoch}
_PROXY_GOOD_TTL  = 300  # 验证通过后缓存 5 分钟，期间跳过重探
_PROXY_PROBE_MAX = 3    # 每次 pick_resi_proxy() 最多发起 HTTP 实探次数
_proxy_good: dict = {}
_proxy_good_lock  = threading.Lock()

# UA + sec-ch-ua 指纹对照表（每条必须内部一致，Cloudflare 会交叉验证）
_UA_PROFILES = [
    {
        "ua":       "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
        "platform": "Windows",
    },
    {
        "ua":       "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
        "platform": "macOS",
    },
    {
        "ua":       "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="136", "Chromium";v="136", "Not.A/Brand";v="99"',
        "platform": "Windows",
    },
    {
        "ua":       "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="136", "Chromium";v="136", "Not.A/Brand";v="99"',
        "platform": "Linux",
    },
    {
        "ua":       "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="135", "Chromium";v="135", "Not-A.Brand";v="8"',
        "platform": "Windows",
    },
    {
        "ua":       "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="135", "Chromium";v="135", "Not-A.Brand";v="8"',
        "platform": "macOS",
    },
]
# 向后兼容（rand_ua() / refresh_token() 等处仍直接取 UA 字符串）
USER_AGENTS = [p["ua"] for p in _UA_PROFILES]

def _rand_profile() -> dict:
    """随机返回一条 UA+sec-ch-ua+platform 指纹组合（内部一致）。"""
    return random.choice(_UA_PROFILES)

# 实测可用的 generation_type（key = 对外暴露的模型名，value = arting 内部字段）
MODEL_MAP: dict[str, str] = {
    # ── 原生支持 ────────────────────────────────────────────────────
    "gpt-5.2":           "gpt-5.2",
    "gpt-5.1":           "gpt-5.1",
    "gpt-5":             "gpt-5",
    "gpt-4o-mini":       "gpt-4o-mini",
    "o4-mini":           "o4-mini",
    "gemini-2.5-pro":    "gemini-2.5-pro",
    "deepseek-v3":       "deepseek-chat",      # arting 内部名称
    "deepseek-r1":       "deepseek-reasoner",  # arting 内部名称
    # ── 常见别名 ────────────────────────────────────────────────────
    "gpt-4o":            "gpt-5.2",
    "gpt-4":             "gpt-5.2",
    "gpt-4-turbo":       "gpt-5.2",
    "gpt-3.5-turbo":     "gpt-4o-mini",
    "gemini":            "gemini-2.5-pro",
    "deepseek":          "deepseek-chat",
    "deepseek-chat":     "deepseek-chat",
    "deepseek-reasoner": "deepseek-reasoner",
}

# arting 鉴权错误码（需重新登录）
AUTH_CODES = {100001, 100005, 100012}
# arting 成功码
OK_CODES   = {100000, 200, 0}
# 上游 LLM 失败，可换账号重试（100804=LLM generation failed）
UPSTREAM_RETRY_CODES = {100804}
RATE_LIMIT_CODES     = {100099}  # 单账号速率限制，冷却 60s 后换账号重试

# ═══════════════════════════════════════════════════════════════════
# 全局共享 httpx 客户端（连接池，高并发复用 TCP 连接）
# 在 main() 中初始化，代理设置固定后不再改变
# ═══════════════════════════════════════════════════════════════════
_CHAT_CLIENT:     Optional[httpx.Client] = None
_CHAT_PROXY_STR:  Optional[str]          = None  # 仅用于状态展示

def get_chat_client() -> httpx.Client:
    assert _CHAT_CLIENT is not None, "chat client not initialized"
    return _CHAT_CLIENT

# ═══════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════
def log(tag: str, msg: str) -> None:
    global _log_ring
    line = f"[{time.strftime('%H:%M:%S')}][{tag}] {msg}"
    print(line, flush=True)
    if _log_ring is None:
        import collections as _c
        _log_ring = _c.deque(maxlen=500)
    with _log_ring_lock:
        _log_ring.append(line)

def rand_ua() -> str:
    return random.choice(USER_AGENTS)

def rand_delay(lo: float = 0.3, hi: float = 1.5) -> None:
    time.sleep(random.uniform(lo, hi))

def rand_password() -> str:
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(chars) for _ in range(20))

def _next_midnight() -> float:
    """返回明日 UTC 00:00:00 的 epoch（arting.ai 大致在此时重置免费次数）。"""
    import datetime
    today    = datetime.datetime.utcnow().date()
    tomorrow = today + datetime.timedelta(days=1)
    return datetime.datetime(tomorrow.year, tomorrow.month, tomorrow.day,
                             tzinfo=datetime.timezone.utc).timestamp()

def _today_str() -> str:
    import datetime
    return datetime.datetime.utcnow().date().isoformat()

def sign_request(method: str, path: str, body_bytes: bytes, uid: str) -> dict:
    """构造 arting.ai HMAC-SHA256 签名头（X-Timestamp + X-Signature）。"""
    ts  = str(int(time.time()))
    bh  = hashlib.sha256(body_bytes).hexdigest()
    msg = f"{ts}\n{method.upper()}\n{path}\n{bh}\n{uid}"
    sig = _hmac.new(HMAC_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return {"X-Timestamp": ts, "X-Signature": sig}

def _estimate_tokens(text: str) -> int:
    """粗估 token 数（中英混合约3字符/token），用于伪装 OpenAI 用量字段。"""
    return max(1, len(text) // 3)

def mark_proxy_failed(port: int) -> None:
    """标记端口冷却黑名单，同时清除 good-cache（代理连接级失败时调用）。"""
    _ttl = _runtime_cfg.get("proxy_cooldown_ttl", PROXY_COOLDOWN_TTL)
    with _proxy_cooldown_lock:
        _proxy_cooldown[port] = time.time() + _ttl
    with _proxy_good_lock:
        _proxy_good.pop(port, None)
    log("proxy", f"port {port} → cooldown {_ttl}s (good-cache evicted)")

def _probe_proxy(proxy_url: str, port: int) -> bool:
    """
    通过代理向 arting.ai 发一个轻量 GET，确认 SOCKS5 + TLS 完整链路可通。
    仅做 TCP 端口探测无法发现"daemon 在线但出口节点已挂"，
    此时真正的错误要等到 40s 注册超时才会暴露。
    成功写入 _proxy_good 缓存（5min TTL）；失败调用 mark_proxy_failed。
    """
    try:
        with httpx.Client(
            proxy=proxy_url,
            timeout=httpx.Timeout(connect=5.0, read=4.0, write=2.0, pool=1.0),
        ) as c:
            r = c.head("https://arting.ai/", follow_redirects=False)
            if r.status_code < 600:
                with _proxy_good_lock:
                    _proxy_good[port] = time.time() + _PROXY_GOOD_TTL
                return True
    except Exception as e:
        log("proxy", f"port {port} probe failed: {type(e).__name__}")
        mark_proxy_failed(port)
    return False


def pick_resi_proxy() -> Optional[str]:
    """
    返回已验证可用的住宅代理 URL（socks5://127.0.0.1:{port}）。

    三层过滤：
      1. 跳过冷却黑名单中的端口
      2. good-cache 命中（5min 内实探通过）→ 免探直接返回
      3. TCP 存活（0.5s）+ HTTP 实探（最多 _PROXY_PROBE_MAX 个端口）
         实探失败的端口自动进入冷却黑名单
    """
    import socket
    now = time.time()

    # 清理过期冷却
    with _proxy_cooldown_lock:
        for p in [p for p, t in list(_proxy_cooldown.items()) if t <= now]:
            del _proxy_cooldown[p]
        cooled = set(_proxy_cooldown)

    # 清理过期 good-cache
    with _proxy_good_lock:
        for p in [p for p, t in list(_proxy_good.items()) if t <= now]:
            del _proxy_good[p]
        good = set(_proxy_good)

    ports = [p for p in RESI_PORTS if p not in cooled]
    if not ports:
        log("proxy", "all resi ports cooling — no proxy available")
        return None
    random.shuffle(ports)

    probed = 0
    for p in ports:
        proxy_url = f"socks5://127.0.0.1:{p}"

        # Layer 2: good-cache hit — skip probe
        if p in good:
            return proxy_url

        # Layer 3a: TCP liveness (local daemon alive?)
        try:
            with socket.create_connection(("127.0.0.1", p), timeout=0.5):
                pass
        except OSError:
            continue

        # Layer 3b: HTTP probe — confirms TLS handshake + routing end-to-end
        probed += 1
        if _probe_proxy(proxy_url, p):
            return proxy_url
        # probe failed: mark_proxy_failed already called inside _probe_proxy
        if probed >= _PROXY_PROBE_MAX:
            log("proxy", f"gave up after {probed} failed probes (link down?)")
            break

    return None

def extract_text(content) -> str:
    """从 OpenAI content 字段提取纯文本，兼容 str 和多模态 list 格式。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text", ""))
        return "".join(parts)
    return ""

# ═══════════════════════════════════════════════════════════════════
# 账号数据类
# ═══════════════════════════════════════════════════════════════════
@dataclass
class ArtAccount:
    id:              str   = field(default_factory=lambda: uuid.uuid4().hex[:8].upper())
    email:           str   = ""
    password:        str   = ""
    user_id:         int   = 0
    token:           str   = ""          # JWT，聊天 Authorization 字段
    identity_uuid:   str   = field(default_factory=lambda: str(uuid.uuid4()))  # 本地指纹，固定
    usage:           int   = 0
    max_usage:       int   = FREE_USAGE_CAP
    enabled:         bool  = True
    disabled_reason: str   = ""
    create_time:     str   = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
    last_used:        float = 0.0
    last_released:    float = 0.0
    quarantine_until: float = 0.0  # epoch; 0 = 未隔离; >0 = 隔离至该时刻（等 arting 次数重置）
    rate_limit_until: float = 0.0  # epoch; 0 = 正常; >0 = 速率限制冷却至该时刻

    @property
    def credits_left(self) -> int:
        return max(0, self.max_usage - self.usage)

    @property
    def is_quarantined(self) -> bool:
        return self.quarantine_until > 0 and time.time() < self.quarantine_until

    @property
    def is_rate_limited(self) -> bool:
        return self.rate_limit_until > 0 and time.time() < self.rate_limit_until

    @property
    def is_active(self) -> bool:
        if self.is_quarantined or self.is_rate_limited:
            return False
        return self.enabled and self.credits_left > 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ArtAccount":
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})

# ═══════════════════════════════════════════════════════════════════
# 账号池
# ═══════════════════════════════════════════════════════════════════
class ArtPool:
    """
    线程安全账号池。

    并发设计：
    - acquire / release 仅做内存操作，不触发文件 I/O，锁持有时间 < 1ms。
    - 持久化由后台 _save_loop 每 SAVE_INTERVAL 秒检查一次 _dirty 标志，
      异步写磁盘，不阻塞请求线程。
    - add() 立即同步写盘（新账号重要，不能丢）。
    """

    def __init__(self, reg_proxy: Optional[str] = None):
        self.reg_proxy    = reg_proxy
        self._accounts:   list[ArtAccount] = []
        self._occupied:   set[str]         = set()
        self._req_times:    dict           = {}   # acc_id → [timestamp,...] 主动限速滑动窗口
        self._cooling_until:dict           = {}   # acc_id → epoch; 硬冷却到期时刻（release后30s内不可复用）
        self._waiting:      int            = 0    # 当前阻塞在 cond.wait() 等待账号的请求数
        self._dirty       = False
        self._cond        = threading.Condition(threading.RLock())  # 高并发排队锁
        self._load()
        log("pool", f"loaded {len(self._accounts)} accounts "
            f"(active={sum(1 for a in self._accounts if a.is_active)})")
        self._start_save_loop()

    # ── 持久化 ────────────────────────────────────────────────────
    def _load(self) -> None:
        if ACCOUNTS_FILE.exists():
            try:
                data = json.loads(ACCOUNTS_FILE.read_text())
                self._accounts = [ArtAccount.from_dict(d) for d in data]
            except Exception as e:
                log("pool", f"⚠ load error: {e}")

    def _save(self) -> None:
        """写磁盘（在锁外调用，避免阻塞请求线程）。"""
        try:
            with self._cond:
                snapshot = [a.to_dict() for a in self._accounts]
                self._dirty = False
            ACCOUNTS_FILE.write_text(
                json.dumps(snapshot, indent=2, ensure_ascii=False)
            )
        except Exception as e:
            log("pool", f"⚠ save error: {e}")

    def _start_save_loop(self) -> None:
        def loop():
            while True:
                time.sleep(SAVE_INTERVAL)
                if self._dirty:
                    self._save()
        t = threading.Thread(target=loop, daemon=True, name="pool-saver")
        t.start()

    # ── 调度：空闲最久优先；全部占用时阻塞等待（高并发排队） ────────

    def _proactive_ok(self, acc: "ArtAccount", now: float) -> bool:
        """主动限速：账号在滑动窗口内请求数未超上限则返回 True。
        调用方必须持有 self._cond 锁。proactive_rpm=0 时禁用。"""
        limit = int(_runtime_cfg.get("proactive_rpm", 0))
        if limit <= 0:
            return True
        window = float(_runtime_cfg.get("proactive_rpm_window", 60))
        times  = self._req_times.get(acc.id, [])
        recent = [t for t in times if now - t < window]
        self._req_times[acc.id] = recent   # 顺手清理过期记录
        return len(recent) < limit

    def acquire(self, timeout: float = 20.0, max_queue: int = 0) -> Optional[ArtAccount]:
        """
        获取一个可用账号（线程安全阻塞版）。
        - 有空闲账号 → 立即返回（最久未使用者优先）
        - 全部被占用 → 阻塞至有账号释放，最多等待 timeout 秒
        - timeout 内仍无可用 → 返回 None（调用方应返回 503 + Retry-After）
        - max_queue > 0 → 若已有 max_queue 个线程在等，立即返回 None（防雪崩）
        """
        deadline = time.monotonic() + timeout
        with self._cond:
            # 快速失败：排队线程已达上限，防止所有线程堆积在 cond.wait()
            if max_queue > 0 and self._waiting >= max_queue:
                return None
            while True:
                _now = time.time()
                # 候选账号：活跃 + 未被占用 + 未在硬冷却期内 + 未触发主动限速
                candidates = [
                    a for a in self._accounts
                    if a.is_active
                    and a.id not in self._occupied
                    and self._cooling_until.get(a.id, 0) <= _now
                    and self._proactive_ok(a, _now)
                ]
                if candidates:
                    # 最久未使用者优先（硬冷却已保证30s间隔，无需再做cold/warm分级）
                    acc = min(candidates, key=lambda a: a.last_used)
                    acc.usage     += 1
                    acc.last_used  = _now
                    self._occupied.add(acc.id)
                    # 记录时间戳供主动限速滑动窗口使用
                    self._req_times.setdefault(acc.id, []).append(_now)
                    self._dirty = True
                    return acc
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                # 阻塞等待：记录排队数，供 stats() 上报
                self._waiting += 1
                self._cond.wait(timeout=min(remaining, 2.0))
                self._waiting -= 1

    def release(self, acc: ArtAccount) -> None:
        """释放账号；额度耗尽时进入隔离区等明日重置，而非永久禁用。
        释放后硬冷却 release_cooldown 秒，期间不分配给新请求（_cooling_until 硬过滤）。"""
        with self._cond:
            now = time.time()
            acc.last_released = now
            self._occupied.discard(acc.id)
            # 硬冷却：写入到期时刻；acquire() 过滤掉冷却中的账号
            rc = float(_runtime_cfg.get("release_cooldown", 30))
            self._cooling_until[acc.id] = now + rc
            if acc.usage >= acc.max_usage and acc.quarantine_until == 0:
                acc.quarantine_until = _next_midnight()
                log("pool", f"⚑ {acc.email} 额度耗尽 → 隔离至 UTC 午夜")
            self._dirty = True
            self._cond.notify_all()   # 唤醒等待 acquire 的线程

    def add(self, acc: ArtAccount) -> None:
        """添加新账号并立即写盘（注册成功的账号不能丢）。"""
        with self._cond:
            self._accounts.append(acc)
            self._cond.notify_all()  # 新账号到来，唤醒等待线程
        log("pool", f"+ {acc.email} (uid={acc.user_id})")
        self._save()  # 同步写，新账号重要

    def stats(self) -> dict:
        with self._cond:
            now          = time.time()
            active       = [a for a in self._accounts if a.is_active]
            quarantined  = [a for a in self._accounts if a.is_quarantined]
            rate_limited = [a for a in self._accounts if a.is_rate_limited]
            # 硬冷却中（释放后30s内不可复用）
            cooling_ids  = {aid for aid, exp in self._cooling_until.items() if exp > now}
            handling     = len(self._occupied)            # 当前处理中的请求
            cooling      = len(cooling_ids)               # 处理完毕但仍在冷却的账号
            # 主动限速：活跃、未被占用、未在冷却期内，但超出滑动窗口 RPM 上限
            proactively_limited = [
                a for a in active
                if a.id not in self._occupied
                and a.id not in cooling_ids
                and not self._proactive_ok(a, now)
            ]
            return {
                "total":               len(self._accounts),
                "enabled":             sum(1 for a in self._accounts if a.enabled),
                "active":              len(active),
                "quarantined":         len(quarantined),
                "rate_limited":        len(rate_limited),
                "handling":            handling,
                "cooling":             cooling,
                "occupied":            handling + cooling,  # 对外"占用中"= 处理中 + 冷却中
                "queued":              self._waiting,       # 排队等待分配账号的请求数
                "proactively_limited": len(proactively_limited),
                "total_credits_left":  sum(a.credits_left for a in active),
            }

    def accounts_detail(self) -> list[dict]:
        """快照引用后在锁外序列化，减少持锁时间（高并发安全）。"""
        with self._cond:
            snapshot = list(self._accounts)   # O(n) 引用复制，锁内极快
        return [a.to_dict() for a in snapshot]  # 序列化在锁外

    def get_by_id(self, acc_id: str) -> Optional[ArtAccount]:
        with self._cond:
            return next((a for a in self._accounts if a.id == acc_id), None)

    def set_enabled(self, acc_id: str, enabled: bool, reason: str = "") -> bool:
        with self._cond:
            acc = next((a for a in self._accounts if a.id == acc_id), None)
            if acc is None:
                return False
            acc.enabled         = enabled
            acc.disabled_reason = "" if enabled else reason or "manual"
            if enabled:
                acc.quarantine_until = 0.0  # 手动启用时解除隔离
            self._dirty = True
        return True

    def delete_account(self, acc_id: str) -> bool:
        """从池中永久删除账号（同时从占用集和冷却表中移除）。"""
        with self._cond:
            before = len(self._accounts)
            self._accounts = [a for a in self._accounts if a.id != acc_id]
            self._occupied.discard(acc_id)
            self._cooling_until.pop(acc_id, None)
            removed = len(self._accounts) < before
            if removed:
                self._dirty = True
                self._cond.notify_all()
        return removed

    def release_rate_limited(self, acc: ArtAccount,
                             cooldown: float = -1) -> None:
        """账号被 arting 速率限制（100099），冷却 cooldown 秒后重新可用。
        速率限制不消耗对话配额——acquire() 里已 usage+1，此处回滚。"""
        if cooldown < 0:
            cooldown = float(_runtime_cfg.get("rate_limit_cooldown", 60))
        with self._cond:
            # 回滚 usage：100099 是频率限制，arting 侧不计入每日配额
            acc.usage = max(0, acc.usage - 1)
            acc.rate_limit_until = time.time() + cooldown
            self._occupied.discard(acc.id)
            self._cooling_until.pop(acc.id, None)   # 放弃冷却，直接进 rate_limit 状态
            self._dirty = True
            self._cond.notify_all()
        log("pool", f"acc {acc.email} 速率限制冷却 {cooldown:.0f}s (usage 已回滚)")

    def restore_quarantined(self) -> int:
        """
        解除所有已到期的隔离账号（arting.ai 每日午夜后次数重置）。
        重置 usage=0、max_usage 同步为最新 free_usage_cap，账号重回活跃池。
        """
        now   = time.time()
        base  = int(_runtime_cfg.get("free_usage_cap", FREE_USAGE_CAP))
        r_min = int(_runtime_cfg.get("usage_reserve_min", 2))
        r_max = int(_runtime_cfg.get("usage_reserve_max", 5))
        count = 0
        with self._cond:
            for acc in self._accounts:
                if acc.quarantine_until > 0 and now >= acc.quarantine_until:
                    acc.quarantine_until = 0.0
                    acc.usage            = 0
                    # 每次解除隔离重新随机余量，使各账号行为不一致
                    acc.max_usage        = max(1, base - random.randint(
                                              min(r_min, r_max), max(r_min, r_max)))
                    acc.enabled          = True
                    acc.disabled_reason  = ""
                    count += 1
            if count:
                self._dirty = True
                self._cond.notify_all()
        return count

    @property
    def valid_count(self) -> int:
        with self._cond:
            return sum(1 for a in self._accounts if a.is_active)

    @property
    def total_count(self) -> int:
        with self._cond:
            return len(self._accounts)

# ═══════════════════════════════════════════════════════════════════
# 注册引擎
# ═══════════════════════════════════════════════════════════════════
class ArtRegistrar:
    def __init__(self, pool: ArtPool, mail_mgr=None):
        self.pool     = pool
        self.mail_mgr = mail_mgr

    @staticmethod
    def _parse_local_port(proxy: Optional[str]) -> Optional[int]:
        """从 socks5://127.0.0.1:{port} 提取本地端口号，非本地代理返回 None。"""
        if not proxy or "127.0.0.1:" not in proxy:
            return None
        try:
            return int(proxy.rsplit(":", 1)[-1])
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _make_reg_client(proxy: Optional[str]) -> httpx.Client:
        """构造注册专用 httpx.Client：与 Chrome 一致的完整指纹 headers + 分段超时。"""
        p = _rand_profile()
        headers = {
            "User-Agent":        p["ua"],
            "sec-ch-ua":         p["sec_ch_ua"],
            "sec-ch-ua-mobile":  "?0",
            "sec-ch-ua-platform": f'"{p["platform"]}"',
            "Accept":            "application/json, text/plain, */*",
            "Accept-Language":   "en-US,en;q=0.9",
            "Accept-Encoding":   "gzip, deflate, br",
            "Origin":            "https://arting.ai",
            "Referer":           "https://arting.ai/cn/register",
            "Sec-Fetch-Site":    "same-origin",
            "Sec-Fetch-Mode":    "cors",
            "Sec-Fetch-Dest":    "empty",
            "Connection":        "keep-alive",
        }
        kw: dict = {
            "timeout": httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0),
            "headers": headers,
        }
        if proxy:
            kw["proxy"] = proxy
        return httpx.Client(**kw)

    def register_one(self) -> Optional[ArtAccount]:
        """最多尝试 4 个不同代理，每次代理级失败后冷却该端口再换下一个。"""
        MAX_ATTEMPTS = 4
        for attempt in range(1, MAX_ATTEMPTS + 1):
            proxy = self.pool.reg_proxy or pick_resi_proxy()
            try:
                return self._do_register(proxy)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError) as e:
                port = self._parse_local_port(proxy)
                if port:
                    mark_proxy_failed(port)
                log("reg", f"✗ proxy fail attempt {attempt}/{MAX_ATTEMPTS} "
                    f"(port={port}): {type(e).__name__}: {e}")
                if attempt >= MAX_ATTEMPTS:
                    return None
            except Exception as e:
                log("reg", f"✗ {e}")
                return None
        return None

    def _do_register(self, proxy: Optional[str]) -> ArtAccount:
        if not self.mail_mgr:
            raise RuntimeError("mail_mgr not available")

        inbox    = self.mail_mgr.create()
        email    = inbox["email"]
        password = rand_password()
        _pstr = proxy.rsplit(":", 1)[-1] if proxy else "direct"
        log("reg", f"→ {email} (proxy={_pstr})")

        with self._make_reg_client(proxy) as c:
            # 1. 注册
            rand_delay(0.5, 1.5)
            r = c.post(f"{ARTING_BASE}/api/wp/user/register",
                       json={"email": email, "password": password})
            r.raise_for_status()
            d = r.json()
            if d.get("code") != 100000:
                raise RuntimeError(f"register failed: {d}")
            cred = d["data"]["credential"]
            log("reg", f"  credential={cred[:16]}…")

            # 2. 等待邮件验证码
            code = self.mail_mgr.wait_for_code(inbox, timeout=120)
            if not code:
                raise RuntimeError("verification code timeout")
            log("reg", f"  code={code}")

            # 3. 验证邮箱
            rand_delay(0.3, 0.8)
            r = c.post(f"{ARTING_BASE}/api/wp/user/register/code/verify",
                       json={"email": email, "credential": cred, "register_code": code})
            v = r.json()
            if v.get("code") != 100000:
                raise RuntimeError(f"verify failed: {v}")

            # 4. 登录获取 JWT
            rand_delay(0.3, 0.8)
            r = c.post(f"{ARTING_BASE}/api/wp/user/login/password",
                       json={"email": email, "password": password})
            r.raise_for_status()
            ld = r.json()
            if ld.get("code") != 100000:
                raise RuntimeError(f"login failed: {ld}")
            token   = ld["data"]["token"]
            user_id = ld["data"]["user_info"]["user_id"]

        cap      = int(_runtime_cfg.get("free_usage_cap", FREE_USAGE_CAP))
        r_min    = int(_runtime_cfg.get("usage_reserve_min", 2))
        r_max    = int(_runtime_cfg.get("usage_reserve_max", 5))
        cap      = max(1, cap - random.randint(min(r_min, r_max), max(r_min, r_max)))
        acc = ArtAccount(email=email, password=password, user_id=user_id, token=token, max_usage=cap)
        log("reg", f"✓ uid={user_id}")
        return acc

    def refresh_token(self, acc: ArtAccount) -> bool:
        """重新登录刷新 JWT token。直连（不走代理），避免住宅代理 SSL 超时。"""
        try:
            with httpx.Client(timeout=20, headers={"User-Agent": rand_ua()}) as c:
                r = c.post(f"{ARTING_BASE}/api/wp/user/login/password",
                           json={"email": acc.email, "password": acc.password})
                r.raise_for_status()
                ld = r.json()
                if ld.get("code") != 100000:
                    log("reg", f"refresh failed {acc.email}: {ld.get('message','?')}")
                    return False
                acc.token = ld["data"]["token"]
                self.pool._dirty = True  # token 更新后标记脏，下次后台保存
                log("reg", f"✓ token refreshed {acc.email}")
                return True
        except Exception as e:
            log("reg", f"refresh error {acc.email}: {e}")
            return False

# ═══════════════════════════════════════════════════════════════════
# 每日定额调度器
# ═══════════════════════════════════════════════════════════════════
class DailyScheduler:
    """
    每日定额注册 + 午夜解隔离：

    阶段一（pool 总量 < POOL_CAP=300）：每日随机注册 80-120 个（100 ±20%）
    阶段二（pool 总量 >= 300）          ：每日随机注册 24-36 个（30 ±20%）
    两阶段均持续注册，300 为速率切换阈值而非硬上限。

    注册时机均匀分散在全天；紧急情况（活跃 < 10）立即补充。
    每日 UTC 00:00 解除所有到期隔离账号（arting.ai 此时重置免费次数）。

    状态持久化至 daily_state.json，重启后继续当天进度，不重复注册。
    """

    def __init__(self, pool: ArtPool, registrar: ArtRegistrar, cap: int = POOL_CAP):
        self.pool      = pool
        self.registrar = registrar
        self.cap       = cap
        self._lock     = threading.Lock()
        self._state    = self._load_state()

    # ── 状态持久化 ────────────────────────────────────────────────
    def _load_state(self) -> dict:
        if DAILY_STATE_FILE.exists():
            try:
                return json.loads(DAILY_STATE_FILE.read_text())
            except Exception:
                pass
        return {"date": "", "registered": 0, "target": 0}

    def _save_state(self) -> None:
        try:
            DAILY_STATE_FILE.write_text(json.dumps(self._state, indent=2))
        except Exception as e:
            log("sched", f"save state error: {e}")

    # ── 今日目标计算 ──────────────────────────────────────────────
    @staticmethod
    def _calc_target(total: int) -> int:
        cap = int(_runtime_cfg.get("pool_cap", POOL_CAP))
        if total < cap:
            lo = int(_runtime_cfg.get("reg_phase1_min", 80))
            hi = int(_runtime_cfg.get("reg_phase1_max", 120))
        else:
            lo = int(_runtime_cfg.get("reg_phase2_min", 24))
            hi = int(_runtime_cfg.get("reg_phase2_max", 36))
        return random.randint(min(lo, hi), max(lo, hi))

    # ── 新日初始化 ────────────────────────────────────────────────
    def _new_day(self) -> None:
        restored = self.pool.restore_quarantined()
        if restored:
            log("sched", f"午夜解隔离: {restored} 个账号恢复活跃")
        total  = self.pool.total_count
        target = self._calc_target(total)
        with self._lock:
            self._state = {"date": _today_str(), "registered": 0, "target": target}
            self._save_state()
        log("sched", f"新的一天 — 目标注册 {target} 个 (pool_total={total})")

    # ── 主循环 ───────────────────────────────────────────────────
    def start(self) -> None:
        if self._state.get("date") != _today_str():
            self._new_day()
        else:
            log("sched", f"恢复当日进度 {self._state['date']}: "
                f"{self._state['registered']}/{self._state['target']}")
        t = threading.Thread(target=self._loop, daemon=True, name="art-scheduler")
        t.start()
        log("sched", f"DailyScheduler 启动 cap={self.cap}")

    def _loop(self) -> None:
        while True:
            try:
                self._tick()
            except Exception as e:
                log("sched", f"⚠ {e}")
                time.sleep(60)

    def _tick(self) -> None:
        import datetime

        # 日期切换检测
        if self._state.get("date") != _today_str():
            self._new_day()

        with self._lock:
            target     = self._state.get("target", 100)
            registered = self._state.get("registered", 0)
        total  = self.pool.total_count
        active = self.pool.valid_count

        # 紧急补充：活跃账号 < 10 且总量未超阈值 3 倍（防失控）
        if active < 10 and total < self.cap * 3:
            log("sched", f"紧急补充 (active={active})")
            acc = self.registrar.register_one()
            if acc:
                self.pool.add(acc)
                with self._lock:
                    self._state["registered"] += 1
                    self._save_state()
                time.sleep(30)  # 成功：稍等，避免连续快速注册
            else:
                time.sleep(10)  # 失败：短退避后立即重试，不浪费等待窗口
            return

        # 正常注册：今日配额未满（阶段切换阈值只影响目标量，不阻止注册）
        if registered < target:
            acc = self.registrar.register_one()
            if acc:
                self.pool.add(acc)
                with self._lock:
                    self._state["registered"] += 1
                    registered = self._state["registered"]  # 更新本地副本
                    self._save_state()
                log("sched", f"注册进度 {registered}/{target}")

                # 注册成功后计算分散间隔（失败不占配额窗口，立即重试）
                now = datetime.datetime.utcnow()
                midnight = (datetime.datetime(now.year, now.month, now.day)
                            + datetime.timedelta(days=1))
                remaining_sec   = (midnight - now).total_seconds()
                remaining_quota = max(1, target - registered)
                # 泊松过程：指数分布间隔，均值=剩余时间/剩余配额，加±50%扰动
                mean_interval = remaining_sec / remaining_quota
                raw       = random.expovariate(1.0 / max(mean_interval, 1))
                sleep_sec = max(30, min(raw * random.uniform(0.5, 1.5), 7200))
                log("sched", f"下次注册 {sleep_sec:.0f}s 后")
                time.sleep(sleep_sec)
            else:
                # 注册失败：短退避后立即重试（不耗分散间隔配额）
                retry_delay = random.uniform(10, 30)
                log("sched", f"注册失败，{retry_delay:.0f}s 后重试")
                time.sleep(retry_delay)
        else:
            # 今日配额已满，5 分钟后再检查（等日期切换 / 隔离解除）
            time.sleep(300)

    def sched_stats(self) -> dict:
        with self._lock:
            s = dict(self._state)
        return {
            "today":       s.get("date"),
            "registered":  s.get("registered", 0),
            "target":      s.get("target", 0),
            "pool_total":  self.pool.total_count,
            "pool_active": self.pool.valid_count,
            "pool_cap":    self.cap,
        }

    def trigger(self, count: int = 1) -> dict:
        """手动触发立即注册 N 个账号（管理接口用）。"""
        ok_n = err_n = 0
        for i in range(count):
            acc = self.registrar.register_one()
            if acc:
                self.pool.add(acc)
                ok_n += 1
            else:
                err_n += 1
            if i < count - 1:
                rand_delay(1, 3)
        return {"ok": ok_n, "err": err_n}

# ═══════════════════════════════════════════════════════════════════
# arting.ai 聊天协议层
# ═══════════════════════════════════════════════════════════════════
def _make_body(model: str, messages: list, stream: bool) -> bytes:
    """
    构造 arting.ai 聊天请求体。
    arting 不支持多轮 messages 结构，将所有消息合并为单段文本：
      [System]: … / Assistant: … / (user 直接拼接)
    每次调用生成新 session_id，确保服务端不混淆会话。
    """
    parts = []
    for m in messages:
        role    = m.get("role", "user")
        content = extract_text(m.get("content", ""))
        if role == "system":
            parts.append(f"[System]: {content}")
        elif role == "assistant":
            parts.append(f"Assistant: {content}")
        else:
            parts.append(content)
    text = "\n".join(parts) if len(parts) > 1 else (parts[0] if parts else "")
    return json.dumps({
        "generation_type": MODEL_MAP.get(model, model),
        "task_type":       "ai-chat",
        "session_id":      str(uuid.uuid4()),
        "stream":          stream,
        "files":           [],
        "text":            text,
    }).encode()

def _make_headers(acc: ArtAccount, body: bytes, model: str) -> dict:
    """
    构造聊天请求头。
      Authorization = acc.token（JWT，裸值，无 Bearer 前缀）
      X-Identity-Id = acc.identity_uuid（账号本地指纹，不随重登改变）
      HMAC 签名中的 uid 字段同样使用 identity_uuid
      sec-ch-ua / Sec-Fetch-* / Origin 与注册请求保持同族指纹
    """
    uid = acc.identity_uuid
    p   = _rand_profile()
    hdrs = sign_request("POST", CHAT_PATH, body, uid)
    hdrs.update({
        "Authorization":      acc.token,
        "X-Identity-Id":      uid,
        "Content-Type":       "application/json",
        "User-Agent":         p["ua"],
        "sec-ch-ua":          p["sec_ch_ua"],
        "sec-ch-ua-mobile":   "?0",
        "sec-ch-ua-platform": f'"{p["platform"]}"',
        "X-Bootstrap-Token":  BOOTSTRAP_TOKEN,
        "Origin":             "https://arting.ai",
        "Referer":            f"https://arting.ai/cn/ai-chat?model={MODEL_MAP.get(model, model)}",
        "Sec-Fetch-Site":     "same-origin",
        "Sec-Fetch-Mode":     "cors",
        "Sec-Fetch-Dest":     "empty",
        "Cookie":             f"uuid={uid}",
    })
    return hdrs

def _openai_chunk(req_id: str, model: str, content: str, finish: bool = False) -> str:
    return "data: " + json.dumps({
        "id":      req_id,
        "object":  "chat.completion.chunk",
        "created": int(time.time()),
        "model":   model,
        "choices": [{
            "index":         0,
            "delta":         {"content": content} if not finish else {},
            "finish_reason": "stop" if finish else None,
            "logprobs":      None,
        }],
    }) + "\n\n"

def _err_chunk(msg: str, err_type: str = "proxy_error") -> str:
    return f"data: {json.dumps({'error': {'message': msg, 'type': err_type}})}\n\n"

# ═══════════════════════════════════════════════════════════════════
# 聊天处理（非流式）
# ═══════════════════════════════════════════════════════════════════
def do_chat_sync(pool: ArtPool, registrar: ArtRegistrar,
                 model: str, messages: list):
    # curr[0] 存当前账号，_released 防止外层 finally 重复释放
    curr      = [pool.acquire(timeout=20, max_queue=120)]
    _released = [False]

    if not curr[0]:
        resp = jsonify({"error": {"message": "The server had an error processing your request. Sorry about that!",
                                  "type": "server_error", "param": None, "code": "server_error"}})
        resp.headers["Retry-After"] = "10"
        return resp, 503

    def _do_release(rate_limit: bool = False):
        if _released[0]:
            return
        _released[0] = True
        if rate_limit:
            pool.release_rate_limited(curr[0])
        else:
            pool.release(curr[0])

    def _request(retry: bool = True) -> str:
        body = _make_body(model, messages, False)
        hdrs = _make_headers(curr[0], body, model)
        r    = get_chat_client().post(ARTING_CHAT_URL, headers=hdrs, content=body)
        if r.status_code in (401, 403):
            if retry and registrar.refresh_token(curr[0]):
                return _request(retry=False)
            raise RuntimeError(f"HTTP auth error {r.status_code}")
        if r.status_code != 200:
            raise RuntimeError(f"upstream HTTP {r.status_code}: {r.text[:200]}")
        d    = r.json()
        code = d.get("code")
        if code in AUTH_CODES:
            if retry and registrar.refresh_token(curr[0]):
                return _request(retry=False)
            raise RuntimeError(f"arting auth {code}: {d.get('message','')}")
        if code in UPSTREAM_RETRY_CODES:
            raise RuntimeError(f"__upstream_retry__:{code}:{d.get('message','')}")
        if code in RATE_LIMIT_CODES:
            raise RuntimeError(f"__rate_limit__:{code}:{d.get('message','')}")
        if code is not None and code not in OK_CODES:
            raise RuntimeError(f"arting error {code}: {d.get('message','')}")
        data = d.get("data", {})
        return data.get("result", "") or data.get("content", "") or ""

    def _build_resp(text, mdl):
        p_tok = sum(_estimate_tokens(extract_text(m.get("content", ""))) for m in messages)
        c_tok = _estimate_tokens(text)
        return jsonify({
            "id":      f"chatcmpl-{uuid.uuid4().hex[:29]}",
            "object":  "chat.completion",
            "created": int(time.time()),
            "model":   mdl,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop", "logprobs": None}],
            "usage":   {"prompt_tokens": p_tok, "completion_tokens": c_tok,
                        "total_tokens": p_tok + c_tok},
            "system_fingerprint": f"fp_{secrets.token_hex(5)}",
        })

    try:
        content = _request()
        if not content:
            log("chat", f"sync: empty response ({curr[0].email})")
        return _build_resp(content, model)

    except RuntimeError as e:
        es = str(e)

        if "__upstream_retry__" in es:
            # 100804 上游失败 → 释放当前账号，换一个重试
            log("chat", f"sync: upstream retry ({curr[0].email}): {e}")
            _do_release()
            curr[0] = pool.acquire(timeout=10)
            if curr[0]:
                _released[0] = False          # 新账号需要释放
                try:
                    return _build_resp(_request(retry=True), model)
                except Exception as e2:
                    log("chat", f"sync retry also failed: {e2}")
                    return jsonify({"error": {"message": "The server had an error processing your request. Sorry about that!",
                                              "type": "server_error", "param": None, "code": "server_error"}}), 502
            return jsonify({"error": {"message": "The server had an error processing your request. Sorry about that!",
                                      "type": "server_error", "param": None, "code": "server_error"}}), 502

        if "__rate_limit__" in es:
            # 100099 速率限制 → 冷却当前账号，换一个重试
            log("chat", f"sync: rate limit ({curr[0].email}), cooling & retry")
            _do_release(rate_limit=True)
            curr[0] = pool.acquire(timeout=10)
            if curr[0]:
                _released[0] = False
                try:
                    return _build_resp(_request(retry=True), model)
                except Exception as e2:
                    log("chat", f"sync rate-limit retry failed: {e2}")
                    return jsonify({"error": {"message": "The server had an error processing your request. Sorry about that!",
                                              "type": "server_error", "param": None, "code": "server_error"}}), 502
            return jsonify({"error": {"message": "Rate limit reached for model. Please try again later.",
                                      "type": "rate_limit_error", "param": None, "code": "rate_limit_exceeded"}}), 429

        log("chat", f"sync error: {e}")
        return jsonify({"error": {"message": "The server had an error processing your request. Sorry about that!",
                                  "type": "server_error", "param": None, "code": "server_error"}}), 500

    except Exception as e:
        log("chat", f"sync error: {e}")
        return jsonify({"error": {"message": "The server had an error processing your request. Sorry about that!",
                                  "type": "server_error", "param": None, "code": "server_error"}}), 500

    finally:
        # 统一兜底释放，_released 确保不重复
        if curr[0]:
            _do_release()

# ═══════════════════════════════════════════════════════════════════
# 聊天处理（流式）
# ═══════════════════════════════════════════════════════════════════
def _stream_arting(acc: ArtAccount, model: str, messages: list):
    """
    向 arting.ai 发起流式请求，逐行 yield。
    arting 流式响应为裸文本行（非 SSE 格式），JSON 行表示错误或状态。
    发生鉴权错误时 yield 特殊 sentinel dict，由调用方处理重试。
    """
    body = _make_body(model, messages, True)
    hdrs = _make_headers(acc, body, model)
    with get_chat_client().stream("POST", ARTING_CHAT_URL, headers=hdrs, content=body) as r:
        if r.status_code in (401, 403):
            yield {"__auth_error__": r.status_code}
            return
        if r.status_code != 200:
            yield {"__http_error__": r.status_code}
            return
        for line in r.iter_lines():
            if not line:
                continue
            # arting 有时以 SSE 格式发错误行：data: {"code":...}
            # 必须先剥离 "data: " 前缀再判断是否 JSON
            stripped = line.strip()
            if stripped.startswith("data: "):
                stripped = stripped[6:].strip()
            if stripped.startswith("{"):
                try:
                    yield json.loads(stripped)   # arting JSON 行（错误体等）
                    continue
                except Exception:
                    pass
            if stripped:
                yield stripped                   # 裸文本内容行

def do_chat_stream(pool: ArtPool, registrar: ArtRegistrar,
                   model: str, messages: list):
    acc = pool.acquire(timeout=20, max_queue=120)
    if not acc:
        def _no_acc():
            yield _err_chunk("The server had an error processing your request. Sorry about that!", "server_error")
            yield "data: [DONE]\n\n"
        return Response(stream_with_context(_no_acc()), mimetype="text/event-stream"), 503

    req_id = f"chatcmpl-{uuid.uuid4().hex[:29]}"
    # curr[0] 保存当前使用的账号，用列表包装以便内层函数修改
    curr = [acc]

    def generate():
        released      = False
        _retry_done  = False

        def _release():
            nonlocal released
            if not released:
                released = True
                pool.release(curr[0])

        def _iter(retry_after_refresh: bool = False):
            """
            一次完整的流式迭代。retry_after_refresh=True 时已完成 refresh，
            不再允许再次重试。
            """
            nonlocal _retry_done, released
            for item in _stream_arting(curr[0], model, messages):
                if isinstance(item, str):
                    # 裸文本内容行 → 转 OpenAI chunk
                    yield _openai_chunk(req_id, model, item)
                    continue

                # item 是 dict（arting JSON 行或内部 sentinel）
                if "__auth_error__" in item or "__http_error__" in item:
                    key  = "__auth_error__" if "__auth_error__" in item else "__http_error__"
                    code = item[key]
                    if key == "__auth_error__" and not _retry_done and registrar.refresh_token(curr[0]):
                        _retry_done = True
                        yield from _iter(retry_after_refresh=True)
                        return
                    yield _err_chunk(f"upstream error {code}")
                    yield "data: [DONE]\n\n"
                    return

                arting_code = item.get("code")
                if arting_code in AUTH_CODES and not _retry_done:
                    _retry_done = True
                    if registrar.refresh_token(curr[0]):
                        yield from _iter(retry_after_refresh=True)
                        return
                    yield _err_chunk(f"arting auth {arting_code}: {item.get('message','')}")
                    yield "data: [DONE]\n\n"
                    return
                if arting_code in UPSTREAM_RETRY_CODES and not _retry_done:
                    # 上游 LLM 失败 → 释放当前账号，换一个重试
                    log("chat", f"stream: 100804 retry acc={curr[0].email}")
                    _retry_done = True
                    pool.release(curr[0])
                    released = True
                    acc_new = pool.acquire(timeout=10)
                    if acc_new:
                        curr[0] = acc_new
                        released = False
                        yield from _iter(retry_after_refresh=True)
                    else:
                        yield _err_chunk(item.get("message", f"arting code {arting_code}"), "upstream_error")
                        yield "data: [DONE]\n\n"
                    return
                if arting_code in RATE_LIMIT_CODES and not _retry_done:
                    # 100099 速率限制 → 冷却账号，换新账号重试
                    log("chat", f"stream: rate limit ({curr[0].email}), cooling & retry")
                    _retry_done = True
                    pool.release_rate_limited(curr[0])
                    released = True
                    acc_new = pool.acquire(timeout=10)
                    if acc_new:
                        curr[0] = acc_new
                        released = False
                        yield from _iter(retry_after_refresh=True)
                    else:
                        yield _err_chunk("Rate limit reached for model. Please try again later.", "rate_limit_error")
                        yield "data: [DONE]\n\n"
                    return
                if arting_code is not None and arting_code not in OK_CODES:
                    yield _err_chunk(item.get("message", f"arting code {arting_code}"), "upstream_error")
                    yield "data: [DONE]\n\n"
                    return
                # code in OK_CODES → 忽略（arting 偶尔发状态包）

        try:
            yield from _iter()
            yield _openai_chunk(req_id, model, "", finish=True)
            yield "data: [DONE]\n\n"
        except GeneratorExit:
            log("chat", f"stream: client disconnected ({curr[0].email})")
        except Exception as e:
            log("chat", f"stream error: {e}")
            yield _err_chunk("The server had an error processing your request. Sorry about that!", "server_error")
        finally:
            _release()

    return Response(stream_with_context(generate()), mimetype="text/event-stream")

# ═══════════════════════════════════════════════════════════════════
# Flask 应用
# ═══════════════════════════════════════════════════════════════════
app         = Flask(__name__)


_pool:       Optional[ArtPool]      = None
_registrar:  Optional[ArtRegistrar] = None
_scheduler:  Optional[DailyScheduler] = None
_admin_pw:   str                    = "yu123456"

def _check_admin():
    if not _admin_pw:
        return None
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth[7:] != _admin_pw:
        return jsonify({"error": "Unauthorized"}), 401
    return None

# ── CORS 预检 + 伪装响应头 ────────────────────────────────────────
@app.before_request
def _handle_options():
    """处理 OPTIONS 预检，避免浏览器客户端 405；同时让 CDN/探针觉得是正常 nginx。"""
    if request.method == "OPTIONS":
        resp = Response(status=204)
        resp.headers["Access-Control-Allow-Origin"]  = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-Requested-With"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Max-Age"]       = "86400"
        return resp

@app.after_request
def _stealth_headers(resp):
    """注入 CORS 和 OpenAI 兼容伪装头；Server 头由 gunicorn.http.wsgi.Response.version 统一注入。"""
    # ── CORS：允许所有来源（OpenAI API 同样开放）───────────────
    resp.headers["Access-Control-Allow-Origin"]  = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-Requested-With"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    # ── OpenAI 风格响应头（聊天/模型接口）──────────────────────
    if request.path.rstrip("/").endswith(("/chat/completions", "/models",
                                          "/v1/completions")):
        resp.headers["X-Request-Id"]          = secrets.token_hex(16)
        resp.headers["openai-organization"]   = "org-" + secrets.token_urlsafe(20)
        resp.headers["openai-processing-ms"]  = str(random.randint(150, 2500))
        resp.headers["openai-version"]        = "2020-10-01"
        resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp

# ── 聊天路由 ──────────────────────────────────────────────────────
@app.route("/ar/v1/models")
@app.route("/v1/models")
def ar_models():
    _created = 1677610602  # 固定时间戳，避免每次请求不同引发指纹怀疑
    return jsonify({
        "object": "list",
        "data":   [{"id": k, "object": "model", "created": _created,
                    "owned_by": "openai", "permission": [], "root": k, "parent": None}
                   for k in MODEL_MAP],
    })

@app.route("/ar/v1/chat/completions", methods=["POST"])
@app.route("/v1/chat/completions", methods=["POST"])
def ar_chat():
    if _pool is None or _registrar is None:
        return jsonify({"error": "pool not ready"}), 503
    body    = request.get_json(force=True) or {}
    model   = body.get("model", "gpt-5.2")
    msgs    = body.get("messages", [])
    stream  = body.get("stream", False)
    # 去掉 new-api 可能透传的 [ar] 前缀
    if model.startswith("[ar]"):
        model = model[4:]
    if stream:
        return do_chat_stream(_pool, _registrar, model, msgs)
    return do_chat_sync(_pool, _registrar, model, msgs)

# ── Admin 路由 ─────────────────────────────────────────────────────
@app.route("/ar/admin/status")
def ar_status():
    denied = _check_admin()
    if denied:
        return denied
    return jsonify({
        "ok":         True,
        "service":    "art-register-tool",
        "version":    "3.0",
        "pool":       _pool.stats() if _pool else {},
        "maintainer": _scheduler.sched_stats() if _scheduler else {},
        "proxy": {
            "resi_total":  len(RESI_PORTS),
            "resi_cooled": len([p for p, u in _proxy_cooldown.items() if u > time.time()]),
        },
        "settings": {
            "reg_proxy":            (_pool.reg_proxy if _pool else None) or "AUTO(resi)",
            "chat_proxy":           _CHAT_PROXY_STR or "DIRECT",
            "admin_pw_set":         bool(_admin_pw),
            "proactive_rpm":        int(_runtime_cfg.get("proactive_rpm", 2)),
            "proactive_rpm_window": int(_runtime_cfg.get("proactive_rpm_window", 60)),
            "release_cooldown":     int(_runtime_cfg.get("release_cooldown", 30)),
            "free_usage_cap":       int(_runtime_cfg.get("free_usage_cap", FREE_USAGE_CAP)),
            "usage_reserve_min":    int(_runtime_cfg.get("usage_reserve_min", 2)),
            "usage_reserve_max":    int(_runtime_cfg.get("usage_reserve_max", 5)),
        },
    })

@app.route("/ar/admin/sched")
def ar_sched():
    denied = _check_admin()
    if denied:
        return denied
    return jsonify(_scheduler.sched_stats() if _scheduler else {"error": "not started"})

@app.route("/ar/admin/register", methods=["POST"])
def ar_register():
    denied = _check_admin()
    if denied:
        return denied
    if _scheduler is None:
        return jsonify({"error": "maintainer not ready"}), 503
    count  = min(int((request.get_json(force=True) or {}).get("count", 1)), 20)
    result = _scheduler.trigger(count)
    return jsonify({"ok": True, **result, "pool": _pool.stats()})

@app.route("/ar/admin/accounts")
def ar_accounts():
    denied = _check_admin()
    if denied:
        return denied
    limit  = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    all_   = _pool.accounts_detail() if _pool else []
    return jsonify({"total": len(all_), "offset": offset, "limit": limit,
                    "accounts": all_[offset: offset + limit]})

@app.route("/ar/admin/accounts/<acc_id>/disable", methods=["POST"])
def ar_disable(acc_id: str):
    denied = _check_admin()
    if denied:
        return denied
    reason = (request.get_json(force=True) or {}).get("reason", "manual")
    return jsonify({"ok": _pool.set_enabled(acc_id, False, reason) if _pool else False})

@app.route("/ar/admin/accounts/<acc_id>/enable", methods=["POST"])
def ar_enable(acc_id: str):
    denied = _check_admin()
    if denied:
        return denied
    return jsonify({"ok": _pool.set_enabled(acc_id, True) if _pool else False})

# ── 健康检查 ───────────────────────────────────────────────────────

# ── Admin Panel HTML ──────────────────────────────────────────────
@app.route("/ar/admin/")
@app.route("/ar/admin")
def ar_admin_html():
    from pathlib import Path as _P
    html_file = _P(__file__).parent / "admin_panel.html"
    if html_file.exists():
        return Response(html_file.read_text(), mimetype="text/html")
    return Response("<h2>admin_panel.html not found</h2>", mimetype="text/html", status=404)

@app.route("/ar/admin/logs")
def ar_admin_logs():
    denied = _check_admin()
    if denied: return denied
    n = min(int(request.args.get("n", 200)), 500)
    with _log_ring_lock:
        lines = list(_log_ring)[-n:] if _log_ring else []
    return jsonify({"lines": lines, "total": len(_log_ring) if _log_ring else 0})

@app.route("/ar/admin/restore", methods=["POST"])
def ar_admin_restore():
    denied = _check_admin()
    if denied: return denied
    if _pool is None:
        return jsonify({"error": "pool not ready"}), 503
    restored = _pool.restore_quarantined()
    log("admin", f"手动解除隔离: {restored} 个")
    return jsonify({"ok": True, "restored": restored, "pool": _pool.stats()})

_SETTINGS_FIELDS = [
    {"key":"rate_limit_cooldown","label":"速率冷却时长(s)","desc":"100099 账号冷却秒数","type":"number","default":60},
    {"key":"proxy_cooldown_ttl","label":"代理冷却时长(s)","desc":"代理端口失效后冷却秒数","type":"number","default":600},
    {"key":"reg_phase1_min","label":"阶段一每日最少注册","desc":"pool < cap 时最少注册数","type":"number","default":80},
    {"key":"reg_phase1_max","label":"阶段一每日最多注册","desc":"pool < cap 时最多注册数","type":"number","default":120},
    {"key":"reg_phase2_min","label":"阶段二每日最少注册","desc":"pool >= cap 时最少注册数","type":"number","default":24},
    {"key":"reg_phase2_max","label":"阶段二每日最多注册","desc":"pool >= cap 时最多注册数","type":"number","default":36},
    {"key":"pool_cap","label":"阶段切换阈值","desc":"总量超过此值后切换为低速注册（阶段二），不阻止继续注册","type":"number","default":300},
    {"key":"release_cooldown","label":"账号冷却时长(s)","desc":"释放后硬占用冷却秒数，期间不分配给新请求（默认30s，与占用时长合并显示）","type":"number","default":30},
    {"key":"free_usage_cap","label":"每账号每日额度基准","desc":"arting 侧每日上限60次，此处设基准值；实际每账号 max_usage=基准-随机余量，重启或解隔离时生效","type":"number","default":60},
    {"key":"usage_reserve_min","label":"余量下限(次)","desc":"每账号随机保留余量的下限，防触及 arting 硬限","type":"number","default":2},
    {"key":"usage_reserve_max","label":"余量上限(次)","desc":"每账号随机保留余量的上限，增加账号行为随机性","type":"number","default":5},
    {"key":"proactive_rpm","label":"主动限速(次/窗口)","desc":"每账号在窗口内最多发出的请求次数，超限自动跳过，0=禁用","type":"number","default":2},
    {"key":"proactive_rpm_window","label":"限速窗口(s)","desc":"主动限速滑动窗口时长（秒），与 proactive_rpm 配合使用","type":"number","default":60},
    {"key":"chat_proxy","label":"聊天代理","desc":"socks5://host:port 或留空直连","type":"text","default":""},
]

@app.route("/ar/admin/settings", methods=["GET", "PATCH"])
def ar_admin_settings():
    denied = _check_admin()
    if denied: return denied
    global _CHAT_CLIENT, _CHAT_PROXY_STR
    if request.method == "GET":
        with _runtime_cfg_lock:
            vals = dict(_runtime_cfg)
        vals["chat_proxy"] = _CHAT_PROXY_STR or ""
        return jsonify({"fields": _SETTINGS_FIELDS, "values": vals})
    # PATCH
    body = request.get_json(force=True) or {}
    applied = {}
    with _runtime_cfg_lock:
        for f in _SETTINGS_FIELDS:
            k = f["key"]
            if k not in body: continue
            if k == "chat_proxy":
                new_proxy = str(body[k]).strip() or None
                if new_proxy != (_CHAT_PROXY_STR or None):
                    _CHAT_PROXY_STR = new_proxy
                    _CHAT_CLIENT = httpx.Client(
                        timeout=httpx.Timeout(connect=10, read=180, write=30, pool=10),
                        limits=httpx.Limits(max_connections=400, max_keepalive_connections=150),
                        **( {"proxy": new_proxy} if new_proxy else {}),
                    )
                    applied[k] = new_proxy or "DIRECT"
                _runtime_cfg[k] = new_proxy or ""
            elif f["type"] == "number":
                _runtime_cfg[k] = float(body[k]) if "." in str(body[k]) else int(body[k])
                applied[k] = _runtime_cfg[k]
    # 同步 pool_cap 到 DailyScheduler
    if "pool_cap" in applied and _scheduler is not None:
        _scheduler.cap = int(applied["pool_cap"])
    # free_usage_cap 变更：仅影响新注册账号和次日解隔离时的 max_usage，
    # 不批量覆写已有账号（避免提前隔离快用完的账号）
    if "free_usage_cap" in applied:
        log("admin", f"free_usage_cap → {applied['free_usage_cap']}，新账号及次日解隔离生效")
    log("admin", f"参数热改: {applied}")
    return jsonify({"ok": True, "applied": applied, "current": dict(_runtime_cfg)})

@app.route("/ar/admin/accounts/<acc_id>/delete", methods=["POST"])
def ar_delete_account(acc_id: str):
    denied = _check_admin()
    if denied: return denied
    if _pool is None:
        return jsonify({"error": "pool not ready"}), 503
    ok = _pool.delete_account(acc_id)
    if ok:
        log("admin", f"删除账号 {acc_id}")
    return jsonify({"ok": ok})

@app.route("/health")
@app.route("/ar/health")
def health():
    return jsonify({
        "status":  "ok",
        "service": "art-register-tool",
        "version": "3.0",
        "pool":    _pool.stats() if _pool else {},
    })

# ═══════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════
def main():
    import argparse
    ap = argparse.ArgumentParser(description="Art-Register-Tool v3.0 — arting.ai 反代服务")
    ap.add_argument("--port",       type=int, default=9097)
    ap.add_argument("--host",       default="0.0.0.0")
    ap.add_argument("--pool-size",  type=int, default=5,   help="启动时预注册账号数")
    ap.add_argument("--reg-proxy",  default=os.environ.get("ART_REG_PROXY", ""),
                    help="注册代理 socks5://… (留空=自动选住宅端口)")
    ap.add_argument("--chat-proxy", default=os.environ.get("ART_CHAT_PROXY", ""),
                    help="聊天代理 socks5://… (留空=直连)")
    ap.add_argument("--admin-pw",   default=os.environ.get("ART_ADMIN_PW", "yu123456"))
    ap.add_argument("--no-preload", action="store_true", help="跳过启动预注册")
    ap.add_argument("--no-maint",   action="store_true", help="不启动后台调度器")
    ap.add_argument("--cap",        type=int, default=POOL_CAP, help="账号池上限（达到后每日 30 ±20%）")
    args = ap.parse_args()

    global _pool, _registrar, _scheduler, _admin_pw, _CHAT_CLIENT, _CHAT_PROXY_STR
    _admin_pw = args.admin_pw

    # 初始化共享 httpx 客户端（连接池，高并发复用 TCP 连接到 arting.ai）
    chat_proxy       = args.chat_proxy or None
    _CHAT_PROXY_STR  = chat_proxy
    _CHAT_CLIENT     = httpx.Client(
        timeout=httpx.Timeout(connect=10, read=180, write=30, pool=10),
        limits=httpx.Limits(max_connections=400, max_keepalive_connections=150),
        **( {"proxy": chat_proxy} if chat_proxy else {}),
    )

    if not _HAS_MAIL_MGR:
        print("ERROR: all_temp_mail.py not found", flush=True)
        sys.exit(1)

    reg_proxy  = args.reg_proxy or None
    mail_mgr   = create_normal_manager(proxy=reg_proxy)
    _pool      = ArtPool(reg_proxy=reg_proxy)
    _registrar = ArtRegistrar(_pool, mail_mgr)

    if not args.no_preload and _pool.valid_count < args.pool_size:
        need = args.pool_size - _pool.valid_count
        log("init", f"pool valid={_pool.valid_count}, pre-registering {need}…")
        for i in range(need):
            acc = _registrar.register_one()
            if acc:
                _pool.add(acc)
            if i < need - 1:
                rand_delay(1, 3)

    log("init", f"pool ready: {_pool.stats()}")

    if not args.no_maint:
        _scheduler = DailyScheduler(
            pool=_pool, registrar=_registrar,
            cap=args.cap,
        )
        _scheduler.start()

    pw_hint = (_admin_pw[:4] + "…") if len(_admin_pw) >= 4 else _admin_pw
    print(f"\n{'='*56}", flush=True)
    print(f" Art-Register-Tool v3.0  [ar] prefix routes", flush=True)
    print(f" Port      : {args.port}", flush=True)
    print(f" Pool      : {_pool.stats()}", flush=True)
    print(f" Reg proxy : {reg_proxy or 'AUTO(resi)'}", flush=True)
    print(f" Chat proxy: {chat_proxy or 'DIRECT'}", flush=True)
    print(f" Admin PW  : {'set (' + pw_hint + ')' if _admin_pw else '⚠ NONE'}", flush=True)
    print(f" Endpoints :", flush=True)
    print(f"   POST http://0.0.0.0:{args.port}/ar/v1/chat/completions", flush=True)
    print(f"   GET  http://0.0.0.0:{args.port}/ar/admin/status (Bearer {pw_hint})", flush=True)
    print(f" 高并发推荐:", flush=True)
    print(f"   [gevent] gunicorn -w 1 -k gevent --worker-connections 1000 --bind 0.0.0.0:{args.port} art_register_tool:app", flush=True)
    print(f"{'='*56}\n", flush=True)

    # ── 生产模式：gunicorn（-w 1 单进程保证池共享，gthread 高并发 I/O）──
    try:
        import gunicorn.app.base  # noqa: F401
        from gunicorn.app.base import BaseApplication
        class _StandaloneApp(BaseApplication):
            def __init__(self, application, options=None):
                self.options     = options or {}
                self.application = application
                super().__init__()
            def load_config(self):
                for k, v in self.options.items():
                    if k in self.cfg.settings:
                        self.cfg.set(k.lower(), v)
            def load(self):
                return self.application
        # ── Worker 自动选择：gevent（协程，单线程，500-1000并发）> gthread（线程池，<300并发）──
        if _GEVENT:
            opts = {
                "bind":                 f"{args.host}:{args.port}",
                "workers":              1,       # 单进程确保账号池内存共享
                "worker_class":         "gevent",
                "worker_connections":   1000,    # 最大并发 greenlet 数
                "timeout":              300,     # 流式请求需要长超时
                "keepalive":            5,
                "worker_rlimit_nofile": 65535,
                "accesslog":            "-",
                "errorlog":             "-",
                "loglevel":             "warning",
            }
            mode_msg = "gevent（单线程协程，最高并发）"
        else:
            opts = {
                "bind":                 f"{args.host}:{args.port}",
                "workers":              1,
                "worker_class":         "gthread",
                "threads":              300,
                "timeout":              300,
                "keepalive":            5,
                "worker_rlimit_nofile": 65535,
                "accesslog":            "-",
                "errorlog":             "-",
                "loglevel":             "warning",
            }
            mode_msg = "gthread 300（线程池，gevent 不可用）"
        import gunicorn.http.wsgi as _gwsgi
        _gwsgi.SERVER = "nginx/1.24.0"
        log("init", f"启动 gunicorn {mode_msg}")
        _StandaloneApp(app, opts).run()
    except ImportError:
        log("init", "gunicorn 未安装，回退到 Flask dev server（仅开发用）")
        app.run(host=args.host, port=args.port, debug=False, threaded=True)

if __name__ == "__main__":
    main()
