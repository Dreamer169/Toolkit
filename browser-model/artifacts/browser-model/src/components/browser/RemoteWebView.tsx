/**
 * 远端浏览器渲染面板：
 *   - 通过 WebSocket 连接 /api/cdp/ws，接收 CDP screencast JPEG 帧画到 <canvas>
 *   - 监听本地 mouse / wheel / keyboard 事件并以 CDP 输入事件格式回传
 *
 * 替代旧 iframe + URL 重写代理方案，彻底绕开 Next.js/TurboPack 懒加载与
 * 第三方脚本 MIME 校验问题（真浏览器跑在服务端，前端只是显示器）。
 */
import { useEffect, useRef, useState } from "react";
import { useBrowserStore, type Tab } from "@/hooks/use-browser-store";
import { Loader2 } from "lucide-react";

interface RemoteWebViewProps {
  tab: Tab;
}

// chromium CDP modifier bits
const MOD_ALT   = 1;
const MOD_CTRL  = 2;
const MOD_META  = 4;
const MOD_SHIFT = 8;

function modBits(e: KeyboardEvent | MouseEvent | WheelEvent): number {
  let m = 0;
  if (e.altKey)   m |= MOD_ALT;
  if (e.ctrlKey)  m |= MOD_CTRL;
  if (e.metaKey)  m |= MOD_META;
  if (e.shiftKey) m |= MOD_SHIFT;
  return m;
}

function buttonName(b: number): "left" | "middle" | "right" | "none" {
  if (b === 0) return "left";
  if (b === 1) return "middle";
  if (b === 2) return "right";
  return "none";
}

// 把按下的鼠标键集合（buttons 位掩码）映射到 CDP 期望的 buttons 整数
// CDP 与浏览器的 MouseEvent.buttons 编码相同（left=1,right=2,middle=4），可直接传

export function RemoteWebView({ tab }: RemoteWebViewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const readyRef = useRef(false);
  const [ready, setReady] = useState(false);
  // 复用一个 Image —— 之前每帧 new Image() + 异步 onload 既泄漏又会乱序
  const reusableImgRef = useRef<HTMLImageElement | null>(null);
  // 最近一帧的 base64，用于在 image 还没空闲时合并丢掉中间帧
  const pendingFrameRef = useRef<string | null>(null);
  const drawingRef = useRef(false);
  const sizeRef = useRef({ w: 1280, h: 800 });
  // 服务端反射回来的 URL —— 用来抑制"前端收到 url 后又把同一个 url 重新 navigate
  // 给服务端"导致的反馈循环（会强制 page.goto 中断 SPA 跳转，表现为点击无反应）
  const reflectedUrlRef = useRef<string | null>(null);
  // 服务端真实当前 URL；与之相同的 tab.url 变更不需要再发 navigate
  const serverUrlRef = useRef<string>("");
  const { updateTabStatus, navigateTab, addToHistory } = useBrowserStore();

  // 单 Image 流水线 —— 拉最新一帧画到 canvas，画完看队列里还有没有再画一次
  function drawNextFrame() {
    const data = pendingFrameRef.current;
    if (!data) { drawingRef.current = false; return; }
    pendingFrameRef.current = null;
    drawingRef.current = true;
    let img = reusableImgRef.current;
    if (!img) { img = new Image(); reusableImgRef.current = img; }
    img.onload = () => {
      const c = canvasRef.current;
      if (c) {
        const ctx = c.getContext("2d");
        if (ctx) ctx.drawImage(img!, 0, 0, c.width, c.height);
      }
      // 看看在画这帧期间又来了新帧没有
      if (pendingFrameRef.current) drawNextFrame();
      else drawingRef.current = false;
    };
    img.onerror = () => {
      drawingRef.current = false;
      if (pendingFrameRef.current) drawNextFrame();
    };
    img.src = `data:image/jpeg;base64,${data}`;
  }

  // 建立 WebSocket
  useEffect(() => {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const initialUrl = encodeURIComponent(tab.url || "about:blank");
    const w = sizeRef.current.w;
    const h = sizeRef.current.h;
    const url = `${proto}//${window.location.host}/api/cdp/ws?w=${w}&h=${h}&url=${initialUrl}`;
    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => {
      // 连接建立后由 query string 中的 url 已触发首次 navigate
      readyRef.current = false;
    };

    ws.onmessage = (ev) => {
      let msg: { type: string; [k: string]: unknown };
      try { msg = JSON.parse(typeof ev.data === "string" ? ev.data : new TextDecoder().decode(ev.data as ArrayBuffer)); }
      catch { return; }
      switch (msg.type) {
        case "ready":
          readyRef.current = true;
          setReady(true);
          updateTabStatus(tab.id, { isLoading: false });
          break;
        case "frame": {
          const data = msg.data as string;
          // 把最新一帧塞进队列；如果当前没在画就触发画图，否则等画完再画
          // 最新的（中间帧丢弃，避免乱序+堆积）
          pendingFrameRef.current = data;
          if (!drawingRef.current) drawNextFrame();
          break;
        }
        case "url": {
          const newUrl = msg.url as string;
          serverUrlRef.current = newUrl;
          if (newUrl && newUrl !== tab.url) {
            // 标记这个 URL 是服务端反射，下面的 useEffect 不要再回送 navigate
            reflectedUrlRef.current = newUrl;
            navigateTab(tab.id, newUrl);
            addToHistory(newUrl, tab.title || newUrl);
          }
          updateTabStatus(tab.id, { isLoading: false });
          break;
        }
        case "title": {
          const t = (msg.title as string) || "";
          if (t) updateTabStatus(tab.id, { title: t });
          break;
        }
        case "navError":
          updateTabStatus(tab.id, { isLoading: false });
          break;
      }
    };

    ws.onclose = () => { readyRef.current = false; setReady(false); };
    ws.onerror = () => { /* will be followed by close */ };

    return () => {
      try { ws.close(); } catch { /* ignore */ }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab.id]);

  // tab.url 变化（地址栏输入）→ 发 navigate
  // 关键：如果这次变化是服务端刚反射回来的（页面内点击/SPA 路由跳转引起），
  //   就不要再 page.goto 一次，否则会打断真浏览器正在执行的跳转，表现为
  //   "点击没反应"。
  useEffect(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== 1) return;
    if (!readyRef.current) return;
    if (!tab.url || tab.url.startsWith("browser://")) return;
    if (reflectedUrlRef.current && reflectedUrlRef.current === tab.url) {
      reflectedUrlRef.current = null;
      return;
    }
    if (tab.url === serverUrlRef.current) return;
    ws.send(JSON.stringify({ type: "navigate", url: tab.url }));
    updateTabStatus(tab.id, { isLoading: true });
  }, [tab.url, tab.id, updateTabStatus]);

  // 容器尺寸变化 → 通知服务端重设 viewport
  // 拖拽窗口时 ResizeObserver 每帧触发一次，每次都会让服务端 stop+start
  // screencast，会卡到不能用。debounce 250ms 等用户拖完再换尺寸。
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    let pending: { w: number; h: number } | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const flush = () => {
      timer = null;
      if (!pending) return;
      const { w: cw, h: ch } = pending;
      pending = null;
      if (cw === sizeRef.current.w && ch === sizeRef.current.h) return;
      sizeRef.current = { w: cw, h: ch };
      const c = canvasRef.current;
      if (c) { c.width = cw; c.height = ch; }
      const ws = wsRef.current;
      if (ws && ws.readyState === 1) {
        ws.send(JSON.stringify({ type: "resize", width: cw, height: ch }));
      }
    };
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const cw = Math.max(320, Math.round(entry.contentRect.width));
        const ch = Math.max(240, Math.round(entry.contentRect.height));
        pending = { w: cw, h: ch };
        if (timer) clearTimeout(timer);
        timer = setTimeout(flush, 250);
      }
    });
    ro.observe(el);
    return () => { ro.disconnect(); if (timer) clearTimeout(timer); };
  }, []);

  // 鼠标 / 滚轮 / 键盘 事件
  function send(obj: Record<string, unknown>) {
    const ws = wsRef.current;
    if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
  }

  function canvasCoords(e: MouseEvent | WheelEvent): { x: number; y: number } {
    const c = canvasRef.current;
    if (!c) return { x: 0, y: 0 };
    const r = c.getBoundingClientRect();
    const sx = c.width / r.width;
    const sy = c.height / r.height;
    return { x: Math.round((e.clientX - r.left) * sx), y: Math.round((e.clientY - r.top) * sy) };
  }

  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const onDown = (e: MouseEvent) => {
      e.preventDefault();
      c.focus();
      const { x, y } = canvasCoords(e);
      send({ type: "mouse", action: "down", x, y, button: buttonName(e.button), buttons: e.buttons, clickCount: e.detail || 1, modifiers: modBits(e) });
    };
    const onUp = (e: MouseEvent) => {
      e.preventDefault();
      const { x, y } = canvasCoords(e);
      send({ type: "mouse", action: "up", x, y, button: buttonName(e.button), buttons: e.buttons, clickCount: e.detail || 1, modifiers: modBits(e) });
    };
    const onMove = (e: MouseEvent) => {
      const { x, y } = canvasCoords(e);
      send({ type: "mouse", action: "move", x, y, button: "none", buttons: e.buttons, modifiers: modBits(e) });
    };
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const { x, y } = canvasCoords(e);
      // WheelEvent.deltaMode: 0=pixel, 1=line(~40px/line), 2=page(~viewport h)
      // CDP 期望像素，必须换算，否则 line 模式下一格滚到底
      let dx = e.deltaX, dy = e.deltaY;
      if (e.deltaMode === 1) { dx *= 40; dy *= 40; }
      else if (e.deltaMode === 2) {
        const c = canvasRef.current;
        const vh = c?.height ?? 800;
        dx *= vh; dy *= vh;
      }
      send({ type: "wheel", x, y, deltaX: dx, deltaY: dy, modifiers: modBits(e) });
    };
    const onCtxMenu = (e: Event) => e.preventDefault();

    c.addEventListener("mousedown", onDown);
    c.addEventListener("mouseup", onUp);
    c.addEventListener("mousemove", onMove);
    c.addEventListener("wheel", onWheel, { passive: false });
    c.addEventListener("contextmenu", onCtxMenu);
    return () => {
      c.removeEventListener("mousedown", onDown);
      c.removeEventListener("mouseup", onUp);
      c.removeEventListener("mousemove", onMove);
      c.removeEventListener("wheel", onWheel);
      c.removeEventListener("contextmenu", onCtxMenu);
    };
  }, []);

  // 键盘事件：仅当 canvas 拥有焦点时转发
  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const onKey = (e: KeyboardEvent, kind: "keyDown" | "keyUp") => {
      if (document.activeElement !== c) return;
      // 让浏览器本身吃 F5/F12 等保留键
      if (e.key === "F5" || e.key === "F12") return;
      e.preventDefault();
      const text = (kind === "keyDown" && e.key.length === 1) ? e.key : undefined;
      send({
        type: "key",
        keyAction: kind === "keyDown" ? (text ? "char" : "rawKeyDown") : "keyUp",
        key: e.key,
        code: e.code,
        text,
        unmodifiedText: text,
        keyCode: e.keyCode,
        modifiers: modBits(e),
        location: e.location,
      });
      // rawKeyDown 之外，再补一次 keyDown 让 input 字符正常提交
      if (kind === "keyDown" && !text) {
        send({
          type: "key",
          keyAction: "keyDown",
          key: e.key, code: e.code,
          keyCode: e.keyCode, modifiers: modBits(e), location: e.location,
        });
      }
    };
    const onDown = (e: KeyboardEvent) => onKey(e, "keyDown");
    const onUp   = (e: KeyboardEvent) => onKey(e, "keyUp");
    window.addEventListener("keydown", onDown);
    window.addEventListener("keyup", onUp);
    return () => {
      window.removeEventListener("keydown", onDown);
      window.removeEventListener("keyup", onUp);
    };
  }, []);

  return (
    <div ref={containerRef} className="absolute inset-0 bg-white">
      <canvas
        ref={canvasRef}
        tabIndex={0}
        width={1280}
        height={800}
        style={{ width: "100%", height: "100%", outline: "none", cursor: "default" }}
        onMouseEnter={(e) => (e.currentTarget as HTMLCanvasElement).focus()}
      />
      {!ready && (
        <div className="absolute top-2 right-2 flex items-center gap-2 text-xs text-muted-foreground bg-background/80 px-2 py-1 rounded">
          <Loader2 className="h-3 w-3 animate-spin" />
          connecting…
        </div>
      )}
    </div>
  );
}
