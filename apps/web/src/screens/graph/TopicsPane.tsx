import { EmptyState, Icon } from '@gen-harness/ui';
import { CardError, SkeletonLines } from '../common';
import { fmtDMClock, fmtInt } from '../../lib/format';
import { useUrlState } from '../../lib/uiStore';
import { NodeGraphPane } from './NodeGraphPane';
import { ACC4, STATE_LABEL, stateTone } from './graphModel';
import { useGraphTopics } from './queries';

/** Chế độ 4 — Luồng chủ đề: danh sách luồng (`GET /graph/topics`) → bấm một luồng mở đồ thị Người↔Người lọc
 * theo `topic` đó (`GET /graph/topics/{topic}`, tái dùng `NodeGraphPane`). */
export function TopicsPane() {
  const [topic, setTopic] = useUrlState<string>('topic', '');
  const topics = useGraphTopics(100);

  if (topic) {
    return (
      <div className="gp-wrap">
        <button type="button" className="gp-back" onClick={() => setTopic('')}>
          <Icon name="ph ph-arrow-left" size={13} /> Tất cả luồng chủ đề
        </button>
        <div className="gp-topic-head">
          <Icon name="ph ph-flow-arrow" size={16} color={ACC4} />
          <span className="gp-topic-head__name">{topic}</span>
        </div>
        <NodeGraphPane kind="people" topic={topic} />
      </div>
    );
  }

  if (topics.isPending) return <SkeletonLines rows={6} />;
  if (topics.isError) return <CardError error={topics.error} onRetry={() => void topics.refetch()} retrying={topics.isFetching} />;
  if (topics.data.items.length === 0) return <EmptyState icon="ph ph-flow-arrow" title="Chưa có luồng chủ đề nào" description="Luồng chủ đề gộp các cạnh Người↔Người cùng sản phẩm được nhắc nhiều nhất — sẽ hiện khi có đủ hội thoại." />;

  return (
    <div className="table-card">
      <div className="gh-table-scroll">
        <table className="gh-table w920" aria-label="Luồng chủ đề">
          <thead>
            <tr>
              <th>Chủ đề</th>
              <th style={{ width: 100 }}>Số cạnh</th>
              <th style={{ width: 100 }}>Số người</th>
              <th style={{ width: 120 }}>Tổng trọng số</th>
              <th style={{ width: 150 }}>Chạm gần nhất</th>
              <th style={{ width: 110 }}>Trạng thái</th>
            </tr>
          </thead>
          <tbody>
            {topics.data.items.map((t) => (
              <tr key={t.topic} className="row-click" onClick={() => setTopic(t.topic)} tabIndex={0} role="button" aria-label={`Mở luồng ${t.topic}`}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    setTopic(t.topic);
                  }
                }}
              >
                <td style={{ fontSize: 12.5, color: 'var(--color-text)' }}>{t.topic}</td>
                <td className="td-id">{fmtInt(t.edges)}</td>
                <td className="td-id">{fmtInt(t.people)}</td>
                <td className="td-id">{t.total_weight.toFixed(1)}</td>
                <td className="td-id">{fmtDMClock(t.last_at)}</td>
                <td>
                  <span className="state-chip" style={{ color: stateTone(t.state), borderColor: stateTone(t.state) }}>
                    <span className="state-chip__dot" style={{ background: stateTone(t.state) }} />
                    {STATE_LABEL[t.state]}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
