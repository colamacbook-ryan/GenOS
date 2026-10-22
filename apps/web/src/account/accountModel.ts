/** Quy tắc ô + hiển thị cho "Tài khoản của tôi" (v0.1.19) — khớp gh/auth/account.py. */
import { ApiError } from '@gen-harness/contracts';
import { PASSWORD_MIN, validateEmail, validatePin, validatePinConfirm } from '../setup/validation';

export const accountKey = ['account'] as const;

export type Errors<K extends string> = Partial<Record<K, string>>;

export interface PasswordValues {
  current_password: string;
  new_password: string;
  new_password_confirm: string;
}

export function passwordErrors(v: PasswordValues): Errors<keyof PasswordValues> {
  const e: Errors<keyof PasswordValues> = {};
  if (!v.current_password) e.current_password = 'Nhập mật khẩu hiện tại.';
  if (!v.new_password) e.new_password = 'Nhập mật khẩu mới.';
  else if (v.new_password.length < PASSWORD_MIN) {
    e.new_password = `Mật khẩu mới cần ít nhất ${PASSWORD_MIN} ký tự (hiện ${v.new_password.length}).`;
  } else if (v.current_password && v.new_password === v.current_password) {
    e.new_password = 'Mật khẩu mới phải khác mật khẩu hiện tại.';
  }
  if (!v.new_password_confirm) e.new_password_confirm = 'Nhập lại mật khẩu mới.';
  else if (v.new_password_confirm !== v.new_password) e.new_password_confirm = 'Hai lần nhập mật khẩu chưa khớp.';
  return e;
}

export interface ProfileValues {
  display_name: string;
  email: string;
  current_password: string;
}

export function profileErrors(v: ProfileValues, originalEmail: string): Errors<keyof ProfileValues> {
  const e: Errors<keyof ProfileValues> = {};
  const name = v.display_name.trim();
  if (!name) e.display_name = 'Nhập tên hiển thị.';
  else if (name.length > 100) e.display_name = 'Tên hiển thị tối đa 100 ký tự.';
  const emailErr = validateEmail(v.email);
  if (emailErr) e.email = emailErr;
  if (emailChanged(v.email, originalEmail) && !v.current_password) {
    e.current_password = 'Nhập mật khẩu hiện tại để đổi email.';
  }
  return e;
}

export const emailChanged = (email: string, original: string) =>
  email.trim().toLowerCase() !== original.trim().toLowerCase();

export interface PinValues {
  current_password: string;
  new_pin: string;
  new_pin_confirm: string;
}

export function pinErrors(v: PinValues): Errors<keyof PinValues> {
  const e: Errors<keyof PinValues> = {};
  if (!v.current_password) e.current_password = 'Nhập mật khẩu hiện tại.';
  const pin = validatePin(v.new_pin);
  if (pin) e.new_pin = pin;
  const confirm = validatePinConfirm(v.new_pin, v.new_pin_confirm);
  if (confirm) e.new_pin_confirm = confirm;
  return e;
}

export const hasErrors = (e: Record<string, string | undefined>) => Object.values(e).some(Boolean);

/** 422 của API → lỗi theo ô (sai mật khẩu hiện tại, email trùng…); lỗi khác → null. */
export function serverFieldErrors(err: unknown): Record<string, string> | null {
  if (err instanceof ApiError && err.status === 422 && Object.keys(err.fieldErrors).length) return err.fieldErrors;
  return null;
}

/** "Chrome · Windows", "Safari · iPhone" từ user-agent; không nhận ra → "Trình duyệt không rõ". */
export function describeDevice(ua: string | null | undefined): string {
  if (!ua) return 'Trình duyệt không rõ';
  const browser = /Edg\//.test(ua)
    ? 'Edge'
    : /OPR\//.test(ua)
      ? 'Opera'
      : /Firefox\//.test(ua)
        ? 'Firefox'
        : /Chrome\//.test(ua)
          ? 'Chrome'
          : /Safari\//.test(ua)
            ? 'Safari'
            : /curl|python|httpx/i.test(ua)
              ? 'Công cụ dòng lệnh'
              : null;
  const os = /iPhone/.test(ua)
    ? 'iPhone'
    : /iPad/.test(ua)
      ? 'iPad'
      : /Android/.test(ua)
        ? 'Android'
        : /Windows/.test(ua)
          ? 'Windows'
          : /Mac OS X|Macintosh/.test(ua)
            ? 'macOS'
            : /Linux/.test(ua)
              ? 'Linux'
              : null;
  if (!browser && !os) return 'Trình duyệt không rõ';
  return [browser, os].filter(Boolean).join(' · ');
}

export function isMobile(ua: string | null | undefined): boolean {
  return !!ua && /iPhone|Android.*Mobile|Mobile Safari/.test(ua);
}
