import { keepPreviousData, useInfiniteQuery, useQuery } from '@tanstack/react-query';
import type { CleanQuery, CliKind, NotebookSubjectType, RawQuery } from '@gen-harness/contracts';
import { api } from './api';

/** Query keys for phase-2 data. Realtime events patch these caches (lib/realtime.ts). */
export const qk2 = {
  pipeline: ['data', 'pipeline'] as const,
  rawRoot: ['raw', 'list'] as const,
  raw: (q: RawQuery) => ['raw', 'list', q] as const,
  rawByGroup: (since: string) => ['raw', 'by-group', since] as const,
  schedule: ['refinery', 'schedule'] as const,
  runs: ['refinery', 'runs'] as const,
  progress: (runId: string) => ['refinery', 'progress', runId] as const,
  latestProgress: ['refinery', 'progress', 'latest'] as const,
  rules: ['rules', 'list'] as const,
  ruleVersions: (id: string) => ['rules', 'versions', id] as const,
  weights: ['rules', 'weights'] as const,
  cleanRoot: ['clean', 'list'] as const,
  clean: (q: CleanQuery) => ['clean', 'list', q] as const,
  cleanEvidence: (id: string) => ['clean', 'evidence', id] as const,
  agentParams: (g?: string, p?: string) => ['clean', 'agentParams', g ?? '', p ?? ''] as const,
  notebook: (t: NotebookSubjectType, id: string) => ['notebooks', t, id] as const,
  compactions: (t: NotebookSubjectType, id: string) => ['notebooks', t, id, 'compactions'] as const,
  idStats: ['identity', 'stats'] as const,
  idCandidates: ['identity', 'candidates'] as const,
  idEvidence: (id: string) => ['identity', 'evidence', id] as const,
  idHistory: ['identity', 'history'] as const,
  channels: ['channels', 'list'] as const,
  channelGroups: (type: string) => ['channels', 'groups', type] as const,
  /** v0.1.65: nhóm mới chờ chọn — nằm DƯỚI khoá `channelGroups` nên invalidate bảng nhóm cũng làm mới hộp này. */
  pendingGroups: (type: string) => ['channels', 'groups', type, 'pending'] as const,
  providers: ['providers', 'list'] as const,
  credentials: ['providers', 'credentials'] as const,
  cliProfiles: ['cli', 'profiles'] as const,
  cliLogin: (loginId: string) => ['cli', 'login', loginId] as const,
  rulePresets: ['setup', 'rule-presets'] as const,
  firstRun: ['setup', 'first-run'] as const,
};

export const usePipeline = () => useQuery({ queryKey: qk2.pipeline, queryFn: ({ signal }) => api.data.pipeline(signal) });

export const RAW_PAGE = 50;

export const useRawList = (q: RawQuery) =>
  useInfiniteQuery({
    queryKey: qk2.raw(q),
    queryFn: ({ pageParam, signal }) => api.raw.list({ ...q, limit: RAW_PAGE, cursor: pageParam ?? undefined }, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    placeholderData: keepPreviousData,
  });

export const useRawByGroup = (since = '24h') =>
  useQuery({
    queryKey: qk2.rawByGroup(since),
    queryFn: ({ signal }) => api.raw.byGroup({ since: since as '24h', limit: 7 }, signal),
  });

export const useSchedule = () =>
  useQuery({ queryKey: qk2.schedule, queryFn: ({ signal }) => api.refinery.schedule(signal) });

export const useRuns = () => useQuery({ queryKey: qk2.runs, queryFn: ({ signal }) => api.refinery.runs(5, signal) });

export const useRules = () => useQuery({ queryKey: qk2.rules, queryFn: ({ signal }) => api.rules.list(signal) });

export const useWeights = () => useQuery({ queryKey: qk2.weights, queryFn: ({ signal }) => api.rules.weights(signal) });

export const useCleanList = (q: CleanQuery) =>
  useInfiniteQuery({
    queryKey: qk2.clean(q),
    queryFn: ({ pageParam, signal }) => api.clean.list({ ...q, limit: RAW_PAGE, cursor: pageParam ?? undefined }, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    placeholderData: keepPreviousData,
  });

export const useChannels = () => useQuery({ queryKey: qk2.channels, queryFn: ({ signal }) => api.channels.list(signal) });

export const useChannelGroups = (type: string, enabled = true) =>
  useQuery({
    queryKey: qk2.channelGroups(type),
    queryFn: ({ signal }) => api.channels.groups(type, signal),
    enabled,
  });

/** v0.1.65: nhóm mới phát hiện đang CHỜ Sếp chọn Im lặng/Không nghe (`GET /channels/{type}/groups?pending=1`). */
export const usePendingGroups = (type: string, enabled = true) =>
  useQuery({
    queryKey: qk2.pendingGroups(type),
    queryFn: ({ signal }) => api.channels.pendingGroups(type, signal),
    enabled,
  });

export const useProviders = () => useQuery({ queryKey: qk2.providers, queryFn: ({ signal }) => api.providers.list(signal) });

/** v0.1.31: hồ sơ theo loại CLI. Khoá `['cli','profiles']` (Antigravity) là tiền tố của khoá Claude → invalidate một lần là đủ cả hai. */
export const cliProfilesKey = (kind: CliKind = 'antigravity_cli'): readonly string[] =>
  kind === 'antigravity_cli' ? qk2.cliProfiles : [...qk2.cliProfiles, kind];

export const useCliProfiles = (kind: CliKind = 'antigravity_cli') =>
  useQuery({
    queryKey: cliProfilesKey(kind),
    queryFn: ({ signal }) => api.cli.profiles(signal, kind),
  });
