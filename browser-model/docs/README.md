# Browser Model — 本地文档索引

生成时间: 2026-05-11 10:10

## 文件清单

| 文件 | 内容 |
|------|------|
| ARCHITECTURE.md | 三层 Stealth Patch 架构详解 |
| PATCHES.md | 所有 patch 块逐项说明 |
| CHANGELOG.md | 变更历史（按日期） |
| TEST_RESULTS.md | 最新全平台测试基准 |
| VPS_OPS.md | 运维手册（部署 / 重启 / 调试） |

## 快速参考

- **源码**: /root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts
- **构建**: cd /root/Toolkit/browser-model/artifacts/api-server && node build.mjs
- **重启**: pm2 restart 15
- **烟雾测试**: node test_smoke.mjs  (exit 0 = 全部通过)
- **完整测试**: node test_all16.mjs && node test_pixelscan_v3.mjs
