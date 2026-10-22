/**
 * Hook đọc/ghi cho 3 tab mới của Điều khiển hệ thống (Quyền hạn, Nhật ký, Dữ liệu & lưu trữ — PLAN 4.5) +
 * bước thiết lập 10–11 (PLAN 4.6). "Bộ não AI" dùng lại `useProviders`/`useCliProfiles` (`lib/dataQueries.ts`)
 * và `useFailoverRules` (`screens/api/queries.ts`) — không có hook riêng ở đây.
 */
import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type {
  AiBudgetBody,
  AiCost,
  AiPriceBody,
  BackgroundSources,
  BackgroundSourcesBody,
  Boundary,
  BoundaryPatchBody,
  DataRequestKind,
  PermissionPatchBody,
  RetentionPatchBody,
  SystemAuditQuery,
} from '@gen-harness/contracts';
import { api } from '../../lib/api';

export const qkSystem = {
  permissions: ['system', 'permissions'] as const,
  listeningGroups: ['system', 'listening-groups'] as const,
  boundaries: ['system', 'boundaries'] as const,
  auditLog: (q: SystemAuditQuery) => ['system', 'audit-log', q] as const,
  retention: ['system', 'retention'] as const,
  dataRequests: (personId: string) => ['system', 'data-requests', personId] as const,
  health: ['system', 'health'] as const,
  /** v0.1.46 (F-21): `GET /system/access` — địa chỉ đăng nhập + chế độ Truy cập từ xa. */
  access: ['system', 'access'] as const,
  /** v0.1.41 (F-84): `['system','ai-cost']` (+ ngày) — prefix dùng để làm mới mọi ngày. */
  aiCost: ['system', 'ai-cost'] as const,
  aiCostDay: (date: string) => ['system', 'ai-cost', date] as const,
  /** v0.1.41 (F-86): nguồn AI cho việc nền. */
  backgroundSources: ['providers', 'background'] as const,
};

/**
 * v0.1.36 (F-6): kind chuông là sự cố sức khoẻ — chuông nhận `notification.new` thuộc nhóm này thì làm mới
 * `['system','health']` để dải "Cần Sếp xử lý" và thẻ "Sức khoẻ hệ thống" đổi ngay, không chờ 60 giây.
 */
export const HEALTH_KINDS: ReadonlySet<string> = new Set([
  'channel.down', 'model.auth_expired', 'update.failed', 'backup.stale', 'worker.silent', 'disk.low', 'host.autostart',
  // v0.1.40 (F-12, F-2): bản sao ngoài máy quá hạn/lỗi; việc nền chạy quá giờ.
  'offsite.stale', 'offsite.failed', 'job.timeout',
  // v0.1.41 (F-84, F-86): vượt trần chi phí AI trong ngày; việc nền không còn nguồn AI nào.
  'ai.budget_exceeded', 'ai.background_no_source',
  // v0.1.46 (F-21): cổng đang mở cho cả mạng (bản cài cũ) — làm mới dải "Cần Sếp xử lý" ngay.
  'network.open_lan',
  // v0.1.49 (F-83): Gen-hub không trả lời hơn 15 phút (sự cố `hub.breaker`).
  'hub.unreachable',
  // v0.1.53 (F-99): lịch tự cập nhật đêm không chạy / Sếp đã tắt mà lịch vẫn bật.
  'host.nightly',
  // v0.1.62 (F051, F064): bộ nhận tin Zalo/WhatsApp im; tin khách nằm trong hàng lỗi.
  'bridge.silent', 'dlq.inbound',
]);

/** v0.1.36 (F-6): `GET /system/health` — chỉ gọi khi vai trò có `system.read` (`enabled`); tự hỏi lại mỗi 60 giây. */
export const useSystemHealth = (enabled: boolean) =>
  useQuery({
    queryKey: qkSystem.health,
    queryFn: ({ signal }) => api.systemHealth.get(signal),
    refetchInterval: 60_000,
    enabled,
  });

/** v0.1.46 (F-21): `GET /system/access` (`system.read`) — hộp mời và thẻ "Truy cập từ xa". */
export const useAccess = (opts?: { pollWhileLocalMs?: number }) =>
  useQuery({
    queryKey: qkSystem.access,
    queryFn: ({ signal }) => api.system.access(signal),
    staleTime: 30_000,
    // v0.1.46: hộp mời báo đỏ "địa chỉ chỉ mở được trên máy chủ" — Owner chạy `genh remote …` rồi quay lại; hỏi lại
    // định kỳ để cảnh báo tự tắt khi địa chỉ đã đổi (không có refetchOnWindowFocus).
    refetchInterval: opts?.pollWhileLocalMs ? (q) => (q.state.data?.public_url_local ? opts.pollWhileLocalMs : false) : undefined,
  });

export const usePermissions = () => useQuery({ queryKey: qkSystem.permissions, queryFn: ({ signal }) => api.permissions.get(signal) });

export const usePatchPermission = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PermissionPatchBody) => api.permissions.patch(body),
    onSuccess: (page) => qc.setQueryData(qkSystem.permissions, page),
  });
};

export const useListeningGroups = () =>
  useQuery({ queryKey: qkSystem.listeningGroups, queryFn: ({ signal }) => api.listeningGroups(signal) });

export const useBoundaries = () => useQuery({ queryKey: qkSystem.boundaries, queryFn: ({ signal }) => api.boundaries.list(signal) });

export const usePatchBoundary = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ code, body }: { code: string; body: BoundaryPatchBody }) => api.boundaries.patch(code, body),
    onSuccess: (row) => qc.setQueryData<Boundary[]>(qkSystem.boundaries, (old) => old?.map((b) => (b.code === row.code ? row : b))),
  });
};

/** Nhật ký phân trang bằng cursor (F099): mỗi trang 50 dòng, `fetchNextPage` = nút "Xem thêm"; bộ lọc/ô tìm chạy ở máy chủ. */
export const AUDIT_PAGE_SIZE = 50;
export const useSystemAuditLog = (q: Omit<SystemAuditQuery, 'cursor' | 'limit'>) =>
  useInfiniteQuery({
    queryKey: qkSystem.auditLog(q),
    queryFn: ({ pageParam, signal }) => api.systemAuditLog.list({ ...q, limit: AUDIT_PAGE_SIZE, cursor: pageParam ?? undefined }, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    placeholderData: keepPreviousData,
  });

export const useExportAuditLog = () => useMutation({ mutationFn: (q: Omit<SystemAuditQuery, 'cursor' | 'limit'>) => api.systemAuditLog.exportCsv(q) });

export const useRetentionPolicies = () => useQuery({ queryKey: qkSystem.retention, queryFn: ({ signal }) => api.retentionPolicies.list(signal) });

export const usePatchRetention = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: RetentionPatchBody) => api.retentionPolicies.patch(body),
    onSuccess: (list) => qc.setQueryData(qkSystem.retention, list),
  });
};

export const usePersonDataRequests = (personId: string, enabled: boolean) =>
  useQuery({ queryKey: qkSystem.dataRequests(personId), queryFn: ({ signal }) => api.personDataRequests.list(personId, signal), enabled });

export const useCreateDataRequest = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ personId, kind }: { personId: string; kind: DataRequestKind }) => api.personDataRequests.create(personId, kind),
    onSuccess: (_r, { personId }) => void qc.invalidateQueries({ queryKey: qkSystem.dataRequests(personId) }),
  });
};

/** v0.1.41 (F-84): `GET /system/ai-cost` (hôm nay khi `date` rỗng) — chỉ gọi khi có `system.read`. */
export const useAiCost = (enabled: boolean, date = '') =>
  useQuery({
    queryKey: qkSystem.aiCostDay(date),
    queryFn: ({ signal }) => api.aiCost.get(date || undefined, signal),
    refetchInterval: 5 * 60_000,
    enabled,
  });

/** Cùng dạng GET ⇒ ghi thẳng vào bộ đệm "hôm nay" và làm mới mọi ngày khác. */
function useAiCostWrite<V>(fn: (v: V) => Promise<AiCost>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (data) => {
      qc.setQueryData(qkSystem.aiCostDay(''), data);
      void qc.invalidateQueries({ queryKey: qkSystem.aiCost });
      // Vượt/hết vượt trần mở/đóng sự cố ai.budget_exceeded ⇒ dải "Cần Sếp xử lý" đổi ngay.
      void qc.invalidateQueries({ queryKey: qkSystem.health });
    },
  });
}

export const useSetAiBudget = () => useAiCostWrite((body: AiBudgetBody) => api.aiCost.setBudget(body));

export const useSetModelPrice = () =>
  useAiCostWrite(({ modelId, body }: { modelId: string; body: AiPriceBody }) => api.aiCost.setPrice(modelId, body));

/** v0.1.41 (F-86): `GET /providers/background` (system.read). */
export const useBackgroundSources = (enabled = true) =>
  useQuery({ queryKey: qkSystem.backgroundSources, queryFn: ({ signal }) => api.providers.background(signal), enabled });

/** Chỉ Owner; thêm CLI ⇒ 423 PIN (hộp PIN tự mở qua lib/api.ts rồi gửi lại) / 422 accept_risk. */
export const useSetBackgroundSources = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BackgroundSourcesBody) => api.providers.setBackground(body),
    onSuccess: (data: BackgroundSources) => {
      qc.setQueryData(qkSystem.backgroundSources, data);
      void qc.invalidateQueries({ queryKey: qkSystem.backgroundSources });
      void qc.invalidateQueries({ queryKey: qkSystem.health });
    },
  });
};

/**
 * v0.1.62 (F064/F102): "Phát lại" / "Đã xem, xoá" một hàng lỗi — `system.manage` + mã PIN (hộp PIN tự mở qua lib/api.ts rồi
 * gửi lại). Xong làm mới thẻ Sức khoẻ và dải "Cần Sếp xử lý" (sự cố `dlq.inbound` đóng ngay khi hàng hết).
 */
export const useReplayQueue = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (stream: string) => api.systemHealth.replayQueue(stream),
    onSettled: () => void qc.invalidateQueries({ queryKey: qkSystem.health }),
  });
};

export const useClearQueue = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (stream: string) => api.systemHealth.clearQueue(stream),
    onSettled: () => void qc.invalidateQueries({ queryKey: qkSystem.health }),
  });
};
