/**
 * v0.1.50 (F-81, QD-18) — hàm thuần cho thẻ đề xuất GHI VÀO KHO DỮ LIỆU (`kho_create` / `kho_update`) và thẻ GHI NHỚ
 * (`memory_note`): nhãn, câu cảnh báo cố định, bảng trường (đúng `fields.record`), form Sửa theo bảng, câu lỗi theo mã.
 * Không chứa bí mật; mọi giá trị từ máy chủ được ép về chuỗi trước khi hiện (không bao giờ render object).
 */
import { ApiError, KHO_DATE_FIELDS, KHO_FIELDS, KHO_LABEL, KHO_REQUIRED, khoMaxLen, relabelKho, type GenProposal, type KhoBang } from '@gen-harness/contracts';
import { errorDetail } from '../lib/errorText';
import { detailToText } from '../lib/friendlyError';

export type KhoProposal = Extract<GenProposal, { type: 'kho_create' | 'kho_update' }>;
export type MemoryProposal = Extract<GenProposal, { type: 'memory_note' }>;

export const isKhoWrite = (p: GenProposal): p is KhoProposal => p.type === 'kho_create' || p.type === 'kho_update';
export const isMemoryNote = (p: GenProposal): p is MemoryProposal => p.type === 'memory_note';

/** Câu cảnh báo cố định trên thẻ "Ghi vào Kho dữ liệu" (còn chờ xác nhận). */
export const KHO_WRITE_WARNING = `Ghi thẳng vào ${KHO_LABEL} qua Gen-hub khi Sếp bấm Xác nhận và nhập mã PIN — không tự hoàn tác.`;
export const KHO_MISSING_TEXT = 'Gen-hub chưa cấp quyền ghi Kho';
export const KHO_MISSING_HINT = 'Vào Gen-hub tick quyền kho_create, kho_update cho token của Gen-Harness rồi bấm Kiểm tra ở Kết nối › Gen-hub.';
/** Đích của nút "Mở thẻ Gen-hub" / "Mở Kết nối › Gen-hub" — thẻ Gen-hub ở Kết nối. */
export const HUB_CARD_PATH = '/connections#genhub';
/** Đích của nút "Mở MCP Hub" — nơi Sếp mở lại tool ghi Kho đã TỰ đóng (Kiểm tra ở Gen-hub không mở lại). */
export const MCP_HUB_PATH = '/mcp';
/** Sếp đã TỰ đóng tool ghi Kho của thẻ ở MCP Hub (`write_hidden`) — tick ở Gen-hub + Kiểm tra không giúp gì, phải mở ở MCP Hub. */
export const khoHiddenText = (tool: string): string =>
  `Sếp đã tự đóng ${tool} ở MCP Hub nên Gen chưa ghi được. Muốn Gen ghi thì mở lại tool đó (và cấp cho Gen) ở MCP Hub — Kiểm tra ở Gen-hub không tự mở lại.`;
/** Thẻ bị Huỷ SAU một lần ghi "chưa chắc" (nhãn `uncertain`): Kho có thể đã có bản ghi — không được nói "không ghi gì vào Kho". */
export const KHO_CANCELLED_UNCERTAIN_TEXT = 'Đã đóng — Gen không ghi thêm vào Kho (lần ghi trước chưa chắc: Kho có thể đã có bản ghi)';
/** Câu khi bấm "Tải lại hội thoại" lúc Gen đang trả lời câu khác — không đè lượt đang chạy, Sếp bấm lại sau. */
export const RELOAD_BUSY_TEXT = 'Gen đang trả lời — chưa tải lại được hội thoại. Đợi Gen trả lời xong rồi bấm Tải lại hội thoại.';
/** "Xem ở Cài đặt" — thẻ Gen nhớ ở Cài đặt › Bộ não AI (cuộn tới thẻ). */
export const MEMORY_CARD_PATH = '/system?tab=brain#gen-memory';

/** Trường chữ dài (ô nhiều dòng khi Sửa). */
const LONG_FIELDS: ReadonlySet<string> = new Set(['Đã chốt', 'Đang bàn', 'Việc tiếp', 'Cảnh báo']);

export type KhoFieldKind = 'text' | 'long' | 'date' | 'status' | 'priority';

export function khoFieldKind(bang: KhoBang, field: string): KhoFieldKind {
  if (KHO_DATE_FIELDS[bang].includes(field)) return 'date';
  if (bang === 'Việc' && field === 'Trạng thái') return 'status';
  if (bang === 'Việc' && field === 'Ưu tiên') return 'priority';
  return LONG_FIELDS.has(field) ? 'long' : 'text';
}

/** Giá trị máy chủ → chuỗi hiện được (chuỗi / số / boolean; còn lại bỏ trống). */
export function valueText(v: unknown): string {
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  return '';
}

/** Bảng của đề xuất: tạo ⇒ `fields.bang`; sửa ⇒ nhãn `bang` hoặc tiền tố của mã ('PHIEN-12' ⇒ Phiên). Lạ ⇒ null. */
export function khoBangOf(p: KhoProposal): KhoBang | null {
  const direct = p.type === 'kho_create' ? p.fields.bang : (p.labels.bang as string | undefined);
  if (direct === 'Phiên' || direct === 'Việc') return direct;
  if (p.type === 'kho_update') {
    const ma = valueText(p.fields.ma).toUpperCase();
    if (ma.startsWith('PHIEN-')) return 'Phiên';
    if (ma.startsWith('VIEC-')) return 'Việc';
  }
  return null;
}

/** Mã / mô tả bản ghi của đề xuất: sửa ⇒ nhãn `target` hoặc `fields.ma`; tạo ⇒ nhãn `target` hoặc "Bản ghi mới". */
export function khoTargetText(p: KhoProposal): string {
  const label = valueText(p.labels.target).trim();
  if (label) return label;
  return p.type === 'kho_update' ? valueText(p.fields.ma) || '—' : 'Bản ghi mới';
}

export interface KhoRow {
  field: string;
  /** Giá trị hiện tại (chỉ khi sửa; trống ⇒ '—'). */
  cur: string | null;
  next: string;
}

/**
 * Các hàng của bảng "Trường | Hiện tại | Sẽ ghi": ĐÚNG các khoá của `fields.record` (không thêm, không bớt — Sếp thấy chính xác
 * cái sẽ ghi), theo thứ tự `KHO_FIELDS`; khoá lạ (máy chủ mới hơn web) đứng cuối theo thứ tự gốc.
 */
export function khoRows(p: KhoProposal): KhoRow[] {
  const record: Record<string, unknown> = p.fields.record && typeof p.fields.record === 'object' ? p.fields.record : {};
  const bang = khoBangOf(p);
  const known = bang ? KHO_FIELDS[bang] : [];
  const keys = Object.keys(record);
  const ordered = [...known.filter((f) => keys.includes(f)), ...keys.filter((k) => !known.includes(k))];
  return ordered.map((field) => ({
    field,
    cur: p.type === 'kho_update' ? valueText(p.labels[`cur:${field}`]).trim() || '—' : null,
    next: valueText(record[field]),
  }));
}

/**
 * Giá trị HIỆN TẠI của một trường khi sửa bản ghi (nhãn `cur:<trường>` máy chủ đọc từ Kho lúc đề xuất). Chỉ có cho trường Gen đề
 * xuất sửa; trường khác ⇒ null (thẻ không biết giá trị cũ). Rỗng ⇒ '' (Kho đang trống).
 */
export function khoCurrent(p: KhoProposal, field: string): string | null {
  if (p.type !== 'kho_update') return null;
  const key = `cur:${field}`;
  return key in p.labels ? valueText(p.labels[key]).trim() : null;
}

/** Nháp form Sửa: mọi trường được phép của bảng, điền sẵn giá trị đang đề xuất (thiếu ⇒ rỗng). Bảng lạ ⇒ rỗng. */
export function khoInitialDraft(p: KhoProposal): Record<string, string> {
  const bang = khoBangOf(p);
  const record: Record<string, unknown> = p.fields.record && typeof p.fields.record === 'object' ? p.fields.record : {};
  const out: Record<string, string> = {};
  if (!bang) return out;
  for (const f of KHO_FIELDS[bang]) out[f] = valueText(record[f]);
  return out;
}

/** Nháp → `record` gửi lên: chỉ trường được phép của bảng và KHÔNG rỗng (xoá trắng một ô = bỏ trường đó khỏi bản ghi sẽ ghi). */
export function khoDraftRecord(bang: KhoBang, d: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of KHO_FIELDS[bang]) {
    const v = (d[f] ?? '').trim();
    if (v) out[f] = v;
  }
  return out;
}

export function sameRecord(a: Record<string, unknown>, b: Record<string, unknown>): boolean {
  const ka = Object.keys(a);
  return ka.length === Object.keys(b).length && ka.every((k) => k in b && valueText(a[k]) === valueText(b[k]));
}

/** Số ký tự như máy chủ đếm (điểm mã Unicode — `len()` của Python), không phải số đơn vị UTF-16. */
export const khoLen = (v: string): number => [...v].length;

/** Lỗi độ dài của một ô khi Sửa ('Tối đa 200 ký tự') hoặc null — cùng giới hạn `kho_write.py` (tính sau khi bỏ khoảng trắng hai đầu). */
export function khoLengthError(bang: KhoBang, field: string, value: string): string | null {
  const max = khoMaxLen(bang, field);
  return khoLen(value.trim()) > max ? `Tối đa ${max} ký tự` : null;
}

/** Form Sửa hợp lệ: tạo ⇒ có trường bắt buộc (Chủ đề / Tiêu đề); sửa ⇒ còn ít nhất một trường; không ô nào quá dài. Link phải là https. */
export function khoDraftValid(p: KhoProposal, d: Record<string, string>): boolean {
  const bang = khoBangOf(p);
  if (!bang) return false;
  const rec = khoDraftRecord(bang, d);
  if (p.type === 'kho_create' && !rec[KHO_REQUIRED[bang]]) return false;
  if (Object.keys(rec).length === 0) return false;
  if (Object.entries(rec).some(([f, v]) => khoLengthError(bang, f, v))) return false;
  const link = rec['Link Issue/PR'];
  return !link || /^https:\/\/\S+$/.test(link);
}

/** Lỗi của ô Link khi Sửa (chuỗi) hoặc null. */
export function khoLinkError(d: Record<string, string>): string | null {
  const link = (d['Link Issue/PR'] ?? '').trim();
  return link && !/^https:\/\/\S+$/.test(link) ? 'Link phải bắt đầu bằng https://' : null;
}

/**
 * F-87: câu trên thẻ ghi Phiên của bản mới (nhãn `release`): mỗi (tổ chức, bản) chỉ ghi Kho MỘT lần — Owner khác ghi rồi thì thẻ tự
 * đóng; Huỷ ở đây là huỷ cho mọi Owner.
 */
export const khoReleaseNote = (version: string): string =>
  `Phiên của bản ${version}: mỗi bản chỉ ghi vào Kho một lần cho cả tổ chức — Owner khác đã ghi thì thẻ này tự đóng; bấm Huỷ là huỷ cho mọi Owner.`;

/** Quyền ghi Kho của token theo nhãn lúc đề xuất. */
export const writeScopeMissing = (p: KhoProposal): boolean => p.labels.write_scope === 'missing';

/** Thẻ có lần ghi "chưa chắc" (502 HUB_WRITE_UNCERTAIN — máy chủ gắn nhãn `uncertain`). */
export const khoUncertain = (p: KhoProposal): boolean => p.labels.uncertain === '1';

/**
 * Quyền ghi Kho HIỆN TẠI cho ĐÚNG tool của thẻ (`kho_create` / `kho_update`) theo `GET /hub/link` — cờ riêng của tool nếu máy chủ gửi,
 * không thì cờ chung `kho` (máy chủ cũ). Chỉ `true` mới là có quyền; thiếu / không phải boolean ⇒ chưa có.
 */
export function khoToolWritable(scopes: Partial<Record<string, unknown>> | null | undefined, tool: KhoProposal['type']): boolean {
  if (!scopes || typeof scopes !== 'object') return false;
  const own = scopes[tool];
  return (typeof own === 'boolean' ? own : scopes.kho) === true;
}

// ── Câu lỗi theo mã (xác nhận Ghi nhớ / Ghi vào Kho) ─────────────────────────────────────────────────────────

export type ProposalErrorAction = 'open_hub' | 'open_mcp' | 'open_memory' | 'reload' | null;
export interface ProposalErrorView {
  text: string;
  /** Nút kèm câu lỗi: mở Kết nối › Gen-hub / mở MCP Hub / mở Cài đặt › Gen nhớ / tải lại hội thoại (thẻ đã đóng ở nơi khác). */
  action: ProposalErrorAction;
}

/** Lý do thật máy chủ gửi kèm (`detail`, vd lời Kho từ chối) — luôn là chuỗi, đã bỏ khoảng trắng; không có ⇒ ''. */
function serverDetail(e: ApiError): string {
  return detailToText(e.problem.detail).trim();
}

export const KHO_REJECTED_FIX = 'Bấm Sửa để chỉnh các trường rồi Xác nhận lại.';
/** 502 HUB_WRITE_UNCERTAIN: có thể đã ghi — nói rõ việc tiếp theo cho cả hai trường hợp (đúng ghi chú phát hành v0.1.50 bước 2). */
export const KHO_UNCERTAIN_TEXT =
  'Chưa chắc đã ghi — Sếp mở Kho kiểm trước khi bấm lại: Kho đã có bản ghi thì bấm Huỷ; chưa có thì bấm Xác nhận lại.';
export const KHO_PERMIT_TEXT = 'Giấy phép ghi không hợp lệ hoặc đã quá 5 phút — chưa ghi gì vào Kho. Bấm Xác nhận lại (nhập mã PIN) để ghi.';
export const KHO_TOKEN_TEXT = 'Token Gen-hub hết hạn hoặc đã bị thu hồi — chưa ghi gì vào Kho. Vào Kết nối › Gen-hub dán token mới rồi bấm Kiểm tra.';
export const PROPOSAL_DECIDED_TEXT =
  'Đề xuất này đã được xác nhận hoặc đã huỷ ở nơi khác (hoặc Owner khác đã ghi / huỷ bản này) — chưa làm gì thêm. Bấm Tải lại hội thoại để xem thẻ đã đóng.';

type ViewSpec = { text: string | ((e: ApiError) => string); action: ProposalErrorAction };

const ERROR_VIEW: Record<string, ViewSpec> = {
  HUB_WRITE_MISSING: {
    text: `${KHO_MISSING_TEXT} — ${KHO_MISSING_HINT} Chưa ghi gì vào Kho.`,
    action: 'open_hub',
  },
  HUB_WRITE_HIDDEN: {
    text: 'Sếp đã tự đóng tool ghi Kho này ở MCP Hub — chưa ghi gì vào Kho. Muốn Gen ghi thì mở lại tool đó (và cấp cho Gen) ở MCP Hub; Kiểm tra ở Gen-hub không tự mở lại.',
    action: 'open_mcp',
  },
  HUB_WRITE_UNCERTAIN: {
    text: KHO_UNCERTAIN_TEXT,
    action: null,
  },
  HUB_WRITE_PERMIT: { text: KHO_PERMIT_TEXT, action: null },
  HUB_TOOL_NOT_ALLOWED: {
    text: 'Thao tác ghi này không nằm trong phạm vi Gen được phép — chưa ghi gì vào Kho.',
    action: null,
  },
  // Kho từ chối (trường sai, Kho báo lỗi): nêu ĐÚNG lý do Kho trả (máy chủ đặt ở `detail`, đã che) để Sếp biết sửa trường nào.
  HUB_WRITE_REJECTED: {
    text: (e) => {
      const why = serverDetail(e);
      return `Kho từ chối lần ghi này${why ? `: ${why.replace(/[.\s]+$/, '')}` : ''} — chưa ghi gì. ${KHO_REJECTED_FIX}`;
    },
    action: null,
  },
  HUB_WRITE_INVALID: {
    text: (e) => {
      const why = serverDetail(e);
      return `Dữ liệu ghi Kho chưa hợp lệ${why ? ` (${why.replace(/[.\s]+$/, '')})` : ''} — chưa ghi gì vào Kho. ${KHO_REJECTED_FIX}`;
    },
    action: null,
  },
  // 401/403 từ Gen-hub lúc ghi: token hết hạn / bị thu hồi — Sửa thẻ không giúp gì, phải đổi token.
  HUB_TOKEN_REJECTED: { text: KHO_TOKEN_TEXT, action: 'open_hub' },
  HUB_BREAKER_OPEN: {
    text: 'Gen-hub tạm không trả lời — Gen tự thử lại sau 1 phút. Chưa ghi gì, Sếp bấm Xác nhận lại sau.',
    action: null,
  },
  HUB_LINK_OFF: {
    text: 'Gen-hub đang tắt — vào Kết nối › Gen-hub bấm Kiểm tra để bật lại. Chưa ghi gì vào Kho.',
    action: 'open_hub',
  },
  HUB_BLOCKED: {
    text: 'Ghi Kho đang bị rào chắn MCP Hub chặn — chưa ghi gì vào Kho. Xem lý do ở "Chi tiết kỹ thuật", rồi mở Kết nối › Gen-hub bấm Kiểm tra.',
    action: 'open_hub',
  },
  HUB_OWNER_ONLY: {
    text: `Chỉ Sếp (Owner) được ghi vào ${KHO_LABEL} — chưa ghi gì.`,
    action: null,
  },
  GEN_MEMORY_FULL: {
    text: 'Gen nhớ đã đủ 30 ghi chú — Sếp xoá bớt ở Cài đặt › Bộ não AI rồi xác nhận lại.',
    action: 'open_memory',
  },
  GEN_MEMORY_DUPLICATE: {
    text: 'Ghi chú này đã có trong Gen nhớ — không cần ghi lại.',
    action: 'open_memory',
  },
  GEN_PROPOSAL_DECIDED: { text: PROPOSAL_DECIDED_TEXT, action: 'reload' },
};

/**
 * Câu thân thiện + nút kèm theo cho mã lỗi của Ghi nhớ / Ghi vào Kho; mã khác ⇒ null (dùng `errorText` chung).
 * `khoLabel` = tên Kho Owner tự đặt (v0.1.57, Nợ #30; `GenProposal.labels.kho`) — câu kết quả dùng nó thay cho "Kho dữ liệu".
 */
export function proposalErrorView(e: unknown, khoLabel?: string | null): ProposalErrorView | null {
  if (!(e instanceof ApiError)) return null;
  const spec = ERROR_VIEW[e.code];
  if (!spec) return null;
  const text = typeof spec.text === 'function' ? spec.text(e) : spec.text;
  return { text: relabelKho(text, khoLabel), action: spec.action };
}

/**
 * Dòng "Chi tiết kỹ thuật" cho lỗi của thẻ: mã HTTP + mã lỗi + mã yêu cầu (`errorDetail`) KÈM lý do máy chủ gửi ở `detail`
 * (lời Kho từ chối, lý do permit EXPIRED/USED, lỗi mạng…) — chuỗi, không bao giờ là đối tượng; không có gì ⇒ null.
 */
export function proposalErrorDetail(e: unknown): string | null {
  const base = errorDetail(e);
  const extra = e instanceof ApiError ? serverDetail(e) : '';
  const parts = [base ?? '', extra && !(base ?? '').includes(extra) ? extra : ''].filter(Boolean);
  return parts.length ? parts.join(' · ') : null;
}
