import type { ReactNode } from 'react';
import { Button } from './Button';
import { Icon } from './Icon';

export interface ErrorStateProps {
  title?: ReactNode;
  message?: ReactNode;
  onRetry?: () => void;
  retrying?: boolean;
  /** v0.1.41: chuỗi kỹ thuật (mã HTTP, mã lỗi) — nằm trong "Chi tiết kỹ thuật" đóng sẵn, dưới câu thân thiện. */
  detail?: string | null;
}

/** Error message + "Thử lại" (docs/handoff/07 §3). */
export function ErrorState({ title = 'Không tải được dữ liệu', message, onRetry, retrying, detail }: ErrorStateProps) {
  return (
    <div className="gh-state gh-state--error" role="alert">
      <div className="gh-state__tile">
        <Icon name="ph ph-warning-circle" size={17} />
      </div>
      <div className="gh-state__text">
        <div className="gh-state__title">{title}</div>
        {message ? <div className="gh-state__desc">{message}</div> : null}
        {typeof detail === 'string' && detail ? (
          <details className="tech-detail">
            <summary>Chi tiết kỹ thuật</summary>
            <code>{detail}</code>
          </details>
        ) : null}
        {onRetry ? (
          <div className="gh-state__actions">
            <Button variant="secondary" icon="ph ph-arrow-clockwise" onClick={onRetry} loading={retrying}>
              Thử lại
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
