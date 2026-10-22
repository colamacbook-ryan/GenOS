import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { PluginItem, PluginLocalInstallBody, PluginLogPage } from '@gen-harness/contracts';
import { api } from '../../lib/api';
import { onRealtimeEvent } from '../../lib/realtime';

export const qkPlugins = {
  list: ['plugins', 'list'] as const,
  logs: (pkg: string) => ['plugins', 'logs', pkg] as const,
};

export const usePlugins = () => useQuery({ queryKey: qkPlugins.list, queryFn: ({ signal }) => api.plugins.list(signal) });

export const usePluginLogs = (pkg: string | null) =>
  useQuery({
    queryKey: qkPlugins.logs(pkg ?? ''),
    queryFn: ({ signal }) => api.plugins.logs(pkg as string, {}, signal),
    enabled: !!pkg,
  });

function patchOne(qc: ReturnType<typeof useQueryClient>, updated: PluginItem) {
  qc.setQueryData<PluginItem[]>(qkPlugins.list, (old) => old?.map((p) => (p.package === updated.package ? updated : p)));
}

export const useTogglePlugin = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ pkg, enabled }: { pkg: string; enabled: boolean }) => api.plugins.toggle(pkg, enabled),
    onSuccess: (p) => patchOne(qc, p),
  });
};

export const useRemovePlugin = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (pkg: string) => api.plugins.remove(pkg),
    onSuccess: (_v, pkg) => qc.setQueryData<PluginItem[]>(qkPlugins.list, (old) => old?.filter((p) => p.package !== pkg)),
  });
};

export const useResetBreaker = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (pkg: string) => api.plugins.breakerReset(pkg),
    onSuccess: (p) => patchOne(qc, p),
  });
};

export const useInstallLocalPlugin = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PluginLocalInstallBody) => api.plugins.installLocal(body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qkPlugins.list }),
  });
};

// Realtime: một dòng nhật ký plugin mới (sức khoẻ, bật/tắt, ngắt mạch…) — thêm vào panel đang mở nếu có,
// và làm mới danh sách để cột Sức khoẻ/Breaker theo kịp (server cũng trả bản mới nhất qua mutation, đây là
// phòng khi thay đổi đến từ nơi khác, vd worker tự ngắt mạch).
onRealtimeEvent('plugin.log', (qc, data) => {
  const e = data as { package: string; id: string; at: string; level: string; message: string; ctx: unknown };
  qc.setQueryData<PluginLogPage>(qkPlugins.logs(e.package), (old) =>
    old && !old.items.some((i) => i.id === e.id) ? { ...old, items: [{ id: e.id, at: e.at, level: e.level, message: e.message, ctx: e.ctx }, ...old.items] } : old,
  );
  void qc.invalidateQueries({ queryKey: qkPlugins.list });
});
