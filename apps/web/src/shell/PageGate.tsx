import type { ReactNode } from 'react';
import { Card, EmptyState, ErrorState, Skeleton } from '@gen-harness/ui';
import { errorText } from '../lib/errorText';
import { useNavigation } from '../lib/queries';
import { screenKeys } from './navModel';

/** v0.1.78 (G0): cổng quyền của một trang — khoá màn phải có trong GET /navigation của vai trò (như ScreenPage cũ). */
export function PageGate({ screenKey, children }: { screenKey: string; children: ReactNode }) {
  const nav = useNavigation();
  if (nav.isPending) {
    return (
      <div className="screen" aria-busy="true">
        <Card padded={false}>
          <div className="gh-state">
            <Skeleton width={34} height={34} radius={8} />
            <div style={{ flex: 1 }}>
              <Skeleton width={260} height={12} />
              <Skeleton width={380} height={10} style={{ marginTop: 8 }} />
            </div>
          </div>
        </Card>
      </div>
    );
  }
  if (nav.isError) {
    return (
      <div className="screen">
        <Card padded={false}>
          <ErrorState message={errorText(nav.error)} onRetry={() => void nav.refetch()} retrying={nav.isFetching} />
        </Card>
      </div>
    );
  }
  if (!screenKeys(nav.data).has(screenKey)) {
    return (
      <div className="screen">
        <Card padded={false}>
          <EmptyState
            icon="ph ph-lock-simple"
            title="Vai trò của bạn không có quyền xem màn này"
            description="Danh mục chỉ hiện những màn được cấp. Liên hệ Owner nếu cần mở quyền."
          />
        </Card>
      </div>
    );
  }
  return <>{children}</>;
}
