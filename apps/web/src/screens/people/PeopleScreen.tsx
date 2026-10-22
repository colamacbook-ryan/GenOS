import { useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import type {
  PeopleReviewDisputeItem,
  PeopleReviewFull,
  PeopleReviewFullDetail,
  PeopleReviewItem,
  PeopleReviewLogItem,
  PeopleReviewPatchBody,
  ReviewBoard,
} from '@gen-harness/contracts';
import { isFullReview } from '@gen-harness/contracts';
import { Button, Chip, Dialog, EmptyState, Icon, Tabs, type TabItem } from '@gen-harness/ui';
import { WhyButton } from '../core/Evidence';
import { errorText } from '../../lib/errorText';
import { fmtDMClock } from '../../lib/format';
import { useCan } from '../../lib/permissions';
import { useNavigation } from '../../lib/queries';
import { useUrlState } from '../../lib/uiStore';
import { findActive } from '../../shell/navModel';
import { CardError, InlineError, ScreenHead, SkeletonLines } from '../common';
import { BOARD_LABEL, REVIEW_BOARD_LIST, STAFF_HOWTO, TREND_ICON, fmtPeriod, initialsOf, scoreTone, suspiciousLabel, trendTone, withStop } from './peopleModel';
import { useClearSuspicious, useOpenDispute, useResolveDispute, useReview, useReviews, useUpdateReview } from './queries';

export function PeopleScreen() {
  const [board, setBoard] = useUrlState<ReviewBoard>('board', 'employee');
  const q = useReviews({ board, limit: 100 });
  const canDirectory = !!findActive(useNavigation().data, 'directory');
  const [detailId, setDetailId] = useState<string | null>(null);

  const tabs: TabItem<ReviewBoard>[] = REVIEW_BOARD_LIST.map((b) => ({ key: b, label: BOARD_LABEL[b] }));

  return (
    <div className="screen">
      <ScreenHead
        title="Đánh giá con người"
        description="Điểm số là công cụ hỗ trợ quản lý, không phải bản án. Mỗi dòng có xu hướng, tín hiệu nổi bật trong tuần, khuyến nghị và nút xem chứng cứ gốc."
        maxWidth={700}
        actions={
          <span className="ppl-lock-badge">
            <Icon name="ph ph-lock-simple" size={13} />
            Dữ liệu khoá ở cấp Owner
          </span>
        }
      />
      <Tabs items={tabs} value={board} onChange={setBoard} label="Tab bảng đánh giá" idPrefix="ppl-tab" />

      {q.isPending ? (
        <SkeletonLines rows={5} />
      ) : q.isError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : q.data.items.length === 0 ? (
        <EmptyState
          icon="ph ph-users-three"
          title="Chưa có đánh giá nào"
          description={
            board === 'employee'
              ? `Đánh giá tự động chấm hằng ngày cho nhân viên có trả lời tin trong kỳ 7 ngày gần nhất đã trọn vẹn. ${STAFF_HOWTO}`
              : 'Đánh giá tự động chấm hằng ngày cho người có trả lời tin trong kỳ 7 ngày gần nhất đã trọn vẹn.'
          }
          actions={board === 'employee' && canDirectory ? <Link to="/directory">Mở Khách &amp; Nhóm</Link> : null}
        />
      ) : (
        <div className="ppl-rows">
          {q.data.items.map((item) => (
            <ReviewRow key={item.id} item={item} onOpen={() => setDetailId(item.id)} />
          ))}
        </div>
      )}

      <div className="ppl-note">
        <Icon name="ph ph-scales" size={16} />
        <span>
          Mọi đánh giá nhân sự và ứng viên đều phải có chứng cứ, có chỗ phản biện và có người chịu trách nhiệm. Cảnh báo ở đây là tín hiệu, không phải kết
          luận kỷ luật tự động — hệ thống không tự ra quyết định nhân sự.
        </span>
      </div>

      {detailId ? <ReviewDetailDialog id={detailId} onClose={() => setDetailId(null)} onIdChange={setDetailId} /> : null}
    </div>
  );
}

function ReviewRow({ item, onOpen }: { item: PeopleReviewItem; onOpen: () => void }) {
  const canWrite = useCan('people_review.write');
  if (!isFullReview(item)) {
    // Nhánh log (Auditor, Q4): KHÔNG hiện điểm/nội dung — chỉ "đã có đánh giá" + số phản biện.
    return (
      <div className="ppl-row ppl-row--log">
        <div className="ppl-row__who">
          <span className="ppl-row__avatar" aria-hidden>
            {initialsOf(item.person.name)}
          </span>
          <div>
            <div className="ppl-row__name">{item.person.name}</div>
            <div className="ppl-row__role">{item.person.code} · kỳ {fmtPeriod(item.period_start, item.period_end)}</div>
          </div>
        </div>
        <div className="ppl-row__log-status">
          <Icon name={item.has_content ? 'ph ph-check-circle' : 'ph ph-circle-dashed'} size={15} />
          {item.has_content ? 'Đã có đánh giá' : 'Chưa có đánh giá'}
          {item.dispute_count > 0 ? <span className="ppl-row__disputes">· {item.dispute_count} phản biện</span> : null}
        </div>
        <Button variant="ghost" size="sm" icon="ph ph-clock-counter-clockwise" onClick={onOpen}>
          Xem nhật ký ai đã xem
        </Button>
      </div>
    );
  }
  return (
    <div className="ppl-row">
      <div className="ppl-row__who">
        <span className="ppl-row__avatar" aria-hidden>
          {initialsOf(item.person.name)}
        </span>
        <div>
          <div className="ppl-row__name">{item.person.name}</div>
          <div className="ppl-row__role">{item.person.org_name ?? item.person.code} · kỳ {fmtPeriod(item.period_start, item.period_end)}</div>
        </div>
      </div>
      <div className="ppl-row__score">
        <span style={{ color: scoreTone(item.score) }}>{Math.round(item.score)}</span>
        {item.trend ? <Icon name={TREND_ICON[item.trend]} size={14} color={trendTone(item.trend)} /> : null}
        {item.overridden ? <span className="ppl-row__overridden">sửa tay</span> : null}
        <SuspiciousChip item={item} />
      </div>
      <div className="ppl-row__signal">
        <div className="ppl-row__label">Tín hiệu nổi bật tuần này</div>
        <div className="ppl-row__text">{item.signal}</div>
      </div>
      <div className="ppl-row__rec">
        <div className="ppl-row__label">Khuyến nghị</div>
        <div className="ppl-row__text">{item.recommendation}</div>
      </div>
      <div className="ppl-row__actions">
        <WhyButton kind="review" id={item.id} size="sm" icon="ph ph-quotes">
          Xem chứng cứ
        </WhyButton>
        <Button variant="ghost" size="sm" onClick={onOpen}>
          {canWrite ? 'Sửa điểm tay' : 'Xem chi tiết'}
        </Button>
      </div>
    </div>
  );
}

/** F-60: chip cảnh báo 'Đáng ngờ' — rê chuột xem lý do (title). */
function SuspiciousChip({ item }: { item: Pick<PeopleReviewFull, 'suspicious' | 'suspicious_reason'> }) {
  const label = suspiciousLabel(item);
  if (!label) return null;
  return (
    <span className="ppl-row__suspicious" title={label.title} data-testid="ppl-suspicious">
      <Chip tone="warn" dot>
        <Icon name="ph ph-warning" size={12} /> {label.text}
      </Chip>
    </span>
  );
}

function ReviewDetailDialog({ id, onClose, onIdChange }: { id: string; onClose: () => void; onIdChange: (id: string) => void }) {
  const q = useReview(id);
  return (
    <Dialog open onClose={onClose} width={620} title={q.data ? q.data.person.name : 'Đánh giá'} kicker={q.data ? `Kỳ ${fmtPeriod(q.data.period_start, q.data.period_end)}` : undefined} actions={<Button variant="secondary" onClick={onClose}>Đóng</Button>}>
      {q.isPending ? (
        <SkeletonLines rows={4} padding="0" />
      ) : q.isError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : q.data && isFullReview(q.data) ? (
        <FullDetailBody detail={q.data} onIdChange={onIdChange} />
      ) : q.data ? (
        <LogDetailBody item={q.data} />
      ) : null}
    </Dialog>
  );
}

function LogDetailBody({ item }: { item: PeopleReviewLogItem }) {
  return (
    <div className="dlg-fields">
      <div className="dlg-kv">
        <span className="dlg-kv__k">Trạng thái</span>
        <span className="dlg-kv__v">{item.has_content ? 'Đã có đánh giá (nội dung khoá ở cấp Owner)' : 'Chưa có đánh giá'}</span>
        <span className="dlg-kv__k">Số phản biện</span>
        <span className="dlg-kv__v">{item.dispute_count}</span>
        <span className="dlg-kv__k">Tạo lúc</span>
        <span className="dlg-kv__v">{fmtDMClock(item.created_at)}</span>
      </div>
      <section className="ev-section" aria-label="Nhật ký ai đã xem">
        <div className="dlg-section-title">Nhật ký ai đã xem</div>
        {item.viewed_by.length === 0 ? (
          <EmptyState icon="ph ph-eye" title="Chưa có ai xem đánh giá này" />
        ) : (
          <div className="dlg-list" role="list">
            {item.viewed_by.map((v, i) => (
              <div key={i} className="dlg-row" role="listitem">
                <Icon name="ph ph-eye" size={13} />
                <span style={{ flex: 1 }}>{v.user?.name ?? 'Không rõ'}</span>
                <span className="dlg-kv__k">{v.action}</span>
                <span className="dlg-kv__k">{fmtDMClock(v.at)}</span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

/** Câu hiện thay nút/form sửa khi chỉ có quyền xem (people_review.read) mà không có people_review.write. */
const READ_ONLY_TEXT = 'Chỉ người có quyền sửa đánh giá mới sửa điểm hoặc bỏ cờ được — nhờ Owner.';

function FullDetailBody({ detail, onIdChange }: { detail: PeopleReviewFullDetail; onIdChange: (id: string) => void }) {
  // Máy chủ đòi people_review.write cho sửa điểm / bỏ cờ — người chỉ có quyền xem không thấy nút chết (403).
  const canWrite = useCan('people_review.write');

  return (
    <div className="dlg-fields">
      <div className="ppl-detail-score">
        <span style={{ color: scoreTone(detail.score) }}>{Math.round(detail.score)}</span>
        <div>
          <div className="ppl-row__text">{detail.signal}</div>
          {detail.overridden ? (
            <div className="ppl-row__label">sửa tay · {detail.overridden_by?.name} · {fmtDMClock(detail.overridden_at)} · lý do: {detail.override_reason}</div>
          ) : null}
        </div>
      </div>

      <SuspiciousNote detail={detail} canWrite={canWrite} />

      {canWrite ? (
        <EditScoreSection detail={detail} onIdChange={onIdChange} />
      ) : (
        <div className="ppl-row__label" data-testid="ppl-read-only">
          {READ_ONLY_TEXT}
        </div>
      )}

      {detail.history.length > 0 ? (
        <section aria-label="Lịch sử điểm">
          <div className="dlg-section-title">Lịch sử điểm · {detail.history.length} bản</div>
          <div className="dlg-list" role="list">
            {detail.history.map((h) => (
              <div key={h.id} className="dlg-row" role="listitem">
                <span style={{ color: scoreTone(h.score), fontFamily: 'var(--font-mono)', fontWeight: 600 }}>{Math.round(h.score)}</span>
                <span style={{ flex: 1 }}>{h.overridden_by ? `sửa tay · ${h.overridden_by.name}${h.override_reason ? ` — ${h.override_reason}` : ''}` : 'tự động (rules+model)'}</span>
                <span className="dlg-kv__k">{fmtDMClock(h.created_at)}</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <DisputesSection detail={detail} />
    </div>
  );
}

/** Sửa điểm tay — giữ lịch sử (cần people_review.write + mã PIN). */
function EditScoreSection({ detail, onIdChange }: { detail: PeopleReviewFullDetail; onIdChange: (id: string) => void }) {
  const [score, setScore] = useState(String(Math.round(detail.score)));
  const [reason, setReason] = useState('');
  const update = useUpdateReview();
  useEffect(() => {
    setScore(String(Math.round(detail.score)));
    setReason('');
    // eslint-disable-next-line react-hooks/exhaustive-deps -- chỉ đặt lại khi đổi SANG bản khác (id đổi); detail.score đổi cùng lúc với id (PATCH luôn sinh id mới), không cần lặp lại đây.
  }, [detail.id]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const n = Number(score);
    if (Number.isNaN(n) || !reason.trim()) return;
    const body: PeopleReviewPatchBody = { score: n, reason: reason.trim(), evidence: detail.evidence, trend: detail.trend, signal: detail.signal, recommendation: detail.recommendation };
    update.mutate({ id: detail.id, body }, { onSuccess: (row) => onIdChange(row.id) });
  };

  return (
    <section aria-label="Sửa điểm tay">
      <div className="dlg-section-title">Sửa điểm tay — giữ lịch sử</div>
      <form className="ppl-edit-form" onSubmit={submit}>
        <label className="gh-field">
          <span className="gh-field__label">Điểm mới (0–100)</span>
          <input className="gh-input" type="number" min={0} max={100} value={score} onChange={(e) => setScore(e.target.value)} style={{ width: 96 }} />
        </label>
        <label className="gh-field" style={{ flex: 1 }}>
          <span className="gh-field__label">Lý do sửa</span>
          <textarea className="gh-textarea" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Bắt buộc — ghi rõ vì sao sửa điểm" rows={2} />
        </label>
        <Button type="submit" variant="primary" size="sm" disabled={!reason.trim() || score === ''} loading={update.isPending}>
          Lưu điểm mới
        </Button>
      </form>
      {update.isError ? <InlineError>{errorText(update.error)}</InlineError> : null}
    </section>
  );
}

/** F-60: ghi chú 'Đáng ngờ' + nút bỏ cờ (bắt buộc lý do, cần mã PIN, ghi Nhật ký); đã bỏ cờ thì hiện ai bỏ, vì sao. */
function SuspiciousNote({ detail, canWrite }: { detail: PeopleReviewFullDetail; canWrite: boolean }) {
  const [open, setOpen] = useState(false);
  const [why, setWhy] = useState('');
  const clear = useClearSuspicious();
  const label = suspiciousLabel(detail);
  const cleared = detail.suspicious_cleared ?? null;
  if (label) {
    return (
      <div className="ppl-note" role="note" aria-label="Cảnh báo đáng ngờ" style={{ borderColor: 'var(--color-warn)' }}>
        <Icon name="ph ph-warning" size={16} color="var(--color-warn)" />
        <div style={{ flex: 1 }}>
          <span>
            <strong>Đáng ngờ:</strong> {label.title} Hệ thống chỉ gắn cờ, không tự đổi điểm — mở &quot;Xem chứng cứ&quot; để đọc tin gốc.
          </span>
          {!canWrite ? null : open ? (
            <form
              className="ppl-dispute-form"
              onSubmit={(e) => {
                e.preventDefault();
                if (why.trim().length < 3) return;
                clear.mutate({ id: detail.id, body: { cleared_reason: why.trim() } }, { onSuccess: () => { setOpen(false); setWhy(''); } });
              }}
            >
              <textarea className="gh-textarea" aria-label="Lý do bỏ cờ" value={why} onChange={(e) => setWhy(e.target.value)} placeholder="Bắt buộc — đã xem chứng cứ gì, vì sao là báo nhầm" rows={2} />
              <div className="ppl-dispute-form__actions">
                <Button type="submit" variant="secondary" size="sm" disabled={why.trim().length < 3} loading={clear.isPending}>
                  Bỏ cờ
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setOpen(false);
                    setWhy('');
                    clear.reset();
                  }}
                >
                  Thôi
                </Button>
              </div>
              <div className="ppl-row__label">Bỏ cờ cần mã PIN và được ghi vào Nhật ký thao tác. Điểm không đổi.</div>
            </form>
          ) : (
            <div style={{ marginTop: 6 }}>
              <Button type="button" variant="ghost" size="sm" icon="ph ph-flag" onClick={() => setOpen(true)}>
                Bỏ cờ (đã xem chứng cứ)
              </Button>
            </div>
          )}
          {clear.isError ? <InlineError>{errorText(clear.error)}</InlineError> : null}
        </div>
      </div>
    );
  }
  if (cleared) {
    return (
      <div className="ppl-row__label" role="note" aria-label="Đã bỏ cờ đáng ngờ" data-testid="ppl-suspicious-cleared">
        Đã bỏ cờ &apos;Đáng ngờ&apos; · {cleared.by?.name ?? 'Không rõ'} · {fmtDMClock(cleared.at)} — {withStop(cleared.reason)}
        {detail.suspicious_reason ? ` Cờ cũ: ${withStop(detail.suspicious_reason)}` : ''}
      </div>
    );
  }
  return null;
}

function DisputesSection({ detail }: { detail: PeopleReviewFullDetail }) {
  const [body, setBody] = useState('');
  const openDispute = useOpenDispute();
  return (
    <section aria-label="Phản biện">
      <div className="dlg-section-title">Phản biện · {detail.disputes.length}</div>
      {detail.disputes.length === 0 ? (
        <EmptyState icon="ph ph-chat-centered-dots" title="Chưa có phản biện nào" />
      ) : (
        <div className="dlg-list" role="list">
          {detail.disputes.map((d) => (
            <DisputeRow key={d.id} d={d} />
          ))}
        </div>
      )}
      <form
        className="ppl-dispute-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (!body.trim()) return;
          openDispute.mutate({ reviewId: detail.id, body: { body: body.trim() } }, { onSuccess: () => setBody('') });
        }}
      >
        <textarea className="gh-textarea" value={body} onChange={(e) => setBody(e.target.value)} placeholder="Mở phản biện mới — nêu rõ điểm không đồng ý" rows={2} />
        <Button type="submit" variant="secondary" size="sm" icon="ph ph-chat-centered-text" disabled={!body.trim()} loading={openDispute.isPending}>
          Mở phản biện
        </Button>
      </form>
      {openDispute.isError ? <InlineError>{errorText(openDispute.error)}</InlineError> : null}
    </section>
  );
}

function DisputeRow({ d }: { d: PeopleReviewDisputeItem }) {
  const [resolution, setResolution] = useState('');
  const resolve = useResolveDispute();
  return (
    <div className="ppl-dispute">
      <div className="dlg-item__meta">
        <span>{d.raised_by.name}</span>
        <span>·</span>
        <span>{fmtDMClock(d.created_at)}</span>
        <span style={{ flex: 1 }} />
        <span className={`ppl-dispute__status ppl-dispute__status--${d.status}`}>{d.status === 'open' ? 'đang mở' : d.status === 'resolved' ? 'đã chấp nhận' : 'đã từ chối'}</span>
      </div>
      <div className="dlg-item__text">{d.body}</div>
      {d.status !== 'open' ? (
        <div className="ppl-row__label" style={{ marginTop: 6 }}>
          {d.resolved_by?.name} · {fmtDMClock(d.resolved_at)} — {d.resolution}
        </div>
      ) : (
        <form
          className="ppl-dispute-form"
          onSubmit={(e) => {
            e.preventDefault();
          }}
        >
          <textarea className="gh-textarea" value={resolution} onChange={(e) => setResolution(e.target.value)} placeholder="Ghi lý do xử lý" rows={2} />
          <div className="ppl-dispute-form__actions">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={!resolution.trim()}
              loading={resolve.isPending}
              onClick={() => resolve.mutate({ id: d.id, body: { status: 'resolved', resolution: resolution.trim() } })}
            >
              Chấp nhận
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={!resolution.trim()}
              loading={resolve.isPending}
              onClick={() => resolve.mutate({ id: d.id, body: { status: 'rejected', resolution: resolution.trim() } })}
            >
              Từ chối
            </Button>
          </div>
          {resolve.isError ? <InlineError>{errorText(resolve.error)}</InlineError> : null}
        </form>
      )}
    </div>
  );
}
