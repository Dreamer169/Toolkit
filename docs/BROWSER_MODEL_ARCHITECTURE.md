# Browser Model — 架构现状 & 自主开发路线图

> 最后更新：2026-05-11  
> 文档范围：cdp-broker / cdp-ws-server / renderer / google-route / nesting-pool  
> 参考外部项目：Open-Anti-Browser (Wtcity22)、EasyBrowser (EasyBrowserDeveloper)

---

## 一、外部项目评估

### 1.1 Open-Anti-Browser

GitHub: `https://github.com/Wtcity22/Open-Anti-Browser`  
语言：Python (Playwright + 直连 CDP HTTP)  
架构：桌面 GUI，每个"配置文件"对应一个独立的 Chrome/Firefox 进程

#### 1.1.1 可借鉴功能评估

| 模块 | 功能 | 对我们的价值 | 适配难度 |
|------|------|------------|---------|
| `synchronizer.py` | CDP 多窗口动作同步（主控→跟随）| **高** — 批量账号同步操作 | 中（需改造为 TS 进程内方案）|
| `browser_manager.py` | 配置文件生命周期管理 | 低 — 我们是单进程多 Context | 高（架构不同）|
| `window_manager.py` | Windows 窗口排列 (win32api) | **无** — Linux VPS 不适用 | N/A |
| `chrome.py` | 指纹参数注入 (`--fingerprint` flag) | 低 — 我们用自研 JS stealth | 中 |

#### 1.1.2 synchronizer.py 深度分析

**工作原理：**

```
主控浏览器                    同步器 (Python)                  跟随浏览器 N
─────────────────────────────────────────────────────────────────────────
CDP HTTP :9222            BrowserSynchronizer              CDP HTTP :9223..N
   │                            │                               │
   │  Runtime.addBinding        │                               │
   │  (__oabSyncBinding)  ←─────│                               │
   │                            │                               │
   │ DOM事件(click/input/        │                               │
   │ wheel/keydown) → binding   │                               │
   │──────────────────────────→ │                               │
   │  Runtime.bindingCalled     │  _apply_event_to_follower()   │
   │                            │─────────────────────────────→ │
   │  Page.frameNavigated       │  client.navigate(url)         │
   │──────────────────────────→ │─────────────────────────────→ │
   │                            │  client.dispatch_mouse_event  │
   │                            │─────────────────────────────→ │
```

**同步事件类型：**
- `navigate` — URL 跳转（Page.frameNavigated / Page.navigatedWithinDocument）
- `click` — 带 CSS selector + 相对坐标 (rx/ry) 双轨定位，先 selector 后坐标
- `input / change` — 表单填值，支持 checkbox/radio/select/contentEditable
- `wheel` — 鼠标滚轮（绝对坐标）
- `scroll` — 元素级滚动（selector + ratioY/ratioX 比例）
- `keydown` — 键盘事件（key/code + modifier）
- `mouse_move` — 鼠标移动
- `browser_new_tab` — 新标签页同步到所有跟随窗口
- `browser_close_current` — 关闭当前 Tab 同步
- `browser_activate_tab` — 切换 Tab 焦点同步

**亮点设计：**
1. **相对坐标 (rx/ry)**：点击位置用 `rx = x / viewport.width`、`ry = y / viewport.height` 归一化，不同分辨率跟随窗口也能正确对应
2. **双轨定位**：优先 CSS selector，fallback 坐标点，最大化跨站点兼容性
3. **延迟新标签**：点击后打开的新 Tab URL 若还是 `about:blank`，暂缓广播等 URL 解析后再同步
4. **每跟随者独立 Worker 线程**：`_follower_workers` dict，避免单个慢跟随窗口阻塞主轮询
5. **选项开关**：`sync_navigation`/`sync_click`/`sync_input`/`sync_scroll`/`sync_keyboard`/`sync_mouse_move`/`sync_browser_ui` 独立控制

**与我们架构的差异：**
- OAB 跨进程通过 CDP HTTP（每次同步都走 HTTP 往返），延迟约 5–50ms
- 我们可以做**进程内同步**（直接调用跟随 Session 的 `handleMessage()`），延迟接近 0

#### 1.1.3 EasyBrowser 评估

GitHub: `https://github.com/EasyBrowserDeveloper/EasyBrowser`  
**仅有 README.md，实质内容为空。无可参考价值。**

---

## 二、我们的 Browser Model 架构现状

### 2.1 整体拓扑

```
VPS (45.205.27.69)
│
├── Xvfb :99                     ← 虚拟显示器，headed Chrome 依赖
│
├── Chromium (单进程，headed)
│   └── 由 Playwright connectOverCDP 连接
│       每个 WS 连接 → 独立 BrowserContext → 独立 Page
│
├── browser-model Node.js 进程 (PORT=8092)
│   ├── Express HTTP server
│   │   ├── GET  /api/health
│   │   ├── GET  /api/cdp-info
│   │   ├── POST /api/cf-warmup
│   │   └── POST /api/proxy        (兼容旧前端，新架构不用)
│   │
│   ├── WebSocket server (/api/cdp/ws)
│   │   └── 每连接 → new CdpSession(ws)
│   │
│   └── Static 前端 (FRONTEND_DIR)
│
├── nesting-pool (4x CF Workers)
│   proxy.jimjio.indevs.in / proxy.jimjon.eu.cc
│   proxy.jonjim.indevs.in / proxy.hackerjim.indevs.in
│
└── SOCKS5 代理池 (xray subnodes :10820–:10916)
    ├── google-route pool  :10820–:10845 (reCAPTCHA token 出口)
    └── broker pool        :10851–:10916 (主浏览器出口，cf_clearance 解决)
```

### 2.2 核心模块

#### 2.2.1 `cdp-broker.ts` — 会话管理核心

**职责：** 单个 WS 连接的完整生命周期。

```typescript
// 单例 Chromium 进程（全局共享）
let _browserPromise: Promise<Browser> | null = null;

// 每个 WebSocket 连接
class CdpSession {
  private ctx:  BrowserContext | null  // 独立 Context（隔离单元）
  private page: Page | null            // 唯一页面
  private cdp:  CDPSession | null      // 原生 CDP channel
  private ws:   WebSocket              // 前端连接
}
```

**创建流程：**
```
WebSocket connect
  → CdpSession.start(w, h)
    → getBrowser()                   // 确保 Chromium 单例
    → browser.newContext({           // ★ 隔离单元
        proxy, userAgent, viewport,
        locale, timezoneId, ...
      })
    → ctx.newPage()
    → attachGoogleProxyRouting(ctx)  // 注入 google-route
    → page.addInitScript(STEALTH)    // 注入指纹 stealth
    → ctx.route('/api/...') → ...    // SW 注册拦截等
    → cdp.send("Page.startScreencast")  // 开始截图流
    → ws.send({ type: "ready" })
```

**消息协议（前端→服务端）：**

| type | 参数 | 实现 |
|------|------|------|
| `navigate` | url | page.goto + CF challenge 检测 |
| `back` / `forward` / `reload` | — | page.goBack/Forward/reload |
| `mouse` | action(down/up/move), x, y, button, buttons | Input.dispatchMouseEvent |
| `wheel` | x, y, deltaX, deltaY | Input.dispatchMouseEvent (mouseWheel) |
| `key` | key, code, keyAction, text, modifiers | Input.dispatchKeyEvent |
| `type` | textBlock | page.keyboard.insertText |
| `resize` | width, height | setViewportSize + 重启 screencast |
| `evaluate` | expression | page.evaluate → evaluateResult |
| `ack` | — | no-op（预留流控）|

**消息协议（服务端→前端）：**

| type | 内容 |
|------|------|
| `ready` | width, height |
| `frame` | JPEG base64 + sessionId |
| `url` | 当前 URL |
| `title` | 页面标题 |
| `httpStatus` | HTTP 状态码 |
| `cfChallenge` | state: waiting / done |
| `dialog` | kind, message |
| `navError` | url, error |
| `evaluateResult` | result / error |

**关闭流程：**
```
WebSocket close
  → session.close()
    → Page.stopScreencast
    → cdp.detach()
    → page.close()
    → ctx.close()           // ★ 完全销毁 Context，清除所有 Cookie/Storage
```

#### 2.2.2 `cdp-ws-server.ts` — WebSocket 接入层

```typescript
// 单一入口
WS /api/cdp/ws?w=1280&h=800&url=https://...

// 每连接独立 CdpSession，无共享状态
wss.on("connection", async (ws, req) => {
  const session = new CdpSession(ws);
  ws.on("message", (data) => session.handleMessage(data));
  ws.on("close",   () => session.close());
  await session.start({ width, height });
  if (initialUrl) await session.handleMessage(navigate(initialUrl));
});
```

#### 2.2.3 `renderer.ts` — 指纹 Stealth

注入为 `page.addInitScript()`，在每个页面 JS 执行前运行：

| 防护项 | 实现方式 |
|--------|---------|
| navigator.* 伪装 | hardwareConcurrency=8, deviceMemory=8, platform=Linux x86_64 |
| userAgent / userAgentData | Chrome 144 Linux, brands list |
| Canvas 指纹 | per-session seed ±1 像素噪声 (toDataURL / getImageData) |
| AudioContext 指纹 | ±1e-7 采样噪声 (getChannelData) |
| ClientRects 指纹 | ±1e-4 纳米级宽高噪声 |
| WebGL vendor/renderer | "Google Inc. (Intel)" + "Mesa Intel UHD 630" |
| WebGL 扩展列表 | 伪装完整 Chrome+Intel 扩展集 |
| Worker stealth | 劫持 Worker constructor，注入同等 stealth blob |
| SharedWorker stealth | 同上 |
| ServiceWorker 注册 | context.route 拦截注入 |
| speechSynthesis | 7 个 Google + eSpeak 声音 |
| 时区 (Intl / Date) | resolveProxyTimezone() 动态 geo 解析，30min LRU 缓存 |
| MediaStreamTrack.label | 过滤 "fake_device_0" 泄露 |
| pdfViewerEnabled | 强制 true |
| navigator.bluetooth/usb/hid/serial | 命名空间存在性保证 |
| window.open() | 拦截后在当前 page 跳转（阻止真实弹窗）|

**时区动态解析（v2，2026-05-11 新增）：**
```typescript
// resolveProxyTimezone(proxyUrl)
// SocksClient + undici → ip-api.com → 真实出口 IP 时区
// 30min LRU cache per proxyUrl
// fallback → 静态 _PROXY_PORT_TZ 表
```

#### 2.2.4 `google-route.ts` — reCAPTCHA 代理隔离

**问题背景：** 主浏览器走 VPS 直连或 HK 代理时，`*.google.com` 评分低（数据中心 IP），reCAPTCHA Enterprise code:1。

**解决：** 对特定域名走独立 SOCKS 池（sticky per BrowserContext）

```
拦截域名: *.google.com, *.gstatic.com, *.recaptcha.net, *.youtube.com
代理池: socks5://127.0.0.1:10820~10845 (13个 xray CF Worker 节点)
粘滞策略: 同一 BrowserContext 生命周期内固定同一出口 IP
         (WeakMap<BrowserContext, URL>)
```

实现：`ctx.route(pattern, handler)` 拦截请求 → undici + SocksAgent 转发

#### 2.2.5 `nesting-pool.ts` — CF 挑战 HTTP 请求转发

**用途：** `abs.incolumitas.com` 等通过普通 SOCKS 不可达的检测站点，通过 CF Worker 反向代理绕过。

```
nestingFetch(url, init)
  → round-robin 选 Worker
  → POST https://{worker}/proxy {target_url, method, headers, body}
  → Worker 返回 {status, headers, body, cf_colo}
  → 重建 Response 对象
Circuit breaker: 失败 → 熔断 60s → 自动恢复
```

---

## 三、Context 级隔离 — 现状与缺口

### 3.1 已覆盖（BrowserContext 天然隔离）

| 隔离维度 | 状态 |
|---------|------|
| Cookie / Session Storage / LocalStorage | ✅ Context 级别完全隔离 |
| IndexedDB / CacheStorage | ✅ 隔离 |
| 代理设置 (`proxy` option) | ✅ 每 Context 独立 |
| 视口 / 时区 / Locale | ✅ 每 Context 独立 |
| User-Agent / Accept-Language | ✅ 每 Context 独立 |
| HTTP credentials | ✅ 隔离 |
| Network conditions | ✅ 隔离 |

### 3.2 已缺口（需自主开发）

#### 缺口 A：指纹 seed 不绑定 Context

**现状：** renderer.ts 中 Canvas/Audio/ClientRects 噪声 seed 是 `Math.random()`，每次页面加载随机，同一 Context 内刷新后噪声不同。

**风险：** CreepJS 多次采样取哈希后对比，看到 canvas hash 每刷新都变 → 判 bot。

**修复方案：**
```typescript
// 在 cdp-broker.ts 创建 Context 时生成固定 seed
const fingerprintSeed = crypto.randomUUID(); // 每 Context 一次

// 注入时作为参数传入
await page.addInitScript({
  content: STEALTH_SCRIPT,
  arg: { seed: fingerprintSeed }   // playwright addInitScript 第二参数
});
```

#### 缺口 B：Worker stealth 指纹值硬编码

**现状：** Worker 内注入的 `hardwareConcurrency=8 / deviceMemory=8 / Chrome 144` 是常量，所有 Context 完全一样。

**风险：** 批量账号从同 IP 发出且 Worker 指纹一模一样 → 群体画像。

**修复方案：** Worker stealth blob 参数化，从 Context 的 fingerprintSeed 派生随机值范围内的值（8核 or 12核 or 16核）。

#### 缺口 C：Session 不持久化

**现状：** WebSocket 断开 → `ctx.close()` → Cookie/Storage 全部清除。

**影响：** 浏览器无法保持登录态，每次连接都要重新登录。

**修复方案：**
```typescript
// 选项1：persistentContext（Playwright 原生）
await chromium.launchPersistentContext(userDataDir, { ...opts });
// 每个账号有固定 userDataDir，Cookie 磁盘持久化

// 选项2：Context.storageState() 导出/导入
const state = await ctx.storageState(); // 断开前导出
// 下次连接时：
await browser.newContext({ storageState: state, ...opts });
```

#### 缺口 D：window.open() 弹窗被吞

**现状：** ctx.on("page") 监听到新 page → 强制关闭，URL 在主 page 跳转。

**影响：** OAuth2 弹窗（Google/GitHub 授权窗口）无法正常工作。

**修复方案：** 区分 OAuth popup（`window.opener` 存在，URL 含 oauth/auth/login）和普通弹窗，OAuth popup 让它正常打开并向前端发送 `{ type: "popup", url }` 通知。

#### 缺口 E：无动作同步（Action Synchronizer）

**见第四章。**

---

## 四、自主开发：动作同步器（Action Synchronizer）

### 4.1 设计目标

- 1 个 WS 连接作为"主控"，N 个 WS 连接作为"跟随"
- 主控的操作（点击/输入/导航/滚动）实时广播到所有跟随
- 同步无需额外进程，全部在 browser-model 进程内完成
- 不影响现有单会话使用

### 4.2 进程内 vs OAB 跨进程对比

| 维度 | OAB 方案（CDP HTTP） | 我们的方案（进程内）|
|------|--------------------|--------------------|
| 同步延迟 | 5–50ms（每次 HTTP 往返）| <1ms（直接函数调用）|
| 部署复杂度 | 高（每 Profile 一个调试端口）| 无（共享同一个 Chromium）|
| Tab 同步 | 支持（跨进程 CDP target）| 需配合 Tab 管理 API |
| 指纹隔离 | 完整（独立进程）| BrowserContext 级别（已足够）|

### 4.3 架构设计

```
                SyncRegistry (全局单例)
                ┌─────────────────────────┐
  主控 WS ──→  │  master: CdpSession     │
                │  followers: Set<Session> │
  跟随 WS1 ──→ │                         │
  跟随 WS2 ──→ │                         │
                └─────────────────────────┘

CdpSession.handleMessage()
  │ 判断：是否为 master session?
  ├── YES → 执行操作 + broadcastToFollowers(msg)
  └── NO  → 仅执行操作

broadcastToFollowers(msg):
  for follower of registry.followers:
    follower.handleMessage(msg)   // 直接调用，无 HTTP
```

### 4.4 API 设计

**HTTP 端点：**
```
POST /api/sync/start
Body: {
  master_ws_id: string,      // 主控 WS 的 sessionId
  follower_ws_ids: string[], // 跟随 WS 的 sessionIds
  options: {
    sync_navigation: true,
    sync_click: true,
    sync_input: true,
    sync_scroll: true,
    sync_keyboard: false,
    sync_mouse_move: false,
    use_relative_coords: true  // rx/ry 归一化
  }
}

POST /api/sync/stop
Body: { master_ws_id: string }

GET  /api/sync/status
```

**WS 协议扩展（前端→服务端）：**
```jsonc
// 注册 session ID（连接后立即发送）
{ "type": "register", "sessionId": "abc123" }

// 主控声明自己
{ "type": "sync_master", "sessionId": "abc123" }
```

### 4.5 事件捕获：JS 注入方案

参考 OAB 的 `__oabSyncBinding` 方案，我们注入监听器到主控 Page：

```typescript
// 在 master CdpSession.start() 时注入
const SYNC_CAPTURE_SCRIPT = `
(function() {
  const send = (type, payload) => {
    // 通过 CDP Runtime.addBinding 上报给 Node
    if (typeof __bmSyncCapture !== 'undefined') {
      __bmSyncCapture(JSON.stringify({ type, payload,
        href: location.href,
        rx: undefined, ry: undefined  // 由 click handler 填充
      }));
    }
  };

  // 点击：记录相对坐标 + CSS selector
  document.addEventListener('click', (e) => {
    const el = e.target;
    const rx = e.clientX / window.innerWidth;
    const ry = e.clientY / window.innerHeight;
    const selector = buildSelector(el);  // 最优 selector
    send('click', { rx, ry, selector,
      button: e.button, ctrlKey: e.ctrlKey,
      shiftKey: e.shiftKey, altKey: e.altKey });
  }, true);

  // 输入：防抖 200ms
  document.addEventListener('change', (e) => {
    const el = e.target;
    send('input', {
      selector: buildSelector(el),
      value: el.value ?? el.innerText,
      tag: el.tagName.toLowerCase(),
      inputType: el.type,
      checked: el.checked
    });
  }, true);

  // 导航：SPA 路由也能捕获（Page.frameNavigated 作为补充）
  // 滚动：节流 100ms
  // ...
})();
`;

// Node 侧接收
await cdp.send("Runtime.addBinding", { name: "__bmSyncCapture" });
cdp.on("Runtime.bindingCalled", ({ name, payload }) => {
  if (name !== "__bmSyncCapture") return;
  const event = JSON.parse(payload);
  broadcastToFollowers(event);
});
```

### 4.6 跟随者事件执行

跟随者收到事件后，直接通过现有 `handleMessage()` 执行：

```typescript
async broadcastEvent(event: SyncEvent) {
  for (const follower of this.followers) {
    // 相对坐标 → 绝对坐标（跟随者自己的 viewport）
    const vp = follower.viewport;
    if (event.type === 'click' && event.payload.rx != null) {
      const x = event.payload.rx * vp.w;
      const y = event.payload.ry * vp.h;
      await follower.handleMessage(JSON.stringify({
        type: 'mouse', action: 'down', x, y, button: 'left', buttons: 1, clickCount: 1
      }));
      await follower.handleMessage(JSON.stringify({
        type: 'mouse', action: 'up', x, y, button: 'left', buttons: 0, clickCount: 1
      }));
    }
    if (event.type === 'navigate') {
      await follower.handleMessage(JSON.stringify({
        type: 'navigate', url: event.payload.url
      }));
    }
    // input / scroll / keydown ...
  }
}
```

---

## 五、自主开发：Tab 级管理 API

### 5.1 现状

当前架构：1 WebSocket = 1 BrowserContext = 1 Page（固定）。

OAB 的"一浏览器进程 N 个隔离 Tab"模型与我们**不适用**，原因：
- OAB 的"Tab 级隔离"实质是让多个账号共用一个 Chrome 进程的不同 Tab，靠 `--profile-directory` 隔离 Cookie（并非真正 Tab 级隔离，是进程级隔离的变体）
- 我们的 BrowserContext 隔离已经**优于** Tab 隔离：独立 Cookie、独立 Storage、独立代理、独立指纹注入时机

因此我们**不需要**也**不应该**仿照 OAB 做"Tab 级隔离"，但需要补充 **Tab 管理 API**（供单个账号多 Tab 浏览使用）。

### 5.2 Tab 管理 WS 协议扩展

```typescript
// 前端→服务端（新增消息类型）
{ type: "tab_create", url?: string }        // 在当前 Context 开新 Tab
{ type: "tab_close", targetId: string }     // 关闭指定 Tab
{ type: "tab_activate", targetId: string }  // 切换活动 Tab
{ type: "tab_list" }                        // 列出所有 Tab

// 服务端→前端（新增消息类型）
{ type: "tab_opened",  targetId, url }
{ type: "tab_closed",  targetId }
{ type: "tab_activated", targetId, url }
{ type: "tab_list_result", tabs: [{targetId, url, title, active}] }
```

### 5.3 实现方案

```typescript
// CdpSession 扩展
private tabs: Map<string, { page: Page; cdp: CDPSession }> = new Map();
private activeTabId: string = "";

async handleTabCreate(url?: string) {
  const page = await this.ctx!.newPage();
  await page.addInitScript(STEALTH_SCRIPT);
  const cdp = await page.context().newCDPSession(page);
  const targetId = (await cdp.send("Target.getTargetInfo")).targetInfo.targetId;
  this.tabs.set(targetId, { page, cdp });
  // 为新 Tab 也启动 screencast
  this.send({ type: "tab_opened", targetId, url: url ?? "" });
  if (url) await page.goto(url);
}

// window.open() 改造：不再吞掉，而是接管并注册为新 Tab
this.ctx!.on("page", async (p) => {
  if (p === this.page) return;
  await p.addInitScript(STEALTH_SCRIPT);
  const cdp = await p.context().newCDPSession(p);
  const targetId = (await cdp.send("Target.getTargetInfo")).targetInfo.targetId;
  this.tabs.set(targetId, { page: p, cdp });
  this.send({ type: "tab_opened", targetId, url: p.url() });
});
```

### 5.4 多 Tab Screencast 策略

单 WS 连接传输多个 Tab 的视频流，选项：
1. **活动 Tab 单流**：只发当前活动 Tab 的帧，切换 Tab 时无缝切换 screencast
2. **缩略图模式**：活动 Tab 全帧 + 其他 Tab 低频缩略图（可选 `tab_thumb` 消息）

推荐方案 1（实现简单，与现有前端兼容）：
```typescript
// 切换活动 Tab 时
async activateTab(targetId: string) {
  const prev = this.tabs.get(this.activeTabId);
  if (prev) await prev.cdp.send("Page.stopScreencast");

  const next = this.tabs.get(targetId)!;
  await next.cdp.send("Page.startScreencast", SCREENCAST_OPTS);
  this.activeTabId = targetId;
  this.send({ type: "tab_activated", targetId, url: next.page.url() });
}
```

---

## 六、开发优先级建议

| 优先级 | 功能 | 工作量 | 收益 |
|--------|------|--------|------|
| P0 | **Session 持久化**（Cookie 保活）| 1天 | 批量账号保登录态 |
| P0 | **指纹 seed 绑定 Context**（Canvas/Audio 固定）| 半天 | 反检测一致性 |
| P1 | **动作同步器 MVP**（navigate + click + input）| 3天 | 批量账号同步操作 |
| P1 | **Tab 管理 API**（create/close/activate）| 2天 | 单账号多 Tab 浏览 |
| P2 | **OAuth popup 支持**（window.open 不吞）| 1天 | Google/GitHub 授权 |
| P2 | **Worker stealth 参数化**（多样性）| 半天 | 群体指纹差异化 |
| P3 | **动作同步器进阶**（scroll/keyboard/mouse_move）| 2天 | 全操作同步 |
| P3 | **多 Tab screencast 缩略图**| 3天 | 多 Tab UI 体验 |

---

## 七、关键文件索引

| 文件 | 职责 |
|------|------|
| `browser-model/artifacts/api-server/src/lib/cdp-broker.ts` | Session 生命周期、消息处理、BrowserContext 管理 |
| `browser-model/artifacts/api-server/src/lib/cdp-ws-server.ts` | WebSocket 接入、Session 创建 |
| `browser-model/artifacts/api-server/src/lib/renderer.ts` | 指纹 stealth 脚本 + 时区动态解析 |
| `browser-model/artifacts/api-server/src/lib/google-route.ts` | reCAPTCHA 代理隔离、sticky per-context |
| `browser-model/artifacts/api-server/src/lib/nesting-pool.ts` | CF Worker 反代池（abs.incolumitas.com 等）|
| `browser-model/artifacts/api-server/src/app.ts` | Express 应用主体 |
| `browser-model/artifacts/api-server/src/routes/index.ts` | HTTP 路由注册 |
| `Toolkit/start-browser-model.sh` | 启动脚本（代理选择 + 端口预清理）|
