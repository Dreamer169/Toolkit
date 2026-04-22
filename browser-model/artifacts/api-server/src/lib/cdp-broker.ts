/**
 * CDP screencast + remote input broker.
 *
 * 替代 URL-rewrite 代理（routes/proxy.ts）。
 * 思路：服务端跑真浏览器（Playwright + 系统 Chromium），通过 CDP
 *   - Page.startScreencast 把 viewport 以 JPEG 帧推给前端
 *   - Input.dispatchMouseEvent / dispatchKeyEvent / dispatchMouseWheelEvent
 *     接收前端坐标/键盘事件并转发给真浏览器
 * 前端只负责把 JPEG 画到 <canvas>，捕获鼠标键盘事件回传。
 *
 * 这样彻底绕过：
 *   - URL 重写代理对 Next.js / TurboPack 懒加载 chunk 的破坏
 *     （__turbopack_load_page_chunks__ is not defined）
 *   - 第三方脚本（googleads/recaptcha）走代理后 MIME 错乱被 Chrome 拒绝
 *   - X-Frame-Options / CSP frame-ancestors 阻止 iframe 嵌入
 */

import { chromium, type Browser, type BrowserContext, type Page } from "playwright";
import type { WebSocket } from "ws";
import { logger } from "./logger.js";

type CDPSession = Awaited<ReturnType<BrowserContext["newCDPSession"]>>;

/**
 * 把地址栏里随便丢进来的字符串规范成可以 page.goto 的 URL：
 *   "google.com"          -> "https://google.com"
 *   "localhost:3000"      -> "http://localhost:3000"
 *   "what is rust"        -> "https://www.google.com/search?q=what%20is%20rust"
 *   "https://x.com/y"     -> 原样
 */
function normalizeUrl(raw: string): string {
  const s = raw.trim();
  if (!s) return "about:blank";
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) return s;
  if (/^about:|^chrome:|^data:|^javascript:|^file:/i.test(s)) return s;
  // host[:port][/path] 形如 "x.y", "localhost", "10.0.0.1:8080"
  const looksLikeHost = /^[\w-]+(\.[\w-]+)+(:\d+)?(\/.*)?$/.test(s)
    || /^localhost(:\d+)?(\/.*)?$/.test(s)
    || /^(\d{1,3}\.){3}\d{1,3}(:\d+)?(\/.*)?$/.test(s);
  if (looksLikeHost) {
    const proto = /^localhost|^127\.|^10\.|^192\.168\./.test(s) ? "http" : "https";
    return `${proto}://${s}`;
  }
  // 没空格也不像 URL → 兜底当域名加 https
  if (!/\s/.test(s) && /^[\w-]+\.[a-z]{2,}/i.test(s)) return `https://${s}`;
  // 含空格 → 走 Google 搜索
  return `https://www.google.com/search?q=${encodeURIComponent(s)}`;
}

interface ClientMsg {
  type:
    | "navigate"
    | "back"
    | "forward"
    | "reload"
    | "mouse"
    | "wheel"
    | "key"
    | "type"
    | "resize"
    | "ack";
  // navigate
  url?: string;
  // mouse
  x?: number;
  y?: number;
  button?: "left" | "middle" | "right" | "none";
  action?: "down" | "up" | "move";
  clickCount?: number;
  buttons?: number;
  modifiers?: number;
  // wheel
  deltaX?: number;
  deltaY?: number;
  // key
  keyCode?: number;
  key?: string;
  code?: string;
  text?: string;
  unmodifiedText?: string;
  isKeypad?: boolean;
  isSystemKey?: boolean;
  location?: number;
  keyAction?: "keyDown" | "keyUp" | "rawKeyDown" | "char";
  // type
  textBlock?: string;
  // resize
  width?: number;
  height?: number;
  deviceScaleFactor?: number;
  // ack
  sessionId?: number;
}

interface SessionOpts {
  width: number;
  height: number;
  proxy?: string;
  userAgent?: string;
}

let _browserPromise: Promise<Browser> | null = null;

async function getBrowser(): Promise<Browser> {
  if (_browserPromise) return _browserPromise;
  const exe = process.env.REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE
    || process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
    || undefined;
  const proxyEnv = process.env.BROWSER_PROXY;
  // 如果有 DISPLAY（生产环境的 Xvfb :99），就跑 headed Chromium —— 真 X 显示器
  // 上的 Chrome 比 headless Chromium 难被反爬检测（Cloudflare / hCaptcha 等）
  const display = process.env.DISPLAY;
  const useHeaded = !!display && process.platform === "linux";

  const args = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-extensions",
    "--mute-audio",
    "--disable-blink-features=AutomationControlled",
    "--no-default-browser-check",
    "--no-first-run",
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-site-isolation-trials",
    // 反爬识别项
    "--exclude-switches=enable-automation",
    "--disable-infobars",
  ];
  if (!useHeaded) args.push("--disable-gpu");

  _browserPromise = chromium.launch({
    headless: !useHeaded,
    executablePath: exe,
    args,
    proxy: proxyEnv ? { server: proxyEnv } : undefined,
    env: useHeaded ? { ...process.env, DISPLAY: display } as Record<string, string> : undefined,
  }).then((b) => {
    logger.info({ exe, proxy: !!proxyEnv, headed: useHeaded, display }, "[cdp-broker] browser launched");
    return b;
  }).catch((err) => {
    _browserPromise = null;
    throw err;
  });
  return _browserPromise;
}

export class CdpSession {
  private ctx: BrowserContext | null = null;
  private page: Page | null = null;
  private cdp: CDPSession | null = null;
  private closed = false;
  private currentUrl = "about:blank";
  private viewport = { w: 1280, h: 800 };

  constructor(private ws: WebSocket) {}

  send(obj: Record<string, unknown>) {
    if (this.ws.readyState === 1) {
      try { this.ws.send(JSON.stringify(obj)); } catch { /* ignore */ }
    }
  }

  async start(opts: SessionOpts) {
    const browser = await getBrowser();
    this.viewport = { w: opts.width, h: opts.height };
    // 用 Linux UA —— 跟服务端真实 platform 对得上，避免 navigator.platform 与
    // userAgent 互相矛盾被反爬识别
    const ua = opts.userAgent || (
      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      + "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    );
    this.ctx = await browser.newContext({
      viewport: { width: opts.width, height: opts.height },
      deviceScaleFactor: opts.deviceScaleFactor ?? 1,
      userAgent: ua,
      locale: "en-US",
      timezoneId: "America/Los_Angeles",
      ignoreHTTPSErrors: true,
    });
    // 反爬指纹修整：抹掉 navigator.webdriver、伪造 chrome.runtime、修整 plugins/languages
    await this.ctx.addInitScript(() => {
      try {
        Object.defineProperty(navigator, "webdriver", { get: () => false });
      } catch { /* ignore */ }
      try {
        // @ts-expect-error - chrome 对象在非 Chrome 启动时可能缺失
        if (!window.chrome) window.chrome = { runtime: {} };
      } catch { /* ignore */ }
      try {
        Object.defineProperty(navigator, "languages", { get: () => ["en-US", "en"] });
      } catch { /* ignore */ }
      try {
        const orig = navigator.permissions?.query?.bind(navigator.permissions);
        if (orig) {
          // @ts-expect-error - 重写以避开 notifications 检测套路
          navigator.permissions.query = (p: { name: string }) => (
            p && p.name === "notifications"
              ? Promise.resolve({ state: Notification.permission } as PermissionStatus)
              : orig(p)
          );
        }
      } catch { /* ignore */ }
    });
    this.page = await this.ctx.newPage();
    this.cdp = await this.ctx.newCDPSession(this.page);

    // 监听导航变化 → 推给前端更新地址栏
    this.page.on("framenavigated", (frame) => {
      if (frame === this.page!.mainFrame()) {
        this.currentUrl = frame.url();
        this.send({ type: "url", url: this.currentUrl, title: "" });
        this.page!.title().then((t) => this.send({ type: "title", title: t })).catch(() => {});
      }
    });
    this.page.on("close", () => this.close().catch(() => {}));
    // alert / confirm / prompt / beforeunload —— 不处理会挂住主线程，
    //   表现为：触发任意 JS 弹窗后整个页面冻住、点哪都没反应。
    //   策略：默认全部 dismiss（confirm/prompt 视为取消），并把内容上报给前端
    //   以便将来弹原生模态。
    this.page.on("dialog", (dialog) => {
      this.send({ type: "dialog", kind: dialog.type(), message: dialog.message() });
      dialog.dismiss().catch(() => {});
    });
    // window.open() 弹窗页面不会画到当前 canvas —— 把它的 URL 拿出来在主页面跳转，
    //   行为类似按住 Ctrl 点链接的反向：把"新窗口"重定向回当前 tab。
    this.ctx.on("page", (p) => {
      if (p === this.page) return;
      const url = p.url();
      p.close().catch(() => {});
      if (url && url !== "about:blank") {
        this.page?.goto(url, { waitUntil: "domcontentloaded", timeout: 60_000 }).catch(() => {});
      }
    });

    // 启动 CDP screencast，每帧推到前端
    await this.cdp.send("Page.enable");
    this.cdp.on("Page.screencastFrame", (params: { data: string; sessionId: number; metadata: unknown }) => {
      this.send({ type: "frame", data: params.data, sid: params.sessionId });
      // 默认服务端立即 ack；如果前端要求节流（resize 时）再以 ack 替代
      this.cdp!.send("Page.screencastFrameAck", { sessionId: params.sessionId }).catch(() => {});
    });
    await this.cdp.send("Page.startScreencast", {
      format: "jpeg",
      quality: 60,
      maxWidth: opts.width,
      maxHeight: opts.height,
      everyNthFrame: 1,
    });

    this.send({ type: "ready", width: opts.width, height: opts.height });
    logger.info({ w: opts.width, h: opts.height }, "[cdp-broker] session started");
  }

  async handleMessage(raw: string | Buffer) {
    if (this.closed || !this.page || !this.cdp) return;
    let msg: ClientMsg;
    try { msg = JSON.parse(raw.toString()) as ClientMsg; } catch { return; }
    try {
      switch (msg.type) {
        case "navigate":
          if (msg.url) {
            const target = normalizeUrl(msg.url);
            await this.page.goto(target, { waitUntil: "domcontentloaded", timeout: 60_000 }).catch((e) => {
              this.send({ type: "navError", url: target, error: String(e?.message ?? e) });
            });
          }
          break;
        case "back":   await this.page.goBack({ waitUntil: "domcontentloaded" }).catch(() => {}); break;
        case "forward":await this.page.goForward({ waitUntil: "domcontentloaded" }).catch(() => {}); break;
        case "reload": await this.page.reload({ waitUntil: "domcontentloaded" }).catch(() => {}); break;
        case "mouse": {
          const action = msg.action ?? "move";
          const cdpType = action === "down" ? "mousePressed" : action === "up" ? "mouseReleased" : "mouseMoved";
          await this.cdp.send("Input.dispatchMouseEvent", {
            type: cdpType,
            x: msg.x ?? 0,
            y: msg.y ?? 0,
            button: msg.button ?? "none",
            buttons: msg.buttons ?? 0,
            clickCount: msg.clickCount ?? (action === "down" ? 1 : 0),
            modifiers: msg.modifiers ?? 0,
          });
          break;
        }
        case "wheel":
          await this.cdp.send("Input.dispatchMouseEvent", {
            type: "mouseWheel",
            x: msg.x ?? 0,
            y: msg.y ?? 0,
            deltaX: msg.deltaX ?? 0,
            deltaY: msg.deltaY ?? 0,
            modifiers: msg.modifiers ?? 0,
          });
          break;
        case "key":
          await this.cdp.send("Input.dispatchKeyEvent", {
            type: msg.keyAction ?? "keyDown",
            modifiers: msg.modifiers ?? 0,
            text: msg.text,
            unmodifiedText: msg.unmodifiedText ?? msg.text,
            key: msg.key,
            code: msg.code,
            windowsVirtualKeyCode: msg.keyCode,
            nativeVirtualKeyCode: msg.keyCode,
            location: msg.location ?? 0,
            isKeypad: msg.isKeypad ?? false,
            isSystemKey: msg.isSystemKey ?? false,
          });
          break;
        case "type":
          if (msg.textBlock) {
            await this.page.keyboard.insertText(msg.textBlock);
          }
          break;
        case "resize":
          if (msg.width && msg.height) {
            this.viewport = { w: msg.width, h: msg.height };
            await this.page.setViewportSize({ width: msg.width, height: msg.height });
            // 重启 screencast 以套用新尺寸
            await this.cdp.send("Page.stopScreencast").catch(() => {});
            await this.cdp.send("Page.startScreencast", {
              format: "jpeg",
              quality: 60,
              maxWidth: msg.width,
              maxHeight: msg.height,
              everyNthFrame: 1,
            });
          }
          break;
        case "ack":
          // 客户端要求显式 ack 控流：默认我们已 ack，所以这里 no-op
          break;
      }
    } catch (e) {
      logger.warn({ msgType: msg.type, err: String(e) }, "[cdp-broker] handleMessage error");
    }
  }

  async close() {
    if (this.closed) return;
    this.closed = true;
    try { await this.cdp?.send("Page.stopScreencast"); } catch {}
    try { await this.cdp?.detach(); } catch {}
    try { await this.page?.close({ runBeforeUnload: false }); } catch {}
    try { await this.ctx?.close(); } catch {}
    this.cdp = null;
    this.page = null;
    this.ctx = null;
    logger.info("[cdp-broker] session closed");
  }
}

export async function shutdownBrowser() {
  if (!_browserPromise) return;
  try {
    const b = await _browserPromise;
    await b.close();
  } catch {}
  _browserPromise = null;
}
