import { useState } from "react";
import { useBrowserStore, TabId } from "@/hooks/use-browser-store";
import { Search, Plus, Trash2, Edit2, Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { isUrl, getSearchUrl, formatUrl, getFaviconUrl } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

interface NewTabPageProps {
  tabId: TabId;
}

export function NewTabPage({ tabId }: NewTabPageProps) {
  const { settings, updateSettings, pinnedShortcuts, history, navigateTab, addPinnedShortcut, removePinnedShortcut } = useBrowserStore();
  const [searchInput, setSearchInput] = useState("");
  const [isEditingShortcuts, setIsEditingShortcuts] = useState(false);
  const [newShortcutUrl, setNewShortcutUrl] = useState("");
  const [newShortcutTitle, setNewShortcutTitle] = useState("");
  const [isAddingShortcut, setIsAddingShortcut] = useState(false);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!searchInput.trim()) return;
    
    let url = searchInput;
    if (isUrl(searchInput)) {
      url = formatUrl(searchInput);
    } else {
      url = getSearchUrl(searchInput, settings.searchEngine);
    }
    
    navigateTab(tabId, url);
  };

  const handleAddShortcut = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newShortcutUrl.trim() || !newShortcutTitle.trim()) return;
    
    addPinnedShortcut(formatUrl(newShortcutUrl), newShortcutTitle);
    setIsAddingShortcut(false);
    setNewShortcutUrl("");
    setNewShortcutTitle("");
  };

  const recentSites = history
    .filter((h, i, arr) => arr.findIndex(t => t.url === h.url) === i)
    .slice(0, 4);

  return (
    <div className="min-h-full flex flex-col items-center pt-[15vh] px-4">
      <div className="w-full max-w-2xl flex flex-col items-center">
        {/* Logo Area */}
        <div className="mb-8 text-center flex flex-col items-center gap-3">
          <h1 className="text-4xl font-bold tracking-tight text-foreground/90">
            {settings.searchEngine}
          </h1>
          <Select value={settings.searchEngine} onValueChange={(val) => updateSettings({ searchEngine: val })}>
            <SelectTrigger className="w-56 h-9 text-sm">
              <SelectValue placeholder="Search engine" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="Google">Google</SelectItem>
              <SelectItem value="DuckDuckGo (HTML)">DuckDuckGo (HTML)</SelectItem>
              <SelectItem value="Bing">Bing</SelectItem>
              <SelectItem value="Brave Search">Brave Search</SelectItem>
              <SelectItem value="DuckDuckGo">DuckDuckGo</SelectItem>
              <SelectItem value="Wikipedia">Wikipedia</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {/* Search Box */}
        <form onSubmit={handleSubmit} className="w-full relative group mb-16">
          <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
            <Search className="h-5 w-5 text-muted-foreground group-focus-within:text-primary transition-colors" />
          </div>
          <input
            type="text"
            className="w-full h-14 pl-12 pr-6 rounded-full bg-background border border-border shadow-sm focus:border-ring focus:ring-1 focus:ring-ring focus:shadow-md transition-all text-lg"
            placeholder={`Search the web...`}
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            autoFocus
          />
        </form>

        {/* Shortcuts */}
        <div className="w-full mb-8">
          <div className="flex justify-between items-center mb-4">
            <h2 className="text-sm font-medium text-muted-foreground uppercase tracking-wider">Pinned</h2>
            <Button
              variant="ghost"
              size="sm"
              className="h-8 text-xs"
              onClick={() => setIsEditingShortcuts(!isEditingShortcuts)}
            >
              {isEditingShortcuts ? "Done" : "Edit"}
            </Button>
          </div>
          
          <div className="grid grid-cols-4 sm:grid-cols-6 gap-4">
            {pinnedShortcuts.map((shortcut) => (
              <div key={shortcut.id} className="relative group flex flex-col items-center">
                <button
                  className="w-14 h-14 rounded-2xl bg-secondary/50 hover:bg-secondary flex items-center justify-center mb-2 transition-colors relative overflow-hidden"
                  onClick={() => !isEditingShortcuts && navigateTab(tabId, shortcut.url)}
                >
                  {shortcut.favicon ? (
                    <img src={shortcut.favicon} alt="" className="h-6 w-6" />
                  ) : (
                    <div className="h-6 w-6 rounded bg-muted" />
                  )}
                </button>
                <span className="text-xs text-muted-foreground w-full text-center truncate px-1">
                  {shortcut.title}
                </span>

                {isEditingShortcuts && (
                  <button
                    className="absolute -top-2 -right-2 bg-destructive text-destructive-foreground rounded-full p-1 shadow-sm opacity-0 group-hover:opacity-100 transition-opacity"
                    onClick={() => removePinnedShortcut(shortcut.id)}
                  >
                    <X className="h-3 w-3" />
                  </button>
                )}
              </div>
            ))}

            {isEditingShortcuts && (
              <button
                className="w-14 h-14 rounded-2xl border-2 border-dashed border-muted-foreground/30 hover:border-primary/50 hover:bg-primary/5 flex items-center justify-center flex-col text-muted-foreground hover:text-primary transition-colors"
                onClick={() => setIsAddingShortcut(true)}
              >
                <Plus className="h-6 w-6" />
              </button>
            )}
          </div>
        </div>

        {/* Add Shortcut Form */}
        {isAddingShortcut && isEditingShortcuts && (
          <form onSubmit={handleAddShortcut} className="w-full bg-card border border-border p-4 rounded-xl shadow-sm mb-8 flex gap-3">
            <div className="flex-1 space-y-3">
              <Input
                placeholder="Name"
                value={newShortcutTitle}
                onChange={(e) => setNewShortcutTitle(e.target.value)}
                autoFocus
              />
              <Input
                placeholder="URL"
                value={newShortcutUrl}
                onChange={(e) => setNewShortcutUrl(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-2 justify-end">
              <Button type="submit" size="sm" className="w-full">Add</Button>
              <Button type="button" variant="outline" size="sm" onClick={() => setIsAddingShortcut(false)}>Cancel</Button>
            </div>
          </form>
        )}

        {/* Recent */}
        {recentSites.length > 0 && !isEditingShortcuts && (
          <div className="w-full">
            <h2 className="text-sm font-medium text-muted-foreground uppercase tracking-wider mb-4">Recent</h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {recentSites.map((site, i) => (
                <button
                  key={i}
                  className="flex items-center p-3 rounded-xl bg-card border border-border hover:bg-accent hover:border-accent-foreground/20 transition-all text-left"
                  onClick={() => navigateTab(tabId, site.url)}
                >
                  <div className="h-8 w-8 rounded-full bg-secondary flex items-center justify-center mr-3 flex-shrink-0">
                    {site.favicon ? (
                      <img src={site.favicon} alt="" className="h-4 w-4" />
                    ) : (
                      <div className="h-4 w-4 rounded-sm bg-muted" />
                    )}
                  </div>
                  <div className="overflow-hidden flex-1">
                    <div className="text-sm font-medium truncate">{site.title}</div>
                    <div className="text-xs text-muted-foreground truncate">{site.url}</div>
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
