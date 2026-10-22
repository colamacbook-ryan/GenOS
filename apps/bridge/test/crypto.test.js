import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash, createHmac } from 'node:crypto';
import { Keyring, checkPermit, deriveSubkey, sha256Hex } from '../src/crypto.js';
import { loadBridgeKey } from '../src/config.js';
import { KEY_B64 } from './fakes.js';

const KEY = Buffer.from(KEY_B64, 'base64');

test('vector: khoá con = sha256(key ‖ purpose), HMAC("abc") khớp phía Python', () => {
  assert.deepEqual([...KEY], Array.from({ length: 32 }, (_, i) => i));
  const permitKey = createHash('sha256').update(Buffer.concat([KEY, Buffer.from('permit')])).digest();
  assert.deepEqual(deriveSubkey(KEY, 'permit'), permitKey);
  assert.equal(deriveSubkey(KEY, 'transport').toString('hex'),
    'ca4c2bf50deb3fa382b0c87e592d4ed873d3da578fc59fa6acf1e0bed24fe882');
  const hex = new Keyring(KEY).permitHmac('abc').toString('hex');
  assert.equal(hex, createHmac('sha256', permitKey).update('abc').digest('hex'));
  assert.equal(hex, '575b8b86d194ac0a6cd104ea301c7eb9d457195926a88a67de21d02d5839cb9c');
  console.log(`HMAC-SHA256(permit subkey, "abc") = ${hex}`);
});

test('mã hoá truyền: khứ hồi, sai AAD / sửa byte → lỗi', () => {
  const k = new Keyring(KEY);
  const blob = k.encryptTransport('{"a":1}', 'zalo:s1');
  const raw = Buffer.from(blob, 'base64');
  assert.equal(raw.length, 12 + 7 + 16);
  assert.equal(k.decryptTransport(blob, 'zalo:s1').toString(), '{"a":1}');
  assert.throws(() => k.decryptTransport(blob, 'zalo:s2'));
  raw[15] ^= 1;
  assert.throws(() => k.decryptTransport(raw.toString('base64'), 'zalo:s1'));
  assert.throws(() => new Keyring(Buffer.alloc(32, 9)).decryptTransport(blob, 'zalo:s1'));
});

test('mã hoá truyền: vector cố định (nonce 00..0b) và bản mã do Python (cryptography AESGCM) sinh', () => {
  const k = new Keyring(KEY);
  const aad = 'zalo:11111111-1111-1111-1111-111111111111';
  const plain = '{"imei":"imei-1","cookie":[],"userAgent":"UA"}';
  const blob = k.encryptTransport(plain, aad, Buffer.from('000102030405060708090a0b', 'hex'));
  assert.equal(blob, 'AAECAwQFBgcICQoLIrO1LxmxqW4NJ2zfq1gXWOrP9l7nfjz8GJy99euBb24KDy/X9y2eQzuEyeSJMmz/dqF+rK2kNs+qaUVJDz4=');
  assert.equal(k.decryptTransport(blob, aad).toString(), plain);
  const fromPython = 'DAsKCQgHBgUEAwIBS+BXYSbQA4SGPzKhj9vtH1zRDkLsBhsb3e0yQU/LRaYB';
  assert.equal(k.decryptTransport(fromPython, 'whatsapp:s-1').toString(), '{"from":"python"}');
});

test('không có khoá: không mã hoá được, permit luôn PERMIT_INVALID', () => {
  const k = new Keyring(null);
  assert.equal(k.enabled, false);
  assert.throws(() => k.encryptTransport('x', 'zalo:s'), /BRIDGE_KEY_MISSING/);
  assert.deepEqual(k.verifyPermitSignature('a.b'), { ok: false, error: 'PERMIT_INVALID', claims: null });
});

test('permit: định dạng base64url(JSON).base64url(HMAC) và các lỗi', () => {
  const k = new Keyring(KEY);
  const now = 1_800_000_000;
  const claims = { nonce: 'n1', draft_id: 'd1', channel: 'zalo', thread_id: 'G1', thread_type: 'group', body_sha256: sha256Hex('chào'), exp: now + 60 };
  const token = k.signPermit(claims);
  const [head, sig] = token.split('.');
  assert.deepEqual(JSON.parse(Buffer.from(head, 'base64url')), claims);
  assert.equal(sig, createHmac('sha256', deriveSubkey(KEY, 'permit')).update(head).digest('base64url'));
  const cmd = { channel: 'zalo', thread_id: 'G1', thread_type: 'group', text: 'chào', permit: token };
  assert.equal(checkPermit(k, cmd, now).ok, true);
  assert.equal(checkPermit(k, { ...cmd, permit: `${head}.${sig.slice(0, -2)}AA` }, now).error, 'PERMIT_INVALID');
  assert.equal(checkPermit(k, { ...cmd, permit: 'rác' }, now).error, 'PERMIT_INVALID');
  assert.equal(checkPermit(k, cmd, now + 61).error, 'PERMIT_EXPIRED');
  assert.equal(checkPermit(k, { ...cmd, text: 'chào!' }, now).error, 'PERMIT_MISMATCH');
  assert.equal(checkPermit(k, { ...cmd, thread_id: 'G2' }, now).error, 'PERMIT_MISMATCH');
  assert.equal(checkPermit(k, { ...cmd, thread_type: 'user' }, now).error, 'PERMIT_MISMATCH');
  assert.equal(checkPermit(k, { ...cmd, channel: 'whatsapp' }, now).error, 'PERMIT_MISMATCH');
  assert.equal(checkPermit(new Keyring(Buffer.alloc(32, 1)), cmd, now).error, 'PERMIT_INVALID');
});

test('loadBridgeKey: env, thiếu, sai độ dài', () => {
  assert.equal(loadBridgeKey({ GH_BRIDGE_KEY: KEY_B64 }).key.length, 32);
  assert.equal(loadBridgeKey({}).error, 'BRIDGE_KEY_MISSING');
  assert.equal(loadBridgeKey({ GH_BRIDGE_KEY: 'AAEC' }).error, 'BRIDGE_KEY_INVALID');
  assert.equal(loadBridgeKey({ GH_BRIDGE_KEY_FILE: '/khong/ton/tai' }).key, null);
});
