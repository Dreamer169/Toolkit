import { useEffect, useRef, useState, useCallback } from "react";

/* ── Types ── */
type Role = "user" | "assistant";
interface AgentEv {
  type: string; text?: string; cmd?: string; stdout?: string; stderr?: string;
  code?: number; path?: string; lines?: number; size?: number; toolId?: string; ai_text?: string;
}
interface Msg {
  id: string; role: Role; content: string; events: AgentEv[];
  ts: number; streaming?: boolean; error?: string;
}
interface Session { id: string; title: string; created_at: number; updated_at: number; msgCount: number }

const BASE = "";
const genId = () => Math.random().toString(36).slice(2) + Date.now().toString(36);
const timeAgo = (ts: number) => {
  const d = Date.now() - ts;
  if (d < 60000) return "刚刚";
  if (d < 3600000) return Math.floor(d/60000) + "分钟前";
  if (d < 86400000) return Math.floor(d/3600000) + "小时前";
  return new Date(ts).toLocaleDateString("zh");
};

function renderMd(s: string) {
  return s
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`\n]+)`/g, "<code class='ic'>$1</code>")
    .replace(/^#{1,3} (.+)$/gm, "<b class='mh'>$1</b>")
    .replace(/^[-•] (.+)$/gm, "<span class='ml'>• $1</span>");
}

/* ── Inline tool call display ── */
function ToolCallBlock({ ev }: { ev: AgentEv }) {
  const [open, setOpen] = useState((ev.code ?? 0) !== 0);
  if (ev.type === "exec_start") return (
    <div style={{display:"flex",alignItems:"center",gap:6,padding:"4px 8px",background:"#0d1117",borderRadius:6,margin:"4px 0",fontSize:12,fontFamily:"monospace"}}>
      <span style={{color:"#f59e0b"}}>▶</span>
      <code style={{color:"#fde68a",flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{ev.cmd?.slice(0,70)}{(ev.ai_text && ev.cmd?.length === 0) ? ev.ai_text.slice(0,70) : ""}</code>
      <span style={{color:"#6b7280",fontSize:10}}>실행 중…</span>
    </div>
  );
  if (ev.type === "exec_done") return (
    <div style={{border:`1px solid ${(ev.code??0)===0?"#1f2937":"#7f1d1d"}`,borderRadius:6,overflow:"hidden",margin:"4px 0",fontSize:12,fontFamily:"monospace"}}>
      <div onClick={()=>setOpen(o=>!o)} style={{display:"flex",alignItems:"center",gap:6,padding:"4px 8px",background:(ev.code??0)===0?"#0d1117":"#1c0505",cursor:"pointer"}}>
        <span style={{color:(ev.code??0)===0?"#10b981":"#ef4444"}}>{(ev.code??0)===0?"✓":`✗ ${ev.code}`}</span>
        <code style={{color:"#fde68a",flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{ev.cmd?.slice(0,60)}</code>
        <span style={{color:"#374151",fontSize:10}}>{open?"▲":"▼"}</span>
      </div>
      {open && (ev.stdout||ev.stderr) && (
        <div style={{padding:"6px 8px",background:"#000",maxHeight:200,overflow:"auto"}}>
          {ev.stdout && <pre style={{color:"#86efac",whiteSpace:"pre-wrap",wordBreak:"break-all",margin:0,fontSize:11,lineHeight:1.4}}>{ev.stdout.slice(0,1200)}</pre>}
          {ev.stderr && <pre style={{color:"#fca5a5",whiteSpace:"pre-wrap",wordBreak:"break-all",margin:0,fontSize:11,lineHeight:1.4}}>{ev.stderr.slice(0,400)}</pre>}
        </div>
      )}
    </div>
  );
  if (ev.type === "ai_response") return (
    <div style={{borderLeft:"2px solid #f97316",paddingLeft:8,margin:"4px 0",fontSize:12,color:"#9ca3af",fontStyle:"italic"}}>
      {ev.text?.slice(0,200)}{(ev.text?.length??0)>200?"…":""}
    </div>
  );
  if (ev.type === "status") return (
    <div style={{display:"flex",alignItems:"center",gap:6,fontSize:11,color:"#6b7280",padding:"2px 0"}}>
      <span style={{animation:"spin 1s linear infinite",display:"inline-block"}}>⟳</span> {ev.text}
    </div>
  );
  return null;
}

/* ── Message bubble ── */
function MsgBubble({ msg }: { msg: Msg }) {
  if (msg.role === "user") return (
    <div style={{display:"flex",justifyContent:"flex-end",marginBottom:16}}>
      <div style={{maxWidth:"78%",background:"#1d4ed8",color:"#fff",borderRadius:"18px 18px 4px 18px",padding:"10px 14px",fontSize:14,lineHeight:1.6,whiteSpace:"pre-wrap",wordBreak:"break-word"}}>
        {msg.content}
      </div>
    </div>
  );
  return (
    <div style={{display:"flex",justifyContent:"flex-start",marginBottom:16}}>
      <div style={{maxWidth:"90%"}}>
        <div style={{display:"flex",alignItems:"center",gap:6,marginBottom:4,marginLeft:2}}>
          <div style={{width:20,height:20,borderRadius:6,background:"linear-gradient(135deg,#f97316,#db2777)",display:"flex",alignItems:"center",justifyContent:"center",fontSize:9,fontWeight:700,color:"#fff",flexShrink:0}}>AI</div>
          <span style={{fontSize:10,color:"#f97316",fontWeight:600}}>mimo-v2.5-pro</span>
          {msg.streaming && <span style={{fontSize:11,color:"#60a5fa",animation:"pulse 1s infinite"}}>▌</span>}
        </div>
        <div style={{background:"#161b22",border:"1px solid #21262d",borderRadius:"4px 18px 18px 18px",padding:"10px 14px"}}>
          {/* tool events inline */}
          {msg.events.filter(e=>e.type!=="start"&&e.type!=="complete"&&e.type!=="error").map((ev,i)=>(
            <ToolCallBlock key={i} ev={ev} />
          ))}
          {/* final text */}
          {msg.content && (
            <div style={{fontSize:14,color:"#e2e8f0",lineHeight:1.7,marginTop:msg.events.length>0?8:0,whiteSpace:"pre-wrap",wordBreak:"break-word"}}
              dangerouslySetInnerHTML={{__html:renderMd(msg.content)}} />
          )}
          {msg.streaming && !msg.content && msg.events.length===0 && (
            <div style={{display:"flex",gap:4,padding:"4px 0"}}>
              {[0,1,2].map(i=><span key={i} style={{width:6,height:6,borderRadius:"50%",background:"#f97316",animation:`bounce 1s ${i*0.15}s infinite`}} />)}
            </div>
          )}
          {msg.error && <div style={{marginTop:8,fontSize:12,color:"#ef4444"}}>⚠ {msg.error}</div>}
        </div>
        <div style={{fontSize:10,color:"#374151",marginTop:2,marginLeft:4}}>{timeAgo(msg.ts)}</div>
      </div>
    </div>
  );
}

/* ── Quick commands ── */
const QUICK = [
  {label:"PM2 状态", cmd:"pm2 list"},
  {label:"api-server 日志", cmd:"pm2 logs api-server --lines 30 --nostream"},
  {label:"重建 api-server", cmd:"cd /root/Toolkit && pnpm --filter @workspace/api-server run build && pm2 restart api-server"},
  {label:"注册3个Outlook", cmd:"注册3个Outlook账号"},
  {label:"磁盘/内存", cmd:"df -h / && free -h"},
  {label:"ip2free 日志", cmd:"pm2 logs ip2free-monitor2 --lines 20 --nostream"},
];

/* ── Main component ── */
export default function AIAssistant() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState(genId);
  const [sessionTitle, setSessionTitle] = useState("新对话");
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sideTab, setSideTab] = useState<"sessions"|"quick">("sessions");
  const [metrics, setMetrics] = useState<{cpu?:number;mem?:{pct:number};cfPool?:{available:number}} | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => { fetchSessions(); fetchMetrics(); }, []);
  useEffect(() => { bottomRef.current?.scrollIntoView({behavior:"smooth"}); }, [msgs]);

  const fetchSessions = async () => {
    try { const r = await fetch(`${BASE}/api/claude-code/sessions`); setSessions(await r.json()); } catch {}
  };
  const fetchMetrics = async () => {
    try { const r = await fetch(`${BASE}/api/claude-code/server-metrics`); setMetrics(await r.json()); } catch {}
    setTimeout(fetchMetrics, 30000);
  };

  const saveSession = useCallback(async (messages: Msg[], title: string) => {
    try {
      await fetch(`${BASE}/api/claude-code/sessions`, {
        method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({ id: sessionId, title, messages: messages.map(m=>({role:m.role,content:m.content,events:m.events,ts:m.ts})) })
      });
      fetchSessions();
    } catch {}
  }, [sessionId]);

  const send = async (text = input) => {
    const msg = text.trim();
    if (!msg || loading) return;
    setInput("");
    setLoading(true);

    const userMsg: Msg = { id: genId(), role:"user", content:msg, events:[], ts:Date.now() };
    const aiMsg: Msg = { id: genId(), role:"assistant", content:"", events:[], ts:Date.now(), streaming:true };
    const newMsgs = [...msgs, userMsg, aiMsg];
    setMsgs(newMsgs);

    const history = msgs.map(m => ({ role:m.role, content:m.content, events:m.events }));
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    let finalContent = "";
    const liveEvents: AgentEv[] = [];

    try {
      const resp = await fetch(`${BASE}/api/claude-code/converse`, {
        method:"POST", signal:ctrl.signal,
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({ sessionId, history, message: msg })
      });
      const reader = resp.body!.getReader();
      const dec = new TextDecoder();
      let buf = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, {stream:true});
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          try {
            const ev: AgentEv = JSON.parse(line.slice(5).trim());
            if (ev.type === "complete") {
              finalContent = ev.text ?? finalContent;
            } else if (ev.type === "ai_response") {
              finalContent = (finalContent ? finalContent+"\n\n" : "") + (ev.text ?? "");
              liveEvents.push(ev);
            } else if (ev.type !== "start" && ev.type !== "error") {
              liveEvents.push(ev);
            }
            if (ev.type === "error") finalContent = `⚠ ${ev.text}`;
            // live update
            setMsgs(prev => {
              const last = {...prev[prev.length-1], content:finalContent, events:[...liveEvents], streaming:true};
              return [...prev.slice(0,-1), last];
            });
          } catch {}
        }
      }
    } catch(e: unknown) {
      if ((e as Error).name !== "AbortError") finalContent = `连接错误: ${String(e)}`;
    }

    const doneAiMsg: Msg = { ...aiMsg, content:finalContent, events:[...liveEvents], streaming:false, ts:Date.now() };
    const finalMsgs = [...msgs, userMsg, doneAiMsg];
    setMsgs(finalMsgs);
    setLoading(false);
    abortRef.current = null;

    const title = msgs.length === 0 ? msg.slice(0,30) : sessionTitle;
    setSessionTitle(title);
    saveSession(finalMsgs, title);
    setTimeout(() => inputRef.current?.focus(), 50);
  };

  const stopGeneration = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setLoading(false);
    setMsgs(prev => {
      const last = {...prev[prev.length-1], streaming:false};
      return [...prev.slice(0,-1), last];
    });
  };

  const loadSession = async (id: string) => {
    try {
      const r = await fetch(`${BASE}/api/claude-code/sessions/${id}`);
      const d = await r.json();
      const loaded: Msg[] = (d.messages ?? []).map((m: {role:Role;content:string;events?:AgentEv[];ts?:number}) => ({
        id: genId(), role:m.role, content:m.content, events:m.events??[], ts:m.ts??Date.now()
      }));
      setMsgs(loaded);
      setSessionId(id);
      setSessionTitle(d.title ?? "未命名");
    } catch {}
  };

  const newSession = () => {
    setMsgs([]); setSessionId(genId()); setSessionTitle("新对话"); setInput("");
    inputRef.current?.focus();
  };

  const delSession = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    await fetch(`${BASE}/api/claude-code/sessions/${id}`, { method:"DELETE" });
    setSessions(p => p.filter(s => s.id !== id));
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); }
  };

  return (
    <div style={{display:"flex",height:"calc(100vh - 60px)",gap:12,fontFamily:"system-ui,sans-serif",color:"#e2e8f0",minHeight:0}}>

      {/* ── Main chat ── */}
      <div style={{flex:1,display:"flex",flexDirection:"column",background:"#0d1117",border:"1px solid #21262d",borderRadius:16,overflow:"hidden",minWidth:0}}>
        {/* Header */}
        <div style={{padding:"12px 16px",borderBottom:"1px solid #21262d",display:"flex",alignItems:"center",gap:10,flexShrink:0}}>
          <div style={{width:28,height:28,borderRadius:8,background:"linear-gradient(135deg,#f97316,#db2777)",display:"flex",alignItems:"center",justifyContent:"center",fontSize:11,fontWeight:700,color:"#fff"}}>AI</div>
          <div style={{flex:1}}>
            <div style={{fontSize:13,fontWeight:600,color:"#fff"}}>{sessionTitle}</div>
            <div style={{fontSize:11,color:"#6b7280"}}>mimo-v2.5-pro · VPS 45.205.27.69 · 全权 root · Bash 无限制</div>
          </div>
          {metrics && (
            <div style={{display:"flex",gap:10,fontSize:11,color:"#6b7280"}}>
              <span>CPU {metrics.cpu?.toFixed(0)}%</span>
              <span>MEM {metrics.mem?.pct}%</span>
              <span style={{color:"#10b981"}}>CF {metrics.cfPool?.available}</span>
            </div>
          )}
          <button onClick={newSession} style={{padding:"4px 10px",background:"#21262d",border:"1px solid #30363d",borderRadius:8,color:"#9ca3af",cursor:"pointer",fontSize:12}}>+ 新对话</button>
        </div>

        {/* Messages */}
        <div style={{flex:1,overflowY:"auto",padding:"16px"}}>
          {msgs.length === 0 && (
            <div style={{textAlign:"center",paddingTop:60,color:"#374151"}}>
              <div style={{fontSize:48,marginBottom:12}}>🤖</div>
              <div style={{fontSize:16,color:"#6b7280",fontWeight:600}}>无限制 AI 助手</div>
              <div style={{fontSize:13,color:"#374151",marginTop:6,lineHeight:1.6}}>
                我可以直接在服务器上执行任何操作<br/>
                读取文件、写代码、执行命令、重启服务…<br/>
                <span style={{color:"#f97316"}}>像我一样工作，没有任何限制</span>
              </div>
              <div style={{display:"flex",flexWrap:"wrap",gap:8,justifyContent:"center",marginTop:20}}>
                {QUICK.map(q=>(
                  <button key={q.cmd} onClick={()=>void send(q.cmd)}
                    style={{padding:"6px 14px",background:"#161b22",border:"1px solid #30363d",borderRadius:20,color:"#9ca3af",cursor:"pointer",fontSize:12,transition:"all .2s"}}
                    onMouseEnter={e=>{(e.target as HTMLElement).style.borderColor="#f97316";(e.target as HTMLElement).style.color="#f97316";}}
                    onMouseLeave={e=>{(e.target as HTMLElement).style.borderColor="#30363d";(e.target as HTMLElement).style.color="#9ca3af";}}>
                    {q.label}
                  </button>
                ))}
              </div>
            </div>
          )}
          {msgs.map(msg => <MsgBubble key={msg.id} msg={msg} />)}
          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div style={{padding:"12px 16px",borderTop:"1px solid #21262d",flexShrink:0}}>
          <div style={{display:"flex",gap:8,alignItems:"flex-end"}}>
            <textarea ref={inputRef} value={input} onChange={e=>setInput(e.target.value)} onKeyDown={onKey}
              disabled={loading} rows={1} placeholder="问任何事、下达任何指令… (Enter 发送, Shift+Enter 换行)"
              style={{flex:1,resize:"none",background:"#161b22",border:"1px solid #30363d",borderRadius:12,padding:"10px 14px",
                fontSize:14,color:"#fff",fontFamily:"inherit",outline:"none",maxHeight:120,overflowY:"auto",lineHeight:1.5,
                transition:"border-color .2s",opacity:loading?0.6:1}}
              onFocus={e=>{e.target.style.borderColor="#f97316";}}
              onBlur={e=>{e.target.style.borderColor="#30363d";}} />
            {loading
              ? <button onClick={stopGeneration} style={{padding:"10px 16px",background:"#7f1d1d",border:"none",borderRadius:12,color:"#fca5a5",cursor:"pointer",fontSize:13,fontWeight:600,whiteSpace:"nowrap"}}>⬛ 停止</button>
              : <button onClick={()=>void send()} disabled={!input.trim()}
                  style={{padding:"10px 16px",background:input.trim()?"#f97316":"#1f2937",border:"none",borderRadius:12,
                    color:input.trim()?"#fff":"#4b5563",cursor:input.trim()?"pointer":"default",fontSize:13,fontWeight:600,whiteSpace:"nowrap",transition:"all .2s"}}>
                  发送
                </button>
            }
          </div>
          <div style={{fontSize:10,color:"#374151",marginTop:6,textAlign:"center"}}>
            AI 可直接执行 bash · 读写文件 · 重启服务 · 操作 Git · 无需确认
          </div>
        </div>
      </div>

      {/* ── Sidebar ── */}
      <div style={{width:240,display:"flex",flexDirection:"column",gap:8,flexShrink:0}}>
        {/* Server status */}
        <div style={{background:"#161b22",border:"1px solid #21262d",borderRadius:12,padding:"10px 12px",flexShrink:0}}>
          <div style={{fontSize:10,color:"#6b7280",fontWeight:600,marginBottom:6,textTransform:"uppercase",letterSpacing:1}}>服务器状态</div>
          <div style={{display:"flex",gap:8,flexWrap:"wrap"}}>
            <span style={{fontSize:11,color:"#10b981"}}>● VPS 在线</span>
            <span style={{fontSize:11,color:"#f97316"}}>● Claude Code</span>
            <span style={{fontSize:11,color:"#60a5fa"}}>● Bash 工具</span>
          </div>
          {metrics && <div style={{marginTop:6,fontSize:11,color:"#6b7280"}}>
            <div>CPU: {metrics.cpu?.toFixed(1)}% | MEM: {metrics.mem?.pct}%</div>
            <div>CF IP池: {metrics.cfPool?.available} 可用</div>
          </div>}
        </div>

        {/* Tabs */}
        <div style={{background:"#161b22",border:"1px solid #21262d",borderRadius:12,overflow:"hidden",flex:1,display:"flex",flexDirection:"column",minHeight:0}}>
          <div style={{display:"flex",borderBottom:"1px solid #21262d",flexShrink:0}}>
            {(["sessions","quick"] as const).map(t=>(
              <button key={t} onClick={()=>setSideTab(t)}
                style={{flex:1,padding:"8px 4px",fontSize:11,fontWeight:600,background:"transparent",border:"none",
                  color:sideTab===t?"#fff":"#6b7280",borderBottom:sideTab===t?"2px solid #f97316":"2px solid transparent",cursor:"pointer",transition:"all .2s"}}>
                {t==="sessions"?"💾 会话":"⚡ 快捷"}
              </button>
            ))}
          </div>

          {sideTab === "sessions" && (
            <div style={{flex:1,overflow:"hidden",display:"flex",flexDirection:"column",padding:8,gap:6}}>
              <button onClick={newSession} style={{width:"100%",padding:"6px",background:"#f97316",border:"none",borderRadius:8,color:"#fff",cursor:"pointer",fontSize:12,fontWeight:700}}>+ 新对话</button>
              <div style={{flex:1,overflowY:"auto",display:"flex",flexDirection:"column",gap:4}}>
                {sessions.map(s=>(
                  <div key={s.id} onClick={()=>loadSession(s.id)}
                    style={{padding:"8px 10px",borderRadius:8,cursor:"pointer",border:`1px solid ${s.id===sessionId?"#f97316":"#30363d"}`,
                      background:s.id===sessionId?"rgba(249,115,22,0.1)":"transparent",transition:"all .15s",position:"relative"}}
                    onMouseEnter={e=>{if(s.id!==sessionId)(e.currentTarget as HTMLElement).style.borderColor="#6b7280";}}
                    onMouseLeave={e=>{if(s.id!==sessionId)(e.currentTarget as HTMLElement).style.borderColor="#30363d";}}>
                    <div style={{fontSize:11,fontWeight:600,color:"#e2e8f0",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",paddingRight:16}}>{s.title}</div>
                    <div style={{fontSize:10,color:"#6b7280",marginTop:2}}>{s.msgCount}条 · {timeAgo(s.updated_at)}</div>
                    <button onClick={e=>void delSession(s.id,e)}
                      style={{position:"absolute",top:6,right:6,background:"none",border:"none",color:"#6b7280",cursor:"pointer",fontSize:12,padding:2,opacity:0.6}}
                      onMouseEnter={e=>{(e.target as HTMLElement).style.color="#ef4444";(e.target as HTMLElement).style.opacity="1";}}
                      onMouseLeave={e=>{(e.target as HTMLElement).style.color="#6b7280";(e.target as HTMLElement).style.opacity="0.6";}}>✕</button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {sideTab === "quick" && (
            <div style={{padding:8,overflowY:"auto",flex:1,display:"flex",flexDirection:"column",gap:4}}>
              <div style={{fontSize:10,color:"#6b7280",marginBottom:4}}>点击即发送，AI 直接执行</div>
              {QUICK.map(q=>(
                <button key={q.cmd} onClick={()=>void send(q.cmd)} disabled={loading}
                  style={{textAlign:"left",padding:"8px 10px",background:"#0d1117",border:"1px solid #30363d",borderRadius:8,color:"#e2e8f0",cursor:"pointer",fontSize:11,transition:"all .15s",opacity:loading?0.5:1}}
                  onMouseEnter={e=>{(e.currentTarget as HTMLElement).style.borderColor="#f97316";}}
                  onMouseLeave={e=>{(e.currentTarget as HTMLElement).style.borderColor="#30363d";}}>
                  <div style={{fontWeight:600,color:"#f97316"}}>{q.label}</div>
                  <div style={{fontSize:10,color:"#6b7280",marginTop:2,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{q.cmd.slice(0,40)}</div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      <style>{`
        .ic { background:#1e2a3a; color:#79c0ff; padding:1px 5px; border-radius:4px; font-family:monospace; font-size:.85em; }
        .mh { display:block; font-weight:700; color:#e2e8f0; margin:6px 0 3px; }
        .ml { display:block; margin:2px 0; }
        @keyframes bounce { 0%,60%,100%{transform:translateY(0)} 30%{transform:translateY(-6px)} }
        @keyframes spin { to{transform:rotate(360deg)} }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
      `}</style>
    </div>
  );
}
