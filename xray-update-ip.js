#!/usr/bin/env node
// 自动从 DNS 解析最新 CF IP 并更新 xray.json
// v2: 双源 - iam.jimhacker.qzz.io + iam.jimhacker.eu.cc (互备)
const dns = require("dns");
const fs  = require("fs");
const CFG = "/root/Toolkit/xray.json";

const DOMAINS = ["iam.jimhacker.qzz.io", "iam.jimhacker.eu.cc"];

function resolve4(domain) {
  return new Promise(res => dns.resolve4(domain, (e, ips) => res(ips || [])));
}

async function main() {
  let ips = [];
  for (const d of DOMAINS) {
    const r = await resolve4(d);
    if (r.length) {
      console.log("  DNS [" + d + "]: " + r.join(", "));
      ips.push(...r);
    }
  }
  // deduplicate
  ips = [...new Set(ips)];
  if (ips.length === 0) {
    console.log("  DNS解析失败(两个域名均无结果)，保留现有IP");
    process.exit(1);
  }
  try {
    const cfg = JSON.parse(fs.readFileSync(CFG, "utf8"));
    cfg.outbounds.forEach((ob, i) => {
      if (ob.settings && ob.settings.vnext) {
        ob.settings.vnext[0].address = ips[i % ips.length];
      }
    });
    fs.writeFileSync(CFG, JSON.stringify(cfg, null, 2));
    console.log("  Xray IP已更新: " + ips.join(", ") + " ✅");
  } catch(e) {
    console.log("  更新失败: " + e.message);
    process.exit(1);
  }
}

main();
