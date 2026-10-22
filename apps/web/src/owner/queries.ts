/**
 * v0.1.55 (G5) + v0.1.59 (G1) — truy vấn của Mặt tiền Owner (`/api/v1/owner/*`, CHỈ ĐỌC, chỉ Owner). Không gọi model.
 * Vai trò khác Owner KHÔNG gọi (máy chủ trả 403) — `enabled` theo `Me.role`. Mọi khoá bắt đầu bằng `['owner', …]`
 * nên sau mỗi lần ghi (`owner/actions/useOwnerActions.ts`) chỉ cần `invalidateQueries({ queryKey: ['owner'] })`.
 *
 * Hook: `useOwnerBrief(id?)` · `useOwnerToday()` · `useOwnerFeed(view, limit?)` (phân trang offset) ·
 * `useOwnerFeedContext(id | null)` · `useOwnerRelations(list)` · `useOwnerInsights(range, only?)` (làm tươi 10 phút) ·
 * `useOwnerProposals()` · `useOwnerTasks()` · `useIsOwnerRole()`.
 */
import { useCallback } from 'react';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { ApiError } from '@gen-harness/contracts';
import type { FeedView, InsightKey, InsightRange, OwnerRelations } from '@gen-harness/contracts';
import { api } from '../lib/api';
import { useMe } from '../lib/queries';
import type { OwnerRelationList } from './ownerModel';

const ownerApi = api.owner;

export const OWNER_KEYS = {
  all: ['owner'] as const,
  today: ['owner', 'today'] as const,
  tasks: ['owner', 'tasks'] as const,
  brief: (id?: string) => ['owner', 'brief', id ?? 'latest'] as const,
  feed: (view: FeedView, limit: number) => ['owner', 'feed', view, limit] as const,
  feedContext: (id: string) => ['owner', 'feed', 'context', id] as const,
  relations: (list: OwnerRelationList) => ['owner', 'relations', list] as const,
  insights: (range: InsightRange, only: string) => ['owner', 'insights', range, only] as const,
  proposals: ['owner', 'proposals'] as const,
};

/** Hôm nay / bản tin / dòng tin tự làm tươi mỗi 5 phút khi đang mở (không gọi model, chỉ đọc). */
const REFRESH_MS = 5 * 60_000;
/** Phân tích nặng hơn (ghép trả lời): làm tươi mỗi 10 phút. */
export const INSIGHTS_REFRESH_MS = 10 * 60_000;

export function useIsOwnerRole(): boolean {
  return useMe().data?.role?.code === 'owner';
}

export const useOwnerToday = () => {
  const owner = useIsOwnerRole();
  return useQuery({
    queryKey: OWNER_KEYS.today,
    queryFn: ({ signal }) => ownerApi.today(signal),
    enabled: owner,
    refetchInterval: owner ? REFRESH_MS : false,
  });
};

/** Bản tin Tình báo: `id` chọn một bản trong `history` (bỏ trống = bản mới nhất). */
export const useOwnerBrief = (id?: string) => {
  const owner = useIsOwnerRole();
  return useQuery({
    queryKey: OWNER_KEYS.brief(id),
    queryFn: ({ signal }) => ownerApi.brief(id, signal),
    enabled: owner,
    refetchInterval: owner ? REFRESH_MS : false,
  });
};

/** Dòng tin "Thông tin": phân trang theo `offset` (`fetchNextPage` khi `hasNextPage`). */
export const useOwnerFeed = (view: FeedView = 'important', limit = 30) => {
  const owner = useIsOwnerRole();
  return useInfiniteQuery({
    queryKey: OWNER_KEYS.feed(view, limit),
    queryFn: ({ pageParam, signal }) => ownerApi.feed({ view, offset: pageParam, limit }, signal),
    initialPageParam: 0,
    getNextPageParam: (last) => last.next_offset ?? undefined,
    enabled: owner,
    refetchInterval: owner ? REFRESH_MS : false,
  });
};

/** Hội thoại quanh một tin; `id` null = chưa chọn tin nào (không gọi). */
export const useOwnerFeedContext = (id: string | null) => {
  const owner = useIsOwnerRole();
  return useQuery({
    queryKey: OWNER_KEYS.feedContext(id ?? ''),
    queryFn: ({ signal }) => ownerApi.feedContext(id as string, signal),
    enabled: owner && !!id,
  });
};

/** "Khách cần chăm" luôn tải MỘT lần ở cỡ này (một khoá truy vấn); nơi cần ít hơn (Tình báo 3, Việc 5) cắt ở máy khách. */
export const CARE_FETCH_LIMIT = 20;

/**
 * 5 danh sách của Quan hệ. `care` dùng chung MỘT khoá cho mọi màn (Tình báo / Việc / Quan hệ) ⇒ một lần tính nặng ở máy chủ
 * thay vì ba. Máy chủ báo `state: "error"` (quá giờ / lỗi) ⇒ coi là lỗi tải (câu thân thiện + Chi tiết kỹ thuật + Thử lại),
 * KHÔNG hiện "không khách nào cần chăm".
 */
export const useOwnerRelations = (list: OwnerRelationList, limit = 20) => {
  const owner = useIsOwnerRole();
  const fetchLimit = list === 'care' ? CARE_FETCH_LIMIT : limit;
  const select = useCallback(
    (d: OwnerRelations): OwnerRelations => {
      if (d.state === 'error') {
        throw new ApiError(503, { code: 'RELATIONS_UNAVAILABLE', title: 'Danh sách này chưa tính được lúc này. Sếp thử lại sau ít phút.' });
      }
      return list === 'care' && limit < fetchLimit && Array.isArray(d.items) ? { ...d, items: d.items.slice(0, limit) } : d;
    },
    [list, limit, fetchLimit],
  );
  return useQuery({
    queryKey: [...OWNER_KEYS.relations(list), fetchLimit] as const,
    queryFn: ({ signal }) => ownerApi.relations(list, fetchLimit, signal),
    enabled: owner,
    select,
  });
};

/** Phân tích 7 / 30 ngày; `only` = khoá cần tính (vd `['messages_in']` cho sparkline của Tình báo). Làm tươi 10 phút. */
export const useOwnerInsights = (range: InsightRange, only?: readonly InsightKey[]) => {
  const owner = useIsOwnerRole();
  const onlyKey = only?.join(',') ?? '';
  return useQuery({
    queryKey: OWNER_KEYS.insights(range, onlyKey),
    queryFn: ({ signal }) => ownerApi.insights(range, onlyKey || undefined, signal),
    enabled: owner,
    refetchInterval: owner ? INSIGHTS_REFRESH_MS : false,
  });
};

/** Đề xuất của Gen còn hiệu lực (hết hạn sau 24 giờ ⇒ làm tươi thường xuyên). */
export const useOwnerProposals = () => {
  const owner = useIsOwnerRole();
  return useQuery({
    queryKey: OWNER_KEYS.proposals,
    queryFn: ({ signal }) => ownerApi.proposals(signal),
    enabled: owner,
    refetchInterval: owner ? REFRESH_MS : false,
  });
};

export const useOwnerTasks = () => {
  const owner = useIsOwnerRole();
  return useQuery({
    queryKey: OWNER_KEYS.tasks,
    queryFn: ({ signal }) => ownerApi.tasks(signal),
    enabled: owner,
    refetchInterval: owner ? REFRESH_MS : false,
  });
};
