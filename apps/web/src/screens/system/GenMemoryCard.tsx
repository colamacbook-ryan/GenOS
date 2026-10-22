import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { GenMemoryPatchBody } from '@gen-harness/contracts';
import { Button, Dialog, EmptyState, Icon } from '@gen-harness/ui';
import { GEN_MEMORY_KEY, MEMORY_DESC, MEMORY_EMPTY, MEMORY_TITLE, charCount, countText, memoryErrorText, memoryItems, memoryLimits, type MemoryItemView } from '../../gen/genMemoryModel';
import { DefaultControls } from '../../defaults/ResetButton';
import { api } from '../../lib/api';
import { errorDetail } from '../../lib/errorText';
import { useOrgTimezone } from '../../lib/permissions';
import { useMe } from '../../lib/queries';
import { CardError, InlineError, Panel, SkeletonLines } from '../common';

/**
 * v0.1.50 (F-81, QD-18) — "Gen nhớ" ở Cài đặt › Bộ não AI: quy ước, sở thích Sếp đã xác nhận; Gen đọc khi trả lời Sếp và khi
 * soạn Bản tin. CHỈ Owner thấy thẻ (vai trò khác: không vẽ, không gọi `/gen/memory`). Ghi chú mới chỉ vào qua thẻ đề xuất
 * "Ghi nhớ" + Xác nhận; ở đây Sếp xem, Sửa tại chỗ (nguồn thành "Sếp sửa") và Xoá (có hộp xác nhận). Không cần mã PIN.
 */
export function GenMemoryCard() {
  const isOwner = useMe().data?.role?.code === 'owner';
  if (!isOwner) return null;
  return <GenMemoryPanel />;
}

function GenMemoryPanel() {
  const q = useQuery({ queryKey: GEN_MEMORY_KEY, queryFn: ({ signal }) => api.gen.memory.list(signal), retry: false });
  const tz = useOrgTimezone();
  const limits = memoryLimits(q.data);
  const items = memoryItems(q.data, tz);
  return (
    <div id="gen-memory" className="gen-mem-wrap">
      <Panel
        title={MEMORY_TITLE}
        genTarget="system.brain.memory"
        kicker={MEMORY_DESC}
        label={MEMORY_TITLE}
        bodyClass="gen-mem"
        aside={
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            {q.data ? <span className="mono gen-mem__count" data-testid="gen-memory-count">{countText(items.length, limits.limit)}</span> : null}
            {/* v0.1.55: Gen — vai trò dùng và số ngày giữ hội thoại (mặc định chỉ Owner · 90 ngày); công tắc bật/tắt Gen giữ nguyên. */}
            <DefaultControls itemKey="gen" />
          </span>
        }
      >
        {q.isPending ? (
          <SkeletonLines rows={3} padding="10px 16px" />
        ) : q.isError ? (
          <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        ) : items.length === 0 ? (
          <EmptyState icon="ph ph-brain" title={MEMORY_EMPTY} />
        ) : (
          <ul className="gen-mem__list">
            {items.map((n) => (
              <MemoryRow key={n.id} note={n} maxLen={limits.maxLen} reasonMax={limits.reasonMax} />
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}

function MemoryRow({ note, maxLen, reasonMax }: { note: MemoryItemView; maxLen: number; reasonMax: number }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(note.text);
  const [reason, setReason] = useState(note.reason ?? '');
  const [confirmDelete, setConfirmDelete] = useState(false);

  const refresh = () => void qc.invalidateQueries({ queryKey: GEN_MEMORY_KEY });
  const save = useMutation({
    mutationFn: (body: GenMemoryPatchBody) => api.gen.memory.update(note.id, body),
    onSuccess: () => {
      setEditing(false);
      refresh();
    },
    // 404 (đã bị xoá ở nơi khác) / 409 (trùng): tải lại danh sách cho khớp máy chủ.
    onError: refresh,
  });
  const remove = useMutation({
    mutationFn: () => api.gen.memory.remove(note.id),
    onSuccess: () => {
      setConfirmDelete(false);
      refresh();
    },
    onError: refresh,
  });

  const startEdit = () => {
    setText(note.text);
    setReason(note.reason ?? '');
    save.reset();
    setEditing(true);
  };
  const trimmed = text.trim();
  const body: GenMemoryPatchBody = {};
  if (trimmed !== note.text) body.text = trimmed;
  if (reason.trim() !== (note.reason ?? '')) body.reason = reason.trim();
  const dirty = Object.keys(body).length > 0;
  const valid = trimmed !== '' && [...text].length <= maxLen && [...reason].length <= reasonMax;

  return (
    <li className="gen-mem__row" data-testid="gen-memory-row">
      {editing ? (
        <form
          className="gen-mem__edit"
          onSubmit={(e) => {
            e.preventDefault();
            if (valid && dirty) save.mutate(body);
          }}
        >
          <div className="gh-field">
            <label className="gh-field__label" htmlFor={`gen-mem-text-${note.id}`}>
              Ghi nhớ
            </label>
            <textarea id={`gen-mem-text-${note.id}`} className="gh-input" rows={3} maxLength={maxLen} value={text} onChange={(e) => setText(e.target.value)} />
            <span className="gen-mem__chars" data-testid="gen-memory-chars-text">
              {charCount(text, maxLen)}
            </span>
          </div>
          <div className="gh-field">
            <label className="gh-field__label" htmlFor={`gen-mem-reason-${note.id}`}>
              Lý do (không bắt buộc)
            </label>
            <textarea id={`gen-mem-reason-${note.id}`} className="gh-input" rows={2} maxLength={reasonMax} value={reason} onChange={(e) => setReason(e.target.value)} />
            <span className="gen-mem__chars" data-testid="gen-memory-chars-reason">
              {charCount(reason, reasonMax)}
            </span>
          </div>
          {save.isError ? <InlineError detail={errorDetail(save.error)}>{memoryErrorText(save.error)}</InlineError> : null}
          <div className="gen-mem__actions">
            <Button variant="primary" type="submit" className="btn-27" icon="ph ph-floppy-disk" loading={save.isPending} disabled={!valid || !dirty}>
              Lưu
            </Button>
            <Button variant="ghost" type="button" className="btn-27" disabled={save.isPending} onClick={() => setEditing(false)}>
              Huỷ
            </Button>
          </div>
        </form>
      ) : (
        <>
          <div className="gen-mem__body">
            <p className="gen-mem__text">{note.text}</p>
            {note.reason ? <p className="gen-mem__reason">{note.reason}</p> : null}
            <p className="gen-mem__meta">
              {note.source} · {note.date}
            </p>
          </div>
          <div className="gen-mem__actions">
            <Button variant="secondary" className="btn-27" icon="ph ph-pencil-simple" onClick={startEdit}>
              Sửa
            </Button>
            <Button variant="ghost" className="btn-27" icon="ph ph-trash" onClick={() => setConfirmDelete(true)}>
              Xoá
            </Button>
          </div>
        </>
      )}
      <Dialog
        open={confirmDelete}
        onClose={() => {
          if (!remove.isPending) setConfirmDelete(false);
        }}
        title="Xoá ghi chú này?"
        kicker="Gen sẽ không còn nhớ điều này"
        actions={
          <>
            <Button variant="secondary" disabled={remove.isPending} onClick={() => setConfirmDelete(false)}>
              Huỷ
            </Button>
            <Button variant="primary" icon="ph ph-trash" loading={remove.isPending} onClick={() => remove.mutate()}>
              Xoá ghi chú
            </Button>
          </>
        }
      >
        <p className="gen-mem__confirm-text">
          <Icon name="ph ph-brain" size={13} /> {note.text}
        </p>
        {remove.isError ? <InlineError detail={errorDetail(remove.error)}>{memoryErrorText(remove.error)}</InlineError> : null}
      </Dialog>
    </li>
  );
}
