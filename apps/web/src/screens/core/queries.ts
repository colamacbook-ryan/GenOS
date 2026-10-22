import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import type { DraftItem, DraftPage, DraftUpdatedEvent } from '@gen-harness/contracts';
import { api } from '../../lib/api';
import { qk } from '../../lib/queries';
import { onRealtimeEvent } from '../../lib/realtime';

/** Khoá truy vấn nền chung giai đoạn 3. Các cụm màn dùng lại, không tự đặt khoá trùng. */
export const qk3 = {
  explain: (kind: string, id: string) => ['explain', kind, id] as const,
  raw: (id: string) => ['explain', 'raw', id] as const,
  views: (screen?: string) => ['views', screen ?? '*'] as const,
  drafts: ['drafts'] as const,
  draftList: (status: string, kind?: string, hideSilenced = false) => ['drafts', 'list', status, kind ?? '', ...(hideSilenced ? ['hide-silenced'] : [])] as const,
  /** Danh sách phân trang của Bàn làm việc (v0.1.61): nằm dưới `drafts` nên mọi sự kiện nháp đều làm mới. */
  draftPages: (status: string, kind?: string) => ['drafts', 'pages', status, kind ?? ''] as const,
  draft: (id: string) => ['drafts', 'one', id] as const,
};

export const useExplain = (kind: string, id: string, enabled = true) =>
  useQuery({
    queryKey: qk3.explain(kind, id),
    queryFn: ({ signal }) => api.explain.get(kind, id, signal),
    enabled,
    staleTime: 30_000,
  });

export const useRawQuote = (rawId: string | null) =>
  useQuery({
    queryKey: qk3.raw(rawId ?? ''),
    queryFn: ({ signal }) => api.explain.raw(rawId as string, signal),
    enabled: !!rawId,
    staleTime: 5 * 60_000,
  });

export const useViews = (screen: string | undefined, enabled = true) =>
  useQuery({
    queryKey: qk3.views(screen),
    queryFn: ({ signal }) => api.views.list(screen, signal),
    enabled,
  });

/** `hideSilenced`: bỏ nháp đã "Tắt tiếng" — Mặt tiền Owner dùng để số nháp khớp `today.nav.decide` (Bàn làm việc của Console vẫn liệt kê hết). */
export const useDrafts = (status: 'pending' | 'decided' | 'all' = 'pending', kind?: string, opts: { hideSilenced?: boolean } = {}) =>
  useQuery({
    queryKey: qk3.draftList(status, kind, opts.hideSilenced === true),
    queryFn: ({ signal }) => api.drafts.list({ status, kind, hide_silenced: opts.hideSilenced ? true : undefined }, signal),
  });

/** Danh sách nháp theo trang (con trỏ `next_cursor`) — Bàn làm việc gộp các trang đã tải, nút "Tải thêm" lấy trang kế. */
export const useDraftsPaged = (status: 'pending' | 'decided' | 'failed' | 'all' = 'pending', kind?: string) =>
  useInfiniteQuery({
    queryKey: qk3.draftPages(status, kind),
    queryFn: ({ pageParam, signal }) => api.drafts.list({ status, kind, cursor: pageParam, limit: 50 }, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last: DraftPage) => last.next_cursor ?? undefined,
  });

export const useDraft = (id: string | null) =>
  useQuery({
    queryKey: qk3.draft(id ?? ''),
    queryFn: ({ signal }) => api.drafts.get(id as string, signal),
    enabled: !!id,
  });

// Realtime: bản nháp mới / đổi trạng thái → cập nhật danh sách chờ và badge danh mục.
onRealtimeEvent('draft.new', (qc, data) => {
  const item = data as DraftItem;
  qc.setQueryData<DraftPage>(qk3.draftList('pending'), (p) =>
    p && !p.items.some((i) => i.id === item.id) ? { ...p, items: [item, ...p.items], total: p.total + 1 } : p,
  );
  void qc.invalidateQueries({ queryKey: qk3.drafts });
  void qc.invalidateQueries({ queryKey: qk.navigation });
});
onRealtimeEvent('draft.updated', (qc, data) => {
  const ev = data as DraftUpdatedEvent;
  void qc.invalidateQueries({ queryKey: qk3.drafts });
  void qc.invalidateQueries({ queryKey: qk3.draft(ev.id) });
  void qc.invalidateQueries({ queryKey: qk.navigation });
});
// Gửi lỗi (v0.1.61, F048): chuông đã do `notification.new` lo; ở đây chỉ làm mới để thẻ "Gửi lỗi" thấy ngay.
onRealtimeEvent('draft.failed', (qc, data) => {
  const ev = data as { id?: string };
  void qc.invalidateQueries({ queryKey: qk3.drafts });
  if (ev.id) void qc.invalidateQueries({ queryKey: qk3.draft(ev.id) });
  void qc.invalidateQueries({ queryKey: qk.navigation });
});
