import { test } from 'node:test';
import assert from 'node:assert/strict';
import { MEDIA_FETCH, MEDIA_RESULT_TTL_S, envelope, mediaResultKey, uuid7 } from '../src/envelope.js';
import { MediaCache, bindingOk, guardedFetch, mediaKey, isPrivateIp, mediaRef, parseMediaRef, validRequestId } from '../src/media.js';

test('uuid7 có phiên bản 7 và sắp theo thời gian', () => {
  const a = uuid7();
  assert.match(a, /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  const ms = parseInt(a.replace(/-/g, '').slice(0, 12), 16);
  assert.ok(Math.abs(ms - Date.now()) < 1000);
});

test('phong bì có đủ trường như EventBus bên Python', () => {
  const e = envelope('bridge.heartbeat', { a: 1 }, { orgId: 'o1' });
  assert.deepEqual(Object.keys(e).sort(), ['actor', 'correlation_id', 'event_id', 'occurred_at', 'org_id',
    'payload', 'schema_version', 'type']);
  assert.equal(e.correlation_id, e.event_id);
  assert.deepEqual(JSON.parse(e.payload), { a: 1 });
});

test('L5 media_ref: khuôn hẹp, không thể là URL/đường dẫn; mã yêu cầu phải là UUID', () => {
  assert.equal(mediaRef('zalo', 's1', '5001'), 'zalo:s1:5001');
  assert.deepEqual(parseMediaRef('whatsapp:w1:ABCD1'), { channel: 'whatsapp', sessionId: 'w1', key: 'ABCD1' });
  for (const bad of ['https://x.vn/a', 'zalo:s1:../../etc', 'zalo:s1:a/b', 'telegram:s1:1', 'zalo:s1:a?b=1', 'zalo:s1:a%2F', '', null, 5]) {
    assert.equal(parseMediaRef(bad), null, String(bad));
  }
  assert.equal(mediaRef('zalo', 's/1', '1'), null);
  assert.equal(validRequestId('0199b2c4-0000-7000-8000-000000000000'), true);
  assert.equal(validRequestId('x:../y'), false);
  assert.equal(mediaResultKey('id'), 'gh:media:res:id');
  assert.deepEqual([MEDIA_FETCH, MEDIA_RESULT_TTL_S], ['media.fetch', 120]);
});

test('L5 MediaCache: LRU theo số lượng và hết hạn theo TTL', () => {
  let t = 0;
  const c = new MediaCache({ max: 2, ttlMs: 100, now: () => t });
  c.set('a', 1); c.set('b', 2); c.set('c', 3);
  assert.deepEqual([c.get('a'), c.get('b'), c.get('c')], [null, 2, 3]);
  t = 101;
  assert.equal(c.get('b'), null);
});

test('L5 isPrivateIp + guardedFetch từ chối cổng/URL lạ trước khi chạm mạng', async () => {
  for (const ip of ['10.1.1.1', '127.0.0.1', '169.254.169.254', '172.16.0.1', '192.168.1.1', '100.64.0.1', '::1', 'fd00::1', '::ffff:10.0.0.1', 'nope']) {
    assert.equal(isPrivateIp(ip), true, ip);
  }
  for (const ip of ['8.8.8.8', '1.1.1.1', '2606:4700::1111']) assert.equal(isPrivateIp(ip), false, ip);
  let called = 0;
  await assert.rejects(guardedFetch('https://evil.example/x', { cap: 10, fetchImpl: () => { called += 1; } }), { code: 'blocked_host' });
  assert.equal(called, 0);
});

test('L5 mediaKey: băm theo chat+người gửi+id; bindingOk đòi khớp đủ cả hai', () => {
  const k = mediaKey('120363@g.us', '8490@s.whatsapp.net', 'ABC');
  assert.match(k, /^[0-9a-f]{32}$/);
  assert.notEqual(k, mediaKey('120999@g.us', '8490@s.whatsapp.net', 'ABC'));
  assert.notEqual(k, mediaKey('120363@g.us', '8491@s.whatsapp.net', 'ABC'));
  assert.notEqual(mediaKey('a|b', 'c', 'd'), mediaKey('a', 'b|c', 'd')); // không nhập nhằng ranh giới trường
  const d = { bind: { chat: 'G', sender: 'S' } };
  assert.equal(bindingOk(d, { chat: 'G', sender: 'S' }), true);
  for (const bad of [{ chat: 'G' }, { sender: 'S' }, { chat: 'X', sender: 'S' }, { chat: 'G', sender: 'X' }, null, { chat: 1, sender: 'S' }]) {
    assert.equal(bindingOk(d, bad), false);
  }
  assert.equal(bindingOk({}, { chat: 'G', sender: 'S' }), false);
});
