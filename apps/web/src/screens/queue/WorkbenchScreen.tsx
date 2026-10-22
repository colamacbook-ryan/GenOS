import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { DraftCreate, DraftDetail, DraftItem, DraftStatus, GroupRef, PersonRef, SendResult } from '@gen-harness/contracts';
import { Button, Card, EmptyState, Icon, Segmented, Skeleton, Switch, Tag, TextField, type Tone } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { errorText } from '../../lib/errorText';
import { fmtDMClock, fmtInt } from '../../lib/format';
import { useCan } from '../../lib/permissions';
import { ActionError, CardError, InlineError, Panel, SkeletonLines } from '../common';
import { WhyButton } from '../core/Evidence';
import { qk3, useDraft, useDraftsPaged } from '../core/queries';
import { DataEmptyState } from '../DataEmptyState';
import { hasMaskedNumber } from './queueModel';

const KIND_TONE: Record<DraftItem['kind'], Tone> = {
  quotation: 'bad',
  contract: 'bad',
  message: 'accent',
  reminder: 'neutral',
  report: 'neutral',
  mcp_write: 'warn',
};

const KIND_LABEL: Record<DraftItem['kind'], string> = {
  message: 'Tin nhắn',
  quotation: 'Báo giá',
  contract: 'Hợp đồng',
  reminder: 'Nhắc việc',
  report: 'Báo cáo',
  mcp_write: 'Ghi hệ thống ngoài',
};

const LANGS: Array<{ value: 'vi' | 'en' | 'zh' | 'ja' | 'ko'; label: string }> = [
  { value: 'vi', label: 'VI' },
  { value: 'en', label: 'EN' },
  { value: 'zh', label: 'ZH' },
  { value: 'ja', label: 'JA' },
  { value: 'ko', label: 'KO' },
];

/**
 * Nhãn trạng thái bản nháp bằng tiếng Việt (F086) — không bao giờ in thẳng mã `failed/approved/...`.
 * `edited` = đã duyệt kèm chỉnh sửa nhưng CHƯA có kết quả từ kênh chat (có thể còn thành gửi lỗi) nên không được nói "đã gửi".
 */
const STATUS_LABEL: Record<DraftStatus, string> = {
  pending: 'Chờ duyệt',
  approved: 'Đã duyệt',
  edited: 'Đã sửa, chờ gửi',
  sent: 'Đã gửi',
  failed: 'Gửi lỗi',
  rejected: 'Đã huỷ',
};
const STATUS_TONE: Record<DraftStatus, Tone> = {
  pending: 'neutral',
  approved: 'warn',
  edited: 'warn',
  sent: 'ok',
  failed: 'bad',
  rejected: 'neutral',
};
/** Chữ cho "đã duyệt nhưng chưa có việc nào được làm": mcp_write cũ (trước v0.1.61 duyệt xong là đặt `sent` dù chưa gọi tool). */
const MCP_WRITE_LEGACY_LABEL = 'Đã duyệt — chưa thực hiện (bản cũ)';
/**
 * Nhãn theo cả loại. Chỉ nhắc việc mới có việc thật khi duyệt xong (tạo công việc) nên mới nói "Đã thực hiện".
 * `mcp_write` + `sent` chỉ còn là yêu cầu cũ chưa hề gọi hệ thống ngoài (F074/Q8-1) → không báo "đã làm".
 * `report` + `sent`: duyệt xong chưa tạo báo cáo nào (Nợ: nối bước tạo báo cáo) → chỉ nói "Đã duyệt".
 * Bản gửi lỗi đã có bản gửi lại: nói rõ để không bấm gửi lại lần nữa.
 */
function statusLabel(status: DraftStatus, kind: DraftItem['kind'], resent = false): string {
  if (status === 'failed' && resent) return 'Gửi lỗi · đã gửi lại';
  if (status === 'sent') {
    if (kind === 'reminder') return 'Đã thực hiện';
    if (kind === 'mcp_write') return MCP_WRITE_LEGACY_LABEL;
    if (kind === 'report') return 'Đã duyệt';
  }
  return STATUS_LABEL[status];
}
function statusTone(status: DraftStatus, kind: DraftItem['kind'], resent = false): Tone {
  if (status === 'failed' && resent) return 'neutral';
  if (status === 'sent' && kind === 'mcp_write') return 'warn';
  if (status === 'sent' && kind === 'report') return 'neutral';
  return STATUS_TONE[status];
}

/**
 * Câu tiếng Việt cho mã lỗi gửi. Máy chủ đã gửi sẵn `send_result.message`; bảng này chỉ cho bản nháp lỗi cũ
 * (trước v0.1.61) chưa có câu đó. Mã gốc luôn nằm ở "Chi tiết kỹ thuật".
 */
const SEND_ERROR_TEXT: Record<string, string> = {
  NO_TARGET: 'Bản nháp chưa có nơi nhận (nhóm hoặc người nhận) nên chưa gửi được.',
  TARGET_NOT_FOUND: 'Không tìm thấy nhóm hoặc tài khoản nhận trên nền tảng (có thể đã rời nhóm hoặc đổi tài khoản).',
  SESSION_NOT_ACTIVE: 'Kênh chat đang ngắt kết nối nên tin chưa đi. Đăng nhập lại kênh rồi gửi lại.',
  PERMIT_EXPIRED: 'Chưa nhận được xác nhận đã gửi từ kênh chat, tin có thể chưa tới người nhận. Kiểm tra trên ứng dụng chat rồi gửi lại.',
  SEND_FAILED: 'Nền tảng chat từ chối hoặc không gửi được tin này.',
};
const SEND_ERROR_FALLBACK = 'Tin chưa gửi được vì lỗi từ kênh chat.';
type SendResultView = SendResult & { message?: string | null };
const sendErrorText = (r: SendResultView): string => r.message || SEND_ERROR_TEXT[r.error ?? ''] || SEND_ERROR_FALLBACK;

/** Chữ khi tool ghi hệ thống ngoài chưa có bước thực thi sau duyệt (Q8-1) — khớp câu của máy chủ. */
const MCP_WRITE_NOTICE =
  'Chưa hỗ trợ thực thi tool ghi — đã giữ yêu cầu. Hệ thống ngoài chưa bị gọi; Sếp chỉ có thể xem hoặc huỷ bản nháp này.';

type Tab = 'pending' | 'decided' | 'failed';
const TAB_TITLE: Record<Tab, string> = { pending: 'Chờ Sếp duyệt', decided: 'Đã xử lý', failed: 'Gửi lỗi — cần gửi lại' };
/** Trần số trang tự tải thêm khi bộ lọc "chỉ bản đã gửi" (lọc ở máy khách) chưa có dòng nào. */
const MAX_AUTO_PAGES = 10;

/** Địa chỉ `?status=&kind=&id=`: KPI ở Tổng quan dẫn vào đây nên phải đọc được bộ lọc (F084). */
function tabOf(status: string | null, kind: string | null): Tab {
  if (status === 'failed') return 'failed';
  if (status === 'decided' || status === 'sent') return 'decided';
  if (status === 'pending') return 'pending';
  return kind ? 'decided' : 'pending';
}

export function WorkbenchScreen() {
  const [params, setParams] = useSearchParams();
  const id = params.get('id') ?? '';
  const status = params.get('status');
  const rawKind = params.get('kind');
  const kind = rawKind && rawKind in KIND_LABEL ? (rawKind as DraftItem['kind']) : null;
  const onlySent = status === 'sent';
  const tab = tabOf(status, kind);

  /** Một lần cập nhật địa chỉ (đổi tab xoá luôn mục đang chọn) — tránh hai lần `set` đè nhau. */
  const patch = useCallback(
    (changes: Record<string, string | null>) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [k, v] of Object.entries(changes)) {
            if (v === null || v === '') next.delete(k);
            else next.set(k, v);
          }
          return next;
        },
        { replace: true },
      ),
    [setParams],
  );

  const pendingList = useDraftsPaged('pending', kind ?? undefined);
  const decidedList = useDraftsPaged('decided', kind ?? undefined);
  // "Gửi lỗi" lọc ở MÁY CHỦ (status=failed, đã bỏ bản có bản gửi lại): đếm đúng cả khi bản lỗi nằm xa trong lịch sử.
  const failedList = useDraftsPaged('failed', kind ?? undefined);
  const list = tab === 'pending' ? pendingList : tab === 'failed' ? failedList : decidedList;
  const draft = useDraft(id || null);

  const rows = useMemo(() => {
    const all = list.data?.pages.flatMap((p) => p.items) ?? [];
    if (tab === 'decided' && onlySent) return all.filter((d) => d.status === 'sent');
    return all;
  }, [list.data, tab, onlySent]);
  const failedTotal = failedList.data?.pages[0]?.total;
  const sentLoaded = useMemo(() => rows.filter((d) => d.status === 'sent').length, [rows]);

  // Chỉ bộ lọc "bản đã gửi" còn lọc ở máy khách: chưa có dòng nào mà còn trang sau thì tải tiếp (có trần).
  const needMore = tab === 'decided' && onlySent && rows.length === 0 && !!list.hasNextPage && (list.data?.pages.length ?? 0) < MAX_AUTO_PAGES;
  const { fetchNextPage, isFetchingNextPage } = list;
  const loadedPages = list.data?.pages.length ?? 0;
  useEffect(() => {
    if (needMore && !isFetchingNextPage) void fetchNextPage();
    // `loadedPages` trong deps: trang về nhanh thì cờ isFetchingNextPage không kịp đổi giữa hai lần vẽ, thiếu nó vòng tải dừng ở trang 2.
  }, [needMore, isFetchingNextPage, fetchNextPage, loadedPages]);

  useEffect(() => {
    if (!id && rows.length) patch({ id: rows[0].id });
  }, [id, rows, patch]);

  const tabOptions: Array<{ value: Tab; label: string }> = [
    { value: 'pending', label: 'Chờ duyệt' },
    { value: 'decided', label: 'Đã xử lý' },
    { value: 'failed', label: failedTotal ? `Gửi lỗi (${fmtInt(failedTotal)})` : 'Gửi lỗi' },
  ];
  const changeTab = (t: Tab) => patch({ status: t === 'pending' ? null : t, id: null });

  const pendingTotal = pendingList.data?.pages[0]?.total;
  const kicker =
    tab === 'pending'
      ? pendingTotal !== undefined
        ? `${fmtInt(pendingTotal)} bản nháp`
        : 'Đang tải…'
      : list.data
        ? tab === 'failed' && failedTotal !== undefined
          ? `${fmtInt(failedTotal)} bản nháp`
          : `${fmtInt(rows.length)}${list.hasNextPage ? '+' : ''} bản nháp${tab === 'decided' && sentLoaded > 0 ? ` · ${fmtInt(sentLoaded)} đã gửi` : ''}`
        : 'Đang tải…';

  return (
    <div className="screen">
      <div className="wb-grid">
        <Panel title={TAB_TITLE[tab]} kicker={kicker} bodyClass="wb-list" genTarget="workbench.drafts">
          <div className="wb-tabs">
            <Segmented options={tabOptions} value={tab} onChange={changeTab} label="Nhóm bản nháp" />
          </div>
          {kind || onlySent ? (
            <div className="wb-filters">
              {kind ? (
                <button type="button" className="wb-filter" onClick={() => patch({ kind: null, id: null })} aria-label={`Bỏ lọc loại ${KIND_LABEL[kind]}`}>
                  Loại: {KIND_LABEL[kind]} <Icon name="ph ph-x" size={11} />
                </button>
              ) : null}
              {onlySent ? (
                <button type="button" className="wb-filter" onClick={() => patch({ status: 'decided', id: null })} aria-label="Bỏ lọc chỉ bản đã gửi">
                  Chỉ bản đã gửi <Icon name="ph ph-x" size={11} />
                </button>
              ) : null}
            </div>
          ) : null}
          {list.isPending ? (
            <SkeletonLines rows={5} padding="12px 16px" />
          ) : list.isError ? (
            <CardError error={list.error} onRetry={() => void list.refetch()} retrying={list.isFetching} />
          ) : rows.length === 0 ? (
            list.isFetchingNextPage || needMore ? (
              <SkeletonLines rows={3} padding="12px 16px" />
            ) : list.hasNextPage ? (
              // Còn trang chưa tải mà các trang đã tải chưa có dòng nào khớp: KHÔNG được nói "không có" (có thể nằm ở trang sau).
              <EmptyState
                icon="ph ph-magnifying-glass"
                title="Chưa quét hết danh sách"
                description="Các bản nháp đã tải chưa có dòng nào khớp, nhưng còn bản cũ hơn chưa tải."
                actions={
                  <Button variant="secondary" className="btn-27" loading={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>
                    Tải thêm
                  </Button>
                }
              />
            ) : tab === 'pending' && !kind ? (
              <DataEmptyState fallback={<EmptyState icon="ph ph-tray" title="Không có bản nháp nào chờ duyệt" />} />
            ) : (
              <EmptyState
                icon={tab === 'failed' ? 'ph ph-check-circle' : 'ph ph-tray'}
                title={tab === 'failed' ? 'Không có tin nào gửi lỗi' : tab === 'decided' ? 'Chưa có bản nháp nào đã xử lý' : 'Không có bản nháp nào chờ duyệt'}
                description={tab === 'failed' ? 'Tin gửi không đi được sẽ hiện ở đây, kèm lý do và nút gửi lại.' : undefined}
              />
            )
          ) : (
            <>
              {rows.map((d) => (
                <button key={d.id} type="button" className="wb-list__row" aria-pressed={d.id === id} onClick={() => patch({ id: d.id })}>
                  <span className="wb-list__tags">
                    <Tag tone={KIND_TONE[d.kind]}>{d.kind_label}</Tag>
                    {tab !== 'pending' ? <Tag tone={statusTone(d.status, d.kind, !!d.resent_by)}>{statusLabel(d.status, d.kind, !!d.resent_by)}</Tag> : null}
                  </span>
                  <span className="wb-list__title">{d.title}</span>
                  <span className="wb-list__meta">
                    {d.agent ? d.agent.name : (d.created_by?.name ?? '—')} · {fmtDMClock(d.created_at)}
                  </span>
                </button>
              ))}
              {list.hasNextPage ? (
                <div className="wb-more">
                  <Button variant="secondary" className="btn-27" loading={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>
                    Tải thêm
                  </Button>
                </div>
              ) : null}
            </>
          )}
        </Panel>

        {!id ? (
          <Card className="wb-empty">
            <EmptyState
              icon="ph ph-pen-nib"
              title="Chọn một bản nháp để xem"
              description={tab === 'pending' ? 'Danh sách bên trái liệt kê mọi bản nháp đang chờ Sếp duyệt.' : 'Chọn một dòng ở danh sách bên trái.'}
            />
          </Card>
        ) : draft.isPending ? (
          <Card>
            <SkeletonLines rows={6} />
          </Card>
        ) : draft.isError ? (
          <Card>
            <CardError error={draft.error} onRetry={() => void draft.refetch()} retrying={draft.isFetching} />
          </Card>
        ) : (
          <DraftDetailView d={draft.data} onOpenDraft={(nid) => patch({ id: nid, status: null })} />
        )}
      </div>
    </div>
  );
}

/** Nội dung gửi lại: bản nháp MỚI chờ duyệt (đi lại đủ cổng Duyệt + PIN), giữ nguyên nơi nhận và nguồn dữ liệu. */
function resendBody(d: DraftDetail): DraftCreate | null {
  if (!d.target || d.kind === 'mcp_write') return null;
  const t = d.target;
  const subject = d.subject ? ('channel' in d.subject ? ({ type: 'group', id: (d.subject as GroupRef).id } as const) : ({ type: 'person', id: (d.subject as PersonRef).id } as const)) : null;
  return {
    kind: d.kind,
    title: d.title,
    text: d.text,
    target: {
      channel: t.channel,
      thread_type: t.thread_type,
      ...(t.group ? { group_id: t.group.id } : {}),
      ...(t.person ? { person_id: t.person.id } : {}),
      ...(t.identity_id ? { identity_id: t.identity_id } : {}),
    },
    amount_vnd: d.amount_vnd,
    subject,
    sources: d.sources,
    resend_of: d.id,     // nối bản mới về bản lỗi này: bản cũ ra khỏi thẻ "Gửi lỗi", bấm lần hai bị máy chủ chặn
  };
}

const CHANNEL_NAME: Record<string, string> = { zalo: 'Zalo', whatsapp: 'WhatsApp', telegram: 'Telegram', linkedin: 'LinkedIn' };

/** "Gửi tới": nơi nhận + (tin 1-1) tài khoản cụ thể — Sếp nhìn thấy trước khi duyệt, không phải đoán tin sẽ đi đâu. */
function TargetLine({ target }: { target: NonNullable<DraftDetail['target']> }) {
  const channel = CHANNEL_NAME[target.channel] ?? target.channel;
  const who = target.group ? `Nhóm ${target.group.name}` : (target.person?.name ?? 'Người nhận');
  return (
    <div className="wb-target">
      <Icon name="ph ph-paper-plane-right" size={13} />
      <span>
        Gửi tới <strong>{who}</strong> qua {channel}
        {!target.group ? (target.account ? <> · tài khoản <strong>{target.account.label}</strong></> : ' · chưa tìm thấy tài khoản nhận') : null}
      </span>
    </div>
  );
}

function DraftDetailView({ d, onOpenDraft }: { d: DraftDetail; onOpenDraft: (id: string) => void }) {
  const qc = useQueryClient();
  const canApprove = useCan('action.approve');
  const canDraft = useCan('action.draft');
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(d.text);
  const [sides, setSides] = useState<Record<string, boolean>>(() => Object.fromEntries(d.side_actions.map((s) => [s.key, s.on])));
  const [translating, setTranslating] = useState(false);
  const [translated, setTranslated] = useState<{ lang: string; text: string } | null>(null);
  // v0.1.67 (F081): ô "Yêu cầu soạn lại" (mềm hơn / ngắn hơn…) — trước đây nút Soạn lại luôn gửi instruction = null.
  const [instruction, setInstruction] = useState('');

  useEffect(() => {
    setText(d.text);
    setEditing(false);
    setTranslated(null);
    setInstruction('');
    setSides(Object.fromEntries(d.side_actions.map((s) => [s.key, s.on])));
  }, [d.id, d.text, d.side_actions]);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: qk3.drafts });
    void qc.invalidateQueries({ queryKey: qk3.draft(d.id) });
  };
  const approve = useMutation({
    mutationFn: () => api.drafts.approve(d.id, { side_actions: sides }),
    onSuccess: invalidate,
  });
  const editSend = useMutation({
    mutationFn: () => api.drafts.editSend(d.id, { text, side_actions: sides }),
    onSuccess: () => {
      setEditing(false);
      invalidate();
    },
  });
  const reject = useMutation({
    mutationFn: (reason: string | null) => api.drafts.reject(d.id, { reason }),
    onSuccess: invalidate,
  });
  const regenerate = useMutation({
    mutationFn: (ask: string | null) => api.drafts.regenerate(d.id, ask),
    onSuccess: () => {
      setInstruction('');
      invalidate();
    },
  });
  const translate = useMutation({
    mutationFn: (lang: (typeof LANGS)[number]['value']) => api.drafts.translate(d.id, lang),
    onSuccess: (r) => setTranslated(r),
  });
  const resend = useMutation({
    mutationFn: () => {
      const body = resendBody(d);
      if (!body) throw new Error('Bản nháp này không có nơi nhận để gửi lại.');
      return api.drafts.create(body);
    },
    onSuccess: (nd) => {
      invalidate();
      onOpenDraft(nd.id);
    },
  });

  const decided = d.status !== 'pending';
  const busy = approve.isPending || editSend.isPending || reject.isPending;
  const isMcpWrite = d.kind === 'mcp_write';
  const sendResult = d.send_result as SendResultView | null;
  const failed = d.status === 'failed';
  const resentBy = failed ? (d.resent_by ?? null) : null;
  const canResend = failed && canDraft && !!resendBody(d) && !resentBy;

  return (
    <Card className="wb-detail" padded={false}>
      <div className="wb-detail__head">
        <div>
          <h2 className="wb-detail__title">{d.title}</h2>
          <div className="wb-detail__meta">
            {d.code} · {d.agent ? `${d.agent.name} soạn` : d.created_by ? `${d.created_by.name} tạo` : 'Hệ thống tạo'} · {fmtDMClock(d.created_at)}
          </div>
        </div>
        <div className="wb-detail__head-actions">
          <Button variant="secondary" icon="ph ph-translate" onClick={() => setTranslating((v) => !v)}>
            Dịch
          </Button>
          <Button
            variant="secondary"
            icon="ph ph-arrow-clockwise"
            loading={regenerate.isPending}
            disabled={decided}
            onClick={() => regenerate.mutate(instruction.trim() || null)}
          >
            Soạn lại
          </Button>
        </div>
      </div>

      {!decided && canDraft && !isMcpWrite ? (
        <div className="wb-regen">
          <TextField
            label="Yêu cầu khi soạn lại (không bắt buộc)"
            placeholder="vd: mềm hơn, ngắn hơn, nhắc lại hạn giao hàng"
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            maxLength={1000}
          />
          {d.versions.length > 0 && canApprove ? (
            <div>
              {hasMaskedNumber(d.versions[d.versions.length - 1].text) ? (
                <p className="wb-notice" role="status">
                  Bản trước có số đã che — chỉ Sếp khôi phục được (gửi đi sẽ mất số thật).
                </p>
              ) : (
                <Button
                  variant="secondary"
                  className="btn-27"
                  icon="ph ph-arrow-u-up-left"
                  onClick={() => {
                    setText(d.versions[d.versions.length - 1].text);
                    setEditing(true);
                  }}
                >
                  Khôi phục bản trước
                </Button>
              )}
            </div>
          ) : null}
        </div>
      ) : null}

      {translating ? (
        <div className="wb-translate">
          <Segmented options={LANGS} value={(translated?.lang as (typeof LANGS)[number]['value']) ?? 'vi'} onChange={(v) => translate.mutate(v)} label="Ngôn ngữ dịch" />
          {translate.isPending ? <Skeleton width="80%" height={12} /> : translated ? <p className="wb-translate__text">{translated.text}</p> : null}
          {translated && !decided && canApprove && !isMcpWrite ? (
            <div>
              <Button
                variant="secondary"
                className="btn-27"
                icon="ph ph-arrow-line-down"
                onClick={() => {
                  setText(translated.text);
                  setEditing(true);
                }}
              >
                Dùng bản dịch này
              </Button>
            </div>
          ) : null}
          {translate.isError ? <ActionError error={translate.error} /> : null}
        </div>
      ) : null}

      <div className="wb-detail__body">
        {editing ? (
          <textarea className="wb-textarea" value={text} onChange={(e) => setText(e.target.value)} rows={12} aria-label="Sửa nội dung bản nháp" />
        ) : (
          d.paragraphs.map((p, i) => (
            <p className="wb-para" key={i}>
              {p}
            </p>
          ))
        )}
      </div>

      {d.target ? <TargetLine target={d.target} /> : null}

      {failed && sendResult ? (
        <div className="wb-fail">
          <InlineError detail={sendResult.error ?? undefined}>{sendErrorText(sendResult)}</InlineError>
        </div>
      ) : null}

      {d.sources.length > 0 ? (
        <div className="wb-sources">
          <div className="wb-sources__title">
            <Icon name="ph ph-database" size={13} />
            Dữ liệu agent đã dùng — không có chỗ nào bịa
          </div>
          <div className="wb-sources__chips">
            {d.sources.map((s, i) =>
              s.ref ? (
                <WhyButton key={i} kind={s.ref.type} id={s.ref.id} icon="ph ph-link" size="sm">
                  {s.label}
                </WhyButton>
              ) : (
                <span key={i} className="wb-source-chip">
                  <Icon name="ph ph-file-text" size={12} />
                  {s.label}
                </span>
              ),
            )}
          </div>
        </div>
      ) : null}

      {d.side_actions.length > 0 ? (
        <div className="wb-sides">
          <div className="wb-sources__title">Tạo kèm theo — hành động đi cùng khi duyệt</div>
          {d.side_actions.map((s) => (
            <label key={s.key} className="wb-sides__row">
              <span>{s.label}</span>
              <Switch checked={sides[s.key] ?? s.on} disabled={decided || !canApprove} onChange={(v) => setSides((prev) => ({ ...prev, [s.key]: v }))} label={s.label} />
            </label>
          ))}
        </div>
      ) : null}

      <div className="wb-footer">
        {decided ? (
          <div className="wb-footer__decided">
            <Tag tone={statusTone(d.status, d.kind, !!resentBy)}>{statusLabel(d.status, d.kind, !!resentBy)}</Tag>
            {d.decision ? (
              <span>
                bởi {d.decision.by.name} · {fmtDMClock(d.decision.at)}
                {d.decision.reason ? ` · ${d.decision.reason}` : ''}
              </span>
            ) : null}
            {d.status === 'approved' || d.status === 'edited' ? <span>Đang chờ kênh chat xác nhận đã gửi.</span> : null}
            {d.status === 'sent' && isMcpWrite ? <span>Yêu cầu duyệt từ bản cũ — hệ thống ngoài chưa hề được gọi. Sếp cần làm việc đó bên ngoài.</span> : null}
            {resentBy ? (
              <>
                <span>Đã gửi lại bằng bản {resentBy.code}.</span>
                <Button variant="secondary" icon="ph ph-arrow-square-out" onClick={() => onOpenDraft(resentBy.id)}>
                  Mở bản gửi lại
                </Button>
              </>
            ) : null}
            {canResend ? (
              <Button variant="primary" icon="ph ph-paper-plane-tilt" loading={resend.isPending} onClick={() => resend.mutate()}>
                Gửi lại
              </Button>
            ) : null}
            {failed && !resendBody(d) && d.kind !== 'mcp_write' ? <span>Bản nháp này không có nơi nhận nên không gửi lại được — soạn lại từ Hộp thư.</span> : null}
          </div>
        ) : !canApprove ? (
          <div className="wb-footer__decided">
            <Icon name="ph ph-hourglass" size={14} />
            <span>Chờ Sếp/Quản lý duyệt — vai trò của bạn chỉ soạn nháp, không duyệt hay gửi.</span>
            {d.hold_reason ? <span className="wb-footer__hold">{d.hold_reason}</span> : null}
          </div>
        ) : isMcpWrite ? (
          <>
            <p className="wb-notice" role="status">
              <Icon name="ph ph-warning" size={14} /> {MCP_WRITE_NOTICE}
            </p>
            <Button variant="ghost" icon="ph ph-x" loading={reject.isPending} disabled={busy} onClick={() => reject.mutate(null)}>
              Huỷ bản nháp
            </Button>
          </>
        ) : editing ? (
          <>
            <Button variant="primary" icon="ph ph-paper-plane-tilt" loading={editSend.isPending} disabled={!text.trim()} onClick={() => editSend.mutate()}>
              Gửi nội dung đã sửa
            </Button>
            <Button variant="secondary" onClick={() => setEditing(false)}>
              Huỷ sửa
            </Button>
          </>
        ) : (
          <>
            <Button variant="primary" icon="ph ph-paper-plane-tilt" loading={approve.isPending} disabled={busy} onClick={() => approve.mutate()}>
              {d.approve_label}
            </Button>
            <Button variant="secondary" icon="ph ph-pencil-simple" disabled={busy} onClick={() => setEditing(true)}>
              Sửa rồi gửi
            </Button>
            <Button variant="ghost" icon="ph ph-x" loading={reject.isPending} disabled={busy} onClick={() => reject.mutate(null)}>
              Huỷ bản nháp
            </Button>
            {d.hold_reason ? <span className="wb-footer__hold">{d.hold_reason}</span> : null}
          </>
        )}
      </div>
      {regenerate.isError ? <ActionError error={regenerate.error} /> : null}
      {approve.isError ? <InlineError>{errorText(approve.error)}</InlineError> : null}
      {editSend.isError ? <InlineError>{errorText(editSend.error)}</InlineError> : null}
      {reject.isError ? <InlineError>{errorText(reject.error)}</InlineError> : null}
      {resend.isError ? <InlineError>{errorText(resend.error)}</InlineError> : null}
    </Card>
  );
}
