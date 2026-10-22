// Tiện ích chung cho adapter kênh.

/** Chuyển đối tượng thư viện sang JSON thuần: Buffer/Uint8Array → base64, Long → số, bigint → chuỗi. */
export function toPlain(value) {
  let json;
  try {
    json = JSON.stringify(value, function replacer(key, v) {
      const raw = this[key];
      if (raw instanceof Uint8Array) return Buffer.from(raw).toString('base64');
      if (v && typeof v === 'object' && v.type === 'Buffer' && Array.isArray(v.data)) return Buffer.from(v.data).toString('base64');
      if (typeof v === 'bigint') return v.toString();
      if (v && typeof v === 'object' && typeof v.toNumber === 'function' && 'low' in v && 'high' in v) return v.toNumber();
      if (typeof v === 'function') return undefined;
      return v;
    });
  } catch {
    return null; // vòng tham chiếu hoặc kiểu lạ: bỏ bản gốc, không làm hỏng tin
  }
  return json === undefined ? null : JSON.parse(json);
}

/** Số giây/ms (number | string | Long) → ISO; không hợp lệ → bây giờ. */
export function toIso(value, unit = 'ms', now = Date.now) {
  let n = value;
  if (n && typeof n === 'object' && typeof n.toNumber === 'function') n = n.toNumber();
  n = Number(n);
  if (!Number.isFinite(n) || n <= 0) return new Date(now()).toISOString();
  return new Date(unit === 's' ? n * 1000 : n).toISOString();
}

/** Tập id đã thấy có giới hạn kích thước (chống trùng tin). */
export class RecentIds {
  constructor(limit = 1000) { this.limit = limit; this.set = new Set(); }

  /** true nếu id mới (và ghi nhận), false nếu đã thấy. id rỗng luôn coi là mới. */
  add(id) {
    if (!id) return true;
    if (this.set.has(id)) return false;
    this.set.add(id);
    if (this.set.size > this.limit) this.set.delete(this.set.values().next().value);
    return true;
  }
}

export function errorCode(err) {
  return err?.code || err?.name || 'ERROR';
}

/**
 * Thông tin phụ adapter gắn vào tin đã chuẩn hoá cho SessionManager (thread 1-1, ID phụ của người gửi, cờ chuyển tiếp).
 * Thuộc tính symbol KHÔNG đếm được ⇒ không bao giờ vào JSON (envelope, `first_message`), chỉ sống trong RAM.
 */
export const META = Symbol('gh.bridge.meta');

export function withMeta(obj, meta) {
  Object.defineProperty(obj, META, { value: meta, enumerable: false });
  return obj;
}
