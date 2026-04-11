import { useState, useMemo } from "react";
import { tools, categories, Tool } from "@/data/tools";
import { ToolCard } from "@/components/ToolCard";
import { ToolDetail } from "@/components/ToolDetail";
import { StatsBar } from "@/components/StatsBar";
import { SearchBar } from "@/components/SearchBar";

export default function Home() {
  const [selectedCategory, setSelectedCategory] = useState("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedTool, setSelectedTool] = useState<Tool | null>(null);
  const [filterWebUI, setFilterWebUI] = useState(false);

  const filtered = useMemo(() => {
    return tools.filter((t) => {
      const matchCat =
        selectedCategory === "all" || t.category === selectedCategory;
      const matchSearch =
        !searchQuery ||
        t.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        t.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
        t.tags.some((tag) =>
          tag.toLowerCase().includes(searchQuery.toLowerCase())
        );
      const matchWebUI = !filterWebUI || t.hasWebUI;
      return matchCat && matchSearch && matchWebUI;
    });
  }, [selectedCategory, searchQuery, filterWebUI]);

  return (
    <div className="text-gray-100">
      <div className="">
        <StatsBar tools={tools} />

        <div className="mt-8 mb-6">
          <SearchBar
            value={searchQuery}
            onChange={setSearchQuery}
            filterWebUI={filterWebUI}
            onFilterWebUI={setFilterWebUI}
            count={filtered.length}
          />
        </div>

        <div className="flex gap-6">
          <aside className="w-48 shrink-0">
            <nav className="space-y-1 sticky top-24">
              {categories.map((cat) => {
                const count =
                  cat.id === "all"
                    ? tools.length
                    : tools.filter((t) => t.category === cat.id).length;
                return (
                  <button
                    key={cat.id}
                    onClick={() => setSelectedCategory(cat.id)}
                    className={`w-full text-left px-3 py-2 rounded-lg text-sm flex items-center justify-between transition-all ${
                      selectedCategory === cat.id
                        ? "bg-blue-600/20 text-blue-400 border border-blue-500/30"
                        : "text-gray-400 hover:text-gray-200 hover:bg-[#21262d]"
                    }`}
                  >
                    <span>{cat.label}</span>
                    <span
                      className={`text-xs px-1.5 py-0.5 rounded-full ${
                        selectedCategory === cat.id
                          ? "bg-blue-500/30 text-blue-300"
                          : "bg-[#30363d] text-gray-500"
                      }`}
                    >
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
                <button
                  onClick={() => {
                    setSearchQuery("");
                    setSelectedCategory("all");
                    setFilterWebUI(false);
                  }}
                  className="mt-3 text-blue-400 hover:text-blue-300 text-sm"
                >
                  清除筛选条件
                </button>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                {filtered.map((tool) => (
                  <ToolCard
                    key={tool.id}
                    tool={tool}
                    onClick={() => setSelectedTool(tool)}
                  />
                ))}
              </div>
            )}
          </main>
        </div>
      </div>

      {selectedTool && (
        <ToolDetail tool={selectedTool} onClose={() => setSelectedTool(null)} />
      )}
    </div>
  );
}
