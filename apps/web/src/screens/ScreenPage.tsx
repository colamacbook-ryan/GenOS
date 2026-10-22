import { useEffect, type ComponentType } from 'react';
import { SCREEN_BY_KEY } from '@gen-harness/contracts';
import { Card, EmptyState, ErrorState, Skeleton } from '@gen-harness/ui';
import { errorText } from '../lib/errorText';
import { useNavigation } from '../lib/queries';
import { screenKeys } from '../shell/navModel';
import { CleanScreen } from './data/CleanScreen';
import { IdentityScreen } from './data/IdentityScreen';
import { ListenLogScreen } from './data/ListenLogScreen';
import { RawScreen } from './data/RawScreen';
import { RulesScreen } from './data/RulesScreen';
import { SystemScreen } from './system/SystemScreen';
import { SCREENS as P3_CORE } from './core';
import { SCREENS as P3_QUEUE } from './queue';
import { SCREENS as P3_RELATIONS } from './relations';
import { SCREENS as P3_GRAPH } from './graph';
import { SCREENS as P3_MARKET } from './market';
import { SCREENS as P3_PEOPLE } from './people';
import { SCREENS as P4_AGENTS } from './agents';
import { SCREENS as P4_API } from './api';
import { SCREENS as P4_MCP } from './mcp';
import { SCREENS as P4_PLUGINS } from './plugins';
import { SCREENS as CONNECTIONS } from './connections';
import { SCREENS as TEAM } from './team';

/** Screens built so far (phase 2, then each phase-3 cluster registers its own). Every other key keeps the placeholder. */
const BUILT: Record<string, ComponentType> = {
  raw: RawScreen,
  listenlog: ListenLogScreen,
  rules: RulesScreen,
  clean: CleanScreen,
  identity: IdentityScreen,
  system: SystemScreen,
  ...P3_CORE,
  ...P3_QUEUE,
  ...P3_RELATIONS,
  ...P3_GRAPH,
  ...P3_MARKET,
  ...P3_PEOPLE,
  ...P4_AGENTS,
  ...P4_API,
  ...P4_MCP,
  ...P4_PLUGINS,
  // v0.1.42 (F-7): Kết nối, Đội ngũ.
  ...CONNECTIONS,
  ...TEAM,
};

/** Screen-title row: title 20px/500 + description 12.5px neutral-400 (docs/01 "Quy ước chung"). */
export function ScreenTitle({ title, description, maxWidth }: { title: string; description: string; maxWidth: number }) {
  return (
    <div className="screen-title-row">
      <div className="screen-title-block" style={{ maxWidth }}>
        <h2 className="screen-title">{title}</h2>
        <p className="screen-desc">{description}</p>
      </div>
    </div>
  );
}

/**
 * Built screens render from the registry; the rest show their title row and a
 * placeholder card — no fake data. A screen the role cannot see (absent from
 * GET /navigation) says so.
 */
export function ScreenPage({ screenKey }: { screenKey: string }) {
  const meta = SCREEN_BY_KEY[screenKey];
  const nav = useNavigation();

  useEffect(() => {
    document.title = `${meta.title} · Gen-Harness`;
  }, [meta.title]);

  // Thiết kế không vẽ dòng tiêu đề cho overview, workbench, profile (header đã mang tiêu đề) → giữ nguyên như vậy.
  const title = meta.designTitleRow ? (
    <ScreenTitle title={meta.title} description={meta.description} maxWidth={meta.descMaxWidth} />
  ) : null;

  if (nav.isPending) {
    return (
      <div className="screen" aria-busy="true">
        {meta.designTitleRow && <div className="screen-title-row">
          <div className="screen-title-block" style={{ maxWidth: meta.descMaxWidth }}>
            <Skeleton width={220} height={20} />
            <Skeleton width="80%" height={12} style={{ marginTop: 9 }} />
            <Skeleton width="55%" height={12} style={{ marginTop: 6 }} />
          </div>
        </div>}
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
        {title}
        <Card padded={false}>
          <ErrorState message={errorText(nav.error)} onRetry={() => void nav.refetch()} retrying={nav.isFetching} />
        </Card>
      </div>
    );
  }

  const allowed = screenKeys(nav.data).has(screenKey);
  const Built = BUILT[screenKey];
  if (Built && allowed) return <Built />;
  return (
    <div className="screen">
      {title}
      <Card padded={false} aria-label={meta.name}>
        {allowed ? (
          <EmptyState
            icon="ph ph-hourglass-medium"
            title="Màn hình này được dựng ở giai đoạn sau"
            description="Khung, danh mục và quyền truy cập đã sẵn sàng. Dữ liệu thật sẽ hiện ở đây khi API của màn này hoàn tất."
          />
        ) : (
          <EmptyState
            icon="ph ph-lock-simple"
            title="Vai trò của bạn không có quyền xem màn này"
            description="Danh mục chỉ hiện những màn được cấp. Liên hệ Owner nếu cần mở quyền."
          />
        )}
      </Card>
    </div>
  );
}
