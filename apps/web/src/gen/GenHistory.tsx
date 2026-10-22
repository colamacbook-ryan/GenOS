import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button, Icon } from '@gen-harness/ui';
import { api } from '../lib/api';
import { errorDetail, errorText } from '../lib/errorText';
import { fmtAgo } from '../lib/format';
import { toast } from '../lib/toast';
import { loadConversation } from './genClient';
import { useGenStore } from './genStore';

/** Khoá cache danh sách "Hội thoại cũ" (luôn tải lại khi mở danh sách). */
const GEN_CONVERSATIONS_KEY = ['gen', 'conversations'] as const;

/**
 * v0.1.41 (F-8a): danh sách "Hội thoại cũ" trong khung Gen — tiêu đề + lúc nói gần nhất, nhãn "Bản tin" cho Bản tin
 * Gen. Bấm một dòng ⇒ mở lại hội thoại đó (nội dung lấy từ server). Esc đóng, tiêu điểm trả về nút "Hội thoại cũ".
 */
export function GenHistory({ userId, onClose }: { userId: string; onClose: () => void }) {
  // Không tự thử lại: lỗi hiện ngay kèm nút "Thử lại" (khung nhỏ, Sếp đang chờ).
  const q = useQuery({
    queryKey: GEN_CONVERSATIONS_KEY,
    queryFn: ({ signal }) => api.gen.conversations(signal),
    refetchOnMount: 'always',
    staleTime: 0,
    retry: false,
  });
  const current = useGenStore((s) => s.conversationId);
  const busy = useGenStore((s) => s.busy);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    boxRef.current?.focus();
  }, []);

  const open = async (id: string) => {
    onClose();
    try {
      const r = await loadConversation(id, userId);
      if (r === 'missing') toast('Hội thoại này không còn — có thể đã bị xoá hoặc quá hạn lưu.', 'warn');
      else if (r === 'busy') toast('Gen đang trả lời — đợi xong rồi mở hội thoại cũ nhé.', 'warn');
    } catch (e) {
      const d = errorDetail(e);
      toast(`${errorText(e) || 'Không mở được hội thoại — thử lại sau.'}${d ? ` (Chi tiết kỹ thuật: ${d})` : ''}`, 'bad');
    }
  };

  const now = Date.now();
  const historyDetail = q.isError ? errorDetail(q.error) : null;
  return (
    <div
      className="gen-history"
      role="dialog"
      aria-label="Hội thoại cũ"
      tabIndex={-1}
      ref={boxRef}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.preventDefault();
          e.stopPropagation();
          onClose();
        }
      }}
    >
      <div className="gen-history__head">
        <Icon name="ph ph-clock-counter-clockwise" size={13} /> Hội thoại cũ
      </div>
      {q.isPending ? (
        <p className="gen-history__state" role="status">
          Đang tải danh sách…
        </p>
      ) : q.isError ? (
        <div className="gen-history__state gen-history__state--error" role="alert">
          <p>Chưa tải được danh sách hội thoại — Sếp thử lại sau ít phút.</p>
          {historyDetail ? (
            <details className="tech-detail">
              <summary>Chi tiết kỹ thuật</summary>
              <code>{historyDetail}</code>
            </details>
          ) : null}
          <Button variant="secondary" size="sm" icon="ph ph-arrow-clockwise" onClick={() => void q.refetch()} loading={q.isFetching}>
            Thử lại
          </Button>
        </div>
      ) : q.data.length === 0 ? (
        <p className="gen-history__state">Chưa có hội thoại nào</p>
      ) : (
        <>
          {busy ? (
            <p className="gen-history__state" role="status" data-testid="gen-history-busy">
              Gen đang trả lời — đợi xong rồi mở hội thoại cũ nhé.
            </p>
          ) : null}
          <ul className="gen-history__list">
            {q.data.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  className="gen-history__item"
                  aria-current={c.id === current ? 'true' : undefined}
                  disabled={busy}
                  onClick={() => void open(c.id)}
                >
                  <span className="gen-history__title">
                    {c.kind === 'briefing' ? <span className="gen-badge">Bản tin</span> : null}
                    <span className="gen-history__name">{c.title || 'Hội thoại không tên'}</span>
                  </span>
                  <span className="gen-history__time">{fmtAgo(c.last_at, now)}</span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
