import http from 'http';
import { exec } from 'child_process';

const PORT = process.env.PM2_STATUS_PORT || 8084;

const server = http.createServer((req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Content-Type', 'application/json');
  if (req.url === '/processes' || req.url === '/') {
    exec('pm2 jlist', { timeout: 8000 }, (err, stdout) => {
      if (err) {
        res.writeHead(200);
        res.end(JSON.stringify({ success: false, processes: [], error: err.message }));
        return;
      }
      try {
        const raw = JSON.parse(stdout.trim());
        const processes = raw.map(p => ({
          name: p.name,
          status: p.pm2_env?.status || 'unknown',
          pid: p.pid,
          pm_id: p.pm_id,
          uptime: p.pm2_env?.pm_uptime,
          restarts: p.pm2_env?.restart_time || 0,
          cpu: p.monit?.cpu || 0,
          memory: p.monit?.memory || 0,
        }));
        res.writeHead(200);
        res.end(JSON.stringify({ success: true, processes }));
      } catch (e) {
        res.writeHead(200);
        res.end(JSON.stringify({ success: false, processes: [], error: 'parse error' }));
      }
    });
  } else {
    res.writeHead(404);
    res.end(JSON.stringify({ error: 'not found' }));
  }
});



// ── 进程控制接口 ────────────────────────────────────────────────────────────
import { execSync } from "child_process";

const origListeners = server.rawListeners("request");
server.removeAllListeners("request");

server.on("request", (req, res) => {
  const url = new URL(req.url, "http://localhost");
  const pathname = url.pathname;

  // CORS preflight
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Content-Type", "application/json");
  if (req.method === "OPTIONS") { res.end(); return; }

  // POST /restart/:name
  if (req.method === "POST" && pathname.startsWith("/restart/")) {
    const rawName = decodeURIComponent(pathname.slice(9));
    const name = rawName.replace(/[^a-zA-Z0-9_.\-]/g, "");
    try {
      execSync("pm2 restart " + name + " --update-env", { timeout: 12000 });
      res.end(JSON.stringify({ success: true, action: "restart", name }));
    } catch(e) {
      res.statusCode = 500;
      res.end(JSON.stringify({ success: false, error: String(e.message) }));
    }
    return;
  }

  // POST /stop/:name
  if (req.method === "POST" && pathname.startsWith("/stop/")) {
    const rawName = decodeURIComponent(pathname.slice(6));
    const name = rawName.replace(/[^a-zA-Z0-9_.\-]/g, "");
    try {
      execSync("pm2 stop " + name, { timeout: 12000 });
      res.end(JSON.stringify({ success: true, action: "stop", name }));
    } catch(e) {
      res.statusCode = 500;
      res.end(JSON.stringify({ success: false, error: String(e.message) }));
    }
    return;
  }


  // POST /start/:name
  if (req.method === "POST" && pathname.startsWith("/start/")) {
    const rawName = decodeURIComponent(pathname.slice(7));
    const name = rawName.replace(/[^a-zA-Z0-9_.\-]/g, "");
    try {
      execSync("pm2 start " + name + " --update-env", { timeout: 12000 });
      res.end(JSON.stringify({ success: true, action: "start", name }));
    } catch(e) {
      res.statusCode = 500;
      res.end(JSON.stringify({ success: false, error: String(e.message) }));
    }
    return;
  }
  // 转发到原有监听器
  for (const listener of origListeners) {
    listener.call(server, req, res);
  }
});

server.listen(PORT, '127.0.0.1', () => {
  console.log('PM2 status server listening on port ' + PORT);
});
