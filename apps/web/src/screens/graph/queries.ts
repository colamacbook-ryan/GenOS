import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { GraphEdgeQuery, GraphLayoutBody, GraphLayoutMode, GraphListQuery } from '@gen-harness/contracts';
import { api } from '../../lib/api';

/** Khoá truy vấn của cụm Bản đồ quan hệ. */
export const qkGraph = {
  list: (q: GraphListQuery) => ['graph', 'list', JSON.stringify(q)] as const,
  people: (q: GraphEdgeQuery) => ['graph', 'people', JSON.stringify(q)] as const,
  groups: (q: GraphEdgeQuery) => ['graph', 'groups', JSON.stringify(q)] as const,
  topics: (limit: number) => ['graph', 'topics', limit] as const,
  topic: (topic: string, nodeLimit: number) => ['graph', 'topic', topic, nodeLimit] as const,
  layout: (mode: GraphLayoutMode) => ['graph', 'layout', mode] as const,
};

export const useGraphList = (q: GraphListQuery, enabled = true) =>
  useQuery({ queryKey: qkGraph.list(q), queryFn: ({ signal }) => api.graph.list(q, signal), enabled });

export const useGraphPeople = (q: GraphEdgeQuery, enabled = true) =>
  useQuery({ queryKey: qkGraph.people(q), queryFn: ({ signal }) => api.graph.people(q, signal), enabled });

export const useGraphGroups = (q: GraphEdgeQuery, enabled = true) =>
  useQuery({ queryKey: qkGraph.groups(q), queryFn: ({ signal }) => api.graph.groups(q, signal), enabled });

export const useGraphTopics = (limit = 50) =>
  useQuery({ queryKey: qkGraph.topics(limit), queryFn: ({ signal }) => api.graph.topics.list({ limit }, signal) });

export const useGraphTopic = (topic: string | null, nodeLimit = 200) =>
  useQuery({
    queryKey: qkGraph.topic(topic ?? '', nodeLimit),
    queryFn: ({ signal }) => api.graph.topics.get(topic as string, { node_limit: nodeLimit }, signal),
    enabled: !!topic,
  });

export const useGraphLayout = (mode: GraphLayoutMode) =>
  useQuery({ queryKey: qkGraph.layout(mode), queryFn: ({ signal }) => api.graph.layout.get(mode, signal) });

export const useSaveGraphLayout = (mode: GraphLayoutMode) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: GraphLayoutBody) => api.graph.layout.put(mode, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qkGraph.layout(mode) }),
  });
};

export const useRecomputeGraph = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.graph.recompute(),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['graph'] }),
  });
};
