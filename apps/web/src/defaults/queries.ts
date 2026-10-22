import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { useMe } from '../lib/queries';
import { OWNER_KEYS } from '../owner/queries';
import type { DefaultsResponse } from './defaultsModel';

const defaultsApi = api.defaults;

/** `GET /defaults` (chỉ Owner). */
export const DEFAULTS_KEY = ['defaults'] as const;

/**
 * Cache cài đặt bị Về mặc định đổi: dòng gán model (`agents`), lọc tin (`queue`), trần chi phí/sao lưu/nguồn việc nền
 * (`system`, `providers`), Gen + Gen nhớ + Gen hướng dẫn (`gen`), lịch sàng lọc (`refinery`), Mặt tiền Owner (`owner`: thẻ
 * "Áp model chuẩn theo vai?" ở Hôm nay đến từ cùng sổ này — không làm tươi thì Sếp tưởng thao tác chưa chạy), thẻ Gen-hub
 * (`hub`: ô "Tên Kho" về mặc định ở v0.1.57) và chính sổ mặc định.
 */
const RELATED_ROOTS: ReadonlyArray<readonly string[]> = [
  DEFAULTS_KEY,
  ['agents'],
  ['queue', 'triage'],
  ['system'],
  ['providers'],
  ['gen'],
  ['refinery'],
  ['hub', 'link'],
  OWNER_KEYS.all,
];

export function refreshSettings(qc: QueryClient): void {
  for (const root of RELATED_ROOTS) void qc.invalidateQueries({ queryKey: [...root] });
}

/** Sổ mặc định của Owner; vai trò khác không gọi (máy chủ trả 403). */
export const useDefaults = () => {
  const isOwner = useMe().data?.role?.code === 'owner';
  return useQuery<DefaultsResponse>({
    queryKey: DEFAULTS_KEY,
    queryFn: ({ signal }) => defaultsApi.list(signal),
    enabled: isOwner,
    retry: false,
  });
};

/** `POST /defaults/{key}/reset` — Về mặc định MỘT mục (máy chủ đòi `confirm: true`; hộp Xác nhận là của ResetButton). */
export const useResetDefault = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (key: string) => defaultsApi.reset(key),
    onSuccess: () => refreshSettings(qc),
  });
};

/** `POST /defaults/apply-standard` — "Áp model chuẩn theo vai". */
export const useApplyStandard = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => defaultsApi.applyStandard(),
    onSuccess: () => refreshSettings(qc),
  });
};

/** `POST /defaults/reset-all` — cần mã PIN `defaults.reset_all`: 423 ⇒ hộp PIN toàn cục tự mở rồi gửi lại. */
export const useResetAll = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => defaultsApi.resetAll(),
    onSuccess: () => refreshSettings(qc),
  });
};
