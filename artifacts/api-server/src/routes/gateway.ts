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
  source: "built-in" | "env" | "runtime";
  lastStatus?: number;
  lastError?: string;
  lastLatencyMs?: number;
  lastUsedAt?: string;
  creditExhaustedAt?: number;
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
  content?: Array<{ type?: string; text?: string }>;
};

type GeminiResult = {
  candidates?: Array<{ content?: { parts?: Array<{ text?: string }> }; finishReason?: string }>;
};

// ═══ 配置 ═══════════════════════════════════════════════════════════════════

const router = Router();
// REMOTE_GATEWAY_BASE_URL: 远端 Sub2API 地址。
// 留空 = 禁用 remote-sub2api 节点（Replit 子节点模式，不需要再路由回 Sub2API）
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
const OPENAI_MODELS = ["gpt-5.2", "gpt-5-mini", "gpt-5-nano", "o4-mini"];
const OPENAI_REASONING_MODELS = ["o4-mini", "o3", "o3-mini", "o1", "o1-mini"]; // 只有这些支持 reasoning 参数
const ANTHROPIC_MODELS = ["claude-sonnet-4-6", "claude-haiku-4-5"];
const GEMINI_MODELS = ["gemini-2.5-flash", "gemini-3-flash-preview"];

// B15 修复：额外凭据组——聚合多套 Replit AI integration / 第三方 OpenAI 兼容端点
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
        source: "runtime" as const,
      }));
  } catch {
    return [];
  }
}

function savePersistedNodes(nodes: GatewayNode[]) {
  try {
    if (!existsSync(DATA_DIR)) mkdirSync(DATA_DIR, { recursive: true });
    writeFileSync(NODES_FILE, JSON.stringify(nodes.map((n) => ({
      id: n.id, name: n.name, type: n.type, baseUrl: n.baseUrl,
      apiKey: n.apiKey, model: n.model, priority: n.priority, enabled: n.enabled,
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
      name: node.name || `Friend 节点 ${index + 1}`,
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
    name: `Replit 子节点 #${i + 1}`,
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
      name: `Reseek OpenAI 子节点 ${index + 1}`,
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
      name: `Reseek Anthropic 子节点 ${index + 1}`,
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
      name: `Reseek Gemini 子节点 ${index + 1}`,
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
    baseUrl: node.type.startsWith("reseek-") ? "Reseek AI integration" : node.baseUrl,
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
  if (!model) return true;
  // B16 Fix: strip provider prefix e.g. "anthropic/claude-sonnet-4-6" -> "claude-sonnet-4-6"
  const bare = model.includes("/") ? model.split("/").pop()! : model;
  if (node.model === model || node.model === bare) return true;
  if (node.type === "reseek-openai" && (OPENAI_MODELS.includes(model) || OPENAI_MODELS.includes(bare))) return true;
  if (node.type === "reseek-anthropic" && (ANTHROPIC_MODELS.includes(model) || ANTHROPIC_MODELS.includes(bare))) return true;
  if (node.type === "reseek-gemini" && (GEMINI_MODELS.includes(model) || GEMINI_MODELS.includes(bare))) return true;
  return node.type === "remote-sub2api" || node.type === "friend-openai";
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
  const model = typeof body.model === "string" ? body.model : "";
  return allowed.includes(model) ? model : node.model;
}

function openAiCompatibleBody(body: ChatBody, node: GatewayNode) {
  const next: ChatBody = { ...body };
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

function extractAnthropicText(data: AnthropicResult) {
  return (data.content ?? [])
    .filter((content) => content.type === "text" && content.text)
    .map((content) => content.text)
    .join("");
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
    },
    body: JSON.stringify(requestBody),
  });
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
    },
    body: JSON.stringify(payload),
  });
  if (!result.response.ok) return result;
  const parsed = JSON.parse(result.text || "{}") as ResponsesApiResult;
  return { response: result.response, text: chatCompletionJson(node, model, extractResponsesText(parsed), parsed.id) };
}

async function callAnthropicNode(node: GatewayNode, body: ChatBody) {
  const system = (body.messages ?? [])
    .filter((message) => message["role"] === "system")
    .map(messageText)
    .join("\n\n") || undefined;
  const messages = (body.messages ?? [])
    .filter((message) => message["role"] !== "system")
    .map((message) => ({
      role: message["role"] === "assistant" ? "assistant" : "user",
      content: messageText(message),
    }));
  const model = requestedModel(body, node, ANTHROPIC_MODELS);
  const result = await fetchTextWithTimeout(`${node.baseUrl}/messages`, {
    method: "POST",
    headers: {
      "x-api-key": String(node.apiKey),
      "anthropic-version": "2023-06-01",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model,
      max_tokens: maxTokens(body),
      ...(system ? { system } : {}),
      messages,
    }),
  });
  if (!result.response.ok) return result;
  const parsed = JSON.parse(result.text || "{}") as AnthropicResult;
  return { response: result.response, text: chatCompletionJson(node, model, extractAnthropicText(parsed), parsed.id) };
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
    },
    body: JSON.stringify({
      ...(systemText ? { systemInstruction: { parts: [{ text: systemText }] } } : {}),
      contents,
      generationConfig: { maxOutputTokens: maxTokens(body) },
    }),
  });
  if (!result.response.ok) return result;
  const parsed = JSON.parse(result.text || "{}") as GeminiResult;
  return { response: result.response, text: chatCompletionJson(node, model, extractGeminiText(parsed)) };
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
        },
        body: JSON.stringify({ ...requestBody, stream: true }),
        signal: ctrl.signal,
      });
      if (!response.ok || !response.body) {
        const text = await response.text();
        recordFailure(node, response.status, text || response.statusText, started);
        propagateFailureToSiblings(node);
        clearTimeout(streamTimeout);
        return false;
      }
      recordSuccess(node, response.status, started);
      res.status(200);
      res.setHeader("Content-Type", response.headers.get("content-type") || "text/event-stream");
      res.setHeader("Cache-Control", "no-cache");
      res.setHeader("Connection", "keep-alive");
      res.setHeader("x-gateway-node", node.id);
      for await (const chunk of response.body) res.write(chunk);
      res.end();
      clearTimeout(streamTimeout);
      return true;
    } catch (error) {
      // B9 修复：fetch() 本身抛异常（网络断开、超时 abort）时补充 recordFailure
      clearTimeout(streamTimeout);
      recordFailure(node, undefined, String(error), started);
      return false;
    }
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
  const sseId = `chatcmpl-${Date.now()}`;
  const sseModel = typeof body.model === "string" ? body.model : node.model;
  const sseCreated = Math.floor(Date.now() / 1000);
  res.write(`data: ${JSON.stringify({ id: sseId, object: "chat.completion.chunk", created: sseCreated, model: sseModel, choices: [{ index: 0, delta: { role: "assistant" }, finish_reason: null }] })}\n\n`);
  if (content)
    res.write(`data: ${JSON.stringify({ id: sseId, object: "chat.completion.chunk", created: sseCreated, model: sseModel, choices: [{ index: 0, delta: { content }, finish_reason: null }] })}\n\n`);
  res.write(`data: ${JSON.stringify({ id: sseId, object: "chat.completion.chunk", created: sseCreated, model: sseModel, choices: [{ index: 0, delta: {}, finish_reason: "stop" }] })}\n\n`);
  res.write("data: [DONE]\n\n");
  res.end();
  return true;
}

// ═══ 节点探测工具 ════════════════════════════════════════════════════════════

async function probeNodeUrl(rawUrl: string, apiKey?: string, timeoutMs = 10_000) {
  const baseUrl = rawUrl.replace(/\/$/, "");
  const started = Date.now();
  for (const path of ["/health", "/v1/models", "/nodes", "/stats"]) {
    try {
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (apiKey) headers["Authorization"] = `Bearer ${apiKey}`;
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), timeoutMs);
      const res = await fetch(`${baseUrl}${path}`, { headers, signal: ctrl.signal });
      clearTimeout(timer);
      if (res.ok) {
        const data = await res.json().catch(() => ({})) as { data?: Array<{ id: string }>; success?: boolean };
        const models = Array.isArray(data.data) ? data.data.map((m) => m.id).slice(0, 5) : [];
        return { ok: true, latencyMs: Date.now() - started, models };
      }
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
      name: item.name || `Friend 节点 ${runtimeNodes.length + 1}`,
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
        const id = stableId("friend", `${baseUrl}-${model || ""}-${runtimeNodes.length}`);
        try {
          const hostname = new URL(baseUrl).hostname.split(".")[0];
          const node: GatewayNode = {
            id, name: `Replit(${hostname})`, type: "friend-openai",
            baseUrl, apiKey, model: model || "gpt-5-mini",
            priority: Number(priority) || 3, enabled: true,
            downUntil: 0, successes: 0, failures: 0, source: "runtime",
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
  // B16 Fix: persist newly registered nodes so they survive server restarts
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
  const data: Array<Record<string, unknown>> = [];
  const errors: Array<Record<string, unknown>> = [];
  const nodes = allNodes();
  // B12 修复：只列出有至少一个 enabled 节点的模型（integration 未配置时不暴露该模型）
  const hasOpenAI = nodes.some((n) => n.type === "reseek-openai" && n.enabled);
  const hasAnthropic = nodes.some((n) => n.type === "reseek-anthropic" && n.enabled);
  const hasGemini = nodes.some((n) => n.type === "reseek-gemini" && n.enabled);
  if (hasOpenAI)
    for (const model of OPENAI_MODELS)
      data.push({ id: model, object: "model", created: 0, owned_by: "reseek-openai", gateway_node: "reseek-openai-pool" });
  if (hasAnthropic)
    for (const model of ANTHROPIC_MODELS)
      data.push({ id: model, object: "model", created: 0, owned_by: "reseek-anthropic", gateway_node: "reseek-anthropic-pool" });
  if (hasGemini)
    for (const model of GEMINI_MODELS)
      data.push({ id: model, object: "model", created: 0, owned_by: "reseek-gemini", gateway_node: "reseek-gemini-pool" });
  for (const node of allNodes().filter((n) => n.type === "remote-sub2api" || n.type === "friend-openai")) {
    if (!node.enabled || node.downUntil > Date.now()) continue;
    const started = Date.now();
    try {
      const authorization = node.apiKey ? `Bearer ${node.apiKey}` : req.header("authorization");
      const result = await fetchTextWithTimeout(`${node.baseUrl}/v1/models`, { method: "GET", headers: { ...(authorization ? { Authorization: authorization } : {}) } }, 12_000);
      if (result.response.ok) {
        const parsed = JSON.parse(result.text || "{}") as { data?: Array<Record<string, unknown>> };
        if (Array.isArray(parsed.data)) data.push(...parsed.data.map((model) => ({ ...model, gateway_node: node.id })));
        recordSuccess(node, result.response.status, started);
      } else {
        errors.push({ node: node.id, status: result.response.status, error: result.text });
        recordFailure(node, result.response.status, result.text || result.response.statusText, started);
      }
    } catch (error) {
      errors.push({ node: node.id, error: String(error) });
      recordFailure(node, undefined, String(error), started);
    }
  }
  res.json({ object: "list", data, gateway: { nodes: allNodes().map(nodeSnapshot), errors } });
});

// ── 聊天补全 ──────────────────────────────────────────────────────────────────
router.post("/v1/chat/completions", async (req, res) => {
  const body = req.body as ChatBody;
  if (!Array.isArray(body.messages)) {
    res.status(400).json({ error: { message: "messages 不能为空", type: "invalid_request_error" } });
    return;
  }
  const candidates = orderedCandidates(typeof body.model === "string" ? body.model : "");
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

// ── HTTP Exec（SSH 替代：远端控制 Replit 工作区）────────────────────────────
// 远端服务器无法 SSH 进 Replit（防火墙封闭），用此端点代替
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

// ── 子节点自注册 (self-register) ──────────────────────────────────────────────
// 新 Replit 工作区启动后调用远端服务器的此接口自动加入节点池
// POST /api/gateway/self-register  body: { gatewayUrl, name? }
router.post("/self-register", async (req, res) => {
  const { gatewayUrl, name } = req.body as {
    gatewayUrl?: string; name?: string; execSecret?: string; // execSecret 保留字段但不再存为 apiKey
  };
  if (!gatewayUrl || !gatewayUrl.startsWith("http")) {
    res.status(400).json({ ok: false, error: "gatewayUrl 必须是 http(s) URL" });
    return;
  }
  const baseUrl = gatewayUrl.replace(/\/$/, "");
  // 探测目标是否真的是 gateway
  const probe = await probeNodeUrl(baseUrl, undefined, 8000);
  if (!probe.ok) {
    res.status(422).json({ ok: false, error: "探测失败，URL 不可达", detail: probe.error });
    return;
  }
  // 已存在则更新名称，否则新建
  const existing = allNodes().find((n) => n.baseUrl === baseUrl);
  if (existing) {
    if (name) existing.name = name;
    res.json({ ok: true, action: "already-registered", node: nodeSnapshot(existing) });
    return;
  }
  let hostname = baseUrl;
  try { hostname = new URL(baseUrl).hostname.split(".")[0]; } catch {}
  const id = stableId("friend", baseUrl);
  const node: GatewayNode = {
    id,
    name: name || `子节点(${hostname})`,
    type: "friend-openai",
    baseUrl,
    apiKey: undefined, // B13 修复：子节点网关不需要 apiKey，调用时用请求方的原始 auth
    model: "gpt-5-mini",
    priority: 3,
    enabled: true,
    downUntil: 0,
    successes: 0,
    failures: 0,
    source: "runtime",
  };
  runtimeNodes.push(node);
  savePersistedNodes(runtimeNodes);
  res.json({ ok: true, action: "registered", nodeId: id, node: nodeSnapshot(node), latencyMs: probe.latencyMs });
});

// ── 对等节点列表 (peers) ───────────────────────────────────────────────────────
// 返回所有 friend-openai 和 runtime 节点的公开信息，用于子节点间互相发现
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
// 任何一个子节点都可以把请求中继到另一个已注册的子节点
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
    // B16 Fix: forward original request Authorization header unless caller overrides it
    const reqAuth = req.header("authorization");
    const relayHeaders: Record<string, string> = { "Content-Type": "application/json" };
    if (reqAuth && !extraHeaders["Authorization"] && !extraHeaders["authorization"]) {
      relayHeaders["Authorization"] = reqAuth;
    }
    Object.assign(relayHeaders, extraHeaders);
    const r = await fetch(url, {
      method: method.toUpperCase(),
      headers: relayHeaders,
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

export default router;

