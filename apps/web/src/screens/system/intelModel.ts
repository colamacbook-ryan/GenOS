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
