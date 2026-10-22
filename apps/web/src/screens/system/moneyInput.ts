/** v0.1.41 (F-84): đọc/ghi ô tiền ₫ ở "Chi phí & trần ngân sách" (trần mỗi ngày, giá ₫/1M token). */

/**
 * Ô tiền ₫ → số. '' → null; không hợp lệ → undefined. Dấu chấm/phẩy theo nhóm ĐÚNG 3 chữ số là phân cách nghìn
 * ("12.500", "1,250,000"); `decimals > 0` thì MỘT dấu phẩy hoặc chấm theo sau 1–`decimals` chữ số là phần lẻ ("0,5", "2.5",
 * "1.250,75") — "0.5" không bao giờ bị hiểu thành 5.
 */
export function parseVnd(raw: string, decimals = 0): number | null | undefined {
  const t = raw.replace(/[\s₫]/g, '');
  if (t === '') return null;
  let intPart = t;
  let frac = '';
  const m = decimals > 0 ? new RegExp(`^(.*)[.,](\\d{1,${decimals}})$`).exec(t) : null;
  if (m) {
    intPart = m[1];
    frac = m[2];
  }
  if (intPart === '') return undefined;
  let digits: string;
  if (/^\d+$/.test(intPart)) digits = intPart;
  else if (/^[1-9]\d{0,2}(\.\d{3})+$/.test(intPart) || /^[1-9]\d{0,2}(,\d{3})+$/.test(intPart)) digits = intPart.replace(/[.,]/g, '');
  else return undefined;
  const n = Number(frac ? `${digits}.${frac}` : digits);
  return Number.isFinite(n) && Number.isSafeInteger(Math.trunc(n)) ? n : undefined;
}

/** Ô tiền → chuỗi trong ô sửa: null → ''; giữ phần lẻ (tối đa 2 số, dấu phẩy) — không làm tròn giá đã lưu. */
export const priceStr = (n: number | null) => {
  if (n == null) return '';
  const r = Math.round(n * 100) / 100;
  return Number.isInteger(r) ? String(r) : String(r).replace('.', ',');
};
