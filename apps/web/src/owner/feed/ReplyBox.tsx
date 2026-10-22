/**
 * v0.1.59 (G4) — ô "Trả lời" ở thanh hành động của một tin. Gen chỉ ĐỀ XUẤT: bấm "Trả lời" CHỈ TẠO BẢN NHÁP (`POST /inbox/{id}/act {text}` qua
 * `useReplyToItem`), không gửi gì. Nháp vừa tạo hiện ngay `<DraftDecide>` ("Duyệt và gửi" — cần mã PIN; client tự hỏi khi API trả 423). KHÔNG có
 * đường gửi thẳng. Các nút còn lại của thanh (Tạo nhắc, Tắt tiếng) do khung chi tiết truyền vào qua `children`.
 * Lỗi = câu thân thiện + "Chi tiết kỹ thuật"; chữ Sếp đã gõ được giữ lại để thử lại.
 */
import { useState, type ReactNode } from 'react';
import { Icon } from '@gen-harness/ui';
import { errorDetail, errorText } from '../../lib/errorText';
import { InlineError } from '../../screens/common';
import { useReplyToItem } from '../actions/useOwnerActions';
import './feed.css';

export interface ReplyBoxProps {
  itemId: string;
  /** Tên người nhận (để gợi ý trong ô nhập). */
  personName?: string;
  /** Nháp vừa tạo: `draftId` null khi máy chủ không trả mã (hiếm) — khung chi tiết vẫn báo đã tạo nháp. */
  onDraft: (draftId: string | null) => void;
  /** Nút khác của thanh hành động, xếp cạnh "Trả lời". */
  children?: ReactNode;
}

export function ReplyBox({ itemId, personName, onDraft, children }: ReplyBoxProps) {
  const [text, setText] = useState('');
  const reply = useReplyToItem(itemId);
  const clean = text.trim();
  const submit = () => {
    if (!clean || reply.isPending) return;
    reply.mutate(clean, {
      onSuccess: (r) => {
        setText('');
        onDraft(r.draft_id);
      },
    });
  };
  const label = personName ? `Soạn trả lời cho ${personName}…` : 'Soạn trả lời…';
  return (
    <div className="feed-reply">
      <textarea
        className="feed-reply__input"
        data-testid="feed-reply-input"
        aria-label="Nội dung trả lời"
        placeholder={label}
        rows={2}
        value={text}
        maxLength={4000}
        onChange={(e) => setText(e.target.value)}
        disabled={reply.isPending}
      />
      {reply.isError ? (
        <div className="feed-reply__error" data-testid="feed-reply-error">
          <InlineError detail={errorDetail(reply.error)}>{errorText(reply.error)}</InlineError>
        </div>
      ) : null}
      <div className="feed-bar__row">
        <button
          type="button"
          className="kit-btn kit-btn--primary"
          data-testid="feed-reply"
          disabled={!clean || reply.isPending}
          aria-busy={reply.isPending || undefined}
          onClick={submit}
        >
          {reply.isPending ? 'Đang tạo nháp…' : 'Trả lời'}
        </button>
        {children}
      </div>
      <p className="feed-bar__hint">
        <Icon name="ph ph-lock-simple" size={14} />
        Gen tạo nháp trước, Sếp duyệt bằng mã PIN rồi mới gửi.
      </p>
    </div>
  );
}
