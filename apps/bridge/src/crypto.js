// Mã hoá phiên khi truyền (AES-256-GCM) và kiểm permit gửi tin (HMAC-SHA256).
// Khớp apps/api/gh/crypto.py: khoá con = sha256(bridge_key ‖ purpose).
import { createCipheriv, createDecipheriv, createHash, createHmac, randomBytes, timingSafeEqual } from 'node:crypto';

export const PERMIT_ERRORS = Object.freeze({
  invalid: 'PERMIT_INVALID',
  expired: 'PERMIT_EXPIRED',
  mismatch: 'PERMIT_MISMATCH',
  reused: 'PERMIT_REUSED',
});

export function deriveSubkey(bridgeKey, purpose) {
  return createHash('sha256').update(Buffer.concat([bridgeKey, Buffer.from(purpose, 'utf8')])).digest();
}

export function sha256Hex(text) {
  return createHash('sha256').update(Buffer.from(String(text), 'utf8')).digest('hex');
}

function b64urlDecode(s) {
  if (typeof s !== 'string' || !/^[A-Za-z0-9_-]*={0,2}$/.test(s)) throw new Error('bad base64url');
  return Buffer.from(s, 'base64url');
}

export class Keyring {
  /** @param {Buffer|null} bridgeKey 32 byte, hoặc null khi chưa cấu hình. */
  constructor(bridgeKey) {
    this.enabled = Boolean(bridgeKey && bridgeKey.length === 32);
    this.transportKey = this.enabled ? deriveSubkey(bridgeKey, 'transport') : null;
    this.permitKey = this.enabled ? deriveSubkey(bridgeKey, 'permit') : null;
  }

  #need() {
    if (!this.enabled) throw Object.assign(new Error('BRIDGE_KEY_MISSING'), { code: 'BRIDGE_KEY_MISSING' });
  }

  /** base64(nonce[12] ‖ ciphertext ‖ tag[16]), AAD = "<channel>:<session_id>". */
  encryptTransport(plaintext, aad, nonce = randomBytes(12)) {
    this.#need();
    const cipher = createCipheriv('aes-256-gcm', this.transportKey, nonce);
    cipher.setAAD(Buffer.from(aad, 'utf8'));
    const ct = Buffer.concat([cipher.update(Buffer.from(plaintext)), cipher.final()]);
    return Buffer.concat([nonce, ct, cipher.getAuthTag()]).toString('base64');
  }

  /** Trả Buffer plaintext; ném lỗi nếu hỏng / sai khoá / sai AAD. */
  decryptTransport(blob, aad) {
    this.#need();
    const raw = Buffer.from(String(blob), 'base64');
    if (raw.length < 12 + 16) throw new Error('transport blob too short');
    const decipher = createDecipheriv('aes-256-gcm', this.transportKey, raw.subarray(0, 12));
    decipher.setAAD(Buffer.from(aad, 'utf8'));
    decipher.setAuthTag(raw.subarray(raw.length - 16));
    return Buffer.concat([decipher.update(raw.subarray(12, raw.length - 16)), decipher.final()]);
  }

  permitHmac(message) {
    this.#need();
    return createHmac('sha256', this.permitKey).update(Buffer.from(message, 'utf8')).digest();
  }

  /** Chỉ dùng cho test / công cụ: phát permit giống phía lõi. */
  signPermit(claims) {
    const head = Buffer.from(JSON.stringify(claims), 'utf8').toString('base64url');
    return `${head}.${this.permitHmac(head).toString('base64url')}`;
  }

  /**
   * Kiểm chữ ký (hằng thời gian) rồi giải claims. Chưa kiểm exp/khớp lệnh/nonce.
   * Trả { ok: true, claims } hoặc { ok: false, error, claims? } (claims chưa xác thực, chỉ để đối chiếu).
   */
  verifyPermitSignature(token) {
    let unverified = null;
    if (!this.enabled) return { ok: false, error: PERMIT_ERRORS.invalid, claims: unverified };
    try {
      const parts = String(token || '').split('.');
      if (parts.length !== 2 || !parts[0] || !parts[1]) return { ok: false, error: PERMIT_ERRORS.invalid, claims: null };
      try { unverified = JSON.parse(b64urlDecode(parts[0]).toString('utf8')); } catch { unverified = null; }
      const sig = b64urlDecode(parts[1]);
      const expected = this.permitHmac(parts[0]);
      if (sig.length !== expected.length || !timingSafeEqual(sig, expected)) {
        return { ok: false, error: PERMIT_ERRORS.invalid, claims: unverified };
      }
      if (!unverified || typeof unverified !== 'object' || Array.isArray(unverified)) {
        return { ok: false, error: PERMIT_ERRORS.invalid, claims: null };
      }
      return { ok: true, claims: unverified };
    } catch {
      return { ok: false, error: PERMIT_ERRORS.invalid, claims: unverified };
    }
  }
}

/** Ba căn cứ tự gửi duy nhất (v0.1.72, khớp `Basis` ở lõi `owner_channel/autosend.py`). */
export const PERMIT_BASES = Object.freeze(['owner_dm', 'owner_tag', 'support']);

/**
 * Kiểm đầy đủ một lệnh message.send trừ bước nonce và lớp 2 `basis` (cần Redis — xem `checkPermitBasis`).
 * Thứ tự: chữ ký → exp → khớp channel/thread_id/thread_type → khớp sha256(text) → dạng claims:
 * không có `basis` = permit bản nháp (đã qua Xác nhận + mã PIN) ⇒ bắt buộc `draft_id`; có `basis` ⇒ phải là một trong
 * `PERMIT_BASES`, lạ ⇒ PERMIT_MISMATCH.
 */
export function checkPermit(keyring, cmd, nowSec = Date.now() / 1000) {
  const sig = keyring.verifyPermitSignature(cmd.permit);
  if (!sig.ok) return sig;
  const c = sig.claims;
  if (typeof c.nonce !== 'string' || !c.nonce || !Number.isFinite(Number(c.exp))) {
    return { ok: false, error: PERMIT_ERRORS.invalid, claims: c };
  }
  if (nowSec > Number(c.exp)) return { ok: false, error: PERMIT_ERRORS.expired, claims: c };
  if (c.channel !== cmd.channel || String(c.thread_id) !== String(cmd.thread_id) || c.thread_type !== cmd.thread_type) {
    return { ok: false, error: PERMIT_ERRORS.mismatch, claims: c };
  }
  if (typeof cmd.text !== 'string' || String(c.body_sha256 || '').toLowerCase() !== sha256Hex(cmd.text)) {
    return { ok: false, error: PERMIT_ERRORS.mismatch, claims: c };
  }
  if (c.basis === undefined || c.basis === null) {
    const d = c.draft_id;
    if (!((typeof d === 'string' && d) || (typeof d === 'number' && Number.isFinite(d)))) {
      return { ok: false, error: PERMIT_ERRORS.invalid, claims: c };
    }
  } else if (!PERMIT_BASES.includes(c.basis)) {
    return { ok: false, error: PERMIT_ERRORS.mismatch, claims: c };
  }
  return { ok: true, claims: c };
}

/**
 * Lớp 2 của permit tự gửi (thiết kế v0.1.71 §4.1): lõi đã kiểm trong DB, bridge kiểm lại theo khoá Redis lõi ghi.
 * Chỉ gọi sau `checkPermit` ok. Claims không `basis` (permit bản nháp) ⇒ cho qua, không đổi hành vi cũ.
 * - phiên `account_kind ≠ bot` ⇒ từ chối mọi `basis`;
 * - `owner_dm`  ⇒ `thread_type='user'`  và `thread_id ∈ gh:bridge:owner_thread:<kênh>`;
 * - `owner_tag` ⇒ `thread_type='group'` và `thread_id ∈ gh:bridge:listen:<kênh>`;
 * - `support`   ⇒ `thread_type='group'` và `thread_id ∈ gh:bridge:support:<kênh>`;
 * - `basis` lạ  ⇒ PERMIT_MISMATCH.
 * @param {object} lookup  { accountKind(): Promise<string|null>, ownerThread(id), listening(id), support(id): Promise<boolean> }
 * @returns {Promise<{ok: true} | {ok: false, error: string}>}
 */
export async function checkPermitBasis(claims, cmd, lookup) {
  const basis = claims?.basis;
  if (basis === undefined || basis === null) return { ok: true };
  const deny = { ok: false, error: PERMIT_ERRORS.mismatch };
  if (!PERMIT_BASES.includes(basis)) return deny;
  if ((await lookup.accountKind()) !== 'bot') return deny;
  const threadId = String(cmd?.thread_id ?? '');
  if (!threadId) return deny;
  if (basis === 'owner_dm') {
    return cmd.thread_type === 'user' && (await lookup.ownerThread(threadId)) ? { ok: true } : deny;
  }
  if (cmd.thread_type !== 'group') return deny;
  const inSet = basis === 'owner_tag' ? await lookup.listening(threadId) : await lookup.support(threadId);
  return inSet ? { ok: true } : deny;
}
