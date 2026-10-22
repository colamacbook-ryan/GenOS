import { test } from 'node:test';
import assert from 'node:assert/strict';
import { STREAMS } from '../src/envelope.js';
import { sha256Hex } from '../src/crypto.js';
import { createLogger } from '../src/log.js';
import { ctl, makeZaloLib, setup, tick, waitFor, zaloMessage } from './fakes.js';

async function activeZalo(opts = {}) {
  const env = await setup(opts);
  const blob = env.keyring.enabled
    ? env.keyring.encryptTransport(JSON.stringify({ imei: 'i', cookie: [], userAgent: 'u' }), 'zalo:s1') : null;
  await env.mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's1', credential: blob }));
  if (env.keyring.enabled) await waitFor(() => env.bus.of(STREAMS.status, 'session.active')[0]);
  env.api = env.zalo.ctrl.api;
  return env;
}

// ─── Khoá cứng tập nghe ────────────────────────────────────────────────────────

test('tập nghe: tin nhóm không thuộc gh:bridge:listen:zalo → không publish gì', async () => {
  const lines = [];
  const { bus, mgr, api } = await activeZalo({ logger: createLogger((l) => lines.push(l)) });
  bus.listen.zalo = new Set(['G1']);
  api.listener.emit('message', zaloMessage({ threadId: 'G9', content: 'bí mật nhóm lạ', msgId: '1' }));
  api.listener.emit('message', zaloMessage({ threadId: 'G1', content: 'được nghe', msgId: '2' }));
  await waitFor(() => bus.of(STREAMS.inbound).length === 1);
  await tick(5);
  const inb = bus.of(STREAMS.inbound);
  assert.equal(inb.length, 1);
  assert.equal(inb[0].type, 'message');
  assert.equal(inb[0].payload.external_group_id, 'G1');
  assert.equal(inb[0].actor, 'bridge:zalo');
  assert.equal(inb[0].orgId, 'org-1');
  // Log không chứa nội dung tin (kể cả tin bị bỏ)
  assert.ok(!lines.some((l) => l.includes('bí mật') || l.includes('được nghe')));
  lines.forEach((l) => JSON.parse(l));
  assert.deepEqual(mgr.health()[0], { channel: 'zalo', session_id: 's1', state: 'active', queued: 0, inbound: 1, dropped: 1 });
  mgr.shutdown();
});

test('tập nghe: tin 1-1 mặc định ĐI (lưu im lặng), chỉ listen_direct = "0" mới chặn; tin của chính mình theo cùng luật', async () => {
  const { bus, mgr, api } = await activeZalo();
  api.listener.emit('message', zaloMessage({ group: false, threadId: '111', msgId: '10' }));   // chưa có khoá ⇒ mặc định đi
  await waitFor(() => bus.of(STREAMS.inbound).length === 1);
  bus.direct.zalo = '0';
  api.listener.emit('message', zaloMessage({ group: false, threadId: '111', msgId: '11' }));
  await tick(5);
  assert.equal(bus.of(STREAMS.inbound).length, 1);
  bus.direct.zalo = '1';
  api.listener.emit('message', zaloMessage({ group: false, threadId: '111', msgId: '12' }));
  api.listener.emit('message', zaloMessage({ group: false, threadId: '111', msgId: '13', isSelf: true, uidFrom: '900' }));
  // Tin mình gửi trong nhóm không được nghe → bỏ
  api.listener.emit('message', zaloMessage({ threadId: 'G5', msgId: '14', isSelf: true, uidFrom: '900' }));
  await waitFor(() => bus.of(STREAMS.inbound).length === 3);
  await tick(5);
  assert.deepEqual(bus.of(STREAMS.inbound).map((e) => [e.payload.external_msg_id, e.payload.direction]),
    [['10', 'inbound'], ['12', 'inbound'], ['13', 'outbound']]);
  mgr.shutdown();
});

// ─── Permit ────────────────────────────────────────────────────────────────────

function sendCmd(keyring, over = {}, claimOver = {}) {
  const text = over.text ?? 'Dạ shop đã nhận đơn';
  const claims = {
    nonce: 'nonce-1', draft_id: 'draft-1', channel: 'zalo', thread_id: 'G1', thread_type: 'group',
    body_sha256: sha256Hex(text), exp: Math.floor(Date.now() / 1000) + 120, ...claimOver,
  };
  return {
    type: 'message.send',
    org_id: 'org-1',
    correlation_id: 'corr-send',
    payload: { channel: 'zalo', session_id: 's1', thread_id: 'G1', thread_type: 'group', text, permit: keyring.signPermit(claims), ...over },
  };
}

test('permit hợp lệ → gửi đúng một lần, send.result ok kèm external_msg_id', async () => {
  const { bus, mgr, keyring, api } = await activeZalo();
  await mgr.handleOutbound(sendCmd(keyring));
  assert.equal(api.sent.length, 1);
  assert.deepEqual(api.sent[0].content, { msg: 'Dạ shop đã nhận đơn' });
  const r = bus.of(STREAMS.status, 'send.result');
  assert.deepEqual(r[0].payload, { nonce: 'nonce-1', draft_id: 'draft-1', channel: 'zalo', ok: true, external_msg_id: '7001' });
  assert.equal(r[0].correlationId, 'corr-send');
  assert.ok(bus.nonces.has('nonce-1'));
  mgr.shutdown();
});

const BAD = [
  ['chữ ký sai', (k) => { const c = sendCmd(k); c.payload.permit = `${c.payload.permit.split('.')[0]}.${Buffer.alloc(32).toString('base64url')}`; return c; }, 'PERMIT_INVALID'],
  ['không phải permit', (k) => sendCmd(k, { permit: 'khong-hop-le' }), 'PERMIT_INVALID'],
  ['đã hết hạn', (k) => sendCmd(k, {}, { exp: Math.floor(Date.now() / 1000) - 1 }), 'PERMIT_EXPIRED'],
  ['nội dung khác', (k) => { const c = sendCmd(k); c.payload.text += ' (đã sửa)'; return c; }, 'PERMIT_MISMATCH'],
  ['thread khác', (k) => { const c = sendCmd(k); c.payload.thread_id = 'G2'; return c; }, 'PERMIT_MISMATCH'],
  ['thread_type khác', (k) => sendCmd(k, {}, { thread_type: 'user' }), 'PERMIT_MISMATCH'],
  ['kênh khác', (k) => sendCmd(k, {}, { channel: 'whatsapp' }), 'PERMIT_MISMATCH'],
];

for (const [name, make, code] of BAD) {
  test(`permit ${name} → không gửi, ${code}`, async () => {
    const { bus, mgr, keyring, api } = await activeZalo();
    await mgr.handleOutbound(make(keyring));
    assert.equal(api.sent.length, 0);
    const r = bus.of(STREAMS.status, 'send.result')[0].payload;
    assert.equal(r.ok, false);
    assert.equal(r.error, code);
    assert.equal(r.channel, 'zalo');
    assert.equal(bus.nonces.size, 0); // nonce không bị đốt khi permit sai
    mgr.shutdown();
  });
}

test('permit dùng lại nonce → PERMIT_REUSED, chỉ gửi lần đầu', async () => {
  const { bus, mgr, keyring, api } = await activeZalo();
  const cmd = sendCmd(keyring);
  await mgr.handleOutbound(cmd);
  await mgr.handleOutbound(cmd);
  assert.equal(api.sent.length, 1);
  const r = bus.of(STREAMS.status, 'send.result').map((e) => e.payload);
  assert.deepEqual(r.map((x) => [x.ok, x.error]), [[true, undefined], [false, 'PERMIT_REUSED']]);
  assert.equal(r[1].nonce, 'nonce-1');
  mgr.shutdown();
});

test('phiên chưa hoạt động → SESSION_NOT_ACTIVE, nonce còn nguyên; lỗi nền tảng → SEND_FAILED', async () => {
  const { bus, mgr, keyring, api } = await activeZalo();
  await mgr.handleOutbound(sendCmd(keyring, { session_id: 'khac' }));
  assert.equal(bus.of(STREAMS.status, 'send.result')[0].payload.error, 'SESSION_NOT_ACTIVE');
  assert.equal(bus.nonces.size, 0);
  api.sendMessage = async () => { throw new Error('rate limit'); };
  await mgr.handleOutbound(sendCmd(keyring));
  const r = bus.of(STREAMS.status, 'send.result')[1].payload;
  assert.deepEqual([r.ok, r.error], [false, 'SEND_FAILED']);
  mgr.shutdown();
});

test('gửi tuần tự theo phiên; heartbeat báo queued', async () => {
  const { bus, mgr, keyring, api } = await activeZalo();
  let release;
  const gate = new Promise((r) => { release = r; });
  const orig = api.sendMessage;
  api.sendMessage = async (...a) => { await gate; return orig(...a); };
  const p1 = mgr.handleOutbound(sendCmd(keyring, {}, { nonce: 'a' }));
  const p2 = mgr.handleOutbound(sendCmd(keyring, {}, { nonce: 'b' }));
  await tick(5);
  await mgr.heartbeat();
  const hb = bus.of(STREAMS.status, 'heartbeat')[0];
  assert.deepEqual(hb.payload, { channel: 'zalo', session_id: 's1', latency_ms: 1, queued: 2 });
  assert.equal(hb.actor, 'bridge:zalo');
  assert.equal(bus.heartbeatKey.ttl, 45);
  release();
  await Promise.all([p1, p2]);
  assert.deepEqual(api.sent.length, 2);
  await mgr.heartbeat();
  assert.equal(bus.of(STREAMS.status, 'heartbeat')[1].payload.queued, 0);
  mgr.shutdown();
});

test('heartbeat chỉ cho phiên đang hoạt động, vẫn đặt khoá gh:bridge:heartbeat', async () => {
  const { bus, mgr } = await setup();
  await mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 'q1', credential: null }));
  await waitFor(() => bus.of(STREAMS.status, 'session.qr')[0]);
  await mgr.heartbeat();
  assert.equal(bus.of(STREAMS.status, 'heartbeat').length, 0);
  assert.ok(bus.heartbeatKey.at);
  mgr.shutdown();
});

// ─── Không có khoá bridge ───────────────────────────────────────────────────────

test('không có khoá bridge: message.send → PERMIT_INVALID; session.login → ended error BRIDGE_KEY_MISSING', async () => {
  const zalo = makeZaloLib();
  const { bus, mgr, keyring } = await setup({ key: null, zalo });
  assert.equal(keyring.enabled, false);
  await mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's1', credential: null }));
  const e = bus.of(STREAMS.status, 'session.ended')[0];
  assert.deepEqual(e.payload, { channel: 'zalo', session_id: 's1', reason: 'error', error: 'BRIDGE_KEY_MISSING' });
  assert.equal(zalo.ctrl.instances.length, 0); // không sinh QR, không đăng nhập
  assert.equal(bus.of(STREAMS.status, 'session.active').length, 0);
  await mgr.handleOutbound({ type: 'message.send', payload: { channel: 'zalo', session_id: 's1', thread_id: 'G1', thread_type: 'group', text: 'x', permit: 'a.b' } });
  const r = bus.of(STREAMS.status, 'send.result')[0].payload;
  assert.deepEqual([r.ok, r.error], [false, 'PERMIT_INVALID']);
});

// ─── Lệnh lạ / thứ tự ─────────────────────────────────────────────────────────

test('lệnh control sai kênh / thiếu session_id / loại lạ bị bỏ qua', async () => {
  const { bus, mgr } = await setup();
  await mgr.handleControl(ctl('session.login', { channel: 'telegram', session_id: 's1' }));
  await mgr.handleControl(ctl('session.login', { channel: 'zalo' }));
  await mgr.handleControl(ctl('session.reboot', { channel: 'zalo', session_id: 's1' }));
  await mgr.handleOutbound({ type: 'message.unknown', payload: {} });
  assert.equal(bus.events.length, 0);
});

test('login lần hai cho cùng phiên thay phiên cũ (không phát ended cho phiên cũ)', async () => {
  const { bus, mgr, zalo } = await setup();
  await mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's1', credential: null }));
  await waitFor(() => bus.of(STREAMS.status, 'session.qr').length === 1);
  await mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's1', credential: null }));
  await waitFor(() => bus.of(STREAMS.status, 'session.qr').length === 2);
  assert.equal(zalo.ctrl.instances.length, 2);
  assert.equal(bus.of(STREAMS.status, 'session.ended').length, 0);
  assert.equal(mgr.health().length, 1);
  mgr.shutdown();
});
