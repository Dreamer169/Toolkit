import app from "./app";
import { logger } from "./lib/logger";

const rawPort = process.env["PORT"];

if (!rawPort) {
  throw new Error(
    "PORT environment variable is required but was not provided.",
  );
}

const port = Number(rawPort);

if (Number.isNaN(port) || port <= 0) {
  throw new Error(`Invalid PORT value: "${rawPort}"`);
}

// ── 启动自注册 ────────────────────────────────────────────────────────────────
// 新 Reseek 工作区启动后向主节点（远端服务器 gateway）报告自己的 URL，自动加入节点池
// 配置：在新工作区设置以下两个 env var 即可：
//   SELF_REGISTER_URL  = 主节点 gateway 地址，例如 https://ngrok地址.ngrok-free.app
//                        或 https://f38ac22e-xxxx.spock.reseek.dev/api/gateway
//   SELF_GATEWAY_URL   = 本工作区自己的 gateway 地址，例如 https://新工作区URL.reseek.dev/api/gateway
//   SELF_REGISTER_NAME = 可选，节点显示名称，例如 "账号B-OpenAI"
const SELF_REGISTER_URL = (process.env["SELF_REGISTER_URL"] || "").trim().replace(/\/$/, "");
const SELF_GATEWAY_URL = ((process.env["SELF_GATEWAY_URL"] || "").trim() || inferSelfGatewayUrl()).replace(/\/$/, "");
const SELF_REGISTER_NAME = (process.env["SELF_REGISTER_NAME"] || process.env["NODE_NAME"] || "").trim();
const SELF_REGISTER_INTERVAL_MS = Math.max(60_000, Number(process.env["SELF_REGISTER_INTERVAL_MS"] || 20 * 60_000));

function inferSelfGatewayUrl() {
  const explicitDomain = (process.env["REPLIT_DEV_DOMAIN"] || "").trim();
  if (explicitDomain) return `https://${explicitDomain}/api/gateway`;
  const domains = (process.env["REPLIT_DOMAINS"] || "").split(",").map((d) => d.trim()).filter(Boolean);
  if (domains[0]) return `https://${domains[0]}/api/gateway`;
  return "";
}

async function doSelfRegister(attempt = 1): Promise<void> {
  if (!SELF_REGISTER_URL || !SELF_GATEWAY_URL) return;
  try {
    const resp = await fetch(`${SELF_REGISTER_URL}/self-register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        gatewayUrl: SELF_GATEWAY_URL,
        name: SELF_REGISTER_NAME || undefined,
        openaiBaseUrl: process.env["AI_INTEGRATIONS_OPENAI_BASE_URL"] || undefined,
        openaiApiKey: process.env["AI_INTEGRATIONS_OPENAI_API_KEY"] || undefined,
        anthropicBaseUrl: process.env["AI_INTEGRATIONS_ANTHROPIC_BASE_URL"] || undefined,
        anthropicApiKey: process.env["AI_INTEGRATIONS_ANTHROPIC_API_KEY"] || undefined,
        geminiBaseUrl: process.env["AI_INTEGRATIONS_GEMINI_BASE_URL"] || undefined,
        geminiApiKey: process.env["AI_INTEGRATIONS_GEMINI_API_KEY"] || undefined,
      }),
      signal: AbortSignal.timeout(12_000),
    });
    const data = await resp.json() as { ok?: boolean; action?: string; node?: { id?: string } };
    if (data.ok) {
      logger.info(
        { action: data.action, nodeId: data.node?.id, target: SELF_REGISTER_URL },
        "Self-register: 已加入主节点",
      );
    } else {
      logger.warn({ data, target: SELF_REGISTER_URL }, "Self-register: 返回失败");
      // 非致命，不重试
    }
  } catch (e) {
    if (attempt <= 3) {
      // 最多重试 3 次，每次间隔 30s（服务器可能还没完全就绪）
      const delayMs = attempt * 30_000;
      logger.warn({ error: String(e), attempt, retryInMs: delayMs }, "Self-register 失败，将重试");
      setTimeout(() => { void doSelfRegister(attempt + 1); }, delayMs);
    } else {
      logger.warn({ error: String(e) }, "Self-register 已达最大重试次数，跳过");
    }
  }
}

app.listen(port, (err) => {
  if (err) {
    logger.error({ err }, "Error listening on port");
    process.exit(1);
  }

  logger.info({ port }, "Server listening");

  // 延迟 5s 再自注册：确保服务本身已完全就绪，可以被主节点探测
  if (SELF_REGISTER_URL && SELF_GATEWAY_URL) {
    logger.info(
      { selfGateway: SELF_GATEWAY_URL, masterGateway: SELF_REGISTER_URL },
      "Self-register: 5s 后向主节点注册",
    );
    setTimeout(() => { void doSelfRegister(); }, 5_000);
    setInterval(() => { void doSelfRegister(); }, SELF_REGISTER_INTERVAL_MS);
  }
});
