// v0.1.72 — kênh quản trị qua bridge: owner.dm, owner.pair, group.owner_ping, replies_to_self, bỏ tin outbound trong
// thread Owner, và lớp 2 của permit (`basis`). Khoá Redis do lõi ghi (bridge chỉ đọc): gh:bridge:owner:<kênh>,
// gh:bridge:owner_thread:<kênh>, gh:bridge:pairing:<kênh>, gh:bridge:support:<kênh>, gh:bridge:account_kind:<kênh>:<phiên>.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { STREAMS } from '../src/envelope.js';
import { Keyring, checkPermit, checkPermitBasis, sha256Hex } from '../src/crypto.js';
import { pairingCode } from '../src/sessions.js';
import { WhatsAppAdapter, memoryAuthState } from '../src/channels/whatsapp.js';
import { ZaloAdapter } from '../src/channels/zalo.js';
import {
  KEY_B64, ThreadType, ctl, makeBaileysLib, makeZaloApi, makeZaloLib, setup, tick, waitFor, waMessage, zaloMessage,
} from './fakes.js';

const OWNER = '555'; // ID Zalo của Sếp
const OWNER_LID = '6666@lid';
const OWNER_PN = '84905555555@s.whatsapp.net';

async function activeZalo({ kind = 'bot', ...opts } = {}) {
  const env = await setup(opts);
  const blob = env.keyring.encryptTransport(JSON.stringify({ imei: 'i', cookie: [], userAgent: 'u' }), 'zalo:s1');
  await env.mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's1', credential: blob }));
  await waitFor(() => env.bus.of(STREAMS.status, 'session.active')[0]);
  env.api = env.zalo.ctrl.api;
  if (kind) env.bus.accountKinds['zalo:s1'] = kind;
  env.bus.owners.zalo = new Set([OWNER]);
  env.bus.ownerThreads.zalo = new Set([OWNER]);
  return env;
}

async function activeWa({ kind = 'bot', ...opts } = {}) {
  const env = await setup(opts);
  const st = memoryAuthState(makeBaileysLib().lib, null);
  st.state.creds.me = { id: '84909999999:3@s.whatsapp.net' };
  await env.mgr.handleControl(ctl('session.login', { channel: 'whatsapp', session_id: 'w1',
    credential: env.keyring.encryptTransport(JSON.stringify(st.serialize()), 'whatsapp:w1') }));
  env.sock = await waitFor(() => env.wa.ctrl.last());
  env.sock.open();
  await waitFor(() => env.bus.of(STREAMS.status, 'session.active')[0]);
  if (kind) env.bus.accountKinds['whatsapp:w1'] = kind;
  env.bus.owners.whatsapp = new Set([OWNER_LID, OWNER_PN]);
  env.bus.ownerThreads.whatsapp = new Set([OWNER_PN, OWNER_LID]);
  return env;
}

const upsert = (sock, m) => sock.ev.emit('messages.upsert', { type: 'notify', messages: [m] });
const inb = (bus, type) => bus.of(STREAMS.inbound, type);

// ─── owner.dm ─────────────────────────────────────────────────────────────────

test('owner.dm: tin 1-1 từ ID trong gh:bridge:owner ⇒ owner.dm (kể cả direct_mode=off), KHÔNG thành message', async () => {
  const { bus, mgr, api } = await activeZalo();
  bus.direct.zalo = '0'; // Sếp chọn không lưu tin 1-1
  api.listener.emit('message', zaloMessage({ group: false, threadId: OWNER, uidFrom: OWNER, msgId: '31', content: '/bao_cao' }));
  const e = await waitFor(() => inb(bus, 'owner.dm')[0]);
  assert.equal(e.actor, 'bridge:zalo');
  assert.deepEqual(e.payload, {
    channel: 'zalo', session_id: 's1', external_msg_id: '31', sender_external_id: OWNER, sender_alt_id: null,
    occurred_at: new Date(1758600000000).toISOString(), kind: 'text', body_text: '/bao_cao', forwarded: false,
  });
  await tick(5);
  assert.equal(inb(bus, 'message').length, 0);
  mgr.shutdown();
});

test('owner.dm: người khác nhắn 1-1 vẫn theo listen_direct như cũ', async () => {
  const { bus, mgr, api } = await activeZalo();
  bus.direct.zalo = '0';
  api.listener.emit('message', zaloMessage({ group: false, threadId: '111', uidFrom: '111', msgId: '41' }));
  await tick(10);
  assert.equal(bus.events.filter((e) => e.stream === STREAMS.inbound).length, 0);
  bus.direct.zalo = '1';
  api.listener.emit('message', zaloMessage({ group: false, threadId: '111', uidFrom: '111', msgId: '42' }));
  await waitFor(() => inb(bus, 'message').length === 1);
  assert.equal(inb(bus, 'owner.dm').length, 0);
  mgr.shutdown();
});

test('owner.dm WhatsApp: khớp theo alt (LID gửi, PN trong set) và tin chuyển tiếp mang forwarded=true', async () => {
  const { bus, mgr, sock } = await activeWa();
  bus.owners.whatsapp = new Set([OWNER_PN]); // lõi mới biết số, tin tới bằng LID
  upsert(sock, waMessage({ remoteJid: OWNER_LID, remoteJidAlt: OWNER_PN, id: 'D1', pushName: 'Sếp',
    message: { extendedTextMessage: { text: 'tình hình?', contextInfo: { isForwarded: true, forwardingScore: 1 } } } }));
  const e = await waitFor(() => inb(bus, 'owner.dm')[0]);
  assert.deepEqual(e.payload, {
    channel: 'whatsapp', session_id: 'w1', external_msg_id: 'D1', sender_external_id: OWNER_LID, sender_alt_id: OWNER_PN,
    occurred_at: new Date(1758600000 * 1000).toISOString(), kind: 'text', body_text: 'tình hình?', forwarded: true,
  });
  assert.equal(inb(bus, 'message').length, 0);
  mgr.shutdown();
});

// ─── owner.pair ───────────────────────────────────────────────────────────────

test('pairingCode: chỉ đúng mẫu ^\\s*(/?(ghep|ghép|start)\\s*)?\\d{6}\\s*$, trả đúng 6 số', () => {
  for (const [body, code] of [['123456', '123456'], ['  654321 ', '654321'], ['ghép 111222', '111222'], ['/ghep 333444', '333444'],
    ['/start 999000', '999000'], ['start000111', '000111'], ['Ghép 121212', '121212'], ['ghép 343434', '343434']]) {
    assert.equal(pairingCode(body), code, body);
  }
  for (const body of ['12345', '1234567', 'mã 123456', '123456 nhé', 'ghép', '/ghep 12 34 56', '１２３４５６', null, 42, '']) {
    assert.equal(pairingCode(body), null, String(body));
  }
});

test('owner.pair: chỉ khi có khoá gh:bridge:pairing + thân khớp; chỉ chuyển code, không chuyển thân tin', async () => {
  const { bus, mgr, api } = await activeZalo();
  api.listener.emit('message', zaloMessage({ group: false, threadId: '777', uidFrom: '777', msgId: '51', content: '123456' }));
  await waitFor(() => inb(bus, 'message').length === 1); // chưa mở ghép ⇒ tin thường
  bus.pairing.zalo = true;
  api.listener.emit('message', zaloMessage({ group: false, threadId: '777', uidFrom: '777', dName: 'Anh Ba', msgId: '52',
    content: ' /ghép 987654 ' }));
  api.listener.emit('message', zaloMessage({ group: false, threadId: '777', uidFrom: '777', msgId: '53', content: 'mã là 987654' }));
  const e = await waitFor(() => inb(bus, 'owner.pair')[0]);
  assert.deepEqual(e.payload, {
    channel: 'zalo', session_id: 's1', sender_external_id: '777', sender_alt_id: null, sender_name: 'Anh Ba',
    occurred_at: new Date(1758600000000).toISOString(), code: '987654', external_msg_id: '52',
  });
  await waitFor(() => inb(bus, 'message').length === 2);
  await tick(5);
  assert.deepEqual(inb(bus, 'message').map((x) => x.payload.external_msg_id), ['51', '53']);
  assert.equal(inb(bus, 'owner.pair').length, 1);
  // Tin trong NHÓM khớp mẫu không bao giờ là ghép
  bus.listen.zalo = new Set(['G1']);
  api.listener.emit('message', zaloMessage({ threadId: 'G1', msgId: '54', content: '123123' }));
  await waitFor(() => inb(bus, 'message').length === 3);
  assert.equal(inb(bus, 'owner.pair').length, 1);
  mgr.shutdown();
});

test('owner.pair WhatsApp: tin chuyển tiếp không được dùng để ghép', async () => {
  const { bus, mgr, sock } = await activeWa();
  bus.pairing.whatsapp = true;
  upsert(sock, waMessage({ remoteJid: '9999@lid', remoteJidAlt: '84907777777@s.whatsapp.net', id: 'P1',
    message: { extendedTextMessage: { text: '112233', contextInfo: { isForwarded: true } } } }));
  upsert(sock, waMessage({ remoteJid: '9999@lid', remoteJidAlt: '84907777777@s.whatsapp.net', id: 'P2', pushName: 'Sếp mới',
    message: { conversation: 'ghep 445566' } }));
  const e = await waitFor(() => inb(bus, 'owner.pair')[0]);
  assert.deepEqual([e.payload.code, e.payload.sender_external_id, e.payload.sender_alt_id, e.payload.sender_name],
    ['445566', '9999@lid', '84907777777@s.whatsapp.net', 'Sếp mới']);
  await waitFor(() => inb(bus, 'message').length === 1);
  assert.equal(inb(bus, 'message')[0].payload.external_msg_id, 'P1');
  assert.equal(inb(bus, 'owner.pair').length, 1);
  mgr.shutdown();
});

// ─── tin outbound trong thread Owner ──────────────────────────────────────────

test('outbound selfListen trong thread Owner bị bỏ; outbound tới người khác vẫn theo luật cũ', async () => {
  const { bus, mgr, api } = await activeZalo();
  api.listener.emit('message', zaloMessage({ group: false, threadId: OWNER, uidFrom: '900', isSelf: true, msgId: '61',
    content: 'Dạ Sếp, báo cáo…' }));
  api.listener.emit('message', zaloMessage({ group: false, threadId: '111', uidFrom: '900', isSelf: true, msgId: '62' }));
  await waitFor(() => inb(bus, 'message').length === 1);
  await tick(5);
  assert.deepEqual(inb(bus, 'message').map((e) => [e.payload.external_msg_id, e.payload.direction]), [['62', 'outbound']]);
  assert.equal(bus.events.filter((e) => e.stream === STREAMS.inbound).length, 1);
  mgr.shutdown();
});

test('outbound WhatsApp trong thread Owner (theo JID hoặc LID) bị bỏ', async () => {
  const { bus, mgr, sock } = await activeWa();
  upsert(sock, waMessage({ remoteJid: OWNER_PN, fromMe: true, id: 'O1', message: { conversation: 'dạ' } }));
  upsert(sock, waMessage({ remoteJid: OWNER_LID, fromMe: true, id: 'O2', message: { conversation: 'dạ' } }));
  upsert(sock, waMessage({ remoteJid: '84901111111@s.whatsapp.net', fromMe: true, id: 'O3', message: { conversation: 'chào' } }));
  await waitFor(() => inb(bus, 'message').length === 1);
  await tick(5);
  assert.deepEqual(inb(bus, 'message').map((e) => e.payload.external_msg_id), ['O3']);
  mgr.shutdown();
});

// ─── group.owner_ping ─────────────────────────────────────────────────────────

test('group.owner_ping: Owner tag bot trong nhóm KHÔNG nghe ⇒ ping không có nội dung; người khác tag / không tag ⇒ không ping', async () => {
  const { bus, mgr, api } = await activeZalo();
  bus.listen.zalo = new Set(['G1']);
  const tag = [{ uid: '900', pos: 0, len: 4, type: 0 }];
  api.listener.emit('message', zaloMessage({ threadId: 'G5', uidFrom: OWNER, msgId: '71', content: '@Bot vào việc', mentions: tag }));
  api.listener.emit('message', zaloMessage({ threadId: 'G5', uidFrom: '111', msgId: '72', content: '@Bot ơi', mentions: tag }));
  api.listener.emit('message', zaloMessage({ threadId: 'G5', uidFrom: OWNER, msgId: '73', content: 'không tag' }));
  api.listener.emit('message', zaloMessage({ threadId: 'G1', uidFrom: OWNER, msgId: '74', content: '@Bot nhóm nghe', mentions: tag }));
  const ping = await waitFor(() => bus.of(STREAMS.directory, 'group.owner_ping')[0]);
  await waitFor(() => inb(bus, 'message').length === 1);
  await tick(5);
  assert.deepEqual(ping.payload, {
    channel: 'zalo', session_id: 's1', group_id: 'G5', name: '', at: new Date(1758600000000).toISOString(),
  });
  assert.equal('body_text' in ping.payload, false);
  assert.ok(!JSON.stringify(ping).includes('vào việc'));
  assert.equal(bus.of(STREAMS.directory, 'group.owner_ping').length, 1);
  assert.deepEqual(inb(bus, 'message').map((e) => e.payload.external_msg_id), ['74']); // nhóm nghe: tin thường, không ping
  mgr.shutdown();
});

test('group.owner_ping WhatsApp: Owner (theo LID) tag bot (theo LID) trong nhóm không nghe', async () => {
  const { bus, mgr, sock } = await activeWa();
  upsert(sock, waMessage({ participant: OWNER_LID, participantAlt: OWNER_PN, id: 'T1',
    message: { extendedTextMessage: { text: '@Kho ra đây', contextInfo: { mentionedJid: ['7777@lid'] } } } }));
  // Người gửi mang hậu tố thiết bị, tập Owner (lõi ghi) ở dạng chuẩn hoá ⇒ vẫn khớp
  upsert(sock, waMessage({ remoteJid: '120364@g.us', participant: '6666:2@lid', id: 'T2',
    message: { extendedTextMessage: { text: '@Kho', contextInfo: { mentionedJid: ['84909999999@s.whatsapp.net'] } } } }));
  await waitFor(() => bus.of(STREAMS.directory, 'group.owner_ping').length === 2);
  const ping = bus.of(STREAMS.directory, 'group.owner_ping')[0];
  assert.equal(bus.of(STREAMS.directory, 'group.owner_ping')[1].payload.group_id, '120364@g.us');
  assert.deepEqual(Object.keys(ping.payload).sort(), ['at', 'channel', 'group_id', 'name', 'session_id']);
  assert.equal(ping.payload.group_id, '120363@g.us');
  mgr.shutdown();
});

// ─── account_kind ≠ bot ⇒ hành vi cũ ──────────────────────────────────────────

for (const kind of ['owner_self', null]) {
  test(`account_kind=${kind}: không phát owner.*, không owner_ping, không bỏ outbound — giữ hành vi cũ`, async () => {
    const { bus, mgr, api } = await activeZalo({ kind });
    bus.pairing.zalo = true;
    const tag = [{ uid: '900', pos: 0, len: 4, type: 0 }];
    api.listener.emit('message', zaloMessage({ group: false, threadId: OWNER, uidFrom: OWNER, msgId: '81', content: 'chào' }));
    api.listener.emit('message', zaloMessage({ group: false, threadId: '777', uidFrom: '777', msgId: '82', content: '123456' }));
    api.listener.emit('message', zaloMessage({ group: false, threadId: OWNER, uidFrom: '900', isSelf: true, msgId: '83' }));
    api.listener.emit('message', zaloMessage({ threadId: 'G5', uidFrom: OWNER, msgId: '84', mentions: tag }));
    await waitFor(() => inb(bus, 'message').length === 3);
    await tick(10);
    assert.deepEqual(inb(bus, 'message').map((e) => e.payload.external_msg_id), ['81', '82', '83']);
    assert.equal(inb(bus, 'owner.dm').length + inb(bus, 'owner.pair').length, 0);
    assert.equal(bus.of(STREAMS.directory, 'group.owner_ping').length, 0);
    mgr.shutdown();
  });
}

// ─── replies_to_self ──────────────────────────────────────────────────────────

test('replies_to_self: Zalo d.quote.ownerId == ownId; WhatsApp contextInfo.participant ∈ {jid, lid}', () => {
  const z = new ZaloAdapter({ lib: { ThreadType }, sessionId: 's1' });
  z.ownId = '900';
  assert.equal(z.normalise(zaloMessage({ quote: { ownerId: '900', cliMsgId: 1, globalMsgId: 2, msg: 'x' } })).replies_to_self, true);
  assert.equal(z.normalise(zaloMessage({ quote: { ownerId: 900, msg: 'x' } })).replies_to_self, true);
  assert.equal(z.normalise(zaloMessage({ quote: { ownerId: '111', msg: 'x' } })).replies_to_self, false);
  assert.equal(z.normalise(zaloMessage()).replies_to_self, false);

  const w = new WhatsAppAdapter({ lib: makeBaileysLib().lib, sessionId: 'w1' });
  w.own = { jid: '84909999999@s.whatsapp.net', lid: '7777@lid', name: 'Kho' };
  const reply = (participant) => w.normalise(waMessage({ message: { extendedTextMessage: { text: 'ừ',
    contextInfo: { stanzaId: 'X', participant, quotedMessage: { conversation: 'hỏi' } } } } }));
  assert.equal(reply('84909999999:2@s.whatsapp.net').replies_to_self, true);
  assert.equal(reply('7777@lid').replies_to_self, true);
  assert.equal(reply('84901111111@s.whatsapp.net').replies_to_self, false);
  assert.equal(w.normalise(waMessage()).replies_to_self, false);
});

test('replies_to_self đi cùng tin message lên gh.bridge.inbound', async () => {
  const { bus, mgr, api } = await activeZalo();
  bus.listen.zalo = new Set(['G1']);
  api.listener.emit('message', zaloMessage({ threadId: 'G1', msgId: '91', quote: { ownerId: '900', msg: 'giá?' } }));
  const m = await waitFor(() => inb(bus, 'message')[0]);
  assert.equal(m.payload.replies_to_self, true);
  mgr.shutdown();
});

// ─── checkPermit lớp 2 (basis) ────────────────────────────────────────────────

const KEY = Buffer.from(KEY_B64, 'base64');

function cmdFor(k, claimOver = {}, over = {}) {
  const text = over.text ?? 'Dạ Sếp';
  const claims = {
    nonce: 'n-auto', auto_send_id: 'as-1', basis: 'owner_dm', channel: 'zalo', thread_id: OWNER, thread_type: 'user',
    body_sha256: sha256Hex(text), exp: 1_800_000_060, ...claimOver,
  };
  for (const [key, v] of Object.entries(claimOver)) if (v === undefined) delete claims[key];
  return { channel: claims.channel, session_id: 's1', thread_id: claims.thread_id, thread_type: claims.thread_type, text,
    permit: k.signPermit(claims), ...over };
}

test('checkPermit: claims không basis phải có draft_id; basis lạ ⇒ PERMIT_MISMATCH', () => {
  const k = new Keyring(KEY);
  const now = 1_800_000_000;
  assert.equal(checkPermit(k, cmdFor(k, { basis: undefined, auto_send_id: undefined, draft_id: 'd1' }), now).ok, true);
  assert.equal(checkPermit(k, cmdFor(k, { basis: undefined, auto_send_id: undefined }), now).error, 'PERMIT_INVALID');
  assert.equal(checkPermit(k, cmdFor(k, { basis: undefined, draft_id: '' }), now).error, 'PERMIT_INVALID');
  assert.equal(checkPermit(k, cmdFor(k, { basis: 'boss_mode' }), now).error, 'PERMIT_MISMATCH');
  assert.equal(checkPermit(k, cmdFor(k, { basis: '' }), now).error, 'PERMIT_MISMATCH');
  assert.equal(checkPermit(k, cmdFor(k), now).ok, true); // basis hợp lệ, không cần draft_id (lớp 2 kiểm tiếp)
});

test('checkPermitBasis: owner_dm / support / owner_tag theo tập Redis; phiên không phải bot ⇒ từ chối', async () => {
  const sets = { ownerThread: new Set([OWNER]), support: new Set(['G2']), listening: new Set(['G1', 'G2']) };
  const lookup = (kind = 'bot') => ({
    accountKind: async () => kind,
    ownerThread: async (id) => sets.ownerThread.has(String(id)),
    support: async (id) => sets.support.has(String(id)),
    listening: async (id) => sets.listening.has(String(id)),
  });
  const c = (basis, thread_id, thread_type) => ({ claims: { basis, thread_id, thread_type }, cmd: { thread_id, thread_type } });
  const run = async (x, kind) => (await checkPermitBasis(x.claims, x.cmd, lookup(kind))).error ?? 'ok';
  assert.equal(await run(c('owner_dm', OWNER, 'user')), 'ok');
  assert.equal(await run(c('owner_dm', '111', 'user')), 'PERMIT_MISMATCH'); // I4: không phải thread Owner
  assert.equal(await run(c('owner_dm', OWNER, 'group')), 'PERMIT_MISMATCH');
  assert.equal(await run(c('support', 'G2', 'group')), 'ok');
  assert.equal(await run(c('support', 'G1', 'group')), 'PERMIT_MISMATCH'); // I4: nhóm không bật hỗ trợ
  assert.equal(await run(c('owner_tag', 'G1', 'group')), 'ok');
  assert.equal(await run(c('owner_tag', 'G9', 'group')), 'PERMIT_MISMATCH'); // nhóm không nghe
  assert.equal(await run(c('owner_tag', OWNER, 'user')), 'PERMIT_MISMATCH');
  assert.equal(await run(c('owner_dm', OWNER, 'user'), 'owner_self'), 'PERMIT_MISMATCH'); // I4: phiên owner_self
  assert.equal(await run(c('owner_dm', OWNER, 'user'), null), 'PERMIT_MISMATCH');
  assert.equal(await run(c('weird', OWNER, 'user')), 'PERMIT_MISMATCH');
  assert.equal(await run({ claims: { draft_id: 'd1' }, cmd: {} }, 'owner_self'), 'ok'); // permit bản nháp cũ: không đổi
});

test('message.send có basis: SessionManager kiểm lớp 2 trước khi gửi, send.result kèm auto_send_id', async () => {
  const { bus, mgr, keyring, api } = await activeZalo();
  const send = async (claimOver, over) => {
    const p = cmdFor(keyring, { exp: Math.floor(Date.now() / 1000) + 120, ...claimOver }, over);
    await mgr.handleOutbound({ type: 'message.send', org_id: 'org-1', correlation_id: 'c', payload: p });
    const r = bus.of(STREAMS.status, 'send.result');
    return r[r.length - 1].payload;
  };
  // owner_dm tới thread Owner ⇒ gửi
  const ok = await send({ nonce: 'a1' });
  assert.deepEqual(ok, { nonce: 'a1', draft_id: null, auto_send_id: 'as-1', channel: 'zalo', ok: true, external_msg_id: '7001' });
  // owner_dm tới người khác ⇒ PERMIT_MISMATCH, không gửi
  const bad = await send({ nonce: 'a2', thread_id: '111' });
  assert.deepEqual([bad.ok, bad.error, bad.auto_send_id], [false, 'PERMIT_MISMATCH', 'as-1']);
  // support tới nhóm không bật ⇒ từ chối; bật ⇒ gửi
  const sup = await send({ nonce: 'a3', basis: 'support', thread_id: 'G2', thread_type: 'group' });
  assert.equal(sup.error, 'PERMIT_MISMATCH');
  bus.support.zalo = new Set(['G2']);
  assert.equal((await send({ nonce: 'a4', basis: 'support', thread_id: 'G2', thread_type: 'group' })).ok, true);
  // owner_tag chỉ tới nhóm đang nghe
  assert.equal((await send({ nonce: 'a5', basis: 'owner_tag', thread_id: 'G1', thread_type: 'group' })).error, 'PERMIT_MISMATCH');
  bus.listen.zalo = new Set(['G1']);
  assert.equal((await send({ nonce: 'a6', basis: 'owner_tag', thread_id: 'G1', thread_type: 'group' })).ok, true);
  // phiên owner_self ⇒ mọi basis bị từ chối, nonce không bị tiêu
  bus.accountKinds['zalo:s1'] = 'owner_self';
  assert.equal((await send({ nonce: 'a7' })).error, 'PERMIT_MISMATCH');
  assert.equal(bus.nonces.has('a7'), false);
  // claims không basis, không draft_id ⇒ từ chối
  assert.equal((await send({ nonce: 'a8', basis: undefined, auto_send_id: undefined })).error, 'PERMIT_INVALID');
  assert.equal(api.sent.length, 3);
  mgr.shutdown();
});

// ─── v0.1.73: «đã xem» + «đang nhập…» CHỈ trong kênh quản trị 1-1 ─────────────────────────────────────────────────────

async function zaloWith(api, opts = {}) {
  const zalo = makeZaloLib({ api });
  const env = await setup({ zalo, ...opts });
  const blob = env.keyring.encryptTransport(JSON.stringify({ imei: 'i', cookie: [], userAgent: 'u' }), 'zalo:s1');
  await env.mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's1', credential: blob }));
  await waitFor(() => env.bus.of(STREAMS.status, 'session.active')[0]);
  env.api = api;
  env.bus.accountKinds['zalo:s1'] = 'bot';
  env.bus.owners.zalo = new Set([OWNER]);
  env.bus.ownerThreads.zalo = new Set([OWNER]);
  return env;
}

test('Zalo: tin 1-1 của Sếp ⇒ đã xem + đang nhập (gửi lại định kỳ); trả lời ⇒ dừng', async () => {
  const env = await zaloWith(makeZaloApi({ ownId: '900' }), { typingRefreshMs: 15, typingMaxMs: 5000 });
  const { bus, mgr, api, keyring } = env;
  api.listener.emit('message', zaloMessage({ group: false, threadId: OWNER, uidFrom: OWNER, msgId: '71', content: 'chào' }));
  await waitFor(() => inb(bus, 'owner.dm')[0]);
  await waitFor(() => api.seen.length === 1 && api.typing.length >= 3);
  assert.equal(api.seen[0].type, ThreadType.User);
  assert.equal(api.seen[0].messages.msgId, '71');
  assert.equal(api.seen[0].messages.uidFrom, OWNER);
  assert.equal(api.typing[0].threadId, OWNER);
  // trả lời đúng permit owner_dm ⇒ dừng gửi «đang nhập»
  const text = 'Dạ Sếp';
  const claims = { nonce: 'n-r1', auto_send_id: 'as-9', basis: 'owner_dm', channel: 'zalo', thread_id: OWNER, thread_type: 'user',
    body_sha256: sha256Hex(text), exp: Math.floor(Date.now() / 1000) + 60 };
  await mgr.handleOutbound({ type: 'message.send', org_id: 'org-1', payload: { channel: 'zalo', session_id: 's1', thread_id: OWNER,
    thread_type: 'user', text, permit: keyring.signPermit(claims) } });
  assert.equal(api.sent.length, 1);
  const n = api.typing.length;
  await tick(80);
  assert.equal(api.typing.length, n);
  mgr.shutdown();
});

test('Zalo: sendSeenEvent / sendTypingEvent lỗi chỉ ghi log — owner.dm vẫn đi tiếp', async () => {
  const env = await zaloWith(makeZaloApi({ failSeen: true, failTyping: true }), { typingRefreshMs: 10, typingMaxMs: 100 });
  env.api.listener.emit('message', zaloMessage({ group: false, threadId: OWNER, uidFrom: OWNER, msgId: '72' }));
  await waitFor(() => inb(env.bus, 'owner.dm')[0]);
  await waitFor(() => env.api.seen.length === 1 && env.api.typing.length >= 1);
  assert.equal(inb(env.bus, 'owner.dm').length, 1);
  env.mgr.shutdown();
});

test('Zalo: nhóm im lặng tuyệt đối — kể cả Sếp tag bot trong nhóm nghe / không nghe', async () => {
  const env = await zaloWith(makeZaloApi({ ownId: '900' }), { typingRefreshMs: 10 });
  const { bus, mgr, api } = env;
  bus.listen.zalo = new Set(['G1']);
  api.listener.emit('message', zaloMessage({ group: true, threadId: 'G1', uidFrom: OWNER, msgId: '73', content: '@bot ơi', mentions: [{ uid: '900' }] }));
  api.listener.emit('message', zaloMessage({ group: true, threadId: 'G9', uidFrom: OWNER, msgId: '74', content: '@bot ơi', mentions: [{ uid: '900' }] }));
  await tick(60);
  assert.equal(api.seen.length, 0);
  assert.equal(api.typing.length, 0);
  assert.equal(inb(bus, 'owner.dm').length, 0);
  mgr.shutdown();
});

test('Zalo: người lạ nhắn 1-1, hoặc phiên không phải bot ⇒ không đã xem / đang nhập', async () => {
  const env = await zaloWith(makeZaloApi(), { typingRefreshMs: 10 });
  const { bus, mgr, api } = env;
  api.listener.emit('message', zaloMessage({ group: false, threadId: '111', uidFrom: '111', msgId: '75' }));
  await tick(40);
  assert.equal(api.seen.length + api.typing.length, 0);
  bus.accountKinds['zalo:s1'] = 'owner_self';
  api.listener.emit('message', zaloMessage({ group: false, threadId: OWNER, uidFrom: OWNER, msgId: '76' }));
  await tick(40);
  assert.equal(api.seen.length + api.typing.length, 0);
  mgr.shutdown();
});

test('WhatsApp: tin 1-1 của Sếp ⇒ readMessages + composing; chuyển tiếp chỉ đã xem; nhóm im lặng', async () => {
  const { bus, mgr, sock } = await activeWa({ typingRefreshMs: 1000, typingMaxMs: 5000 });
  upsert(sock, waMessage({ remoteJid: OWNER_PN, id: 'S1', pushName: 'Sếp', message: { conversation: 'tình hình?' } }));
  await waitFor(() => inb(bus, 'owner.dm')[0]);
  await waitFor(() => sock.reads.length === 1 && sock.presence.length === 1);
  assert.deepEqual(sock.reads[0], [{ remoteJid: OWNER_PN, id: 'S1', fromMe: false }]);
  assert.deepEqual(sock.presence[0], { type: 'composing', jid: OWNER_PN });
  upsert(sock, waMessage({ remoteJid: OWNER_PN, id: 'S2', message: { extendedTextMessage: { text: 'fw', contextInfo: { isForwarded: true } } } }));
  await waitFor(() => sock.reads.length === 2);
  assert.equal(sock.presence.filter((x) => x.type === 'composing').length, 1);
  bus.listen.whatsapp = new Set(['120363@g.us']);
  upsert(sock, waMessage({ remoteJid: '120363@g.us', participant: OWNER_PN, id: 'G1', message: { conversation: 'chào nhóm' } }));
  upsert(sock, waMessage({ remoteJid: '120999@g.us', participant: OWNER_PN, id: 'G2', message: { conversation: 'nhóm lạ' } }));
  await tick(40);
  assert.equal(sock.reads.length, 2);
  assert.equal(sock.presence.length, 1);
  mgr.shutdown();
});

test('WhatsApp: tự chuyển composing → paused sau typingMaxMs; lỗi API không làm hỏng luồng', async () => {
  const { bus, mgr, sock } = await activeWa({ typingRefreshMs: 1000, typingMaxMs: 60 });
  sock.failRead = true;
  sock.failPresence = true;
  upsert(sock, waMessage({ remoteJid: OWNER_PN, id: 'S3', message: { conversation: 'alo' } }));
  await waitFor(() => inb(bus, 'owner.dm')[0]);
  await waitFor(() => sock.presence.some((x) => x.type === 'paused'));
  assert.deepEqual(sock.presence.map((x) => x.type), ['composing', 'paused']);
  mgr.shutdown();
});

test('WhatsApp: trả lời Sếp ⇒ paused ngay, không chờ hết giờ', async () => {
  const { bus, mgr, sock, keyring } = await activeWa({ typingRefreshMs: 1000, typingMaxMs: 60000 });
  upsert(sock, waMessage({ remoteJid: OWNER_PN, id: 'S4', message: { conversation: 'alo' } }));
  await waitFor(() => inb(bus, 'owner.dm')[0]);
  await waitFor(() => sock.presence.length === 1);
  const text = 'Dạ Sếp';
  const claims = { nonce: 'n-r2', auto_send_id: 'as-10', basis: 'owner_dm', channel: 'whatsapp', thread_id: OWNER_PN,
    thread_type: 'user', body_sha256: sha256Hex(text), exp: Math.floor(Date.now() / 1000) + 60 };
  await mgr.handleOutbound({ type: 'message.send', org_id: 'org-1', payload: { channel: 'whatsapp', session_id: 'w1',
    thread_id: OWNER_PN, thread_type: 'user', text, permit: keyring.signPermit(claims) } });
  assert.equal(sock.sent.length, 1);
  assert.deepEqual(sock.presence.map((x) => x.type), ['composing', 'paused']);
  mgr.shutdown();
});
