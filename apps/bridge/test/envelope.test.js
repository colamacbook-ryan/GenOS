import { test } from 'node:test';
import assert from 'node:assert/strict';
import { envelope, uuid7 } from '../src/envelope.js';

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
