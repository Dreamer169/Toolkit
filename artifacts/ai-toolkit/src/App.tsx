import { useState, useRef, useEffect } from "react";
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
            <div className="flex flex-col items-center mb-8">
              <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-blue-500 to-violet-600 flex items-center justify-center font-bold text-white text-xl shadow-lg mb-4">
                OL
              </div>
              <h1 className="text-white font-bold text-xl">Outlook 工作台</h1>
              <p className="text-gray-500 text-sm mt-1">请输入访问密码</p>
            </div>
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
                    error ? "border-red-500 focus:border-red-500" : "border-[#30363d] focus:border-blue-500"
                  }`}
                />
                <button type="button" onClick={() => setShow(v => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300 text-sm">
                  {show ? "🙈" : "👁️"}
                </button>
              </div>
              {error && <p className="text-red-400 text-xs text-center">密码错误，请重试</p>}
              <button type="submit"
                className="w-full py-3 bg-blue-600 hover:bg-blue-500 rounded-xl text-white text-sm font-semibold transition-colors">
                进入
              </button>
            </form>
          </div>
          <p className="text-center text-gray-700 text-xs mt-6">Outlook 工作台 — 仅供授权用户访问</p>
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
  import UnitoolLogin from "@/pages/UnitoolLogin";
  import SmsCenter from "@/pages/SmsCenter";
  import WebshareRegister from "@/pages/WebshareRegister";
  import OxylabsRegister from "@/pages/OxylabsRegister";

  const queryClient = new QueryClient();

  type Tab = "home" | "agent" | "email" | "bulk-email" | "free-email" | "keycheck" | "tokencheck" | "ip" | "info" | "machine-reset" | "fingerprint" | "outlook" | "mail-center" | "cursor-register" | "replit-register" | "sub2api" | "team-register" | "openai-pool" | "data-manager" | "full-workflow" | "monitor" | "sms-center" | "captcha" | "waf-bypass" | "unitool-login" | "webshare-register" | "oxylabs-register";

  // ─── 主导航标签（常驻显示）───────────────────────────────────────────────────
  const PRIMARY_TABS: { id: Tab; label: string; icon: string }[] = [
    { id: "full-workflow", label: "完整工作流", icon: "🔗" },
    { id: "monitor",       label: "实时监控",   icon: "📡" },
    { id: "mail-center",   label: "邮件中心",   icon: "✉️"  },
    { id: "data-manager",  label: "数据管理",   icon: "🗄️" },
    { id: "outlook",       label: "Outlook 工作流", icon: "📧" },
  ];

  // ─── 更多工具（折叠在下拉菜单中）─────────────────────────────────────────────
  const MORE_TABS: { id: Tab; label: string; icon: string; group: string }[] = [
    { id: "agent",            label: "任务中枢",        icon: "🧭", group: "AI 工具" },
    { id: "home",             label: "工具导航",        icon: "🗂️", group: "AI 工具" },
    { id: "email",            label: "临时邮箱",        icon: "📬", group: "邮箱工具" },
    { id: "bulk-email",       label: "批量邮箱",        icon: "📮", group: "邮箱工具" },
    { id: "free-email",       label: "免费身份邮箱",    icon: "🆓", group: "邮箱工具" },
    { id: "sms-center",       label: "短信接收中心",    icon: "📱", group: "邮箱工具" },
    { id: "cursor-register",  label: "Cursor 自动注册", icon: "🖱️", group: "自动注册" },
    { id: "replit-register",  label: "Reseek 自动注册", icon: "🤖", group: "自动注册" },
    { id: "webshare-register",label: "Webshare 注册",   icon: "🌐", group: "自动注册" },
    { id: "oxylabs-register", label: "Oxylabs 注册",   icon: "🌱", group: "自动注册" },
    { id: "captcha",          label: "验证码识别",      icon: "🔢", group: "工具箱" },
    { id: "waf-bypass",       label: "WAF 绕过",        icon: "🛡️", group: "工具箱" },
    { id: "unitool-login",    label: "unitool 自动登录", icon: "🔓", group: "工具箱" },
    { id: "sub2api",          label: "Token 转发管理",  icon: "🚀", group: "工具箱" },
    { id: "keycheck",         label: "Key 验证",        icon: "🔑", group: "工具箱" },
    { id: "tokencheck",       label: "批量检测",        icon: "⚡", group: "工具箱" },
    { id: "ip",               label: "IP 查询",         icon: "🌐", group: "工具箱" },
    { id: "info",             label: "信息生成",        icon: "👤", group: "工具箱" },
    { id: "machine-reset",    label: "机器ID重置",      icon: "🔄", group: "工具箱" },
    { id: "fingerprint",      label: "浏览器指纹",      icon: "🎭", group: "工具箱" },
    { id: "team-register",    label: "Team 注册面板",   icon: "🤖", group: "旧版面板" },
    { id: "openai-pool",      label: "OpenAI 注册管理", icon: "🏊", group: "旧版面板" },
  ];

  const PRIMARY_IDS = new Set(PRIMARY_TABS.map(t => t.id));

  function groupMoreTabs(tabs: typeof MORE_TABS) {
    const groups: Record<string, typeof MORE_TABS> = {};
    for (const t of tabs) {
      if (!groups[t.group]) groups[t.group] = [];
      groups[t.group].push(t);
    }
    return groups;
  }

  function App() {
    const [tab, setTab] = useState<Tab>("full-workflow");
    const [moreOpen, setMoreOpen] = useState(false);
    const moreRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
      const handler = (e: MouseEvent) => {
        if (moreRef.current && !moreRef.current.contains(e.target as Node)) {
          setMoreOpen(false);
        }
      };
      document.addEventListener("mousedown", handler);
      return () => document.removeEventListener("mousedown", handler);
    }, []);

    const isMoreActive = !PRIMARY_IDS.has(tab);
    const groups = groupMoreTabs(MORE_TABS);
    const activeMoreTab = MORE_TABS.find(t => t.id === tab);

    const switchTab = (id: Tab) => {
      setTab(id);
      setMoreOpen(false);
    };

    return (
      <QueryClientProvider client={queryClient}>
        <div className="min-h-screen bg-[#0d1117] text-gray-100">
          <header className="border-b border-[#21262d] bg-[#161b22] sticky top-0 z-40">
            <div className="max-w-7xl mx-auto px-4">
              <div className="flex items-center justify-between py-3 border-b border-[#21262d]">
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-violet-600 flex items-center justify-center font-bold text-white text-xs shadow-lg">
                    OL
                  </div>
                  <div>
                    <h1 className="font-bold text-white text-base leading-none">Outlook 工作台</h1>
                    <p className="text-xs text-gray-500 mt-0.5">注册 · 收发件 · 监控</p>
                  </div>
                </div>
                {isMoreActive && activeMoreTab && (
                  <div className="flex items-center gap-2 text-xs text-gray-400">
                    <span>更多工具</span>
                    <span className="text-gray-600">/</span>
                    <span className="text-white">{activeMoreTab.icon} {activeMoreTab.label}</span>
                  </div>
                )}
              </div>
              <nav className="flex items-center gap-0.5 py-1.5">
                {PRIMARY_TABS.map((t) => (
                  <button
                    key={t.id}
                    onClick={() => switchTab(t.id)}
                    className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm transition-all whitespace-nowrap ${
                      tab === t.id
                        ? "bg-blue-600/20 text-blue-400 font-medium"
                        : "text-gray-400 hover:text-gray-200 hover:bg-[#21262d]"
                    }`}
                  >
                    <span>{t.icon}</span>
                    <span>{t.label}</span>
                  </button>
                ))}
                <div className="w-px h-5 bg-[#30363d] mx-1" />
                <div ref={moreRef} className="relative">
                  <button
                    onClick={() => setMoreOpen(v => !v)}
                    className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm transition-all whitespace-nowrap ${
                      isMoreActive
                        ? "bg-violet-600/20 text-violet-400 font-medium"
                        : moreOpen
                        ? "bg-[#21262d] text-gray-200"
                        : "text-gray-400 hover:text-gray-200 hover:bg-[#21262d]"
                    }`}
                  >
                    <span>🧰</span>
                    <span>更多工具</span>
                    <svg className={`w-3.5 h-3.5 transition-transform ${moreOpen ? "rotate-180" : ""}`}
                      fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                    </svg>
                  </button>
                  {moreOpen && (
                    <div className="absolute left-0 top-full mt-1 w-[520px] bg-[#161b22] border border-[#30363d] rounded-xl shadow-2xl z-50 p-3 overflow-auto max-h-[70vh]">
                      <div className="grid grid-cols-2 gap-3">
                        {Object.entries(groups).map(([group, items]) => (
                          <div key={group}>
                            <p className="text-[10px] font-semibold text-gray-600 uppercase tracking-wider px-1 mb-1.5">{group}</p>
                            <div className="space-y-0.5">
                              {items.map(t => (
                                <button
                                  key={t.id}
                                  onClick={() => switchTab(t.id)}
                                  className={`w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-sm transition-all text-left ${
                                    tab === t.id
                                      ? "bg-violet-600/20 text-violet-400"
                                      : "text-gray-400 hover:text-gray-200 hover:bg-[#21262d]"
                                  }`}
                                >
                                  <span className="text-base">{t.icon}</span>
                                  <span>{t.label}</span>
                                </button>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </nav>
            </div>
          </header>

          {(tab === "team-register" || tab === "openai-pool") ? (
            <div className="flex-1 flex flex-col" style={{ height: "calc(100vh - 112px)" }}>
              <iframe
                src={tab === "team-register" ? "/team-all-in-one/" : "/openai-pool/"}
                className="w-full flex-1 border-0"
                title={tab === "team-register" ? "ChatGPT Team 注册面板" : "OpenAI 账号注册管理"}
                sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
              />
            </div>
          ) : (
            <main className="max-w-7xl mx-auto px-4 py-8">
              {tab === "home"              && <Home />}
              {tab === "agent"             && <AIAssistant onNavigate={(nextTab) => switchTab(nextTab as Tab)} />}
              {tab === "email"             && <TempEmail />}
              {tab === "bulk-email"        && <BulkEmail />}
              {tab === "free-email"        && <FreeEmail />}
              {tab === "keycheck"          && <KeyChecker />}
              {tab === "tokencheck"        && <TokenBatch />}
              {tab === "ip"                && <IpChecker />}
              {tab === "info"              && <InfoGenerator />}
              {tab === "machine-reset"     && <MachineReset />}
              {tab === "fingerprint"       && <Fingerprint />}
              {tab === "mail-center"       && <MailCenter />}
              {tab === "outlook"           && <OutlookManager />}
              {tab === "cursor-register"   && <CursorRegister />}
              {tab === "replit-register"   && <ReplitRegister />}
              {tab === "captcha"           && <CaptchaRecognition />}
              {tab === "waf-bypass"        && <WafBypass />}
              {tab === "unitool-login"     && <UnitoolLogin />}
              {tab === "sms-center"        && <SmsCenter />}
              {tab === "webshare-register" && <WebshareRegister />}
              {tab === "oxylabs-register"  && <OxylabsRegister />}
              {tab === "sub2api"           && <Sub2ApiManager />}
              {tab === "data-manager"      && <DataManager />}
              {tab === "full-workflow"     && <FullWorkflow />}
              {tab === "monitor"           && <Monitor />}
            </main>
          )}
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
  