/**
 * cdp-synchronizer.ts
 *
 * CDP 多窗口动作同步器。
 *
 * 架构：
 *   master session → DOM 事件捕获脚本 → CDP Runtime.addBinding 实时推送 (<5ms)
 *   → CdpSynchronizer.bindingListener → 广播到所有 follower sessions
 *   → page.evaluate(expr) / cdp.send(Input.dispatch*)
 *
 * 支持事件：navigate / click / input / change / wheel / scroll / keydown / mouse_move
 * Browser-UI 同步（new-tab/close-tab）通过 Playwright context events 实现（优于轮询）。
 */
import { logger } from "./logger.js";
import { sessionRegistry } from "./cdp-broker.js";
import type { Page, CDPSession as PwCDPSession } from "playwright";

export interface SyncOptions {
  syncNavigation?: boolean;
  syncClick?: boolean;
  syncInput?: boolean;
  syncScroll?: boolean;
  syncKeyboard?: boolean;
  syncMouseMove?: boolean;
  syncBrowserUi?: boolean;
  clickDelayMs?: number;
  inputDelayMs?: number;
}

const DEFAULT_OPTIONS: Required<SyncOptions> = {
  syncNavigation: true,
  syncClick: true,
  syncInput: true,
  syncScroll: true,
  syncKeyboard: true,
  syncMouseMove: false,
  syncBrowserUi: true,
  clickDelayMs: 0,
  inputDelayMs: 0,
};

const BINDING_NAME = "__bmSyncBinding";

// ─── Master capture script ────────────────────────────────────────────────────
// Injected into master page via page.evaluate() after CDP binding is registered.
// Primary transport: window.__bmSyncBinding (CDP Runtime.addBinding, lowest latency).
// Fallback: console.debug with "__BM_SYNC__" prefix (drained by Runtime.evaluate polling).
const MASTER_CAPTURE_SCRIPT = String.raw`
(() => {
  if (window.__bmSyncInstalled) return 'already';
  window.__bmSyncInstalled = true;
  const BN = '__bmSyncBinding';
  const emit = (type, payload) => {
    const body = JSON.stringify({ type, payload, href: location.href, ts: Date.now() });
    try { if (typeof window[BN] === 'function') { window[BN](body); return; } } catch(_) {}
    try { console.debug('__BM_SYNC__' + body); } catch(_) {}
  };

  // CSS selector builder: id > data-testid/data-test/name > nth-of-type path
  const cssEsc = v => { try { return CSS && CSS.escape ? CSS.escape(String(v)) : String(v).replace(/([^\w-])/g,'\\$1'); } catch(_){ return String(v); } };
  const attrSel = (node, k) => {
    const val = node.getAttribute(k); if (!val) return null;
    const s = node.localName.toLowerCase()+'['+k+'="'+String(val).replace(/\\/g,'\\\\').replace(/"/g,'\\"')+'"]';
    try { return document.querySelectorAll(s).length===1?s:null; } catch(_){ return null; }
  };
  const buildSel = node => {
    if (!node || node.nodeType !== 1) return '';
    if (node.id) return '#' + cssEsc(node.id);
    for (const k of ['data-testid','data-test','name']) { const r = attrSel(node,k); if(r) return r; }
    const parts = []; let cur = node;
    while (cur && cur.nodeType===1 && parts.length<7) {
      if (cur.id) { parts.unshift('#'+cssEsc(cur.id)); break; }
      let p = cur.localName.toLowerCase();
      const nm = cur.getAttribute && cur.getAttribute('name');
      if (nm) p += '[name="'+String(nm).replace(/\\/g,'\\\\').replace(/"/g,'\\"')+'"]';
      const par = cur.parentElement;
      if (par) {
        const sibs = Array.from(par.children).filter(s => s.localName===cur.localName);
        if (sibs.length>1) p += ':nth-of-type('+(sibs.indexOf(cur)+1)+')';
      }
      parts.unshift(p);
      try { if(document.querySelectorAll(parts.join(' > ')).length===1) break; } catch(_){}
      cur = cur.parentElement;
    }
    return parts.join(' > ');
  };

  const pt = e => ({
    x: e.clientX|0, y: e.clientY|0,
    rx: window.innerWidth  ? e.clientX/window.innerWidth  : 0,
    ry: window.innerHeight ? e.clientY/window.innerHeight : 0,
  });

  // Click
  document.addEventListener('click', e => {
    emit('click', { ...pt(e), selector: buildSel(e.target), button: e.button|0,
      ctrlKey: !!e.ctrlKey, shiftKey: !!e.shiftKey, altKey: !!e.altKey, metaKey: !!e.metaKey });
  }, true);

  // Input / change (40ms debounce on input)
  const emitInput = (type, e) => {
    const t = e.target; if (!t || t.nodeType!==1) return;
    const tag = (t.tagName||'').toLowerCase();
    if (!['input','textarea','select'].includes(tag) && !t.isContentEditable) return;
    emit(type, { selector: buildSel(t), tag, inputType: t.type||'',
      value: t.isContentEditable ? t.innerText : (typeof t.value==='string'?t.value:''),
      checked: typeof t.checked==='boolean' ? !!t.checked : null });
  };
  let _it = null;
  document.addEventListener('input',  e => { clearTimeout(_it); _it = setTimeout(()=>emitInput('input',e), 40); }, true);
  document.addEventListener('change', e => emitInput('change', e), true);

  // Keydown (meaningful keys only: Enter/Tab/Esc/modifier combos)
  document.addEventListener('keydown', e => {
    if (!['Enter','Tab','Escape'].includes(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey) return;
    emit('keydown', { key: e.key, code: e.code, ctrlKey: !!e.ctrlKey, shiftKey: !!e.shiftKey,
      altKey: !!e.altKey, metaKey: !!e.metaKey, selector: buildSel(document.activeElement) });
  }, true);

  // Wheel (rAF-throttled)
  let _wf = null;
  document.addEventListener('wheel', e => {
    if (_wf) return;
    _wf = requestAnimationFrame(() => { _wf = null;
      emit('wheel', { deltaX: e.deltaX, deltaY: e.deltaY, x: e.clientX|0, y: e.clientY|0,
        rx: window.innerWidth?e.clientX/window.innerWidth:0,
        ry: window.innerHeight?e.clientY/window.innerHeight:0 }); });
  }, { passive: true, capture: true });

  // Scroll position ratio (window-level, rAF-throttled)
  let _sf = null;
  window.addEventListener('scroll', () => {
    if (_sf) return;
    _sf = requestAnimationFrame(() => { _sf = null;
      const maxY = Math.max(document.documentElement.scrollHeight,document.body.scrollHeight)-window.innerHeight;
      const maxX = Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) -window.innerWidth;
      emit('scroll', { ratioY: maxY>0?window.scrollY/maxY:0, ratioX: maxX>0?window.scrollX/maxX:0 }); });
  }, { passive: true, capture: true });

  // Mouse move (rAF-throttled, only dispatched when syncMouseMove option is on)
  let _mf = null, _mp = null;
  document.addEventListener('mousemove', e => {
    _mp = pt(e);
    if (_mf) return;
    _mf = requestAnimationFrame(() => { _mf = null; if (_mp) emit('mouse_move', _mp); });
  }, { passive: true, capture: true });

  return 'installed';
})()
`;

// ─── JS expressions applied to follower pages ─────────────────────────────────

function buildInputExpr(payload: Record<string, unknown>): string {
  const d = JSON.stringify(payload);
  return `(()=>{
    const p=${d};
    let t=null; if(p.selector){try{t=document.querySelector(p.selector);}catch(_){}}
    if(!t)return false;
    t.focus?.();
    if(p.tag==='select'){t.value=p.value??'';t.dispatchEvent(new Event('change',{bubbles:true}));return true;}
    if(p.inputType==='checkbox'||p.inputType==='radio'){
      t.checked=!!p.checked;
      t.dispatchEvent(new Event('input',{bubbles:true}));
      t.dispatchEvent(new Event('change',{bubbles:true}));
      return true;
    }
    if(t.isContentEditable){t.innerText=p.value??'';}
    else if('value' in t){t.value=p.value??'';}
    else return false;
    try{t.dispatchEvent(new InputEvent('input',{bubbles:true,data:String(p.value??''),inputType:'insertReplacementText'}));}
    catch(_){t.dispatchEvent(new Event('input',{bubbles:true}));}
    t.dispatchEvent(new Event('change',{bubbles:true}));
    return true;
  })()`;
}

function buildScrollExpr(payload: Record<string, unknown>): string {
  const d = JSON.stringify(payload);
  return `(()=>{
    const p=${d};
    const maxY=Math.max(document.documentElement.scrollHeight,document.body.scrollHeight)-window.innerHeight;
    const maxX=Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) -window.innerWidth;
    window.scrollTo({
      top:  Number.isFinite(+p.ratioY)&&maxY>0 ? +p.ratioY*maxY : 0,
      left: Number.isFinite(+p.ratioX)&&maxX>0 ? +p.ratioX*maxX : 0,
      behavior:'auto'
    });
    return true;
  })()`;
}

function buildKeyExpr(payload: Record<string, unknown>): string {
  const d = JSON.stringify(payload);
  return `(()=>{
    const p=${d};
    let t=document.activeElement||document.body;
    if(p.selector){try{t=document.querySelector(p.selector)||t;}catch(_){}}
    t.focus?.();
    const init={key:p.key||'',code:p.code||'',ctrlKey:!!p.ctrlKey,shiftKey:!!p.shiftKey,
      altKey:!!p.altKey,metaKey:!!p.metaKey,bubbles:true,cancelable:true};
    t.dispatchEvent(new KeyboardEvent('keydown',init));
    t.dispatchEvent(new KeyboardEvent('keyup',  init));
    return true;
  })()`;
}

function resolveClickPointExpr(payload: Record<string, unknown>): string {
  const d = JSON.stringify(payload);
  return `(()=>{
    const p=${d};
    const clamp=pt=>({
      x:Math.max(0,Math.min(window.innerWidth -1,Math.round(pt.x))),
      y:Math.max(0,Math.min(window.innerHeight-1,Math.round(pt.y)))
    });
    let t=null;
    if(p.selector){try{t=document.querySelector(p.selector);}catch(_){}}
    if(!t){
      const x=Number.isFinite(+p.x)?+p.x:(+p.rx||0)*window.innerWidth;
      const y=Number.isFinite(+p.y)?+p.y:(+p.ry||0)*window.innerHeight;
      t=document.elementFromPoint(x,y);
    }
    if(!t) return {ok:false};
    t.focus?.();
    const r=t.getBoundingClientRect();
    if(!r||r.width<1||r.height<1){
      const fb=clamp({
        x:Number.isFinite(+p.x)?+p.x:(+p.rx||0)*window.innerWidth,
        y:Number.isFinite(+p.y)?+p.y:(+p.ry||0)*window.innerHeight
      });
      return {ok:true,...fb};
    }
    return {ok:true,...clamp({x:r.left+r.width/2,y:r.top+r.height/2})};
  })()`;
}

// ─── Types ────────────────────────────────────────────────────────────────────

interface SyncEvent {
  type: string;
  payload: Record<string, unknown>;
  href?: string;
  ts?: number;
}

interface EventRecord {
  type: string;
  at: number;
  payload?: Record<string, unknown>;
}

export interface SyncStatus {
  active: boolean;
  masterSessionId: string | null;
  followerSessionIds: string[];
  options: SyncOptions;
  eventCount: number;
  recentEvents: EventRecord[];
  errors: string[];
}

interface SessionLike {
  getPage: () => Page | null;
  getCdp:  () => PwCDPSession | null;
}

// ─── CdpSynchronizer ─────────────────────────────────────────────────────────

export class CdpSynchronizer {
  private masterSessionId: string | null = null;
  private followerSessionIds: string[] = [];
  private opts: Required<SyncOptions> = { ...DEFAULT_OPTIONS };
  private active = false;
  private eventCount = 0;
  private recentEvents: EventRecord[] = [];
  private errors: string[] = [];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private bindingListener: ((p: any) => void) | null = null;
  private masterCdp: PwCDPSession | null = null;

  // ── Public API ──────────────────────────────────────────────────────────────

  start(masterSessionId: string, followerSessionIds: string[], options: SyncOptions = {}): SyncStatus {
    if (this.active) this.stop();
    if (!masterSessionId) throw new Error("masterSessionId is required");
    if (!followerSessionIds.length) throw new Error("at least one followerSessionId required");

    const master = sessionRegistry.get(masterSessionId);
    if (!master) throw new Error(`Master session "${masterSessionId}" not found in registry`);
    for (const id of followerSessionIds) {
      if (!sessionRegistry.get(id)) throw new Error(`Follower session "${id}" not found in registry`);
    }

    this.masterSessionId    = masterSessionId;
    this.followerSessionIds = [...followerSessionIds];
    this.opts               = { ...DEFAULT_OPTIONS, ...options };
    this.eventCount         = 0;
    this.recentEvents       = [];
    this.errors             = [];
    this.active             = true;

    this._initMaster(master).catch(e => this._addError(String(e)));
    logger.info({ master: masterSessionId, followers: followerSessionIds }, "[sync] started");
    return this.status();
  }

  stop(): SyncStatus {
    if (!this.active) return this.status();
    if (this.masterCdp && this.bindingListener) {
      try { this.masterCdp.off("Runtime.bindingCalled", this.bindingListener); } catch { /* ignore */ }
    }
    this.bindingListener = null;
    this.masterCdp       = null;
    this.active          = false;
    this.masterSessionId = null;
    this.followerSessionIds = [];
    logger.info("[sync] stopped");
    return this.status();
  }

  status(): SyncStatus {
    return {
      active:             this.active,
      masterSessionId:    this.masterSessionId,
      followerSessionIds: this.followerSessionIds,
      options:            this.opts,
      eventCount:         this.eventCount,
      recentEvents:       this.recentEvents.slice(-20),
      errors:             this.errors.slice(-10),
    };
  }

  navigate(url: string, includeMaster = true): { dispatched: number } {
    let count = 0;
    const ids: string[] = includeMaster && this.masterSessionId
      ? [this.masterSessionId, ...this.followerSessionIds]
      : [...this.followerSessionIds];
    for (const id of ids) {
      const page = sessionRegistry.get(id)?.getPage();
      if (!page) continue;
      page.goto(url, { waitUntil: "domcontentloaded", timeout: 60_000 }).catch(() => {});
      count++;
    }
    return { dispatched: count };
  }

  // ── Private ─────────────────────────────────────────────────────────────────

  private async _initMaster(master: SessionLike): Promise<void> {
    const cdp  = master.getCdp();
    const page = master.getPage();
    if (!cdp || !page) throw new Error("master page/cdp not yet available (session still starting?)");

    this.masterCdp = cdp;

    // Register CDP binding: window.__bmSyncBinding becomes callable from page JS
    await cdp.send("Runtime.addBinding", { name: BINDING_NAME });

    // Inject capture script (current document)
    await page.evaluate(MASTER_CAPTURE_SCRIPT).catch(e =>
      logger.warn({ err: String(e) }, "[sync] capture script inject warning (non-fatal)"),
    );

    // Re-inject after every navigation (page JS wiped on load)
    page.on("load", () => {
      if (!this.active) return;
      cdp.send("Runtime.addBinding", { name: BINDING_NAME }).catch(() => {});
      page.evaluate(MASTER_CAPTURE_SCRIPT).catch(() => {});
    });

    // Receive events via CDP binding (primary path, <5ms latency)
    this.bindingListener = (p: { name: string; payload: string }) => {
      if (p.name !== BINDING_NAME) return;
      try { this._dispatchEvent(JSON.parse(p.payload) as SyncEvent); } catch { /* malformed */ }
    };
    cdp.on("Runtime.bindingCalled", this.bindingListener);

    // URL sync via CDP (catches navigations that happen before capture script is injected)
    cdp.on("Page.frameNavigated", (p: { frame: { url: string; parentId?: string } }) => {
      if (!this.active || !this.opts.syncNavigation) return;
      if (p.frame.parentId) return; // sub-frame
      const url = p.frame.url;
      if (!url || url === "about:blank" || url.startsWith("chrome://")) return;
      this._broadcastNavigate(url);
    });
  }

  private _broadcastNavigate(url: string): void {
    for (const id of this.followerSessionIds) {
      sessionRegistry.get(id)?.getPage()
        ?.goto(url, { waitUntil: "domcontentloaded", timeout: 60_000 }).catch(() => {});
    }
    this._record("navigate", { url });
  }

  private _dispatchEvent(event: SyncEvent): void {
    const { type, payload } = event;
    const guard: Partial<Record<string, keyof Required<SyncOptions>>> = {
      navigate:   "syncNavigation",
      click:      "syncClick",
      input:      "syncInput",
      change:     "syncInput",
      wheel:      "syncScroll",
      scroll:     "syncScroll",
      keydown:    "syncKeyboard",
      mouse_move: "syncMouseMove",
    };
    const optKey = guard[type];
    if (optKey && !this.opts[optKey]) return;

    if (type === "navigate") {
      this._broadcastNavigate(String(payload.url ?? ""));
      return;
    }

    this.eventCount++;
    this._record(type, payload);

    for (const id of this.followerSessionIds) {
      const sess = sessionRegistry.get(id);
      if (!sess) continue;
      this._applyToFollower(sess, type, payload).catch(e =>
        this._addError(`follower ${id}: ${String(e)}`),
      );
    }
  }

  private async _applyToFollower(
    sess: SessionLike,
    type: string,
    payload: Record<string, unknown>,
  ): Promise<void> {
    const page = sess.getPage();
    const cdp  = sess.getCdp();
    if (!page || !cdp) return;

    switch (type) {
      case "click": {
        if (this.opts.clickDelayMs) await sleep(this.opts.clickDelayMs);
        const point = await page.evaluate(resolveClickPointExpr(payload))
          .catch(() => ({ ok: false })) as { ok: boolean; x?: number; y?: number };
        if (!point.ok) break;
        const x = point.x ?? 0, y = point.y ?? 0;
        const btn = payload.button === 1 ? "middle" : payload.button === 2 ? "right" : "left";
        const mods = ((payload.ctrlKey ? 2 : 0) | (payload.shiftKey ? 8 : 0)
          | (payload.altKey ? 1 : 0) | (payload.metaKey ? 4 : 0));
        await cdp.send("Input.dispatchMouseEvent",
          { type: "mousePressed",  x, y, button: btn, buttons: 1, clickCount: 1, modifiers: mods }).catch(() => {});
        await cdp.send("Input.dispatchMouseEvent",
          { type: "mouseReleased", x, y, button: btn, buttons: 0, clickCount: 1, modifiers: mods }).catch(() => {});
        break;
      }
      case "wheel":
        await cdp.send("Input.dispatchMouseEvent", {
          type: "mouseWheel",
          x: Number(payload.x ?? 0), y: Number(payload.y ?? 0),
          deltaX: Number(payload.deltaX ?? 0), deltaY: Number(payload.deltaY ?? 0),
        }).catch(() => {});
        break;
      case "input":
      case "change":
        if (this.opts.inputDelayMs) await sleep(this.opts.inputDelayMs);
        await page.evaluate(buildInputExpr(payload)).catch(() => {});
        break;
      case "scroll":
        await page.evaluate(buildScrollExpr(payload)).catch(() => {});
        break;
      case "keydown":
        await page.evaluate(buildKeyExpr(payload)).catch(() => {});
        break;
      case "mouse_move":
        await cdp.send("Input.dispatchMouseEvent", {
          type: "mouseMoved",
          x: Number(payload.x ?? 0), y: Number(payload.y ?? 0),
        }).catch(() => {});
        break;
    }
  }

  private _record(type: string, payload?: Record<string, unknown>): void {
    this.recentEvents.push({ type, at: Date.now(), payload });
    if (this.recentEvents.length > 200) this.recentEvents.splice(0, 100);
  }

  private _addError(msg: string): void {
    logger.warn({ msg }, "[sync] dispatch error");
    this.errors.push(new Date().toISOString() + " " + msg);
    if (this.errors.length > 50) this.errors.splice(0, 25);
  }
}

function sleep(ms: number): Promise<void> { return new Promise(r => setTimeout(r, ms)); }

export const synchronizer = new CdpSynchronizer();
