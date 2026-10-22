/**
 * v0.1.39 (F-74) — "Việc Sếp cần làm" (`/guide/viec-sep`): nhãn, câu lỗi thân thiện theo mã lỗi thống nhất, định dạng
 * ô kết quả. Không chứa bí mật: kết quả máy chủ trả chỉ có mã lỗi + câu đã lọc.
 */
import type { BossCheck, BossCheckKey, BossOverview } from '@gen-harness/contracts';
import { friendlyError } from '../lib/friendlyError';
import { DEFAULT_TZ, fmtDM, fmtHM } from '../lib/format';
import { TELEGRAM_ERROR_TEXT } from '../screens/connections/telegramModel';

/** Khoá cache của `GET /boss-checks`. */
export const BOSS_CHECKS_KEY = ['boss-checks'] as const;

/** Đường dẫn trang con của Hướng dẫn thiết lập. */
export const BOSS_CHECKS_PATH = '/guide/viec-sep';

/**
 * v0.1.54 (Gen hướng dẫn): mục tiêu `data-gen-target` của các dòng kết nối "Việc Sếp cần làm" — khoá = `key` của dòng (khớp `ROWS`
 * trong apps/api/gh/boss_checks/service.py; vitest `coach-v0154` so khớp hai bên), `row` = số dòng. Các literal
 * `genTarget: '…'` ĐỂ NGUYÊN dạng này: bộ quét tĩnh của gen-targets.test.ts đọc chúng để biết id nào đã được gắn vào màn hình.
 * v0.1.55: dòng 0 `ai` (nguồn AI, bắt buộc duy nhất).
 */
export const BOSS_ROW_TARGETS = {
  ai: { row: 0, genTarget: 'boss_checks.row.ai' },
  hub: { row: 1, genTarget: 'boss_checks.row.hub' },
  facebook: { row: 2, genTarget: 'boss_checks.row.facebook' },
  agy: { row: 3, genTarget: 'boss_checks.row.agy' },
  claude: { row: 4, genTarget: 'boss_checks.row.claude' },
  jev: { row: 5, genTarget: 'boss_checks.row.jev' },
  telegram: { row: 6, genTarget: 'boss_checks.row.telegram' },
  remote: { row: 7, genTarget: 'boss_checks.row.remote' },
  facebook_reply: { row: 8, genTarget: 'boss_checks.row.facebook_reply' },
  kho_write: { row: 9, genTarget: 'boss_checks.row.kho_write' },
} as const;

/** Số dòng (0..9) → id `data-gen-target` của dòng đó; số lạ ⇒ undefined (không gắn). */
export function bossRowTarget(n: number): string | undefined {
  return Object.values(BOSS_ROW_TARGETS).find((r) => r.row === n)?.genTarget;
}

/** Thăm lại mỗi 3 giây khi có kết quả đang chạy (vd đọc Facebook chạy nền). */
export const BOSS_CHECKS_POLL_MS = 3000;

/** Câu cho Sếp theo mã lỗi (khớp mã máy chủ). Mã lạ → câu máy chủ qua `friendlyError`. */
export const BOSS_ERROR_TEXT: Record<string, string> = {
  HUB_LINK_NOT_CONFIGURED: 'Chưa nhập địa chỉ và token Gen-hub — điền rồi bấm Kiểm tra.',
  HUB_ENDPOINT_FORBIDDEN: 'Địa chỉ Gen-hub này không được phép gọi — kiểm tra lại địa chỉ.',
  HUB_ENDPOINT_INVALID: 'Địa chỉ Gen-hub không hợp lệ — kiểm tra lại (dạng https://<máy-chủ>/mcp)',
  MCP_NETWORK_BLOCKED: "Bật 'Cho phép Gen-hub ở mạng công cộng' ngay trong thẻ này.",
  HUB_TOKEN_REJECTED: 'Gen-hub từ chối token — token sai, hết hạn hoặc đã bị thu hồi. Tạo token mới rồi dán lại.',
  HUB_RATE_LIMITED: 'Gen-hub đang giới hạn số lần gọi — đợi vài phút rồi kiểm tra lại.',
  HUB_UNREACHABLE: 'Không gọi được Gen-hub — kiểm tra địa chỉ và mạng của máy chủ.',
  HUB_TOOLS_MISSING: 'Token chưa được bật đủ quyền đọc Kho — bật quyền tóm tắt, tìm, xem một mục rồi kiểm tra lại.',
  HUB_ERROR: 'Gen-hub báo lỗi — thử lại sau ít phút.',
  SOCIAL_NO_ACCOUNT: 'Chưa có tài khoản Facebook — thêm và đăng nhập ở trang Tài khoản mạng xã hội.',
  SOCIAL_NOT_ACTIVE: 'Tài khoản Facebook chưa đăng nhập — bấm Đăng nhập ở trang Tài khoản mạng xã hội.',
  SOCIAL_NO_SESSION: 'Phiên đăng nhập Facebook không còn — đăng nhập lại ở trang Tài khoản mạng xã hội.',
  SOCIAL_NEEDS_LOGIN: 'Phiên Facebook không mở được trên máy này — bấm Đăng nhập lại ở trang Tài khoản mạng xã hội.',
  SOCIAL_BUSY: 'Tài khoản Facebook đang chạy một việc khác — đợi việc đó xong rồi bấm Đọc ngay.',
  SOCIAL_HALTED: 'Đang dừng tất cả việc trình duyệt — bấm Bật lại ở trang Tài khoản mạng xã hội trước.',
  SOCIAL_RATE_LIMIT: 'Đã đọc đủ số lần cho phép — đợi một lúc rồi bấm Đọc ngay lại.',
  SOCIAL_READ_FAILED: 'Đọc Facebook không thành công — mở trang Tài khoản mạng xã hội xem lý do.',
  SOCIAL_READ_HALTED: 'Đọc mạng xã hội đang bị dừng (Dừng tất cả) — bật lại ở trang Tài khoản mạng xã hội.',
  SOCIAL_READ_CANCELLED: 'Lượt đọc Facebook đã bị huỷ — bấm Đọc ngay lần nữa.',
  WORKER_TIMEOUT: 'Lượt đọc Facebook chạy quá lâu nên đã dừng — mở trang Tài khoản mạng xã hội xem rồi bấm Đọc ngay lần nữa.',
  SOCIAL_JOB_MISSING: 'Không thấy lượt đọc vừa chạy — bấm Đọc ngay lại.',
  // v0.1.55: dòng 0 "nguồn AI" (ai_source).
  AI_NO_SOURCE: 'Chưa có nguồn AI nào — thêm khoá API hoặc đăng nhập Google / Claude Code ở màn Thiết lập nguồn AI rồi bấm Kiểm tra.',
  AI_KEY_MISSING: 'Nguồn AI đầu chuỗi chưa có khoá API — thêm khoá ở màn Thiết lập nguồn AI rồi bấm Kiểm tra.',
  AGY_NOT_LOGGED_IN: 'Chưa đăng nhập Google cho Antigravity — bấm Đăng nhập Google.',
  AGY_ACCOUNT_MISMATCH: 'Gọi thử vẫn chạy bằng tài khoản khác với tài khoản vừa chọn — bấm Đăng nhập lại và đăng nhập đúng tài khoản đó.',
  CLI_PROFILE_NO_SESSION: 'Tài khoản này chưa có phiên đăng nhập đã lưu — bấm Đăng nhập lại tài khoản đó.',
  CLAUDE_NOT_LOGGED_IN: 'Chưa đăng nhập Claude Code — bấm Đăng nhập Claude Code.',
  CLI_LOGIN_IN_PROGRESS: 'Đang có một lượt đăng nhập dở — hoàn tất hoặc huỷ lượt đó trước.',
  CLI_MISSING: 'Máy chủ chưa cài công cụ dòng lệnh này — chạy lại trình cài genh.',
  CLI_LOGIN_TIMEOUT: 'Đăng nhập quá lâu nên đã hết hạn — bấm đăng nhập lại.',
  CLI_LOGIN_FAILED: 'Đăng nhập không thành công — mã xác thực sai hoặc đã hết hạn, thử lại.',
  AUTH_EXPIRED: 'Phiên đăng nhập đã hết hạn — đăng nhập lại.',
  MODEL_REJECTED: 'Model đang chọn không dùng được với tài khoản này — chọn model khác ở API & Model.',
  TIMEOUT: 'Trả lời quá chậm — thử lại sau ít phút.',
  PROVIDER_ERROR: 'Nguồn AI báo lỗi — thử lại sau ít phút.',
  PROBE_RATE_LIMITED: 'Vừa gọi thử quá nhiều lần — đợi một phút rồi thử lại.',
  JEV_NOT_CONFIGURED: 'Chưa nhập khoá Jev.',
  JEV_ERROR: 'Thẻ Jev sẽ ẩn, không cần làm thêm.',
  // v0.1.44 (F-8c): dòng 6 Telegram — cùng câu với thẻ Kết nối › Telegram.
  ...TELEGRAM_ERROR_TEXT,
  // v0.1.46 (F-21): dòng 7 Truy cập từ xa.
  REMOTE_NOT_CONFIGURED: 'Chưa chọn cách truy cập từ xa — trên máy chủ chạy genh remote tailscale (khuyên dùng) hoặc genh remote --lan.',
  REMOTE_OPENED_ON_SERVER:
    'Đang mở trên chính máy chủ — mở Console trên điện thoại bằng địa chỉ ở Cài đặt › Sao lưu & cập nhật › Truy cập từ xa rồi bấm Kiểm tra từ đó.',
};

/**
 * v0.1.49 (QD-16): dòng phụ của hàng Gen-hub — quyền ĐỌC thêm (không bắt buộc) lấy từ `results.hub.detail.read_scopes`.
 * Máy chủ cũ / bản ghi cũ không có `read_scopes`, hoặc có giá trị không phải boolean ⇒ `null` (ẩn dòng, không đoán).
 */
export interface HubScopeItem {
  key: 'calendar' | 'mail' | 'tasks' | 'drive';
  label: 'Lịch' | 'Mail' | 'Việc' | 'Drive';
  has: boolean;
}

const HUB_SCOPE_ITEMS: ReadonlyArray<Pick<HubScopeItem, 'key' | 'label'>> = [
  { key: 'calendar', label: 'Lịch' },
  { key: 'mail', label: 'Mail' },
  { key: 'tasks', label: 'Việc' },
  { key: 'drive', label: 'Drive' },
];

export function hubScopesOf(c: Pick<BossCheck, 'detail'> | null | undefined): HubScopeItem[] | null {
  const raw = c?.detail?.read_scopes;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const src = raw as Record<string, unknown>;
  const out: HubScopeItem[] = [];
  for (const it of HUB_SCOPE_ITEMS) {
    const v = src[it.key];
    if (typeof v !== 'boolean') return null;
    out.push({ ...it, has: v });
  }
  return out;
}

/** "Quyền đọc thêm (không bắt buộc): Lịch ✓ · Mail ✗ · Việc ✓ · Drive ✗". */
export function hubScopesLine(items: readonly HubScopeItem[]): string {
  return `Quyền đọc thêm (không bắt buộc): ${items.map((i) => `${i.label} ${i.has ? '✓' : '✗'}`).join(' · ')}`;
}

/**
 * Có ✗ thì hướng dẫn tick thêm quyền ở Gen-hub (không bắt buộc: không tick Gen vẫn chạy); đủ ⇒ null. Hạ chữ thường
 * "Lịch/Mail/Việc" trong câu, giữ nguyên "Drive" (tên riêng).
 */
export function hubScopesHint(items: readonly HubScopeItem[]): string | null {
  const missing = items.filter((i) => !i.has).map((i) => (i.key === 'drive' ? i.label : i.label.toLowerCase()));
  if (missing.length === 0) return null;
  return `Muốn Gen đọc cả ${missing.join(', ')}: vào Gen-hub tick thêm quyền đọc cho token của Gen-Harness rồi bấm Kiểm tra lại. Không tick cũng được — Gen vẫn chạy bình thường.`;
}

/**
 * v0.1.50 (F-81, QD-18): dòng phụ của hàng Gen-hub — quyền GHI Kho (không bắt buộc) lấy từ `results.hub.detail.write_scopes`
 * — MỘT dòng theo cờ chung `kho` (có CẢ kho_create và kho_update; từng tool xem ở Kết nối › Gen-hub). Vắng / không phải boolean
 * (máy chủ cũ, bản ghi cũ) ⇒ `null` (ẩn dòng, không đoán).
 */
export function hubWriteScopeOf(c: Pick<BossCheck, 'detail'> | null | undefined): boolean | null {
  const raw = c?.detail?.write_scopes;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const v = (raw as Record<string, unknown>).kho;
  return typeof v === 'boolean' ? v : null;
}

/** "Quyền ghi Kho (không bắt buộc): Có" / "…: Chưa". */
export function hubWriteLine(has: boolean): string {
  return `Quyền ghi Kho (không bắt buộc): ${has ? 'Có' : 'Chưa'}`;
}

/** Chưa có quyền ghi Kho ⇒ hướng dẫn tick (không bắt buộc: không tick Gen vẫn đọc Kho bình thường); đã có ⇒ null. */
export function hubWriteHint(has: boolean): string | null {
  if (has) return null;
  return 'Muốn Gen ghi được Kho: vào Gen-hub tick quyền ghi Kho (tạo và sửa bản ghi — tên trong Gen-hub: kho_create, kho_update) cho token của Gen-Harness rồi bấm Kiểm tra lại. Không tick cũng được — Gen vẫn đọc Kho bình thường.';
}

/** Dòng 9 "Gen ghi Kho" (không bắt buộc, không có nút kiểm): 2 bước để máy chủ ghi 'pass' sau lần ghi Kho thật đầu tiên. */
export const KHO_WRITE_STEPS: readonly string[] = [
  'Trong Gen-hub, tick quyền ghi Kho (tạo và sửa bản ghi — tên trong Gen-hub: kho_create, kho_update) cho token của Gen-Harness rồi bấm Kiểm tra ở dòng 1 (hoặc Kết nối › Gen-hub)',
  'Duyệt đề xuất ghi Phiên đầu tiên của Gen (Gen đề xuất ghi Phiên mỗi bản phát hành): bấm Xác nhận và ghi Kho, nhập mã PIN, đợi câu “Đã ghi vào Kho” kèm mã của Phiên vừa tạo',
];

/** Dòng 8 (không bắt buộc): các bước thử gửi một câu trả lời bình luận thật bằng Gen. */
export const FACEBOOK_REPLY_STEPS: readonly string[] = [
  'Hỏi Gen: “đọc Facebook”',
  'Hỏi Gen: “trả lời bình luận của <tên> trên bài của tôi: …”',
  'Đọc kỹ thẻ, bấm Xác nhận và gửi, nhập mã PIN',
  'Đợi “Đã gửi”, bấm Xem ảnh chụp, mở Facebook xem lại',
];

/** Lỗi chỉ sửa được bằng ĐĂNG NHẬP LẠI (đổi lại hay gọi thử lại chỉ lặp lại lỗi) → dòng hiện nút "Đăng nhập lại". */
export const RELOGIN_CODES: ReadonlySet<string> = new Set(['AUTH_EXPIRED', 'CLI_PROFILE_NO_SESSION', 'AGY_ACCOUNT_MISMATCH']);

/** Lỗi Gen-hub do ĐỊA CHỈ/mạng → chỉ lối sửa địa chỉ ở Kết nối MCP (/mcp). */
export const HUB_ADDRESS_CODES: ReadonlySet<string> = new Set(['HUB_ENDPOINT_FORBIDDEN', 'HUB_ENDPOINT_INVALID', 'HUB_UNREACHABLE', 'MCP_NETWORK_BLOCKED']);

/** Kết quả lỗi cần đăng nhập lại (theo mã lỗi thống nhất). */
export function needsRelogin(c: Pick<BossCheck, 'status' | 'error_code'> | null | undefined): boolean {
  return !!c && c.status === 'fail' && !!c.error_code && RELOGIN_CODES.has(c.error_code);
}

/**
 * v0.1.55: câu "Đạt" của dòng 0 (nguồn AI) theo cách máy chủ chứng minh: lượt gọi model thật (`via = model_calls`), Claude /
 * Google đã gọi thử đạt (`claude_call` / `agy_call`), hoặc bấm Kiểm tra (không có `via`). Luôn là chuỗi.
 */
export function aiSourceOkText(c: Pick<BossCheck, 'detail' | 'checked_at'>, tz: string = DEFAULT_TZ): string {
  const via = c.detail?.via;
  const when = fmtCheckedAt(c.checked_at, tz);
  if (via === 'model_calls') return `Đạt · Gen đã gọi model thật thành công (gần nhất ${when})`;
  if (via === 'claude_call') return `Đạt · Claude Code đã gọi thử thành công (${when})`;
  if (via === 'agy_call') return `Đạt · Google (Antigravity) đã gọi thử thành công (${when})`;
  return `Đạt · ${when}`;
}

/** Token Gen-hub trang yêu cầu tạo là 90 ngày → hạn gửi kèm khi lưu token (nhắc trước 14 ngày chạy được). */
export const HUB_TOKEN_DAYS = 90;
export function hubTokenExpiry(now: number = Date.now()): string {
  return new Date(now + HUB_TOKEN_DAYS * 86_400_000).toISOString();
}

/** Câu thân thiện cho một kết quả lỗi — luôn là chuỗi. */
export function bossErrorText(c: Pick<BossCheck, 'error_code' | 'message'>): string {
  if (c.error_code && BOSS_ERROR_TEXT[c.error_code]) return BOSS_ERROR_TEXT[c.error_code];
  return friendlyError(c.message, 'Chưa đạt — thử lại sau ít phút.').message;
}

/** "14:05 02/10" theo múi giờ tổ chức. */
export function fmtCheckedAt(iso: string, tz: string = DEFAULT_TZ): string {
  return `${fmtHM(iso, tz)} ${fmtDM(iso, tz)}`;
}

/** Kết quả của một mã kiểm (null = chưa kiểm). */
export function resultOf(o: BossOverview | undefined, key: BossCheckKey): BossCheck | null {
  return o?.results?.[key] ?? null;
}

/**
 * Tài khoản đang dùng của một kết quả: email đầy đủ khi vừa chạy (phản hồi `run`), sau khi tải lại thì máy chủ chỉ
 * còn dạng che trong `detail.account_masked` (CSDL không lưu email đầy đủ). Luôn là chuỗi hoặc null.
 */
export function accountOf(c: Pick<BossCheck, 'key' | 'checked_at' | 'account' | 'detail'>, fresh?: BossCheck | null): string | null {
  if (typeof c.account === 'string' && c.account) return c.account;
  // Bản tổng quan tải lại ngay sau lượt chạy chỉ có email đã che → giữ email đầy đủ của CHÍNH lượt vừa bấm (cùng mục,
  // cùng giờ kiểm) tới khi tải lại trang, để ô không "nháy" từ email đầy đủ sang dạng che.
  if (fresh && fresh.key === c.key && fresh.checked_at === c.checked_at && typeof fresh.account === 'string' && fresh.account) return fresh.account;
  const m = c.detail?.account_masked;
  return typeof m === 'string' && m ? m : null;
}

/** Lượt đọc Facebook treo quá lâu (khớp `social.service.STALE_AFTER` = 15 phút): máy chủ đóng việc ở lần tải kế. */
export const SOCIAL_STALE_MS = 15 * 60_000;

/** Kết quả đang chạy đã quá `SOCIAL_STALE_MS` → web mở lại nút và chỉ sang trang Tài khoản mạng xã hội. */
export function isStalePending(c: Pick<BossCheck, 'status' | 'checked_at'> | null | undefined, now: number = Date.now()): boolean {
  if (!c || c.status !== 'pending') return false;
  const t = Date.parse(c.checked_at);
  return Number.isFinite(t) && now - t > SOCIAL_STALE_MS;
}

/** Có kết quả nào đang chạy → trang thăm lại. */
export function hasPending(o: BossOverview | undefined): boolean {
  return !!o && Object.values(o.results ?? {}).some((c) => c?.status === 'pending');
}

/** Ghi kết quả vừa chạy vào bản tổng quan đang có (hiện ngay, trước khi tải lại). */
export function withResult(o: BossOverview | undefined, c: BossCheck): BossOverview | undefined {
  if (!o) return o;
  return { ...o, results: { ...o.results, [c.key]: c } };
}
