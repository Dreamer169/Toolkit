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
//
// 【快速接入：只需设置 1 个 env var】
//   SELF_REGISTER_URL  = 主节点 gateway 地址
//                        例: https://ngrok地址.ngrok-free.app/api/gateway
//                        或: http://45.205.27.69:8080/api/gateway
//
// 【可选 env var】
//   SELF_REGISTER_NAME = 节点显示名称，例 "账号B-Claude"（不设则自动从域名推断）
//   SELF_GATEWAY_URL   = 本节点公开 URL（不设则自动从 REPLIT_DEV_DOMAIN 推断）
//   GATEWAY_PATH       = 注册路径后缀（默认 /api/gateway；设为空字符串可隐蔽路由）
//   SELF_REGISTER_INTERVAL_MS = 心跳间隔毫秒（默认 20 分钟）
//
// 【隐蔽模式】设置 GATEWAY_PATH="" 时，节点以根路径注册（VPS 调用 /v1/chat/completions）
// 外部看起来像普通 OpenAI 兼容 API 服务，不暴露 /api/gateway 路径
const SELF_REGISTER_URL = (process.env["SELF_REGISTER_URL"] || "").trim().replace(/\/$/, "");
const SELF_GATEWAY_URL = ((process.env["SELF_GATEWAY_URL"] || "").trim() || inferSelfGatewayUrl()).replace(/\/$/, "");
const SELF_REGISTER_NAME = (process.env["SELF_REGISTER_NAME"] || process.env["NODE_NAME"] || "").trim();
const SELF_REGISTER_INTERVAL_MS = Math.max(60_000, Number(process.env["SELF_REGISTER_INTERVAL_MS"] || 20 * 60_000));

function inferSelfGatewayUrl() {
  // GATEWAY_PATH 控制路径后缀：
  //   未设置 → 默认 /api/gateway（明确暴露 gateway 路径）
  //   设为 ""  → 根路径（隐蔽模式，外部看起来像普通 API 服务）
  //   设为其他 → 使用自定义路径，如 /v1 或 /proxy
  const rawPath = process.env["GATEWAY_PATH"];
  const gatewayPath = rawPath !== undefined ? rawPath.trim() : "/api/gateway";
  const suffix = gatewayPath === "" ? "" : gatewayPath.startsWith("/") ? gatewayPath : `/${gatewayPath}`;

  const explicitDomain = (process.env["REPLIT_DEV_DOMAIN"] || "").trim();
  if (explicitDomain) return `https://${explicitDomain}${suffix}`;
  const domains = (process.env["REPLIT_DOMAINS"] || "").split(",").map((d) => d.trim()).filter(Boolean);
  if (domains[0]) return `https://${domains[0]}${suffix}`;
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
    const data = await resp.json() as { ok?: boolean; action?: string; node?: { id?: string; name?: string } };
    if (data.ok) {
      logger.info(
        { action: data.action, nodeId: data.node?.id, nodeName: data.node?.name, target: SELF_REGISTER_URL, selfUrl: SELF_GATEWAY_URL },
        "Self-register: 已加入主节点",
      );
    } else {
      logger.warn({ data, target: SELF_REGISTER_URL }, "Self-register: 返回失败");
    }
  } catch (e) {
    if (attempt <= 3) {
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

  if (SELF_REGISTER_URL && SELF_GATEWAY_URL) {
    logger.info(
      { selfGateway: SELF_GATEWAY_URL, masterGateway: SELF_REGISTER_URL, name: SELF_REGISTER_NAME || "(auto)" },
      "Self-register: 5s 后向主节点注册",
    );
    setTimeout(() => { void doSelfRegister(); }, 5_000);
    setInterval(() => { void doSelfRegister(); }, SELF_REGISTER_INTERVAL_MS);
  } else if (!SELF_REGISTER_URL) {
    logger.info("Self-register: 未设置 SELF_REGISTER_URL，跳过（以独立模式运行）");
  }
});
