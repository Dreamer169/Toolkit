import { Router, type Request, type Response } from "express";
import { readFileSync, writeFileSync, mkdirSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

// ═══ 类型 ═══════════════════════════════════════════════════════════════════

type NodeType = "remote-sub2api" | "local-gateway" | "reseek-ai" | "friend";

type GatewayNode = {
  id: string;
  name: string;
  type: NodeType;
  baseUrl: string;
  apiKey?: string;
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
  persistent?: boolean;
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

// ═══ 配置 ═══════════════════════════════════════════════════════════════════

const router = Router();

const __filename = fileURLToPath(import.meta.url);
const __dirname_local = dirname(__filename);
const DATA_DIR = resolve(__dirname_local, "../../data");
const NODES_FILE = resolve(DATA_DIR, "gateway-nodes.json");

const LOCAL_GATEWAY_BASE_URL = (process.env["LOCAL_GATEWAY_BASE_URL"] || "").replace(/\/$/, "");
const REMOTE_GATEWAY_BASE_URL = (process.env["REMOTE_GATEWAY_BASE_URL"] || "http://45.205.27.69:9090").replace(/\/$/, "");
const RESEEK_AI_BASE_URL = (process.env["AI_INTEGRATIONS_OPENAI_BASE_URL"] || "").replace(/\/$/, "");
const RESEEK_AI_API_KEY = process.env["AI_INTEGRATIONS_OPENAI_API_KEY"] || "";
const RESEEK_NODE_COUNT = Math.min(12, Math.max(1, Number(process.env["RESEEK_AI_NODE_COUNT"] || 4)));
const NODE_DOWN_MS = Math.max(30_000, Number(process.env["GATEWAY_NODE_DOWN_MS"] || 180_000));
const LOCAL_MODELS = ["gpt-5.2", "gpt-5-mini", "gpt-5-nano", "o4-mini"];
const SUB2API_ADMIN_URL = "http://127.0.0.1:9090";
const SUB2API_ADMIN_KEY = process.env["SUB2API_ADMIN_KEY"] || "";

// ═══ 持久化 friend 节点 ═══════════════════════════════════════════════════

type PersistentNode = Pick<GatewayNode, "id" | "name" | "baseUrl" | "apiKey" | "model" | "priority" | "enabled">;

function loadPersistentNodes(): PersistentNode[] {
  try {
    mkdirSync(DATA_DIR, { recursive: true });
    const raw = readFileSync(NODES_FILE, "utf-8");
    return JSON.parse(raw) as PersistentNode[];
  } catch {
    return [];
  }
}

function savePersistentNodes(friends: GatewayNode[]) {
  try {
    mkdirSync(DATA_DIR, { recursive: true });
    const toSave: PersistentNode[] = friends.map((n) => ({
      id: n.id,
      name: n.name,
      baseUrl: n.baseUrl,
      apiKey: n.apiKey,
      model: n.model,
      priority: n.priority,
      enabled: n.enabled,
    }));
    writeFileSync(NODES_FILE, JSON.stringify(toSave, null, 2), "utf-8");
  } catch (e) {
    console.error("[gateway] 持久化节点保存失败:", e);
  }
}

function buildFriendNode(p: PersistentNode): GatewayNode {
  return {
    ...p,
    type: "friend",
    downUntil: 0,
    successes: 0,
    failures: 0,
    persistent: true,
  };
}

// ═══ 内置节点 ═══════════════════════════════════════════════════════════════

const builtinNodes: GatewayNode[] = [
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
  {
    id: "local-replit-gateway",
    name: "Replit 本地兜底网关",
    type: "local-gateway",
    baseUrl: LOCAL_GATEWAY_BASE_URL,
    model: "gpt-5.2",
    priority: 2,
    enabled: Boolean(LOCAL_GATEWAY_BASE_URL),
    downUntil: 0,
    successes: 0,
    failures: 0,
  },
  ...Array.from({ length: RESEEK_NODE_COUNT }, (_, i) => ({
    id: `reseek-ai-${i + 1}`,
    name: `Reseek AI 子节点 ${i + 1}`,
    type: "reseek-ai" as NodeType,
    baseUrl: RESEEK_AI_BASE_URL,
    model: LOCAL_MODELS[i % LOCAL_MODELS.length],
    priority: 3,
    enabled: Boolean(RESEEK_AI_BASE_URL && RESEEK_AI_API_KEY),
    downUntil: 0,
    successes: 0,
    failures: 0,
  })),
];

const persistedFriends: GatewayNode[] = loadPersistentNodes().map(buildFriendNode);
const nodes: GatewayNode[] = [...builtinNodes, ...persistedFriends];

function getFriendNodes(): GatewayNode[] {
  return nodes.filter((n) => n.type === "friend");
}

// ═══ 状态工具 ════════════════════════════════════════════════════════════════

let cursor = 0;

function nodeSnapshot(node: GatewayNode) {
  const now = Date.now();
  return {
    id: node.id,
    name: node.name,
    type: node.type,
    baseUrl: node.type === "reseek-ai" ? "[Reseek AI Integration]" : node.baseUrl,
    model: node.model,
    priority: node.priority,
    enabled: node.enabled,
    persistent: node.persistent ?? false,
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
  if (
    /no available|temporarily unavailable|503|rate limit|quota/i.test(error) ||
    status === 503 || status === 429
  ) {
    node.downUntil = Date.now() + NODE_DOWN_MS;
  }
}

function orderedCandidates() {
  const now = Date.now();
  const ready = nodes
    .filter((n) => n.enabled && n.downUntil <= now)
    .sort((a, b) => a.priority - b.priority);
  if (ready.length < 2) return ready;
  const head = ready.slice(0, 1);
  const rest = ready.slice(1);
  const i = cursor % rest.length;
  cursor += 1;
  return head.concat(rest.slice(i).concat(rest.slice(0, i)));
}

// ═══ 调用实现 ════════════════════════════════════════════════════════════════

async function fetchTextWithTimeout(url: string, init: RequestInit, timeoutMs = 120_000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...init, signal: ctrl.signal });
    const text = await response.text();
    return { response, text };
  } finally {
    clearTimeout(timer);
  }
}

function sanitizeForReseek(body: ChatBody, model: string) {
  const next: ChatBody = { ...body };
  next.model = LOCAL_MODELS.includes(model) ? model : "gpt-5.2";
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

function messageText(msg: Record<string, unknown>) {
  const c = msg["content"];
  if (typeof c === "string") return c;
  if (Array.isArray(c)) {
    return c
      .map((p) => (p && typeof p === "object" && "text" in p ? String((p as { text?: unknown }).text ?? "") : ""))
      .filter(Boolean)
      .join("\n");
  }
  return "";
}

function responsesInput(body: ChatBody) {
  return (body.messages ?? [])
    .map((m) => `${String(m["role"] ?? "user").toUpperCase()}:\n${messageText(m)}`)
    .join("\n\n");
}

function extractResponsesText(data: ResponsesApiResult) {
  return (data.output ?? [])
    .flatMap((item) =>
      (item.content ?? []).filter((c) => c.type === "output_text").map((c) => c.text ?? "")
    )
    .join("");
}

async function callNode(
  node: GatewayNode,
  req: Request,
  body: ChatBody,
): Promise<{ response: Response; text: string } | false> {
  const started = Date.now();

  // ── Reseek AI（Responses API）──────────────────────────────
  if (node.type === "reseek-ai") {
    const san = sanitizeForReseek(body, node.model);
    const payload: Record<string, unknown> = {
      model: san.model,
      input: responsesInput(san),
    };
    if (typeof san.max_completion_tokens === "number") payload["max_output_tokens"] = san.max_completion_tokens;
    try {
      const result = await fetchTextWithTimeout(`${node.baseUrl}/responses`, {
        method: "POST",
        headers: { Authorization: `Bearer ${RESEEK_AI_API_KEY}`, "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!result.response.ok) {
        recordFailure(node, result.response.status, result.text, started);
        return result;
      }
      const parsed = JSON.parse(result.text || "{}") as ResponsesApiResult;
      const content = extractResponsesText(parsed);
      recordSuccess(node, result.response.status, started);
      return {
        response: new Response(null, { status: 200 }),
        text: JSON.stringify({
          id: parsed.id || `chatcmpl-${Date.now()}`,
          object: "chat.completion",
          created: Math.floor(Date.now() / 1000),
          model: String(payload["model"]),
          choices: [{ index: 0, message: { role: "assistant", content }, finish_reason: "stop" }],
          gateway_node: node.id,
        }),
      };
    } catch (e) {
      recordFailure(node, undefined, String(e), started);
      return false;
    }
  }

  // ── Local gateway（/v1/chat/completions）───────────────────
  if (node.type === "local-gateway") {
    try {
      const result = await fetchTextWithTimeout(`${node.baseUrl}/v1/chat/completions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...body, stream: false }),
      });
      if (!result.response.ok) {
        recordFailure(node, result.response.status, result.text, started);
        return false;
      }
      recordSuccess(node, result.response.status, started);
      return result;
    } catch (e) {
      recordFailure(node, undefined, String(e), started);
      return false;
    }
  }

  // ── Friend / Remote sub2api ─────────────────────────────────
  const authKey =
    node.type === "friend"
      ? node.apiKey || req.header("authorization")?.replace(/^Bearer\s+/i, "") || ""
      : req.header("authorization")?.replace(/^Bearer\s+/i, "") || "";
  try {
    const result = await fetchTextWithTimeout(`${node.baseUrl}/v1/chat/completions`, {
      method: "POST",
      headers: { Authorization: `Bearer ${authKey}`, "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, stream: false }),
    });
    if (!result.response.ok) {
      recordFailure(node, result.response.status, result.text, started);
      return result;
    }
    recordSuccess(node, result.response.status, started);
    return result;
  } catch (e) {
    recordFailure(node, undefined, String(e), started);
    return false;
  }
}

async function streamNode(node: GatewayNode, req: Request, res: Response, body: ChatBody): Promise<boolean> {
  if (node.type === "reseek-ai" || node.type === "local-gateway") {
    const result = await callNode(node, req, body);
    if (!result || !result.response.ok) return false;
    let text = "";
    try {
      const parsed = JSON.parse(result.text) as { choices?: Array<{ message?: { content?: string } }> };
      text = parsed.choices?.[0]?.message?.content ?? result.text;
    } catch {
      text = result.text;
    }
    if (!res.headersSent) {
      res.setHeader("Content-Type", "text/event-stream");
      res.setHeader("Cache-Control", "no-cache");
      res.setHeader("Connection", "keep-alive");
      res.setHeader("x-gateway-node", node.id);
    }
    const chunk = {
      id: `chatcmpl-${Date.now()}`,
      object: "chat.completion.chunk",
      created: Math.floor(Date.now() / 1000),
      model: body.model || "gpt-5.2",
      choices: [{ index: 0, delta: { role: "assistant", content: text }, finish_reason: null }],
      gateway_node: node.id,
    };
    res.write(`data: ${JSON.stringify(chunk)}\n\n`);
    res.write("data: [DONE]\n\n");
    res.end();
    return true;
  }

  const started = Date.now();
  const authKey =
    node.type === "friend"
      ? node.apiKey || req.header("authorization")?.replace(/^Bearer\s+/i, "") || ""
      : req.header("authorization")?.replace(/^Bearer\s+/i, "") || "";
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 120_000);
  try {
    const response = await fetch(`${node.baseUrl}/v1/chat/completions`, {
      method: "POST",
      headers: { Authorization: `Bearer ${authKey}`, "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, stream: true }),
      signal: ctrl.signal,
    });
    if (!response.ok || !response.body) {
      const text = await response.text();
      recordFailure(node, response.status, text, started);
      return false;
    }
    recordSuccess(node, response.status, started);
    if (!res.headersSent) {
      res.setHeader("Content-Type", "text/event-stream");
      res.setHeader("Cache-Control", "no-cache");
      res.setHeader("Connection", "keep-alive");
      res.setHeader("x-gateway-node", node.id);
    }
    for await (const chunk of response.body) {
      res.write(chunk);
    }
    res.end();
    return true;
  } catch (e) {
    recordFailure(node, undefined, String(e), started);
    return false;
  } finally {
    clearTimeout(timer);
  }
}

// ═══ HTTP 路由 ════════════════════════════════════════════════════════════════

router.get(["/health", "/v1/stats", "/stats"], (_req, res) => {
  res.json({
    success: nodes.some((n) => n.enabled && n.downUntil <= Date.now()),
    mode: "multi-node-openai-compatible",
    nodeCount: nodes.length,
    friendNodeCount: getFriendNodes().length,
    reseekNodes: RESEEK_NODE_COUNT,
    localAiConfigured: Boolean(RESEEK_AI_BASE_URL && RESEEK_AI_API_KEY),
    nodeDownMs: NODE_DOWN_MS,
    nodes: nodes.map(nodeSnapshot),
  });
});

router.get("/v1/models", async (req, res) => {
  const data: Array<Record<string, unknown>> = [];
  const errors: Array<Record<string, unknown>> = [];
  const remote = nodes.find((n) => n.type === "remote-sub2api");
  if (remote?.enabled && remote.downUntil <= Date.now()) {
    const started = Date.now();
    try {
      const result = await fetchTextWithTimeout(
        `${remote.baseUrl}/v1/models`,
        { method: "GET", headers: { Authorization: req.header("authorization") || "", "Content-Type": "application/json" } },
        12_000,
      );
      if (result.response.ok) {
        const parsed = JSON.parse(result.text || "{}") as { data?: Array<Record<string, unknown>> };
        if (Array.isArray(parsed.data)) data.push(...parsed.data.map((m) => ({ ...m, gateway_node: remote.id })));
        recordSuccess(remote, result.response.status, started);
      } else {
        errors.push({ node: remote.id, status: result.response.status, error: result.text });
        recordFailure(remote, result.response.status, result.text, started);
      }
    } catch (e) {
      errors.push({ node: remote.id, error: String(e) });
      recordFailure(remote, undefined, String(e), started);
    }
  }
  for (const model of LOCAL_MODELS) {
    data.push({ id: model, object: "model", created: 0, owned_by: "reseek-ai", gateway_node: "reseek-ai-pool" });
  }
  for (const fn of getFriendNodes().filter((n) => n.enabled)) {
    data.push({ id: fn.model, object: "model", created: 0, owned_by: fn.name, gateway_node: fn.id });
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
      } catch (e) {
        errors.push({ node: node.id, error: String(e) });
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
      if (result && result.response.ok) {
        res.status(result.response.status);
        res.setHeader("x-gateway-node", node.id);
        res.type(result.response.headers.get("content-type") || "application/json");
        res.send(result.text);
        return;
      }
      if (result) errors.push({ node: node.id, status: result.response.status, error: result.text });
      else errors.push({ node: node.id, error: "node returned false" });
    } catch (e) {
      errors.push({ node: node.id, error: String(e) });
    }
  }
  res.status(503).json({ error: { message: "所有网关节点都暂时不可用", type: "gateway_unavailable", details: errors } });
});

// ═══ 节点管理 API ════════════════════════════════════════════════════════════

router.get("/v1/nodes", (_req, res) => {
  res.json({ success: true, nodes: nodes.map(nodeSnapshot) });
});

router.post("/v1/nodes/friend", (req, res) => {
  const { name, baseUrl, apiKey = "", model = "gpt-4o", priority = 5 } = req.body as {
    name?: string; baseUrl?: string; apiKey?: string; model?: string; priority?: number;
  };
  if (!name || !baseUrl) {
    res.status(400).json({ success: false, error: "name 和 baseUrl 必填" });
    return;
  }
  const id = `friend-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
  const node = buildFriendNode({ id, name, baseUrl: baseUrl.replace(/\/$/, ""), apiKey, model, priority, enabled: true });
  nodes.push(node);
  savePersistentNodes(getFriendNodes());
  res.json({ success: true, node: nodeSnapshot(node) });
});

router.patch("/v1/nodes/:id", (req, res) => {
  const node = nodes.find((n) => n.id === req.params.id);
  if (!node) { res.status(404).json({ success: false, error: "节点不存在" }); return; }
  const { enabled, name, priority, apiKey, model } = req.body as {
    enabled?: boolean; name?: string; priority?: number; apiKey?: string; model?: string;
  };
  if (typeof enabled === "boolean") { node.enabled = enabled; node.downUntil = 0; }
  if (name) node.name = name;
  if (typeof priority === "number") node.priority = priority;
  if (apiKey !== undefined) node.apiKey = apiKey;
  if (model) node.model = model;
  if (node.type === "friend") savePersistentNodes(getFriendNodes());
  res.json({ success: true, node: nodeSnapshot(node) });
});

router.delete("/v1/nodes/:id", (req, res) => {
  const idx = nodes.findIndex((n) => n.id === req.params.id && n.type === "friend");
  if (idx === -1) { res.status(404).json({ success: false, error: "friend 节点不存在或内置节点不可删除" }); return; }
  const [removed] = nodes.splice(idx, 1);
  savePersistentNodes(getFriendNodes());
  res.json({ success: true, removed: removed.id });
});

router.post("/v1/nodes/:id/test", async (req, res) => {
  const node = nodes.find((n) => n.id === req.params.id);
  if (!node) { res.status(404).json({ success: false, error: "节点不存在" }); return; }
  const testBody: ChatBody = {
    model: node.model === "upstream" ? "gpt-4o-mini" : node.model,
    messages: [{ role: "user", content: "Hi, reply with OK only." }],
    max_completion_tokens: 10,
  };
  try {
    const result = await callNode(node, req, testBody);
    if (!result) {
      res.json({ success: false, node: node.id, error: node.lastError, latencyMs: node.lastLatencyMs });
      return;
    }
    let preview = "";
    try {
      const parsed = JSON.parse(result.text) as { choices?: Array<{ message?: { content?: string } }> };
      preview = parsed.choices?.[0]?.message?.content?.slice(0, 100) ?? result.text.slice(0, 100);
    } catch {
      preview = result.text.slice(0, 100);
    }
    res.json({
      success: result.response.ok,
      node: node.id,
      status: result.response.status,
      latencyMs: node.lastLatencyMs,
      preview,
      error: result.response.ok ? undefined : result.text.slice(0, 300),
    });
  } catch (e) {
    res.json({ success: false, node: node.id, error: String(e) });
  }
});

router.post("/v1/nodes/:id/recover", (req, res) => {
  const node = nodes.find((n) => n.id === req.params.id);
  if (!node) { res.status(404).json({ success: false, error: "节点不存在" }); return; }
  node.downUntil = 0;
  node.enabled = true;
  res.json({ success: true, node: nodeSnapshot(node) });
});

// ═══ Sub2API 管理代理 ═════════════════════════════════════════════════════════

router.use("/v1/sub2api-admin", async (req: Request, res: Response) => {
  const subPath = (req.url as string).split("?")[0];
  const targetUrl = `${SUB2API_ADMIN_URL}${subPath}`;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 30_000);
  try {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const inAuth = req.header("authorization");
    if (inAuth) headers["Authorization"] = inAuth;
    else if (SUB2API_ADMIN_KEY) headers["Authorization"] = `Bearer ${SUB2API_ADMIN_KEY}`;
    const opts: RequestInit = { method: req.method, headers, signal: ctrl.signal };
    if (req.method !== "GET" && req.method !== "HEAD") opts.body = JSON.stringify(req.body);
    const upstream = await fetch(targetUrl, opts);
    const text = await upstream.text();
    res.status(upstream.status);
    res.type(upstream.headers.get("content-type") || "application/json");
    res.send(text);
  } catch (e) {
    res.status(502).json({ success: false, error: `sub2api 代理失败: ${String(e)}` });
  } finally {
    clearTimeout(timer);
  }
});

// ═══ 连通性诊断 ═══════════════════════════════════════════════════════════════

router.get("/v1/diagnose", async (_req, res) => {
  const results: Record<string, unknown> = {};

  // 1. sub2api 健康
  try {
    const r = await fetchTextWithTimeout(`${SUB2API_ADMIN_URL}/health`, {}, 5_000);
    results["sub2api_local"] = { ok: r.response.ok, status: r.response.status };
  } catch (e) {
    results["sub2api_local"] = { ok: false, error: String(e) };
  }

  // 2. sub2api v1/models
  try {
    const r = await fetchTextWithTimeout(`${SUB2API_ADMIN_URL}/v1/models`, SUB2API_ADMIN_KEY ? { headers: { Authorization: `Bearer ${SUB2API_ADMIN_KEY}` } } : {}, 8_000);
    results["sub2api_models"] = { ok: r.response.ok, status: r.response.status, body: r.text.slice(0, 200) };
  } catch (e) {
    results["sub2api_models"] = { ok: false, error: String(e) };
  }

  // 3. Replit gateway 可达性
  if (LOCAL_GATEWAY_BASE_URL) {
    try {
      const r = await fetchTextWithTimeout(`${LOCAL_GATEWAY_BASE_URL}/v1/models`, {}, 15_000);
      results["replit_gateway"] = { ok: r.response.ok, status: r.response.status, body: r.text.slice(0, 200) };
    } catch (e) {
      results["replit_gateway"] = { ok: false, error: String(e) };
    }
  } else {
    results["replit_gateway"] = { ok: false, error: "LOCAL_GATEWAY_BASE_URL 未配置" };
  }

  // 4. sub2api accounts DB 状态
  try {
    const { Pool } = await import("pg");
    const pool = new Pool({ host: "127.0.0.1", port: 5432, user: "postgres", password: "postgres", database: "sub2api", max: 2 });
    const r = await pool.query("SELECT id, name, platform, status FROM accounts WHERE deleted_at IS NULL ORDER BY id");
    results["sub2api_accounts_db"] = { ok: true, count: r.rows.length, accounts: r.rows };
    const ag = await pool.query("SELECT account_id, group_id FROM account_groups ORDER BY account_id LIMIT 20");
    results["sub2api_account_groups"] = { ok: true, count: ag.rows.length, rows: ag.rows };
    await pool.end();
  } catch (e) {
    results["sub2api_accounts_db"] = { ok: false, error: String(e) };
  }

  // 5. 网关节点状态
  results["gateway_nodes"] = {
    total: nodes.length,
    ready: nodes.filter((n) => n.enabled && n.downUntil <= Date.now()).length,
    nodes: nodes.map(nodeSnapshot),
  };

  res.json({ success: true, timestamp: new Date().toISOString(), results });
});

export default router;
