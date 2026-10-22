/** Field rules for the Owner setup wizard (docs/handoff/06, docs/api/phase-1.md). */

export const PASSWORD_MIN = 12;

export function validateRequired(v: string, message: string): string | null {
  return v.trim() ? null : message;
}

export function validateToken(v: string): string | null {
  return v.trim() ? null : 'Nhập mã thiết lập hiện trong màn hình dòng lệnh của trình cài.';
}

export function validateEmail(v: string): string | null {
  const s = v.trim();
  if (!s) return 'Nhập email.';
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s) ? null : 'Email chưa đúng định dạng.';
}

export function validatePassword(v: string): string | null {
  if (!v) return 'Nhập mật khẩu.';
  return v.length >= PASSWORD_MIN ? null : `Mật khẩu cần ít nhất ${PASSWORD_MIN} ký tự (hiện ${v.length}).`;
}

export function validatePin(v: string): string | null {
  return /^\d{6}$/.test(v) ? null : 'PIN gồm đúng 6 chữ số.';
}

export function validatePinConfirm(pin: string, confirm: string): string | null {
  if (!/^\d{6}$/.test(confirm)) return 'Nhập lại đủ 6 chữ số.';
  return pin === confirm ? null : 'Hai lần nhập mã PIN chưa khớp.';
}

export interface Strength {
  /** 0–4 */
  score: number;
  label: string;
  tone: 'bad' | 'warn' | 'ok';
}

/**
 * Lightweight strength meter: length is the main factor (≥12 required),
 * then character variety; repeated/sequential runs are penalised.
 */
export function passwordStrength(v: string): Strength {
  if (!v) return { score: 0, label: 'Chưa nhập', tone: 'bad' };
  let score = 0;
  if (v.length >= PASSWORD_MIN) score += 1;
  if (v.length >= 16) score += 1;
  const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((r) => r.test(v)).length;
  if (classes >= 2) score += 1;
  if (classes >= 3) score += 1;
  if (/(.)\1{2,}/.test(v) || /(0123|1234|2345|3456|4567|5678|6789|abcd|qwer|asdf)/i.test(v)) score -= 1;
  if (v.length < PASSWORD_MIN) score = Math.min(score, 1);
  score = Math.max(0, Math.min(4, score));
  const labels = ['Rất yếu', 'Yếu', 'Trung bình', 'Khá', 'Mạnh'];
  const tone: Strength['tone'] = score <= 1 ? 'bad' : score === 2 ? 'warn' : 'ok';
  return { score, label: labels[score], tone };
}

export interface Step1Values {
  token: string;
  language: 'vi' | 'en';
  mode: 'empty' | 'sample';
}
export interface Step2Values {
  token: string;
  display_name: string;
  email: string;
  password: string;
  pin: string;
  pin_confirm: string;
}
export interface Step3Values {
  org_name: string;
  timezone: string;
  currency: string;
  self_name: string;
  bot_calls_me: string;
}

export type Errors<T> = Partial<Record<keyof T, string>>;

/** v0.1.55: cách xưng hô điền sẵn ở bước 3 (cả "Sếp tự xưng" lẫn "Agent gọi Sếp") — khớp `DEFAULT_ADDRESSING` của api. */
export const DEFAULT_ADDRESSING = 'Sếp';

export function step1Errors(v: Step1Values): Errors<Step1Values> {
  const e: Errors<Step1Values> = {};
  const t = validateToken(v.token);
  if (t) e.token = t;
  return e;
}

export function step2Errors(v: Step2Values): Errors<Step2Values> {
  const e: Errors<Step2Values> = {};
  const checks: Array<[keyof Step2Values, string | null]> = [
    ['token', validateToken(v.token)],
    ['display_name', validateRequired(v.display_name, 'Nhập tên hiển thị.')],
    ['email', validateEmail(v.email)],
    ['password', validatePassword(v.password)],
    ['pin', validatePin(v.pin)],
    ['pin_confirm', validatePinConfirm(v.pin, v.pin_confirm)],
  ];
  for (const [k, msg] of checks) if (msg) e[k] = msg;
  return e;
}

export function step3Errors(v: Step3Values): Errors<Step3Values> {
  const e: Errors<Step3Values> = {};
  const checks: Array<[keyof Step3Values, string | null]> = [
    ['org_name', validateRequired(v.org_name, 'Nhập tên tổ chức.')],
    ['timezone', validateRequired(v.timezone, 'Chọn múi giờ.')],
    ['currency', validateRequired(v.currency, 'Chọn tiền tệ.')],
    ['self_name', validateRequired(v.self_name, 'Nhập cách Sếp tự xưng.')],
    ['bot_calls_me', validateRequired(v.bot_calls_me, 'Nhập cách agent gọi Sếp.')],
  ];
  for (const [k, msg] of checks) if (msg) e[k] = msg;
  return e;
}

export const isComplete = (errors: object): boolean => Object.keys(errors).length === 0;

/** Step 3 live preview — one line of a sample exchange with the chosen addressing. */
export function addressingPreview(selfName: string, botCallsMe: string): string {
  const self = (selfName.trim() || 'Anh').toLocaleLowerCase('vi');
  const boss = botCallsMe.trim() || 'Sếp';
  const Boss = boss.charAt(0).toLocaleUpperCase('vi') + boss.slice(1);
  return `“Sáng nay ${self} cần xem gì?” → “Dạ ${Boss}, sáng nay có 3 cơ hội nóng và 2 tin đang chờ ${boss} duyệt ạ.”`;
}

/**
 * v0.1.28 (UX V1): lý do "Tiếp tục" đang khoá — lỗi đầu tiên CHƯA hiện dưới ô nào (ô chưa rời khỏi / chưa chạm),
 * để không lặp lại câu đã có ngay dưới ô.
 */
export function firstHiddenError<K extends string>(errors: Partial<Record<K, string>>, shown: (k: K) => string | null): string | null {
  for (const [k, msg] of Object.entries(errors) as Array<[K, string | undefined]>) if (msg && !shown(k)) return `Còn thiếu: ${msg}`;
  return null;
}
