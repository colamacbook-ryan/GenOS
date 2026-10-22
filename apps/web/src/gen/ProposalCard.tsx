/**
 * Gen v2 (A4) — thẻ đề xuất thao tác có xác nhận: nháp tin gửi đi, nhắc việc, giao người phụ trách. v0.1.50 (F-81, QD-18):
 * thẻ "Ghi nhớ" (Gen nhớ, không PIN) và thẻ "Ghi vào Kho dữ liệu" (tạo / sửa bản ghi Phiên · Việc qua Gen-hub, cần PIN).
 *
 * Gen KHÔNG tự làm: thẻ hiện form điền sẵn + tóm tắt do hệ thống viết; chỉ khi bấm **Xác nhận** web mới gọi
 * `POST /gen/proposals/{id}/confirm` (server thực hiện nhân danh người bấm qua endpoint sẵn có, đúng quyền + PIN).
 * **Sửa** mở form để chỉnh trường được phép; **Huỷ** bỏ đề xuất. Cần PIN → API trả 423, client tự hỏi PIN rồi gửi lại.
 */
import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  GEN_SCREEN_BY_KEY,
  KHO_FIELDS,
  KHO_PRIORITY,
  KHO_REQUIRED,
  KHO_STATUS,
  khoMaxLen,
  relabelKho,
  type AssignFields,
  type DraftMessageFields,
  type GenProposal,
  type ReminderFields,
  type SocialWriteFields,
} from '@gen-harness/contracts';
import { Button, Icon, SelectField, TextField } from '@gen-harness/ui';
import { api } from '../lib/api';
import { errorText } from '../lib/errorText';
import { fmtDMClock } from '../lib/format';
import { navigateTo } from '../lib/navigation';
import { useCan } from '../lib/permissions';
import { useNow } from '../lib/useNow';
import { writeHidden } from '../screens/mcp/mcpModel';
import { qkMcp } from '../screens/mcp/queries';
import { ErrorWithDetail, WriteProofDialog } from '../social/WriteProofDialog';
import { PROOF_MISSING_TEXT, WRITE_RISK_PATH, proofMissing, qkSocial, writeStatusView } from '../social/socialModel';
import { charCount, MEMORY_MAX_LEN, MEMORY_REASON_MAX } from './genMemoryModel';
import { loadConversation } from './genClient';
import { patchProposal, useGenStore } from './genStore';
import {
  HUB_CARD_PATH,
  KHO_CANCELLED_UNCERTAIN_TEXT,
  KHO_MISSING_HINT,
  KHO_MISSING_TEXT,
  KHO_WRITE_WARNING,
  MCP_HUB_PATH,
  MEMORY_CARD_PATH,
  RELOAD_BUSY_TEXT,
  isKhoWrite,
  isMemoryNote,
  khoBangOf,
  khoCurrent,
  khoHiddenText,
  khoReleaseNote,
  khoToolWritable,
  khoUncertain,
  khoDraftValid,
  khoFieldKind,
  khoLengthError,
  khoLinkError,
  khoRows,
  khoTargetText,
  proposalErrorDetail,
  proposalErrorView,
  valueText,
  writeScopeMissing,
  type KhoProposal,
  type MemoryProposal,
} from './khoWriteModel';
import {
  SOCIAL_SUSPICIOUS_WARNING,
  SOCIAL_WRITE_WARNING,
  WRITE_MAX_TEXT,
  WRITE_POLL_MAX_MS,
  WRITE_POLL_MS,
  changedFields,
  fromLocalInput,
  initialDraft,
  isSocialWrite,
  isTerminalJob,
  PROPOSAL_TITLE,
  writeErrorKind,
  type Draft,
} from './proposalModel';

const PROPOSAL_ICON: Record<GenProposal['type'], string> = {
  draft_message: 'ph ph-note-pencil',
  reminder: 'ph ph-alarm',
  assign: 'ph ph-user-switch',
  social_reply: 'ph ph-chat-circle-text',
  social_dm: 'ph ph-paper-plane-tilt',
  memory_note: 'ph ph-brain',
  kho_create: 'ph ph-database',
  kho_update: 'ph ph-database',
};

const PRIORITIES = [
  { value: 'P1', label: 'P1 — gấp' },
  { value: 'P2', label: 'P2' },
  { value: 'P3', label: 'P3' },
];

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </>
  );
}

/** Thẻ Ghi nhớ: nội dung sẽ nhớ + lý do (Gen nhớ hiện có n/30 ghi chú). */
function MemorySummary({ p }: { p: MemoryProposal }) {
  const reason = valueText(p.fields.reason).trim();
  const count = valueText(p.labels.count).trim();
  return (
    <dl className="gen-prop__dl gen-prop__dl--memory">
      <Row label="Ghi nhớ">
        <span className="gen-prop__text">{valueText(p.fields.text)}</span>
      </Row>
      {reason ? <Row label="Lý do">{reason}</Row> : null}
      {count ? <Row label="Gen nhớ">{count} ghi chú</Row> : null}
    </dl>
  );
}

/** Thẻ Ghi vào Kho dữ liệu: bảng + bản ghi (khoá cứng) và BẢNG trường — đúng `fields.record`, hiện tại → sẽ ghi khi sửa. */
function KhoSummary({ p }: { p: KhoProposal }) {
  const update = p.type === 'kho_update';
  const rows = khoRows(p);
  return (
    <>
      <dl className="gen-prop__dl gen-prop__dl--kho">
        <Row label="Bảng">{khoBangOf(p) ?? (valueText(p.labels.bang) || '—')}</Row>
        <Row label="Bản ghi">{khoTargetText(p)}</Row>
      </dl>
      <table className="gen-prop__tbl" data-testid="gen-kho-table">
        <thead>
          <tr>
            <th scope="col">Trường</th>
            {update ? <th scope="col">Hiện tại</th> : null}
            <th scope="col">Sẽ ghi</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.field} data-field={r.field}>
              <th scope="row">{r.field}</th>
              {update ? <td className="gen-prop__tbl-cur">{r.cur}</td> : null}
              <td className="gen-prop__tbl-next">{r.next}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function Summary({ p }: { p: GenProposal }) {
  if (isMemoryNote(p)) return <MemorySummary p={p} />;
  if (isKhoWrite(p)) return <KhoSummary p={p} />;
  if (p.type === 'draft_message') {
    const f: DraftMessageFields = p.fields;
    return (
      <dl className="gen-prop__dl">
        <Row label="Tiêu đề">{f.title}</Row>
        {p.labels.subject ? <Row label="Gửi cho">{p.labels.subject}</Row> : null}
        <Row label="Nội dung">
          <span className="gen-prop__text">{f.text}</span>
        </Row>
      </dl>
    );
  }
  if (p.type === 'reminder') {
    const f: ReminderFields = p.fields;
    return (
      <dl className="gen-prop__dl">
        <Row label="Việc">{f.title}</Row>
        <Row label="Nhắc lúc">{fmtDMClock(f.remind_at)}</Row>
        {f.due_at ? <Row label="Hạn">{fmtDMClock(f.due_at)}</Row> : null}
        <Row label="Ưu tiên">{f.priority}</Row>
        <Row label="Giao cho">{p.labels.user ?? '—'}</Row>
      </dl>
    );
  }
  if (p.type === 'social_reply' || p.type === 'social_dm') {
    const f: SocialWriteFields = p.fields;
    return (
      <dl className="gen-prop__dl gen-prop__dl--social">
        <Row label="Tài khoản">{p.labels.account ?? f.account_id}</Row>
        <Row label={p.type === 'social_reply' ? 'Trả lời vào' : 'Nhắn cho'}>{p.labels.target ?? f.target_url}</Row>
        <Row label="Nội dung">
          <span className="gen-prop__text">{f.text}</span>
        </Row>
      </dl>
    );
  }
  const f: AssignFields = p.fields;
  return (
    <dl className="gen-prop__dl">
      <Row label={f.item_type === 'task' ? 'Việc' : 'Mục'}>{p.labels.item ?? '—'}</Row>
      <Row label="Giao cho">{p.labels.user ?? '—'}</Row>
    </dl>
  );
}

/**
 * Form Sửa của thẻ Ghi nhớ: hai ô nhiều dòng, mỗi ô có bộ đếm ký tự (0/280, 0/200). Lý do KHÔNG bắt buộc khi Sếp xác nhận — cùng
 * quy tắc ô "Lý do (không bắt buộc)" ở Cài đặt › Gen nhớ (Gen thì phải nêu lý do khi đề xuất).
 */
function MemoryEditForm({ p, draft, set }: { p: MemoryProposal; draft: Draft; set: (k: string, v: string) => void }) {
  return (
    <div className="gen-prop__form">
      <div className="gh-field">
        <label className="gh-field__label" htmlFor={`gen-prop-mem-text-${p.id}`}>
          Ghi nhớ
        </label>
        <textarea id={`gen-prop-mem-text-${p.id}`} className="gh-input" rows={4} maxLength={MEMORY_MAX_LEN} value={draft.text ?? ''} onChange={(e) => set('text', e.target.value)} />
        <span className="gen-prop__count" data-testid="gen-mem-count-text">
          {charCount(draft.text ?? '', MEMORY_MAX_LEN)}
        </span>
      </div>
      <div className="gh-field">
        <label className="gh-field__label" htmlFor={`gen-prop-mem-reason-${p.id}`}>
          Lý do (không bắt buộc)
        </label>
        <textarea id={`gen-prop-mem-reason-${p.id}`} className="gh-input" rows={3} maxLength={MEMORY_REASON_MAX} value={draft.reason ?? ''} onChange={(e) => set('reason', e.target.value)} />
        <span className="gen-prop__count" data-testid="gen-mem-count-reason">
          {charCount(draft.reason ?? '', MEMORY_REASON_MAX)}
        </span>
      </div>
    </div>
  );
}

/**
 * Form Sửa của thẻ Ghi vào Kho: một ô cho từng trường được phép của bảng (bảng / mã bản ghi khoá cứng, không có ô). Mỗi ô chữ có
 * giới hạn ký tự đúng `kho_write.py` + bộ đếm. Sửa bản ghi (`kho_update`): dưới mỗi ô là giá trị HIỆN TẠI trong Kho (nhãn
 * `cur:<trường>`; trường Gen không đề xuất sửa ⇒ "chưa đọc") và câu "Để trống = giữ nguyên" — xoá trắng một ô KHÔNG xoá trường
 * trong Kho (trường rỗng bị bỏ khỏi bản ghi gửi đi).
 */
function KhoEditForm({ p, draft, set }: { p: KhoProposal; draft: Draft; set: (k: string, v: string) => void }) {
  const bang = khoBangOf(p);
  if (!bang) return <p className="gen-prop__note">Không rõ bảng của đề xuất này — không sửa được; bấm Huỷ và nhờ Gen đề xuất lại.</p>;
  const update = p.type === 'kho_update';
  const linkError = khoLinkError(draft);
  return (
    <div className="gen-prop__form" data-testid="gen-kho-form">
      {update ? (
        <p className="gen-prop__note" data-testid="gen-kho-keep-note">
          Để trống = giữ nguyên trong Kho (không xoá giá trị cũ). Dưới mỗi ô là giá trị hiện tại.
        </p>
      ) : null}
      {KHO_FIELDS[bang].map((field) => {
        const kind = khoFieldKind(bang, field);
        const value = draft[field] ?? '';
        const cur = khoCurrent(p, field);
        const curHint = !update ? undefined : cur === null ? 'Hiện tại: chưa đọc (Gen không đề xuất sửa trường này)' : `Hiện tại: ${cur || '—'}`;
        const hint = field === KHO_REQUIRED[bang] && p.type === 'kho_create' ? 'Bắt buộc' : curHint;
        if (kind === 'status' || kind === 'priority') {
          const list: readonly string[] = kind === 'status' ? KHO_STATUS : KHO_PRIORITY;
          const options = [{ value: '', label: '— Không đặt —' }, ...(value && !list.includes(value) ? [{ value, label: value }] : []), ...list.map((v) => ({ value: v, label: v }))];
          return <SelectField key={field} label={field} value={value} hint={hint} options={options} onChange={(e) => set(field, e.target.value)} />;
        }
        const max = khoMaxLen(bang, field);
        const lengthError = kind === 'date' ? null : khoLengthError(bang, field, value);
        const counter =
          kind === 'date' ? undefined : (
            <span className="gen-prop__count" data-testid={`gen-kho-count-${field}`}>
              {charCount(value, max)}
            </span>
          );
        if (kind === 'long') {
          const id = `gen-prop-kho-${p.id}-${field}`;
          return (
            <div className="gh-field" key={field}>
              <label className="gh-field__label" htmlFor={id}>
                {field}
              </label>
              <textarea id={id} className="gh-input" rows={3} maxLength={max} value={value} aria-invalid={lengthError ? true : undefined} onChange={(e) => set(field, e.target.value)} />
              {counter}
              {lengthError ? <div className="gh-field__error">{lengthError}</div> : hint ? <div className="gh-field__hint">{hint}</div> : null}
            </div>
          );
        }
        return (
          <TextField
            key={field}
            label={field}
            type={kind === 'date' ? 'date' : 'text'}
            value={value}
            hint={hint}
            error={(field === 'Link Issue/PR' ? linkError : null) ?? lengthError}
            maxLength={kind === 'date' ? undefined : max}
            after={counter}
            placeholder={field === 'Link Issue/PR' ? 'https://github.com/…' : undefined}
            onChange={(e) => set(field, e.target.value)}
          />
        );
      })}
    </div>
  );
}

function EditForm({ p, draft, set }: { p: GenProposal; draft: Draft; set: (k: string, v: string) => void }) {
  const needsPeople = p.type === 'reminder' || p.type === 'assign';
  const people = useQuery({
    queryKey: ['gen', 'assignees'],
    queryFn: ({ signal }) => api.gen.assignees(signal),
    enabled: needsPeople,
    staleTime: 60_000,
  });
  const currentUserId = p.type === 'assign' ? p.fields.user_id : p.type === 'reminder' ? (p.fields.assignee_user_id ?? '') : '';
  const peopleOptions = [
    ...(people.data?.items ?? []).map((u) => ({ value: u.id, label: u.me ? `${u.name} (tôi)` : u.name })),
  ];
  if (currentUserId && !peopleOptions.some((o) => o.value === currentUserId)) peopleOptions.unshift({ value: currentUserId, label: p.labels.user ?? currentUserId });

  if (isMemoryNote(p)) return <MemoryEditForm p={p} draft={draft} set={set} />;
  if (isKhoWrite(p)) return <KhoEditForm p={p} draft={draft} set={set} />;
  if (p.type === 'social_reply' || p.type === 'social_dm')
    return (
      <div className="gen-prop__form">
        <div className="gh-field">
          <label className="gh-field__label" htmlFor={`gen-prop-text-${p.id}`}>
            Nội dung
          </label>
          <textarea id={`gen-prop-text-${p.id}`} className="gh-input" rows={5} maxLength={WRITE_MAX_TEXT} value={draft.text} onChange={(e) => set('text', e.target.value)} />
        </div>
      </div>
    );
  if (p.type === 'draft_message')
    return (
      <div className="gen-prop__form">
        <TextField label="Tiêu đề" value={draft.title} maxLength={200} onChange={(e) => set('title', e.target.value)} />
        <div className="gh-field">
          <label className="gh-field__label" htmlFor={`gen-prop-text-${p.id}`}>
            Nội dung
          </label>
          <textarea id={`gen-prop-text-${p.id}`} className="gh-input" rows={5} maxLength={4000} value={draft.text} onChange={(e) => set('text', e.target.value)} />
        </div>
      </div>
    );
  if (p.type === 'reminder')
    return (
      <div className="gen-prop__form">
        <TextField label="Việc" value={draft.title} maxLength={200} onChange={(e) => set('title', e.target.value)} />
        <TextField label="Nhắc lúc" type="datetime-local" value={draft.remind_at} onChange={(e) => set('remind_at', e.target.value)} />
        <TextField label="Hạn (không bắt buộc)" type="datetime-local" value={draft.due_at} onChange={(e) => set('due_at', e.target.value)} />
        <SelectField label="Ưu tiên" value={draft.priority} options={PRIORITIES} onChange={(e) => set('priority', e.target.value)} />
        <SelectField label="Giao cho" value={draft.assignee_user_id} options={peopleOptions} onChange={(e) => set('assignee_user_id', e.target.value)} />
      </div>
    );
  return (
    <div className="gen-prop__form">
      <SelectField label="Giao cho" value={draft.user_id} options={peopleOptions} onChange={(e) => set('user_id', e.target.value)} />
    </div>
  );
}

function valid(p: GenProposal, d: Draft): boolean {
  if (isMemoryNote(p)) return !!d.text?.trim() && [...d.text].length <= MEMORY_MAX_LEN && [...(d.reason ?? '')].length <= MEMORY_REASON_MAX;
  if (isKhoWrite(p)) return khoDraftValid(p, d);
  if (p.type === 'draft_message') return !!d.title?.trim() && !!d.text?.trim();
  if (p.type === 'reminder') return !!d.title?.trim() && !!fromLocalInput(d.remind_at);
  if (isSocialWrite(p)) return !!d.text?.trim() && d.text.length <= WRITE_MAX_TEXT;
  return !!d.user_id;
}

/** Theo dõi việc gửi (poll mỗi 2 giây, dừng khi xong, tối đa 4 phút): chờ → đang gửi → đã gửi (+ ảnh chụp) / lỗi / đã dừng. */
function WriteProgress({ jobId, initial }: { jobId: string; initial?: string }) {
  const started = useRef(Date.now());
  const now = useNow(5000);
  const [proof, setProof] = useState(false);
  const q = useQuery({
    queryKey: qkSocial.job(jobId),
    queryFn: ({ signal }) => api.social.job(jobId, signal),
    refetchInterval: (query) =>
      isTerminalJob(query.state.data?.status) || Date.now() - started.current > WRITE_POLL_MAX_MS ? false : WRITE_POLL_MS,
  });
  const job = q.data;
  const status = job?.status ?? (initial as 'queued' | undefined) ?? 'queued';
  const view = writeStatusView(job ?? { status: status as 'queued' });
  const timedOut = !view.terminal && now - started.current > WRITE_POLL_MAX_MS;
  const icon =
    status === 'done' ? 'ph-fill ph-check-circle' : status === 'failed' ? 'ph ph-warning-circle' : status === 'halted' || status === 'cancelled' ? 'ph ph-hand-palm' : 'ph ph-circle-notch';
  return (
    <div className="gen-prop__write" data-testid="gen-write-status" data-status={status} aria-live="polite">
      <div className="gen-prop__write-line" data-tone={view.tone}>
        <Icon name={icon} size={13} />
        <span>{view.label}</span>
      </div>
      {status === 'failed' ? (
        <ErrorWithDetail text={job?.error_text || 'Việc gửi gặp lỗi — chưa có gì được gửi đi.'} detail={job?.error ? `Mã lỗi ${job.error}` : null} />
      ) : null}
      {view.notes
        .filter((n) => n !== PROOF_MISSING_TEXT)
        .map((n) => (
          <p key={n} className="gen-prop__note">
            {n}
          </p>
        ))}
      {status === 'done' && job?.has_proof !== false ? (
        <Button variant="secondary" className="btn-27" icon="ph ph-image" onClick={() => setProof(true)}>
          Xem ảnh chụp
        </Button>
      ) : null}
      {status === 'done' && job && proofMissing(job) ? (
        <ErrorWithDetail text={PROOF_MISSING_TEXT} detail={job.result?.proof_error ? `Mã lỗi ${job.result.proof_error}` : null} className="write-error gen-prop__proof-missing" />
      ) : null}
      {timedOut ? (
        <>
          <p className="gen-prop__note">Chưa thấy kết quả sau 4 phút — Sếp xem ở trang Tài khoản mạng xã hội (Lần gửi gần đây).</p>
          <Button variant="secondary" className="btn-27" icon="ph ph-arrow-square-out" onClick={() => navigateTo('/social')}>
            Mở Tài khoản mạng xã hội
          </Button>
        </>
      ) : null}
      {q.isError && !job ? <ErrorWithDetail error={q.error} /> : null}
      {proof ? <WriteProofDialog jobId={jobId} open onClose={() => setProof(false)} /> : null}
    </div>
  );
}

export function ProposalCard({ proposal: p }: { proposal: GenProposal }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Draft>(() => initialDraft(p));
  const [busy, setBusy] = useState<'confirm' | 'cancel' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorRaw, setErrorRaw] = useState<unknown>(null);
  const social = isSocialWrite(p);
  const memory = isMemoryNote(p);
  const kho = isKhoWrite(p);
  const khoLabel = kho ? p.labels.kho : undefined;       // v0.1.57: tên Kho Owner tự đặt (máy chủ gắn vào nhãn; vắng = mặc định)
  // Cổng ghi: nhãn lúc đề xuất có thể đã cũ (Sếp vừa đồng ý ở trang cảnh báo) → hỏi lại cổng khi thẻ còn chờ và đang khoá.
  const labelLocked = social && p.labels.write_gate === 'locked';
  const gate = useQuery({
    queryKey: qkSocial.writeGate,
    queryFn: ({ signal }) => api.social.writeGate(signal),
    enabled: labelLocked && p.status === 'pending',
    retry: false,
  });
  const locked = labelLocked && !gate.data?.open;
  // v0.1.50: quyền ghi Kho — nhãn lúc đề xuất có thể đã cũ (Sếp vừa tick quyền ở Gen-hub) → hỏi lại liên kết Gen-hub khi thẻ
  // còn chờ và đang khoá; chỉ mở khoá khi Gen-hub báo RÕ đã có quyền ghi bằng ĐÚNG tool của thẻ (kho_create / kho_update —
  // như máy chủ kiểm theo từng tool; máy chủ cũ chỉ có cờ chung `kho`).
  const labelNoWrite = kho && writeScopeMissing(p);
  const hub = useQuery({
    queryKey: qkMcp.hubLink,
    queryFn: ({ signal }) => api.hub.link.get(signal),
    enabled: labelNoWrite && p.status === 'pending',
    retry: false,
  });
  const noWrite = labelNoWrite && !khoToolWritable(hub.data?.write_scopes, p.type as KhoProposal['type']);
  // Sếp TỰ đóng tool của thẻ ở MCP Hub ⇒ tick ở Gen-hub + Kiểm tra không giúp gì: chỉ đúng MCP Hub.
  const hiddenByOwner = noWrite && writeHidden(hub.data?.write_hidden).includes(p.type);
  const set = (k: string, v: string) => setDraft((d) => ({ ...d, [k]: v }));

  const confirm = async () => {
    setBusy('confirm');
    setError(null);
    setErrorRaw(null);
    try {
      const next = await api.gen.confirmProposal(p.id, editing ? changedFields(p, draft) : {});
      setEditing(false);
      patchProposal(next);
    } catch (e) {
      setError(proposalErrorView(e, khoLabel)?.text ?? errorText(e));
      setErrorRaw(e);
    } finally {
      setBusy(null);
    }
  };
  const cancel = async () => {
    setBusy('cancel');
    setError(null);
    setErrorRaw(null);
    try {
      patchProposal(await api.gen.cancelProposal(p.id));
    } catch (e) {
      setError(proposalErrorView(e, khoLabel)?.text ?? errorText(e));
      setErrorRaw(e);
    } finally {
      setBusy(null);
    }
  };

  // GEN_PROPOSAL_DECIDED: thẻ đã được xác nhận / đóng ở nơi khác (vd Owner khác đã ghi Phiên của bản này) — máy chủ đã lưu trạng
  // thái mới vào hội thoại ⇒ tải lại hội thoại để thẻ hiện đúng (không còn nút Xác nhận chết).
  const conversationId = useGenStore((s) => s.conversationId);
  const reload = async () => {
    const cid = conversationId;
    if (!cid) return;
    setBusy('cancel');
    try {
      const res = await loadConversation(cid);
      if (res === 'busy') {
        // Gen đang trả lời câu khác — không đè lượt đang chạy; giữ lỗi gốc (còn nút Tải lại) nhưng nói rõ vì sao chưa tải.
        setError(RELOAD_BUSY_TEXT);
      } else {
        // Tin của thẻ có thể giữ nguyên id sau khi tải (mở từ chuông) ⇒ thẻ KHÔNG gắn lại: phải tự xoá lỗi cũ.
        setError(null);
        setErrorRaw(null);
      }
    } catch (e) {
      setError(errorText(e));
      setErrorRaw(e);
    } finally {
      setBusy(null);
    }
  };

  const title = relabelKho(PROPOSAL_TITLE[p.type], khoLabel);
  const resultScreen = p.result?.screen ? GEN_SCREEN_BY_KEY[p.result.screen] : undefined;
  // v0.1.43 (F-24): nháp tin chỉ được LƯU, chưa gửi — nói rõ và dẫn thẳng tới đúng nháp ở Bàn làm việc để duyệt/gửi.
  const isDraft = p.type === 'draft_message';
  const draftId = isDraft && p.result?.type === 'draft' && p.result.id ? p.result.id : null;
  // Chỉ hứa "Mở để duyệt và gửi" khi nháp gửi được THẬT (API báo có nơi gửi) VÀ người bấm có quyền duyệt; còn lại chỉ mở nháp.
  // `sendable` thiếu (kết quả lưu trước v0.1.43) = không rõ → chỉ chip "Mở nháp" trung tính, KHÔNG khẳng định "chưa có nơi gửi".
  const canApprove = useCan('action.approve');
  const sendable = p.result?.sendable === true;
  const noTarget = p.result?.sendable === false;
  const draftNote = !draftId ? null : noTarget ? 'Nháp chưa có nơi gửi' : sendable && !canApprove ? 'Chờ Sếp duyệt rồi mới gửi' : null;
  const code = p.result?.code ? ` · ${p.result.code}` : '';
  // v0.1.50: thẻ ghi nhớ / ghi Kho luôn vẽ lỗi theo khuôn "câu thân thiện + Chi tiết kỹ thuật" (như thẻ gửi Facebook).
  const richError = social || memory || kho;
  const errView = proposalErrorView(errorRaw, khoLabel);
  // Lỗi của lần bấm trước chỉ có nghĩa khi thẻ còn chờ: thẻ đã đóng / đã xác nhận (tải lại, máy chủ cập nhật) thì không hiện nữa.
  const shownError = p.status === 'pending' ? error : null;
  // F-87: thẻ ghi Phiên của bản mới bị đóng vì Owner khác đã ghi / huỷ / ghi chưa chắc — máy chủ nêu lý do ở `labels.closed`.
  const closedNote = valueText(p.labels.closed).trim();
  // F-87: thẻ ghi Phiên của bản mới (job `gen_kho_release`, nhãn `release` = phiên bản) — mỗi bản ghi MỘT lần cho cả tổ chức.
  const releaseVersion = kho ? valueText(p.labels.release).trim() : '';
  const confirmLabel = social ? 'Xác nhận và gửi' : kho ? 'Xác nhận và ghi Kho' : memory ? 'Xác nhận ghi nhớ' : 'Xác nhận';
  return (
    <div className="gen-prop" data-status={p.status} role="group" aria-label={`Đề xuất: ${title}`}>
      <div className="gen-prop__head">
        <Icon name={PROPOSAL_ICON[p.type]} size={13} />
        <span className="gen-prop__title">Đề xuất · {title}</span>
        {p.requires_pin && p.status === 'pending' ? (
          <span className="gen-prop__pin" title="Thao tác nhạy cảm — hỏi mã PIN khi xác nhận">
            <Icon name="ph ph-lock-simple" size={11} /> Cần mã PIN
          </span>
        ) : null}
      </div>
      <p className="gen-prop__summary">{p.summary}</p>
      {editing && p.status === 'pending' ? <EditForm p={p} draft={draft} set={set} /> : <Summary p={p} />}
      {social && p.status === 'pending' ? (
        <>
          <p className="gen-prop__warn gen-prop__warn--send" role="note">
            <Icon name="ph ph-warning" size={13} /> {SOCIAL_WRITE_WARNING}
          </p>
          {p.labels.suspicious === '1' ? (
            <p className="gen-prop__warn gen-prop__warn--yellow" role="note" data-testid="gen-write-suspicious">
              <Icon name="ph ph-seal-warning" size={13} /> {SOCIAL_SUSPICIOUS_WARNING}
            </p>
          ) : null}
          {locked ? (
            <p className="gen-prop__warn" role="note" data-testid="gen-write-locked">
              <Icon name="ph ph-lock-simple" size={13} /> Gửi lên Facebook đang khoá — Sếp cần đọc cảnh báo và đồng ý (hoặc bật sandbox) trước.
            </p>
          ) : null}
        </>
      ) : null}
      {kho && p.status === 'pending' ? (
        <>
          <p className="gen-prop__warn gen-prop__warn--send" role="note" data-testid="gen-kho-warning">
            <Icon name="ph ph-warning" size={13} /> {relabelKho(KHO_WRITE_WARNING, khoLabel)}
          </p>
          {releaseVersion ? (
            <p className="gen-prop__note" role="note" data-testid="gen-kho-release-note">
              {khoReleaseNote(releaseVersion)}
            </p>
          ) : null}
          {noWrite ? (
            <p className="gen-prop__warn" role="note" data-testid="gen-kho-missing">
              <Icon name="ph ph-lock-simple" size={13} />
              <span>{hiddenByOwner ? khoHiddenText(p.type) : `${KHO_MISSING_TEXT}. ${KHO_MISSING_HINT}`}</span>
            </p>
          ) : null}
        </>
      ) : null}
      {shownError && richError ? (
        <>
          <ErrorWithDetail error={errorRaw} text={shownError} detail={proposalErrorDetail(errorRaw)} className="gen-prop__error write-error" />
          {social && writeErrorKind((errorRaw as { code?: string } | null)?.code) === 'locked' ? (
            <Button variant="secondary" className="btn-27" icon="ph ph-shield-warning" onClick={() => navigateTo(WRITE_RISK_PATH)}>
              Đọc cảnh báo & đồng ý
            </Button>
          ) : null}
          {errView?.action === 'open_hub' ? (
            <Button variant="secondary" className="btn-27" icon="ph ph-plugs-connected" onClick={() => navigateTo(HUB_CARD_PATH)}>
              Mở Kết nối › Gen-hub
            </Button>
          ) : null}
          {errView?.action === 'open_mcp' ? (
            <Button variant="secondary" className="btn-27" icon="ph ph-plugs" onClick={() => navigateTo(MCP_HUB_PATH)}>
              Mở MCP Hub
            </Button>
          ) : null}
          {errView?.action === 'reload' && p.status === 'pending' && conversationId ? (
            <Button variant="secondary" className="btn-27" icon="ph ph-arrows-clockwise" disabled={busy !== null} onClick={() => void reload()}>
              Tải lại hội thoại
            </Button>
          ) : null}
          {errView?.action === 'open_memory' ? (
            <Button variant="secondary" className="btn-27" icon="ph ph-arrow-square-out" onClick={() => navigateTo(MEMORY_CARD_PATH)}>
              Xem ở Cài đặt
            </Button>
          ) : null}
        </>
      ) : shownError ? (
        <p className="gen-prop__error" role="alert">
          {shownError}
        </p>
      ) : null}
      {p.status === 'pending' ? (
        <div className="gen-prop__actions">
          <Button
            variant="primary"
            className="btn-27"
            icon="ph ph-check"
            loading={busy === 'confirm'}
            disabled={busy !== null || locked || noWrite || (editing && !valid(p, draft))}
            onClick={() => void confirm()}
          >
            {confirmLabel}
          </Button>
          {locked ? (
            <Button variant="secondary" className="btn-27" icon="ph ph-shield-warning" disabled={busy !== null} onClick={() => navigateTo(WRITE_RISK_PATH)}>
              Đọc cảnh báo & đồng ý
            </Button>
          ) : null}
          {noWrite ? (
            hiddenByOwner ? (
              <Button variant="secondary" className="btn-27" icon="ph ph-plugs" disabled={busy !== null} onClick={() => navigateTo(MCP_HUB_PATH)}>
                Mở MCP Hub
              </Button>
            ) : (
              <Button variant="secondary" className="btn-27" icon="ph ph-plugs-connected" disabled={busy !== null} onClick={() => navigateTo(HUB_CARD_PATH)}>
                Mở thẻ Gen-hub
              </Button>
            )
          ) : null}
          <Button
            variant="secondary"
            className="btn-27"
            icon={editing ? 'ph ph-arrow-counter-clockwise' : 'ph ph-pencil-simple'}
            disabled={busy !== null}
            onClick={() => {
              if (editing) setDraft(initialDraft(p));
              setEditing((v) => !v);
            }}
          >
            {editing ? 'Bỏ sửa' : 'Sửa'}
          </Button>
          <Button variant="ghost" className="btn-27" loading={busy === 'cancel'} disabled={busy !== null} onClick={() => void cancel()}>
            Huỷ
          </Button>
        </div>
      ) : p.status === 'confirmed' && social && p.result?.type === 'social_write' && p.result.id ? (
        <WriteProgress jobId={p.result.id} initial={p.result.status} />
      ) : p.status === 'confirmed' && memory ? (
        <div className="gen-prop__done" data-testid="gen-memory-done">
          <Icon name="ph-fill ph-check-circle" size={13} /> Đã ghi nhớ
          <Button variant="secondary" className="btn-27" icon="ph ph-arrow-square-out" onClick={() => navigateTo(MEMORY_CARD_PATH)}>
            Xem ở Cài đặt
          </Button>
        </div>
      ) : p.status === 'confirmed' && kho ? (
        <div className="gen-prop__done" data-testid="gen-kho-done">
          <Icon name="ph-fill ph-check-circle" size={13} /> Đã ghi vào Kho: {valueText(p.result?.code) || 'bản ghi mới'}
        </div>
      ) : p.status === 'confirmed' ? (
        <div className="gen-prop__done">
          {isDraft ? (
            <>
              <Icon name="ph ph-floppy-disk" size={13} /> Đã lưu nháp — chưa gửi{code}
            </>
          ) : (
            <>
              <Icon name="ph-fill ph-check-circle" size={13} /> Đã xác nhận{code}
            </>
          )}
          {draftId && sendable && canApprove ? (
            <Button variant="primary" className="btn-27" icon="ph ph-arrow-square-out" onClick={() => navigateTo(`/workbench?id=${encodeURIComponent(draftId)}`)}>
              Mở để duyệt và gửi
            </Button>
          ) : draftId ? (
            <>
              {draftNote ? <span className="gen-prop__note">{draftNote}</span> : null}
              <button type="button" className="gen-chip gen-chip--btn" onClick={() => navigateTo(`/workbench?id=${encodeURIComponent(draftId)}`)}>
                <Icon name="ph ph-arrow-square-out" size={11} /> {noTarget ? 'Mở nháp ở Bàn làm việc' : 'Mở nháp'}
              </button>
            </>
          ) : resultScreen ? (
            <button type="button" className="gen-chip gen-chip--btn" onClick={() => navigateTo(resultScreen.path)}>
              <Icon name="ph ph-arrow-square-out" size={11} /> Mở {resultScreen.title}
            </button>
          ) : null}
        </div>
      ) : (
        <div className="gen-prop__done gen-prop__done--off" data-testid="gen-prop-cancelled">
          <Icon name="ph ph-x-circle" size={13} />{' '}
          {closedNote
            ? `Thẻ đã đóng — ${closedNote}`
            : kho
              ? khoUncertain(p as KhoProposal)
                ? KHO_CANCELLED_UNCERTAIN_TEXT
                : 'Đã huỷ — không ghi gì vào Kho'
              : memory
                ? 'Đã huỷ — không ghi nhớ'
                : 'Đã huỷ — không làm gì'}
        </div>
      )}
    </div>
  );
}
