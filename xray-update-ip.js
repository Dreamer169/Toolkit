#!/usr/bin/env node
// 自动从 DNS 解析最新 CF IP 并更新 xray.json
const dns = require('dns');
const fs = require('fs');
const CFG = '/root/Toolkit/xray.json';
const DOMAIN = 'iam.jimhacker.qzz.io';

dns.resolve4(DOMAIN, (err, ipv4) => {
  dns.resolve6(DOMAIN, (err6, ipv6) => {
    const ips = [...(ipv4 || [])].filter(Boolean);
    if (ips.length === 0) {
      console.log('  DNS解析失败，保留现有IP');
      process.exit(1);
    }
    try {
      const cfg = JSON.parse(fs.readFileSync(CFG, 'utf8'));
      cfg.outbounds.forEach((ob, i) => {
        if (ob.settings && ob.settings.vnext) {
          ob.settings.vnext[0].address = ips[i % ips.length];
        }
      });
      fs.writeFileSync(CFG, JSON.stringify(cfg, null, 2));
      console.log('  Xray IP已更新: ' + ips.join(', ') + ' ✅');
    } catch(e) {
      console.log('  更新失败: ' + e.message);
      process.exit(1);
    }
  });
});
