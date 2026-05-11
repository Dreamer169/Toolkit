# Patch 块详细说明

生成时间: 2026-05-11 10:10

## STEALTH_INIT（第 1 层，~39,080 chars）

  类别              覆盖内容
  --------          --------
  Navigator         userAgent / platform / vendor / hardwareConcurrency /
                    deviceMemory / language / languages / plugins /
                    mimeTypes / pdfViewerEnabled
  WebGL             renderer / vendor (NVIDIA RTX 5070 Ti) / 扩展名单 /
                    getParameter 拦截
  Audio             AnalyserNode / AudioBuffer 随机微扰 (+-1e-7)
  Font              measureText 随机微扰
  Timing            performance.now / Date 轻微噪声
  WebRTC            RTCPeerConnection ice candidate 过滤
  Screen            width=1920 height=1080 colorDepth=24
  UserAgentData     brands / platform / mobile / 架构信息
  Chrome object     app / csi / loadTimes / runtime 完整 stub
  _mkN()            bind() 工厂：所有 navigator API toString()=[native code]

## LATE_FIX_PATCHES（第 2 层，~3,095 chars）

  块  修复内容                                        对应 CreepJS 检查
  --  -----                                           -----
  A   window.taskbar = {visible:true}               noTaskbar
  B   Notification.permission getter -> "default"    notificationIsDenied
  C   navigator.share / canShare  bind() stub        noWebShare
  D   getComputedStyle 背景色 -> rgb(255,255,255)    hasKnownBgColor
  E   matchMedia("prefers-color-scheme:dark") -> true prefersLightColor
  F   window.ContentIndex = function ContentIndex()  noContentIndex
  G   window.ContactsManager = function ContactsManager() noContactsManager
  H   NetworkInformation.downlinkMax ODP -> Infinity  noDownlinkMax

## WORKER_STEALTH_PATCH（第 3 层，~8,630 chars）

  块                   覆盖内容                          对应检查
  --                   --------                          --------
  _wMkN()              Worker 版 bind() 工厂              Pixelscan TamperedFunctions
  share/canShare        WorkerNavigator stub              noWebShare (worker probe)
  hardwareConcurrency   4                                 Pixelscan Worker 一致性
  deviceMemory          4                                 Pixelscan Worker 一致性
  platform              Linux x86_64                     Pixelscan Worker 一致性
  language/languages    en-US / [en-US, en]              Pixelscan Worker 一致性
  WebGL OffscreenCanvas getContext 拦截 + renderer stub   WebGL worker probe
  ContentIndex          self.ContentIndex stub            noContentIndex (worker)
  ContactsManager       self.ContactsManager stub         noContactsManager (worker)
  downlinkMax           connection ODP -> Infinity        noDownlinkMax (worker)

## 关键不变式

1. share/canShare 必须 bind()：否则 Pixelscan v6 toString() 检查失败
2. LATE_FIX 必须是第 2 个 addInitScript：C++ getter 在第 1 个之后重置
3. WORKER_STEALTH 必须镜像 LATE_FIX：CreepJS 同时探测 main + SharedWorker
4. colorScheme:"dark" 在 newContext：LATE_FIX 的 matchMedia 覆盖依赖此选项
