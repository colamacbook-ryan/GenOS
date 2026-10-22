// F046/F067: mất mạng KHÔNG được xoá phiên đã lưu; chỉ nền tảng từ chối thật (401 / đăng xuất) mới `expired`.
// Phiên mới thay phiên cũ ⇒ lõi ra lệnh `session.close` để bridge không giữ kết nối ma.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { classifyLoginError, isCredentialRejected } from '../src/channels/zalo.js';
import { memoryAuthState } from '../src/channels/whatsapp.js';
import { STREAMS } from '../src/envelope.js';
import { ctl, makeBaileysLib, makeZaloLib, setup, tick, waitFor } from './fakes.js';

const CRED = { imei: 'imei-1', cookie: [{ key: 'zpw', value: 'v' }], userAgent: 'UA' };
const netErr = (code = 'ENOTFOUND') => Object.assign(new Error(`getaddrinfo ${code} wpa.chat.zalo.me`), { code });

/** Zalo giả: `behave(n)` quyết định lần đăng nhập thứ n (1…) ném lỗi (trả Error) hay thành công (trả null). */
function flakyZalo(behave) {
  const base = makeZaloLib();
  const calls = [];
  class Zalo extends base.lib.Zalo {
    async login(credential) {
      calls.push(credential);
      const err = behave(calls.length);
      if (err) throw err;
      return base.ctrl.api;
    }
  }
  return { lib: { ...base.lib, Zalo }, ctrl: { ...base.ctrl, calls } };
}

const typesOf = (bus) => bus.of(STREAMS.status).map((e) => e.type);

test('phân loại lỗi: chỉ 401/đăng xuất mới là bị từ chối; lỗi mạng/máy chủ thì không', () => {
  assert.equal(isCredentialRejected(new Error('Đăng nhập thất bại')), true);
  assert.equal(isCredentialRejected(new Error('Failed to fetch server info: Unauthorized')), true);
  assert.equal(isCredentialRejected(Object.assign(new Error('x'), { status: 401 })), true);
  assert.equal(isCredentialRejected(netErr('ENOTFOUND')), false);
  assert.equal(isCredentialRejected(netErr('ETIMEDOUT')), false);
  assert.equal(isCredentialRejected(new TypeError('fetch failed')), false);
  assert.equal(isCredentialRejected(new Error('Failed to fetch server info: Service Unavailable')), false);
  assert.equal(isCredentialRejected(null), false);
});

test('Zalo: khởi động khi chưa có mạng → thử lại tới khi nối được, KHÔNG expired, giữ nguyên credential', async () => {
  const zalo = flakyZalo((n) => (n <= 4 ? netErr() : null));
  const { bus, mgr, keyring } = await setup({ zalo });
  const blob = keyring.encryptTransport(JSON.stringify(CRED), 'zalo:s1');
  await mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's1', credential: blob }));
  const act = await waitFor(() => bus.of(STREAMS.status, 'session.active')[0]);
  assert.equal(zalo.ctrl.calls.length, 5);
  assert.deepEqual(zalo.ctrl.calls.every((c) => c.imei === 'imei-1'), true);
  assert.deepEqual(JSON.parse(keyring.decryptTransport(act.payload.credential, 'zalo:s1')), CRED);
  assert.equal(bus.of(STREAMS.status, 'session.ended').length, 0, 'mất mạng không được phát session.ended');
  // Báo lõi đúng một lần "đang nối lại", rồi mới "đã nối".
  const types = typesOf(bus);
  assert.equal(types.filter((t) => t === 'session.reconnecting').length, 1);
  assert.ok(types.indexOf('session.reconnecting') < types.indexOf('session.active'));
  assert.deepEqual(bus.of(STREAMS.status, 'session.reconnecting')[0].payload,
    { channel: 'zalo', session_id: 's1', error: 'NETWORK_UNAVAILABLE' });
  mgr.shutdown();
});

test('Zalo: mất mạng mãi mãi → không bao giờ expired; dừng thử khi bridge tắt; heartbeat ngừng trong lúc mất mạng', async () => {
  const zalo = flakyZalo(() => netErr('ETIMEDOUT'));
  const { bus, mgr, keyring } = await setup({ zalo });
  const blob = keyring.encryptTransport(JSON.stringify(CRED), 'zalo:s1');
  await mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's1', credential: blob }));
  await waitFor(() => zalo.ctrl.calls.length >= 8, { timeout: 4000 });
  await mgr.heartbeat();
  assert.equal(bus.of(STREAMS.status, 'heartbeat').length, 0, 'phiên đang mất mạng không được báo còn sống');
  assert.equal(bus.of(STREAMS.status, 'session.ended').length, 0);
  assert.equal(mgr.health()[0].state, 'retrying');
  mgr.shutdown();
  const n = zalo.ctrl.calls.length;
  await tick(40);
  assert.ok(zalo.ctrl.calls.length <= n + 1, 'tắt bridge thì vòng thử lại dừng');
});

test('Zalo: nền tảng từ chối thật (401) → expired CREDENTIAL_REJECTED, không thử lại', async () => {
  const zalo = flakyZalo(() => Object.assign(new Error('Failed to fetch server info: Unauthorized'), { status: 401 }));
  const { bus, mgr, keyring } = await setup({ zalo });
  const blob = keyring.encryptTransport(JSON.stringify(CRED), 'zalo:s1');
  await mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's1', credential: blob }));
  const e = await waitFor(() => bus.of(STREAMS.status, 'session.ended')[0]);
  assert.deepEqual([e.payload.reason, e.payload.error], ['expired', 'CREDENTIAL_REJECTED']);
  await tick(20);
  assert.equal(zalo.ctrl.calls.length, 1);
});

test('Zalo: đang chạy thì websocket đóng hẳn + mất mạng → nối lại không xoá phiên, nối được thì báo active lại', async () => {
  let down = false;
  const zalo = flakyZalo(() => (down ? netErr('ENETUNREACH') : null));
  const { bus, mgr, keyring } = await setup({ zalo });
  const blob = keyring.encryptTransport(JSON.stringify(CRED), 'zalo:s1');
  await mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's1', credential: blob }));
  await waitFor(() => bus.of(STREAMS.status, 'session.active')[0]);
  down = true;
  zalo.ctrl.api.listener.emit('closed', 1006);
  await waitFor(() => bus.of(STREAMS.status, 'session.reconnecting')[0]);
  assert.equal(bus.of(STREAMS.status, 'session.ended').length, 0);
  down = false;
  await waitFor(() => bus.of(STREAMS.status, 'session.active').length === 2);
  assert.equal(bus.of(STREAMS.status, 'session.ended').length, 0);
  assert.equal(mgr.health()[0].state, 'active');
  mgr.shutdown();
});

test('Zalo: đang chạy, websocket đóng rồi nền tảng từ chối phiên → expired', async () => {
  let reject = false;
  const zalo = flakyZalo(() => (reject ? new Error('Đăng nhập thất bại') : null));
  const { bus, mgr, keyring } = await setup({ zalo });
  const blob = keyring.encryptTransport(JSON.stringify(CRED), 'zalo:s1');
  await mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's1', credential: blob }));
  await waitFor(() => bus.of(STREAMS.status, 'session.active')[0]);
  reject = true;
  zalo.ctrl.api.listener.emit('closed', 1006);
  const e = await waitFor(() => bus.of(STREAMS.status, 'session.ended')[0]);
  assert.deepEqual([e.payload.reason, e.payload.error], ['expired', 'CREDENTIAL_REJECTED']);
});

function pairedBlob(keyring, sessionId) {
  const { lib } = makeBaileysLib();
  const st = memoryAuthState(lib, null);
  st.state.creds.me = { id: '84909999999:3@s.whatsapp.net', name: 'Kho Gen' };
  return keyring.encryptTransport(JSON.stringify(st.serialize()), `whatsapp:${sessionId}`);
}

test('WhatsApp: phiên đã lưu mất mạng quá 10 lần → KHÔNG expired, vẫn thử tiếp; nối được thì báo active', async () => {
  const { bus, mgr, keyring, wa } = await setup();
  await mgr.handleControl(ctl('session.login', { channel: 'whatsapp', session_id: 'w1', credential: pairedBlob(keyring, 'w1') }));
  await waitFor(() => wa.ctrl.last());
  for (let i = 0; i < 14; i += 1) {
    const before = wa.ctrl.sockets.length;
    wa.ctrl.last().close(408, 'getaddrinfo ENOTFOUND web.whatsapp.com');
    await waitFor(() => wa.ctrl.sockets.length > before, { timeout: 4000 });
  }
  assert.equal(bus.of(STREAMS.status, 'session.ended').length, 0, 'mất mạng lâu không được xoá phiên');
  assert.equal(bus.of(STREAMS.status, 'session.reconnecting').length, 1);
  wa.ctrl.last().open();
  await waitFor(() => bus.of(STREAMS.status, 'session.active')[0]);
  assert.equal(bus.of(STREAMS.status, 'session.ended').length, 0);
  mgr.shutdown();
});

test('WhatsApp: đang hoạt động mất mạng lâu → báo reconnecting, nối lại thì active lần nữa; 401 thì mới expired', async () => {
  const { bus, mgr, keyring, wa } = await setup();
  await mgr.handleControl(ctl('session.login', { channel: 'whatsapp', session_id: 'w1', credential: pairedBlob(keyring, 'w1') }));
  const first = await waitFor(() => wa.ctrl.last());
  first.open();
  await waitFor(() => bus.of(STREAMS.status, 'session.active')[0]);
  for (let i = 0; i < 12; i += 1) {
    const before = wa.ctrl.sockets.length;
    wa.ctrl.last().close(428);
    await waitFor(() => wa.ctrl.sockets.length > before, { timeout: 4000 });
  }
  assert.equal(bus.of(STREAMS.status, 'session.ended').length, 0);
  assert.equal(bus.of(STREAMS.status, 'session.reconnecting').length, 1);
  await mgr.heartbeat();
  assert.equal(bus.of(STREAMS.status, 'heartbeat').length, 0);
  wa.ctrl.last().open();
  await waitFor(() => bus.of(STREAMS.status, 'session.active').length === 2);
  wa.ctrl.last().close(401);
  const e = await waitFor(() => bus.of(STREAMS.status, 'session.ended')[0]);
  assert.deepEqual([e.payload.reason, e.payload.error], ['expired', 'LOGGED_OUT_REMOTELY']);
});

test('session.close: đóng kết nối của phiên cũ, KHÔNG đăng xuất nền tảng, KHÔNG phát session.ended', async () => {
  const { bus, mgr, keyring, zalo, wa } = await setup();
  const blob = keyring.encryptTransport(JSON.stringify(CRED), 'zalo:old');
  await mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 'old', credential: blob }));
  await waitFor(() => bus.of(STREAMS.status, 'session.active')[0]);
  await mgr.handleControl(ctl('session.close', { channel: 'zalo', session_id: 'old' }));
  assert.equal(zalo.ctrl.api.listener.stopped, 1);
  assert.equal(mgr.health().length, 0);
  assert.equal(bus.of(STREAMS.status, 'session.ended').length, 0);

  await mgr.handleControl(ctl('session.login', { channel: 'whatsapp', session_id: 'w1', credential: pairedBlob(keyring, 'w1') }));
  const sock = await waitFor(() => wa.ctrl.last());
  sock.open();
  await waitFor(() => bus.of(STREAMS.status, 'session.active').length === 2);
  await mgr.handleControl(ctl('session.close', { channel: 'whatsapp', session_id: 'w1' }));
  assert.equal(sock.loggedOut, 0, 'đóng phiên cũ không được đăng xuất tài khoản (phiên mới có thể cùng tài khoản)');
  assert.ok(sock.ended >= 1);
  assert.equal(bus.of(STREAMS.status, 'session.ended').length, 0);
  // Phiên không tồn tại: bỏ qua, không lỗi.
  await mgr.handleControl(ctl('session.close', { channel: 'zalo', session_id: 'khong-co' }));
  assert.equal(bus.of(STREAMS.status, 'session.ended').length, 0);
});

// ─── P3 (rà soát v0.1.62): lỗi lạ không được thử lại mãi mãi ─────────────────────────────────────────────────
test('phân loại lỗi đăng nhập lại: từ chối · mạng · lạ', () => {
  assert.equal(classifyLoginError(new Error('Đăng nhập thất bại')), 'rejected');
  assert.equal(classifyLoginError(Object.assign(new Error('x'), { status: 401 })), 'rejected');
  assert.equal(classifyLoginError(netErr('ENOTFOUND')), 'network');
  assert.equal(classifyLoginError(netErr('ECONNRESET')), 'network');
  assert.equal(classifyLoginError(new TypeError('fetch failed')), 'network');
  assert.equal(classifyLoginError(new Error('Failed to fetch server info: Service Unavailable')), 'network');
  assert.equal(classifyLoginError(Object.assign(new Error('x'), { status: 503 })), 'network');
  assert.equal(classifyLoginError(Object.assign(new Error('x'), { status: 429 })), 'network');
  assert.equal(classifyLoginError(new Error('Cannot read properties of undefined')), 'unknown');
  assert.equal(classifyLoginError(null), 'unknown');
});

test('Zalo: lỗi lạ (không phải mạng, không phải từ chối) lặp lại → dừng sau giới hạn, session.ended error RECONNECT_FAILED', async () => {
  const zalo = flakyZalo(() => new Error('Cannot read properties of undefined (reading "x")'));
  const { bus, mgr, keyring } = await setup({ zalo });
  const blob = keyring.encryptTransport(JSON.stringify(CRED), 'zalo:s1');
  await mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's1', credential: blob }));
  const e = await waitFor(() => bus.of(STREAMS.status, 'session.ended')[0], { timeout: 4000 });
  assert.deepEqual([e.payload.reason, e.payload.error], ['error', 'RECONNECT_FAILED']);
  const n = zalo.ctrl.calls.length;
  assert.ok(n >= 3 && n <= 12, `phải dừng sau vài lần, không phải mãi (n=${n})`);
  await tick(30);
  assert.equal(zalo.ctrl.calls.length, n, 'đã dừng thì không thử lại nữa');
  mgr.shutdown();
});

test('Zalo: lỗi lạ xen lỗi mạng — bộ đếm lỗi lạ tính liên tiếp, lỗi mạng đặt lại', async () => {
  // 3 lỗi lạ, 1 lỗi mạng, 3 lỗi lạ, rồi nối được: nếu đếm cộng dồn thì đã dừng.
  const seq = ['u', 'u', 'u', 'n', 'u', 'u', 'u', null];
  const zalo = flakyZalo((n) => {
    const k = seq[n - 1];
    return k === 'u' ? new Error('lạ') : k === 'n' ? netErr() : null;
  });
  const { bus, mgr, keyring } = await setup({ zalo });
  const blob = keyring.encryptTransport(JSON.stringify(CRED), 'zalo:s1');
  await mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's1', credential: blob }));
  await waitFor(() => bus.of(STREAMS.status, 'session.active')[0], { timeout: 4000 });
  assert.equal(bus.of(STREAMS.status, 'session.ended').length, 0);
  mgr.shutdown();
});
