#!/usr/bin/env python3
"""
统一打码模块 v3 — 支持 FunCaptcha (Arkose) + Turnstile + hCaptcha
提供商: 2captcha | capmonster | yescaptcha | capsolver
支持自动切换: 当首选提供商失败时依次尝试备选
"""

import time
import httpx

ARKOSE_PUBLIC_KEY   = "B7D8911C-5CC8-A9A3-35B0-554ACEE604DA"
TURNSTILE_SITE_KEY  = "0x4AAAAAAAMNIvC45A4Wjjln"    # Cursor.sh
REPLIT_HCAPTCHA_KEY = "a5f74b19-9e45-40e0-b45d-47ff91b7a6c2"  # Replit signup


# ── 基础轮询工具 ────────────────────────────────────────────────────────────
def _poll(check_fn, max_tries=72, interval=5):
    for _ in range(max_tries):
        time.sleep(interval)
        result = check_fn()
        if result is not None:
            return result
    raise TimeoutError("打码超时")


# ── 2captcha ────────────────────────────────────────────────────────────────
class TwoCaptchaSolver:
    BASE = "https://2captcha.com"

    def __init__(self, api_key: str):
        self.api_key = api_key.strip()

    def _submit(self, data: dict) -> str:
        resp = httpx.post(f"{self.BASE}/in.php", data=data, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        if result.get("status") != 1:
            raise RuntimeError(f"2captcha 提交失败: {result}")
        return result["request"]

    def _get(self, task_id: str):
        r = httpx.get(f"{self.BASE}/res.php",
                      params={"key": self.api_key, "action": "get",
                              "id": task_id, "json": 1}, timeout=15).json()
        if r.get("status") == 1:
            return r["request"]
        if r.get("request") not in ("CAPCHA_NOT_READY",):
            raise RuntimeError(f"2captcha 错误: {r}")
        return None

    def solve_funcaptcha(self, page_url: str, blob=None) -> str:
        data = {"key": self.api_key, "method": "funcaptcha",
                "publickey": ARKOSE_PUBLIC_KEY, "pageurl": page_url, "json": 1}
        if blob:
            data["data[blob]"] = blob
        task_id = self._submit(data)
        print(f"[2captcha/arkose] task={task_id}", flush=True)
        return _poll(lambda: self._get(task_id))

    def solve_turnstile(self, page_url: str, site_key: str) -> str:
        task_id = self._submit({"key": self.api_key, "method": "turnstile",
                                "sitekey": site_key, "pageurl": page_url, "json": 1})
        print(f"[2captcha/turnstile] task={task_id}", flush=True)
        return _poll(lambda: self._get(task_id))

    def solve_hcaptcha(self, page_url: str, site_key: str) -> str:
        task_id = self._submit({"key": self.api_key, "method": "hcaptcha",
                                "sitekey": site_key, "pageurl": page_url, "json": 1})
        print(f"[2captcha/hcaptcha] task={task_id}", flush=True)
        return _poll(lambda: self._get(task_id))


# ── CapMonster ──────────────────────────────────────────────────────────────
class CapMonsterSolver:
    BASE = "https://api.capmonster.cloud"

    def __init__(self, api_key: str):
        self.api_key = api_key.strip()

    def _create(self, task: dict) -> str:
        r = httpx.post(f"{self.BASE}/createTask",
                       json={"clientKey": self.api_key, "task": task}, timeout=30).json()
        if r.get("errorId") != 0:
            raise RuntimeError(f"CapMonster 提交失败: {r}")
        return r["taskId"]

    def _result(self, task_id: str):
        r = httpx.post(f"{self.BASE}/getTaskResult",
                       json={"clientKey": self.api_key, "taskId": task_id}, timeout=15).json()
        if r.get("status") == "ready":
            return r["solution"]["token"]
        if r.get("errorId") not in (0, None):
            raise RuntimeError(f"CapMonster 错误: {r}")
        return None

    def solve_funcaptcha(self, page_url: str, blob=None) -> str:
        task = {"type": "FunCaptchaTaskProxyless", "websiteURL": page_url,
                "websitePublicKey": ARKOSE_PUBLIC_KEY}
        if blob:
            task["data"] = {"blob": blob}
        tid = self._create(task)
        print(f"[CapMonster/arkose] task={tid}", flush=True)
        return _poll(lambda: self._result(tid))

    def solve_turnstile(self, page_url: str, site_key: str) -> str:
        tid = self._create({"type": "TurnstileTaskProxyless",
                            "websiteURL": page_url, "websiteKey": site_key})
        print(f"[CapMonster/turnstile] task={tid}", flush=True)
        return _poll(lambda: self._result(tid))

    def solve_hcaptcha(self, page_url: str, site_key: str) -> str:
        tid = self._create({"type": "HCaptchaTaskProxyless",
                            "websiteURL": page_url, "websiteKey": site_key})
        print(f"[CapMonster/hcaptcha] task={tid}", flush=True)
        return _poll(lambda: self._result(tid))


# ── YesCaptcha ──────────────────────────────────────────────────────────────
class YesCaptchaSolver:
    BASE = "https://api.yescaptcha.com"

    def __init__(self, api_key: str):
        self.api_key = api_key.strip()

    def _create(self, task: dict) -> str:
        r = httpx.post(f"{self.BASE}/createTask",
                       json={"clientKey": self.api_key, "task": task}, timeout=30).json()
        if r.get("errorId", 1) != 0:
            raise RuntimeError(f"YesCaptcha 提交失败: {r}")
        return r["taskId"]

    def _result(self, task_id: str):
        r = httpx.post(f"{self.BASE}/getTaskResult",
                       json={"clientKey": self.api_key, "taskId": task_id}, timeout=15).json()
        if r.get("status") == "ready":
            return r["solution"]["token"]
        if r.get("errorId", 0) != 0:
            raise RuntimeError(f"YesCaptcha 错误: {r}")
        return None

    def solve_funcaptcha(self, page_url: str, blob=None) -> str:
        task = {"type": "FunCaptchaTaskProxylessM1", "websiteURL": page_url,
                "websitePublicKey": ARKOSE_PUBLIC_KEY}
        if blob:
            task["data"] = {"blob": blob}
        tid = self._create(task)
        print(f"[YesCaptcha/arkose] task={tid}", flush=True)
        return _poll(lambda: self._result(tid))

    def solve_turnstile(self, page_url: str, site_key: str) -> str:
        tid = self._create({"type": "TurnstileTaskProxylessM1",
                            "websiteURL": page_url, "websiteKey": site_key})
        print(f"[YesCaptcha/turnstile] task={tid}", flush=True)
        return _poll(lambda: self._result(tid))

    def solve_hcaptcha(self, page_url: str, site_key: str) -> str:
        tid = self._create({"type": "HCaptchaTaskProxylessM1",
                            "websiteURL": page_url, "websiteKey": site_key})
        print(f"[YesCaptcha/hcaptcha] task={tid}", flush=True)
        return _poll(lambda: self._result(tid))


# ── CapSolver ───────────────────────────────────────────────────────────────
class CapSolverSolver:
    BASE = "https://api.capsolver.com"

    def __init__(self, api_key: str):
        self.api_key = api_key.strip()

    def _create(self, task: dict) -> str:
        r = httpx.post(f"{self.BASE}/createTask",
                       json={"clientKey": self.api_key, "task": task}, timeout=30).json()
        if r.get("errorId", 0) != 0:
            raise RuntimeError(f"CapSolver 提交失败: {r}")
        return r["taskId"]

    def _result(self, task_id: str):
        r = httpx.post(f"{self.BASE}/getTaskResult",
                       json={"clientKey": self.api_key, "taskId": task_id}, timeout=15).json()
        if r.get("status") == "ready":
            return r["solution"]["token"]
        if r.get("errorId", 0) != 0:
            raise RuntimeError(f"CapSolver 错误: {r}")
        return None

    def solve_funcaptcha(self, page_url: str, blob=None) -> str:
        task = {"type": "FunCaptchaTaskProxyLess", "websiteURL": page_url,
                "websitePublicKey": ARKOSE_PUBLIC_KEY}
        if blob:
            task["funcaptchaApiJSSubdomain"] = blob
        tid = self._create(task)
        print(f"[CapSolver/arkose] task={tid}", flush=True)
        return _poll(lambda: self._result(tid))

    def solve_turnstile(self, page_url: str, site_key: str) -> str:
        tid = self._create({"type": "AntiTurnstileTaskProxyLess",
                            "websiteURL": page_url, "websiteKey": site_key})
        print(f"[CapSolver/turnstile] task={tid}", flush=True)
        return _poll(lambda: self._result(tid))

    def solve_hcaptcha(self, page_url: str, site_key: str) -> str:
        tid = self._create({"type": "HCaptchaTaskProxyLess",
                            "websiteURL": page_url, "websiteKey": site_key})
        print(f"[CapSolver/hcaptcha] task={tid}", flush=True)
        return _poll(lambda: self._result(tid))


# ── 工厂 + 自动切换 ──────────────────────────────────────────────────────────
_SOLVER_MAP = {
    "2captcha":   TwoCaptchaSolver,
    "capmonster": CapMonsterSolver,
    "yescaptcha": YesCaptchaSolver,
    "capsolver":  CapSolverSolver,
    "cap_solver": CapSolverSolver,
}


def build_solver(service: str, api_key: str):
    if not api_key:
        return None
    cls = _SOLVER_MAP.get(service.lower()) or _SOLVER_MAP.get(service.lower().replace("_", ""))
    if not cls:
        raise ValueError(f"未知打码服务: {service}，支持 2captcha/capmonster/yescaptcha/capsolver")
    return cls(api_key)


def solve_with_fallback(providers: list, captcha_type: str, **kwargs) -> str:
    """
    providers: [("yescaptcha", "key1"), ("capsolver", "key2"), ...]
    captcha_type: "funcaptcha" | "turnstile" | "hcaptcha"
    kwargs: page_url=, blob=None (funcaptcha), site_key= (turnstile/hcaptcha)
    """
    last_err = None
    for service, api_key in providers:
        try:
            solver = build_solver(service, api_key)
            if solver is None:
                continue
            if captcha_type == "funcaptcha":
                return solver.solve_funcaptcha(kwargs["page_url"], blob=kwargs.get("blob"))
            elif captcha_type == "turnstile":
                return solver.solve_turnstile(kwargs["page_url"], kwargs["site_key"])
            elif captcha_type == "hcaptcha":
                return solver.solve_hcaptcha(kwargs["page_url"], kwargs["site_key"])
            else:
                raise ValueError(f"未知 captcha_type: {captcha_type}")
        except Exception as e:
            print(f"[captcha] {service} 失败: {e}，尝试下一个...", flush=True)
            last_err = e
    raise RuntimeError(f"所有打码服务均失败，最后错误: {last_err}")
