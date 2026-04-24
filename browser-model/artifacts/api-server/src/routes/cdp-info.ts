import { Router, type IRouter } from "express";
import * as net from "node:net";
import { readFileSync } from "node:fs";
import type { Dispatcher } from "undici";

const router: IRouter = Router();

// Backwards-compatible static info (kept so old clients don't break)
router.get("/cdp/info", (_req, res) => {
  res.json({
    transport: "websocket",
    path: "/api/cdp/ws",
    note: "Use ws:// or wss:// upgrade. Send {type:'navigate',url} to start; receive {type:'frame',data:base64jpeg}",
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// v7.68 — broker outbound proxy introspection
//
// Pool order (matches start-browser-model.sh _pick_browser_proxy()):
//   40000 WARP   — Cloudflare backbone (MASQUE/HTTP3), best CF reputation
//   10824 Kirino — datacenter, audited 0391f15 google池
//   10826 DigitalOcean — datacenter, audited 0391f15 google池
//
// All probes are bounded by short timeouts; never block /cdp/health > 5s.
// ─────────────────────────────────────────────────────────────────────────────

interface ProxyCandidate {
  port: number;
  name: string;
}

const PROXY_POOL: ProxyCandidate[] = [
  { port: 40000, name: "WARP" },
  { port: 10824, name: "Kirino" },
  { port: 10826, name: "DigitalOcean" },
];

function tcpProbe(host: string, port: number, timeoutMs: number): Promise<boolean> {
  return new Promise((resolve) => {
    const s = net.connect(port, host);
    let done = false;
    const finish = (v: boolean) => { if (!done) { done = true; try { s.destroy(); } catch { /* ignore */ } resolve(v); } };
    s.once("connect", () => finish(true));
    s.once("error", () => finish(false));
    setTimeout(() => finish(false), timeoutMs);
  });
}

// Build a one-shot undici dispatcher that tunnels through a SOCKS5 proxy on 127.0.0.1:port.
// Lighter clone of routes/proxy.ts — no DNS pre-resolve (we hit IP literals only).
async function makeSocksDispatcher(port: number): Promise<Dispatcher> {
  const { Agent } = await import("undici");
  const { SocksClient } = await import("socks");
  const tls = await import("node:tls");
  return new Agent({
    connect: async (opts: Record<string, unknown>, cb: (err: Error | null, sock?: unknown) => void) => {
      try {
        const dstHost = String(opts.hostname);
        const isTls = opts.protocol === "https:";
        const rawPort = opts.port;
        const dstPort = rawPort && Number(rawPort) > 0 ? Number(rawPort) : (isTls ? 443 : 80);
        const { socket } = await SocksClient.createConnection({
          proxy: { host: "127.0.0.1", port, type: 5 },
          command: "connect",
          destination: { host: dstHost, port: dstPort },
          timeout: 4000,
        });
        if (isTls) {
          const tlsSocket = tls.connect({
            socket,
            servername: (opts.servername as string) || dstHost,
            ALPNProtocols: opts.ALPNProtocols as string[] | undefined,
          });
          tlsSocket.once("secureConnect", () => cb(null, tlsSocket));
          tlsSocket.once("error", (err: Error) => cb(err));
        } else {
          cb(null, socket);
        }
      } catch (err) {
        cb(err as Error);
      }
    },
  });
}

interface ExitProbe {
  port: number;
  name: string;
  listen: boolean;
  alive: boolean;
  ip: string | null;
  ms: number | null;
  error?: string;
}

// Cache the pool sweep so /cdp/health doesn't re-probe every hit. Hot path: 30s TTL.
let poolCache: { at: number; results: ExitProbe[] } | null = null;
const POOL_TTL_MS = 30_000;

async function probeOne(port: number, name: string, timeoutMs = 4000): Promise<ExitProbe> {
  const t0 = Date.now();
  const listen = await tcpProbe("127.0.0.1", port, 800);
  if (!listen) {
    return { port, name, listen: false, alive: false, ip: null, ms: null, error: "no LISTEN" };
  }
  let dispatcher: Dispatcher | undefined;
  try {
    dispatcher = await makeSocksDispatcher(port);
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), timeoutMs);
    const r = await fetch("https://1.1.1.1/cdn-cgi/trace", {
      signal: ctl.signal,
      // @ts-expect-error undici dispatcher is supported at runtime
      dispatcher,
    });
    clearTimeout(t);
    const text = await r.text();
    const ipLine = text.split("\n").find((l) => l.startsWith("ip="));
    const ip = ipLine ? ipLine.slice(3).trim() : null;
    return {
      port, name, listen: true, alive: r.ok && !!ip,
      ip, ms: Date.now() - t0,
      ...(r.ok ? {} : { error: `http ${r.status}` }),
    };
  } catch (e) {
    return {
      port, name, listen: true, alive: false, ip: null,
      ms: Date.now() - t0,
      error: String((e as Error).message ?? e).slice(0, 200),
    };
  } finally {
    try { (dispatcher as unknown as { destroy?: () => void } | undefined)?.destroy?.(); } catch { /* ignore */ }
  }
}

async function probePool(force = false): Promise<ExitProbe[]> {
  if (!force && poolCache && Date.now() - poolCache.at < POOL_TTL_MS) return poolCache.results;
  const results = await Promise.all(PROXY_POOL.map((c) => probeOne(c.port, c.name)));
  poolCache = { at: Date.now(), results };
  return results;
}

function readChromiumProcInfo(): {
  pid: number | null;
  uptimeSec: number | null;
  cmdline: { proxy?: string; userDataDir?: string; remoteDebuggingPort?: string; remoteDebuggingPipe: boolean } | null;
} {
  try {
    const fs = require("node:fs") as typeof import("node:fs");
    const all = fs.readdirSync("/proc").filter((d) => /^\d+$/.test(d));
    for (const p of all) {
      try {
        const cmd = fs.readFileSync(`/proc/${p}/cmdline`, "utf8").replace(/\0/g, " ");
        if (!cmd.includes("chrome") || !cmd.includes("--remote-debugging-port=9222")) continue;
        if (cmd.includes("--type=")) continue;
        const pid = Number.parseInt(p, 10);
        const stat = fs.readFileSync(`/proc/${p}/stat`, "utf8").split(" ");
        const startTicks = Number.parseInt(stat[21] ?? "0", 10);
        const uptime = Number.parseFloat(fs.readFileSync("/proc/uptime", "utf8").split(" ")[0] ?? "0");
        const procUptimeSec = Math.max(0, Math.round(uptime - startTicks / 100));
        const grab = (re: RegExp) => (cmd.match(re)?.[1] ?? undefined);
        return {
          pid,
          uptimeSec: procUptimeSec,
          cmdline: {
            proxy:               grab(/--proxy-server=(\S+)/),
            userDataDir:         grab(/--user-data-dir=(\S+)/),
            remoteDebuggingPort: grab(/--remote-debugging-port=(\d+)/),
            remoteDebuggingPipe: cmd.includes("--remote-debugging-pipe"),
          },
        };
      } catch { /* skip unreadable */ }
    }
  } catch { /* /proc not available */ }
  return { pid: null, uptimeSec: null, cmdline: null };
}

// Pull the running chromium's --proxy-server=socks5://127.0.0.1:PORT and look it up
// in the pool to label it. Returns null if no proxy / unknown port.
function brokerActiveProxy(): { port: number; name: string; url: string } | null {
  const proc = readChromiumProcInfo();
  const url = proc.cmdline?.proxy;
  if (!url) return null;
  const m = url.match(/^socks5h?:\/\/127\.0\.0\.1:(\d+)\/?$/i);
  if (!m) return { port: -1, name: "unknown", url };
  const port = Number(m[1]);
  const known = PROXY_POOL.find((c) => c.port === port);
  return { port, name: known?.name ?? `port-${port}`, url };
}

// ── /api/cdp/health ────────────────────────────────────────────────────────
router.get("/cdp/health", async (_req, res) => {
  const t0 = Date.now();
  const tcp = await tcpProbe("127.0.0.1", 9222, 1500);
  let http = false;
  let browser: string | undefined;
  let proto: string | undefined;
  let userAgent: string | undefined;
  let targets: number | null = null;
  let httpError: string | undefined;

  if (tcp) {
    try {
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), 2500);
      const r = await fetch("http://127.0.0.1:9222/json/version", { signal: ctl.signal });
      clearTimeout(t);
      if (r.ok) {
        const j = await r.json() as { Browser?: string; "Protocol-Version"?: string; "User-Agent"?: string };
        browser   = j.Browser;
        proto     = j["Protocol-Version"];
        userAgent = j["User-Agent"];
        http = true;
      } else {
        httpError = `http ${r.status}`;
      }
    } catch (e) {
      httpError = String((e as Error).message ?? e).slice(0, 200);
    }

    try {
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), 2500);
      const r = await fetch("http://127.0.0.1:9222/json/list", { signal: ctl.signal });
      clearTimeout(t);
      if (r.ok) {
        const arr = await r.json() as unknown[];
        targets = Array.isArray(arr) ? arr.length : null;
      }
    } catch { /* targets best-effort */ }
  }

  const proc = readChromiumProcInfo();
  const active = brokerActiveProxy();
  // Find this proxy in the cached pool sweep — bounded, never re-probes inline if cache fresh.
  const pool = await probePool(false).catch(() => [] as ExitProbe[]);
  const activeProbe = active ? pool.find((p) => p.port === active.port) ?? null : null;

  const probedMs = Date.now() - t0;
  const ok = tcp && http;

  res.status(ok ? 200 : 503).json({
    ok,
    tcp,
    http,
    httpError,
    browser,
    protocol: proto,
    userAgent,
    targets,
    chromium: proc,
    proxy: active
      ? {
          url: active.url,
          port: active.port,
          name: active.name,
          listen: activeProbe?.listen ?? null,
          alive: activeProbe?.alive ?? null,
          ip: activeProbe?.ip ?? null,
          probeMs: activeProbe?.ms ?? null,
          probeError: activeProbe?.error,
          probeFreshness: poolCache ? Date.now() - poolCache.at : null,
        }
      : null,
    probedMs,
    timestamp: new Date().toISOString(),
  });
});

// ── /api/cdp/exit-ip ───────────────────────────────────────────────────────
// Cheap: returns the cached active-proxy probe (≤30s old). ?fresh=1 forces re-probe.
router.get("/cdp/exit-ip", async (req, res) => {
  const force = String(req.query["fresh"] ?? "") === "1";
  const active = brokerActiveProxy();
  if (!active) {
    res.status(503).json({ ok: false, error: "broker chromium not running OR has no --proxy-server", proxy: null, ip: null });
    return;
  }
  const sweep = await probePool(force);
  const probe = sweep.find((p) => p.port === active.port) ?? await probeOne(active.port, active.name);
  res.status(probe.alive ? 200 : 503).json({
    ok: probe.alive,
    proxy: { url: active.url, port: active.port, name: active.name },
    ip: probe.ip,
    listen: probe.listen,
    alive: probe.alive,
    probeMs: probe.ms,
    probeError: probe.error,
    cacheAgeMs: poolCache ? Date.now() - poolCache.at : null,
    timestamp: new Date().toISOString(),
  });
});

// ── /api/cdp/probe-pool ────────────────────────────────────────────────────
// Returns full pool sweep so upstream routers can pick the best alive exit.
// ?fresh=1 forces a re-probe (otherwise cached ≤30s).
router.get("/cdp/probe-pool", async (req, res) => {
  const force = String(req.query["fresh"] ?? "") === "1";
  const sweep = await probePool(force);
  const active = brokerActiveProxy();
  // Prefer alive exits; among alive, preserve declared pool order (WARP first).
  const recommended = sweep.find((p) => p.alive)?.port ?? null;
  res.json({
    ok: true,
    active: active ? { port: active.port, name: active.name, url: active.url } : null,
    recommended,
    needsRestart: !!(active && recommended != null && active.port !== recommended),
    cacheAgeMs: poolCache ? Date.now() - poolCache.at : null,
    pool: sweep,
    timestamp: new Date().toISOString(),
  });
});

export default router;
