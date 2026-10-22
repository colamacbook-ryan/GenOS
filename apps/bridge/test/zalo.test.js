import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ZaloAdapter } from '../src/channels/zalo.js';
import { STREAMS } from '../src/envelope.js';
import { ThreadType, ctl, makeZaloApi, makeZaloLib, setup, tick, waitFor, zaloMessage } from './fakes.js';

function adapter() {
  const a = new ZaloAdapter({ lib: makeZaloLib().lib, sessionId: 's1' });
  a.ownId = '900';
  return a;
}

test('chuẩn hoá: tin văn bản trong nhóm', () => {
  const a = adapter();
  a.groupNames.set('G1', 'Nhóm bán hàng');
  const p = a.normalise(zaloMessage({ mentions: [{ uid: '900', pos: 0, len: 4, type: 0 }] }));
  assert.deepEqual({ ...p, payload: undefined }, {
    channel: 'zalo', session_id: 's1', external_msg_id: '5001', external_group_id: 'G1', group_name: 'Nhóm bán hàng',
    sender_external_id: '111', sender_name: 'Lan', occurred_at: new Date(1758600000000).toISOString(),
    kind: 'text', body_text: 'xin chào', direction: 'inbound', mentions_self: true, replies_to_self: false, payload: undefined,
  });
  assert.equal(p.payload.data.content, 'xin chào');
  assert.equal(p.payload.threadId, 'G1');
});

test('chuẩn hoá: 1-1, ảnh, sticker, tệp, link, tin của chính mình', () => {
  const a = adapter();
  const direct = a.normalise(zaloMessage({ group: false, threadId: '111' }));
  assert.equal(direct.external_group_id, null);
  assert.equal(direct.mentions_self, false);
  assert.equal('group_name' in direct, false);

  const img = a.normalise(zaloMessage({ msgType: 'chat.photo', content: { title: 'ảnh hàng', href: 'https://f/x.jpg', thumb: '' } }));
  assert.equal(img.kind, 'image');
  assert.equal(img.body_text, 'ảnh hàng');

  const st = a.normalise(zaloMessage({ msgType: 'chat.sticker', content: { id: 1, catId: 2, type: 7 } }));
  assert.equal(st.kind, 'sticker');
  assert.equal(st.body_text, null);

  const file = a.normalise(zaloMessage({ msgType: 'share.file', content: { title: 'bao-gia.pdf', href: 'https://f/b.pdf' } }));
  assert.deepEqual([file.kind, file.body_text], ['file', 'bao-gia.pdf']);

  const link = a.normalise(zaloMessage({ msgType: 'chat.link', content: { title: 'Trang', href: 'https://x.vn' } }));
  assert.deepEqual([link.kind, link.body_text], ['text', 'Trang https://x.vn']);

  const voice = a.normalise(zaloMessage({ msgType: 'chat.voice', content: { href: 'https://v/a.aac' } }));
  assert.deepEqual([voice.kind, voice.body_text], ['other', null]);

  const self = a.normalise(zaloMessage({ isSelf: true, uidFrom: '900' }));
  assert.equal(self.direction, 'outbound');
  assert.equal(self.sender_external_id, '900');

  const notMe = a.normalise(zaloMessage({ mentions: [{ uid: '123', pos: 0, len: 3, type: 0 }] }));
  assert.equal(notMe.mentions_self, false);
});

test('chuẩn hoá: reaction zca-js', () => {
  const a = adapter();
  const p = a.normalise({ threadId: 'G1', isSelf: false, isGroup: true,
    data: { msgId: '6001', uidFrom: '111', dName: 'Lan', ts: '1758600000000', content: { rIcon: '/-heart', rType: 5, rMsg: [{ gMsgID: '5001' }] } } });
  assert.deepEqual([p.kind, p.body_text, p.external_group_id], ['reaction', '/-heart', 'G1']);
});

test('QR: QRCodeGenerated → session.qr (data URL) · Scanned → session.scanned · GotLoginInfo → session.active mã hoá', async () => {
  const { bus, mgr, keyring, zalo } = await setup();
  await mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's1', credential: null }));
  const qr = await waitFor(() => bus.of(STREAMS.status, 'session.qr')[0]);
  assert.equal(qr.payload.image, 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==');
  assert.ok(Date.parse(qr.payload.expires_at) > Date.now() + 90000);
  assert.equal(qr.actor, 'bridge:zalo');
  assert.equal(qr.orgId, 'org-1');
  assert.equal(qr.correlationId, 'corr-session.login');
  assert.equal(zalo.ctrl.instances[0].options.selfListen, true);
  assert.equal(zalo.ctrl.instances[0].options.logging, false);

  zalo.ctrl.scan();
  const sc = await waitFor(() => bus.of(STREAMS.status, 'session.scanned')[0]);
  assert.deepEqual(sc.payload, { channel: 'zalo', session_id: 's1', display_name: 'Chủ shop' });

  zalo.ctrl.confirm();
  const act = await waitFor(() => bus.of(STREAMS.status, 'session.active')[0]);
  assert.deepEqual(act.payload.account, { id: '900', name: 'Cửa hàng A', phone: '84900000000' });
  const cred = JSON.parse(keyring.decryptTransport(act.payload.credential, 'zalo:s1').toString());
  assert.deepEqual(cred, { cookie: [{ key: 'zpw', value: 'secret' }], imei: 'imei-1', userAgent: 'UA' });
  assert.equal(zalo.ctrl.api.listener.started, 1);
  assert.deepEqual(mgr.health()[0].state, 'active');
  // Danh bạ đồng bộ ngay khi đăng nhập xong
  await waitFor(() => bus.of(STREAMS.directory, 'groups')[0]);
  mgr.shutdown();
});

test('QR: hết hạn 5 lần → session.ended expired, không xin QR thứ 6', async () => {
  const { bus, mgr, zalo } = await setup();
  await mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's1', credential: null }));
  for (let i = 1; i <= 5; i += 1) {
    await waitFor(() => bus.of(STREAMS.status, 'session.qr').length === i);
    zalo.ctrl.expire();
  }
  const ended = await waitFor(() => bus.of(STREAMS.status, 'session.ended')[0]);
  assert.deepEqual(ended.payload, { channel: 'zalo', session_id: 's1', reason: 'expired', error: 'QR_EXPIRED' });
  await tick(10);
  assert.equal(zalo.ctrl.qrGenerated, 5);
  assert.equal(bus.of(STREAMS.status, 'session.qr').length, 5);
  assert.equal(mgr.health().length, 0);
});

test('QR bị từ chối trên điện thoại → QR mới (tính vào giới hạn)', async () => {
  const { bus, mgr, zalo } = await setup();
  await mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's1', credential: null }));
  await waitFor(() => bus.of(STREAMS.status, 'session.qr').length === 1);
  zalo.ctrl.decline();
  await waitFor(() => bus.of(STREAMS.status, 'session.qr').length === 2);
  mgr.shutdown();
});

test('đăng nhập lại bằng credential đã lưu; credential hỏng / bị từ chối → expired', async () => {
  const { bus, mgr, keyring, zalo } = await setup();
  const cred = { imei: 'imei-1', cookie: [{ key: 'zpw', value: 'v' }], userAgent: 'UA' };
  const blob = keyring.encryptTransport(JSON.stringify(cred), 'zalo:s1');
  await mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's1', credential: blob }));
  const act = await waitFor(() => bus.of(STREAMS.status, 'session.active')[0]);
  assert.deepEqual(zalo.ctrl.loginCalls[0], cred);
  assert.deepEqual(JSON.parse(keyring.decryptTransport(act.payload.credential, 'zalo:s1')), cred);
  assert.equal(bus.of(STREAMS.status, 'session.qr').length, 0);

  // Credential sai AAD (của phiên khác) → không giải được → expired
  await mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's2', credential: blob }));
  const e2 = await waitFor(() => bus.of(STREAMS.status, 'session.ended').find((e) => e.payload.session_id === 's2'));
  assert.deepEqual([e2.payload.reason, e2.payload.error], ['expired', 'CREDENTIAL_UNREADABLE']);
  mgr.shutdown();

  const rejected = await setup({ zalo: makeZaloLib({ loginError: new Error('Đăng nhập thất bại') }) });
  const blob3 = rejected.keyring.encryptTransport(JSON.stringify(cred), 'zalo:s3');
  await rejected.mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's3', credential: blob3 }));
  const e3 = await waitFor(() => rejected.bus.of(STREAMS.status, 'session.ended')[0]);
  assert.deepEqual([e3.payload.reason, e3.payload.error], ['expired', 'CREDENTIAL_REJECTED']);
});

test('logout: dừng listener, bỏ phiên khỏi RAM, session.ended logged_out', async () => {
  const { bus, mgr, keyring, zalo } = await setup();
  const blob = keyring.encryptTransport(JSON.stringify({ imei: 'i', cookie: [], userAgent: 'u' }), 'zalo:s1');
  await mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's1', credential: blob }));
  await waitFor(() => bus.of(STREAMS.status, 'session.active')[0]);
  await mgr.handleControl(ctl('session.logout', { channel: 'zalo', session_id: 's1' }));
  const ended = bus.of(STREAMS.status, 'session.ended');
  assert.equal(ended.length, 1);
  assert.deepEqual(ended[0].payload, { channel: 'zalo', session_id: 's1', reason: 'logged_out' });
  assert.equal(ended[0].correlationId, 'corr-session.logout');
  assert.equal(zalo.ctrl.api.listener.stopped, 1);
  assert.equal(mgr.health().length, 0);
  // Tin đến sau khi đăng xuất không được đẩy
  zalo.ctrl.api.listener.emit('message', zaloMessage());
  await tick(5);
  assert.equal(bus.of(STREAMS.inbound).length, 0);
});

test('logout giữa lúc chờ quét QR → huỷ QR, không phát expired', async () => {
  const { bus, mgr } = await setup();
  await mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's1', credential: null }));
  await waitFor(() => bus.of(STREAMS.status, 'session.qr')[0]);
  await mgr.handleControl(ctl('session.logout', { channel: 'zalo', session_id: 's1' }));
  await tick(10);
  const ended = bus.of(STREAMS.status, 'session.ended');
  assert.equal(ended.length, 1);
  assert.equal(ended[0].payload.reason, 'logged_out');
});

test('đồng bộ danh bạ: nhóm, số thành viên, tên thành viên; directory.sync theo lệnh', async () => {
  const api = makeZaloApi({ groups: { G1: { name: 'Nhóm bán hàng', totalMember: 2, memVerList: ['111_0', '222_3'] } } });
  const { bus, mgr, keyring } = await setup({ zalo: makeZaloLib({ api }), manualMinGapMs: 0 });
  const blob = keyring.encryptTransport(JSON.stringify({ imei: 'i', cookie: [], userAgent: 'u' }), 'zalo:s1');
  await mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's1', credential: blob }));
  const g = await waitFor(() => bus.of(STREAMS.directory, 'groups')[0]);
  assert.deepEqual(g.payload, {
    channel: 'zalo', session_id: 's1', groups_complete: true,
    groups: [{ external_id: 'G1', name: 'Nhóm bán hàng', member_count: 2, members_complete: true,
      members: [{ external_id: '111', name: 'Người 111' }, { external_id: '222', name: 'Người 222' }] }],
  });
  await mgr.handleControl(ctl('directory.sync', { channel: 'zalo', session_id: 's1' }));
  assert.equal(bus.of(STREAMS.directory, 'groups').length, 2);
  // group_name lấy từ danh bạ
  bus.listen.zalo = new Set(['G1']);
  api.listener.emit('message', zaloMessage());
  const m = await waitFor(() => bus.of(STREAMS.inbound, 'message')[0]);
  assert.equal(m.payload.group_name, 'Nhóm bán hàng');
  mgr.shutdown();
});

test('gửi tin Zalo dùng ThreadType đúng', async () => {
  const a = new ZaloAdapter({ lib: makeZaloLib().lib, sessionId: 's1' });
  a.state = 'active';
  a.api = makeZaloApi();
  assert.equal(await a.send('G1', 'group', 'a'), '7001');
  assert.equal(await a.send('111', 'user', 'b'), '7002');
  assert.deepEqual(a.api.sent.map((x) => [x.content.msg, x.threadId, x.type]), [['a', 'G1', ThreadType.Group], ['b', '111', ThreadType.User]]);
});
