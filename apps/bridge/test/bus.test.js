import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CONTROL_STREAM, GROUP, OUTBOUND_STREAM, RedisBus, decodeEntry } from '../src/bus.js';
import { envelope } from '../src/envelope.js';
import { waitFor } from './fakes.js';

// Client redis v4 giả, chỉ các lệnh bridge dùng.
function fakeRedis() {
  const r = {
    calls: [], kv: new Map(), sets: new Map(), acks: [], groups: new Set(), queue: [], pending: [],
    async xAdd(stream, id, fields, opts) { r.calls.push(['xAdd', stream, fields, opts]); return '1-0'; },
    async sIsMember(key, m) { return r.sets.get(key)?.has(m) ? 1 : 0; },
    async get(key) { return r.kv.get(key) ?? null; },
    async set(key, v, opts = {}) {
      r.calls.push(['set', key, v, opts]);
      if (opts.NX && r.kv.has(key)) return null;
      r.kv.set(key, v);
      return 'OK';
    },
    async ping() { return 'PONG'; },
    async xGroupCreate(stream, group, id, opts) {
      r.calls.push(['xGroupCreate', stream, group, id, opts]);
      if (r.groups.has(stream)) throw new Error('BUSYGROUP Consumer Group name already exists');
      r.groups.add(stream);
      return 'OK';
    },
    async xAutoClaim(stream) {
      const mine = r.pending.filter((p) => p.stream === stream);
      r.pending = r.pending.filter((p) => p.stream !== stream);
      return { nextId: '0-0', messages: mine.map((p) => ({ id: p.id, message: p.message })) };
    },
    async xReadGroup(group, consumer, streams, opts) {
      r.calls.push(['xReadGroup', group, consumer, streams, opts]);
      await new Promise((res) => { setTimeout(res, 2); });
      const batch = r.queue.splice(0);
      if (!batch.length) return null;
      const by = {};
      for (const b of batch) (by[b.stream] ||= []).push({ id: b.id, message: b.message });
      return Object.entries(by).map(([name, messages]) => ({ name, messages }));
    },
    async xAck(stream, group, id) { r.acks.push([stream, group, id]); return 1; },
  };
  r.groups.add(CONTROL_STREAM); // nhóm đã có sẵn → BUSYGROUP được bỏ qua
  return r;
}

test('publish: envelope chuẩn + MAXLEN ~', async () => {
  const redis = fakeRedis();
  const bus = new RedisBus({ redis, maxlen: 500 });
  await bus.publish('gh.bridge.status', 'session.qr', { channel: 'zalo' }, { actor: 'bridge:zalo', orgId: 'o1', correlationId: 'c1' });
  const [, stream, fields, opts] = redis.calls[0];
  assert.equal(stream, 'gh.bridge.status');
  assert.deepEqual(Object.keys(fields).sort(), ['actor', 'correlation_id', 'event_id', 'occurred_at', 'org_id', 'payload', 'schema_version', 'type']);
  assert.deepEqual([fields.type, fields.actor, fields.org_id, fields.correlation_id], ['session.qr', 'bridge:zalo', 'o1', 'c1']);
  assert.deepEqual(opts, { TRIM: { strategy: 'MAXLEN', strategyModifier: '~', threshold: 500 } });
});

test('newGroupMode: đọc cờ gh:bridge:new_group_mode:<kênh>; chưa có khoá ⇒ null (lõi cũ chưa ghi cờ)', async () => {
  const redis = fakeRedis();
  const bus = new RedisBus({ redis });
  assert.equal(await bus.newGroupMode('zalo'), null);
  redis.kv.set('gh:bridge:new_group_mode:zalo', 'ask');
  assert.equal(await bus.newGroupMode('zalo'), 'ask');
  assert.equal(await bus.newGroupMode('whatsapp'), null);
});

test('tập nghe, listen_direct, nonce NX EX 600, khoá heartbeat TTL 45', async () => {
  const redis = fakeRedis();
  const bus = new RedisBus({ redis });
  redis.sets.set('gh:bridge:listen:zalo', new Set(['G1']));
  assert.equal(await bus.isListening('zalo', 'G1'), true);
  assert.equal(await bus.isListening('zalo', 'G2'), false);
  assert.equal(await bus.listenDirect('zalo'), true); // chưa có khoá: mặc định lưu im lặng (v0.1.65)
  redis.kv.set('gh:bridge:listen_direct:zalo', '0');
  assert.equal(await bus.listenDirect('zalo'), false); // Sếp chọn không lưu tin 1-1
  redis.kv.set('gh:bridge:listen_direct:zalo', '1');
  assert.equal(await bus.listenDirect('zalo'), true);
  assert.equal(await bus.claimNonce('n1'), true);
  assert.equal(await bus.claimNonce('n1'), false);
  assert.deepEqual(redis.calls.find((c) => c[1] === 'gh:permit:used:n1').slice(2), ['1', { NX: true, EX: 600 }]);
  await bus.setHeartbeat('2026-01-01T00:00:00Z');
  assert.deepEqual(redis.calls.at(-1), ['set', 'gh:bridge:heartbeat', '2026-01-01T00:00:00Z', { EX: 45 }]);
  assert.equal(typeof await bus.ping(), 'number');
});

test('consume: tạo nhóm (bỏ qua BUSYGROUP), nhận lại tin treo, đọc > và ack sau handler (kể cả khi lỗi)', async () => {
  const redis = fakeRedis();
  const bus = new RedisBus({ redis, consumer: 'host-1' });
  const seen = [];
  redis.pending.push({ stream: OUTBOUND_STREAM, id: '1-1', message: envelope('message.send', { a: 0 }) });
  redis.queue.push({ stream: CONTROL_STREAM, id: '2-1', message: envelope('session.login', { a: 1 }, { orgId: 'o' }) });
  redis.queue.push({ stream: OUTBOUND_STREAM, id: '2-2', message: envelope('message.send', { a: 2 }) });
  const run = bus.consume({
    [CONTROL_STREAM]: async (e) => { seen.push([e.stream, e.type, e.payload.a, e.org_id]); throw new Error('handler hỏng'); },
    [OUTBOUND_STREAM]: async (e) => { seen.push([e.stream, e.type, e.payload.a]); },
  }, { blockMs: 10 });
  await waitFor(() => redis.acks.length === 3);
  bus.stop();
  await run;
  assert.deepEqual(seen, [
    [OUTBOUND_STREAM, 'message.send', 0],
    [CONTROL_STREAM, 'session.login', 1, 'o'],
    [OUTBOUND_STREAM, 'message.send', 2],
  ]);
  assert.deepEqual(redis.acks.map((a) => [a[1], a[2]]), [[GROUP, '1-1'], [GROUP, '2-1'], [GROUP, '2-2']]);
  const create = redis.calls.filter((c) => c[0] === 'xGroupCreate');
  assert.deepEqual(create.map((c) => [c[1], c[2], c[3], c[4]]), [
    [CONTROL_STREAM, 'bridge', '0', { MKSTREAM: true }], [OUTBOUND_STREAM, 'bridge', '0', { MKSTREAM: true }]]);
  const read = redis.calls.find((c) => c[0] === 'xReadGroup');
  assert.deepEqual(read.slice(1, 4), ['bridge', 'host-1', [{ key: CONTROL_STREAM, id: '>' }, { key: OUTBOUND_STREAM, id: '>' }]]);
});

test('decodeEntry: payload JSON, schema_version số', () => {
  const e = decodeEntry('s', '1-0', envelope('x', { k: 'v' }));
  assert.deepEqual([e.type, e.payload, e.schema_version, e.correlation_id === e.event_id], ['x', { k: 'v' }, 1, true]);
});

test('v0.1.72: khoá kênh quản trị đọc đúng tên (owner, owner_thread, pairing, support, account_kind)', async () => {
  const redis = fakeRedis();
  const bus = new RedisBus({ redis });
  assert.equal(await bus.accountKind('zalo', 's1'), null);
  redis.kv.set('gh:bridge:account_kind:zalo:s1', 'bot');
  assert.equal(await bus.accountKind('zalo', 's1'), 'bot');
  assert.equal(await bus.accountKind('zalo', 's2'), null);
  redis.sets.set('gh:bridge:owner:whatsapp', new Set(['84905555555@s.whatsapp.net']));
  assert.equal(await bus.isOwner('whatsapp', ['6666@lid', '84905555555@s.whatsapp.net']), true); // khớp theo alt
  assert.equal(await bus.isOwner('whatsapp', ['6666@lid', undefined]), false);
  assert.equal(await bus.isOwner('zalo', '84905555555@s.whatsapp.net'), false);
  redis.sets.set('gh:bridge:owner_thread:zalo', new Set(['555']));
  assert.equal(await bus.isOwnerThread('zalo', ['555']), true);
  assert.equal(await bus.isOwnerThread('zalo', ['111']), false);
  assert.equal(await bus.pairingOpen('zalo'), false);
  redis.kv.set('gh:bridge:pairing:zalo', '1');
  assert.equal(await bus.pairingOpen('zalo'), true);
  redis.sets.set('gh:bridge:support:zalo', new Set(['G2']));
  assert.equal(await bus.isSupportGroup('zalo', 'G2'), true);
  assert.equal(await bus.isSupportGroup('zalo', 'G1'), false);
});
