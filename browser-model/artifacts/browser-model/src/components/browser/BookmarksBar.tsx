import { useBrowserStore } from "@/hooks/use-browser-store";
import { Button } from "@/components/ui/button";

export function BookmarksBar() {
  const { bookmarks, navigateTab, activeTabId } = useBrowserStore();

  if (bookmarks.length === 0) return null;

  return (
    <div className="flex items-center px-4 py-1.5 bg-background border-b border-border gap-2 overflow-x-auto no-scrollbar">
      {bookmarks.map((bookmark) => (
        <Button
          key={bookmark.id}
          variant="ghost"
          size="sm"
          className="h-7 px-2 text-xs font-normal whitespace-nowrap"
          onClick={() => activeTabId && navigateTab(activeTabId, bookmark.url)}
        >
          {bookmark.favicon ? (
            <img src={bookmark.favicon} alt="" className="h-3.5 w-3.5 mr-1.5 rounded-sm bg-white" />
          ) : (
            <div className="h-3.5 w-3.5 mr-1.5 rounded-sm bg-muted" />
          )}
          {bookmark.title}
        </Button>
      ))}
    </div>
  );
}
