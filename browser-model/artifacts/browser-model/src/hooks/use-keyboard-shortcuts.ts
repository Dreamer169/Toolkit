import { useEffect, useState } from "react";
import { useBrowserStore } from "./use-browser-store";

export function useKeyboardShortcuts() {
  const { addTab, closeTab, switchTab, tabs, activeTabId, navigateTab } = useBrowserStore();
  const [showHelp, setShowHelp] = useState(false);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const isMac = navigator.platform.toUpperCase().indexOf("MAC") >= 0;
      const cmdOrCtrl = isMac ? e.metaKey : e.ctrlKey;

      if (cmdOrCtrl && e.key === "t") {
        e.preventDefault();
        addTab();
      } else if (cmdOrCtrl && e.key === "w") {
        e.preventDefault();
        if (activeTabId) closeTab(activeTabId);
      } else if (cmdOrCtrl && e.key === "l") {
        e.preventDefault();
        document.getElementById("browser-address-bar")?.focus();
      } else if (cmdOrCtrl && e.key === "r") {
        e.preventDefault();
        const activeTab = tabs.find((t) => t.id === activeTabId);
        if (activeTabId && activeTab && !activeTab.url.startsWith("browser://")) {
          useBrowserStore.getState().reloadTab(activeTabId);
        }
      } else if (cmdOrCtrl && e.key === "Tab") {
        e.preventDefault();
        if (tabs.length > 1) {
          const currentIndex = tabs.findIndex((t) => t.id === activeTabId);
          const nextIndex = e.shiftKey
            ? (currentIndex - 1 + tabs.length) % tabs.length
            : (currentIndex + 1) % tabs.length;
          switchTab(tabs[nextIndex].id);
        }
      } else if (cmdOrCtrl && e.key >= "1" && e.key <= "9") {
        e.preventDefault();
        const index = parseInt(e.key) - 1;
        if (index < tabs.length) {
          switchTab(tabs[index].id);
        } else if (e.key === "9" && tabs.length > 0) {
          // 9 usually goes to last tab
          switchTab(tabs[tabs.length - 1].id);
        }
      } else if (cmdOrCtrl && e.key === "/") {
        e.preventDefault();
        setShowHelp((prev) => !prev);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [addTab, closeTab, switchTab, tabs, activeTabId]);

  return { showHelp, setShowHelp };
}
