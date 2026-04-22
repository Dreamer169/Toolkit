import { useState, useRef, useEffect } from "react";
import { useBrowserStore } from "@/hooks/use-browser-store";
import { Search, Star, StarOff, MoreVertical, RefreshCw, X, ArrowLeft, ArrowRight, Home } from "lucide-react";
import { isUrl, formatUrl, getSearchUrl, getFaviconUrl } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, DropdownMenuSeparator } from "@/components/ui/dropdown-menu";

export function AddressBar() {
  const { tabs, activeTabId, navigateTab, goBack, goForward, reloadTab, addTab, bookmarks, toggleBookmark, history } = useBrowserStore();
  const activeTab = tabs.find(t => t.id === activeTabId);
  const [input, setInput] = useState("");
  const [isFocused, setIsFocused] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (activeTab && !isFocused) {
      setInput(activeTab.url === "browser://newtab" ? "" : activeTab.url);
    }
  }, [activeTab?.url, isFocused]);

  const canGoBack = activeTab ? activeTab.historyIndex > 0 : false;
  const canGoForward = activeTab ? activeTab.historyIndex < activeTab.history.length - 1 : false;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || !activeTabId) return;
    
    navigateTab(activeTabId, input);
    inputRef.current?.blur();
  };

  const handleBookmark = () => {
    if (!activeTab || activeTab.url.startsWith("browser://")) return;
    toggleBookmark(activeTab.url, activeTab.title);
  };

  const isBookmarked = activeTab ? bookmarks.some(b => b.url === activeTab.url) : false;

  const suggestions = [
    ...bookmarks.filter(b => b.url.includes(input) || b.title.toLowerCase().includes(input.toLowerCase())),
    ...history.filter(h => h.url.includes(input) || h.title.toLowerCase().includes(input.toLowerCase()))
  ].slice(0, 5); // Simple suggestions

  return (
    <div className="flex items-center space-x-2 px-4 py-2 bg-background border-b border-border">
      <div className="flex items-center space-x-1">
        <Button variant="ghost" size="icon" className="h-8 w-8 rounded-full" disabled={!canGoBack} onClick={() => activeTabId && goBack(activeTabId)}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <Button variant="ghost" size="icon" className="h-8 w-8 rounded-full" disabled={!canGoForward} onClick={() => activeTabId && goForward(activeTabId)}>
          <ArrowRight className="h-4 w-4" />
        </Button>
        <Button variant="ghost" size="icon" className="h-8 w-8 rounded-full" onClick={() => activeTabId && reloadTab(activeTabId)}>
          {activeTab?.isLoading ? <X className="h-4 w-4" /> : <RefreshCw className="h-4 w-4" />}
        </Button>
        <Button variant="ghost" size="icon" className="h-8 w-8 rounded-full" onClick={() => activeTabId && navigateTab(activeTabId, "browser://newtab")}>
          <Home className="h-4 w-4" />
        </Button>
      </div>

      <div className="flex-1 relative">
        <form onSubmit={handleSubmit} className="relative flex items-center">
          <input
            ref={inputRef}
            id="browser-address-bar"
            type="text"
            className="w-full h-9 pl-10 pr-10 rounded-full bg-secondary/50 border-transparent focus:bg-background focus:border-ring focus:ring-1 focus:ring-ring text-sm transition-all"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onFocus={() => setIsFocused(true)}
            onBlur={() => setTimeout(() => setIsFocused(false), 200)}
            placeholder="Search or enter web address"
            autoComplete="off"
            spellCheck={false}
          />
          <Search className="absolute left-3.5 h-4 w-4 text-muted-foreground" />
          
          {activeTab && !activeTab.url.startsWith("browser://") && (
            <button
              type="button"
              onClick={handleBookmark}
              className="absolute right-2 h-7 w-7 flex items-center justify-center rounded-full hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
            >
              {isBookmarked ? <Star className="h-4 w-4 fill-primary text-primary" /> : <StarOff className="h-4 w-4" />}
            </button>
          )}
        </form>

        {isFocused && input && suggestions.length > 0 && (
          <div className="absolute top-full left-0 right-0 mt-1 bg-popover border border-border rounded-lg shadow-lg overflow-hidden z-50">
            {suggestions.map((s, i) => (
              <button
                key={i}
                className="w-full text-left px-4 py-2 hover:bg-muted flex items-center space-x-3"
                onMouseDown={() => {
                  setInput(s.url);
                  if (activeTabId) navigateTab(activeTabId, s.url);
                }}
              >
                <Search className="h-4 w-4 text-muted-foreground" />
                <div className="flex flex-col overflow-hidden">
                  <span className="text-sm font-medium truncate">{s.title}</span>
                  <span className="text-xs text-muted-foreground truncate">{s.url}</span>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" className="h-8 w-8 rounded-full">
            <MoreVertical className="h-4 w-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-48">
          <DropdownMenuItem onClick={() => addTab()}>New Tab</DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => activeTabId && navigateTab(activeTabId, "browser://history")}>History</DropdownMenuItem>
          <DropdownMenuItem onClick={() => activeTabId && navigateTab(activeTabId, "browser://bookmarks")}>Bookmarks</DropdownMenuItem>
          <DropdownMenuItem onClick={() => activeTabId && navigateTab(activeTabId, "browser://downloads")}>Downloads</DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => activeTabId && navigateTab(activeTabId, "browser://settings")}>Settings</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
