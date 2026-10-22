import { useQuery } from '@tanstack/react-query';
import { Dialog, EmptyState } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { fmtDMClock } from '../../lib/format';
import { useOrgTimezone } from '../../lib/permissions';
import { CardError, SkeletonLines, StateChip } from '../common';
import { BAD, N4, OK, WARN } from '../data/dataModel';

const PIN_ACTION_LABEL: Record<string, { label: string; tone: string }> = {
  'auth.pin_verified': { label: 'Nhập đúng', tone: OK },
  'auth.pin_failed': { label: 'Nhập sai', tone: WARN },
  'auth.pin_locked': { label: 'Khoá 15 phút', tone: BAD },
  'auth.pin_attempt_while_locked': { label: 'Nhập khi đang khoá', tone: BAD },
  'auth.pin_not_set': { label: 'Chưa đặt PIN', tone: WARN },
  // Chỉ còn trong nhật ký cũ: từ v0.1.61 đổi PIN đi qua Tài khoản của tôi (ghi account.pin_changed), không vào bảng này.
  'auth.pin_changed': { label: 'Đổi PIN', tone: N4 },
};

/**
 * v0.1.42 (F-61): thẻ mã PIN chỉ còn ở một chỗ — Tài khoản của tôi (AccountPage). File này chỉ giữ hộp "Lịch sử nhập
 * PIN" (đọc Nhật ký hành động) để thẻ đó mở khi vai trò có `audit.read`.
 */
export function PinHistoryDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const tz = useOrgTimezone();
  const q = useQuery({
    queryKey: ['audit', 'pin'],
    queryFn: () => api.audit.list({ action: 'auth.pin', limit: 30 }),
    enabled: open,
  });
  return (
    <Dialog open={open} onClose={onClose} width={480} title="Lịch sử nhập mã PIN (cả tổ chức)" kicker="30 lần gần nhất của mọi người dùng · từ Nhật ký hành động">
      {q.isPending ? (
        <SkeletonLines rows={4} padding="0" />
      ) : q.isError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : q.data.items.length === 0 ? (
        <EmptyState icon="ph ph-password" title="Chưa có lần nhập mã PIN nào" />
      ) : (
        <div>
          {q.data.items.map((a) => {
            const l = PIN_ACTION_LABEL[a.action] ?? { label: a.action, tone: N4 };
            return (
              <div className="profile-row" key={a.id}>
                <span className="td-id" style={{ width: 110, flex: 'none' }}>
                  {fmtDMClock(a.at, tz)}
                </span>
                <span style={{ flex: 1, minWidth: 0, fontSize: 11.5, color: 'var(--color-neutral-300)' }}>{a.actor_label ?? '—'}</span>
                <StateChip color={l.tone} border={l.tone === N4 ? 'var(--color-neutral-800)' : l.tone}>
                  {l.label}
                </StateChip>
              </div>
            );
          })}
        </div>
      )}
    </Dialog>
  );
}
