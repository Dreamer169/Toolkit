const http = require('http');
const { exec } = require('child_process');
const fs = require('fs');

const TOKEN = process.env.EXEC_SECRET || process.env.EXEC_TOKEN || 'zencoder-exec-2026';
const DEFAULT_CWD = fs.existsSync('/root/Toolkit') ? '/root/Toolkit' : '/workspaces/Toolkit';
const PORT = Number(process.env.EXEC_PORT || 9999);

const server = http.createServer((req, res) => {
  if (req.method === 'GET' && req.url === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ success: true, cwd: DEFAULT_CWD, time: new Date().toISOString() }));
    return;
  }

  if (req.method !== 'POST' || req.url !== '/exec') {
    res.writeHead(404); res.end('Not found'); return;
  }
  const auth = req.headers['x-token'];
  if (auth !== TOKEN) {
    res.writeHead(401); res.end('Unauthorized'); return;
  }
  let body = '';
  req.on('data', d => body += d);
  req.on('end', () => {
    try {
      const { cmd, cwd, timeout } = JSON.parse(body || '{}');
      if (!cmd || typeof cmd !== 'string') {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ success: false, error: 'cmd is required' }));
        return;
      }
      exec(cmd, { cwd: cwd || DEFAULT_CWD, timeout: Math.min(Number(timeout) || 120000, 300000), maxBuffer: 10 * 1024 * 1024 }, (err, stdout, stderr) => {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ success: !err, code: err ? err.code || 1 : 0, stdout, stderr }));
      });
    } catch (err) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ success: false, error: String(err) }));
    }
  });
});

function startServer(retries = 5) {
  server.listen(PORT, '0.0.0.0', () => {
    console.log('Remote exec server running on port ' + PORT);
  });
}

server.on('error', (err) => {
  if (err.code === 'EADDRINUSE' && arguments[0] !== 0) {
    console.error('Port ' + PORT + ' in use, retrying in 3s...');
    setTimeout(() => {
      server.close();
      startServer();
    }, 3000);
  } else {
    console.error('Server error:', err.message);
    process.exit(1);
  }
});

process.on('SIGTERM', () => { server.close(() => process.exit(0)); });
process.on('SIGINT',  () => { server.close(() => process.exit(0)); });

startServer();
