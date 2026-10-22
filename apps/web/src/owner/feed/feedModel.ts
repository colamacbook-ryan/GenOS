/**
 * v0.1.59 (G4) — màn Thông tin (`/owner/thong-tin`): hàm THUẦN định dạng + truy vấn chi tiết của một tin. Không React.
 *
 * Mức quan trọng (`importance`) do máy chủ tự tính (KHÔNG theo `priority` của Hộp thư); ở đây chỉ chọn màu/chữ để hiện. Mọi chuỗi từ máy
 * chủ đi qua `asText` (không bao giờ render object). Chữ đời thường — không có thuật ngữ kỹ thuật.
 */
import { useQuery } from '@tanstack/react-query';
import type { FeedCounts, FeedItem, FeedView, InboxDetail } from '@gen-harness/contracts';
import { api } from '../../lib/api';
import { DEFAULT_TZ, fmtDM, fmtHM, sameDay } from '../../lib/format';
import { asText } from '../ownerModel';
import { useIsOwnerRole } from '../queries';

export interface FeedViewDef {
  view: FeedView;
  label: string;
  countKey: keyof FeedCounts;
}

/** 5 mục lọc của danh sách — thứ tự cố định, "Quan trọng" mặc định. */
export const FEED_VIEWS: readonly FeedViewDef[] = [
  { view: 'important', label: 'Quan trọng', countKey: 'important' },
  { view: 'all', label: 'Tất cả', countKey: 'all' },
  { view: 'opportunity', label: 'Cơ hội', countKey: 'opportunity' },
  { view: 'alert', label: 'Cảnh báo', countKey: 'alert' },
  { view: 'approval', label: 'Chờ duyệt', countKey: 'approval' },
];

export function isFeedView(v: unknown): v is FeedView {
  return FEED_VIEWS.some((d) => d.view === v);
}

/** Số đếm an toàn: không phải số / âm ⇒ 0. */
export function safeCount(n: unknown): number {
  return typeof n === 'number' && Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

export interface Headline {
  /** Số lớn (28px): tin cần Sếp để ý = Khẩn + Quan trọng. */
  total: number;
  /** "1 Khẩn, 6 Quan trọng" — bỏ phần bằng 0; rỗng khi không có tin nào. */
  breakdown: string;
}

/** Đầu danh sách: "7 tin cần Sếp để ý · 1 Khẩn, 6 Quan trọng". `urgent_open` ⊂ `important` ⇒ Quan trọng = important − urgent_open. */
export function headline(counts: Partial<FeedCounts> | null | undefined): Headline {
  const total = safeCount(counts?.important);
  const urgent = Math.min(safeCount(counts?.urgent_open), total);
  const normal = total - urgent;
  const parts = [urgent > 0 ? `${urgent} Khẩn` : '', normal > 0 ? `${normal} Quan trọng` : ''].filter(Boolean);
  return { total, breakdown: parts.join(', ') };
}

const CHANNEL_LABEL: Record<string, string> = { zalo: 'Zalo', whatsapp: 'WhatsApp', telegram: 'Telegram', linkedin: 'LinkedIn' };

/** Tên kênh đời thường; kênh lạ ⇒ rỗng (không đoán). */
export function channelLabel(channel: unknown): string {
  return CHANNEL_LABEL[asText(channel).toLowerCase()] ?? '';
}

/** "Hoàng Thị E · Nhóm Khách sỉ · Zalo" — tin 1-1 ghi "Nhắn riêng"; cảnh báo/nháp không nhóm thì bỏ phần nhóm. */
export function rowMeta(item: Pick<FeedItem, 'person' | 'group' | 'item_type'>): string {
  const person = asText(item.person?.name);
  const group = item.group ? asText(item.group.name) : item.item_type === 'unit' ? 'Nhắn riêng' : '';
  return [person, group, channelLabel(item.group?.channel)].filter(Boolean).join(' · ');
}

/** Giờ của dòng tin: hôm nay "08:42" · hôm qua "Hôm qua" · cũ hơn "11/10". Thiếu giờ ⇒ rỗng. */
export function rowTime(iso: unknown, now: number = Date.now(), tz: string = DEFAULT_TZ): string {
  const s = asText(iso);
  if (!s || Number.isNaN(Date.parse(s))) return '';
  if (sameDay(s, new Date(now), tz)) return fmtHM(s, tz);
  if (sameDay(s, new Date(now - 24 * 3_600_000), tz)) return 'Hôm qua';
  return fmtDM(s, tz);
}

/** Dòng "thời điểm" ở đầu khung chi tiết: "hôm nay 08:42" · "hôm qua 21:10" · "11/10 08:42". */
export function whenText(iso: unknown, now: number = Date.now(), tz: string = DEFAULT_TZ): string {
  const s = asText(iso);
  if (!s || Number.isNaN(Date.parse(s))) return '';
  const hm = fmtHM(s, tz);
  if (sameDay(s, new Date(now), tz)) return `hôm nay ${hm}`;
  if (sameDay(s, new Date(now - 24 * 3_600_000), tz)) return `hôm qua ${hm}`;
  return `${fmtDM(s, tz)} ${hm}`;
}

/** Gộp các trang của danh sách phân trang, bỏ dòng trùng mã (làm tươi giữa chừng có thể dịch trang). */
export function mergePages(pages: ReadonlyArray<{ items: readonly FeedItem[] }> | undefined): FeedItem[] {
  const seen = new Set<string>();
  const out: FeedItem[] = [];
  for (const p of pages ?? []) {
    for (const it of Array.isArray(p.items) ? p.items : []) {
      const id = asText(it?.id);
      if (!id || seen.has(id)) continue;
      seen.add(id);
      out.push(it);
    }
  }
  return out;
}

/** Danh sách rỗng ở từng mục lọc — MỘT câu (mục "Quan trọng" kèm số tin Gen đã lọc). */
export function emptyText(view: FeedView, filtered: number): string {
  switch (view) {
    case 'important':
      return filtered > 0 ? `Không có tin cần Sếp để ý. Gen đã lọc ${filtered} tin.` : 'Không có tin cần Sếp để ý.';
    case 'opportunity':
      return 'Chưa có cơ hội mới trong 7 ngày qua.';
    case 'alert':
      return 'Không có cảnh báo nào đang mở.';
    case 'approval':
      return 'Không có bản nháp nào chờ Sếp duyệt.';
    default:
      return 'Chưa có tin nào trong 7 ngày qua.';
  }
}

/** Chân danh sách: "Gen đã lọc 124 tin rác/trùng trong 7 ngày" — không có gì đã lọc thì không hiện. */
export function filteredText(filtered: unknown): string | null {
  const n = safeCount(filtered);
  return n > 0 ? `Gen đã lọc ${n} tin rác/trùng trong 7 ngày` : null;
}

/** Đường dẫn sang hồ sơ khách ở màn Quan hệ. */
export function profileLink(personId: string): string {
  return `/owner/quan-he?nguoi=${encodeURIComponent(personId)}`;
}

/** Thông tin đầu khung chi tiết, lấy từ dòng tin (nếu có) hoặc từ chi tiết Hộp thư (mở thẳng bằng `?tin=`). */
export interface DetailHead {
  itemType: FeedItem['item_type'];
  title: string;
  /** Mức (Khẩn / Quan trọng / Thường) — chỉ khi mở từ dòng tin trong danh sách; mở thẳng bằng `?tin=` thì rỗng (không đoán từ `priority`). */
  importance: string;
  summary: string;
  at: string | null;
  personId: string | null;
  personName: string;
  groupName: string;
  channel: string;
  suggestedAction: string;
}

export function detailHead(item: FeedItem | undefined, detail: InboxDetail | undefined): DetailHead | null {
  if (item) {
    return {
      itemType: item.item_type,
      title: asText(item.title),
      importance: asText(item.importance),
      summary: asText(item.summary),
      at: item.created_at ?? null,
      personId: item.person ? asText(item.person.id) || null : null,
      personName: asText(item.person?.name),
      groupName: asText(item.group?.name),
      channel: channelLabel(item.group?.channel),
      suggestedAction: asText(item.suggested_action) || asText(detail?.suggested_action),
    };
  }
  if (!detail) return null;
  const subject = detail.subject as { id?: unknown; name?: unknown; channel?: unknown } | null;
  // Người (PersonRef) khác nhóm (GroupRef): nhóm có `channel`, người thì không.
  const subjectIsGroup = !!subject && subject.channel !== undefined;
  const group = detail.group ?? (subjectIsGroup ? (subject as unknown as InboxDetail['group']) : null);
  const person = subject && !subjectIsGroup ? subject : null;
  return {
    itemType: detail.item_type,
    title: asText(detail.title),
    importance: '',
    summary: asText(detail.summary),
    at: detail.created_at ?? null,
    personId: person ? asText(person.id) || null : null,
    personName: asText(person?.name),
    groupName: asText(group?.name),
    channel: channelLabel(group?.channel),
    suggestedAction: asText(detail.suggested_action),
  };
}

/** Dòng phụ ở đầu khung chi tiết: "Nhóm Khách sỉ · Zalo · hôm nay 08:42". */
export function headSubtitle(h: DetailHead, now: number = Date.now()): string {
  return [h.groupName || (h.itemType === 'unit' ? 'Nhắn riêng' : ''), h.channel, whenText(h.at, now)].filter(Boolean).join(' · ');
}

/** Chứng cứ Gen trích: câu kết luận của từng ý, bỏ câu rỗng/trùng, tối đa 6. */
export function evidenceLines(detail: Pick<InboxDetail, 'units'> | undefined): string[] {
  const out: string[] = [];
  for (const u of Array.isArray(detail?.units) ? detail.units : []) {
    const t = asText(u?.conclusion).trim();
    if (t && !out.includes(t)) out.push(t);
  }
  return out.slice(0, 6);
}

/** Câu báo sau khi Sếp xử lý xong nháp ở khung chi tiết. */
export function draftDoneText(status: unknown): string {
  switch (asText(status)) {
    case 'rejected':
      return 'Đã từ chối bản nháp trả lời.';
    case 'failed':
      return 'Bản nháp chưa gửi được — Sếp mở Bàn làm việc (Console) để xem lỗi và thử lại.';
    default:
      return 'Đã duyệt và gửi bản nháp trả lời.';
  }
}

// ── truy vấn chi tiết ───────────────────────────────────────────────────────────────────────────────────────────

/**
 * Chi tiết Hộp thư của một tin (`GET /inbox/{id}`): chứng cứ Gen trích + gợi ý. Khoá bắt đầu `['owner', …]` nên sau mỗi lần ghi
 * (`invalidateAfterWrite`) tự tải lại. `enabled=false` (nháp chờ duyệt không có chứng cứ) ⇒ không gọi.
 */
export function useOwnerInboxDetail(id: string | null, enabled = true) {
  const owner = useIsOwnerRole();
  return useQuery({
    queryKey: ['owner', 'inbox', id ?? ''] as const,
    queryFn: ({ signal }) => api.queue.inbox.get(id as string, signal),
    enabled: owner && !!id && enabled,
  });
}
