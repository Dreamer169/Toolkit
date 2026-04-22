// SOCKS5 wrapper: force IPv4 resolution before chaining to upstream SOCKS5.
// listens 127.0.0.1:1193  ->  upstream 127.0.0.1:1093 (Replit ws-tunnel)
// purpose: prevent IPv6 egress from Replit container that triggers Google captcha.
const net = require('net');
const dns = require('dns');
const { SocksClient } = require('/root/browser-model/node_modules/.pnpm/socks@2.8.7/node_modules/socks');

// 干净 DNS：TCP 模式查询 1.1.1.1 / 8.8.8.8 / 9.9.9.9，绕开 GFW UDP 污染。
// 系统 systemd-resolved (127.0.0.53) 走 UDP 会拿到污染 IP（如 search.brave.com → Facebook IP）。
const cleanResolver = new dns.promises.Resolver({ timeout: 4000, tries: 2 });
cleanResolver.setServers(['1.1.1.1', '8.8.8.8', '9.9.9.9']);
const dnsCache = new Map(); // host -> { ip, exp }
async function cleanResolve4(host) {
  const c = dnsCache.get(host);
  if (c && c.exp > Date.now()) return c.ip;
  const ips = await cleanResolver.resolve4(host);
  if (!ips || !ips.length) throw new Error('no A record');
  const ip = ips[Math.floor(Math.random() * ips.length)];
  dnsCache.set(host, { ip, exp: Date.now() + 5 * 60 * 1000 });
  return ip;
}

const LOCAL_PORT    = parseInt(process.env.LOCAL_PORT    || '1193', 10);
const UPSTREAM_HOST = process.env.UPSTREAM_HOST          || '127.0.0.1';
const UPSTREAM_PORT = parseInt(process.env.UPSTREAM_PORT || '1093', 10);

function readN(sock, n) {
  return new Promise((resolve, reject) => {
    const buf = []; let got = 0;
    const onR = () => {
      while (got < n) {
        const c = sock.read(n - got);
        if (!c) return;
        buf.push(c); got += c.length;
      }
      sock.removeListener('readable', onR);
      sock.removeListener('error', onErr);
      sock.removeListener('end', onEnd);
      resolve(Buffer.concat(buf));
    };
    const onErr = e => { sock.removeListener('readable', onR); reject(e); };
    const onEnd = () => onErr(new Error('EOF'));
    sock.on('readable', onR);
    sock.on('error', onErr);
    sock.on('end', onEnd);
    onR();
  });
}

const server = net.createServer(async (client) => {
  client.on('error', () => {});
  try {
    const g = await readN(client, 2);
    if (g[0] !== 5) { client.end(); return; }
    await readN(client, g[1]);
    client.write(Buffer.from([5, 0]));

    const r = await readN(client, 4);
    if (r[0] !== 5 || r[1] !== 1) { client.end(); return; }
    const atyp = r[3]; let host;
    if (atyp === 1)      host = Array.from(await readN(client, 4)).join('.');
    else if (atyp === 3) { const l = (await readN(client, 1))[0]; host = (await readN(client, l)).toString(); }
    else if (atyp === 4) {
      const b = await readN(client, 16); const p = [];
      for (let i = 0; i < 16; i += 2) p.push(b.readUInt16BE(i).toString(16));
      host = p.join(':');
    } else { client.end(); return; }
    const port = (await readN(client, 2)).readUInt16BE();

    let dest = host;
    if (atyp === 3) {
      try {
        dest = await cleanResolve4(host);
      } catch (e) {
        console.warn('[socks5-v4only] clean DNS failed for', host, e && e.message);
        // 退化：直接放弃（不要 fallback 到污染的系统 DNS）
        try { client.write(Buffer.from([5,4,0,1, 0,0,0,0, 0,0])); client.end(); } catch {}
        return;
      }
    }

    const { socket: up } = await SocksClient.createConnection({
      proxy: { host: UPSTREAM_HOST, port: UPSTREAM_PORT, type: 5 },
      command: 'connect',
      destination: { host: dest, port }
    });
    client.write(Buffer.from([5,0,0,1, 0,0,0,0, 0,0]));
    up.on('error',  () => { try { client.destroy(); } catch {} });
    client.on('close', () => { try { up.destroy();    } catch {} });
    up.on('close',  () => { try { client.destroy(); } catch {} });
    up.pipe(client); client.pipe(up);
  } catch (e) {
    try { client.write(Buffer.from([5,5,0,1, 0,0,0,0, 0,0])); client.end(); } catch {}
  }
});
server.listen(LOCAL_PORT, '127.0.0.1', () =>
  console.log(`[socks5-v4only] :${LOCAL_PORT} -> ${UPSTREAM_HOST}:${UPSTREAM_PORT}`));
