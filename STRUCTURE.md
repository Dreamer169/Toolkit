# Toolkit / Repository Structure

  ## 关键路径 (2026-04-25 整合后)

  ```
  /root/Toolkit/                          ← git working tree (Dreamer169/Toolkit @ main)
  ├── artifacts/api-server/
  │   ├── replit_register.py              ← Python signup orchestrator
  │   ├── outlook_register.py
  │   └── dist/index.mjs                  ← TS api-server 编译产物
  ├── browser-model/                      ← 唯一权威源 (PM2 通过 symlink 跑这里)
  │   └── artifacts/api-server/
  │       ├── src/lib/
  │       │   ├── renderer.ts             ← getBrowser + cf-warmup + v8.10 bootstrap
  │       │   ├── cdp-broker.ts           ← 仅服务于外部 CDP attach (cdp-ws-server 用)
  │       │   ├── cdp-ws-server.ts        ← /api/cdp/ws WS bridge
  │       │   ├── google-route.ts         ← v7.76 sticky-per-context Google 子请求路由
  │       │   └── ...
  │       └── dist/index.mjs              ← node ./build.mjs 产物 (gitignored)
  ├── start-browser-model.sh              ← PM2 browser-model 入口
  └── STRUCTURE.md                        ← 本文件

  /root/browser-model                     ← symlink → /root/Toolkit/browser-model
  ```

  ## 历史教训 (为什么有 symlink)

  2026-04-25 之前, `/root/browser-model/` 是与 `/root/Toolkit/browser-model/` 完全独立的两个目录:
  - runtime 副本 = `/root/browser-model/` (PM2 实际运行)
  - git 副本 = `/root/Toolkit/browser-model/` (commit/推送用)
  - 没有任何同步机制, 靠人手 `cp` 维持

  后果:
  - 在 git 副本改代码 + `pm2 restart` → 看不到任何变化 (PM2 跑的是另一份)
  - 在 runtime 副本改代码 + 跑通了 → 改动不到 git (改的是 untracked 副本)
  - 关键文件长期漂移: v7.76 google-route.ts 只存在于 runtime; v8.10 死码只存在于 git 的 cdp-broker.ts

  整合方案:
  1. 提升 v7.76 google-route.ts: runtime → git
  2. 删 runtime cdp-broker.ts 的 v8.10 死码 (renderer.ts 不调它)
  3. 清理两边所有 `.bak*` 残留
  4. `rm -rf /root/browser-model && ln -s /root/Toolkit/browser-model /root/browser-model`
  5. 在 symlinked 树里 `node ./build.mjs` 重建 dist
  6. PM2 restart browser-model

  ## 工作流程 (整合后)

  ```bash
  # 1. 改 TS 源
  vim /root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts

  # 2. 重建 dist (任一路径都可, 符号链接同一目录)
  cd /root/browser-model/artifacts/api-server && node ./build.mjs

  # 3. 重启 PM2
  pm2 restart browser-model

  # 4. 提交 git (Toolkit 仓库)
  cd /root/Toolkit && git add browser-model/ && (commit && push)
  ```

  ## PM2 服务清单

  | Service | Script Path | 关键说明 |
  |---------|-------------|---------|
  | `browser-model` | `/root/Toolkit/start-browser-model.sh` | 内部启 `/root/browser-model/.../dist/index.mjs` (符号链接到 Toolkit) |
  | `api-server` | `/root/Toolkit/artifacts/api-server/dist/index.mjs` | TS HTTP 服务, shell-out 调用 `replit_register.py` |
  | `xray` | `/usr/bin/xray run -confdir /root/xray-confs` | 25+ VLESS outbound (10800-10832 SOCKS5) + WARP backup |
  | `xvfb` | `Xvfb :99 -screen 0 1920x1080x24` | broker chromium 用的虚拟显示 |

  ## 上游/出口拓扑

  ```
  Python register → CDP (ws://localhost:8092/api/cdp/ws)
                   ↓
         broker chromium (--remote-debugging-port=9222, headed full chrome)
                   ↓ proxy-server=
         socks5://127.0.0.1:40000  (WARP, 解 CF challenge, 出口 104.28.x.x)
                   ↓ google-route 拦截
         *.google/*.gstatic/*.recaptcha/*.youtube → sticky-per-ctx SOCKS (10820/10822/10824/...)
  ```

  WARP 走 replit.com (解 CF), Google 子请求走 SOCKS 池 (抬 reCAPTCHA score).
  v7.76 sticky-per-context 保证同 ctx 内 Google 请求**永远同一出口 IP** (避免 score 归零).
  