// v0.1.72 — bridge báo AI thêm / mời bot ra khỏi nhóm (gh.bridge.directory group.self_joined / group.self_removed).
// Payload dựng theo mã nguồn zca-js 2.2.0 (models/GroupEvent.js, apis/listen.js) và Baileys 7.0.0-rc14
// (Utils/process-message.js `group-participants.update`, Socket/messages-recv.js `groups.upsert`).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { STREAMS } from '../src/envelope.js';
import { memoryAuthState } from '../src/channels/whatsapp.js';
import { ctl, makeBaileysLib, setup, tick, waitFor, zaloGroupEvent } from './fakes.js';

async function activeZalo(opts = {}) {
  const env = await setup(opts);
  const blob = env.keyring.encryptTransport(JSON.stringify({ imei: 'i', cookie: [], userAgent: 'u' }), 'zalo:s1');
  await env.mgr.handleControl(ctl('session.login', { channel: 'zalo', session_id: 's1', credential: blob }));
  await waitFor(() => env.bus.of(STREAMS.status, 'session.active')[0]);
  env.api = env.zalo.ctrl.api;
  return env;
}

async function activeWa(opts = {}) {
  const env = await setup(opts);
  const st = memoryAuthState(makeBaileysLib().lib, null);
  st.state.creds.me = { id: '84909999999:3@s.whatsapp.net' };
  await env.mgr.handleControl(ctl('session.login', { channel: 'whatsapp', session_id: 'w1',
    credential: env.keyring.encryptTransport(JSON.stringify(st.serialize()), 'whatsapp:w1') }));
  env.sock = await waitFor(() => env.wa.ctrl.last());
  env.sock.open(); // mình: 84909999999@s.whatsapp.net · LID 7777@lid
  await waitFor(() => env.bus.of(STREAMS.status, 'session.active')[0]);
  return env;
}

const dir = (bus, type) => bus.of(STREAMS.directory, type);

// ─── Zalo ─────────────────────────────────────────────────────────────────────

test('Zalo: group_event join có mình, người khác thêm ⇒ group.self_joined{added_by=sourceId, method=add}', async () => {
  const { bus, mgr, api } = await activeZalo();
  api.listener.emit('group_event', zaloGroupEvent({ type: 'join', groupId: 'G7', groupName: 'Khách sỉ', sourceId: '111' }));
  const e = await waitFor(() => dir(bus, 'group.self_joined')[0]);
  assert.equal(e.actor, 'bridge:zalo');
  assert.equal(e.orgId, 'org-1');
  assert.deepEqual(e.payload, {
    channel: 'zalo', session_id: 's1', group_id: 'G7', name: 'Khách sỉ', added_by: '111', added_by_alt: null,
    method: 'add', at: new Date(1758600000000).toISOString(),
  });
  mgr.shutdown();
});

test('Zalo: vào bằng link (sourceId là chính mình) ⇒ method=link, added_by=null; thiếu sourceId ⇒ unknown', async () => {
  const { bus, mgr, api } = await activeZalo();
  api.listener.emit('group_event', zaloGroupEvent({ groupId: 'G8', sourceId: '900' }));
  api.listener.emit('group_event', zaloGroupEvent({ groupId: 'G9', sourceId: '' }));
  await waitFor(() => dir(bus, 'group.self_joined').length === 2);
  const [a, b] = dir(bus, 'group.self_joined').map((e) => e.payload);
  assert.deepEqual([a.group_id, a.method, a.added_by], ['G8', 'link', null]);
  assert.deepEqual([b.group_id, b.method, b.added_by], ['G9', 'unknown', null]);
  mgr.shutdown();
});

test('Zalo: join của NGƯỜI KHÁC (không có mình) và sự kiện loại khác ⇒ không phát gì', async () => {
  const { bus, mgr, api } = await activeZalo();
  // isSelf=true vì sourceId là mình (mình thêm người khác) nhưng updateMembers không có mình
  api.listener.emit('group_event', zaloGroupEvent({ groupId: 'G1', sourceId: '900', members: ['222'] }));
  api.listener.emit('group_event', zaloGroupEvent({ groupId: 'G1', sourceId: '111', members: ['333'] }));
  api.listener.emit('group_event', zaloGroupEvent({ type: 'update', groupId: 'G1', sourceId: '111', members: ['900'] }));
  api.listener.emit('group_event', { type: 'join_request', data: { uids: ['900'], groupId: 'G1' }, threadId: 'G1', isSelf: false });
  await tick(10);
  assert.equal(dir(bus, 'group.self_joined').length, 0);
  assert.equal(dir(bus, 'group.self_removed').length, 0);
  mgr.shutdown();
});

test('Zalo: remove_member / block_member có mình ⇒ group.self_removed{by=sourceId}', async () => {
  const { bus, mgr, api } = await activeZalo();
  api.listener.emit('group_event', zaloGroupEvent({ type: 'remove_member', groupId: 'G7', sourceId: '111' }));
  api.listener.emit('group_event', zaloGroupEvent({ type: 'block_member', groupId: 'G8', sourceId: '222' }));
  api.listener.emit('group_event', zaloGroupEvent({ type: 'remove_member', groupId: 'G9', sourceId: '111', members: ['333'] }));
  await waitFor(() => dir(bus, 'group.self_removed').length === 2);
  await tick(5);
  assert.deepEqual(dir(bus, 'group.self_removed').map((e) => e.payload), [
    { channel: 'zalo', session_id: 's1', group_id: 'G7', by: '111', at: new Date(1758600000000).toISOString() },
    { channel: 'zalo', session_id: 's1', group_id: 'G8', by: '222', at: new Date(1758600000000).toISOString() },
  ]);
  mgr.shutdown();
});

// ─── WhatsApp ─────────────────────────────────────────────────────────────────

test('WhatsApp: group-participants.update add có mình theo JID ⇒ self_joined{added_by=author, alt=authorPn}', async () => {
  const { bus, mgr, sock } = await activeWa();
  sock.ev.emit('group-participants.update', {
    id: '120999@g.us', author: '5555@lid', authorPn: '84901111111@s.whatsapp.net', action: 'add',
    participants: [{ id: '84909999999@s.whatsapp.net', phoneNumber: '84909999999@s.whatsapp.net' }],
  });
  const e = await waitFor(() => dir(bus, 'group.self_joined')[0]);
  assert.equal(e.actor, 'bridge:whatsapp');
  const { at, ...rest } = e.payload;
  assert.deepEqual(rest, {
    channel: 'whatsapp', session_id: 'w1', group_id: '120999@g.us', name: '', added_by: '5555@lid',
    added_by_alt: '84901111111@s.whatsapp.net', method: 'add',
  });
  assert.ok(!Number.isNaN(Date.parse(at)));
  mgr.shutdown();
});

test('WhatsApp: add có mình theo LID (participant chỉ có id LID) ⇒ self_joined', async () => {
  const { bus, mgr, sock } = await activeWa();
  sock.ev.emit('group-participants.update', {
    id: '120998@g.us', author: '5555@lid', authorPn: '84901111111@s.whatsapp.net', action: 'add',
    participants: [{ id: '7777@lid' }],
  });
  // participant dạng lid có thiết bị + phoneNumber của người KHÁC: không phải mình
  sock.ev.emit('group-participants.update', {
    id: '120997@g.us', author: '5555@lid', action: 'add', participants: [{ id: '8888@lid', phoneNumber: '84903333333@s.whatsapp.net' }],
  });
  await waitFor(() => dir(bus, 'group.self_joined').length === 1);
  await tick(5);
  const ev = dir(bus, 'group.self_joined');
  assert.equal(ev.length, 1);
  assert.deepEqual([ev[0].payload.group_id, ev[0].payload.added_by, ev[0].payload.added_by_alt],
    ['120998@g.us', '5555@lid', '84901111111@s.whatsapp.net']);
  mgr.shutdown();
});

test('WhatsApp: tự vào (author là mình) ⇒ link; không có author ⇒ unknown; groups.upsert ⇒ create', async () => {
  const { bus, mgr, sock } = await activeWa();
  sock.ev.emit('group-participants.update', {
    id: '1201@g.us', author: '7777@lid', authorPn: '84909999999@s.whatsapp.net', action: 'add',
    participants: [{ id: '7777@lid', phoneNumber: '84909999999@s.whatsapp.net' }],
  });
  sock.ev.emit('group-participants.update', { id: '1202@g.us', action: 'add', participants: [{ id: '7777@lid' }] });
  sock.ev.emit('groups.upsert', [{
    id: '1203@g.us', subject: 'Nhóm tạo mới', owner: '5555@lid', author: '5555@lid', authorPn: '84901111111@s.whatsapp.net',
    participants: [{ id: '5555@lid', admin: 'superadmin' }, { id: '7777@lid' }],
  }]);
  await waitFor(() => dir(bus, 'group.self_joined').length === 3);
  const got = dir(bus, 'group.self_joined').map((e) => [e.payload.group_id, e.payload.method, e.payload.added_by,
    e.payload.added_by_alt, e.payload.name]);
  assert.deepEqual(got, [
    ['1201@g.us', 'link', null, null, ''],
    ['1202@g.us', 'unknown', null, null, ''],
    ['1203@g.us', 'create', '5555@lid', '84901111111@s.whatsapp.net', 'Nhóm tạo mới'],
  ]);
  mgr.shutdown();
});

test('WhatsApp: remove có mình ⇒ self_removed{by=author}; remove người khác ⇒ không phát', async () => {
  const { bus, mgr, sock } = await activeWa();
  sock.ev.emit('group-participants.update', {
    id: '120363@g.us', author: '5555@lid', authorPn: '84901111111@s.whatsapp.net', action: 'remove',
    participants: [{ id: '84909999999@s.whatsapp.net' }],
  });
  sock.ev.emit('group-participants.update', {
    id: '120363@g.us', author: '5555@lid', action: 'remove', participants: [{ id: '84902222222@s.whatsapp.net' }],
  });
  sock.ev.emit('group-participants.update', {
    id: '120363@g.us', author: '5555@lid', action: 'promote', participants: [{ id: '7777@lid' }],
  });
  await waitFor(() => dir(bus, 'group.self_removed').length === 1);
  await tick(5);
  assert.equal(dir(bus, 'group.self_removed').length, 1);
  assert.equal(dir(bus, 'group.self_joined').length, 0);
  const { at, ...rest } = dir(bus, 'group.self_removed')[0].payload;
  assert.deepEqual(rest, { channel: 'whatsapp', session_id: 'w1', group_id: '120363@g.us', by: '5555@lid' });
  assert.ok(at);
  mgr.shutdown();
});
