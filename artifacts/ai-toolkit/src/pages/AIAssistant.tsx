import { useEffect, useRef, useState, useCallback } from "react";

/* ─── Types ─────────────────────────────────── */
type Role = "user" | "assistant";
interface Msg { role: Role; content: string; isStreaming?: boolean; modelId?: string }
interface ExecResult { stdout: string; stderr: string; code: number; success: boolean }
interface LogEntry { cmd: string; result: ExecResult; ts: number }
interface Session { id: string; title: string; created_at: number; updated_at: number; msgCount: number; model: string }

/* Agent event from /api/claude-code/agent */
interface AgentEvent {
  type: "start"|"status"|"ai_thinking"|"ai_response"|"read_file"|"write_file"|"write_error"|"exec_start"|"exec_done"|"complete"|"stuck"|"error";
  text?: string; path?: string; cmd?: string; stdout?: string; stderr?: string;
  code?: number; lines?: number; size?: number; iteration?: number; iterations?: number; error?: boolean | string;
}

/* ─── Models ─────────────────────────────────── */
const MODELS = [
  { id: "default", label: "Claude Code 内置", badge: "mimo-v2.5-pro", desc: "默认·最强" },
  { id: "opus",    label: "claude-opus-4-7",  badge: "Opus",          desc: "旗舰"     },
  { id: "sonnet",  label: "claude-sonnet-4-6", badge: "Sonnet",        desc: "均衡"     },
  { id: "haiku",   label: "claude-haiku-4-5",  badge: "Haiku",         desc: "极速"     },
];

/* ─── Chat system prompt (for quick Q&A mode) ─── */
const CHAT_SYS = `你是部署在 VPS（45.205.27.69）上的 AI 运维和编程助手，拥有完整服务器控制权。
工作目录：/root/Toolkit (pnpm monorepo)
服务：api-server(PM2#62,8081)、frontend(PM2#1,3000,Vite HMR)、remote-exec(PM2#5,9999)、ip2free-monitor2(PM2#85)、ip2free-solve-all(PM2#86)、xray(PM2#4)、ngrok(PM2#22-24)
需执行操作时用 \`\`\`bash 块，需写文件用 [WRITE: /path]内容[/WRITE]，需读文件用 [READ: /path]。
回答简洁，优先中文。`;

/* ─── Helpers ────────────────────────────────── */
const genId = () => Math.random().toString(36).slice(2) + Date.now().toString(36);
const fmtDate = (ts: number) => {
  const diff = Date.now() - ts;
  if (diff < 60000) return "刚刚";
  if (diff < 3600000) return Math.floor(diff / 60000) + "分钟前";
  const d = new Date(ts);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString("zh", { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString("zh", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
};
const fmtSize = (n: number) => n > 1024 ? (n / 1024).toFixed(1) + "k" : n + "B";

function renderMd(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, "<strong class='text-white'>$1</strong>")
    .replace(/`([^`\n]+)`/g, "<code class='ic'>$1</code>")
    .replace(/^#{1,3} (.+)$/gm, "<span class='mh'>$1</span>")
    .replace(/^[-•] (.+)$/gm, "<span class='ml'>• $1</span>")
    .replace(/^\d+\. (.+)$/gm, "<span class='ml'>$&</span>");
}

/* ─── Agent Event Log Item ───────────────────── */
function AgentLogItem({ ev }: { ev: AgentEvent }) {
  const [open, setOpen] = useState(ev.type === "exec_done" && ev.code !== 0);
  switch (ev.type) {
    case "status": case "ai_thinking":
      return <div className="flex items-center gap-2 text-[11px] text-gray-500 py-1">
        <span className="animate-spin text-orange-400">⟳</span> {ev.text}
      </div>;
    case "read_file":
      return <div className={`flex items-center gap-2 text-[11px] py-1 ${ev.error ? "text-red-400" : "text-blue-400"}`}>
        <span>📂</span>
        <span className="font-mono truncate flex-1" title={ev.path}>{ev.path}</span>
        {ev.lines && <span className="text-gray-600">{ev.lines}行</span>}
        {ev.error && <span className="text-red-400">未找到</span>}
      </div>;
    case "write_file":
      return <div className="flex items-center gap-2 text-[11px] text-emerald-400 py-1">
        <span>✏️</span>
        <span className="font-mono truncate flex-1" title={ev.path}>{ev.path}</span>
        {ev.lines && <span className="text-gray-600">{ev.lines}行 {ev.size ? fmtSize(ev.size) : ""}</span>}
      </div>;
    case "write_error":
      return <div className="flex items-center gap-2 text-[11px] text-red-400 py-1">
        <span>❌</span> <span className="font-mono truncate flex-1">{ev.path}</span>
        <span className="text-red-300/70">{String(ev.error).slice(0, 50)}</span>
      </div>;
    case "exec_start":
      return <div className="flex items-center gap-2 text-[11px] text-yellow-400 py-1">
        <span>▶</span> <code className="truncate flex-1 text-yellow-200/70">{ev.cmd?.slice(0, 60)}</code>
      </div>;
    case "exec_done":
      return <div className="border border-[#30363d] rounded-lg overflow-hidden text-[11px] font-mono my-1">
        <div className={`flex items-center gap-2 px-2 py-1.5 cursor-pointer hover:bg-[#161b22] ${ev.code === 0 ? "bg-[#0d1117]" : "bg-red-900/20"}`}
          onClick={() => setOpen(o => !o)}>
          <span className={ev.code === 0 ? "text-emerald-400" : "text-red-400"}>{ev.code === 0 ? "✓" : `✗ ${ev.code}`}</span>
          <code className="flex-1 truncate text-yellow-200/70">{ev.cmd?.slice(0, 55)}</code>
          <span className="text-gray-600">{open ? "▲" : "▼"}</span>
        </div>
        {open && (ev.stdout || ev.stderr) && (
          <div className="px-2 py-1.5 bg-black/40 max-h-36 overflow-y-auto">
            {ev.stdout && <pre className="text-emerald-300/80 whitespace-pre-wrap break-all leading-4">{ev.stdout.slice(0, 800)}</pre>}
            {ev.stderr && <pre className="text-red-300/70 whitespace-pre-wrap break-all leading-4">{ev.stderr.slice(0, 400)}</pre>}
          </div>
        )}
      </div>;
    case "ai_response":
      return <div className="border-l-2 border-orange-500/40 pl-2 py-1 my-1">
        <div className="text-[10px] text-orange-400/60 mb-0.5">AI 第 {ev.iteration} 轮回复</div>
        <div className="text-[11px] text-gray-400 line-clamp-3 whitespace-pre-wrap">{ev.text?.slice(0, 200)}{(ev.text?.length ?? 0) > 200 ? "…" : ""}</div>
      </div>;
    case "complete":
      return <div className="bg-emerald-900/20 border border-emerald-500/30 rounded-lg px-3 py-2 text-[11px] text-emerald-300">
        ✅ 任务完成 ({ev.iterations} 轮)
      </div>;
    case "stuck":
      return <div className="bg-yellow-900/20 border border-yellow-500/30 rounded-lg px-3 py-2 text-[11px] text-yellow-300">
        ⚠️ 遇到阻碍：{ev.text?.replace("[STUCK:", "").replace("]", "")}
      </div>;
    case "error":
      return <div className="bg-red-900/20 border border-red-500/30 rounded-lg px-3 py-2 text-[11px] text-red-300">
        ❌ 错误：{ev.text}
      </div>;
    default: return null;
  }
}

/* ─── ExecBlock (in chat messages) ──────────────────── */
function ExecBlock({ cmd, result, onRun, autoRan }: {
  cmd: string; result?: ExecResult; onRun: (cmd: string) => void; autoRan?: boolean;
}) {
  const [open, setOpen] = useState(!!result);
  return (
    <div className="my-2 border border-[#30363d] rounded-xl overflow-hidden text-xs font-mono">
      <div className="flex items-center gap-2 px-3 py-2 bg-[#0d1117] cursor-pointer hover:bg-[#161b22]"
        onClick={() => result && setOpen(o => !o)}>
        <span className="text-yellow-400">$</span>
        <code className="flex-1 text-yellow-200/80 truncate">{cmd}</code>
        {autoRan && <span className="text-[9px] bg-purple-500/20 text-purple-300 px-1.5 py-0.5 rounded font-sans">auto</span>}
        {!result
          ? <button onClick={e => { e.stopPropagation(); onRun(cmd); }}
              className="px-2.5 py-0.5 rounded bg-blue-600/40 hover:bg-blue-600/70 text-blue-300 text-[11px] font-sans">▶ 运行</button>
          : <span className={`text-[10px] px-1.5 rounded font-sans ${result.code === 0 ? "text-emerald-400" : "text-red-400"}`}>
              {result.code === 0 ? "✓ OK" : `✗ exit ${result.code}`}</span>
        }
        {result && <span className="text-gray-600 font-sans text-[10px]">{open ? "▲" : "▼"}</span>}
      </div>
      {open && result && (
        <div className="px-3 py-2 bg-black/50 max-h-52 overflow-y-auto">
          {result.stdout?.trim() && <pre className="text-emerald-300/80 whitespace-pre-wrap break-all leading-5">{result.stdout.trim()}</pre>}
          {result.stderr?.trim() && <pre className="text-red-300/70 whitespace-pre-wrap break-all leading-5">{result.stderr.trim()}</pre>}
        </div>
      )}
    </div>
  );
}

/* ─── FileWriteBlock ─────────────────────────── */
function FileWriteBlock({ filePath, content, onApply }: {
  filePath: string; content: string; onApply: (p: string, c: string) => Promise<void>;
}) {
  const [applied, setApplied] = useState(false);
  const [applying, setApplying] = useState(false);
  return (
    <div className="my-2 border border-emerald-500/25 rounded-xl overflow-hidden text-xs font-mono">
      <div className="flex items-center gap-2 px-3 py-2 bg-[#0d1117] border-b border-[#21262d]">
        <span className="text-emerald-400">📝</span>
        <span className="flex-1 text-emerald-200/80 truncate">{filePath}</span>
        <span className="text-gray-600">{content.split("\n").length}行</span>
        {!applied
          ? <button onClick={async () => { setApplying(true); await onApply(filePath, content); setApplied(true); setApplying(false); }}
              disabled={applying}
              className="px-2.5 py-0.5 rounded bg-emerald-600/40 hover:bg-emerald-600/70 text-emerald-300 text-[11px] font-sans disabled:opacity-50">
              {applying ? "写入…" : "✏️ 写入"}
            </button>
          : <span className="text-emerald-400 text-[11px] font-sans">✓ 已写入</span>
        }
      </div>
      <div className="px-3 py-2 bg-black/40 max-h-40 overflow-y-auto">
        <pre className="text-gray-300/80 whitespace-pre-wrap break-all leading-5">{content.slice(0, 1500)}{content.length > 1500 ? "\n…" : ""}</pre>
      </div>
    </div>
  );
}

/* ─── MsgBubble ──────────────────────────────── */
function MsgBubble({ msg, execResults, autoRanCmds, onRun, onWriteFile }: {
  msg: Msg; execResults: Map<string, ExecResult>; autoRanCmds: Set<string>;
  onRun: (cmd: string) => void; onWriteFile: (p: string, c: string) => Promise<void>;
}) {
  const modelBadge = MODELS.find(m => m.id === (msg.modelId ?? "default"))?.badge ?? "mimo-v2.5-pro";
  if (msg.role === "user") return (
    <div className="flex justify-end mb-3">
      <div className="max-w-[82%] bg-blue-600/90 text-white rounded-2xl rounded-br-sm px-4 py-2.5 text-sm whitespace-pre-wrap leading-relaxed">
        {msg.content}
      </div>
    </div>
  );
  const parts: React.ReactNode[] = [];
  const re = /```(?:bash|sh|shell|cmd)?\n?([\s\S]*?)```|\[WRITE:\s*([^\]]+)\]\n?([\s\S]*?)\[\/WRITE\]/g;
  let last = 0; let m; let ki = 0;
  while ((m = re.exec(msg.content)) !== null) {
    const before = msg.content.slice(last, m.index);
    if (before) parts.push(<span key={ki++} className="whitespace-pre-wrap text-sm leading-relaxed block" dangerouslySetInnerHTML={{ __html: renderMd(before) }} />);
    if (m[1] !== undefined) {
      const rawCmd = m[1].trim();
      parts.push(<ExecBlock key={ki++} cmd={rawCmd} result={execResults.get(rawCmd)} onRun={onRun} autoRan={autoRanCmds.has(rawCmd)} />);
    } else if (m[2]) {
      parts.push(<FileWriteBlock key={ki++} filePath={m[2]} content={m[3]?.trim() ?? ""} onApply={onWriteFile} />);
    }
    last = m.index + m[0].length;
  }
  const tail = msg.content.slice(last);
  if (tail) parts.push(<span key={ki++} className="whitespace-pre-wrap text-sm leading-relaxed block" dangerouslySetInnerHTML={{ __html: renderMd(tail) }} />);
  return (
    <div className="flex justify-start mb-4">
      <div className="max-w-[90%]">
        <div className="text-[10px] text-gray-500 mb-1 ml-1 flex items-center gap-2">
          <span className="w-3 h-3 rounded-full bg-gradient-to-br from-orange-400 to-rose-500 inline-block shrink-0" />
          <span className="font-medium text-orange-400/80">{modelBadge}</span>
          {msg.isStreaming && <span className="animate-pulse text-blue-400 text-xs">▌</span>}
        </div>
        <div className="bg-[#1a2030] border border-[#21262d] text-gray-200 rounded-2xl rounded-bl-sm px-4 py-3 leading-relaxed">
          {parts}
        </div>
      </div>
    </div>
  );
}

/* ─── ModelPicker ────────────────────────────── */
function ModelPicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="flex gap-1 flex-wrap">
      {MODELS.map(m => (
        <button key={m.id} onClick={() => onChange(m.id)} title={m.label}
          className={`text-[11px] px-2.5 py-1 rounded-lg border transition-all ${value === m.id ? "bg-orange-500/20 border-orange-500/50 text-orange-300 font-semibold" : "bg-[#0d1117] border-[#30363d] text-gray-400 hover:text-gray-200 hover:border-[#444c56]"}`}>
          <span className="opacity-60">{m.desc} · </span>{m.badge}
        </button>
      ))}
    </div>
  );
}

/* ─── SessionPanel ───────────────────────────── */
function SessionPanel({ current, onLoad, onNew }: { current: string; onLoad: (id: string) => void; onNew: () => void }) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const refresh = useCallback(async () => {
    try { setSessions(await (await fetch("/api/claude-code/sessions")).json()); }
    catch { /* */ } finally { setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  const del = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    await fetch(`/api/claude-code/sessions/${id}`, { method: "DELETE" });
    setSessions(p => p.filter(s => s.id !== id));
  };
  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between mb-3 shrink-0">
        <span className="text-[11px] text-gray-500 font-medium uppercase tracking-wider">历史会话</span>
        <div className="flex gap-1.5">
          <button onClick={() => void refresh()} className="text-[10px] text-gray-600 hover:text-gray-400 px-1.5 py-1 rounded hover:bg-[#21262d]">↺</button>
          <button onClick={onNew} className="text-[10px] bg-orange-600/20 hover:bg-orange-600/40 border border-orange-600/30 text-orange-300 px-2 py-1 rounded">+ 新</button>
        </div>
      </div>
      {loading ? <div className="text-xs text-gray-600 text-center py-6 animate-pulse">加载…</div>
        : sessions.length === 0 ? <div className="text-xs text-gray-600 text-center py-6">暂无历史</div>
        : <div className="space-y-1 overflow-y-auto flex-1">
            {sessions.map(s => (
              <div key={s.id} onClick={() => onLoad(s.id)}
                className={`group px-3 py-2 rounded-xl border cursor-pointer transition-all ${s.id === current ? "bg-orange-500/10 border-orange-500/40" : "bg-[#0d1117] border-[#30363d] hover:border-[#444c56] hover:bg-[#161b22]"}`}>
                <div className="flex items-start gap-1.5">
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-medium truncate text-gray-200">{s.title}</div>
                    <div className="text-[10px] text-gray-600 mt-0.5">{s.msgCount}条 · {fmtDate(s.updated_at)}</div>
                  </div>
                  <button onClick={e => void del(s.id, e)} className="opacity-0 group-hover:opacity-100 text-[10px] text-red-400/60 hover:text-red-400 px-1 rounded">✕</button>
                </div>
              </div>
            ))}
          </div>
      }
    </div>
  );
}

/* ─── Quick shortcuts ────────────────────────── */
const QUICK_CMDS = [
  { label: "PM2 总览",         cmd: "pm2 list" },
  { label: "api-server 日志",  cmd: "pm2 logs api-server --lines 30 --nostream" },
  { label: "ip2free 监控",     cmd: "pm2 logs ip2free-monitor2 --lines 20 --nostream" },
  { label: "solve-all 日志",   cmd: "pm2 logs ip2free-solve-all --lines 20 --nostream" },
  { label: "磁盘/内存",        cmd: "df -h / && free -h" },
  { label: "重建 api-server",  cmd: "cd /root/Toolkit && pnpm --filter @workspace/api-server run build && pm2 restart api-server" },
];

const AGENT_TEMPLATES = [
  { label: "分析 api-server 路由", task: "读取 /root/Toolkit/artifacts/api-server/src/routes/index.ts 和所有路由文件，分析当前所有 API 路由的结构和功能，列出清单。" },
  { label: "续写未完成代码",       task: "扫描 /root/Toolkit/artifacts 下所有 TypeScript 文件，找出有 TODO 注释或未实现（throw Error、// ...）的函数，读取后续写完整实现。" },
  { label: "磁盘清理建议",         task: "分析服务器磁盘使用情况：执行 du -sh /root/Toolkit /root/Toolkit/node_modules /root/Toolkit/artifacts/*/node_modules 等，找出最大的目录，给出清理方案并执行安全的清理操作。" },
  { label: "检查所有服务健康",     task: "检查所有 PM2 服务状态：pm2 list、各关键服务的最近日志（api-server, ip2free-monitor2, ip2free-solve-all, xray），找出异常并给出修复建议。" },
  { label: "新增 API 路由",        task: "在 /root/Toolkit/artifacts/api-server/src/routes/ 下新增一个示例路由文件，读取 routes/index.ts 了解挂载模式，然后新增路由并正确挂载到 index.ts，最后构建并验证。" },
];

/* ─── Main ───────────────────────────────────── */
export default function AIAssistant({ onNavigate: _onNavigate }: { onNavigate: (tab: string) => void }) {
  const initialMsg: Msg = { role: "assistant", modelId: "default",
    content: `你好！我是任务中枢 AI，搭载 **Claude Code 内置模型 (mimo-v2.5-pro)**，直连 VPS 45.205.27.69。

我有两种工作模式：

**💬 对话模式** — 快速问答，生成代码和命令供你一键执行

**🤖 Agent 模式** — 我来全权执行：自动读取相关文件、写入代码、运行命令、构建服务，直到任务完成。适合：
• 续写服务器上未完成的代码
• 新增 API 路由/前端页面（自动对接路由配置）
• 排查修复服务故障
• 磁盘清理、服务管理` };

  const [mode, setMode] = useState<"chat" | "agent">("chat");
  // Chat state
  const [msgs, setMsgs] = useState<Msg[]>([initialMsg]);
  const [input, setInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const [execResults, setExecResults] = useState<Map<string, ExecResult>>(new Map());
  const [autoRanCmds, setAutoRanCmds] = useState<Set<string>>(new Set());
  const [execLog, setExecLog] = useState<LogEntry[]>([]);
  // Agent state
  const [agentTask, setAgentTask] = useState("");
  const [agentRunning, setAgentRunning] = useState(false);
  const [agentEvents, setAgentEvents] = useState<AgentEvent[]>([]);
  const [agentFinalMsg, setAgentFinalMsg] = useState("");
  // Shared
  const [model, setModel] = useState("default");
  const [sessionId, setSessionId] = useState(genId);
  const [sessionTitle, setSessionTitle] = useState("新会话");
  const [bashInput, setBashInput] = useState("");
  const [bashRunning, setBashRunning] = useState(false);
  const [sideTab, setSideTab] = useState<"sessions" | "bash" | "log">("sessions");
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const agentBottomRef = useRef<HTMLDivElement>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs]);
  useEffect(() => { agentBottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [agentEvents]);

  /* ─── Auto-save ─── */
  const saveSession = useCallback((messages: Msg[], title: string) => {
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      if (messages.length <= 1) return;
      await fetch("/api/claude-code/sessions", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: sessionId, title, messages, model }),
      });
    }, 1500);
  }, [sessionId, model]);

  /* ─── Load session ─── */
  const loadSession = useCallback(async (id: string) => {
    try {
      const data = await (await fetch(`/api/claude-code/sessions/${id}`)).json();
      setMsgs(data.messages ?? [initialMsg]);
      setModel(data.model ?? "default");
      setSessionId(data.id);
      setSessionTitle(data.title ?? "会话");
      setExecResults(new Map());
      setAutoRanCmds(new Set());
      setMode("chat");
      setSideTab("bash");
    } catch { /* */ }
  }, []);

  /* ─── New session ─── */
  const newSession = useCallback(() => {
    setMsgs([initialMsg]);
    setSessionId(genId());
    setSessionTitle("新会话");
    setExecResults(new Map());
    setAutoRanCmds(new Set());
    setExecLog([]);
    setAgentEvents([]);
    setAgentFinalMsg("");
    setTimeout(() => inputRef.current?.focus(), 50);
  }, []);

  /* ─── runCmd (exec panel) ─── */
  const runCmd = useCallback(async (cmd: string): Promise<ExecResult> => {
    const ts = Date.now();
    try {
      const r = await fetch("/api/claude-code/exec", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cmd }),
      });
      const data: ExecResult = await r.json();
      setExecResults(p => new Map(p).set(cmd, data));
      setExecLog(p => [...p.slice(-49), { cmd, result: data, ts }]);
      setSideTab("log");
      return data;
    } catch (e) {
      const err: ExecResult = { stdout: "", stderr: String(e), code: 1, success: false };
      setExecResults(p => new Map(p).set(cmd, err));
      setExecLog(p => [...p.slice(-49), { cmd, result: err, ts }]);
      return err;
    }
  }, []);

  const bashRun = useCallback(async (cmd: string) => {
    setBashRunning(true); await runCmd(cmd); setBashRunning(false);
  }, [runCmd]);

  const writeFile = useCallback(async (filePath: string, content: string) => {
    await fetch("/api/claude-code/file-write", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: filePath, content, createBackup: true }),
    });
  }, []);

  /* ─── Extract bash from AI text ─── */
  const extractBash = (text: string): string[] => {
    const re = /```(?:bash|sh|shell|cmd)?\n?([\s\S]*?)```/g;
    const cmds: string[] = []; let m;
    while ((m = re.exec(text)) !== null) cmds.push(m[1].trim());
    return cmds;
  };

  /* ─── Auto-read files from user message ─── */
  const autoReadFiles = async (userMsg: string): Promise<string> => {
    const re = /\/[^\s"'`<>()[\]]+\.[a-zA-Z]{1,10}/g;
    const paths = [...new Set([...userMsg.matchAll(re)].map(m => m[0]))];
    if (!paths.length) return "";
    const reads = await Promise.all(paths.map(async p => {
      try {
        const r = await fetch("/api/claude-code/file-read", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: p }) });
        if (!r.ok) return null;
        const d = await r.json();
        return `\n[文件: ${p}]\n\`\`\`\n${d.content}\n\`\`\``;
      } catch { return null; }
    }));
    return reads.filter(Boolean).join("\n");
  };

  /* ─── Chat: send message ─── */
  const sendChat = async (text = input) => {
    const msg = text.trim();
    if (!msg || chatLoading) return;
    setInput("");
    setChatLoading(true);
    const fileCtx = await autoReadFiles(msg);
    const userMsg: Msg = { role: "user", content: msg };
    const assistantMsg: Msg = { role: "assistant", content: "", isStreaming: true, modelId: model };
    setMsgs(prev => [...prev, userMsg, assistantMsg]);
    const execCtx = execLog.length > 0
      ? "\n[最近执行]\n" + execLog.slice(-2).map(e => `$ ${e.cmd}\n${(e.result.stdout || "").slice(0, 300)}\n[exit: ${e.result.code}]`).join("\n\n")
      : "";
    const history = msgs.slice(-10).filter(m => m.content).map(m => (m.role === "user" ? "Human" : "Assistant") + ": " + m.content.slice(0, 600)).join("\n");
    const fullPrompt = [CHAT_SYS, execCtx, fileCtx, history ? history + "\nHuman: " + msg : msg].filter(Boolean).join("\n");
    let finalText = "";
    try {
      const resp = await fetch("/api/claude-code/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: fullPrompt, model }) });
      const reader = resp.body!.getReader();
      const dec = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split("\n"); buf = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const ev = JSON.parse(line.slice(6));
            if (ev.type === "text") { finalText += ev.content; setMsgs(prev => { const last = { ...prev[prev.length - 1], content: finalText }; return [...prev.slice(0, -1), last]; }); }
          } catch { /* */ }
        }
      }
    } catch (e) {
      finalText = `[错误] ${String(e)}`;
      setMsgs(prev => { const last = { ...prev[prev.length - 1], content: finalText }; return [...prev.slice(0, -1), last]; });
    }
    setMsgs(prev => { const last = { ...prev[prev.length - 1], isStreaming: false }; return [...prev.slice(0, -1), last]; });
    setChatLoading(false);
    const newTitle = msgs.length <= 1 ? msg.slice(0, 30) : sessionTitle;
    setSessionTitle(newTitle);
    saveSession([...msgs, userMsg, { role: "assistant", content: finalText, modelId: model }], newTitle);
    setTimeout(() => inputRef.current?.focus(), 50);
  };

  /* ─── Agent: run task ─── */
  const runAgent = async (taskText = agentTask) => {
    const task = taskText.trim();
    if (!task || agentRunning) return;
    setAgentRunning(true);
    setAgentEvents([]);
    setAgentFinalMsg("");
    try {
      const resp = await fetch("/api/claude-code/agent", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task, model }),
      });
      const reader = resp.body!.getReader();
      const dec = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split("\n"); buf = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const ev: AgentEvent = JSON.parse(line.slice(6));
            setAgentEvents(prev => [...prev, ev]);
            if (ev.type === "complete" || ev.type === "stuck") setAgentFinalMsg(ev.text ?? "");
          } catch { /* */ }
        }
      }
    } catch (e) {
      setAgentEvents(prev => [...prev, { type: "error", text: String(e) }]);
    }
    setAgentRunning(false);
    // Save agent task to chat history as a summary
    const summary: Msg = { role: "user", content: `[Agent任务] ${task}` };
    const result: Msg = { role: "assistant", content: agentFinalMsg || "Agent 任务已完成", modelId: model };
    const newTitle = task.slice(0, 30);
    setSessionTitle(newTitle);
    saveSession([...msgs, summary, result], newTitle);
  };

  const onChatKey = (e: React.KeyboardEvent) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void sendChat(); } };
  const onAgentKey = (e: React.KeyboardEvent) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void runAgent(); } };
  const currentBadge = MODELS.find(m => m.id === model)?.badge ?? "mimo-v2.5-pro";

  return (
    <div className="grid lg:grid-cols-[minmax(0,1fr)_290px] gap-4" style={{ height: "calc(100vh - 180px)" }}>

      {/* ── Left: Main Panel ── */}
      <section className="bg-[#161b22] border border-[#21262d] rounded-2xl flex flex-col overflow-hidden">
        {/* Header */}
        <div className="px-4 py-3 border-b border-[#21262d] shrink-0 space-y-2.5">
          <div className="flex items-center gap-3">
            <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-orange-400 to-rose-500 flex items-center justify-center text-[11px] font-bold text-white shrink-0">AI</div>
            <div className="flex-1 min-w-0">
              <h2 className="text-white font-semibold text-sm leading-none truncate">{sessionTitle}</h2>
              <p className="text-[11px] text-gray-500 mt-0.5">Claude Code · VPS 45.205.27.69</p>
            </div>
            {/* Mode toggle */}
            <div className="flex gap-1 bg-[#0d1117] border border-[#30363d] rounded-lg p-0.5 shrink-0">
              <button onClick={() => setMode("chat")}
                className={`text-[11px] px-3 py-1.5 rounded-md transition-all font-medium ${mode === "chat" ? "bg-blue-600/30 text-blue-300 border border-blue-500/40" : "text-gray-500 hover:text-gray-300"}`}>
                💬 对话
              </button>
              <button onClick={() => setMode("agent")}
                className={`text-[11px] px-3 py-1.5 rounded-md transition-all font-medium ${mode === "agent" ? "bg-purple-600/30 text-purple-300 border border-purple-500/40" : "text-gray-500 hover:text-gray-300"}`}>
                🤖 Agent
              </button>
            </div>
          </div>
          <ModelPicker value={model} onChange={setModel} />
        </div>

        {/* ── Chat Mode ── */}
        {mode === "chat" && <>
          <div className="flex-1 overflow-y-auto px-4 py-4">
            {msgs.map((msg, i) => (
              <MsgBubble key={i} msg={msg} execResults={execResults} autoRanCmds={autoRanCmds} onRun={bashRun} onWriteFile={writeFile} />
            ))}
            {chatLoading && msgs[msgs.length - 1]?.content === "" && (
              <div className="flex justify-start mb-3">
                <div className="bg-[#1a2030] border border-[#21262d] rounded-2xl px-5 py-3">
                  <span className="flex gap-1.5 items-center">
                    {[0,1,2].map(i => <span key={i} className="w-1.5 h-1.5 rounded-full bg-orange-400/60 animate-bounce" style={{ animationDelay: `${i * 0.15}s` }} />)}
                    <span className="text-[11px] text-gray-600 ml-1">{currentBadge} 思考中…</span>
                  </span>
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>
          <div className="px-4 py-3 border-t border-[#21262d] shrink-0">
            <div className="flex gap-2 items-end">
              <textarea ref={inputRef} value={input} onChange={e => setInput(e.target.value)} onKeyDown={onChatKey}
                disabled={chatLoading} rows={1}
                placeholder={`问 ${currentBadge} 任何问题，或让我操作服务器… (Enter)`}
                className="flex-1 resize-none bg-[#0d1117] border border-[#30363d] rounded-xl px-4 py-2.5 text-sm text-white placeholder-gray-600 outline-none focus:border-blue-500/60 disabled:opacity-50 leading-relaxed"
                style={{ maxHeight: "100px", overflowY: "auto" }} />
              <button onClick={() => void sendChat()} disabled={chatLoading || !input.trim()}
                className="px-4 py-2.5 rounded-xl bg-blue-600/80 hover:bg-blue-500 disabled:opacity-40 text-white text-sm font-semibold shrink-0 transition-colors">
                发送
              </button>
            </div>
          </div>
        </>}

        {/* ── Agent Mode ── */}
        {mode === "agent" && <>
          {/* Task input */}
          <div className="px-4 py-4 border-b border-[#21262d] shrink-0 space-y-3">
            <div>
              <label className="text-[11px] text-gray-500 mb-1.5 block">任务描述（AI 将自主执行，直到完成）</label>
              <textarea value={agentTask} onChange={e => setAgentTask(e.target.value)} onKeyDown={onAgentKey}
                disabled={agentRunning} rows={3}
                placeholder={"例如：读取 /root/Toolkit/artifacts/api-server/src/routes/ 下的所有路由文件，新增一个 /api/test/ping 路由返回服务器时间，并正确挂载到 index.ts，构建并验证。\n\n（Ctrl+Enter 执行）"}
                className="w-full resize-none bg-[#0d1117] border border-[#30363d] rounded-xl px-4 py-3 text-sm text-white placeholder-gray-600 outline-none focus:border-purple-500/60 disabled:opacity-50 leading-relaxed" />
            </div>
            {/* Templates */}
            <div className="flex gap-1.5 flex-wrap">
              {AGENT_TEMPLATES.map(t => (
                <button key={t.label} onClick={() => setAgentTask(t.task)} disabled={agentRunning}
                  className="text-[11px] px-2.5 py-1 rounded-lg bg-[#21262d] hover:bg-[#30363d] border border-[#30363d] text-gray-400 hover:text-gray-200 disabled:opacity-40 transition-colors">
                  {t.label}
                </button>
              ))}
            </div>
            <button onClick={() => void runAgent()} disabled={agentRunning || !agentTask.trim()}
              className={`w-full py-2.5 rounded-xl font-semibold text-sm transition-all ${agentRunning ? "bg-purple-800/40 border border-purple-500/40 text-purple-300 cursor-not-allowed" : "bg-purple-600/80 hover:bg-purple-500 text-white disabled:opacity-40"}`}>
              {agentRunning ? "🤖 Agent 执行中…" : "🚀 启动 Agent"}
            </button>
          </div>

          {/* Agent execution log */}
          <div className="flex-1 overflow-y-auto px-4 py-3 space-y-0.5">
            {agentEvents.length === 0 && !agentRunning && (
              <div className="text-center text-gray-600 text-xs py-12">
                <div className="text-3xl mb-3">🤖</div>
                <div>描述任务，点击「启动 Agent」</div>
                <div className="mt-1 text-[11px]">AI 会自主读取文件、写入代码、执行命令直到完成</div>
              </div>
            )}
            {agentEvents.map((ev, i) => <AgentLogItem key={i} ev={ev} />)}
            {agentRunning && (
              <div className="flex items-center gap-2 text-[11px] text-purple-400 py-2 animate-pulse">
                <span className="w-1.5 h-1.5 rounded-full bg-purple-400" />
                <span className="w-1.5 h-1.5 rounded-full bg-purple-400 animation-delay-150" />
                <span className="w-1.5 h-1.5 rounded-full bg-purple-400 animation-delay-300" />
                Agent 执行中…
              </div>
            )}
            {agentFinalMsg && !agentRunning && (
              <div className="mt-3 bg-[#1a2030] border border-[#21262d] rounded-xl px-4 py-3 text-sm text-gray-200">
                <div className="text-[10px] text-orange-400/70 mb-2">AI 执行总结</div>
                <div className="whitespace-pre-wrap leading-relaxed" dangerouslySetInnerHTML={{ __html: renderMd(agentFinalMsg) }} />
              </div>
            )}
            <div ref={agentBottomRef} />
          </div>
        </>}
      </section>

      {/* ── Right: Sidebar ── */}
      <aside className="flex flex-col gap-3 overflow-hidden" style={{ maxHeight: "calc(100vh - 180px)" }}>
        {/* Status */}
        <div className="bg-[#161b22] border border-[#21262d] rounded-2xl px-3 py-2.5 flex flex-wrap gap-x-3 gap-y-1 text-[11px] shrink-0">
          <div className="flex items-center gap-1.5"><span className="text-emerald-400">●</span><span className="text-gray-400">VPS 45.205.27.69</span></div>
          <div className="flex items-center gap-1.5"><span className="text-orange-400">●</span><span className="text-gray-400">Claude Code</span></div>
          {agentRunning && <div className="flex items-center gap-1.5"><span className="text-purple-400 animate-pulse">●</span><span className="text-purple-300">Agent 运行中</span></div>}
        </div>

        {/* Tabs */}
        <div className="bg-[#161b22] border border-[#21262d] rounded-2xl overflow-hidden flex flex-col flex-1 min-h-0">
          <div className="flex border-b border-[#21262d] shrink-0">
            {(["sessions", "bash", "log"] as const).map(t => (
              <button key={t} onClick={() => setSideTab(t)}
                className={`flex-1 py-2 text-[11px] font-medium transition-colors ${sideTab === t ? "text-white border-b-2 border-orange-500 bg-[#1a2030]" : "text-gray-500 hover:text-gray-300"}`}>
                {t === "sessions" ? "💾 会话" : t === "bash" ? "🖥 命令" : `📋${execLog.length > 0 ? `(${execLog.length})` : ""}`}
              </button>
            ))}
          </div>

          {sideTab === "sessions" && (
            <div className="p-3 flex-1 min-h-0 overflow-hidden flex flex-col">
              <SessionPanel current={sessionId} onLoad={loadSession} onNew={newSession} />
            </div>
          )}

          {sideTab === "bash" && (
            <div className="p-3 space-y-2.5 overflow-y-auto flex-1">
              <div className="flex gap-2">
                <input value={bashInput} onChange={e => setBashInput(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter" && bashInput.trim()) { void bashRun(bashInput); setBashInput(""); } }}
                  placeholder="bash 命令…"
                  className="flex-1 bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-xs text-white font-mono placeholder-gray-600 outline-none focus:border-yellow-500/60" />
                <button onClick={() => { if (bashInput.trim()) { void bashRun(bashInput); setBashInput(""); } }}
                  disabled={bashRunning || !bashInput.trim()}
                  className="px-3 py-2 rounded-lg bg-yellow-600/20 hover:bg-yellow-600/40 border border-yellow-600/30 text-yellow-300 text-xs disabled:opacity-40 font-bold">{bashRunning ? "…" : "▶"}</button>
              </div>
              <div className="space-y-1">
                {QUICK_CMDS.map(({ label, cmd }) => (
                  <button key={cmd} onClick={() => void bashRun(cmd)} disabled={bashRunning}
                    className="w-full text-left px-3 py-2 rounded-lg bg-[#0d1117] hover:bg-[#21262d] border border-[#30363d] hover:border-[#444c56] transition-colors disabled:opacity-40 group">
                    <div className="text-[11px] text-gray-200 font-medium group-hover:text-white">{label}</div>
                    <div className="text-[10px] text-gray-600 font-mono truncate mt-0.5">{cmd.slice(0, 50)}</div>
                  </button>
                ))}
              </div>
            </div>
          )}

          {sideTab === "log" && (
            <div className="p-3 flex-1 overflow-y-auto">
              {execLog.length === 0
                ? <p className="text-xs text-gray-600 text-center py-8">暂无记录</p>
                : <>
                    <div className="flex justify-end mb-2"><button onClick={() => setExecLog([])} className="text-[10px] text-gray-600 hover:text-gray-400">清空</button></div>
                    <div className="space-y-1.5">
                      {[...execLog].reverse().map((entry, i) => (
                        <div key={i} className="bg-[#0d1117] border border-[#30363d] rounded-xl overflow-hidden text-xs font-mono">
                          <div className="flex items-center gap-2 px-2.5 py-1.5 border-b border-[#21262d]">
                            <span className={entry.result.code === 0 ? "text-emerald-400" : "text-red-400"}>{entry.result.code === 0 ? "✓" : `✗${entry.result.code}`}</span>
                            <code className="flex-1 truncate text-yellow-100/70">{entry.cmd.slice(0, 40)}</code>
                          </div>
                          {(entry.result.stdout?.trim() || entry.result.stderr?.trim()) && (
                            <pre className="px-2.5 py-1.5 text-emerald-300/70 whitespace-pre-wrap max-h-20 overflow-y-auto break-all leading-5">
                              {(entry.result.stdout?.trim() || entry.result.stderr?.trim() || "").slice(0, 200)}
                            </pre>
                          )}
                        </div>
                      ))}
                    </div>
                  </>
              }
            </div>
          )}
        </div>
      </aside>

      <style>{`
        .ic { background:#1e2a3a; color:#79c0ff; padding:1px 5px; border-radius:4px; font-family:monospace; font-size:.85em; }
        .mh { display:block; font-weight:700; color:#e2e8f0; font-size:1.05em; margin:8px 0 4px; }
        .ml { display:block; margin:2px 0; }
      `}</style>
    </div>
  );
}
