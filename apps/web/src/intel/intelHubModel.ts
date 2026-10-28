/**
 * v0.1.79 (G1) — hợp đồng JSON + hàm thuần của trang Tình báo (`/intel`): `GET /signals`, `GET /signals/{id}`, `GET /asks`.
 * Chỉ chuỗi/số/bool/null. Mọi chuỗi từ máy chủ đi qua `txt` trước khi render (không bao giờ render object).
 */
import { KIND_LABEL, vnTime } from './intelModel';

export type SignalLevel = 'red' | 'amber' | 'grey' | 'off';
export type SignalStatus = 'open' | 'acknowledged' | 'resolved' | 'dismissed' | 'expired' | 'broken';
export type SignalTransition = 'acknowledged' | 'resolved' | 'dismissed';
export type SignalVerdict = 'correct' | 'wrong' | 'unimportant' | 'missed';

export interface SignalItem {
  id: string;
  code: string | null;
  kind: string;
  level: SignalLevel;
  gate: string | null;
  status: SignalStatus;
  delivery: string | null;
  privacy: string | null;
  title: string;
  summary: string | null;
  suggested_action: string | null;
  confidence_level: 'high' | 'medium' | 'low' | null;
  reasons: string[];
  created_at: string | null;
  would_push_at: string | null;
  due_at: string | null;
  group_id: string | null;
  evidence_n: number | null;
  feedback_verdict: SignalVerdict | null;
}
export interface SignalList {
  items: SignalItem[];
  hidden_pin_n: number;
}
export interface SignalEvidence {
  type?: string | null;
  id?: string | null;
  quote_status?: string | null;
  quote_display?: string | null;
  received_at?: string | null;
}
export interface SignalDetail extends SignalItem {
  evidence: SignalEvidence[];
}
export interface AskItem {
  id: string;
  code: string | null;
  source: string | null;
  decision_kind: string | null;
  summary: string | null;
  signal_id: string | null;
  expires_at: string | null;
}
export interface AskList {
  items: AskItem[];
  hidden_pin_n: number;
}

/** Chuỗi an toàn: không phải chuỗi ⇒ rỗng (không `[object Object]`). */
export const txt = (v: unknown): string => (typeof v === 'string' ? v : typeof v === 'number' && Number.isFinite(v) ? String(v) : '');

/** Ba mức Sếp thấy (mức `off` không bao giờ hiện). */
export const LEVELS: Array<{ level: Exclude<SignalLevel, 'off'>; dot: string; label: string; hint: string }> = [
  { level: 'red', dot: '🔴', label: 'Khẩn', hint: 'Gen báo ngay' },
  { level: 'amber', dot: '🟠', label: 'Đầu bản tin', hint: 'Nằm đầu bản tin sáng' },
  { level: 'grey', dot: '⚪', label: 'Để biết', hint: 'Xem khi rảnh' },
];
export const levelMeta = (l: string) => LEVELS.find((x) => x.level === l) ?? LEVELS[2];

export const STATUS_LABEL: Record<string, string> = {
  open: 'Đang mở',
  acknowledged: 'Đã ghi nhận',
  resolved: 'Đã xong',
  dismissed: 'Đã bỏ qua',
  expired: 'Hết hạn',
  broken: 'Không còn đúng',
};
export const statusLabel = (s: string) => STATUS_LABEL[s] ?? 'Không rõ';
/** Trạng thái để lọc ở bảng (máy chủ mặc định `open`; mỗi lần lọc đúng MỘT trạng thái). */
export const STATUS_FILTERS = ['open', 'acknowledged', 'resolved', 'dismissed', 'expired'] as const;

/** Mười loại tín hiệu của máy chủ (`levels.KINDS`) bằng chữ Sếp dùng; loại lạ ⇒ "Tín hiệu". */
export const ALL_KIND_LABEL: Record<string, string> = {
  ...KIND_LABEL,
  rui_ro_lua_dao: 'Rủi ro lừa đảo',
  suc_khoe_an_toan: 'Sức khoẻ & an toàn',
};
export const signalKindLabel = (k: string) => ALL_KIND_LABEL[k] ?? 'Tín hiệu';

export const CONFIDENCE_LABEL: Record<string, string> = { high: 'cao', medium: 'vừa', low: 'thấp' };
export const confidenceLabel = (c: string | null | undefined) => (c ? (CONFIDENCE_LABEL[c] ?? '') : '');

/** Bốn cổng thường của chạy thử 14 ngày (`levels.REGULAR_GATES`) bằng chữ Sếp dùng. */
export const GATE_LABEL: Record<string, string> = {
  khach_che_cong_khai_doa_bo: 'Khách chê / doạ bỏ',
  loi_hua_khach_sap_vo: 'Lời hứa sắp vỡ',
  co_hoi_lon_sap_troi: 'Cơ hội sắp trôi',
  su_co_cong_viec_gap: 'Sự cố công việc gấp',
};
export const REGULAR_GATES = Object.keys(GATE_LABEL);
export const gateLabel = (g: string) => GATE_LABEL[g] ?? 'Loại khác';

/** Giờ của dòng: "HH:MM dd/MM" theo giờ VN, thiếu ⇒ rỗng. */
export const when = (iso: string | null | undefined) => vnTime(iso);

/** Gom tín hiệu theo mức (🔴 → 🟠 → ⚪); mức lạ/`off` bị bỏ. Trong mỗi mức giữ thứ tự máy chủ (mới trước). */
export function groupByLevel(items: SignalItem[]): Array<{ level: Exclude<SignalLevel, 'off'>; dot: string; label: string; hint: string; items: SignalItem[] }> {
  return LEVELS.map((m) => ({ ...m, items: items.filter((i) => i.level === m.level) }));
}

/** Số 🔴 đang mở (cho tiêu đề). */
export const redCount = (items: SignalItem[]) => items.filter((i) => i.level === 'red').length;

/** Phản hồi sai khuôn ⇒ lỗi tại chỗ thay vì vỡ khi đọc trường thiếu. */
export function isSignalItem(v: unknown): v is SignalItem {
  const o = v as Partial<SignalItem> | null;
  return !!o && typeof o === 'object' && typeof o.id === 'string' && typeof o.title === 'string' && typeof o.level === 'string' && typeof o.kind === 'string' && typeof o.status === 'string';
}
export function isSignalList(v: unknown): v is SignalList {
  const o = v as Partial<SignalList> | null;
  return !!o && typeof o === 'object' && Array.isArray(o.items) && o.items.every(isSignalItem);
}
export function isAskList(v: unknown): v is AskList {
  const o = v as Partial<AskList> | null;
  return !!o && typeof o === 'object' && Array.isArray(o.items) && o.items.every((a) => !!a && typeof a.id === 'string');
}

export interface SignalFilters {
  level: string;
  status: string;
  kind: string;
}
export const DEFAULT_FILTERS: SignalFilters = { level: '', status: 'open', kind: '' };

/** Chuỗi truy vấn cho `GET /signals` (bỏ bộ lọc rỗng). */
export function signalQuery(f: SignalFilters, limit = 200): string {
  const p = new URLSearchParams();
  if (f.level) p.set('level', f.level);
  if (f.kind) p.set('kind', f.kind);
  p.set('status', f.status || 'open');
  p.set('limit', String(limit));
  return p.toString();
}
