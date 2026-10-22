import { Button, Segmented } from '@gen-harness/ui';
import { ScreenHead } from '../common';
import { errorText } from '../../lib/errorText';
import { useUrlState } from '../../lib/uiStore';
import { ListPane } from './ListPane';
import { NodeGraphPane } from './NodeGraphPane';
import { TopicsPane } from './TopicsPane';
import { MODE_OPTIONS, type GraphModeKey } from './graphModel';
import { useRecomputeGraph } from './queries';

/**
 * Bản đồ quan hệ (`graph`) — một màn, bốn chế độ (PLAN §3.5): danh sách (bộ lọc mạnh), Người↔Người,
 * Nhóm↔Nhóm, Luồng chủ đề. Ba chế độ sau cộng đồ thị lực tương tác (xem `GraphCanvas.tsx`).
 */
export function GraphScreen() {
  const [mode, setMode] = useUrlState<GraphModeKey>('mode', 'list');
  const recompute = useRecomputeGraph();

  return (
    <div className="screen">
      <ScreenHead
        title="Bản đồ quan hệ"
        description="Không phải danh bạ. Trọng số của mỗi quan hệ thay đổi theo tần suất, chiều tương tác và giai đoạn — nên thấy được ai là cầu nối, khách nào đang lạnh, ai đang ôm quá nhiều việc."
        maxWidth={700}
        actions={
          <div className="gp-head-actions">
            <Segmented options={MODE_OPTIONS} value={mode} onChange={setMode} label="Chế độ bản đồ quan hệ" />
            <Button
              variant="secondary"
              size="sm"
              icon="ph ph-arrows-clockwise"
              loading={recompute.isPending}
              onClick={() => recompute.mutate()}
            >
              Dựng lại
            </Button>
          </div>
        }
      />
      {recompute.isSuccess ? (
        <div className="gp-recompute-ok" role="status">
          Đã dựng lại — {recompute.data.counts.interacts} cạnh Người↔Người · {recompute.data.counts.shares_members} cạnh Nhóm↔Nhóm ·{' '}
          {recompute.data.counts.bridges} cầu nối.
        </div>
      ) : recompute.isError ? (
        <div className="gp-recompute-err" role="alert">
          {errorText(recompute.error)}
        </div>
      ) : null}

      {mode === 'list' ? <ListPane /> : mode === 'people' ? <NodeGraphPane kind="people" /> : mode === 'groups' ? <NodeGraphPane kind="groups" /> : <TopicsPane />}
    </div>
  );
}
