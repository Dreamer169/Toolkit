"""
FunCaptcha / Arkose Labs 自动打码模块
支持:
  - 2captcha   (https://2captcha.com)
  - CapMonster (https://capmonster.cloud)

Microsoft Outlook 注册页面使用的 Arkose Labs publicKey:
  B7D8911C-5CC8-A9A3-35B0-554ACEE604DA

用法:
    solver = build_solver("2captcha", "YOUR_API_KEY")
    token  = solver.solve(page_url="https://signup.live.com/signup", blob=session_blob)
    # 然后将 token 注入页面
"""

import time
import httpx

ARKOSE_PUBLIC_KEY = "B7D8911C-5CC8-A9A3-35B0-554ACEE604DA"


# ── 2captcha ────────────────────────────────────────────────────────────────
class TwoCaptchaSolver:
    BASE = "https://2captcha.com"

    def __init__(self, api_key: str):
        self.api_key = api_key.strip()

    def solve(self, page_url: str, blob: str | None = None) -> str:
        """提交任务 → 轮询直到拿到 token，返回 token 字符串"""
        data: dict = {
            "key":       self.api_key,
            "method":    "funcaptcha",
            "publickey": ARKOSE_PUBLIC_KEY,
            "pageurl":   page_url,
            "json":      1,
        }
        if blob:
            data["data[blob]"] = blob

        resp = httpx.post(f"{self.BASE}/in.php", data=data, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        if result.get("status") != 1:
            raise RuntimeError(f"2captcha 提交失败: {result}")

        task_id = result["request"]
        print(f"[2captcha] 任务已提交 ID={task_id}，等待解答…", flush=True)

        for _ in range(72):          # 最多等 6 分钟
            time.sleep(5)
            resp = httpx.get(f"{self.BASE}/res.php", params={
                "key":    self.api_key,
                "action": "get",
                "id":     task_id,
                "json":   1,
            }, timeout=15)
            resp.raise_for_status()
            result = resp.json()
            if result.get("status") == 1:
                token = result["request"]
                print(f"[2captcha] ✅ 解答成功，token 长度={len(token)}", flush=True)
                return token
            if result.get("request") not in ("CAPCHA_NOT_READY", "CAPCHA_NOT_READY"):
                raise RuntimeError(f"2captcha 错误: {result}")

        raise TimeoutError("2captcha 超时未返回结果")


# ── CapMonster ──────────────────────────────────────────────────────────────
class CapMonsterSolver:
    BASE = "https://api.capmonster.cloud"

    def __init__(self, api_key: str):
        self.api_key = api_key.strip()

    def solve(self, page_url: str, blob: str | None = None) -> str:
        task: dict = {
            "type":             "FunCaptchaTaskProxyless",
            "websiteURL":       page_url,
            "websitePublicKey": ARKOSE_PUBLIC_KEY,
        }
        if blob:
            task["data"] = {"blob": blob}

        resp = httpx.post(f"{self.BASE}/createTask", json={
            "clientKey": self.api_key,
            "task":      task,
        }, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        if result.get("errorId") != 0:
            raise RuntimeError(f"CapMonster 提交失败: {result}")

        task_id = result["taskId"]
        print(f"[CapMonster] 任务已提交 ID={task_id}，等待解答…", flush=True)

        for _ in range(72):
            time.sleep(5)
            resp = httpx.post(f"{self.BASE}/getTaskResult", json={
                "clientKey": self.api_key,
                "taskId":    task_id,
            }, timeout=15)
            resp.raise_for_status()
            result = resp.json()
            if result.get("status") == "ready":
                token = result["solution"]["token"]
                print(f"[CapMonster] ✅ 解答成功，token 长度={len(token)}", flush=True)
                return token
            if result.get("errorId") not in (0, None):
                raise RuntimeError(f"CapMonster 错误: {result}")

        raise TimeoutError("CapMonster 超时未返回结果")


# ── 工厂 ────────────────────────────────────────────────────────────────────
def build_solver(service: str, api_key: str):
    """
    service: "2captcha" | "capmonster"
    返回对应的 Solver 对象，均暴露 .solve(page_url, blob=None) -> str
    """
    if not api_key:
        return None
    s = service.lower().replace("-", "").replace("_", "")
    if s == "2captcha":
        return TwoCaptchaSolver(api_key)
    if s in ("capmonster", "capmonstercloud"):
        return CapMonsterSolver(api_key)
    raise ValueError(f"未知打码服务: {service}，支持 2captcha / capmonster")
