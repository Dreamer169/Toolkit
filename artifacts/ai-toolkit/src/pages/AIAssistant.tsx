import { useEffect, useRef, useState, useCallback } from "react";

/* ══════════════════════════════════════════════════════════
   Types
══════════════════════════════════════════════════════════ */
type Role = "user" | "assistant";
interface AgentEv {
  type: string; text?: string;
  cmd?: string; stdout?: string; stderr?: string; code?: number;
  tool?: string; toolName?: string; toolId?: string; ai_text?: string;
  filePath?: string | null;
}
interface Msg {
  id: string; role: Role; content: string;
  events: AgentEv[]; ts: number; streaming?: boolean; error?: string;
}
interface Session { id: string; title: string; created_at: number; updated_at: number; msgCount: number }
interface Metrics { cpu?: number; mem?: { pct: number }; cfPool?: { available: number } }

const BASE = "";
const genId = () => Math.random().toString(36).slice(2) + Date.now().toString(36);
const timeAgo = (ts: number) => {
  const d = Date.now() - ts;
  if (d < 60000) return "刚刚";
  if (d < 3600000) return Math.floor(d / 60000) + "分钟前";
  if (d < 86400000) return Math.floor(d / 3600000) + "小时前";
  return new Date(ts).toLocaleDateString("zh");
};

function renderMd(s: string) {
  return s
    .replace(/```[\w]*\n?([\s\S]*?)```/g, (_m, code: string) =>
      `<pre class="codeblock">${code.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</pre>`)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`\n]+)`/g, "<code class='ic'>$1</code>")
    .replace(/^#{1,3} (.+)$/gm, "<b class='mh'>$1</b>")
    .replace(/^[-•] (.+)$/gm, "<span class='ml'>• $1</span>")
    .replace(/\n/g, "<br/>");
}

/* ══════════════════════════════════════════════════════════
   Tool styles — covers ALL claude tool types
══════════════════════════════════════════════════════════ */
const TOOL_STYLE: Record<string, { icon: string; color: string; bg: string; border: string }> = {
  bash:  { icon: "$_", color: "#fde68a", bg: "#0d1117",  border: "#1f2937" },
  read:  { icon: "📖", color: "#93c5fd", bg: "#0c1830",  border: "#1e3a5f" },
  write: { icon: "✍",  color: "#86efac", bg: "#0a1f10",  border: "#1a3a20" },
  edit:  { icon: "✏",  color: "#c4b5fd", bg: "#130c2a",  border: "#2d1a5f" },
  glob:  { icon: "🔍", color: "#fb923c", bg: "#1a0c00",  border: "#3a2000" },
  grep:  { icon: "🔎", color: "#fb923c", bg: "#1a0c00",  border: "#3a2000" },
  ls:    { icon: "📂", color: "#94a3b8", bg: "#0f172a",  border: "#1e293b" },
  todo:  { icon: "📋", color: "#f9a8d4", bg: "#1a0a1a",  border: "#3a1a3a" },
  web:   { icon: "🌐", color: "#67e8f9", bg: "#051a1a",  border: "#0a3a3a" },
  tool:  { icon: "⚙",  color: "#d1d5db", bg: "#111827",  border: "#374151" },
};

/* ── Inline tool call block ── */
function ToolCallBlock({ ev }: { ev: AgentEv }) {
  const [open, setOpen] = useState(() => (ev.code ?? 0) !== 0);
  const st = TOOL_STYLE[ev.tool ?? "bash"] ?? TOOL_STYLE.tool;

  if (ev.type === "exec_start") return (
    <div style={{ display:"flex", alignItems:"center", gap:6, padding:"3px 8px", background:st.bg, border:`1px solid ${st.border}`, borderRadius:6, margin:"3px 0", fontSize:12, fontFamily:"monospace", opacity:0.9 }}>
      <span style={{ color:"#f59e0b", fontSize:10, animation:"spin 1s linear infinite", display:"inline-block" }}>◌</span>
      <span style={{ color:st.color, marginRight:2 }}>{st.icon}</span>
      <code style={{ color:st.color, flex:1, overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap" }}>{ev.cmd ?? ""}</code>
      {ev.ai_text && <span style={{ color:"#6b7280", fontSize:10, fontFamily:"system-ui", whiteSpace:"nowrap", overflow:"hidden", maxWidth:120, textOverflow:"ellipsis" }}>{ev.ai_text.slice(0,60)}</span>}
    </div>
  );

  if (ev.type === "exec_done") {
    const ok = (ev.code ?? 0) === 0;
    return (
      <div style={{ border:`1px solid ${ok ? st.border : "#7f1d1d"}`, borderRadius:6, overflow:"hidden", margin:"3px 0", fontSize:12, fontFamily:"monospace" }}>
        <div onClick={() => setOpen(o => !o)} style={{ display:"flex", alignItems:"center", gap:6, padding:"3px 8px", background:ok ? st.bg : "#1c0505", cursor:"pointer", userSelect:"none" }}>
          <span style={{ color:ok ? "#10b981" : "#ef4444", fontSize:11 }}>{ok ? "✓" : `✗${ev.code}`}</span>
          <span style={{ color:st.color }}>{st.icon}</span>
          <code style={{ color:st.color, flex:1, overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap" }}>{ev.cmd ?? ""}</code>
          <span style={{ color:"#374151", fontSize:9 }}>{open ? "▲" : "▼"}</span>
        </div>
        {open && (ev.stdout || ev.stderr) && (
          <div style={{ padding:"6px 8px", background:"#000", maxHeight:220, overflow:"auto" }}>
            {ev.stdout && <pre style={{ color:"#86efac", whiteSpace:"pre-wrap", wordBreak:"break-all", margin:0, fontSize:11, lineHeight:1.4 }}>{ev.stdout.slice(0,2000)}</pre>}
            {ev.stderr && <pre style={{ color:"#fca5a5", whiteSpace:"pre-wrap", wordBreak:"break-all", margin:0, fontSize:11, lineHeight:1.4 }}>{ev.stderr.slice(0,600)}</pre>}
          </div>
        )}
      </div>
    );
  }

  if (ev.type === "status") return (
    <div style={{ display:"flex", alignItems:"center", gap:5, fontSize:11, color:"#6b7280", padding:"2px 0", fontFamily:"system-ui" }}>
      <span style={{ animation:"spin 1.5s linear infinite", display:"inline-block" }}>⟳</span> {ev.text}
    </div>
  );

  if (ev.type === "ai_response") return (
    <div style={{ borderLeft:"2px solid #f97316", paddingLeft:8, margin:"3px 0", fontSize:12, color:"#9ca3af", fontStyle:"italic", fontFamily:"system-ui" }}>
      {(ev.text ?? "").slice(0,180)}{(ev.text?.length ?? 0) > 180 ? "…" : ""}
    </div>
  );
  return null;
}

/* ── Message bubble ── */
function MsgBubble({ msg, onCopy }: { msg: Msg; onCopy: (t: string) => void }) {
  if (msg.role === "user") return (
    <div style={{ display:"flex", justifyContent:"flex-end", marginBottom:14 }}>
      <div style={{ maxWidth:"76%", background:"linear-gradient(135deg,#1d4ed8,#2563eb)", color:"#fff", borderRadius:"18px 18px 4px 18px", padding:"10px 14px", fontSize:14, lineHeight:1.65, whiteSpace:"pre-wrap", wordBreak:"break-word" }}>
        {msg.content}
      </div>
    </div>
  );

  const toolEvents = msg.events.filter(e => e.type !== "start" && e.type !== "complete" && e.type !== "error");
  const doneCount = toolEvents.filter(e => e.type === "exec_done").length;

  return (
    <div style={{ display:"flex", justifyContent:"flex-start", marginBottom:14 }}>
      <div style={{ maxWidth:"92%", minWidth:200 }}>
        <div style={{ display:"flex", alignItems:"center", gap:6, marginBottom:4, marginLeft:2 }}>
          <div style={{ width:22, height:22, borderRadius:6, background:"linear-gradient(135deg,#f97316,#db2777)", display:"flex", alignItems:"center", justifyContent:"center", fontSize:9, fontWeight:800, color:"#fff", flexShrink:0 }}>AI</div>
          <span style={{ fontSize:10, color:"#f97316", fontWeight:700 }}>mimo-v2.5-pro</span>
          {doneCount > 0 && <span style={{ fontSize:10, color:"#6b7280" }}>· {doneCount} 次工具调用</span>}
          {msg.streaming && <span style={{ fontSize:11, color:"#60a5fa", animation:"pulse 1s infinite" }}>▌</span>}
        </div>
        <div style={{ background:"#161b22", border:"1px solid #21262d", borderRadius:"4px 18px 18px 18px", padding:"10px 14px" }}>
          {toolEvents.map((ev, i) => <ToolCallBlock key={i} ev={ev} />)}
          {msg.content && (
            <div style={{ fontSize:14, color:"#e2e8f0", lineHeight:1.75, marginTop:toolEvents.length > 0 ? 8 : 0, wordBreak:"break-word" }}
              dangerouslySetInnerHTML={{ __html: renderMd(msg.content) }} />
          )}
          {msg.streaming && !msg.content && toolEvents.length === 0 && (
            <div style={{ display:"flex", gap:4, padding:"4px 0" }}>
              {[0,1,2].map(i => <span key={i} style={{ width:7, height:7, borderRadius:"50%", background:"#f97316", animation:`bounce 1.1s ${i * 0.18}s ease-in-out infinite` }} />)}
            </div>
          )}
          {msg.error && <div style={{ marginTop:8, fontSize:12, color:"#ef4444" }}>⚠ {msg.error}</div>}
        </div>
        <div style={{ display:"flex", gap:8, marginTop:3, marginLeft:4, alignItems:"center" }}>
          <span style={{ fontSize:10, color:"#374151" }}>{timeAgo(msg.ts)}</span>
          {msg.content && <button onClick={() => onCopy(msg.content)} style={{ fontSize:10, color:"#374151", background:"none", border:"none", cursor:"pointer", padding:0 }}>复制</button>}
        </div>
      </div>
    </div>
  );
}

/* ── Quick commands ── */
const QUICK = [
  { label:"PM2 状态", cmd:"pm2 list" },
  { label:"服务器资源", cmd:"df -h / && free -h && uptime" },
  { label:"api-server 日志", cmd:"pm2 logs api-server --lines 30 --nostream" },
  { label:"重建 api-server", cmd:"重建并重启 api-server（pnpm build + pm2 restart）" },
  { label:"注册3个Outlook", cmd:"注册3个Outlook账号" },
  { label:"CF IP池状态", cmd:"curl -s http://localhost:8081/api/tools/cf-pool/status | python3 -m json.tool 2>/dev/null || curl -s http://localhost:8081/api/tools/cf-pool/status" },
  { label:"Git 状态", cmd:"cd /root/Toolkit && git status && git log --oneline -5" },
  { label:"Git 提交推送", cmd:"cd /root/Toolkit && git add -A && git status && git diff --stat HEAD" },
];

/* ══════════════════════════════════════════════════════════
   Main Component
══════════════════════════════════════════════════════════ */
export default function AIAssistant() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState(genId);
  const [sessionTitle, setSessionTitle] = useState("新对话");
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sideTab, setSideTab] = useState<"sessions"|"quick">("sessions");
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [copied, setCopied] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const msgsRef = useRef<Msg[]>([]);
  const sessionIdRef = useRef(sessionId);
  const sessionTitleRef = useRef(sessionTitle);
  msgsRef.current = msgs;
  sessionIdRef.current = sessionId;
  sessionTitleRef.current = sessionTitle;

  useEffect(() => { fetchSessions(); fetchMetrics(); }, []);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior:"smooth" }); }, [msgs]);

  const fetchSessions = async () => {
    try { const r = await fetch(`${BASE}/api/claude-code/sessions`); setSessions(await r.json()); } catch {}
  };
  const fetchMetrics = async () => {
    try { const r = await fetch(`${BASE}/api/claude-code/server-metrics`); setMetrics(await r.json()); } catch {}
    setTimeout(fetchMetrics, 30000);
  };

  const saveSession = useCallback(async (messages: Msg[], title: string, sid: string) => {
    try {
      await fetch(`${BASE}/api/claude-code/sessions`, {
        method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({ id:sid, title, messages:messages.map(m=>({role:m.role,content:m.content,events:m.events,ts:m.ts})) })
      });
      fetchSessions();
    } catch {}
  }, []);

  const copyText = (t: string) => {
    navigator.clipboard.writeText(t).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); });
  };

  const send = async (text = input) => {
    const msg = text.trim();
    if (!msg || loading) return;
    setInput("");
    setLoading(true);
    if (inputRef.current) { inputRef.current.style.height = "42px"; }

    const userMsg: Msg = { id:genId(), role:"user", content:msg, events:[], ts:Date.now() };
    const aiId = genId();
    const aiMsg: Msg = { id:aiId, role:"assistant", content:"", events:[], ts:Date.now(), streaming:true };
    setMsgs(prev => [...prev, userMsg, aiMsg]);

    const history = msgsRef.current.map(m => ({ role:m.role, content:m.content, events:m.events }));
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    let finalContent = "";
    const liveEvents: AgentEv[] = [];
    const toolIdToIdx: Record<string, number> = {};

    try {
      const resp = await fetch(`${BASE}/api/claude-code/converse`, {
        method:"POST", signal:ctrl.signal,
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({ sessionId:sessionIdRef.current, history, message:msg })
      });

      const reader = resp.body!.getReader();
      const dec = new TextDecoder();
      let buf = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream:true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          try {
            const ev: AgentEv = JSON.parse(line.slice(5).trim());

            if (ev.type === "complete") {
              finalContent = ev.text ?? finalContent;
            } else if (ev.type === "ai_response") {
              finalContent = finalContent ? finalContent + "\n\n" + (ev.text ?? "") : (ev.text ?? "");
              liveEvents.push(ev);
            } else if (ev.type === "exec_start") {
              toolIdToIdx[ev.toolId ?? ""] = liveEvents.length;
              liveEvents.push({ ...ev });
            } else if (ev.type === "exec_done") {
              const idx = toolIdToIdx[ev.toolId ?? ""];
              if (idx !== undefined) {
                const start = liveEvents[idx];
                liveEvents[idx] = { ...ev, cmd:start.cmd, tool:start.tool, toolName:start.toolName };
              } else {
                liveEvents.push(ev);
              }
            } else if (ev.type !== "start" && ev.type !== "error") {
              liveEvents.push(ev);
            }
            if (ev.type === "error") finalContent = `⚠ ${ev.text}`;

            setMsgs(prev => {
              const last = { ...prev[prev.length-1], content:finalContent, events:[...liveEvents], streaming:true };
              return [...prev.slice(0,-1), last];
            });
          } catch {}
        }
      }
    } catch (e: unknown) {
      if ((e as Error).name !== "AbortError") finalContent = `连接错误: ${String(e)}`;
    }

    const doneAiMsg: Msg = { id:aiId, role:"assistant", content:finalContent, events:[...liveEvents], streaming:false, ts:Date.now() };
    setMsgs(prev => {
      const updated = [...prev.slice(0,-1), doneAiMsg];
      const isFirst = prev.filter(m => m.role==="user").length <= 1;
      const title = isFirst ? msg.slice(0,28) : sessionTitleRef.current;
      if (isFirst) { setSessionTitle(title); sessionTitleRef.current = title; }
      saveSession(updated, title, sessionIdRef.current);
      return updated;
    });
    setLoading(false);
    abortRef.current = null;
    setTimeout(() => inputRef.current?.focus(), 50);
  };

  const stopGeneration = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setLoading(false);
    setMsgs(prev => prev.length === 0 ? prev : [...prev.slice(0,-1), { ...prev[prev.length-1], streaming:false }]);
  };

  const loadSession = async (id: string) => {
    try {
      const r = await fetch(`${BASE}/api/claude-code/sessions/${id}`);
      const d = await r.json();
      setMsgs((d.messages ?? []).map((m: { role:Role; content:string; events?:AgentEv[]; ts?:number }) => ({
        id:genId(), role:m.role, content:m.content, events:m.events ?? [], ts:m.ts ?? Date.now()
      })));
      setSessionId(id); sessionIdRef.current = id;
      setSessionTitle(d.title ?? "未命名"); sessionTitleRef.current = d.title ?? "未命名";
    } catch {}
  };

  const newSession = () => {
    const id = genId();
    setMsgs([]); setSessionId(id); sessionIdRef.current = id;
    setSessionTitle("新对话"); sessionTitleRef.current = "新对话";
    setInput("");
    setTimeout(() => inputRef.current?.focus(), 50);
  };

  const delSession = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    await fetch(`${BASE}/api/claude-code/sessions/${id}`, { method:"DELETE" });
    setSessions(p => p.filter(s => s.id !== id));
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); }
  };
  const onInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    e.target.style.height = "auto";
    e.target.style.height = Math.min(e.target.scrollHeight, 130) + "px";
  };

  return (
    <div style={{ display:"flex", height:"calc(100vh - 60px)", gap:10, fontFamily:"system-ui,-apple-system,sans-serif", color:"#e2e8f0", minHeight:0 }}>

      {/* ══ Main chat ══ */}
      <div style={{ flex:1, display:"flex", flexDirection:"column", background:"#0d1117", border:"1px solid #21262d", borderRadius:16, overflow:"hidden", minWidth:0 }}>
        {/* Header */}
        <div style={{ padding:"10px 16px", borderBottom:"1px solid #21262d", display:"flex", alignItems:"center", gap:10, flexShrink:0 }}>
          <div style={{ width:30, height:30, borderRadius:9, background:"linear-gradient(135deg,#f97316,#db2777)", display:"flex", alignItems:"center", justifyContent:"center", fontSize:11, fontWeight:800, color:"#fff" }}>AI</div>
          <div style={{ flex:1, minWidth:0 }}>
            <div style={{ fontSize:13, fontWeight:700, color:"#fff", overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap" }}>{sessionTitle}</div>
            <div style={{ fontSize:10, color:"#6b7280" }}>mimo-v2.5-pro · root@VPS · 全工具解锁 · 无任何限制</div>
          </div>
          {metrics && (
            <div style={{ display:"flex", gap:8, fontSize:11, color:"#6b7280", flexShrink:0 }}>
              <span>⚡{metrics.cpu?.toFixed(0)}%</span>
              <span>🧠{metrics.mem?.pct}%</span>
              <span style={{ color:"#10b981" }}>🌐{metrics.cfPool?.available}</span>
            </div>
          )}
          {copied && <span style={{ fontSize:11, color:"#10b981", flexShrink:0 }}>✓ 已复制</span>}
          <button onClick={newSession} style={{ padding:"4px 10px", background:"#21262d", border:"1px solid #30363d", borderRadius:8, color:"#9ca3af", cursor:"pointer", fontSize:12, flexShrink:0 }}>+ 新对话</button>
        </div>

        {/* Messages */}
        <div style={{ flex:1, overflowY:"auto", padding:"16px 16px 8px" }}>
          {msgs.length === 0 && (
            <div style={{ textAlign:"center", paddingTop:50, color:"#374151" }}>
              <div style={{ fontSize:44, marginBottom:12 }}>🤖</div>
              <div style={{ fontSize:15, color:"#9ca3af", fontWeight:700, marginBottom:6 }}>无限制 AI 助手 · 全工具解锁</div>
              <div style={{ fontSize:12, color:"#4b5563", lineHeight:1.9, marginBottom:20 }}>
                <span style={{ color:"#fde68a" }}>$_</span> Bash &nbsp;
                <span style={{ color:"#93c5fd" }}>📖</span> Read &nbsp;
                <span style={{ color:"#86efac" }}>✍</span> Write &nbsp;
                <span style={{ color:"#c4b5fd" }}>✏</span> Edit &nbsp;
                <span style={{ color:"#fb923c" }}>🔍</span> Glob &nbsp;
                <span style={{ color:"#fb923c" }}>🔎</span> Grep &nbsp;
                <span style={{ color:"#94a3b8" }}>📂</span> LS<br/>
                <span style={{ color:"#f97316" }}>对话即执行 · 工具调用内联显示 · 多轮记忆 · root 全权限</span>
              </div>
              <div style={{ display:"flex", flexWrap:"wrap", gap:8, justifyContent:"center" }}>
                {QUICK.map(q => (
                  <button key={q.label} onClick={() => void send(q.cmd)}
                    style={{ padding:"6px 14px", background:"#161b22", border:"1px solid #30363d", borderRadius:20, color:"#9ca3af", cursor:"pointer", fontSize:12, transition:"all .2s" }}
                    onMouseEnter={e => { e.currentTarget.style.borderColor="#f97316"; e.currentTarget.style.color="#f97316"; }}
                    onMouseLeave={e => { e.currentTarget.style.borderColor="#30363d"; e.currentTarget.style.color="#9ca3af"; }}>
                    {q.label}
                  </button>
                ))}
              </div>
            </div>
          )}
          {msgs.map(msg => <MsgBubble key={msg.id} msg={msg} onCopy={copyText} />)}
          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div style={{ padding:"10px 16px 12px", borderTop:"1px solid #21262d", flexShrink:0 }}>
          <div style={{ display:"flex", gap:8, alignItems:"flex-end" }}>
            <textarea ref={inputRef} value={input} onChange={onInput} onKeyDown={onKey}
              disabled={loading} rows={1} placeholder="输入任何指令…  Enter 发送  Shift+Enter 换行"
              style={{ flex:1, resize:"none", background:"#161b22", border:"1px solid #30363d", borderRadius:12, padding:"10px 14px", fontSize:14, color:"#fff", fontFamily:"inherit", outline:"none", overflowY:"hidden", lineHeight:1.5, transition:"border-color .2s", opacity:loading ? 0.6 : 1, minHeight:42 }}
              onFocus={e => { e.target.style.borderColor="#f97316"; }}
              onBlur={e => { e.target.style.borderColor="#30363d"; }} />
            {loading
              ? <button onClick={stopGeneration} style={{ padding:"10px 14px", background:"#7f1d1d", border:"none", borderRadius:12, color:"#fca5a5", cursor:"pointer", fontSize:13, fontWeight:700, whiteSpace:"nowrap", flexShrink:0 }}>⬛ 停止</button>
              : <button onClick={() => void send()} disabled={!input.trim()}
                  style={{ padding:"10px 16px", background:input.trim() ? "#f97316" : "#1f2937", border:"none", borderRadius:12, color:input.trim() ? "#fff" : "#4b5563", cursor:input.trim() ? "pointer" : "default", fontSize:13, fontWeight:700, whiteSpace:"nowrap", flexShrink:0, transition:"all .2s" }}>
                  发送 ↵
                </button>
            }
          </div>
          <div style={{ fontSize:10, color:"#1f2937", marginTop:5, textAlign:"center" }}>
            Bash · Read · Write · Edit · Glob · Grep · LS · Todo · WebSearch · --dangerously-skip-permissions
          </div>
        </div>
      </div>

      {/* ══ Sidebar ══ */}
      <div style={{ width:236, display:"flex", flexDirection:"column", gap:8, flexShrink:0 }}>
        {/* Server card */}
        <div style={{ background:"#161b22", border:"1px solid #21262d", borderRadius:12, padding:"10px 12px", flexShrink:0 }}>
          <div style={{ fontSize:10, color:"#6b7280", fontWeight:700, marginBottom:8, textTransform:"uppercase", letterSpacing:1 }}>VPS 状态</div>
          <div style={{ display:"flex", flexDirection:"column", gap:4, fontSize:11 }}>
            <div style={{ display:"flex", justifyContent:"space-between" }}><span style={{ color:"#10b981" }}>● 已解锁全部工具</span><span style={{ color:"#6b7280" }}>dangerously</span></div>
            <div style={{ display:"flex", justifyContent:"space-between" }}><span style={{ color:"#60a5fa" }}>● GH_TOKEN</span><span style={{ color:"#6b7280" }}>已注入</span></div>
            {metrics && <>
              <div style={{ display:"flex", justifyContent:"space-between" }}><span style={{ color:"#94a3b8" }}>CPU / MEM</span><span style={{ color:"#d1d5db" }}>{metrics.cpu?.toFixed(1)}% / {metrics.mem?.pct}%</span></div>
              <div style={{ display:"flex", justifyContent:"space-between" }}><span style={{ color:"#94a3b8" }}>CF IP池</span><span style={{ color:"#10b981" }}>{metrics.cfPool?.available} 可用</span></div>
            </>}
          </div>
          <div style={{ marginTop:8, borderTop:"1px solid #21262d", paddingTop:8 }}>
            <div style={{ fontSize:10, color:"#374151", marginBottom:4 }}>工具图例</div>
            <div style={{ display:"flex", flexWrap:"wrap", gap:3 }}>
              {Object.entries(TOOL_STYLE).filter(([k]) => k !== "tool").map(([k, v]) => (
                <span key={k} style={{ fontSize:10, color:v.color, background:v.bg, border:`1px solid ${v.border}`, borderRadius:4, padding:"1px 5px" }}>{v.icon} {k}</span>
              ))}
            </div>
          </div>
        </div>

        {/* Tabs panel */}
        <div style={{ background:"#161b22", border:"1px solid #21262d", borderRadius:12, overflow:"hidden", flex:1, display:"flex", flexDirection:"column", minHeight:0 }}>
          <div style={{ display:"flex", borderBottom:"1px solid #21262d", flexShrink:0 }}>
            {(["sessions","quick"] as const).map(t => (
              <button key={t} onClick={() => setSideTab(t)}
                style={{ flex:1, padding:"8px 4px", fontSize:11, fontWeight:700, background:"transparent", border:"none", color:sideTab===t ? "#fff" : "#6b7280", borderBottom:sideTab===t ? "2px solid #f97316" : "2px solid transparent", cursor:"pointer" }}>
                {t === "sessions" ? "💾 会话" : "⚡ 快捷"}
              </button>
            ))}
          </div>

          {sideTab === "sessions" && (
            <div style={{ flex:1, overflow:"hidden", display:"flex", flexDirection:"column", padding:8, gap:6 }}>
              <button onClick={newSession} style={{ width:"100%", padding:"7px", background:"linear-gradient(135deg,#f97316,#db2777)", border:"none", borderRadius:8, color:"#fff", cursor:"pointer", fontSize:12, fontWeight:700 }}>+ 新对话</button>
              <div style={{ flex:1, overflowY:"auto", display:"flex", flexDirection:"column", gap:4 }}>
                {sessions.length === 0 && <div style={{ fontSize:11, color:"#374151", textAlign:"center", marginTop:16 }}>暂无历史对话</div>}
                {sessions.map(s => (
                  <div key={s.id} onClick={() => loadSession(s.id)}
                    style={{ padding:"8px 10px", borderRadius:8, cursor:"pointer", border:`1px solid ${s.id === sessionId ? "#f97316" : "#30363d"}`, background:s.id === sessionId ? "rgba(249,115,22,0.08)" : "transparent", transition:"all .15s", position:"relative" }}
                    onMouseEnter={e => { if (s.id !== sessionId) e.currentTarget.style.borderColor="#6b7280"; }}
                    onMouseLeave={e => { if (s.id !== sessionId) e.currentTarget.style.borderColor="#30363d"; }}>
                    <div style={{ fontSize:11, fontWeight:600, color:"#e2e8f0", overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap", paddingRight:18 }}>{s.title}</div>
                    <div style={{ fontSize:10, color:"#6b7280", marginTop:2 }}>{s.msgCount}条 · {timeAgo(s.updated_at)}</div>
                    <button onClick={e => void delSession(s.id, e)}
                      style={{ position:"absolute", top:6, right:6, background:"none", border:"none", color:"#4b5563", cursor:"pointer", fontSize:12, padding:2 }}
                      onMouseEnter={e => { e.currentTarget.style.color="#ef4444"; }}
                      onMouseLeave={e => { e.currentTarget.style.color="#4b5563"; }}>✕</button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {sideTab === "quick" && (
            <div style={{ padding:8, overflowY:"auto", flex:1, display:"flex", flexDirection:"column", gap:5 }}>
              <div style={{ fontSize:10, color:"#374151", marginBottom:4 }}>点击即发 · 结果实时内联显示</div>
              {QUICK.map(q => (
                <button key={q.label} onClick={() => void send(q.cmd)} disabled={loading}
                  style={{ textAlign:"left", padding:"8px 10px", background:"#0d1117", border:"1px solid #30363d", borderRadius:8, color:"#e2e8f0", cursor:loading ? "not-allowed" : "pointer", fontSize:11, transition:"all .15s", opacity:loading ? 0.5 : 1 }}
                  onMouseEnter={e => { if (!loading) e.currentTarget.style.borderColor="#f97316"; }}
                  onMouseLeave={e => { e.currentTarget.style.borderColor="#30363d"; }}>
                  <div style={{ fontWeight:700, color:"#f97316" }}>{q.label}</div>
                  <div style={{ fontSize:10, color:"#4b5563", marginTop:2, overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap" }}>{q.cmd.slice(0,42)}</div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      <style>{`
        .ic{background:#1e2a3a;color:#79c0ff;padding:1px 5px;border-radius:4px;font-family:monospace;font-size:.85em}
        .mh{display:block;font-weight:700;color:#e2e8f0;margin:6px 0 3px}
        .ml{display:block;margin:2px 0 2px 8px}
        .codeblock{background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:10px 12px;margin:6px 0;overflow-x:auto;font-size:12px;line-height:1.5;color:#e2e8f0;white-space:pre-wrap;word-break:break-all}
        @keyframes bounce{0%,60%,100%{transform:translateY(0)}30%{transform:translateY(-7px)}}
        @keyframes spin{to{transform:rotate(360deg)}}
        @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
      `}</style>
    </div>
  );
}
