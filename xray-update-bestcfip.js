#!/usr/bin/env node
// xray-update-bestcfip.js v2.1
// 从 joname1/BestCFip 获取最优 CF IP
// - 若 xray.json 有 VLESS 出口: 随机分配到出口地址（原逻辑）
// - 若无 VLESS 出口: 把优选 IP 注入 cf_ip_pool + 触发 retest（新逻辑）

const https   = require("https");
const fs      = require("fs");
const { execSync, spawnSync } = require("child_process");

const CFG        = "/root/Toolkit/xray.json";   // /root/Toolkit -> /data/Toolkit symlink, same on both servers
const POOL_STATE = "/var/lib/toolkit/cf_pool_state.json";
const POOL_API   = "/root/Toolkit/artifacts/api-server/cf_pool_api.py";

const SOURCES = [
  "https://raw.githubusercontent.com/joname1/BestCFip/refs/heads/main/ipv4.txt",
  "https://raw.gitmirror.com/joname1/BestCFip/refs/heads/main/ipv4.txt",
];

function log(msg) {
  const ts = new Date().toISOString().replace("T", " ").slice(0, 19);
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
      log(`正在从 ${url.split("/").slice(2, 4).join("/")} 拉取...`);
      const text = await fetchUrl(url);
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

function reloadXray() {
  // cp 到备用路径（静默，目标不存在时跳过）
  try { execSync("cp " + CFG + " /usr/local/etc/xray/config.json 2>/dev/null || true", { shell: true }); } catch (_) {}
  // pm2 reload 独立执行
  try {
    execSync("pm2 reload xray 2>/dev/null || true", { shell: true });
    log("✅ xray 已热重载");
  } catch (e) {
    log("⚠️  pm2 reload 失败: " + e.message);
  }
}

function injectIntoPool(ips) {
  log(`注入 ${ips.length} 个优选IP 到 cf_ip_pool...`);
  let state = { available: [], used_history: [], banned: [] };
  try { state = JSON.parse(fs.readFileSync(POOL_STATE, "utf8")); } catch (_) {}

  const banned   = new Set((state.banned || []).map(x => typeof x === "string" ? x : x.ip));
  const existing = new Set((state.available || []).map(e => e.ip));
  const candidates = shuffle(ips.filter(ip => !banned.has(ip) && !existing.has(ip))).slice(0, 60);
  const newEntries = candidates.map(ip => ({ ip, latency: 5.0, proxy: `http://${ip}:443` }));
  state.available = [...(state.available || []), ...newEntries];

  const path = require("path");
  const dir = path.dirname(POOL_STATE);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(POOL_STATE, JSON.stringify(state, null, 2));
  log(`✅ 写入 ${candidates.length} 个新候选IP（nominal 5ms）`);

  if (fs.existsSync(POOL_API)) {
    log("触发 cf_pool_api retest（threads=30, max-latency=400ms）...");
    const r = spawnSync("python3", [POOL_API, "retest",
      "--threads", "30", "--port", "443", "--max-latency", "400"],
      { timeout: 90000, encoding: "utf8" });
    try {
      const res = JSON.parse(r.stdout || "{}");
      log(`retest 完成: 保留 ${res.kept}，移除 ${res.removed} 个无效IP`);
    } catch (_) {
      if (r.stdout) log("retest: " + r.stdout.slice(0, 200));
    }
    const s = spawnSync("python3", [POOL_API, "status"], { encoding: "utf8" });
    try {
      const st = JSON.parse(s.stdout || "{}");
      log(`pool 最终: available=${st.available}, banned=${st.banned_total}`);
    } catch (_) {}
  }
}

async function main() {
  log("=== BestCFip 更新开始 ===");

  const ips = await fetchIPs();
  if (ips.length === 0) {
    log("❌ 所有源均失败，退出");
    process.exit(1);
  }

  let outbounds = [];
  try {
    const cfg = JSON.parse(fs.readFileSync(CFG, "utf8"));
    outbounds = cfg.outbounds.filter(o => o.settings && o.settings.vnext);
  } catch (e) {
    log(`⚠️  读取 xray.json 失败: ${e.message}`);
  }

  if (outbounds.length > 0) {
    const shuffled = shuffle([...ips]);
    const cfg = JSON.parse(fs.readFileSync(CFG, "utf8"));
    const allOut = cfg.outbounds.filter(o => o.settings && o.settings.vnext);
    const oldIPs = [...new Set(allOut.map(o => o.settings.vnext[0].address))];
    allOut.forEach((ob, i) => { ob.settings.vnext[0].address = shuffled[i % shuffled.length]; });
    fs.writeFileSync(CFG, JSON.stringify(cfg, null, 2));
    const newIPs = [...new Set(allOut.map(o => o.settings.vnext[0].address))];
    log(`旧IP (${oldIPs.length}个): ${oldIPs.join(", ")}`);
    log(`新IP (${newIPs.length}个): ${newIPs.slice(0, 8).join(", ")}${newIPs.length > 8 ? " ..." : ""}`);
    log(`共更新 ${allOut.length} 个VLESS出口`);
    reloadXray();
  } else {
    log("xray.json 无 VLESS 出口 → 改为注入 cf_ip_pool");
    injectIntoPool(ips);
  }

  log("=== 更新完成 ===");
}

main().catch(e => { log(`致命错误: ${e.message}`); process.exit(1); });
