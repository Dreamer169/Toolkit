import { Tool } from "@/data/tools";

interface Props {
  tools: Tool[];
}

export function StatsBar({ tools }: Props) {
  const webUICount = tools.filter((t) => t.hasWebUI).length;
  const pythonCount = tools.filter((t) => t.language === "Python").length;
  const jsCount = tools.filter(
    (t) => t.language === "JavaScript"
  ).length;

  const stats = [
    {
      label: "工具总数",
      value: tools.length,
      icon: "🛠️",
      color: "text-blue-400",
      bg: "bg-blue-500/10 border-blue-500/20",
    },
    {
      label: "含 Web 界面",
      value: webUICount,
      icon: "🖥️",
      color: "text-emerald-400",
      bg: "bg-emerald-500/10 border-emerald-500/20",
    },
    {
      label: "Python 工具",
      value: pythonCount,
      icon: "🐍",
      color: "text-yellow-400",
      bg: "bg-yellow-500/10 border-yellow-500/20",
    },
    {
      label: "JS 工具",
      value: jsCount,
      icon: "⚡",
      color: "text-orange-400",
      bg: "bg-orange-500/10 border-orange-500/20",
    },
  ];

  return (
    <div>
      <div className="mb-6">
        <h2 className="text-2xl font-bold text-white mb-1">
          AI 账号注册与管理工具集
        </h2>
        <p className="text-gray-400">
          涵盖 ChatGPT、Claude、Gemini、Codex、Cursor、Grok 的批量注册、Token
          管理、临时邮箱服务等 30+ 工具的完整工具集
        </p>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {stats.map((s) => (
          <div
            key={s.label}
            className={`rounded-xl border p-4 ${s.bg} flex items-center gap-3`}
          >
            <div className="text-2xl">{s.icon}</div>
            <div>
              <div className={`text-2xl font-bold ${s.color}`}>{s.value}</div>
              <div className="text-xs text-gray-400">{s.label}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
