import { Chip } from '@gen-harness/ui';
import { CHANGED_CHIP, DEFAULT_CHIP, findDefault } from './defaultsModel';
import { useDefaults } from './queries';

/**
 * v0.1.55 — chip trạng thái của một cài đặt: "Mặc định" (đang dùng chuẩn) hoặc "Đã đổi" (Sếp đã đổi khác mặc định).
 * Thuần hiển thị: `customized` do máy chủ tính ra, không lưu cờ riêng.
 */
export function DefaultBadge({ customized, testId = 'default-badge' }: { customized: boolean; testId?: string }) {
  return (
    <span data-testid={testId} data-state={customized ? 'changed' : 'default'}>
      <Chip tone={customized ? 'warn' : 'neutral'}>{customized ? CHANGED_CHIP : DEFAULT_CHIP}</Chip>
    </span>
  );
}

/**
 * Chip của MỘT khoá trong sổ mặc định (`GET /defaults`, chỉ Owner). Vai trò khác, đang tải, lỗi hoặc máy chủ chưa có khoá ấy ⇒
 * không vẽ gì (chip không bao giờ chặn thẻ cài đặt).
 */
export function DefaultChip({ itemKey }: { itemKey: string }) {
  const q = useDefaults();
  const item = findDefault(q.data, itemKey);
  if (!item) return null;
  return <DefaultBadge customized={item.customized} testId={`default-badge-${itemKey}`} />;
}
