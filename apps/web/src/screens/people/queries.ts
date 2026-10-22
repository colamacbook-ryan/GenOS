import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type {
  CareIssuesQuery,
  CareResponseQuery,
  CareScenariosQuery,
  DisputeCreateBody,
  DisputeResolveBody,
  PeopleReviewPatchBody,
  PeopleReviewQuery,
  PeopleReviewSuspiciousClearBody,
} from '@gen-harness/contracts';
import { api } from '../../lib/api';

/** Khoá truy vấn của cụm Con người & Chất lượng. */
export const qkPeople = {
  reviews: (q: Record<string, string | number | undefined>) => ['people', 'reviews', JSON.stringify(q)] as const,
  review: (id: string) => ['people', 'review', id] as const,
  care: {
    responseTimes: (q: Record<string, string | undefined>) => ['care', 'response-times', JSON.stringify(q)] as const,
    issues: (q: Record<string, string | number | undefined>) => ['care', 'issues', JSON.stringify(q)] as const,
    scenarios: (q: Record<string, string | number | undefined>) => ['care', 'scenarios', JSON.stringify(q)] as const,
  },
};

function invalidateReviews(qc: ReturnType<typeof useQueryClient>) {
  void qc.invalidateQueries({ queryKey: ['people', 'reviews'] });
  void qc.invalidateQueries({ queryKey: ['people', 'review'] });
}

// ── Đánh giá con người ──
export const useReviews = (q: PeopleReviewQuery) =>
  useQuery({
    queryKey: qkPeople.reviews(q as Record<string, string | number | undefined>),
    queryFn: ({ signal }) => api.people.reviews.list(q, signal),
  });

export const useReview = (id: string | null) =>
  useQuery({
    queryKey: qkPeople.review(id ?? ''),
    queryFn: ({ signal }) => api.people.reviews.get(id as string, signal),
    enabled: !!id,
  });

export const useUpdateReview = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: PeopleReviewPatchBody }) => api.people.reviews.update(id, body),
    onSuccess: () => invalidateReviews(qc),
  });
};

/** F-60: bỏ cờ 'Đáng ngờ' sau khi đã xem chứng cứ (bắt buộc lý do, cần mã PIN, ghi Nhật ký). */
export const useClearSuspicious = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: PeopleReviewSuspiciousClearBody }) => api.people.reviews.clearSuspicious(id, body),
    onSuccess: () => invalidateReviews(qc),
  });
};

export const useOpenDispute = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ reviewId, body }: { reviewId: string; body: DisputeCreateBody }) => api.people.reviews.disputes.create(reviewId, body),
    onSuccess: () => invalidateReviews(qc),
  });
};

export const useResolveDispute = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: DisputeResolveBody }) => api.people.reviews.disputes.resolve(id, body),
    onSuccess: () => invalidateReviews(qc),
  });
};

// ── Chất lượng chăm sóc ──
export const useCareResponseTimes = (q: CareResponseQuery = {}) =>
  useQuery({
    queryKey: qkPeople.care.responseTimes(q as Record<string, string | undefined>),
    queryFn: ({ signal }) => api.people.care.responseTimes(q, signal),
  });

export const useCareIssues = (q: CareIssuesQuery = {}) =>
  useQuery({
    queryKey: qkPeople.care.issues(q as Record<string, string | number | undefined>),
    queryFn: ({ signal }) => api.people.care.repeatedIssues(q, signal),
  });

export const useCareScenarios = (q: CareScenariosQuery = {}) =>
  useQuery({
    queryKey: qkPeople.care.scenarios(q as Record<string, string | number | undefined>),
    queryFn: ({ signal }) => api.people.care.scenarios(q, signal),
  });
