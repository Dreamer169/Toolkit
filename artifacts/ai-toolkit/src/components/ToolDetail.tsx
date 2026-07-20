import { useEffect } from "react";
import { Tool } from "@/data/tools";

const difficultyColor = {
  简单: "text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
  中等: "text-yellow-400 bg-yellow-500/10 border-yellow-500/20",
  高级: "text-red-400 bg-red-500/10 border-red-500/20",
};

interface Props {
  tool: Tool;
  onClose: () => void;
}

export function ToolDetail({ tool, onClose }: Props) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", handler);
      document.body.style.overflow = "";
    };
  }, [onClose]);

  const githubUrl = `https://github.com/adminlove520/AI-Account-Toolkit/tree/main/${tool.githubPath}`;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" />
      <div
        className="relative bg-[#161b22] border border-[#30363d] rounded-2xl shadow-2xl w-full max-w-2xl max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 bg-[#161b22] border-b border-[#21262d] px-6 py-4 flex items-start justify-between gap-4 z-10">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap mb-1">
              <span
                className={`text-xs px-2 py-0.5 rounded-full border ${difficultyColor[tool.difficulty]}`}
              >
                {tool.difficulty}
              </span>
              {tool.hasWebUI && (
                <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                  含 Web 界面
                </span>
              )}
              <span className="text-xs px-2 py-0.5 rounded-full bg-[#21262d] text-gray-400">
                {tool.language}
              </span>
            </div>
            <h2 className="text-xl font-bold text-white leading-tight">
              {tool.name}
            </h2>
            <p className="text-sm text-gray-500 font-mono mt-0.5">
              {tool.nameEn}
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-2 hover:bg-[#21262d] rounded-lg text-gray-400 hover:text-white transition-all shrink-0"
          >
            <svg
              className="w-5 h-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>

        <div className="px-6 py-6 space-y-6">
          <div>
            <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
              项目描述
            </h3>
            <p className="text-gray-300 text-sm leading-relaxed">
              {tool.description}
            </p>
          </div>

          <div>
            <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">
              主要功能
            </h3>
            <ul className="space-y-2">
              {tool.features.map((f, i) => (
                <li key={i} className="flex items-start gap-2 text-sm">
                  <span className="text-blue-500 mt-0.5 shrink-0">▸</span>
                  <span className="text-gray-300">{f}</span>
                </li>
              ))}
            </ul>
          </div>

          <div>
            <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">
              技术栈
            </h3>
            <div className="flex flex-wrap gap-2">
              {tool.techStack.map((tech) => (
                <span
                  key={tech}
                  className="text-xs px-3 py-1 bg-[#0d1117] border border-[#30363d] rounded-full text-gray-300"
                >
                  {tech}
                </span>
              ))}
            </div>
          </div>

          <div>
            <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">
              标签
            </h3>
            <div className="flex flex-wrap gap-2">
              {tool.tags.map((tag) => (
                <span
                  key={tag}
                  className="text-xs px-2 py-1 bg-blue-500/10 border border-blue-500/20 rounded-md text-blue-400"
                >
                  {tag}
                </span>
              ))}
            </div>
          </div>

          <div className="pt-2">
            <a
              href={githubUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center justify-center gap-2 w-full py-3 bg-[#21262d] hover:bg-[#30363d] border border-[#30363d] hover:border-[#4a5568] rounded-xl text-gray-200 hover:text-white transition-all font-medium"
            >
              <svg viewBox="0 0 24 24" className="w-5 h-5 fill-current">
                <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0 0 24 12c0-6.63-5.37-12-12-12z" />
              </svg>
              在 GitHub 查看源码
            </a>
          </div>

          {tool.hasWebUI && (
            <div className="bg-emerald-500/5 border border-emerald-500/20 rounded-xl p-4">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-emerald-400 text-sm font-semibold">
                  此工具包含 Web 管理界面
                </span>
              </div>
              <p className="text-xs text-gray-400">
                该工具提供完整的 Web UI，可在浏览器中操作。部署后访问对应端口即可使用。
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
