# VPS 运维手册

生成时间: 2026-05-11 10:10

## 环境概览

  VPS IP           : 45.205.27.69
  用户             : root
  Toolkit 根目录   : /root/Toolkit
  项目目录         : /root/Toolkit/browser-model/artifacts/api-server
  源码             : src/lib/renderer.ts
  编译输出         : dist/index.mjs (gitignored)
  pm2 服务 ID      : 15 (browser-model)
  虚拟显示         : :99 (Xvfb，headless=false 依赖)
  工作代理端口     : 10916 (socks5://127.0.0.1:10916)
  浏览器二进制     : /opt/fingerprint-chromium/squashfs-root/opt/ungoogled-chromium/chrome

## 标准部署流程

  # 1. 编辑源码
  vim /root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts

  # 2. 构建
  cd /root/Toolkit/browser-model/artifacts/api-server && node build.mjs

  # 3. 重启
  pm2 restart 15

  # 4. 验证
  node test_smoke.mjs

## 测试脚本说明

  test_smoke.mjs          CreepJS + Pixelscan 合并，exit 0/1      ~2 min
  test_creepjs_final.mjs  CreepJS like-headless 详细输出           ~60s
  test_all16.mjs          16 项逐一列出，便于定位单项失败          ~60s
  test_pixelscan_v3.mjs   Pixelscan 完整 JSON + 逐项状态           ~40s

## 常见问题排查

### pm2 日志
  pm2 logs 15 --lines 50
  pm2 logs 15 --err --lines 50

### 服务未响应
  pm2 status
  pm2 restart 15
  curl localhost:5000/health

### Python 正则匹配 renderer.ts 失败
  # 精确打印 WORKER_STEALTH_PATCH 末尾 200 chars
  python3 -c "
  import re, sys
  src = open('/root/Toolkit/browser-model/artifacts/api-server/src/lib/renderer.ts').read()
  m = re.search('const WORKER_STEALTH_PATCH = .([\\s\\S]*?).;', src)
  print(repr(m.group(1)[-200:]))
  "

### 代理端口失效
  curl -x socks5://127.0.0.1:10916 https://httpbin.org/ip --max-time 10

### Xvfb 未运行
  ps aux | grep Xvfb
  # 若无：
  Xvfb :99 -screen 0 1920x1080x24 &

### 多行文件写入（heredoc 因特殊字符失败）
  # 永远使用 Python base64 管道方式
  echo "<base64>" | base64 -d > /path/to/file

## renderer.ts 修改注意事项

  1. 修改后必须重建：node build.mjs 才会更新 dist/index.mjs
  2. share/canShare 必须 bind()：raw 函数被 Pixelscan v6 检测到
  3. Worker 用 self 不用 window：WorkerNavigator 和 Navigator 独立原型链
  4. colorScheme 必须在 newContext：LATE_FIX 的 matchMedia 触Ꞇ盖依赖此选项
  5. LATE_FIX 大小敏感：当前 3,095 chars，匹配失败先 dump 精确内容

## 文档维护

  本目录文档仅在 VPS 本地维护，不推送 git。
  更新后手动修改 CHANGELOG.md 和 TEST_RESULTS.md 中的时间戳和数据。
