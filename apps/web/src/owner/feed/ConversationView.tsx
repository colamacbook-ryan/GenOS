/**
 * v0.1.59 (G4) — HỘI THOẠI quanh một tin (`GET /owner/feed/{id}/context`, qua `useOwnerFeedContext`). Tối đa 10 tin: 5 trước, tin chính,
 * 4 sau, cùng kênh/nhóm. Bong bóng chat: tin ĐẾN (inbound) bên trái, tin ĐI (outbound) bên phải; mỗi bong bóng có tên người gửi + giờ;
 * tin chính tô nền `info` và gắn nhãn "Tin này". Không có hội thoại gốc (`messages` rỗng) ⇒ "Chưa có hội thoại gốc cho tin này".
 * Lỗi chỉ của khối này (OwnerError + Thử lại). Mọi chuỗi qua `asText` — không bao giờ render object.
 */
import type { FeedContextMessage } from '@gen-harness/contracts';
import { Badge, Skeleton, cx } from '@gen-harness/ui';
import { fmtHM } from '../../lib/format';
import { asText } from '../ownerModel';
import { OwnerError } from '../parts';
import { useOwnerFeedContext } from '../queries';
import './feed.css';

function Bubble({ m }: { m: FeedContextMessage }) {
  const out = m.direction === 'outbound';
  const sender = asText(m.sender) || (out ? 'Bên mình' : 'Khách');
  const at = fmtHM(asText(m.at) || null);
  return (
    <li
      className={cx('conv-msg', out ? 'conv-msg--out' : 'conv-msg--in', m.is_center && 'conv-msg--center')}
      data-testid="conv-msg"
      data-dir={out ? 'out' : 'in'}
      data-center={m.is_center ? 'true' : undefined}
      aria-label={m.is_center ? 'Tin chính' : undefined}
    >
      <span className="conv-msg__meta">
        <span className="conv-msg__who">{sender}</span>
        {at !== '—' ? <span className="conv-msg__at">{at}</span> : null}
        {m.is_center ? (
          <Badge kind="status" tone="info">
            Tin này
          </Badge>
        ) : null}
      </span>
      <span className="conv-msg__text">{asText(m.text)}</span>
    </li>
  );
}

export function ConversationView({ id, summary }: { id: string; summary?: string }) {
  const q = useOwnerFeedContext(id);
  const messages = Array.isArray(q.data?.messages) ? q.data.messages : [];
  return (
    <section className="feed-block" aria-label="Hội thoại" data-testid="feed-conv">
      <div className="feed-block__head">
        <h3 className="feed-block__title">Hội thoại</h3>
        {messages.length > 0 ? <span className="feed-block__aux">{messages.length} tin quanh tin này</span> : null}
      </div>
      {q.isPending ? (
        <div className="conv conv--skel" aria-busy="true" aria-label="Đang tải hội thoại" data-testid="conv-loading">
          <Skeleton width="70%" height={56} radius={8} />
          <Skeleton width="60%" height={56} radius={8} style={{ alignSelf: 'flex-end' }} />
          <Skeleton width="70%" height={56} radius={8} />
        </div>
      ) : q.isError ? (
        <OwnerError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : messages.length === 0 ? (
        <div data-testid="conv-empty">
          <p className="feed-muted">Chưa có hội thoại gốc cho tin này</p>
          {summary ? <p className="feed-quote">{summary}</p> : null}
        </div>
      ) : (
        <ol className="conv" aria-label="Hội thoại quanh tin này" data-testid="conv-list">
          {messages.map((m, i) => (
            <Bubble key={asText(m.id) || i} m={m} />
          ))}
        </ol>
      )}
    </section>
  );
}
