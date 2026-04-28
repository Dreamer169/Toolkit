import { logger } from "./logger.js";
import { microsoftFetch, getMicrosoftBrowserProxy, getMicrosoftProxyEnv } from "./proxy-fetch.js";
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
let _lastStats: { total: number; clicked: number; skipped: number; failed: number; ok: number } = { total: 0, clicked: 0, skipped: 0, failed: 0, ok: 0 };

/** msgId → 失败次数；永久失败3次/瞬态失败5次才放弃（标记已读） */
const failCounts = new Map<string, number>();
/** msgId → 处理时间戳；24h 内同一邮件视为已处理，避免 Graph 索引滞后导致重复扫描 */
const recentlyHandled = new Map<string, number>();
const RECENT_TTL_MS = 24 * 60 * 60 * 1000;
function markHandled(msgId: string) {
  recentlyHandled.set(msgId, Date.now());
  // 顺手清理过期项，防止内存膨胀
  if (recentlyHandled.size > 500) {
    const cutoff = Date.now() - RECENT_TTL_MS;
    for (const [k, t] of recentlyHandled) if (t < cutoff) recentlyHandled.delete(k);
  }
}
function isRecentlyHandled(msgId: string): boolean {
  const t = recentlyHandled.get(msgId);
  if (!t) return false;
  if (Date.now() - t > RECENT_TTL_MS) { recentlyHandled.delete(msgId); return false; }
  return true;
}

/** 当前正在执行的 python 子进程集合，关闭时统一 kill */
const _inflightChildren = new Set<import("child_process").ChildProcess>();

export function setLiveVerifyEnabled(val: boolean) {
  _enabled = val;
  if (!val) {
    for (const c of _inflightChildren) {
      try { c.kill("SIGTERM"); } catch {}
    }
    _inflightChildren.clear();
    logger.info("[live-verify] 已关闭，强制中断所有在飞子进程");
  }
}
export function getLiveVerifyStatus() { return { enabled: _enabled, lastRun: _lastRun, lastStats: _lastStats }; }

/** 给账号追加 tag 并更新状态（幂等：tag 已存在不重复写） */
async function tagAccount(id: number, tag: string, status?: string): Promise<void> {
  try {
    const { query, execute } = await import("../db.js");
    const rows = await query<{ tags: string | null; status: string | null }>(
      "SELECT tags, status FROM accounts WHERE id=$1", [id]
    );
    const cur = rows[0];
    if (!cur) return;
    const mergedTags = Array.from(new Set([...(cur.tags ?? "").split(",").map(t => t.trim()).filter(Boolean), tag])).join(",");
    if (status) {
      await execute(
        "UPDATE accounts SET tags=$1, status=$2, updated_at=NOW() WHERE id=$3",
        [mergedTags || null, status, id]
      );
    } else {
      await execute("UPDATE accounts SET tags=$1, updated_at=NOW() WHERE id=$2", [mergedTags || null, id]);
    }
    logger.info({ id, tag, status }, "[live-verify] 账号已自动打标签");
  } catch (e) {
    logger.warn({ id, tag, err: String(e) }, "[live-verify] tagAccount 失败");
  }
}

/** 调用 Graph API 把邮件标记为已读，避免重复处理 */
async function markAsRead(accessToken: string, messageId: string): Promise<void> {
  try {
    await microsoftFetch(`https://graph.microsoft.com/v1.0/me/messages/${encodeURIComponent(messageId)}`, {
      method: "PATCH",
      headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
      body: JSON.stringify({ isRead: true }),
    });
  } catch (e) {
    logger.warn({ messageId, err: String(e) }, "[live-verify] 标记已读失败（不影响主流程）");
  }
}

/** 刷新 access_token，返回 token 及错误码（便于自动打标签） */
async function refreshToken(rt: string, proxy?: string | null): Promise<{ token: string | null; errorCode?: string; errorDesc?: string }> {
  try {
    const r = await microsoftFetch("https://login.microsoftonline.com/common/oauth2/v2.0/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "refresh_token",
        client_id: OAUTH_CLIENT_ID,
        refresh_token: rt,
        scope: "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/User.Read offline_access",
      }).toString(),
    }, proxy);
    const td = await r.json() as { access_token?: string; error?: string; error_description?: string };
    if (td.access_token) return { token: td.access_token };
    return { token: null, errorCode: td.error, errorDesc: td.error_description };
  } catch (e) {
    return { token: null, errorCode: "network_error", errorDesc: String(e) };
  }
}

async function runOnce() {
  if (_running) { logger.info("[live-verify] 上次轮询尚未结束，跳过"); return; }
  _running = true;
  const stats = { total: 0, clicked: 0, skipped: 0, failed: 0, ok: 0 };
  try {
    const { query } = await import("../db.js");
    // 排除 suspended / abuse_mode / token_invalid 账号
    const rows = await query<{ id: number; email: string; token: string | null; refresh_token: string | null }>(
      `SELECT id, email, token, refresh_token FROM accounts
       WHERE platform='outlook'
         AND COALESCE(status,'') != 'suspended'
         AND COALESCE(tags,'') NOT LIKE '%abuse_mode%'
         AND COALESCE(tags,'') NOT LIKE '%token_invalid%'
         AND (
           (token IS NOT NULL AND token != '')
           OR (refresh_token IS NOT NULL AND refresh_token != '')
         )`,
      []
    );
    stats.total = rows.length;
    if (!rows.length) { _running = false; return; }
    logger.info({ count: rows.length }, "[live-verify] 开始本轮扫描");

    // ── 单账号处理（并发调用）────────────────────────────────────────────
    const processOneAccount = async (acc: { id: number; email: string; token: string | null; refresh_token: string | null }) => {
      try {
        let accessToken = "";

        if (acc.refresh_token) {
          const result = await refreshToken(acc.refresh_token);
          if (result.token) {
            accessToken = result.token;
          } else {
            const desc = result.errorDesc ?? "";
            const code = result.errorCode ?? "";
            if (desc.includes("AADSTS70000") || desc.includes("service abuse")) {
              logger.warn({ email: acc.email, errorCode: code }, "[live-verify] 账号触发 service abuse，自动打标签");
              await tagAccount(acc.id, "abuse_mode", "suspended");
              stats.skipped++;
              return;
            }
            if (code === "invalid_grant" || desc.includes("AADSTS70008") || desc.includes("AADSTS700082")) {
              logger.warn({ email: acc.email }, "[live-verify] refresh_token 已失效，自动打 token_invalid");
              await tagAccount(acc.id, "token_invalid", "suspended");
              stats.skipped++;
              return;
            }
            accessToken = acc.token ?? "";
          }
        } else {
          accessToken = acc.token ?? "";
        }

        if (!accessToken) { stats.skipped++; return; }

        // ── v8.46 — 禁用邮件扫描+自动点击 (保留 token 健康检查, 上面已完成) ──
        // 历史 bug: live-verify-poller 跟 accounts.ts 的 Step3 polling 都点同一个
        // verify 链接, 但 oobCode 是单次消费的. live-verify-poller 每 3s 跑赢 Step3
        // (12s 启动延迟 + 30 轮 * 15s) → 抢先点击成功 → 把 outlook 标 replit_used,
        // 但因为不知道 outlook ↔ replit 的关联, 没法把对应 replit 行写 status=registered.
        // 与此同时 Step3 polling 拿到的链接已被消费 → "invalid or has been used"
        // → verified=false → replit 行永远卡 status=unverified.
        // 修复: 只保留 token 健康检查 (refresh_token 失败 → abuse_mode/token_invalid),
        // 邮件扫描+点击完全交给 accounts.ts Step3 polling (它知道 outlook ↔ replit 关联).
        // processMessage 函数体保留, 便于通过 git revert 快速恢复.
        stats.ok++;
        return;
      } catch (e) {
        logger.warn({ email: acc.email, err: String(e) }, "[live-verify] 账号处理出错");
        stats.skipped++;
      }
    };

    // 并发执行，最多 5 个账号同时处理
    const CONCURRENCY = 5;
    for (let i = 0; i < rows.length; i += CONCURRENCY) {
      if (!_enabled) { logger.info("[live-verify] 检测到关闭信号，提前结束本轮"); break; }
      const chunk = rows.slice(i, i + CONCURRENCY);
      await Promise.allSettled(chunk.map(acc => processOneAccount(acc)));
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
  stats: { clicked: number; failed: number; skipped: number },
  proxy?: string | null
) {
  const scriptPath = path.resolve(__dirname, "../click_verify_link.py");
  // proxy: null → python 的 Graph API 调用直连 graph.microsoft.com（注释明示可达），Chromium 走 WARP auto-detect
  const params = JSON.stringify({ token: accessToken, message_id: msg.id, verify_url: "", proxy: null });
  const result = await new Promise<{ success: boolean; verify_url?: string; final_url?: string; title?: string; http_status?: number; error?: string }>((resolve) => {
    const child = spawn("python3", [scriptPath, params], { env: { ...process.env, ...getMicrosoftProxyEnv(proxy), PYTHONUNBUFFERED: "1" } });
    _inflightChildren.add(child);
    let out = "";
    child.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    child.on("close", () => {
      _inflightChildren.delete(child);
      const last = out.trim().split("\n").at(-1) ?? "";
      try { resolve(JSON.parse(last)); } catch { resolve({ success: false, error: last.slice(0, 200) }); }
    });
    child.on("error", (e) => { _inflightChildren.delete(child); resolve({ success: false, error: e.message }); });
    setTimeout(() => { try { child.kill(); } catch {} _inflightChildren.delete(child); resolve({ success: false, error: "timeout" }); }, 45_000);
  });

  const _marker  = ((result as Record<string, unknown>).verified_marker as string) ?? "";
  const _bodySnip = (((result as Record<string, unknown>).body_snippet as string) ?? "").slice(0, 120);
  // 严格只认 Python 端正文 marker == "success"。marker 缺失/为 failure 一律视为非成功，
  // 彻底消除标题启发式假阳性（"Verifying..."、"Replit"、空 title 等都会误判）。
  const trueSuccess = _marker === "success";

  // 区分瞬态故障 vs 永久故障：
  //   瞬态（代理 4xx/5xx、网络、超时、未知 http_status）→ 不计入失败次数，下轮继续
  //   永久（请求确实送到目标且 http_status 在 200-399，但页面非验证成功）→ 计入失败次数
  const status = typeof result.http_status === "number" ? result.http_status : 0;
  const errStr = (result.error ?? "").toLowerCase();
  const isTransient = !trueSuccess && (
    status === 0 ||
    status === 403 ||    // Cloudflare bot challenge — 可重试
    status === 407 ||
    status === 408 ||
    status === 429 ||
    status >= 500 ||
    errStr.includes("timeout") ||
    errStr.includes("proxy") ||
    errStr.includes("network") ||
    errStr.includes("connect") ||
    errStr.includes("reset") ||
    errStr.includes("refused") ||
    errStr.includes("econnrefused") ||
    errStr.includes("econnreset") ||
    errStr.includes("etimedout")
  );

  logger.info({
    email: acc.email,
    subject: msg.subject,
    trueSuccess,
    marker: _marker,
    bodySnip: _bodySnip,
    finalUrl: result.final_url?.slice(0, 120),
    title: result.title,
    httpStatus: result.http_status,
    error: result.error,
    transient: isTransient,
  }, "[live-verify] 点击验证结果");

  // oobCode 是单次使用的，已用/无效/过期类失败一定是终态，立即放弃
  const _bodyLow = _bodySnip.toLowerCase();
  const isTerminalUsed = !trueSuccess && (
    _marker === "failure" ||
    _bodyLow.includes("invalid or has been used") ||
    _bodyLow.includes("已使用") || _bodyLow.includes("已过期") || _bodyLow.includes("无效") ||
    _bodyLow.includes("expired") || _bodyLow.includes("invalid")
  );

  if (trueSuccess) {
    // 把 verify_url 落到共享缓存，让 python 注册端的同会话能直接接管 username 步骤
    // (避免 python 自己再读 inbox 时邮件已被 poller 消费的竞态)
    try {
      if (result.verify_url) {
        const fs = await import("fs");
        const dir = "/tmp/replit_verify_cache";
        try { fs.mkdirSync(dir, { recursive: true }); } catch {}
        const safeKey = acc.email.toLowerCase().replace(/[^a-z0-9._@+-]/g, "_");
        const payload = JSON.stringify({ verify_url: result.verify_url, ts: Date.now(), source: "live-verify-poller" });
        fs.writeFileSync(`${dir}/${safeKey}.json`, payload);
      }
    } catch (e) {
      logger.warn({ email: acc.email, err: String(e) }, "[live-verify] verify_url 缓存落盘失败 (非致命)");
    }
    await markAsRead(accessToken, msg.id);
    failCounts.delete(msg.id);
    markHandled(msg.id);
    stats.clicked++;
    // v8.17: 验证邮件被真实 verify 成功 (Replit 服务端已确认 oobCode 消费)
    // → outlook 邮箱已被永久绑定到一个 Replit 账号，立即打 replit_used 落库,
    //   避免后续轮被再次选中重复注册。严格依赖 trueSuccess (marker=success),
    //   瞬态错误 / oobCode 已用 / failure 一律不打。
    try {
      await tagAccount(acc.id, "replit_used");
      logger.info({ email: acc.email, accountId: acc.id }, "[live-verify] verify 真实成功 → 已自动标 replit_used");
    } catch (e) {
      logger.warn({ email: acc.email, err: String(e) }, "[live-verify] 标 replit_used 失败 (非致命)");
    }
  } else if (isTerminalUsed) {
    logger.warn({ email: acc.email, msgId: msg.id.slice(0, 20), bodySnip: _bodySnip.slice(0, 80) }, "[live-verify] oobCode 已用/无效，立即放弃并标记已读");
    await markAsRead(accessToken, msg.id);
    failCounts.delete(msg.id);
    markHandled(msg.id);
    stats.failed++;
  } else if (isTransient) {
    // 瞬态错误：仍然封顶，避免同一邮件无限重试导致 Replit 限流
    const prev = failCounts.get(msg.id) ?? 0;
    const next = prev + 1;
    failCounts.set(msg.id, next);
    if (next >= 5) {
      logger.warn({ email: acc.email, msgId: msg.id.slice(0, 20), attempts: next }, "[live-verify] 连续5次瞬态失败，放弃并标记已读（避免拖累其他邮件）");
      await markAsRead(accessToken, msg.id);
      failCounts.delete(msg.id);
      markHandled(msg.id);
      stats.failed++;
    } else {
      logger.info({ email: acc.email, httpStatus: status, error: result.error, attempts: next }, "[live-verify] 瞬态故障，下轮重试");
      stats.skipped++;
    }
  } else {
    const prev = failCounts.get(msg.id) ?? 0;
    const next  = prev + 1;
    failCounts.set(msg.id, next);
    if (next >= 3) {
      logger.warn({ email: acc.email, msgId: msg.id.slice(0, 20), attempts: next }, "[live-verify] 连续3次失败，放弃并标记已读");
      await markAsRead(accessToken, msg.id);
      failCounts.delete(msg.id);
      markHandled(msg.id);
    } else {
      logger.info({ email: acc.email, attempts: next }, "[live-verify] 点击失败，下轮重试");
    }
    stats.failed++;
  }
}

export function startLiveVerifyPoller(intervalMs = 3_000) {
  if (_intervalId) clearInterval(_intervalId);
  if (_enabled) runOnce().catch(() => {});
  _intervalId = setInterval(() => {
    if (_enabled) runOnce().catch(() => {});
  }, intervalMs);
  logger.info({ intervalMs }, "[live-verify] 实时验证轮询已启动（每 3 秒）");
}
