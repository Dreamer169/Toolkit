import { Router } from "express";
import { readFileSync } from "fs";
import { execSync } from "child_process";

const router = Router();

interface BypassStats {
  window_hours: number;
  total_attempts: number;
  phase1_natural: number;
  phase2_managed: number;
  phase2_oopif: number;
  phase3_reload_natural: number;
  phase3_reload_managed: number;
  bypass_failed: number;
  ip_retry_success: number;
  full_browser_fallback: number;
  chain_ok: number;
  chain_fail: number;
  phase1_rate: string;
  phase2_rate: string;
  phase2_oopif_rate: string;
  phase3_rate: string;
  fail_rate: string;
  success_rate: string;
  last_updated: string;
  log_lines_scanned: number;
  log_files: string[];
}

function parseBypassLogs(hours = 24): BypassStats {
  // Collect log lines using grep (fast, handles large files)
  // Log pattern: [HH:MM:SS] — no date in line, use file mtime to filter by hours
  const logFiles: string[] = [];
  for (let w = 0; w <= 5; w++) {
    const p = `/tmp/unitool_chain_v3_w${w}.log`;
    try {
      const mtime = execSync(`stat -c %Y "${p}" 2>/dev/null || echo 0`, { timeout: 2000 })
        .toString().trim();
      const age_hours = (Date.now() / 1000 - Number(mtime)) / 3600;
      if (age_hours < hours + 1) logFiles.push(p);
    } catch { /* skip */ }
  }

  const stats = {
    window_hours: hours,
    total_attempts: 0,
    phase1_natural: 0,
    phase2_managed: 0,
    phase2_oopif: 0,
    phase3_reload_natural: 0,
    phase3_reload_managed: 0,
    bypass_failed: 0,
    ip_retry_success: 0,
    full_browser_fallback: 0,
    chain_ok: 0,
    chain_fail: 0,
    log_lines_scanned: 0,
    log_files: logFiles,
  };

  if (logFiles.length === 0) {
    return {
      ...stats,
      phase1_rate: "N/A", phase2_rate: "N/A", phase2_oopif_rate: "N/A",
      phase3_rate: "N/A", fail_rate: "N/A", success_rate: "N/A",
      last_updated: new Date().toISOString(),
    };
  }

  // Use grep -c for fast counting across all log files
  const grepCount = (pattern: string): number => {
    try {
      const out = execSync(
        `grep -c "${pattern}" ${logFiles.join(" ")} 2>/dev/null | awk -F: {s+=} END{print s}`,
        { timeout: 5000 }
      ).toString().trim();
      return parseInt(out, 10) || 0;
    } catch { return 0; }
  };

  // Line counts
  try {
    const wc = execSync(
      `wc -l ${logFiles.join(" ")} 2>/dev/null | tail -1 | awk "{print \\$1}"`,
      { timeout: 3000 }
    ).toString().trim();
    stats.log_lines_scanned = parseInt(wc, 10) || 0;
  } catch { /* ignore */ }

  stats.total_attempts       = grepCount("\\[phase1\\].*waiting natural");
  stats.phase1_natural       = grepCount("natural token len=");
  // Remove reload natural from phase1 count
  const reloadNatural        = grepCount("reload natural token len=");
  stats.phase3_reload_natural = reloadNatural;
  stats.phase1_natural       = Math.max(0, stats.phase1_natural - reloadNatural);

  stats.phase2_managed       = grepCount("managed token rnd=[0-9]* len=");
  stats.phase2_oopif         = grepCount("OOPIF CDP token");
  stats.phase3_reload_managed = grepCount("reload managed token len=");
  stats.bypass_failed        = grepCount("all phases failed");
  stats.ip_retry_success     = grepCount("换IP重试成功");
  stats.full_browser_fallback = grepCount("降级全浏览器 unitool_register");
  stats.chain_ok             = grepCount("\\[CHAIN_OK\\]");
  stats.chain_fail           = grepCount("db_mark_fail\\|no_ssid_after\\|three-fallback fail");

  const attempts = stats.total_attempts || 1;
  const phase3   = stats.phase3_reload_natural + stats.phase3_reload_managed;
  const chain_total = stats.chain_ok + stats.chain_fail || 1;

  return {
    ...stats,
    phase1_rate:      `${((stats.phase1_natural / attempts) * 100).toFixed(1)}%`,
    phase2_rate:      `${((stats.phase2_managed / attempts) * 100).toFixed(1)}%`,
    phase2_oopif_rate:`${((stats.phase2_oopif / attempts) * 100).toFixed(1)}%`,
    phase3_rate:      `${((phase3 / attempts) * 100).toFixed(1)}%`,
    fail_rate:        `${((stats.bypass_failed / attempts) * 100).toFixed(1)}%`,
    success_rate:     `${((stats.chain_ok / chain_total) * 100).toFixed(1)}%`,
    last_updated:     new Date().toISOString(),
  };
}

// 60s cache
let _cache: { ts: number; data: BypassStats } | null = null;

router.get("/bypass-stats", (req, res) => {
  const hours = Math.min(Number(req.query["hours"] ?? 24), 168);
  const now = Date.now();
  if (_cache && now - _cache.ts < 60_000 && hours === 24) {
    res.json(_cache.data);
    return;
  }
  const data = parseBypassLogs(hours);
  if (hours === 24) _cache = { ts: now, data };
  res.json(data);
});

export default router;