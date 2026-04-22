import { useBrowserStore, Tab } from "@/hooks/use-browser-store";
import { motion, AnimatePresence } from "framer-motion";
import { NewTabPage } from "@/pages/browser/NewTabPage";
import { HistoryPage } from "@/pages/browser/HistoryPage";
import { BookmarksPage } from "@/pages/browser/BookmarksPage";
import { SettingsPage } from "@/pages/browser/SettingsPage";
import { DownloadsPage } from "@/pages/browser/DownloadsPage";
import { RemoteWebView } from "./RemoteWebView";

interface WebViewProps {
  tab: Tab;
  isActive: boolean;
}

/**
 * 新架构：
 *   - 内部页面 (browser://*) 仍走本地 React 渲染。
 *   - 其他外部 URL 全部交给 RemoteWebView：服务端真浏览器 + CDP 截图流 + 远程键鼠。
 *
 * 旧实现（/api/proxy URL 重写 + iframe）已废弃 —— 它在 Next.js/TurboPack 站点上
 * 必然失败：__turbopack_load_page_chunks__ 未定义、第三方脚本 MIME 错乱被 Chrome
 * 拒绝执行，导致 onClick 处理器从未注册。新方案没有这些问题。
 */
export function WebView({ tab, isActive }: WebViewProps) {
  const { tabs } = useBrowserStore();
  void tabs;

  const isInternal = tab.url.startsWith("browser://");

  const renderInternalPage = () => {
    switch (tab.url) {
      case "browser://newtab":    return <NewTabPage tabId={tab.id} />;
      case "browser://history":   return <HistoryPage />;
      case "browser://bookmarks": return <BookmarksPage />;
      case "browser://settings":  return <SettingsPage />;
      case "browser://downloads": return <DownloadsPage />;
      default:                    return <NewTabPage tabId={tab.id} />;
    }
  };

  return (
    <div
      className="absolute inset-0 bg-background flex flex-col"
      style={{
        zIndex: isActive ? 10 : 0,
        opacity: isActive ? 1 : 0,
        pointerEvents: isActive ? "auto" : "none",
      }}
    >
      <AnimatePresence>
        {tab.isLoading && (
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="h-0.5 w-full bg-transparent absolute top-0 left-0 z-50 overflow-hidden"
          >
            <motion.div
              className="h-full bg-primary"
              initial={{ width: "10%" }}
              animate={{ width: ["10%", "60%", "85%"] }}
              transition={{ duration: 1.6, repeat: Infinity }}
            />
          </motion.div>
        )}
      </AnimatePresence>

      {isInternal ? (
        <div className="flex-1 overflow-y-auto">{renderInternalPage()}</div>
      ) : (
        <div className="flex-1 relative">
          {/* key 强制每个 tab 拥有独立的 WS 会话；切换 tab 不会跨用 */}
          <RemoteWebView key={tab.id} tab={tab} />
        </div>
      )}
    </div>
  );
}
