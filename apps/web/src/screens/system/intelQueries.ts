import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { PersonType } from '@gen-harness/contracts';
import { apiClient } from '../../lib/api';
import { api } from '../../lib/api';
import type { IntelBudget, IntelConsole, IntelMode, IntelRamp, IntelSetup, WrongReason } from './intelModel';

/** v0.1.76 (T11): thẻ "Tình báo". Thao tác cần PIN ⇒ máy chủ trả 423 ⇒ hộp PIN chung tự mở rồi gửi lại (lib/api.ts). */
export const qkIntel = {
  console: ['intel', 'console'] as const,
  budget: ['intel', 'budget'] as const,
  setup: ['intel', 'setup'] as const,
};

const req = <T>(path: string, method: 'GET' | 'POST' | 'PUT' | 'DELETE' = 'GET', body?: unknown, signal?: AbortSignal) =>
  apiClient.request<T>(path, { method, ...(body !== undefined ? { body } : {}), ...(signal ? { signal } : {}) });

/** Phản hồi sai khuôn (máy chủ cũ, proxy trả trang khác…) ⇒ lỗi tại chỗ, không để thẻ vỡ khi đọc trường thiếu. */
function isConsole(v: unknown): v is IntelConsole {
  const o = v as Partial<IntelConsole> | null;
  return !!o && typeof o === 'object' && !!o.assistant && !!o.outbound && !!o.backup_channel && Array.isArray(o.mutes) && Array.isArray(o.payees) && Array.isArray(o.groups) && Array.isArray(o.contexts) &&
    o.payees.every((p) => typeof p?.title === 'string' && typeof p?.signal_id === 'string') &&
    o.groups.every((g) => typeof g?.name === 'string' && typeof g?.id === 'string' && Array.isArray(g?.labels));
}

export const useIntelConsole = (enabled: boolean) =>
  useQuery({
    queryKey: qkIntel.console,
    queryFn: async ({ signal }) => {
      const v = await req<unknown>('/intel/console', 'GET', undefined, signal);
      if (!isConsole(v)) throw new Error('Máy chủ trả dữ liệu Tình báo sai khuôn');
      return v;
    },
    enabled,
    refetchInterval: 60_000,
  });

function useIntelWrite<V, R = unknown>(fn: (v: V) => Promise<R>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qkIntel.console });
      void qc.invalidateQueries({ queryKey: qkIntel.setup });
      void qc.invalidateQueries({ queryKey: ['system', 'health'] });
    },
  });
}

export const useLockAssistant = () => useIntelWrite(() => req('/intel/lock-assistant', 'POST', {}));
export const useUnlockAssistant = () => useIntelWrite(() => req('/intel/unlock-assistant', 'POST', {}));
export const useUnlockToday = () => useIntelWrite(() => req('/intel/unlock-today', 'POST', {}));
export const useIntelSettings = () =>
  useIntelWrite((b: { mode?: IntelMode; ramp_gates?: IntelRamp }) => req('/intel/settings', 'PUT', b));
export const useVerifyPayee = () => useIntelWrite((signalId: string) => req(`/intel/signals/${encodeURIComponent(signalId)}/verify-payee`, 'POST', {}));
export const useMuteKind = () =>
  useIntelWrite((kind: string) => req('/intel/mutes', 'POST', { scope_type: 'kind', scope_value: kind }));
export const useUnmute = () => useIntelWrite((id: string) => req(`/intel/mutes/${encodeURIComponent(id)}`, 'DELETE'));
export const useBulkContexts = () =>
  useIntelWrite((b: { group_ids: string[]; labels: Array<{ context: string; is_primary: boolean }> }) => req('/intel/group-contexts/bulk', 'POST', b));

function isSetup(v: unknown): v is IntelSetup {
  const o = v as Partial<IntelSetup> | null;
  return !!o && typeof o === 'object' && typeof o.ready === 'boolean' && !!o.groups && !!o.staff && Array.isArray(o.groups.unlabeled) &&
    Array.isArray(o.staff.people) && Array.isArray(o.contexts) && Array.isArray(o.gated_kinds) &&
    o.groups.unlabeled.every((g) => typeof g?.id === 'string' && typeof g?.name === 'string') &&
    o.staff.people.every((p) => typeof p?.id === 'string' && typeof p?.name === 'string') &&
    (o.key_customers === undefined ||
      (Array.isArray(o.key_customers.customers) && o.key_customers.customers.every((c) => typeof c?.id === 'string' && typeof c?.entity_id === 'string' && typeof c?.name === 'string')));
}

/** v0.1.76 (T14, D3): trạng thái "Thiết lập Tình báo" — chỉ Owner (máy chủ trả 403 với vai khác). */
export const useIntelSetup = (enabled: boolean) =>
  useQuery({
    queryKey: qkIntel.setup,
    queryFn: async ({ signal }) => {
      const v = await req<unknown>('/intel/setup', 'GET', undefined, signal);
      if (!isSetup(v)) throw new Error('Máy chủ trả dữ liệu Thiết lập Tình báo sai khuôn');
      return v;
    },
    enabled,
  });
export const useConfirmKeyCustomers = () => useIntelWrite(() => req('/intel/setup/key-customers-confirmed', 'POST', {}));
/** v0.1.77 (R2): đánh dấu / bỏ đánh dấu khách chủ chốt — đúng đường Hồ sơ sẵn có (`POST /me/profile/items` mục `key_relations`, `DELETE
 * /me/profile/items/{id}`; Owner + mã PIN: 423 ⇒ hộp PIN chung tự mở rồi gửi lại). Trợ lý chỉ gợi ý tên; chỉ Sếp tích mới thành `owner_confirmed`. */
export const useSetKeyCustomer = () =>
  useIntelWrite(({ entityId, factId }: { entityId: string; factId: string | null }) =>
    factId ? req(`/me/profile/items/${encodeURIComponent(factId)}`, 'DELETE') : req('/me/profile/items', 'POST', { section: 'key_relations', object_entity_id: entityId }));
/** Chấm dòng bóng: Đúng, hoặc Sai kèm 1 trong 3 lý do (`biz.signal_feedback.reason`). Chỉ Owner (máy chủ kiểm). */
export const useSignalFeedback = () =>
  useIntelWrite(({ id, verdict, reason }: { id: string; verdict: 'correct' | 'wrong'; reason?: WrongReason }) =>
    req(`/signals/${encodeURIComponent(id)}/feedback`, 'POST', { verdict, ...(reason ? { reason } : {}) }));
export const useConfirmStaff = () => useIntelWrite(() => req('/intel/setup/staff-confirmed', 'POST', {}));
/** Đánh dấu / bỏ đánh dấu nhân viên — đúng đường "Đánh dấu là nhân viên" của hồ sơ (`PATCH /profile/{id}`, Owner + mã PIN: 423 ⇒ hộp PIN
 * chung tự mở). Tắt ⇒ trả về loại trước đó (`prev_type`, vd khách); máy chủ tự khôi phục quan hệ cũ. */
export const useSetStaff = () =>
  useIntelWrite(({ id, staff, prevType }: { id: string; staff: boolean; prevType?: string | null }) =>
    api.relations.profile.update(id, staff ? { person_type: 'staff', relation_to_owner: 'staff' } : { person_type: (prevType || 'unknown') as PersonType }));

export const useIntelBudget = (enabled: boolean) =>
  useQuery({
    queryKey: qkIntel.budget,
    queryFn: async ({ signal }) => {
      const v = await req<Partial<IntelBudget> | null>('/system/ai-cost/intel', 'GET', undefined, signal);
      if (!v || typeof v.values !== 'object' || typeof v.defaults !== 'object' || !v.values || !v.defaults) throw new Error('Máy chủ trả trần tình báo sai khuôn');
      return v as IntelBudget;
    },
    enabled,
  });

export const useSetIntelBudget = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: Record<string, number | null>) => req<IntelBudget>('/system/ai-cost/intel', 'PUT', b),
    onSuccess: (data) => {
      qc.setQueryData(qkIntel.budget, data);
      void qc.invalidateQueries({ queryKey: qkIntel.budget });
    },
  });
};
