import { useState } from "react";
import { useBrowserStore } from "@/hooks/use-browser-store";
import { Star, Search, Trash2, MoreVertical, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";

export function BookmarksPage() {
  const { bookmarks, removeBookmark, activeTabId, navigateTab, addTab } = useBrowserStore();
  const [searchQuery, setSearchQuery] = useState("");

  const filteredBookmarks = bookmarks.filter(item => 
    item.title.toLowerCase().includes(searchQuery.toLowerCase()) || 
    item.url.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className="max-w-4xl mx-auto py-8 px-6">
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-primary/10 text-primary rounded-lg">
            <Star className="h-6 w-6" />
          </div>
          <h1 className="text-2xl font-bold">Bookmarks</h1>
        </div>
        
        <div className="relative w-64">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input 
            placeholder="Search bookmarks..." 
            className="pl-9"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>
      </div>

      {bookmarks.length === 0 ? (
        <div className="text-center py-20 bg-card rounded-xl border border-border mt-8">
          <Star className="h-12 w-12 text-muted-foreground mx-auto mb-4 opacity-50" />
          <h2 className="text-xl font-semibold mb-2">No bookmarks yet</h2>
          <p className="text-muted-foreground">Click the star icon in the address bar to save pages.</p>
        </div>
      ) : filteredBookmarks.length === 0 ? (
        <div className="text-center py-20">
          <p className="text-muted-foreground">No results found for "{searchQuery}"</p>
        </div>
      ) : (
        <div className="bg-card border border-border rounded-xl overflow-hidden shadow-sm">
          {filteredBookmarks.map((bookmark, index) => (
            <div 
              key={bookmark.id} 
              className={`flex items-center p-3 hover:bg-muted/50 transition-colors group ${
                index < filteredBookmarks.length - 1 ? "border-b border-border" : ""
              }`}
            >
              <div className="h-8 w-8 rounded bg-background flex items-center justify-center mr-4 border border-border flex-shrink-0">
                {bookmark.favicon ? (
                  <img src={bookmark.favicon} alt="" className="h-4 w-4" />
                ) : (
                  <div className="h-4 w-4 rounded-sm bg-muted" />
                )}
              </div>
              
              <div className="flex-1 overflow-hidden min-w-0 flex items-baseline gap-2">
                <button 
                  className="text-sm font-medium truncate hover:underline text-left max-w-[50%]"
                  onClick={() => activeTabId && navigateTab(activeTabId, bookmark.url)}
                >
                  {bookmark.title}
                </button>
                <span className="text-xs text-muted-foreground truncate flex-1">
                  {bookmark.url}
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
                    <DropdownMenuItem onClick={() => addTab(bookmark.url, true)}>
                      <ExternalLink className="mr-2 h-4 w-4" /> Open in new tab
                    </DropdownMenuItem>
                    <DropdownMenuItem className="text-destructive focus:text-destructive" onClick={() => removeBookmark(bookmark.id)}>
                      <Trash2 className="mr-2 h-4 w-4" /> Delete bookmark
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
