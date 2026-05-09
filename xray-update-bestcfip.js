#!/usr/bin/env node
// xray-update-bestcfip.js v1.0
// 从 joname1/BestCFip 获取最优 CF IP，随机分配到 xray.json 所有 VLESS 出口
// 每4小时由 cron 触发，与 xray-watchdog.sh 互补（watchdog 只在断连时被动修复）

const https = require("https");
const fs    = require("fs");
const CFG   = "/root/Toolkit/xray.json";

const SOURCES = [
  "https://raw.githubusercontent.com/joname1/BestCFip/refs/heads/main/ipv4.txt",
  "https://raw.gitmirror.com/joname1/BestCFip/refs/heads/main/ipv4.txt",
];

function log(msg) {
  const ts = new Date().toISOString().replace("T"," ").slice(0,19);
  console.log(`[${ts}] ${msg}`);
}

function fetchUrl(url) {
  return new Promise((resolve, reject) => {
    https.get(url, { timeout: 15000 }, (res) => {
      if (res.statusCode !== 200) { reject(new Error(`HTTP ${res.statusCode}`)); return; }
      let data = "";
      res.on("data", c => data += c);
      res.on("end", () => resolve(data));
    }).on("error", reject).on("timeout", () => reject(new Error("timeout")));
  });
}

function shuffle(arr) {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

async function fetchIPs() {
  for (const url of SOURCES) {
    try {
      log(`正在从 ${url.split("/").slice(2,4).join("/")} 拉取...`);
      const text = await fetchUrl(url);
      // 格式: 104.16.105.166:443#US-xxx 或 104.16.105.166#注释 或纯IP
      const ips = text.split("\n")
        .map(l => l.trim())
        .filter(l => l && !l.startsWith("#") && !l.startsWith("ipv4"))
        .map(l => l.split(":")[0].split("#")[0].trim())
        .filter(l => /^\d{1,3}(\.\d{1,3}){3}$/.test(l));
      if (ips.length > 0) {
        log(`✅ 获取到 ${ips.length} 个优选IP`);
        return ips;
      }
    } catch (e) {
      log(`❌ 源失败: ${e.message}`);
    }
  }
  return [];
}

async function main() {
  log("=== BestCFip xray IP 更新开始 ===");

  const ips = await fetchIPs();
  if (ips.length === 0) {
    log("❌ 所有源均失败，保留现有配置");
    process.exit(1);
  }

  const shuffled = shuffle([...ips]);

  let cfg;
  try {
    cfg = JSON.parse(fs.readFileSync(CFG, "utf8"));
  } catch (e) {
    log(`❌ 读取 xray.json 失败: ${e.message}`);
    process.exit(1);
  }

  const outbounds = cfg.outbounds.filter(o => o.settings && o.settings.vnext);
  if (outbounds.length === 0) {
    log("❌ xray.json 中无 VLESS 出口");
    process.exit(1);
  }

  const oldIPs = [...new Set(outbounds.map(o => o.settings.vnext[0].address))];

  outbounds.forEach((ob, i) => {
    ob.settings.vnext[0].address = shuffled[i % shuffled.length];
  });

  fs.writeFileSync(CFG, JSON.stringify(cfg, null, 2));

  const newIPs = [...new Set(outbounds.map(o => o.settings.vnext[0].address))];
  log(`旧IP (${oldIPs.length}个): ${oldIPs.join(", ")}`);
  log(`新IP (${newIPs.length}个): ${newIPs.slice(0,8).join(", ")}${newIPs.length>8?" ...":""}`);
  log(`共更新 ${outbounds.length} 个出口 (从 ${ips.length} 个优选IP中随机分配)`);

  // 同步到 xray 实际配置并热重载
  try {
    const { execSync } = require("child_process");
    execSync("cp /root/Toolkit/xray.json /usr/local/etc/xray/config.json");
    execSync("pm2 reload xray 2>/dev/null || true");
    log("✅ xray 已热重载");
  } catch (e) {
    log(`⚠️  重载失败(不影响配置写入): ${e.message}`);
  }

  log("=== 更新完成 ===");
}

main().catch(e => { log(`致命错误: ${e.message}`); process.exit(1); });
