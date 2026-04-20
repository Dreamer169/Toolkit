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

export async function microsoftFetch(input: RequestInfo | URL, init: RequestInit = {}, preferredProxy?: string | null): Promise<Response> {
  const dispatcher = createProxyAgent(preferredProxy);
  if (!dispatcher) return fetch(input, init);
  return fetch(input, { ...init, dispatcher } as RequestInitWithDispatcher);
}

export function getMicrosoftBrowserProxy(preferred?: string | null): string {
  return withScheme(preferred?.trim() || firstEnv(BROWSER_PROXY_ENV_KEYS) || DEFAULT_MICROSOFT_HTTP_PROXY);
}

export function getMicrosoftProxyEnv(preferred?: string | null): Record<string, string> {
  const proxy = getMicrosoftBrowserProxy(preferred);
  const url = new URL(proxy);
  if (!url.username && !url.password) {
    const token = localProxyToken(url);
    if (token) url.password = token;
  }
  const value = url.toString();
  return { HTTP_PROXY: value, HTTPS_PROXY: value, http_proxy: value, https_proxy: value };
}
