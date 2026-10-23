// Phong bì sự kiện — cùng định dạng với apps/api/gh/chassis/bus.py (EventBus.publish).
import { randomBytes } from 'node:crypto';

export const STREAMS = {
  inbound: 'gh.bridge.inbound',
  outbound: 'gh.bridge.outbound',
  status: 'gh.bridge.status',
  directory: 'gh.bridge.directory',
};

export function uuid7() {
  const ms = BigInt(Date.now());
  const rand = randomBytes(10);
  const b = Buffer.alloc(16);
  b.writeUIntBE(Number(ms >> 16n), 0, 4);
  b.writeUInt16BE(Number(ms & 0xffffn), 4);
  rand.copy(b, 6);
  b[6] = (b[6] & 0x0f) | 0x70;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = b.toString('hex');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

export function envelope(type, payload, { actor = 'bridge', orgId = '', correlationId } = {}) {
  const eventId = uuid7();
  return {
    event_id: eventId,
    type,
    org_id: orgId ? String(orgId) : '',
    correlation_id: correlationId || eventId,
    actor,
    occurred_at: new Date().toISOString(),
    schema_version: '1',
    payload: JSON.stringify(payload),
  };
}

// ─── Đa phương tiện (L5): lệnh media.fetch{media_ref} → bytes ───────────────────────────────────────────────────────
// Lõi gửi `media.fetch` lên gh.bridge.control; bridge trả kết quả qua một danh sách Redis ngắn hạn (không đẩy file lên
// stream): RPUSH gh:media:res:<request_id> <JSON>, lõi BLPOP. JSON: {request_id, ok, mime, size, sha256, data_b64} hoặc
// {request_id, ok:false, error:<mã>}. Mã lỗi: xem MEDIA_ERRORS trong media.js.
export const MEDIA_FETCH = 'media.fetch';
export const MEDIA_RESULT_TTL_S = 120;
export const mediaResultKey = (requestId) => `gh:media:res:${requestId}`;
