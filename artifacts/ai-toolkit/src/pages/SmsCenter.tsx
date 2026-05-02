import { useState, useEffect, useCallback } from "react";

interface PhoneNumber {
  id: number;
  number: string;
  country: string;
  countryName: string;
  countryCode: string;
}

interface SmsMessage {
  info: string;
  body: string;
}

interface MessagesResult {
  phoneNumber?: string;
  phoneId?: number;
  messages?: SmsMessage[];
  count?: number;
  error?: string;
}

const COUNTRY_FLAGS: Record<string, string> = {
  US: "🇺🇸", GB: "🇬🇧", CA: "🇨🇦", DE: "🇩🇪",
  TH: "🇹🇭", MY: "🇲🇾", PH: "🇵🇭",
};

const COUNTRIES = [
  { id: "all", label: "全部" },
  { id: "us", label: "🇺🇸 美国" },
  { id: "gb", label: "🇬🇧 英国" },
  { id: "ca", label: "🇨🇦 加拿大" },
  { id: "de", label: "🇩🇪 德国" },
  { id: "th", label: "🇹🇭 泰国" },
  { id: "my", label: "🇲🇾 马来西亚" },
  { id: "ph", label: "🇵🇭 菲律宾" },
];

function extractCode(text: string): string {
  const m = text.match(/\b(\d{4,8})\b/);
  return m ? m[1] : "";
}

export default function SmsCenter() {
  const [country, setCountry] = useState("us");
  const [numbers, setNumbers] = useState<PhoneNumber[]>([]);
  const [numbersLoading, setNumbersLoading] = useState(false);
  const [selectedPhone, setSelectedPhone] = useState<PhoneNumber | null>(null);
  const [messages, setMessages] = useState<MessagesResult | null>(null);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [lastRefresh, setLastRefresh] = useState<string>("");

  const loadNumbers = useCallback(async (c: string) => {
    setNumbersLoading(true);
    try {
      const r = await fetch(`/api/tools/sms/numbers?country=${c}`);
      const data = await r.json() as PhoneNumber[] | { error: string };
      if (Array.isArray(data)) setNumbers(data);
      else setNumbers([]);
    } catch {
      setNumbers([]);
    }
    setNumbersLoading(false);
  }, []);

  useEffect(() => { loadNumbers(country); }, [country, loadNumbers]);

  const fetchMessages = useCallback(async (phone: PhoneNumber) => {
    setSelectedPhone(phone);
    setMessagesLoading(true);
    setMessages(null);
    try {
      const r = await fetch("/api/tools/sms/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phoneId: phone.id }),
      });
      const data = await r.json() as MessagesResult;
      setMessages(data);
      setLastRefresh(new Date().toLocaleTimeString());
    } catch (e) {
      setMessages({ error: String(e), messages: [] });
    }
    setMessagesLoading(false);
  }, []);

  useEffect(() => {
    if (!autoRefresh || !selectedPhone) return;
    const t = setInterval(() => { fetchMessages(selectedPhone); }, 30000);
    return () => clearInterval(t);
  }, [autoRefresh, selectedPhone, fetchMessages]);

  const filtered = numbers.filter(n =>
    !search || n.number.includes(search.replace(/\D/g, ""))
  );

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-sm font-bold text-white">📱 短信接收中心</h2>
            <p className="text-[10px] text-gray-500 mt-0.5">
              数据来源：jiemahao.com — 免费临时手机号接收验证码
            </p>
          </div>
          <span className="text-[10px] bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 px-2 py-0.5 rounded-full">
            pydoll CF bypass
          </span>
        </div>
      </div>

      <div className="grid grid-cols-12 gap-4">
        {/* Left: Phone Number List */}
        <div className="col-span-5 space-y-3">
          {/* Country Filter */}
          <div className="flex flex-wrap gap-1.5">
            {COUNTRIES.map(c => (
              <button key={c.id} onClick={() => setCountry(c.id)}
                className={`text-[10px] px-2 py-1 rounded-lg border transition-all ${
                  country === c.id
                    ? "bg-blue-600/20 border-blue-500/40 text-blue-400"
                    : "bg-[#0d1117] border-[#21262d] text-gray-400 hover:border-[#30363d] hover:text-gray-300"
                }`}>
                {c.label}
              </button>
            ))}
          </div>

          {/* Search */}
          <input
            type="text" value={search} onChange={e => setSearch(e.target.value)}
            placeholder="搜索号码..."
            className="w-full bg-[#0d1117] border border-[#21262d] rounded-lg px-3 py-1.5 text-xs font-mono text-gray-300 focus:outline-none focus:border-blue-500/50"
          />

          {/* Number List */}
          <div className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
            <div className="flex items-center justify-between px-3 py-2 border-b border-[#21262d]">
              <span className="text-[10px] text-gray-500">
                {numbersLoading ? "加载中..." : `${filtered.length} 个号码`}
              </span>
              <button onClick={() => loadNumbers(country)}
                className="text-[10px] text-blue-400 hover:text-blue-300">
                刷新列表
              </button>
            </div>
            <div className="max-h-[500px] overflow-y-auto">
              {numbersLoading ? (
                <div className="py-8 text-center text-gray-600 text-xs">加载号码列表...</div>
              ) : filtered.length === 0 ? (
                <div className="py-8 text-center text-gray-600 text-xs">暂无号码</div>
              ) : (
                filtered.map(phone => (
                  <button key={phone.id} onClick={() => fetchMessages(phone)}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 hover:bg-[#21262d] border-b border-[#21262d]/50 transition-all text-left ${
                      selectedPhone?.id === phone.id ? "bg-blue-600/10 border-l-2 border-l-blue-500" : ""
                    }`}>
                    <span className="text-lg">
                      {COUNTRY_FLAGS[phone.countryCode] || "🌍"}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="text-xs font-mono text-white">{phone.number}</div>
                      <div className="text-[10px] text-gray-500">{phone.countryName}</div>
                    </div>
                    {selectedPhone?.id === phone.id && (
                      <span className="text-[10px] text-blue-400">●</span>
                    )}
                  </button>
                ))
              )}
            </div>
          </div>
        </div>

        {/* Right: SMS Messages */}
        <div className="col-span-7 space-y-3">
          {!selectedPhone ? (
            <div className="bg-[#161b22] border border-[#21262d] rounded-xl flex items-center justify-center h-64">
              <div className="text-center">
                <div className="text-3xl mb-2">📨</div>
                <p className="text-gray-500 text-xs">从左侧选择号码查看短信</p>
                <p className="text-gray-600 text-[10px] mt-1">
                  注意：查看短信需绕过 Turnstile，约需 30 秒
                </p>
              </div>
            </div>
          ) : (
            <div className="bg-[#161b22] border border-[#21262d] rounded-xl overflow-hidden">
              {/* Message header */}
              <div className="flex items-center justify-between px-4 py-3 border-b border-[#21262d]">
                <div>
                  <div className="text-sm font-mono text-white">
                    {COUNTRY_FLAGS[selectedPhone.countryCode] || "🌍"} {selectedPhone.number}
                  </div>
                  <div className="text-[10px] text-gray-500">
                    {selectedPhone.countryName}
                    {lastRefresh && ` · 更新于 ${lastRefresh}`}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <input type="checkbox" checked={autoRefresh}
                      onChange={e => setAutoRefresh(e.target.checked)}
                      className="w-3 h-3 accent-blue-500" />
                    <span className="text-[10px] text-gray-400">自动刷新(30s)</span>
                  </label>
                  <button onClick={() => fetchMessages(selectedPhone)}
                    disabled={messagesLoading}
                    className="text-[10px] px-2 py-1 bg-blue-600/20 border border-blue-500/30 rounded text-blue-400 hover:bg-blue-600/30 disabled:opacity-40">
                    {messagesLoading ? "读取中..." : "刷新短信"}
                  </button>
                </div>
              </div>

              {/* Messages */}
              <div className="max-h-[500px] overflow-y-auto p-3 space-y-2">
                {messagesLoading ? (
                  <div className="py-12 text-center">
                    <div className="text-2xl mb-3 animate-pulse">⏳</div>
                    <p className="text-gray-400 text-xs">正在绕过 Cloudflare Turnstile...</p>
                    <p className="text-gray-600 text-[10px] mt-1">约需 30~60 秒，请耐心等待</p>
                  </div>
                ) : messages?.error ? (
                  <div className="py-8 text-center">
                    <div className="text-2xl mb-2">⚠️</div>
                    <p className="text-red-400 text-xs">{messages.error}</p>
                  </div>
                ) : !messages ? null : messages.messages?.length === 0 ? (
                  <div className="py-8 text-center">
                    <div className="text-2xl mb-2">📭</div>
                    <p className="text-gray-500 text-xs">暂无短信</p>
                  </div>
                ) : (
                  messages.messages?.map((msg, i) => {
                    const code = extractCode(msg.body);
                    return (
                      <div key={i} className="bg-[#0d1117] border border-[#21262d] rounded-lg p-3 space-y-1.5 hover:border-[#30363d] transition-all">
                        {msg.info && (
                          <div className="text-[10px] text-gray-500 font-mono">{msg.info}</div>
                        )}
                        <p className="text-xs text-gray-200 leading-relaxed">{msg.body}</p>
                        {code && (
                          <div className="flex items-center gap-2 pt-1">
                            <span className="text-[10px] text-gray-500">验证码：</span>
                            <button
                              onClick={() => navigator.clipboard.writeText(code)}
                              className="font-mono text-sm font-bold text-emerald-400 bg-emerald-500/10 border border-emerald-500/30 px-2 py-0.5 rounded hover:bg-emerald-500/20 transition-all"
                              title="点击复制">
                              {code}
                            </button>
                            <span className="text-[9px] text-gray-600">点击复制</span>
                          </div>
                        )}
                      </div>
                    );
                  })
                )}
              </div>

              {messages && !messagesLoading && (messages.messages?.length ?? 0) > 0 && (
                <div className="px-4 py-2 border-t border-[#21262d] text-[10px] text-gray-600">
                  共 {messages.count} 条短信
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
