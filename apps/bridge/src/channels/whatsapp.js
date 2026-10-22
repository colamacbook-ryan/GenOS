// Adapter WhatsApp (Baileys). Một instance = một phiên. Trạng thái xác thực chỉ nằm trong RAM
// (initAuthCreds + BufferJSON) và được tuần tự hoá thành credential. Thư viện tiêm qua `lib`.
// Sự kiện phát ra: qr · scanned · active · credential · ended · inbound.
import { randomBytes } from 'node:crypto';
import { EventEmitter } from 'node:events';
import { VERSION } from '../config.js';
import { silentLogger } from '../log.js';
import { RecentIds, errorCode, toIso, toPlain, withMeta } from '../util.js';

export const WA_FIRST_QR_TTL_MS = 60000; // Baileys: QR đầu sống 60 giây, các QR sau 20 giây
export const WA_NEXT_QR_TTL_MS = 20000;
// Phiên CHƯA từng hoạt động (ghép cặp xong mà không mở được socket) vẫn có trần; phiên đã lưu / đã từng hoạt động mất mạng
// thì thử lại KHÔNG giới hạn (lùi tối đa 60 giây) — chỉ 401 (loggedOut) mới là bị từ chối thật, mất mạng KHÔNG được xoá phiên.
const MAX_RECONNECTS = 10;
export const WA_RETRY_NOTICE_AFTER = 2; // báo lõi "đang nối lại" sau chừng này lần hụt liên tiếp
// Socket đóng TRƯỚC khi ra được mã QR nào (mất mạng/DNS, máy chủ WhatsApp từ chối đăng ký thiết bị: 405/428…):
// thử lại có giãn cách rồi báo lỗi thật, không quay vòng vô hạn (bản cũ mở >40 socket/giây và UI kẹt "Đang tạo mã QR…").
// 403 (DR.forbidden) KHÔNG nằm trong nhóm này: kết thúc ngay với FORBIDDEN (xem #onClose).
const MAX_NO_QR_ATTEMPTS = 4;
const VERSION_FETCH_TIMEOUT_MS = 5000;
const VERSION_CACHE_MS = 30 * 60 * 1000;
// Yêu cầu lấy phiên bản WA Web TỰ NHẬN là bridge của Gen-Harness. `fetchLatestWaWebVersion` của Baileys mặc định giả
// user-agent Chrome + `sec-fetch-site: none` "để qua mặt chống bot" — luật cứng của repo: không né chống bot. Ở đây ghi
// đè cả hai (options.headers thắng mặc định); nếu WhatsApp từ chối thì dùng phiên bản đóng gói trong Baileys.
export const waVersionHeaders = () => ({
  'user-agent': `Gen-Harness-bridge/${VERSION}`,
  'sec-fetch-site': 'cross-site',
});
// Máy chủ WhatsApp (2026-07 trở đi) gửi <notification type="companion_reg_refresh"> tới thiết bị chưa ghép cặp: khoá ADV
// trong mã QR đã cũ → điện thoại báo "không liên kết được thiết bị". Baileys 7.0.0-rc14 bỏ qua thông báo này
// (WhiskeySockets/Baileys#2737, bản vá #2765 chưa phát hành) nên adapter tự xử lý như WhatsApp Web.
const REFRESH_TAGS = new Set(['companion_reg_refresh', 'pair-device-rotate-qr']);
const NETWORK_RE = /ENOTFOUND|EAI_AGAIN|ECONNREFUSED|ECONNRESET|ETIMEDOUT|ENETUNREACH|EHOSTUNREACH|certificate|timed out|socket hang up/i;

const isWaVersion = (v) => Array.isArray(v) && v.length === 3 && v.every((n) => Number.isInteger(n) && n > 0);
const olderThan = (a, b) => {
  for (let i = 0; i < 3; i += 1) if (a[i] !== b[i]) return a[i] < b[i];
  return false;
};

/** Kết quả fetchLatestWaWebVersion → phiên bản dùng được, hoặc undefined (để thư viện dùng bản đóng gói). */
export function pickWaVersion(fetched, bundled) {
  if (!fetched?.isLatest || !isWaVersion(fetched.version)) return undefined;
  // Không bao giờ báo phiên bản cũ hơn bản đã biết tốt (WhiskeySockets/Baileys#2777: cũ hơn ⇒ máy chủ trả 408/428).
  if (isWaVersion(bundled) && olderThan(fetched.version, bundled)) return undefined;
  return fetched.version;
}

/** notification companion_reg_refresh (theo thuộc tính type hoặc nút con). */
export function isCompanionRefresh(node) {
  if (!node || node.tag !== 'notification') return false;
  if (node.attrs?.type === 'companion_reg_refresh') return true;
  const kids = Array.isArray(node.content) ? node.content : [];
  return kids.some((k) => REFRESH_TAGS.has(k?.tag));
}

/** Lý do (mã) khi socket đóng trước khi ra mã QR: lỗi mạng hay máy chủ WhatsApp từ chối. */
export function noQrErrorCode(statusCode, message) {
  if (NETWORK_RE.test(String(message || ''))) return 'WA_NETWORK';
  return `WA_NO_QR_${Number.isInteger(statusCode) ? statusCode : 0}`;
}

export async function loadWhatsAppLib() {
  const [b, qrcode, pino] = await Promise.all([
    import('@whiskeysockets/baileys'), import('qrcode'), import('pino'),
  ]);
  const toDataURL = qrcode.toDataURL || qrcode.default?.toDataURL;
  const pinoFn = pino.default || pino;
  const bundled = b.DEFAULT_CONNECTION_CONFIG?.version;
  let cache = { at: 0, version: undefined };
  let inflight = null;
  // Phiên bản WA Web hiện hành lấy từ sw.js của chính web.whatsapp.com (cùng máy chủ với websocket, không cần thêm đường ra),
  // với user-agent TỰ NHẬN `Gen-Harness-bridge/<phiên bản>` (không giả trình duyệt — xem `waVersionHeaders`).
  // Bản cũ đọc dòng 7 của master Baileys = đúng bản đóng gói ⇒ không bao giờ mới hơn và hỏng khi chặn raw.githubusercontent.com.
  // Lấy lại mỗi 30 phút theo từng lần đăng nhập (không chốt một lần lúc khởi động); lỗi ⇒ dùng bản lần trước/đóng gói.
  const getVersion = async () => {
    if (cache.version && Date.now() - cache.at < VERSION_CACHE_MS) return cache.version;
    inflight ||= (async () => {
      try {
        const fetched = await Promise.race([
          b.fetchLatestWaWebVersion({ signal: AbortSignal.timeout(VERSION_FETCH_TIMEOUT_MS), headers: waVersionHeaders() }),
          new Promise((_, rej) => { setTimeout(() => rej(new Error('timeout')), VERSION_FETCH_TIMEOUT_MS + 500).unref?.(); }),
        ]);
        const v = pickWaVersion(fetched, bundled);
        if (v) cache = { at: Date.now(), version: v };
        return v || cache.version;
      } catch { return cache.version; }
    })().finally(() => { inflight = null; });
    return inflight;
  };
  return {
    makeWASocket: b.makeWASocket || b.default,
    DisconnectReason: b.DisconnectReason,
    initAuthCreds: b.initAuthCreds,
    BufferJSON: b.BufferJSON,
    proto: b.proto,
    version: undefined,
    bundledVersion: bundled,
    getVersion,
    // Mã dày (URL wa.me + 4 khoá ≈ 270 ký tự ⇒ 57 module ở mức sửa lỗi L, 65 ở M): mức L + vùng trắng chuẩn 4 module + ảnh lớn
    // để thu nhỏ vẫn đọc được; web hiển thị ≥ 240px cho WhatsApp.
    toDataURL: (text) => toDataURL(text, { errorCorrectionLevel: 'L', margin: 4, width: 640 }),
    logger: pinoFn({ level: 'silent' }),
  };
}

/** Trạng thái xác thực trong RAM; `stored` là object đã tuần tự bằng BufferJSON.replacer. */
export function memoryAuthState(lib, stored) {
  const parsed = stored ? JSON.parse(JSON.stringify(stored), lib.BufferJSON.reviver) : null;
  const creds = parsed?.creds || lib.initAuthCreds();
  const keys = parsed?.keys || {};
  return {
    state: {
      creds,
      keys: {
        get: async (type, ids) => {
          const out = {};
          for (const id of ids) {
            let v = keys[type]?.[id] ?? null;
            if (v && type === 'app-state-sync-key' && lib.proto?.Message?.AppStateSyncKeyData) {
              v = lib.proto.Message.AppStateSyncKeyData.fromObject(v);
            }
            out[id] = v;
          }
          return out;
        },
        set: async (data) => {
          for (const cat of Object.keys(data)) {
            keys[cat] ||= {};
            for (const id of Object.keys(data[cat])) {
              const v = data[cat][id];
              if (v) keys[cat][id] = v; else delete keys[cat][id];
            }
          }
        },
      },
    },
    serialize: () => JSON.parse(JSON.stringify({ creds, keys }, lib.BufferJSON.replacer)),
  };
}

function bareJid(jid) {
  if (!jid || typeof jid !== 'string') return '';
  const [user, server] = jid.split('@');
  return server ? `${user.split(':')[0]}@${server}` : user.split(':')[0];
}

function phoneOf(jid) {
  if (!jid || typeof jid !== 'string' || !jid.endsWith('@s.whatsapp.net')) return undefined;
  const digits = jid.split('@')[0].split(':')[0];
  return /^\d{6,}$/.test(digits) ? `+${digits}` : undefined;
}

const WRAPPERS = ['ephemeralMessage', 'viewOnceMessage', 'viewOnceMessageV2', 'viewOnceMessageV2Extension',
  'documentWithCaptionMessage', 'editedMessage'];
const IGNORED_KEYS = new Set(['messageContextInfo', 'senderKeyDistributionMessage']);

function unwrap(message) {
  let m = message;
  for (let i = 0; i < 5 && m; i += 1) {
    const inner = WRAPPERS.map((w) => m[w]?.message).find(Boolean);
    if (!inner) break;
    m = inner;
  }
  return m;
}

/** → { kind, body, ctx } hoặc null (tin giao thức nội bộ, bỏ qua). */
function classify(message, stubType) {
  const m = unwrap(message);
  if (!m) return stubType ? { kind: 'system', body: null, ctx: null } : null;
  const type = Object.keys(m).find((k) => !IGNORED_KEYS.has(k) && m[k] !== null && m[k] !== undefined);
  if (!type) return stubType ? { kind: 'system', body: null, ctx: null } : null;
  const c = m[type];
  switch (type) {
    case 'conversation': return { kind: 'text', body: String(c), ctx: null };
    case 'extendedTextMessage': return { kind: 'text', body: c.text ?? null, ctx: c.contextInfo };
    case 'imageMessage': return { kind: 'image', body: c.caption || null, ctx: c.contextInfo };
    case 'stickerMessage': return { kind: 'sticker', body: null, ctx: c.contextInfo };
    case 'documentMessage': return { kind: 'file', body: c.caption || c.fileName || null, ctx: c.contextInfo };
    case 'videoMessage':
    case 'ptvMessage': return { kind: 'other', body: c.caption || null, ctx: c.contextInfo };
    case 'audioMessage': return { kind: 'other', body: null, ctx: c.contextInfo };
    case 'reactionMessage': return { kind: 'reaction', body: c.text || null, ctx: null };
    case 'protocolMessage':
      // Chỉ giữ thu hồi tin (REVOKE = 0); các loại khác là đồng bộ nội bộ của WhatsApp.
      return Number(c?.type ?? -1) === 0 ? { kind: 'system', body: null, ctx: null } : null;
    default: return { kind: 'other', body: null, ctx: c?.contextInfo || null };
  }
}

export class WhatsAppAdapter extends EventEmitter {
  channel = 'whatsapp';

  constructor({ lib, sessionId, logger = silentLogger, qrMax = 5, now = Date.now, reconnectBaseMs = 2000 } = {}) {
    super();
    this.lib = lib;
    this.sessionId = sessionId;
    this.log = logger;
    this.qrMax = qrMax;
    this.now = now;
    this.reconnectBaseMs = reconnectBaseMs;
    this.state = 'idle';
    this.sock = null;
    this.gen = 0;
    this.qrCount = 0;
    this.qrInSocket = 0;
    this.noQrFails = 0; // số socket liên tiếp đóng trước khi ra mã QR đầu tiên
    this.lastQr = null; // { raw, expiresAt } của mã đang hiện (để dựng lại khi máy chủ yêu cầu làm mới)
    this.lastRefreshNode = null;
    this.reconnects = 0;
    this.retrying = false; // đã báo lõi "mất mạng, đang nối lại"
    this.everActive = false;
    this.cancelled = false;
    this.lastSyncComplete = true;
    this.groupNames = new Map();
    this.names = new Map(); // jid → pushName (chỉ trong RAM, dùng cho danh bạ nhóm)
    this.recent = new RecentIds(2000);
    this.own = { jid: '', lid: '', name: '' };
  }

  /** credential = object đã tuần tự (BufferJSON) hoặc null (QR). Resolve khi active hoặc ended. */
  async login(credential) {
    this.lib ||= await loadWhatsAppLib();
    try {
      this.auth = memoryAuthState(this.lib, credential);
    } catch {
      return this.#end('expired', 'CREDENTIAL_INVALID');
    }
    this.resuming = Boolean(credential);
    if (credential && !this.auth.state.creds?.me?.id) return this.#end('expired', 'CREDENTIAL_INVALID');
    // Phiên bản WA Web hiện hành, lấy lại theo từng lần đăng nhập (lỗi/hết giờ ⇒ để thư viện dùng bản đóng gói).
    this.version = (await Promise.resolve(this.lib.getVersion?.()).catch(() => undefined)) || this.lib.version;
    if (this.cancelled) return 'closed';
    this.log.info('whatsapp: phiên bản WA Web', { version: this.version ? this.version.join('.') : 'đóng gói trong Baileys' });
    this.state = credential ? 'connecting' : 'qr';
    return new Promise((resolve) => {
      this.settle = resolve;
      this.#connect();
    });
  }

  #connect() {
    if (this.cancelled || this.state === 'ended') return;
    const gen = ++this.gen;
    this.qrInSocket = 0;
    this.lastQr = null; // ref của socket cũ không còn dùng được
    const sock = this.lib.makeWASocket({
      ...(this.version ? { version: this.version } : {}),
      auth: this.auth.state,
      printQRInTerminal: false,
      logger: this.lib.logger,
      browser: ['Gen-Harness', 'Chrome', '1.0.0'],
      markOnlineOnConnect: false, // để điện thoại vẫn nhận thông báo
      syncFullHistory: false,
      shouldSyncHistoryMessage: () => false,
      getMessage: async () => undefined,
    });
    this.sock = sock;
    sock.ev.on('creds.update', () => {
      if (gen === this.gen && this.state === 'active') this.emit('credential');
    });
    sock.ev.on('connection.update', (u) => {
      this.#onConnection(u, gen).catch((err) => this.log.error('whatsapp: connection.update lỗi', { error: errorCode(err) }));
    });
    sock.ev.on('messages.upsert', (ev) => {
      if (gen !== this.gen || ev?.type !== 'notify') return;
      for (const m of ev.messages || []) this.#onLibMessage(m);
    });
    // v0.1.72: ai thêm / mời mình ra khỏi nhóm. Baileys phát cho cả stub `notify` lẫn `append` (offline).
    sock.ev.on('group-participants.update', (u) => {
      if (gen !== this.gen || this.state !== 'active') return;
      this.#emitGroupEvent(() => this.normaliseParticipants(u));
    });
    sock.ev.on('groups.upsert', (list) => {
      if (gen !== this.gen || this.state !== 'active') return;
      for (const g of Array.isArray(list) ? list : []) this.#emitGroupEvent(() => this.normaliseGroupCreate(g));
    });
    // Baileys chưa xử lý companion_reg_refresh: tự nghe thông báo thô trên websocket của socket này.
    if (typeof sock.ws?.on === 'function') {
      const onRefresh = (node) => {
        this.#onCompanionRefresh(node, gen).catch((err) => this.log.error('whatsapp: làm mới mã QR lỗi', { error: errorCode(err) }));
      };
      sock.ws.on('CB:notification,type:companion_reg_refresh', onRefresh);
      sock.ws.on('CB:notification,,companion_reg_refresh', onRefresh);
      sock.ws.on('CB:notification,,pair-device-rotate-qr', onRefresh);
    }
  }

  /** Mã QR dùng khoá ADV hiện hành của creds (đổi sau companion_reg_refresh; Baileys chốt khoá cũ trong closure). */
  #currentQr(raw) {
    const adv = this.auth?.state?.creds?.advSecretKey;
    const hash = raw.indexOf('#');
    if (!adv || hash < 0) return raw;
    const parts = raw.slice(hash + 1).split(',');
    if (parts.length < 4 || parts[3] === adv) return raw;
    parts[3] = adv; // [ref, noiseKey, identityKey, advSecretKey, platform]
    return `${raw.slice(0, hash + 1)}${parts.join(',')}`;
  }

  /**
   * Máy chủ báo companion_reg_refresh cho thiết bị CHƯA ghép cặp: LUÔN đổi khoá ADV (32 byte ngẫu nhiên, như WhatsApp Web) —
   * kể cả khi chưa có mã QR nào (thông báo có thể tới trước / cùng lúc iq pair-device: Baileys đọc `creds.advSecretKey` đúng
   * lúc dựng mã đầu tiên nên sẽ dùng khoá mới). Chỉ khi đã có mã đang hiện mới dựng lại đúng mã đó bằng khoá mới — không tốn
   * thêm mã (qrCount) và không đổi hạn của mã.
   */
  async #onCompanionRefresh(node, gen) {
    if (gen !== this.gen || this.cancelled || this.state === 'ended') return;
    if (!isCompanionRefresh(node) || node === this.lastRefreshNode) return;
    this.lastRefreshNode = node;
    const creds = this.auth?.state?.creds;
    if (!creds || creds.me?.id) return; // đã ghép cặp: giữ nguyên khoá
    creds.advSecretKey = randomBytes(32).toString('base64');
    try { this.sock?.ev?.emit?.('creds.update', { advSecretKey: creds.advSecretKey }); } catch { /* chỉ để thư viện đồng bộ */ }
    if (!this.lastQr) {
      this.log.info('whatsapp: máy chủ yêu cầu làm mới mã QR (companion_reg_refresh) trước khi có mã — đã đổi khoá ghép cặp');
      return;
    }
    const { raw, expiresAt } = this.lastQr;
    this.log.info('whatsapp: máy chủ yêu cầu làm mới mã QR (companion_reg_refresh) — đã đổi khoá ghép cặp, dựng lại mã');
    const image = await this.lib.toDataURL(this.#currentQr(raw));
    if (gen !== this.gen || this.cancelled || this.state === 'ended') return;
    this.emit('qr', { image, expiresAt });
  }

  async #onConnection(u, gen) {
    if (gen !== this.gen || this.cancelled || this.state === 'ended') return;
    const { connection, lastDisconnect, qr, isNewLogin } = u || {};
    if (qr && this.resuming) {
      // Phiên đã lưu mà máy chủ lại đòi QR → phiên không còn hiệu lực.
      this.#end('expired', 'CREDENTIAL_REJECTED');
      return;
    }
    if (qr) {
      // Mỗi mã QR mới = mã trước đã hết hạn. Quá qrMax mã → dừng.
      if (this.qrCount >= this.qrMax) {
        this.#end('expired', 'QR_EXPIRED');
        return;
      }
      this.qrCount += 1;
      this.qrInSocket += 1;
      this.noQrFails = 0;
      const ttl = this.qrInSocket === 1 ? WA_FIRST_QR_TTL_MS : WA_NEXT_QR_TTL_MS;
      const expiresAt = new Date(this.now() + ttl).toISOString();
      this.lastQr = { raw: qr, expiresAt };
      const image = await this.lib.toDataURL(this.#currentQr(qr));
      if (gen !== this.gen || this.cancelled || this.state === 'ended') return;
      this.emit('qr', { image, expiresAt });
    }
    if (isNewLogin) {
      this.emit('scanned', { displayName: this.auth.state.creds?.me?.name || null });
    }
    if (connection === 'open') {
      this.reconnects = 0;
      this.everActive = true;
      const user = this.sock.user || this.auth.state.creds?.me || {};
      this.own = { jid: bareJid(user.id), lid: bareJid(user.lid), name: user.name || user.verifiedName || '' };
      if (this.state !== 'active') {
        this.state = 'active';
        const account = { id: this.own.jid, name: this.own.name };
        const phone = phoneOf(this.own.jid);
        if (phone) account.phone = phone;
        this.retrying = false;
        this.emit('active', { account, credential: this.getCredential() });
        this.#settle('active');
      } else if (this.retrying) {
        // Đã báo lõi mất mạng ⇒ báo lại "đã nối" để lõi đổi trạng thái về Đang kết nối.
        this.retrying = false;
        const account = { id: this.own.jid, name: this.own.name };
        const phone = phoneOf(this.own.jid);
        if (phone) account.phone = phone;
        this.emit('active', { account, credential: this.getCredential() });
      }
      return;
    }
    if (connection === 'close') this.#onClose(lastDisconnect?.error?.output?.statusCode, gen, lastDisconnect?.error?.message);
  }

  #onClose(code, gen, message) {
    const DR = this.lib.DisconnectReason;
    this.sock = null;
    this.log.info('whatsapp: kết nối đóng', { code: code ?? null, qr_in_socket: this.qrInSocket });
    if (code === DR.loggedOut) {
      // 401: phiên đã lưu bị từ chối / bị đăng xuất; còn khi đang ghép cặp bằng QR là máy chủ từ chối chính lần ghép này.
      const why = this.state === 'active' || this.everActive ? 'LOGGED_OUT_REMOTELY' : this.resuming ? 'CREDENTIAL_REJECTED' : 'PAIRING_REJECTED';
      this.#end('expired', why);
      return;
    }
    if (code === DR.connectionReplaced) return this.#end('error', 'CONNECTION_REPLACED');
    if (code === DR.forbidden) return this.#end('error', 'FORBIDDEN');
    if (code === DR.multideviceMismatch) return this.#end('error', 'MULTIDEVICE_MISMATCH');
    if (code === DR.restartRequired) return this.#schedule(0, gen); // sau khi ghép cặp QR
    const pairing = this.state === 'qr' && !this.auth.state.creds?.me?.id;
    if (pairing) {
      if (this.qrInSocket === 0) {
        // Socket đóng khi CHƯA ra mã QR nào: mất mạng/DNS hoặc máy chủ từ chối đăng ký thiết bị (405/428…).
        // Thử lại có giãn cách (2s, 4s, 8s…) rồi báo lỗi thật; không mở socket liên tục.
        this.noQrFails += 1;
        if (this.noQrFails >= MAX_NO_QR_ATTEMPTS) return this.#end('error', noQrErrorCode(code, message));
        return this.#schedule(Math.min(30000, this.reconnectBaseMs * 2 ** (this.noQrFails - 1)), gen);
      }
      // Hết lượt QR của socket này → mở socket mới để xin QR tiếp, trong giới hạn qrMax.
      if (this.qrCount >= this.qrMax) return this.#end('expired', 'QR_EXPIRED');
      return this.#schedule(0, gen);
    }
    this.reconnects += 1;
    if (!this.resuming && !this.everActive && this.reconnects > MAX_RECONNECTS) {
      return this.#end('error', 'RECONNECT_FAILED');
    }
    if (this.reconnects >= WA_RETRY_NOTICE_AFTER && !this.retrying) {
      this.retrying = true;
      this.emit('retrying', { error: 'NETWORK_UNAVAILABLE' });
    }
    return this.#schedule(Math.min(60000, this.reconnectBaseMs * 2 ** Math.min(this.reconnects - 1, 10)), gen);
  }

  #schedule(delayMs, gen) {
    const t = setTimeout(() => { if (gen === this.gen) this.#connect(); }, delayMs);
    t.unref?.();
  }

  #onLibMessage(m) {
    let p;
    try { p = this.normalise(m); } catch (err) {
      this.log.warn('whatsapp: không chuẩn hoá được tin', { error: errorCode(err) });
      return;
    }
    if (!p) return;
    if (!this.recent.add(`${p.external_msg_id}:${p.external_group_id || ''}`)) return;
    this.emit('inbound', p);
  }

  #emitGroupEvent(fn) {
    let ev;
    try { ev = fn(); } catch (err) {
      this.log.warn('whatsapp: không chuẩn hoá được sự kiện nhóm', { error: errorCode(err) });
      return;
    }
    if (ev) this.emit('group_event', ev);
  }

  #mine() {
    return new Set([this.own?.jid, this.own?.lid].filter(Boolean));
  }

  /** Người tác động (author LID / authorPn JID) → {by, alt, self}. author là mình ⇒ self (tự vào bằng link / tự rời). */
  #actor(author, authorPn) {
    const mine = this.#mine();
    const a = bareJid(author);
    const pn = bareJid(authorPn);
    const self = [a, pn].some((j) => j && mine.has(j));
    if (self) return { by: null, alt: null, self: true };
    return { by: a || pn || null, alt: a ? (pn || null) : null, self: false };
  }

  /**
   * `group-participants.update` ({id, author, authorPn, participants[], action}) → self_joined / self_removed hoặc null.
   * `participants[]` là object {id, phoneNumber?, lid?} (bản cũ là chuỗi) ⇒ so cả JID lẫn LID của mình.
   */
  normaliseParticipants(u) {
    const action = u?.action;
    if (action !== 'add' && action !== 'remove') return null;
    const groupId = String(u.id || '');
    if (!groupId.endsWith('@g.us')) return null;
    const mine = this.#mine();
    const parts = Array.isArray(u.participants) ? u.participants : [];
    const hit = parts.some((p) => (typeof p === 'string' ? [p] : [p?.id, p?.lid, p?.phoneNumber])
      .map(bareJid).some((j) => j && mine.has(j)));
    if (!hit) return null;
    const at = new Date(this.now()).toISOString();
    const actor = this.#actor(u.author, u.authorPn);
    if (action === 'remove') return { type: 'group.self_removed', payload: { group_id: groupId, by: actor.by, at } };
    let method = 'add';
    if (actor.self) method = 'link';
    else if (!actor.by) method = 'unknown';
    return {
      type: 'group.self_joined',
      payload: {
        group_id: groupId, name: this.groupNames.get(groupId) || '', added_by: actor.by, added_by_alt: actor.alt, method, at,
      },
    };
  }

  /** `groups.upsert` (nhóm vừa được tạo có mình, Socket/messages-recv.js) → self_joined method=create. */
  normaliseGroupCreate(g) {
    const groupId = String(g?.id || '');
    if (!groupId.endsWith('@g.us')) return null;
    if (g.subject) this.groupNames.set(groupId, String(g.subject));
    const actor = this.#actor(g.author || g.owner, g.authorPn || g.ownerPn);
    return {
      type: 'group.self_joined',
      payload: {
        group_id: groupId, name: this.groupNames.get(groupId) || '', added_by: actor.by, added_by_alt: actor.alt,
        method: 'create', at: new Date(this.now()).toISOString(),
      },
    };
  }

  #settle(result) {
    const s = this.settle;
    this.settle = null;
    s?.(result);
  }

  #end(reason, error) {
    if (this.state === 'ended') return 'ended';
    this.state = 'ended';
    this.gen += 1; // bỏ mọi sự kiện muộn của socket cũ
    const sock = this.sock;
    this.sock = null;
    try { sock?.end?.(undefined); } catch { /* đã đóng */ }
    this.emit('ended', { reason, error });
    this.#settle('ended');
    return 'ended';
  }

  getCredential() {
    return this.auth ? this.auth.serialize() : null;
  }

  /** Đăng xuất trên WhatsApp (gỡ thiết bị liên kết), rồi bỏ phiên khỏi RAM. */
  async logout() {
    const sock = this.sock;
    const wasActive = this.state === 'active';
    this.cancelled = true;
    this.gen += 1;
    if (sock && wasActive) {
      try { await sock.logout(); } catch (err) { this.log.warn('whatsapp: logout lỗi', { error: errorCode(err) }); }
    }
    this.close();
  }

  /** Dừng không phát sự kiện. */
  close() {
    this.cancelled = true;
    this.gen += 1;
    const sock = this.sock;
    this.sock = null;
    try { sock?.end?.(undefined); } catch { /* đã đóng */ }
    this.state = 'ended';
    this.auth = null;
    this.#settle('closed');
  }

  async send(threadId, threadType, text) {
    if (this.state !== 'active' || !this.sock) throw Object.assign(new Error('SESSION_NOT_ACTIVE'), { code: 'SESSION_NOT_ACTIVE' });
    const res = await this.sock.sendMessage(String(threadId), { text });
    return res?.key?.id ? String(res.key.id) : null;
  }

  /**
   * Danh bạ: MỌI nhóm kèm MỌI thành viên (Q5=2). Một lần gọi duy nhất nên không cần giãn cách. `role` = 'admin' khi
   * participant.admin ('admin' | 'superadmin'), còn lại 'member'.
   */
  async syncGroups() {
    if (!this.sock) throw new Error('SESSION_NOT_ACTIVE');
    const all = await this.sock.groupFetchAllParticipating();
    this.lastSyncComplete = true;
    const mine = new Set([this.own?.jid, this.own?.lid].filter(Boolean));
    return Object.entries(all || {}).map(([jid, g]) => {
      const participants = Array.isArray(g.participants) ? g.participants : [];
      const name = g.subject || '';
      this.groupNames.set(jid, name);
      const count = Number(g.size ?? participants.length) || participants.length;
      return {
        external_id: jid,
        name,
        member_count: count,
        members_complete: participants.length > 0 && participants.length >= count,
        members: participants.map((p) => {
          // Nhóm địa chỉ theo LID liệt kê CHÍNH MÌNH bằng LID (kèm phoneNumber = JID), còn tin của mình luôn mang JID ⇒ nếu
          // giữ nguyên LID thì người của Sếp (nhận diện theo JID) "vắng" trong danh sách đủ và bị đánh dấu đã rời nhóm.
          // So cả JID lẫn LID của chính mình (id, lid, phoneNumber) và báo bằng JID.
          const isMe = [p.id, p.lid, p.phoneNumber].map(bareJid).some((j) => j && mine.has(j));
          const id = isMe && this.own?.jid ? this.own.jid : (p.id || '');
          const member = { external_id: id, name: p.name || p.notify || this.names.get(bareJid(id)) || '' };
          const phone = phoneOf(p.phoneNumber) || phoneOf(id);
          if (phone) member.phone = phone;
          member.role = p.admin ? 'admin' : 'member';
          return member;
        }),
      };
    });
  }

  /** WAMessage (messages.upsert) → payload gh.bridge.inbound `message`. null = bỏ qua. */
  normalise(m) {
    const key = m?.key || {};
    const remote = key.remoteJid || '';
    if (!remote || remote.endsWith('@broadcast') || remote.endsWith('@newsletter')) return null;
    const cls = classify(m.message, m.messageStubType);
    if (!cls) return null;
    const isGroup = remote.endsWith('@g.us');
    const fromMe = Boolean(key.fromMe);
    let sender;
    let senderAlt;
    if (fromMe) sender = this.own.jid || '';
    else if (isGroup) { sender = key.participant || ''; senderAlt = key.participantAlt || key.participantPn; }
    else { sender = remote; senderAlt = key.remoteJidAlt || key.senderPn; }
    const senderName = fromMe ? this.own.name : (m.pushName || '');
    if (!fromMe && sender && m.pushName) this.names.set(bareJid(sender), m.pushName);
    const mentioned = Array.isArray(cls.ctx?.mentionedJid) ? cls.ctx.mentionedJid.map(bareJid) : [];
    const mine = [this.own.jid, this.own.lid].filter(Boolean);
    const out = {
      channel: 'whatsapp',
      session_id: this.sessionId,
      external_msg_id: String(key.id || ''),
      external_group_id: isGroup ? remote : null,
      sender_external_id: sender,
      sender_name: senderName || '',
      occurred_at: toIso(m.messageTimestamp, 's', this.now),
      kind: cls.kind,
      body_text: cls.body,
      direction: fromMe ? 'outbound' : 'inbound',
      mentions_self: mentioned.some((j) => mine.includes(j)),
      // Trả lời (trích dẫn) tin của chính mình: contextInfo.participant = người viết tin được trích (JID hoặc LID).
      replies_to_self: Boolean(cls.ctx?.participant && mine.includes(bareJid(cls.ctx.participant))),
      payload: toPlain(m),
    };
    if (isGroup && this.groupNames.has(remote)) out.group_name = this.groupNames.get(remote);
    const phone = phoneOf(sender) || phoneOf(senderAlt);
    if (phone) out.sender_phone = phone;
    const forwarded = Boolean(cls.ctx?.isForwarded || Number(cls.ctx?.forwardingScore || 0) > 0);
    return withMeta(out, {
      thread_ids: isGroup ? [] : [remote, key.remoteJidAlt].map(bareJid).filter(Boolean),
      sender_alt_id: fromMe ? null : (senderAlt || null),
      // Lõi ghi tập Owner ở dạng chuẩn hoá (bỏ hậu tố thiết bị) ⇒ so thêm dạng đó.
      sender_ids: fromMe ? [] : [sender, senderAlt].map(bareJid).filter(Boolean),
      forwarded,
      read_ref: isGroup || fromMe ? null : { remoteJid: remote, id: String(key.id || ''), fromMe: false },
    });
  }

  /** v0.1.73: đánh dấu «đã xem» tin 1-1 (CHỈ kênh quản trị với Sếp — SessionManager quyết định gọi). */
  async markSeen(ref) {
    if (this.state !== 'active' || !this.sock || !ref) return;
    await this.sock.readMessages([ref]);
  }

  /** v0.1.73: «đang nhập…» (composing) / tắt (paused) trong thread 1-1. */
  async setTyping(threadId, on) {
    if (this.state !== 'active' || !this.sock) return;
    await this.sock.sendPresenceUpdate(on ? 'composing' : 'paused', String(threadId));
  }
}
