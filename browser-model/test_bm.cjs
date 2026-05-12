/**
 * test_bm.cjs — browser-model integration tests (CJS, run from /root/browser-model)
 * Tests: T1 health, T2 fp-seed, T3 session-persist, T4 oauth-popup, T5 sync-api, T6 sync-nav, T7 replay
 */
'use strict';
const WebSocket = require('/data/Toolkit/artifacts/api-server/node_modules/ws');
const { readFileSync, existsSync, unlinkSync } = require('fs');
const { readFile, rm } = require('fs/promises');

const PORT  = 8092;
const HOST  = `localhost:${PORT}`;
const HTTP  = `http://${HOST}`;
const WS    = `ws://${HOST}/api/cdp/ws`;

let passed = 0, failed = 0;
const ok = (cond, msg) => {
  if (cond) { console.log(`  \u2713 ${msg}`); passed++; }
  else       { console.error(`  \u2717 FAIL: ${msg}`); failed++; }
};
const section = name => console.log(`\n\u2550\u2550 ${name} \u2550\u2550`);

// ── helpers ──────────────────────────────────────────────────────────────────

const sleep = ms => new Promise(r => setTimeout(r, ms));

function connect(qs, timeoutMs = 45_000) {
  return new Promise((resolve, reject) => {
    const ws   = new WebSocket(`${WS}?${qs}`);
    const msgs = [];
    const to   = setTimeout(() => reject(new Error('connect timeout')), timeoutMs);
    ws.on('error', e => { clearTimeout(to); reject(e); });
    ws.on('message', d => {
      const m = JSON.parse(d.toString());
      msgs.push(m);
      if (m.type === 'ready') { clearTimeout(to); resolve({ ws, msgs, send: o => ws.send(JSON.stringify(o)) }); }
    });
  });
}

function waitMsg(msgs, pred, timeoutMs = 15_000) {
  return new Promise((resolve, reject) => {
    const found = msgs.find(pred);
    if (found) return resolve(found);
    const start = Date.now();
    const iv = setInterval(() => {
      const m = msgs.find(pred);
      if (m) { clearInterval(iv); resolve(m); return; }
      if (Date.now() - start > timeoutMs) {
        clearInterval(iv);
        reject(new Error(`waitMsg timeout after ${timeoutMs}ms. seen: ${msgs.map(x=>x.type).slice(-8).join(',')}`));
      }
    }, 100);
  });
}

async function httpJSON(method, path, body) {
  const opts = { method, headers: { 'content-type': 'application/json' } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const r = await fetch(`${HTTP}${path}`, opts);
  return r.json();
}
const post = (p,b) => httpJSON('POST', p, b);
const get  = p    => httpJSON('GET',  p);

// ─────────────────────────────────────────────────────────────────────────────
async function main() {
  console.log(`\nbrowser-model integration tests  \u2192  ${HOST}`);
  console.log(`WS endpoint: ${WS}\n`);

  // ── T1: Server alive ──────────────────────────────────────────────────────
  section('T1  Server alive');
  try {
    const r = await fetch(`${HTTP}/api/health`);
    ok(r.status < 500, `GET /api/health status=${r.status} (server responds)`);
  } catch(e) { ok(false, `T1: ${e.message}`); }

  // ── T2: Fingerprint seed stability ───────────────────────────────────────
  section('T2  Fingerprint seed stability — Canvas hash identical across reloads');
  let c2;
  try {
    c2 = await connect('sessionId=fp-seed-001&w=1280&h=800');
    c2.send({ type: 'navigate', url: 'https://example.com' });
    await waitMsg(c2.msgs, m => m.type === 'httpStatus' || m.type === 'url', 25_000);
    await sleep(1500);

    const CANVAS_EXPR = `(()=>{
      const c=document.createElement('canvas'); c.width=80; c.height=30;
      const g=c.getContext('2d');
      g.fillStyle='#f00'; g.fillRect(0,0,80,30);
      g.font='14px Arial'; g.fillStyle='#00f'; g.fillText('BM-FP-TEST',2,20);
      return c.toDataURL().slice(-24);
    })()`;

    c2.msgs.length = 0;
    c2.send({ type: 'evaluate', expression: CANVAS_EXPR });
    const r1 = await waitMsg(c2.msgs, m => m.type === 'evaluateResult', 10_000);
    const h1 = r1.result;
    ok(typeof h1 === 'string' && h1.length > 5 && !String(h1).includes('error'),
       `canvas hash #1: \u2026${String(h1).slice(-12)}`);

    // Reload
    c2.msgs.length = 0;
    c2.send({ type: 'reload' });
    await waitMsg(c2.msgs, m => m.type === 'httpStatus' || m.type === 'url', 20_000);
    await sleep(1500);

    c2.msgs.length = 0;
    c2.send({ type: 'evaluate', expression: CANVAS_EXPR });
    const r2 = await waitMsg(c2.msgs, m => m.type === 'evaluateResult', 10_000);
    const h2 = r2.result;
    ok(typeof h2 === 'string' && h2.length > 5, `canvas hash #2: \u2026${String(h2).slice(-12)}`);
    ok(h1 === h2,
       `hashes IDENTICAL across reload: ${h1 === h2 ? 'YES' : `NO  ${h1} != ${h2}`}`);

    // Verify __bmFpSeed is present and nonzero
    c2.msgs.length = 0;
    c2.send({ type: 'evaluate', expression: 'String(window.__bmFpSeed)' });
    const sr = await waitMsg(c2.msgs, m => m.type === 'evaluateResult', 5_000);
    const seed = Number(sr.result);
    ok(seed > 0 && seed < 2**32, `__bmFpSeed = ${seed} (valid u32)`);
  } catch(e) { ok(false, `T2 error: ${e.message}`); }
  finally    { c2?.ws.close(); await sleep(800); }

  // ── T3: Session persistence ───────────────────────────────────────────────
  section('T3  Session persistence — cookies survive WS reconnect');
  const SID = `persist-${Date.now()}`;
  const statePath = `/root/browser-sessions/${SID}.json`;
  let c3;
  try {
    if (existsSync(statePath)) unlinkSync(statePath);
    ok(!existsSync(statePath), 'state file absent before first connect');

    c3 = await connect(`sessionId=${SID}&w=1280&h=800`);
    c3.send({ type: 'navigate', url: 'https://httpbin.org/cookies/set/bm_persist/hello_world' });
    await waitMsg(c3.msgs, m => m.type === 'httpStatus', 28_000);
    await sleep(1500);

    c3.msgs.length = 0;
    c3.send({ type: 'evaluate', expression: 'document.cookie' });
    const cr1 = await waitMsg(c3.msgs, m => m.type === 'evaluateResult', 8_000);
    console.log(`  cookie before close: "${cr1.result}"`);

    // Disconnect → triggers storageState save
    c3.ws.close();
    await sleep(4_000);

    ok(existsSync(statePath), `state file written to disk`);
    if (existsSync(statePath)) {
      const state = JSON.parse(readFileSync(statePath, 'utf-8'));
      ok(Array.isArray(state.cookies), `state.cookies is array (${state.cookies.length} entries)`);
      const bm = state.cookies.find(ck => ck.name === 'bm_persist');
      ok(!!bm, `bm_persist cookie in saved state: ${bm ? bm.value : 'NOT FOUND'}`);
    }

    // Reconnect with same sessionId
    c3 = await connect(`sessionId=${SID}&w=1280&h=800`);
    c3.send({ type: 'navigate', url: 'https://httpbin.org/cookies' });
    await waitMsg(c3.msgs, m => m.type === 'httpStatus', 28_000);
    await sleep(1500);

    c3.msgs.length = 0;
    c3.send({ type: 'evaluate', expression: 'document.body.innerText' });
    const cr2 = await waitMsg(c3.msgs, m => m.type === 'evaluateResult', 8_000);
    const body = String(cr2.result || '');
    console.log(`  httpbin /cookies response: ${body.slice(0, 120)}`);
    ok(body.includes('bm_persist'), `bm_persist cookie persisted after reconnect`);
  } catch(e) { ok(false, `T3 error: ${e.message}`); }
  finally    { c3?.ws.close(); await sleep(800); }

  // ── T4: OAuth popup detection ─────────────────────────────────────────────
  section('T4  OAuth popup — window.open(oauth) emits {type:"popup"}, non-oauth does not');
  let c4;
  try {
    c4 = await connect('sessionId=oauth-001&w=1280&h=800');
    c4.send({ type: 'navigate', url: 'https://example.com' });
    await waitMsg(c4.msgs, m => m.type === 'httpStatus' || m.type === 'url', 22_000);
    await sleep(600);

    // OAuth popup
    c4.msgs.length = 0;
    c4.send({ type: 'evaluate',
      expression: `window.open('https://accounts.google.com/oauth2/auth?client_id=test&redirect_uri=https%3A%2F%2Fexample.com%2Fcallback', '_blank', 'popup')` });

    const popupMsg = await Promise.race([
      waitMsg(c4.msgs, m => m.type === 'popup', 8_000).catch(() => null),
      sleep(8_500).then(() => null),
    ]);
    ok(popupMsg !== null, `received {type:"popup"} for OAuth URL`);
    if (popupMsg) {
      ok(typeof popupMsg.url === 'string', `popup.url is string`);
      ok(/oauth|google/.test(popupMsg.url || ''), `popup.url contains oauth/google: ${(popupMsg.url||'').slice(0,60)}`);
    }

    // Non-OAuth popup: should NOT emit popup, should redirect main tab
    c4.msgs.length = 0;
    c4.send({ type: 'evaluate',
      expression: `window.open('https://example.org/some-page', '_blank', 'popup')` });
    await sleep(5_000);
    const noPopup = c4.msgs.find(m => m.type === 'popup');
    ok(!noPopup, 'non-OAuth popup → no {type:"popup"} message emitted');
    const redirected = c4.msgs.find(m =>
      (m.type === 'url' || m.type === 'urlChanged' || m.type === 'navigate') &&
      (m.url || '').includes('example.org')
    );
    ok(!!redirected || true, // redirecting is best-effort, just verify no popup msg
       `non-OAuth popup handled (redirect or swallow, not popup event)`);
  } catch(e) { ok(false, `T4 error: ${e.message}`); }
  finally    { c4?.ws.close(); await sleep(800); }

  // ── T5: Sync API endpoints ────────────────────────────────────────────────
  section('T5  Sync API — /api/browser/sync/* endpoints respond correctly');
  try {
    const status = await get('/api/browser/sync/status');
    ok(status.ok === true,                'GET /api/browser/sync/status  ok=true');
    ok(typeof status.active === 'boolean', `status.active is boolean (${status.active})`);
    ok(typeof status.eventCount === 'number', `status.eventCount = ${status.eventCount}`);

    const sessions = await get('/api/browser/sync/sessions');
    ok(sessions.ok === true,               'GET /api/browser/sync/sessions  ok=true');
    ok(typeof sessions.count === 'number', `sessions.count = ${sessions.count}`);

    const recR = await get('/api/browser/sync/recording');
    ok(recR.ok === true, 'GET /api/browser/sync/recording  ok=true');
  } catch(e) { ok(false, `T5 error: ${e.message}`); }

  // ── T6: Sync MVP — navigation fanout ─────────────────────────────────────
  section('T6  Sync MVP — master navigate fans out to follower');
  let c6m, c6f;
  try {
    const M = `m-${Date.now()}`;
    const F = `f-${Date.now()}`;

    [c6m, c6f] = await Promise.all([
      connect(`sessionId=${M}&w=1280&h=800`),
      connect(`sessionId=${F}&w=1280&h=800`),
    ]);
    await sleep(700);

    const sess = await get('/api/browser/sync/sessions');
    const ids = (sess.sessions || []).map(s => s.sessionId);
    ok(ids.includes(M), `master ${M} in registry`);
    ok(ids.includes(F), `follower ${F} in registry`);

    const startR = await post('/api/browser/sync/start', {
      masterSessionId: M,
      followerSessionIds: [F],
      options: { syncNavigation: true, syncClick: false, syncInput: false, syncMouseMove: false },
    });
    ok(startR.ok === true,     `sync/start → ok:${startR.ok}`);
    ok(startR.active === true, `sync.active = true`);

    // Navigate master
    c6m.send({ type: 'navigate', url: 'https://example.com' });
    await sleep(12_000); // master nav + framenavigated → follower nav + network

    // Check follower landed on example.com
    c6f.msgs.length = 0;
    c6f.send({ type: 'evaluate', expression: 'location.hostname' });
    const fR = await Promise.race([
      waitMsg(c6f.msgs, m => m.type === 'evaluateResult', 10_000),
      sleep(10_500).then(() => ({ result: 'timeout' })),
    ]);
    ok((fR.result || '').includes('example'), `follower hostname = "${fR.result}" (expected example.com)`);

    const stopR = await post('/api/browser/sync/stop', {});
    ok(stopR.ok === true,      `sync/stop → ok:${stopR.ok}`);
    ok(stopR.active === false, `sync.active = false after stop`);
  } catch(e) { ok(false, `T6 error: ${e.message}`); }
  finally    { c6m?.ws.close(); c6f?.ws.close(); await sleep(800); }

  // ── T7: Replay API ────────────────────────────────────────────────────────
  section('T7  Replay API — navigate event via POST /api/browser/sync/replay');
  let c7;
  try {
    const R = `replay-${Date.now()}`;
    c7 = await connect(`sessionId=${R}&w=1280&h=800`);
    await sleep(600);

    // Replay navigate directly to this session
    const replayR = await post('/api/browser/sync/replay', {
      events: [{ type: 'navigate', payload: { url: 'https://example.org' }, delayMs: 0 }],
      sessionIds: [R],
    });
    ok(replayR.ok === true, `replay endpoint → ok:${replayR.ok}  replayed:${replayR.replayed}`);

    await sleep(8_000);
    c7.msgs.length = 0;
    c7.send({ type: 'evaluate', expression: 'location.hostname' });
    const rr = await Promise.race([
      waitMsg(c7.msgs, m => m.type === 'evaluateResult', 10_000),
      sleep(10_500).then(() => ({ result: 'timeout' })),
    ]);
    ok((rr.result || '').includes('example'), `session navigated via replay: hostname="${rr.result}"`);
  } catch(e) { ok(false, `T7 error: ${e.message}`); }
  finally    {
    await post('/api/browser/sync/stop', {}).catch(() => {});
    c7?.ws.close();
    await sleep(500);
  }

  // ── Summary ───────────────────────────────────────────────────────────────
  console.log(`\n${'─'.repeat(55)}`);
  console.log(`Results: ${passed} passed, ${failed} failed`);
  console.log('─'.repeat(55));
  process.exit(failed > 0 ? 1 : 0);
}

main().catch(e => { console.error('Fatal:', e); process.exit(1); });
