// nesting-pool.ts
//
// Microsoft API 请求通过 jimjio nesting-proxy Worker 路由。
// Worker: nestingproxy.qtsvwfvabe.workers.dev  (jimjio 账号，非 jimhacker)
//
// 收益:
//   - microsoftFetch / live-verify 调用完全不消耗 jimhacker 100k/天配额
//   - Worker 内置 UA 随机化，降低 Microsoft 检测概率
//   - 不依赖任何额外 npm 包 (纯 Node.js built-ins + global fetch)
//
// 路由: Node.js → POST /proxy → nestingproxy Worker → Target
// 出口 IP: CF jimjio 账号 Colo IP (SEA/LAX/SJC/…，随边缘路由分配)

import { logger } from "./logger.js";

const WORKER_HOST = "proxy.jimjio.indevs.in";
const PROXY_EP    = `https://${WORKER_HOST}/proxy`;

// ── Envelope helpers (protocol: JSON 信封) ────────────────────────────────

function buildEnvelope(targetUrl: string, init: RequestInit = {}): Record<string, unknown> {
  const method = ((init.method ?? "GET") as string).toUpperCase();
  const env: Record<string, unknown> = {
    target_url: targetUrl,
    method,
    redirect: (init as Record<string, unknown>)["redirect"] ?? "follow",
  };
  if (init.headers) {
    env["headers"] = init.headers instanceof Headers
      ? Object.fromEntries(init.headers.entries())
      : { ...(init.headers as Record<string, string>) };
  }
  if (method !== "GET" && method !== "HEAD" && init.body != null) {
    env["body"] = String(init.body);
  }
  return env;
}

interface WorkerEnvelope {
  status: number;
  headers: Record<string, string>;
  body: string;
  cf_colo?: string;
}

function envelopeToResponse(json: WorkerEnvelope): Response {
  const hdrs = new Headers();
  for (const [k, v] of Object.entries(json.headers ?? {})) {
    try { hdrs.set(k, String(v)); } catch { /* skip malformed headers */ }
  }
  return new Response(json.body ?? "", { status: json.status, headers: hdrs });
}

// ── Circuit breaker ───────────────────────────────────────────────────────

let _circuitUntil = 0;

function circuitOpen(): boolean { return Date.now() < _circuitUntil; }
function tripCircuit(): void { _circuitUntil = Date.now() + 60_000; }

function isConnectError(err: unknown): boolean {
  const msg = String((err as { message?: string })?.message ?? err ?? "").toLowerCase();
  return (
    msg.includes("fetch failed") ||
    msg.includes("econnrefused") ||
    msg.includes("econnreset") ||
    msg.includes("socket hang up") ||
    msg.includes("timeout") ||
    msg.includes("network error")
  );
}

// ── Main fetch ────────────────────────────────────────────────────────────

let _totalRequests = 0;
let _totalErrors   = 0;
let _lastColo      = "unknown";

/**
 * Route a fetch through the jimjio nesting-proxy Worker.
 * Returns a Web API Response identical to what the target URL returns.
 * Throws on network/circuit error so the caller can fall back.
 */
export async function nestingFetch(
  input: Parameters<typeof fetch>[0],
  init: RequestInit = {},
): Promise<Response> {
  if (circuitOpen()) throw new Error("nesting: circuit open");

  _totalRequests++;
  const envelope = buildEnvelope(String(input), init);

  try {
    const res = await fetch(PROXY_EP, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(envelope),
      signal: AbortSignal.timeout(28_000),
    });

    if (!res.ok) throw new Error(`nesting Worker HTTP ${res.status}`);

    const json = await res.json() as WorkerEnvelope;
    _lastColo = json.cf_colo ?? "unknown";
    return envelopeToResponse(json);
  } catch (err) {
    _totalErrors++;
    if (isConnectError(err)) tripCircuit();
    throw err;
  }
}

// ── Startup + status ──────────────────────────────────────────────────────

let _started = false;

/** Called once at server startup. Does a quick health-check of the Worker. */
export function startNestingPool(): void {
  if (_started) return;
  _started = true;

  // Health check: non-blocking, just logs
  fetch(`https://${WORKER_HOST}/health`, { signal: AbortSignal.timeout(10_000) })
    .then((r) => r.json())
    .then((j) => logger.info({ worker: WORKER_HOST, health: j }, "[nesting-pool] Worker 健康"))
    .catch((e) => logger.warn({ err: String(e) }, "[nesting-pool] Worker 健康检查失败"));
}

export function getNestingPoolStatus() {
  return {
    workerHost: WORKER_HOST,
    circuitOpen: circuitOpen(),
    circuitUntil: _circuitUntil ? new Date(_circuitUntil).toISOString() : null,
    totalRequests: _totalRequests,
    totalErrors: _totalErrors,
    lastColo: _lastColo,
  };
}
