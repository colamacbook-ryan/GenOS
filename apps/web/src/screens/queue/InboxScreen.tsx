import { useState } from 'react';
import { Link } from 'react-router-dom';
import { EVENT_INTENT_OPTIONS, type InboxItem, type InboxTab } from '@gen-harness/contracts';
import { Button, Card, Dialog, EmptyState, FilterSelect, Skeleton, Switch, Tabs, Tag, TextField, Tooltip, type TabItem } from '@gen-harness/ui';
import { WhyButton } from '../core/Evidence';
import { DataEmptyState } from '../DataEmptyState';
import { errorText } from '../../lib/errorText';
import { fmtAgo } from '../../lib/format';
import { useUrlState } from '../../lib/uiStore';
import { TRUNCATED_USERS_TEXT, useAssignees, useEmptyUsersText } from '../../lib/pickers';
import { CardError, InlineError, ScreenHead, SkeletonLines } from '../common';
import { itemTag, itemTagTone, priorityTone, triageBadges } from './queueModel';
import { LoadMore } from './LoadMore';
import { useInbox, useInboxAct, useInboxAssign, useInboxSilence } from './queries';

// v0.1.69 (F062): đủ 15 loại, một bảng chung (contracts `eventTypes.ts`) — trước đây chỉ 8 loại, tự khai ở đây.
const INTENT_OPTIONS = [...EVENT_INTENT_OPTIONS];

export function InboxScreen() {
  const [tab, setTab] = useUrlState<InboxTab>('tab', 'all');
  const [intent, setIntent] = useUrlState<string>('intent', '');
  const [hide, setHide] = useUrlState<'' | '1'>('hide', '');
  const q = useInbox(tab, intent, hide === '1');
  const triage = q.data?.triage;
  const [assignFor, setAssignFor] = useState<InboxItem | null>(null);
  const [silenceFor, setSilenceFor] = useState<InboxItem | null>(null);

  const items: TabItem<InboxTab>[] = (
    [
      ['all', 'Tất cả'],
      ['opportunity', 'Cơ hội'],
      ['alert', 'Cảnh báo'],
      ['approval', 'Chờ duyệt'],
      ['reply', 'Cần soạn'],
      ['candidate', 'Ứng viên'],
    ] as const
  ).map(([key, label]) => ({ key, label, count: q.data?.counts[key] }));

  return (
    <div className="screen">
      <ScreenHead
        title="Hộp thư"
        description="Không phải tin nhắn thô. Mỗi dòng là một ý chính rút ra từ tin nhắn: nguồn, người liên quan, điểm ưu tiên, tóm tắt hai câu, gợi ý việc nên làm và tin gốc làm chứng cứ."
        maxWidth={700}
        actions={
          <>
            {triage?.enabled ? (
              <span className="ib-hide" data-gen-target="inbox.hide_junk">
                <Switch checked={hide === '1'} onChange={(v) => setHide(v ? '1' : '')} label="Ẩn rác & trùng" />
                {/* v0.1.28 (UX V7): bấm vào chữ cũng bật/tắt được, không chỉ nút gạt nhỏ. */}
                <span aria-hidden className="ib-hide__text" onClick={() => setHide(hide === '1' ? '' : '1')}>
                  Ẩn rác &amp; trùng
                </span>
              </span>
            ) : null}
            <FilterSelect label="Ý định" value={intent} onChange={setIntent} options={INTENT_OPTIONS} />
          </>
        }
      />
      {hide === '1' && triage?.enabled && triage.hidden > 0 ? (
        <p className="ib-hidden-note" role="status">
          Đã ẩn {triage.hidden} mục trùng, rác hoặc điểm dưới {triage.min_score}.
        </p>
      ) : null}
      <Tabs items={items} value={tab} onChange={setTab} label="Tab hộp thư" idPrefix="inbox-tab" />

      {q.isPending ? (
        <div className="ib-list">
          {Array.from({ length: 4 }, (_, i) => (
            <Card key={i}>
              <Skeleton width={120} height={12} />
              <Skeleton width="70%" height={16} style={{ marginTop: 10 }} />
              <Skeleton width="90%" height={10} style={{ marginTop: 8 }} />
            </Card>
          ))}
        </div>
      ) : q.isError && !q.isFetchNextPageError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : q.data.items.length === 0 ? (
        <DataEmptyState
          filtered={tab !== 'all' || intent !== '' || hide === '1'}
          fallback={<EmptyState icon="ph ph-tray" title="Hộp thư đang trống" description="Không có tin, cảnh báo hay bản nháp nào khớp bộ lọc hiện tại." />}
        />
      ) : (
        <>
          <div className="ib-list">
            {q.data.items.map((item) => (
              <InboxCard key={item.id} item={item} onAssign={() => setAssignFor(item)} onSilence={() => setSilenceFor(item)} />
            ))}
          </div>
          <LoadMore
            shown={q.data.items.length}
            // Số trên tab đếm theo tab (và "Ẩn rác"), không theo ô Ý định ⇒ đang lọc ý định thì không biết tổng, chỉ ghi số đã xem.
            total={intent ? null : (q.data.counts[tab] ?? null)}
            hasMore={q.hasNextPage}
            loading={q.isFetchingNextPage}
            onMore={() => void q.fetchNextPage()}
            error={q.isFetchNextPageError ? q.error : null}
            testId="inbox-xem-them"
          />
        </>
      )}

      {assignFor ? <AssignDialog item={assignFor} onClose={() => setAssignFor(null)} /> : null}
      {silenceFor ? <SilenceDialog item={silenceFor} onClose={() => setSilenceFor(null)} /> : null}
    </div>
  );
}

function InboxCard({ item, onAssign, onSilence }: { item: InboxItem; onAssign: () => void; onSilence: () => void }) {
  const [composing, setComposing] = useState(false);
  const [text, setText] = useState('');
  const act = useInboxAct();

  const submitReply = () => {
    if (!text.trim()) return;
    act.mutate(
      { id: item.id, body: { text } },
      {
        onSuccess: () => {
          setComposing(false);
          setText('');
        },
      },
    );
  };

  return (
    <article className="ib-card" aria-label={item.title} data-gen-target={`inbox.row:${item.id}`}>
      <div className="ib-card__head">
        {/* v0.1.43 (F-30): độ tin cậy không hiện thường trực — rê chuột/focus huy hiệu ưu tiên mới thấy. */}
        {item.confidence_band ? (
          <Tooltip content={`Độ tin cậy: ${item.confidence_band}`}>
            <span
              className="ib-card__prio"
              style={{ color: priorityTone(item.priority) }}
              tabIndex={0}
              aria-label={`Ưu tiên ${item.priority} · Độ tin cậy: ${item.confidence_band}`}
              data-confidence={item.confidence_band}
            >
              {item.priority}
            </span>
          </Tooltip>
        ) : (
          <span className="ib-card__prio" style={{ color: priorityTone(item.priority) }}>
            {item.priority}
          </span>
        )}
        {/* v0.1.28 (UX V8): mục nghi rác không còn mang nhãn "Cơ hội" cùng lúc với "Rác". */}
        <span
          className="ib-card__tag"
          style={item.triage?.spam ? { color: 'var(--color-neutral-500)', borderColor: 'var(--color-neutral-700)' } : { color: itemTagTone(item), borderColor: itemTagTone(item) }}
        >
          {item.triage?.spam ? 'Nghi rác' : itemTag(item)}
        </span>
        {item.code ? <span className="mono">{item.code}</span> : null}
        {triageBadges(item.triage).map((b) => (
          <span key={b.key} title={b.title}>
            <Tag tone={b.tone} className="ib-card__triage">
              {b.label}
            </Tag>
          </span>
        ))}
        {item.group ? <span className="ib-card__meta">{item.group.name}</span> : null}
        <span className="ib-card__meta">{fmtAgo(item.created_at)}</span>
        <span style={{ flex: 1 }} />
        {item.score !== null ? (
          <span className="ib-card__score" title="Độ ưu tiên: mức đáng xử lý trước, 0–100">
            ưu tiên <b>{Math.round(item.score * 100)}</b>/100
          </span>
        ) : null}
      </div>
      <h3 className="ib-card__title">{item.summary || item.title}</h3>
      {item.suggested_action ? <p className="ib-card__suggest">Gợi ý: {item.suggested_action}</p> : null}

      {composing ? (
        <div className="ib-compose">
          <TextField label="Nội dung trả lời" value={text} onChange={(e) => setText(e.target.value)} placeholder="Soạn nội dung gửi cho khách…" />
          <div className="ib-compose__actions">
            <Button variant="primary" icon="ph ph-paper-plane-tilt" loading={act.isPending} onClick={submitReply} disabled={!text.trim()}>
              Tạo bản nháp trả lời
            </Button>
            <Button variant="ghost" onClick={() => setComposing(false)}>
              Huỷ
            </Button>
          </div>
          {act.isError ? <InlineError>{errorText(act.error)}</InlineError> : null}
        </div>
      ) : (
        <div className="ib-card__actions">
          {item.item_type === 'unit' ? (
            <Button variant="secondary" icon="ph ph-chat-circle-text" onClick={() => setComposing(true)}>
              Soạn trả lời
            </Button>
          ) : item.item_type === 'alert' ? (
            <Button
              variant="secondary"
              icon="ph ph-check-circle"
              loading={act.isPending}
              onClick={() => act.mutate({ id: item.id, body: { create_task: true } })}
            >
              Xác nhận đã xử lý + tạo việc theo dõi
            </Button>
          ) : (
            <Link to={`/workbench?id=${encodeURIComponent(item.id)}`} className="gh-btn gh-btn--secondary">
              Mở trong Bàn làm việc
            </Link>
          )}
          <Button variant="ghost" icon="ph ph-user-switch" onClick={onAssign}>
            Giao cho người khác
          </Button>
          <Button variant="ghost" icon="ph ph-speaker-slash" onClick={onSilence}>
            Im lặng có chủ đích
          </Button>
          <span style={{ flex: 1 }} />
          <WhyButton kind={item.item_type === 'unit' ? 'meaning_unit' : item.item_type} id={item.id} size="sm">
            Vì sao hệ thống nghĩ vậy
          </WhyButton>
        </div>
      )}
      {act.isError && !composing ? <InlineError>{errorText(act.error)}</InlineError> : null}
    </article>
  );
}

function AssignDialog({ item, onClose }: { item: InboxItem; onClose: () => void }) {
  const assign = useInboxAssign();
  const { options, hasOthers, truncated, query: people } = useAssignees();
  const emptyText = useEmptyUsersText();
  return (
    <Dialog
      open
      onClose={onClose}
      width={380}
      title="Giao cho người khác"
      kicker={item.title}
      actions={
        <Button variant="secondary" onClick={onClose}>
          Đóng
        </Button>
      }
    >
      {people.isPending ? (
        <SkeletonLines rows={3} />
      ) : people.isError ? (
        <InlineError>{errorText(people.error)}</InlineError>
      ) : (
        <div className="dlg-list" role="list">
          {options.map((u) => (
            <button
              key={u.id}
              type="button"
              className="sv-open"
              disabled={assign.isPending}
              onClick={() =>
                assign.mutate(
                  { id: item.id, userId: u.id },
                  {
                    onSuccess: onClose,
                  },
                )
              }
            >
              <span className="sv-open__name">{u.label}</span>
            </button>
          ))}
          {!hasOthers ? <p className="muted-note">{emptyText}</p> : null}
          {truncated ? <p className="muted-note">{TRUNCATED_USERS_TEXT}</p> : null}
        </div>
      )}
      {assign.isError ? <InlineError>{errorText(assign.error)}</InlineError> : null}
    </Dialog>
  );
}

function SilenceDialog({ item, onClose }: { item: InboxItem; onClose: () => void }) {
  const silence = useInboxSilence();
  const [reason, setReason] = useState('');
  return (
    <Dialog
      open
      onClose={onClose}
      width={420}
      title="Im lặng có chủ đích"
      kicker={item.title}
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button
            variant="primary"
            icon="ph ph-speaker-slash"
            loading={silence.isPending}
            onClick={() => silence.mutate({ id: item.id, reason: reason || null, until: null }, { onSuccess: onClose })}
          >
            Im lặng mục này
          </Button>
        </>
      }
    >
      <div className="dlg-fields">
        <TextField label="Lý do (không bắt buộc)" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Đã xử lý ngoài hệ thống, không cần nhắc lại…" />
        {silence.isError ? <InlineError>{errorText(silence.error)}</InlineError> : null}
      </div>
    </Dialog>
  );
}
