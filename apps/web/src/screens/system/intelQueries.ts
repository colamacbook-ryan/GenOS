import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../../lib/api';
import type { IntelBudget, IntelConsole, IntelMode, IntelRamp } from './intelModel';

/** v0.1.76 (T11): thẻ "Tình báo". Thao tác cần PIN ⇒ máy chủ trả 423 ⇒ hộp PIN chung tự mở rồi gửi lại (lib/api.ts). */
export const qkIntel = {
  console: ['intel', 'console'] as const,
  budget: ['intel', 'budget'] as const,
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
