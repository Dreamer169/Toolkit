// nesting-pool.ts v2.0
//
// 4-Worker 轮询池（round-robin + per-worker circuit breaker）:
//   CF-ORIG-1  proxy.jimjio.indevs.in      (original)
//   CF-ORIG-2  proxy.jimjon.eu.cc          (new)
//   CF-NEW-3   proxy.jonjim.indevs.in      (new)
//   CF-NEW-4   proxy.hackerjim.indevs.in   (new)
//
// 路由: Node.js → POST /proxy → nestingproxy Worker → Target
// 协议: JSON 信封 { target_url, method, headers?, body?, json_body? }
// 响应: JSON 信封 { status, headers, body, cf_colo? }

import { logger } from "./logger.js";

// ── Worker pool ───────────────────────────────────────────────────────────

const WORKERS: readonly string[] = [
  "proxy.jimjio.indevs.in",
  "proxy.jimjon.eu.cc",
  "proxy.jonjim.indevs.in",
  "proxy.hackerjim.indevs.in",
];

const _circuitUntil = new Array<number>(WORKERS.length).fill(0);
const _errors       = new Array<number>(WORKERS.length).fill(0);
const _requests     = new Array<number>(WORKERS.length).fill(0);
const _colos        = new Array<string>(WORKERS.length).fill("unknown");
let   _rrIndex      = 0;

function pickWorker(): { host: string; idx: number } {
  const now = Date.now();
  for (let i = 0; i < WORKERS.length; i++) {
    const idx = (_rrIndex + i) % WORKERS.length;
    if (now >= _circuitUntil[idx]) {
      _rrIndex = (idx + 1) % WORKERS.length;
      return { host: WORKERS[idx], idx };
    }
  }
  // 全部 circuit open → 重置最旧的
  let oldest = 0;
  for (let i = 1; i < WORKERS.length; i++) {
    if (_circuitUntil[i] < _circuitUntil[oldest]) oldest = i;
  }
  _circuitUntil[oldest] = 0;
  _rrIndex = (oldest + 1) % WORKERS.length;
  return { host: WORKERS[oldest], idx: oldest };
}

function tripCircuit(idx: number): void { _circuitUntil[idx] = Date.now() + 60_000; }

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

// ── Envelope helpers ──────────────────────────────────────────────────────

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
    const bodyStr = String(init.body);
    const hdrs = init.headers instanceof Headers
      ? init.headers
      : new Headers(init.headers as Record<string, string>);
    const ct = hdrs.get("content-type") ?? "";
    if (ct.includes("application/json")) {
      try { env["json_body"] = JSON.parse(bodyStr); } catch { env["body"] = bodyStr; }
    } else {
      env["body"] = bodyStr;
    }
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
    try { hdrs.set(k, String(v)); } catch { /* skip */ }
  }
  return new Response(json.body ?? "", { status: json.status, headers: hdrs });
}

// ── Main fetch ────────────────────────────────────────────────────────────

/**
 * 通过 nesting-proxy Worker 池发起请求（round-robin，自动故障转移）。
 * 行为与 global fetch() 相同，调用方无感知。
 * 全部 Worker 不可用时抛出，调用方应 fallback 到直连。
 */
export async function nestingFetch(
  input: Parameters<typeof fetch>[0],
  init: RequestInit = {},
): Promise<Response> {
  const now = Date.now();
  for (let attempt = 0; attempt < WORKERS.length; attempt++) {
    const { host, idx } = pickWorker();
    if (now < _circuitUntil[idx]) continue;

    _requests[idx]++;
    const envelope = buildEnvelope(String(input), init);

    try {
      const res = await fetch(`https://${host}/proxy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(envelope),
        signal: AbortSignal.timeout(28_000),
      });

      if (!res.ok) throw new Error(`nesting Worker HTTP ${res.status}`);

      const json = await res.json() as WorkerEnvelope;
      _colos[idx] = json.cf_colo ?? "unknown";
      return envelopeToResponse(json);
    } catch (err) {
      _errors[idx]++;
      if (isConnectError(err)) tripCircuit(idx);
      logger.warn({ err: String(err), host, attempt }, "[nesting-pool] worker error, trying next");
    }
  }
  throw new Error("nesting-pool: all workers unavailable");
}

// ── Startup + status ──────────────────────────────────────────────────────

let _started = false;

export function startNestingPool(): void {
  if (_started) return;
  _started = true;
  for (let i = 0; i < WORKERS.length; i++) {
    const host = WORKERS[i];
    fetch(`https://${host}/health`, { signal: AbortSignal.timeout(10_000) })
      .then((r) => r.json())
      .then((j) => logger.info({ worker: host, health: j }, "[nesting-pool] Worker healthy"))
      .catch((e) => logger.warn({ err: String(e), worker: host }, "[nesting-pool] Worker health check failed"));
  }
}

export function getNestingPoolStatus() {
  return WORKERS.map((host, i) => ({
    host,
    circuitOpen: Date.now() < _circuitUntil[i],
    circuitUntil: _circuitUntil[i] ? new Date(_circuitUntil[i]).toISOString() : null,
    totalRequests: _requests[i],
    totalErrors: _errors[i],
    lastColo: _colos[i],
  }));
}
