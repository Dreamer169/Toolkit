# Toolkit / Repository Structure

  ## 关键路径 (2026-04-25 整合后)

  ```
  /root/Toolkit/                          ← git working tree (Dreamer169/Toolkit @ main)
  ├── artifacts/api-server/
  │   ├── replit_register.py              ← Python signup orchestrator
  │   ├── outlook_register.py
  │   └── dist/index.mjs                  ← TS api-server 编译产物
  ├── browser-model/                      ← 唯一权威源 (PM2 通过 symlink 跑这里)
  │   └── artifacts/api-server/
  │       ├── src/lib/
  │       │   ├── renderer.ts             ← getBrowser + cf-warmup + v8.10 bootstrap
  │       │   ├── cdp-broker.ts           ← 仅服务于外部 CDP attach (cdp-ws-server 用)
  │       │   ├── cdp-ws-server.ts        ← /api/cdp/ws WS bridge
  │       │   ├── google-route.ts         ← v7.76 sticky-per-context Google 子请求路由
  │       │   └── ...
  │       └── dist/index.mjs              ← node ./build.mjs 产物 (gitignored)
  ├── start-browser-model.sh              ← PM2 browser-model 入口
  └── STRUCTURE.md                        ← 本文件

  /root/browser-model                     ← symlink → /root/Toolkit/browser-model
  ```

  ## 历史教训 (为什么有 symlink)

  2026-04-25 之前, `/root/browser-model/` 是与 `/root/Toolkit/browser-model/` 完全独立的两个目录:
  - runtime 副本 = `/root/browser-model/` (PM2 实际运行)
  - git 副本 = `/root/Toolkit/browser-model/` (commit/推送用)
  - 没有任何同步机制, 靠人手 `cp` 维持

  后果:
  - 在 git 副本改代码 + `pm2 restart` → 看不到任何变化 (PM2 跑的是另一份)
  - 在 runtime 副本改代码 + 跑通了 → 改动不到 git (改的是 untracked 副本)
  - 关键文件长期漂移: v7.76 google-route.ts 只存在于 runtime; v8.10 死码只存在于 git 的 cdp-broker.ts

  整合方案:
  1. 提升 v7.76 google-route.ts: runtime → git
  2. 删 runtime cdp-broker.ts 的 v8.10 死码 (renderer.ts 不调它)
  3. 清理两边所有 `.bak*` 残留
  4. `rm -rf /root/browser-model && ln -s /root/Toolkit/browser-model /root/browser-model`
  5. 在 symlinked 树里 `node ./build.mjs` 重建 dist
  6. PM2 restart browser-model

  ## 工作流程 (整合后)

  ```bash
  # 1. 改 TS 源
  vim /root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts

  # 2. 重建 dist (任一路径都可, 符号链接同一目录)
  cd /root/browser-model/artifacts/api-server && node ./build.mjs

  # 3. 重启 PM2
  pm2 restart browser-model

  # 4. 提交 git (Toolkit 仓库)
  cd /root/Toolkit && git add browser-model/ && (commit && push)
  ```

  ## PM2 服务清单

  | Service | Script Path | 关键说明 |
  |---------|-------------|---------|
  | `browser-model` | `/root/Toolkit/start-browser-model.sh` | 内部启 `/root/browser-model/.../dist/index.mjs` (符号链接到 Toolkit) |
  | `api-server` | `/root/Toolkit/artifacts/api-server/dist/index.mjs` | TS HTTP 服务, shell-out 调用 `replit_register.py` |
  | `xray` | `/usr/bin/xray run -confdir /root/xray-confs` | 25+ VLESS outbound (10800-10832 SOCKS5) + WARP backup |
  | `xvfb` | `Xvfb :99 -screen 0 1920x1080x24` | broker chromium 用的虚拟显示 |

  ## 上游/出口拓扑

  ```
  Python register → CDP (ws://localhost:8092/api/cdp/ws)
                   ↓
         broker chromium (--remote-debugging-port=9222, headed full chrome)
                   ↓ proxy-server=
         socks5://127.0.0.1:40000  (WARP, 解 CF challenge, 出口 104.28.x.x)
                   ↓ google-route 拦截
         *.google/*.gstatic/*.recaptcha/*.youtube → sticky-per-ctx SOCKS (10820/10822/10824/...)
  ```

  WARP 走 replit.com (解 CF), Google 子请求走 SOCKS 池 (抬 reCAPTCHA score).
  v7.76 sticky-per-context 保证同 ctx 内 Google 请求**永远同一出口 IP** (避免 score 归零).
  
## obvious.ai 沙箱池

persistent e2b Debian 13 / 2vCPU / 8GB 沙箱，用于跑 Playwright / Python 脚本（诊断、cookie预热等）。

| 文件/目录 | 作用 |
|-----------|------|
| `scripts/obvious_provision.py` | 一键注册新 obvious.ai 账号 + 捕获 manifest |
| `scripts/obvious_client.py` | 单账号 HTTP 接口（cookie auth，mode=auto 有 run-shell）|
| `scripts/obvious_pool.py` | 多账号池：健康检查 / 并发分发 / 自动补号 |
| `scripts/obvious_executor.py` | 高层封装：register / diagnose / health 命令 |
| `scripts/obvious_ssh_bridge.py` | 在沙箱内建 SSH 隧道引入住宅代理 |
| `scripts/obvious_warmup.py` | 用沙箱做 Playwright cookie 预热 |
| `scripts/e2b_direct.py` | 沙箱诊断 + e2b envd 直连探测 |
| `/root/obvious-accounts/` | 账号数据（cookie / manifest），不入 git |
| `docs/obvious-provisioning.md` | **完整操作手册，新人从这里开始** |
| `docs/obvious-findings.md` | API 研究记录（mode 对比 / tool 列表 / 响应格式）|

⚠️ 关键：`mode=fast` 无 `run-shell` 工具，执行命令必须用 `mode=auto` 或 `mode=deep`。

## 验证码识别服务

两个验证码识别服务通过 PM2 常驻，并由 api-server 对外暴露代理路由。

| Service | Port | 类型 | 准确率 | API路由 |
|---------|------|------|--------|---------|
|  | 8765 | CNN 数字图片验证码 | ~96.3% |  |
|  | 8767 | YOLO+ONNX 文字点选验证码 | ~96% |  |

### captcha-api (CNN，port 8765)

模型训练于 Replit (CPU)，共跑 ~300 epoch，识别单字符数字图片验证码。

**调用示例（在注册脚本里替换 ddddocr）：**
```python
import urllib.request, json, base64

def solve_captcha_api(img_bytes: bytes) -> str:
    """调用 captcha-api CNN 识别，替代本地 ddddocr"""
    b64 = base64.b64encode(img_bytes).decode()
    body = json.dumps({'base64': b64}).encode()
    req = urllib.request.Request(
        'http://localhost:8765/recognize',
        data=body, headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())['result']
```

> ⚠️  目前仍使用本地 ddddocr，可替换为上方调用以获得更高准确率。

### text-captcha (YOLO+ONNX，port 8767)

识别文字点选类验证码，返回需点击的汉字坐标列表。

**调用示例：**
```python
import requests, base64

def solve_text_captcha(bg_img_b64: str, chars: list[str]) -> list[dict]:
    """返回 [{char, x, y}, ...] 坐标列表"""
    resp = requests.post('http://localhost:8767/identify', json={
        'image': bg_img_b64,   # base64 背景图
        'chars': chars         # 需点击的汉字列表
    })
    return resp.json().get('data', [])
```

### 模型更新流程

CNN 模型在 Replit 本地训练后自动部署：
```bash
# Replit 上（CNN Training workflow 会自动跑）：
cd /home/runner/workspace/captcha_train
python3 train.py --skip-gen --resume --start-epoch <N> --best-acc <acc>
python3 deploy_model.py   # SCP 到远程 + pm2 restart captcha-api
```

## 验证码识别服务

两个验证码识别服务通过 PM2 常驻，并由 api-server 对外暴露代理路由。

| Service | Port | 类型 | 准确率 | API路由前缀 |
|---------|------|------|--------|------------|
| `captcha-api` | 8765 | CNN 数字图片验证码 | ~96.3% | `/api/tools/captcha/` |
| `text-captcha` | 8767 | YOLO+ONNX 文字点选验证码 | ~96% | `/api/tools/text-captcha/` |

### captcha-api (CNN，port 8765)

模型训练于 Replit (CPU)，共跑 ~300 epoch，识别单字符数字图片验证码。

**调用示例（在注册脚本里替换 ddddocr）：**

```python
import urllib.request, json, base64

def solve_captcha_api(img_bytes):
    # 调用 captcha-api CNN 识别，替代本地 ddddocr
    b64 = base64.b64encode(img_bytes).decode()
    body = json.dumps({'base64': b64}).encode()
    req = urllib.request.Request(
        'http://localhost:8765/recognize',
        data=body, headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())['result']
```

> `novproxy_register_final.py` 目前仍使用本地 ddddocr，可替换上方调用获得更高准确率。

### text-captcha (YOLO+ONNX，port 8767)

识别文字点选类验证码，返回需点击的汉字坐标列表。

**调用示例：**

```python
import requests

def solve_text_captcha(bg_img_b64, chars):
    # 返回 [{char, x, y}, ...] 坐标列表
    resp = requests.post('http://localhost:8767/identify', json={
        'image': bg_img_b64,
        'chars': chars
    })
    return resp.json().get('data', [])
```

### 模型更新流程

CNN 模型在 Replit 本地训练后自动部署：

```bash
# Replit CNN Training workflow 会自动执行，也可手动触发：
cd /home/runner/workspace/captcha_train
python3 train.py --skip-gen --resume --start-epoch <N> --best-acc <acc>
python3 deploy_model.py   # SCP 到远程 + pm2 restart captcha-api
```

详细说明见 `scripts/captcha_recognition/README.md`。
