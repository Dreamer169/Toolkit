#!/bin/bash
# 从域名解析最新 CF IP 并更新 xray.json（使用 Node.js DNS，兼容无 dig/nslookup 环境）
DOMAIN=iam.jimhacker.qzz.io
CFG=/root/Toolkit/xray.json

FRESH_IPS=$(node -e "
require('dns').resolve4('$DOMAIN', (err, addrs) => {
  if (err || !addrs || addrs.length === 0) { process.stderr.write('DNS FAIL: ' + (err ? err.message : 'empty') + '\n'); process.exit(1); }
  console.log(addrs.join(' '));
});
" 2>/dev/null)

if [ -z "$FRESH_IPS" ]; then
  echo '  DNS解析失败，保留现有IP'
  exit 1
fi

node -e "
const fs = require('fs');
const ips = '$FRESH_IPS'.trim().split(/\s+/).filter(Boolean);
const cfg = JSON.parse(fs.readFileSync('$CFG', 'utf8'));
cfg.outbounds.forEach((ob, i) => {
  if (ob.settings && ob.settings.vnext) {
    ob.settings.vnext[0].address = ips[i % ips.length];
  }
});
fs.writeFileSync('$CFG', JSON.stringify(cfg, null, 2));
console.log('  Xray IP已更新: ' + ips.join(', '));
" && echo '  ✅ 更新成功' || echo '  ❌ 更新失败'
