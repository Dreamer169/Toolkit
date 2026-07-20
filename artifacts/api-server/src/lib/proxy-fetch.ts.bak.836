import { Buffer } from "buffer";
import { readFileSync, readdirSync } from "fs";
import { ProxyAgent, type Dispatcher } from "undici";

const DEFAULT_MICROSOFT_HTTP_PROXY = "http://127.0.0.1:8091";
const HTTP_PROXY_ENV_KEYS = ["MICROSOFT_HTTP_PROXY", "OUTLOOK_HTTP_PROXY", "LIVE_VERIFY_HTTP_PROXY"];
const BROWSER_PROXY_ENV_KEYS = ["MICROSOFT_BROWSER_PROXY", "OUTLOOK_BROWSER_PROXY", "MICROSOFT_HTTP_PROXY", "OUTLOOK_HTTP_PROXY", "LIVE_VERIFY_HTTP_PROXY"];

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

function readConnectProxyTokenFromProc(): string | null {
  try {
    for (const pid of readdirSync("/proc")) {
      if (!/^\d+$/.test(pid)) continue;
      let cmd = "";
      try { cmd = readFileSync(`/proc/${pid}/cmdline`, "utf8"); } catch { continue; }
      if (!cmd.includes("http_connect_proxy.py")) continue;
      const env = readFileSync(`/proc/${pid}/environ`, "utf8").split("\0");
      const row = env.find((item) => item.startsWith("CONNECT_PROXY_TOKEN="));
      if (row) return row.slice("CONNECT_PROXY_TOKEN=".length);
    }
  } catch {}
  return null;
}

function localProxyToken(url: URL): string | null {
  const isLocal = ["127.0.0.1", "localhost"].includes(url.hostname) && url.port === "8091";
  if (!isLocal) return null;
  return process.env["CONNECT_PROXY_TOKEN"] || process.env["SESSION_SECRET"] || readConnectProxyTokenFromProc() || "replproxy2024";
}

function basicToken(username: string, password: string): string {
  return `Basic ${Buffer.from(`${username}:${password}`).toString("base64")}`;
}

function resolveHttpProxy(preferred?: string | null): string | null {
  const raw = preferred?.trim() || firstEnv(HTTP_PROXY_ENV_KEYS) || DEFAULT_MICROSOFT_HTTP_PROXY;
  if (!raw) return null;
  const proxy = withScheme(raw);
  if (/^socks[45]?:\/\//i.test(proxy)) return firstEnv(HTTP_PROXY_ENV_KEYS) || DEFAULT_MICROSOFT_HTTP_PROXY;
  return proxy;
}

function createProxyAgent(preferred?: string | null): ProxyAgent | undefined {
  const proxy = resolveHttpProxy(preferred);
  if (!proxy) return undefined;
  const url = new URL(proxy);
  const username = decodeURIComponent(url.username || "");
  const password = decodeURIComponent(url.password || "");
  const fallbackToken = localProxyToken(url);
  const token = username || password ? basicToken(username, password) : fallbackToken ? basicToken("", fallbackToken) : undefined;
  const uri = `${url.protocol}//${url.hostname}${url.port ? `:${url.port}` : ""}`;
  return new ProxyAgent({ uri, token } as ConstructorParameters<typeof ProxyAgent>[0]);
}

// 本地 8091 CONNECT 代理偶尔挂掉（pm2 未起 / 端口被占 / 子进程崩溃）。
// 之前实现是"代理失败 → 全部 fetch failed"，导致 live-verify 轮询每 10 秒
// 给所有账号刷一遍 ECONNREFUSED 错误，污染日志。改为：
//   1) 默认走代理；
//   2) 一旦遇到连接级错误（ECONNREFUSED / fetch failed 等），打开 60 秒熔断，
//      期间所有 microsoftFetch 直连 fetch（不带 dispatcher）；
//   3) 熔断到期后再尝试代理，恢复正常即继续走代理。
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
  // 熔断打开期间：直连，跳过代理
  if (now < circuitOpenUntil) return fetch(input, init);
  const dispatcher = createProxyAgent(preferredProxy);
  if (!dispatcher) return fetch(input, init);
  try {
    return await fetch(input, { ...init, dispatcher } as RequestInitWithDispatcher);
  } catch (err) {
    if (isProxyConnectError(err)) {
      circuitOpenUntil = Date.now() + 60_000;
      // 直连兜底，让 live-verify / refreshToken 等不再连环失败
      return fetch(input, init);
    }
    throw err;
  }
}

// ── Liveness-aware proxy resolver (v8.36 fix for needs_oauth_manual loop) ──
// Issue: DEFAULT_MICROSOFT_HTTP_PROXY=:8091 has had no listener for months.
// auto_device_code.py / auto OAuth subprocess receives the dead URL and
// patchright fails with ERR_PROXY_CONNECTION_FAILED -> account stuck in
// needs_oauth_manual forever. We probe candidates and cache the live one.
import { execFileSync } from "child_process";
const XRAY_LOCAL_PORTS = [10820, 10821, 10822, 10823, 10824, 10825, 10826, 10827, 10828, 10829];
let _liveProxyCache: { value: string; expires: number } | null = null;

function probeTcpAlive(host: string, port: number, timeoutMs = 1500): boolean {
  // nc -z exit code is accurate (0=open, 1=refused/closed). Bash /dev/tcp
  // is unreliable because the trailing "exec 3>&-" always returns 0 even
  // when the connect failed, causing every port to look alive.
  try {
    execFileSync("nc", ["-z", "-w", "1", host, String(port)],
      { timeout: timeoutMs, stdio: "ignore" });
    return true;
  } catch { return false; }
}

function pickLiveBrowserProxy(): string {
  const now = Date.now();
  if (_liveProxyCache && now < _liveProxyCache.expires) return _liveProxyCache.value;
  // Candidate order: legacy 8091 first (auth-token CONNECT proxy if present), then xray HTTP pool
  const candidates: string[] = [
    "http://127.0.0.1:8091",
    ...XRAY_LOCAL_PORTS.map((p) => `http://127.0.0.1:${p}`),
  ];
  for (const cand of candidates) {
    const u = new URL(cand);
    if (probeTcpAlive(u.hostname, parseInt(u.port, 10))) {
      _liveProxyCache = { value: cand, expires: now + 60_000 };
      return cand;
    }
  }
  _liveProxyCache = { value: "", expires: now + 30_000 };
  return "";
}

export function getMicrosoftBrowserProxy(preferred?: string | null): string {
  const explicit = preferred?.trim() || firstEnv(BROWSER_PROXY_ENV_KEYS);
  if (explicit) return withScheme(explicit);
  return pickLiveBrowserProxy() || DEFAULT_MICROSOFT_HTTP_PROXY;
}

export function getMicrosoftProxyEnv(preferred?: string | null): Record<string, string> {
  const proxy = getMicrosoftBrowserProxy(preferred);
  if (!proxy) return {};
  const url = new URL(proxy);
  if (!url.username && !url.password) {
    const token = localProxyToken(url);
    if (token) url.password = token;
  }
  const value = url.toString();
  // 8091 (legacy CONNECT proxy) requires auth-token; when alive (probed) it works.
  // When dead, pickLiveBrowserProxy already routed us to a live xray port → safe.
  return { HTTP_PROXY: value, HTTPS_PROXY: value, http_proxy: value, https_proxy: value };
}
