/**
 * v0.1.59 (G3) — "Cần Sếp quyết" của màn Việc (`/owner/viec`): nháp chờ duyệt (`GET /drafts?status=pending`) + đề xuất của Gen
 * (`GET /owner/proposals`). Nháp vượt ngưỡng tự gửi / ghi ra ngoài đứng đầu. Mỗi nháp một dòng: gửi cho ai, loại, 2 dòng đầu nội
 * dung, lý do giữ lại; "Xem và duyệt" mở rộng → `DraftDecide` (Duyệt và gửi · Sửa rồi gửi · Từ chối — cả ba cần mã PIN do client tự
 * hỏi). Đề xuất của Gen: tóm tắt, nhãn loại, "hết hạn sau X giờ", `ProposalDecide` (Xác nhận · Huỷ).
 *
 * Gen chỉ ĐỀ XUẤT: không có đường gửi thẳng. Chuỗi từ máy chủ đi qua `asText` (không render object).
 */
/* eslint-disable react-refresh/only-export-components */
import { useState } from 'react';
import { useQueries, type UseQueryResult } from '@tanstack/react-query';
import type { DraftDetail, DraftItem, DraftPage, OwnerProposal, OwnerProposals } from '@gen-harness/contracts';
import { Badge, Card, EmptyState, Icon, Skeleton, cx } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { DEFAULT_TZ, fmtDM, fmtHM, sameDay } from '../../lib/format';
import { qk3 } from '../../screens/core/queries';
import { DraftDecide } from '../actions/DraftDecide';
import { ProposalDecide } from '../actions/ProposalDecide';
import { draftTargetText } from '../actions/useOwnerActions';
import { ASK_GEN_PROMPTS, asText, countText } from '../ownerModel';
import { openAskGen } from '../AskGenScreen';
import { OwnerError, PartialLoadNote } from '../parts';
import './work.css';

/** Số nháp đầu danh sách được tải chi tiết (cờ + 2 dòng đầu). */
const DETAIL_MAX = 20;

/** Mã PIN: một câu dùng lại ở đầu khối. */
export const PIN_HINT = 'Mỗi lần duyệt, sửa rồi gửi hoặc từ chối đều cần mã PIN của Sếp.';

export interface DraftFlags {
  over_threshold: boolean;
  writes_external: boolean;
}

/** Cờ của nháp (chỉ có ở chi tiết). Thiếu / lạ ⇒ không cờ nào. */
export function draftFlags(d: Pick<DraftDetail, 'flags'> | null | undefined): DraftFlags {
  const f = d?.flags;
  return { over_threshold: f?.over_threshold === true, writes_external: f?.writes_external === true };
}

/** Hạng để xếp: vượt ngưỡng tự gửi (0) → ghi ra ngoài (1) → còn lại (2). Ổn định theo thứ tự máy chủ (mới trước). */
export function draftRank(f: DraftFlags): number {
  return f.over_threshold ? 0 : f.writes_external ? 1 : 2;
}

/** Xếp nháp: cờ lên đầu; chưa tải được chi tiết ⇒ hạng 2 (giữ thứ tự cũ). */
export function sortDrafts(items: readonly DraftItem[], details: ReadonlyMap<string, DraftDetail>): DraftItem[] {
  return items
    .map((d, i) => ({ d, i, r: draftRank(draftFlags(details.get(asText(d.id)))) }))
    .sort((a, b) => a.r - b.r || a.i - b.i)
    .map((x) => x.d);
}

/** Hai dòng đầu của nội dung (cắt gọn ở CSS bằng line-clamp; ở đây chỉ bỏ dòng trống). */
export function firstLines(text: unknown, n = 2): string {
  return asText(text)
    .split(/\n+/)
    .map((l) => l.trim())
    .filter(Boolean)
    .slice(0, n)
    .join('\n');
}

const personOrGroupName = (s: unknown): string => (s && typeof s === 'object' ? asText((s as { name?: unknown }).name) : '');

function timeLabel(iso: unknown, tz: string): string {
  const s = asText(iso);
  if (!Number.isFinite(Date.parse(s))) return '';
  return sameDay(s, new Date(), tz) ? fmtHM(s, tz) : `${fmtDM(s, tz)} ${fmtHM(s, tz)}`;
}

function DraftRow({ item, detail, loading, open, onToggle, tz }: { item: DraftItem; detail?: DraftDetail; loading: boolean; open: boolean; onToggle: () => void; tz: string }) {
  const id = asText(item.id);
  const flags = draftFlags(detail);
  const target = detail ? draftTargetText(detail) : '';
  const who = target || personOrGroupName(item.subject);
  const hold = asText(item.hold_reason) || asText(detail?.hold_reason);
  const preview = firstLines(detail?.text);
  const meta = [asText(item.kind_label), who ? `Gửi ${who}` : '', timeLabel(item.created_at, tz)].filter(Boolean).join(' · ');
  return (
    <li className="work-draft" data-testid="work-draft-row" data-draft-id={id}>
      <div className="work-draft__head">
        <div className="work-draft__text">
          <span className="work-draft__title">
            {flags.over_threshold ? <Badge kind="status" tone="warn">Trên mức tự gửi</Badge> : flags.writes_external ? <Badge kind="status" tone="neutral">Gửi ra ngoài</Badge> : null}
            <span>{asText(item.title) || 'Bản nháp chờ duyệt'}</span>
          </span>
          {meta ? <span className="work-draft__meta">{meta}</span> : null}
        </div>
        <button type="button" className="kit-btn kit-btn--secondary work-btn" aria-expanded={open} data-testid="work-draft-toggle" onClick={onToggle}>
          {open ? 'Thu gọn' : 'Xem và duyệt'}
        </button>
      </div>
      {open ? null : (
        <>
          {preview ? (
            <blockquote className="work-draft__quote" data-testid="work-draft-preview">
              {preview}
            </blockquote>
          ) : loading ? (
            <Skeleton width="70%" height={14} />
          ) : null}
          {hold ? (
            <p className="work-draft__hold" data-testid="work-draft-hold">
              <Icon name="ph ph-warning" size={14} />
              <span>Gen giữ lại để Sếp duyệt: {hold}</span>
            </p>
          ) : null}
        </>
      )}
      {open ? <DraftDecide draftId={id} onDone={onToggle} /> : null}
    </li>
  );
}

export interface DecideCounts {
  drafts: number;
  proposals: number;
  total: number;
}

/** Số mục chờ quyết: tổng nháp chờ duyệt (máy chủ) + đề xuất còn hiệu lực. Chưa tải ⇒ 0. */
export function decideCounts(drafts: Pick<UseQueryResult<DraftPage>, 'data'>, proposals: Pick<UseQueryResult<OwnerProposals>, 'data'>): DecideCounts {
  const items = Array.isArray(drafts.data?.items) ? drafts.data.items : [];
  const nd = typeof drafts.data?.total === 'number' ? drafts.data.total : items.length;
  const np = Array.isArray(proposals.data?.items) ? proposals.data.items.length : 0;
  return { drafts: nd, proposals: np, total: nd + np };
}

export function DecideList({
  drafts,
  proposals,
  tz = DEFAULT_TZ,
  className,
}: {
  drafts: UseQueryResult<DraftPage>;
  proposals: UseQueryResult<OwnerProposals>;
  tz?: string;
  className?: string;
}) {
  const [openId, setOpenId] = useState<string | null>(null);
  const items = Array.isArray(drafts.data?.items) ? drafts.data.items.filter((d) => asText(d?.id)) : [];
  // Chi tiết từng nháp (cờ vượt ngưỡng / ghi ra ngoài + nội dung); cùng khoá với DraftDecide nên mở rộng không tải lại từ đầu.
  // Chỉ tải chi tiết cho DETAIL_MAX nháp đầu (danh sách rất dài không bắn hàng chục yêu cầu); nháp sau vẫn hiện, mở rộng thì mới tải.
  const detailItems = items.slice(0, DETAIL_MAX);
  const detailQs = useQueries({
    queries: detailItems.map((d) => ({
      queryKey: qk3.draft(asText(d.id)),
      queryFn: ({ signal }: { signal: AbortSignal }) => api.drafts.get(asText(d.id), signal),
      staleTime: 30_000,
    })),
  });
  const details = new Map<string, DraftDetail>();
  const loadingIds = new Set<string>();
  detailItems.forEach((d, i) => {
    const data = detailQs[i]?.data;
    if (data) details.set(asText(d.id), data);
    else if (detailQs[i]?.isPending) loadingIds.add(asText(d.id));
  });
  const title = 'Cần Sếp quyết';

  const retryAll = () => void Promise.all([drafts.refetch(), proposals.refetch()]);
  const retrying = drafts.isFetching || proposals.isFetching;
  // Nháp là nguồn chính: chưa tải xong thì chưa được nói "không có gì". Đề xuất của Gen "không đọc được" (lỗi hoặc máy chủ báo
  // unavailable) cũng KHÔNG phải "không có đề xuất".
  const proposalsDown = proposals.isError || proposals.data?.state === 'unavailable';
  if (drafts.isPending) return <Card title={title} state="loading" className={className} data-testid="work-decide-loading" />;
  const props: OwnerProposal[] = Array.isArray(proposals.data?.items) ? proposals.data.items.filter((p) => asText(p?.id)) : [];
  const counts = decideCounts(drafts, proposals);
  const nothingToShow = counts.total === 0 && items.length === 0 && props.length === 0;
  if (nothingToShow && (drafts.isError || proposalsDown)) {
    return (
      <Card title={title} state="error" className={className} data-testid="work-decide-error">
        <OwnerError error={drafts.isError ? drafts.error : proposals.error ?? new Error('Máy chủ chưa đọc được đề xuất của Gen')} onRetry={retryAll} retrying={retrying} />
      </Card>
    );
  }
  if (nothingToShow && proposals.isPending) return <Card title={title} state="loading" className={className} data-testid="work-decide-loading" />;
  if (nothingToShow) {
    return (
      <Card title={title} className={className} data-testid="work-decide">
        <EmptyState
          icon="ph ph-check-circle"
          title="Không có gì chờ Sếp duyệt."
          action={
            <button type="button" className="kit-btn kit-btn--secondary" data-testid="work-decide-ask" onClick={() => openAskGen(ASK_GEN_PROMPTS[3])}>
              Nhờ Gen soạn nháp
            </button>
          }
        />
      </Card>
    );
  }
  const sorted = sortDrafts(items, details);
  const parts = [counts.drafts > 0 ? `${countText(counts.drafts)} nháp gửi khách` : '', counts.proposals > 0 ? `${countText(counts.proposals)} đề xuất của Gen` : ''].filter(Boolean);
  return (
    <Card title={title} padded={false} className={cx('work-decide', className)} data-testid="work-decide">
      <div className="work-decide__head">
        <p className="work-decide__count">
          <span className="work-big" data-testid="work-decide-count">
            {countText(counts.total)}
          </span>
          <span> việc cần Sếp quyết{parts.length > 0 ? ` · ${parts.join(', ')}` : ''}</span>
        </p>
        <p className="work-pin">
          <Icon name="ph ph-lock-key" size={14} />
          <span>{PIN_HINT}</span>
        </p>
        {drafts.isError || proposalsDown ? (
          <PartialLoadNote
            error={drafts.isError ? drafts.error : proposals.error}
            detail={drafts.isError || proposals.isError ? undefined : 'Máy chủ báo chưa đọc được đề xuất của Gen (state=unavailable)'}
            onRetry={retryAll}
            retrying={retrying}
            testId="work-decide-partial"
          >
            {drafts.isError ? 'Chưa tải được danh sách nháp chờ duyệt — danh sách dưới đây có thể thiếu.' : 'Chưa đọc được đề xuất của Gen — danh sách dưới đây có thể thiếu.'}
          </PartialLoadNote>
        ) : null}
      </div>
      <ul className="work-list" aria-label="Việc chờ Sếp quyết">
        {sorted.map((d) => (
          <DraftRow
            key={asText(d.id)}
            item={d}
            detail={details.get(asText(d.id))}
            loading={loadingIds.has(asText(d.id))}
            open={openId === asText(d.id)}
            onToggle={() => setOpenId((cur) => (cur === asText(d.id) ? null : asText(d.id)))}
            tz={tz}
          />
        ))}
        {props.map((p) => (
          <li key={asText(p.id)} className="work-proposal" data-testid="work-proposal-row" data-proposal-id={asText(p.id)}>
            <ProposalDecide proposal={p} />
          </li>
        ))}
      </ul>
    </Card>
  );
}
