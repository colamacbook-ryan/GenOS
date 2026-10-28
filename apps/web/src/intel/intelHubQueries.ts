import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { qkIntel } from './intelQueries';
import {
  isAskList,
  isSignalItem,
  isSignalList,
  signalQuery,
  type AskList,
  type SignalDetail,
  type SignalFilters,
  type SignalList,
  type SignalTransition,
} from './intelHubModel';

/**
 * v0.1.79 (G1): truy vấn của trang Tình báo — dùng đúng API sẵn có (`GET /signals`, `GET /signals/{id}`, `POST /signals/{id}/transition`,
 * `GET /asks`). Chấm Đúng/Sai/Không cần báo dùng `useSignalFeedback` (intelQueries). Phản hồi sai khuôn ⇒ lỗi tại chỗ.
 */
const get = <T>(path: string, signal?: AbortSignal) => apiClient.request<T>(path, { method: 'GET', ...(signal ? { signal } : {}) });

/** Danh sách tín hiệu theo bộ lọc. Chỉ Owner thấy trang này nên `enabled` do bên gọi truyền. */
export const useSignals = (f: SignalFilters, enabled = true) =>
  useQuery({
    queryKey: [...qkIntel.signals, 'list', f.level, f.status, f.kind] as const,
    queryFn: async ({ signal }): Promise<SignalList> => {
      const v = await get<unknown>(`/signals?${signalQuery(f)}`, signal);
      if (!isSignalList(v)) throw new Error('Máy chủ trả danh sách tín hiệu sai khuôn');
      return v;
    },
    enabled,
    refetchInterval: 60_000,
  });

/** Chi tiết một tín hiệu (bằng chứng đã che). Tín hiệu riêng tư cần PIN ⇒ máy chủ 423 ⇒ hộp PIN chung tự mở rồi gửi lại. */
export const useSignalDetail = (id: string | null) =>
  useQuery({
    queryKey: [...qkIntel.signals, 'detail', id] as const,
    queryFn: async ({ signal }): Promise<SignalDetail> => {
      const v = await get<unknown>(`/signals/${encodeURIComponent(id ?? '')}`, signal);
      if (!isSignalItem(v)) throw new Error('Máy chủ trả chi tiết tín hiệu sai khuôn');
      const o = v as SignalDetail;
      return { ...o, evidence: Array.isArray(o.evidence) ? o.evidence : [] };
    },
    enabled: !!id,
  });

/** Hàng Cần quyết đang mở (Hôm nay chỉ đọc vài dòng đầu). */
export const useAsks = (enabled = true) =>
  useQuery({
    queryKey: qkIntel.asks,
    queryFn: async ({ signal }): Promise<AskList> => {
      const v = await get<unknown>('/asks', signal);
      if (!isAskList(v)) throw new Error('Máy chủ trả hàng Cần quyết sai khuôn');
      return v;
    },
    enabled,
    refetchInterval: 60_000,
  });

/** Chuyển trạng thái: Ghi nhận / Đã xong / Bỏ qua. 409 ⇒ "đã được xử lý" (hiện câu thân thiện ở bên gọi). */
export const useSignalTransition = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, to }: { id: string; to: SignalTransition }) =>
      apiClient.request(`/signals/${encodeURIComponent(id)}/transition`, { method: 'POST', body: { to } }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qkIntel.signals });
      void qc.invalidateQueries({ queryKey: qkIntel.console });
    },
  });
};
