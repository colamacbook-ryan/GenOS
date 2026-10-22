import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type {
  CasePatchBody,
  CaseQuery,
  DealPatchBody,
  DealQuery,
  MarketSignalQuery,
  MatchQuery,
  OppStage,
  SearchBulkBody,
  SearchQuery,
} from '@gen-harness/contracts';
import { api } from '../../lib/api';

/** Khoá truy vấn của cụm Cơ hội & Thị trường. */
export const qkMarket = {
  opportunities: (q: Record<string, string | undefined>) => ['market', 'opportunities', JSON.stringify(q)] as const,
  pipeline: ['market', 'pipeline'] as const,
  supply: (q: Record<string, string | undefined>) => ['market', 'supply', JSON.stringify(q)] as const,
  matches: (q: Record<string, string | number | undefined>) => ['market', 'matches', JSON.stringify(q)] as const,
  search: (q: Record<string, string | undefined>) => ['market', 'search', JSON.stringify(q)] as const,
  deals: (q: Record<string, string | undefined>) => ['market', 'deals', JSON.stringify(q)] as const,
  cases: (q: Record<string, string | undefined>) => ['market', 'cases', JSON.stringify(q)] as const,
};

// ── Bảng cơ hội ──
export const useOpportunities = () =>
  useQuery({
    queryKey: qkMarket.opportunities({}),
    queryFn: ({ signal }) => api.market.opportunities.list({ limit: 200 }, signal),
  });

export const useOpportunityPipeline = () =>
  useQuery({
    queryKey: qkMarket.pipeline,
    queryFn: ({ signal }) => api.market.opportunities.pipeline(signal),
  });

export const useChangeStage = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, toStage }: { id: string; toStage: OppStage }) => api.market.opportunities.setStage(id, toStage),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['market', 'opportunities'] });
      void qc.invalidateQueries({ queryKey: qkMarket.pipeline });
    },
  });
};

// ── Cung ↔ Cầu ──
export const useSupply = (q: MarketSignalQuery) =>
  useQuery({
    queryKey: qkMarket.supply(q as Record<string, string | undefined>),
    queryFn: ({ signal }) => api.market.supply.list(q, signal),
  });

export const useMatches = (q: MatchQuery = {}) =>
  useQuery({
    queryKey: qkMarket.matches(q as Record<string, string | number | undefined>),
    queryFn: ({ signal }) => api.market.matches.list(q, signal),
  });

function invalidateSupply(qc: ReturnType<typeof useQueryClient>) {
  void qc.invalidateQueries({ queryKey: ['market', 'supply'] });
  void qc.invalidateQueries({ queryKey: ['market', 'matches'] });
  void qc.invalidateQueries({ queryKey: ['market', 'opportunities'] });
  void qc.invalidateQueries({ queryKey: qkMarket.pipeline });
}

export const useIntroduceMatch = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.market.matches.introduce(id),
    onSuccess: () => invalidateSupply(qc),
  });
};
export const useRejectMatch = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.market.matches.reject(id),
    onSuccess: () => invalidateSupply(qc),
  });
};
export const useRecomputeMatches = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.market.matches.recompute(),
    onSuccess: () => invalidateSupply(qc),
  });
};

// ── Kho hội thoại ──
export const useSearch = (q: SearchQuery) =>
  useQuery({
    queryKey: qkMarket.search(q as Record<string, string | undefined>),
    queryFn: ({ signal }) => api.market.search.query(q, signal),
  });

export const useSearchBulk = () => useMutation({ mutationFn: (body: SearchBulkBody) => api.market.search.bulk(body) });

// ── Deal & Vụ việc ──
export const useDeals = (q: DealQuery) =>
  useQuery({
    queryKey: qkMarket.deals(q as Record<string, string | undefined>),
    queryFn: ({ signal }) => api.market.deals.list(q, signal),
  });

export const usePatchDeal = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: DealPatchBody }) => api.market.deals.update(id, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['market', 'deals'] }),
  });
};

export const useCases = (q: CaseQuery) =>
  useQuery({
    queryKey: qkMarket.cases(q as Record<string, string | undefined>),
    queryFn: ({ signal }) => api.market.cases.list(q, signal),
  });

export const usePatchCase = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: CasePatchBody }) => api.market.cases.update(id, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['market', 'cases'] }),
  });
};
