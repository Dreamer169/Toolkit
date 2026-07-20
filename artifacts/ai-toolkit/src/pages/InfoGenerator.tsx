import { useState } from "react";

interface PersonInfo {
  name: string;
  firstName: string;
  lastName: string;
  email: string;
  username: string;
  password: string;
  phone: string;
  address: string;
  city: string;
  state: string;
  zip: string;
  country: string;
  dob: string;
  gender: string;
}

export default function InfoGenerator() {
  const [info, setInfo] = useState<PersonInfo | null>(null);
  const [count, setCount] = useState(1);
  const [batch, setBatch] = useState<PersonInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);

  const generate = async (num = 1) => {
    setLoading(true);
    try {
      const r = await fetch(`/api/tools/info-generate?count=${num}`);
      const d = await r.json();
      if (d.success) {
        if (num === 1) {
          setInfo(d.data[0]);
          setBatch([]);
        } else {
          setBatch(d.data);
          setInfo(null);
        }
      }
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  };

  const copy = (text: string, key: string) => {
    navigator.clipboard.writeText(text);
    setCopied(key);
    setTimeout(() => setCopied(null), 1200);
  };

  const exportBatch = () => {
    const lines = batch.map((p) =>
      `${p.firstName} ${p.lastName} | ${p.email} | ${p.password} | ${p.phone} | ${p.address}, ${p.city}, ${p.state} ${p.zip}`
    );
    const blob = new Blob([lines.join("\n")], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "person_info.txt";
    a.click();
    URL.revokeObjectURL(url);
  };

  const Field = ({ label, value, fieldKey }: { label: string; value: string; fieldKey: string }) => (
    <div className="flex items-center justify-between gap-2 py-2 border-b border-[#21262d] last:border-0">
      <span className="text-xs text-gray-500 w-24 shrink-0">{label}</span>
      <span className="text-sm text-gray-200 font-mono flex-1 truncate">{value}</span>
      <button
        onClick={() => copy(value, fieldKey)}
        className={`text-xs px-2 py-0.5 rounded border transition-all shrink-0 ${copied === fieldKey ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400" : "bg-[#21262d] border-[#30363d] text-gray-500 hover:text-white"}`}
      >
        {copied === fieldKey ? "✓" : "复制"}
      </button>
    </div>
  );

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-white mb-1">注册信息生成器</h2>
        <p className="text-sm text-gray-400">
          生成真实的美国人员信息（姓名、地址、手机号、邮箱等），适合填写注册表单
        </p>
      </div>

      <div className="bg-[#161b22] border border-[#21262d] rounded-xl p-6 space-y-4">
        <div className="flex gap-3">
          <button
            onClick={() => generate(1)}
            disabled={loading}
            className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded-lg text-white font-medium text-sm transition-all"
          >
            {loading ? "生成中..." : "生成单个"}
          </button>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={2}
              max={20}
              value={count}
              onChange={(e) => setCount(Math.min(20, Math.max(2, Number(e.target.value))))}
              className="w-16 bg-[#0d1117] border border-[#30363d] rounded-lg px-2 py-2 text-sm text-gray-200 focus:outline-none focus:border-blue-500 text-center"
            />
            <button
              onClick={() => generate(count)}
              disabled={loading}
              className="px-4 py-2.5 bg-[#21262d] hover:bg-[#30363d] border border-[#30363d] disabled:opacity-50 rounded-lg text-gray-300 hover:text-white font-medium text-sm transition-all whitespace-nowrap"
            >
              批量生成
            </button>
          </div>
        </div>

        {info && (
          <div className="bg-[#0d1117] rounded-xl border border-[#21262d] px-5 py-2">
            <Field label="姓名" value={`${info.firstName} ${info.lastName}`} fieldKey="name" />
            <Field label="用户名" value={info.username} fieldKey="username" />
            <Field label="邮箱" value={info.email} fieldKey="email" />
            <Field label="密码" value={info.password} fieldKey="password" />
            <Field label="手机" value={info.phone} fieldKey="phone" />
            <Field label="地址" value={info.address} fieldKey="address" />
            <Field label="城市" value={info.city} fieldKey="city" />
            <Field label="州" value={info.state} fieldKey="state" />
            <Field label="邮编" value={info.zip} fieldKey="zip" />
            <Field label="国家" value={info.country} fieldKey="country" />
            <Field label="生日" value={info.dob} fieldKey="dob" />
            <Field label="性别" value={info.gender === "male" ? "男" : "女"} fieldKey="gender" />
            <div className="pt-3">
              <button
                onClick={() =>
                  copy(
                    `姓名: ${info.firstName} ${info.lastName}\n用户名: ${info.username}\n邮箱: ${info.email}\n密码: ${info.password}\n手机: ${info.phone}\n地址: ${info.address}, ${info.city}, ${info.state} ${info.zip}\n生日: ${info.dob}`,
                    "all"
                  )
                }
                className={`text-xs px-3 py-1.5 rounded-lg border transition-all ${copied === "all" ? "bg-emerald-500/20 border-emerald-500/30 text-emerald-400" : "bg-[#21262d] border-[#30363d] text-gray-400 hover:text-white"}`}
              >
                {copied === "all" ? "已复制全部" : "复制全部信息"}
              </button>
            </div>
          </div>
        )}

        {batch.length > 0 && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-gray-300">已生成 {batch.length} 条信息</span>
              <button
                onClick={exportBatch}
                className="text-xs px-3 py-1.5 rounded-lg border border-[#30363d] bg-[#21262d] text-gray-400 hover:text-white transition-all"
              >
                导出 .txt
              </button>
            </div>
            <div className="rounded-xl border border-[#21262d] overflow-hidden">
              <div className="grid grid-cols-4 px-4 py-2 bg-[#0d1117] text-xs text-gray-500 border-b border-[#21262d]">
                <span>姓名</span>
                <span>邮箱</span>
                <span>手机</span>
                <span>城市</span>
              </div>
              <div className="max-h-72 overflow-y-auto divide-y divide-[#21262d]">
                {batch.map((p, i) => (
                  <div key={i} className="grid grid-cols-4 px-4 py-2.5 text-xs hover:bg-[#1c2128]">
                    <span className="text-gray-300 truncate">{p.firstName} {p.lastName}</span>
                    <span className="text-gray-400 truncate font-mono pr-2">{p.email}</span>
                    <span className="text-gray-400 font-mono">{p.phone}</span>
                    <span className="text-gray-500">{p.city}, {p.state}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
