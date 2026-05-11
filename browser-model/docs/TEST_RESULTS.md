# 测试结果基准

最后验证时间: 2026-05-11 10:10

## 当前基准（全部通过）

### CreepJS — test_creepjs_final.mjs

  like-headless : 0%    PASS
  headless      : 0%    PASS
  stealth       : 0%    PASS
  trust level   : 100%  PASS

### All-16 逐项 — test_all16.mjs

   1  noChrome             false  PASS
   2  hasPermissionsBug    false  PASS
   3  noPlugins            false  PASS
   4  noMimeTypes          false  PASS
   5  notificationIsDenied false  PASS
   6  hasKnownBgColor      false  PASS
   7  prefersLightColor    false  PASS
   8  uaDataIsBlank        false  PASS
   9  pdfIsDisabled        false  PASS
  10  noTaskbar            false  PASS
  11  hasVvpScreenRes      false  PASS
  12  hasSwiftShader       false  PASS  (NVIDIA RTX 5070 Ti / ANGLE)
  13  noWebShare           false  PASS
  14  noContentIndex       false  PASS
  15  noContactsManager    false  PASS
  16  noDownlinkMax        false  PASS

  总计: 0/16 = 0% like-headless

### Pixelscan v6 — test_pixelscan_v3.mjs

  综合判定          You're Definitely a Human  PASS
  Navigator         Clear (73 参数)             PASS
  Webdriver         Clear (37 参数)             PASS
  CDP               Clear (2 参数)              PASS
  User Agent        Clear (5 参数)              PASS
  Share             Clear                       PASS
  CanShare          Clear                       PASS
  TamperedFunctions Clear                       PASS
  AdvancedBotDetect Clear                       PASS
  HeadlessChrome    Clear                       PASS

## 环境参数

  Browser                   fingerprint-chromium ungoogled Chrome 144
  STEALTH_INIT              39,080 chars
  LATE_FIX_PATCHES           3,095 chars
  WORKER_STEALTH_PATCH       8,630 chars
  Proxy                     socks5://127.0.0.1:10916
  Display                   :99 (Xvfb)
  WebGL Renderer            NVIDIA GeForce RTX 5070 Ti (ANGLE)

## 烟雾测试命令

  cd /root/Toolkit/browser-model/artifacts/api-server
  node test_smoke.mjs      # exit 0 = 全部通过，exit 1 = 有失败
