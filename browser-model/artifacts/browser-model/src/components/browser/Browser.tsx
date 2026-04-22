import { useEffect } from "react";
import { useBrowserStore } from "@/hooks/use-browser-store";
import { useKeyboardShortcuts } from "@/hooks/use-keyboard-shortcuts";
import { TabBar } from "./TabBar";
import { AddressBar } from "./AddressBar";
import { BookmarksBar } from "./BookmarksBar";
import { WebView } from "./WebView";
import { KeyboardShortcutsHelp } from "./KeyboardShortcutsHelp";

export function Browser() {
  const { tabs, activeTabId, settings } = useBrowserStore();
  const { showHelp, setShowHelp } = useKeyboardShortcuts();

  // Apply theme class to a wrapper if we don't want to mess with the whole document,
  // but we applied it to html in SettingsPage. For completeness, we'll ensure it runs here too.
  useEffect(() => {
    const root = window.document.documentElement;
    root.classList.remove("light", "dark");
    if (settings.theme === "system") {
      const systemTheme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
      root.classList.add(systemTheme);
    } else {
      root.classList.add(settings.theme);
    }
  }, [settings.theme]);

  return (
    <div className="h-screen w-full flex flex-col bg-background text-foreground overflow-hidden font-sans">
      {/* Browser Chrome */}
      <div className="flex flex-col z-20 shadow-sm relative">
        <TabBar />
        <AddressBar />
        <BookmarksBar />
      </div>

      {/* Viewport Area */}
      <div className="flex-1 relative bg-secondary/30 z-10">
        {tabs.map((tab) => (
          <WebView 
            key={tab.id} 
            tab={tab} 
            isActive={tab.id === activeTabId} 
          />
        ))}
      </div>

      {/* Overlays */}
      <KeyboardShortcutsHelp open={showHelp} onClose={() => setShowHelp(false)} />
    </div>
  );
}
