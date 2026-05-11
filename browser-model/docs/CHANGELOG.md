# 变更历史

## 2026-05-11 — WORKER_STEALTH_PATCH 扩展 + 本地文档建立

### 变更内容

renderer.ts — WORKER_STEALTH_PATCH 新增 3 个 stub
  - self.ContentIndex stub (noContentIndex worker probe)
  - self.ContactsManager stub (noContactsManager worker probe)
  - self.navigator.connection.downlinkMax ODP (noDownlinkMax worker probe)
  - WORKER_STEALTH_PATCH 大小：7,736 -> 8,630 chars

新增文件
  - test_smoke.mjs：CreepJS + Pixelscan 合并烟雾测试，exit 0/1

### 测试结果
  - CreepJS: 0/16 like-headless, trust=100% PASS
  - Pixelscan: You're Definitely a Human, Navigator Clear (73) PASS

---

## 前期 — LATE_FIX_PATCHES 完善

### 变更内容
  - 8 个修复块（A-H）覆盖 fingerprint-chromium C++ getter 重置后的值
  - share/canShare 改用 .bind(null) -> Pixelscan v6 toString() 检查通过
  - colorScheme:"dark" 写入 newContext 选项

### 测试结果
  - CreepJS: 0/16 like-headless（首次达成）
  - Pixelscan: Share/CanShare Clear（首次达成）

---

## 早期 — 初始 STEALTH_INIT 建立

  - fingerprint-chromium + Playwright 集成
  - Navigator / WebGL / Audio / Font / WebRTC 基础伪装
  - Chrome object stub / UserAgentData 完整覆盖
