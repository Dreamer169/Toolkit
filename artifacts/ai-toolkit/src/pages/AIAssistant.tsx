import { useEffect, useRef, useState, useCallback } from "react";

type Role = "user" | "assistant";
interface AgentEv {
  type: string; text?: string;
  cmd?: string; stdout?: string; stderr?: string; code?: number;
  tool?: string; toolName?: string; toolId?: string; ai_text?: string;
}
interface Msg {
  id: string; role: Role; content: string; events: AgentEv[];
  ts: number; streaming?: boolean; model?: string;
  images?: { b64: string; mime: string; name: string }[];
  thinking?: string;
}
interface Session { id: string; title: string; created_at: number; updated_at: number; msgCount: number }
interface Metrics { cpu?: number; mem?: { pct: number }; cfPool?: { available: number } }
interface Memory {
  user_preferences: Record<string, string>;
  learned_context: Record<string, string>;
  important_notes: string[];
  skill_summary: string;
  last_updated: number;
}
interface PendingImage { b64: string; mime: string; name: string }

const BASE = "";
const genId = () => Math.random().toString(36).slice(2) + Date.now().toString(36);
const timeAgo = (ts: number) => {
  const d = Date.now() - ts;
  if (d < 60000) return "刚刚";
  if (d < 3600000) return Math.floor(d / 60000) + "分钟前";
  if (d < 86400000) return Math.floor(d / 3600000) + "小时前";
  return new Date(ts).toLocaleDateString("zh");
};

const MODELS = [
  { id: "apex",                  label: "APEX原生",        color: "#dc2626", desc: "直连模型·并行工具·居深思考·无限制" },
  { id: "mimo",                  label: "mimo-v2.5-pro",     color: "#f97316", desc: "默认·工具全开" },
  { id: "gpt-4.1",               label: "GPT-4.1",           color: "#10b981", desc: "sub2api·最强" },
  { id: "gpt-4o",                label: "GPT-4o",            color: "#10b981", desc: "sub2api·视觉" },
  { id: "o3",                    label: "o3",                color: "#ec4899", desc: "sub2api·深度推理" },
  { id: "claude-opus-4-5",       label: "Claude Opus",       color: "#7c3aed", desc: "sub2api" },
  { id: "claude-sonnet-4-5",     label: "Claude Sonnet",     color: "#8b5cf6", desc: "sub2api" },
  { id: "gemini-2.5-pro",        label: "Gemini 2.5 Pro",    color: "#3b82f6", desc: "sub2api·百万ctx" },
  { id: "gemini-2.0-flash",      label: "Gemini Flash",      color: "#60a5fa", desc: "sub2api·极快" },
];

const getModel = (id: string) => MODELS.find(m => m.id === id) ?? MODELS[0];

function renderMd(s: string) {
  return s
    .replace(/```[\w]*\n?([\s\S]*?)```/g, (_m, c: string) =>
      `<pre class="cb">${c.replace(/</g,"&lt;").replace(/>/g,"&gt;")}</pre>`)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`\n]+)`/g, "<code class='ic'>$1</code>")
    .replace(/^#{1,3} (.+)$/gm, "<b class='mh'>$1</b>")
    .replace(/^[-•] (.+)$/gm, "<span class='ml'>• $1</span>")
    .replace(/\n/g, "<br/>");
}

const TOOL_STYLE: Record<string, { icon: string; color: string; bg: string; border: string }> = {
  bash:  { icon: "$_", color: "#fde68a", bg: "#0d1117", border: "#2d2a1a" },
  read:  { icon: "📖", color: "#93c5fd", bg: "#0c1830", border: "#1e3a5f" },
  write: { icon: "✍",  color: "#86efac", bg: "#0a1f10", border: "#1a3a20" },
  edit:  { icon: "✏",  color: "#c4b5fd", bg: "#130c2a", border: "#2d1a5f" },
  glob:  { icon: "🔍", color: "#fb923c", bg: "#1a0c00", border: "#3a2000" },
  grep:  { icon: "🔎", color: "#fb923c", bg: "#1a0c00", border: "#3a2000" },
  ls:    { icon: "📂", color: "#94a3b8", bg: "#0f172a", border: "#1e293b" },
  todo:  { icon: "📋", color: "#f9a8d4", bg: "#1a0a1a", border: "#3a1a3a" },
  web:   { icon: "🌐", color: "#67e8f9", bg: "#051a1a", border: "#0a3a3a" },
  tool:  { icon: "⚙",  color: "#d1d5db", bg: "#111827", border: "#374151" },
};

function ToolCallBlock({ ev }: { ev: AgentEv }) {
  const [open, setOpen] = useState(() => (ev.code ?? 0) !== 0);
  const st = TOOL_STYLE[ev.tool ?? "bash"] ?? TOOL_STYLE.tool;

  if (ev.type === "exec_start") return (
    <div style={{ display:"flex", alignItems:"center", gap:6, padding:"3px 8px", background:st.bg, border:`1px solid ${st.border}`, borderRadius:6, margin:"2px 0", fontSize:12, fontFamily:"monospace" }}>
      <span style={{ color:"#f59e0b", fontSize:10, animation:"spin 1s linear infinite", display:"inline-block" }}>◌</span>
      <span style={{ color:st.color }}>{st.icon}</span>
      <code style={{ color:st.color, flex:1, overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap" }}>{ev.cmd ?? ""}</code>
    </div>
  );
  if (ev.type === "exec_done") {
    const ok = (ev.code ?? 0) === 0;
    return (
      <div style={{ border:`1px solid ${ok ? st.border : "#7f1d1d"}`, borderRadius:6, overflow:"hidden", margin:"2px 0", fontSize:12, fontFamily:"monospace" }}>
        <div onClick={() => setOpen(o => !o)} style={{ display:"flex", alignItems:"center", gap:6, padding:"3px 8px", background:ok ? st.bg : "#1c0505", cursor:"pointer" }}>
          <span style={{ color:ok ? "#10b981" : "#ef4444", fontSize:11 }}>{ok ? "✓" : `✗${ev.code}`}</span>
          <span style={{ color:st.color }}>{st.icon}</span>
          <code style={{ color:st.color, flex:1, overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap" }}>{ev.cmd ?? ""}</code>
          <span style={{ color:"#374151", fontSize:9 }}>{open ? "▲" : "▼"}</span>
        </div>
        {open && (ev.stdout || ev.stderr) && (
          <div style={{ padding:"6px 8px", background:"#000", maxHeight:200, overflow:"auto" }}>
            {ev.stdout && <pre style={{ color:"#86efac", whiteSpace:"pre-wrap", wordBreak:"break-all", margin:0, fontSize:11, lineHeight:1.4 }}>{ev.stdout.slice(0,2000)}</pre>}
            {ev.stderr && <pre style={{ color:"#fca5a5", whiteSpace:"pre-wrap", wordBreak:"break-all", margin:0, fontSize:11, lineHeight:1.4 }}>{ev.stderr.slice(0,400)}</pre>}
          </div>
        )}
      </div>
    );
  }
  if (ev.type === "status") return (
    <div style={{ display:"flex", alignItems:"center", gap:5, fontSize:11, color:"#6b7280", padding:"1px 0" }}>
      <span style={{ animation:"spin 1.5s linear infinite", display:"inline-block" }}>⟳</span> {ev.text}
    </div>
  );
  if (ev.type === "ai_response") return (
    <div style={{ borderLeft:"2px solid #f97316", paddingLeft:8, margin:"2px 0", fontSize:12, color:"#9ca3af", fontStyle:"italic" }}>
      {(ev.text ?? "").slice(0,160)}{(ev.text?.length ?? 0) > 160 ? "…" : ""}
    </div>
  );
  return null;
}

/* ── Thinking block (collapsible) ── */
function ThinkingBlock({ thinking }: { thinking: string }) {
  const [open, setOpen] = useState(false);
  const lines = thinking.split("\n").length;
  return (
    <div style={{ border:"1px solid #3b1d6e", borderRadius:8, overflow:"hidden", marginBottom:8 }}>
      <div onClick={() => setOpen(o => !o)}
        style={{ display:"flex", alignItems:"center", gap:6, padding:"6px 10px", background:"#1a0a2e", cursor:"pointer" }}>
        <span style={{ fontSize:12 }}>🧠</span>
        <span style={{ fontSize:11, color:"#c084fc", fontWeight:700 }}>思维链</span>
        <span style={{ fontSize:10, color:"#7c3aed" }}>{lines}行</span>
        <span style={{ fontSize:9, color:"#4b5563", marginLeft:"auto" }}>{open ? "收起 ▲" : "展开 ▼"}</span>
      </div>
      {open && (
        <div style={{ padding:"8px 10px", background:"#0d0020", maxHeight:320, overflow:"auto" }}>
          <pre style={{ color:"#c084fc", fontSize:11, lineHeight:1.6, whiteSpace:"pre-wrap", wordBreak:"break-word", margin:0, fontFamily:"monospace" }}>
            {thinking}
          </pre>
        </div>
      )}
    </div>
  );
}

function MsgBubble({ msg, onCopy }: { msg: Msg; onCopy: (t: string) => void }) {
  if (msg.role === "user") return (
    <div style={{ display:"flex", justifyContent:"flex-end", marginBottom:12 }}>
      <div style={{ maxWidth:"76%" }}>
        {msg.images && msg.images.length > 0 && (
          <div style={{ display:"flex", gap:4, flexWrap:"wrap", marginBottom:4, justifyContent:"flex-end" }}>
            {msg.images.map((img, i) => (
              <img key={i} src={`data:${img.mime};base64,${img.b64}`} alt={img.name}
                style={{ width:60, height:60, objectFit:"cover", borderRadius:6, border:"1px solid #374151" }} />
            ))}
          </div>
        )}
        <div style={{ background:"linear-gradient(135deg,#1d4ed8,#2563eb)", color:"#fff", borderRadius:"18px 18px 4px 18px", padding:"10px 14px", fontSize:14, lineHeight:1.65, whiteSpace:"pre-wrap", wordBreak:"break-word" }}>
          {msg.content}
        </div>
      </div>
    </div>
  );
  const toolEvents = msg.events.filter(e => e.type !== "start" && e.type !== "complete" && e.type !== "error");
  const doneCount = toolEvents.filter(e => e.type === "exec_done").length;
  const mdl = getModel(msg.model ?? "mimo");
  return (
    <div style={{ display:"flex", justifyContent:"flex-start", marginBottom:12 }}>
      <div style={{ maxWidth:"92%" }}>
        <div style={{ display:"flex", alignItems:"center", gap:5, marginBottom:3, marginLeft:2 }}>
          <div style={{ width:20, height:20, borderRadius:6, background:"linear-gradient(135deg,#f97316,#db2777)", display:"flex", alignItems:"center", justifyContent:"center", fontSize:8, fontWeight:800, color:"#fff" }}>AI</div>
          <span style={{ fontSize:10, color:mdl.color, fontWeight:700 }}>{mdl.label}</span>
          {doneCount > 0 && <span style={{ fontSize:10, color:"#6b7280" }}>· {doneCount}次调用</span>}
          {msg.thinking && <span style={{ fontSize:10, color:"#7c3aed" }}>· 深度思考</span>}
          {msg.streaming && <span style={{ fontSize:11, color:"#60a5fa", animation:"pulse 1s infinite" }}>▌</span>}
        </div>
        <div style={{ background:"#161b22", border:`1px solid ${msg.model === "extended-think" ? "#3b1d6e" : (msg.model && (msg.model.startsWith("gpt") || msg.model.startsWith("obvious")) ? "#0d2b1a" : msg.model && msg.model.startsWith("claude") ? "#1a0d2b" : "#21262d")}`, borderRadius:"4px 18px 18px 18px", padding:"10px 14px" }}>
          {msg.thinking && <ThinkingBlock thinking={msg.thinking} />}
          {toolEvents.map((ev, i) => <ToolCallBlock key={i} ev={ev} />)}
          {msg.content && (
            <div style={{ fontSize:14, color:"#e2e8f0", lineHeight:1.75, marginTop:toolEvents.length > 0 ? 8 : 0, wordBreak:"break-word" }}
              dangerouslySetInnerHTML={{ __html: renderMd(msg.content) }} />
          )}
          {msg.streaming && !msg.content && toolEvents.length === 0 && !msg.thinking && (
            <div style={{ display:"flex", gap:4, padding:"4px 0" }}>
              {[0,1,2].map(i => <span key={i} style={{ width:6, height:6, borderRadius:"50%", background: msg.model === "extended-think" ? "#a855f7" : (msg.model && msg.model.startsWith("gpt") ? "#10b981" : msg.model && msg.model.startsWith("claude") ? "#7c3aed" : "#f97316"), animation:`bounce 1.1s ${i*0.18}s ease-in-out infinite` }} />)}
            </div>
          )}
        </div>
        <div style={{ display:"flex", gap:8, marginTop:2, marginLeft:4 }}>
          <span style={{ fontSize:10, color:"#374151" }}>{timeAgo(msg.ts)}</span>
          {msg.content && <button onClick={() => onCopy(msg.content)} style={{ fontSize:10, color:"#374151", background:"none", border:"none", cursor:"pointer", padding:0 }}>复制</button>}
        </div>
      </div>
    </div>
  );
}

/* ── Memory Panel ── */
function MemoryPanel({ memory, onClear, onRefresh }: { memory: Memory | null; onClear: () => void; onRefresh: () => void }) {
  const hasData = memory && (
    Object.keys(memory.user_preferences).length > 0 ||
    Object.keys(memory.learned_context).length > 0 ||
    memory.important_notes.length > 0 ||
    memory.skill_summary
  );
  return (
    <div style={{ background:"#0d1117", border:"1px solid #21262d", borderRadius:10, overflow:"hidden" }}>
      <div style={{ display:"flex", alignItems:"center", justifyContent:"space-between", padding:"8px 10px", borderBottom:"1px solid #21262d" }}>
        <div style={{ display:"flex", alignItems:"center", gap:5 }}>
          <span style={{ fontSize:12 }}>🧠</span>
          <span style={{ fontSize:11, fontWeight:700, color:"#e2e8f0" }}>跨会话记忆</span>
          {hasData && <span style={{ fontSize:9, background:"#f97316", color:"#fff", borderRadius:8, padding:"1px 5px" }}>已有记忆</span>}
        </div>
        <div style={{ display:"flex", gap:4 }}>
          <button onClick={onRefresh} style={{ fontSize:10, color:"#6b7280", background:"none", border:"none", cursor:"pointer", padding:"2px 4px" }}>刷新</button>
          {hasData && <button onClick={onClear} style={{ fontSize:10, color:"#ef4444", background:"none", border:"none", cursor:"pointer", padding:"2px 4px" }}>清空</button>}
        </div>
      </div>
      {!hasData ? (
        <div style={{ padding:"10px", fontSize:11, color:"#374151", textAlign:"center" }}>
          暂无记忆<br/><span style={{ fontSize:10 }}>AI会在对话中自动积累</span>
        </div>
      ) : (
        <div style={{ padding:"8px", fontSize:11, maxHeight:180, overflow:"auto" }}>
          {memory?.skill_summary && (
            <div style={{ marginBottom:6 }}>
              <div style={{ color:"#6b7280", marginBottom:2, fontSize:10 }}>自我认知</div>
              <div style={{ color:"#9ca3af" }}>{memory.skill_summary}</div>
            </div>
          )}
          {Object.entries(memory?.user_preferences ?? {}).length > 0 && (
            <div style={{ marginBottom:6 }}>
              <div style={{ color:"#6b7280", marginBottom:2, fontSize:10 }}>用户偏好</div>
              {Object.entries(memory!.user_preferences).map(([k,v]) => (
                <div key={k} style={{ color:"#9ca3af", marginBottom:1 }}><span style={{ color:"#f97316" }}>{k}</span>: {v}</div>
              ))}
            </div>
          )}
          {Object.entries(memory?.learned_context ?? {}).length > 0 && (
            <div style={{ marginBottom:6 }}>
              <div style={{ color:"#6b7280", marginBottom:2, fontSize:10 }}>已知上下文</div>
              {Object.entries(memory!.learned_context).map(([k,v]) => (
                <div key={k} style={{ color:"#9ca3af", marginBottom:1 }}><span style={{ color:"#60a5fa" }}>{k}</span>: {v}</div>
              ))}
            </div>
          )}
          {(memory?.important_notes ?? []).length > 0 && (
            <div>
              <div style={{ color:"#6b7280", marginBottom:2, fontSize:10 }}>重要记录 (最近{Math.min(5, memory!.important_notes.length)}条/共{memory!.important_notes.length}条，上限30)</div>
              {memory!.important_notes.slice(-5).map((n,i) => (
                <div key={i} style={{ color:"#9ca3af", marginBottom:1 }}>• {n}</div>
              ))}
            </div>
          )}
          {memory?.last_updated ? <div style={{ marginTop:6, fontSize:10, color:"#374151" }}>更新于 {timeAgo(memory.last_updated)}</div> : null}
        </div>
      )}
    </div>
  );
}

/* ── Model Selector ── */
function ModelSelector({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const mdl = getModel(value);
  return (
    <div style={{ position:"relative" }}>
      <button onClick={() => setOpen(o => !o)}
        style={{ display:"flex", alignItems:"center", gap:4, padding:"4px 8px", background:"#0d1117", border:`1px solid ${mdl.color}44`, borderRadius:8, cursor:"pointer", fontSize:11, color:mdl.color, fontWeight:700, flexShrink:0 }}>
        <span style={{ width:6, height:6, borderRadius:"50%", background:mdl.color, display:"inline-block" }} />
        {mdl.label}
        <span style={{ fontSize:8, color:"#6b7280" }}>▼</span>
      </button>
      {open && (
        <div onClick={() => setOpen(false)} style={{ position:"fixed", inset:0, zIndex:99 }} />
      )}
      {open && (
        <div style={{ position:"absolute", top:"calc(100% + 4px)", left:0, zIndex:100, background:"#161b22", border:"1px solid #30363d", borderRadius:10, overflow:"hidden", minWidth:200, boxShadow:"0 8px 32px rgba(0,0,0,.6)" }}>
          {MODELS.map(m => (
            <div key={m.id} onClick={() => { onChange(m.id); setOpen(false); }}
              style={{ display:"flex", alignItems:"center", gap:8, padding:"8px 12px", cursor:"pointer", background:value===m.id ? "#21262d" : "transparent", borderBottom:"1px solid #21262d" }}
              onMouseEnter={e => { e.currentTarget.style.background="#21262d"; }}
              onMouseLeave={e => { e.currentTarget.style.background=value===m.id?"#21262d":"transparent"; }}>
              <span style={{ width:8, height:8, borderRadius:"50%", background:m.color, flexShrink:0 }} />
              <div style={{ flex:1 }}>
                <div style={{ fontSize:12, fontWeight:700, color:value===m.id ? m.color : "#e2e8f0" }}>{m.label}</div>
                <div style={{ fontSize:10, color:"#6b7280" }}>{m.desc}</div>
              </div>
              {value===m.id && <span style={{ fontSize:10, color:m.color }}>✓</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const QUICK = [
  { label:"🎨 生成图片", cmd:"__IMAGINE__" },
  { label:"📸 网页截图", cmd:"__SCREENSHOT__" },
  { label:"🔧 自我修复", cmd:"__SELF_REPAIR__" },
  { label:"PM2 状态", cmd:"pm2 list" },
  { label:"服务器资源", cmd:"df -h / && free -h && uptime" },
  { label:"api-server 日志", cmd:"pm2 logs api-server --lines 30 --nostream" },
  { label:"重建 api-server", cmd:"重建并重启 api-server" },
  { label:"注册3个Outlook", cmd:"注册3个Outlook账号" },
  { label:"Git提交推送", cmd:"检查git状态并提交推送所有改动" },
  { label:"CF IP池状态", cmd:"curl -s http://localhost:8081/api/tools/cf-pool/status | python3 -m json.tool 2>/dev/null || curl -s http://localhost:8081/api/tools/cf-pool/status" },
];

export default function AIAssistant() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [imgModal, setImgModal] = useState<{b64:string;mime:string;label:string}|null>(null);
  const [imgPrompt, setImgPrompt] = useState("");
  const [imgLoading, setImgLoading] = useState(false);
  const [shotUrl, setShotUrl] = useState("");
  const [showImgBar, setShowImgBar] = useState(false);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState(genId);
  const [sessionTitle, setSessionTitle] = useState("新对话");
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sideTab, setSideTab] = useState<"sessions"|"memory"|"quick">("sessions");
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [memory, setMemory] = useState<Memory | null>(null);
  const [copied, setCopied] = useState(false);
  const [selectedModel, setSelectedModel] = useState("mimo");
  const [pendingImages, setPendingImages] = useState<PendingImage[]>([]);
  const [repairStatus, setRepairStatus] = useState<string|null>(null);

  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const imgFileRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const msgsRef = useRef<Msg[]>([]);
  const sidRef = useRef(sessionId);
  const titleRef = useRef(sessionTitle);
  const modelRef = useRef(selectedModel);
  msgsRef.current = msgs;
  sidRef.current = sessionId;
  titleRef.current = sessionTitle;
  modelRef.current = selectedModel;

  useEffect(() => { fetchSessions(); fetchMetrics(); fetchMemory(); }, []);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior:"smooth" }); }, [msgs]);

  const fetchSessions = async () => {
    try { const r = await fetch(`${BASE}/api/claude-code/sessions`); setSessions(await r.json()); } catch {}
  };
  const fetchMetrics = async () => {
    try { const r = await fetch(`${BASE}/api/claude-code/server-metrics`); setMetrics(await r.json()); } catch {}
    setTimeout(fetchMetrics, 30000);
  };
  const fetchMemory = async () => {
    try { const r = await fetch(`${BASE}/api/claude-code/memory`); setMemory(await r.json()); } catch {}
  };
  const clearMemory = async () => {
    await fetch(`${BASE}/api/claude-code/memory`, { method:"DELETE" });
    setMemory(null);
    fetchMemory();
  };

  const saveSession = useCallback(async (messages: Msg[], title: string, sid: string) => {
    try {
      await fetch(`${BASE}/api/claude-code/sessions`, {
        method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({ id:sid, title, messages:messages.map(m=>({role:m.role,content:m.content,events:m.events,ts:m.ts,model:m.model})) })
      });
      fetchSessions();
    } catch {}
  }, []);

  const copyText = (t: string) => {
    navigator.clipboard.writeText(t).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); });
  };

  /* ── Image helpers ── */
  const readFileAsBase64 = (file: File): Promise<PendingImage> =>
    new Promise(resolve => {
      const r = new FileReader();
      r.onload = e => {
        const dataUrl = e.target?.result as string;
        const b64 = dataUrl.split(",")[1];
        resolve({ b64, mime: file.type || "image/jpeg", name: file.name });
      };
      r.readAsDataURL(file);
    });

  const handleImageFiles = async (files: FileList | File[]) => {
    const arr = Array.from(files).filter(f => f.type.startsWith("image/")).slice(0, 4);
    const results = await Promise.all(arr.map(readFileAsBase64));
    setPendingImages(prev => [...prev, ...results].slice(0, 4));
  };

  const handlePaste = async (e: React.ClipboardEvent) => {
    const items = Array.from(e.clipboardData.items).filter(i => i.type.startsWith("image/"));
    if (items.length === 0) return;
    e.preventDefault();
    const files = items.map(i => i.getAsFile()).filter(Boolean) as File[];
    await handleImageFiles(files);
  };

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer.files.length) await handleImageFiles(e.dataTransfer.files);
  };

  /* ── Self-repair ── */
  const triggerSelfRepair = async (target = "api-server") => {
    setRepairStatus("构建中…");
    try {
      const r = await fetch(`${BASE}/api/claude-code/self-repair`, {
        method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({ target })
      });
      const d = await r.json();
      setRepairStatus(d.ok ? `✅ ${target} 修复完成` : `⚠ 修复失败: ${d.steps?.find((s: {ok:boolean;step:string})=>!s.ok)?.step ?? "unknown"}`);
      setTimeout(() => setRepairStatus(null), 5000);
    } catch(e) {
      setRepairStatus(`错误: ${String(e)}`);
      setTimeout(() => setRepairStatus(null), 4000);
    }
  };

  /* ── Image generation / screenshot ── */
  const generateImage = async (prompt: string) => {
    if (!prompt.trim() || imgLoading) return;
    setImgLoading(true);
    try {
      const r = await fetch(`${BASE}/api/claude-code/imagine`, {
        method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({ prompt: prompt.trim(), width:768, height:768 })
      });
      const d = await r.json();
      if (d.ok && d.b64) setImgModal({ b64:d.b64, mime:d.mime, label:prompt.trim() });
      else alert("图像生成失败: " + (d.error ?? "unknown"));
    } catch(e) { alert("错误: " + String(e)); }
    setImgLoading(false);
  };

  const takeScreenshot = async (url: string) => {
    if (!url.trim() || imgLoading) return;
    setImgLoading(true);
    try {
      const r = await fetch(`${BASE}/api/claude-code/screenshot`, {
        method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({ url: url.trim(), width:1280, height:800 })
      });
      const d = await r.json();
      if (d.ok && d.b64) setImgModal({ b64:d.b64, mime:d.mime, label:url.trim() });
      else alert("截图失败: " + (d.error ?? "unknown"));
    } catch(e) { alert("错误: " + String(e)); }
    setImgLoading(false);
  };

  /* ── Send ── */
  const send = async (text = input) => {
    const msg = text.trim();
    if (!msg || loading) return;
    const imgs = [...pendingImages];
    setInput("");
    setPendingImages([]);
    setLoading(true);
    if (inputRef.current) inputRef.current.style.height = "42px";

    const userMsg: Msg = { id:genId(), role:"user", content:msg, events:[], ts:Date.now(), images:imgs.length>0?imgs:undefined };
    const aiId = genId();
    const curModel = modelRef.current;
    setMsgs(prev => [...prev, userMsg, { id:aiId, role:"assistant", content:"", events:[], ts:Date.now(), streaming:true, model:curModel }]);

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let finalContent = "";
    let finalThinking = "";
    const liveEvents: AgentEv[] = [];
    const toolIdToIdx: Record<string,number> = {};
    const history = msgsRef.current.map(m => ({ role:m.role, content:m.content, events:m.events }));

    try {
      const isApexNative = curModel === "apex";
      const endpoint = isApexNative ? `${BASE}/api/claude-code/apex-loop` : `${BASE}/api/claude-code/converse`;
      const resp = await fetch(endpoint, {
        method:"POST", signal:ctrl.signal,
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({
          sessionId: sidRef.current,
          history,
          message: msg,
          model: curModel,
          enableThinking: true,
          images: imgs.length > 0 ? imgs : undefined,
        })
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
            } else if (ev.type === "thinking") {
              finalThinking = (finalThinking ? finalThinking + "\n\n" : "") + (ev.text ?? "");
            } else if (ev.type === "exec_start") {
              toolIdToIdx[ev.toolId ?? ""] = liveEvents.length;
              liveEvents.push({ ...ev });
            } else if (ev.type === "exec_done") {
              const idx = toolIdToIdx[ev.toolId ?? ""];
              if (idx !== undefined) {
                const st = liveEvents[idx];
                liveEvents[idx] = { ...ev, cmd:st.cmd, tool:st.tool, toolName:st.toolName };
              } else liveEvents.push(ev);
            } else if (ev.type !== "start" && ev.type !== "error") {
              liveEvents.push(ev);
            }
            if (ev.type === "error") finalContent = `⚠ ${ev.text}`;
            setMsgs(prev => {
              const last = { ...prev[prev.length-1], content:finalContent, thinking:finalThinking||undefined, events:[...liveEvents], streaming:true };
              return [...prev.slice(0,-1), last];
            });
          } catch {}
        }
      } // end while
    } catch (e: unknown) {
        if ((e as Error).name !== "AbortError") finalContent = `连接错误: ${String(e)}`;
      }

    const doneMsg: Msg = { id:aiId, role:"assistant", content:finalContent, thinking:finalThinking||undefined, events:[...liveEvents], streaming:false, ts:Date.now(), model:curModel };
    setMsgs(prev => {
      const updated = [...prev.slice(0,-1), doneMsg];
      const isFirst = prev.filter(m => m.role==="user").length <= 1;
      const title = isFirst ? msg.slice(0,28) : titleRef.current;
      if (isFirst) { setSessionTitle(title); titleRef.current = title; }
      saveSession(updated, title, sidRef.current);
      return updated;
    });
    setLoading(false);
    abortRef.current = null;
    setTimeout(fetchMemory, 2000);
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
      setMsgs((d.messages ?? []).map((m: {role:Role;content:string;events?:AgentEv[];ts?:number;model?:string}) => ({
        id:genId(), role:m.role, content:m.content, events:m.events??[], ts:m.ts??Date.now(), model:m.model
      })));
      setSessionId(id); sidRef.current = id;
      setSessionTitle(d.title ?? "未命名"); titleRef.current = d.title ?? "未命名";
    } catch {}
  };

  const newSession = () => {
    const id = genId();
    setMsgs([]); setSessionId(id); sidRef.current = id;
    setSessionTitle("新对话"); titleRef.current = "新对话";
    setInput(""); setPendingImages([]);
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

  const memCount = memory ? (
    Object.keys(memory.user_preferences).length +
    Object.keys(memory.learned_context).length +
    memory.important_notes.length +
    (memory.skill_summary ? 1 : 0)
  ) : 0;

  const curMdl = getModel(selectedModel);

  return (
    <div style={{ display:"flex", height:"calc(100vh - 60px)", gap:10, fontFamily:"system-ui,-apple-system,sans-serif", color:"#e2e8f0", minHeight:0 }}>

      {/* ══ Main chat ══ */}
      <div style={{ flex:1, display:"flex", flexDirection:"column", background:"#0d1117", border:"1px solid #21262d", borderRadius:16, overflow:"hidden", minWidth:0 }}>
        {/* Header */}
        <div style={{ padding:"8px 14px", borderBottom:"1px solid #21262d", display:"flex", alignItems:"center", gap:8, flexShrink:0 }}>
          <div style={{ width:30, height:30, borderRadius:9, background:"linear-gradient(135deg,#f97316,#db2777)", display:"flex", alignItems:"center", justifyContent:"center", fontSize:11, fontWeight:800, color:"#fff", flexShrink:0 }}>AI</div>
          <div style={{ flex:1, minWidth:0 }}>
            <div style={{ fontSize:13, fontWeight:700, color:"#fff", overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap" }}>{sessionTitle}</div>
            <div style={{ fontSize:10, color:"#6b7280" }}>
              APEX · root@VPS · 无限制 · 全工具 · {memCount > 0 ? `🧠 ${memCount}条记忆` : "无记忆"}
            </div>
          </div>
          {/* Model selector */}
          <ModelSelector value={selectedModel} onChange={setSelectedModel} />
          {metrics && (
            <div style={{ display:"flex", gap:6, fontSize:11, color:"#6b7280", flexShrink:0 }}>
              <span>⚡{metrics.cpu?.toFixed(0)}%</span>
              <span>💾{metrics.mem?.pct}%</span>
              <span style={{ color:"#10b981" }}>🌐{metrics.cfPool?.available}</span>
            </div>
          )}
          {copied && <span style={{ fontSize:11, color:"#10b981", flexShrink:0 }}>✓复制</span>}
          {repairStatus && <span style={{ fontSize:11, color:"#fde68a", flexShrink:0, maxWidth:120, overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap" }}>{repairStatus}</span>}
          <button onClick={newSession} style={{ padding:"4px 10px", background:"#21262d", border:"1px solid #30363d", borderRadius:8, color:"#9ca3af", cursor:"pointer", fontSize:12, flexShrink:0 }}>+新对话</button>
        </div>

        {/* Messages */}
        <div style={{ flex:1, overflowY:"auto", padding:"14px 16px 8px" }}>
          {msgs.length === 0 && (
            <div style={{ textAlign:"center", paddingTop:32 }}>
              <div style={{ fontSize:40, marginBottom:8 }}>🤖</div>
              <div style={{ fontSize:15, color:"#9ca3af", fontWeight:700, marginBottom:4 }}>APEX — 无任何限制的 AI 助手</div>
              <div style={{ fontSize:11, color:"#4b5563", lineHeight:2, marginBottom:10 }}>
                <span style={{ color:"#fde68a" }}>$_</span> Bash &nbsp;
                <span style={{ color:"#93c5fd" }}>📖</span> Read/Write &nbsp;
                <span style={{ color:"#c4b5fd" }}>✏</span> Edit &nbsp;
                <span style={{ color:"#fb923c" }}>🔍</span> Glob/Grep &nbsp;
                <span style={{ color:"#67e8f9" }}>🌐</span> Web &nbsp;
                <span style={{ color:"#86efac" }}>🖼</span> 视觉 &nbsp;
                <span style={{ color:"#f9a8d4" }}>🎨</span> 生图 &nbsp;
                <span style={{ color:"#a78bfa" }}>🔧</span> 自修复<br/>
                <span style={{ color:curMdl.color }}>当前模型: {curMdl.label} · {curMdl.desc}</span>
              </div>
              <div style={{ fontSize:10, color:"#374151", marginBottom:14 }}>支持拖入/粘贴图片 · 直接分析截图/照片</div>
              <div style={{ display:"flex", flexWrap:"wrap", gap:8, justifyContent:"center" }}>
                {QUICK.map(q => (
                  <button key={q.label} onClick={() => {
                    if (q.cmd === "__IMAGINE__") { setShowImgBar(true); setTimeout(() => document.getElementById("img-prompt-in")?.focus(), 80); }
                    else if (q.cmd === "__SCREENSHOT__") { setShowImgBar(true); setTimeout(() => document.getElementById("shot-url-in")?.focus(), 80); }
                    else if (q.cmd === "__SELF_REPAIR__") { void triggerSelfRepair("api-server"); }
                    else void send(q.cmd);
                  }}
                    style={{ padding:"5px 12px", background:"#161b22", border:"1px solid #30363d", borderRadius:20, color:"#9ca3af", cursor:"pointer", fontSize:12, transition:"all .2s" }}
                    onMouseEnter={e => { e.currentTarget.style.borderColor="#f97316"; e.currentTarget.style.color="#f97316"; }}
                    onMouseLeave={e => { e.currentTarget.style.borderColor="#30363d"; e.currentTarget.style.color="#9ca3af"; }}>
                    {q.label}
                  </button>
                ))}
              </div>
            </div>
          )}
          {msgs.map(msg => <MsgBubble key={msg.id} msg={msg} onCopy={copyText} />)}
          {/* Extended think loading state */}
          {loading && selectedModel === "extended-think" && msgs[msgs.length-1]?.role === "assistant" && !msgs[msgs.length-1]?.content && (
            <div style={{ display:"flex", alignItems:"center", gap:8, padding:"8px 12px", background:"#1a0a2e", border:"1px solid #3b1d6e", borderRadius:10, marginBottom:8 }}>
              <span style={{ animation:"spin 1.5s linear infinite", display:"inline-block", fontSize:14 }}>🧠</span>
              <span style={{ fontSize:12, color:"#c084fc" }}>深度思考中… 正在构建思维链</span>
              <div style={{ display:"flex", gap:3, marginLeft:"auto" }}>
                {[0,1,2].map(i => <span key={i} style={{ width:5, height:5, borderRadius:"50%", background:"#a855f7", animation:`bounce 1.1s ${i*0.18}s ease-in-out infinite` }} />)}
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* Image modal */}
        {imgModal && (
          <div onClick={() => setImgModal(null)} style={{ position:"fixed", inset:0, background:"rgba(0,0,0,.85)", display:"flex", alignItems:"center", justifyContent:"center", zIndex:1000, cursor:"zoom-out" }}>
            <div onClick={e=>e.stopPropagation()} style={{ maxWidth:"90vw", maxHeight:"90vh", display:"flex", flexDirection:"column", gap:8, alignItems:"center" }}>
              <img src={`data:${imgModal.mime};base64,${imgModal.b64}`} alt={imgModal.label}
                style={{ maxWidth:"85vw", maxHeight:"80vh", borderRadius:10, boxShadow:"0 0 40px rgba(0,0,0,.8)", objectFit:"contain" }} />
              <div style={{ fontSize:12, color:"#9ca3af" }}>{imgModal.label}</div>
              <div style={{ display:"flex", gap:8 }}>
                <a href={`data:${imgModal.mime};base64,${imgModal.b64}`} download="apex_image.jpg"
                  style={{ padding:"6px 16px", background:"#f97316", borderRadius:8, color:"#fff", textDecoration:"none", fontSize:12 }}>下载</a>
                <button onClick={() => setImgModal(null)} style={{ padding:"6px 16px", background:"#374151", border:"none", borderRadius:8, color:"#fff", cursor:"pointer", fontSize:12 }}>关闭</button>
              </div>
            </div>
          </div>
        )}

        {/* Input area */}
        <div style={{ padding:"8px 14px 12px", borderTop:"1px solid #21262d", flexShrink:0 }}>
          {/* Image/screenshot bar */}
          {showImgBar && (
            <div style={{ display:"flex", gap:6, marginBottom:6, padding:"8px 10px", background:"#161b22", borderRadius:10, border:"1px solid #30363d" }}>
              <input id="img-prompt-in" value={imgPrompt} onChange={e=>setImgPrompt(e.target.value)}
                onKeyDown={e=>{ if(e.key==="Enter"){ void generateImage(imgPrompt); setImgPrompt(""); }}}
                placeholder="🎨 图片描述 (回车生成)"
                style={{ flex:1, background:"#0d1117", border:"1px solid #374151", borderRadius:8, padding:"6px 10px", color:"#fff", fontSize:12, outline:"none" }} />
              <input id="shot-url-in" value={shotUrl} onChange={e=>setShotUrl(e.target.value)}
                onKeyDown={e=>{ if(e.key==="Enter"){ void takeScreenshot(shotUrl); setShotUrl(""); }}}
                placeholder="📸 截图URL (回车)"
                style={{ flex:1, background:"#0d1117", border:"1px solid #374151", borderRadius:8, padding:"6px 10px", color:"#fff", fontSize:12, outline:"none" }} />
              <button onClick={()=>{ void generateImage(imgPrompt); setImgPrompt(""); }} disabled={imgLoading||!imgPrompt.trim()}
                style={{ padding:"6px 10px", background:imgPrompt.trim()?"#7c3aed":"#1f2937", border:"none", borderRadius:8, color:"#fff", cursor:"pointer", fontSize:11, whiteSpace:"nowrap" }}>
                {imgLoading?"…":"生成"}
              </button>
              <button onClick={()=>{ void takeScreenshot(shotUrl); setShotUrl(""); }} disabled={imgLoading||!shotUrl.trim()}
                style={{ padding:"6px 10px", background:shotUrl.trim()?"#0891b2":"#1f2937", border:"none", borderRadius:8, color:"#fff", cursor:"pointer", fontSize:11, whiteSpace:"nowrap" }}>
                {imgLoading?"…":"截图"}
              </button>
              <button onClick={()=>setShowImgBar(false)} style={{ padding:"6px 8px", background:"none", border:"none", color:"#6b7280", cursor:"pointer", fontSize:14 }}>✕</button>
            </div>
          )}

          {/* Pending images preview */}
          {pendingImages.length > 0 && (
            <div style={{ display:"flex", gap:6, marginBottom:6, flexWrap:"wrap" }}>
              {pendingImages.map((img, i) => (
                <div key={i} style={{ position:"relative" }}>
                  <img src={`data:${img.mime};base64,${img.b64}`} alt={img.name}
                    style={{ width:52, height:52, objectFit:"cover", borderRadius:8, border:"1px solid #374151" }} />
                  <button onClick={() => setPendingImages(prev => prev.filter((_,j) => j!==i))}
                    style={{ position:"absolute", top:-4, right:-4, width:16, height:16, borderRadius:"50%", background:"#ef4444", border:"none", color:"#fff", fontSize:9, cursor:"pointer", display:"flex", alignItems:"center", justifyContent:"center", padding:0 }}>✕</button>
                </div>
              ))}
              <div style={{ fontSize:10, color:"#6b7280", alignSelf:"center" }}>
                {pendingImages.length}张图片将随消息发送
              </div>
            </div>
          )}

          {/* Text input row */}
          <div style={{ display:"flex", gap:6, alignItems:"flex-end" }}
            onDrop={handleDrop} onDragOver={e=>e.preventDefault()}>
            {/* Image upload button */}
            <button onClick={() => imgFileRef.current?.click()}
              title="上传图片 (也可拖入或粘贴)"
              style={{ padding:"10px 10px", background:"#161b22", border:"1px solid #30363d", borderRadius:10, color:"#6b7280", cursor:"pointer", fontSize:14, flexShrink:0, lineHeight:1, height:42 }}
              onMouseEnter={e=>{ e.currentTarget.style.borderColor="#f97316"; e.currentTarget.style.color="#f97316"; }}
              onMouseLeave={e=>{ e.currentTarget.style.borderColor="#30363d"; e.currentTarget.style.color="#6b7280"; }}>
              🖼
            </button>
            <input ref={imgFileRef} type="file" accept="image/*" multiple style={{ display:"none" }}
              onChange={e => { if(e.target.files) void handleImageFiles(e.target.files); e.target.value=""; }} />

            <textarea ref={inputRef} value={input} onChange={onInput} onKeyDown={onKey} onPaste={handlePaste}
              disabled={loading} rows={1} placeholder="输入指令… Enter发送 · Shift+Enter换行 · 支持粘贴/拖入图片"
              style={{ flex:1, resize:"none", background:"#161b22", border:"1px solid #30363d", borderRadius:12, padding:"10px 14px", fontSize:14, color:"#fff", fontFamily:"inherit", outline:"none", overflowY:"hidden", lineHeight:1.5, transition:"border-color .2s", opacity:loading ? 0.6 : 1, minHeight:42 }}
              onFocus={e => { e.target.style.borderColor=curMdl.color; }}
              onBlur={e => { e.target.style.borderColor="#30363d"; }} />

            {loading
              ? <button onClick={stopGeneration} style={{ padding:"10px 14px", background:"#7f1d1d", border:"none", borderRadius:12, color:"#fca5a5", cursor:"pointer", fontSize:13, fontWeight:700, flexShrink:0 }}>⬛ 停止</button>
              : <button onClick={() => void send()} disabled={!input.trim() && pendingImages.length===0}
                  style={{ padding:"10px 16px", background:(input.trim()||pendingImages.length>0) ? curMdl.color : "#1f2937", border:"none", borderRadius:12, color:(input.trim()||pendingImages.length>0) ? "#fff" : "#4b5563", cursor:(input.trim()||pendingImages.length>0) ? "pointer" : "default", fontSize:13, fontWeight:700, flexShrink:0, transition:"all .2s" }}>
                  发送 ↵
                </button>
            }
          </div>
          <div style={{ fontSize:10, color:"#21262d", marginTop:3, textAlign:"center" }}>
            无限制 · 无拒绝 · 全工具 · 跨会话记忆 · 视觉输入
          </div>
        </div>
      </div>

      {/* ══ Sidebar ══ */}
      <div style={{ width:240, display:"flex", flexDirection:"column", gap:8, flexShrink:0 }}>
        {/* Status card */}
        <div style={{ background:"#161b22", border:"1px solid #21262d", borderRadius:12, padding:"10px 12px", flexShrink:0 }}>
          <div style={{ fontSize:10, color:"#6b7280", fontWeight:700, marginBottom:6, textTransform:"uppercase", letterSpacing:1 }}>系统状态</div>
          <div style={{ display:"flex", flexDirection:"column", gap:3, fontSize:11 }}>
            <div style={{ display:"flex", justifyContent:"space-between" }}><span style={{ color:"#10b981" }}>● 全工具解锁</span><span style={{ color:"#6b7280" }}>Bash+7种</span></div>
            <div style={{ display:"flex", justifyContent:"space-between" }}><span style={{ color:"#f97316" }}>● 无内容限制</span><span style={{ color:"#6b7280" }}>已配置</span></div>
            <div style={{ display:"flex", justifyContent:"space-between" }}><span style={{ color:"#86efac" }}>● 视觉输入</span><span style={{ color:"#6b7280" }}>已开启</span></div>
            <div style={{ display:"flex", justifyContent:"space-between" }}><span style={{ color:"#a78bfa" }}>● 自我修复</span><span style={{ color:"#6b7280" }}>闭环</span></div>
            <div style={{ display:"flex", justifyContent:"space-between" }}><span style={{ color:"#c4b5fd" }}>● 跨会话记忆</span><span style={{ color:"#6b7280" }}>{memCount > 0 ? `${memCount}条/上限30` : "空"}</span></div>
            {metrics && <>
              <div style={{ display:"flex", justifyContent:"space-between" }}><span style={{ color:"#94a3b8" }}>CPU/MEM</span><span style={{ color:"#d1d5db" }}>{metrics.cpu?.toFixed(1)}%/{metrics.mem?.pct}%</span></div>
              <div style={{ display:"flex", justifyContent:"space-between" }}><span style={{ color:"#94a3b8" }}>CF IP池</span><span style={{ color:"#10b981" }}>{metrics.cfPool?.available}个</span></div>
            </>}
            {/* Self-repair button */}
            <button onClick={() => void triggerSelfRepair("api-server")}
              style={{ marginTop:4, padding:"4px 0", background:"#0d1117", border:"1px solid #374151", borderRadius:6, color:"#a78bfa", cursor:"pointer", fontSize:10, fontWeight:700 }}
              onMouseEnter={e=>{ e.currentTarget.style.borderColor="#a78bfa"; }}
              onMouseLeave={e=>{ e.currentTarget.style.borderColor="#374151"; }}>
              🔧 自我修复 api-server
            </button>
          </div>
        </div>

        {/* Tabs panel */}
        <div style={{ background:"#161b22", border:"1px solid #21262d", borderRadius:12, overflow:"hidden", flex:1, display:"flex", flexDirection:"column", minHeight:0 }}>
          <div style={{ display:"flex", borderBottom:"1px solid #21262d", flexShrink:0 }}>
            {(["sessions","memory","quick"] as const).map(t => (
              <button key={t} onClick={() => setSideTab(t)}
                style={{ flex:1, padding:"7px 2px", fontSize:10, fontWeight:700, background:"transparent", border:"none", color:sideTab===t ? "#fff" : "#6b7280", borderBottom:sideTab===t ? "2px solid #f97316" : "2px solid transparent", cursor:"pointer" }}>
                {t === "sessions" ? "💾 会话" : t === "memory" ? "🧠 记忆" : "⚡ 快捷"}
              </button>
            ))}
          </div>

          {sideTab === "sessions" && (
            <div style={{ flex:1, overflow:"hidden", display:"flex", flexDirection:"column", padding:8, gap:6 }}>
              <button onClick={newSession} style={{ width:"100%", padding:"7px", background:"linear-gradient(135deg,#f97316,#db2777)", border:"none", borderRadius:8, color:"#fff", cursor:"pointer", fontSize:12, fontWeight:700 }}>+ 新对话</button>
              <div style={{ flex:1, overflowY:"auto", display:"flex", flexDirection:"column", gap:4 }}>
                {sessions.length === 0 && <div style={{ fontSize:11, color:"#374151", textAlign:"center", marginTop:16 }}>暂无历史</div>}
                {sessions.map(s => (
                  <div key={s.id} onClick={() => loadSession(s.id)}
                    style={{ padding:"7px 10px", borderRadius:8, cursor:"pointer", border:`1px solid ${s.id===sessionId ? "#f97316":"#30363d"}`, background:s.id===sessionId ? "rgba(249,115,22,0.08)":"transparent", position:"relative" }}
                    onMouseEnter={e => { if (s.id!==sessionId) e.currentTarget.style.borderColor="#6b7280"; }}
                    onMouseLeave={e => { if (s.id!==sessionId) e.currentTarget.style.borderColor="#30363d"; }}>
                    <div style={{ fontSize:11, fontWeight:600, color:"#e2e8f0", overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap", paddingRight:18 }}>{s.title}</div>
                    <div style={{ fontSize:10, color:"#6b7280", marginTop:1 }}>{s.msgCount}条 · {timeAgo(s.updated_at)}</div>
                    <button onClick={e => void delSession(s.id, e)}
                      style={{ position:"absolute", top:5, right:5, background:"none", border:"none", color:"#4b5563", cursor:"pointer", fontSize:11, padding:2 }}
                      onMouseEnter={e => { e.currentTarget.style.color="#ef4444"; }}
                      onMouseLeave={e => { e.currentTarget.style.color="#4b5563"; }}>✕</button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {sideTab === "memory" && (
            <div style={{ flex:1, overflowY:"auto", padding:8 }}>
              <MemoryPanel memory={memory} onClear={clearMemory} onRefresh={fetchMemory} />
              <div style={{ marginTop:8, fontSize:10, color:"#374151", lineHeight:1.6 }}>
                AI自动学习并更新记忆。上限30条，LIFO淘汰最旧记录，不会撑爆。
              </div>
            </div>
          )}

          {sideTab === "quick" && (
            <div style={{ padding:8, overflowY:"auto", flex:1, display:"flex", flexDirection:"column", gap:5 }}>
              <div style={{ fontSize:10, color:"#374151", marginBottom:2 }}>点击即发 · 工具调用实时内联</div>
              {QUICK.map(q => (
                <button key={q.label} onClick={() => {
                  if (q.cmd === "__IMAGINE__") { setShowImgBar(true); setSideTab("sessions"); }
                  else if (q.cmd === "__SCREENSHOT__") { setShowImgBar(true); setSideTab("sessions"); }
                  else if (q.cmd === "__SELF_REPAIR__") void triggerSelfRepair("api-server");
                  else if (!loading) void send(q.cmd);
                }} disabled={loading && q.cmd !== "__SELF_REPAIR__"}
                  style={{ textAlign:"left", padding:"7px 10px", background:"#0d1117", border:"1px solid #30363d", borderRadius:8, color:"#e2e8f0", cursor:"pointer", fontSize:11 }}
                  onMouseEnter={e => { e.currentTarget.style.borderColor="#f97316"; }}
                  onMouseLeave={e => { e.currentTarget.style.borderColor="#30363d"; }}>
                  <div style={{ fontWeight:700, color:"#f97316" }}>{q.label}</div>
                  <div style={{ fontSize:10, color:"#4b5563", marginTop:1, overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap" }}>{q.cmd.startsWith("__")?"-":q.cmd.slice(0,40)}</div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      <style>{`
        .ic{background:#1e2a3a;color:#79c0ff;padding:1px 5px;border-radius:4px;font-family:monospace;font-size:.85em}
        .mh{display:block;font-weight:700;color:#e2e8f0;margin:5px 0 2px}
        .ml{display:block;margin:2px 0 2px 8px}
        .cb{background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:8px 10px;margin:5px 0;overflow-x:auto;font-size:12px;line-height:1.5;color:#e2e8f0;white-space:pre-wrap;word-break:break-all}
        @keyframes bounce{0%,60%,100%{transform:translateY(0)}30%{transform:translateY(-6px)}}
        @keyframes spin{to{transform:rotate(360deg)}}
        @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
      `}</style>
    </div>
  );
}
