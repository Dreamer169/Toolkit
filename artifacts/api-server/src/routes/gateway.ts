import { Router, type Request, type Response } from "express";

type GatewayNode = {
  id: string;
  name: string;
  type: "remote-sub2api" | "reseek-ai";
  baseUrl: string;
  model: string;
  priority: number;
  enabled: boolean;
  downUntil: number;
  successes: number;
  failures: number;
  lastStatus?: number;
  lastError?: string;
  lastLatencyMs?: number;
  lastUsedAt?: string;
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
  output?: Array<{
    type?: string;
    role?: string;
    content?: Array<{ type?: string; text?: string }>;
  }>;
  error?: { message?: string };
};

const router = Router();
const REMOTE_GATEWAY_BASE_URL = (process.env["REMOTE_GATEWAY_BASE_URL"] || "http://45.205.27.69:9090").replace(/\/$/, "");
const RESEEK_AI_BASE_URL = (process.env["AI_INTEGRATIONS_OPENAI_BASE_URL"] || "").replace(/\/$/, "");
const RESEEK_AI_API_KEY = process.env["AI_INTEGRATIONS_OPENAI_API_KEY"] || "";
const RESEEK_NODE_COUNT = Math.min(12, Math.max(1, Number(process.env["RESEEK_AI_NODE_COUNT"] || 4)));
const NODE_DOWN_MS = Math.max(30_000, Number(process.env["GATEWAY_NODE_DOWN_MS"] || 180_000));
const LOCAL_MODELS = ["gpt-5.2", "gpt-5-mini", "gpt-5-nano", "o4-mini"];

const nodes: GatewayNode[] = [
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
  },
  ...Array.from({ length: RESEEK_NODE_COUNT }, (_, index) => ({
    id: `reseek-ai-${index + 1}`,
    name: `Reseek AI 子节点 ${index + 1}`,
    type: "reseek-ai" as const,
    baseUrl: RESEEK_AI_BASE_URL,
    model: LOCAL_MODELS[index % LOCAL_MODELS.length],
    priority: 2,
    enabled: Boolean(RESEEK_AI_BASE_URL && RESEEK_AI_API_KEY),
    downUntil: 0,
    successes: 0,
    failures: 0,
  })),
];

let cursor = 0;

function nodeSnapshot(node: GatewayNode) {
  const now = Date.now();
  return {
    id: node.id,
    name: node.name,
    type: node.type,
    baseUrl: node.type === "reseek-ai" ? "Reseek AI integration" : node.baseUrl,
    model: node.model,
    priority: node.priority,
    enabled: node.enabled,
    status: !node.enabled ? "disabled" : node.downUntil > now ? "down" : "ready",
    downUntil: node.downUntil > now ? new Date(node.downUntil).toISOString() : null,
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
  if (/no available OpenAI accounts|temporarily unavailable|503|rate limit|quota/i.test(error) || status === 503 || status === 429) {
    node.downUntil = Date.now() + NODE_DOWN_MS;
  }
}

function orderedCandidates() {
  const now = Date.now();
  const ready = nodes
    .filter((node) => node.enabled && node.downUntil <= now)
    .sort((a, b) => a.priority - b.priority);
  if (ready.length < 2) return ready;
  const head = ready.slice(0, 1);
  const rest = ready.slice(1);
  const rotated = rest.slice(cursor % rest.length).concat(rest.slice(0, cursor % rest.length));
  cursor += 1;
  return head.concat(rotated);
}

function getAuthHeaders(req: Request, node: GatewayNode) {
  if (node.type === "reseek-ai") {
    return {
      Authorization: `Bearer ${RESEEK_AI_API_KEY}`,
      "Content-Type": "application/json",
    };
  }
  const authorization = req.header("authorization");
  return {
    ...(authorization ? { Authorization: authorization } : {}),
    "Content-Type": "application/json",
  };
}

function sanitizeLocalChatBody(body: ChatBody, node: GatewayNode) {
  const next: ChatBody = { ...body };
  const requested = typeof next.model === "string" ? next.model : "";
  next.model = LOCAL_MODELS.includes(requested) || /^gpt-|^o\d|^o4-/i.test(requested) ? requested : node.model;
  if (typeof next.max_tokens === "number" && typeof next.max_completion_tokens !== "number") {
    next.max_completion_tokens = next.max_tokens;
  }
  delete next.max_tokens;
  if (/^gpt-5|^o\d|^o4-/i.test(String(next.model))) {
    delete next.temperature;
    delete next.top_p;
  }
  return next;
}

function messageText(message: Record<string, unknown>) {
  const content = message["content"];
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map((part) => {
      if (typeof part === "string") return part;
      if (part && typeof part === "object" && "text" in part) return String((part as { text?: unknown }).text ?? "");
      return "";
    }).filter(Boolean).join("\n");
  }
  return "";
}

function responsesInput(body: ChatBody) {
  return (body.messages ?? [])
    .map((message) => `${String(message["role"] ?? "user").toUpperCase()}:\n${messageText(message)}`)
    .join("\n\n");
}

function extractResponsesText(data: ResponsesApiResult) {
  const chunks: string[] = [];
  for (const item of data.output ?? []) {
    for (const content of item.content ?? []) {
      if (content.type === "output_text" && content.text) chunks.push(content.text);
    }
  }
  return chunks.join("");
}

async function callLocalResponsesNode(node: GatewayNode, body: ChatBody) {
  const requestBody = sanitizeLocalChatBody(body, node);
  const payload: Record<string, unknown> = {
    model: requestBody.model,
    input: responsesInput(requestBody),
  };
  const maxTokens = requestBody.max_completion_tokens;
  if (typeof maxTokens === "number") payload["max_output_tokens"] = maxTokens;
  const result = await fetchTextWithTimeout(`${node.baseUrl}/responses`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${RESEEK_AI_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!result.response.ok) return result;
  const parsed = JSON.parse(result.text || "{}") as ResponsesApiResult;
  const content = extractResponsesText(parsed);
  return {
    response: result.response,
    text: JSON.stringify({
      id: parsed.id || `chatcmpl-${Date.now()}`,
      object: "chat.completion",
      created: Math.floor(Date.now() / 1000),
      model: String(payload["model"]),
      choices: [
        {
          index: 0,
          message: { role: "assistant", content },
          finish_reason: "stop",
        },
      ],
      gateway_node: node.id,
    }),
  };
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

async function callNode(node: GatewayNode, req: Request, body: ChatBody) {
  const started = Date.now();
  if (node.type === "reseek-ai") {
    try {
      const result = await callLocalResponsesNode(node, body);
      if (result.response.ok) {
        recordSuccess(node, result.response.status, started);
        return result;
      }
      recordFailure(node, result.response.status, result.text || result.response.statusText, started);
      return result;
    } catch (error) {
      recordFailure(node, undefined, String(error), started);
      throw error;
    }
  }
  const requestBody = node.type === "reseek-ai" ? sanitizeLocalChatBody(body, node) : body;
  const url = `${node.baseUrl}/v1/chat/completions`;
  try {
    const result = await fetchTextWithTimeout(url, {
      method: "POST",
      headers: getAuthHeaders(req, node),
      body: JSON.stringify(requestBody),
    });
    if (result.response.ok) {
      recordSuccess(node, result.response.status, started);
      return result;
    }
    recordFailure(node, result.response.status, result.text || result.response.statusText, started);
    return result;
  } catch (error) {
    recordFailure(node, undefined, String(error), started);
    throw error;
  }
}

async function streamNode(node: GatewayNode, req: Request, res: Response, body: ChatBody) {
  const started = Date.now();
  if (node.type === "reseek-ai") {
    const result = await callLocalResponsesNode(node, body);
    if (!result.response.ok) {
      recordFailure(node, result.response.status, result.text || result.response.statusText, started);
      return false;
    }
    recordSuccess(node, result.response.status, started);
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
    if (content) {
      res.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: { content }, finish_reason: null }] })}\n\n`);
    }
    res.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: {}, finish_reason: "stop" }] })}\n\n`);
    res.write("data: [DONE]\n\n");
    res.end();
    return true;
  }
  const requestBody = node.type === "reseek-ai" ? sanitizeLocalChatBody(body, node) : body;
  const response = await fetch(`${node.baseUrl}/v1/chat/completions`, {
    method: "POST",
    headers: getAuthHeaders(req, node),
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
  for await (const chunk of response.body) {
    res.write(chunk);
  }
  res.end();
  return true;
}

router.get(["/health", "/v1/stats", "/stats"], (_req, res) => {
  const snapshots = nodes.map(nodeSnapshot);
  res.json({
    success: snapshots.some((node) => node.status === "ready"),
    mode: "multi-node-openai-compatible",
    remoteGateway: REMOTE_GATEWAY_BASE_URL,
    reseekNodes: RESEEK_NODE_COUNT,
    localAiConfigured: Boolean(RESEEK_AI_BASE_URL && RESEEK_AI_API_KEY),
    nodeDownMs: NODE_DOWN_MS,
    nodes: snapshots,
  });
});

router.get("/v1/models", async (req, res) => {
  const data: Array<Record<string, unknown>> = [];
  const errors: Array<Record<string, unknown>> = [];
  const remote = nodes.find((node) => node.type === "remote-sub2api");
  if (remote?.enabled && remote.downUntil <= Date.now()) {
    const started = Date.now();
    try {
      const result = await fetchTextWithTimeout(`${remote.baseUrl}/v1/models`, {
        method: "GET",
        headers: getAuthHeaders(req, remote),
      }, 12_000);
      if (result.response.ok) {
        const parsed = JSON.parse(result.text || "{}") as { data?: Array<Record<string, unknown>> };
        if (Array.isArray(parsed.data)) data.push(...parsed.data.map((model) => ({ ...model, gateway_node: remote.id })));
        recordSuccess(remote, result.response.status, started);
      } else {
        errors.push({ node: remote.id, status: result.response.status, error: result.text });
        recordFailure(remote, result.response.status, result.text || result.response.statusText, started);
      }
    } catch (error) {
      errors.push({ node: remote.id, error: String(error) });
      recordFailure(remote, undefined, String(error), started);
    }
  }
  for (const model of LOCAL_MODELS) {
    data.push({
      id: model,
      object: "model",
      created: 0,
      owned_by: "reseek-ai",
      gateway_node: "reseek-ai-pool",
    });
  }
  res.json({ object: "list", data, gateway: { nodes: nodes.map(nodeSnapshot), errors } });
});

router.post("/v1/chat/completions", async (req, res) => {
  const body = req.body as ChatBody;
  if (!Array.isArray(body.messages)) {
    res.status(400).json({ error: { message: "messages 不能为空", type: "invalid_request_error" } });
    return;
  }
  const candidates = orderedCandidates();
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
    if (!res.headersSent) {
      res.status(503).json({ error: { message: "所有网关节点都暂时不可用", type: "gateway_unavailable", details: errors } });
    }
    return;
  }
  for (const node of candidates) {
    try {
      const result = await callNode(node, req, body);
      if (result.response.ok) {
        res.status(result.response.status);
        res.setHeader("x-gateway-node", node.id);
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

export default router;