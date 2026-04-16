import { logger } from "./logger.js";
import { spawn } from "child_process";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OAUTH_CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753";
const SUBJECT_FILTER  = "verify";

let _enabled    = true;
let _running    = false;
let _intervalId: ReturnType<typeof setInterval> | null = null;
let _lastRun: string | null = null;
let _lastStats: { total: number; clicked: number; skipped: number; failed: number } = { total: 0, clicked: 0, skipped: 0, failed: 0 };

/** msgId → 失败次数；失败3次才放弃（标记已读） */
const failCounts = new Map<string, number>();

export function setLiveVerifyEnabled(val: boolean) { _enabled = val; }
export function getLiveVerifyStatus() { return { enabled: _enabled, lastRun: _lastRun, lastStats: _lastStats }; }

/** 调用 Graph API 把邮件标记为已读，避免重复处理 */
async function markAsRead(accessToken: string, messageId: string): Promise<void> {
  try {
    await fetch(`https://graph.microsoft.com/v1.0/me/messages/${encodeURIComponent(messageId)}`, {
      method: "PATCH",
      headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
      body: JSON.stringify({ isRead: true }),
    });
  } catch (e) {
    logger.warn({ messageId, err: String(e) }, "[live-verify] 标记已读失败（不影响主流程）");
  }
}

/** 刷新 access_token */
async function refreshToken(refreshToken: string): Promise<string | null> {
  try {
    const r = await fetch("https://login.microsoftonline.com/common/oauth2/v2.0/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "refresh_token",
        client_id: OAUTH_CLIENT_ID,
        refresh_token: refreshToken,
        scope: "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/User.Read offline_access",
      }).toString(),
    });
    const td = await r.json() as { access_token?: string };
    return td.access_token ?? null;
  } catch { return null; }
}

async function runOnce() {
  if (_running) { logger.info("[live-verify] 上次轮询尚未结束，跳过"); return; }
  _running = true;
  const stats = { total: 0, clicked: 0, skipped: 0, failed: 0 };
  try {
    const { query } = await import("../db.js");
    const rows = await query<{ id: number; email: string; token: string | null; refresh_token: string | null }>(
      "SELECT id, email, token, refresh_token FROM accounts WHERE platform='outlook' AND (token IS NOT NULL AND token!='' OR refresh_token IS NOT NULL AND refresh_token!='')",
      []
    );
    stats.total = rows.length;
    if (!rows.length) { _running = false; return; }
    logger.info({ count: rows.length }, "[live-verify] 开始本轮扫描");

    for (const acc of rows) {
      try {
        // 优先用 refresh_token 换新 access_token
        let accessToken = "";
        if (acc.refresh_token) {
          const newToken = await refreshToken(acc.refresh_token);
          if (newToken) accessToken = newToken;
        }
        if (!accessToken) accessToken = acc.token ?? "";
        if (!accessToken) { stats.skipped++; continue; }

        // 搜索未读验证邮件（用 $filter 而非 $search，避免搜索缓存延迟）
        const filterUrl = `https://graph.microsoft.com/v1.0/me/messages?$filter=isRead eq false and contains(subject,'${SUBJECT_FILTER}')&$select=id,subject,isRead,receivedDateTime&$top=20&$orderby=receivedDateTime desc`;
        const gr = await fetch(filterUrl, { headers: { Authorization: `Bearer ${accessToken}` } });
        if (!gr.ok) {
          // filter 可能不支持 contains，退回 search
          const searchUrl = `https://graph.microsoft.com/v1.0/me/messages?$search="subject:${SUBJECT_FILTER}"&$select=id,subject,isRead,receivedDateTime&$top=20`;
          const gr2 = await fetch(searchUrl, { headers: { Authorization: `Bearer ${accessToken}` } });
          if (!gr2.ok) { stats.skipped++; continue; }
          const gd2 = await gr2.json() as { value?: Array<{ id: string; subject: string; isRead: boolean; receivedDateTime: string }> };
          const msgs2 = (gd2.value ?? []).filter(m => !m.isRead && m.subject.toLowerCase().includes(SUBJECT_FILTER.toLowerCase()));
          if (!msgs2.length) continue;
          for (const msg of msgs2) await processMessage(acc, msg, accessToken, stats);
          continue;
        }
        const gd = await gr.json() as { value?: Array<{ id: string; subject: string; isRead: boolean; receivedDateTime: string }> };
        const msgs = (gd.value ?? []).filter(m => m.subject.toLowerCase().includes(SUBJECT_FILTER.toLowerCase()));
        if (!msgs.length) continue;
        logger.info({ email: acc.email, count: msgs.length }, "[live-verify] 发现未读验证邮件");
        for (const msg of msgs) await processMessage(acc, msg, accessToken, stats);
      } catch (e) {
        logger.warn({ email: acc.email, err: String(e) }, "[live-verify] 账号处理出错");
        stats.skipped++;
      }
    }
  } catch (e) {
    logger.error({ err: String(e) }, "[live-verify] 轮询出错");
  } finally {
    _running  = false;
    _lastRun  = new Date().toISOString();
    _lastStats = stats;
    logger.info(stats, "[live-verify] 本轮完成");
  }
}

async function processMessage(
  acc: { id: number; email: string },
  msg: { id: string; subject: string; receivedDateTime?: string },
  accessToken: string,
  stats: { clicked: number; failed: number; skipped: number }
) {
  const scriptPath = path.resolve(__dirname, "../click_verify_link.py");
  const params = JSON.stringify({ token: accessToken, message_id: msg.id, verify_url: "" });
  const result = await new Promise<{ success: boolean; verify_url?: string; final_url?: string; title?: string; http_status?: number; error?: string }>((resolve) => {
    const child = spawn("python3", [scriptPath, params], { env: { ...process.env, PYTHONUNBUFFERED: "1" } });
    let out = "";
    child.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    child.on("close", () => {
      const last = out.trim().split("\n").at(-1) ?? "";
      try { resolve(JSON.parse(last)); } catch { resolve({ success: false, error: last.slice(0, 200) }); }
    });
    child.on("error", (e) => resolve({ success: false, error: e.message }));
    setTimeout(() => { child.kill(); resolve({ success: false, error: "timeout" }); }, 45_000);
  });

  // 真正成功：patchright 未抛异常 且 页面标题不含 404
  const trueSuccess = result.success && !!result.title && !result.title.toLowerCase().includes("404");

  logger.info({
    email: acc.email,
    subject: msg.subject,
    trueSuccess,
    finalUrl: result.final_url?.slice(0, 120),
    title: result.title,
    httpStatus: result.http_status,
    error: result.error,
  }, "[live-verify] 点击验证结果");

  if (trueSuccess) {
    // 成功后才标记已读，并清除失败计数
    await markAsRead(accessToken, msg.id);
    failCounts.delete(msg.id);
    stats.clicked++;
  } else {
    // 失败：计数+1；达到3次才放弃并标记已读
    const prev = failCounts.get(msg.id) ?? 0;
    const next  = prev + 1;
    failCounts.set(msg.id, next);
    if (next >= 3) {
      logger.warn({ email: acc.email, msgId: msg.id.slice(0, 20), attempts: next }, "[live-verify] 连续3次失败，放弃并标记已读");
      await markAsRead(accessToken, msg.id);
      failCounts.delete(msg.id);
    } else {
      logger.info({ email: acc.email, attempts: next }, "[live-verify] 点击失败，下轮重试");
    }
    stats.failed++;
  }
}

export function startLiveVerifyPoller(intervalMs = 10_000) {
  if (_intervalId) clearInterval(_intervalId);
  runOnce().catch(() => {});
  _intervalId = setInterval(() => {
    if (_enabled) runOnce().catch(() => {});
  }, intervalMs);
  logger.info({ intervalMs }, "[live-verify] 实时验证轮询已启动（每 10 秒）");
}
