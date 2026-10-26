/**
 * v0.1.76 (T11): hợp đồng JSON của thẻ "Tình báo" (`GET /intel/console` + các `POST/PUT /intel/*`) và trần `intel_*`
 * (`GET/PUT /system/ai-cost/intel`, L2). Chỉ chuỗi/số/bool/null — không có mã, PIN hay số tài khoản đầy đủ.
 */
export type IntelMode = 'live' | 'shadow';
export type IntelRamp = 'digest_top' | 'push';

export interface IntelMute {
  id: string;
  scope_value: string;
  source: string;
  until: string | null;
}
export interface IntelPayee {
  signal_id: string;
  title: string;
  last3: string | null;
  bank: string | null;
}
export interface IntelGroupLabel {
  context: string;
  is_primary: boolean;
}
export interface IntelGroup {
  id: string;
  name: string;
  labels: IntelGroupLabel[];
}
export interface IntelConsole {
  pin_active: boolean;
  mode: IntelMode;
  ramp_gates: IntelRamp;
  assistant: { locked: boolean; locked_at: string | null; locked_via: string | null };
  outbound: { unlocked_today: boolean; locked_until: string | null };
  backup_channel: { paired: boolean; kind: 'telegram' | 'none' };
  mutes: IntelMute[];
  payees: IntelPayee[];
  groups: IntelGroup[];
  contexts: string[];
  /** v0.1.76 (Ryan quyết 26/10): false ⇒ bốn cổng thường chỉ lên đầu bản tin, khoá «Báo ngay». Máy chủ cũ không gửi ⇒ coi như bật. */
  regular_push_enabled?: boolean;
  /** v0.1.77 (R4): chạy thử 14 ngày của bốn cổng thường + M1 gộp. Máy chủ cũ không gửi ⇒ không hiện banner. */
  ramp?: IntelRampState;
  /** v0.1.77: dòng bóng "đáng lẽ báo ngay" chờ Sếp chấm Đúng/Sai. Máy chủ cũ không gửi ⇒ không hiện. */
  shadow_items?: IntelShadowItem[];
}

export interface IntelRampState {
  started_at: string | null;
  ends_at: string | null;
  days: number;
  days_left: number | null;
  /** Còn trong 14 ngày hoặc bốn cổng chưa qua M1 ⇒ bốn cổng thường chưa nhắn riêng. */
  in_trial: boolean;
  passed_gates: string[];
  m1: { n: number; k: number; p: number | null; lo95: number | null; ok: boolean; min_n: number; wrong_by_reason: Record<string, number>; low_precision_gates: string[] };
}

export interface IntelShadowItem {
  id: string;
  kind: string;
  gate: string | null;
  title: string;
  reasons: string[];
  would_push_at: string | null;
  verdict: string | null;
}

/** Ba lý do của nút "Sai" (`biz.signal_feedback.reason`): bậc hành vi · độ gấp · thẩm quyền. */
export type WrongReason = 'still_buying' | 'not_urgent' | 'staff_handled';
export const WRONG_REASONS: Array<{ value: WrongReason; label: string }> = [
  { value: 'still_buying', label: 'Khách vẫn mua' },
  { value: 'not_urgent', label: 'Không gấp' },
  { value: 'staff_handled', label: 'NV lo rồi' },
];

/** Lý do mức (INT-10) bằng chữ thường dùng; mã lạ ⇒ bỏ (không hiện mã máy cho Sếp). */
export const REASON_LABEL: Record<string, string> = {
  shadow: 'đang chạy thử, chưa nhắn riêng',
  key_customer: 'khách chủ chốt (Sếp đã đánh dấu)',
  window_expired: 'quá 24 giờ chưa xử lý',
  milestone_due_soon: 'sắp tới hạn lời hứa',
  staff_owned: 'nhân viên đang lo',
  asks_for_owner: 'khách đòi gặp Sếp',
  capped: 'quá 3 tin báo ngay hôm nay',
  precision: 'loại này bị chấm Sai nhiều',
};
export const reasonText = (codes: string[]): string[] => codes.map((c) => REASON_LABEL[c]).filter((x): x is string => !!x);

/** Tên loại tín hiệu cho Sếp; loại lạ ⇒ "Tín hiệu". */
export const KIND_LABEL: Record<string, string> = {
  nghia_vu_loi_hua: 'Lời hứa với khách',
  viec_han: 'Việc có hạn',
  cau_hoi_cho_xu_ly: 'Câu hỏi chờ trả lời',
  xung_dot_khieu_nai: 'Khách chê / doạ bỏ',
  quan_he_nong_nguoi: 'Quan hệ cần để ý',
  co_hoi_ket_noi: 'Cơ hội',
  su_kien_dip: 'Sự kiện, dịp',
  thi_truong_thong_tin: 'Thông tin thị trường',
};
export const kindLabel = (k: string) => KIND_LABEL[k] ?? 'Tín hiệu';

/** "dd/MM" theo giờ VN từ ISO (ghép từ các phần, không phụ thuộc dấu phân cách của locale); không đọc được ⇒ rỗng. */
export function vnDay(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const parts = new Intl.DateTimeFormat('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh', day: '2-digit', month: '2-digit' }).formatToParts(d);
  const pick = (t: string) => parts.find((x) => x.type === t)?.value ?? '';
  return `${pick('day')}/${pick('month')}`;
}

/** Banner R4: "Chạy thử lại 14 ngày (từ dd/MM)…" — chỉ khi đang chạy thử; hết thử mà chưa đạt M1 thì nói còn thiếu bao nhiêu lượt chấm. */
export function rampBannerText(r: IntelRampState | undefined): string {
  if (!r || !r.in_trial) return '';
  const from = vnDay(r.started_at);
  const head = `Chạy thử lại ${r.days} ngày${from ? ` (từ ${from})` : ''}`;
  const left = r.days_left != null && r.days_left > 0 ? `, còn ${r.days_left} ngày` : '';
  const need = Math.max(0, r.m1.min_n - r.m1.n);
  const m1 = need > 0 ? `Sếp đã chấm ${r.m1.n}/${r.m1.min_n} lượt` : r.m1.ok ? 'Độ đúng đã đạt' : `Đã chấm ${r.m1.n} lượt, chưa đủ chính xác để nhắn ngay`;
  return `${head}${left}: khách chê/doạ bỏ, lời hứa sắp vỡ, cơ hội sắp trôi và sự cố việc chỉ nằm đầu bản tin. ${m1}. Lừa đảo và an toàn vẫn báo ngay.`;
}

/** v0.1.76 (T14, Ryan chốt D3): `GET /intel/setup` — hai bước "Thiết lập Tình báo" (gán bối cảnh nhóm, khai nhân viên). */
export interface IntelSetupGroup {
  id: string;
  name: string;
  member_count: number | null;
}
export interface IntelSetupPerson {
  id: string;
  name: string;
  person_type: string;
  /** Loại trước khi được đánh dấu nhân viên (tắt nhân viên ⇒ trả về đúng loại này). */
  prev_type?: string | null;
  /** Đang là nhân viên nhưng chưa được Sếp đánh dấu bằng mã PIN (trước v0.1.76 / quy tắc cũ) ⇒ Gen chưa tin vai này cho cảnh báo lừa đảo. */
  needs_reconfirm?: boolean;
}
/** v0.1.77 (R2): bước 3 "Khách chủ chốt" — Sếp khai; trợ lý chỉ gợi ý tên (`suggested`), không dùng chấm mức. */
export interface IntelSetupCustomer {
  id: string;
  /** Thực thể Hồ sơ (đối tượng của `owner_key_relation`). */
  entity_id: string;
  name: string;
  /** Mục Hồ sơ đã xác nhận (có ⇒ đang là khách chủ chốt; dùng để bỏ đánh dấu). */
  key_fact_id: string | null;
  suggested: boolean;
}
export interface IntelSetupKeyCustomers {
  done: boolean;
  confirmed_at: string | null;
  key_count: number;
  customers: IntelSetupCustomer[];
}
export interface IntelSetup {
  ready: boolean;
  groups: { done: boolean; total: number; unlabeled_count: number; unlabeled: IntelSetupGroup[] };
  staff: { done: boolean; confirmed_at: string | null; staff_count: number; people: IntelSetupPerson[] };
  /** Máy chủ cũ không gửi ⇒ coi như không có bước 3. */
  key_customers?: IntelSetupKeyCustomers;
  contexts: string[];
  gated_kinds: string[];
}

/** Câu nhắc ngắn khi chưa xong (banner thẻ Tình báo): chỉ nêu phần còn thiếu. */
export function setupPendingText(s: IntelSetup): string {
  const parts: string[] = [];
  if (!s.groups.done) parts.push(`còn ${s.groups.unlabeled_count} nhóm chưa gán bối cảnh`);
  if (!s.staff.done) parts.push('chưa khai nhân viên');
  if (s.key_customers && !s.key_customers.done) parts.push('chưa khai khách chủ chốt');
  return parts.join(', ');
}

export interface IntelBudget {
  values: Record<string, number>;
  defaults: Record<string, number>;
}

/** Hai loại cảnh báo "hạ phòng thủ": tắt chúng chỉ làm được trên Console + PIN (spec §8.1). */
export const DEFENSIVE_KINDS: Array<{ kind: string; label: string }> = [
  { kind: 'rui_ro_lua_dao', label: 'Rủi ro lừa đảo' },
  { kind: 'suc_khoe_an_toan', label: 'Sức khoẻ & an toàn' },
];

export const CONTEXT_LABEL: Record<string, string> = {
  kinh_doanh: 'Kinh doanh',
  cong_viec: 'Công việc',
  hoc_tap: 'Học tập',
  gia_dinh: 'Gia đình',
  ban_be: 'Bạn bè',
  nhan_vien: 'Nhân viên',
  giao_thuong: 'Giao thương',
  khac: 'Khác',
};
export const contextLabel = (c: string) => CONTEXT_LABEL[c] ?? c;

/** Bảy khoá trần tình báo (`settings.ai_cost.intel_*`): tên hiển thị + đơn vị. Lượt = số lượt gọi model/ngày; VND = ₫. */
export const INTEL_CAPS: Array<{ key: string; label: string; unit: 'lượt' | '₫' }> = [
  { key: 'intel_daily_call_cap', label: 'Trần lượt gọi mỗi ngày', unit: 'lượt' },
  { key: 'intel_safety_call_cap', label: 'Trần lượt an toàn (vượt chỉ báo, không cắt)', unit: 'lượt' },
  { key: 'intel_gate_call_cap', label: 'Trần lượt 4 cổng thường', unit: 'lượt' },
  { key: 'intel_daily_budget_vnd', label: 'Ngân sách tình báo mỗi ngày', unit: '₫' },
  { key: 'intel_safety_budget_vnd', label: 'Ngân sách an toàn mỗi ngày', unit: '₫' },
  { key: 'intel_gate_budget_vnd', label: 'Ngân sách 4 cổng thường mỗi ngày', unit: '₫' },
  { key: 'intel_media_daily_budget_vnd', label: 'Ngân sách đọc ảnh/tiếng mỗi ngày', unit: '₫' },
];

/** "HH:MM dd/MM" theo giờ VN từ ISO; không đọc được ⇒ chuỗi rỗng (không bao giờ render object). */
export function vnTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh', hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' });
}
