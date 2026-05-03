import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ─── 密码保护门 ───────────────────────────────────────────────────────────────
const CORRECT_PASSWORD = "yu123456";
const AUTH_KEY = "toolkit_auth_v1";

function PasswordGate({ children }: { children: React.ReactNode }) {
  const [authed, setAuthed] = useState(() => sessionStorage.getItem(AUTH_KEY) === "1");
  const [input, setInput] = useState("");
  const [error, setError] = useState(false);
  const [show, setShow] = useState(false);
  const [shaking, setShaking] = useState(false);

  if (authed) return <>{children}</>;

  const submit = () => {
    if (input === CORRECT_PASSWORD) {
      sessionStorage.setItem(AUTH_KEY, "1");
      setAuthed(true);
    } else {
      setError(true);
      setShaking(true);
      setTimeout(() => setShaking(false), 500);
      setInput("");
    }
  };

  return (
    <div className="min-h-screen bg-[#0d1117] flex items-center justify-center px-4">
      <div className={`w-full max-w-sm ${shaking ? "animate-shake" : ""}`}>
        <div className="bg-[#161b22] border border-[#30363d] rounded-2xl p-8 shadow-2xl">
          {/* Logo */}
          <div className="flex flex-col items-center mb-8">
            <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-blue-500 to-violet-600 flex items-center justify-center font-bold text-white text-xl shadow-lg mb-4">
              AI
            </div>
            <h1 className="text-white font-bold text-xl">AI Account Toolkit</h1>
            <p className="text-gray-500 text-sm mt-1">请输入访问密码</p>
          </div>

          {/* 输入区 */}
          <form className="space-y-4" onSubmit={e => { e.preventDefault(); submit(); }}>
            <div className="relative">
              <input
                type={show ? "text" : "password"}
                value={input}
                onChange={e => { setInput(e.target.value); setError(false); }}
                placeholder="输入密码..."
                autoFocus
                autoComplete="current-password"
                className={`w-full bg-[#0d1117] border rounded-xl px-4 py-3 text-white text-sm placeholder-gray-600 outline-none transition-all pr-10 ${
                  error
                    ? "border-red-500 focus:border-red-500"
                    : "border-[#30363d] focus:border-blue-500"
                }`}
              />
              <button
                type="button"
                onClick={() => setShow(v => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300 text-sm"
              >
                {show ? "🙈" : "👁️"}
              </button>
            </div>

            {error && (
              <p className="text-red-400 text-xs text-center">密码错误，请重试</p>
            )}

            <button
              type="submit"
              className="w-full py-3 bg-blue-600 hover:bg-blue-500 rounded-xl text-white text-sm font-semibold transition-colors"
            >
              进入
            </button>
          </form>
        </div>

        <p className="text-center text-gray-700 text-xs mt-6">
          AI Account Toolkit — 仅供授权用户访问
        </p>
      </div>

      <style>{`
        @keyframes shake {
          0%, 100% { transform: translateX(0); }
          20% { transform: translateX(-8px); }
          40% { transform: translateX(8px); }
          60% { transform: translateX(-6px); }
          80% { transform: translateX(6px); }
        }
        .animate-shake { animation: shake 0.5s ease-in-out; }
      `}</style>
    </div>
  );
}
import Home from "@/pages/Home";
import TempEmail from "@/pages/TempEmail";
import KeyChecker from "@/pages/KeyChecker";
import TokenBatch from "@/pages/TokenBatch";
import IpChecker from "@/pages/IpChecker";
import BulkEmail from "@/pages/BulkEmail";
import InfoGenerator from "@/pages/InfoGenerator";
import FreeEmail from "@/pages/FreeEmail";
import MachineReset from "@/pages/MachineReset";
import Fingerprint from "@/pages/Fingerprint";
import OutlookManager from "@/pages/OutlookManager";
import DataManager from "@/pages/DataManager";
import FullWorkflow from "@/pages/FullWorkflow";
import Monitor from "@/pages/Monitor";
import CursorRegister from "@/pages/CursorRegister";
import Sub2ApiManager from "@/pages/Sub2ApiManager";
import MailCenter from "@/pages/MailCenter";
import AIAssistant from "@/pages/AIAssistant";
import ReplitRegister from "@/pages/ReplitRegister";
import CaptchaRecognition from "@/pages/CaptchaRecognition";
import WafBypass from "@/pages/WafBypass";
import NovproxyPanel from "@/pages/NovproxyPanel";
import SmsCenter from "@/pages/SmsCenter";
import WebshareRegister from "@/pages/WebshareRegister";
import OxylabsRegister from "@/pages/OxylabsRegister";

const queryClient = new QueryClient();

type Tab = "home" | "agent" | "email" | "bulk-email" | "free-email" | "keycheck" | "tokencheck" | "ip" | "info" | "machine-reset" | "fingerprint" | "outlook" | "mail-center" | "cursor-register" | "replit-register" | "sub2api" | "team-register" | "openai-pool" | "data-manager" | "full-workflow" | "monitor" | "sms-center" | "captcha" | "waf-bypass" | "novproxy" | "webshare-register" | "oxylabs-register";

const tabs: { id: Tab; label: string; icon: string; badge?: string }[] = [
  { id: "home",             label: "工具导航",        icon: "🗂️" },
  { id: "agent",            label: "任务中枢",        icon: "🧭", badge: "主功能" },
  { id: "monitor",          label: "实时监控",        icon: "📡", badge: "Live" },
  { id: "full-workflow",    label: "完整工作流",       icon: "🔗", badge: "一键生成" },
  { id: "data-manager",     label: "数据管理中心",     icon: "🗄️", badge: "持久化" },
  { id: "email",            label: "临时邮箱",        icon: "📬", badge: "真实可用" },
  { id: "bulk-email",       label: "批量邮箱",        icon: "📮", badge: "MailTM" },
  { id: "free-email",       label: "免费身份邮箱",     icon: "🆓", badge: "无需Key" },
  { id: "mail-center",      label: "邮件中心",         icon: "✉️",  badge: "三列布局" },
  { id: "outlook",          label: "Outlook 工作流",  icon: "📧", badge: "OAuth2" },
  { id: "cursor-register",  label: "Cursor 自动注册",  icon: "🖱️", badge: "自动化" },
  { id: "replit-register",  label: "Reseek 自动注册", icon: "🤖", badge: "新增" },
  { id: "captcha",          label: "验证码识别",        icon: "🔢", badge: "CNN" },
  { id: "waf-bypass",      label: "WAF 绕过",          icon: "🛡️", badge: "pydoll" },
  { id: "novproxy",         label: "Novproxy 工作流",   icon: "🔐", badge: "pydoll" },
  { id: "sms-center",      label: "短信接收中心",       icon: "📱", badge: "免费号码" },
  { id: "webshare-register", label: "Webshare 注册",    icon: "🌐", badge: "全自动" },
  { id: "oxylabs-register",  label: "Oxylabs 注册",    icon: "🌱", badge: "全自动" },
  { id: "sub2api",          label: "Token 转发管理",   icon: "🚀", badge: "Sub2Api" },
  { id: "keycheck",         label: "Key 验证",        icon: "🔑", badge: "多平台" },
  { id: "tokencheck",       label: "批量检测",        icon: "⚡", badge: "多平台" },
  { id: "ip",               label: "IP 查询",         icon: "🌐", badge: "真实可用" },
  { id: "info",             label: "信息生成",        icon: "👤", badge: "真实可用" },
  { id: "machine-reset",    label: "机器ID重置",      icon: "🔄", badge: "Cursor" },
  { id: "fingerprint",      label: "浏览器指纹",      icon: "🎭", badge: "新增" },
  { id: "team-register",    label: "Team 注册面板",   icon: "🤖", badge: "Python 原版" },
  { id: "openai-pool",      label: "OpenAI账号注册管理",     icon: "🏊", badge: "Python 原版" },
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

            <nav className="flex gap-0.5 py-1.5 overflow-x-auto scrollbar-none">
              {tabs.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm transition-all relative whitespace-nowrap shrink-0 ${
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

        {(tab === "team-register" || tab === "openai-pool") ? (
          <div className="flex-1 flex flex-col" style={{ height: "calc(100vh - 120px)" }}>
            <iframe
              src={tab === "team-register" ? "/team-all-in-one/" : "/openai-pool/"}
              className="w-full flex-1 border-0"
              title={tab === "team-register" ? "ChatGPT Team 注册面板" : "OpenAI 账号注册管理"}
              sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
            />
          </div>
        ) : (
          <main className="max-w-7xl mx-auto px-4 py-8">
            {tab === "home" && <Home />}
            {tab === "agent" && <AIAssistant onNavigate={(nextTab) => setTab(nextTab as Tab)} />}
            {tab === "email" && <TempEmail />}
            {tab === "bulk-email" && <BulkEmail />}
            {tab === "free-email" && <FreeEmail />}
            {tab === "keycheck" && <KeyChecker />}
            {tab === "tokencheck" && <TokenBatch />}
            {tab === "ip" && <IpChecker />}
            {tab === "info" && <InfoGenerator />}
            {tab === "machine-reset" && <MachineReset />}
            {tab === "fingerprint" && <Fingerprint />}
            {tab === "mail-center" && <MailCenter />}
            {tab === "outlook" && <OutlookManager />}
            {tab === "cursor-register" && <CursorRegister />}
            {tab === "replit-register" && <ReplitRegister />}
            {tab === "captcha" && <CaptchaRecognition />}
            {tab === "waf-bypass" && <WafBypass />}
            {tab === "novproxy" && <NovproxyPanel />}
            {tab === "sms-center" && <SmsCenter />}
            {tab === "webshare-register" && <WebshareRegister />}
            {tab === "oxylabs-register"  && <OxylabsRegister  />}
            {tab === "sub2api" && <Sub2ApiManager />}
            {tab === "data-manager" && <DataManager />}
            {tab === "full-workflow" && <FullWorkflow />}
            {tab === "monitor" && <Monitor />}
          </main>
        )}

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

function Root() {
  return (
    <PasswordGate>
      <App />
    </PasswordGate>
  );
}

export default Root;
