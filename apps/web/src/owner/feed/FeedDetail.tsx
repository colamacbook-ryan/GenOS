/**
 * v0.1.59 (G4) — khung NGỮ CẢNH của một tin (cột phải 7/12 trên máy tính; ngăn chi tiết toàn màn trên điện thoại).
 *
 * Từ trên xuống: đầu khung (người / nhóm / kênh / thời điểm + "Mở hồ sơ" → /owner/quan-he?nguoi=<id>) · Hội thoại (ConversationView) ·
 * Chứng cứ Gen trích (`GET /inbox/{id}` → units) · Về khách này (`GET /profile/{person_id}`: 3 điểm kèm lời + tóm tắt 5 dòng) · Gen gợi ý
 * (`suggested_action`) · THANH HÀNH ĐỘNG dính đáy:
 *   tin (unit)   — Trả lời (ReplyBox → nháp → DraftDecide "Duyệt và gửi" có PIN; KHÔNG có đường gửi thẳng) · Tạo nhắc · Tắt tiếng
 *   cảnh báo     — Ghi nhận · Tạo nhắc · Tắt tiếng
 *   nháp chờ duyệt — DraftDecide (vì `/act` trả 409 USE_WORKBENCH)
 * Mỗi khối lỗi riêng (OwnerError / CardError) — khối khác vẫn chạy. Mọi chuỗi qua `asText`. Gen chỉ đề xuất: không nút nào gửi tin trực tiếp.
 */
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import type { DraftDetail, FeedItem } from '@gen-harness/contracts';
import { Badge, Icon, Skeleton } from '@gen-harness/ui';
import { errorDetail, errorText } from '../../lib/errorText';
import { InlineError } from '../../screens/common';
import { DraftDecide } from '../actions/DraftDecide';
import { useAckItem, useSilenceItem } from '../actions/useOwnerActions';
import { avatarText } from '../ownerModel';
import { OwnerError } from '../parts';
import { ScoreRows, SummaryList } from '../relations/ProfilePanel';
import { RemindButton } from '../relations/RemindButton';
import { personTypeLabel, profileSubtitle, scoreLines, summaryLines, useOwnerProfile } from '../relations/relationsModel';
import { ConversationView } from './ConversationView';
import { ReplyBox } from './ReplyBox';
import { detailHead, draftDoneText, evidenceLines, headSubtitle, profileLink, useOwnerInboxDetail } from './feedModel';
import './feed.css';

export interface FeedDetailProps {
  id: string;
  /** Dòng tin trong danh sách (nếu có); mở thẳng bằng `?tin=` mà tin không nằm trong danh sách đang xem thì thiếu — lấy đầu khung từ Hộp thư. */
  item?: FeedItem;
  /** Tin đã được xử lý (tắt tiếng / ghi nhận) ⇒ khung chi tiết đóng, danh sách hiện câu báo. */
  onResolved: (message: string) => void;
}

/** "Về khách này": 3 điểm kèm lời + tóm tắt 5 dòng. Lỗi riêng khối này. */
function AboutCustomer({ personId, personName }: { personId: string; personName: string }) {
  const q = useOwnerProfile(personId);
  const profile = q.data;
  return (
    <section className="feed-block" aria-label="Về khách này" data-testid="feed-about">
      <div className="feed-block__head">
        <h3 className="feed-block__title">Về khách này</h3>
        {profile ? <span className="feed-block__aux">{profileSubtitle(profile, personTypeLabel(profile.person?.type))}</span> : null}
      </div>
      {q.isPending ? (
        <div className="feed-skel" aria-busy="true" aria-label="Đang tải hồ sơ khách" data-testid="about-loading">
          <Skeleton width="100%" height={44} radius={8} />
          <Skeleton width="100%" height={44} radius={8} />
        </div>
      ) : q.isError ? (
        <OwnerError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : scoreLines(profile?.scores).length === 0 && summaryLines(profile).length === 0 ? (
        <p className="feed-muted">Gen chưa có đủ dữ liệu về {personName || 'khách này'}.</p>
      ) : (
        <>
          <ScoreRows lines={scoreLines(profile?.scores)} />
          <SummaryList profile={profile} testId="about-summary" />
        </>
      )}
    </section>
  );
}

/** "Chứng cứ Gen trích": các ý Gen rút ra từ tin (câu kết luận). Không có ý nào thì ẩn khối (không chồng thêm chữ rỗng). */
function Evidence({ id, enabled }: { id: string; enabled: boolean }) {
  const q = useOwnerInboxDetail(id, enabled);
  if (!enabled) return null;
  if (q.isPending) {
    return (
      <div className="feed-skel" aria-busy="true" aria-label="Đang tải chứng cứ" data-testid="evidence-loading">
        <Skeleton width="80%" height={14} />
        <Skeleton width="60%" height={14} />
      </div>
    );
  }
  if (q.isError) {
    return (
      <section className="feed-block" aria-label="Chứng cứ Gen trích">
        <OwnerError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      </section>
    );
  }
  const lines = evidenceLines(q.data);
  if (lines.length === 0) return null;
  return (
    <section className="feed-block" aria-label="Chứng cứ Gen trích" data-testid="feed-evidence">
      <div className="feed-block__head">
        <h3 className="feed-block__title">Gen trích từ tin</h3>
      </div>
      <ul className="feed-evidence">
        {lines.map((t) => (
          <li key={t}>
            <Icon name="ph ph-check" size={16} />
            <span>{t}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function FeedDetail({ id, item, onResolved }: FeedDetailProps) {
  const itemIsDraft = item?.item_type === 'draft';
  // Chi tiết Hộp thư: cần cho đầu khung khi mở thẳng bằng `?tin=` (chưa có dòng tin) và cho gợi ý/chứng cứ; nháp chờ duyệt không có chứng cứ.
  const detail = useOwnerInboxDetail(id, !itemIsDraft);
  const head = detailHead(item, detail.data);
  const isDraft = head?.itemType === 'draft' || itemIsDraft;
  const isAlert = head?.itemType === 'alert';

  const [draftId, setDraftId] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const draftRef = useRef<HTMLDivElement>(null);
  const silence = useSilenceItem(id);
  const ack = useAckItem(id);

  // Nháp vừa tạo: đưa vào tầm mắt (thanh hành động dính đáy che một phần màn). DraftDecide tải nội dung nháp SAU khi khung xuất hiện nên khung
  // cao dần — cuộn lại theo chiều cao mới trong vài giây đầu để nút "Duyệt và gửi" không nằm dưới thanh hành động.
  useEffect(() => {
    const el = draftRef.current;
    if (!draftId || !el) return;
    el.scrollIntoView?.({ block: 'end', behavior: 'smooth' });
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => el.scrollIntoView?.({ block: 'end', behavior: 'auto' }));
    ro.observe(el);
    const stop = setTimeout(() => ro.disconnect(), 3_000);
    return () => {
      clearTimeout(stop);
      ro.disconnect();
    };
  }, [draftId]);

  if (!head) {
    if (detail.isError) {
      return (
        <div className="feed-detail" data-testid="feed-detail" data-item={id}>
          <OwnerError error={detail.error} onRetry={() => void detail.refetch()} retrying={detail.isFetching} />
        </div>
      );
    }
    return (
      <div className="feed-detail" data-testid="feed-detail" data-item={id} aria-busy="true" aria-label="Đang tải tin">
        <Skeleton width="55%" height={22} />
        <Skeleton width="100%" height={160} radius={8} />
      </div>
    );
  }

  const who = head.personName || head.groupName || head.title;
  const subtitle = headSubtitle(head);
  const failed = silence.error ?? ack.error;
  const busy = silence.isPending || ack.isPending;
  const reminderTitle = head.personName ? `Liên hệ lại ${head.personName}` : `Xem lại: ${head.title}`;
  const reminderSubject = head.personId ? ({ type: 'person', id: head.personId } as const) : undefined;

  const doSilence = () => silence.mutate(undefined, { onSuccess: () => onResolved('Đã tắt tiếng tin này — Gen sẽ không nhắc lại.') });
  const doAck = () => ack.mutate(undefined, { onSuccess: () => onResolved('Đã ghi nhận cảnh báo.') });
  const onDraftDone = (d: DraftDetail) => {
    setDraftId(null);
    setNote(draftDoneText(d.status));
  };

  const silenceButton = (
    <button type="button" className="kit-btn kit-btn--ghost" data-testid="feed-silence" disabled={busy} onClick={doSilence}>
      Tắt tiếng
    </button>
  );
  const remindButton = <RemindButton title={reminderTitle} subject={reminderSubject} testId="feed-remind" />;

  return (
    <div className="feed-detail" data-testid="feed-detail" data-item={id} data-type={head.itemType}>
      <div className="feed-detail__body">
        <header className="feed-detail__head">
          <span className="kit-avatar" aria-hidden>
            {avatarText(who)}
          </span>
          <div className="feed-detail__who">
            <h2 className="feed-detail__name" data-testid="feed-detail-name">
              {who}
            </h2>
            {subtitle ? <span className="feed-detail__sub">{subtitle}</span> : null}
          </div>
          {head.personId ? (
            <Link to={profileLink(head.personId)} className="kit-btn kit-btn--secondary feed-detail__profile" data-testid="feed-open-profile">
              Mở hồ sơ
            </Link>
          ) : null}
        </header>

        {isAlert ? (
          <section className="feed-detail__alert" aria-label="Cảnh báo" data-testid="feed-alert">
            <span className="feed-row__head">
              {head.importance ? (
                <Badge kind="level" level={head.importance}>
                  {head.importance}
                </Badge>
              ) : null}
              <span className="feed-row__label">{head.title}</span>
            </span>
            {head.summary ? <p className="feed-detail__summary">{head.summary}</p> : null}
          </section>
        ) : null}

        {isDraft ? (
          <section className="feed-block" aria-label="Bản nháp chờ duyệt" data-testid="feed-draft-pending">
            <DraftDecide draftId={id} onDone={() => onResolved('Đã xử lý bản nháp.')} />
          </section>
        ) : (
          <>
            <ConversationView id={id} summary={isAlert ? '' : head.summary} />
            <Evidence id={id} enabled={!isDraft} />
            {head.personId ? <AboutCustomer personId={head.personId} personName={head.personName} /> : null}
            {head.suggestedAction ? (
              <section className="feed-suggest" aria-label="Gen gợi ý" data-testid="feed-suggest">
                <span className="feed-suggest__title">
                  <Icon name="ph ph-sparkle" size={16} />
                  Gen gợi ý
                </span>
                <p className="feed-suggest__text">{head.suggestedAction}</p>
              </section>
            ) : null}
            {draftId ? (
              <div ref={draftRef} className="feed-draft-slot">
                <section className="feed-block" aria-label="Bản nháp trả lời" data-testid="feed-draft">
                  <div className="feed-block__head">
                    <h3 className="feed-block__title">Bản nháp trả lời</h3>
                  </div>
                  <DraftDecide draftId={draftId} onDone={onDraftDone} />
                </section>
              </div>
            ) : null}
          </>
        )}
      </div>

      {isDraft ? null : (
        <div className="feed-bar" data-testid="feed-bar">
          {note ? (
            <p className="feed-bar__note" role="status" data-testid="feed-note">
              {note}
            </p>
          ) : null}
          {failed ? (
            <div className="feed-reply__error" data-testid="feed-action-error">
              <InlineError detail={errorDetail(failed)}>{errorText(failed)}</InlineError>
            </div>
          ) : null}
          {isAlert ? (
            <div className="feed-bar__row">
              <button type="button" className="kit-btn kit-btn--primary" data-testid="feed-ack" disabled={busy} onClick={doAck}>
                {ack.isPending ? 'Đang ghi nhận…' : 'Ghi nhận'}
              </button>
              {remindButton}
              {silenceButton}
            </div>
          ) : (
            <ReplyBox
              itemId={id}
              personName={head.personName}
              onDraft={(d) => {
                setNote('');
                if (d) setDraftId(d);
                else setNote('Đã tạo bản nháp trả lời — Sếp duyệt ở Việc.');
              }}
            >
              {remindButton}
              {silenceButton}
            </ReplyBox>
          )}
        </div>
      )}
    </div>
  );
}
