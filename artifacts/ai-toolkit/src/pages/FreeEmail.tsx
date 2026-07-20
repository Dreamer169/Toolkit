import { useState, useEffect, useRef, ReactNode } from "react";
import { QRCodeSVG } from "qrcode.react";

interface Identity {
  email: string; login: string; domain: string;
  username: string; password: string; guid: string;
  name: string; phone: string; birthday: string;
  birthdayDay: string; birthdayMonth: string; birthdayYear: string;
  age: string; zodiac: string; blood: string; color: string;
  motherMaidenName: string;
  street: string; city: string; state: string; zip: string;
  coords: string; countryCode: string;
  company: string; occupation: string; website: string;
  height: string; heightcm: string; weight: string; weightkg: string; vehicle: string;
  ssn: string; card: string; cvv2: string; expiration: string;
  moneygram: string; westernunion: string; ups: string;
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
  const [showQR, setShowQR] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const generate = async () => {
    setLoading(true);
    setMessages([]);
    setSelectedMsg(null);
    setWatching(false);
    setShowQR(false);
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

  const getQRContent = (id: Identity) =>
    `姓名: ${id.name}\n邮箱: ${id.email}\n密码: ${id.password}\n用户名: ${id.username}\n电话: +${id.countryCode} ${id.phone}\n地址: ${id.street}, ${id.city}, ${id.state} ${id.zip}\n生日: ${id.birthday}\nSSN: ${id.ssn}\n信用卡: ${id.card} CVV:${id.cvv2} 有效期:${id.expiration}`;

  const getFirstName = (name: string) => name.split(" ")[0].replace(/\./g, "").trim();

  const extractCode = (text: string) => {
    const m = text?.match(/\b(\d{6})\b/) || text?.match(/\b([A-Z0-9]{8,})\b/);
    return m ? m[1] : null;
  };

  const F = ({ label, value, k, mono = true }: {
    label: string; value: string; k: string; mono?: boolean;
  }) => (
    <div className="flex items-start gap-2 py-1.5 border-b border-[#21262d] last:border-0">
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

  const Section = ({ title, icon, children }: { title: string; icon: string; children: ReactNode }) => (
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
          {/* 三个快捷功能横幅 */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {/* 名字含义 */}
            <a
              href={`https://www.behindthename.com/name/${getFirstName(identity.name).toLowerCase()}`}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-3 bg-[#161b22] border border-[#21262d] hover:border-purple-500/50 rounded-xl px-4 py-3 transition-all group"
            >
              <span className="text-2xl">📖</span>
              <div>
                <p className="text-xs font-medium text-gray-200 group-hover:text-purple-300 transition-colors">
                  好奇 <span className="text-purple-400 font-bold">{getFirstName(identity.name)}</span> 是什么意思？
                </p>
                <p className="text-[11px] text-gray-500 mt-0.5">点击查看名字含义 →</p>
              </div>
            </a>

            {/* SSN 在线查询 */}
            <a
              href={`https://www.ssn-search.com/`}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-3 bg-[#161b22] border border-[#21262d] hover:border-yellow-500/50 rounded-xl px-4 py-3 transition-all group"
            >
              <span className="text-2xl">🔍</span>
              <div>
                <p className="text-xs font-medium text-gray-200 group-hover:text-yellow-300 transition-colors">
                  SSN <span className="text-yellow-400 font-mono font-bold">{identity.ssn}</span>
                </p>
                <p className="text-[11px] text-gray-500 mt-0.5">点击查询是否已泄露在线 →</p>
              </div>
            </a>

            {/* QR 码 */}
            <button
              onClick={() => setShowQR(!showQR)}
              className="flex items-center gap-3 bg-[#161b22] border border-[#21262d] hover:border-emerald-500/50 rounded-xl px-4 py-3 transition-all group text-left"
            >
              <span className="text-2xl">📱</span>
              <div>
                <p className="text-xs font-medium text-gray-200 group-hover:text-emerald-300 transition-colors">
                  查看此身份的 QR 码
                </p>
                <p className="text-[11px] text-gray-500 mt-0.5">{showQR ? "点击收起 ▲" : "点击展开 ▼"}</p>
              </div>
            </button>
          </div>

          {/* QR 码展开区 */}
          {showQR && (
            <div className="bg-[#161b22] border border-emerald-500/30 rounded-xl p-6 flex flex-col sm:flex-row items-center gap-6">
              <div className="bg-white p-3 rounded-xl shadow-lg">
                <QRCodeSVG
                  value={getQRContent(identity)}
                  size={160}
                  bgColor="#ffffff"
                  fgColor="#000000"
                  level="M"
                />
              </div>
              <div className="flex-1">
                <p className="text-sm font-semibold text-emerald-400 mb-2">此身份的 QR 码</p>
                <p className="text-xs text-gray-400 leading-relaxed mb-3">
                  扫描此二维码可在手机上快速查看该身份的完整信息，包含姓名、邮箱、密码、电话、地址、SSN 和信用卡信息。
                </p>
                <div className="text-[11px] font-mono text-gray-500 bg-[#0d1117] rounded-lg p-3 whitespace-pre-wrap leading-relaxed max-h-32 overflow-y-auto">
                  {getQRContent(identity)}
                </div>
              </div>
            </div>
          )}

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

          {/* 技术信息 */}
          <Section title="技术信息" icon="🌐">
            <F label="User Agent" value={identity.useragent} k="ua" />
          </Section>
        </>
      )}

      {!identity && (
        <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-8 text-center">
          <div className="text-4xl mb-3">🆔</div>
          <p className="text-gray-400 text-sm">点击上方按钮生成完整美国身份</p>
          <p className="text-gray-600 text-xs mt-1">包含 31 个字段：账号、个人、地址、工作、体格、财务、技术信息 + 真实收件箱 + 名字含义 + SSN 查询 + QR 码</p>
        </div>
      )}

      <div className="bg-[#0d1117] border border-[#30363d] rounded-xl p-4">
        <p className="text-xs font-semibold text-gray-400 mb-2">数据来源说明</p>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-[11px] text-gray-500">
          <div className="flex gap-2"><span className="text-blue-400 shrink-0">①</span><span><strong className="text-gray-400">fakenamegenerator.com</strong> 提供 31 个字段的真实风格美国身份（含信用卡、SSN、坐标等）</span></div>
          <div className="flex gap-2"><span className="text-blue-400 shrink-0">②</span><span>邮箱由 <strong className="text-gray-400">fakemailgenerator.com</strong> 真实托管，可接收任意来源邮件，免费无限制</span></div>
          <div className="flex gap-2"><span className="text-blue-400 shrink-0">③</span><span><strong className="text-gray-400">behindthename.com</strong> 查名字含义 / <strong className="text-gray-400">QR 码</strong>包含完整身份信息 / SSN 在线泄露查询</span></div>
        </div>
      </div>
    </div>
  );
}
