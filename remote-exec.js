const http = require('http');
const { exec } = require('child_process');
const fs = require('fs');

const TOKEN = process.env.EXEC_TOKEN || 'zencoder-exec-2026';
const DEFAULT_CWD = fs.existsSync('/root/Toolkit') ? '/root/Toolkit' : '/workspaces/Toolkit';

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

server.listen(Number(process.env.EXEC_PORT || 9999), '0.0.0.0', () => {
  console.log('Remote exec server running');
});
