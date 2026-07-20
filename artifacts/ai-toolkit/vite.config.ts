import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

const rawPort = process.env.PORT;
const port = rawPort ? Number(rawPort) : 3000;
const basePath = process.env.BASE_PATH ?? "/";

export default defineConfig({
  base: basePath,
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "src"),
      "@assets": path.resolve(import.meta.dirname, "..", "..", "attached_assets"),
    },
    dedupe: ["react", "react-dom"],
  },
  root: path.resolve(import.meta.dirname),
  build: {
    outDir: path.resolve(import.meta.dirname, "dist/public"),
    emptyOutDir: true,
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks: {
          // React 核心
          "vendor-react": ["react", "react-dom"],
          // Radix UI 组件库
          "vendor-radix": [
            "@radix-ui/react-accordion", "@radix-ui/react-alert-dialog",
            "@radix-ui/react-checkbox", "@radix-ui/react-collapsible",
            "@radix-ui/react-dialog", "@radix-ui/react-dropdown-menu",
            "@radix-ui/react-label", "@radix-ui/react-popover",
            "@radix-ui/react-scroll-area", "@radix-ui/react-select",
            "@radix-ui/react-separator", "@radix-ui/react-slider",
            "@radix-ui/react-switch", "@radix-ui/react-tabs",
            "@radix-ui/react-toast", "@radix-ui/react-tooltip",
          ],
          // 图表 & 动画
          "vendor-charts": ["recharts", "framer-motion"],
          // TanStack Query
          "vendor-query": ["@tanstack/react-query"],
          // 工具库
          "vendor-utils": ["lucide-react", "clsx", "tailwind-merge", "sonner", "date-fns"],
          // 面板页（懒加载候选）
          "panels": [
            "./src/pages/GratisPanel",
            "./src/pages/ArPanel",
            "./src/pages/Monitor",
            "./src/pages/MailCenter",
            "./src/pages/GeneralTools",
            "./src/pages/DataManager",
          ],
          // 注册工具页
          "register-pages": [
            "./src/pages/CursorRegister",
            "./src/pages/ReplitRegister",
            "./src/pages/WebshareRegister",
            "./src/pages/OxylabsRegister",
            "./src/pages/GpRegister",
            "./src/pages/YnRegister",
          ],
        },
      },
    },
  },
  server: {
    port,
    host: "0.0.0.0",
    allowedHosts: true,
    proxy: {
      "/api/v1": { target: "http://localhost:8080", changeOrigin: true },
      "/api": { target: "http://localhost:8081", changeOrigin: true },
      "/pm2-api": { target: "http://127.0.0.1:8083", changeOrigin: true, rewrite: (p) => p.replace(/^\/pm2-api/, "") },
    },
  },
  preview: { port, host: "0.0.0.0", allowedHosts: true },
});
