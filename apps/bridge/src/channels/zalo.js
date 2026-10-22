// Adapter Zalo (zca-js). Một instance = một phiên. Thư viện được tiêm qua `lib` để test không chạm mạng.
// Sự kiện phát ra: qr · scanned · active · credential · ended · inbound (payload đã chuẩn hoá).
// Không ghi file, không in QR, không log nội dung tin.
import { EventEmitter } from 'node:events';
import { silentLogger } from '../log.js';
import { RecentIds, errorCode, toIso, toPlain, withMeta } from '../util.js';

export const ZALO_QR_TTL_MS = 100000; // zca-js tự hết hạn QR sau 100 giây
export const ZALO_RETRY_MAX_MS = 60000; // lùi tối đa 60 giây giữa hai lần thử nối lại
export const ZALO_UNKNOWN_RETRY_MAX = 5; // lỗi LẠ (không phải mạng, không phải bị từ chối) liên tiếp quá chừng này lần ⇒ dừng, báo lỗi
export const ZALO_RETRY_NOTICE_AFTER = 2; // báo lõi "đang nối lại" sau chừng này lần hụt liên tiếp (một lần hụt lẻ không đáng báo)

// Nền tảng TỪ CHỐI phiên đã lưu (hết hạn / đăng xuất / cookie sai) — khác lỗi mạng tạm. zca-js ném ZaloApiError
// "Đăng nhập thất bại" khi máy chủ không trả thông tin đăng nhập, "Failed to fetch server info: Unauthorized" khi 401.
// Mọi lỗi KHÁC (ENOTFOUND, ETIMEDOUT, fetch failed, 5xx…) là lỗi tạm: giữ phiên, lùi và thử lại.
const REJECTED_RE = /Đăng nhập thất bại|Missing required params|unauthori[sz]ed|logged.?out|invalid (cookie|session|credential)/i;

/** true khi lỗi nghĩa là nền tảng từ chối phiên đã lưu (HTTP 401 / loggedOut); false = lỗi mạng/tạm thời. */
export function isCredentialRejected(err) {
  const status = Number(err?.status ?? err?.statusCode ?? err?.response?.status ?? err?.code);
  if (status === 401) return true;
  return REJECTED_RE.test(String(err?.message || ''));
}

// Lỗi mạng/máy chủ tạm thời đã biết ⇒ thử lại mãi (có lùi dần). Lỗi KHÔNG khớp (lỗi mã, dữ liệu lạ…) là "lạ": chỉ thử
// vài lần rồi dừng với RECONNECT_FAILED để Sếp thấy thẻ 'Lỗi kết nối' thay vì bridge lặng lẽ thử mỗi 60 giây mãi.
const NETWORK_RE = /ENOTFOUND|EAI_AGAIN|ECONNREFUSED|ECONNRESET|ECONNABORTED|ETIMEDOUT|ENETUNREACH|EHOSTUNREACH|ENETDOWN|EPIPE|certificate|time(d)?[ -]?out|socket hang up|fetch failed|network|Service Unavailable|Bad Gateway|Gateway Time|Too Many Requests|WebSocket (was )?closed/i;

/** 'rejected' = nền tảng từ chối phiên · 'network' = lỗi mạng/máy chủ tạm (thử mãi) · 'unknown' = không phân loại được (thử có giới hạn). */
export function classifyLoginError(err) {
  if (isCredentialRejected(err)) return 'rejected';
  const status = Number(err?.status ?? err?.statusCode ?? err?.response?.status);
  if (status === 429 || status >= 500) return 'network';
  if (NETWORK_RE.test(`${err?.code || ''} ${err?.message || ''}`)) return 'network';
  return 'unknown';
}

export async function loadZaloLib() {
  const m = await import('zca-js');
  return { Zalo: m.Zalo, ThreadType: m.ThreadType, LoginQRCallbackEventType: m.LoginQRCallbackEventType };
}

const KIND_BY_MSGTYPE = {
  webchat: 'text',
  'chat.link': 'text',
  'chat.photo': 'image',
  'chat.gif': 'image',
  'chat.doodle': 'image',
  'chat.sticker': 'sticker',
  'share.file': 'file',
};

// Bridge chỉ gửi văn bản; zca-js đòi hàm này khi gửi ảnh từ đường dẫn — không hỗ trợ.
async function imageMetadataGetter() {
  throw new Error('IMAGE_SEND_UNSUPPORTED');
}

function chunk(arr, n) {
  const out = [];
  for (let i = 0; i < arr.length; i += n) out.push(arr.slice(i, i + n));
  return out;
}

export class ZaloAdapter extends EventEmitter {
  channel = 'zalo';

  constructor({
    lib, sessionId, logger = silentLogger, qrMax = 5, now = Date.now, reconnectDelayMs = 5000,
    retryMaxMs = ZALO_RETRY_MAX_MS, retryNoticeAfter = ZALO_RETRY_NOTICE_AFTER, unknownRetryMax = ZALO_UNKNOWN_RETRY_MAX,
    directoryGapMs = 0,
  } = {}) {
    super();
    this.lib = lib;
    this.sessionId = sessionId;
    this.log = logger;
    this.qrMax = qrMax;
    this.now = now;
    this.reconnectDelayMs = reconnectDelayMs;
    this.retryMaxMs = retryMaxMs;
    this.retryNoticeAfter = retryNoticeAfter;
    this.unknownRetryMax = unknownRetryMax;
    this.retrying = false; // đã báo lõi "mất mạng, đang nối lại"
    this.wake = null;
    this.account = null;
    this.state = 'idle';
    this.credential = null;
    this.api = null;
    this.ownId = '';
    this.groupNames = new Map();
    this.directoryGapMs = directoryGapMs;
    this.lastSyncComplete = true;
    this.recent = new RecentIds(2000);
    this.qrCount = 0;
    this.qrActions = null;
    this.cancelled = false;
  }

  /** Đăng nhập: credential {imei, cookie, userAgent} → phiên đã lưu; null → QR. Resolve khi active hoặc ended. */
  async login(credential) {
    this.lib ||= await loadZaloLib();
    this.zalo = new this.lib.Zalo({ selfListen: true, logging: false, checkUpdate: false, imageMetadataGetter });
    let api;
    if (credential) {
      if (!credential.imei || !credential.cookie || !credential.userAgent) {
        return this.#end('expired', 'CREDENTIAL_INVALID');
      }
      this.credential = credential; // giữ trong RAM để thử lại khi mất mạng
      this.state = 'connecting';
      api = await this.#loginWithRetry(credential);
      if (!api) return this.cancelled ? undefined : 'ended';
    } else {
      this.state = 'qr';
      this.qrCount = 0;
      this.qrExhausted = false;
      try {
        api = await this.zalo.loginQR({}, (evt) => this.#onQrEvent(evt));
      } catch (err) {
        if (this.cancelled) return undefined;
        if (this.qrExhausted) return this.#end('expired', 'QR_EXPIRED');
        this.log.warn('zalo: đăng nhập QR lỗi', { error: errorCode(err) });
        return this.#end('error', 'LOGIN_FAILED');
      }
      if (!this.credential) return this.#end('error', 'NO_LOGIN_INFO');
    }
    if (this.cancelled) return undefined;
    return this.#activate(api);
  }

  /**
   * Nối lại bằng phiên đã lưu. CHỈ khi nền tảng từ chối thật (401 / đăng xuất) mới `expired` (lõi xoá phiên);
   * lỗi mạng (DNS, timeout, máy chủ 5xx) → lùi dần (5s, 10s… tối đa 60s) và thử lại KHÔNG giới hạn, giữ nguyên phiên.
   * Lỗi LẠ (không khớp mạng/từ chối) liên tiếp quá `unknownRetryMax` lần → dừng, `session.ended` error RECONNECT_FAILED
   * (giữ credential để Sếp bấm "Nối lại"). Trả api khi xong, null khi đã kết thúc/bị huỷ.
   */
  async #loginWithRetry(credential) {
    let unknown = 0;
    for (let attempt = 1; ; attempt += 1) {
      try {
        return await this.zalo.login(credential);
      } catch (err) {
        if (this.cancelled) return null;
        const kind = classifyLoginError(err);
        if (kind === 'rejected') {
          this.log.warn('zalo: phiên đã lưu bị từ chối', { error: errorCode(err) });
          this.#end('expired', 'CREDENTIAL_REJECTED');
          return null;
        }
        unknown = kind === 'unknown' ? unknown + 1 : 0;
        if (unknown >= this.unknownRetryMax) {
          this.log.warn('zalo: lỗi lạ lặp lại, dừng nối lại', { error: errorCode(err), attempt });
          this.#end('error', 'RECONNECT_FAILED');
          return null;
        }
        this.log.warn('zalo: chưa nối lại được (mạng?), sẽ thử lại', { error: errorCode(err), attempt });
        if (attempt >= this.retryNoticeAfter) this.#noticeRetrying();
        await this.#sleep(Math.min(this.retryMaxMs, this.reconnectDelayMs * 2 ** Math.min(attempt - 1, 10)));
        if (this.cancelled) return null;
      }
    }
  }

  #noticeRetrying() {
    if (this.retrying || this.cancelled || this.state === 'ended') return;
    this.retrying = true;
    this.emit('retrying', { error: 'NETWORK_UNAVAILABLE' });
  }

  #sleep(ms) {
    return new Promise((resolve) => {
      const t = setTimeout(() => { this.wake = null; resolve(); }, ms);
      t.unref?.();
      this.wake = () => { clearTimeout(t); this.wake = null; resolve(); };
    });
  }

  #onQrEvent(evt) {
    const T = this.lib.LoginQRCallbackEventType;
    if (this.cancelled) {
      try { evt?.actions?.abort?.(); } catch { /* đã huỷ */ }
      return;
    }
    switch (evt?.type) {
      case T.QRCodeGenerated: {
        this.qrCount += 1;
        this.qrActions = evt.actions || null;
        const img = String(evt.data?.image || '');
        const image = img.startsWith('data:') ? img : `data:image/png;base64,${img}`;
        this.emit('qr', { image, expiresAt: new Date(this.now() + ZALO_QR_TTL_MS).toISOString() });
        break;
      }
      case T.QRCodeScanned:
        this.emit('scanned', { displayName: evt.data?.display_name || null });
        break;
      case T.QRCodeExpired:
      case T.QRCodeDeclined:
        // Tự xin QR mới; đủ qrMax mã mà không đăng nhập được → dừng (session.ended expired).
        if (this.qrCount >= this.qrMax) {
          this.qrExhausted = true;
          evt.actions?.abort?.();
        } else {
          evt.actions?.retry?.();
        }
        break;
      case T.GotLoginInfo:
        // evt.data = {cookie, imei, userAgent}: chỉ giữ trong RAM.
        this.credential = { ...evt.data };
        break;
      default:
        break;
    }
  }

  async #activate(api) {
    this.api = api;
    try { this.ownId = String(api.getOwnId() || ''); } catch { this.ownId = ''; }
    let name = '';
    let phone;
    try {
      const info = await api.fetchAccountInfo();
      name = info?.profile?.displayName || info?.profile?.zaloName || '';
      phone = info?.profile?.phoneNumber || undefined;
    } catch { /* không bắt buộc */ }
    if (this.cancelled) return undefined;
    this.#attach(api);
    this.state = 'active';
    this.retrying = false;
    const account = { id: this.ownId, name };
    if (phone) account.phone = phone;
    this.account = account;
    this.emit('active', { account, credential: this.getCredential() });
    return 'active';
  }

  #onLibEvent(raw) {
    let p;
    try { p = this.normalise(raw); } catch (err) {
      this.log.warn('zalo: không chuẩn hoá được tin', { error: errorCode(err) });
      return;
    }
    if (!p) return;
    if (!this.recent.add(`${p.kind}:${p.external_msg_id}:${p.sender_external_id}`)) return;
    this.emit('inbound', p);
  }

  #onGroupEvent(raw) {
    let ev;
    try { ev = this.normaliseGroupEvent(raw); } catch (err) {
      this.log.warn('zalo: không chuẩn hoá được sự kiện nhóm', { error: errorCode(err) });
      return;
    }
    if (ev) this.emit('group_event', ev);
  }

  /**
   * Sự kiện nhóm zca-js (`group_event`) → `{type: 'group.self_joined'|'group.self_removed', payload}` hoặc null.
   * Chỉ quan tâm sự kiện tác động lên CHÍNH MÌNH: `updateMembers` chứa ownId (không tin cờ `isSelf` của thư viện — cờ đó
   * cũng bật khi mình là người thực hiện). `sourceId` = người thực hiện; vào bằng link thì `sourceId` là chính mình ⇒
   * `method: 'link'`, không biết người thêm; thiếu `sourceId` ⇒ `unknown`.
   */
  normaliseGroupEvent(ev) {
    const type = ev?.type;
    if (type !== 'join' && type !== 'remove_member' && type !== 'block_member') return null;
    const d = ev.data || {};
    const members = Array.isArray(d.updateMembers) ? d.updateMembers : [];
    if (!this.ownId || !members.some((m) => String(m?.id ?? m) === this.ownId)) return null;
    const groupId = String(d.groupId ?? ev.threadId ?? '');
    if (!groupId) return null;
    const source = d.sourceId === undefined || d.sourceId === null ? '' : String(d.sourceId);
    const at = toIso(d.time, 'ms', this.now);
    if (type === 'join') {
      if (d.groupName) this.groupNames.set(groupId, String(d.groupName));
      let method = 'unknown';
      let addedBy = null;
      if (source === this.ownId) method = 'link';
      else if (source) { method = 'add'; addedBy = source; }
      return {
        type: 'group.self_joined',
        payload: {
          group_id: groupId, name: this.groupNames.get(groupId) || '', added_by: addedBy, added_by_alt: null, method, at,
        },
      };
    }
    return { type: 'group.self_removed', payload: { group_id: groupId, by: source && source !== this.ownId ? source : null, at } };
  }

  #attach(api) {
    this.api = api;
    const L = api.listener;
    L.on('message', (msg) => this.#onLibEvent(msg));
    L.on('reaction', (r) => this.#onLibEvent(r));
    L.on('group_event', (ev) => this.#onGroupEvent(ev));
    L.on('error', (err) => this.log.warn('zalo: websocket lỗi', { error: errorCode(err) }));
    L.on('closed', (code) => { this.#onClosed(code); });
    L.start({ retryOnClose: true });
  }

  // zca-js tự nối lại với các mã đóng tạm thời; 'closed' nghĩa là thư viện đã bỏ cuộc.
  async #onClosed(code) {
    if (this.cancelled || this.state !== 'active') return;
    this.log.warn('zalo: websocket đóng hẳn', { code });
    if (Number(code) === 3000) {
      this.#end('error', 'CONNECTION_REPLACED'); // tài khoản mở Zalo Web ở nơi khác
      return;
    }
    // Đăng nhập lại bằng phiên trong RAM: lỗi mạng → thử mãi (không xoá phiên), chỉ bị từ chối thật mới `expired`.
    this.#stopListener();
    this.state = 'connecting'; // trong lúc nối lại không nhận lệnh gửi (send() từ chối SESSION_NOT_ACTIVE)
    await this.#sleep(this.reconnectDelayMs);
    if (this.cancelled) return;
    const api = await this.#loginWithRetry(this.credential);
    if (!api || this.cancelled) return;
    this.api = api;
    this.#attach(api);
    this.state = 'active';
    if (this.retrying) {
      // Đã báo lõi mất mạng ⇒ báo lại "đã nối" để lõi đổi trạng thái về Đang kết nối.
      this.retrying = false;
      this.emit('active', { account: this.account || { id: this.ownId, name: '' }, credential: this.getCredential() });
    }
  }

  #end(reason, error) {
    if (this.state === 'ended') return 'ended';
    this.state = 'ended';
    this.#stopListener();
    this.emit('ended', { reason, error });
    return 'ended';
  }

  #stopListener() {
    const L = this.api?.listener;
    if (!L) return;
    try {
      L.removeAllListeners?.();
      L.on?.('error', () => {}); // 'error' không có người nghe sẽ làm sập tiến trình
    } catch { /* bỏ qua */ }
    try { L.stop?.(); } catch { /* đã dừng */ }
  }

  getCredential() {
    return this.credential ? { ...this.credential } : null;
  }

  /** zca-js không có API đăng xuất phía máy chủ: dừng listener, huỷ QR đang chờ, bỏ phiên khỏi RAM. */
  async logout() {
    this.close();
  }

  /** Dừng không phát sự kiện (tắt tiến trình / thay phiên). */
  close() {
    this.cancelled = true;
    this.wake?.();
    try { this.qrActions?.abort?.(); } catch { /* không còn chờ */ }
    this.#stopListener();
    this.credential = null;
    this.state = 'ended';
  }

  async send(threadId, threadType, text) {
    if (this.state !== 'active' || !this.api) throw Object.assign(new Error('SESSION_NOT_ACTIVE'), { code: 'SESSION_NOT_ACTIVE' });
    const T = this.lib.ThreadType;
    const res = await this.api.sendMessage({ msg: text }, String(threadId), threadType === 'group' ? T.Group : T.User);
    const id = res?.message?.msgId;
    return id === undefined || id === null ? null : String(id);
  }

  /** Giãn cách giữa hai lần hỏi nền tảng khi quét danh bạ (tránh dồn dập; 0 = không chờ, dùng trong test). */
  #gap() {
    return this.directoryGapMs > 0 ? new Promise((r) => { setTimeout(r, this.directoryGapMs); }) : Promise.resolve();
  }

  /**
   * Danh bạ: MỌI nhóm kèm MỌI thành viên (Sếp chốt Q5=2 — kể cả nhóm "Không nghe"), có giãn cách giữa các lần hỏi.
   * `this.lastSyncComplete` = false nếu có nhóm lỗi/thiếu ⇒ lõi KHÔNG được coi nhóm vắng là "đã rời".
   * Mỗi thành viên có `role` ('admin' | 'member') khi nền tảng cho biết người tạo/quản trị (creatorId, adminIds).
   */
  async syncGroups() {
    const api = this.api;
    const all = await api.getAllGroups();
    const ids = Object.keys(all?.gridVerMap || {});
    const groups = [];
    // Nền tảng trả lỗi (có thể là hãm tốc) ⇒ DỪNG cả lượt, không hỏi tiếp: hỏi dồn khi đang bị từ chối chỉ làm tệ thêm.
    // Lõi nhận phần đã quét kèm `groups_complete=false` (không coi nhóm vắng là "đã rời"); bridge lùi lịch quét kế tiếp.
    let halted = false;
    let missing = false; // nền tảng không trả thông tin của một nhóm (không phải lỗi): chưa đủ nhưng không cần dừng
    let first = true;
    for (const part of chunk(ids, 20)) {
      if (halted) break;
      if (!first) await this.#gap();
      first = false;
      let info;
      try { info = await api.getGroupInfo(part); } catch (err) {
        this.log.warn('zalo: getGroupInfo lỗi — dừng lượt quét', { error: errorCode(err) });
        halted = true;
        break;
      }
      for (const id of part) {
        const g = info?.gridInfoMap?.[id];
        if (!g) { missing = true; continue; }
        const memberIds = (Array.isArray(g.memberIds) && g.memberIds.length
          ? g.memberIds : (g.memVerList || []).map((s) => String(s).split('_')[0])).map(String);
        const profiles = {};
        for (const ids2 of chunk(memberIds, 100)) {
          if (halted) break;
          await this.#gap();
          try {
            const r = await api.getGroupMembersInfo(ids2);
            Object.assign(profiles, r?.profiles || {});
          } catch (err) { // tên thành viên không bắt buộc, nhưng nền tảng đã lỗi ⇒ không hỏi thêm ở lượt này
            this.log.warn('zalo: getGroupMembersInfo lỗi — dừng lượt quét', { error: errorCode(err) });
            halted = true;
          }
        }
        const name = g.name || '';
        this.groupNames.set(String(id), name);
        const count = Number(g.totalMember ?? memberIds.length) || memberIds.length;
        const admins = new Set([g.creatorId, ...(Array.isArray(g.adminIds) ? g.adminIds : [])].filter(Boolean).map(String));
        groups.push({
          external_id: String(id),
          name,
          member_count: count,
          // Đủ khi nhận được ít nhất bằng số thành viên nền tảng báo — lõi mới dám đánh dấu người vắng là đã rời.
          members_complete: memberIds.length > 0 && memberIds.length >= count,
          members: memberIds.map((uid) => {
            const pr = profiles[uid] || profiles[`${uid}_0`];
            const m = { external_id: uid, name: pr?.displayName || pr?.zaloName || '' };
            if (admins.size) m.role = admins.has(uid) ? 'admin' : 'member';
            return m;
          }),
        });
      }
    }
    this.lastSyncComplete = !halted && !missing;
    return groups;
  }

  /** Tin/reaction zca-js → payload gh.bridge.inbound `message` (chưa kiểm tập nghe). null = bỏ qua. */
  normalise(msg) {
    if (!msg || !msg.data) return null;
    const T = this.lib.ThreadType;
    const d = msg.data;
    const isReaction = msg.type === undefined && typeof msg.isGroup === 'boolean';
    const isGroup = isReaction ? msg.isGroup : msg.type === T.Group;
    const threadId = String(msg.threadId ?? '');
    let kind;
    let body = null;
    if (isReaction) {
      kind = 'reaction';
      body = d.content?.rIcon || null;
    } else {
      kind = KIND_BY_MSGTYPE[d.msgType] || (typeof d.content === 'string' && !d.msgType ? 'text' : 'other');
      const c = d.content;
      if (typeof c === 'string') {
        body = kind === 'text' ? c : null;
      } else if (c && typeof c === 'object') {
        if (kind === 'text') body = [c.title || c.description, c.href].filter(Boolean).join(' ') || null;
        else if (kind === 'image') body = c.title || c.description || null;
        else if (kind === 'file') body = c.title || null;
      }
    }
    const mentions = Array.isArray(d.mentions) ? d.mentions : [];
    const out = {
      channel: 'zalo',
      session_id: this.sessionId,
      external_msg_id: String(d.msgId ?? d.realMsgId ?? d.cliMsgId ?? ''),
      external_group_id: isGroup ? threadId : null,
      sender_external_id: String(d.uidFrom ?? ''),
      sender_name: d.dName || '',
      occurred_at: toIso(d.ts, 'ms', this.now),
      kind,
      body_text: body,
      direction: msg.isSelf ? 'outbound' : 'inbound',
      mentions_self: Boolean(this.ownId && mentions.some((m) => String(m?.uid) === this.ownId)),
      // Trả lời (trích dẫn) tin của chính mình: TQuote.ownerId = người viết tin được trích.
      replies_to_self: Boolean(this.ownId && d.quote && String(d.quote.ownerId ?? '') === this.ownId),
      payload: toPlain({ type: msg.type, threadId: msg.threadId, isSelf: msg.isSelf, isGroup: msg.isGroup, data: d }),
    };
    if (isGroup && this.groupNames.has(threadId)) {
      out.group_name = this.groupNames.get(threadId);
    }
    // zca-js 2.2.0 không có cờ «chuyển tiếp» ⇒ forwarded=false; thread 1-1 = threadId (UID người kia, cả khi mình gửi).
    // `read_ref`: đủ trường để đánh dấu «đã xem» tin 1-1 của người khác (chỉ RAM, không vào JSON). Nhóm / tin mình gửi ⇒ null.
    const readRef = isGroup || isReaction || msg.isSelf ? null : {
      msgId: d.msgId, cliMsgId: d.cliMsgId, uidFrom: d.uidFrom, idTo: d.idTo, msgType: d.msgType, st: d.st, at: d.at, cmd: d.cmd, ts: d.ts,
    };
    return withMeta(out, { thread_ids: isGroup ? [] : [threadId], sender_alt_id: null, forwarded: false, read_ref: readRef });
  }

  /** v0.1.73: đánh dấu «đã xem» một tin 1-1 (CHỈ kênh quản trị với Sếp — SessionManager quyết định gọi). */
  async markSeen(ref) {
    if (this.state !== 'active' || !this.api || !ref) return;
    await this.api.sendSeenEvent(ref, this.lib.ThreadType.User);
  }

  /** v0.1.73: «đang nhập…» trong thread 1-1. Zalo không có lệnh tắt — chỉ hiệu lực vài giây nên SessionManager gửi lại định kỳ. */
  async setTyping(threadId, on) {
    if (!on || this.state !== 'active' || !this.api) return;
    await this.api.sendTypingEvent(String(threadId), this.lib.ThreadType.User);
  }
}
