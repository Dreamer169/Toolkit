import { useState, useRef, useEffect } from "react";

const API = import.meta.env.BASE_URL.replace(/\/$/, "") + "/api";

interface LogEntry {
  id: string;
  type: "prompt" | "output" | "error" | "system";
  content: string;
  ts: number;
  exitCode?: number;
  durationMs?: number;
  model?: string;
}

interface CodexStatus {
  loggedIn: boolean;
  authMode: string | null;
  model: string;
  version: string;
  skillCount: number;
  sessionCount: number;
  vpsHost: string;
  uptime: string;
}

interface Skill {
  name: string;
  path: string;
  description: string | null;
  hasAgents: boolean;
}

const genId = () => Math.random().toString(36).slice(2) + Date.now().toString(36);

export default function CodexControl() {
  const [status, setStatus] = useState<CodexStatus | null>(null);
  const [statusErr, setStatusErr] = useState(false);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [view, setView] = useState<"terminal" | "skills">("terminal");
  const [skills, setSkills] = useState<Skill[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    fetch(API + "/codex/status")
      .then((r) => r.json())
      .then((d: CodexStatus) => {
        setStatus(d);
        setLogs([{
          id: genId(), type: "system", ts: Date.now(),
          content: `CODEX_CLI v${d.version} | HOST: ${d.vpsHost} | MODEL: ${d.model}\n已登录: ${d.loggedIn ? "是 (" + (d.authMode || "api key") + ")" : "否"} | 技能: ${d.skillCount} | 会话: ${d.sessionCount}\n输入 prompt 后按 Enter 执行，Shift+Enter 换行`,
        }]);
      })
      .catch(() => setStatusErr(true));
  }, []);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [logs, pending]);

  const loadSkills = () => {
    if (skills.length > 0) { setView("skills"); return; }
    setSkillsLoading(true);
    setView("skills");
    fetch(API + "/codex/skills")
      .then((r) => r.json())
      .then((d: Skill[]) => setSkills(d))
      .catch(() => {})
      .finally(() => setSkillsLoading(false));
  };

  const submit = async () => {
    const text = input.trim();
    if (!text || pending) return;
    setInput("");
    setLogs((p) => [...p, { id: genId(), type: "prompt", ts: Date.now(), content: text }]);
    setPending(true);
    try {
      const r = await fetch(API + "/codex/exec", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: text, model: status?.model }),
      });
      const d = await r.json();
      setLogs((p) => [...p, {
        id: genId(), type: d.exitCode === 0 ? "output" : "error",
        ts: Date.now(), content: d.output || "(no output)",
        exitCode: d.exitCode, durationMs: d.durationMs, model: d.model,
      }]);
    } catch (e: any) {
      setLogs((p) => [...p, { id: genId(), type: "error", ts: Date.now(), content: "请求失败: " + e.message }]);
    } finally {
      setPending(false);
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  };

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-white font-bold text-xl flex items-center gap-2">
            <span className="text-green-400">⬡</span> Codex 控制台
          </h2>
          <p className="text-gray-500 text-sm mt-0.5">通过 OpenAI Codex CLI 在 VPS 上执行 AI 任务</p>
        </div>
        {status && (
          <div className="flex items-center gap-3">
            <div className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full border ${status.loggedIn ? "border-green-800 bg-green-900/20 text-green-400" : "border-red-800 bg-red-900/20 text-red-400"}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${status.loggedIn ? "bg-green-400" : "bg-red-400"}`} />
              {status.loggedIn ? "已连接" : "未登录"}
            </div>
            <div className="text-xs text-gray-500 font-mono">{status.model}</div>
          </div>
        )}
      </div>

      {/* Status cards */}
      {status && (
        <div className="grid grid-cols-4 gap-3">
          {[
            { label: "VPS", value: status.vpsHost },
            { label: "版本", value: status.version.replace("codex-cli ", "") },
            { label: "技能数", value: String(status.skillCount) },
            { label: "运行时间", value: status.uptime.slice(0, 20) },
          ].map((c) => (
            <div key={c.label} className="bg-[#161b22] border border-[#30363d] rounded-xl px-4 py-3">
              <div className="text-gray-500 text-xs mb-1">{c.label}</div>
              <div className="text-white text-sm font-mono font-semibold truncate">{c.value}</div>
            </div>
          ))}
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 border-b border-[#30363d]">
        {[{ id: "terminal" as const, label: "终端", icon: ">_" }, { id: "skills" as const, label: "技能库", icon: "◈" }].map((t) => (
          <button
            key={t.id}
            onClick={() => t.id === "skills" ? loadSkills() : setView("terminal")}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${view === t.id ? "border-blue-500 text-blue-400" : "border-transparent text-gray-500 hover:text-gray-300"}`}
          >
            <span className="font-mono mr-1.5">{t.icon}</span>{t.label}
          </button>
        ))}
      </div>

      {/* Terminal view */}
      {view === "terminal" && (
        <div className="bg-[#0d1117] border border-[#30363d] rounded-xl overflow-hidden">
          {/* Output */}
          <div ref={scrollRef} className="h-96 overflow-y-auto p-4 space-y-3 font-mono text-sm">
            {statusErr && (
              <div className="text-red-400 border border-red-800 bg-red-900/10 rounded-lg px-4 py-3 text-xs">
                无法连接 Codex API — 请检查 api-server 是否运行
              </div>
            )}
            {logs.map((log) => (
              <div key={log.id}>
                {log.type === "system" && (
                  <div className="text-green-400/70 whitespace-pre-wrap border-l-2 border-green-700/40 pl-3 py-1 text-xs leading-relaxed">
                    {log.content}
                  </div>
                )}
                {log.type === "prompt" && (
                  <div className="flex gap-2">
                    <span className="text-green-400 shrink-0 select-none">❯</span>
                    <span className="text-gray-200 whitespace-pre-wrap break-words">{log.content}</span>
                  </div>
                )}
                {log.type === "output" && (
                  <div className="border-l-2 border-green-600/40 pl-3 ml-4">
                    <div className="text-gray-300 whitespace-pre-wrap break-words leading-relaxed">{log.content}</div>
                    <div className="flex gap-3 mt-2 text-[10px] text-gray-600 uppercase">
                      <span className="text-green-500">EXIT 0</span>
                      <span>{log.durationMs}ms</span>
                      {log.model && <span>{log.model}</span>}
                    </div>
                  </div>
                )}
                {log.type === "error" && (
                  <div className="border-l-2 border-red-600/50 pl-3 ml-4">
                    <div className="text-red-300 whitespace-pre-wrap break-words leading-relaxed">{log.content}</div>
                    {log.exitCode !== undefined && log.exitCode !== 0 && (
                      <div className="flex gap-3 mt-2 text-[10px] text-gray-600 uppercase">
                        <span className="text-red-400">EXIT {log.exitCode}</span>
                        {log.durationMs !== undefined && <span>{log.durationMs}ms</span>}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
            {pending && (
              <div className="flex gap-2 text-gray-500 items-center">
                <span className="text-green-400">❯</span>
                <span className="flex items-center gap-2 text-xs">
                  <span className="inline-block w-3 h-3 border-2 border-green-500 border-t-transparent rounded-full animate-spin" />
                  正在执行...
                </span>
              </div>
            )}
          </div>

          {/* Input */}
          <div className="border-t border-[#30363d] p-3 flex gap-2 items-end bg-[#161b22]/50">
            <span className="text-green-400 font-mono pb-2.5 shrink-0">❯</span>
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKey}
              disabled={pending}
              rows={1}
              placeholder="输入 prompt（Enter 执行，Shift+Enter 换行）..."
              className="flex-1 bg-transparent text-gray-200 text-sm font-mono resize-none outline-none placeholder-gray-600 leading-relaxed"
              style={{ minHeight: "2rem", maxHeight: "8rem" }}
              onInput={(e) => {
                const t = e.currentTarget;
                t.style.height = "auto";
                t.style.height = Math.min(t.scrollHeight, 128) + "px";
              }}
            />
            <button
              onClick={submit}
              disabled={pending || !input.trim()}
              className="shrink-0 px-3 py-1.5 bg-green-700 hover:bg-green-600 disabled:opacity-30 disabled:cursor-not-allowed text-white text-xs rounded-lg font-mono transition-colors mb-0.5"
            >
              执行
            </button>
          </div>
        </div>
      )}

      {/* Skills view */}
      {view === "skills" && (
        <div className="bg-[#0d1117] border border-[#30363d] rounded-xl p-4">
          {skillsLoading && (
            <div className="text-gray-500 text-sm text-center py-8">加载技能库...</div>
          )}
          {!skillsLoading && skills.length === 0 && (
            <div className="text-gray-600 text-sm text-center py-8">未找到已安装的技能</div>
          )}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 max-h-96 overflow-y-auto">
            {skills.map((s) => (
              <div key={s.name} className="border border-[#30363d] rounded-lg px-4 py-3 bg-[#161b22] hover:border-[#444c56] transition-colors">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-green-400 font-mono text-xs">◈</span>
                  <span className="text-white text-sm font-medium font-mono">{s.name}</span>
                  {s.hasAgents && (
                    <span className="ml-auto text-[10px] px-1.5 py-0.5 rounded bg-blue-900/40 text-blue-400 border border-blue-800/50">agents</span>
                  )}
                </div>
                {s.description && (
                  <p className="text-gray-500 text-xs leading-relaxed line-clamp-2">{s.description}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
