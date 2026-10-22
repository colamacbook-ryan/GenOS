import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from './api';
import { useCan } from './permissions';

/**
 * Ô chọn người / trợ lý THẬT (v0.1.35, F-1) — dùng chung cho «Giao cho người khác» (Hộp thư), «Gán người xử lý»
 * (Vụ việc), «Gán BOT trực nhóm» / BOT của một người (Nhóm & Con người) và bộ lọc «Phụ trách» (Bản đồ quan hệ).
 * Không bao giờ quay về danh sách cứng: lỗi → màn hiện lỗi, rỗng → màn hiện hướng dẫn.
 */
export const qkPickers = {
  users: ['pickers', 'users'] as const,
  agents: ['pickers', 'agents'] as const,
};

export interface PickerOption {
  id: string;
  label: string;
}

/** Nhãn của người đang đăng nhập trong mọi ô chọn người. */
export const ME_LABEL = 'Tôi';
/** Gợi ý khi danh sách rỗng — đường dẫn đầy đủ cho người CÓ quyền; người không có quyền được bảo nhờ Owner. */
export const EMPTY_USERS_TEXT = 'Chưa có người dùng nào khác — mời thêm ở Đội ngũ › Người dùng';
export const EMPTY_USERS_TEXT_ASK = 'Chưa có người dùng nào khác — nhờ Owner mời thêm người dùng';
/** Danh sách người bị cắt ở trần của API (`truncated: true`) — báo rõ thay vì im lặng thiếu người. */
export const TRUNCATED_USERS_TEXT = 'Chỉ hiện 500 người đầu theo tên — người cần tìm có thể nằm ngoài danh sách này';
export const EMPTY_AGENTS_TEXT = 'Chưa có trợ lý nào đang bật — tạo ở màn Danh tính Agent';
export const EMPTY_AGENTS_TEXT_ASK = 'Chưa có trợ lý nào đang bật — nhờ Owner tạo ở màn Danh tính Agent';

/** Câu gợi ý khi không có người nào khác: mời người dùng cần `roles.manage` (apps/api/gh/auth/users.py). */
export function useEmptyUsersText(): string {
  return useCan('roles.manage') ? EMPTY_USERS_TEXT : EMPTY_USERS_TEXT_ASK;
}

/** Câu gợi ý khi không có trợ lý nào bật: tạo/bật Danh tính Agent cần `system.manage`. */
export function useEmptyAgentsText(): string {
  return useCan('system.manage') ? EMPTY_AGENTS_TEXT : EMPTY_AGENTS_TEXT_ASK;
}

const STALE_MS = 60_000;

/** Người có thể được giao việc — người đang đăng nhập là "Tôi" và đứng đầu; còn lại giữ thứ tự tên của API. */
export function useAssignees(opts: { enabled?: boolean } = {}) {
  const query = useQuery({
    queryKey: qkPickers.users,
    queryFn: ({ signal }) => api.pickers.users(signal),
    staleTime: STALE_MS,
    enabled: opts.enabled ?? true,
  });
  const data = query.data;
  const options = useMemo<PickerOption[]>(() => {
    const items = data?.items ?? [];
    return [
      ...items.filter((u) => u.me).map((u) => ({ id: u.id, label: ME_LABEL })),
      ...items.filter((u) => !u.me).map((u) => ({ id: u.id, label: u.name })),
    ];
  }, [data]);
  /** Có người nào khác ngoài người đang đăng nhập không (để hiện gợi ý "mời thêm"). */
  const hasOthers = (data?.items ?? []).some((u) => !u.me);
  /** API cắt danh sách ở trần 500 người. */
  const truncated = data?.truncated === true;
  return { options, hasOthers, truncated, query };
}

/** Trợ lý (agent) đang bật — để gán BOT. */
export function useAgentOptions(opts: { enabled?: boolean } = {}) {
  const query = useQuery({
    queryKey: qkPickers.agents,
    queryFn: ({ signal }) => api.pickers.agents(signal),
    staleTime: STALE_MS,
    enabled: opts.enabled ?? true,
  });
  const data = query.data;
  const options = useMemo<PickerOption[]>(() => (data?.items ?? []).map((a) => ({ id: a.id, label: a.name })), [data]);
  return { options, query };
}
