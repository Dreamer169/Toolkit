import { useState } from "react";

const BASE = import.meta.env.BASE_URL.replace(/\/$/, "");

interface ServiceTool {
  id: string;
  name: string;
  desc: string;
  path: string;
  status: "running";
}

interface CliTool {
  id: string;
  name: string;
  desc: string;
  commands: string[];
}

interface RefOnly {
  id: string;
  name: string;
  desc: string;
  reason: string;
}

const RUNNING_SERVICES: ServiceTool[] = [
  { id: "toolify", name: "Toolify", desc: "工具聚合 / API 转发服务", path: "/general-tools/toolify/", status: "running" },
  { id: "grok2api", name: "grok2api", desc: "Grok 转 OpenAI 兼容 API 网关", path: "/general-tools/grok2api/health", status: "running" },
  { id: "chatgpt2api", name: "chatgpt2api", desc: "ChatGPT 账号池转 API 网关", path: "/general-tools/chatgpt2api/", status: "running" },
  { id: "mail-hub", name: "mail-hub", desc: "多邮箱聚合收发中心", path: "/general-tools/mail-hub/", status: "running" },
  { id: "codex-patcher", name: "codex-session-patcher", desc: "AI 编程会话清理 / 补丁 Web 面板", path: "/general-tools/codex-patcher/", status: "running" },
  { id: "resin", name: "Resin", desc: "代理节点管理 / 订阅转换服务", path: "/general-tools/resin/", status: "running" },
];

const CLI_TOOLS: CliTool[] = [
  {
    id: "exa-register",
    name: "exa-register",
    desc: "Exa 账号自动注册脚本（一次性运行，非常驻服务）",
    commands: [
      "cd /data/Toolkit/reference-tools/Register/exa-register",
      "./venv/bin/python exa_core.py",
    ],
  },
  {
    id: "grok-register",
    name: "grok-register",
    desc: "Grok 账号自动注册脚本 — 产出的 SSO token 每 5 分钟自动导入 grok2api 账号池（已联动，无需手动搬运）",
    commands: [
      "cd /data/Toolkit/reference-tools/Register/grok-register",
      "./venv/bin/python grok.py --email-provider luckmail",
    ],
  },
  {
    id: "openai-register",
    name: "openai-register",
    desc: "OpenAI 账号自动注册脚本 — 产出的 token 每 5 分钟自动导入 chatgpt2api 账号池（已联动，无需手动搬运）",
    commands: [
      "cd /data/Toolkit/reference-tools/Register/openai-register",
      "./venv/bin/python openai_register.py --once",
    ],
  },
  {
    id: "tavily-register",
    name: "tavily-register",
    desc: "Tavily 账号批量注册脚本",
    commands: [
      "cd /data/Toolkit/reference-tools/Register/tavily-register",
      "./venv/bin/python batch_signup.py -n 20",
    ],
  },
  {
    id: "baiqi-register-template",
    name: "baiqi-register-template",
    desc: "通用注册脚本模板框架，可基于此扩展新的注册渠道",
    commands: [
      "cd /data/Toolkit/reference-tools/baiqi-register-template",
      "./venv/bin/python run.py --help",
    ],
  },
];

const REFERENCE_ONLY: RefOnly[] = [
  { id: "crawl4ai", name: "crawl4ai", desc: "AI 友好的网页爬虫框架", reason: "官方以 Docker 部署为主，未封装为独立 API 服务，暂作代码参考（后续可按需补建 API 层）" },
  { id: "mihomo", name: "mihomo (仓库名同名)", desc: "崩坏：星穹铁道玩家数据解析 Python 库", reason: "该 GitHub 仓库实际是第三方数据模型库，并非代理内核软件，无法作为网络服务部署" },
  { id: "ruyipage", name: "ruyipage", desc: "网页抓取 / 指纹浏览器工具库", reason: "是 Python 库而非服务，供其他项目按需 import 使用" },
  { id: "ctf-sandbox", name: "CTF-Sandbox-Orchestrator", desc: "CTF 靶场训练题目合集", reason: "是安全训练题目集合，不是可部署的服务" },
  { id: "fastapi", name: "fastapi", desc: "FastAPI 框架源码", reason: "是开发框架本身，不是应用" },
  { id: "ponytail", name: "ponytail", desc: "AI Agent 技能插件", reason: "是本地开发工具插件，非网络服务" },
  { id: "mattpocock-skills", name: "mattpocock-skills", desc: "AI Agent 技能文档集", reason: "文档/技能素材，非服务" },
  { id: "superpowers", name: "Superpowers", desc: "AI Agent 技能库", reason: "技能素材库，非服务" },
  { id: "baiqi-cf-worker-mihomo", name: "baiqi-cf-worker-mihomo", desc: "Cloudflare Worker 脚本", reason: "仅能运行在 Cloudflare Workers 平台，无法部署到本 VPS" },
  { id: "open-reverselab", name: "open-reverselab", desc: "逆向工程知识库", reason: "是文档 / 案例知识库，非可运行服务" },
];

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-[#161b22] border border-[#30363d] rounded-xl p-4 hover:border-[#3d444d] transition-colors">
      {children}
    </div>
  );
}

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span className={`inline-block w-2 h-2 rounded-full ${ok ? "bg-green-500" : "bg-gray-500"} mr-1.5`} />
  );
}

export default function GeneralTools() {
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const copy = (id: string, text: string) => {
    navigator.clipboard.writeText(text).catch(() => {});
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 1500);
  };

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-xl font-bold text-white mb-1">🧩 通用模块</h2>
        <p className="text-sm text-gray-500">
          精选 18 个开源项目，已在本机原生部署（不依赖 Docker），供团队直接调用，避免重复造轮子。
        </p>
      </div>

      <div className="bg-blue-950/30 border border-blue-800/40 rounded-xl p-4">
        <h3 className="text-sm font-semibold text-blue-300 mb-2">🔗 已联动的账号流水线</h3>
        <p className="text-xs text-gray-400 leading-relaxed">
          Register 注册出的账号会由后台定时任务（每 5 分钟）自动搬运进对应网关的账号池，注册即可用，无需手动复制粘贴：
        </p>
        <ul className="text-xs text-gray-400 mt-2 space-y-1 list-disc list-inside">
          <li><code className="text-blue-300">grok-register</code> → 自动写入 <code className="text-blue-300">grok2api</code> 账号池（basic pool）</li>
          <li><code className="text-blue-300">openai-register</code> → 自动写入 <code className="text-blue-300">chatgpt2api</code> 账号池</li>
        </ul>
      </div>

      <section>
        <h3 className="text-sm font-semibold text-gray-300 mb-3 flex items-center gap-2">
          <StatusDot ok /> 常驻在线服务（{RUNNING_SERVICES.length}）
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {RUNNING_SERVICES.map((s) => (
            <Card key={s.id}>
              <div className="flex items-start justify-between mb-2">
                <h4 className="font-semibold text-white text-sm">{s.name}</h4>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-600/20 text-green-400 font-medium">
                  运行中
                </span>
              </div>
              <p className="text-xs text-gray-500 mb-3 leading-relaxed">{s.desc}</p>
              <a
                href={`${BASE}${s.path}`}
                target="_blank"
                rel="noreferrer"
                className="inline-block text-xs px-3 py-1.5 rounded-lg bg-blue-600/20 text-blue-400 hover:bg-blue-600/30 transition-colors"
              >
                打开 →
              </a>
            </Card>
          ))}
        </div>
      </section>

      <section>
        <h3 className="text-sm font-semibold text-gray-300 mb-3 flex items-center gap-2">
          <StatusDot ok={false} /> 命令行工具（{CLI_TOOLS.length}）— 按需手动运行，非常驻服务
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {CLI_TOOLS.map((t) => (
            <Card key={t.id}>
              <h4 className="font-semibold text-white text-sm mb-1">{t.name}</h4>
              <p className="text-xs text-gray-500 mb-3 leading-relaxed">{t.desc}</p>
              <div className="bg-[#0d1117] border border-[#30363d] rounded-lg p-2.5 font-mono text-[11px] text-gray-400 space-y-0.5">
                {t.commands.map((c, i) => (
                  <div key={i} className="whitespace-pre-wrap break-all">{c}</div>
                ))}
              </div>
              <button
                onClick={() => copy(t.id, t.commands.join("\n"))}
                className="mt-2 text-xs px-3 py-1 rounded-lg bg-[#21262d] text-gray-300 hover:bg-[#30363d] transition-colors"
              >
                {copiedId === t.id ? "已复制 ✓" : "复制命令"}
              </button>
            </Card>
          ))}
        </div>
      </section>

      <section>
        <h3 className="text-sm font-semibold text-gray-300 mb-3">📚 仅作代码参考（{REFERENCE_ONLY.length}）— 非服务性质，不部署</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {REFERENCE_ONLY.map((r) => (
            <Card key={r.id}>
              <h4 className="font-semibold text-gray-300 text-sm mb-1">{r.name}</h4>
              <p className="text-xs text-gray-600 mb-2">{r.desc}</p>
              <p className="text-[11px] text-gray-600 italic border-t border-[#30363d] pt-2">{r.reason}</p>
            </Card>
          ))}
        </div>
      </section>
    </div>
  );
}
