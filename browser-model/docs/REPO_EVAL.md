# 外部仓库评估报告：browser-model 能力对标

生成时间: 2026-05-11
评估者: Reseek Agent (gh: Dreamer169)
目标仓库: Open-Anti-Browser (Wtcity22)、EasyBrowser (EasyBrowserDeveloper)
评估目的: CDP 多窗口动作同步器、页签级隔离架构 —— 对当前 browser-model 的启示与差距

---

## 一、评估对象概述

| 维度 | Open-Anti-Browser (Wtcity22) | EasyBrowser (EasyBrowserDeveloper) |
|---|---|---|
| 开放程度 | 完整源码（Python）| README 仅（未开源）|
| 语言/运行时 | Python 3 + FastAPI + Playwright | 定制 Chromium 二进制 + EasyCDP(Python) |
| 隔离粒度 | 进程级（每 Profile = 独立 Chrome 进程）| 页签级（单进程 N 容器）|
| 核心亮点 | CDP 多窗口动作同步器（synchronizer.py）| 内核级页签隔离（指纹/Cookie/代理）|
| 对 browser-model 价值 | 高（同步器逻辑可参考移植）| 中（架构思路，无法直接复用）|

---

## 二、Open-Anti-Browser 深度分析

### 2.1 整体架构

```
BrowserManager（browser_manager.py）
  ├── start_profile(profile_id)  →  launch_chrome_profile()
  │     每个 Profile 启动独立 Chrome 进程，--remote-debugging-port=<port>
  │     代理：--proxy-server=<url>（进程级）
  │
  ├── BrowserSynchronizer（services/synchronizer.py）
  │     master_profile_id  →  主窗口（被模仿者）
  │     follower_profile_ids[]  →  从窗口（镜像操作）
  │
  └── CdpPageClient（services/synchronizer.py）
        纯 CDP-over-HTTP（urllib + websocket-client）
        不走 Playwright，直连 Chrome 的 /json/version 端点获取 wsDebuggerUrl
```

### 2.2 BrowserSynchronizer 工作机制

#### 启动流程

```
synchronizer.start(master_id, follower_ids, options)
  1. 为 master 创建 CdpPageClient（HTTP to :debug_port）
  2. 为每个 follower 创建 CdpPageClient
  3. 向 master 注入 MASTER_INJECT_SCRIPT（via Runtime.evaluate）
  4. 启动后台轮询线程（1.2s heartbeat）
     每轮：_install_master_script() + _sync_browser_ui_changes() + _drain_master_poll_events()
  5. 为每个 follower 启动独立 FollowerWorker（线程池，queue limit=280）
```

#### MASTER_INJECT_SCRIPT 事件捕获机制

Master 页面注入一段 JS，监听 DOM 事件并将结构化事件放入队列：

```javascript
window.__oabSyncQueue = []          // 环形队列，最多 220 条
window.__oabSyncDrain = () => ...   // Python 侧 drain 调用
window.__oabSyncBinding(body)       // CDP Runtime.addBinding 优先通道

// 监听的事件（capturePhase=true，拦截所有子元素）：
document.addEventListener(click,   ...)   → emit(click,    {x,y,rx,ry,selector,button,ctrlKey,...})
document.addEventListener(input,   ...)   → emit(input,    {selector,tag,inputType,value,checked})
document.addEventListener(change,  ...)   → emit(change,   {selector,tag,...})
document.addEventListener(keydown, ...)   → emit(keydown,  {key,code,ctrlKey,...})  // 仅 Enter/Tab/Esc/Ctrl/Meta/Alt
document.addEventListener(wheel,   ...)   → emit(wheel,    {deltaX,deltaY,x,y,mode,...})
document.addEventListener(scroll,  ...)   → emit(scroll,   {ratioX,ratioY,scrollTop,...})
document.addEventListener(mousemove,...)  → emit(mouse_move,{x,y,rx,ry})  // rAF 节流
Page.frameNavigated / navigatedWithinDocument → emit(navigate,{url})
```

selector 构建策略：`#id` > `[data-testid]` > `tag:nth-of-type` 级联（最多 7 级）

#### 事件分发到 follower

```
_drain_master_poll_events()
  → _dispatch_master_event(event)
      → option_map 过滤（sync_navigation/sync_click/sync_input/sync_scroll/sync_keyboard/sync_mouse_move）
      → FollowerWorker.submit(event_type, payload)  # 异步线程队列
          → _apply_event_to_follower(client, event_type, payload)
```

**对 follower 的实际 CDP 操作：**

| 事件类型 | follower 端执行 |
|---|---|
| `navigate` | `client.navigate(url)` → `Target.activateTarget + Page.navigate` |
| `click` | `Runtime.evaluate(_resolve_click_point_expression)` 定位元素 → `Input.dispatchMouseEvent` ×4（down/up/move/click）|
| `input`/`change` | `Runtime.evaluate(_build_input_expression)` 设置 value，触发 input/change Event |
| `keydown` | `Runtime.evaluate(_build_key_expression)` 派发 KeyboardEvent |
| `wheel` | `Input.dispatchMouseEvent(type=mouseWheel)` 直接 CDP |
| `scroll` | `Runtime.evaluate(_build_scroll_expression)` 按比例 scrollTo |
| `mouse_move` | `Input.dispatchMouseEvent(type=mouseMoved)` |

#### 浏览器 UI 同步（sync_browser_ui 选项）

轮询检测 master 的 Target 列表变化，向 follower 同步标签操作：

```
新开标签（browser_new_tab）  → follower.create_target(url)
关闭标签（browser_close_current）→ follower.close_target(current_id)
激活标签（browser_activate_tab）→ follower.activate_target(target_id)
```

延迟处理（`_deferred_new_target_ids`）：点击后 1.5s 内打开的 about:blank 推迟等待真实 URL 加载后再同步。

#### CdpPageClient 实现要点

```python
class CdpPageClient:
    # 连接：HTTP GET http://127.0.0.1:<port>/json/version → wsDebuggerUrl
    # WS：websocket.WebSocket（无异步，同步阻塞 + 事件 drain）
    # 超时：SYNC_COMMAND_TIMEOUT=6s，SYNC_DISCOVERY_TIMEOUT=5s
    
    def list_targets(self)       → GET /json → [{id, type, url, title}]
    def switch_target(id)        → 切换 WS 连接到 /devtools/page/<id>
    def activate_target(id)      → GET /json/activate/<id>
    def navigate(url)            → Page.navigate via CDP WS
    def evaluate(expr)           → Runtime.evaluate via CDP WS
    def dispatch_mouse_event(p)  → Input.dispatchMouseEvent via CDP WS
    def create_target(url, bg)   → Target.createTarget via CDP WS
    def close_target(id)         → Target.closeTarget via CDP WS
    def drain_events()           → Runtime.evaluate(__oabSyncDrain()) 取队列
    def get_location()           → Runtime.evaluate(location.href)
    def current_target_id()      → 返回当前 WS 连接的 targetId
```

### 2.3 代理隔离方式

- 进程级：每个 Profile 启动 Chrome 时传 `--proxy-server=<url>`
- `resolve_geo_profile(proxy_url)` 调用 IP 地理库自动推算 timezone/language/locale，三者自洽注入 fingerprint
- Firefox 通过 Marionette port，Chrome 通过 remote-debugging-port

### 2.4 对 browser-model 可移植的设计

| 要素 | Open-Anti-Browser 做法 | browser-model 移植建议 |
|---|---|---|
| 事件捕获 | MASTER_INJECT_SCRIPT 注入 DOM listener + 队列 | 可在 CdpSession.start() 时 addInitScript 注入类似脚本，通过 CDP binding 实时推送 |
| 事件分发 | CdpPageClient（纯 CDP HTTP/WS）| browser-model 已有 `this.cdp.send(Input.dispatch*)` + `page.evaluate()`，直接可用 |
| follower 并发 | 每个 follower 独立线程队列 | Node.js 中用 `Promise.all + worker_threads` 或简单 async 队列 |
| Tab UI 同步 | 轮询 /json 端点对比 Target 列表 diff | Playwright 的 `context.on(page)` / `page.on(close)` 更优（事件驱动，无轮询） |
| 点击定位 | CSS selector + 相对坐标(rx,ry)回退 | browser-model 当前已有 Input.dispatchMouseEvent，需加 selector 解析层 |
| 选项控制 | `sync_navigation/sync_click/...` 6 维 bool | 与当前 SessionOpts 并列，可加 `syncOptions` 字段 |

---

## 三、EasyBrowser 架构分析

### 3.1 设计目标与定位

EasyBrowser 是一个**未开源的商业产品**（内测阶段），核心卖点是：

> 一个 Chromium 进程实例，运行 N 个完全隔离的"容器"（Tab），每个容器独立指纹、Cookie、代理。

这与 browser-model 当前"1 BrowserContext per WS 连接"形成对比。

### 3.2 Tab 级隔离架构设计（从 README 推断）

```
┌─────────────────────── Chromium 进程 ──────────────────────────┐
│                                                                  │
│  容器 A (Tab 1)          容器 B (Tab 2)        容器 C (Tab 3)   │
│  ┌──────────────┐        ┌──────────────┐       ┌────────────┐  │
│  │ 指纹 A       │        │ 指纹 B       │       │ 指纹 C     │  │
│  │ Cookie A     │        │ Cookie B     │       │ Cookie C   │  │
│  │ Proxy A      │        │ Proxy B      │       │ Proxy C    │  │
│  │ IndexedDB A  │        │ IndexedDB B  │       │ IndexedDB C│  │
│  └──────────────┘        └──────────────┘       └────────────┘  │
│                                                                  │
│  ── 共享 ──────────────────────────────────────────────────── │
│  浏览器静态缓存（图片/CSS/JS）源码级 bypass 规则               │
│  Chromium 进程内存基础结构                                       │
└──────────────────────────────────────────────────────────────────┘
```

**隔离维度：**

| 隔离项 | 实现层 | 备注 |
|---|---|---|
| 指纹（CPU/内存/语言/时区/WebRTC/WebGL/Canvas/Audio）| Chromium C++ 源码改造 | 真内核级，非 JS 注入 |
| Cookie/LocalStorage/IndexedDB | Chromium 存储分区 | 同进程不同存储命名空间 |
| 代理 | 源码级 per-tab 代理路由 | 非 --proxy-server（进程级）|
| 静态资源缓存 | 跨容器共享（节省流量）| bypass 规则让静态资源直连 |

**声明通过的检测：** Browserscan、CreepJS、Pixelscan

### 3.3 EasyCDP API 设计（从 README 代码示例）

```python
browser = await EasyBrowserCDP.launch_and_connect(port=9992, executable=rfp_chrome.exe)
container = await browser.new_container(
    name="account-1",
    fingerprint=fp,       # 每个容器独立 FP 对象
    proxy="http://user:pass@host:port"  # 每个容器独立代理
)
page = await container.new_page("https://example.com")
# CDP 控制走 container 维度，不是 browser 维度
```

说明底层很可能是：
- Chromium 扩展协议：`new_container` → 创建有独立 StoragePartition 的 Tab
- CDP 连接附加到 container 级别的 Target，而非整个 BrowserContext

### 3.4 对 browser-model 的借鉴价值

| 设计点 | 借鉴价值 | 实现难度 | 建议 |
|---|---|---|---|
| 同进程多容器 Tab | ★★★★★ 内存节省 30%+ | 极高（需改 Chromium 源码）| 长期目标；短期不可行 |
| 源码级代理 bypass | ★★★★ 大幅降代理流量 | 极高 | 同上 |
| per-Tab 代理路由 | ★★★★★ 多账号并发必须 | 极高 | 短期替代方案：多 BrowserContext |
| JS 层 per-Tab 指纹 | ★★★ 可行但有泄漏风险 | 低 | 当前 browser-model 已有 fpSeed，可扩展到 per-context |
| 容器 API 抽象 | ★★★ 清晰的多账号 API | 中 | 可在 cdp-ws-server 路由层参考 |

---

## 四、当前 browser-model 能力差距

### 4.1 架构现状

```
WebSocket 连接 (cdp-ws-server.ts)
    │
    └── new CdpSession(ws, sessionId?)
            │
            ├── browser.newContext()   ← 每连接一个独立 BrowserContext（Cookie/存储隔离）
            ├── addInitScript(STEALTH_INIT)   ← 3 层 JS 指纹伪装
            ├── ctx.newPage()
            └── cdp = ctx.newCDPSession(page)
                    ├── Page.startScreencast → JPEG 帧推前端
                    └── Input.dispatchMouse/Key → 前端操作 → 浏览器
```

### 4.2 具体差距

| 能力 | Open-Anti-Browser | EasyBrowser | browser-model 现状 | 差距描述 |
|---|---|---|---|---|
| **多窗口 CDP 动作同步** | ✅ BrowserSynchronizer | ✗ | ✗ | 无 master/follower 同步机制，每个 WS session 相互独立 |
| **Tab UI 同步** | ✅ 新建/关闭/激活标签同步 | ✗ | ✗ | 无跨 session 的标签页同步 |
| **页签级指纹隔离** | ✗（进程级）| ✅（内核级）| ✗ | 每个 BrowserContext 共享同一个 fpSeed 模板，无 per-tab 差异 |
| **页签级 Cookie 隔离** | ✗（进程级）| ✅（内核级）| ✅ | browser-model 每连接独立 BrowserContext，Cookie 已隔离 |
| **页签级代理路由** | ✗（进程级）| ✅（内核级）| ✗ | 单一 BROWSER_PROXY 环境变量，所有 session 共享同一代理 |
| **Session 持久化** | ✅ 磁盘 profile 目录 | ✅ 容器持久化 | ✅ | browser-model 已有 /root/browser-sessions/<id>.json |
| **Geo 三自洽（IP/TZ/语言）**| ✅ resolve_geo_profile | ✅ | ✗ | 当前 TZ/locale 硬编码为 LA，代理换 IP 后不同步 |
| **多 Tab 内存优化** | ✗（多进程）| ✅（单进程）| ✗ | 当前无 Tab 复用机制，多账号需多 WS 连接 |
| **Cloudflare 挑战自动等待** | ✗ | ✗ | ✅ | browser-model 独有 |
| **ServiceWorker 指纹注入** | ✗ | ✗ | ✅ | browser-model 独有 |

---

## 五、自研路线图建议

### P0：多窗口 CDP 动作同步器（可借鉴 Open-Anti-Browser）

**目标：** browser-model 支持 master/follower 多 session 同步，适用于批量账号同步操作场景。

**实现方案（TypeScript，复用现有 CdpSession）：**

```
新增 cdp-synchronizer.ts：

class CdpSynchronizer {
  master: CdpSession
  followers: Map<string, CdpSession>
  options: SyncOptions  // navigate/click/input/scroll/keyboard/mouseMove/browserUI

  start(masterId, followerIds, opts) {
    // 1. 向 master 的 page 注入捕获脚本（改写 MASTER_INJECT_SCRIPT 为 TS）
    //    使用 CDP Runtime.addBinding(__bmSyncBinding) 注册回调
    //    比 Open-Anti-Browser 的轮询更实时（事件驱动，无 1.2s 延迟）
    // 2. master CDP session 监听 Runtime.bindingCalled 事件
    // 3. 事件 dispatch 到 followers 的 CdpSession
    //    this.cdp.send(Input.dispatchMouseEvent, ...)  ← 已有
    //    this.page.evaluate(expr)                        ← 已有
  }
}
```

**与 Open-Anti-Browser 的差异点：**
- 用 CDP Runtime.addBinding（事件驱动）代替 1.2s 轮询，延迟降至 <20ms
- 不需要 CdpPageClient（已有 Playwright CdpSession）
- Tab UI 同步用 Playwright `context.on(page)` 事件代替 /json 轮询

**工作量估算：** ~600-800 行 TS，~3-5 天

**API 设计：**
```http
POST /api/browser/sync/start
  { master_session_id, follower_session_ids[], options:{navigate,click,input,scroll,keyboard,mouseMove,browserUI} }

POST /api/browser/sync/stop
POST /api/browser/sync/navigate   { url, include_master }
GET  /api/browser/sync/status
```

---

### P1：per-Session 代理路由

**目标：** 每个 WS session 可传入独立代理，打破全局 BROWSER_PROXY 限制。

**实现方案：**

```typescript
// SessionOpts 中新增：
proxy?: string  // "http://user:pass@host:port" 或 "socks5://..."

// CdpSession.start() 中：
// 方案 A（推荐）：每 session 独立 BrowserContext，newContext({proxy: {server}})
//   Playwright 支持 context 级代理，覆盖进程级 --proxy-server
this.ctx = await browser.newContext({
  ...,
  proxy: opts.proxy ? { server: opts.proxy } : undefined,
});

// 方案 B：多进程（参考 Open-Anti-Browser），资源消耗高，不推荐
```

**注意：** Playwright BrowserContext-level proxy 已完整支持，SOCKS5/HTTP 均可，无需改动 Chrome 启动参数。

**工作量估算：** ~50 行修改，~0.5 天

---

### P2：Geo 三自洽（IP/时区/语言联动）

**目标：** session 传入代理后自动推算 TZ/locale，确保 IP、时区、Accept-Language 一致，通过 Browserscan TZ 一致性检测。

**参考 Open-Anti-Browser：** `resolve_geo_profile(proxy_url)` 调用 ip-api.com/ipinfo.io 获取 geo 信息。

**实现方案：**
```typescript
// 新增 lib/geo-resolver.ts
async function resolveGeoProfile(proxy: string): Promise<GeoProfile>
// 调用 ip-api.com JSON endpoint（走代理），返回 {timezone, language, locale, latitude, longitude}

// CdpSession.start() 中：
const geo = opts.proxy ? await resolveGeoProfile(opts.proxy) : DEFAULT_GEO_LA;
// 用 geo.timezone 替换硬编码 "America/Los_Angeles"
// 用 geo.language 替换硬编码 "en-US"
// 用 geo.latitude/longitude 替换硬编码地理坐标
// STEALTH_INIT 中 timezone 和 locale 通过 window.__bmGeo = {...} 注入
```

**工作量估算：** ~150 行 TS，~1 天

---

### P3：页签级隔离（长期，仿 EasyBrowser 思路）

**目标：** 单 BrowserContext 下 N 个 Tab，每 Tab 独立 Cookie/Storage（通过 StoragePartition）。

**实现路径（无需改 Chromium 源码的近似方案）：**

```
方案 A（当前架构延伸，推荐）：
  继续保持 1 BrowserContext per Session
  通过 P1 的 per-context proxy 实现代理隔离
  通过 storageState 持久化实现账号切换
  优点：实现简单，已验证可用
  缺点：N session = N BrowserContext，内存无法像 EasyBrowser 那样共享

方案 B（单进程多账号，中期）：
  每个"账号"= 1 BrowserContext（Playwright 已支持多 context 共享进程）
  同一 browser 实例下创建多个 context（已部分实现——所有 session 共享同一 browser 单例）
  当前差距：需要 per-context proxy（P1 已覆盖）+ per-context fpSeed（已有）
  实际上 browser-model 当前架构已接近此方案，差的是 per-context proxy

方案 C（真页签级，长期）：
  需定制 Chromium 源码（StoragePartition per tab），参考 EasyBrowser 
  工作量：数月，超出当前 scope
```

**结论：** 方案 B（P1 完成后的当前架构）已能满足 80% 的多账号并发需求，方案 C 留作长期演进目标。

---

## 六、总结

### Open-Anti-Browser 评估结论

**可借鉴价值：高。** 其 `BrowserSynchronizer` 设计完整、工程质量较好，事件捕获（DOM listener + selector 构建）和事件分发（Input.dispatch*/Runtime.evaluate）的实现细节可直接参考移植到 browser-model TypeScript 栈。主要差异在于 browser-model 用 Playwright CdpSession 而非 CdpPageClient，事件传输层可升级为 CDP Runtime.addBinding 减少轮询延迟。

**不借鉴部分：** 进程级代理（用 --proxy-server），browser-model 用 Playwright context-level proxy 更优雅；轮询 /json 端点检测 Tab 变化，browser-model 用 Playwright 事件更实时。

### EasyBrowser 评估结论

**可借鉴价值：中（架构设计层面）。** 由于未开源，无法直接复用代码。其核心价值在于验证了"单进程 N 容器"思路在商业产品中的可行性，以及 per-tab 代理路由（源码级）的必要性。browser-model 短期通过 Playwright context-level proxy（P1）可实现等效的代理隔离，中期多 context 共享进程已是同等架构。真正的 Tab 级 StoragePartition 隔离需要改 Chromium 内核，不在当前路线图。

### 优先级建议

```
P0  多窗口 CDP 动作同步器   ~5天   高价值，有完整参考，自研可行
P1  per-Session 代理路由    ~0.5天 低成本，Playwright 原生支持
P2  Geo 三自洽              ~1天   提升指纹一致性评分
P3  Tab 级隔离（方案 B）    通过 P1 即可满足，无额外开发
```

---

*本文档由 Reseek Agent 自动生成，基于源码静态分析，未进行运行时测试验证。*
