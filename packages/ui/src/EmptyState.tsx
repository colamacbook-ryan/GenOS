import type { ReactNode } from 'react';
import { cx } from './cx';
import { Icon } from './Icon';

export interface EmptyStateProps {
  icon?: string;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  /** v0.1.59: cách viết số ít của `actions` — chuẩn Mặt tiền là icon + MỘT câu + MỘT nút. Có cả hai thì hiện cả hai. */
  action?: ReactNode;
  className?: string;
  'data-testid'?: string;
}

/**
 * Trạng thái rỗng. Chuẩn Mặt tiền (v0.1.59): icon + một câu (`title`) + một nút việc tiếp (`action`); `description` là câu
 * giải thích thêm khi cần. Trong vùng `.kit-scope` (owner-kit.css) khối canh giữa theo mock; ngoài vùng đó — cả Console —
 * vẽ y như cũ (props cũ `icon`/`title`/`description`/`actions` giữ nguyên).
 */
export function EmptyState({ icon = 'ph ph-hourglass-medium', title, description, actions, action, className, 'data-testid': testId }: EmptyStateProps) {
  const buttons = actions || action ? (
    <div className="gh-state__actions">
      {actions}
      {action}
    </div>
  ) : null;
  return (
    <div className={cx('gh-state', 'gh-state--empty', className)} data-testid={testId}>
      <div className="gh-state__tile">
        <Icon name={icon} size={17} />
      </div>
      <div className="gh-state__text">
        <div className="gh-state__title">{title}</div>
        {description ? <div className="gh-state__desc">{description}</div> : null}
        {buttons}
      </div>
    </div>
  );
}
