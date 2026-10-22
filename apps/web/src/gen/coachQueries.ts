/**
 * v0.1.54 — truy vấn và hành động của Gen hướng dẫn (chỉ Owner). Một khoá cache chung `['gen','coach','today']` cho
 * chấm đỏ ở nút Gen VÀ thẻ "Hôm nay của Sếp" (một lần tải, hai nơi dùng). Không gọi model, không ghi hội thoại.
 */
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import type { CoachItemAction, CoachPrefsPatch, CoachToday } from '@gen-harness/contracts';
import { api } from '../lib/api';
import { useMe } from '../lib/queries';
import { COACH_CURRICULUM_KEY, COACH_POLL_MS, COACH_PREFS_KEY, COACH_TODAY_KEY } from './coachModel';

/** Người xem là Owner (Sếp) — chỉ Owner gọi `/gen/coach/*` (vai trò khác 403). */
export function useIsOwner(): boolean {
  return useMe().data?.role?.code === 'owner';
}

/** Owner và Gen đang bật cho người này (`Me.features.gen`) — điều kiện vẽ thẻ và gọi `today`. */
export function useCoachAudience(): boolean {
  const me = useMe();
  return me.data?.role?.code === 'owner' && !!me.data?.features?.gen;
}

/**
 * `GET /gen/coach/today` — tải khi mở app, hỏi lại mỗi 30 phút. Dùng chung cho chấm đỏ và thẻ. `enabled=false` ⇒ KHÔNG
 * gọi API (vai trò khác Owner, Gen tắt).
 */
export function useCoachToday(enabled: boolean) {
  return useQuery({
    queryKey: COACH_TODAY_KEY,
    queryFn: ({ signal }) => api.gen.coach.today(false, signal),
    enabled,
    refetchInterval: enabled ? COACH_POLL_MS : false,
  });
}

/** `GET /gen/coach/prefs` — Cài đặt › Bộ não AI › Gen hướng dẫn, và hạn "Việc thiết lập tiếp". */
export function useCoachPrefs(enabled = true) {
  return useQuery({ queryKey: COACH_PREFS_KEY, queryFn: ({ signal }) => api.gen.coach.prefs(signal), enabled });
}

/** `GET /gen/coach/curriculum` — Trợ giúp › Lộ trình học cùng Gen. */
export function useCoachCurriculum(enabled = true) {
  return useQuery({ queryKey: COACH_CURRICULUM_KEY, queryFn: ({ signal }) => api.gen.coach.curriculum(signal), enabled });
}

/** Làm mới mọi thứ của Gen hướng dẫn (sau khi Sếp bấm một nút hoặc đổi cài đặt). */
export function invalidateCoach(qc: QueryClient): Promise<void> {
  return qc.invalidateQueries({ queryKey: ['gen', 'coach'] });
}

/** `POST /gen/coach/items/{item_key}` rồi làm mới thẻ, cài đặt và lộ trình. Lỗi ném lại cho nơi gọi hiện câu thân thiện. */
export function useCoachItemAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ itemKey, body }: { itemKey: string; body: CoachItemAction }) => api.gen.coach.itemAction(itemKey, body),
    onSettled: () => void invalidateCoach(qc),
  });
}

/** `PATCH /gen/coach/prefs` — công tắc, chuông, số bài mỗi ngày, giờ yên lặng, "Hoãn tất cả". Cập nhật ngay bản cache. */
export function usePatchCoachPrefs() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CoachPrefsPatch) => api.gen.coach.patchPrefs(body),
    onSuccess: (prefs) => qc.setQueryData(COACH_PREFS_KEY, prefs),
    onSettled: () => void invalidateCoach(qc),
  });
}

/**
 * Đánh dấu Sếp đã thấy thẻ (`?mark_shown=1`) rồi ghi kết quả vào cache với `unseen=false` (tắt chấm đỏ ngay). Lỗi bị nuốt:
 * không đánh dấu được thì chấm đỏ ở lần tải sau — không phải chuyện để báo Sếp.
 */
export async function markCoachShown(qc: QueryClient): Promise<void> {
  try {
    const t = await api.gen.coach.today(true);
    qc.setQueryData<CoachToday>(COACH_TODAY_KEY, { ...t, unseen: false });
  } catch {
    /* im lặng */
  }
}
