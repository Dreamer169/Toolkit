import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Home from "@/pages/Home";
import TempEmail from "@/pages/TempEmail";
import KeyChecker from "@/pages/KeyChecker";
import TokenBatch from "@/pages/TokenBatch";

const queryClient = new QueryClient();

type Tab = "home" | "email" | "keycheck" | "tokencheck";

const tabs: { id: Tab; label: string; icon: string; badge?: string }[] = [
  { id: "home", label: "工具导航", icon: "🗂️" },
  { id: "email", label: "临时邮箱", icon: "📬", badge: "真实可用" },
  { id: "keycheck", label: "Key 验证", icon: "🔑", badge: "真实可用" },
  { id: "tokencheck", label: "批量检测", icon: "⚡", badge: "真实可用" },
];

function App() {
  const [tab, setTab] = useState<Tab>("home");

  return (
    <QueryClientProvider client={queryClient}>
      <div className="min-h-screen bg-[#0d1117] text-gray-100">
        <header className="border-b border-[#21262d] bg-[#161b22] sticky top-0 z-40">
          <div className="max-w-7xl mx-auto px-4">
            <div className="flex items-center justify-between py-3 border-b border-[#21262d]">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-violet-600 flex items-center justify-center font-bold text-white text-xs shadow-lg">
                  AI
                </div>
                <div>
                  <h1 className="font-bold text-white text-base leading-none">
                    AI Account Toolkit
                  </h1>
                  <p className="text-xs text-gray-500 mt-0.5">AI 账号注册与管理工具集</p>
                </div>
              </div>
              <a
                href="https://github.com/adminlove520/AI-Account-Toolkit"
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#21262d] hover:bg-[#30363d] border border-[#30363d] text-xs text-gray-300 hover:text-white transition-all"
              >
                <svg viewBox="0 0 24 24" className="w-3.5 h-3.5 fill-current">
                  <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0 0 24 12c0-6.63-5.37-12-12-12z" />
                </svg>
                GitHub
              </a>
            </div>

            <nav className="flex gap-1 py-1.5">
              {tabs.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm transition-all relative ${
                    tab === t.id
                      ? "bg-blue-600/20 text-blue-400"
                      : "text-gray-400 hover:text-gray-200 hover:bg-[#21262d]"
                  }`}
                >
                  <span>{t.icon}</span>
                  <span>{t.label}</span>
                  {t.badge && (
                    <span className="text-xs px-1.5 py-0.5 bg-emerald-500/20 text-emerald-400 rounded-full leading-none">
                      {t.badge}
                    </span>
                  )}
                </button>
              ))}
            </nav>
          </div>
        </header>

        <main className="max-w-7xl mx-auto px-4 py-8">
          {tab === "home" && <HomeContent />}
          {tab === "email" && <TempEmail />}
          {tab === "keycheck" && <KeyChecker />}
          {tab === "tokencheck" && <TokenBatch />}
        </main>

        <footer className="mt-8 border-t border-[#21262d]">
          <div className="max-w-7xl mx-auto px-4 py-6 text-center text-gray-600 text-xs">
            AI Account Toolkit — 开源项目，仅供学习研究使用 &nbsp;|&nbsp;{" "}
            <a
              href="https://github.com/adminlove520/AI-Account-Toolkit"
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 hover:text-blue-500"
            >
              github.com/adminlove520/AI-Account-Toolkit
            </a>
          </div>
        </footer>
      </div>
    </QueryClientProvider>
  );
}

function HomeContent() {
  return <Home />;
}

export default App;
