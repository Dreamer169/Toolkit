import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import { getFaviconUrl, isUrl, formatUrl, getSearchUrl } from "@/lib/utils";

export type TabId = string;

export interface Tab {
  id: TabId;
  url: string;
  title: string;
  favicon: string;
  isLoading: boolean;
  history: string[];
  historyIndex: number;
}

export interface HistoryEntry {
  id: string;
  url: string;
  title: string;
  timestamp: number;
  favicon: string;
}

export interface Bookmark {
  id: string;
  url: string;
  title: string;
  favicon: string;
  order: number;
}

export interface PinnedShortcut {
  id: string;
  url: string;
  title: string;
  favicon: string;
}

export interface BrowserSettings {
  searchEngine: string; // Google, DuckDuckGo, Bing, Brave Search
  theme: "light" | "dark" | "system";
  homepage: string;
}

export interface BrowserState {
  tabs: Tab[];
  activeTabId: TabId | null;
  history: HistoryEntry[];
  bookmarks: Bookmark[];
  pinnedShortcuts: PinnedShortcut[];
  settings: BrowserSettings;

  // Tab Actions
  addTab: (url?: string, active?: boolean) => void;
  closeTab: (id: TabId) => void;
  switchTab: (id: TabId) => void;
  reorderTabs: (newTabs: Tab[]) => void;
  navigateTab: (id: TabId, input: string) => void;
  goBack: (id: TabId) => void;
  goForward: (id: TabId) => void;
  reloadTab: (id: TabId) => void;
  updateTabStatus: (id: TabId, updates: Partial<Tab>) => void;

  // Bookmarks
  toggleBookmark: (url: string, title: string) => void;
  removeBookmark: (id: string) => void;
  reorderBookmarks: (newBookmarks: Bookmark[]) => void;

  // History
  addToHistory: (url: string, title: string) => void;
  removeFromHistory: (id: string) => void;
  clearHistory: () => void;

  // Pinned Shortcuts
  addPinnedShortcut: (url: string, title: string) => void;
  removePinnedShortcut: (id: string) => void;

  // Settings
  updateSettings: (settings: Partial<BrowserSettings>) => void;
  clearAllData: () => void;
}

const defaultSettings: BrowserSettings = {
  searchEngine: "DuckDuckGo (HTML)",
  theme: "system",
  homepage: "browser://newtab",
};

const defaultPinnedShortcuts: PinnedShortcut[] = [
  { id: "1", url: "https://en.wikipedia.org", title: "Wikipedia", favicon: getFaviconUrl("https://en.wikipedia.org") },
  { id: "2", url: "https://example.com", title: "Example", favicon: getFaviconUrl("https://example.com") },
  { id: "3", url: "https://developer.mozilla.org", title: "MDN", favicon: getFaviconUrl("https://developer.mozilla.org") },
  { id: "4", url: "https://news.ycombinator.com", title: "Hacker News", favicon: getFaviconUrl("https://news.ycombinator.com") },
  { id: "5", url: "https://html.duckduckgo.com/html", title: "DDG (HTML)", favicon: getFaviconUrl("https://duckduckgo.com") },
  { id: "6", url: "https://www.google.com", title: "Google", favicon: getFaviconUrl("https://www.google.com") },
];

function generateId() {
  return Math.random().toString(36).substring(2, 9);
}

function processUrl(input: string, engine: string): string {
  if (input.startsWith("browser://")) return input;
  if (isUrl(input)) return formatUrl(input);
  return getSearchUrl(input, engine);
}

export const useBrowserStore = create<BrowserState>()(
  persist(
    (set, get) => ({
      tabs: [{
        id: "initial-tab",
        url: "browser://newtab",
        title: "New Tab",
        favicon: "",
        isLoading: false,
        history: ["browser://newtab"],
        historyIndex: 0,
      }],
      activeTabId: "initial-tab",
      history: [],
      bookmarks: [],
      pinnedShortcuts: defaultPinnedShortcuts,
      settings: defaultSettings,

      addTab: (url = "browser://newtab", active = true) => {
        set((state) => {
          const newTab: Tab = {
            id: generateId(),
            url,
            title: url === "browser://newtab" ? "New Tab" : url,
            favicon: getFaviconUrl(url),
            isLoading: !url.startsWith("browser://"),
            history: [url],
            historyIndex: 0,
          };
          return {
            tabs: [...state.tabs, newTab],
            activeTabId: active ? newTab.id : state.activeTabId,
          };
        });
      },

      closeTab: (id: TabId) => {
        set((state) => {
          const tabIndex = state.tabs.findIndex((t) => t.id === id);
          if (tabIndex === -1) return state;

          const newTabs = state.tabs.filter((t) => t.id !== id);
          
          if (newTabs.length === 0) {
            // Never empty, create a new tab
            const fallbackTab: Tab = {
              id: generateId(),
              url: "browser://newtab",
              title: "New Tab",
              favicon: "",
              isLoading: false,
              history: ["browser://newtab"],
              historyIndex: 0,
            };
            return { tabs: [fallbackTab], activeTabId: fallbackTab.id };
          }

          let newActiveId = state.activeTabId;
          if (state.activeTabId === id) {
            // Switch to the tab to the right, or left if at the end
            const nextTab = newTabs[tabIndex] || newTabs[tabIndex - 1];
            newActiveId = nextTab.id;
          }

          return { tabs: newTabs, activeTabId: newActiveId };
        });
      },

      switchTab: (id: TabId) => set({ activeTabId: id }),

      reorderTabs: (newTabs: Tab[]) => set({ tabs: newTabs }),

      navigateTab: (id: TabId, input: string) => {
        set((state) => {
          const finalUrl = processUrl(input, state.settings.searchEngine);
          const newTabs = state.tabs.map((t) => {
            if (t.id === id) {
              const newHistory = t.history.slice(0, t.historyIndex + 1);
              newHistory.push(finalUrl);
              return {
                ...t,
                url: finalUrl,
                title: finalUrl,
                favicon: getFaviconUrl(finalUrl),
                isLoading: !finalUrl.startsWith("browser://"),
                history: newHistory,
                historyIndex: newHistory.length - 1,
              };
            }
            return t;
          });
          return { tabs: newTabs };
        });
      },

      goBack: (id: TabId) => {
        set((state) => {
          return {
            tabs: state.tabs.map((t) => {
              if (t.id === id && t.historyIndex > 0) {
                const newIndex = t.historyIndex - 1;
                const prevUrl = t.history[newIndex];
                return {
                  ...t,
                  url: prevUrl,
                  historyIndex: newIndex,
                  title: prevUrl,
                  favicon: getFaviconUrl(prevUrl),
                  isLoading: !prevUrl.startsWith("browser://"),
                };
              }
              return t;
            })
          };
        });
      },

      goForward: (id: TabId) => {
        set((state) => {
          return {
            tabs: state.tabs.map((t) => {
              if (t.id === id && t.historyIndex < t.history.length - 1) {
                const newIndex = t.historyIndex + 1;
                const nextUrl = t.history[newIndex];
                return {
                  ...t,
                  url: nextUrl,
                  historyIndex: newIndex,
                  title: nextUrl,
                  favicon: getFaviconUrl(nextUrl),
                  isLoading: !nextUrl.startsWith("browser://"),
                };
              }
              return t;
            })
          };
        });
      },

      reloadTab: (id: TabId) => {
        set((state) => {
          return {
            tabs: state.tabs.map((t) => {
              if (t.id === id && !t.url.startsWith("browser://")) {
                return { ...t, isLoading: true }; // Triggers an iframe reload in the view
              }
              return t;
            })
          };
        });
      },

      updateTabStatus: (id: TabId, updates: Partial<Tab>) => {
        set((state) => {
          return {
            tabs: state.tabs.map((t) => (t.id === id ? { ...t, ...updates } : t))
          };
        });
      },

      toggleBookmark: (url: string, title: string) => {
        if (url.startsWith("browser://")) return;
        set((state) => {
          const existingIndex = state.bookmarks.findIndex((b) => b.url === url);
          if (existingIndex !== -1) {
            // Remove
            return { bookmarks: state.bookmarks.filter((b) => b.url !== url) };
          } else {
            // Add
            const newBookmark: Bookmark = {
              id: generateId(),
              url,
              title,
              favicon: getFaviconUrl(url),
              order: state.bookmarks.length,
            };
            return { bookmarks: [...state.bookmarks, newBookmark] };
          }
        });
      },

      removeBookmark: (id: string) => set((state) => ({ bookmarks: state.bookmarks.filter((b) => b.id !== id) })),

      reorderBookmarks: (newBookmarks: Bookmark[]) => set({ bookmarks: newBookmarks }),

      addToHistory: (url: string, title: string) => {
        if (url.startsWith("browser://")) return;
        set((state) => {
          // Prevent consecutive duplicate history entries
          if (state.history.length > 0 && state.history[0].url === url) {
            return state;
          }
          const newEntry: HistoryEntry = {
            id: generateId(),
            url,
            title,
            timestamp: Date.now(),
            favicon: getFaviconUrl(url),
          };
          return { history: [newEntry, ...state.history].slice(0, 1000) }; // Keep last 1000
        });
      },

      removeFromHistory: (id: string) => set((state) => ({ history: state.history.filter((h) => h.id !== id) })),

      clearHistory: () => set({ history: [] }),

      addPinnedShortcut: (url: string, title: string) => set((state) => ({
        pinnedShortcuts: [...state.pinnedShortcuts, { id: generateId(), url, title, favicon: getFaviconUrl(url) }]
      })),

      removePinnedShortcut: (id: string) => set((state) => ({
        pinnedShortcuts: state.pinnedShortcuts.filter((p) => p.id !== id)
      })),

      updateSettings: (newSettings) => set((state) => ({ settings: { ...state.settings, ...newSettings } })),

      clearAllData: () => set(() => ({
        history: [],
        bookmarks: [],
        tabs: [{
          id: generateId(),
          url: "browser://newtab",
          title: "New Tab",
          favicon: "",
          isLoading: false,
          history: ["browser://newtab"],
          historyIndex: 0,
        }],
        settings: defaultSettings,
        pinnedShortcuts: defaultPinnedShortcuts,
      })),
    }),
    {
      name: "browser-storage",
      storage: createJSONStorage(() => localStorage),
      version: 4,
      migrate: (persisted: any, version: number) => {
        if (!persisted) return persisted;
        if (version < 4) {
          const list = Array.isArray(persisted.pinnedShortcuts) ? persisted.pinnedShortcuts : [];
          if (!list.some((s: PinnedShortcut) => /google\.com/i.test(s.url))) {
            list.push({ id: "6", url: "https://www.google.com", title: "Google", favicon: getFaviconUrl("https://www.google.com") });
          }
          persisted.pinnedShortcuts = list;
          if (persisted.settings) {
            persisted.settings.searchEngine = "Google";
          }
        }
        return persisted;
      },
    }
  )
);
