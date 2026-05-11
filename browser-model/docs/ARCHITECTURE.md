# Stealth Patch 架构

生成时间: 2026-05-11 10:10

## 总体结构

fingerprint-chromium 在 C++ 层对部分 JS getter 进行原生重置，
单次 addInitScript 无法生存。采用两次 initScript + 独立 worker evaluate 三层注入。

    Browser Context 创建
      |
      +-- addInitScript(STEALTH_INIT + _WORKER_BOOT_SUFFIX)   第 1 层
      |       主页面 navigator / webgl / audio / font / webrtc 伪装
      |
      +-- addInitScript(LATE_FIX_PATCHES)                     第 2 层
      |       fingerprint-chromium C++ getter 重置后再次覆盖
      |       覆盖 8 项：taskbar / notification / share / bgColor /
      |                  matchMedia / ContentIndex / ContactsManager / downlinkMax
      |
      +-- ctx.on("page", p => p.on("worker", w =>
              w.evaluate(WORKER_STEALTH_PATCH)))               第 3 层
                Worker 作用域一致性：镜像 LATE_FIX_PATCHES 所有 stub

## 为什么需要第 2 层 (LATE_FIX_PATCHES)

fingerprint-chromium 的 C++ 原生 getter 在第 1 个 addInitScript 之后触发，
将部分 JS 属性还原为 native 值。第 2 个 addInitScript 在同一 document-start
时序槽内排在后面，能赢得竞争。

## bind() 技巧 (Share / CanShare)

  错误：navigator.share = function share() { ... }
        toString() 返回源码 → Pixelscan v6 检测到

  正确：var _bound = (function share(){ ... }).bind(null);
        Object.defineProperty(Navigator.prototype, "share", { value: _bound });
        V8 对 bound function 的 toString() 固定返回 "[native code]"

Pixelscan v6 对每个 navigator API 做 toString() 检查，只有 bind() 能通过。

## patch 大小参考（当前）

  STEALTH_INIT + _WORKER_BOOT_SUFFIX : 39,080 chars
  LATE_FIX_PATCHES                   :  3,095 chars
  WORKER_STEALTH_PATCH               :  8,630 chars

## Worker 作用域注意事项

- Worker 内没有 window，使用 self
- Navigator.prototype → 改用 self.navigator.constructor.prototype
- WorkerNavigator 和 Navigator 是不同原型链，主页面的 defineProperty 不传递
- WebGL 在 worker 里通过 OffscreenCanvas.getContext 注入
