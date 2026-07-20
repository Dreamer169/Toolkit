interface Props {
  value: string;
  onChange: (v: string) => void;
  filterWebUI: boolean;
  onFilterWebUI: (v: boolean) => void;
  count: number;
}

export function SearchBar({
  value,
  onChange,
  filterWebUI,
  onFilterWebUI,
  count,
}: Props) {
  return (
    <div className="flex flex-col sm:flex-row gap-3 items-start sm:items-center">
      <div className="relative flex-1">
        <svg
          className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
          />
        </svg>
        <input
          type="search"
          placeholder="搜索工具名称、描述或标签..."
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full pl-10 pr-4 py-2.5 bg-[#161b22] border border-[#30363d] rounded-lg text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/30 transition-all"
        />
      </div>
      <div className="flex items-center gap-3">
        <button
          onClick={() => onFilterWebUI(!filterWebUI)}
          className={`flex items-center gap-2 px-3 py-2.5 rounded-lg border text-sm transition-all ${
            filterWebUI
              ? "bg-emerald-500/15 border-emerald-500/40 text-emerald-400"
              : "bg-[#161b22] border-[#30363d] text-gray-400 hover:text-gray-200 hover:border-[#4a5568]"
          }`}
        >
          <svg
            className="w-4 h-4"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"
            />
          </svg>
          仅含 Web 界面
        </button>
        <span className="text-sm text-gray-500 whitespace-nowrap">
          {count} 个工具
        </span>
      </div>
    </div>
  );
}
