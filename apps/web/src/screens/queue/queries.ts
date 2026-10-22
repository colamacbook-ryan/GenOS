import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { InboxItem, InboxPage, InboxTab, Task, TaskPage, TriageSettings, TaskCreateBody, TaskPatchBody, TaskPriority, TaskStatus } from '@gen-harness/contracts';
import { api } from '../../lib/api';
import { onRealtimeEvent } from '../../lib/realtime';

/** Khoá truy vấn của cụm Hàng đợi & Hành động. */
export const qkQueue = {
  overview: ['queue', 'overview'] as const,
  inbox: (tab: InboxTab, intent?: string, hideJunk = false) => ['queue', 'inbox', tab, intent ?? '', hideJunk] as const,
  inboxItem: (id: string) => ['queue', 'inbox', 'one', id] as const,
  tasks: (q: Record<string, string | boolean | undefined>) => ['queue', 'tasks', JSON.stringify(q)] as const,
  /** Danh sách việc phân trang (màn Việc & Nhắc hẹn) — khoá riêng vì dữ liệu là nhiều trang, khác `tasks` (một trang). */
  taskList: (q: Record<string, string | boolean | undefined>) => ['queue', 'tasks', 'list', JSON.stringify(q)] as const,
  task: (id: string) => ['queue', 'tasks', 'one', id] as const,
  promises: (status: string) => ['queue', 'promises', status] as const,
  triageSettings: ['queue', 'triage', 'settings'] as const,
  triageSummary: (days: number) => ['queue', 'triage', 'summary', days] as const,
};

export const useOverview = () =>
  useQuery({
    queryKey: qkQueue.overview,
    queryFn: ({ signal }) => api.queue.overview(signal),
  });

/** Số dòng mỗi lần tải ở Hộp thư và Việc; "Xem thêm" nối trang kế theo `next_cursor` của máy chủ. */
export const QUEUE_PAGE = 50;

/** Gộp các trang đã tải thành một danh sách (bỏ dòng trùng id nếu hai trang chạm nhau). */
function mergePages<T extends { id: string }>(pages: { items: T[] }[]): T[] {
  const seen = new Set<string>();
  const out: T[] = [];
  for (const p of pages) {
    for (const it of p.items) {
      if (seen.has(it.id)) continue;
      seen.add(it.id);
      out.push(it);
    }
  }
  return out;
}

export interface InboxView {
  /** Mọi dòng đã tải (các trang gộp lại, mới nhất trước). */
  items: InboxItem[];
  /** Số theo tab + trạng thái lọc đầu: lấy ở trang tải gần nhất. */
  counts: InboxPage['counts'];
  triage: InboxPage['triage'];
}

export const useInbox = (tab: InboxTab, intent?: string, hideJunk = false) =>
  useInfiniteQuery({
    queryKey: qkQueue.inbox(tab, intent, hideJunk),
    queryFn: ({ pageParam, signal }) =>
      api.queue.inbox.list(
        { tab, intent: intent || undefined, hide_junk: hideJunk || undefined, limit: QUEUE_PAGE, cursor: pageParam ?? undefined },
        signal,
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    select: (d): InboxView => {
      const last = d.pages[d.pages.length - 1];
      return { items: mergePages(d.pages), counts: last.counts, triage: last.triage };
    },
  });

/** Lọc đầu Hộp thư (v0.1.25) — cấu hình đọc được cho mọi người; chỉ Owner sửa (server kiểm). */
export const useTriageSettings = () =>
  useQuery({ queryKey: qkQueue.triageSettings, queryFn: ({ signal }) => api.queue.triage.settings(signal) });

export const useTriageSummary = (days = 7, enabled = true) =>
  useQuery({
    queryKey: qkQueue.triageSummary(days),
    queryFn: ({ signal }) => api.queue.triage.summary(days, signal),
    enabled,
  });

export const useSetTriageSettings = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<TriageSettings>) => api.queue.triage.setSettings(body),
    onSuccess: (data) => {
      qc.setQueryData(qkQueue.triageSettings, data);
      void qc.invalidateQueries({ queryKey: ['queue', 'triage', 'summary'] });
      void qc.invalidateQueries({ queryKey: ['queue', 'inbox'] });
    },
  });
};

export const useInboxItem = (id: string | null) =>
  useQuery({
    queryKey: qkQueue.inboxItem(id ?? ''),
    queryFn: ({ signal }) => api.queue.inbox.get(id as string, signal),
    enabled: !!id,
  });

function invalidateInbox(qc: ReturnType<typeof useQueryClient>) {
  void qc.invalidateQueries({ queryKey: ['queue', 'inbox'] });
  void qc.invalidateQueries({ queryKey: qkQueue.overview });
}

export const useInboxAct = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: { text?: string; create_task?: boolean } }) => api.queue.inbox.act(id, body),
    onSuccess: () => invalidateInbox(qc),
  });
};

export const useInboxAssign = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, userId }: { id: string; userId: string }) => api.queue.inbox.assign(id, userId),
    onSuccess: () => invalidateInbox(qc),
  });
};

export const useInboxSilence = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason, until }: { id: string; reason?: string | null; until?: string | null }) =>
      api.queue.inbox.silence(id, { reason, until }),
    onSuccess: () => invalidateInbox(qc),
  });
};

export interface TaskFilters {
  status?: TaskStatus;
  priority?: TaskPriority;
  overdue?: boolean;
  /** ISO — chỉ việc CÓ hạn và hạn < mốc này (lọc ở máy chủ). */
  due_before?: string;
  limit?: number;
}

export const useTasks = (filters: TaskFilters) =>
  useQuery({
    queryKey: qkQueue.tasks(filters as Record<string, string | boolean | undefined>),
    queryFn: ({ signal }) => api.queue.tasks.list(filters, signal),
  });

export interface TaskListView {
  items: Task[];
  /** Tổng số việc khớp bộ lọc ở máy chủ (không phải số đã tải). */
  total: number;
}

/** Danh sách việc phân trang cho màn Việc & Nhắc hẹn. (`useTasks` giữ một trang cho "Việc hôm nay" của Mặt tiền.) */
export const useTaskList = (filters: TaskFilters) =>
  useInfiniteQuery({
    queryKey: qkQueue.taskList(filters as Record<string, string | boolean | undefined>),
    queryFn: ({ pageParam, signal }) =>
      api.queue.tasks.list({ ...filters, limit: filters.limit ?? QUEUE_PAGE, cursor: pageParam ?? undefined }, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (last: TaskPage) => last.next_cursor,
    select: (d): TaskListView => ({ items: mergePages(d.pages), total: d.pages[d.pages.length - 1].total }),
  });

export const useTask = (id: string | null) =>
  useQuery({
    queryKey: qkQueue.task(id ?? ''),
    queryFn: ({ signal }) => api.queue.tasks.get(id as string, signal),
    enabled: !!id,
  });

function invalidateTasks(qc: ReturnType<typeof useQueryClient>) {
  void qc.invalidateQueries({ queryKey: ['queue', 'tasks'] });
  void qc.invalidateQueries({ queryKey: qkQueue.overview });
}

export const useCreateTask = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: TaskCreateBody) => api.queue.tasks.create(body),
    onSuccess: () => invalidateTasks(qc),
  });
};

export const useUpdateTask = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: TaskPatchBody }) => api.queue.tasks.update(id, body),
    onSuccess: () => invalidateTasks(qc),
  });
};

export const usePromises = (status: 'upcoming' | 'overdue' | 'kept' | 'all', enabled = true) =>
  useQuery({
    queryKey: qkQueue.promises(status),
    queryFn: ({ signal }) => api.queue.tasks.promises.list({ status }, signal),
    enabled,
  });

export const useKeepPromise = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, kept }: { id: string; kept: boolean }) => api.queue.tasks.promises.keep(id, kept),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['queue', 'promises'] });
      void qc.invalidateQueries({ queryKey: qkQueue.overview });
    },
  });
};

// Realtime: cảnh báo mới → tải lại Hộp thư và Tổng quan (WS chỉ báo "có cảnh báo mới", không kèm dữ liệu).
onRealtimeEvent('alert.new', (qc) => invalidateInbox(qc));
