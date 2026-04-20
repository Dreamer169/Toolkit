import { Router, type Request, type Response } from "express";
import { createHash } from "crypto";
import { exec as execCmd } from "child_process";
import { promisify } from "util";
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "fs";
import { join } from "path";

const execAsync = promisify(execCmd);

// ═══ 类型 ═══════════════════════════════════════════════════════════════════

type GatewayNodeType = "remote-sub2api" | "friend-openai" | "reseek-openai" | "reseek-anthropic" | "reseek-gemini";

type GatewayNode = {
  id: string;
  name: string;
  type: GatewayNodeType;
  baseUrl: string;
  model: string;
  priority: number;
  enabled: boolean;
  downUntil: number;
  successes: number;
  failures: number;
  apiKey?: string;
  source: "built-in" | "env" | "runtime" | "register";
  lastStatus?: number;
  lastError?: string;
  lastLatencyMs?: number;
  lastUsedAt?: string;
  creditExhaustedAt?: number;
  models?: string[];
};

type ChatBody = {
  model?: string;
  messages?: Array<Record<string, unknown>>;
  stream?: boolean;
  max_tokens?: number;
  max_completion_tokens?: number;
  temperature?: number;
  top_p?: number;
  [key: string]: unknown;
};

type ResponsesApiResult = {
  id?: string;
  model?: string;
  output?: Array<{ content?: Array<{ type?: string; text?: string }> }>;
};

type AnthropicResult = {
  id?: string;
  model?: string;
  content?: Array<{ type?: string; text?: string; thinking?: string }>;
};

type GeminiResult = {
  candidates?: Array<{ content?: { parts?: Array<{ text?: string }> }; finishReason?: string }>;
};

type SelfRegisterBody = {
  gatewayUrl?: string;
  name?: string;
  execSecret?: string;
  openaiBaseUrl?: string;
  openaiApiKey?: string;
  anthropicBaseUrl?: string;
  anthropicApiKey?: string;
  geminiBaseUrl?: string;
  geminiApiKey?: string;
  models?: string[];
};

// ═══ 配置 ═══════════════════════════════════════════════════════════════════

const router = Router();
// REMOTE_GATEWAY_BASE_URL: 远端 Sub2API 地址。
// 留空 = 禁用 remote-sub2api 节点（Reseek 友节点模式，不需要再路由回 Sub2API）
// 设为 http://localhost:9090 = 远端主节点模式（PM2 ecosystem 显式配置）
const REMOTE_SUB2API_URL = (process.env["REMOTE_GATEWAY_BASE_URL"] || "").replace(/\/$/, "");
const SUB2API_ENABLED = REMOTE_SUB2API_URL.length > 0 && REMOTE_SUB2API_URL !== "disabled";
const SUB2API_API_KEY = process.env["SUB2API_API_KEY"] || process.env["SUB2API_ADMIN_KEY"] || "";
const OPENAI_BASE_URL = (process.env["AI_INTEGRATIONS_OPENAI_BASE_URL"] || "").replace(/\/$/, "");
const OPENAI_API_KEY = process.env["AI_INTEGRATIONS_OPENAI_API_KEY"] || "";
const ANTHROPIC_BASE_URL = (process.env["AI_INTEGRATIONS_ANTHROPIC_BASE_URL"] || "").replace(/\/$/, "");
const ANTHROPIC_API_KEY = process.env["AI_INTEGRATIONS_ANTHROPIC_API_KEY"] || "";
const GEMINI_BASE_URL = (process.env["AI_INTEGRATIONS_GEMINI_BASE_URL"] || "").replace(/\/$/, "");
const GEMINI_API_KEY = process.env["AI_INTEGRATIONS_GEMINI_API_KEY"] || "";
const RESEEK_OPENAI_NODE_COUNT = Math.min(24, Math.max(1, Number(process.env["RESEEK_AI_NODE_COUNT"] || 6)));
// NODE_DOWN_MS: 节点失败后的冷却时间（默认 60s）。
// 调低至 60s 是为了让 Sub2API 在添加账号后更快恢复。
const NODE_DOWN_MS = Math.max(30_000, Number(process.env["GATEWAY_NODE_DOWN_MS"] || 60_000));
const OPENAI_MODELS = [
  // GPT-5 系列
  "gpt-5.2", "gpt-5-mini", "gpt-5-nano",
  // GPT-4.1 系列（2025-04 发布）
  "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
  // GPT-4o 系列
  "gpt-4o", "gpt-4o-mini", "gpt-4o-2024-11-20", "gpt-4o-2024-08-06",
  // GPT-4 旧系列
  "gpt-4-turbo", "gpt-4-turbo-preview", "gpt-4",
  // o 系列推理模型（需 reasoning 参数）
  "o4-mini", "o3", "o3-mini", "o1", "o1-mini", "o1-preview",
];
const OPENAI_REASONING_MODELS = ["o4-mini", "o3", "o3-mini", "o1", "o1-mini"]; // 只有这些支持 reasoning 参数
const ANTHROPIC_MODELS = [
  // ── 网关内部命名（自动 thinking 参数注入）──────────────────────────────────
  "claude-opus-4-6", "claude-opus-4-6-thinking", "claude-opus-4-6-thinking-visible",
  "claude-opus-4-5", "claude-opus-4-5-thinking", "claude-opus-4-5-thinking-visible",
  "claude-opus-4-1", "claude-opus-4-1-thinking", "claude-opus-4-1-thinking-visible",
  "claude-sonnet-4-6", "claude-sonnet-4-6-thinking", "claude-sonnet-4-6-thinking-visible",
  "claude-sonnet-4-5", "claude-sonnet-4-5-thinking", "claude-sonnet-4-5-thinking-visible",
  "claude-haiku-4-5", "claude-haiku-4-5-thinking", "claude-haiku-4-5-thinking-visible",
  // ── Anthropic 官方 API model ID（直透传，无 thinking 注入）─────────────────
  "claude-opus-4-5-20251101", "claude-opus-4-1-20250514",
  "claude-sonnet-4-5-20251101", "claude-sonnet-4-5-20250514",
  "claude-haiku-4-5-20251101",
  "claude-3-7-sonnet-20250219", "claude-3-5-sonnet-20241022", "claude-3-5-sonnet-20240620",
  "claude-3-5-haiku-20241022",
  "claude-3-opus-20240229", "claude-3-sonnet-20240229", "claude-3-haiku-20240307",
];
const GEMINI_MODELS = [
  // Gemini 2.5 系列（2025 新）
  "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-preview-04-17",
  // Gemini 3 系列
  "gemini-3-flash-preview",
  // Gemini 2.0 系列
  "gemini-2.0-flash", "gemini-2.0-flash-thinking-exp", "gemini-2.0-pro-exp",
  // Gemini 1.5 系列
  "gemini-1.5-pro", "gemini-1.5-flash", "gemini-1.5-flash-8b",
];
const SUB2API_GROUP_IDS = {
  openai: 2,
  anthropic: 6,
  gemini: 3,
};

// ── Sub2API 管理员 JWT 缓存（避免每次 login）──────────────────────────────────
let _sub2apiJwtToken: string | null = null;
let _sub2apiJwtExpiry = 0;

async function getSub2ApiAdminJWT(): Promise<string | null> {
  if (_sub2apiJwtToken && _sub2apiJwtExpiry > Date.now() + 120_000) return _sub2apiJwtToken;
  if (!REMOTE_SUB2API_URL) return null;
  const email = process.env["SUB2API_ADMIN_EMAIL"] || "admin@proxy.local";
  const password = process.env["SUB2API_ADMIN_PASSWORD"] || "Proxy2024";
  try {
    const resp = await fetch(`${REMOTE_SUB2API_URL}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
      signal: AbortSignal.timeout(10_000),
    });
    const data = await resp.json() as { data?: { access_token?: string; expires_in?: number } };
    if (data?.data?.access_token) {
      _sub2apiJwtToken = data.data.access_token;
      _sub2apiJwtExpiry = Date.now() + (data.data.expires_in ?? 86400) * 1000;
      return _sub2apiJwtToken;
    }
  } catch (e) { /* login failed */ }
  return null;
}

async function ensureSub2ApiSetup(): Promise<void> {
  if (!REMOTE_SUB2API_URL) return;
  const jwt = await getSub2ApiAdminJWT();
  if (!jwt) return;
  const h = { "Authorization": `Bearer ${jwt}`, "Content-Type": "application/json" };
  // 1. 确保渠道 1 绑定到三个 provider 组（2=openai 3=gemini 6=anthropic）
  try {
    const chResp = await fetch(`${REMOTE_SUB2API_URL}/api/v1/admin/channels?page=1&page_size=1`, { headers: h, signal: AbortSignal.timeout(8_000) });
    const chData = await chResp.json() as { data?: { items?: Array<{ id: number }> } };
    const existingChannel = chData?.data?.items?.[0];
    if (existingChannel) {
      await fetch(`${REMOTE_SUB2API_URL}/api/v1/admin/channels/${existingChannel.id}`, {
        method: "PUT", headers: h,
        body: JSON.stringify({ name: existingChannel.id === 1 ? "AI Gateway" : "AI Gateway", status: "active", group_ids: [SUB2API_GROUP_IDS.openai, SUB2API_GROUP_IDS.gemini, SUB2API_GROUP_IDS.anthropic] }),
        signal: AbortSignal.timeout(8_000),
      });
    } else {
      await fetch(`${REMOTE_SUB2API_URL}/api/v1/admin/channels`, {
        method: "POST", headers: h,
        body: JSON.stringify({ name: "AI Gateway", status: "active", group_ids: [SUB2API_GROUP_IDS.openai, SUB2API_GROUP_IDS.gemini, SUB2API_GROUP_IDS.anthropic] }),
        signal: AbortSignal.timeout(8_000),
      });
    }
  } catch { /* ignore */ }
  // 2. 确保 admin user 在三个组里（通过直接 SQL 不行，用 API 试一试用户组接口）
  // 这已在部署时通过 psql 直接插入，此处跳过
}

// B15 修复：额外凭据组——聚合多套 Reseek AI integration / 第三方 OpenAI 兼容端点
// EXTRA_OPENAI_NODES=[{"baseUrl":"https://...","apiKey":"sk-...","name":"slot2","count":4}]
// EXTRA_ANTHROPIC_NODES=[{"baseUrl":"https://...","apiKey":"sk-ant-..."}]
// EXTRA_GEMINI_NODES=[{"baseUrl":"https://...","apiKey":"AIza..."}]
type ExtraNodeSlot = { baseUrl: string; apiKey: string; name?: string; count?: number };
function parseExtraSlots(envKey: string): ExtraNodeSlot[] {
  const raw = process.env[envKey] || "";
  if (!raw.trim()) return [];
  try {
    const parsed = JSON.parse(raw) as Array<Partial<ExtraNodeSlot>>;
    return parsed.filter((s) => s.baseUrl && s.apiKey).map((s) => ({
      baseUrl: String(s.baseUrl).replace(/\/$/, ""),
      apiKey: String(s.apiKey),
      name: s.name,
      count: Math.min(12, Math.max(1, Number(s.count || 2))),
    }));
  } catch { return []; }
}
const EXTRA_OPENAI_SLOTS = parseExtraSlots("EXTRA_OPENAI_NODES");
const EXTRA_ANTHROPIC_SLOTS = parseExtraSlots("EXTRA_ANTHROPIC_NODES");
const EXTRA_GEMINI_SLOTS = parseExtraSlots("EXTRA_GEMINI_NODES");

// 额度耗尽识别
const CREDIT_PATTERNS = [
  /out of credits/i, /insufficient_quota/i, /billing/i, /payment required/i,
  /exceeded.*quota/i, /exceeded.*limit/i, /quota exceeded/i, /no available OpenAI accounts/i,
  /credit balance/i, /usage limit/i,
];
function isCreditExhausted(text: string) {
  return CREDIT_PATTERNS.some((p) => p.test(text));
}

function isReplitIdleHtml(text: string) {
  return /Run this app to see the results here|Go to Replit|replit\.com/i.test(text)
    && /<html|<!doctype html/i.test(text);
}

function isBrowserAppHtml(text: string, contentType = "") {
  return /text\/html/i.test(contentType)
    || /^\s*<!doctype html/i.test(text)
    || /id=["']root["']|You need to enable JavaScript/i.test(text)
    || isReplitIdleHtml(text);
}

// 到下个 UTC 00:00 的剩余毫秒（额度冷却时长）
function msUntilUtcMidnight(): number {
  const now = Date.now();
  const midnight = new Date(now);
  midnight.setUTCDate(midnight.getUTCDate() + 1);
  midnight.setUTCHours(0, 0, 0, 0);
  return Math.max(60_000, midnight.getTime() - now);
}

// ═══ 持久化存储 ══════════════════════════════════════════════════════════════
// 运行时动态注册的节点写到 JSON 文件，服务重启后自动恢复
const DATA_DIR = (process.env["DATA_DIR"] || join(process.cwd(), "data")).replace(/\/$/, "");
const NODES_FILE = join(DATA_DIR, "gateway-nodes.json");
const EXEC_SECRET = process.env["EXEC_SECRET"] || "";

function loadPersistedNodes(): GatewayNode[] {
  try {
    if (!existsSync(NODES_FILE)) return [];
    const raw = readFileSync(NODES_FILE, "utf8");
    const arr = JSON.parse(raw) as Array<Partial<GatewayNode>>;
    return arr
      .filter((n) => n.baseUrl && n.type)
      .map((n) => ({
        id: n.id || stableId("friend", String(n.baseUrl)),
        name: n.name || "持久化节点",
        type: (n.type as GatewayNodeType) || "friend-openai",
        baseUrl: String(n.baseUrl).replace(/\/$/, ""),
        apiKey: n.apiKey,
        model: n.model || "gpt-5-mini",
        priority: n.priority ?? 3,
        enabled: n.enabled !== false,
        downUntil: 0,
        successes: 0,
        failures: 0,
        source: (n.source as GatewayNode["source"]) || "runtime",
        models: Array.isArray(n.models) ? n.models : [],
      }));
  } catch {
    return [];
  }
}

function savePersistedNodes(nodes: GatewayNode[]) {
  try {
    if (!existsSync(DATA_DIR)) mkdirSync(DATA_DIR, { recursive: true });
    writeFileSync(NODES_FILE, JSON.stringify(nodes.map((n) => ({
      id: n.id, name: n.name, type: n.type, baseUrl: n.baseUrl, source: n.source,
      apiKey: n.apiKey, model: n.model, priority: n.priority, enabled: n.enabled,
      models: n.models && n.models.length > 0 ? n.models : undefined,
    })), null, 2), "utf8");
  } catch {}
}

const runtimeNodes: GatewayNode[] = loadPersistedNodes();
let cursor = 0;

function stableId(prefix: string, value: string) {
  return `${prefix}-${createHash("sha1").update(value).digest("hex").slice(0, 10)}`;
}

function parseFriendNodesFromEnv(): GatewayNode[] {
  const raw = process.env["GATEWAY_FRIEND_NODES"];
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as Array<{
      id?: string; name?: string; baseUrl?: string;
      apiKey?: string; model?: string; priority?: number; enabled?: boolean;
    }>;
    return parsed.filter((node) => node.baseUrl).map((node, index) => ({
      id: node.id || stableId("friend", `${node.baseUrl}-${index}`),
      name: node.name || `友节点 ${index + 1}`,
      type: "friend-openai" as const,
      baseUrl: String(node.baseUrl).replace(/\/$/, ""),
      apiKey: node.apiKey,
      model: node.model || "gpt-5-mini",
      priority: node.priority ?? 3,
      enabled: node.enabled !== false,
      downUntil: 0,
      successes: 0,
      failures: 0,
      source: "env" as const,
    }));
  } catch {
    return [];
  }
}

function parseReplitSubnodes(): GatewayNode[] {
  const raw = (process.env["REPLIT_SUBNODES"] || "").trim();
  const localUrl = (process.env["LOCAL_GATEWAY_BASE_URL"] || "").trim().replace(/\/$/, "");
  const urls = [
    ...(localUrl.startsWith("http") ? [localUrl] : []),
    ...raw.split(/[,|;\s]+/).map((s) => s.trim().replace(/\/$/, "")),
  ].filter((u) => u.startsWith("http")).filter((u, i, a) => a.indexOf(u) === i);

  return urls.map((url, i) => ({
    id: stableId("replit", url),
    name: `Reseek 友节点 #${i + 1}`,
    type: "friend-openai" as const,
    baseUrl: url,
    model: "gpt-5-mini",
    priority: 2,
    enabled: true,
    downUntil: 0,
    successes: 0,
    failures: 0,
    source: "env" as const,
  }));
}

function createBuiltInNodes(): GatewayNode[] {
  return [
    {
      id: "remote-sub2api",
      name: "45.205.27.69 Sub2API",
      type: "remote-sub2api",
      baseUrl: REMOTE_SUB2API_URL || "http://disabled.invalid",
      apiKey: SUB2API_API_KEY || undefined,
      model: "upstream",
      priority: 1,
      enabled: SUB2API_ENABLED,
      downUntil: 0,
      successes: 0,
      failures: 0,
      source: "built-in",
    },
    ...Array.from({ length: RESEEK_OPENAI_NODE_COUNT }, (_, index) => ({
      id: `reseek-openai-${index + 1}`,
      name: `Reseek OpenAI 友节点 ${index + 1}`,
      type: "reseek-openai" as const,
      baseUrl: OPENAI_BASE_URL,
      apiKey: OPENAI_API_KEY,
      model: OPENAI_MODELS[index % OPENAI_MODELS.length],
      priority: 2,
      enabled: Boolean(OPENAI_BASE_URL && OPENAI_API_KEY),
      downUntil: 0,
      successes: 0,
      failures: 0,
      source: "built-in" as const,
    })),
    ...ANTHROPIC_MODELS.map((model, index) => ({
      id: `reseek-anthropic-${index + 1}`,
      name: `Reseek Anthropic 友节点 ${index + 1}`,
      type: "reseek-anthropic" as const,
      baseUrl: ANTHROPIC_BASE_URL,
      apiKey: ANTHROPIC_API_KEY,
      model,
      priority: 2,
      enabled: Boolean(ANTHROPIC_BASE_URL && ANTHROPIC_API_KEY),
      downUntil: 0,
      successes: 0,
      failures: 0,
      source: "built-in" as const,
    })),
    ...GEMINI_MODELS.map((model, index) => ({
      id: `reseek-gemini-${index + 1}`,
      name: `Reseek Gemini 友节点 ${index + 1}`,
      type: "reseek-gemini" as const,
      baseUrl: GEMINI_BASE_URL,
      apiKey: GEMINI_API_KEY,
      model,
      priority: 2,
      enabled: Boolean(GEMINI_BASE_URL && GEMINI_API_KEY),
      downUntil: 0,
      successes: 0,
      failures: 0,
      source: "built-in" as const,
    })),
    // B15: 额外 OpenAI 凭据组（每组独立 baseUrl+apiKey，避免单点限流）
    ...EXTRA_OPENAI_SLOTS.flatMap((slot, si) =>
      Array.from({ length: slot.count ?? 2 }, (_, ni) => ({
        id: `extra-openai-s${si + 1}-n${ni + 1}`,
        name: `${slot.name || `Extra OpenAI S${si + 1}`} 节点${ni + 1}`,
        type: "reseek-openai" as const,
        baseUrl: slot.baseUrl,
        apiKey: slot.apiKey,
        model: OPENAI_MODELS[ni % OPENAI_MODELS.length],
        priority: 2,
        enabled: true,
        downUntil: 0,
        successes: 0,
        failures: 0,
        source: "built-in" as const,
      }))
    ),
    // B15: 额外 Anthropic 凭据组
    ...EXTRA_ANTHROPIC_SLOTS.flatMap((slot, si) =>
      ANTHROPIC_MODELS.map((model, ni) => ({
        id: `extra-anthropic-s${si + 1}-n${ni + 1}`,
        name: `${slot.name || `Extra Anthropic S${si + 1}`} ${model}`,
        type: "reseek-anthropic" as const,
        baseUrl: slot.baseUrl,
        apiKey: slot.apiKey,
        model,
        priority: 2,
        enabled: true,
        downUntil: 0,
        successes: 0,
        failures: 0,
        source: "built-in" as const,
      }))
    ),
    // B15: 额外 Gemini 凭据组
    ...EXTRA_GEMINI_SLOTS.flatMap((slot, si) =>
      GEMINI_MODELS.map((model, ni) => ({
        id: `extra-gemini-s${si + 1}-n${ni + 1}`,
        name: `${slot.name || `Extra Gemini S${si + 1}`} ${model}`,
        type: "reseek-gemini" as const,
        baseUrl: slot.baseUrl,
        apiKey: slot.apiKey,
        model,
        priority: 2,
        enabled: true,
        downUntil: 0,
        successes: 0,
        failures: 0,
        source: "built-in" as const,
      }))
    ),
  ];
}

const builtInGatewayNodes = createBuiltInNodes();
const envFriendNodes = [...parseFriendNodesFromEnv(), ...parseReplitSubnodes()];

function allNodes() {
  return [...builtInGatewayNodes, ...envFriendNodes, ...runtimeNodes];
}

// ═══ 状态工具 ════════════════════════════════════════════════════════════════

function nodeSnapshot(node: GatewayNode) {
  const now = Date.now();
  const isCreditDown = Boolean(node.creditExhaustedAt && node.downUntil > now);
  return {
    id: node.id,
    name: node.name,
    type: node.type,
    baseUrl: (node.type || "").startsWith("reseek-") ? "Reseek AI integration" : node.baseUrl,
    model: node.model,
    priority: node.priority,
    enabled: node.enabled,
    source: node.source,
    hasApiKey: Boolean(node.apiKey),
    status: !node.enabled ? "disabled" : node.downUntil > now ? (isCreditDown ? "credit-exhausted" : "down") : "ready",
    downUntil: node.downUntil > now ? new Date(node.downUntil).toISOString() : null,
    creditExhaustedAt: node.creditExhaustedAt ? new Date(node.creditExhaustedAt).toISOString() : null,
    successes: node.successes,
    failures: node.failures,
    lastStatus: node.lastStatus,
    lastError: node.lastError,
    lastLatencyMs: node.lastLatencyMs,
    lastUsedAt: node.lastUsedAt,
    models: node.models ?? [],
  };
}

function recordSuccess(node: GatewayNode, status: number, started: number) {
  node.successes += 1;
  node.lastStatus = status;
  node.lastError = undefined;
  node.creditExhaustedAt = undefined;
  node.lastLatencyMs = Date.now() - started;
  node.lastUsedAt = new Date().toISOString();
  node.downUntil = 0;
}

function recordFailure(node: GatewayNode, status: number | undefined, error: string, started: number) {
  node.failures += 1;
  node.lastStatus = status;
  node.lastError = error.slice(0, 500);
  node.lastLatencyMs = Date.now() - started;
  node.lastUsedAt = new Date().toISOString();
  if (isCreditExhausted(error) || status === 402) {
    // 额度耗尽：冷却到 UTC 次日零点
    node.downUntil = Date.now() + msUntilUtcMidnight();
    node.creditExhaustedAt = Date.now();
  } else if (/no available.*account|ErrNoAvailableAccounts/i.test(error)) {
    // Sub2API 无可用账号（未配置订阅账号）：30s 短冷却，账号添加后快速恢复
    node.downUntil = Date.now() + 30_000;
  } else if (status === 401 || status === 403) {
    // 认证失败（账号被禁、密钥失效、工作区下线）：冷却 NODE_DOWN_MS
    // 关键：不能无限重试一个已失效的 key
    node.downUntil = Date.now() + NODE_DOWN_MS;
  } else if (
    /temporarily unavailable|invalid_endpoint|rate limit|quota|overloaded/i.test(error)
    || status === 503 || status === 429
  ) {
    // 节点过载/限流/不可用：NODE_DOWN_MS 冷却（默认 60s）
    node.downUntil = Date.now() + NODE_DOWN_MS;
  } else if (
    /ECONNREFUSED|ECONNRESET|ETIMEDOUT|ENOTFOUND|network.*error|fetch.*failed|aborted/i.test(error)
    || status === undefined
  ) {
    // 网络中断/节点宕机：NODE_DOWN_MS 冷却（之前没退避，会无限重试）
    node.downUntil = Date.now() + NODE_DOWN_MS;
  } else if (node.type === "friend-openai" && (status === 404 || isReplitIdleHtml(error))) {
    node.downUntil = Date.now() + 30 * 60_000;
  }
}

// B7 修复：当一个节点因认证/额度问题失败时，同源节点（相同 baseUrl+apiKey）
// 会遭遇完全相同的错误——批量标记为 down，避免逐一重试浪费请求
function propagateFailureToSiblings(failedNode: GatewayNode) {
  const now = Date.now();
  // 只对认证/额度类失败传播（这些是"整个 key 失效"，非偶发超时）
  if (!failedNode.downUntil || failedNode.downUntil <= now) return;
  const isAuthOrCredit = (
    failedNode.lastStatus === 401
    || failedNode.lastStatus === 403
    || failedNode.lastStatus === 402
    || Boolean(failedNode.creditExhaustedAt)
  );
  if (!isAuthOrCredit) return;
  for (const sibling of allNodes()) {
    if (sibling === failedNode) continue;
    if (sibling.baseUrl !== failedNode.baseUrl) continue;
    if (sibling.apiKey !== failedNode.apiKey) continue;
    // 用相同的 downUntil（信用额度耗尽到零点，或认证失败 NODE_DOWN_MS）
    if (sibling.downUntil < failedNode.downUntil) {
      sibling.downUntil = failedNode.downUntil;
      sibling.lastError = `[同源传播] ${failedNode.id}: ${failedNode.lastError || ""}`;
      sibling.lastStatus = failedNode.lastStatus;
      if (failedNode.creditExhaustedAt) sibling.creditExhaustedAt = failedNode.creditExhaustedAt;
    }
  }
}

function nodeMatchesRequestedModel(node: GatewayNode, model: string) {
  const normalized = normalizeRequestedModel(model);
  if (!normalized) return true;
  if (normalizeRequestedModel(node.model) === normalized) return true;
  if (node.type === "reseek-openai" && OPENAI_MODELS.includes(normalized)) return true;
  if (node.type === "reseek-anthropic" && ANTHROPIC_MODELS.includes(normalized)) return true;
  if (node.type === "reseek-gemini" && GEMINI_MODELS.includes(normalized)) return true;
  if (node.type === "friend-openai") {
    // 若友节点有明确的 models 列表，按列表过滤；否则接受所有请求（向后兼容）
    if (Array.isArray(node.models) && node.models.length > 0) {
      return node.models.includes(normalized);
    }
    return true;
  }
  return node.type === "remote-sub2api";
}

function normalizeRequestedModel(model: string) {
  const value = String(model || "").trim();
  if (!value) return "";
  return value.includes("/") ? value.split("/").pop() || value : value;
}

function parseClaudeThinkingSuffix(model: string): { baseModel: string; thinkingEnabled: boolean; thinkingVisible: boolean } {
  if (model.endsWith("-thinking-visible")) {
    return { baseModel: model.slice(0, -"-thinking-visible".length), thinkingEnabled: true, thinkingVisible: true };
  }
  if (model.endsWith("-thinking")) {
    return { baseModel: model.slice(0, -"-thinking".length), thinkingEnabled: true, thinkingVisible: false };
  }
  return { baseModel: model, thinkingEnabled: false, thinkingVisible: false };
}

function claudeMaxTokens(baseModel: string, thinkingEnabled: boolean): number {
  const table: Record<string, number> = {
    "claude-haiku-4-5": 8096,
    "claude-sonnet-4-5": 64000,
    "claude-sonnet-4-6": 64000,
    "claude-opus-4-1": 64000,
    "claude-opus-4-5": 64000,
    "claude-opus-4-6": 64000,
  };
  const modelMax = table[baseModel] ?? 32000;
  return thinkingEnabled ? Math.max(modelMax, 32000) : modelMax;
}

function orderedCandidates(model = "") {
  const now = Date.now();
  const ready = allNodes()
    .filter((node) => node.enabled && node.downUntil <= now)
    .sort((a, b) => a.priority - b.priority);
  if (ready.length < 2) return ready;
  const primary = ready.filter((node) => node.priority === 1);
  const rest = ready.filter((node) => node.priority !== 1);
  const rotated = rest
    .slice(cursor % Math.max(1, rest.length))
    .concat(rest.slice(0, cursor % Math.max(1, rest.length)));
  cursor += 1;
  return primary.concat(
    rotated.filter((node) => nodeMatchesRequestedModel(node, model)),
    rotated.filter((node) => !nodeMatchesRequestedModel(node, model)),
  );
}

// ═══ 调用实现 ════════════════════════════════════════════════════════════════

function messageText(message: Record<string, unknown>) {
  const content = message["content"];
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((part) => {
        if (typeof part === "string") return part;
        if (part && typeof part === "object" && "text" in part)
          return String((part as { text?: unknown }).text ?? "");
        return "";
      })
      .filter(Boolean)
      .join("\n");
  }
  return "";
}

function maxTokens(body: ChatBody, fallback = 8192) {
  return Math.max(1, Number(body.max_completion_tokens || body.max_tokens || fallback));
}

function requestedModel(body: ChatBody, node: GatewayNode, allowed: string[]) {
  const model = normalizeRequestedModel(typeof body.model === "string" ? body.model : "");
  return allowed.includes(model) ? model : node.model;
}

function openAiCompatibleBody(body: ChatBody, node: GatewayNode) {
  const next: ChatBody = { ...body };
  if (typeof next.model === "string") next.model = normalizeRequestedModel(next.model);
  if (!next.model || next.model === "upstream") next.model = node.model;
  if (typeof next.max_tokens === "number" && typeof next.max_completion_tokens !== "number")
    next.max_completion_tokens = next.max_tokens;
  delete next.max_tokens;
  if (/^gpt-5|^o\d|^o4-/i.test(String(next.model))) {
    delete next.temperature;
    delete next.top_p;
  }
  return next;
}

function responsesInput(body: ChatBody) {
  return (body.messages ?? [])
    .map((message) => `${String(message["role"] ?? "user").toUpperCase()}:\n${messageText(message)}`)
    .join("\n\n");
}

function extractResponsesText(data: ResponsesApiResult) {
  return (data.output ?? [])
    .flatMap((item) => item.content ?? [])
    .filter((content) => content.type === "output_text" && content.text)
    .map((content) => content.text)
    .join("");
}

function extractAnthropicText(data: AnthropicResult, thinkingVisible = false) {
  const blocks = data.content ?? [];
  const thinkingTexts = blocks
    .filter((b) => b.type === "thinking" && b.thinking)
    .map((b) => b.thinking ?? "");
  const text = blocks
    .filter((b) => b.type === "text" && b.text)
    .map((b) => b.text ?? "")
    .join("");
  if (thinkingVisible && thinkingTexts.length > 0) {
    return `<thinking>\n${thinkingTexts.join("\n")}\n</thinking>\n\n${text}`;
  }
  return text;
}

function extractGeminiText(data: GeminiResult) {
  return (data.candidates ?? [])
    .flatMap((candidate) => candidate.content?.parts ?? [])
    .map((part) => part.text ?? "")
    .join("");
}

function chatCompletionJson(node: GatewayNode, model: string, content: string, id?: string) {
  return JSON.stringify({
    id: id || `chatcmpl-${Date.now()}`,
    object: "chat.completion",
    created: Math.floor(Date.now() / 1000),
    model,
    choices: [{ index: 0, message: { role: "assistant", content }, finish_reason: "stop" }],
    gateway_node: node.id,
    gateway_provider: node.type,
  });
}

async function fetchTextWithTimeout(url: string, init: RequestInit, timeoutMs = 120_000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...init, signal: controller.signal });
    const text = await response.text();
    return { response, text };
  } finally {
    clearTimeout(timeout);
  }
}

async function callOpenAiCompatibleNode(node: GatewayNode, req: Request, body: ChatBody) {
  const requestBody = openAiCompatibleBody(body, node);
  const authorization = node.apiKey ? `Bearer ${node.apiKey}` : req.header("authorization");
  return await fetchTextWithTimeout(`${node.baseUrl}/v1/chat/completions`, {
    method: "POST",
    headers: {
      ...(authorization ? { Authorization: authorization } : {}),
      "Content-Type": "application/json",
      "ngrok-skip-browser-warning": "1",
      ...(node.type === "friend-openai" ? { "x-gateway-hop": "1" } : {}),
    },
    body: JSON.stringify(requestBody),
  });
}

function inheritedAuthHeaders(req: Request, extraHeaders: Record<string, string> = {}) {
  const headers: Record<string, string> = { ...extraHeaders };
  for (const name of [
    "authorization",
    "x-api-key",
    "x-goog-api-key",
    "anthropic-version",
    "anthropic-beta",
    "openai-organization",
    "openai-project",
  ]) {
    const value = req.header(name);
    if (value && !headers[name] && !headers[name.toLowerCase()]) headers[name] = value;
  }
  return headers;
}

function sseChunk(id: string, model: string, delta: Record<string, unknown>, finishReason: string | null = null) {
  return {
    id,
    object: "chat.completion.chunk",
    created: Math.floor(Date.now() / 1000),
    model,
    choices: [{ index: 0, delta, finish_reason: finishReason }],
  };
}

function isSub2ApiEmptyAccount(node: GatewayNode, status: number, text: string) {
  return node.type === "remote-sub2api"
    && status === 503
    && /no available.*account|temporarily unavailable|Service temporarily unavailable/i.test(text);
}

async function callOpenAiResponsesNode(node: GatewayNode, body: ChatBody) {
  const model = requestedModel(body, node, OPENAI_MODELS);
  const outputTokens = Math.max(512, maxTokens(body));
  // BUG FIX: reasoning 参数仅对 o 系列推理模型有效（o4-mini, o3 等）
  // gpt-5.x 传 reasoning 会返回 400，必须剔除
  const isReasoningModel = OPENAI_REASONING_MODELS.some((m) => model === m || model.startsWith(m));
  const payload: Record<string, unknown> = {
    model,
    input: responsesInput(body),
    max_output_tokens: outputTokens,
  };
  if (isReasoningModel) {
    payload["reasoning"] = { effort: "low" };
  }
  const result = await fetchTextWithTimeout(`${node.baseUrl}/responses`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${node.apiKey}`,
      "Content-Type": "application/json",
      "ngrok-skip-browser-warning": "1",
    },
    body: JSON.stringify(payload),
  });
  if (!result.response.ok) return result;
  try {
    const parsed = JSON.parse(result.text || "{}") as ResponsesApiResult;
    return { response: result.response, text: chatCompletionJson(node, model, extractResponsesText(parsed), parsed.id) };
  } catch {
    return result;
  }
}

function convertMessagesForAnthropic(messages: Array<Record<string, unknown>>) {
  return messages
    .filter((m) => m["role"] !== "system")
    .map((m) => {
      const role = m["role"] === "assistant" ? "assistant" : "user";
      const content = m["content"];
      if (typeof content === "string") return { role, content };
      if (Array.isArray(content)) {
        const parts = content.map((part: unknown) => {
          if (!part || typeof part !== "object") return { type: "text", text: String(part ?? "") };
          const p = part as Record<string, unknown>;
          if (p["type"] === "text") return { type: "text", text: String(p["text"] ?? "") };
          if (p["type"] === "image_url") {
            const imageUrl = p["image_url"] as Record<string, unknown> | undefined;
            const url = typeof imageUrl === "object" ? String(imageUrl?.["url"] ?? "") : String(imageUrl ?? "");
            if (url.startsWith("data:")) {
              const match = url.match(/^data:([^;]+);base64,(.+)$/);
              if (match) return { type: "image", source: { type: "base64", media_type: match[1], data: match[2] } };
            }
            return { type: "image", source: { type: "url", url } };
          }
          if ("text" in p) return { type: "text", text: String(p["text"] ?? "") };
          return null;
        }).filter(Boolean);
        return { role, content: parts };
      }
      return { role, content: String(content ?? "") };
    });
}

async function streamAnthropicNode(node: GatewayNode, res: import("express").Response, body: ChatBody): Promise<boolean> {
  const rawModel = normalizeRequestedModel(typeof body.model === "string" ? body.model : "");
  const { baseModel, thinkingEnabled, thinkingVisible } = parseClaudeThinkingSuffix(rawModel);
  const baseModels = ANTHROPIC_MODELS.map((m) => parseClaudeThinkingSuffix(m).baseModel);
  const model = baseModels.includes(baseModel) ? baseModel : parseClaudeThinkingSuffix(node.model).baseModel || node.model;

  const system = (body.messages ?? [])
    .filter((m) => m["role"] === "system")
    .map((m) => messageText(m))
    .join("\n\n") || undefined;
  const messages = convertMessagesForAnthropic(body.messages ?? []);

  const requestedMax = body.max_completion_tokens || body.max_tokens;
  const modelMax = claudeMaxTokens(model, thinkingEnabled);
  const finalMaxTokens = requestedMax ? Math.min(Number(requestedMax), modelMax) : modelMax;

  const anthropicPayload: Record<string, unknown> = {
    model, max_tokens: finalMaxTokens,
    ...(system ? { system } : {}),
    messages, stream: true,
  };
  if (thinkingEnabled) {
    anthropicPayload["thinking"] = { type: "enabled", budget_tokens: 16000 };
    anthropicPayload["temperature"] = 1;
  }

  const ctrl = new AbortController();
  const streamTimeout = setTimeout(() => ctrl.abort(), 120_000); // B30 修复：防止流挂起永久占用连接
  const streamId = `chatcmpl-${Date.now()}`;
  const displayModel = rawModel || model;

  const started = Date.now();
  let fetchRes: Awaited<ReturnType<typeof fetch>>;
  try {
    fetchRes = await fetch(`${node.baseUrl}/messages`, {
      method: "POST",
      headers: { "x-api-key": String(node.apiKey), "anthropic-version": "2023-06-01", "Content-Type": "application/json", "ngrok-skip-browser-warning": "1" },
      body: JSON.stringify(anthropicPayload),
      signal: ctrl.signal,
    });
  } catch (e) {
    clearTimeout(streamTimeout);
    recordFailure(node, undefined, String(e), started);
    propagateFailureToSiblings(node);
    return false;
  }
  if (!fetchRes.ok || !fetchRes.body) {
    clearTimeout(streamTimeout);
    const errText = await fetchRes.text().catch(() => fetchRes.statusText);
    recordFailure(node, fetchRes.status, errText, started);
    propagateFailureToSiblings(node);
    return false;
  }

  res.status(200);
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.setHeader("X-Accel-Buffering", "no");
  res.setHeader("x-gateway-node", node.id);

  // keepalive 心跳：每 5 秒发送一次，防止 thinking 期间超时
  const keepalive = setInterval(() => { try { res.write(": keepalive\n\n"); } catch {} }, 5000);

  let inThinking = false;
  let hasThinkingBlock = false;
  let buffer = "";

  res.write(`data: ${JSON.stringify(sseChunk(streamId, displayModel, { role: "assistant", content: "" }))}

`);

  try {
    const reader = fetchRes.body.getReader();
    const decoder = new TextDecoder();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const dataStr = line.slice(6).trim();
        if (!dataStr || dataStr === "[DONE]") continue;
        let evt: Record<string, unknown>;
        try { evt = JSON.parse(dataStr); } catch { continue; }
        const type = evt["type"] as string | undefined;

        if (type === "content_block_start") {
          const block = evt["content_block"] as Record<string, unknown> | undefined;
          if (block?.["type"] === "thinking") {
            inThinking = true; hasThinkingBlock = true;
            if (thinkingVisible) res.write(`data: ${JSON.stringify(sseChunk(streamId, displayModel, { content: "<thinking>\n" }))}

`);
          } else if (block?.["type"] === "text" && hasThinkingBlock) {
            if (thinkingVisible && inThinking) {
              res.write(`data: ${JSON.stringify(sseChunk(streamId, displayModel, { content: "\n</thinking>\n\n" }))}

`);
            }
            inThinking = false;
          }
        } else if (type === "content_block_delta") {
          const delta = evt["delta"] as Record<string, unknown> | undefined;
          if (delta?.["type"] === "thinking_delta") {
            if (thinkingVisible) res.write(`data: ${JSON.stringify(sseChunk(streamId, displayModel, { content: String(delta["thinking"] ?? "") }))}

`);
          } else if (delta?.["type"] === "text_delta") {
            res.write(`data: ${JSON.stringify(sseChunk(streamId, displayModel, { content: String(delta["text"] ?? "") }))}

`);
          }
        } else if (type === "message_delta") {
          const delta = evt["delta"] as Record<string, unknown> | undefined;
          const usage = evt["usage"] as Record<string, unknown> | undefined;
          const finishReason = String(delta?.["stop_reason"] ?? "stop");
          const usageData = usage ? { prompt_tokens: Number(usage["input_tokens"] ?? 0), completion_tokens: Number(usage["output_tokens"] ?? 0) } : undefined;
          const chunk = sseChunk(streamId, displayModel, {}, finishReason === "end_turn" ? "stop" : finishReason);
          if (usageData) (chunk as Record<string, unknown>)["usage"] = usageData;
          res.write(`data: ${JSON.stringify(chunk)}

`);
        }
      }
    }
  } catch (e) {
    clearTimeout(streamTimeout);
    clearInterval(keepalive);
    recordFailure(node, undefined, String(e), started);
    propagateFailureToSiblings(node);
    if (!res.writableEnded) res.end();
    return false;
  }

  clearTimeout(streamTimeout);
  clearInterval(keepalive);
  if (inThinking && thinkingVisible) res.write(`data: ${JSON.stringify(sseChunk(streamId, displayModel, { content: "\n</thinking>\n\n" }))}

`);
  res.write("data: [DONE]\n\n");
  res.end();
  recordSuccess(node, fetchRes.status, started);
  return true;
}

async function callAnthropicNode(node: GatewayNode, body: ChatBody) {
  // ── 解析 thinking suffix ──────────────────────────────────────────────────
  const rawModel = normalizeRequestedModel(typeof body.model === "string" ? body.model : "");
  const { baseModel, thinkingEnabled, thinkingVisible } = parseClaudeThinkingSuffix(rawModel);
  const baseModels = ANTHROPIC_MODELS.map((m) => parseClaudeThinkingSuffix(m).baseModel);
  const model = baseModels.includes(baseModel) ? baseModel : parseClaudeThinkingSuffix(node.model).baseModel || node.model;

  // ── 消息格式转换 ──────────────────────────────────────────────────────────
  const system = (body.messages ?? [])
    .filter((message) => message["role"] === "system")
    .map(messageText)
    .join("\n\n") || undefined;
  const messages = convertMessagesForAnthropic(body.messages ?? []);

  // ── max_tokens 按模型表决定 ───────────────────────────────────────────────
  const requestedMax = body.max_completion_tokens || body.max_tokens;
  const modelMax = claudeMaxTokens(model, thinkingEnabled);
  const finalMaxTokens = requestedMax ? Math.min(Number(requestedMax), modelMax) : modelMax;

  // ── 构建请求体 ────────────────────────────────────────────────────────────
  const anthropicPayload: Record<string, unknown> = {
    model,
    max_tokens: finalMaxTokens,
    ...(system ? { system } : {}),
    messages,
  };
  if (thinkingEnabled) {
    anthropicPayload["thinking"] = { type: "enabled", budget_tokens: 16000 };
    anthropicPayload["temperature"] = 1;
  }

  const result = await fetchTextWithTimeout(`${node.baseUrl}/messages`, {
    method: "POST",
    headers: {
      "x-api-key": String(node.apiKey),
      "anthropic-version": "2023-06-01",
      "Content-Type": "application/json",
      "ngrok-skip-browser-warning": "1",
    },
    body: JSON.stringify(anthropicPayload),
  });
  if (!result.response.ok) return result;
  try {
    const parsed = JSON.parse(result.text || "{}") as AnthropicResult;
    return { response: result.response, text: chatCompletionJson(node, rawModel || model, extractAnthropicText(parsed, thinkingVisible), parsed.id) };
  } catch {
    return result;
  }
}

async function callGeminiNode(node: GatewayNode, body: ChatBody) {
  const model = requestedModel(body, node, GEMINI_MODELS);
  const contents = (body.messages ?? [])
    .filter((message) => message["role"] !== "system")
    .map((message) => ({
      role: message["role"] === "assistant" ? "model" : "user",
      parts: [{ text: messageText(message) }],
    }));
  const systemText = (body.messages ?? [])
    .filter((message) => message["role"] === "system")
    .map(messageText)
    .join("\n\n");
  const result = await fetchTextWithTimeout(`${node.baseUrl}/models/${model}:generateContent`, {
    method: "POST",
    headers: {
      "x-goog-api-key": String(node.apiKey),
      "Content-Type": "application/json",
      "ngrok-skip-browser-warning": "1",
    },
    body: JSON.stringify({
      ...(systemText ? { systemInstruction: { parts: [{ text: systemText }] } } : {}),
      contents,
      generationConfig: { maxOutputTokens: maxTokens(body) },
    }),
  });
  if (!result.response.ok) return result;
  try {
    const parsed = JSON.parse(result.text || "{}") as GeminiResult;
    return { response: result.response, text: chatCompletionJson(node, model, extractGeminiText(parsed)) };
  } catch {
    return result;
  }
}

async function callNode(node: GatewayNode, req: Request, body: ChatBody) {
  const started = Date.now();
  try {
    const result =
      node.type === "reseek-openai"
        ? await callOpenAiResponsesNode(node, body)
        : node.type === "reseek-anthropic"
          ? await callAnthropicNode(node, body)
          : node.type === "reseek-gemini"
            ? await callGeminiNode(node, body)
            : await callOpenAiCompatibleNode(node, req, body);
    if (result.response.ok) {
      recordSuccess(node, result.response.status, started);
    } else if (isSub2ApiEmptyAccount(node, result.response.status, result.text)) {
      node.lastStatus = result.response.status;
      node.lastError = result.text || result.response.statusText;
      node.lastLatencyMs = Date.now() - started;
    } else {
      recordFailure(node, result.response.status, result.text || result.response.statusText, started);
      // B7 修复：认证/额度失败立即传播到所有同源节点，避免逐一重试浪费请求
      propagateFailureToSiblings(node);
    }
    return result;
  } catch (error) {
    recordFailure(node, undefined, String(error), started);
    // 网络异常不传播：可能只是当前请求超时，兄弟节点可能正常
    throw error;
  }
}

async function streamNode(node: GatewayNode, req: Request, res: Response, body: ChatBody) {
  if (node.type === "remote-sub2api" || node.type === "friend-openai") {
    const started = Date.now();
    const requestBody = openAiCompatibleBody(body, node);
    const authorization = node.apiKey ? `Bearer ${node.apiKey}` : req.header("authorization");
    // B11 修复：加超时控制，避免上游挂起时永久占用连接（默认 120s）
    const ctrl = new AbortController();
    const streamTimeout = setTimeout(() => ctrl.abort(), 120_000);
    try {
      const response = await fetch(`${node.baseUrl}/v1/chat/completions`, {
        method: "POST",
        headers: {
          ...(authorization ? { Authorization: authorization } : {}),
          "Content-Type": "application/json",
          "ngrok-skip-browser-warning": "1",
          ...(node.type === "friend-openai" ? { "x-gateway-hop": "1" } : {}),
        },
        body: JSON.stringify({ ...requestBody, stream: true }),
        signal: ctrl.signal,
      });
      if (!response.ok || !response.body) {
        const text = await response.text();
        if (isSub2ApiEmptyAccount(node, response.status, text)) {
          node.lastStatus = response.status;
          node.lastError = text || response.statusText;
          node.lastLatencyMs = Date.now() - started;
        } else {
          recordFailure(node, response.status, text || response.statusText, started);
          propagateFailureToSiblings(node);
        }
        clearTimeout(streamTimeout);
        return false;
      }
      recordSuccess(node, response.status, started);
      res.status(200);
      res.setHeader("Content-Type", response.headers.get("content-type") || "text/event-stream");
      res.setHeader("Cache-Control", "no-cache");
      res.setHeader("Connection", "keep-alive");
      res.setHeader("x-gateway-node", node.id);
      // 监听客户端断连，提前中止上游 fetch 节约资源
      const onClientClose = () => { ctrl.abort(); };
      res.on("close", onClientClose);
      try {
        for await (const chunk of response.body) res.write(chunk);
        res.end();
      } catch (writeErr) {
        // EPIPE / ERR_HTTP_HEADERS_SENT = 客户端已断连，节点本身正常，不记故障
        const msg = String(writeErr);
        if (/EPIPE|ERR_HTTP|write after end|aborted/i.test(msg)) {
          ctrl.abort(); // 中止上游拉流
          clearTimeout(streamTimeout);
          res.off("close", onClientClose);
          return true;  // 节点调用本身成功
        }
        throw writeErr; // 真正的上游写入异常，向外抛
      }
      res.off("close", onClientClose);
      clearTimeout(streamTimeout);
      return true;
    } catch (error) {
      // B9 修复：fetch() 本身抛异常（网络断开、超时 abort）时补充 recordFailure
      clearTimeout(streamTimeout);
      // 客户端主动关闭连接导致的 abort 不算节点故障
      if (/abort/i.test(String(error)) && res.writableEnded) return true;
      recordFailure(node, undefined, String(error), started);
      // Bug12: headers 已发出（流已开始）→ 不能让外层循环再写下一节点（会污染 SSE 流）
      // 发送错误 SSE 事件通知客户端，然后终止流，外层循环停止重试
      if (res.headersSent && !res.writableEnded) {
        try {
          const errEvent = JSON.stringify({ error: { type: "stream_interrupted", message: "上游连接中断，请重试" } });
          res.write("data: " + errEvent + "\n\n");
          res.end();
        } catch {}
        return true;
      }
      return false;
    }
  }
  // reseek-anthropic 走真实 Anthropic SSE（含 thinking 心跳）
  if (node.type === "reseek-anthropic") {
    return streamAnthropicNode(node, res, body);
  }

  const result = await callNode(node, req, { ...body, stream: false });
  if (!result.response.ok) return false;
  let content = "";
  try {
    const parsed = JSON.parse(result.text) as { choices?: Array<{ message?: { content?: string } }> };
    content = parsed.choices?.[0]?.message?.content ?? "";
  } catch {}
  res.status(200);
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.setHeader("x-gateway-node", node.id);
  const streamId = `chatcmpl-${Date.now()}`;
  const model = requestedModel(body, node, [...OPENAI_MODELS, ...ANTHROPIC_MODELS, ...GEMINI_MODELS]);
  res.write(`data: ${JSON.stringify(sseChunk(streamId, model, { role: "assistant" }))}\n\n`);
  if (content)
    res.write(`data: ${JSON.stringify(sseChunk(streamId, model, { content }))}\n\n`);
  res.write(`data: ${JSON.stringify(sseChunk(streamId, model, {}, "stop"))}\n\n`);
  res.write("data: [DONE]\n\n");
  res.end();
  return true;
}

// ═══ 节点探测工具 ════════════════════════════════════════════════════════════

async function probeNodeUrl(rawUrl: string, apiKey?: string, timeoutMs = 10_000, requireOpenAiModels = false) {
  const baseUrl = rawUrl.replace(/\/$/, "");
  const started = Date.now();
  for (const path of ["/v1/models", "/health", "/nodes", "/stats"]) {
    try {
      const headers: Record<string, string> = { "Content-Type": "application/json", "ngrok-skip-browser-warning": "1" };
      if (apiKey) headers["Authorization"] = `Bearer ${apiKey}`;
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), timeoutMs);
      const res = await fetch(`${baseUrl}${path}`, { headers, signal: ctrl.signal });
      clearTimeout(timer);
      const contentType = res.headers.get("content-type") || "";
      const text = await res.text();
      // 仅对 HTML 响应检测 Replit idle 页面和浏览器 app，避免 JSON 内含 HTML 片段误判
      const isJson = contentType.includes("application/json");
      if (!isJson && isReplitIdleHtml(text)) return { ok: false, latencyMs: Date.now() - started, error: "replit_idle_placeholder" };
      if (!isJson && isBrowserAppHtml(text, contentType)) continue;
      if (!res.ok) continue;
      const data = JSON.parse(text || "{}") as { data?: Array<{ id: string }>; success?: boolean; status?: string };
      const models = Array.isArray(data.data) ? data.data.map((m) => m.id).slice(0, 100) : [];
      if (requireOpenAiModels && path !== "/v1/models") continue;
      if (requireOpenAiModels && models.length === 0) continue;
      return { ok: true, latencyMs: Date.now() - started, models };
    } catch {}
  }
  return { ok: false, latencyMs: Date.now() - started, error: "所有探测路径均无响应" };
}

// ═══ HTTP 路由 — 状态和管理 ═══════════════════════════════════════════════════

// ── 健康/统计 ─────────────────────────────────────────────────────────────────
router.get(["/health", "/v1/stats", "/stats", "/nodes"], (_req, res) => {
  const snapshots = allNodes().map(nodeSnapshot);
  const msLeft = msUntilUtcMidnight();
  res.json({
    success: snapshots.some((node) => node.status === "ready"),
    mode: "multi-provider-openai-compatible",
    nextUtcMidnight: new Date(Date.now() + msLeft).toISOString(),
    msUntilMidnight: msLeft,
    totals: {
      nodes: snapshots.length,
      ready: snapshots.filter((node) => node.status === "ready").length,
      down: snapshots.filter((node) => node.status === "down").length,
      creditExhausted: snapshots.filter((node) => node.status === "credit-exhausted").length,
      disabled: snapshots.filter((node) => node.status === "disabled").length,
      builtIn: snapshots.filter((node) => node.source === "built-in").length,
      env: snapshots.filter((node) => node.source === "env").length,
      runtime: snapshots.filter((node) => node.source === "runtime").length,
    },
    integrations: {
      openai: Boolean(OPENAI_BASE_URL && OPENAI_API_KEY),
      anthropic: Boolean(ANTHROPIC_BASE_URL && ANTHROPIC_API_KEY),
      gemini: Boolean(GEMINI_BASE_URL && GEMINI_API_KEY),
    },
    nodeDownMs: NODE_DOWN_MS,
    nodes: snapshots,
  });
});

// ── 连通性诊断 ────────────────────────────────────────────────────────────────
router.get("/diagnose", async (_req, res) => {
  const results: Record<string, { ok: boolean; latencyMs: number; error?: string; detail?: string }> = {};

  // 1. 远端 Sub2API（仅检查连通性，不需要账号）
  {
    const t = Date.now();
    try {
      const r = await fetch(`${REMOTE_SUB2API_URL}/api/v1/admin/dashboard/stats`, {
        headers: { Authorization: "Bearer test" },
        signal: AbortSignal.timeout(6000),
      });
      const text = await r.text();
      // 401 说明服务可达但需要认证，视为连通
      results["remote-sub2api"] = {
        ok: r.status === 401 || r.ok,
        latencyMs: Date.now() - t,
        detail: `HTTP ${r.status}`,
        ...(!r.ok && r.status !== 401 ? { error: text.slice(0, 100) } : {}),
      };
    } catch (e) {
      results["remote-sub2api"] = { ok: false, latencyMs: Date.now() - t, error: String(e) };
    }
  }

  // 2. Reseek OpenAI（用真实 /responses 调用，最低 token）
  if (OPENAI_BASE_URL && OPENAI_API_KEY) {
    const t = Date.now();
    try {
      const r = await fetch(`${OPENAI_BASE_URL}/responses`, {
        method: "POST",
        headers: { Authorization: `Bearer ${OPENAI_API_KEY}`, "Content-Type": "application/json" },
        body: JSON.stringify({ model: "gpt-5-nano", input: "hi", max_output_tokens: 64 }),
        signal: AbortSignal.timeout(15000),
      });
      const text = await r.text();
      results["reseek-openai"] = {
        ok: r.ok,
        latencyMs: Date.now() - t,
        detail: `HTTP ${r.status}`,
        ...(!r.ok ? { error: text.slice(0, 150) } : {}),
      };
    } catch (e) {
      results["reseek-openai"] = { ok: false, latencyMs: Date.now() - t, error: String(e) };
    }
  } else {
    results["reseek-openai"] = { ok: false, latencyMs: 0, error: "集成未配置" };
  }

  // 3. Reseek Anthropic
  if (ANTHROPIC_BASE_URL && ANTHROPIC_API_KEY) {
    const t = Date.now();
    try {
      const r = await fetch(`${ANTHROPIC_BASE_URL}/messages`, {
        method: "POST",
        headers: { "x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "Content-Type": "application/json" },
        body: JSON.stringify({ model: "claude-haiku-4-5", max_tokens: 5, messages: [{ role: "user", content: "hi" }] }),
        signal: AbortSignal.timeout(15000),
      });
      const text = await r.text();
      results["reseek-anthropic"] = {
        ok: r.ok,
        latencyMs: Date.now() - t,
        detail: `HTTP ${r.status}`,
        ...(!r.ok ? { error: text.slice(0, 150) } : {}),
      };
    } catch (e) {
      results["reseek-anthropic"] = { ok: false, latencyMs: Date.now() - t, error: String(e) };
    }
  } else {
    results["reseek-anthropic"] = { ok: false, latencyMs: 0, error: "集成未配置" };
  }

  // 4. Reseek Gemini
  if (GEMINI_BASE_URL && GEMINI_API_KEY) {
    const t = Date.now();
    try {
      const r = await fetch(`${GEMINI_BASE_URL}/models/gemini-2.5-flash:generateContent`, {
        method: "POST",
        headers: { "x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json" },
        body: JSON.stringify({ contents: [{ role: "user", parts: [{ text: "hi" }] }], generationConfig: { maxOutputTokens: 5 } }),
        signal: AbortSignal.timeout(15000),
      });
      const text = await r.text();
      results["reseek-gemini"] = {
        ok: r.ok,
        latencyMs: Date.now() - t,
        detail: `HTTP ${r.status}`,
        ...(!r.ok ? { error: text.slice(0, 150) } : {}),
      };
    } catch (e) {
      results["reseek-gemini"] = { ok: false, latencyMs: Date.now() - t, error: String(e) };
    }
  } else {
    results["reseek-gemini"] = { ok: false, latencyMs: 0, error: "集成未配置" };
  }

  const allOk = Object.values(results).some((r) => r.ok);
  res.json({ ok: allOk, timestamp: new Date().toISOString(), checks: results });
});

// ── Sub2API 管理接口透传 ──────────────────────────────────────────────────────
// 前端以 /api/gateway 为 baseUrl，向 /api/gateway/api/v1/admin/* 等路径发请求
// 这些路径在 Express 路由层收到时已去掉了 /api/gateway 前缀
// 所以这里匹配 /api/v1/* 和 /api/accounts/* 转发到远端 Sub2API
router.all(/^\/(api\/v1|api\/accounts)\//, async (req, res) => {
  const targetUrl = `${REMOTE_SUB2API_URL}${req.path}`;
  const queryStr = Object.keys(req.query).length
    ? "?" + new URLSearchParams(req.query as Record<string, string>).toString()
    : "";
  const upstream = `${targetUrl}${queryStr}`;

  try {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const auth = req.header("authorization") || req.header("x-api-key");
    if (auth) {
      headers["Authorization"] = auth.startsWith("Bearer ") ? auth : `Bearer ${auth}`;
    }

    const hasBody = ["POST", "PUT", "PATCH"].includes(req.method);
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 30_000);

    const upstream_res = await fetch(upstream, {
      method: req.method,
      headers,
      ...(hasBody && req.body ? { body: JSON.stringify(req.body) } : {}),
      signal: ctrl.signal,
    });
    clearTimeout(timer);

    const text = await upstream_res.text();
    res.status(upstream_res.status);
    const ct = upstream_res.headers.get("content-type") || "application/json";
    res.setHeader("content-type", ct);
    res.setHeader("x-proxied-to", REMOTE_SUB2API_URL);
    res.send(text);
  } catch (e) {
    res.status(502).json({ success: false, error: "远端 Sub2API 不可达", detail: String(e), target: REMOTE_SUB2API_URL });
  }
});

// ── 节点状态 (供桥接器健康同步) ───────────────────────────────────────────────
// GET /api/gateway/nodes/status — 轻量健康端点
// 只返回 friend-openai 节点，带 ready/down 状态，桥接器据此过滤死节点
router.get("/nodes/status", (_req, res) => {
  const now = Date.now();
  const nodes = allNodes()
    .filter((n) => n.type === "friend-openai" && n.enabled)
    .map((n) => {
      // 规范化 baseUrl：去掉末尾 /api（桥接器自己追加路径）
      const rawBase = n.baseUrl ?? "";
      const base = rawBase.endsWith("/api/gateway") ? rawBase.slice(0, -12) : rawBase.endsWith("/api") ? rawBase.slice(0, -4) : rawBase;
      const isDown = n.downUntil > now;
      return {
        id: n.id,
        baseUrl: base.replace(/\/$/, ""),
        status: isDown ? "down" : "ready",
        downUntil: isDown ? new Date(n.downUntil).toISOString() : null,
        lastLatencyMs: n.lastLatencyMs ?? null,
        lastError: isDown ? (n.lastError ?? null) : null,
        successes: n.successes,
        failures: n.failures,
        lastChecked: n.lastUsedAt ?? null,
      };
    });
  const ready = nodes.filter((n) => n.status === "ready").length;
  res.json({ ok: true, total: nodes.length, ready, down: nodes.length - ready, nodes });
});

// ── 节点管理 ─────────────────────────────────────────────────────────────────
router.post("/nodes", (req, res) => {
  const body = req.body as {
    nodes?: Array<{ name?: string; baseUrl?: string; apiKey?: string; model?: string; priority?: number }>;
    name?: string; baseUrl?: string; apiKey?: string; model?: string; priority?: number;
  };
  const incoming = Array.isArray(body.nodes) ? body.nodes : [body];
  const added: ReturnType<typeof nodeSnapshot>[] = [];
  for (const item of incoming) {
    if (!item.baseUrl) continue;
    const baseUrl = String(item.baseUrl).replace(/\/$/, "");
    if (allNodes().some((n) => n.baseUrl === baseUrl)) continue;
    const id = stableId("friend", `${baseUrl}-${item.model || ""}-${runtimeNodes.length}`);
    const node: GatewayNode = {
      id,
      name: item.name || `友节点 ${runtimeNodes.length + 1}`,
      type: "friend-openai",
      baseUrl,
      apiKey: item.apiKey,
      model: item.model || "gpt-5-mini",
      priority: item.priority ?? 3,
      enabled: true,
      downUntil: 0,
      successes: 0,
      failures: 0,
      source: "runtime",
    };
    runtimeNodes.push(node);
    added.push(nodeSnapshot(node));
  }
  if (added.length > 0) savePersistedNodes(runtimeNodes);
  res.json({ success: added.length > 0, added, nodes: allNodes().map(nodeSnapshot) });
});

router.delete("/nodes/:id", (req, res) => {
  const index = runtimeNodes.findIndex((node) => node.id === req.params.id);
  if (index < 0) {
    res.status(404).json({ success: false, error: "只能删除运行时添加的 friend 节点" });
    return;
  }
  const removed = runtimeNodes.splice(index, 1).map(nodeSnapshot);
  savePersistedNodes(runtimeNodes);
  res.json({ success: true, removed, nodes: allNodes().map(nodeSnapshot) });
});

router.patch("/nodes/:id", (req, res) => {
  const node = allNodes().find((n) => n.id === req.params.id);
  if (!node) {
    res.status(404).json({ success: false, error: "节点不存在" });
    return;
  }
  const { enabled, priority, apiKey, model, name } = req.body as {
    enabled?: boolean; priority?: number; apiKey?: string; model?: string; name?: string;
  };
  if (typeof enabled === "boolean") {
    node.enabled = enabled;
    if (enabled) { node.downUntil = 0; node.creditExhaustedAt = undefined; }
  }
  if (typeof priority === "number") node.priority = priority;
  if (apiKey !== undefined) node.apiKey = apiKey;
  if (model) node.model = model;
  if (name) node.name = name;
  res.json({ success: true, node: nodeSnapshot(node) });
});

// ── 单节点探测 ────────────────────────────────────────────────────────────────
router.post("/nodes/probe", async (req, res) => {
  const { baseUrl, apiKey } = req.body as { baseUrl?: string; apiKey?: string };
  if (!baseUrl) {
    res.status(400).json({ success: false, error: "baseUrl 必填" });
    return;
  }
  const result = await probeNodeUrl(baseUrl, apiKey);
  res.json({ success: result.ok, baseUrl, ...result });
});

// B26: 修复 pushSub2ApiAccount
// - 使用 JWT admin 认证（/api/v1/auth/login）而非 API key
// - 正确 payload 格式：platform, type:"apikey", credentials:{api_key,base_url}, group_ids:[X]
// - 如同名账号已存在则更新而非重复创建
async function pushSub2ApiAccount(
  provider: "openai" | "anthropic" | "gemini",
  baseUrl?: string, apiKey?: string, nodeBaseUrl?: string, name?: string,
): Promise<{ ok: boolean; skipped?: boolean; accountId?: number; error?: string }> {
  if (!REMOTE_SUB2API_URL || !baseUrl || !apiKey) return { ok: false, skipped: true };
  const jwt = await getSub2ApiAdminJWT();
  if (!jwt) return { ok: false, error: "sub2api admin JWT 获取失败" };

  const label = `${name || "友节点"}-${provider}`;
  
  // B27: 所有平台都使用友节点公开 gateway URL
  const effectiveBaseUrl = (nodeBaseUrl
    ? nodeBaseUrl : baseUrl).replace(/\/$/, "");

  const headers = { "Authorization": `Bearer ${jwt}`, "Content-Type": "application/json" };
  const groupId = SUB2API_GROUP_IDS[provider];

  // 查重：同名账号已存在则更新
  try {
    const listResp = await fetch(
      `${REMOTE_SUB2API_URL}/api/v1/admin/accounts?page=1&page_size=100`,
      { headers, signal: AbortSignal.timeout(8_000) },
    );
    const listData = await listResp.json() as { data?: { items?: Array<{ id: number; name: string }> } };
    const existing = listData?.data?.items?.find((a) => a.name === label);
    if (existing) {
      const updResp = await fetch(`${REMOTE_SUB2API_URL}/api/v1/admin/accounts/${existing.id}`, {
        method: "PUT", headers,
        body: JSON.stringify({
          name: label, platform: provider, type: "apikey", status: "active",
          credentials: { api_key: apiKey, base_url: effectiveBaseUrl },
          group_ids: [groupId], concurrency: 10, priority: 1,
        }),
        signal: AbortSignal.timeout(12_000),
      });
      const updData = await updResp.json() as { code?: number };
      return { ok: updData?.code === 0, accountId: existing.id };
    }
  } catch { /* 查重失败则继续创建 */ }

  // 创建新账号
  try {
    const resp = await fetch(`${REMOTE_SUB2API_URL}/api/v1/admin/accounts`, {
      method: "POST", headers,
      body: JSON.stringify({
        name: label, platform: provider, type: "apikey", status: "active",
        credentials: { api_key: apiKey, base_url: effectiveBaseUrl },
        group_ids: [groupId], concurrency: 10, priority: 1,
      }),
      signal: AbortSignal.timeout(12_000),
    });
    const data = await resp.json() as { code?: number; data?: { id?: number } };
    if (data?.code === 0) return { ok: true, accountId: data?.data?.id };
    return { ok: false, error: JSON.stringify(data).slice(0, 300) };
  } catch (error) {
    return { ok: false, error: String(error) };
  }
}

async function pushSelfRegisterCredentialsToSub2Api(body: SelfRegisterBody, nodeBaseUrl: string, name?: string) {
  const results = await Promise.allSettled([
    body.openaiApiKey ? pushSub2ApiAccount("openai", body.openaiBaseUrl, body.openaiApiKey, nodeBaseUrl, name) : Promise.resolve({ ok: false, skipped: true }),
    body.anthropicApiKey ? pushSub2ApiAccount("anthropic", body.anthropicBaseUrl, body.anthropicApiKey, nodeBaseUrl, name) : Promise.resolve({ ok: false, skipped: true }),
    body.geminiApiKey ? pushSub2ApiAccount("gemini", body.geminiBaseUrl, body.geminiApiKey, nodeBaseUrl, name) : Promise.resolve({ ok: false, skipped: true }),
  ]);
  return results.map((result) => result.status === "fulfilled" ? result.value : { ok: false, error: String(result.reason) });
}

// ── 批量探测 + 自动注册 ──────────────────────────────────────────────────────
router.post("/nodes/batch-probe", async (req, res) => {
  const { urls, apiKey, model, autoRegister = true, priority = 3 } = req.body as {
    urls?: string[]; apiKey?: string; model?: string; autoRegister?: boolean; priority?: number;
  };
  if (!Array.isArray(urls) || urls.length === 0) {
    res.status(400).json({ success: false, error: "urls 数组不能为空" });
    return;
  }

  const results = await Promise.allSettled(
    urls.slice(0, 50).map(async (rawUrl) => {
      const baseUrl = rawUrl.trim().replace(/\/$/, "");
      const probe = await probeNodeUrl(baseUrl, apiKey);
      let registered = false;
      let nodeId: string | undefined;
      if (probe.ok && autoRegister && !allNodes().some((n) => n.baseUrl === baseUrl)) {
        const id = stableId("friend", baseUrl);
        try {
          const hostname = new URL(baseUrl).hostname.split(".")[0];
          const node: GatewayNode = {
            id, name: `Reseek(${hostname})`, type: "friend-openai",
            baseUrl, apiKey, model: model || "gpt-5-mini",
            priority: Number(priority) || 3, enabled: true,
            downUntil: 0, successes: 0, failures: 0, source: "runtime",
            models: probe.models ?? [],
          };
          runtimeNodes.push(node);
          registered = true;
          nodeId = id;
        } catch {}
      } else if (allNodes().some((n) => n.baseUrl === baseUrl)) {
        registered = true;
        nodeId = allNodes().find((n) => n.baseUrl === baseUrl)?.id;
      }
      return { url: baseUrl, ok: probe.ok, latencyMs: probe.latencyMs, models: probe.models, error: probe.error, registered, nodeId };
    }),
  );

  const rows = results.map((r) => r.status === "fulfilled" ? r.value : { url: "", ok: false, error: String((r as PromiseRejectedResult).reason), registered: false });
  const succeeded = rows.filter((r) => r.ok).length;
  const registered = rows.filter((r) => r.registered).length;
  if (registered > 0) savePersistedNodes(runtimeNodes);
  res.json({ success: succeeded > 0, summary: { total: rows.length, succeeded, failed: rows.length - succeeded, registered }, rows, nodes: allNodes().map(nodeSnapshot) });
});

// ── 测试节点 ──────────────────────────────────────────────────────────────────
router.post("/nodes/:id/test", async (req, res) => {
  const node = allNodes().find((n) => n.id === req.params.id);
  if (!node) {
    res.status(404).json({ success: false, error: "节点不存在" });
    return;
  }
  const testBody: ChatBody = {
    model: node.model === "upstream" ? "gpt-5-mini" : node.model,
    messages: [{ role: "user", content: "Reply with OK only." }],
    max_tokens: 20,
  };
  try {
    const result = await callNode(node, req, testBody);
    if (result.response.ok) {
      let content = "";
      try {
        const parsed = JSON.parse(result.text) as { choices?: Array<{ message?: { content?: string } }> };
        content = parsed.choices?.[0]?.message?.content ?? result.text;
      } catch { content = result.text; }
      res.json({ success: true, node: node.id, latencyMs: node.lastLatencyMs, content });
    } else {
      res.json({ success: false, node: node.id, status: result.response.status, error: result.text });
    }
  } catch (e) {
    res.json({ success: false, node: node.id, error: String(e) });
  }
});

// ── 模型列表 ──────────────────────────────────────────────────────────────────
router.get("/v1/models", async (req, res) => {
  const seen = new Set<string>();
  const data: Array<Record<string, unknown>> = [];
  const errors: Array<Record<string, unknown>> = [];
  const nodes = allNodes();

  // ── 1. 本地 Reseek 集成（直接枚举，无需 HTTP）────────────────────────────
  // B12：只列出 enabled 节点对应的型号
  const hasOpenAI = nodes.some((n) => n.type === "reseek-openai" && n.enabled);
  const hasAnthropic = nodes.some((n) => n.type === "reseek-anthropic" && n.enabled);
  const hasGemini = nodes.some((n) => n.type === "reseek-gemini" && n.enabled);
  if (hasOpenAI)
    for (const m of OPENAI_MODELS)
      if (!seen.has(m)) { seen.add(m); data.push({ id: m, object: "model", created: 0, owned_by: "reseek-openai", gateway_node: "reseek-openai-pool" }); }
  if (hasAnthropic)
    for (const m of ANTHROPIC_MODELS)
      if (!seen.has(m)) { seen.add(m); data.push({ id: m, object: "model", created: 0, owned_by: "reseek-anthropic", gateway_node: "reseek-anthropic-pool" }); }
  if (hasGemini)
    for (const m of GEMINI_MODELS)
      if (!seen.has(m)) { seen.add(m); data.push({ id: m, object: "model", created: 0, owned_by: "reseek-gemini", gateway_node: "reseek-gemini-pool" }); }

  // ── 2. 友节点聚合：直接读取注册时存入的 models[]，零 HTTP 开销 ───────────
  const friendModelMap = new Map<string, string[]>();   // modelId → [nodeId, ...]
  const legacyFriendNodes: GatewayNode[] = [];          // 无 models[] 的旧节点，回退到 HTTP
  for (const node of nodes) {
    if (node.type !== "friend-openai") continue;
    if (!node.enabled || node.downUntil > Date.now()) continue;
    if (node.models && node.models.length > 0) {
      for (const m of node.models) {
        if (!friendModelMap.has(m)) friendModelMap.set(m, []);
        friendModelMap.get(m)!.push(node.id);
      }
    } else {
      legacyFriendNodes.push(node);
    }
  }
  // 去重后写入 data（model 已在本地 Reseek 中出现的跳过）
  for (const [modelId, nodeIds] of friendModelMap) {
    if (!seen.has(modelId)) {
      seen.add(modelId);
      data.push({
        id: modelId,
        object: "model",
        created: 0,
        owned_by: "friend-openai",
        gateway_node: nodeIds[0],          // 首个支持该型号的节点
        supported_nodes: nodeIds.length,   // 支持该型号的节点数
      });
    }
  }

  // ── 3. 旧版无 models[] 的友节点 + remote-sub2api：回退到 HTTP 查询 ───────
  const fetchTargets = [
    ...legacyFriendNodes,
    ...nodes.filter((n) => n.type === "remote-sub2api" && n.enabled && n.downUntil <= Date.now()),
  ];
  await Promise.allSettled(
    fetchTargets.map(async (node) => {
      const started = Date.now();
      try {
        const authorization = node.apiKey ? `Bearer ${node.apiKey}` : req.header("authorization");
        const result = await fetchTextWithTimeout(
          `${node.baseUrl}/v1/models`,
          { method: "GET", headers: { ...(authorization ? { Authorization: authorization } : {}) } },
          12_000,
        );
        if (result.response.ok) {
          const parsed = JSON.parse(result.text || "{}") as { data?: Array<Record<string, unknown>> };
          if (Array.isArray(parsed.data)) {
            for (const model of parsed.data) {
              const mid = String(model["id"] ?? "");
              if (mid && !seen.has(mid)) { seen.add(mid); data.push({ ...model, gateway_node: node.id }); }
            }
          }
          recordSuccess(node, result.response.status, started);
        } else {
          errors.push({ node: node.id, status: result.response.status, error: result.text });
          if (!isSub2ApiEmptyAccount(node, result.response.status, result.text))
            recordFailure(node, result.response.status, result.text || result.response.statusText, started);
        }
      } catch (error) {
        errors.push({ node: node.id, error: String(error) });
        recordFailure(node, undefined, String(error), started);
      }
    }),
  );

  res.json({ object: "list", data, total: data.length, gateway: { nodes: allNodes().map(nodeSnapshot), errors } });
});


// ── Responses API 代理（B25）──────────────────────────────────────────────────
// 调用友节点非流式 OpenAI compatible 接口（/v1/responses）
async function callOpenAINode(
  node: GatewayNode,
  body: ChatBody,
  reqAuth?: string,
): Promise<{ response: Awaited<ReturnType<typeof fetch>>; text: string }> {
  const authorization = node.apiKey ? `Bearer ${node.apiKey}` : reqAuth;
  const started = Date.now();
  const response = await fetch(`${node.baseUrl}/v1/responses`, {
    method: "POST",
    headers: {
      ...(authorization ? { Authorization: authorization } : {}),
      "Content-Type": "application/json",
      "ngrok-skip-browser-warning": "1",
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(90_000),
  });
  const text = await response.text();
  if (response.ok) {
    recordSuccess(node, response.status, started);
  } else {
    recordFailure(node, response.status, text || response.statusText, started);
    propagateFailureToSiblings(node);
  }
  return { response, text };
}

// 调用友节点流式 Responses API（SSE 直透传，含 120s 超时保护）
async function streamOpenAIResponseNode(
  node: GatewayNode,
  body: Record<string, unknown>,
  reqAuth: string | undefined,
  res: import("express").Response,
): Promise<boolean> {
  const authorization = node.apiKey ? `Bearer ${node.apiKey}` : reqAuth;
  const ctrl = new AbortController();
  const streamTimeout = setTimeout(() => ctrl.abort(), 120_000);
  const started = Date.now();
  try {
    const r = await fetch(`${node.baseUrl}/v1/responses`, {
      method: "POST",
      headers: {
        ...(authorization ? { Authorization: authorization } : {}),
        "Content-Type": "application/json",
        "ngrok-skip-browser-warning": "1",
      },
      body: JSON.stringify({ ...body, stream: true }),
      signal: ctrl.signal,
    });
    if (!r.ok || !r.body) {
      clearTimeout(streamTimeout);
      const errText = await r.text().catch(() => r.statusText);
      recordFailure(node, r.status, errText, started);
      propagateFailureToSiblings(node);
      return false;
    }
    recordSuccess(node, r.status, started);
    res.status(200);
    res.setHeader("Content-Type", r.headers.get("content-type") || "text/event-stream");
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection", "keep-alive");
    res.setHeader("X-Accel-Buffering", "no");
    res.setHeader("x-gateway-node", node.id);
    for await (const chunk of r.body) res.write(chunk);
    res.end();
    clearTimeout(streamTimeout);
    return true;
  } catch (e) {
    clearTimeout(streamTimeout);
    recordFailure(node, undefined, String(e), started);
    propagateFailureToSiblings(node);
    return false;
  }
}

// sub2api 注册账号后会调 POST {nodeBaseUrl}/v1/responses
// 友节点（有 integration）：直接代理到 Reseek /responses（不加 /v1/）
// VPS（无 integration）：经 pool 路由调 callOpenAINode / streamOpenAIResponseNode
router.post("/v1/responses", async (req, res) => {
  const isStream = (req.body as Record<string, unknown>)?.stream === true;

  // ── 友节点：有本地 OpenAI integration → 直接代理（流式 + 非流式）──
  if (OPENAI_BASE_URL && OPENAI_API_KEY) {
    const headers: Record<string, string> = {
      Authorization: `Bearer ${OPENAI_API_KEY}`,
      "Content-Type": "application/json",
    };
    const oaiBeta = req.headers["openai-beta"];
    if (oaiBeta) headers["OpenAI-Beta"] = String(oaiBeta);
    try {
      const ctrl = new AbortController();
      const tout = setTimeout(() => ctrl.abort(), 120_000);
      const r = await fetch(`${OPENAI_BASE_URL}/responses`, {
        method: "POST",
        headers,
        body: JSON.stringify(req.body),
        signal: ctrl.signal,
      });
      clearTimeout(tout);
      if (isStream && r.ok && r.body) {
        res.status(200);
        res.setHeader("Content-Type", r.headers.get("content-type") || "text/event-stream");
        res.setHeader("Cache-Control", "no-cache");
        res.setHeader("Connection", "keep-alive");
        res.setHeader("X-Accel-Buffering", "no");
        res.setHeader("x-gateway-node", "local-integration");
        for await (const chunk of r.body) res.write(chunk);
        res.end();
      } else {
        const text = await r.text();
        res.status(r.status);
        res.setHeader("content-type", r.headers.get("content-type") || "application/json");
        res.setHeader("x-gateway-node", "local-integration");
        res.send(text);
      }
    } catch (e) {
      if (!res.headersSent)
        res.status(502).json({ error: { message: "responses proxy failed", detail: String(e) } });
      else res.end();
    }
    return;
  }

  // ── VPS / 无 integration：通过 pool 路由 friend-openai 节点 ──
  const body = req.body as ChatBody;
  const candidates = orderedCandidates(typeof body.model === "string" ? body.model : "")
    .filter((n) => n.type === "friend-openai");
  const errors: Array<Record<string, unknown>> = [];

  if (isStream) {
    // 流式：SSE 直透传，成功即返回
    const reqAuth = req.header("authorization");
    for (const node of candidates) {
      if (!node.enabled || node.downUntil > Date.now()) {
        errors.push({ node: node.id, error: "[跳过]" });
        continue;
      }
      const ok = await streamOpenAIResponseNode(node, body as Record<string, unknown>, reqAuth, res);
      if (ok) return;
      errors.push({ node: node.id, error: node.lastError ?? "stream failed" });
    }
    if (!res.headersSent)
      res.status(503).json({ error: { message: "所有节点不可用", type: "gateway_unavailable", details: errors } });
    return;
  }

  // 非流式：等待完整响应
  const nonStreamAuth = req.header("authorization");
  for (const node of candidates) {
    if (!node.enabled || node.downUntil > Date.now()) {
      errors.push({ node: node.id, error: "[跳过]" });
      continue;
    }
    try {
      const result = await callOpenAINode(node, body, nonStreamAuth);
      if (result.response.ok) {
        res.status(result.response.status);
        res.setHeader("x-gateway-node", node.id);
        res.setHeader("content-type", result.response.headers.get("content-type") || "application/json");
        res.send(result.text);
        return;
      }
      errors.push({ node: node.id, status: result.response.status, error: result.text?.slice(0, 200) });
    } catch (error) {
      recordFailure(node, undefined, String(error), Date.now());
      propagateFailureToSiblings(node);
      errors.push({ node: node.id, error: String(error) });
    }
  }
  res.status(503).json({ error: { message: "所有节点不可用", type: "gateway_unavailable", details: errors } });
});

// ── 聊天补全 ──────────────────────────────────────────────────────────────────
router.post("/v1/chat/completions", async (req, res) => {
  const body = req.body as ChatBody;
  if (!Array.isArray(body.messages)) {
    res.status(400).json({ error: { message: "messages 不能为空", type: "invalid_request_error" } });
    return;
  }
  // B12 修复：循环检测 - 若请求来自另一个网关节点（hop 请求），禁止再路由到 friend-openai 节点，防止 VPS 通过自身 ngrok 回环
  const isHopRequest = Boolean(req.header("x-gateway-hop"));
  const candidates = orderedCandidates(typeof body.model === "string" ? body.model : "")
    .filter((n) => !isHopRequest || n.type !== "friend-openai");
  const errors: Array<Record<string, unknown>> = [];

  if (body.stream) {
    for (const node of candidates) {
      // B10 修复：兄弟传播后节点可能在循环进行中被标记为 down，必须重新检查
      if (!node.enabled || node.downUntil > Date.now()) {
        errors.push({ node: node.id, status: node.lastStatus, error: node.lastError || "[传播跳过]" });
        continue;
      }
      try {
        const done = await streamNode(node, req, res, body);
        if (done) return;
        errors.push({ node: node.id, status: node.lastStatus, error: node.lastError });
      } catch (error) {
        errors.push({ node: node.id, error: String(error) });
      }
    }
    if (!res.headersSent)
      res.status(503).json({ error: { message: "所有网关节点都暂时不可用", type: "gateway_unavailable", details: errors } });
    return;
  }

  for (const node of candidates) {
    // B10 修复：循环中重新检查 downUntil，跳过已被传播标记为 down 的兄弟节点
    if (!node.enabled || node.downUntil > Date.now()) {
      errors.push({ node: node.id, status: node.lastStatus, error: node.lastError || "[传播跳过]" });
      continue;
    }
    try {
      const result = await callNode(node, req, body);
      if (result.response.ok) {
        res.status(result.response.status);
        res.setHeader("x-gateway-node", node.id);
        res.setHeader("x-gateway-provider", node.type);
        res.type(result.response.headers.get("content-type") || "application/json");
        res.send(result.text);
        return;
      }
      errors.push({ node: node.id, status: result.response.status, error: result.text });
    } catch (error) {
      errors.push({ node: node.id, error: String(error) });
    }
  }
  res.status(503).json({ error: { message: "所有网关节点都暂时不可用", type: "gateway_unavailable", details: errors } });
});

// ── HTTP Exec（SSH 替代：远端控制 Reseek 工作区）────────────────────────────
// 远端服务器无法 SSH 进 Reseek（防火墙封闭），用此端点代替
// 安全：必须设置 EXEC_SECRET 环境变量，且请求头携带 X-Exec-Secret 匹配
router.post("/exec", async (req, res) => {
  if (!EXEC_SECRET) {
    res.status(403).json({ ok: false, error: "未配置 EXEC_SECRET，exec 端点已禁用" });
    return;
  }
  const reqSecret = req.header("x-exec-secret") || req.header("authorization")?.replace(/^Bearer /, "");
  if (reqSecret !== EXEC_SECRET) {
    res.status(401).json({ ok: false, error: "认证失败" });
    return;
  }
  const { cmd, timeout: timeoutSec = 30 } = req.body as { cmd?: string; timeout?: number };
  if (!cmd || typeof cmd !== "string") {
    res.status(400).json({ ok: false, error: "cmd 不能为空" });
    return;
  }
  // 拒绝明显危险命令
  const FORBIDDEN = /rm\s+-rf\s+\/|mkfs|dd\s+if=/;
  if (FORBIDDEN.test(cmd)) {
    res.status(400).json({ ok: false, error: "命令被拒绝（危险操作）" });
    return;
  }
  try {
    const { stdout, stderr } = await execAsync(cmd, {
      timeout: Math.min(Number(timeoutSec), 120) * 1000,
      maxBuffer: 512 * 1024,
    });
    res.json({ ok: true, stdout: stdout.slice(0, 50000), stderr: stderr.slice(0, 10000) });
  } catch (e: unknown) {
    const err = e as { stdout?: string; stderr?: string; message?: string };
    res.status(500).json({ ok: false, stdout: err.stdout || "", stderr: err.stderr || "", error: err.message });
  }
});

// ── 节点强制恢复 (B14 修复) ───────────────────────────────────────────────────
// 当所有节点都处于 down/credit-exhausted 状态时，可调此接口手动清除冷却
// POST /api/gateway/nodes/recover-all → 清除所有节点 downUntil（不含永久 disabled）
router.post("/nodes/recover-all", (_req, res) => {
  const recovered: string[] = [];
  for (const node of allNodes()) {
    if (!node.enabled) continue;
    if (node.downUntil > 0) {
      node.downUntil = 0;
      node.creditExhaustedAt = undefined;
      recovered.push(node.id);
    }
  }
  res.json({ ok: true, recovered, count: recovered.length, nodes: allNodes().map(nodeSnapshot) });
});

// POST /api/gateway/nodes/:id/recover → 恢复单个节点
router.post("/nodes/:id/recover", (req, res) => {
  const node = allNodes().find((n) => n.id === req.params.id);
  if (!node) {
    res.status(404).json({ ok: false, error: "节点不存在" });
    return;
  }
  node.downUntil = 0;
  node.creditExhaustedAt = undefined;
  res.json({ ok: true, node: nodeSnapshot(node) });
});

// ── 友节点自注册 (self-register) ──────────────────────────────────────────────
// 新 Reseek 工作区启动后调用远端服务器的此接口自动加入节点池
// POST /api/gateway/self-register  body: { gatewayUrl, name? }
router.post("/self-register", async (req, res) => {
  const body = req.body as SelfRegisterBody;
  const { gatewayUrl, name } = body;
  if (!gatewayUrl || !gatewayUrl.startsWith("http")) {
    res.status(400).json({ ok: false, error: "gatewayUrl 必须是 http(s) URL" });
    return;
  }
  const baseUrl = gatewayUrl.replace(/\/$/, "");
  // B13 修复：注册鉴权 - 若 EXEC_SECRET 已配置则要求请求方提供正确 token
  const regSecret = (body as Record<string, unknown>).execSecret ?? (body as Record<string, unknown>).token;
  if (EXEC_SECRET && regSecret !== EXEC_SECRET) {
    res.status(401).json({ ok: false, error: "注册密钥不正确，请提供正确的 execSecret" });
    return;
  }
  // 探测目标是否真的是 gateway
  const probe = await probeNodeUrl(baseUrl, undefined, 8000, false);
  if (!probe.ok) {
    res.status(422).json({ ok: false, error: "探测失败，URL 不可达", detail: probe.error });
    return;
  }
  // B27+: 先按 normalizeBaseUrl 查（同时处理带/不带 /api/gateway 后缀的情况），再按 name 查
  const _normUrl = (u: string) => u.trim().replace(/\/$/, "")
    .replace(/\/api\/gateway$/, "").replace(/\/api$/, "").replace(/\/$/, "");
  let existing = allNodes().find((n) => _normUrl(n.baseUrl) === _normUrl(baseUrl));
  if (!existing && name) {
    const byName = allNodes().find((n) => n.name === name);
    if (byName) {
      byName.baseUrl = baseUrl;
      byName.id = stableId("friend", baseUrl);
      if (!runtimeNodes.includes(byName)) runtimeNodes.push(byName);
      savePersistedNodes(runtimeNodes);
      existing = byName;
    }
  }
  if (existing) {
    if (name) existing.name = name;
    // 重新注册时同步更新 baseUrl（URL 可能因重启变化）和 priority
    if (baseUrl && baseUrl !== existing.baseUrl) existing.baseUrl = baseUrl;
    // 确保 type 字段正确（兼容旧持久化数据中可能缺失 type 的情况）
    if (!existing.type) existing.type = "friend-openai";
    const newPriority = (body as {priority?: number}).priority;
    if (newPriority != null) existing.priority = newPriority;
    // 更新存活时间：重置 downUntil（探测通过说明节点仍存活）
    existing.downUntil = 0;
    existing.failures = 0;
    // 同步更新 models 能力列表（有明确列表时更新，空列表时清空旧列表）
    if (Array.isArray(body.models)) existing.models = body.models;
    // 若节点来自 runtimeNodes（动态注册），持久化保存
    if (runtimeNodes.includes(existing)) savePersistedNodes(runtimeNodes);
    const credentialPush = await pushSelfRegisterCredentialsToSub2Api(body, baseUrl, name);
    res.json({ ok: true, action: "already-registered", node: nodeSnapshot(existing), credentialPush });
    return;
  }
  let hostname = baseUrl;
  try { hostname = new URL(baseUrl).hostname.split(".")[0]; } catch {}
  const id = stableId("friend", baseUrl);
  const node: GatewayNode = {
    id,
    name: name || `友节点(${hostname})`,
    type: "friend-openai",
    baseUrl,
    apiKey: undefined, // B13 修复：友节点网关不需要 apiKey，调用时用请求方的原始 auth
    model: "gpt-5-mini",
    priority: (body as {priority?: number}).priority ?? 2,
    enabled: true,
    downUntil: 0,
    successes: 0,
    failures: 0,
    source: "register",
    models: (body as {models?: string[]}).models ?? [],
  };
  runtimeNodes.push(node);
  savePersistedNodes(runtimeNodes);
  const credentialPush = await pushSelfRegisterCredentialsToSub2Api(body, baseUrl, name);
  res.json({ ok: true, action: "registered", nodeId: id, node: nodeSnapshot(node), latencyMs: probe.latencyMs, credentialPush });
});

// ── 对等节点列表 (peers) ───────────────────────────────────────────────────────
// 返回所有 friend-openai 和 runtime 节点的公开信息，用于友节点间互相发现
router.get("/peers", (_req, res) => {
  const peers = allNodes()
    .filter((n) => n.type === "friend-openai" || n.source === "env" || n.source === "runtime")
    .map((n) => ({
      id: n.id,
      name: n.name,
      baseUrl: n.baseUrl,
      model: n.model,
      status: n.enabled && n.downUntil <= Date.now() ? "ready" : "down",
      latencyMs: n.lastLatencyMs,
    }));
  res.json({ peers, count: peers.length });
});

// ── 对等节点中继 (relay) ───────────────────────────────────────────────────────
// 任何一个友节点都可以把请求中继到另一个已注册的友节点
// POST /api/gateway/relay/:nodeId   body: { path, method, headers, body }
// 或者 POST /api/gateway/relay  body: { targetUrl, path, method, headers?, body? }
router.post(["/relay/:nodeId", "/relay"], async (req, res) => {
  const nodeId = (req.params as { nodeId?: string }).nodeId;
  const { targetUrl, path: targetPath = "/v1/chat/completions", method = "POST",
    headers: extraHeaders = {}, body: relayBody } = req.body as {
    targetUrl?: string; path?: string; method?: string;
    headers?: Record<string, string>; body?: unknown;
  };

  let targetBaseUrl = targetUrl?.replace(/\/$/, "");
  if (nodeId) {
    const node = allNodes().find((n) => n.id === nodeId);
    if (!node) {
      res.status(404).json({ ok: false, error: "目标节点不存在", nodeId });
      return;
    }
    targetBaseUrl = node.baseUrl;
  }
  if (!targetBaseUrl) {
    res.status(400).json({ ok: false, error: "需要 nodeId 或 targetUrl" });
    return;
  }

  const url = `${targetBaseUrl}${targetPath.startsWith("/") ? targetPath : "/" + targetPath}`;
  const started = Date.now();
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 60_000);
    const headers = inheritedAuthHeaders(req, { "Content-Type": "application/json", ...extraHeaders });
    const r = await fetch(url, {
      method: method.toUpperCase(),
      headers,
      ...(relayBody ? { body: JSON.stringify(relayBody) } : {}),
      signal: ctrl.signal,
    });
    clearTimeout(timer);
    const text = await r.text();
    res.status(r.status);
    res.setHeader("x-relay-target", targetBaseUrl);
    res.setHeader("x-relay-latency-ms", String(Date.now() - started));
    res.setHeader("content-type", r.headers.get("content-type") || "application/json");
    res.send(text);
  } catch (e) {
    res.status(502).json({ ok: false, error: "中继失败", target: url, detail: String(e) });
  }
});

// ═══ 周期性持久化自动存盘（每 5 分钟）══════════════════════════════════════════
setInterval(() => {
  if (runtimeNodes.length > 0) savePersistedNodes(runtimeNodes);
}, 30_000);

// ═══ B21：后台周期探测 friend-openai / register 节点 ══════════════════════════
// 每 5 分钟对所有 register/runtime source 的 friend-openai 节点做一次健康探测
// 探测失败时用指数退避：1m → 5m → 30m → 2h → UTC 凌晨
const PROBE_INTERVAL_MS = 30_000;
const probeFailCounts = new Map<string, number>();

function probeBackoffMs(failCount: number): number {
  const steps = [60_000, 5 * 60_000, 30 * 60_000, 2 * 60 * 60_000];
  if (failCount - 1 < steps.length) return steps[failCount - 1]!;
  return msUntilUtcMidnight();
}

async function backgroundProbeLoop(): Promise<void> {
  const targets = allNodes().filter(
    (n) => n.enabled && (n.source === "register" || n.source === "runtime") && n.type === "friend-openai",
  );
  for (const node of targets) {
    try {
      const result = await probeNodeUrl(node.baseUrl, node.apiKey, 12_000);
      if (result.ok) {
        probeFailCounts.delete(node.id);
        node.downUntil = 0;   // 探测通过 → 立即解除惩罚，无论惩罚是否已到期
        node.failures = 0;
        node.lastLatencyMs = result.latencyMs;  // Bug7: 同步探测延迟
        const modelsChanged = result.models && result.models.length > 0 && JSON.stringify(result.models) !== JSON.stringify(node.models);
        if (result.models && result.models.length > 0) node.models = result.models;
        if (modelsChanged) savePersistedNodes(runtimeNodes);  // Bug8: 持久化 models 变更
      } else {
        const fails = (probeFailCounts.get(node.id) ?? 0) + 1;
        probeFailCounts.set(node.id, fails);
        node.downUntil = Date.now() + probeBackoffMs(fails);
        node.lastError = `B21探测失败(${fails}次): ${result.error ?? "无响应"}`;
      }
    } catch { /* 单个节点探测异常不影响其他 */ }
  }
}

setInterval(() => { void backgroundProbeLoop(); }, PROBE_INTERVAL_MS);
setTimeout(() => { void backgroundProbeLoop(); }, 5_000); // 启动 5s 后先探测一次


// B26: 启动时确认 sub2api channel+group 绑定
setTimeout(() => { void ensureSub2ApiSetup(); }, 10_000);


export default router;

