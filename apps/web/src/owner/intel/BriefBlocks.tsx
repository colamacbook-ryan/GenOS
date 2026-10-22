/**
 * v0.1.59 (G3) — các khối "bản tin" của Tình báo (`/owner`): Hàng 1 "Bản tin hôm nay" và ba khối Hàng 2 (Việc phải quyết ·
 * Cảnh báo · Điểm chính). Dữ liệu: `GET /owner/brief` (latest = bản đã lưu, live = số liệu đọc lúc này, alerts, history) +
 * nháp chờ duyệt + đề xuất của Gen (số 28px khớp `today.nav.decide`).
 *
 * Phòng thủ: mục bản tin lạ / `lines` không phải chuỗi đi qua `safeSection` (asText) — không bao giờ ném, không render object.
 * Gen chỉ ĐỀ XUẤT: "Duyệt" chỉ MỞ `DraftDecide` / `ProposalDecide` trong ngăn chi tiết; gửi tin vẫn qua Xác nhận + mã PIN.
 *
 * Hàm thuần (safeSection, buildAlertRows…) được export riêng để test; chỉ là hàm hỗ trợ, không ảnh hưởng làm nóng (fast refresh).
 */
/* eslint-disable react-refresh/only-export-components */
import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { UseQueryResult } from '@tanstack/react-query';
import type { BriefHistoryItem, DraftItem, DraftPage, OwnerBrief, OwnerProposal, OwnerProposals, OwnerToday } from '@gen-harness/contracts';
import { Badge, Card, DetailSheet, EmptyState, Icon, ListRow, Skeleton, cx } from '@gen-harness/ui';
import { DEFAULT_TZ, fmtAgo, fmtDM, fmtHM } from '../../lib/format';
import { DraftDecide } from '../actions/DraftDecide';
import { ProposalDecide } from '../actions/ProposalDecide';
import { OWNER_PATHS, asText, countText, safeLink } from '../ownerModel';
import { OwnerError, PartialLoadNote } from '../parts';
import './intel.css';

/** Đích duy nhất của dải "chưa có nguồn AI" (G5 mở bước Nguồn AI ở đây). */
export const AI_SETUP_PATH = '/setup?buoc=nguon-ai';
/** Câu thay cho tóm tắt khi chưa có bản tin / bản tin chưa có tóm tắt (vẫn có số liệu lúc này bên dưới). */
export const FIRST_BRIEF_TEXT = 'Bản tin đầu tiên lúc 07:30. Dưới đây là số liệu lúc này.';
export const AI_STRIP_TEXT = 'Gen chưa có nguồn AI nên chưa viết tóm tắt — ';
const DECIDE_ROWS_MAX = 3;
const LINES_MAX = 5;

// ── mục bản tin phòng thủ ─────────────────────────────────────────────────────────────────────────────────────────────

export interface SafeSection {
  key: string;
  title: string;
  /** null = máy chủ không trả số. */
  count: number | null;
  /** Chỉ chuỗi, ≤ 5 dòng (phần tử không phải chuỗi bị bỏ). */
  lines: string[];
  to: string;
  group: 'decide' | 'alert' | 'key';
  state: 'error' | 'empty' | null;
  more: boolean;
}

/** Một mục bản tin bất kỳ → mục an toàn; không phải object / thiếu khoá ⇒ null. Không bao giờ ném. */
export function safeSection(raw: unknown): SafeSection | null {
  if (!raw || typeof raw !== 'object') return null;
  const o = raw as Record<string, unknown>;
  const key = asText(o.key).trim();
  const title = asText(o.title).trim();
  if (!key && !title) return null;
  const count = typeof o.count === 'number' && Number.isFinite(o.count) && o.count >= 0 ? Math.floor(o.count) : null;
  const lines = (Array.isArray(o.lines) ? o.lines : [])
    .map((l) => asText(l).trim())
    .filter(Boolean)
    .slice(0, LINES_MAX);
  const group = o.group === 'decide' || o.group === 'alert' ? o.group : 'key';
  const state = o.state === 'error' ? 'error' : o.state === 'empty' ? 'empty' : null;
  return { key: key || title, title: title || key, count, lines, to: safeLink(o.to, OWNER_PATHS.intel), group, state, more: o.more === true };
}

export function safeSections(raw: unknown): SafeSection[] {
  return (Array.isArray(raw) ? raw : []).map(safeSection).filter((s): s is SafeSection => s !== null);
}

/** Số liệu lúc này (`live`) + mục Gen-hub chỉ có ở bản tin đã lưu (`latest`): lịch, mail, việc Google. Trùng khoá ⇒ lấy `live`. */
export function briefSections(brief: Pick<OwnerBrief, 'latest' | 'live'> | null | undefined): SafeSection[] {
  const live = safeSections(brief?.live?.sections);
  const have = new Set(live.map((s) => s.key));
  const hub = safeSections(brief?.latest?.sections).filter((s) => !have.has(s.key));
  return [...live, ...hub];
}

/** Mục có số hoặc có dòng để xem (rỗng hẳn thì không đáng một dòng). */
const hasContent = (s: SafeSection): boolean => (s.count ?? 0) > 0 || s.lines.length > 0;

/** "Điểm chính": mọi mục trừ nháp chờ duyệt (đã có ở "Việc phải quyết"), sự cố (ở "Cảnh báo") và mục lỗi (cũng ở "Cảnh báo"). */
export function keySections(sections: readonly SafeSection[]): SafeSection[] {
  return sections.filter((s) => s.group !== 'alert' && s.key !== 'drafts_pending' && s.state !== 'error' && hasContent(s));
}

// ── giờ giấc ─────────────────────────────────────────────────────────────────────────────────────────────────────────

const WEEKDAY = ['Chủ nhật', 'Thứ hai', 'Thứ ba', 'Thứ tư', 'Thứ năm', 'Thứ sáu', 'Thứ bảy'];
const EN_DAY = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

/** "Thứ bảy 11/10" theo giờ tổ chức. */
export function dayHeading(now: Date | string | number = Date.now(), tz: string = DEFAULT_TZ): string {
  const d = new Date(now);
  if (Number.isNaN(d.getTime())) return '';
  let wd = '';
  try {
    wd = new Intl.DateTimeFormat('en-US', { timeZone: tz, weekday: 'short' }).format(d);
  } catch {
    wd = new Intl.DateTimeFormat('en-US', { timeZone: DEFAULT_TZ, weekday: 'short' }).format(d);
  }
  const name = WEEKDAY[EN_DAY.indexOf(wd)] ?? '';
  return `${name} ${fmtDM(d.toISOString(), tz)}`.trim();
}

/** "sáng" / "chiều" của một mốc giờ. */
export function slotWord(iso: unknown, tz: string = DEFAULT_TZ): string {
  const t = Date.parse(asText(iso));
  if (Number.isNaN(t)) return '';
  const h = Number.parseInt(fmtHM(new Date(t).toISOString(), tz).slice(0, 2), 10);
  return Number.isFinite(h) && h < 12 ? 'sáng' : 'chiều';
}

/** Dòng phụ 12px: "Bản tin sáng 07:30 · số liệu cập nhật 09:12 · bản tin chiều lúc 17:30" (thiếu phần nào bỏ phần đó). */
export function heroSubline(brief: OwnerBrief, tz: string = DEFAULT_TZ): string {
  const parts: string[] = [];
  const latest = brief.latest;
  if (latest && Number.isFinite(Date.parse(asText(latest.at)))) parts.push(`Bản tin ${asText(latest.slot)} ${fmtHM(latest.at, tz)}`.replace(/\s+/g, ' '));
  const liveAt = asText(brief.live?.at);
  if (Number.isFinite(Date.parse(liveAt))) parts.push(`số liệu cập nhật ${fmtHM(liveAt, tz)}`);
  const next = asText(brief.next_at);
  if (Number.isFinite(Date.parse(next))) parts.push(`bản tin ${slotWord(next, tz)} lúc ${fmtHM(next, tz)}`);
  return parts.join(' · ');
}

/** Dải "Gen chưa có nguồn AI": CHỈ khi bản tin báo thiếu khoá HOẶC `ai_ready === false`. KHÔNG dựa vào `progress`. */
export function showAiStrip(brief: Pick<OwnerBrief, 'latest' | 'ai_ready'> | null | undefined): boolean {
  return brief?.latest?.needs_api_key === true || brief?.ai_ready === false;
}

// ── Hàng 1: Bản tin hôm nay ───────────────────────────────────────────────────────────────────────────────────────────

export function BriefHero({
  brief,
  selectedId,
  onSelect,
  tz = DEFAULT_TZ,
}: {
  brief: UseQueryResult<OwnerBrief>;
  /** Mã bản tin cũ đang xem (undefined = bản mới nhất). */
  selectedId: string | undefined;
  onSelect: (id: string | undefined) => void;
  tz?: string;
}) {
  const [open, setOpen] = useState(false);
  if (brief.isPending) {
    return (
      <section className="intel-hero" aria-busy="true" aria-label="Đang tải bản tin" data-testid="intel-hero-loading">
        <Skeleton width={160} height={14} />
        <Skeleton width="80%" height={24} />
        <Skeleton width="60%" height={12} />
      </section>
    );
  }
  if (brief.isError) {
    return (
      <Card state="error" title="Bản tin hôm nay" data-testid="intel-hero-error">
        <OwnerError error={brief.error} onRetry={() => void brief.refetch()} retrying={brief.isFetching} />
      </Card>
    );
  }
  const d = brief.data;
  const summary = asText(d.latest?.summary).trim();
  const history: BriefHistoryItem[] = (Array.isArray(d.history) ? d.history : []).filter((h) => asText(h?.id));
  const viewingOld = selectedId !== undefined && history.length > 0 && asText(d.latest?.id) !== asText(history[0].id);
  const aiStrip = showAiStrip(d);
  const sub = heroSubline(d, tz);
  return (
    <section className="intel-hero" aria-label="Bản tin hôm nay" data-testid="intel-hero">
      <div className="intel-hero__top">
        <div className="intel-hero__text">
          <p className="intel-hello" data-testid="intel-hello">
            Chào Sếp · {dayHeading(asText(d.live?.at) || Date.now(), tz)}
          </p>
          <h2 className="intel-summary" data-testid="intel-summary">
            {summary || FIRST_BRIEF_TEXT}
          </h2>
          {sub ? (
            <p className="intel-sub" data-testid="intel-hero-sub">
              {sub}
            </p>
          ) : null}
        </div>
        {history.length > 0 ? (
          <button type="button" className="kit-btn kit-btn--secondary intel-hero__prev" data-testid="intel-brief-history" onClick={() => setOpen(true)}>
            Xem bản tin trước
          </button>
        ) : null}
      </div>
      {viewingOld ? (
        <p className="intel-old" data-testid="intel-brief-old">
          Sếp đang xem bản tin cũ.{' '}
          <button type="button" className="kit-link" onClick={() => onSelect(undefined)}>
            Xem bản tin mới nhất
          </button>
        </p>
      ) : null}
      {aiStrip ? (
        <p className="intel-ai" role="status" data-testid="intel-ai-strip">
          <Icon name="ph ph-sparkle" size={16} />
          <span>
            {AI_STRIP_TEXT}
            <Link to={AI_SETUP_PATH} className="kit-link" data-testid="intel-ai-setup">
              Thiết lập 2 phút
            </Link>
          </span>
        </p>
      ) : null}
      <DetailSheet open={open} title="Bản tin trước" onClose={() => setOpen(false)} data-testid="intel-history-sheet">
        <ul className="kit-list intel-history" aria-label="Các bản tin gần đây">
          {history.map((h, i) => (
            <ListRow
              key={asText(h.id)}
              as="li"
              title={asText(h.title) || 'Bản tin Gen'}
              meta={`${fmtDM(h.at, tz)} · ${asText(h.slot)} ${fmtHM(h.at, tz)}`.replace(/\s+/g, ' ')}
              selected={asText(d.latest?.id) === asText(h.id)}
              onClick={() => {
                onSelect(i === 0 ? undefined : asText(h.id));
                setOpen(false);
              }}
              data-testid="intel-history-row"
            />
          ))}
        </ul>
      </DetailSheet>
    </section>
  );
}

// ── Hàng 2a: Việc phải quyết (điểm nhìn đầu) ─────────────────────────────────────────────────────────────────────────

export interface DecideRow {
  kind: 'draft' | 'proposal' | 'line';
  key: string;
  title: string;
  meta: string;
  draftId?: string;
  proposal?: OwnerProposal;
  to?: string;
}

const personOrGroupName = (s: unknown): string => (s && typeof s === 'object' ? asText((s as { name?: unknown }).name) : '');

/** ≤ 3 dòng: nháp (có lý do giữ lên trước) → đề xuất của Gen → (khi không đọc được nháp) các dòng của bản tin. */
export function buildDecideRows(
  drafts: readonly DraftItem[] | null,
  proposals: readonly OwnerProposal[] | null,
  fallback: SafeSection | null,
  limit = DECIDE_ROWS_MAX,
): DecideRow[] {
  const rows: DecideRow[] = [];
  const list = [...(drafts ?? [])].filter((d) => asText(d?.id));
  list.sort((a, b) => Number(!!asText(b.hold_reason)) - Number(!!asText(a.hold_reason)));
  for (const d of list) {
    const who = personOrGroupName(d.subject);
    rows.push({
      kind: 'draft',
      key: `d-${asText(d.id)}`,
      draftId: asText(d.id),
      title: asText(d.title) || 'Bản nháp chờ duyệt',
      meta: [asText(d.kind_label), who ? `Gửi ${who}` : '', asText(d.hold_reason)].filter(Boolean).join(' · '),
    });
  }
  for (const p of proposals ?? []) {
    if (!asText(p?.id)) continue;
    rows.push({ kind: 'proposal', key: `p-${asText(p.id)}`, proposal: p, title: `Gen đề xuất: ${asText(p.type_label) || 'việc mới'}`, meta: asText(p.summary) });
  }
  if (drafts === null && fallback) {
    fallback.lines.forEach((l, i) => rows.push({ kind: 'line', key: `l-${i}`, title: l, meta: '', to: fallback.to }));
  }
  return rows.slice(0, limit);
}

type Sheet = { kind: 'draft'; id: string } | { kind: 'proposal'; proposal: OwnerProposal } | null;

export function DecideBlock({
  drafts,
  proposals,
  today,
  brief,
  hideWhenEmpty,
  cell,
}: {
  drafts: UseQueryResult<DraftPage>;
  proposals: UseQueryResult<OwnerProposals>;
  today: UseQueryResult<OwnerToday>;
  brief: UseQueryResult<OwnerBrief>;
  /** Ngày đầu (chưa nối kênh): không có mục nào thì ẩn hẳn khối, không để EmptyState chồng lên thẻ "Nối Zalo". */
  hideWhenEmpty?: boolean;
  /** Lớp ô lưới (kit-c4 kit-tb4…). */
  cell?: string;
}) {
  const [sheet, setSheet] = useState<Sheet>(null);
  const title = 'Việc phải quyết';
  const more = (
    <Link to={OWNER_PATHS.work} className="kit-link" data-testid="intel-decide-all">
      Xem tất cả
    </Link>
  );
  const retryAll = () => void Promise.all([drafts.refetch(), proposals.refetch()]);
  const retrying = drafts.isFetching || proposals.isFetching;
  // Đề xuất của Gen "không đọc được" (lỗi hoặc máy chủ báo unavailable) KHÔNG phải "không có đề xuất".
  const proposalsDown = proposals.isError || proposals.data?.state === 'unavailable' || today.data?.nav?.proposals_unavailable === true;
  // Nháp là nguồn chính: chưa tải xong thì chưa được nói "không có gì" (nav.decide hợp lệ thì tin con số máy chủ).
  const navDecide = today.data?.nav?.decide;
  const navKnown = typeof navDecide === 'number' && Number.isFinite(navDecide) && navDecide >= 0;
  if (drafts.isPending && !navKnown) {
    return <Card title={title} state="loading" className={cx('intel-decide', cell)} data-testid="intel-decide-loading" />;
  }
  const draftItems = drafts.data ? (Array.isArray(drafts.data.items) ? drafts.data.items : []) : null;
  const propItems = proposals.data ? (Array.isArray(proposals.data.items) ? proposals.data.items : []) : null;
  const nDrafts = drafts.data ? (typeof drafts.data.total === 'number' ? drafts.data.total : (draftItems?.length ?? 0)) : 0;
  const nProps = propItems?.length ?? 0;
  const count = navKnown ? navDecide : nDrafts + nProps;
  const fallback = briefSections(brief.data).find((s) => s.key === 'drafts_pending') ?? null;
  const rows = buildDecideRows(draftItems, propItems, drafts.isError ? fallback : null);

  if (count === 0 && rows.length === 0) {
    // Không biết chắc là rỗng: chưa tải đủ hoặc có nguồn lỗi ⇒ không được viết "Không có gì chờ Sếp duyệt."
    const unsure = !navKnown && (drafts.isError || proposalsDown || proposals.isPending);
    if (unsure && (drafts.isError || proposalsDown)) {
      return (
        <Card title={title} state="error" className={cx('intel-decide', cell)} data-testid="intel-decide-error">
          <OwnerError error={drafts.isError ? drafts.error : proposals.error ?? new Error('Máy chủ chưa đọc được đề xuất của Gen')} onRetry={retryAll} retrying={retrying} />
        </Card>
      );
    }
    if (unsure) return <Card title={title} state="loading" className={cx('intel-decide', cell)} data-testid="intel-decide-loading" />;
    if (hideWhenEmpty) return null;
    return (
      <Card title={title} action={more} className={cx('intel-decide', cell)} data-testid="intel-decide">
        <EmptyState
          icon="ph ph-check-circle"
          title="Không có gì chờ Sếp duyệt."
          action={
            <Link to={OWNER_PATHS.work} className="kit-btn kit-btn--secondary">
              Xem việc hôm nay
            </Link>
          }
        />
      </Card>
    );
  }
  const context = [nDrafts > 0 ? `${countText(nDrafts)} nháp gửi khách` : '', nProps > 0 ? `${countText(nProps)} đề xuất của Gen` : ''].filter(Boolean).join(' · ');
  return (
    <Card title={title} action={more} padded={false} className={cx('intel-decide', cell)} data-testid="intel-decide">
      <div className="intel-decide__head">
        <p className="intel-decide__count">
          <span className="intel-big" data-testid="intel-decide-count">
            {countText(count)}
          </span>
          <span> việc chờ Sếp quyết</span>
        </p>
        {context ? <p className="owner-muted">{context}</p> : null}
        {drafts.isError || proposalsDown ? (
          <PartialLoadNote
            error={drafts.isError ? drafts.error : proposals.error}
            detail={drafts.isError || proposals.isError ? undefined : 'Máy chủ báo chưa đọc được đề xuất của Gen (state=unavailable)'}
            onRetry={retryAll}
            retrying={retrying}
            testId="intel-decide-partial"
          >
            {drafts.isError ? 'Chưa tải được danh sách nháp — Sếp mở Việc để xem đủ.' : 'Chưa đọc được đề xuất của Gen — Sếp mở Việc để xem đủ.'}
          </PartialLoadNote>
        ) : null}
      </div>
      <ul className="kit-list" aria-label="Việc chờ Sếp quyết">
        {rows.map((r) => (
          <ListRow
            key={r.key}
            as="li"
            title={r.title}
            meta={r.meta || undefined}
            data-testid={`intel-decide-row-${r.kind}`}
            actions={
              r.kind === 'draft' ? (
                <button type="button" className="kit-btn kit-btn--primary intel-btn" data-testid="intel-decide-approve" onClick={() => setSheet({ kind: 'draft', id: r.draftId ?? '' })}>
                  Duyệt
                </button>
              ) : r.kind === 'proposal' && r.proposal ? (
                <button type="button" className="kit-btn kit-btn--secondary intel-btn" data-testid="intel-decide-view" onClick={() => setSheet({ kind: 'proposal', proposal: r.proposal as OwnerProposal })}>
                  Xem
                </button>
              ) : (
                <Link to={safeLink(r.to, OWNER_PATHS.work)} className="kit-btn kit-btn--secondary intel-btn">
                  Xem
                </Link>
              )
            }
          />
        ))}
      </ul>
      <DetailSheet
        open={sheet !== null}
        title={sheet?.kind === 'proposal' ? 'Đề xuất của Gen' : 'Duyệt bản nháp'}
        onClose={() => setSheet(null)}
        data-testid="intel-decide-sheet"
      >
        {sheet?.kind === 'draft' ? <DraftDecide draftId={sheet.id} onDone={() => setSheet(null)} /> : null}
        {sheet?.kind === 'proposal' ? <ProposalDecide proposal={sheet.proposal} onDone={(o) => o !== 'expired' && setSheet(null)} /> : null}
      </DetailSheet>
    </Card>
  );
}

// ── Hàng 2b: Cảnh báo ────────────────────────────────────────────────────────────────────────────────────────────────

export interface AlertRowModel {
  key: string;
  title: string;
  meta: string;
  to: string;
  /** Mức: Khẩn (bad) / Quan trọng (warn) — hoặc trạng thái "Chưa tải được" cho mục bản tin lỗi. */
  badge: { kind: 'level'; level: 'Khẩn' | 'Quan trọng' } | { kind: 'status'; text: string };
}

const same = (a: string, b: string): boolean => {
  const x = a.toLowerCase().replace(/\s+/g, ' ').trim();
  const y = b.toLowerCase().replace(/\s+/g, ' ').trim();
  return !!x && !!y && (x.includes(y) || y.includes(x));
};

/** `brief.alerts` + dòng của mục `alert` (không trùng) + mục bản tin `state=error`. Chuỗi lạ ⇒ asText; object bị bỏ. */
export function buildAlertRows(alerts: unknown, sections: readonly SafeSection[], now: number = Date.now()): AlertRowModel[] {
  const rows: AlertRowModel[] = [];
  for (const a of Array.isArray(alerts) ? alerts : []) {
    if (!a || typeof a !== 'object') continue;
    const o = a as Record<string, unknown>;
    const title = asText(o.title).trim();
    if (!title) continue;
    const at = asText(o.raised_at);
    rows.push({
      key: `a-${asText(o.key) || rows.length}`,
      title,
      meta: Number.isFinite(Date.parse(at)) ? fmtAgo(at, now) : '',
      to: safeLink(o.to, OWNER_PATHS.intel),
      badge: { kind: 'level', level: o.severity === 'bad' ? 'Khẩn' : 'Quan trọng' },
    });
  }
  for (const s of sections) {
    if (s.group === 'alert' && s.state !== 'error') {
      const lines = s.lines.length > 0 ? s.lines : (s.count ?? 0) > 0 ? [`${s.title}: ${countText(s.count)}`] : [];
      for (const l of lines) {
        if (rows.some((r) => same(r.title, l))) continue;
        rows.push({ key: `s-${s.key}-${rows.length}`, title: l, meta: s.title, to: s.to, badge: { kind: 'level', level: 'Quan trọng' } });
      }
    }
  }
  for (const s of sections) {
    if (s.state === 'error') {
      rows.push({
        key: `e-${s.key}`,
        title: `Chưa lấy được mục “${s.title}”`,
        meta: 'Sếp thử lại sau ít phút',
        to: s.to,
        badge: { kind: 'status', text: 'Chưa tải được' },
      });
    }
  }
  return rows;
}

export function AlertBlock({ brief, hideWhenEmpty, cell }: { brief: UseQueryResult<OwnerBrief>; hideWhenEmpty?: boolean; cell?: string }) {
  const title = 'Cảnh báo';
  if (brief.isPending) return <Card title={title} state="loading" className={cx('intel-alerts', cell)} data-testid="intel-alerts-loading" />;
  if (brief.isError) {
    return (
      <Card title={title} state="error" className={cx('intel-alerts', cell)} data-testid="intel-alerts-error">
        <OwnerError error={brief.error} onRetry={() => void brief.refetch()} retrying={brief.isFetching} />
      </Card>
    );
  }
  const rows = buildAlertRows(brief.data.alerts, briefSections(brief.data));
  const urgent = rows.some((r) => r.badge.kind === 'level' && r.badge.level === 'Khẩn');
  const cls = ['intel-alerts', urgent ? 'intel-alerts--bad' : '', rows.length === 0 ? 'intel-alerts--quiet' : ''].filter(Boolean).join(' ');
  if (rows.length === 0) {
    if (hideWhenEmpty) return null;
    return (
      <Card title={title} className={cx(cls, cell)} data-testid="intel-alerts">
        <EmptyState
          icon="ph ph-shield-check"
          title="Không có cảnh báo nào đang mở."
          action={
            <Link to="/system?tab=storage&focus=health" className="kit-btn kit-btn--secondary">
              Xem tình trạng hệ thống
            </Link>
          }
        />
      </Card>
    );
  }
  return (
    <Card title={title} kicker={`${countText(rows.length)} mục đang mở`} padded={false} className={cx(cls, cell)} data-testid="intel-alerts" data-urgent={urgent || undefined}>
      <ul className="kit-list" aria-label="Cảnh báo đang mở">
        {rows.map((r) => (
          <ListRow
            key={r.key}
            as="li"
            title={
              <span className="intel-alert-title">
                {r.badge.kind === 'level' ? <Badge kind="level" level={r.badge.level} /> : <Badge kind="status" tone="warn">{r.badge.text}</Badge>}
                <span>{r.title}</span>
              </span>
            }
            meta={r.meta || undefined}
            data-testid="intel-alert-row"
            actions={
              <Link to={r.to} className="kit-btn kit-btn--secondary intel-btn">
                Xem
              </Link>
            }
          />
        ))}
      </ul>
    </Card>
  );
}

// ── Hàng 2c: Điểm chính ──────────────────────────────────────────────────────────────────────────────────────────────

export function KeyBlock({ brief, cell }: { brief: UseQueryResult<OwnerBrief>; cell?: string }) {
  const [open, setOpen] = useState<string | null>(null);
  const title = 'Điểm chính';
  if (brief.isPending) return <Card title={title} state="loading" className={cx('intel-key', cell)} data-testid="intel-key-loading" />;
  if (brief.isError) {
    return (
      <Card title={title} state="error" className={cx('intel-key', cell)} data-testid="intel-key-error">
        <OwnerError error={brief.error} onRetry={() => void brief.refetch()} retrying={brief.isFetching} />
      </Card>
    );
  }
  const items = keySections(briefSections(brief.data));
  if (items.length === 0) {
    return (
      <Card title={title} className={cx('intel-key', cell)} data-testid="intel-key">
        <EmptyState
          icon="ph ph-list-checks"
          title="Hôm nay chưa có điểm chính nào."
          action={
            <Link to={OWNER_PATHS.feed} className="kit-btn kit-btn--secondary">
              Xem Thông tin
            </Link>
          }
        />
      </Card>
    );
  }
  return (
    <Card title={title} kicker="Bấm để xem" padded={false} className={cx('intel-key', cell)} data-testid="intel-key">
      <ul className="kit-list" aria-label="Điểm chính hôm nay">
        {items.map((s) => {
          const expanded = open === s.key;
          const id = `intel-key-lines-${s.key.replace(/[^\w-]/g, '_')}`;
          return (
            <li key={s.key} className="intel-keyrow" data-testid={`intel-key-${s.key}`}>
              <button type="button" className="intel-keyrow__main" aria-expanded={expanded} aria-controls={id} onClick={() => setOpen(expanded ? null : s.key)}>
                <span className="intel-keyrow__title">{s.title}</span>
                <span className="intel-keyrow__n">{s.count === null ? '—' : `${countText(s.count)}${s.more ? '+' : ''}`}</span>
                <Icon name={expanded ? 'ph ph-caret-down' : 'ph ph-caret-right'} size={14} />
              </button>
              {expanded ? (
                <div className="intel-keyrow__more" id={id}>
                  {s.lines.length > 0 ? (
                    <ul className="intel-lines">
                      {s.lines.map((l, i) => (
                        <li key={i}>{l}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="owner-muted">Chưa có dòng nào để hiện.</p>
                  )}
                  <Link to={s.to} className="kit-link">
                    Xem tất cả
                  </Link>
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
