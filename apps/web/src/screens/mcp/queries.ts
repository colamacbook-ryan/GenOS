import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { HubLink, HubLinkPatchBody, McpCall, McpCallBody, McpServer, McpServerCreateBody, McpServerPatchBody, McpTool } from '@gen-harness/contracts';
import { api } from '../../lib/api';
import { onRealtimeEvent } from '../../lib/realtime';

export const qkMcp = {
  servers: ['mcp', 'servers'] as const,
  tools: ['mcp', 'tools'] as const,
  calls: ['mcp', 'calls'] as const,
  hubLink: ['hub', 'link'] as const,
};

export const useMcpServers = () => useQuery({ queryKey: qkMcp.servers, queryFn: ({ signal }) => api.mcp.servers.list(signal) });

export const useMcpTools = () => useQuery({ queryKey: qkMcp.tools, queryFn: ({ signal }) => api.mcp.tools.list(undefined, signal) });

export const useMcpCalls = () => useQuery({ queryKey: qkMcp.calls, queryFn: ({ signal }) => api.mcp.calls({ limit: 50 }, signal) });

function invalidateServers(qc: ReturnType<typeof useQueryClient>) {
  void qc.invalidateQueries({ queryKey: qkMcp.servers });
}
function invalidateTools(qc: ReturnType<typeof useQueryClient>) {
  void qc.invalidateQueries({ queryKey: qkMcp.tools });
}

export const useCreateServer = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (body: McpServerCreateBody) => api.mcp.servers.create(body), onSuccess: () => invalidateServers(qc) });
};

export const useUpdateServer = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: McpServerPatchBody }) => api.mcp.servers.update(id, body),
    onSuccess: (s) => qc.setQueryData<McpServer[]>(qkMcp.servers, (old) => old?.map((x) => (x.id === s.id ? s : x))),
  });
};

export const useDeleteServer = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.mcp.servers.remove(id),
    onSuccess: (_v, id) => {
      qc.setQueryData<McpServer[]>(qkMcp.servers, (old) => old?.filter((x) => x.id !== id));
      qc.setQueryData<McpTool[]>(qkMcp.tools, (old) => old?.filter((t) => t.server_id !== id));
    },
  });
};

export const useDiscoverTools = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.mcp.servers.discover(id),
    onSuccess: () => {
      invalidateServers(qc);
      invalidateTools(qc);
    },
  });
};

function patchTool(qc: ReturnType<typeof useQueryClient>, t: McpTool) {
  qc.setQueryData<McpTool[]>(qkMcp.tools, (old) => old?.map((x) => (x.id === t.id ? t : x)));
  invalidateServers(qc); // exposed_count đổi
}

export const useExposeTool = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, isExposed }: { id: string; isExposed: boolean }) => api.mcp.tools.expose(id, isExposed),
    onSuccess: (t) => patchTool(qc, t),
  });
};

export const useGrantTool = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, agentKey }: { id: string; agentKey: string }) => api.mcp.tools.grant(id, agentKey),
    onSuccess: (t) => patchTool(qc, t),
  });
};

export const useUngrantTool = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, agentKey }: { id: string; agentKey: string }) => api.mcp.tools.ungrant(id, agentKey),
    onSuccess: (_v, { id, agentKey }) =>
      qc.setQueryData<McpTool[]>(qkMcp.tools, (old) => old?.map((t) => (t.id === id ? { ...t, grants: t.grants.filter((a) => a !== agentKey) } : t))),
  });
};

export const useCallTool = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: McpCallBody }) => api.mcp.tools.call(id, body),
    onSettled: () => void qc.invalidateQueries({ queryKey: qkMcp.calls }),
  });
};

// ─── Gen-hub (v0.1.26) ────────────────────────────────────────────────────────
export const useHubLink = () => useQuery({ queryKey: qkMcp.hubLink, queryFn: ({ signal }) => api.hub.link.get(signal) });

export const useUpdateHubLink = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: HubLinkPatchBody) => api.hub.link.update(body),
    onSuccess: (l) => {
      qc.setQueryData<HubLink>(qkMcp.hubLink, l);
      invalidateServers(qc);
    },
  });
};

export const useTestHubLink = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.hub.link.test(),
    onSuccess: (r) => {
      qc.setQueryData<HubLink>(qkMcp.hubLink, r.link);
      invalidateServers(qc);
      invalidateTools(qc);
    },
    onSettled: () => void qc.invalidateQueries({ queryKey: qkMcp.calls }),
  });
};

// Realtime: nhật ký gọi tool LIVE (`mcp.call`) — thêm dòng mới nhất lên đầu nếu panel đang mở.
onRealtimeEvent('mcp.call', (qc, data) => {
  const item = data as McpCall;
  qc.setQueryData<{ items: McpCall[]; next_cursor: string | null }>(qkMcp.calls, (old) =>
    old && !old.items.some((c) => c.id === item.id) ? { ...old, items: [item, ...old.items] } : old,
  );
});

// Realtime: sức khoẻ máy chủ đổi (`mcp.server_health`, ví dụ khám phá/gọi tool ở nơi khác làm đổi trạng thái).
onRealtimeEvent('mcp.server_health', (qc, data) => {
  const e = data as { server_id: string; health: string };
  qc.setQueryData<McpServer[]>(qkMcp.servers, (old) => old?.map((s) => (s.id === e.server_id ? { ...s, health: e.health } : s)));
});
