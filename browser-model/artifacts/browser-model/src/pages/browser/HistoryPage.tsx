import { useState } from "react";
import { useBrowserStore } from "@/hooks/use-browser-store";
import { Search, Trash2, Clock, MoreVertical, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { formatDate, formatTime } from "@/lib/utils";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";

export function HistoryPage() {
  const { history, removeFromHistory, clearHistory, activeTabId, navigateTab, addTab } = useBrowserStore();
  const [searchQuery, setSearchQuery] = useState("");

  const filteredHistory = history.filter(item => 
    item.title.toLowerCase().includes(searchQuery.toLowerCase()) || 
    item.url.toLowerCase().includes(searchQuery.toLowerCase())
  );

  // Group by day
  const groupedHistory = filteredHistory.reduce((acc, item) => {
    const day = formatDate(item.timestamp);
    if (!acc[day]) acc[day] = [];
    acc[day].push(item);
    return acc;
  }, {} as Record<string, typeof history>);

  return (
    <div className="max-w-4xl mx-auto py-8 px-6">
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-primary/10 text-primary rounded-lg">
            <Clock className="h-6 w-6" />
          </div>
          <h1 className="text-2xl font-bold">History</h1>
        </div>
        
        <div className="flex items-center gap-4">
          <div className="relative w-64">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input 
              placeholder="Search history..." 
              className="pl-9"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>
          
          <Button variant="outline" className="text-destructive hover:text-destructive hover:bg-destructive/10" onClick={clearHistory} disabled={history.length === 0}>
            Clear browsing data
          </Button>
        </div>
      </div>

      {history.length === 0 ? (
        <div className="text-center py-20 bg-card rounded-xl border border-border mt-8">
          <Clock className="h-12 w-12 text-muted-foreground mx-auto mb-4 opacity-50" />
          <h2 className="text-xl font-semibold mb-2">No history yet</h2>
          <p className="text-muted-foreground">Sites you visit will appear here.</p>
        </div>
      ) : filteredHistory.length === 0 ? (
        <div className="text-center py-20">
          <p className="text-muted-foreground">No results found for "{searchQuery}"</p>
        </div>
      ) : (
        <div className="space-y-8">
          {Object.entries(groupedHistory).map(([day, items]) => (
            <div key={day} className="space-y-4">
              <h2 className="text-sm font-medium text-muted-foreground sticky top-0 bg-background/95 backdrop-blur py-2 z-10">{day}</h2>
              <div className="bg-card border border-border rounded-xl overflow-hidden shadow-sm">
                {items.map((item, index) => (
                  <div 
                    key={item.id} 
                    className={`flex items-center p-3 hover:bg-muted/50 transition-colors group ${
                      index < items.length - 1 ? "border-b border-border" : ""
                    }`}
                  >
                    <div className="text-xs text-muted-foreground w-16 tabular-nums">
                      {formatTime(item.timestamp)}
                    </div>
                    
                    <div className="h-8 w-8 rounded bg-background flex items-center justify-center mx-3 border border-border flex-shrink-0">
                      {item.favicon ? (
                        <img src={item.favicon} alt="" className="h-4 w-4" />
                      ) : (
                        <div className="h-4 w-4 rounded-sm bg-muted" />
                      )}
                    </div>
                    
                    <div className="flex-1 overflow-hidden min-w-0 flex items-baseline gap-2">
                      <button 
                        className="text-sm font-medium truncate hover:underline text-left max-w-[60%]"
                        onClick={() => activeTabId && navigateTab(activeTabId, item.url)}
                      >
                        {item.title}
                      </button>
                      <span className="text-xs text-muted-foreground truncate flex-1">
                        {item.url}
                      </span>
                    </div>
                    
                    <div className="opacity-0 group-hover:opacity-100 transition-opacity flex items-center">
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="icon" className="h-8 w-8 rounded-full">
                            <MoreVertical className="h-4 w-4 text-muted-foreground" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => addTab(item.url, true)}>
                            <ExternalLink className="mr-2 h-4 w-4" /> Open in new tab
                          </DropdownMenuItem>
                          <DropdownMenuItem className="text-destructive focus:text-destructive" onClick={() => removeFromHistory(item.id)}>
                            <Trash2 className="mr-2 h-4 w-4" /> Remove from history
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
