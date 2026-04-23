// Per-host Google routing — bypass WARP→GCP datacenter IPs that tank
// reCAPTCHA Enterprise scores. Replit.com / CF-challenge traffic stays
// on WARP (needed for cf_clearance), but *.google.com / *.gstatic.com /
// *.recaptcha.net / *.youtube.com requests are diverted through a pool
// of vetted non-GCP SOCKS5 exits (xray subnodes 10820+).
//
// Activation: call attachGoogleProxyRouting(context) on any
// BrowserContext after creation. Idempotent; safe to call once per ctx.
import { Agent, fetch as undiciFetch } from "undici";
import { SocksClient } from "socks";
import * as tls from "node:tls";
import type { BrowserContext } from "playwright";

const DEFAULT_POOL = [
  "socks5://127.0.0.1:10820",
  "socks5://127.0.0.1:10822",
  "socks5://127.0.0.1:10823",
  "socks5://127.0.0.1:10824",
  "socks5://127.0.0.1:10825",
  "socks5://127.0.0.1:10826",
  "socks5://127.0.0.1:10828",
  "socks5://127.0.0.1:10830",
  "socks5://127.0.0.1:10831",
  "socks5://127.0.0.1:10836",
  "socks5://127.0.0.1:10837",
  "socks5://127.0.0.1:10845",
];

function loadPool(): URL[] {
  const raw = (process.env.GOOGLE_PROXY_POOL || DEFAULT_POOL.join(","))
    .split(",").map((s) => s.trim()).filter(Boolean);
  const out: URL[] = [];
  for (const s of raw) {
    try { out.push(new URL(s)); } catch { /* */ }
  }
  return out.length ? out : DEFAULT_POOL.map((s) => new URL(s));
}

const POOL: URL[] = loadPool();
let cursor = 0;
function pickProxy(): URL {
  const p = POOL[cursor % POOL.length];
  cursor = (cursor + 1) % Math.max(POOL.length, 1);
  return p;
}

const GOOGLE_HOST_RE =
  /(^|\.)(google\.com|gstatic\.com|recaptcha\.net|youtube\.com|googleapis\.com|googleusercontent\.com|googletagmanager\.com|googleadservices\.com|google-analytics\.com|doubleclick\.net|ytimg\.com)$/i;

function makeSocksAgent(proxy: URL): Agent {
  return new Agent({
    connect: (opts: any, callback: any) => {
      const targetHost: string = opts.hostname || opts.host;
      const targetPort: number = Number(opts.port) || (opts.protocol === "http:" ? 80 : 443);
      SocksClient.createConnection({
        proxy: {
          host: proxy.hostname,
          port: Number(proxy.port) || 1080,
          type: 5,
        },
        command: "connect",
        destination: { host: targetHost, port: targetPort },
        timeout: 10000,
      }).then(({ socket }) => {
        if (opts.protocol === "https:") {
          const tlsSock = tls.connect({
            socket,
            servername: opts.servername || targetHost,
            ALPNProtocols: ["h2", "http/1.1"],
            rejectUnauthorized: false,
          });
          tlsSock.once("secureConnect", () => callback(null, tlsSock));
          tlsSock.once("error", (e) => callback(e));
        } else {
          callback(null, socket);
        }
      }).catch((e: Error) => callback(e));
    },
    connectTimeout: 12000,
    headersTimeout: 25000,
    bodyTimeout: 25000,
    pipelining: 1,
    allowH2: true,
  });
}

const agentCache = new Map<string, Agent>();
function getAgent(proxy: URL): Agent {
  const key = proxy.toString();
  let a = agentCache.get(key);
  if (!a) { a = makeSocksAgent(proxy); agentCache.set(key, a); }
  return a;
}

export interface GoogleRouteStats {
  proxied: number;
  failed: number;
  bypassed: number;
}

export async function attachGoogleProxyRouting(
  ctx: BrowserContext,
  stats?: GoogleRouteStats,
): Promise<void> {
  await ctx.route("**/*", async (route, request) => {
    let u: URL;
    try { u = new URL(request.url()); } catch { return route.fallback(); }
    if (!GOOGLE_HOST_RE.test(u.hostname)) {
      if (stats) stats.bypassed++;
      return route.fallback();
    }
    const proxy = pickProxy();
    try {
      const agent = getAgent(proxy);
      const headers: Record<string, string> = {};
      const all = await request.allHeaders();
      for (const [k, v] of Object.entries(all)) {
        const lk = k.toLowerCase();
        if (lk === "accept-encoding" || lk === "host" || lk === "connection" ||
            lk === "content-length" || lk.startsWith(":")) continue;
        headers[k] = v as string;
      }
      const body = request.postDataBuffer();
      const init: any = {
        method: request.method(),
        headers,
        dispatcher: agent,
        redirect: "manual",
      };
      if (body && body.length) init.body = body;
      const resp = await undiciFetch(u.toString(), init);
      const buf = Buffer.from(await resp.arrayBuffer());
      const respHeaders: Record<string, string> = {};
      resp.headers.forEach((v, k) => {
        const lk = k.toLowerCase();
        if (lk === "content-encoding" || lk === "content-length" ||
            lk === "transfer-encoding" || lk === "connection") return;
        respHeaders[k] = v;
      });
      if (stats) stats.proxied++;
      await route.fulfill({
        status: resp.status,
        headers: respHeaders,
        body: buf,
      });
    } catch (e) {
      if (stats) stats.failed++;
      console.error("[google-route]", u.hostname, "via", proxy.toString(), "->", (e as Error).message);
      try { await route.fallback(); } catch { /* route already handled */ }
    }
  });
}

export function googleProxyPoolInfo(): { pool: string[]; size: number } {
  return { pool: POOL.map((u) => u.toString()), size: POOL.length };
}
