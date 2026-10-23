import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import {
  WhatsAppAdapter, isCompanionRefresh, loadWhatsAppLib, memoryAuthState, noQrErrorCode, pickWaVersion, waVersionHeaders,
} from '../src/channels/whatsapp.js';
import { VERSION } from '../src/config.js';
import { STREAMS } from '../src/envelope.js';
import { ADV0, BufferJSON, ctl, makeBaileysLib, setup, tick, waitFor, waMessage } from './fakes.js';

function adapter() {
  const a = new WhatsAppAdapter({ lib: makeBaileysLib().lib, sessionId: 'w1' });
  a.own = { jid: '84909999999@s.whatsapp.net', lid: '7777@lid', name: 'Kho Gen' };
  return a;
}

test('chuẩn hoá: tin văn bản trong nhóm (conversation)', () => {
  const a = adapter();
  a.groupNames.set('120363@g.us', 'Nhóm kho');
  const p = a.normalise(waMessage());
  assert.deepEqual({ ...p, payload: undefined }, {
    channel: 'whatsapp', session_id: 'w1', external_msg_id: 'ABCD1', external_group_id: '120363@g.us', group_name: 'Nhóm kho',
    sender_external_id: '84901111111@s.whatsapp.net', sender_name: 'Minh', sender_phone: '+84901111111',
    occurred_at: new Date(1758600000 * 1000).toISOString(), kind: 'text', body_text: 'chào cả nhà',
    direction: 'inbound', mentions_self: false, replies_to_self: false, payload: undefined, forwarded: false,
  });
  assert.equal(p.payload.key.remoteJid, '120363@g.us');
  assert.equal(p.payload.message.conversation, 'chào cả nhà');
});

test('chuẩn hoá: extendedTextMessage có nhắc tới mình (jid có thiết bị / lid)', () => {
  const a = adapter();
  const p = a.normalise(waMessage({ message: { extendedTextMessage: { text: '@Kho kiểm hàng', contextInfo: { mentionedJid: ['84909999999:3@s.whatsapp.net'] } } } }));
  assert.deepEqual([p.kind, p.body_text, p.mentions_self], ['text', '@Kho kiểm hàng', true]);
  const byLid = a.normalise(waMessage({ message: { extendedTextMessage: { text: 'x', contextInfo: { mentionedJid: ['7777@lid'] } } } }));
  assert.equal(byLid.mentions_self, true);
  const other = a.normalise(waMessage({ message: { extendedTextMessage: { text: 'x', contextInfo: { mentionedJid: ['8411@s.whatsapp.net'] } } } }));
  assert.equal(other.mentions_self, false);
});

test('chuẩn hoá: ảnh, sticker, tệp, reaction, 1-1, tin của mình, bọc ephemeral, bỏ status/protocol', () => {
  const a = adapter();
  const img = a.normalise(waMessage({ message: { imageMessage: { caption: 'hàng về', mimetype: 'image/jpeg', mediaKey: new Uint8Array([1, 2, 3]) } } }));
  assert.deepEqual([img.kind, img.body_text], ['image', 'hàng về']);
  assert.equal(img.payload.message.imageMessage.mediaKey, undefined); // khoá giải mã KHÔNG vào payload thô (L5)
  const noCap = a.normalise(waMessage({ message: { imageMessage: { mimetype: 'image/jpeg' } } }));
  assert.equal(noCap.body_text, null);

  const st = a.normalise(waMessage({ message: { stickerMessage: { mimetype: 'image/webp' } } }));
  assert.deepEqual([st.kind, st.body_text], ['sticker', null]);

  const doc = a.normalise(waMessage({ message: { documentMessage: { fileName: 'hoa-don.pdf' } } }));
  assert.deepEqual([doc.kind, doc.body_text], ['file', 'hoa-don.pdf']);

  const rx = a.normalise(waMessage({ message: { reactionMessage: { key: { id: 'ABCD0' }, text: '👍' } } }));
  assert.deepEqual([rx.kind, rx.body_text], ['reaction', '👍']);

  const direct = a.normalise(waMessage({ remoteJid: '84903333333@s.whatsapp.net', participant: null }));
  assert.equal(direct.external_group_id, null);
  assert.equal(direct.sender_external_id, '84903333333@s.whatsapp.net');
  assert.equal(direct.sender_phone, '+84903333333');

  const lidDirect = a.normalise({ key: { remoteJid: '5555@lid', remoteJidAlt: '84904444444@s.whatsapp.net', fromMe: false, id: 'L1' }, pushName: 'Hà', message: { conversation: 'hi' }, messageTimestamp: 1758600000 });
  assert.equal(lidDirect.sender_external_id, '5555@lid');
  assert.equal(lidDirect.sender_phone, '+84904444444');

  const mine = a.normalise(waMessage({ fromMe: true, pushName: undefined }));
  assert.equal(mine.direction, 'outbound');
  assert.equal(mine.sender_external_id, '84909999999@s.whatsapp.net');
  assert.equal(mine.sender_name, 'Kho Gen');

  const eph = a.normalise(waMessage({ message: { ephemeralMessage: { message: { extendedTextMessage: { text: 'tự huỷ' } } } } }));
  assert.deepEqual([eph.kind, eph.body_text], ['text', 'tự huỷ']);

  const withSkd = a.normalise(waMessage({ message: { senderKeyDistributionMessage: { groupId: 'x' }, conversation: 'kèm khoá' } }));
  assert.equal(withSkd.body_text, 'kèm khoá');

  assert.equal(a.normalise(waMessage({ remoteJid: 'status@broadcast' })), null);
  assert.equal(a.normalise(waMessage({ message: { protocolMessage: { type: 3 } } })), null);
  const rev = a.normalise(waMessage({ message: { protocolMessage: { type: 0, key: { id: 'x' } } } }));
  assert.deepEqual([rev.kind, rev.revokes], ['system', 'x']); // thu hồi mang id tin bị thu hồi
  assert.equal(a.normalise({ ...waMessage({ message: null }), messageStubType: 27 }).kind, 'system');
});

test('trạng thái xác thực trong RAM: tuần tự hoá ↔ khôi phục, keys get/set', async () => {
  const { lib } = makeBaileysLib();
  const a = memoryAuthState(lib, null);
  a.state.creds.me = { id: '849@s.whatsapp.net' };
  await a.state.keys.set({ 'pre-key': { 1: { public: Buffer.from([9]) }, 2: null }, session: { x: Buffer.from('s') } });
  const ser = a.serialize();
  assert.deepEqual(JSON.parse(JSON.stringify(ser)), ser); // JSON thuần
  const b = memoryAuthState(lib, ser);
  assert.deepEqual(b.state.creds.noiseKey.public, Buffer.from([1, 2, 3]));
  assert.deepEqual(await b.state.keys.get('pre-key', ['1', '2']), { 1: { public: Buffer.from([9]) }, 2: null });
  assert.deepEqual((await b.state.keys.get('session', ['x'])).x, Buffer.from('s'));
  assert.equal(BufferJSON.replacer('', Buffer.from([1])).type, 'Buffer');
});

test('QR: mỗi mã qr → session.qr data URL; ghép cặp → scanned; restartRequired → nối lại; open → active', async () => {
  const { bus, mgr, keyring, wa } = await setup();
  await mgr.handleControl(ctl('session.login', { channel: 'whatsapp', session_id: 'w1', credential: null }));
  const sock = await waitFor(() => wa.ctrl.last());
  assert.equal(sock.config.printQRInTerminal, false);
  assert.equal(sock.config.markOnlineOnConnect, false);
  sock.ev.emit('connection.update', { qr: '2@abc,def,ghi' });
  const qr = await waitFor(() => bus.of(STREAMS.status, 'session.qr')[0]);
  assert.match(qr.payload.image, /^data:image\/png;base64,/);
  assert.equal(qr.actor, 'bridge:whatsapp');

  sock.config.auth.creds.me = { id: '84909999999:3@s.whatsapp.net', name: 'Kho Gen' };
  sock.ev.emit('connection.update', { isNewLogin: true, qr: undefined });
  const sc = await waitFor(() => bus.of(STREAMS.status, 'session.scanned')[0]);
  assert.equal(sc.payload.display_name, 'Kho Gen');
  sock.close(515);
  await waitFor(() => wa.ctrl.sockets.length === 2);
  const sock2 = wa.ctrl.last();
  assert.equal(sock2.config.auth.creds, sock.config.auth.creds); // cùng trạng thái trong RAM
  sock2.open();
  const act = await waitFor(() => bus.of(STREAMS.status, 'session.active')[0]);
  assert.deepEqual(act.payload.account, { id: '84909999999@s.whatsapp.net', name: 'Kho Gen', phone: '+84909999999' });
  const cred = JSON.parse(keyring.decryptTransport(act.payload.credential, 'whatsapp:w1'));
  assert.equal(cred.creds.me.id, '84909999999:3@s.whatsapp.net');
  assert.deepEqual(cred.creds.noiseKey.public, { type: 'Buffer', data: 'AQID' });
  const dir = await waitFor(() => bus.of(STREAMS.directory, 'groups')[0]);
  assert.deepEqual(dir.payload.groups[0].members, [
    { external_id: '84901111111@s.whatsapp.net', name: '', phone: '+84901111111', role: 'admin' },
    { external_id: '5555@lid', name: '', phone: '+84902222222', role: 'member' },
  ]);
  mgr.shutdown();
});

test('QR: 5 mã hết hạn → session.ended expired (kể cả khi socket hết lượt ref)', async () => {
  const { bus, mgr, wa } = await setup();
  await mgr.handleControl(ctl('session.login', { channel: 'whatsapp', session_id: 'w1', credential: null }));
  const s1 = await waitFor(() => wa.ctrl.last());
  for (let i = 1; i <= 3; i += 1) s1.ev.emit('connection.update', { qr: `ref${i}` });
  await waitFor(() => bus.of(STREAMS.status, 'session.qr').length === 3);
  s1.close(408); // "QR refs attempts ended" → socket mới
  await waitFor(() => wa.ctrl.sockets.length === 2);
  const s2 = wa.ctrl.last();
  s2.ev.emit('connection.update', { qr: 'ref4' });
  s2.ev.emit('connection.update', { qr: 'ref5' });
  await waitFor(() => bus.of(STREAMS.status, 'session.qr').length === 5);
  s2.ev.emit('connection.update', { qr: 'ref6' }); // mã thứ 5 đã hết hạn
  const ended = await waitFor(() => bus.of(STREAMS.status, 'session.ended')[0]);
  assert.deepEqual(ended.payload, { channel: 'whatsapp', session_id: 'w1', reason: 'expired', error: 'QR_EXPIRED' });
  assert.equal(bus.of(STREAMS.status, 'session.qr').length, 5);
  assert.ok(s2.ended >= 1);
});

test('resume bằng credential; creds.update gộp → session.credential; loggedOut → expired', async () => {
  const { bus, mgr, keyring, wa } = await setup({ credentialCoalesceMs: 40 });
  const { lib } = makeBaileysLib();
  const st = memoryAuthState(lib, null);
  st.state.creds.me = { id: '84909999999:3@s.whatsapp.net', name: 'Kho Gen' };
  const blob = keyring.encryptTransport(JSON.stringify(st.serialize()), 'whatsapp:w1');
  await mgr.handleControl(ctl('session.login', { channel: 'whatsapp', session_id: 'w1', credential: blob }));
  const sock = await waitFor(() => wa.ctrl.last());
  assert.equal(sock.config.auth.creds.me.id, '84909999999:3@s.whatsapp.net');
  sock.open();
  await waitFor(() => bus.of(STREAMS.status, 'session.active')[0]);
  for (let i = 0; i < 5; i += 1) {
    sock.config.auth.creds.accountSyncCounter = i;
    sock.ev.emit('creds.update', { accountSyncCounter: i });
  }
  await tick(15);
  assert.equal(bus.of(STREAMS.status, 'session.credential').length, 0);
  const c = await waitFor(() => bus.of(STREAMS.status, 'session.credential')[0]);
  await tick(60);
  assert.equal(bus.of(STREAMS.status, 'session.credential').length, 1);
  assert.equal(JSON.parse(keyring.decryptTransport(c.payload.credential, 'whatsapp:w1')).creds.accountSyncCounter, 4);

  sock.close(401);
  const ended = await waitFor(() => bus.of(STREAMS.status, 'session.ended')[0]);
  assert.deepEqual([ended.payload.reason, ended.payload.error], ['expired', 'LOGGED_OUT_REMOTELY']);
  assert.equal(mgr.health().length, 0);
});

test('resume: credential chưa ghép cặp hoặc bị máy chủ từ chối → expired', async () => {
  const { bus, mgr, keyring, wa } = await setup();
  const { lib } = makeBaileysLib();
  const unpaired = keyring.encryptTransport(JSON.stringify(memoryAuthState(lib, null).serialize()), 'whatsapp:w1');
  await mgr.handleControl(ctl('session.login', { channel: 'whatsapp', session_id: 'w1', credential: unpaired }));
  const e1 = await waitFor(() => bus.of(STREAMS.status, 'session.ended')[0]);
  assert.deepEqual([e1.payload.reason, e1.payload.error], ['expired', 'CREDENTIAL_INVALID']);

  const st = memoryAuthState(lib, null);
  st.state.creds.me = { id: '849:1@s.whatsapp.net' };
  const paired = keyring.encryptTransport(JSON.stringify(st.serialize()), 'whatsapp:w2');
  await mgr.handleControl(ctl('session.login', { channel: 'whatsapp', session_id: 'w2', credential: paired }));
  const sock = await waitFor(() => wa.ctrl.last());
  sock.close(401);
  const e2 = await waitFor(() => bus.of(STREAMS.status, 'session.ended').find((e) => e.payload.session_id === 'w2'));
  assert.deepEqual([e2.payload.reason, e2.payload.error], ['expired', 'CREDENTIAL_REJECTED']);
});

test('mất kết nối tạm thời → nối lại, không phát ended; logout → sock.logout + logged_out', async () => {
  const { bus, mgr, keyring, wa } = await setup();
  const { lib } = makeBaileysLib();
  const st = memoryAuthState(lib, null);
  st.state.creds.me = { id: '849:1@s.whatsapp.net' };
  await mgr.handleControl(ctl('session.login', { channel: 'whatsapp', session_id: 'w1',
    credential: keyring.encryptTransport(JSON.stringify(st.serialize()), 'whatsapp:w1') }));
  const s1 = await waitFor(() => wa.ctrl.last());
  s1.open();
  await waitFor(() => bus.of(STREAMS.status, 'session.active')[0]);
  s1.close(428);
  await waitFor(() => wa.ctrl.sockets.length === 2);
  const s2 = wa.ctrl.last();
  s2.open();
  await tick(5);
  assert.equal(bus.of(STREAMS.status, 'session.active').length, 1);
  assert.equal(bus.of(STREAMS.status, 'session.ended').length, 0);

  await mgr.handleControl(ctl('session.logout', { channel: 'whatsapp', session_id: 'w1' }));
  assert.equal(s2.loggedOut, 1);
  assert.ok(s2.ended >= 1);
  const ended = bus.of(STREAMS.status, 'session.ended');
  assert.deepEqual(ended.map((e) => e.payload.reason), ['logged_out']);
  s2.close(401); // sự kiện muộn sau logout bị bỏ qua
  await tick(5);
  assert.equal(bus.of(STREAMS.status, 'session.ended').length, 1);
});

test('messages.upsert: chỉ type notify; gửi tin trả key.id', async () => {
  const { bus, mgr, keyring, wa } = await setup();
  bus.listen.whatsapp = new Set(['120363@g.us']);
  const { lib } = makeBaileysLib();
  const st = memoryAuthState(lib, null);
  st.state.creds.me = { id: '849:1@s.whatsapp.net' };
  await mgr.handleControl(ctl('session.login', { channel: 'whatsapp', session_id: 'w1',
    credential: keyring.encryptTransport(JSON.stringify(st.serialize()), 'whatsapp:w1') }));
  const sock = await waitFor(() => wa.ctrl.last());
  sock.open();
  await waitFor(() => bus.of(STREAMS.status, 'session.active')[0]);
  sock.ev.emit('messages.upsert', { type: 'append', messages: [waMessage({ id: 'H1' })] });
  sock.ev.emit('messages.upsert', { type: 'notify', messages: [waMessage({ id: 'N1' }), waMessage({ id: 'N1' })] });
  await waitFor(() => bus.of(STREAMS.inbound).length === 1);
  await tick(5);
  assert.deepEqual(bus.of(STREAMS.inbound).map((e) => e.payload.external_msg_id), ['N1']);
  const a = mgr.sessions.get('whatsapp:w1').adapter;
  assert.equal(await a.send('120363@g.us', 'group', 'ok'), '3EB01');
  assert.deepEqual(sock.sent, [{ jid: '120363@g.us', content: { text: 'ok' } }]);
  mgr.shutdown();
});

// ─── v0.1.59: WhatsApp quét QR chưa được ───────────────────────────────────────────────────────────────────────────

const WA_URL = 'https://wa.me/settings/linked_devices#';
const waQr = (ref, adv = ADV0) => `${WA_URL}${ref},bm9pc2U=,aWRlbnQ=,${adv},1`;
const refreshNode = (over = {}) => ({ tag: 'notification', attrs: { type: 'companion_reg_refresh', id: 'n1' }, content: undefined, ...over });
const advOf = (qrText) => qrText.slice(qrText.indexOf('#') + 1).split(',')[3];

async function startQr(opts) {
  const env = await setup(opts);
  await env.mgr.handleControl(ctl('session.login', { channel: 'whatsapp', session_id: 'w1', credential: null }));
  const sock = await waitFor(() => env.wa.ctrl.last());
  return { ...env, sock, adapter: () => env.mgr.sessions.get('whatsapp:w1').adapter };
}

test('companion_reg_refresh: đổi khoá ADV, dựng lại ĐÚNG mã đang hiện, không tốn thêm mã; mã sau của thư viện cũng dùng khoá mới', async () => {
  const { bus, wa, sock, adapter, mgr } = await startQr();
  sock.ev.emit('connection.update', { qr: waQr('REF1') });
  await waitFor(() => bus.of(STREAMS.status, 'session.qr').length === 1);
  const first = bus.of(STREAMS.status, 'session.qr')[0].payload;
  assert.equal(advOf(wa.ctrl.qrTexts[0]), ADV0);

  sock.ws.emit('CB:notification,type:companion_reg_refresh', refreshNode());
  await waitFor(() => bus.of(STREAMS.status, 'session.qr').length === 2);
  const second = bus.of(STREAMS.status, 'session.qr')[1].payload;
  const rotated = sock.config.auth.creds.advSecretKey;
  assert.notEqual(rotated, ADV0);
  assert.equal(Buffer.from(rotated, 'base64').length, 32);
  assert.equal(advOf(wa.ctrl.qrTexts[1]), rotated);
  assert.equal(wa.ctrl.qrTexts[1].split('#')[1].split(',')[0], 'REF1'); // cùng ref, chỉ đổi khoá
  assert.equal(second.expires_at, first.expires_at); // không kéo dài hạn của mã
  assert.equal(adapter().qrCount, 1); // làm mới không tốn lượt (qrMax)

  // Baileys chốt khoá cũ trong closure: mã kế tiếp của thư viện vẫn mang ADV0 → adapter thay bằng khoá hiện hành.
  sock.ev.emit('connection.update', { qr: waQr('REF2', ADV0) });
  await waitFor(() => bus.of(STREAMS.status, 'session.qr').length === 3);
  assert.equal(advOf(wa.ctrl.qrTexts[2]), rotated);
  mgr.shutdown();
});

test('companion_reg_refresh TRƯỚC mã QR đầu: vẫn đổi khoá ADV (không phát qr thừa); mã đầu tiên mang khoá mới', async () => {
  const { bus, wa, sock, adapter, mgr } = await startQr();
  sock.ws.emit('CB:notification,type:companion_reg_refresh', refreshNode()); // chưa có mã nào để dựng lại
  await waitFor(() => sock.config.auth.creds.advSecretKey !== ADV0);
  const rotated = sock.config.auth.creds.advSecretKey;
  assert.equal(Buffer.from(rotated, 'base64').length, 32);
  await tick(15);
  assert.equal(bus.of(STREAMS.status, 'session.qr').length, 0); // chưa có mã ⇒ không có gì để phát lại
  assert.equal(wa.ctrl.qrTexts.length, 0);
  assert.equal(adapter().qrCount, 0);

  // Baileys đọc creds.advSecretKey lúc nhận iq pair-device ⇒ mã đầu tiên đã mang khoá mới; kể cả mã còn mang khoá cũ
  // (chốt trong closure) thì adapter cũng thay bằng khoá hiện hành.
  sock.ev.emit('connection.update', { qr: waQr('REF1', ADV0) });
  await waitFor(() => bus.of(STREAMS.status, 'session.qr').length === 1);
  assert.equal(advOf(wa.ctrl.qrTexts[0]), rotated);
  assert.equal(adapter().qrCount, 1);
  mgr.shutdown();
});

test('companion_reg_refresh: nhận qua nút con, bỏ trùng; không xoay khi đã ghép cặp hoặc thông báo khác', async () => {
  const { bus, wa, sock, mgr } = await startQr();
  sock.ev.emit('connection.update', { qr: waQr('REF1') });
  await waitFor(() => bus.of(STREAMS.status, 'session.qr').length === 1);
  sock.ws.emit('CB:notification,,devices', { tag: 'notification', attrs: { type: 'devices' }, content: [{ tag: 'add', attrs: {} }] });
  await tick(10);
  assert.equal(bus.of(STREAMS.status, 'session.qr').length, 1);
  assert.equal(sock.config.auth.creds.advSecretKey, ADV0); // thông báo khác: không đụng khoá

  const node = refreshNode({ attrs: { type: 'other' }, content: [{ tag: 'pair-device-rotate-qr', attrs: {} }] });
  sock.ws.emit('CB:notification,,pair-device-rotate-qr', node);
  sock.ws.emit('CB:notification,,companion_reg_refresh', node); // cùng một nút đến qua hai tên sự kiện
  await waitFor(() => bus.of(STREAMS.status, 'session.qr').length === 2);
  await tick(15);
  assert.equal(bus.of(STREAMS.status, 'session.qr').length, 2);
  assert.equal(wa.ctrl.qrTexts.length, 2);

  sock.config.auth.creds.me = { id: '84909999999:3@s.whatsapp.net' }; // đã ghép cặp: giữ nguyên khoá
  const before = sock.config.auth.creds.advSecretKey;
  sock.ws.emit('CB:notification,type:companion_reg_refresh', refreshNode({ attrs: { type: 'companion_reg_refresh', id: 'n2' } }));
  await tick(15);
  assert.equal(sock.config.auth.creds.advSecretKey, before);
  assert.equal(bus.of(STREAMS.status, 'session.qr').length, 2);
  mgr.shutdown();
});

test('waVersionHeaders: user-agent tự nhận, không giả trình duyệt', () => {
  const h = waVersionHeaders();
  assert.match(h['user-agent'], /^Gen-Harness-bridge\/\d+\.\d+\.\d+/);
  assert.doesNotMatch(h['user-agent'], /Mozilla|Chrome|Safari|AppleWebKit/i);
  assert.equal(h['sec-fetch-site'], 'cross-site');
});

test('isCompanionRefresh / noQrErrorCode / pickWaVersion', () => {
  assert.equal(isCompanionRefresh(refreshNode()), true);
  assert.equal(isCompanionRefresh({ tag: 'notification', attrs: { type: 'x' }, content: [{ tag: 'companion_reg_refresh' }] }), true);
  assert.equal(isCompanionRefresh({ tag: 'notification', attrs: { type: 'devices' }, content: [{ tag: 'add' }] }), false);
  assert.equal(isCompanionRefresh({ tag: 'message', attrs: { type: 'companion_reg_refresh' } }), false);
  assert.equal(isCompanionRefresh(null), false);

  assert.equal(noQrErrorCode(408, 'WebSocket Error (getaddrinfo ENOTFOUND web.whatsapp.com)'), 'WA_NETWORK');
  assert.equal(noQrErrorCode(408, 'Connection Timed Out'), 'WA_NETWORK');
  assert.equal(noQrErrorCode(405, 'Connection Failure'), 'WA_NO_QR_405');
  assert.equal(noQrErrorCode(undefined, undefined), 'WA_NO_QR_0');

  const bundled = [2, 3000, 1043857760];
  assert.deepEqual(pickWaVersion({ isLatest: true, version: [2, 3000, 1045716975] }, bundled), [2, 3000, 1045716975]);
  assert.deepEqual(pickWaVersion({ isLatest: true, version: bundled }, bundled), bundled);
  assert.equal(pickWaVersion({ isLatest: true, version: [2, 3000, 1000000000] }, bundled), undefined); // cũ hơn bản đã biết tốt
  assert.equal(pickWaVersion({ isLatest: false, version: [2, 3000, 1045716975] }, bundled), undefined); // lấy lỗi → bản đóng gói
  assert.equal(pickWaVersion({ isLatest: true, version: [2, 3000, 'x'] }, bundled), undefined);
  assert.equal(pickWaVersion(undefined, bundled), undefined);
});

test('socket đóng trước khi ra mã QR (405): thử lại có giãn cách rồi báo lỗi thật, không quay vòng vô hạn', async () => {
  const { bus, wa, mgr } = await startQr();
  for (let i = 1; i <= 3; i += 1) {
    await waitFor(() => wa.ctrl.sockets.length === i);
    wa.ctrl.last().close(405, 'Connection Failure');
  }
  await waitFor(() => wa.ctrl.sockets.length === 4);
  assert.equal(bus.of(STREAMS.status, 'session.ended').length, 0);
  wa.ctrl.last().close(405, 'Connection Failure');
  const ended = await waitFor(() => bus.of(STREAMS.status, 'session.ended')[0]);
  assert.deepEqual(ended.payload, { channel: 'whatsapp', session_id: 'w1', reason: 'error', error: 'WA_NO_QR_405' });
  await tick(30);
  assert.equal(wa.ctrl.sockets.length, 4); // đã dừng, không mở thêm socket
  assert.equal(bus.of(STREAMS.status, 'session.qr').length, 0);
  assert.equal(mgr.health().length, 0);
});

test('socket đóng trước khi ra mã QR vì không tới được máy chủ WhatsApp → WA_NETWORK', async () => {
  const { bus, wa } = await startQr();
  for (let i = 1; i <= 4; i += 1) {
    await waitFor(() => wa.ctrl.sockets.length === i);
    wa.ctrl.last().close(408, 'WebSocket Error (getaddrinfo ENOTFOUND web.whatsapp.com)');
  }
  const ended = await waitFor(() => bus.of(STREAMS.status, 'session.ended')[0]);
  assert.deepEqual([ended.payload.reason, ended.payload.error], ['error', 'WA_NETWORK']);
});

test('đã ra được mã QR thì bộ đếm lỗi trước-QR về 0 (socket hết lượt ref không bị tính là lỗi)', async () => {
  const { bus, wa, sock } = await startQr();
  sock.close(405); // 1 lần lỗi trước QR
  await waitFor(() => wa.ctrl.sockets.length === 2);
  const s2 = wa.ctrl.last();
  s2.ev.emit('connection.update', { qr: waQr('REF1') });
  await waitFor(() => bus.of(STREAMS.status, 'session.qr').length === 1);
  s2.close(408); // hết ref → socket mới ngay
  await waitFor(() => wa.ctrl.sockets.length === 3);
  for (let i = 3; i <= 5; i += 1) {
    await waitFor(() => wa.ctrl.sockets.length === i);
    wa.ctrl.last().close(405);
  }
  await waitFor(() => wa.ctrl.sockets.length === 6); // 3 lần lỗi liên tiếp sau QR vẫn còn lượt thử
  assert.equal(bus.of(STREAMS.status, 'session.ended').length, 0);
});

test('401 khi đang ghép cặp bằng QR → PAIRING_REJECTED (không nhầm với phiên đã lưu bị từ chối)', async () => {
  const { bus, sock } = await startQr();
  sock.ev.emit('connection.update', { qr: waQr('REF1') });
  await waitFor(() => bus.of(STREAMS.status, 'session.qr').length === 1);
  sock.close(401);
  const ended = await waitFor(() => bus.of(STREAMS.status, 'session.ended')[0]);
  assert.deepEqual([ended.payload.reason, ended.payload.error], ['expired', 'PAIRING_REJECTED']);
});

test('phiên bản WA Web: lấy theo từng lần đăng nhập; lỗi/không có → để thư viện dùng bản đóng gói', async () => {
  const wa = makeBaileysLib();
  let calls = 0;
  wa.lib.getVersion = async () => { calls += 1; return [2, 3000, 1045716975]; };
  const { mgr } = await setup({ wa });
  await mgr.handleControl(ctl('session.login', { channel: 'whatsapp', session_id: 'w1', credential: null }));
  assert.deepEqual((await waitFor(() => wa.ctrl.last())).config.version, [2, 3000, 1045716975]);
  await mgr.handleControl(ctl('session.login', { channel: 'whatsapp', session_id: 'w2', credential: null }));
  await waitFor(() => wa.ctrl.sockets.length === 2);
  assert.equal(calls, 2);
  mgr.shutdown();

  const wb = makeBaileysLib();
  wb.lib.getVersion = async () => { throw new Error('mạng'); };
  const env = await setup({ wa: wb });
  await env.mgr.handleControl(ctl('session.login', { channel: 'whatsapp', session_id: 'w1', credential: null }));
  const s = await waitFor(() => wb.ctrl.last());
  assert.equal('version' in s.config, false);
  env.mgr.shutdown();
});

// ─── Baileys thật: hợp đồng API mà adapter dựa vào (bắt sớm khi nâng phiên bản) ────────────────────────────────────

test('Baileys thật: các hàm/hằng adapter cần còn đủ, ghim đúng phiên bản', async () => {
  const b = await import('@whiskeysockets/baileys');
  const pkg = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'));
  assert.match(pkg.dependencies['@whiskeysockets/baileys'], /^\d+\.\d+\.\d+(-[\w.]+)?$/, 'phải ghim CHÍNH XÁC, không dùng ^ hay ~');
  assert.equal(typeof b.makeWASocket, 'function');
  assert.equal(typeof b.fetchLatestWaWebVersion, 'function');
  assert.equal(typeof b.initAuthCreds, 'function');
  assert.equal(b.DisconnectReason.restartRequired, 515);
  assert.equal(b.DisconnectReason.loggedOut, 401);
  assert.equal(b.DisconnectReason.forbidden, 403);
  assert.equal(b.DisconnectReason.connectionClosed, 428);
  assert.ok(Array.isArray(b.DEFAULT_CONNECTION_CONFIG.version) && b.DEFAULT_CONNECTION_CONFIG.version.length === 3);
  const creds = b.initAuthCreds();
  assert.equal(Buffer.from(creds.advSecretKey, 'base64').length, 32); // khoá ADV nằm trong creds, đổi được tại chỗ
});

test('Baileys thật: getVersion lấy client_revision từ sw.js, nhớ 30 phút, chặn bản cũ hơn bản đóng gói, lỗi → undefined', async () => {
  const realFetch = globalThis.fetch;
  const hits = [];
  let body = 'x{"client_revision":1049999999}y';
  let ok = true;
  const sent = [];
  globalThis.fetch = async (url, init) => {
    hits.push(String(url));
    sent.push(init?.headers || {});
    return { ok, statusText: 'X', text: async () => body };
  };
  try {
    const lib = await loadWhatsAppLib();
    assert.deepEqual(await lib.getVersion(), [2, 3000, 1049999999]);
    assert.deepEqual(await lib.getVersion(), [2, 3000, 1049999999]); // trong 30 phút: không gọi lại
    assert.deepEqual(hits, ['https://web.whatsapp.com/sw.js']);
    // Tự nhận là bridge Gen-Harness: KHÔNG giả user-agent trình duyệt, KHÔNG giữ mặc định "qua mặt chống bot" của Baileys.
    assert.equal(sent.length, 1);
    assert.equal(sent[0]['user-agent'], `Gen-Harness-bridge/${VERSION}`);
    assert.equal(sent[0]['sec-fetch-site'], 'cross-site');
    assert.doesNotMatch(String(sent[0]['user-agent']), /Mozilla|Chrome|Safari|AppleWebKit/i);

    const stale = await loadWhatsAppLib();
    body = '{"client_revision":1000000}';
    assert.equal(await stale.getVersion(), undefined); // cũ hơn bản đóng gói → bỏ
    const failing = await loadWhatsAppLib();
    ok = false;
    assert.equal(await failing.getVersion(), undefined); // HTTP lỗi → bản đóng gói
  } finally {
    globalThis.fetch = realFetch;
  }
});

test('Baileys thật: ảnh QR WhatsApp đủ lớn để đọc được khi thu nhỏ (≥ 8px/module, vùng trắng 4 module, mức sửa lỗi L)', async () => {
  const lib = await loadWhatsAppLib();
  const ref = `2@${Buffer.from(Array.from({ length: 70 }, (_, i) => (i * 37 + 11) % 256)).toString('base64').replace(/=/g, '')}`;
  const key = (n) => Buffer.alloc(32, n).toString('base64');
  const text = `${WA_URL}${ref},${key(5)},${key(6)},${key(9)},1`;
  const url = await lib.toDataURL(text);
  assert.match(url, /^data:image\/png;base64,/);
  const png = Buffer.from(url.split(',')[1], 'base64');
  const width = png.readUInt32BE(16); // IHDR: rộng
  const QR = (await import('qrcode')).default;
  const modules = QR.create(text, { errorCorrectionLevel: 'L' }).modules.size;
  assert.ok(modules <= 61, `mã quá dày: ${modules} module`);
  assert.ok(width / (modules + 8) >= 8, `ảnh ${width}px cho ${modules}+8 module: dưới 8px/module`);
  assert.ok(png.length < 40000, 'ảnh quá nặng cho Redis/WebSocket');
});


// ─── L5: voice / ảnh / chuyển tiếp / tải file an toàn ─────────────────────────────────────────────────────────────

const voiceMsg = (over = {}) => waMessage({
  id: 'VOICE1',
  message: { audioMessage: { mimetype: 'audio/ogg; codecs=opus', fileLength: 2048, seconds: 12, ptt: true, url: 'https://evil.example/x?sig=SECRET',
    directPath: '/v/t62.7117-24/abc_def?ccb=11-4&oh=01_X&oe=ABC', mediaKey: new Uint8Array([1, 2, 3]), contextInfo: { isForwarded: true, forwardingScore: 2 } } },
});

function activeAdapter() {
  const wa = makeBaileysLib();
  const a = new WhatsAppAdapter({ lib: wa.lib, sessionId: 'w1' });
  a.own = { jid: '84909999999@s.whatsapp.net', lid: '7777@lid', name: 'Kho Gen' };
  a.state = 'active';
  a.sock = {};
  return { a, wa };
}

test('L5 chuẩn hoá: voice → kind voice + media_ref + mô tả khai báo; không rò url/khoá vào payload; cờ chuyển tiếp thật', () => {
  const a = adapter();
  const p = a.normalise(voiceMsg());
  assert.equal(p.kind, 'voice');
  assert.equal(p.body_text, null);
  assert.equal(p.media_ref, 'whatsapp:w1:VOICE1');
  assert.deepEqual(p.media, { kind: 'voice', mime: 'audio/ogg', size: 2048, seconds: 12, ptt: true });
  assert.equal(p.forwarded, true);
  const raw = JSON.stringify(p);
  for (const bad of ['evil.example', 'SECRET', 'directPath', 'mediaKey', 'ccb=11']) assert.equal(raw.includes(bad), false, bad);
  assert.equal(a.media.size, 1); // chỉ sổ RAM giữ khoá để tải
});

test('L5 chuẩn hoá: ảnh có media_ref; ảnh không khai mime/size vẫn có ref', () => {
  const a = adapter();
  const p = a.normalise(waMessage({ id: 'IMG1', message: { imageMessage: { caption: 'hoá đơn', mimetype: 'image/jpeg', fileLength: '4000', mediaKey: new Uint8Array([1]), directPath: '/v/x' } } }));
  assert.deepEqual([p.kind, p.body_text, p.media_ref, p.media.size], ['image', 'hoá đơn', 'whatsapp:w1:IMG1', 4000]);
});

test('L5 fetchMedia: tải qua phiên, ép host mặc định, KHÔNG chuyển url của người gửi', async () => {
  const { a, wa } = activeAdapter();
  const p = a.normalise(voiceMsg());
  const got = await a.fetchMedia(p.media_ref);
  assert.equal(got.bytes.toString(), 'OggS-fake-voice');
  assert.equal(got.mime, 'audio/ogg');
  const call = wa.ctrl.downloads[0];
  assert.deepEqual(Object.keys(call.desc).sort(), ['directPath', 'mediaKey']); // không có `url`
  assert.equal(call.opts.host, 'mmg.whatsapp.net');
  assert.equal(call.type, 'audio');
});

test('L5 fetchMedia: ref lạ / hết hạn → not_found; quá lớn khai báo → too_large (không tải); mime lạ; vượt trần khi tải; directPath bẩn', async () => {
  const { a, wa } = activeAdapter();
  await assert.rejects(a.fetchMedia('whatsapp:w1:NOPE'), { code: 'not_found' });
  await assert.rejects(a.fetchMedia('https://evil.example/x'), { code: 'not_found' }); // URL không bao giờ là ref hợp lệ

  const big = a.normalise(waMessage({ id: 'BIG', message: { audioMessage: { mimetype: 'audio/ogg', fileLength: 6 * 1024 * 1024, seconds: 5, directPath: '/v/x', mediaKey: new Uint8Array([1]) } } }));
  await assert.rejects(a.fetchMedia(big.media_ref), { code: 'too_large' });
  const long = a.normalise(waMessage({ id: 'LONG', message: { audioMessage: { mimetype: 'audio/ogg', fileLength: 100, seconds: 601, directPath: '/v/x', mediaKey: new Uint8Array([1]) } } }));
  await assert.rejects(a.fetchMedia(long.media_ref), { code: 'too_large' });
  const exe = a.normalise(waMessage({ id: 'EXE', message: { audioMessage: { mimetype: 'application/x-msdownload', fileLength: 10, directPath: '/v/x', mediaKey: new Uint8Array([1]) } } }));
  await assert.rejects(a.fetchMedia(exe.media_ref), { code: 'mime_not_allowed' });
  assert.equal(wa.ctrl.downloads.length, 0); // ba ca trên không chạm mạng

  const lying = a.normalise(waMessage({ id: 'LIE', message: { imageMessage: { mimetype: 'image/png', fileLength: 10, directPath: '/v/x', mediaKey: new Uint8Array([1]) } } }));
  wa.ctrl.mediaBytes = Buffer.alloc(8 * 1024 * 1024 + 1);
  await assert.rejects(a.fetchMedia(lying.media_ref), { code: 'too_large' }); // khai 10 byte nhưng thực tế vượt trần ⇒ cắt

  wa.ctrl.mediaBytes = Buffer.from('x');
  const evil = a.normalise(waMessage({ id: 'EV', message: { audioMessage: { mimetype: 'audio/ogg', directPath: '//evil.example/../x', mediaKey: new Uint8Array([1]) } } }));
  const bad = a.normalise(waMessage({ id: 'EV2', message: { audioMessage: { mimetype: 'audio/ogg', directPath: 'https://evil.example/x', mediaKey: new Uint8Array([1]) } } }));
  await assert.rejects(a.fetchMedia(bad.media_ref), { code: 'bad_request' }); // directPath phải bắt đầu bằng "/"
  void evil;

  wa.ctrl.downloadError = new Error('403 https://mmg.whatsapp.net/secret?oh=TOKEN');
  const ok = a.normalise(voiceMsg());
  await assert.rejects(a.fetchMedia(ok.media_ref), (e) => e.code === 'download_failed' && !String(e.message).includes('TOKEN'));
});

test('L5 fetchMedia: phiên chưa hoạt động → session_inactive', async () => {
  const { a } = activeAdapter();
  const p = a.normalise(voiceMsg());
  a.state = 'connecting';
  await assert.rejects(a.fetchMedia(p.media_ref), { code: 'session_inactive' });
});

test('L5 media.fetch qua SessionManager: trả bytes + sha256 vào danh sách kết quả; ref lạ/URL/request_id sai không chạm mạng', async () => {
  const { createHash } = await import('node:crypto');
  const { bus, mgr, keyring, wa } = await setup();
  const { lib } = makeBaileysLib();
  const st = memoryAuthState(lib, null);
  st.state.creds.me = { id: '84909999999:3@s.whatsapp.net', name: 'Kho Gen' };
  await mgr.handleControl(ctl('session.login', { channel: 'whatsapp', session_id: 'w1',
    credential: keyring.encryptTransport(JSON.stringify(st.serialize()), 'whatsapp:w1') }));
  const sock = await waitFor(() => wa.ctrl.last());
  sock.open();
  await waitFor(() => bus.of(STREAMS.status, 'session.active')[0]);
  bus.listen.whatsapp = new Set(['120363@g.us']);
  sock.ev.emit('messages.upsert', { type: 'notify', messages: [voiceMsg()] });
  const inb = await waitFor(() => bus.of(STREAMS.inbound, 'message')[0]);
  assert.equal(inb.payload.media_ref, 'whatsapp:w1:VOICE1');

  const rid = '0199b2c4-0000-7000-8000-000000000001';
  await mgr.handleControl(ctl('media.fetch', { request_id: rid, media_ref: inb.payload.media_ref }));
  const r = bus.mediaResults.find((x) => x.requestId === rid).result;
  assert.deepEqual([r.ok, r.size, r.mime, r.request_id], [true, 15, 'audio/ogg', rid]);
  assert.equal(Buffer.from(r.data_b64, 'base64').toString(), 'OggS-fake-voice');
  assert.equal(r.sha256, createHash('sha256').update('OggS-fake-voice').digest('hex'));

  const rid2 = '0199b2c4-0000-7000-8000-000000000002';
  await mgr.handleControl(ctl('media.fetch', { request_id: rid2, media_ref: 'https://evil.example/x' }));
  assert.deepEqual(bus.mediaResults.find((x) => x.requestId === rid2).result, { request_id: rid2, ok: false, error: 'bad_request' });
  const rid3 = '0199b2c4-0000-7000-8000-000000000003';
  await mgr.handleControl(ctl('media.fetch', { request_id: rid3, media_ref: 'whatsapp:w1:KHONGCO' }));
  assert.equal(bus.mediaResults.find((x) => x.requestId === rid3).result.error, 'not_found');
  const before = bus.mediaResults.length;
  await mgr.handleControl(ctl('media.fetch', { request_id: 'a:b', media_ref: inb.payload.media_ref }));
  assert.equal(bus.mediaResults.length, before); // request_id sai khuôn bị bỏ, không tạo khoá Redis
  assert.equal(wa.ctrl.downloads.length, 1);
  mgr.shutdown();
});
