#!/usr/bin/env bash
# setup-gateway-node.sh
# Run this in any fresh Replit pnpm workspace to add a deployable gateway node.
# Usage:  bash <(curl -fsSL http://45.205.27.69:8080/setup-gateway-node.sh)
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

const UPSTREAM = "${UPSTREAM}";
const REMOTE_REGISTER = "${REMOTE_REGISTER}";

const MODELS = [
  "gpt-5-mini", "gpt-5", "gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini",
  "claude-opus-4-5", "claude-sonnet-4-5", "claude-3-5-haiku-20241022",
  "gemini-2.5-pro", "gemini-2.5-flash",
];

function getSelfUrl(): string {
  const domains = process.env["REPLIT_DOMAINS"];
  if (domains) return "https://" + domains.split(",")[0]!.trim();
  const dev = process.env["REPLIT_DEV_DOMAIN"];
  if (dev) return "https://" + dev;
  return "http://localhost:" + (process.env["PORT"] ?? "3000");
}

function getNodeName(): string {
  const domains = process.env["REPLIT_DOMAINS"];
  if (domains) return "Replit(" + (domains.split(",")[0]!.split(".")[0] ?? "prod") + ")";
  const dev = process.env["REPLIT_DEV_DOMAIN"]?.split(".")[0];
  return "Replit(" + (dev ?? "dev") + ")";
}

async function selfRegister() {
  const gatewayUrl = getSelfUrl() + "/api/gateway";
  try {
    const r = await fetch(REMOTE_REGISTER, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: getNodeName(), gatewayUrl, models: MODELS, priority: 5 }),
      signal: AbortSignal.timeout(10_000),
    });
    const b = await r.json().catch(() => ({})) as Record<string, unknown>;
    console.log("[register]", r.status, "nodeId=" + (b["nodeId"] ?? "?"), "url=" + gatewayUrl);
  } catch (e) {
    console.warn("[register] failed:", String(e));
  }
}

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

const app = express();
app.use(express.json({ limit: "10mb" }));

app.get("/api/healthz", (_req, res) => res.json({ ok: true, ts: Date.now() }));

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

// ── Status page ────────────────────────────────────────────────────────────────
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
<p class="sub">Replit 友节点 · 代理链路</p>
<div class="card">
  <div class="row"><span class="label">状态</span><span class="value online">online</span></div>
  <div class="row"><span class="label">节点</span><span class="value">\${d.node}</span></div>
  <div class="row"><span class="label">URL</span><span class="value">\${d.workspace}</span></div>
  <div class="row"><span class="label">上游</span><span class="value">\${UPSTREAM}</span></div>
  <div class="row"><span class="label">模式</span><span class="value">proxy</span></div>
</div>
<div class="card">
  <div class="row"><span class="label">/api/gateway/health</span><a class="value" href="/api/gateway/health" target="_blank">/api/gateway/health ↗</a></div>
  <div class="row"><span class="label">/api/gateway/v1/models</span><a class="value" href="/api/gateway/v1/models" target="_blank">/api/gateway/v1/models ↗</a></div>
</div>
<p class="ts">自注册间隔：每 5 分钟</p>
</body></html>\`);
});

const PORT = Number(process.env["PORT"] ?? 3000);
app.listen(PORT, "0.0.0.0", () => {
  console.log("[gateway-node] :" + PORT + "  self=" + getSelfUrl());
  selfRegister();
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
echo "  Done! gateway-node is ready."
echo ""
echo "  Next step: click [Publish] in the Replit UI."
echo "  The node will auto-register with the gateway"
echo "  on first boot using the production URL."
echo "======================================================"
echo ""
