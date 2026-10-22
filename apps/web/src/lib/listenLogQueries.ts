import { keepPreviousData, useInfiniteQuery, useQuery, type QueryClient } from '@tanstack/react-query';
import type { ListenLogQuery } from '@gen-harness/contracts';
import { api } from './api';

/** Khoá truy vấn của Nhật ký lắng nghe — một gốc để làm mới cả danh sách lẫn số liệu đầu màn. */
export const qkLog = {
  root: ['listenlog'] as const,
  /** Gốc của MỌI truy vấn danh sách (không gồm số liệu 24 giờ) — tín hiệu WebSocket chỉ làm mới phần này. */
  listRoot: ['listenlog', 'list'] as const,
  list: (q: ListenLogQuery) => ['listenlog', 'list', q] as const,
  stats: ['listenlog', 'stats'] as const,
};

export const LOG_PAGE = 40;

export const useListenLog = (q: ListenLogQuery) =>
  useInfiniteQuery({
    queryKey: qkLog.list(q),
    queryFn: ({ pageParam, signal }) => api.listenLog.list({ ...q, limit: LOG_PAGE, cursor: pageParam ?? undefined }, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    placeholderData: keepPreviousData,
  });

export const useListenLogStats = () => useQuery({ queryKey: qkLog.stats, queryFn: ({ signal }) => api.listenLog.stats(signal), refetchInterval: 120_000 });

// ── cập nhật trực tiếp ────────────────────────────────────────────────────

/** Sự kiện WebSocket báo "có gì mới cho Nhật ký" (tin mới, tin đổi trạng thái sàng lọc, agent vừa quyết định, sự kiện kênh). */
export const LISTEN_LOG_SIGNALS: ReadonlySet<string> = new Set(['raw.new', 'raw.state', 'listen.event', 'agent.decision', 'draft.new', 'draft.updated']);

const TRAILING_MS = 700;
const MAX_WAIT_MS = 3000;
let timer: ReturnType<typeof setTimeout> | null = null;
let firstAt = 0;
let live = true;

/**
 * Công tắc "Cập nhật trực tiếp" của màn (tắt ⇒ tín hiệu bị bỏ, người dùng bấm Làm mới).
 * Truyền `qc` khi người dùng BẬT LẠI sau lúc tạm dừng ⇒ tải bù ngay một lần để tin đến lúc tạm dừng hiện ra
 * (không tải khi đã bật sẵn, hay khi màn đóng — lúc đó không có `qc`).
 */
export function setListenLogLive(on: boolean, qc?: QueryClient): void {
  const resumed = on && !live;
  live = on;
  if (resumed && qc) void qc.invalidateQueries({ queryKey: qkLog.listRoot });
}
export function isListenLogLive(): boolean {
  return live;
}

/**
 * Gom nhiều tín hiệu liên tiếp thành MỘT lần tải lại (sàng lọc bắn cả trăm `raw.state` một lượt): chờ 0,7 giây sau tín
 * hiệu cuối, tối đa 3 giây kể từ tín hiệu đầu. Chỉ tải khi màn đang mở (có truy vấn nhật ký trong bộ nhớ đệm).
 * Danh sách luôn tải lại qua API ⇒ phạm vi quyền và việc che số vẫn do máy chủ quyết, WebSocket chỉ là cái chuông.
 */
export function scheduleListenLogRefresh(qc: QueryClient): void {
  if (!live) return;
  if (qc.getQueryCache().findAll({ queryKey: qkLog.root }).length === 0) return;
  const now = Date.now();
  if (timer === null) firstAt = now;
  else clearTimeout(timer);
  const wait = Math.max(0, Math.min(TRAILING_MS, firstAt + MAX_WAIT_MS - now));
  timer = setTimeout(() => {
    timer = null;
    // Chỉ danh sách: số liệu 24 giờ đầu màn tự làm mới theo chu kỳ riêng (2 phút) và nút «Làm mới» — không kéo theo
    // mỗi nhịp WebSocket (sàng lọc bắn cả trăm tín hiệu).
    void qc.invalidateQueries({ queryKey: qkLog.listRoot });
  }, wait);
}

/** Chỉ cho test. */
export function resetListenLogRefresh(): void {
  if (timer) clearTimeout(timer);
  timer = null;
  live = true;
}
