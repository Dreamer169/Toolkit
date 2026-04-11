import { useState, useEffect, useRef } from "react";

interface Identity {
  // 账号
  email: string; login: string; domain: string;
  username: string; password: string; guid: string;
  // 个人
  name: string; phone: string; birthday: string;
  birthdayDay: string; birthdayMonth: string; birthdayYear: string;
  age: string; zodiac: string; blood: string; color: string;
  motherMaidenName: string;
  // 地址
  street: string; city: string; state: string; zip: string;
  coords: string; countryCode: string;
  // 工作
  company: string; occupation: string; website: string;
  // 体格
  height: string; heightcm: string; weight: string; weightkg: string; vehicle: string;
  // 财务
  ssn: string; card: string; cvv2: string; expiration: string;
  moneygram: string; westernunion: string; ups: string;
  // 技术
  useragent: string;
}

interface Message {
  id: string; from: string; subject: string;
  date: string; body: string; received_at: number;
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
    } catch (e) { console.error(e); }
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

  const copyAll = () => {
    if (!identity) return;
    const lines = [
      `=== 账号信息 ===`,
      `邮箱: ${identity.email}`,
      `用户名: ${identity.username}`,
      `密码: ${identity.password}`,
      `GUID: ${identity.guid}`,
      ``,
      `=== 个人信息 ===`,
      `姓名: ${identity.name}`,
      `电话: +${identity.countryCode} ${identity.phone}`,
      `生日: ${identity.birthday} (${identity.age}岁)`,
      `星座: ${identity.zodiac}`,
      `血型: ${identity.blood}`,
      `喜欢颜色: ${identity.color}`,
      `母亲婚前姓: ${identity.motherMaidenName}`,
      ``,
      `=== 地址信息 ===`,
      `街道: ${identity.street}`,
      `城市: ${identity.city}, ${identity.state} ${identity.zip}`,
      `坐标: ${identity.coords}`,
      ``,
      `=== 工作信息 ===`,
      `公司: ${identity.company}`,
      `职业: ${identity.occupation}`,
      `网站: ${identity.website}`,
      ``,
      `=== 体格信息 ===`,
      `身高: ${identity.height} (${identity.heightcm}cm)`,
      `体重: ${identity.weight}lbs (${identity.weightkg}kg)`,
      `车辆: ${identity.vehicle}`,
      ``,
      `=== 财务信息 ===`,
      `SSN: ${identity.ssn}`,
      `信用卡: ${identity.card}`,
      `CVV2: ${identity.cvv2}`,
      `有效期: ${identity.expiration}`,
      `MoneyGram: ${identity.moneygram}`,
      `Western Union: ${identity.westernunion}`,
      `UPS: ${identity.ups}`,
      ``,
      `=== 技术信息 ===`,
      `User Agent: ${identity.useragent}`,
    ];
    copy(lines.join("\n"), "all");
  };

  const extractCode = (text: string) => {
    const m = text?.match(/\b(\d{6})\b/) || text?.match(/\b([A-Z0-9]{8,})\b/);
    return m ? m[1] : null;
  };

  const F = ({ label, value, k, mono = true, full = false }: {
    label: string; value: string; k: string; mono?: boolean; full?: boolean;
  }) => (
    <div className={`flex items-start gap-2 py-1.5 border-b border-[#21262d] last:border-0 ${full ? "col-span-2" : ""}`}>
      <span className="text-[11px] text-gray-500 w-20 shrink-0 pt-0.5">{label}</span>
      <span className={`text-[12px] text-gray-200 flex-1 break-all leading-relaxed ${mono ? "font-mono" : ""}`}>{value || "—"}</span>
      {value && (
        <button
          onClick={() => copy(value, k)}
          className={`text-[11px] px-1.5 py-0.5 rounded border transition-all shrink-0 ${
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

  const Section = ({ title, icon, children }: { title: string; icon: string; children: React.ReactNode }) => (
    <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4">
      <h3 className="text-xs font-semibold text-gray-400 mb-3 flex items-center gap-2">
        <span>{icon}</span>{title}
      </h3>
      <div className="space-y-0">{children}</div>
    </div>
  );

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-xl font-bold text-white mb-1">免费身份 + 真实邮箱</h2>
        <p className="text-sm text-gray-400">
          通过 <span className="text-blue-400">fakenamegenerator.com</span> 生成完整美国身份（31 个字段），配套真实可收信邮箱，自动监听验证码
        </p>
      </div>

      <div className="flex gap-3">
        <button
          onClick={generate}
          disabled={loading}
          className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded-xl text-white font-medium text-sm transition-all"
        >
          {loading ? "生成中..." : "⚡ 生成完整身份 + 激活邮箱"}
        </button>
        {identity && (
          <button
            onClick={copyAll}
            className={`px-4 py-2.5 rounded-xl border text-sm transition-all ${
              copied === "all"
                ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400"
                : "bg-[#21262d] border-[#30363d] text-gray-400 hover:text-white"
            }`}
          >
            {copied === "all" ? "✓ 已复制" : "📋 复制全部"}
          </button>
        )}
      </div>

      {identity && (
        <>
          {/* 账号信息 + 收件箱并排 */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Section title="账号信息" icon="🔑">
              <F label="邮箱" value={identity.email} k="email" />
              <F label="用户名" value={identity.username} k="username" />
              <F label="密码" value={identity.password} k="password" />
              <F label="GUID" value={identity.guid} k="guid" />
            </Section>

            {/* 真实收件箱 */}
            <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 flex flex-col">
              <div className="flex items-center justify-between mb-3">
                <div>
                  <h3 className="text-xs font-semibold text-gray-400 flex items-center gap-2">📬 真实收件箱</h3>
                  <p className="text-[11px] text-blue-400 font-mono mt-0.5">{identity.email}</p>
                </div>
                <div className="flex items-center gap-2">
                  {watching && (
                    <span className="flex items-center gap-1 text-[11px] text-emerald-400">
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />监听中
                    </span>
                  )}
                  <span className="text-[11px] text-gray-500 bg-[#21262d] px-2 py-0.5 rounded-full">{messages.length} 封</span>
                </div>
              </div>
              <div className="flex-1 min-h-[120px]">
                {messages.length === 0 ? (
                  <div className="flex flex-col items-center justify-center h-28 text-gray-600 text-center">
                    <div className="text-2xl mb-1">📭</div>
                    <p className="text-xs">等待收信，每 3 秒自动刷新</p>
                    <p className="text-[11px] mt-0.5">用上方邮箱注册后，验证码将出现在这里</p>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {messages.map((msg, i) => {
                      const code = extractCode(msg.body || msg.subject);
                      return (
                        <div
                          key={i}
                          onClick={() => setSelectedMsg(msg === selectedMsg ? null : msg)}
                          className="bg-[#0d1117] border border-[#21262d] rounded-lg p-2.5 cursor-pointer hover:border-blue-500/40 transition-all"
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div className="flex-1 min-w-0">
                              <p className="text-xs font-medium text-gray-200 truncate">{msg.subject || "(无主题)"}</p>
                              <p className="text-[11px] text-gray-500 mt-0.5 truncate">来自: {msg.from}</p>
                            </div>
                            {code && (
                              <button
                                onClick={(e) => { e.stopPropagation(); copy(code, `code-${i}`); }}
                                className={`text-[11px] px-2 py-0.5 rounded border font-mono font-bold shrink-0 transition-all ${
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
                            <div className="mt-2 pt-2 border-t border-[#21262d] text-[11px] text-gray-400 whitespace-pre-wrap break-words max-h-32 overflow-y-auto">
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

          {/* 个人信息 */}
          <Section title="个人信息" icon="👤">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6">
              <F label="姓名" value={identity.name} k="name" mono={false} />
              <F label="电话" value={`+${identity.countryCode} ${identity.phone}`} k="phone" />
              <F label="生日" value={`${identity.birthday} (${identity.age}岁)`} k="birthday" />
              <F label="星座" value={identity.zodiac} k="zodiac" mono={false} />
              <F label="血型" value={identity.blood} k="blood" />
              <F label="喜欢颜色" value={identity.color} k="color" mono={false} />
              <F label="母亲婚前姓" value={identity.motherMaidenName} k="maiden" mono={false} />
            </div>
          </Section>

          {/* 地址 + 工作 */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Section title="地址信息" icon="🏠">
              <F label="街道" value={identity.street} k="street" mono={false} />
              <F label="城市" value={`${identity.city}, ${identity.state} ${identity.zip}`} k="city" />
              <F label="坐标" value={identity.coords} k="coords" />
            </Section>
            <Section title="工作信息" icon="💼">
              <F label="公司" value={identity.company} k="company" mono={false} />
              <F label="职业" value={identity.occupation} k="occupation" mono={false} />
              <F label="网站" value={identity.website} k="website" />
            </Section>
          </div>

          {/* 体格 + 财务 */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Section title="体格信息" icon="🏃">
              <F label="身高" value={`${identity.height} / ${identity.heightcm}cm`} k="height" />
              <F label="体重" value={`${identity.weight}lbs / ${identity.weightkg}kg`} k="weight" />
              <F label="车辆" value={identity.vehicle} k="vehicle" mono={false} />
            </Section>
            <Section title="财务信息" icon="💳">
              <F label="SSN" value={identity.ssn} k="ssn" />
              <F label="信用卡" value={identity.card} k="card" />
              <F label="CVV2" value={identity.cvv2} k="cvv2" />
              <F label="有效期" value={identity.expiration} k="exp" />
              <F label="MoneyGram" value={identity.moneygram} k="mg" />
              <F label="西联汇款" value={identity.westernunion} k="wu" />
              <F label="UPS" value={identity.ups} k="ups" />
            </Section>
          </div>

          {/* User Agent */}
          <Section title="技术信息" icon="🌐">
            <F label="User Agent" value={identity.useragent} k="ua" full />
          </Section>
        </>
      )}

      {!identity && (
        <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-8 text-center">
          <div className="text-4xl mb-3">🆔</div>
          <p className="text-gray-400 text-sm">点击上方按钮生成完整美国身份</p>
          <p className="text-gray-600 text-xs mt-1">包含 31 个字段：账号、个人、地址、工作、体格、财务、技术信息 + 真实收件箱</p>
        </div>
      )}

      <div className="bg-[#0d1117] border border-[#30363d] rounded-xl p-4">
        <p className="text-xs font-semibold text-gray-400 mb-2">数据来源说明</p>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-[11px] text-gray-500">
          <div className="flex gap-2"><span className="text-blue-400 shrink-0">①</span><span><strong className="text-gray-400">fakenamegenerator.com</strong> 提供 31 个字段的真实风格美国身份（含信用卡、SSN、坐标等）</span></div>
          <div className="flex gap-2"><span className="text-blue-400 shrink-0">②</span><span>邮箱由 <strong className="text-gray-400">fakemailgenerator.com</strong> 真实托管，可接收任意来源邮件，免费无限制</span></div>
          <div className="flex gap-2"><span className="text-blue-400 shrink-0">③</span><span>收件箱通过 <strong className="text-gray-400">socket.io</strong> 实时监听，验证码自动识别，一键复制，全程零 API Key</span></div>
        </div>
      </div>
    </div>
  );
}
