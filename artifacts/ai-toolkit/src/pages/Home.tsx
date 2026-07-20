import { useState, useMemo, useEffect } from "react";
import { tools, categories, Tool } from "@/data/tools";
import { ToolCard } from "@/components/ToolCard";
import { ToolDetail } from "@/components/ToolDetail";
import { StatsBar } from "@/components/StatsBar";
import { SearchBar } from "@/components/SearchBar";

// ── 快速面板状态卡片 ────────────────────────────────────────────────────────────
interface PanelStat {
  label: string;
  value: string | number;
  tone?: "ok" | "warn" | "err" | "dim";
}

function PanelCard({
  title, icon, color, stats, loading, error, onOpen,
}: {
  title: string; icon: string; color: string;
  stats: PanelStat[]; loading: boolean; error?: string | null;
  onOpen: () => void;
}) {
  return (
    <div
      onClick={onOpen}
      className="bg-[#161b22] border border-[#21262d] rounded-xl p-4 cursor-pointer hover:border-[#30363d] hover:bg-[#1c2129] transition-all group"
    >
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className={`w-8 h-8 rounded-lg ${color} flex items-center justify-center text-base`}>{icon}</div>
          <span className="text-sm font-semibold text-white">{title}</span>
        </div>
        {loading ? (
          <svg className="animate-spin w-4 h-4 text-gray-600" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
          </svg>
        ) : error ? (
          <span className="text-[10px] text-red-400 bg-red-400/10 px-2 py-0.5 rounded-full">离线</span>
        ) : (
          <span className="text-[10px] text-emerald-400 bg-emerald-400/10 px-2 py-0.5 rounded-full">在线</span>
        )}
      </div>
      {error ? (
        <p className="text-xs text-gray-600">{error}</p>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          {stats.map((s, i) => {
            const color = s.tone === "ok" ? "text-emerald-400" : s.tone === "warn" ? "text-amber-400" : s.tone === "err" ? "text-red-400" : s.tone === "dim" ? "text-gray-500" : "text-white";
            return (
              <div key={i} className="bg-[#0d1117] rounded-lg px-2.5 py-1.5 border border-[#21262d]">
                <div className="text-[9px] text-gray-600 uppercase tracking-wide truncate">{s.label}</div>
                <div className={`text-sm font-bold ${color}`}>{loading ? "…" : s.value}</div>
              </div>
            );
          })}
        </div>
      )}
      <div className="mt-3 text-[10px] text-gray-600 group-hover:text-gray-400 transition-colors text-right">点击进入控制台 →</div>
    </div>
  );
}

function usePanelStatus(url: string, token: string) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const r = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
        const j = await r.json();
        if (!cancelled) { setData(j); setError(null); }
      } catch (e: any) {
        if (!cancelled) setError(e.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    const t = setInterval(load, 60000);
    return () => { cancelled = true; clearInterval(t); };
  }, [url, token]);

  return { data, loading, error };
}

function PanelSummary({ onNavigate }: { onNavigate: (tab: string) => void }) {
  const ADMIN_PW = "yu123456";
  const gratis = usePanelStatus("/api/gratis/admin/status", ADMIN_PW);
  const art    = usePanelStatus("/api/ar/admin/status", ADMIN_PW);

  const gratisStats: PanelStat[] = gratis.data ? [
    { label: "活跃账号", value: gratis.data.pool?.active ?? "—", tone: (gratis.data.pool?.active ?? 0) > 0 ? "ok" : "err" },
    { label: "总账号",   value: gratis.data.pool?.total ?? "—" },
    { label: "剩余积分", value: (gratis.data.pool?.total_credits_left ?? 0).toLocaleString() },
    { label: "下次注册", value: (() => { const s = gratis.data.sched?.next_in_sec; if (!s || s <= 0) return "—"; const m = Math.floor(s/60), r = Math.round(s%60); return m>0?`${m}m${r}s`:`${r}s`; })(), tone: "dim" },
  ] : [];

  const artStats: PanelStat[] = art.data ? [
    { label: "活跃账号", value: art.data.pool?.active ?? "—", tone: (art.data.pool?.active ?? 0) > 0 ? "ok" : "err" },
    { label: "今日注册", value: art.data.maintainer?.registered ?? 0, tone: "dim" },
    { label: "剩余积分", value: (art.data.pool?.total_credits_left ?? 0).toLocaleString() },
    { label: "住宅代理", value: `${art.data.proxy?.resi_total ?? 0} 个`, tone: "dim" },
  ] : [];

  return (
    <div className="mb-6">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">账号池快览</span>
        <div className="flex-1 border-t border-[#21262d]" />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <PanelCard
          title="Gratis 账号池"
          icon="🆓"
          color="bg-emerald-500/15"
          stats={gratisStats}
          loading={gratis.loading}
          error={gratis.error}
          onOpen={() => onNavigate("gratis-panel")}
        />
        <PanelCard
          title="Arting 账号池"
          icon="🎨"
          color="bg-violet-500/15"
          stats={artStats}
          loading={art.loading}
          error={art.error}
          onOpen={() => onNavigate("ar-panel")}
        />
      </div>
    </div>
  );
}

// ── 主页 ──────────────────────────────────────────────────────────────────────
export default function Home({ onNavigate }: { onNavigate?: (tab: string) => void }) {
  const [selectedCategory, setSelectedCategory] = useState("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedTool, setSelectedTool] = useState<Tool | null>(null);
  const [filterWebUI, setFilterWebUI] = useState(false);

  const filtered = useMemo(() => {
    return tools.filter(t => {
      const matchCat    = selectedCategory === "all" || t.category === selectedCategory;
      const matchSearch = !searchQuery ||
        t.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        t.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
        t.tags.some(tag => tag.toLowerCase().includes(searchQuery.toLowerCase()));
      const matchWebUI = !filterWebUI || t.hasWebUI;
      return matchCat && matchSearch && matchWebUI;
    });
  }, [selectedCategory, searchQuery, filterWebUI]);

  return (
    <div className="text-gray-100">
      <StatsBar tools={tools} />

      {/* 账号池快速面板 */}
      {onNavigate && (
        <div className="mt-6">
          <PanelSummary onNavigate={onNavigate} />
        </div>
      )}

      <div className="mt-4 mb-6">
        <SearchBar
          value={searchQuery}
          onChange={setSearchQuery}
          filterWebUI={filterWebUI}
          onFilterWebUI={setFilterWebUI}
          count={filtered.length}
        />
      </div>

      <div className="flex gap-6">
        <aside className="w-44 shrink-0">
          <nav className="space-y-1 sticky top-24">
            {categories.map(cat => {
              const count = cat.id === "all" ? tools.length : tools.filter(t => t.category === cat.id).length;
              return (
                <button key={cat.id} onClick={() => setSelectedCategory(cat.id)}
                  className={`w-full text-left px-3 py-2 rounded-lg text-sm flex items-center justify-between transition-all ${
                    selectedCategory === cat.id
                      ? "bg-blue-600/20 text-blue-400 border border-blue-500/30"
                      : "text-gray-400 hover:text-gray-200 hover:bg-[#21262d]"
                  }`}>
                  <span>{cat.label}</span>
                  <span className={`text-xs px-1.5 py-0.5 rounded-full ${selectedCategory === cat.id ? "bg-blue-500/30 text-blue-300" : "bg-[#30363d] text-gray-500"}`}>
                    {count}
                  </span>
                </button>
              );
            })}
          </nav>
        </aside>

        <main className="flex-1 min-w-0">
          {filtered.length === 0 ? (
            <div className="text-center py-20 text-gray-500">
              <div className="text-4xl mb-3">🔍</div>
              <p>没有找到匹配的工具</p>
              <button onClick={() => { setSearchQuery(""); setSelectedCategory("all"); setFilterWebUI(false); }}
                className="mt-3 text-blue-400 hover:text-blue-300 text-sm">清除筛选条件</button>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {filtered.map(tool => (
                <ToolCard key={tool.id} tool={tool} onClick={() => setSelectedTool(tool)} />
              ))}
            </div>
          )}
        </main>
      </div>

      {selectedTool && <ToolDetail tool={selectedTool} onClose={() => setSelectedTool(null)} />}
    </div>
  );
}
