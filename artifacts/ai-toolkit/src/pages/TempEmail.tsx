import { useState, useEffect, useCallback } from "react";

const API = import.meta.env.BASE_URL.replace(/\/$/, "") + "/../api/tools";

interface Message {
  id: string;
  from: { address: string; name: string };
  subject: string;
  intro: string;
  createdAt: string;
  seen: boolean;
}

interface MessageDetail {
  id: string;
  from: { address: string; name: string };
  subject: string;
  text: string;
  html: string[];
  createdAt: string;
}

function randomStr(len = 10) {
  return Math.random().toString(36).slice(2, 2 + len);
}

export default function TempEmail() {
  const [domains, setDomains] = useState<string[]>([]);
  const [address, setAddress] = useState("");
  const [password] = useState(() => randomStr(12));
  const [token, setToken] = useState("");
  const [accountId, setAccountId] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [selected, setSelected] = useState<MessageDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [polling, setPolling] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    fetch("/api/tools/email/domains")
      .then((r) => r.json())
      .then((d) => {
        if (d.domains?.length) {
          setDomains(d.domains);
          setAddress(`${randomStr(10)}@${d.domains[0]}`);
        }
      });
  }, []);

  const fetchMessages = useCallback(async (tok: string) => {
    setLoading(true);
    const r = await fetch("/api/tools/email/messages", {
      headers: { "x-mail-token": tok },
    });
    const d = await r.json();
    if (d.success) setMessages(d.messages ?? []);
    setLoading(false);
  }, []);

  useEffect(() => {
    if (!token || !polling) return;
    const id = setInterval(() => fetchMessages(token), 5000);
    return () => clearInterval(id);
  }, [token, polling, fetchMessages]);

  const create = async () => {
    if (!address) return;
    setCreating(true);
    setError("");
    const r = await fetch("/api/tools/email/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ address, password }),
    });
    const d = await r.json();
    setCreating(false);
    if (!d.success) {
      setError(d.error ?? "创建失败");
      return;
    }
    setToken(d.token);
    setAccountId(d.account?.id ?? "");
    setPolling(true);
    fetchMessages(d.token);
  };

  const openMessage = async (msg: Message) => {
    if (!token) return;
    const r = await fetch(`/api/tools/email/messages/${msg.id}`, {
      headers: { "x-mail-token": token },
    });
    const d = await r.json();
    if (d.success) setSelected(d.message);
  };

  const copy = () => {
    navigator.clipboard.writeText(address);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const reset = () => {
    setToken("");
    setAccountId("");
    setMessages([]);
    setSelected(null);
    setPolling(false);
    setAddress(`${randomStr(10)}@${domains[0] ?? ""}`);
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-white mb-1">临时邮箱</h2>
        <p className="text-sm text-gray-400">
          使用 MailTM 免费 API 创建临时邮箱，实时接收邮件，无需注册任何账号
        </p>
      </div>

      {!token ? (
        <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-6 space-y-4">
          <div>
            <label className="block text-sm text-gray-400 mb-2">邮箱地址</label>
            <div className="flex gap-2">
              <input
                value={address.split("@")[0]}
                onChange={(e) => setAddress(`${e.target.value}@${domains[0] ?? ""}`)}
                className="flex-1 bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-blue-500"
                placeholder="用户名"
              />
              <span className="flex items-center text-gray-500 text-sm px-2">@</span>
              <select
                value={domains[0] ?? ""}
                onChange={(e) => setAddress(`${address.split("@")[0]}@${e.target.value}`)}
                className="bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm text-gray-300 focus:outline-none focus:border-blue-500"
              >
                {domains.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {error && (
            <div className="text-red-400 text-sm bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
              {error}
            </div>
          )}
          <button
            onClick={create}
            disabled={creating || !address}
            className="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg text-white font-medium text-sm transition-all"
          >
            {creating ? "创建中..." : "创建临时邮箱"}
          </button>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="bg-[#161b22] border border-emerald-500/30 rounded-xl p-5">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-xs text-emerald-400 font-medium mb-1">邮箱已创建</p>
                <p className="text-white font-mono font-semibold">{address}</p>
                <p className="text-xs text-gray-500 mt-1">密码: {password}</p>
              </div>
              <div className="flex gap-2 shrink-0">
                <button
                  onClick={copy}
                  className={`px-3 py-1.5 rounded-lg text-sm transition-all border ${
                    copied
                      ? "bg-emerald-500/20 border-emerald-500/40 text-emerald-400"
                      : "bg-[#21262d] border-[#30363d] text-gray-300 hover:text-white"
                  }`}
                >
                  {copied ? "已复制" : "复制"}
                </button>
                <button
                  onClick={() => fetchMessages(token)}
                  disabled={loading}
                  className="px-3 py-1.5 rounded-lg text-sm bg-[#21262d] border border-[#30363d] text-gray-300 hover:text-white transition-all"
                >
                  {loading ? "..." : "刷新"}
                </button>
                <button
                  onClick={reset}
                  className="px-3 py-1.5 rounded-lg text-sm bg-red-500/10 border border-red-500/20 text-red-400 hover:bg-red-500/20 transition-all"
                >
                  新邮箱
                </button>
              </div>
            </div>
            <div className="mt-3 flex items-center gap-2">
              <span className="w-2 h-2 bg-emerald-400 rounded-full animate-pulse" />
              <span className="text-xs text-gray-400">每 5 秒自动刷新</span>
            </div>
          </div>

          {selected ? (
            <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5">
              <div className="flex items-center justify-between mb-4">
                <button
                  onClick={() => setSelected(null)}
                  className="flex items-center gap-1 text-sm text-gray-400 hover:text-white transition-all"
                >
                  ← 返回列表
                </button>
              </div>
              <h3 className="text-white font-semibold mb-1">{selected.subject}</h3>
              <p className="text-xs text-gray-500 mb-4">
                发件人: {selected.from.address} &nbsp;|&nbsp;{" "}
                {new Date(selected.createdAt).toLocaleString("zh-CN")}
              </p>
              <div className="bg-[#0d1117] rounded-lg p-4 text-sm text-gray-300 leading-relaxed whitespace-pre-wrap max-h-96 overflow-y-auto">
                {selected.text ||
                  (selected.html?.[0]
                    ? selected.html[0].replace(/<[^>]+>/g, " ").trim()
                    : "（邮件内容为空）")}
              </div>
            </div>
          ) : (
            <div className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
              <div className="px-5 py-3 border-b border-[#21262d] flex items-center justify-between">
                <span className="text-sm font-medium text-gray-300">
                  收件箱 {messages.length > 0 && `(${messages.length})`}
                </span>
              </div>
              {messages.length === 0 ? (
                <div className="py-16 text-center text-gray-600 text-sm">
                  <p className="text-2xl mb-2">📭</p>
                  <p>暂无邮件，等待接收中...</p>
                </div>
              ) : (
                <div className="divide-y divide-[#21262d]">
                  {messages.map((msg) => (
                    <button
                      key={msg.id}
                      onClick={() => openMessage(msg)}
                      className="w-full text-left px-5 py-4 hover:bg-[#1c2128] transition-all"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <p className={`text-sm font-medium truncate ${msg.seen ? "text-gray-400" : "text-white"}`}>
                            {msg.subject || "(无主题)"}
                          </p>
                          <p className="text-xs text-gray-500 mt-0.5 truncate">
                            {msg.from.address}
                          </p>
                          {msg.intro && (
                            <p className="text-xs text-gray-600 mt-1 truncate">{msg.intro}</p>
                          )}
                        </div>
                        <p className="text-xs text-gray-600 shrink-0">
                          {new Date(msg.createdAt).toLocaleTimeString("zh-CN")}
                        </p>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
