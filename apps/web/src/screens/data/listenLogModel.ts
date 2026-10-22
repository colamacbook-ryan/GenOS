/**
 * Nhật ký lắng nghe — phần logic thuần của màn (không React): bộ lọc → tham số API, nhóm theo ngày, nhãn/tông cho các
 * bước "hệ thống đã làm gì". Chữ tiếng Việt về sự kiện/quyết định do MÁY CHỦ viết sẵn (đã che theo quyền); ở đây chỉ
 * giữ chữ của giao diện.
 */
import type { ListenLogDecision, ListenLogItem, ListenLogKind, ListenLogQuery, RawState, Since } from '@gen-harness/contracts';
import { fmtDM, sameDay } from '../../lib/format';
import { BAD, N4, N5, OK, WARN, ACC3, rawStateLabel } from './dataModel';

export const LL_PAGE = 40;

/** Ghi chú đầu màn: vì sao có nhóm không thấy dòng tin nào. */
export const NOT_LISTENING_NOTE =
  'Nhóm ở chế độ «Không nghe», «Tạm dừng» hoặc «Chờ Sếp chọn» không lưu tin nên không có dòng tin nào của nhóm đó ở đây — chỉ có sự kiện (vào, rời, đổi tên). Tin riêng 1-1 chỉ Owner hoặc người phụ trách mới thấy.';

export const KIND_OPTIONS: Array<{ value: ListenLogKind; label: string }> = [
  { value: 'all', label: 'Tin và sự kiện' },
  { value: 'message', label: 'Chỉ tin' },
  { value: 'event', label: 'Chỉ sự kiện' },
];

export const SINCE_OPTIONS: Array<{ value: Since; label: string }> = [
  { value: '24h', label: '24 giờ' },
  { value: '7d', label: '7 ngày' },
  { value: '30d', label: '30 ngày' },
  { value: 'all', label: 'Tất cả' },
];

export interface LogFilters {
  channel: string;
  groupId: string;
  kind: ListenLogKind;
  since: Since;
  tagged: boolean;
  acted: boolean;
  q: string;
}

export const DEFAULT_FILTERS: LogFilters = { channel: '', groupId: '', kind: 'all', since: '7d', tagged: false, acted: false, q: '' };

/** Bộ lọc → tham số API (bỏ giá trị mặc định/rỗng). "Chỉ tin được tag/đã xử lý" tự loại sự kiện ở máy chủ. */
export function toQuery(f: LogFilters): ListenLogQuery {
  return {
    channel: f.channel || undefined,
    group_id: f.groupId || undefined,
    kind: f.kind === 'all' ? undefined : f.kind,
    since: f.since,
    tagged: f.tagged || undefined,
    acted: f.acted || undefined,
    q: f.q.trim() || undefined,
  };
}

/** Bộ lọc nào đang bật (để hiện nút "Xoá bộ lọc"). */
export function activeFilterCount(f: LogFilters): number {
  return [f.channel, f.groupId, f.kind !== 'all', f.since !== DEFAULT_FILTERS.since, f.tagged, f.acted, f.q.trim()].filter(Boolean).length;
}

/** Hai cờ "chỉ tin được tag" / "chỉ tin agent đã xử lý" không có sự kiện nào ⇒ ghi chú cho người dùng. */
export function eventsHiddenByFilters(f: LogFilters): boolean {
  return f.kind !== 'message' && (f.tagged || f.acted);
}

export interface DayGroup {
  key: string;
  label: string;
  items: ListenLogItem[];
}

function dayKey(iso: string, tz: string): string {
  const d = new Date(iso);
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit' }).format(d);
  return parts;
}

/** "Hôm nay · 17/10", "Hôm qua · 16/10", "Thứ Sáu · 10/10". */
export function dayLabel(iso: string, tz: string, now: Date = new Date()): string {
  const dm = fmtDM(iso, tz);
  if (sameDay(iso, now, tz)) return `Hôm nay · ${dm}`;
  if (sameDay(iso, new Date(now.getTime() - 86_400_000), tz)) return `Hôm qua · ${dm}`;
  const wd = new Intl.DateTimeFormat('vi-VN', { timeZone: tz, weekday: 'long' }).format(new Date(iso));
  return `${wd.charAt(0).toUpperCase()}${wd.slice(1)} · ${dm}`;
}

/** Nhóm các dòng (đã sắp mới → cũ) theo ngày địa phương của tổ chức. */
export function groupByDay(items: ListenLogItem[], tz: string, now: Date = new Date()): DayGroup[] {
  const out: DayGroup[] = [];
  for (const it of items) {
    const key = dayKey(it.at, tz);
    const last = out[out.length - 1];
    if (last && last.key === key) last.items.push(it);
    else out.push({ key, label: dayLabel(it.at, tz, now), items: [it] });
  }
  return out;
}

/** Tông màu (biến CSS) của một sự kiện theo `tone` máy chủ gửi. */
export function eventTone(tone: string): string {
  switch (tone) {
    case 'ok':
      return OK;
    case 'warn':
      return WARN;
    case 'bad':
      return BAD;
    case 'accent':
      return ACC3;
    default:
      return N4;
  }
}

/** Màu chấm của bước sàng lọc. */
export function screeningTone(state: RawState | string): string {
  switch (state) {
    case 'clean':
      return OK;
    case 'lowconf':
      return WARN;
    case 'error':
      return BAD;
    case 'pending':
    case 'processing':
      return ACC3;
    default:
      return N5;
  }
}

export function screeningText(state: RawState | string, label: string | null, confidence: number | null): string {
  const base = rawStateLabel(state);
  const parts = [base];
  if (label && label !== 'Noise') parts.push(label);
  if (confidence != null && state !== 'pending' && state !== 'processing') parts.push(`tin cậy ${confidence.toFixed(2).replace('.', ',')}`);
  return parts.join(' · ');
}

/** Tông bước agent: im lặng = xám; có việc thật = nhấn; nháp chờ duyệt = cảnh báo nhẹ; lỗi/loại = đỏ. */
export function decisionTone(d: ListenLogDecision): string {
  if (d.draft?.status === 'failed' || d.draft?.status === 'rejected') return BAD;
  if (d.decision === 'silent') return d.outcome === 'rejected' ? WARN : N5;
  if (d.draft?.status === 'pending') return WARN;
  return ACC3;
}

/** Đường dẫn bản nháp ở Bàn làm việc. */
export const draftHref = (id: string): string => `/workbench?id=${encodeURIComponent(id)}`;

/** Tên người gửi hiển thị: tên (đã che) → mã → "Không rõ". */
export function senderName(s: { name: string | null; code: string | null } | null): string {
  return s ? s.name || s.code || 'Không rõ người gửi' : 'Không rõ người gửi';
}

/**
 * v0.1.72: nhãn ngắn cạnh dòng sự kiện (chữ chính do máy chủ viết sẵn). Sự kiện của bot tài khoản riêng có nhãn riêng để
 * Sếp lướt nhanh; loại khác giữ «Sự kiện».
 */
export const EVENT_KIND_LABEL: Record<string, string> = {
  group_added_by_other: 'Người khác thêm bot',
  bot_removed: 'Bot bị mời ra',
  owner_called: 'Sếp gọi',
  owner_paired: 'Kênh quản trị',
  owner_unpaired: 'Kênh quản trị',
  support_on: 'Hỗ trợ',
  support_off: 'Hỗ trợ',
  support_escalated: 'Chuyển Sếp',
};
export const eventKindLabel = (t: string): string => EVENT_KIND_LABEL[t] ?? 'Sự kiện';

/** Tin bot tự gửi lọc theo kênh đang chọn (sổ `/auto-sends` không lọc kênh ở máy chủ). */
export function autoSendsForChannel<T extends { channel: string }>(items: T[], channel: string): T[] {
  return channel ? items.filter((i) => i.channel === channel) : items;
}
