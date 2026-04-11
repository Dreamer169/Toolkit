import { useState, useEffect, useRef } from "react";

interface Identity {
  email: string;
  login: string;
  domain: string;
  name: string;
  phone: string;
  address: string;
  city: string;
  state: string;
  zip: string;
  username: string;
  password: string;
  birthday: string;
  ssn: string;
}

interface Message {
  id: string;
  from: string;
  subject: string;
  date: string;
  body: string;
  received_at: number;
}

export default function FreeEmail() {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [loading, setLoading] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [watching, setWatching] = useState(false);
  const [selectedMsg, setSelectedMsg] = useState<Message | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const generate = async () => {
    setLoading(true);
    setMessages([]);
    setSelectedMsg(null);
    setWatching(false);
    if (pollRef.current) clearInterval(pollRef.current);

    try {
      const r = await fetch("/api/fakemail/identity");
      const d = await r.json();
      if (d.success) {
        setIdentity(d.data);
        setWatching(true);
        startPolling(d.data.login, d.data.domain);
      }
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  };

  const startPolling = (login: string, domain: string) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch(`/api/fakemail/messages?login=${login}&domain=${domain}`);
        const d = await r.json();
        if (d.success) setMessages(d.messages || []);
      } catch {}
    }, 3000);
  };

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const copy = (text: string, key: string) => {
    navigator.clipboard.writeText(text);
    setCopied(key);
    setTimeout(() => setCopied(null), 1200);
  };

  const extractCode = (text: string) => {
    const m = text?.match(/\b(\d{6})\b/) || text?.match(/\b([A-Z0-9]{8,})\b/);
    return m ? m[1] : null;
  };

  const Field = ({ label, value, k }: { label: string; value: string; k: string }) => (
    <div className="flex items-center gap-2 py-1.5 border-b border-[#21262d] last:border-0">
      <span className="text-xs text-gray-500 w-16 shrink-0">{label}</span>
      <span className="text-xs text-gray-200 font-mono flex-1 truncate">{value || "—"}</span>
      {value && (
        <button
          onClick={() => copy(value, k)}
          className={`text-xs px-1.5 py-0.5 rounded border transition-all shrink-0 ${
            copied === k
              ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400"
              : "bg-[#21262d] border-[#30363d] text-gray-500 hover:text-white"
          }`}
        >
          {copied === k ? "✓" : "复制"}
        </button>
      )}
    </div>
  );

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-white mb-1">免费身份 + 真实邮箱</h2>
        <p className="text-sm text-gray-400">
          通过 <span className="text-blue-400">fakenamegenerator.com</span> 生成完整美国身份，配套真实可收信邮箱（
          <span className="text-blue-400">fakemailgenerator.com</span>），无需任何 API Key，可接收验证码
        </p>
      </div>

      <div className="flex gap-3">
        <button
          onClick={generate}
          disabled={loading}
          className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed rounded-xl text-white font-medium text-sm transition-all"
        >
          {loading ? "生成中..." : "生成新身份 + 激活邮箱"}
        </button>
        {identity && (
          <button
            onClick={() => copy(
              `姓名: ${identity.name}\n邮箱: ${identity.email}\n密码: ${identity.password}\n用户名: ${identity.username}\n电话: ${identity.phone}\n地址: ${identity.address}, ${identity.city}, ${identity.state} ${identity.zip}\n生日: ${identity.birthday}`,
              "all"
            )}
            className={`px-4 py-2.5 rounded-xl border text-sm transition-all ${
              copied === "all"
                ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400"
                : "bg-[#21262d] border-[#30363d] text-gray-400 hover:text-white"
            }`}
          >
            {copied === "all" ? "已复制" : "复制全部"}
          </button>
        )}
      </div>

      {identity && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-gray-300">身份信息</h3>
              <span className="text-xs text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded-full">真实数据</span>
            </div>
            <div className="space-y-0">
              <Field label="姓名" value={identity.name} k="name" />
              <Field label="邮箱" value={identity.email} k="email" />
              <Field label="密码" value={identity.password} k="password" />
              <Field label="用户名" value={identity.username} k="username" />
              <Field label="电话" value={identity.phone} k="phone" />
              <Field label="地址" value={identity.address} k="address" />
              <Field label="城市" value={`${identity.city}, ${identity.state} ${identity.zip}`} k="city" />
              <Field label="生日" value={identity.birthday} k="birthday" />
            </div>
          </div>

          <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-5 flex flex-col">
            <div className="flex items-center justify-between mb-3">
              <div>
                <h3 className="text-sm font-semibold text-gray-300">真实收件箱</h3>
                <p className="text-xs text-gray-500 font-mono mt-0.5">{identity.email}</p>
              </div>
              <div className="flex items-center gap-1.5">
                {watching && (
                  <span className="flex items-center gap-1 text-xs text-emerald-400">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                    监听中
                  </span>
                )}
                <span className="text-xs text-gray-500 bg-[#21262d] px-2 py-0.5 rounded-full">
                  {messages.length} 封
                </span>
              </div>
            </div>

            <div className="flex-1">
              {messages.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-32 text-gray-600">
                  <div className="text-2xl mb-2">📭</div>
                  <p className="text-sm">等待收信中，每 3 秒自动刷新...</p>
                  <p className="text-xs mt-1">用上方邮箱地址注册后，验证码会出现在这里</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {messages.map((msg, i) => {
                    const code = extractCode(msg.body || msg.subject);
                    return (
                      <div
                        key={i}
                        onClick={() => setSelectedMsg(msg === selectedMsg ? null : msg)}
                        className="bg-[#0d1117] border border-[#21262d] rounded-lg p-3 cursor-pointer hover:border-blue-500/40 transition-all"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="flex-1 min-w-0">
                            <p className="text-xs font-medium text-gray-200 truncate">{msg.subject || "(无主题)"}</p>
                            <p className="text-xs text-gray-500 mt-0.5 truncate">来自: {msg.from}</p>
                          </div>
                          {code && (
                            <button
                              onClick={(e) => { e.stopPropagation(); copy(code, `code-${i}`); }}
                              className={`text-xs px-2 py-1 rounded border font-mono font-bold shrink-0 transition-all ${
                                copied === `code-${i}`
                                  ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400"
                                  : "bg-yellow-500/10 border-yellow-500/30 text-yellow-400"
                              }`}
                            >
                              {copied === `code-${i}` ? "已复制" : `验证码: ${code}`}
                            </button>
                          )}
                        </div>
                        {selectedMsg === msg && msg.body && (
                          <div className="mt-2 pt-2 border-t border-[#21262d] text-xs text-gray-400 whitespace-pre-wrap break-words max-h-32 overflow-y-auto">
                            {msg.body}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      <div className="bg-[#0d1117] border border-[#30363d] rounded-xl p-4">
        <p className="text-xs font-semibold text-gray-400 mb-2">这个工具的工作原理</p>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs text-gray-500">
          <div className="flex gap-2">
            <span className="text-blue-400 shrink-0">1.</span>
            <span>从 <strong className="text-gray-400">fakenamegenerator.com</strong> 生成含真实邮箱的美国人身份（名字、地址、SSN 等）</span>
          </div>
          <div className="flex gap-2">
            <span className="text-blue-400 shrink-0">2.</span>
            <span>邮箱域名（如 armyspy.com）由 <strong className="text-gray-400">fakemailgenerator.com</strong> 真实托管，可接收任意邮件</span>
          </div>
          <div className="flex gap-2">
            <span className="text-blue-400 shrink-0">3.</span>
            <span>通过 socket.io 实时监听收件箱，验证码自动识别高亮，一键复制</span>
          </div>
        </div>
      </div>
    </div>
  );
}
