import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { AgentCloneBody, AgentCreateBody, AgentPatchBody } from '@gen-harness/contracts';
import { api } from '../../lib/api';
import { qk } from '../../lib/queries';

export const qkAgents = {
  list: ['agents', 'list'] as const,
  templates: ['agents', 'templates'] as const,
  diplomat: ['agents', 'diplomat'] as const,
  decisions: (agentId?: string, decision?: string) => ['agents', 'decisions', agentId ?? '', decision ?? ''] as const,
};

export const useAgents = () => useQuery({ queryKey: qkAgents.list, queryFn: ({ signal }) => api.agents.list(signal) });

export const useAgentTemplates = () =>
  useQuery({ queryKey: qkAgents.templates, queryFn: ({ signal }) => api.agents.templates(signal) });

export const useAgentDecisions = (agentId?: string, decision?: string, limit = 8) =>
  useQuery({
    queryKey: [...qkAgents.decisions(agentId, decision), limit],
    queryFn: ({ signal }) => api.agentDecisions({ agent_id: agentId, decision, limit }, signal),
  });

export const useDiplomat = () =>
  useQuery({ queryKey: qkAgents.diplomat, queryFn: ({ signal }) => api.agents.diplomat(signal) });

/** v0.1.73: chọn / bỏ Agent ngoại giao (PIN `agent.manage`: hộp PIN tự mở khi máy chủ trả 423). */
export const useSetDiplomat = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (agentId: string | null) => api.agents.setDiplomat(agentId),
    onSuccess: (data) => qc.setQueryData(qkAgents.diplomat, data),
  });
};

function invalidateAgents(qc: ReturnType<typeof useQueryClient>) {
  void qc.invalidateQueries({ queryKey: qkAgents.list });
  // Bước 9 (Hướng dẫn) điền sẵn mức tự trị của agent thiết lập — sửa/tạo agent ở đây thì đọc lại.
  void qc.invalidateQueries({ queryKey: qk.setupStep9 });
}

export const useCreateAgent = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AgentCreateBody) => api.agents.create(body),
    onSuccess: () => invalidateAgents(qc),
  });
};

export const useUpdateAgent = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: AgentPatchBody }) => api.agents.update(id, body),
    onSuccess: () => invalidateAgents(qc),
  });
};

export const useCloneAgent = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: AgentCloneBody }) => api.agents.clone(id, body),
    onSuccess: () => invalidateAgents(qc),
  });
};

export const useSetAgentEnabled = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => api.agents.setEnabled(id, enabled),
    onSuccess: () => invalidateAgents(qc),
  });
};

/** v0.1.67 (F085): Lưu trữ agent (PIN `agent.manage`) — agent biến khỏi danh sách, lịch sử quyết định còn nguyên. */
export const useArchiveAgent = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.agents.archive(id),
    onSuccess: () => {
      invalidateAgents(qc);
      void qc.invalidateQueries({ queryKey: ['agents', 'decisions'] });
      void qc.invalidateQueries({ queryKey: ['agents', 'bindings'] });
    },
  });
};
