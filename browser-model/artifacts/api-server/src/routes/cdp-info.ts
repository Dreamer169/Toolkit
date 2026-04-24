import { Router, type IRouter } from "express";
import * as net from "node:net";
import { readFileSync } from "node:fs";

const router: IRouter = Router();

// Backwards-compatible static info (kept so old clients don't break)
router.get("/cdp/info", (_req, res) => {
  res.json({
    transport: "websocket",
    path: "/api/cdp/ws",
    note: "Use ws:// or wss:// upgrade. Send {type:'navigate',url} to start; receive {type:'frame',data:base64jpeg}",
  });
});

// ── /api/cdp/health ────────────────────────────────────────────────────────
// Probes the broker-managed chromium on 127.0.0.1:9222.
// Exposes:
//   ok            — overall CDP HTTP reachable
//   tcp           — :9222 TCP listen
//   http          — /json/version returns 200
//   browser/proto — Chrome/Protocol-Version strings
//   chromium      — pid, uptime, cmdline summary (proxy/user-data-dir/pipe)
//   targets       — /json/list count (open pages)
//   probedMs      — round-trip latency
async function tcpProbe(host: string, port: number, timeoutMs: number): Promise<boolean> {
  return new Promise((resolve) => {
    const s = net.connect(port, host);
    let done = false;
    const finish = (v: boolean) => { if (!done) { done = true; try { s.destroy(); } catch { /* ignore */ } resolve(v); } };
    s.once("connect", () => finish(true));
    s.once("error",   () => finish(false));
    setTimeout(() => finish(false), timeoutMs);
  });
}

function readChromiumProcInfo(): {
  pid: number | null;
  uptimeSec: number | null;
  cmdline: { proxy?: string; userDataDir?: string; remoteDebuggingPort?: string; remoteDebuggingPipe: boolean } | null;
} {
  try {
    const pids = readFileSync("/proc/self/status", "utf8") ? [] : [];
    void pids;
    const procFs = "/proc";
    const fs = require("node:fs") as typeof import("node:fs");
    const all = fs.readdirSync(procFs).filter((d) => /^\d+$/.test(d));
    for (const p of all) {
      try {
        const cmd = fs.readFileSync(`${procFs}/${p}/cmdline`, "utf8").replace(/\0/g, " ");
        if (!cmd.includes("chrome") || !cmd.includes("--remote-debugging-port=9222")) continue;
        if (cmd.includes("--type=")) continue; // skip renderer/zygote children
        const pid = Number.parseInt(p, 10);
        const stat = fs.readFileSync(`${procFs}/${p}/stat`, "utf8").split(" ");
        const startTicks = Number.parseInt(stat[21] ?? "0", 10);
        const ticksPerSec = 100;
        const uptime = Number.parseFloat(fs.readFileSync("/proc/uptime", "utf8").split(" ")[0] ?? "0");
        const procUptimeSec = Math.max(0, Math.round(uptime - startTicks / ticksPerSec));
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
    probedMs,
    timestamp: new Date().toISOString(),
  });
});

export default router;
