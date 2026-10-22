// v0.1.65 (F1a) — danh bạ: thu thành viên MỌI nhóm (kể cả "Không nghe"), vai trò admin, cờ "danh sách đủ", giãn cách,
// nhóm mới báo `group.discovered` (kèm tin đầu tiên để lõi lưu nếu nhóm vào «Lắng nghe im lặng»), quét lại có giới hạn tần suất, lệnh directory.sync dùng chung một lượt.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { STREAMS } from '../src/envelope.js';
import { ZaloAdapter } from '../src/channels/zalo.js';
import { WhatsAppAdapter } from '../src/channels/whatsapp.js';
import { createLogger } from '../src/log.js';
import {
  ctl, makeBaileysLib, makeZaloApi, makeZaloLib, setup, tick, waitFor, zaloMessage, waMessage,
} from './fakes.js';

const GROUPS = {
  G1: { name: 'Nhóm sỉ', totalMember: 3, creatorId: '111', adminIds: ['222'], memVerList: ['111_0', '222_0', '333_0'] },
  G2: { name: 'Nội bộ (Không nghe)', totalMember: 2, memVerList: ['444_0', '555_0'] },
};

async function loginZalo(api, opts = {}) {
  const env = await setup({ zalo: makeZaloLib({ api }), ...opts });
  const blob = env.keyring.encryptTransport(JSON.stringify({ imei: 'i', cookie: [], userAgent: 'u' }), 'zalo:s1');
  await env.mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's1', credential: blob }));
  await waitFor(() => env.bus.of(STREAMS.status, 'session.active')[0]);
  return env;
}

test('Zalo: thu thành viên MỌI nhóm kể cả nhóm không nằm trong tập nghe; creator/admin có role, nhóm không có thông tin thì không gán role', async () => {
  const api = makeZaloApi({ groups: GROUPS });
  const { bus, mgr } = await loginZalo(api);
  const dir = await waitFor(() => bus.of(STREAMS.directory, 'groups')[0]);
  assert.equal(bus.listen.zalo, undefined); // không nhóm nào trong tập nghe
  const [g1, g2] = dir.payload.groups;
  assert.deepEqual(g1.members.map((m) => [m.external_id, m.role]), [['111', 'admin'], ['222', 'admin'], ['333', 'member']]);
  assert.deepEqual(g2.members.map((m) => [m.external_id, m.role]), [['444', undefined], ['555', undefined]]);
  assert.equal(g2.members.length, 2);
  assert.equal(g1.members_complete, true);
  assert.equal(dir.payload.groups_complete, true);
  mgr.shutdown();
});

test('Zalo: thiếu thành viên so với số nền tảng báo ⇒ members_complete = false; nhóm lỗi getGroupInfo ⇒ groups_complete = false', async () => {
  const api = makeZaloApi({ groups: { G1: { name: 'A', totalMember: 5, memVerList: ['1_0', '2_0'] } } });
  const first = await loginZalo(api);
  const d1 = await waitFor(() => first.bus.of(STREAMS.directory, 'groups')[0]);
  assert.equal(d1.payload.groups[0].members_complete, false);
  assert.equal(d1.payload.groups_complete, true);
  first.mgr.shutdown();

  const api2 = makeZaloApi({ groups: GROUPS });
  let calls = 0;
  const real = api2.getGroupInfo;
  api2.getGroupInfo = async (ids) => { calls += 1; if (calls === 1) throw new Error('mạng'); return real(ids); };
  const second = await loginZalo(api2);
  const d2 = await waitFor(() => second.bus.of(STREAMS.directory, 'groups')[0]);
  assert.deepEqual(d2.payload.groups, []);
  assert.equal(d2.payload.groups_complete, false); // rỗng + lỗi ⇒ lõi không được coi là "rời hết"
  second.mgr.shutdown();
});

test('Zalo: có giãn cách giữa các lần hỏi nền tảng khi quét thành viên', async () => {
  const api = makeZaloApi({ groups: GROUPS });
  const stamps = [];
  const info = api.getGroupMembersInfo;
  api.getGroupMembersInfo = async (ids) => { stamps.push(Date.now()); return info(ids); };
  const a = new ZaloAdapter({ lib: makeZaloLib({ api }).lib, sessionId: 's1', directoryGapMs: 30 });
  a.api = api;
  const groups = await a.syncGroups();
  assert.equal(groups.length, 2);
  assert.equal(stamps.length, 2);
  assert.ok(stamps[1] - stamps[0] >= 25, `khoảng cách ${stamps[1] - stamps[0]}ms quá ngắn`);
  assert.equal(a.lastSyncComplete, true);
});

test('WhatsApp: role admin/member + cờ danh sách đủ', async () => {
  const wa = makeBaileysLib();
  const a = new WhatsAppAdapter({ lib: wa.lib, sessionId: 'w1' });
  a.sock = { groupFetchAllParticipating: async () => ({
    '1@g.us': { id: '1@g.us', subject: 'N1', size: 3, participants: [{ id: 'a@s.whatsapp.net', admin: 'superadmin' }, { id: 'b@s.whatsapp.net', admin: null }, { id: 'c@lid' }] },
    '2@g.us': { id: '2@g.us', subject: 'N2', size: 9, participants: [{ id: 'x@s.whatsapp.net', admin: 'admin' }] },
  }) };
  const [g1, g2] = await a.syncGroups();
  assert.deepEqual(g1.members.map((m) => m.role), ['admin', 'member', 'member']);
  assert.equal(g1.members_complete, true);
  assert.equal(g2.members_complete, false);
  assert.equal(a.lastSyncComplete, true);
});

test('tin từ nhóm LẠ: không đi luồng inbound, phát group.discovered đúng một lần kèm TIN ĐẦU (first_message), rồi quét lại danh bạ', async () => {
  const lines = [];
  const api = makeZaloApi({ groups: GROUPS });
  const { bus, mgr } = await loginZalo(api, { logger: createLogger((l) => lines.push(l)), discoverDelayMs: 10, discoverMinGapMs: 0 });
  await waitFor(() => bus.of(STREAMS.directory, 'groups')[0]);
  // G9 chưa có trong danh bạ vừa quét
  api.listener.emit('message', zaloMessage({ threadId: 'G9', content: 'bí mật khách lạ', msgId: '91' }));
  api.listener.emit('message', zaloMessage({ threadId: 'G9', content: 'tin thứ hai', msgId: '92' }));
  const found = await waitFor(() => bus.of(STREAMS.directory, 'group.discovered')[0]);
  const { first_message: first, ...rest } = found.payload;
  assert.deepEqual(rest, { channel: 'zalo', session_id: 's1', group_id: 'G9', name: '', member_count: null });
  // tin ĐẦU (không phải tin thứ hai) đi kèm nguyên văn để lõi lưu khi nhóm mới vào «Lắng nghe im lặng»
  assert.equal(first.external_msg_id, '91');
  assert.equal(first.external_group_id, 'G9');
  assert.equal(first.body_text, 'bí mật khách lạ');
  await waitFor(() => bus.of(STREAMS.directory, 'groups').length === 2); // quét lại sau khi phát hiện
  await tick(20);
  assert.equal(bus.of(STREAMS.directory, 'group.discovered').length, 1);
  assert.equal(bus.of(STREAMS.inbound).length, 0); // không đi luồng inbound: lõi quyết định lưu hay không theo chế độ nhóm
  assert.ok(!lines.some((l) => l.includes('bí mật') || l.includes('thứ hai')));
  // nhóm đã có trong danh bạ (G1, "Không nghe") bị bỏ tin nhưng KHÔNG báo lại là nhóm mới
  api.listener.emit('message', zaloMessage({ threadId: 'G1', msgId: '93' }));
  await tick(30);
  assert.equal(bus.of(STREAMS.directory, 'group.discovered').length, 1);
  assert.equal(bus.of(STREAMS.inbound).length, 0);
  mgr.shutdown();
});

// ── P2-1 (review v0.1.65): chỉ gắn first_message khi chắc chắn là nhóm MỚI và nhóm sẽ vào «Lắng nghe im lặng» ──

test('first_message CHỈ gắn khi danh bạ đã quét xong; chưa quét xong ⇒ group.discovered không mang nội dung tin', async () => {
  const api = makeZaloApi({ groups: GROUPS });
  let release;
  const gate = new Promise((r) => { release = r; });
  const real = api.getAllGroups;
  api.getAllGroups = async () => { await gate; return real(); };
  const env = await setup({ zalo: makeZaloLib({ api }), discoverDelayMs: 10, discoverMinGapMs: 0 });
  const blob = env.keyring.encryptTransport(JSON.stringify({ imei: 'i', cookie: [], userAgent: 'u' }), 'zalo:s1');
  await env.mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's1', credential: blob }));
  await waitFor(() => env.bus.of(STREAMS.status, 'session.active')[0]);
  // bridge vừa khởi động lại: danh bạ chưa quét xong ⇒ nhóm "Không nghe" cũ cũng trông như nhóm lạ
  api.listener.emit('message', zaloMessage({ threadId: 'G2', content: 'nhóm tắt nghe cũ', msgId: '71' }));
  const found = await waitFor(() => env.bus.of(STREAMS.directory, 'group.discovered')[0]);
  assert.equal(found.payload.group_id, 'G2');
  assert.equal('first_message' in found.payload, false);
  assert.ok(!JSON.stringify(env.bus.events).includes('nhóm tắt nghe cũ'));
  release();
  env.mgr.shutdown();
});

for (const mode of ['ask', 'off']) {
  test(`first_message KHÔNG gắn khi cờ new_group_mode của kênh = ${mode} (nhóm mới không vào «Lắng nghe im lặng»)`, async () => {
    const api = makeZaloApi({ groups: GROUPS });
    const env = await loginZalo(api, { discoverDelayMs: 10, discoverMinGapMs: 0 });
    await waitFor(() => env.bus.of(STREAMS.directory, 'groups')[0]);
    env.bus.groupMode.zalo = mode;
    api.listener.emit('message', zaloMessage({ threadId: 'G9', content: 'tin không được rò', msgId: '72' }));
    const found = await waitFor(() => env.bus.of(STREAMS.directory, 'group.discovered')[0]);
    assert.equal(found.payload.group_id, 'G9');
    assert.equal('first_message' in found.payload, false);
    assert.ok(!JSON.stringify(env.bus.events).includes('tin không được rò'));
    env.mgr.shutdown();
  });
}

test('first_message gắn khi cờ new_group_mode = silent (và khi lõi cũ chưa ghi cờ)', async () => {
  const api = makeZaloApi({ groups: GROUPS });
  const env = await loginZalo(api, { discoverDelayMs: 10, discoverMinGapMs: 0 });
  await waitFor(() => env.bus.of(STREAMS.directory, 'groups')[0]);
  env.bus.groupMode.zalo = 'silent';
  api.listener.emit('message', zaloMessage({ threadId: 'G7', msgId: '73' }));
  api.listener.emit('message', zaloMessage({ threadId: 'G8', msgId: '74' }));
  await waitFor(() => env.bus.of(STREAMS.directory, 'group.discovered').length === 2);
  const [a, b] = env.bus.of(STREAMS.directory, 'group.discovered');
  assert.equal(a.payload.first_message.external_msg_id, '73');
  assert.equal(b.payload.first_message.external_msg_id, '74');
  env.mgr.shutdown();
});

test('quét lại sau phát hiện bị giới hạn tần suất: không quét hai lần trong discoverMinGapMs', async () => {
  const api = makeZaloApi({ groups: GROUPS });
  const { bus, mgr } = await loginZalo(api, { discoverDelayMs: 5, discoverMinGapMs: 400 });
  await waitFor(() => bus.of(STREAMS.directory, 'groups')[0]);
  api.listener.emit('message', zaloMessage({ threadId: 'G8', msgId: '81' }));
  await waitFor(() => bus.of(STREAMS.directory, 'group.discovered').length === 1);
  await tick(60);
  assert.equal(bus.of(STREAMS.directory, 'groups').length, 1); // chưa tới hạn: mới có lượt quét lúc đăng nhập
  mgr.shutdown();
});

test('directory.sync (nút "Cập nhật danh sách nhóm") và chu kỳ chạm nhau dùng chung MỘT lượt quét', async () => {
  const api = makeZaloApi({ groups: GROUPS });
  let scans = 0;
  const real = api.getAllGroups;
  api.getAllGroups = async () => { scans += 1; await tick(20); return real(); };
  const { bus, mgr } = await loginZalo(api, { manualMinGapMs: 0 });
  await waitFor(() => bus.of(STREAMS.directory, 'groups')[0]);
  const before = scans;
  await Promise.all([
    mgr.handleControl(ctl('directory.sync', { channel: 'zalo', session_id: 's1' })),
    mgr.syncDirectory(mgr.sessions.get('zalo:s1')),
  ]);
  assert.equal(scans - before, 1);
  assert.equal(bus.of(STREAMS.directory, 'groups').length, 2);
  mgr.shutdown();
});

test('WhatsApp: tin từ nhóm lạ cũng báo group.discovered (kèm tên nếu biết)', async () => {
  const { bus, mgr, wa } = await setup();
  await mgr.handleControl(ctl('session.login', { channel: 'whatsapp', session_id: 'w1', credential: null }));
  const sock = await waitFor(() => wa.ctrl.last());
  sock.config.auth.creds.me = { id: '84909999999:3@s.whatsapp.net', name: 'Kho Gen' };
  sock.ev.emit('connection.update', { isNewLogin: true, qr: undefined });
  sock.open();
  await waitFor(() => bus.of(STREAMS.directory, 'groups')[0]);
  sock.ev.emit('messages.upsert', { type: 'notify', messages: [waMessage({ remoteJid: '999@g.us', id: 'Z1' })] });
  const found = await waitFor(() => bus.of(STREAMS.directory, 'group.discovered')[0]);
  assert.equal(found.payload.group_id, '999@g.us');
  assert.equal(found.payload.first_message.external_msg_id, 'Z1');
  assert.equal(found.payload.first_message.external_group_id, '999@g.us');
  assert.equal(bus.of(STREAMS.inbound).length, 0);
  mgr.shutdown();
});

// ── P2-1 (review v0.1.65): nền tảng trả lỗi ⇒ DỪNG cả lượt (không hỏi tiếp), đánh dấu chưa đủ, lùi lịch ──

const manyGroups = (n) => Object.fromEntries(Array.from({ length: n }, (_, i) => [
  `G${i}`, { name: `N${i}`, totalMember: 1, memVerList: [`${i}_0`] },
]));

test('Zalo: getGroupInfo lỗi ở lô đầu ⇒ DỪNG lượt, không hỏi lô sau; lastSyncComplete = false', async () => {
  const api = makeZaloApi({ groups: manyGroups(45) }); // 3 lô (20 + 20 + 5)
  let calls = 0;
  api.getGroupInfo = async () => { calls += 1; throw new Error('429'); };
  const a = new ZaloAdapter({ lib: makeZaloLib({ api }).lib, sessionId: 's1', directoryGapMs: 0 });
  a.api = api;
  const groups = await a.syncGroups();
  assert.equal(calls, 1);
  assert.deepEqual(groups, []);
  assert.equal(a.lastSyncComplete, false);
});

test('Zalo: lô sau lỗi ⇒ giữ nhóm của lô trước, KHÔNG hỏi lô thứ ba', async () => {
  const api = makeZaloApi({ groups: manyGroups(45) });
  const real = api.getGroupInfo;
  let calls = 0;
  api.getGroupInfo = async (ids) => { calls += 1; if (calls === 2) throw new Error('mạng'); return real(ids); };
  const a = new ZaloAdapter({ lib: makeZaloLib({ api }).lib, sessionId: 's1', directoryGapMs: 0 });
  a.api = api;
  const groups = await a.syncGroups();
  assert.equal(calls, 2);
  assert.equal(groups.length, 20);
  assert.equal(a.lastSyncComplete, false);
});

test('Zalo: getGroupMembersInfo lỗi ⇒ DỪNG lượt (không hỏi thành viên của nhóm sau), chưa đủ', async () => {
  const api = makeZaloApi({ groups: manyGroups(5) });
  let calls = 0;
  api.getGroupMembersInfo = async () => { calls += 1; throw new Error('429'); };
  const a = new ZaloAdapter({ lib: makeZaloLib({ api }).lib, sessionId: 's1', directoryGapMs: 0 });
  a.api = api;
  await a.syncGroups();
  assert.equal(calls, 1);
  assert.equal(a.lastSyncComplete, false);
});

test('lượt quét lỗi ⇒ lùi lịch gấp đôi chu kỳ mỗi lần (tối đa 8 giờ), lượt đủ ⇒ về chu kỳ gốc', async () => {
  const api = makeZaloApi({ groups: GROUPS });
  const real = api.getAllGroups;
  let fail = true;
  api.getAllGroups = async () => { if (fail) throw new Error('429'); return real(); };
  const env = await loginZalo(api, { directoryMs: 2 * 60 * 60 * 1000, manualMinGapMs: 0, discoverMinGapMs: 0 });
  const s = env.mgr.sessions.get('zalo:s1');
  await waitFor(() => s.syncFailures === 1);
  assert.equal(s.nextSyncDelayMs, 4 * 60 * 60 * 1000);
  await env.mgr.syncDirectory(s, 'manual');
  assert.equal(s.syncFailures, 2);
  assert.equal(s.nextSyncDelayMs, 8 * 60 * 60 * 1000);
  await env.mgr.syncDirectory(s, 'manual');
  assert.equal(s.nextSyncDelayMs, 8 * 60 * 60 * 1000); // trần 8 giờ
  fail = false;
  await env.mgr.syncDirectory(s, 'manual');
  assert.equal(s.syncFailures, 0);
  assert.equal(s.nextSyncDelayMs, 2 * 60 * 60 * 1000);
  env.mgr.shutdown();
});

// ── P3 (review v0.1.65): giãn cách thật giữa các lượt quét, tính từ lúc BẮT ĐẦU ──

const countingApi = () => {
  const api = makeZaloApi({ groups: GROUPS });
  const real = api.getAllGroups;
  const box = { scans: 0, fail: false };
  api.getAllGroups = async () => { box.scans += 1; if (box.fail) throw new Error('429'); return real(); };
  return { api, box };
};

test('nối lại sau mất mạng (adapter báo active lần nữa) KHÔNG quét ngay: phải đủ giãn cách', async () => {
  const { api, box } = countingApi();
  const env = await loginZalo(api);
  await waitFor(() => env.bus.of(STREAMS.directory, 'groups')[0]);
  assert.equal(box.scans, 1);
  const s = env.mgr.sessions.get('zalo:s1');
  s.adapter.emit('active', { account: { id: '900', name: 'A' }, credential: { imei: 'i', cookie: [], userAgent: 'u' } });
  await tick(40);
  assert.equal(box.scans, 1);
  env.mgr.shutdown();
});

test('lastSyncAt đặt lúc BẮT ĐẦU: lượt lỗi vẫn tính giãn cách (không quét lại ngay khi lỗi)', async () => {
  const { api, box } = countingApi();
  box.fail = true;
  let t = 1_000_000;
  const env = await loginZalo(api, { now: () => t });
  const s = env.mgr.sessions.get('zalo:s1');
  await waitFor(() => s.syncFailures === 1);
  assert.equal(s.lastSyncAt, 1_000_000);
  await env.mgr.syncDirectory(s, 'discover');
  await env.mgr.syncDirectory(s, 'login');
  assert.equal(box.scans, 1); // trong giãn cách 10 phút
  t += 10 * 60 * 1000;
  await env.mgr.syncDirectory(s, 'discover');
  assert.equal(box.scans, 1); // đang lùi lịch vì lỗi (4 giờ): nhóm mới / nối lại cũng chờ hết chu kỳ lùi
  t += 4 * 60 * 60 * 1000;
  box.fail = false;
  await env.mgr.syncDirectory(s, 'discover');
  assert.equal(box.scans, 2);
  assert.equal(s.syncFailures, 0);
  env.mgr.shutdown();
});

test('nút «Cập nhật danh sách nhóm»: giãn 5 phút từ lần quét trước, hết giãn thì quét', async () => {
  const { api, box } = countingApi();
  let t = 5_000_000;
  const env = await loginZalo(api, { now: () => t });
  await waitFor(() => env.bus.of(STREAMS.directory, 'groups')[0]);
  const ctrl = () => env.mgr.handleControl(ctl('directory.sync', { channel: 'zalo', session_id: 's1' }));
  await ctrl();
  await ctrl();
  assert.equal(box.scans, 1); // bấm dồn ngay sau lượt đăng nhập: bỏ qua
  t += 5 * 60 * 1000 - 1;
  await ctrl();
  assert.equal(box.scans, 1);
  t += 1;
  await ctrl();
  assert.equal(box.scans, 2);
  env.mgr.shutdown();
});

test('hẹn quét lại sau phát hiện nhóm mới tính lại lúc tới hạn: lượt quét khác chạy xen giữa thì không quét thừa', async () => {
  const { api, box } = countingApi();
  const env = await loginZalo(api, { discoverDelayMs: 5, discoverMinGapMs: 300, manualMinGapMs: 0 });
  await waitFor(() => env.bus.of(STREAMS.directory, 'groups')[0]);
  api.listener.emit('message', zaloMessage({ threadId: 'G8', msgId: '81' }));
  await waitFor(() => env.bus.of(STREAMS.directory, 'group.discovered').length === 1);
  await tick(60);
  await env.mgr.handleControl(ctl('directory.sync', { channel: 'zalo', session_id: 's1' })); // quét tay xen giữa
  assert.equal(box.scans, 2);
  await tick(450); // quá hạn hẹn cũ: lượt quét tay đã thấy nhóm mới ⇒ không quét thừa
  assert.equal(box.scans, 2);
  env.mgr.shutdown();
});

// ── P3: nhóm WhatsApp địa chỉ theo LID — Sếp không được bị coi là "đã rời" ──

test('WhatsApp: nhóm LID liệt kê chính mình bằng LID ⇒ báo bằng JID của Sếp (khớp tin của mình); người khác giữ nguyên', async () => {
  const wa = makeBaileysLib();
  const a = new WhatsAppAdapter({ lib: wa.lib, sessionId: 'w1' });
  a.own = { jid: '84909999999@s.whatsapp.net', lid: '555@lid', name: 'Kho Gen' };
  a.sock = { groupFetchAllParticipating: async () => ({
    '1@g.us': { id: '1@g.us', subject: 'N1', size: 3, participants: [
      { id: '555@lid', phoneNumber: '84909999999@s.whatsapp.net', admin: 'admin' },
      { id: '777:4@lid', phoneNumber: '84911111111@s.whatsapp.net' },
      { id: '84922222222@s.whatsapp.net' }] },
    '2@g.us': { id: '2@g.us', subject: 'N2', size: 1, participants: [{ id: '555:2@lid' }] },
    '3@g.us': { id: '3@g.us', subject: 'N3', size: 1, participants: [{ id: '888@lid', lid: '555@lid' }] },
  }) };
  const [g1, g2, g3] = await a.syncGroups();
  assert.deepEqual(g1.members.map((m) => m.external_id), ['84909999999@s.whatsapp.net', '777:4@lid', '84922222222@s.whatsapp.net']);
  assert.equal(g1.members[0].role, 'admin');
  assert.equal(g1.members[0].phone, '+84909999999');
  assert.deepEqual(g2.members.map((m) => m.external_id), ['84909999999@s.whatsapp.net']); // chỉ LID, không có phoneNumber
  assert.deepEqual(g3.members.map((m) => m.external_id), ['84909999999@s.whatsapp.net']); // khớp qua trường lid
});
