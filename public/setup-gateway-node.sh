#!/usr/bin/env bash
# setup-gateway-node.sh
# Run this in any fresh Replit pnpm workspace to add a deployable gateway node.
# Usage:  bash <(curl -fsSL http://45.205.27.69:8080/api/gateway/setup-gateway-node.sh)
set -e

UPSTREAM="http://45.205.27.69:8080/api/gateway"
REMOTE_REGISTER="http://45.205.27.69:8080/api/gateway/self-register"
ARTIFACT_DIR="artifacts/gateway-node"
WORKSPACE_ROOT="$(pwd)"

echo ""
echo "==> Setting up gateway-node artifact in: $WORKSPACE_ROOT"
echo ""

# ── 1. Create directory structure ──────────────────────────────────────────────
mkdir -p "$ARTIFACT_DIR/src"
mkdir -p "$ARTIFACT_DIR/.replit-artifact"

# ── 2. artifact.toml ──────────────────────────────────────────────────────────
cat > "$ARTIFACT_DIR/.replit-artifact/artifact.toml" << 'TOML'
kind = "web"
previewPath = "/"
title = "Gateway Node"
version = "1.0.0"
id = "artifacts/gateway-node"
router = "path"

[[services]]
name = "web"
paths = ["/"]
localPort = 3000

[services.development]
run = "pnpm --filter @workspace/gateway-node run dev"

[services.production]
build = ["pnpm", "--filter", "@workspace/gateway-node", "run", "build"]

[services.production.run]
args = ["node", "artifacts/gateway-node/dist/server.mjs"]

[services.env]
PORT = "3000"
BASE_PATH = "/"
TOML

# ── 3. package.json ────────────────────────────────────────────────────────────
cat > "$ARTIFACT_DIR/package.json" << 'JSON'
{
  "name": "@workspace/gateway-node",
  "version": "0.0.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "node --watch --experimental-strip-types src/index.ts",
    "build": "node build.mjs",
    "serve": "node dist/server.mjs"
  },
  "dependencies": {
    "express": "^5.1.0"
  },
  "devDependencies": {
    "esbuild": "^0.27.0",
    "@types/node": "^22.0.0",
    "@types/express": "^5.0.0"
  }
}
JSON

# ── 4. build.mjs ───────────────────────────────────────────────────────────────
cat > "$ARTIFACT_DIR/build.mjs" << 'JS'
import { build } from "esbuild";
import path from "node:path";
import { fileURLToPath } from "node:url";

const dir = path.dirname(fileURLToPath(import.meta.url));

await build({
  entryPoints: [path.join(dir, "src/index.ts")],
  platform: "node",
  bundle: true,
  format: "esm",
  outfile: path.join(dir, "dist/server.mjs"),
  external: ["express"],
  logLevel: "info",
  sourcemap: true,
});
JS

# ── 5. src/index.ts ────────────────────────────────────────────────────────────
cat > "$ARTIFACT_DIR/src/index.ts" << TYPESCRIPT
import express, { type Request, type Response } from "express";
import * as net from "node:net";

const UPSTREAM = "${UPSTREAM}";
const REMOTE_REGISTER = "${REMOTE_REGISTER}";
const TUNNEL_TOKEN = process.env["TUNNEL_TOKEN"] ?? "123456";
const NODE_NAME_ENV = process.env["REPLIT_USER"] ?? "";

const MODELS = [
  "gpt-5-mini", "gpt-5", "gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini",
  "claude-opus-4-5", "claude-sonnet-4-5", "claude-3-5-haiku-20241022",
  "gemini-2.5-pro", "gemini-2.5-flash",
];

// ── 隧道会话管理 ────────────────────────────────────────────────────────────────
const SESSION_IDLE_MS = 5 * 60_000;

interface Session {
  socket: net.Socket;
  readBuffer: Buffer[];
  waiters: Array<(data: Buffer | null) => void>;
  closed: boolean;
  lastActivityAt: number;
}

const sessions = new Map<string, Session>();

function createId(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

function cleanupSession(id: string): void {
  const s = sessions.get(id);
  if (!s) return;
  s.closed = true;
  for (const w of s.waiters.splice(0)) w(null);
  try { s.socket.destroy(); } catch {}
  sessions.delete(id);
}

setInterval(() => {
  const now = Date.now();
  for (const [id, s] of sessions.entries()) {
    if (s.closed || now - s.lastActivityAt > SESSION_IDLE_MS) cleanupSession(id);
  }
}, 30_000).unref();

// ── 工具函数 ────────────────────────────────────────────────────────────────────
function checkToken(req: Request): boolean {
  const t = req.query["token"] ?? req.query["tok"];
  return typeof t === "string" && t === TUNNEL_TOKEN;
}

function getSelfUrl(): string {
  const domains = process.env["REPLIT_DOMAINS"];
  if (domains) return "https://" + domains.split(",")[0]!.trim();
  const dev = process.env["REPLIT_DEV_DOMAIN"];
  if (dev) return "https://" + dev;
  return "http://localhost:" + (process.env["PORT"] ?? "3000");
}

function getNodeName(): string {
  if (NODE_NAME_ENV) return NODE_NAME_ENV;
  const domains = process.env["REPLIT_DOMAINS"];
  if (domains) return "Replit(" + (domains.split(",")[0]!.split(".")[0] ?? "prod") + ")";
  const dev = process.env["REPLIT_DEV_DOMAIN"]?.split(".")[0];
  return "Replit(" + (dev ?? "dev") + ")";
}

// ── 自注册 ──────────────────────────────────────────────────────────────────────
async function selfRegister(attempt = 0): Promise<void> {
  const selfBase = getSelfUrl();
  const gatewayUrl = selfBase + "/api/gateway";
  try {
    const r = await fetch(REMOTE_REGISTER, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: getNodeName(),
        gatewayUrl,
        token: TUNNEL_TOKEN,
        models: MODELS,
        priority: 5,
        source: "runtime",
      }),
      signal: AbortSignal.timeout(10_000),
    });
    const b = await r.json().catch(() => ({})) as Record<string, unknown>;
    console.log("[register]", r.status, "nodeId=" + (b["nodeId"] ?? "?"), "url=" + gatewayUrl);
    if (!r.ok && attempt < 5) {
      setTimeout(() => selfRegister(attempt + 1), 30_000 * Math.pow(2, attempt));
    }
  } catch (e) {
    console.warn("[register] failed:", String(e));
    if (attempt < 5) setTimeout(() => selfRegister(attempt + 1), 30_000 * Math.pow(2, attempt));
  }
}

// ── HTTP 代理转发 ───────────────────────────────────────────────────────────────
function proxyFetch(url: string, req: Request, res: Response, body?: unknown) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 120_000);
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const auth = req.header("authorization") || req.header("x-api-key");
  if (auth) headers["Authorization"] = auth.startsWith("Bearer ") ? auth : "Bearer " + auth;
  const isStream = body && typeof body === "object" && (body as Record<string, unknown>)["stream"] === true;

  fetch(url, { method: req.method, headers, ...(body ? { body: JSON.stringify(body) } : {}), signal: ctrl.signal })
    .then(async (up) => {
      clearTimeout(timer);
      res.status(up.status);
      res.setHeader("Content-Type", up.headers.get("content-type") ?? "application/json");
      res.setHeader("X-Proxied-To", UPSTREAM);
      if (isStream && up.body) {
        res.setHeader("Cache-Control", "no-cache");
        res.setHeader("X-Accel-Buffering", "no");
        const reader = up.body.getReader();
        const pump = (): Promise<void> => reader.read().then(({ done, value }) => {
          if (done) { res.end(); return; }
          res.write(value); return pump();
        });
        pump().catch(() => res.end());
      } else {
        res.send(await up.text());
      }
    })
    .catch((e) => { clearTimeout(timer); if (!res.headersSent) res.status(502).json({ ok: false, error: String(e) }); });
}

// ── Express 应用 ────────────────────────────────────────────────────────────────
const app = express();
app.use(express.urlencoded({ extended: true }));

// ── 隧道路由（HTTP 长轮询，供 http-poll-bridge 使用）─────────────────────────────
function addTunnelRoutes(prefix: "stream" | "tunnel") {
  // POST /api/{prefix}/open?host=...&port=...&token=...
  app.post(\`/api/\${prefix}/open\`, (req, res) => {
    if (!checkToken(req)) { res.status(403).json({ error: "forbidden" }); return; }
    const host = String(req.query["host"] ?? "");
    const port = Number.parseInt(String(req.query["port"] ?? ""), 10);
    if (!host || !Number.isInteger(port) || port <= 0 || port > 65535) {
      res.status(400).json({ error: "host/port required" }); return;
    }
    const id = createId();
    const socket = net.createConnection({ host, port }, () => {
      const session: Session = { socket, readBuffer: [], waiters: [], closed: false, lastActivityAt: Date.now() };
      sessions.set(id, session);
      socket.on("data", (chunk: Buffer) => {
        const cur = sessions.get(id); if (!cur) return;
        const w = cur.waiters.shift();
        if (w) w(chunk); else cur.readBuffer.push(chunk);
      });
      const close = () => { const cur = sessions.get(id); if (!cur) return; cur.closed = true; for (const w of cur.waiters.splice(0)) w(null); };
      socket.on("close", close); socket.on("error", close);
      res.json({ ok: true, id });
    });
    socket.on("error", (err: Error) => { if (!sessions.has(id) && !res.headersSent) res.status(502).json({ error: err.message }); });
    setTimeout(() => { if (!sessions.has(id)) socket.destroy(); }, 10_000);
  });

  // GET /api/{prefix}/read/:id?token=...
  app.get(\`/api/\${prefix}/read/:id\`, async (req, res) => {
    if (!checkToken(req)) { res.status(403).json({ error: "forbidden" }); return; }
    const s = sessions.get(req.params["id"]);
    if (!s) { res.status(404).json({ error: "no session" }); return; }
    s.lastActivityAt = Date.now();
    res.set({ "Content-Type": "application/octet-stream", "Cache-Control": "no-store", "X-Accel-Buffering": "no", Connection: "close" });
    if (s.readBuffer.length > 0) { res.end(Buffer.concat(s.readBuffer.splice(0))); return; }
    if (s.closed) { cleanupSession(req.params["id"]); res.status(410).json({ error: "session closed" }); return; }
    const data = await new Promise<Buffer | null>((resolve) => {
      let waiter: (c: Buffer | null) => void;
      const timer = setTimeout(() => { const i = s.waiters.indexOf(waiter); if (i >= 0) s.waiters.splice(i, 1); resolve(Buffer.alloc(0)); }, 25_000);
      waiter = (c) => { clearTimeout(timer); resolve(c); };
      s.waiters.push(waiter);
    });
    if (!data) { cleanupSession(req.params["id"]); res.status(410).json({ error: "session closed" }); return; }
    res.end(data);
  });

  // POST /api/{prefix}/write/:id?token=...
  app.post(\`/api/\${prefix}/write/:id\`, (req, res) => {
    if (!checkToken(req)) { res.status(403).json({ error: "forbidden" }); return; }
    const s = sessions.get(req.params["id"]);
    if (!s) { res.status(404).json({ error: "no session" }); return; }
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => { s.lastActivityAt = Date.now(); s.socket.write(Buffer.concat(chunks)); res.json({ ok: true }); });
  });

  // DELETE /api/{prefix}/:id?token=...
  app.delete(\`/api/\${prefix}/:id\`, (req, res) => {
    if (!checkToken(req)) { res.status(403).json({ error: "forbidden" }); return; }
    cleanupSession(req.params["id"]);
    res.json({ ok: true });
  });

  // GET /api/{prefix}/health
  app.get(\`/api/\${prefix}/health\`, (_req, res) => {
    res.json({ ok: true, sessions: sessions.size, name: getNodeName(), time: new Date().toISOString() });
  });
}

addTunnelRoutes("stream");
addTunnelRoutes("tunnel");

// ── 健康检查 ────────────────────────────────────────────────────────────────────
app.use(express.json({ limit: "10mb" }));
app.get("/api/healthz", (_req, res) => res.json({ status: "ok", ts: Date.now() }));
app.get("/health", (_req, res) => res.json({ ok: true, name: "replit-subnode", tunnel: true }));

// ── 网关代理路由 ────────────────────────────────────────────────────────────────
const info = () => ({
  ok: true, success: true, service: "replit-gateway-node",
  workspace: getSelfUrl(), node: getNodeName(), mode: "proxy", upstream: UPSTREAM,
  nodes_total: 1, nodes_ready: 1,
  nodes: [{ id: "upstream", name: getNodeName(), status: "ready", baseUrl: UPSTREAM }],
});

app.get(["/api/gateway/health", "/api/gateway/nodes", "/api/gateway/stats", "/api/gateway/v1/stats"],
  (_req, res) => res.json(info()));
app.get("/api/gateway/v1/models", (_req, res) =>
  res.json({ object: "list", data: MODELS.map((id) => ({ id, object: "model", created: 0, owned_by: "replit-proxy" })) }));
app.all("/api/gateway/v1/chat/completions", (req, res) => proxyFetch(UPSTREAM + "/v1/chat/completions", req, res, req.body));
app.all("/api/gateway/v1/responses",        (req, res) => proxyFetch(UPSTREAM + "/v1/responses",        req, res, req.body));
app.all("/api/gateway/v1/embeddings",       (req, res) => proxyFetch(UPSTREAM + "/v1/embeddings",       req, res, req.body));
app.all("/api/gateway/*path", (req, res) => {
  const qs = req.url.includes("?") ? "?" + req.url.split("?").slice(1).join("?") : "";
  proxyFetch(UPSTREAM + "/" + (req.params as Record<string,string>)["path"] + qs, req, res,
    ["POST","PUT","PATCH"].includes(req.method) ? req.body : undefined);
});

// ── 状态页 ──────────────────────────────────────────────────────────────────────
app.get(["/", "/index.html"], (_req, res) => {
  const d = info();
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.send(\`<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>Gateway Node</title>
<style>body{background:#0d1117;color:#e6edf3;font-family:system-ui,sans-serif;max-width:700px;margin:60px auto;padding:0 24px}
h1{font-size:1.6rem;margin-bottom:4px}.sub{color:#8b949e;font-size:.9rem;margin-bottom:32px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px 24px;margin-bottom:16px}
.row{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #21262d}
.row:last-child{border-bottom:none}.label{color:#8b949e}.value{font-family:monospace}
.online{color:#3fb950}.ts{color:#8b949e;font-size:.8rem;text-align:center;margin-top:24px}</style></head>
<body><h1><span class="online">●</span> Gateway Node</h1>
<p class="sub">Replit 友节点 · 代理 + 隧道</p>
<div class="card">
  <div class="row"><span class="label">状态</span><span class="value online">online</span></div>
  <div class="row"><span class="label">节点</span><span class="value">\${d.node}</span></div>
  <div class="row"><span class="label">URL</span><span class="value">\${d.workspace}</span></div>
  <div class="row"><span class="label">上游</span><span class="value">\${UPSTREAM}</span></div>
  <div class="row"><span class="label">隧道会话</span><span class="value">\${sessions.size}</span></div>
</div>
<div class="card">
  <div class="row"><span class="label">/api/stream/health</span><a class="value" href="/api/stream/health" target="_blank">↗</a></div>
  <div class="row"><span class="label">/api/gateway/health</span><a class="value" href="/api/gateway/health" target="_blank">↗</a></div>
  <div class="row"><span class="label">/api/gateway/v1/models</span><a class="value" href="/api/gateway/v1/models" target="_blank">↗</a></div>
</div>
<p class="ts">自注册间隔：每 5 分钟 · 含隧道路由</p>
</body></html>\`);
});

const PORT = Number(process.env["PORT"] ?? 3000);
app.listen(PORT, "0.0.0.0", () => {
  console.log("[gateway-node] :" + PORT + "  self=" + getSelfUrl() + "  tunnel=enabled");
  setTimeout(() => selfRegister(), 3_000);
  setInterval(selfRegister, 5 * 60_000);
});
TYPESCRIPT

# ── 6. Install & build ─────────────────────────────────────────────────────────
echo "==> Installing dependencies..."
pnpm install --filter @workspace/gateway-node 2>&1 | tail -5

echo "==> Building server..."
pnpm --filter @workspace/gateway-node run build

echo ""
echo "======================================================"
echo "  [1/2 Done] 代码已就绪，已完成构建。"
echo ""
echo "  ★ 最终步骤（仅需做一次）："
echo "  在 Replit Agent 对话框中输入："
echo "  「帮我把 artifacts/gateway-node 注册为"
echo "    可发布的 web 应用（kind=web）」"
echo ""
echo "  Agent 注册完成后点击 [Publish] 即可。"
echo "  发布后节点会自动用生产 URL 注册到网关。"
echo "======================================================"
echo ""
