import { Router, type Request, type Response } from "express";
import { createHash } from "crypto";

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
const REMOTE_GATEWAY_BASE_URL = (process.env["REMOTE_GATEWAY_BASE_URL"] || "http://45.205.27.69:9090").replace(/\/$/, "");
const OPENAI_BASE_URL = (process.env["AI_INTEGRATIONS_OPENAI_BASE_URL"] || "").replace(/\/$/, "");
const OPENAI_API_KEY = process.env["AI_INTEGRATIONS_OPENAI_API_KEY"] || "";
const ANTHROPIC_BASE_URL = (process.env["AI_INTEGRATIONS_ANTHROPIC_BASE_URL"] || "").replace(/\/$/, "");
const ANTHROPIC_API_KEY = process.env["AI_INTEGRATIONS_ANTHROPIC_API_KEY"] || "";
const GEMINI_BASE_URL = (process.env["AI_INTEGRATIONS_GEMINI_BASE_URL"] || "").replace(/\/$/, "");
const GEMINI_API_KEY = process.env["AI_INTEGRATIONS_GEMINI_API_KEY"] || "";
const RESEEK_OPENAI_NODE_COUNT = Math.min(24, Math.max(1, Number(process.env["RESEEK_AI_NODE_COUNT"] || 6)));
const NODE_DOWN_MS = Math.max(30_000, Number(process.env["GATEWAY_NODE_DOWN_MS"] || 180_000));
const OPENAI_MODELS = ["gpt-5.2", "gpt-5-mini", "gpt-5-nano", "o4-mini"];
const ANTHROPIC_MODELS = ["claude-sonnet-4-6", "claude-haiku-4-5"];
const GEMINI_MODELS = ["gemini-2.5-flash", "gemini-3-flash-preview"];

// 额度耗尽识别
const CREDIT_PATTERNS = [
  /out of credits/i, /insufficient_quota/i, /billing/i, /payment required/i,
  /exceeded.*quota/i, /exceeded.*limit/i, /quota exceeded/i, /no available OpenAI accounts/i,
  /credit balance/i, /usage limit/i,
];
function isCreditExhausted(text: string) {
  return CREDIT_PATTERNS.some((p) => p.test(text));
}

// 计算到下个 UTC 00:00 的剩余毫秒（至少 60s，防止边界情况）
function msUntilUtcMidnight(): number {
  const now = Date.now();
  const midnight = new Date(now);
  midnight.setUTCDate(midnight.getUTCDate() + 1);
  midnight.setUTCHours(0, 0, 0, 0);
  return Math.max(60_000, midnight.getTime() - now);
}

const runtimeNodes: GatewayNode[] = [];
let cursor = 0;

function stableId(prefix: string, value: string) {
  return `${prefix}-${createHash("sha1").update(value).digest("hex").slice(0, 10)}`;
}

function parseFriendNodesFromEnv() {
  const raw = process.env["GATEWAY_FRIEND_NODES"];
  if (!raw) return [] as GatewayNode[];
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
    return [] as GatewayNode[];
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
      baseUrl: REMOTE_GATEWAY_BASE_URL,
      model: "upstream",
      priority: 1,
      enabled: true,
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
    // 额度耗尽：冷却到下个 UTC 00:00（而非固定 23h）
    // 注意：到期后自动恢复参与调度，无需手动批量启用
    node.downUntil = Date.now() + msUntilUtcMidnight();
    node.creditExhaustedAt = Date.now();
  } else if (
    /no available OpenAI accounts|temporarily unavailable|invalid_endpoint|rate limit|quota|overloaded/i.test(error)
    || status === 503 || status === 429
  ) {
    node.downUntil = Date.now() + NODE_DOWN_MS;
  }
}

function nodeMatchesRequestedModel(node: GatewayNode, model: string) {
  if (!model) return true;
  if (node.model === model) return true;
  if (node.type === "reseek-openai" && OPENAI_MODELS.includes(model)) return true;
  if (node.type === "reseek-anthropic" && ANTHROPIC_MODELS.includes(model)) return true;
  if (node.type === "reseek-gemini" && GEMINI_MODELS.includes(model)) return true;
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
  const result = await fetchTextWithTimeout(`${node.baseUrl}/responses`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${node.apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model,
      input: responsesInput(body),
      max_output_tokens: outputTokens,
      reasoning: { effort: "minimal" },
      text: { verbosity: "low" },
    }),
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
    if (result.response.ok) recordSuccess(node, result.response.status, started);
    else recordFailure(node, result.response.status, result.text || result.response.statusText, started);
    return result;
  } catch (error) {
    recordFailure(node, undefined, String(error), started);
    throw error;
  }
}

async function streamNode(node: GatewayNode, req: Request, res: Response, body: ChatBody) {
  if (node.type === "remote-sub2api" || node.type === "friend-openai") {
    const started = Date.now();
    const requestBody = openAiCompatibleBody(body, node);
    const authorization = node.apiKey ? `Bearer ${node.apiKey}` : req.header("authorization");
    const response = await fetch(`${node.baseUrl}/v1/chat/completions`, {
      method: "POST",
      headers: {
        ...(authorization ? { Authorization: authorization } : {}),
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ...requestBody, stream: true }),
    });
    if (!response.ok || !response.body) {
      const text = await response.text();
      recordFailure(node, response.status, text || response.statusText, started);
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
    return true;
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
  res.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: { role: "assistant" }, finish_reason: null }] })}\n\n`);
  if (content)
    res.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: { content }, finish_reason: null }] })}\n\n`);
  res.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: {}, finish_reason: "stop" }] })}\n\n`);
  res.write("data: [DONE]\n\n");
  res.end();
  return true;
}

// ═══ 节点探测工具 ════════════════════════════════════════════════════════════

async function probeNodeUrl(rawUrl: string, apiKey?: string, timeoutMs = 10_000): Promise<{
  ok: boolean; latencyMs: number; models?: string[]; error?: string;
}> {
  const baseUrl = rawUrl.replace(/\/$/, "");
  const started = Date.now();
  try {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (apiKey) headers["Authorization"] = `Bearer ${apiKey}`;

    // 先试 /health，再试 /v1/models
    for (const path of ["/health", "/v1/models", "/nodes"]) {
      try {
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
  } catch (e) {
    return { ok: false, latencyMs: Date.now() - started, error: String(e) };
  }
}

// ═══ HTTP 路由 ════════════════════════════════════════════════════════════════

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

// ── 添加节点（单个或批量）────────────────────────────────────────────────────
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
    if (allNodes().some((n) => n.baseUrl === baseUrl)) continue; // 去重
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
  res.json({ success: added.length > 0, added, nodes: allNodes().map(nodeSnapshot) });
});

// ── 删除节点 ─────────────────────────────────────────────────────────────────
router.delete("/nodes/:id", (req, res) => {
  const index = runtimeNodes.findIndex((node) => node.id === req.params.id);
  if (index < 0) {
    res.status(404).json({ success: false, error: "只能删除运行时添加的 friend 节点" });
    return;
  }
  const removed = runtimeNodes.splice(index, 1).map(nodeSnapshot);
  res.json({ success: true, removed, nodes: allNodes().map(nodeSnapshot) });
});

// ── 修改节点（启用/禁用/更新）────────────────────────────────────────────────
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
    if (enabled) { node.downUntil = 0; node.creditExhaustedAt = undefined; } // 手动启用时清除冷却
  }
  if (typeof priority === "number") node.priority = priority;
  if (apiKey !== undefined) node.apiKey = apiKey;
  if (model) node.model = model;
  if (name) node.name = name;
  res.json({ success: true, node: nodeSnapshot(node) });
});

// ── 单节点探测（不注册）──────────────────────────────────────────────────────
router.post("/nodes/probe", async (req, res) => {
  const { baseUrl, apiKey } = req.body as { baseUrl?: string; apiKey?: string };
  if (!baseUrl) {
    res.status(400).json({ success: false, error: "baseUrl 必填" });
    return;
  }
  const result = await probeNodeUrl(baseUrl, apiKey);
  res.json({ success: result.ok, baseUrl, ...result });
});

// ── 批量探测 + 注册（一键接入多个 Replit 子节点）────────────────────────────
// POST /api/gateway/nodes/batch-probe
// Body: { urls: ["https://...", ...], apiKey?: "", model?: "", autoRegister?: true }
router.post("/nodes/batch-probe", async (req, res) => {
  const { urls, apiKey, model, autoRegister = true, priority = 3 } = req.body as {
    urls?: string[]; apiKey?: string; model?: string; autoRegister?: boolean; priority?: number;
  };
  if (!Array.isArray(urls) || urls.length === 0) {
    res.status(400).json({ success: false, error: "urls 数组不能为空" });
    return;
  }
  const limited = urls.slice(0, 50);

  const results = await Promise.allSettled(
    limited.map(async (rawUrl) => {
      const baseUrl = rawUrl.trim().replace(/\/$/, "");
      const probe = await probeNodeUrl(baseUrl, apiKey);
      let registered = false;
      let nodeId: string | undefined;
      if (probe.ok && autoRegister && !allNodes().some((n) => n.baseUrl === baseUrl)) {
        const id = stableId("friend", `${baseUrl}-${model || ""}-${runtimeNodes.length}`);
        const node: GatewayNode = {
          id,
          name: `Replit 子节点 (${new URL(baseUrl).hostname.split(".")[0]})`,
          type: "friend-openai",
          baseUrl,
          apiKey,
          model: model || "gpt-5-mini",
          priority: Number(priority) || 3,
          enabled: true,
          downUntil: 0,
          successes: 0,
          failures: 0,
          source: "runtime",
        };
        runtimeNodes.push(node);
        registered = true;
        nodeId = id;
      } else if (allNodes().some((n) => n.baseUrl === baseUrl)) {
        registered = true;
        nodeId = allNodes().find((n) => n.baseUrl === baseUrl)?.id;
      }
      return { url: baseUrl, ok: probe.ok, latencyMs: probe.latencyMs, models: probe.models, error: probe.error, registered, nodeId };
    }),
  );

  const rows = results.map((r) => (r.status === "fulfilled" ? r.value : { url: "", ok: false, error: String((r as PromiseRejectedResult).reason), registered: false }));
  const succeeded = rows.filter((r) => r.ok).length;
  const registered = rows.filter((r) => r.registered).length;

  res.json({
    success: succeeded > 0,
    summary: { total: rows.length, succeeded, failed: rows.length - succeeded, registered },
    rows,
    nodes: allNodes().map(nodeSnapshot),
  });
});

// ── 测试单个节点（简单 chat）──────────────────────────────────────────────────
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
  for (const model of OPENAI_MODELS)
    data.push({ id: model, object: "model", created: 0, owned_by: "reseek-openai", gateway_node: "reseek-openai-pool" });
  for (const model of ANTHROPIC_MODELS)
    data.push({ id: model, object: "model", created: 0, owned_by: "reseek-anthropic", gateway_node: "reseek-anthropic-pool" });
  for (const model of GEMINI_MODELS)
    data.push({ id: model, object: "model", created: 0, owned_by: "reseek-gemini", gateway_node: "reseek-gemini-pool" });
  for (const node of allNodes().filter((n) => n.type === "remote-sub2api" || n.type === "friend-openai")) {
    if (!node.enabled || node.downUntil > Date.now()) continue;
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
        if (Array.isArray(parsed.data))
          data.push(...parsed.data.map((model) => ({ ...model, gateway_node: node.id })));
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
      try {
        const done = await streamNode(node, req, res, body);
        if (done) return;
        errors.push({ node: node.id, status: node.lastStatus, error: node.lastError });
      } catch (error) {
        errors.push({ node: node.id, error: String(error) });
      }
    }
    if (!res.headersSent)
      res.status(503).json({
        error: { message: "所有网关节点都暂时不可用", type: "gateway_unavailable", details: errors },
      });
    return;
  }

  for (const node of candidates) {
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
  res.status(503).json({
    error: { message: "所有网关节点都暂时不可用", type: "gateway_unavailable", details: errors },
  });
});

export default router;
