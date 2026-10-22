/** Presentation logic for MCP Hub — cùng khuôn `apiModel.ts`/`pluginsModel.ts`. */
import {
  ApiError,
  type HubLinkStatus,
  type HubLinkTestResult,
  type HubReadScopes,
  type HubWriteScopes,
  type McpArgsDigest,
  type McpCall,
  type McpCallOutcome,
  type McpTool,
  type McpToolAccess,
  type McpTransport,
} from '@gen-harness/contracts';
import { errorText } from '../../lib/errorText';

export const OK = 'var(--color-ok)';
export const WARN = 'var(--color-warn)';
export const BAD = 'var(--color-bad)';
export const ACC3 = 'var(--color-accent-300)';
export const N5 = 'var(--color-neutral-500)';

export const TRANSPORT_LABEL: Record<McpTransport, string> = {
  stdio: 'stdio',
  'http+sse': 'HTTP + SSE',
  streamable_http: 'Streamable HTTP',
};

export function healthLabel(h: string): string {
  if (h === 'healthy') return 'Đang chạy';
  if (h === 'blocked') return 'Bị chặn mạng';
  if (h === 'error') return 'Lỗi kết nối';
  return 'Chưa rõ';
}

export function healthTone(h: string): string {
  if (h === 'healthy') return OK;
  if (h === 'error' || h === 'blocked') return BAD;
  return N5;
}

export const ACCESS_LABEL: Record<McpTool['access'], string> = {
  read: 'Chỉ đọc',
  write: 'Có ghi — cần Sếp duyệt',
};

export const OUTCOME_LABEL: Record<McpCallOutcome, string> = {
  ok: 'OK',
  held_for_approval: 'Chờ duyệt',
  blocked: 'Bị chặn',
  error: 'Lỗi',
};

export function outcomeTone(o: McpCallOutcome): string {
  if (o === 'ok') return OK;
  if (o === 'held_for_approval') return WARN;
  return BAD;
}

export function fmtLatency(ms: number | null): string {
  return ms == null ? '—' : ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

/**
 * `errorText()` dùng chung đổi MỌI lỗi 403 thành "Vai trò của bạn không có quyền làm thao tác này" — đúng cho
 * hầu hết màn (thiếu quyền RBAC) nhưng SAI cho lượt gọi tool MCP bị chặn (khoá cứng #4 cũng trả 403, kèm lý do
 * cụ thể ở `detail`, vd "Bị chặn: tool chưa được Owner mở" — đây là thông tin nghiệp vụ phải hiện đúng, không
 * phải lỗi phân quyền). Mã lỗi `MCP_*` giữ nguyên message gốc; còn lại rơi về `errorText()` như thường.
 */
export function mcpErrorText(e: unknown): string {
  if (e instanceof ApiError && e.code.startsWith('MCP_')) return e.message;
  return errorText(e);
}

/** Thẻ Gen-hub (v0.1.26) — trạng thái liên kết. */
export const HUB_STATUS_LABEL: Record<HubLinkStatus, string> = {
  off: 'Đang tắt',
  ok: 'Đang nối',
  expiring: 'Token sắp hết hạn',
  expired: 'Token hết hạn',
  error: 'Lỗi kết nối',
};

export function hubStatusTone(s: HubLinkStatus): string {
  if (s === 'ok') return OK;
  if (s === 'expiring') return WARN;
  if (s === 'expired' || s === 'error') return BAD;
  return N5;
}

/**
 * v0.1.49 (QD-16): quyền ĐỌC thêm (tuỳ chọn) của token Gen-hub. `label` là tên dòng trên thẻ, `short` là nhãn trong câu
 * "Còn thiếu quyền: …" (cùng nhãn máy chủ trả ở `read_missing`). Gen chỉ đọc — không có quyền ghi nào được dùng.
 */
export const READ_SCOPE_KEYS = ['calendar', 'mail', 'tasks', 'drive'] as const;
export const READ_SCOPE_META: Record<keyof HubReadScopes, { label: string; short: string }> = {
  calendar: { label: 'Đọc lịch', short: 'đọc lịch' },
  mail: { label: 'Đọc mail', short: 'đọc mail' },
  tasks: { label: 'Đọc việc (Google Tasks)', short: 'đọc việc (Google Tasks)' },
  drive: { label: 'Tìm tệp Drive', short: 'tìm tệp Drive' },
};

export interface ScopeRow {
  key: keyof HubReadScopes;
  label: string;
  /** `yes` = Có · `no` = Chưa · `unknown` = Chưa kiểm (không có dữ liệu hoặc giá trị không phải boolean). */
  state: 'yes' | 'no' | 'unknown';
  text: 'Có' | 'Chưa' | 'Chưa kiểm';
}

/** 4 dòng quyền đọc thêm từ `read_scopes`; giá trị nào không phải boolean (hoặc không có dữ liệu) ⇒ "Chưa kiểm". */
export function scopeRows(scopes: Partial<Record<keyof HubReadScopes, unknown>> | null | undefined): ScopeRow[] {
  const src = scopes && typeof scopes === 'object' ? scopes : {};
  return READ_SCOPE_KEYS.map((key) => {
    const v = src[key];
    const state = v === true ? 'yes' : v === false ? 'no' : 'unknown';
    return { key, label: READ_SCOPE_META[key].label, state, text: state === 'yes' ? 'Có' : state === 'no' ? 'Chưa' : 'Chưa kiểm' };
  });
}

/** Cả 4 quyền đều có giá trị boolean (đã kiểm). */
export function scopesKnown(scopes: Partial<Record<keyof HubReadScopes, unknown>> | null | undefined): boolean {
  return scopeRows(scopes).every((r) => r.state !== 'unknown');
}

/** Nhãn các quyền còn thiếu (đã biết là `false`) theo `read_scopes` — dùng khi máy chủ chưa gửi `read_missing`. */
export function missingFromScopes(scopes: Partial<Record<keyof HubReadScopes, unknown>> | null | undefined): string[] {
  return scopeRows(scopes)
    .filter((r) => r.state === 'no')
    .map((r) => READ_SCOPE_META[r.key].short);
}

/** Câu cho Sếp khi token còn thiếu quyền đọc; danh sách rỗng (hoặc không có chuỗi nào) ⇒ null. Chỉ nhận chuỗi. */
export function missingText(list: unknown): string | null {
  const items = Array.isArray(list) ? list.filter((x): x is string => typeof x === 'string' && x.trim() !== '').map((x) => x.trim()) : [];
  if (items.length === 0) return null;
  return `Còn thiếu quyền: ${items.join(', ')} — vào Gen-hub tick thêm cho token của Gen-Harness rồi bấm Kiểm tra lại.`;
}

export const SCOPES_ENOUGH_TEXT = 'Đủ quyền đọc lịch, mail, việc và Drive.';
export const SCOPES_READ_ONLY_TEXT = 'Gen chỉ đọc — không gửi mail, không tạo lịch hay tệp.';
export const HUB_BREAKER_STRIP = 'Gen-hub tạm không trả lời — Gen tự thử lại sau 1 phút.';

/** Câu về quyền sau một lần Kiểm tra: thiếu ⇒ cảnh báo, đủ ⇒ câu đủ quyền, chưa có dữ liệu ⇒ null. */
export function scopesMessage(readMissing: unknown, scopes: Partial<Record<keyof HubReadScopes, unknown>> | null | undefined): { tone: 'warn' | 'ok'; text: string } | null {
  const fromServer = missingText(readMissing);
  if (fromServer) return { tone: 'warn', text: fromServer };
  if (Array.isArray(readMissing) && readMissing.length === 0) return { tone: 'ok', text: SCOPES_ENOUGH_TEXT };
  const fromScopes = missingText(missingFromScopes(scopes));
  if (fromScopes) return { tone: 'warn', text: fromScopes };
  return scopesKnown(scopes) ? { tone: 'ok', text: SCOPES_ENOUGH_TEXT } : null;
}

/**
 * v0.1.50 (F-81, QD-18): quyền GHI Kho (tuỳ chọn) của token Gen-hub, THEO TỪNG tool — máy chủ báo `write_scopes.kho_create`,
 * `write_scopes.kho_update` (và `kho` = có cả hai); máy chủ cũ chỉ có `kho` ⇒ hai dòng theo `kho`. Gen KHÔNG tự ghi: chỉ ghi khi
 * Sếp bấm Xác nhận + nhập mã PIN trên thẻ đề xuất "Ghi vào Kho dữ liệu". Thiếu quyền ghi không làm Kiểm tra đỏ.
 */
export const WRITE_SCOPE_ROWS: ReadonlyArray<{ key: 'kho_create' | 'kho_update'; label: string }> = [
  { key: 'kho_create', label: 'Tạo bản ghi Phiên/Việc' },
  { key: 'kho_update', label: 'Sửa bản ghi Phiên/Việc' },
];

export interface WriteScopeRow {
  key: 'kho_create' | 'kho_update';
  label: string;
  /** `yes` = Có · `no` = Chưa · `unknown` = Chưa kiểm (không có dữ liệu hoặc giá trị không phải boolean). */
  state: 'yes' | 'no' | 'unknown';
  text: 'Có' | 'Chưa' | 'Chưa kiểm';
}

type WriteScopesIn = Partial<Record<keyof HubWriteScopes, unknown>> | null | undefined;

/** Có quyền của MỘT tool: cờ riêng của tool (boolean) nếu máy chủ gửi, không thì cờ chung `kho` (máy chủ cũ); khác ⇒ undefined. */
function writeScopeOf(scopes: WriteScopesIn, key: 'kho_create' | 'kho_update'): boolean | undefined {
  if (!scopes || typeof scopes !== 'object') return undefined;
  const own = scopes[key];
  if (typeof own === 'boolean') return own;
  return typeof scopes.kho === 'boolean' ? scopes.kho : undefined;
}

export function writeScopeRows(scopes: WriteScopesIn): WriteScopeRow[] {
  return WRITE_SCOPE_ROWS.map((r) => {
    const v = writeScopeOf(scopes, r.key);
    const state = v === true ? 'yes' : v === false ? 'no' : 'unknown';
    return { ...r, state, text: state === 'yes' ? 'Có' : state === 'no' ? 'Chưa' : 'Chưa kiểm' };
  });
}

export const WRITE_MISSING_TEXT = 'Vào Gen-hub tick quyền ghi Kho (tạo và sửa bản ghi — tên trong Gen-hub: kho_create, kho_update) cho token của Gen-Harness rồi bấm Kiểm tra.';
export const WRITE_ENOUGH_TEXT = 'Đủ quyền ghi Kho (Phiên, Việc).';
export const WRITE_CONFIRM_ONLY_TEXT = 'Gen chỉ ghi khi Sếp bấm Xác nhận + nhập mã PIN trên thẻ đề xuất.';
/** Câu khi Owner TỰ đóng tool ghi Kho ở MCP Hub — Kiểm tra không mở lại; tắt hẳn quyền ghi thì bỏ tick ở Gen-hub. */
export const WRITE_HIDDEN_TEXT = (tools: string[]) =>
  `Sếp đã tự đóng ${tools.join(', ')} ở MCP Hub — Kiểm tra không mở lại. Muốn Gen ghi lại thì mở ở MCP Hub; muốn tắt hẳn thì bỏ tick ở Gen-hub.`;

/** Hậu tố tool ghi Kho Owner tự đóng ở MCP Hub (`write_hidden` của máy chủ) — chỉ giữ 'kho_create' / 'kho_update'. */
export function writeHidden(list: unknown): string[] {
  if (!Array.isArray(list)) return [];
  return list.filter((t): t is string => t === 'kho_create' || t === 'kho_update');
}

/**
 * Câu về quyền ghi Kho sau một lần Kiểm tra: thiếu ở Gen-hub (máy chủ báo `write_missing` không rỗng, hoặc có tool `false` mà
 * không phải do Owner tự đóng) ⇒ hướng dẫn tick quyền; Owner tự đóng ở MCP Hub ⇒ câu riêng; đủ ⇒ câu đủ quyền; chưa có dữ liệu ⇒
 * null. Chỉ nhận chuỗi / boolean.
 */
export function writeScopesMessage(writeMissing: unknown, scopes: WriteScopesIn, hidden: unknown = undefined): { tone: 'warn' | 'ok'; text: string } | null {
  const listed = Array.isArray(writeMissing) ? writeMissing.filter((x): x is string => typeof x === 'string' && x.trim() !== '') : [];
  if (listed.length > 0) return { tone: 'warn', text: WRITE_MISSING_TEXT };
  const off = writeHidden(hidden);
  const rows = writeScopeRows(scopes);
  if (rows.some((r) => r.state === 'no' && !off.includes(r.key))) return { tone: 'warn', text: WRITE_MISSING_TEXT };
  if (off.length > 0) return { tone: 'warn', text: WRITE_HIDDEN_TEXT(off) };
  if (rows.every((r) => r.state === 'yes')) return { tone: 'ok', text: WRITE_ENOUGH_TEXT };
  return null;
}

/** Dòng phụ của thẻ Gen-hub: Gen đọc; ghi Kho chỉ khi Sếp xác nhận + mã PIN (cùng lời với thẻ Trợ giúp). */
export const HUB_KICKER = 'Gen đọc; ghi Kho khi Sếp xác nhận + mã PIN · chỉ Sếp · tắt tới khi Kiểm tra xanh';

/** Tool ghi Kho trong một danh sách tên tool (theo hậu tố, như máy chủ). */
const isKhoWriteTool = (t: string): boolean => /(^|[_.:/-])kho_(create|update)$/.test(t);

/**
 * "mở 5 tool đọc + 2 tool ghi Kho cho Gen" sau Kiểm tra xanh: đếm RIÊNG tool đọc và tool ghi. Máy chủ gửi `exposed_write_tools`;
 * máy chủ cũ không gửi ⇒ tách theo tên (kho_create / kho_update). Không có tool ghi ⇒ "mở 5 tool đọc cho Gen".
 */
export function testedToolsText(result: Pick<HubLinkTestResult, 'exposed_tools' | 'exposed_write_tools'>): string {
  const all = Array.isArray(result.exposed_tools) ? result.exposed_tools.filter((t): t is string => typeof t === 'string') : [];
  const fromServer = Array.isArray(result.exposed_write_tools) ? result.exposed_write_tools.filter((t): t is string => typeof t === 'string') : null;
  const writes = new Set(fromServer ?? all.filter(isKhoWriteTool));
  const reads = all.filter((t) => !writes.has(t)).length;
  return writes.size > 0 ? `mở ${reads} tool đọc + ${writes.size} tool ghi Kho cho Gen` : `mở ${reads} tool đọc cho Gen`;
}

/** Quyền GHI khác mà token đang có (ngoài ghi Kho, vốn là tuỳ chọn có chủ đích) — Gen không dùng, nên tắt. Chỉ giữ chuỗi. */
export function otherWriteTools(list: unknown): string[] {
  if (!Array.isArray(list)) return [];
  return list.filter((t): t is string => typeof t === 'string' && t !== '' && !isKhoWriteTool(t));
}

/** `YYYY-MM-DD` (ô ngày) → ISO cuối ngày giờ VN; rỗng → null. */
export function expiryToIso(day: string): string | null {
  return day ? `${day}T23:59:00+07:00` : null;
}

export function isoToDay(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : new Date(d.getTime() + 7 * 3600_000).toISOString().slice(0, 10);
}

/** v0.1.39 (F-31): câu cố định khi máy chủ chặn Gen-hub ở mạng công cộng (`MCP_NETWORK_BLOCKED`). */
export const PUBLIC_NET_HINT = "Bật 'Cho phép Gen-hub ở mạng công cộng' ngay trong thẻ này.";

const PRIVATE_SUFFIXES = ['.localhost', '.local', '.lan', '.internal', '.home.arpa'];

function privateIpv4(host: string): boolean | null {
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host);
  if (!m) return null;
  const a = Number(m[1]);
  const b = Number(m[2]);
  if (host === '0.0.0.0') return true;
  return a === 10 || a === 127 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 169 && b === 254);
}

function privateIpv6(host: string): boolean | null {
  if (!host.includes(':')) return null;
  if (host === '::1' || host === '::') return true;
  // ::ffff:a.b.c.d (URL chuẩn hoá thành ::ffff:xxxx:yyyy) — xét như IPv4, khớp `_unmap` của máy chủ.
  const mapped = /^::ffff:([0-9a-f]{1,4}):([0-9a-f]{1,4})$/.exec(host);
  if (mapped) {
    const hi = parseInt(mapped[1], 16);
    const lo = parseInt(mapped[2], 16);
    return privateIpv4(`${hi >> 8}.${hi & 255}.${lo >> 8}.${lo & 255}`) ?? false;
  }
  const first = parseInt(host.split(':')[0] || '0', 16);
  // fc00::/7 (ULA), fe80::/10 (link-local).
  return (first & 0xfe00) === 0xfc00 || (first & 0xffc0) === 0xfe80;
}

/**
 * v0.1.39 (F-31): địa chỉ là https VÀ trỏ ra Internet (không phải localhost/.local/.lan/.internal/.home.arpa, tên
 * một nhãn, IP riêng/loopback/link-local) → thẻ Gen-hub bật sẵn "Cho phép Gen-hub ở mạng công cộng". 100.64/10 coi
 * là công khai như `_is_public` của máy chủ.
 */
export function isPublicHttpsUrl(url: string): boolean {
  let u: URL;
  try {
    u = new URL(url.trim());
  } catch {
    return false;
  }
  if (u.protocol !== 'https:') return false;
  const host = u.hostname.replace(/^\[|\]$/g, '').replace(/\.$/, '').toLowerCase();
  if (!host) return false;
  const v4 = privateIpv4(host);
  if (v4 !== null) return !v4;
  const v6 = privateIpv6(host);
  if (v6 !== null) return !v6;
  if (host === 'localhost' || !host.includes('.')) return false;
  return !PRIVATE_SUFFIXES.some((s) => host.endsWith(s));
}

/** v0.1.45 (F-20): đổi tool ghi → đọc là bỏ qua duyệt (tool đọc chạy ngay) ⇒ máy chủ đòi mã PIN (423 → hộp PIN). */
export const ACCESS_PIN_HINT = 'Chuyển tool ghi sang đọc cần mã PIN (tool đọc chạy không qua duyệt)';

/** Có hiện gợi ý PIN khi đổi loại tool không: chỉ ghi → đọc; đọc → ghi (chặt hơn) và giữ nguyên thì không. */
export function accessChangeNeedsPin(from: McpToolAccess, to: McpToolAccess): boolean {
  return from === 'write' && to === 'read';
}

export function isArgsDigest(args: McpCall['args']): args is McpArgsDigest {
  const a = args as Partial<McpArgsDigest>;
  return typeof a.sha256 === 'string' && Array.isArray(a.keys) && typeof a.bytes === 'number';
}

/** v0.1.45 (F-57): nhật ký MCP chỉ lưu dấu vết tham số {sha256, keys, bytes} — tóm tắt ngắn để hiển thị. */
export function describeCallArgs(args: McpCall['args']): string {
  if (isArgsDigest(args)) {
    const keys = args.keys.length ? args.keys.join(', ') : 'không có tham số';
    return `${keys} · ${args.bytes} byte · ${args.sha256.slice(0, 8)}`;
  }
  const keys = Object.keys(args);
  return keys.length ? keys.join(', ') : 'không có tham số';
}
