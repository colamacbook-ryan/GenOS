/**
 * Number and time formatting. The API returns raw numbers and UTC ISO times
 * (docs/api/phase-2.md); the web formats them in Vietnamese and in the org's
 * timezone: 18412 → "18.412", 0.94 → "0,94", 900 s → "15 phút".
 */

const LOCALE = 'vi-VN';
export const DEFAULT_TZ = 'Asia/Ho_Chi_Minh';

const intFmt = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 });

/** 18412 → "18.412"; null → "—". */
export function fmtInt(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return intFmt.format(Math.round(n));
}

/** 0.94 → "0,94" (fixed decimals). */
export function fmtDec(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return new Intl.NumberFormat(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n);
}

/** 99.9 → "99,9%"; digits = max fraction digits. */
export function fmtPct(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return `${new Intl.NumberFormat(LOCALE, { maximumFractionDigits: digits }).format(n)}%`;
}

/** 0.7 → "70%" (fraction → whole percent). */
export function fmtFractionPct(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return `${Math.round(n * 100)}%`;
}

/** 420 ms → "0,42s". */
export function fmtLatency(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return '—';
  return `${fmtDec(ms / 1000, 2)}s`;
}

/** Interval in seconds → "15 phút" / "90 giây" / "2 giờ". */
export function fmtInterval(seconds: number): string {
  if (seconds % 3600 === 0 && seconds >= 3600) return `${fmtInt(seconds / 3600)} giờ`;
  if (seconds % 60 === 0) return `${fmtInt(seconds / 60)} phút`;
  if (seconds > 60) return `${new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 1 }).format(seconds / 60)} phút`;
  return `${fmtInt(seconds)} giây`;
}

/** Minutes value for the pipeline strip ("15", "1,5"). */
export function minutesValue(seconds: number): string {
  return new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 1 }).format(seconds / 60);
}

function parts(iso: string | null | undefined, tz: string, opts: Intl.DateTimeFormatOptions): Record<string, string> | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  let fmt: Intl.DateTimeFormat;
  try {
    fmt = new Intl.DateTimeFormat('en-GB', { timeZone: tz, hourCycle: 'h23', ...opts });
  } catch {
    fmt = new Intl.DateTimeFormat('en-GB', { timeZone: DEFAULT_TZ, hourCycle: 'h23', ...opts });
  }
  const out: Record<string, string> = {};
  for (const p of fmt.formatToParts(d)) out[p.type] = p.value;
  return out;
}

/** "15:11:44" in the org timezone. */
export function fmtClock(iso: string | null | undefined, tz = DEFAULT_TZ): string {
  const p = parts(iso, tz, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  return p ? `${p.hour}:${p.minute}:${p.second}` : '—';
}

/** "15:00". */
export function fmtHM(iso: string | null | undefined, tz = DEFAULT_TZ): string {
  const p = parts(iso, tz, { hour: '2-digit', minute: '2-digit' });
  return p ? `${p.hour}:${p.minute}` : '—';
}

/** "12/09". */
export function fmtDM(iso: string | null | undefined, tz = DEFAULT_TZ): string {
  const p = parts(iso, tz, { day: '2-digit', month: '2-digit' });
  return p ? `${p.day}/${p.month}` : '—';
}

/** "21/09 15:11:44". */
export function fmtDMClock(iso: string | null | undefined, tz = DEFAULT_TZ): string {
  if (!iso) return '—';
  return `${fmtDM(iso, tz)} ${fmtClock(iso, tz)}`;
}

/** Same calendar day in the org timezone? */
export function sameDay(a: string | Date, b: string | Date, tz = DEFAULT_TZ): boolean {
  const pa = parts(new Date(a).toISOString(), tz, { year: 'numeric', month: '2-digit', day: '2-digit' });
  const pb = parts(new Date(b).toISOString(), tz, { year: 'numeric', month: '2-digit', day: '2-digit' });
  return !!pa && !!pb && pa.year === pb.year && pa.month === pb.month && pa.day === pb.day;
}

/** "2 phút trước", "3 giờ trước", "hôm qua", "12/09". */
export function fmtAgo(iso: string | null | undefined, now = Date.now(), tz = DEFAULT_TZ): string {
  if (!iso) return '—';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '—';
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return 'vừa xong';
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} phút trước`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} giờ trước`;
  if (h < 48) return 'hôm qua';
  return fmtDM(iso, tz);
}

/** Remaining time "4 phút 12 giây" / "41 giây" / "2 giờ 5 phút". */
export function fmtCountdown(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s} giây`;
  const m = Math.floor(s / 60);
  if (m < 60) return s % 60 ? `${m} phút ${s % 60} giây` : `${m} phút`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h} giờ ${m % 60} phút` : `${h} giờ`;
}

/** Session age "14 ngày 06:41" (days + hh:mm). */
export function fmtSessionAge(sinceIso: string | null | undefined, now = Date.now()): string {
  if (!sinceIso) return '—';
  const t = new Date(sinceIso).getTime();
  if (Number.isNaN(t)) return '—';
  const totalMin = Math.max(0, Math.floor((now - t) / 60000));
  const days = Math.floor(totalMin / 1440);
  const hh = String(Math.floor((totalMin % 1440) / 60)).padStart(2, '0');
  const mm = String(totalMin % 60).padStart(2, '0');
  return days > 0 ? `${days} ngày ${hh}:${mm}` : `${hh}:${mm}`;
}

/** Validity left "23 giờ", "2 ngày", "45 phút". */
export function fmtRemaining(untilIso: string | null | undefined, now = Date.now()): string {
  if (!untilIso) return '—';
  const ms = new Date(untilIso).getTime() - now;
  if (Number.isNaN(ms)) return '—';
  if (ms <= 0) return 'đã hết';
  const m = Math.floor(ms / 60000);
  if (m < 60) return `${Math.max(1, m)} phút`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} giờ`;
  return `${Math.floor(h / 24)} ngày`;
}

const WORDS = ['không', 'một', 'hai', 'ba', 'bốn', 'năm', 'sáu', 'bảy', 'tám', 'chín', 'mười'];

/** 3 → "Ba" (sentence start), 14 → "14". */
export function countWord(n: number, capital = true): string {
  const w = n >= 0 && n <= 10 ? WORDS[n] : fmtInt(n);
  return capital ? w.charAt(0).toLocaleUpperCase('vi') + w.slice(1) : w;
}

/** Two-letter avatar from an email ("a@example.test" → "A"). */
export function emailInitials(email: string | null | undefined): string {
  const local = (email ?? '').split('@')[0]?.replace(/[^\p{L}\p{N}]/gu, '') ?? '';
  return (local.slice(0, 2) || '·').toLocaleUpperCase('vi');
}

/**
 * F-38 (v0.1.43): định nghĩa DUY NHẤT (các *Model.ts chỉ re-export).
 * Chữ viết tắt avatar: chữ đầu của từ đầu + từ cuối ("Nguyễn Văn An" → "NA"), tối đa 2 ký tự; tên MỘT từ (tên Zalo hay
 * gặp) → 2 ký tự đầu ("Lan" → "LA", không phải "LL"); rỗng → "·".
 */
export function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 1) return [...parts[0]].slice(0, 2).join('').toLocaleUpperCase('vi');
  const last = parts.at(-1)?.[0] ?? '';
  const first = parts[0]?.[0] ?? '';
  return (first + last).toLocaleUpperCase('vi').slice(0, 2) || '·';
}

const vndFmt = new Intl.NumberFormat(LOCALE);

/** 1500000 → "1.500.000 ₫"; null/undefined → "—". */
export function fmtVnd(n: number | null | undefined): string {
  return n === null || n === undefined ? '—' : `${vndFmt.format(n)} ₫`;
}
