// Event bus trên Redis Streams: publish (XADD + envelope) và đọc consumer group `bridge` có ack.
// Cùng giao diện với FakeBus trong test: publish · isListening · listenDirect · claimNonce · ping · setHeartbeat.
import { envelope } from './envelope.js';

export const GROUP = 'bridge';
export const CONTROL_STREAM = 'gh.bridge.control';
export const OUTBOUND_STREAM = 'gh.bridge.outbound';

/** Giải một bản ghi stream (các field là chuỗi) thành sự kiện; payload JSON. */
export function decodeEntry(stream, id, fields) {
  let payload = {};
  try { payload = JSON.parse(fields.payload || '{}'); } catch { payload = null; }
  return {
    stream,
    id,
    event_id: fields.event_id,
    type: fields.type,
    org_id: fields.org_id || '',
    correlation_id: fields.correlation_id || fields.event_id,
    actor: fields.actor,
    occurred_at: fields.occurred_at,
    schema_version: Number(fields.schema_version || 1),
    payload,
  };
}

export class RedisBus {
  /**
   * @param {object} o
   * @param {import('redis').RedisClientType} o.redis   client dùng chung (lệnh ngắn)
   * @param {import('redis').RedisClientType} [o.reader] client riêng cho XREADGROUP BLOCK
   */
  constructor({ redis, reader, maxlen = 100000, consumer = 'bridge', logger }) {
    this.redis = redis;
    this.reader = reader || redis;
    this.maxlen = maxlen;
    this.consumer = consumer;
    this.log = logger;
    this.running = false;
  }

  async publish(stream, type, payload, { actor = 'bridge', orgId = '', correlationId } = {}) {
    return this.redis.xAdd(stream, '*', envelope(type, payload, { actor, orgId, correlationId }), {
      TRIM: { strategy: 'MAXLEN', strategyModifier: '~', threshold: this.maxlen },
    });
  }

  async isListening(channel, groupId) {
    return Boolean(await this.redis.sIsMember(`gh:bridge:listen:${channel}`, String(groupId)));
  }

  /**
   * Tin 1-1 mặc định được LƯU (im lặng — v0.1.65): chỉ khoá "0" (Sếp chọn không lưu) mới chặn. Khoá chưa có
   * (lõi chưa đồng bộ) cho qua; lõi kiểm lần hai ở ingest nên không rò tin khi Sếp đã tắt.
   */
  async listenDirect(channel) {
    return (await this.redis.get(`gh:bridge:listen_direct:${channel}`)) !== '0';
  }

  /**
   * Cờ «nhóm mới» của kênh (`gh:bridge:new_group_mode:<kênh>` = silent | ask | off, lõi ghi cùng tập nghe). Chưa có khoá
   * (lõi cũ / lõi chưa đồng bộ) ⇒ null.
   */
  async newGroupMode(channel) {
    return (await this.redis.get(`gh:bridge:new_group_mode:${channel}`)) ?? null;
  }

  // ─── Kênh quản trị (v0.1.72): khoá do lõi ghi (`sync_listen_sets`), bridge CHỈ ĐỌC ───────────────────────────

  async #anyMember(key, ids) {
    for (const id of [].concat(ids)) {
      if (id && await this.redis.sIsMember(key, String(id))) return true;
    }
    return false;
  }

  /** `gh:bridge:account_kind:<kênh>:<phiên>` = bot | owner_self; chưa có khoá ⇒ null (coi như không phải bot). */
  async accountKind(channel, sessionId) {
    return (await this.redis.get(`gh:bridge:account_kind:${channel}:${sessionId}`)) ?? null;
  }

  /** Một trong các ID (ID chính + alt) thuộc tập Owner `gh:bridge:owner:<kênh>`. */
  async isOwner(channel, ids) { return this.#anyMember(`gh:bridge:owner:${channel}`, ids); }

  /** Thread 1-1 (theo một trong các ID) thuộc `gh:bridge:owner_thread:<kênh>`. */
  async isOwnerThread(channel, ids) { return this.#anyMember(`gh:bridge:owner_thread:${channel}`, ids); }

  /** Đang mở ghép mã: khoá `gh:bridge:pairing:<kênh>` tồn tại (TTL = hạn mã). */
  async pairingOpen(channel) {
    return (await this.redis.get(`gh:bridge:pairing:${channel}`)) !== null;
  }

  /** Nhóm đang bật chế độ hỗ trợ: `gh:bridge:support:<kênh>`. */
  async isSupportGroup(channel, groupId) {
    return Boolean(await this.redis.sIsMember(`gh:bridge:support:${channel}`, String(groupId)));
  }

  /** SET gh:permit:used:<nonce> 1 NX EX 600 → true nếu lần đầu. */
  async claimNonce(nonce) {
    return (await this.redis.set(`gh:permit:used:${nonce}`, '1', { NX: true, EX: 600 })) === 'OK';
  }

  async setHeartbeat(at, ttlSec = 45) {
    await this.redis.set('gh:bridge:heartbeat', at, { EX: ttlSec });
  }

  /** Thời gian khứ hồi Redis (ms). */
  async ping() {
    const t = process.hrtime.bigint();
    await this.redis.ping();
    return Number((process.hrtime.bigint() - t) / 1000000n);
  }

  async ensureGroup(stream) {
    try {
      await this.redis.xGroupCreate(stream, GROUP, '0', { MKSTREAM: true });
    } catch (err) {
      if (!String(err?.message).includes('BUSYGROUP')) throw err;
    }
  }

  async #deliver(stream, id, fields, handler) {
    try {
      await handler(decodeEntry(stream, id, fields));
    } catch (err) {
      this.log?.error('handler lỗi', { stream, id, error: err?.message });
    } finally {
      await this.redis.xAck(stream, GROUP, id).catch((e) => this.log?.error('xack lỗi', { stream, id, error: e.message }));
    }
  }

  /** Nhận lại tin còn treo (của consumer cũ đã chết) trước khi đọc tin mới. */
  async #recover(stream, handler, minIdleMs) {
    let cursor = '0-0';
    for (let i = 0; i < 100; i += 1) {
      const res = await this.redis.xAutoClaim(stream, GROUP, this.consumer, minIdleMs, cursor, { COUNT: 100 });
      for (const m of res.messages || []) {
        if (m) await this.#deliver(stream, m.id, m.message, handler);
      }
      cursor = res.nextId;
      if (!cursor || cursor === '0-0') break;
    }
  }

  /**
   * Vòng đọc: handlers = { [stream]: async (event) => void }. Handler tự quyết có chờ hay không;
   * tin được ack sau khi promise của handler xong (kể cả lỗi — lỗi đã được phát thành sự kiện).
   */
  async consume(handlers, { blockMs = 5000, count = 20, minIdleMs = 60000 } = {}) {
    const streams = Object.keys(handlers);
    for (const s of streams) await this.ensureGroup(s);
    for (const s of streams) await this.#recover(s, handlers[s], minIdleMs).catch((e) => this.log?.warn('recover lỗi', { stream: s, error: e.message }));
    this.running = true;
    while (this.running) {
      let res;
      try {
        res = await this.reader.xReadGroup(GROUP, this.consumer, streams.map((key) => ({ key, id: '>' })),
          { COUNT: count, BLOCK: blockMs });
      } catch (err) {
        if (!this.running) break;
        this.log?.error('xreadgroup lỗi', { error: err.message });
        if (String(err.message).includes('NOGROUP')) for (const s of streams) await this.ensureGroup(s).catch(() => {});
        await new Promise((r) => setTimeout(r, 1000));
        continue;
      }
      for (const { name, messages } of res || []) {
        for (const m of messages) {
          // Không chờ: handler outbound tự xếp hàng theo phiên; ack khi handler xong.
          this.#deliver(name, m.id, m.message, handlers[name]);
        }
      }
    }
  }

  stop() { this.running = false; }
}
