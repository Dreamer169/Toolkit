import { Tool } from "@/data/tools";

const difficultyColor = {
  简单: "text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
  中等: "text-yellow-400 bg-yellow-500/10 border-yellow-500/20",
  高级: "text-red-400 bg-red-500/10 border-red-500/20",
};

const categoryColor: Record<string, string> = {
  openai: "bg-green-500/10 text-green-400",
  codex: "bg-purple-500/10 text-purple-400",
  claude: "bg-orange-500/10 text-orange-400",
  gemini: "bg-blue-500/10 text-blue-400",
  cursor: "bg-cyan-500/10 text-cyan-400",
  grok: "bg-pink-500/10 text-pink-400",
  email: "bg-indigo-500/10 text-indigo-400",
  general: "bg-gray-500/10 text-gray-400",
};

const categoryLabel: Record<string, string> = {
  openai: "OpenAI",
  codex: "Codex",
  claude: "Claude",
  gemini: "Gemini",
  cursor: "Cursor",
  grok: "Grok",
  email: "邮箱",
  general: "通用",
};

interface Props {
  tool: Tool;
  onClick: () => void;
}

export function ToolCard({ tool, onClick }: Props) {
  return (
    <button
      onClick={onClick}
      className="text-left w-full bg-[#161b22] border border-[#21262d] rounded-xl p-5 hover:border-[#388bfd]/50 hover:bg-[#1c2128] transition-all group"
    >
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className={`text-xs px-2 py-0.5 rounded-full font-medium ${categoryColor[tool.category] ?? "bg-gray-500/10 text-gray-400"}`}
          >
            {categoryLabel[tool.category] ?? tool.category}
          </span>
          {tool.hasWebUI && (
            <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              Web 界面
            </span>
          )}
        </div>
        <span
          className={`text-xs px-2 py-0.5 rounded-full border whitespace-nowrap ${difficultyColor[tool.difficulty]}`}
        >
          {tool.difficulty}
        </span>
      </div>

      <h3 className="font-semibold text-white group-hover:text-blue-400 transition-colors text-sm leading-snug mb-1">
        {tool.name}
      </h3>
      <p className="text-xs text-gray-500 font-mono mb-3">{tool.nameEn}</p>

      <p className="text-xs text-gray-400 leading-relaxed line-clamp-3 mb-3">
        {tool.description}
      </p>

      <div className="flex items-center gap-1.5 flex-wrap">
        {tool.tags.slice(0, 3).map((tag) => (
          <span
            key={tag}
            className="text-xs px-2 py-0.5 bg-[#21262d] rounded-md text-gray-500"
          >
            {tag}
          </span>
        ))}
        {tool.tags.length > 3 && (
          <span className="text-xs text-gray-600">+{tool.tags.length - 3}</span>
        )}
      </div>

      <div className="mt-3 pt-3 border-t border-[#21262d] flex items-center justify-between">
        <span className="text-xs text-gray-600">{tool.language}</span>
        <span className="text-xs text-blue-500 group-hover:text-blue-400 transition-colors">
          查看详情 →
        </span>
      </div>
    </button>
  );
}
