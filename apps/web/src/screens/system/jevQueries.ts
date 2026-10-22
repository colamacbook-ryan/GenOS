import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { JevEnableBody } from '@gen-harness/contracts';
import { api } from '../../lib/api';
import { qk2 } from '../../lib/dataQueries';

/**
 * v0.1.55 (G4) — truy vấn của thẻ Jev: bật 1 chạm, thử 12 câu mẫu, số đo giá trị, "Tin đã bỏ qua". Để riêng ở đây
 * (không đụng screens/api/queries.ts); chỉ Owner gọi được /jev/* (máy chủ kiểm), "Tin đã bỏ qua" cần quyền queue.read.
 */
export const qkJev = {
  valueSummary: (days: number) => ['jev', 'value-summary', days] as const,
  skipped: (limit: number) => ['jev', 'skipped', limit] as const,
};

export const useJevValueSummary = (days = 7, enabled = true) =>
  useQuery({
    queryKey: qkJev.valueSummary(days),
    queryFn: ({ signal }) => api.queue.jev.valueSummary(days, signal),
    enabled,
    retry: false,
  });

/** Danh sách chỉ đọc "Tin đã bỏ qua" (J2). */
export const useSkippedItems = (limit = 50, enabled = true) =>
  useQuery({
    queryKey: qkJev.skipped(limit),
    queryFn: ({ signal }) => api.queue.triage.skipped(limit, signal),
    enabled,
    retry: false,
  });

/** "Thử 12 câu mẫu" — mỗi lần bấm là 12 lượt gọi Jev thật nên là mutation, không tự chạy. */
export const useJevBenchmark = () => useMutation({ mutationFn: () => api.queue.jev.benchmark() });

/** "Bật Jev 1 chạm" — cần mã PIN (khung PIN của client tự hiện khi máy chủ trả 423). Khoá không quay về trình duyệt. */
export const useEnableJev = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: JevEnableBody) => api.queue.jev.enable(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk2.providers });
      void qc.invalidateQueries({ queryKey: qk2.credentials });
      void qc.invalidateQueries({ queryKey: ['jev', 'value-summary'] });
    },
  });
};
