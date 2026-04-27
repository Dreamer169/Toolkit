import { Buffer } from "buffer";
import { execFileSync } from "child_process";
import { ProxyAgent, type Dispatcher } from "undici";

// ============================================================================
// Microsoft outbound HTTP proxy resolver
// ----------------------------------------------------------------------------
// History: pre-v8.36 hard-coded 127.0.0.1:8091 as DEFAULT_MICROSOFT_HTTP_PROXY,
// plus a `localProxyToken` helper that injected an auth token (env / proc /
// "replproxy2024" literal) for that one port. The :8091 listener has been
// gone for weeks (`ss -tnlp` shows nothing, `nc -z` exits 1). Every consumer
// — microsoftFetch (undici dispatcher) and getMicrosoftBrowserProxy (handed
// to patchright via subprocess argv) — therefore tried to use a dead proxy,
// which broke automatic device-code OAuth for all needs_oauth accounts and
// poisoned every microsoftFetch call with ECONNREFUSED before the circuit
// breaker tripped.
//
// v8.36 cleanup: 8091 / CONNECT_PROXY_TOKEN / replproxy2024 are all gone.
// All paths now resolve through a single liveness-probed pool (xray HTTP
// inbounds 10820..10829, all verified alive). Empty result → direct fetch.
// ============================================================================

const BROWSER_PROXY_ENV_KEYS = [
  "MICROSOFT_BROWSER_PROXY",
  "OUTLOOK_BROWSER_PROXY",
  "MICROSOFT_HTTP_PROXY",
  "OUTLOOK_HTTP_PROXY",
  "LIVE_VERIFY_HTTP_PROXY",
];
const HTTP_PROXY_ENV_KEYS = [
  "MICROSOFT_HTTP_PROXY",
  "OUTLOOK_HTTP_PROXY",
  "LIVE_VERIFY_HTTP_PROXY",
];
const XRAY_LOCAL_PORTS = [10820, 10821, 10822, 10823, 10824, 10825, 10826, 10827, 10828, 10829];

type RequestInitWithDispatcher = RequestInit & { dispatcher?: Dispatcher };

function firstEnv(keys: string[]): string | null {
  for (const key of keys) {
    const val = process.env[key]?.trim();
    if (val) return val;
  }
  return null;
}

function withScheme(proxy: string): string {
  return /^https?:\/\//i.test(proxy) || /^socks[45]?:\/\//i.test(proxy) ? proxy : `http://${proxy}`;
}

// nc -z exit code is accurate (0=open, 1=refused/closed). Bash `</dev/tcp/...`
// is unreliable: the trailing `exec 3>&-` always returns 0 even when connect
// failed, so every port looked alive (root cause of the v8.35 false-positive).
function probeTcpAlive(host: string, port: number, timeoutMs = 1500): boolean {
  try {
    execFileSync("nc", ["-z", "-w", "1", host, String(port)], { timeout: timeoutMs, stdio: "ignore" });
    return true;
  } catch { return false; }
}

let _liveProxyCache: { value: string; expires: number } | null = null;

function pickLiveProxy(): string {
  const now = Date.now();
  if (_liveProxyCache && now < _liveProxyCache.expires) return _liveProxyCache.value;
  for (const port of XRAY_LOCAL_PORTS) {
    if (probeTcpAlive("127.0.0.1", port)) {
      const value = `http://127.0.0.1:${port}`;
      _liveProxyCache = { value, expires: now + 60_000 };
      return value;
    }
  }
  _liveProxyCache = { value: "", expires: now + 30_000 };
  return "";
}

function resolveProxyUrl(preferred?: string | null, envKeys: string[] = HTTP_PROXY_ENV_KEYS): string {
  const explicit = preferred?.trim() || firstEnv(envKeys);
  if (explicit) {
    const url = withScheme(explicit);
    // SOCKS not supported by undici ProxyAgent — fall through to live pool.
    if (/^socks[45]?:\/\//i.test(url)) return pickLiveProxy();
    return url;
  }
  return pickLiveProxy();
}

function createProxyAgent(preferred?: string | null): ProxyAgent | undefined {
  const proxy = resolveProxyUrl(preferred, HTTP_PROXY_ENV_KEYS);
  if (!proxy) return undefined;
  const url = new URL(proxy);
  const username = decodeURIComponent(url.username || "");
  const password = decodeURIComponent(url.password || "");
  const token = username || password
    ? `Basic ${Buffer.from(`${username}:${password}`).toString("base64")}`
    : undefined;
  const uri = `${url.protocol}//${url.hostname}${url.port ? `:${url.port}` : ""}`;
  return new ProxyAgent({ uri, token } as ConstructorParameters<typeof ProxyAgent>[0]);
}

// Circuit breaker: if a proxy fetch hits a connection-level error, skip the
// proxy for 60s and direct-connect so live-verify / token refresh do not
// cascade-fail every account on a transient outage.
let circuitOpenUntil = 0;
function isProxyConnectError(err: unknown): boolean {
  const msg = String((err as { message?: string })?.message ?? err ?? "").toLowerCase();
  const cause = String(((err as { cause?: { code?: string; message?: string } })?.cause?.code) ?? ((err as { cause?: { code?: string; message?: string } })?.cause?.message) ?? "").toLowerCase();
  return (
    msg.includes("fetch failed") ||
    cause.includes("econnrefused") ||
    cause.includes("econnreset") ||
    cause.includes("socket hang up") ||
    cause.includes("other side closed")
  );
}

export async function microsoftFetch(input: Parameters<typeof fetch>[0], init: RequestInit = {}, preferredProxy?: string | null): Promise<Response> {
  const now = Date.now();
  if (now < circuitOpenUntil) return fetch(input, init);
  const dispatcher = createProxyAgent(preferredProxy);
  if (!dispatcher) return fetch(input, init);
  try {
    return await fetch(input, { ...init, dispatcher } as RequestInitWithDispatcher);
  } catch (err) {
    if (isProxyConnectError(err)) {
      circuitOpenUntil = Date.now() + 60_000;
      _liveProxyCache = null; // force re-probe next call
      return fetch(input, init);
    }
    throw err;
  }
}

export function getMicrosoftBrowserProxy(preferred?: string | null): string {
  return resolveProxyUrl(preferred, BROWSER_PROXY_ENV_KEYS);
}

export function getMicrosoftProxyEnv(preferred?: string | null): Record<string, string> {
  const proxy = getMicrosoftBrowserProxy(preferred);
  if (!proxy) return {};
  return { HTTP_PROXY: proxy, HTTPS_PROXY: proxy, http_proxy: proxy, https_proxy: proxy };
}
