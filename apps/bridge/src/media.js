// Đa phương tiện (L5): giữ mô tả file trong RAM, kiểm giới hạn/MIME, tải có trần, chặn SSRF.
// Quy tắc cứng:
//  - `media_ref` chỉ là KHOÁ TRA CỨU trong sổ RAM của chính bridge — không bao giờ là URL, không bao giờ được fetch trực tiếp.
//  - Bridge chỉ tải bằng phiên đang có (Baileys giải mã từ khoá của tin; Zalo: URL do chính phiên Zalo nhận, host trong allowlist).
//  - Không ghi token / URL có chữ ký vào log: lỗi chỉ mang MÃ (xem MEDIA_ERRORS), không mang URL hay nội dung.
import { createHash } from 'node:crypto';
import dns from 'node:dns/promises';
import net from 'node:net';

export const MEDIA_CAPS = Object.freeze({
  voiceBytes: 5 * 1024 * 1024, // voice ≤ 5 MB
  voiceSeconds: 10 * 60, //        hoặc ≤ 10 phút (vượt một trong hai ⇒ too_large)
  imageBytes: 8 * 1024 * 1024, //  ảnh ≤ 8 MB
});

/** Mã lỗi gửi về lõi (không bao giờ kèm URL/khoá). */
export const MEDIA_ERRORS = Object.freeze({
  notFound: 'not_found', //            ref không có trong sổ (bridge khởi động lại / quá hạn / sai phiên)
  tooLarge: 'too_large',
  mimeNotAllowed: 'mime_not_allowed',
  blockedHost: 'blocked_host', //      URL ngoài allowlist hoặc trỏ IP nội bộ
  downloadFailed: 'download_failed',
  sessionInactive: 'session_inactive',
  timeout: 'timeout',
  badRequest: 'bad_request',
});

export class MediaError extends Error {
  constructor(code) {
    super(code); // thông điệp = mã, cố ý KHÔNG nhận chuỗi tự do (không để lọt URL/khoá)
    this.name = 'MediaError';
    this.code = code;
  }
}

export const VOICE_MIMES = new Set([
  'audio/ogg', 'audio/opus', 'audio/mpeg', 'audio/mp3', 'audio/mp4', 'audio/aac', 'audio/x-m4a', 'audio/m4a',
  'audio/wav', 'audio/x-wav', 'audio/amr', 'audio/webm',
]);
export const IMAGE_MIMES = new Set(['image/jpeg', 'image/png', 'image/webp', 'image/gif']);

/** `audio/ogg; codecs=opus` → `audio/ogg`. Không phải chuỗi → ''. */
export function baseMime(mime) {
  return typeof mime === 'string' ? mime.split(';')[0].trim().toLowerCase() : '';
}

export function mimeAllowed(kind, mime) {
  const m = baseMime(mime);
  return kind === 'voice' ? VOICE_MIMES.has(m) : kind === 'image' ? IMAGE_MIMES.has(m) : false;
}

export function capBytes(kind) {
  return kind === 'voice' ? MEDIA_CAPS.voiceBytes : MEDIA_CAPS.imageBytes;
}

/**
 * Kiểm mô tả KHAI BÁO trong tin (chưa tải gì). Trả mã lỗi hoặc null. `mime` rỗng được chấp nhận ở đây (Zalo không khai
 * mime): sẽ kiểm lại theo phản hồi thật khi tải.
 */
export function checkDeclared(desc) {
  if (!desc || (desc.kind !== 'voice' && desc.kind !== 'image')) return MEDIA_ERRORS.badRequest;
  if (desc.mime && !mimeAllowed(desc.kind, desc.mime)) return MEDIA_ERRORS.mimeNotAllowed;
  if (Number(desc.size) > capBytes(desc.kind)) return MEDIA_ERRORS.tooLarge;
  if (desc.kind === 'voice' && Number(desc.seconds) > MEDIA_CAPS.voiceSeconds) return MEDIA_ERRORS.tooLarge;
  return null;
}

// ─── media_ref ───────────────────────────────────────────────────────────────

// Khuôn: <kênh>:<phiên>:<khoá tin>. Bộ ký tự hẹp, KHÔNG có `/`, `?`, `%`, `#` ⇒ không thể là URL hay đường dẫn.
const REF_RE = /^(whatsapp|zalo):[A-Za-z0-9_-]{1,64}:[A-Za-z0-9_.@=-]{1,120}$/;

export function mediaRef(channel, sessionId, msgKey) {
  const ref = `${channel}:${sessionId}:${msgKey}`;
  return REF_RE.test(ref) ? ref : null;
}

/** → {channel, sessionId, key} hoặc null (sai khuôn). */
export function parseMediaRef(ref) {
  if (typeof ref !== 'string' || !REF_RE.test(ref)) return null;
  const [channel, sessionId, ...rest] = ref.split(':');
  return { channel, sessionId, key: rest.join(':') };
}

/**
 * Khoá tra sổ = băm (kênh-chat | người gửi | id tin) — id tin đơn lẻ có thể trùng/bị đoán giữa các chat khác nhau, nên
 * không dùng làm khoá. 128 bit đầu của SHA-256 ⇒ khuôn `[0-9a-f]{32}` của media_ref.
 */
export function mediaKey(chat, sender, msgId) {
  return createHash('sha256').update(JSON.stringify([String(chat ?? ''), String(sender ?? ''), String(msgId ?? '')])).digest('hex').slice(0, 32);
}

/**
 * Đăng ký mô tả vào sổ — CHỈ gọi sau khi bộ lọc tập nghe đã nhận tin. Mỗi mục mang ràng buộc `bind {chat, sender}`;
 * không ghi đè mục có sẵn nếu chat/sender khác (chống đầu độc sổ). Trả true nếu đã ghi.
 */
export function registerMedia(cache, m) {
  const e = m?.entry;
  if (!m?.ref || !e?.bind) return false;
  const cur = cache.get(m.ref);
  if (cur && (cur.bind.chat !== e.bind.chat || cur.bind.sender !== e.bind.sender)) return false;
  cache.set(m.ref, e);
  return true;
}

/** Sự kiện `media.fetch` phải khớp ràng buộc lúc đăng ký (`expect {chat, sender}` do lõi lấy từ chính tin trong Kho thô). */
export function bindingOk(desc, expect) {
  return Boolean(desc?.bind) && typeof expect?.chat === 'string' && typeof expect?.sender === 'string'
    && desc.bind.chat === expect.chat && desc.bind.sender === expect.sender;
}

/** Mã yêu cầu do lõi sinh (UUID) — dùng làm một phần tên khoá Redis nên phải kiểm chặt. */
export function validRequestId(id) {
  return typeof id === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(id);
}

// ─── sổ mô tả trong RAM ──────────────────────────────────────────────────────

/** LRU có hạn kích thước + TTL. Chỉ RAM: bridge khởi động lại ⇒ ref cũ `not_found` (lõi ghi "không đọc được", không treo). */
export class MediaCache {
  constructor({ max = 500, ttlMs = 6 * 60 * 60 * 1000, now = Date.now } = {}) {
    this.max = max;
    this.ttlMs = ttlMs;
    this.now = now;
    this.map = new Map();
  }

  set(ref, desc) {
    if (!ref) return;
    this.map.delete(ref);
    this.map.set(ref, { desc, at: this.now() });
    while (this.map.size > this.max) this.map.delete(this.map.keys().next().value);
  }

  get(ref) {
    const e = this.map.get(ref);
    if (!e) return null;
    if (this.now() - e.at > this.ttlMs) { this.map.delete(ref); return null; }
    return e.desc;
  }

  get size() { return this.map.size; }
}

// ─── đọc có trần ─────────────────────────────────────────────────────────────

/** Gom async-iterable (stream Baileys / body fetch) thành Buffer; vượt `cap` byte ⇒ too_large NGAY (không đọc tiếp). */
export async function collectCapped(iterable, cap) {
  const chunks = [];
  let total = 0;
  for await (const chunk of iterable) {
    const b = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    total += b.length;
    if (total > cap) {
      try { (iterable.destroy ?? iterable.cancel)?.call(iterable); } catch { /* đã đóng */ }
      throw new MediaError(MEDIA_ERRORS.tooLarge);
    }
    chunks.push(b);
  }
  return Buffer.concat(chunks, total);
}

// ─── chặn SSRF cho tải theo URL (chỉ Zalo) ───────────────────────────────────

// CDN của Zalo. Khớp CHÍNH XÁC tên miền hoặc tên miền con (hậu tố có dấu chấm đứng trước) — không khớp chuỗi con.
export const ZALO_MEDIA_HOST_SUFFIXES = Object.freeze(['zadn.vn', 'zdn.vn', 'zalo.me', 'zaloapp.com']);

export function hostAllowed(hostname, suffixes = ZALO_MEDIA_HOST_SUFFIXES) {
  const h = String(hostname || '').toLowerCase().replace(/\.$/, '');
  if (!h || net.isIP(h)) return false; // IP thẳng không bao giờ được
  return suffixes.some((s) => h === s || h.endsWith(`.${s}`));
}

/** IPv4/IPv6 riêng/loopback/link-local/CGNAT/multicast/chưa xác định ⇒ true (không được tải). */
export function isPrivateIp(ip) {
  const v = net.isIP(ip);
  if (v === 4) {
    const [a, b] = ip.split('.').map(Number);
    return a === 0 || a === 10 || a === 127 || (a === 100 && b >= 64 && b <= 127) || (a === 169 && b === 254)
      || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 192 && b === 0) || (a === 198 && (b === 18 || b === 19))
      || a >= 224;
  }
  if (v === 6) {
    const x = ip.toLowerCase();
    if (x === '::' || x === '::1') return true;
    const mapped = /^::ffff:(\d+\.\d+\.\d+\.\d+)$/.exec(x);
    if (mapped) return isPrivateIp(mapped[1]);
    return x.startsWith('fc') || x.startsWith('fd') || x.startsWith('fe8') || x.startsWith('fe9') || x.startsWith('fea')
      || x.startsWith('feb') || x.startsWith('ff') || x.startsWith('2001:db8');
  }
  return true; // không phải IP hợp lệ
}

/** URL tải được: https, không tài khoản, cổng mặc định, host trong allowlist. Trả URL đã parse hoặc null. */
export function safeMediaUrl(raw, suffixes = ZALO_MEDIA_HOST_SUFFIXES) {
  let u;
  try { u = new URL(String(raw)); } catch { return null; }
  if (u.protocol !== 'https:' || u.username || u.password || (u.port && u.port !== '443')) return null;
  return hostAllowed(u.hostname, suffixes) ? u : null;
}

const MAX_REDIRECTS = 3;

/**
 * Tải một URL Zalo có kiểm soát: allowlist host, mọi địa chỉ phân giải phải công khai, tự đi theo redirect (≤ 3, kiểm lại
 * từng chặng), trần byte (Content-Length rồi đếm dòng chảy), hạn giờ. Lỗi chỉ ném MediaError (mã, không URL).
 * Còn lại: phân giải DNS ≠ địa chỉ fetch dùng (rebinding) — giảm bằng allowlist tên miền của Zalo; không ghim được IP với fetch.
 * @returns {Promise<{bytes: Buffer, mime: string}>} mime = mime của phản hồi (đã bỏ tham số), '' nếu không có/generic
 */
export async function guardedFetch(rawUrl, {
  cap, fetchImpl = globalThis.fetch, lookup = (h) => dns.lookup(h, { all: true }), timeoutMs = 20000,
  suffixes = ZALO_MEDIA_HOST_SUFFIXES,
} = {}) {
  let url = safeMediaUrl(rawUrl, suffixes);
  if (!url) throw new MediaError(MEDIA_ERRORS.blockedHost);
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  timer.unref?.();
  try {
    for (let hop = 0; hop <= MAX_REDIRECTS; hop += 1) {
      let addrs;
      try { addrs = await lookup(url.hostname); } catch { throw new MediaError(MEDIA_ERRORS.downloadFailed); }
      const list = (Array.isArray(addrs) ? addrs : [addrs]).map((a) => (typeof a === 'string' ? a : a?.address));
      if (!list.length || list.some((ip) => !ip || isPrivateIp(ip))) throw new MediaError(MEDIA_ERRORS.blockedHost);
      let res;
      try {
        res = await fetchImpl(url.toString(), {
          redirect: 'manual', signal: ctl.signal, headers: { 'user-agent': 'Gen-Harness-bridge', accept: '*/*' },
        });
      } catch (err) {
        throw new MediaError(ctl.signal.aborted || err?.name === 'AbortError' ? MEDIA_ERRORS.timeout : MEDIA_ERRORS.downloadFailed);
      }
      if (res.status >= 300 && res.status < 400) {
        const next = res.headers?.get?.('location');
        try { res.body?.cancel?.(); } catch { /* bỏ */ }
        const target = next ? safeMediaUrl(new URL(next, url).toString(), suffixes) : null;
        if (!target) throw new MediaError(MEDIA_ERRORS.blockedHost);
        url = target;
        continue;
      }
      if (!res.ok) { try { res.body?.cancel?.(); } catch { /* bỏ */ } throw new MediaError(MEDIA_ERRORS.downloadFailed); }
      const declared = Number(res.headers?.get?.('content-length'));
      if (Number.isFinite(declared) && declared > cap) {
        try { res.body?.cancel?.(); } catch { /* bỏ */ }
        throw new MediaError(MEDIA_ERRORS.tooLarge);
      }
      let bytes;
      try { bytes = await collectCapped(res.body, cap); } catch (err) {
        if (err instanceof MediaError) throw err;
        throw new MediaError(ctl.signal.aborted ? MEDIA_ERRORS.timeout : MEDIA_ERRORS.downloadFailed);
      }
      let mime = baseMime(res.headers?.get?.('content-type'));
      if (mime === 'application/octet-stream' || mime === 'binary/octet-stream') mime = '';
      return { bytes, mime };
    }
    throw new MediaError(MEDIA_ERRORS.downloadFailed); // quá nhiều chuyển hướng
  } finally {
    clearTimeout(timer);
  }
}

/** Giới hạn đồng thời cho tải media (tránh dồn lên phiên/nền tảng). */
export class Semaphore {
  constructor(n) { this.n = n; this.q = []; }

  async run(fn) {
    if (this.n <= 0) await new Promise((r) => { this.q.push(r); });
    else this.n -= 1;
    try { return await fn(); } finally {
      const next = this.q.shift();
      if (next) next(); else this.n += 1;
    }
  }
}
