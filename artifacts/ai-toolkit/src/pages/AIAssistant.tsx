import { useState, useEffect, useRef, useCallback } from "react";

  // ─── Types ───────────────────────────────────────────────────────────────────
  interface Msg { role: "user"|"assistant"; content: string; ts: number; }
  interface Session { id: string; title: string; msgCount: number; updated_at: number; model: string; }
  interface HistoryEntry { id: string; ts: number; cmd: string; stdout: string; code: number; duration: number; source?: string; }
  interface Proc { id: number; name: string; status: string; cpu: number; mem: number; restarts: number; }
  interface Metrics { cpu: number; mem:{total:number;used:number;pct:number}; disk:{fs:string;used:string;avail:string;pct:string}[]; processes:Proc[]; cfPool:{available:number;used_total:number;banned_total:number}; git:string; ts: number; }
  interface OutlookJob { id:string; status:string; accountCount?:number; lastLog?:{message?:string}; }
  interface AgentEvent { type:string; text?:string; cmd?:string; stdout?:string; ai_text?:string; toolId?:string; status?:string; job?:OutlookJob; accountCount?:number; lastLog?:string; jobId?:string; accounts?:{email:string;password:string}[]; code?:number; }

  const BASE = "";

  // ─── Helpers ─────────────────────────────────────────────────────────────────
  function timeAgo(ts: number) {
    const s = Math.floor((Date.now()-ts)/1000);
    if (s<60) return `${s}s前`;
    if (s<3600) return `${Math.floor(s/60)}m前`;
    return `${Math.floor(s/3600)}h前`;
  }
  function genId() { return Date.now().toString(36)+Math.random().toString(36).slice(2,6); }
  function fmtBytes(mb: number) { return mb>1024 ? `${(mb/1024).toFixed(1)}GB` : `${mb}MB`; }

  // ─── GaugeMini ────────────────────────────────────────────────────────────────
  function Gauge({val,label,warn=70,danger=85}:{val:number;label:string;warn?:number;danger?:number}) {
    const col = val>=danger?"#ef4444":val>=warn?"#f59e0b":"#10b981";
    return (
      <div style={{textAlign:"center",minWidth:64}}>
        <svg width="64" height="64" viewBox="0 0 64 64">
          <circle cx="32" cy="32" r="26" fill="none" stroke="#1e293b" strokeWidth="7"/>
          <circle cx="32" cy="32" r="26" fill="none" stroke={col} strokeWidth="7"
            strokeDasharray={2*Math.PI*26} strokeDashoffset={2*Math.PI*26*(1-val/100)}
            strokeLinecap="round" style={{transition:"stroke-dashoffset 0.5s",transform:"rotate(-90deg)",transformOrigin:"center"}}/>
          <text x="32" y="37" textAnchor="middle" fill="#f1f5f9" fontSize="13" fontWeight="bold">{Math.round(val)}%</text>
        </svg>
        <div style={{color:"#94a3b8",fontSize:11,marginTop:-4}}>{label}</div>
      </div>
    );
  }

  // ─── StatusBadge ──────────────────────────────────────────────────────────────
  function Badge({status}:{status:string}) {
    const colors: Record<string,string> = {online:"#10b981",running:"#10b981",done:"#10b981",stopped:"#ef4444",error:"#ef4444",errored:"#ef4444",waiting:"#f59e0b",launching:"#f59e0b"};
    return <span style={{background:colors[status]??"#64748b",color:"#fff",borderRadius:4,padding:"1px 6px",fontSize:11,fontWeight:600}}>{status}</span>;
  }

  // ─── Main Component ──────────────────────────────────────────────────────────
  export default function AIAssistant() {
    const [tab, setTab] = useState<"hub"|"agent"|"chat">("hub");

    return (
      <div style={{height:"100vh",display:"flex",flexDirection:"column",background:"#0f172a",color:"#f1f5f9",fontFamily:"'SF Mono',Consolas,monospace",overflow:"hidden"}}>
        {/* Header */}
        <div style={{display:"flex",alignItems:"center",gap:12,padding:"10px 16px",background:"#1e293b",borderBottom:"1px solid #334155",flexShrink:0}}>
          <span style={{fontSize:18,fontWeight:700,letterSpacing:1}}>🤖 AI 全权运维</span>
          <span style={{fontSize:11,color:"#64748b",flex:1}}>root@45.205.27.69 · mimo-v2.5-pro · 无限制</span>
          {["hub","agent","chat"].map(t=>(
            <button key={t} onClick={()=>setTab(t as "hub"|"agent"|"chat")} style={{
              padding:"4px 14px",borderRadius:6,border:"none",cursor:"pointer",fontSize:13,fontWeight:600,
              background:tab===t?"#3b82f6":"#334155",color:tab===t?"#fff":"#94a3b8",transition:"all 0.15s"
            }}>
              {t==="hub"?"🧭 任务中枢":t==="agent"?"🤖 Agent":"💬 AI对话"}
            </button>
          ))}
        </div>
        {/* Body */}
        <div style={{flex:1,overflow:"hidden",display:"flex"}}>
          {tab==="hub" && <TaskHub/>}
          {tab==="agent" && <AgentMode/>}
          {tab==="chat" && <ChatMode/>}
        </div>
      </div>
    );
  }

  // ═══════════════════════════════════════════════════════════════════════════════
  // TASK HUB
  // ═══════════════════════════════════════════════════════════════════════════════
  function TaskHub() {
    const [metrics, setMetrics] = useState<Metrics|null>(null);
    const [subTab, setSubTab] = useState<"quick"|"outlook"|"pm2"|"git"|"history">("quick");
    const [loading, setLoading] = useState(false);
    const [output, setOutput] = useState("");
    const [outlookJobs, setOutlookJobs] = useState<OutlookJob[]>([]);
    const [history, setHistory] = useState<HistoryEntry[]>([]);

    const fetchMetrics = useCallback(async () => {
      try { const r = await fetch(`${BASE}/api/claude-code/server-metrics`); const d = await r.json(); setMetrics(d); } catch {}
    }, []);

    useEffect(() => { fetchMetrics(); const t = setInterval(fetchMetrics, 15000); return ()=>clearInterval(t); }, [fetchMetrics]);

    useEffect(()=>{
      if (subTab==="outlook") fetchOutlookJobs();
      if (subTab==="history") fetchHistory();
    }, [subTab]);

    const fetchOutlookJobs = async () => {
      try { const r = await fetch(`${BASE}/api/claude-code/outlook-jobs`); setOutlookJobs(await r.json()); } catch {}
    };
    const fetchHistory = async () => {
      try { const r = await fetch(`${BASE}/api/claude-code/history`); setHistory(await r.json()); } catch {}
    };

    const runAction = async (label: string, cmd: string) => {
      setLoading(true); setOutput(`▶ ${label}\n`);
      const es = new EventSource(`${BASE}/api/claude-code/exec-stream`);
      // exec-stream is POST, use fetch with ReadableStream
      es.close();
      try {
        const res = await fetch(`${BASE}/api/claude-code/exec-stream`, {
          method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({cmd})
        });
        const reader = res.body!.getReader(); const dec = new TextDecoder();
        let buf="";
        while(true) {
          const {done,value} = await reader.read(); if(done) break;
          buf += dec.decode(value,{stream:true});
          const lines = buf.split("\n"); buf = lines.pop()??"";
          for (const line of lines) {
            if (!line.startsWith("data:")) continue;
            try { const e=JSON.parse(line.slice(5).trim());
              if (e.type==="stdout"||e.type==="stderr") setOutput(p=>p+e.text);
              if (e.type==="done") setOutput(p=>p+`\n✓ 完成 (exit ${e.code}, ${e.duration}ms)`);
            } catch {}
          }
        }
      } catch (e) { setOutput(p=>p+String(e)); }
      setLoading(false);
    };

    const quickActions: [string,string,string][] = [
      ["🔵 注册1个Outlook","outlook-1","#1d4ed8"],
      ["🔵 注册3个Outlook","outlook-3","#1d4ed8"],
      ["🔵 注册5个Outlook","outlook-5","#1d4ed8"],
      ["🔵 注册10个Outlook","outlook-10","#1d4ed8"],
      ["🔨 构建重启API","build-api","#7c3aed"],
      ["📝 Git提交推送","git-push","#0369a1"],
      ["📋 PM2进程列表","pm2-list","#064e3b"],
      ["📊 CF IP池状态","cf-pool","#064e3b"],
      ["🐍 pm2日志 api-server","pm2-logs","#92400e"],
      ["♻️ 重启 api-server","restart-api","#7c2d12"],
      ["♻️ 重启前端","restart-fe","#7c2d12"],
      ["🔍 执行历史","history","#374151"],
    ];

    const handleQuick = async (id: string) => {
      if (id.startsWith("outlook-")) {
        const n = id.split("-")[1];
        setSubTab("outlook");
        return;
      }
      const cmds: Record<string,string> = {
        "build-api": "cd /root/Toolkit && pnpm --filter @workspace/api-server run build && pm2 restart api-server 2>&1 | tail -5",
        "git-push": `cd /root/Toolkit && git add -A && git status --short | head -5 && git commit -m "AI Agent: auto sync ${new Date().toISOString().slice(0,16)}" 2>&1 | tail -3`,
        "pm2-list": "pm2 list 2>&1",
        "cf-pool": "curl -s http://localhost:8081/api/tools/cf-pool/status | python3 -m json.tool 2>&1 | head -20",
        "pm2-logs": "pm2 logs api-server --lines 30 --nostream 2>&1 | tail -30",
        "restart-api": "pm2 restart api-server 2>&1",
        "restart-fe": "pm2 restart ai-toolkit 2>&1 || pm2 restart 1 2>&1",
        "history": "cat /root/Toolkit/.ai-sessions/exec-history.json 2>/dev/null | python3 -c \"import sys,json;h=json.load(sys.stdin);[print(e.get('cmd','?')[:60],'-',e.get('code',0)) for e in h[-10:]]\"",
      };
      if (cmds[id]) await runAction(id, cmds[id]);
      else setSubTab("outlook");
    };

    return (
      <div style={{flex:1,display:"flex",overflow:"hidden"}}>
        {/* Left: Metrics Panel */}
        <div style={{width:260,background:"#1e293b",borderRight:"1px solid #334155",padding:14,overflowY:"auto",flexShrink:0}}>
          <div style={{fontSize:13,fontWeight:700,marginBottom:10,color:"#60a5fa"}}>📡 服务器状态</div>
          {metrics ? (
            <>
              <div style={{display:"flex",gap:8,justifyContent:"space-around",marginBottom:12}}>
                <Gauge val={metrics.cpu} label="CPU"/>
                <Gauge val={metrics.mem.pct} label="内存" warn={80} danger={90}/>
              </div>
              {/* Disk */}
              {metrics.disk?.map((d,i)=>(
                <div key={i} style={{marginBottom:6}}>
                  <div style={{display:"flex",justifyContent:"space-between",fontSize:11,color:"#94a3b8"}}>
                    <span>💾 {d.fs}</span><span>{d.used}/{d.avail} ({d.pct})</span>
                  </div>
                  <div style={{height:4,background:"#334155",borderRadius:2,marginTop:2}}>
                    <div style={{height:"100%",width:d.pct,background:parseInt(d.pct||"0")>80?"#ef4444":"#3b82f6",borderRadius:2}}/>
                  </div>
                </div>
              ))}
              {/* CF Pool */}
              <div style={{marginTop:10,padding:8,background:"#0f172a",borderRadius:6}}>
                <div style={{fontSize:11,color:"#60a5fa",fontWeight:600,marginBottom:4}}>☁️ CF IP 池</div>
                <div style={{fontSize:12,color:"#94a3b8"}}>可用: <span style={{color:"#10b981",fontWeight:700}}>{metrics.cfPool?.available}</span></div>
                <div style={{fontSize:12,color:"#94a3b8"}}>已用: {metrics.cfPool?.used_total} · 封禁: {metrics.cfPool?.banned_total}</div>
              </div>
              {/* Git */}
              <div style={{marginTop:8,padding:8,background:"#0f172a",borderRadius:6}}>
                <div style={{fontSize:11,color:"#a78bfa",fontWeight:600,marginBottom:2}}>📁 Git</div>
                <pre style={{fontSize:10,color:"#94a3b8",margin:0,whiteSpace:"pre-wrap",wordBreak:"break-all"}}>{metrics.git?.slice(0,200)||"clean"}</pre>
              </div>
              {/* Processes summary */}
              <div style={{marginTop:8,padding:8,background:"#0f172a",borderRadius:6}}>
                <div style={{fontSize:11,color:"#fbbf24",fontWeight:600,marginBottom:4}}>🔧 PM2 进程</div>
                {metrics.processes?.map((p)=>(
                  <div key={p.id} style={{display:"flex",alignItems:"center",gap:4,marginBottom:2}}>
                    <Badge status={p.status}/>
                    <span style={{fontSize:11,flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{p.name}</span>
                    <span style={{fontSize:10,color:"#64748b"}}>{p.cpu}%</span>
                  </div>
                ))}
              </div>
              <div style={{fontSize:10,color:"#475569",marginTop:8,textAlign:"right"}}>更新: {new Date(metrics.ts).toLocaleTimeString()}</div>
            </>
          ) : (
            <div style={{color:"#64748b",textAlign:"center",paddingTop:40}}>加载中…</div>
          )}
          <button onClick={fetchMetrics} style={{width:"100%",marginTop:8,padding:"5px 0",background:"#1e3a5f",border:"1px solid #334155",borderRadius:4,color:"#60a5fa",fontSize:12,cursor:"pointer"}}>🔄 刷新</button>
        </div>

        {/* Right: Action Panel */}
        <div style={{flex:1,display:"flex",flexDirection:"column",overflow:"hidden"}}>
          {/* SubTabs */}
          <div style={{display:"flex",gap:4,padding:"8px 12px",background:"#1e293b",borderBottom:"1px solid #334155",flexShrink:0}}>
            {(["quick","outlook","pm2","git","history"] as const).map(t=>(
              <button key={t} onClick={()=>setSubTab(t)} style={{
                padding:"3px 10px",borderRadius:4,border:"none",cursor:"pointer",fontSize:12,
                background:subTab===t?"#3b82f6":"#334155",color:subTab===t?"#fff":"#94a3b8"
              }}>
                {t==="quick"?"⚡ 快捷操作":t==="outlook"?"📧 Outlook注册":t==="pm2"?"⚙️ PM2":t==="git"?"📁 Git":"🕐 历史"}
              </button>
            ))}
          </div>

          <div style={{flex:1,display:"flex",overflow:"hidden"}}>
            {/* Sub panels */}
            {subTab==="quick" && (
              <div style={{flex:1,display:"flex",flexDirection:"column",overflow:"hidden"}}>
                <div style={{padding:12,display:"grid",gridTemplateColumns:"1fr 1fr 1fr",gap:8,flexShrink:0}}>
                  {quickActions.map(([label,id,color])=>(
                    <button key={id} onClick={()=>handleQuick(id)} disabled={loading}
                      style={{padding:"8px 10px",background:color,border:"none",borderRadius:6,color:"#fff",fontSize:12,cursor:"pointer",textAlign:"left",opacity:loading?0.6:1}}>
                      {label}
                    </button>
                  ))}
                </div>
                {output && (
                  <div style={{flex:1,overflow:"hidden",display:"flex",flexDirection:"column",margin:"0 12px 12px"}}>
                    <div style={{fontSize:11,color:"#64748b",marginBottom:4}}>输出：</div>
                    <pre style={{flex:1,overflow:"auto",background:"#0f172a",border:"1px solid #334155",borderRadius:6,padding:10,margin:0,fontSize:12,color:"#e2e8f0",whiteSpace:"pre-wrap"}}>
                      {output}
                    </pre>
                  </div>
                )}
              </div>
            )}
            {subTab==="outlook" && <OutlookPanel jobs={outlookJobs} onRefresh={fetchOutlookJobs}/>}
            {subTab==="pm2" && <PM2Panel procs={metrics?.processes??[]} onAction={runAction}/>}
            {subTab==="git" && <GitPanel onAction={runAction} output={output}/>}
            {subTab==="history" && <HistoryPanel history={history} onRefresh={fetchHistory}/>}
          </div>
        </div>
      </div>
    );
  }

  // ─── Outlook Panel ────────────────────────────────────────────────────────────
  function OutlookPanel({jobs, onRefresh}:{jobs:OutlookJob[];onRefresh:()=>void}) {
    const [count, setCount] = useState(3);
    const [engine, setEngine] = useState("patchright");
    const [events, setEvents] = useState<AgentEvent[]>([]);
    const [running, setRunning] = useState(false);
    const [activeJob, setActiveJob] = useState<AgentEvent|null>(null);
    const eventRef = useRef<HTMLDivElement>(null);

    const startRegister = async () => {
      setRunning(true); setEvents([]); setActiveJob(null);
      try {
        const res = await fetch(`${BASE}/api/claude-code/outlook-register`, {
          method:"POST", headers:{"Content-Type":"application/json"},
          body: JSON.stringify({count,engine,proxyMode:"cf",headless:true,wait:11,retries:2})
        });
        const reader = res.body!.getReader(); const dec = new TextDecoder(); let buf="";
        while(true) {
          const {done,value} = await reader.read(); if(done) break;
          buf+=dec.decode(value,{stream:true});
          const lines=buf.split("\n"); buf=lines.pop()??"";
          for (const line of lines) {
            if(!line.startsWith("data:")) continue;
            try {
              const e:AgentEvent = JSON.parse(line.slice(5).trim());
              setEvents(p=>[...p,e]);
              if(e.type==="progress"||e.type==="complete"||e.type==="started") setActiveJob(e);
              if(eventRef.current) eventRef.current.scrollTop=eventRef.current.scrollHeight;
            } catch {}
          }
        }
      } catch(e) { setEvents(p=>[...p,{type:"error",text:String(e)}]); }
      setRunning(false); onRefresh();
    };

    const getStatusColor = (status?: string) => status==="done"?"#10b981":status==="running"?"#f59e0b":status==="error"?"#ef4444":"#64748b";

    return (
      <div style={{flex:1,display:"flex",flexDirection:"column",overflow:"hidden",padding:12,gap:10}}>
        {/* Controls */}
        <div style={{display:"flex",gap:8,alignItems:"center",flexShrink:0,background:"#1e293b",padding:10,borderRadius:8}}>
          <span style={{fontSize:13,fontWeight:700,color:"#60a5fa"}}>📧 Outlook 批量注册</span>
          <select value={engine} onChange={e=>setEngine(e.target.value)} style={{background:"#334155",color:"#f1f5f9",border:"none",borderRadius:4,padding:"3px 6px",fontSize:12}}>
            <option value="patchright">patchright</option>
            <option value="playwright">playwright</option>
          </select>
          <label style={{fontSize:12,color:"#94a3b8"}}>数量:</label>
          {[1,3,5,10].map(n=>(
            <button key={n} onClick={()=>setCount(n)} style={{
              padding:"3px 10px",borderRadius:4,border:"none",cursor:"pointer",fontSize:13,fontWeight:count===n?700:400,
              background:count===n?"#1d4ed8":"#334155",color:"#fff"
            }}>{n}</button>
          ))}
          <button onClick={startRegister} disabled={running}
            style={{marginLeft:"auto",padding:"5px 18px",background:running?"#374151":"#16a34a",border:"none",borderRadius:6,color:"#fff",cursor:"pointer",fontSize:13,fontWeight:700}}>
            {running?"⏳ 注册中…":"▶ 开始注册"}
          </button>
        </div>

        <div style={{flex:1,display:"flex",gap:10,overflow:"hidden"}}>
          {/* Live progress */}
          <div style={{flex:1,display:"flex",flexDirection:"column",gap:8,overflow:"hidden"}}>
            {activeJob && (
              <div style={{background:"#0f172a",border:`1px solid ${getStatusColor(activeJob.status)}`,borderRadius:8,padding:10,flexShrink:0}}>
                <div style={{display:"flex",gap:8,alignItems:"center"}}>
                  <span style={{fontSize:20}}>{activeJob.status==="done"?"✅":activeJob.status==="error"?"❌":"⏳"}</span>
                  <div>
                    <div style={{fontSize:13,fontWeight:700,color:getStatusColor(activeJob.status)}}>
                      {activeJob.status==="done"?"注册完成":activeJob.status==="error"?"注册失败":"注册进行中…"}
                    </div>
                    {activeJob.jobId && <div style={{fontSize:11,color:"#64748b"}}>Job: {activeJob.jobId}</div>}
                  </div>
                  <div style={{marginLeft:"auto",fontSize:24,fontWeight:700,color:"#10b981"}}>
                    {activeJob.accountCount??0}<span style={{fontSize:13,color:"#64748b"}}>/{count}</span>
                  </div>
                </div>
                {activeJob.lastLog && <div style={{marginTop:6,fontSize:12,color:"#94a3b8"}}>{activeJob.lastLog}</div>}
                {activeJob.accounts && activeJob.accounts.length>0 && (
                  <div style={{marginTop:8}}>
                    <div style={{fontSize:11,color:"#10b981",fontWeight:600,marginBottom:4}}>✅ 注册成功账号：</div>
                    {activeJob.accounts.map((a,i)=>(
                      <div key={i} style={{fontSize:12,color:"#e2e8f0",padding:"2px 4px",background:"#1e293b",borderRadius:3,marginBottom:2}}>
                        {a.email} <span style={{color:"#64748b"}}>/ {a.password}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
            <div ref={eventRef} style={{flex:1,overflow:"auto",background:"#0f172a",border:"1px solid #334155",borderRadius:6,padding:8}}>
              <div style={{fontSize:11,color:"#64748b",marginBottom:4}}>实时日志：</div>
              {events.map((e,i)=>(
                <div key={i} style={{fontSize:11,color:e.type==="error"?"#ef4444":e.type==="progress"?"#fbbf24":e.type==="complete"?"#10b981":"#94a3b8",marginBottom:1}}>
                  {e.type==="status" && `📡 ${e.text}`}
                  {e.type==="started" && `🚀 ${e.message ?? "任务已启动"}`}
                  {e.type==="progress" && `⏳ [${e.status}] ${e.accountCount??0} 个账号 · ${e.lastLog??""}`}
                  {e.type==="complete" && `✅ 完成：${e.accountCount??0} 个账号`}
                  {e.type==="error" && `❌ ${e.text}`}
                </div>
              ))}
              {running && <div style={{fontSize:11,color:"#f59e0b"}}>● 等待中…</div>}
            </div>
          </div>
          {/* Job history */}
          <div style={{width:260,background:"#0f172a",border:"1px solid #334155",borderRadius:6,padding:8,overflowY:"auto",flexShrink:0}}>
            <div style={{display:"flex",justifyContent:"space-between",marginBottom:6}}>
              <span style={{fontSize:11,color:"#60a5fa",fontWeight:600}}>近期任务</span>
              <button onClick={onRefresh} style={{background:"none",border:"none",color:"#64748b",cursor:"pointer",fontSize:11}}>🔄</button>
            </div>
            {jobs.length===0 && <div style={{fontSize:11,color:"#475569"}}>暂无任务</div>}
            {jobs.map((j)=>(
              <div key={j.id} style={{marginBottom:6,padding:6,background:"#1e293b",borderRadius:4,borderLeft:`3px solid ${getStatusColor(j.status)}`}}>
                <div style={{display:"flex",justifyContent:"space-between"}}>
                  <Badge status={j.status}/>
                  <span style={{fontSize:12,fontWeight:700,color:"#10b981"}}>{j.accountCount??0}个</span>
                </div>
                <div style={{fontSize:10,color:"#64748b",marginTop:2}}>{j.id.slice(0,25)}</div>
                <div style={{fontSize:10,color:"#94a3b8",marginTop:1}}>{j.lastLog?.message?.slice(0,40)??"…"}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  // ─── PM2 Panel ────────────────────────────────────────────────────────────────
  function PM2Panel({procs, onAction}:{procs:Proc[];onAction:(label:string,cmd:string)=>void}) {
    return (
      <div style={{flex:1,overflowY:"auto",padding:12}}>
        <div style={{fontSize:13,fontWeight:700,color:"#fbbf24",marginBottom:10}}>⚙️ PM2 进程管理</div>
        <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
          <thead>
            <tr style={{color:"#64748b",textAlign:"left"}}>
              {["ID","名称","状态","CPU","内存","重启","操作"].map(h=>(
                <th key={h} style={{padding:"4px 8px",borderBottom:"1px solid #334155"}}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {procs.map(p=>(
              <tr key={p.id} style={{borderBottom:"1px solid #1e293b"}}>
                <td style={{padding:"5px 8px",color:"#64748b"}}>{p.id}</td>
                <td style={{padding:"5px 8px",fontWeight:600}}>{p.name}</td>
                <td style={{padding:"5px 8px"}}><Badge status={p.status}/></td>
                <td style={{padding:"5px 8px",color:p.cpu>50?"#ef4444":"#94a3b8"}}>{p.cpu}%</td>
                <td style={{padding:"5px 8px",color:"#94a3b8"}}>{fmtBytes(p.mem)}</td>
                <td style={{padding:"5px 8px",color:p.restarts>5?"#f59e0b":"#94a3b8"}}>{p.restarts}</td>
                <td style={{padding:"5px 8px",display:"flex",gap:4}}>
                  <button onClick={()=>onAction(`restart ${p.name}`,`pm2 restart ${p.name} 2>&1`)} style={{padding:"2px 8px",background:"#7c3aed",border:"none",borderRadius:3,color:"#fff",cursor:"pointer",fontSize:11}}>重启</button>
                  <button onClick={()=>onAction(`logs ${p.name}`,`pm2 logs ${p.name} --lines 30 --nostream 2>&1 | tail -30`)} style={{padding:"2px 8px",background:"#0369a1",border:"none",borderRadius:3,color:"#fff",cursor:"pointer",fontSize:11}}>日志</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  // ─── Git Panel ────────────────────────────────────────────────────────────────
  function GitPanel({onAction,output}:{onAction:(label:string,cmd:string)=>void;output:string}) {
    const [msg, setMsg] = useState("AI Agent: auto sync");
    const actions = [
      ["📋 状态","git --no-optional-locks status 2>&1"],
      ["📜 日志","git --no-optional-locks log --oneline -15 2>&1"],
      ["🔍 Diff","git --no-optional-locks diff --stat HEAD 2>&1 | head -30"],
      ["⬇️ Pull","cd /root/Toolkit && git stash 2>/dev/null; git pull origin main 2>&1 | tail -8"],
    ];
    return (
      <div style={{flex:1,display:"flex",flexDirection:"column",padding:12,gap:8,overflow:"auto"}}>
        <div style={{display:"flex",gap:6,flexWrap:"wrap",flexShrink:0}}>
          {actions.map(([label,cmd])=>(
            <button key={label} onClick={()=>onAction(label,cmd)} style={{padding:"5px 12px",background:"#0369a1",border:"none",borderRadius:5,color:"#fff",cursor:"pointer",fontSize:12}}>{label}</button>
          ))}
        </div>
        <div style={{display:"flex",gap:6,flexShrink:0}}>
          <input value={msg} onChange={e=>setMsg(e.target.value)} style={{flex:1,background:"#1e293b",border:"1px solid #334155",borderRadius:4,color:"#f1f5f9",padding:"4px 8px",fontSize:12}}/>
          <button onClick={()=>onAction("Git Commit+Push",`cd /root/Toolkit && git add -A && git commit -m "${msg}" && git push 2>&1 | tail -5`)}
            style={{padding:"4px 14px",background:"#16a34a",border:"none",borderRadius:4,color:"#fff",cursor:"pointer",fontSize:12,fontWeight:700}}>提交推送</button>
        </div>
        {output && <pre style={{flex:1,overflow:"auto",background:"#0f172a",border:"1px solid #334155",borderRadius:6,padding:10,margin:0,fontSize:12,color:"#e2e8f0",whiteSpace:"pre-wrap"}}>{output}</pre>}
      </div>
    );
  }

  // ─── History Panel ────────────────────────────────────────────────────────────
  function HistoryPanel({history,onRefresh}:{history:HistoryEntry[];onRefresh:()=>void}) {
    const [filter, setFilter] = useState<"all"|"ok"|"fail">("all");
    const filtered = history.filter(h=>filter==="all"?true:filter==="ok"?h.code===0:h.code!==0).slice(0,50);
    return (
      <div style={{flex:1,display:"flex",flexDirection:"column",overflow:"hidden",padding:12}}>
        <div style={{display:"flex",gap:8,marginBottom:8,flexShrink:0}}>
          {(["all","ok","fail"] as const).map(f=>(
            <button key={f} onClick={()=>setFilter(f)} style={{padding:"3px 10px",background:filter===f?"#3b82f6":"#334155",border:"none",borderRadius:4,color:"#fff",cursor:"pointer",fontSize:12}}>
              {f==="all"?"全部":f==="ok"?"✅ 成功":"❌ 失败"}
            </button>
          ))}
          <button onClick={onRefresh} style={{padding:"3px 10px",background:"#334155",border:"none",borderRadius:4,color:"#94a3b8",cursor:"pointer",fontSize:12,marginLeft:"auto"}}>🔄</button>
        </div>
        <div style={{flex:1,overflowY:"auto"}}>
          {filtered.map((h)=>(
            <div key={h.id} style={{marginBottom:4,padding:6,background:"#1e293b",borderRadius:4,borderLeft:`3px solid ${h.code===0?"#10b981":"#ef4444"}`}}>
              <div style={{display:"flex",justifyContent:"space-between",marginBottom:2}}>
                <code style={{fontSize:12,color:"#e2e8f0",flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{h.cmd.slice(0,80)}</code>
                <span style={{fontSize:10,color:"#64748b",marginLeft:8,flexShrink:0}}>{timeAgo(h.ts)}</span>
              </div>
              {h.stdout && <pre style={{fontSize:11,color:"#94a3b8",margin:0,maxHeight:40,overflow:"hidden",whiteSpace:"pre-wrap"}}>{h.stdout.slice(0,120)}</pre>}
            </div>
          ))}
        </div>
      </div>
    );
  }

  // ═══════════════════════════════════════════════════════════════════════════════
  // AGENT MODE — Real Claude -p --allowedTools Bash
  // ═══════════════════════════════════════════════════════════════════════════════
  function AgentMode() {
    const [task, setTask] = useState("");
    const [events, setEvents] = useState<AgentEvent[]>([]);
    const [running, setRunning] = useState(false);
    const eventRef = useRef<HTMLDivElement>(null);

    const examples = [
      "注册3个Outlook账号，等待完成后告诉我账号信息",
      "检查所有PM2进程状态，重启有问题的进程",
      "查看api-server最近50行日志，分析是否有错误",
      "检查CF IP池状态，如果可用IP少于100则补充",
      "构建重启api-server，验证/api/healthz返回正常",
      "查看最新Outlook注册任务的账号列表",
      "git提交当前所有改动并推送到GitHub",
      "分析注册失败原因并尝试修复",
    ];

    const runAgent = async () => {
      if (!task.trim() || running) return;
      setRunning(true); setEvents([]);
      try {
        const res = await fetch(`${BASE}/api/claude-code/agent`, {
          method:"POST", headers:{"Content-Type":"application/json"},
          body: JSON.stringify({task})
        });
        const reader = res.body!.getReader(); const dec = new TextDecoder(); let buf="";
        while(true) {
          const {done,value} = await reader.read(); if(done) break;
          buf+=dec.decode(value,{stream:true});
          const lines=buf.split("\n"); buf=lines.pop()??"";
          for (const line of lines) {
            if(!line.startsWith("data:")) continue;
            try {
              const e:AgentEvent=JSON.parse(line.slice(5).trim());
              setEvents(p=>[...p,e]);
              if(eventRef.current) eventRef.current.scrollTop=eventRef.current.scrollHeight;
            } catch {}
          }
        }
      } catch(e){ setEvents(p=>[...p,{type:"error",text:String(e)}]); }
      setRunning(false);
    };

    return (
      <div style={{flex:1,display:"flex",flexDirection:"column",overflow:"hidden"}}>
        {/* Task input */}
        <div style={{padding:12,background:"#1e293b",borderBottom:"1px solid #334155",flexShrink:0}}>
          <div style={{fontSize:13,fontWeight:700,color:"#60a5fa",marginBottom:8}}>
            🤖 Agent 模式 — 真实 Claude Bash 执行 (mimo-v2.5-pro · 无限制)
          </div>
          <div style={{display:"flex",gap:8}}>
            <textarea value={task} onChange={e=>setTask(e.target.value)}
              onKeyDown={e=>{if(e.key==="Enter"&&(e.ctrlKey||e.metaKey))runAgent();}}
              placeholder="描述你要做的任务… 例如：注册3个Outlook账号"
              style={{flex:1,background:"#0f172a",border:"1px solid #334155",borderRadius:6,color:"#f1f5f9",padding:"8px 10px",fontSize:13,resize:"none",height:64,fontFamily:"inherit"}}/>
            <button onClick={runAgent} disabled={running||!task.trim()}
              style={{padding:"0 20px",background:running?"#374151":"#7c3aed",border:"none",borderRadius:6,color:"#fff",cursor:"pointer",fontSize:14,fontWeight:700,minWidth:80,opacity:!task.trim()?0.5:1}}>
              {running?"⏳":"▶ 执行"}
            </button>
          </div>
          {/* Example prompts */}
          <div style={{display:"flex",gap:6,marginTop:8,flexWrap:"wrap"}}>
            {examples.map((ex,i)=>(
              <button key={i} onClick={()=>setTask(ex)} style={{padding:"2px 8px",background:"#1e293b",border:"1px solid #475569",borderRadius:12,color:"#94a3b8",cursor:"pointer",fontSize:11}}>
                {ex.slice(0,30)}…
              </button>
            ))}
          </div>
        </div>

        {/* Events stream */}
        <div ref={eventRef} style={{flex:1,overflowY:"auto",padding:12,display:"flex",flexDirection:"column",gap:6}}>
          {events.length===0 && !running && (
            <div style={{textAlign:"center",paddingTop:60,color:"#475569"}}>
              <div style={{fontSize:40,marginBottom:12}}>🤖</div>
              <div style={{fontSize:14,marginBottom:8}}>AI Agent 就绪 (Claude Code · Bash 工具 · 无权限限制)</div>
              <div style={{fontSize:12,color:"#334155"}}>选择示例或输入任务，Agent 将自主执行并实时汇报每一步</div>
            </div>
          )}

          {events.map((e,i)=>{
            if (e.type==="start") return <div key={i} style={{color:"#64748b",fontSize:12}}>▷ Agent 启动…</div>;
            if (e.type==="status") return <div key={i} style={{color:"#64748b",fontSize:12}}>◦ {e.text}</div>;
            if (e.type==="ai_response"||e.type==="complete") return (
              <div key={i} style={{background:"#1e293b",border:"1px solid #334155",borderRadius:8,padding:10}}>
                <div style={{fontSize:11,color:"#7c3aed",fontWeight:600,marginBottom:4}}>🤖 Claude 分析</div>
                <div style={{fontSize:13,color:"#e2e8f0",whiteSpace:"pre-wrap",lineHeight:1.6}}>{e.text}</div>
              </div>
            );
            if (e.type==="exec_start") return (
              <div key={i} style={{background:"#0f172a",border:"1px solid #1e3a5f",borderRadius:6,padding:8}}>
                <div style={{fontSize:11,color:"#60a5fa",fontWeight:600,marginBottom:4}}>⚡ 执行命令</div>
                {e.ai_text && <div style={{fontSize:11,color:"#64748b",marginBottom:4}}>{e.ai_text}</div>}
                <code style={{fontSize:12,color:"#fbbf24",display:"block",background:"#020617",padding:"4px 8px",borderRadius:4,whiteSpace:"pre-wrap"}}>{e.cmd}</code>
              </div>
            );
            if (e.type==="exec_done") return (
              <div key={i} style={{background:"#0c1a0c",border:"1px solid #14532d",borderRadius:6,padding:8}}>
                <div style={{fontSize:11,color:"#10b981",marginBottom:4}}>✓ 输出</div>
                <pre style={{fontSize:12,color:"#86efac",margin:0,whiteSpace:"pre-wrap",maxHeight:300,overflow:"auto"}}>{e.stdout}</pre>
              </div>
            );
            if (e.type==="error") return (
              <div key={i} style={{background:"#1c0505",border:"1px solid #7f1d1d",borderRadius:6,padding:8,fontSize:12,color:"#fca5a5"}}>❌ {e.text}</div>
            );
            return null;
          })}
          {running && (
            <div style={{display:"flex",alignItems:"center",gap:8,color:"#f59e0b",padding:"8px 0"}}>
              <span style={{animation:"spin 1s linear infinite",display:"inline-block"}}>⟳</span>
              <span style={{fontSize:13}}>Claude Agent 执行中… (使用 Bash 工具，无权限限制)</span>
            </div>
          )}
        </div>
      </div>
    );
  }

  // ═══════════════════════════════════════════════════════════════════════════════
  // CHAT MODE — Single-turn with real Claude
  // ═══════════════════════════════════════════════════════════════════════════════
  function ChatMode() {
    const [sessions, setSessions] = useState<Session[]>([]);
    const [activeId, setActiveId] = useState<string|null>(null);
    const [messages, setMessages] = useState<Msg[]>([]);
    const [input, setInput] = useState("");
    const [streaming, setStreaming] = useState(false);
    const chatRef = useRef<HTMLDivElement>(null);

    useEffect(()=>{ fetchSessions(); },[]);
    useEffect(()=>{ if(chatRef.current) chatRef.current.scrollTop=chatRef.current.scrollHeight; },[messages]);

    const fetchSessions = async () => {
      try { const r=await fetch(`${BASE}/api/claude-code/sessions`); setSessions(await r.json()); } catch {}
    };

    const newSession = () => {
      const id=genId(); setActiveId(id); setMessages([]);
    };

    const loadSession = async (id: string) => {
      try { const r=await fetch(`${BASE}/api/claude-code/sessions/${id}`); const d=await r.json(); setMessages(d.messages??[]); setActiveId(id); } catch {}
    };

    const saveSession = async (msgs: Msg[]) => {
      if (!activeId) return;
      try {
        await fetch(`${BASE}/api/claude-code/sessions`, {
          method:"POST", headers:{"Content-Type":"application/json"},
          body: JSON.stringify({id:activeId, title:msgs[0]?.content.slice(0,30)??"新会话", messages:msgs})
        });
        fetchSessions();
      } catch {}
    };

    const sendMessage = async () => {
      if (!input.trim() || streaming) return;
      if (!activeId) { const id=genId(); setActiveId(id); }
      const userMsg: Msg = {role:"user",content:input.trim(),ts:Date.now()};
      const newMsgs = [...messages, userMsg];
      setMessages(newMsgs); setInput(""); setStreaming(true);
      let aiContent="";
      const aiMsg: Msg = {role:"assistant",content:"",ts:Date.now()};
      setMessages([...newMsgs, aiMsg]);
      try {
        const res=await fetch(`${BASE}/api/claude-code/chat`, {
          method:"POST", headers:{"Content-Type":"application/json"},
          body: JSON.stringify({message:userMsg.content})
        });
        const reader=res.body!.getReader(); const dec=new TextDecoder(); let buf="";
        while(true) {
          const {done,value}=await reader.read(); if(done) break;
          buf+=dec.decode(value,{stream:true});
          const lines=buf.split("\n"); buf=lines.pop()??"";
          for (const line of lines) {
            if(!line.startsWith("data:")) continue;
            try {
              const e=JSON.parse(line.slice(5).trim());
              if(e.type==="text") { aiContent+=e.content; setMessages([...newMsgs,{role:"assistant",content:aiContent,ts:Date.now()}]); }
            } catch {}
          }
        }
      } catch(err){ aiContent="[错误: "+String(err)+"]"; setMessages([...newMsgs,{role:"assistant",content:aiContent,ts:Date.now()}]); }
      const finalMsgs=[...newMsgs,{role:"assistant" as const,content:aiContent,ts:Date.now()}];
      setMessages(finalMsgs); setStreaming(false);
      saveSession(finalMsgs);
    };

    return (
      <div style={{flex:1,display:"flex",overflow:"hidden"}}>
        {/* Session list */}
        <div style={{width:200,background:"#1e293b",borderRight:"1px solid #334155",display:"flex",flexDirection:"column",overflow:"hidden"}}>
          <button onClick={newSession} style={{margin:8,padding:"6px 0",background:"#7c3aed",border:"none",borderRadius:6,color:"#fff",cursor:"pointer",fontSize:13,fontWeight:700}}>+ 新会话</button>
          <div style={{flex:1,overflowY:"auto"}}>
            {sessions.map(s=>(
              <div key={s.id} onClick={()=>loadSession(s.id)}
                style={{padding:"8px 10px",cursor:"pointer",borderBottom:"1px solid #334155",background:activeId===s.id?"#1e3a5f":"transparent"}}>
                <div style={{fontSize:12,fontWeight:600,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{s.title}</div>
                <div style={{fontSize:10,color:"#64748b"}}>{s.msgCount}条 · {timeAgo(s.updated_at)}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Chat area */}
        <div style={{flex:1,display:"flex",flexDirection:"column",overflow:"hidden"}}>
          <div ref={chatRef} style={{flex:1,overflowY:"auto",padding:16,display:"flex",flexDirection:"column",gap:10}}>
            {messages.length===0 && (
              <div style={{textAlign:"center",paddingTop:60,color:"#475569"}}>
                <div style={{fontSize:36}}>💬</div>
                <div style={{fontSize:14,marginTop:8}}>AI 对话 — mimo-v2.5-pro</div>
                <div style={{fontSize:12,color:"#334155",marginTop:4}}>随意提问，可以问服务器状态、代码问题、操作建议等</div>
              </div>
            )}
            {messages.map((m,i)=>(
              <div key={i} style={{display:"flex",flexDirection:"column",alignItems:m.role==="user"?"flex-end":"flex-start"}}>
                <div style={{
                  maxWidth:"80%",padding:"8px 12px",borderRadius:10,fontSize:13,lineHeight:1.6,
                  background:m.role==="user"?"#1d4ed8":"#1e293b",
                  color:m.role==="user"?"#fff":"#e2e8f0",
                  whiteSpace:"pre-wrap",wordBreak:"break-word"
                }}>{m.content || (streaming&&i===messages.length-1?"●":"")}</div>
                <div style={{fontSize:10,color:"#475569",marginTop:2}}>{timeAgo(m.ts)}</div>
              </div>
            ))}
          </div>
          {/* Input */}
          <div style={{padding:12,background:"#1e293b",borderTop:"1px solid #334155",display:"flex",gap:8}}>
            <textarea value={input} onChange={e=>setInput(e.target.value)}
              onKeyDown={e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();sendMessage();}}}
              placeholder="输入消息… (Enter发送, Shift+Enter换行)"
              style={{flex:1,background:"#0f172a",border:"1px solid #334155",borderRadius:6,color:"#f1f5f9",padding:"8px 10px",fontSize:13,resize:"none",height:56,fontFamily:"inherit"}}/>
            <button onClick={sendMessage} disabled={streaming||!input.trim()}
              style={{padding:"0 16px",background:streaming?"#374151":"#3b82f6",border:"none",borderRadius:6,color:"#fff",cursor:"pointer",fontSize:14,fontWeight:700,opacity:!input.trim()?0.5:1}}>
              {streaming?"⏳":"发送"}
            </button>
          </div>
        </div>
      </div>
    );
  }
  