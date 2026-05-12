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
import type { Page, CDPSession as PwCDPSession, BrowserContext } from "playwright";

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
  tabCount: number;
  recentEvents: EventRecord[];
  errors: string[];
}

export interface RecordingStatus {
  active:     boolean;
  eventCount: number;
  maxEvents:  number;
}

/** Entry stored for each follower tab page (secondary tab opened via context.on("page")). */
interface FollowerTabEntry {
  page: Page;
  /** CDPSession on the follower tab page — used for Input.dispatch* event replay. */
  cdp:  PwCDPSession | null;
}

interface SessionLike {
  getPage: () => Page | null;
  getCdp:  () => PwCDPSession | null;
  getCtx:  () => BrowserContext | null;
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

  // Browser-UI tab sync (P3)
  /** masterPage → (followerSessionId → FollowerTabEntry) */
  private tabMap = new Map<Page, Map<string, FollowerTabEntry>>();
  /** masterPage → CDPSession on that master tab (for capture-script injection + cleanup) */
  private masterTabCdps = new Map<Page, PwCDPSession>();
  private ctxPageHandler: ((p: Page) => void) | null = null;
  private masterCtx: BrowserContext | null = null;

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
    // Tear down tab-sync: remove ctx listener + close all follower tab pages
    if (this.masterCtx && this.ctxPageHandler) {
      try { this.masterCtx.off("page", this.ctxPageHandler); } catch { /* ignore */ }
    }
    for (const followerMap of this.tabMap.values()) {
      for (const entry of followerMap.values()) {
        if (!entry.page.isClosed()) entry.page.close().catch(() => {});
      }
    }
    this.tabMap.clear();
    // Detach master-tab CDPSessions
    for (const cdp of this.masterTabCdps.values()) {
      cdp.detach().catch(() => {});
    }
    this.masterTabCdps.clear();
    this.ctxPageHandler  = null;
    this.masterCtx       = null;
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
      tabCount:           this.tabMap.size,
      recentEvents:       this.recentEvents.slice(-20),
      errors:             this.errors.slice(-10),
    };
  }

  /**
   * Replay a pre-recorded sequence of CDP events to specified sessions.
   *
   * Each event: { type, payload, delayMs? }
   * Options:
   *   sessionIds   — target session IDs (default: all active followers)
   *   includeMaster — also apply to the master session (default: false)
   *
   * Returns { replayed, errors } where replayed counts individual event×session dispatches.
   */
  async replay(
    events: Array<{ type: string; payload: Record<string, unknown>; delayMs?: number }>,
    opts: { sessionIds?: string[]; includeMaster?: boolean } = {},
  ): Promise<{ replayed: number; errors: string[] }> {
    if (!Array.isArray(events) || events.length === 0) return { replayed: 0, errors: [] };
    const errors: string[] = [];
    let replayed = 0;

    // Build target list
    const targetIds: string[] = opts.sessionIds
      ? [...opts.sessionIds]
      : [...this.followerSessionIds];
    if (opts.includeMaster && this.masterSessionId && !targetIds.includes(this.masterSessionId)) {
      targetIds.unshift(this.masterSessionId);
    }

    for (const event of events) {
      if ((event.delayMs ?? 0) > 0) await sleep(event.delayMs!);

      for (const id of targetIds) {
        const sess = sessionRegistry.get(id);
        if (!sess) { errors.push(`session "${id}" not in registry`); continue; }
        try {
          if (event.type === "navigate") {
            const url = String(event.payload.url ?? "");
            if (url) {
              sess.getPage()?.goto(url, { waitUntil: "domcontentloaded", timeout: 60_000 })
                .catch(() => {});
            }
          } else {
            await this._applyToFollower(sess, event.type, event.payload);
          }
          replayed++;
        } catch (e) {
          errors.push(`${id}/${event.type}: ${String((e as Error).message ?? e)}`);
        }
      }
      this._record(`replay:${event.type}`, event.payload);
    }

    logger.info({ events: events.length, replayed, errors: errors.length }, "[sync] replay done");
    return { replayed, errors };
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

    // Browser-UI tab sync: hook master context for new tabs opened after sync starts.
    // (Pages already open at sync-start time are not retroactively tracked.)
    if (this.opts.syncBrowserUi) {
      const ctx = (master as SessionLike).getCtx?.();
      if (ctx) {
        this.masterCtx = ctx;
        this.ctxPageHandler = (newPage: Page) => { this._onMasterNewPage(newPage); };
        ctx.on("page", this.ctxPageHandler);
        logger.info("[sync] browser-UI tab sync enabled (context hook attached)");
      } else {
        logger.warn("[sync] syncBrowserUi=true but master has no BrowserContext yet");
      }
    }
  }

  // ── Browser-UI Tab Sync methods ────────────────────────────────────────────

  /**
   * Called when master's BrowserContext emits "page" (a new tab/popup was opened).
   * Creates a matching page in every follower context, then mirrors per-tab navigation
   * and close events.
   */
  /**
   * Called when master context emits "page" (a new tab/popup was opened).
   *
   * Actions:
   *  1. Open a matching tab in every follower context.
   *  2. Create a CDPSession on each follower tab (for Input.dispatch* event replay).
   *  3. Create a CDPSession on the master tab, inject the capture script,
   *     and forward click/input/scroll/key/wheel events to the matching follower tabs.
   *  4. Mirror top-frame navigation (framenavigated) to follower tabs.
   *  5. Close follower tabs when master tab closes.
   */
  private _onMasterNewPage(masterPage: Page): void {
    if (!this.active) return;
    const initialUrl = masterPage.url();
    if (initialUrl.startsWith("chrome://") || initialUrl.startsWith("devtools://")) return;

    const followerMap = new Map<string, FollowerTabEntry>();
    this.tabMap.set(masterPage, followerMap);

    // Open a matching page in each follower context + create CDPSession on it
    for (const fId of this.followerSessionIds) {
      const fSess = sessionRegistry.get(fId);
      const fCtx  = fSess?.getCtx?.();
      if (!fCtx) continue;
      fCtx.newPage().then(async (fPage) => {
        // Create CDPSession on follower tab for Input.dispatch* commands
        let fCdp: PwCDPSession | null = null;
        try { fCdp = await fCtx.newCDPSession(fPage); } catch { /* non-fatal */ }
        followerMap.set(fId, { page: fPage, cdp: fCdp });
        // Match viewport
        const vp = masterPage.viewportSize();
        if (vp) await fPage.setViewportSize(vp).catch(() => {});
        // Catch up to current master URL
        const url = masterPage.url();
        if (url && url !== "about:blank") {
          await fPage.goto(url, { waitUntil: "domcontentloaded", timeout: 30_000 }).catch(() => {});
        }
        logger.info({ follower: fId, url }, "[sync] follower tab opened");
      }).catch((e: unknown) =>
        this._addError(`open follower tab ${fId}: ${String((e as Error).message ?? e)}`),
      );
    }

    // ── Set up capture script on master tab (async) ────────────────────────
    masterPage.context().newCDPSession(masterPage).then(async (masterTabCdp) => {
      this.masterTabCdps.set(masterPage, masterTabCdp);

      const injectCapture = async () => {
        await masterTabCdp.send("Runtime.addBinding", { name: BINDING_NAME }).catch(() => {});
        await masterPage.evaluate(MASTER_CAPTURE_SCRIPT).catch(() => {});
      };
      await injectCapture();

      // Re-inject after each navigation (page JS is wiped on load)
      masterPage.on("load", () => {
        if (!this.active) return;
        injectCapture().catch(() => {});
      });

      // Forward captured events to follower TAB pages (not primary follower pages)
      masterTabCdp.on("Runtime.bindingCalled", (p: { name: string; payload: string }) => {
        if (p.name !== BINDING_NAME || !this.active) return;
        try {
          const event = JSON.parse(p.payload) as SyncEvent;
          const optKey = ({
            navigate: "syncNavigation", click: "syncClick", input: "syncInput",
            change: "syncInput", wheel: "syncScroll", scroll: "syncScroll",
            keydown: "syncKeyboard", mouse_move: "syncMouseMove",
          } as Record<string, keyof Required<SyncOptions>>)[event.type];
          if (optKey && !this.opts[optKey]) return;
          for (const entry of followerMap.values()) {
            if (entry.page.isClosed()) continue;
            const sessLike: SessionLike = {
              getPage: () => entry.page,
              getCdp:  () => entry.cdp,
              getCtx:  () => null,
            };
            this._applyToFollower(sessLike, event.type, event.payload).catch(() => {});
          }
          this.eventCount++;
          this._record(event.type, event.payload);
        } catch { /* malformed payload */ }
      });

      logger.info({ url: initialUrl }, "[sync] master tab capture script injected");
    }).catch((e: unknown) =>
      this._addError(`master tab CDP session: ${String((e as Error).message ?? e)}`),
    );

    // Mirror top-frame navigations to corresponding follower tab pages
    masterPage.on("framenavigated", (frame) => {
      if (!this.active) return;
      if (frame !== masterPage.mainFrame()) return;
      const url = frame.url();
      if (!url || url === "about:blank" || url.startsWith("chrome://")) return;
      for (const [fId, entry] of followerMap) {
        if (entry.page.isClosed()) { followerMap.delete(fId); continue; }
        entry.page.goto(url, { waitUntil: "domcontentloaded", timeout: 30_000 }).catch(() => {});
      }
      this._record("tab_navigate", { url });
    });

    // Close follower tabs when this master tab closes
    masterPage.on("close", () => this._onMasterTabClose(masterPage));

    this._record("tab_open", { url: initialUrl || "about:blank" });
    logger.info(
      { followers: this.followerSessionIds.length, url: initialUrl },
      "[sync] new master tab → follower tabs queued",
    );
  }

  /** Called when a master tab (non-primary) is closed. Closes all follower tabs for it. */
  private _onMasterTabClose(masterPage: Page): void {
    const followerMap = this.tabMap.get(masterPage);
    this.tabMap.delete(masterPage);
    // Detach master tab CDPSession
    const masterTabCdp = this.masterTabCdps.get(masterPage);
    if (masterTabCdp) { masterTabCdp.detach().catch(() => {}); this.masterTabCdps.delete(masterPage); }
    if (!followerMap) return;
    let closed = 0;
    for (const entry of followerMap.values()) {
      if (!entry.page.isClosed()) { entry.page.close().catch(() => {}); closed++; }
    }
    followerMap.clear();
    this._record("tab_close", { closedFollowers: closed });
    logger.info({ closedFollowers: closed }, "[sync] master tab closed → follower tabs closed");
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
    const now = Date.now();
    this.recentEvents.push({ type, at: now, payload });
    if (this.recentEvents.length > 200) this.recentEvents.splice(0, 100);
    // Recording buffer (active only when startRecording() called)
    if (this._recActive) {
      if (!this._recFilter.length || this._recFilter.includes(type)) {
        const delayMs = this._recLastTs ? now - this._recLastTs : 0;
        this._recLastTs = now;
        this._recBuf.push({ type, ts: now, delayMs, payload });
        if (this._recBuf.length > this._recMax) this._recBuf.splice(0, Math.ceil(this._recMax * 0.2));
      }
    }
  }

  // ── Public recording API (called by routes/sync.ts) ──────────────────────

  startRecording(opts: { maxEvents?: number; filterTypes?: string[]; clearFirst?: boolean } = {}): RecordingStatus {
    if (opts.clearFirst !== false) this._recBuf = [];
    this._recActive = true;
    this._recMax    = opts.maxEvents ?? 5000;
    this._recFilter = opts.filterTypes ?? [];
    this._recLastTs = Date.now();
    logger.info({ max: this._recMax, filter: this._recFilter }, "[sync] recording started");
    return this._recStatus();
  }

  stopRecording(): RecordingStatus {
    this._recActive = false;
    logger.info({ events: this._recBuf.length }, "[sync] recording stopped");
    return this._recStatus();
  }

  getRecording(opts: { types?: string[]; maxEvents?: number; asReplay?: boolean } = {}): {
    recording: RecordingStatus; events: Array<Record<string, unknown>>;
  } {
    let evts = this._recBuf.slice();
    if (opts.types?.length) evts = evts.filter(e => opts.types!.includes(e.type));
    if (typeof opts.maxEvents === "number") evts = evts.slice(-opts.maxEvents);
    const events: Array<Record<string, unknown>> = opts.asReplay
      ? evts.map(({ ts: _ts, ...rest }) => rest as Record<string, unknown>)
      : evts as unknown as Array<Record<string, unknown>>;
    return { recording: this._recStatus(), events };
  }

  clearRecording(): RecordingStatus {
    this._recBuf = [];
    return this._recStatus();
  }

  private _recStatus(): RecordingStatus {
    return { active: this._recActive, eventCount: this._recBuf.length, maxEvents: this._recMax };
  }

  private _addError(msg: string): void {
    logger.warn({ msg }, "[sync] dispatch error");
    this.errors.push(new Date().toISOString() + " " + msg);
    if (this.errors.length > 50) this.errors.splice(0, 25);
  }
}

function sleep(ms: number): Promise<void> { return new Promise(r => setTimeout(r, ms)); }

export const synchronizer = new CdpSynchronizer();
