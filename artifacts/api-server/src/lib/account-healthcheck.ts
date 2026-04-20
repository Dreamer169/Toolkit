/**
 * 账号健康检查器
 * 每5分钟扫描所有 Outlook 账号，自动处理：
 *  1. 无 OAuth token + 有密码 → 全自动走设备码授权补 token
 *  2. token 刷新失败（abuse_mode / invalid_grant）→ 已由 live-verify-poller 在线打标；
 *     healthcheck 负责离线扫描（无 refresh 但 token 已过期的边角情况）
 */
import { logger } from "./logger.js";
import { microsoftFetch, getMicrosoftBrowserProxy, getMicrosoftProxyEnv } from "./proxy-fetch.js";
import { spawn } from "child_process";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OAUTH_CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753";

let _running    = false;
let _intervalId: ReturnType<typeof setInterval> | null = null;

// ── 获取设备码 ─────────────────────────────────────────────────────────────
async function getDeviceCode(email: string, proxy?: string | null): Promise<{
  deviceCode: string; userCode: string; verificationUri: string;
} | null> {
  try {
    const r = await microsoftFetch("https://login.microsoftonline.com/common/oauth2/v2.0/devicecode", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        client_id: OAUTH_CLIENT_ID,
        scope: "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/User.Read offline_access",
      }).toString(),
    }, proxy);
    const d = await r.json() as { device_code?: string; user_code?: string; verification_uri?: string; error?: string };
    if (!d.device_code || !d.user_code) {
      logger.warn({ email, error: d.error }, "[healthcheck] 获取设备码失败");
      return null;
    }
    return { deviceCode: d.device_code, userCode: d.user_code, verificationUri: d.verification_uri ?? "" };
  } catch (e) {
    logger.warn({ email, err: String(e) }, "[healthcheck] 获取设备码网络错误");
    return null;
  }
}

// ── 轮询 token（等 patchright 完成浏览器操作后） ──────────────────────────
async function pollForToken(deviceCode: string, proxy?: string | null, maxAttempts = 18): Promise<{
  accessToken: string; refreshToken: string;
} | null> {
  for (let i = 0; i < maxAttempts; i++) {
    await new Promise(r => setTimeout(r, 5_000));
    try {
      const r = await microsoftFetch("https://login.microsoftonline.com/common/oauth2/v2.0/token", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "urn:ietf:params:oauth:grant-type:device_code",
          client_id: OAUTH_CLIENT_ID,
          device_code: deviceCode,
        }).toString(),
      }, proxy);
      const d = await r.json() as { access_token?: string; refresh_token?: string; error?: string };
      if (d.access_token) return { accessToken: d.access_token, refreshToken: d.refresh_token ?? "" };
      if (d.error === "access_denied" || d.error === "expired_token") return null;
      // authorization_pending → continue polling
    } catch { /* ignore, retry */ }
  }
  return null;
}

// ── 单账号全自动 OAuth ─────────────────────────────────────────────────────
async function autoOAuth(acc: { id: number; email: string; password: string }): Promise<boolean> {
  logger.info({ email: acc.email }, "[healthcheck] 开始自动补授权");

  const proxy = getMicrosoftBrowserProxy();
  const dc = await getDeviceCode(acc.email, proxy);
  if (!dc) return false;

  logger.info({ email: acc.email, userCode: dc.userCode }, "[healthcheck] 设备码已获取，启动 patchright");

  const scriptPath = path.resolve(__dirname, "../auto_device_code.py");
  const payload    = JSON.stringify([{
    email:     acc.email,
    password:  acc.password,
    userCode:  dc.userCode,
    accountId: acc.id,
  }]);

  // patchright 完成浏览器端授权
  const autoResult = await new Promise<{ status: string; msg?: string }>((resolve) => {
    const child = spawn("python3", [scriptPath, payload, proxy], {
      env: { ...process.env, ...getMicrosoftProxyEnv(proxy), PYTHONUNBUFFERED: "1" },
    });
    let out = "";
    child.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    child.stderr.on("data", (d: Buffer) => {
      const line = d.toString().trim();
      if (line) logger.debug({ email: acc.email, line }, "[healthcheck] auto_device_code stderr");
    });
    child.on("close", () => {
      const match = out.match(/RESULTS:(.+)/);
      if (match) {
        try {
          const results = JSON.parse(match[1]) as Array<{ status: string; msg?: string }>;
          resolve(results[0] ?? { status: "error", msg: "empty results" });
        } catch { resolve({ status: "error", msg: "parse error" }); }
      } else {
        resolve({ status: "error", msg: out.slice(-200) });
      }
    });
    child.on("error", (e) => resolve({ status: "error", msg: e.message }));
    setTimeout(() => { child.kill(); resolve({ status: "timeout" }); }, 120_000);
  });

  if (autoResult.status !== "done") {
    logger.warn({ email: acc.email, status: autoResult.status, msg: autoResult.msg },
      "[healthcheck] patchright 授权失败");
    return false;
  }

  logger.info({ email: acc.email }, "[healthcheck] 浏览器授权完成，轮询 token");

  const tokens = await pollForToken(dc.deviceCode, proxy);
  if (!tokens) {
    logger.warn({ email: acc.email }, "[healthcheck] token 轮询超时");
    return false;
  }

  const { execute } = await import("../db.js");
  await execute(
    "UPDATE accounts SET token=$1, refresh_token=$2, status='active', updated_at=NOW() WHERE id=$3",
    [tokens.accessToken, tokens.refreshToken, acc.id]
  );
  logger.info({ email: acc.email }, "[healthcheck] 自动补授权成功，token 已入库");
  return true;
}

// ── 主扫描逻辑 ────────────────────────────────────────────────────────────
async function runCheck() {
  if (_running) return;
  _running = true;
  try {
    const { query, execute } = await import("../db.js");

    // ── 1. 找无 token 且有密码的账号（排除已挂起/已标记手动处理/已在处理中的）──
    const needsOAuth = await query<{ id: number; email: string; password: string }>(
      `SELECT id, email, password FROM accounts
       WHERE platform = 'outlook'
         AND COALESCE(status, '') NOT IN ('suspended', 'done', 'needs_oauth_pending')
         AND (token IS NULL OR token = '')
         AND (refresh_token IS NULL OR refresh_token = '')
         AND password IS NOT NULL AND password != ''
         AND COALESCE(tags, '') NOT LIKE '%abuse_mode%'
         AND COALESCE(tags, '') NOT LIKE '%needs_oauth_manual%'
       ORDER BY created_at ASC
       LIMIT 3`,  // 每轮最多并行处理3个，避免内存压力
      []
    );

    if (needsOAuth.length > 0) {
      logger.info({ count: needsOAuth.length }, "[healthcheck] 发现需要补授权的账号");

      for (const acc of needsOAuth) {
        // 标记为 pending，防止并发重复处理
        await execute(
          "UPDATE accounts SET status='needs_oauth_pending', updated_at=NOW() WHERE id=$1",
          [acc.id]
        );
        const ok = await autoOAuth(acc);
        if (!ok) {
          // 补授权失败 → 标记为需要手动处理（不再自动重试，避免无限循环）
          await execute(
            `UPDATE accounts
             SET status = 'needs_oauth',
                 tags   = (
                   SELECT NULLIF(string_agg(DISTINCT tag, ','), '')
                   FROM unnest(string_to_array(COALESCE(tags,'') || ',needs_oauth_manual', ',')) AS tag
                   WHERE tag <> ''
                 ),
                 updated_at = NOW()
             WHERE id = $1`,
            [acc.id]
          );
          logger.warn({ email: acc.email }, "[healthcheck] 自动补授权失败，已标记 needs_oauth_manual");
        }
      }
    }


    // ── 1b. 定期重试 needs_oauth_manual 账号（每12小时一次）──────────────
    const manualRetryRows = await query<{ id: number; email: string; password: string }>(
      `SELECT id, email, password FROM accounts
       WHERE platform = 'outlook'
         AND status NOT IN ('suspended', 'done', 'needs_oauth_pending')
         AND COALESCE(tags, '') LIKE '%needs_oauth_manual%'
         AND COALESCE(tags, '') NOT LIKE '%abuse_mode%'
         AND password IS NOT NULL AND password != ''
         AND updated_at < NOW() - INTERVAL '12 hours'
       ORDER BY updated_at ASC
       LIMIT 2`,
      []
    );
    if (manualRetryRows.length > 0) {
      logger.info({ count: manualRetryRows.length }, "[healthcheck] 发现可重试的 needs_oauth_manual 账号");
      for (const acc of manualRetryRows) {
        await execute("UPDATE accounts SET status='needs_oauth_pending', updated_at=NOW() WHERE id=$1", [acc.id]);
        const ok = await autoOAuth(acc);
        if (ok) {
          // 清除 needs_oauth_manual 标签
          await execute(
            `UPDATE accounts SET tags = NULLIF(TRIM(BOTH ',' FROM
               REGEXP_REPLACE(COALESCE(tags,''), '(^|,?)needs_oauth_manual(,|$)', ',', 'g')
             ), ','), status='active', updated_at=NOW() WHERE id=$1`,
            [acc.id]
          );
          logger.info({ email: acc.email }, "[healthcheck] needs_oauth_manual 重新授权成功，标签已清除");
        } else {
          await execute("UPDATE accounts SET status='needs_oauth', updated_at=NOW() WHERE id=$1", [acc.id]);
          logger.warn({ email: acc.email }, "[healthcheck] needs_oauth_manual 重试仍失败，12h后再试");
        }
      }
    }

    // ── 2. 扫描有 token/refresh 但状态异常（未在 live-verify 中覆盖到）的账号 ──
    // 只检查 status=active 且带有 needs_oauth_pending 遗留的清理
    await execute(
      `UPDATE accounts SET status='needs_oauth'
       WHERE platform='outlook'
         AND status='needs_oauth_pending'
         AND updated_at < NOW() - INTERVAL '10 minutes'`,
      []
    );

  } catch (e) {
    logger.error({ err: String(e) }, "[healthcheck] 账号健康检查出错");
  } finally {
    _running = false;
  }
}

// ── 对外接口 ──────────────────────────────────────────────────────────────
export function startAccountHealthcheck(intervalMs = 5 * 60 * 1000) {
  if (_intervalId) clearInterval(_intervalId);
  _intervalId = setInterval(() => runCheck().catch(() => {}), intervalMs);
  // 延迟45秒首次运行，等待 api-server 完全就绪
  setTimeout(() => runCheck().catch(() => {}), 45_000);
  logger.info({ intervalMs }, "[healthcheck] 账号健康检查已启动（每 5 分钟）");
}
