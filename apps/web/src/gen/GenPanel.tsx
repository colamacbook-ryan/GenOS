import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { GEN_SCREEN_BY_KEY, GEN_TARGET_BY_ID, KHO_LABEL, splitTargetId, type GenStep, type UiAction } from '@gen-harness/contracts';
import { Button, Icon, IconButton } from '@gen-harness/ui';
import { api } from '../lib/api';
import { useCan } from '../lib/permissions';
import { useMe } from '../lib/queries';
import { qkMcp } from '../screens/mcp/queries';
import { BriefingHubSections } from './BriefingHubSections';
import { hubCardsAt } from './briefingModel';
import { COACH_URGENT_PROMPT, hasUrgent } from './coachModel';
import { useCoachAudience, useCoachToday } from './coachQueries';
import { CoachTodayCard } from './CoachTodayCard';
import { closeSpotlight, executeUiAction } from './director';
import { restoreIfNeeded, retryRestore, sendFeedback, sendQuestion } from './genClient';
import { GenHistory } from './GenHistory';
import { ModelPicker } from './ModelPicker';
import { ProposalCard } from './ProposalCard';
import { toolLabel, toolLabelHasKho } from './toolLabels';
import { useGenStore, type GenChatMessage } from './genStore';

const EXAMPLES = ['Hôm nay có gì cần tôi xử lý?', 'Khách nào hỏi giá hôm nay?', 'Nhắc tôi gọi lại khách lúc 3 giờ chiều'];

function targetLabel(id: string): string {
  return GEN_TARGET_BY_ID[splitTargetId(id).base]?.label ?? id;
}

function actionLabel(a: UiAction): string {
  if (a.type === 'navigate') return `Đã mở ${GEN_SCREEN_BY_KEY[a.screen]?.title ?? a.screen}`;
  if (a.type === 'highlight') return `Đang chỉ: ${targetLabel(a.target)}`;
  return `Dẫn ${a.steps.length} bước`;
}

/** Hiện câu Gen chào theo cách xưng hô đã chọn ở trình thiết lập ("Sếp"). */
function useAddressing(): string {
  const me = useMe();
  return me.data?.addressing?.bot_calls_me || 'Sếp';
}

/**
 * Chip "Đã tra …": nhãn tra Kho theo tên Kho Sếp tự đặt (F117). Tên lấy từ truy vấn thật `GET /hub/link` (cùng khoá với thẻ
 * Gen-hub nên React Query gộp một lần gọi) — không đọc cache của truy vấn đã tắt: mở lại hội thoại cũ thì cache còn trống và
 * chip nói tên mặc định. Chỉ hỏi khi nhãn của bước thật sự có tên Kho và người xem đọc được cài đặt; lỗi ⇒ tên mặc định.
 */
function ToolChip({ name }: { name: string }) {
  const canReadSystem = useCan('system.read');
  const hub = useQuery({
    queryKey: qkMcp.hubLink,
    queryFn: ({ signal }) => api.hub.link.get(signal),
    enabled: canReadSystem && toolLabelHasKho(name),
    retry: false,
  });
  return (
    <span className="gen-chip">
      <Icon name="ph ph-magnifying-glass" size={11} /> Đã tra {toolLabel(name, hub.data?.kho_label)}
    </span>
  );
}

function Step({ step, turnId }: { step: GenStep; turnId?: string }) {
  if (step.kind === 'say') return <p className="gen-msg__text">{step.text}</p>;
  // v0.1.55 (G3): thông báo của hệ thống (vd hạ về Tự động) — một dòng nhỏ, luôn là chuỗi.
  if (step.kind === 'notice')
    return (
      <p className="gen-msg__text" role="note" style={{ display: 'flex', alignItems: 'flex-start', gap: 5, fontSize: 11.5, color: 'var(--color-neutral-400)' }}>
        <Icon name="ph ph-info" size={12} style={{ marginTop: 2 }} />
        <span>{typeof step.text === 'string' ? step.text : ''}</span>
      </p>
    );
  if (step.kind === 'tool') return <ToolChip name={step.name} />;
  if (step.kind === 'ui')
    return (
      <button type="button" className="gen-chip gen-chip--btn" onClick={() => void executeUiAction(step.action, turnId)} title="Làm lại">
        <Icon name={step.action.type === 'navigate' ? 'ph ph-arrow-square-out' : 'ph ph-cursor-click'} size={11} /> {actionLabel(step.action)}
      </button>
    );
  if (step.kind === 'proposal') return <ProposalCard proposal={step.proposal} />;
  if (step.kind === 'suggest')
    return (
      <div className="gen-suggest">
        {step.items.map((it) => (
          <button key={it.label} type="button" className="gh-btn gh-btn--secondary btn-24" onClick={() => void executeUiAction(it.action, turnId)}>
            {it.label}
          </button>
        ))}
      </div>
    );
  return null;
}

/** v0.1.41 (F-86): Hữu ích / Không hữu ích cho một câu trả lời đã xong (bấm lại nút đang chọn ⇒ bỏ đánh giá). */
function Feedback({ m }: { m: GenChatMessage }) {
  return (
    <div className="gen-rate" role="group" aria-label="Đánh giá câu trả lời">
      <button type="button" className="gen-rate__btn" aria-pressed={m.feedback === 'helpful'} onClick={() => void sendFeedback(m, 'helpful')}>
        <Icon name={m.feedback === 'helpful' ? 'ph-fill ph-thumbs-up' : 'ph ph-thumbs-up'} size={12} /> Hữu ích
      </button>
      <button type="button" className="gen-rate__btn" aria-pressed={m.feedback === 'not_helpful'} onClick={() => void sendFeedback(m, 'not_helpful')}>
        <Icon name={m.feedback === 'not_helpful' ? 'ph-fill ph-thumbs-down' : 'ph ph-thumbs-down'} size={12} /> Không hữu ích
      </button>
    </div>
  );
}

function Message({ m, userId }: { m: GenChatMessage; userId?: string }) {
  if (m.role === 'user') return <div className="gen-msg gen-msg--user">{m.text}</div>;
  const steps = m.steps.filter(Boolean);
  const rateable = m.status === 'done' && !!m.turnId;
  // v0.1.49 (F-8, QD-16): thẻ Lịch hôm nay / Mail cần trả lời / Việc Google đang mở (Gen-hub, chỉ đọc) chèn ngay sau
  // "Sự cố cần Sếp" — trước Facebook/Kho và các lời nhắc + nút.
  const hubAt = m.kind === 'briefing' ? hubCardsAt(steps, m.hubAt) : steps.length;
  return (
    <div className={m.kind === 'briefing' ? 'gen-msg gen-msg--gen gen-msg--briefing' : 'gen-msg gen-msg--gen'} aria-busy={m.status === 'running' || undefined}>
      {m.kind === 'briefing' ? (
        <span className="gen-badge">
          <Icon name="ph ph-newspaper" size={11} /> Bản tin
        </span>
      ) : null}
      {steps.slice(0, hubAt).map((s, i) => (
        <Step key={i} step={s} turnId={m.turnId} />
      ))}
      {m.kind === 'briefing' ? <BriefingHubSections sections={m.sections} /> : null}
      {steps.slice(hubAt).map((s, i) => (
        <Step key={hubAt + i} step={s} turnId={m.turnId} />
      ))}
      {typeof m.detail === 'string' && m.detail ? (
        <details className="tech-detail">
          <summary>Chi tiết kỹ thuật</summary>
          <code>{m.detail}</code>
        </details>
      ) : null}
      {m.retryConversation && userId ? (
        <Button variant="secondary" size="sm" icon="ph ph-arrow-clockwise" onClick={() => void retryRestore(m.retryConversation as string, userId)}>
          Thử lại
        </Button>
      ) : null}
      {m.status === 'running' ? (
        <span className="gen-thinking" role="status">
          <span />
          <span />
          <span /> Gen đang nghĩ…
        </span>
      ) : null}
      {rateable ? <Feedback m={m} /> : null}
    </div>
  );
}

/**
 * Khung chat Gen bên phải (docs/design/gen-v1.md §3.1). Bật/tắt bằng nút ở Header; trên điện thoại là tấm phủ toàn
 * màn hình. Câu trả lời, "đang nghĩ", chip hành động (bấm để chỉ lại), gợi ý; v2 (A4): thẻ đề xuất thao tác có
 * Xác nhận / Sửa / Huỷ (ProposalCard) — Gen không tự ghi gì.
 */
export function GenPanel({ userId }: { userId: string }) {
  const messages = useGenStore((s) => s.messages);
  const busy = useGenStore((s) => s.busy);
  const restoring = useGenStore((s) => s.restoring);
  // Đang tải nội dung hội thoại (tải lại trang, mở từ "Hội thoại cũ" hay bản tin từ chuông) ⇒ khoá gửi + "Đang mở…".
  const loadingConversation = useGenStore((s) => s.loadingConversation);
  const opening = restoring || loadingConversation;
  const setOpen = useGenStore((s) => s.setOpen);
  const reset = useGenStore((s) => s.reset);
  const addr = useAddressing();
  // v0.1.57 (Nợ #30): câu chào nhắc "ghi vào Kho dữ liệu" dùng Tên Kho Owner tự đặt — lấy từ `GET /hub/link` (cùng cache
  // với thẻ Gen-hub / thẻ đề xuất), chỉ hỏi khi khung còn trống (lúc có câu chào) và người xem đọc được cài đặt; lỗi ⇒ tên mặc định.
  const canReadSystem = useCan('system.read');
  const hubLink = useQuery({
    queryKey: qkMcp.hubLink,
    queryFn: ({ signal }) => api.hub.link.get(signal),
    enabled: canReadSystem && messages.length === 0,
    retry: false,
  });
  const khoName = hubLink.data?.kho_label?.trim() || KHO_LABEL;
  // v0.1.54 (Gen hướng dẫn): có việc khẩn (P0/P1) ⇒ thêm câu mẫu "Hôm nay em cần làm gì?" (dùng chung cache với thẻ).
  const coachAudience = useCoachAudience();
  const urgent = hasUrgent(useCoachToday(coachAudience).data);
  const examples = urgent ? [COACH_URGENT_PROMPT, ...EXAMPLES] : EXAMPLES;
  const [text, setText] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const historyBtnRef = useRef<HTMLButtonElement>(null);

  // v0.1.41 (F-8a): tải lại trang ⇒ mở lại hội thoại đã lưu (một lần; đang trả lời thì không đụng).
  useEffect(() => {
    if (!useGenStore.getState().busy) void restoreIfNeeded(userId);
  }, [userId]);

  const closeHistory = () => {
    setHistoryOpen(false);
    historyBtnRef.current?.focus();
  };

  useEffect(() => {
    const el = listRef.current;
    // v0.1.54: chưa có tin nào ⇒ giữ nguyên đầu danh sách (thẻ "Hôm nay của Sếp" nằm ở đó); có tin ⇒ cuộn xuống tin mới nhất.
    if (el && messages.length > 0) el.scrollTop = el.scrollHeight;
  }, [messages]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // v0.1.54: "Hỏi Gen thêm" của thẻ chỉ ĐIỀN SẴN ô nhập (không gửi) — lấy câu rồi hạ về null.
  const draft = useGenStore((s) => s.composerDraft);
  useEffect(() => {
    if (draft === null) return;
    setText(draft);
    useGenStore.getState().setComposerDraft(null);
    const el = inputRef.current;
    el?.focus();
    el?.setSelectionRange?.(draft.length, draft.length);
  }, [draft]);

  const submit = (q = text) => {
    const st = useGenStore.getState();
    if (!q.trim() || busy || st.restoring || st.loadingConversation) return;
    setText('');
    // v0.1.28 (UX V12): câu hỏi mới → dừng lượt dẫn đường cũ (ô khoanh sáng không còn đè màn hình).
    if (useGenStore.getState().spotlight) closeSpotlight();
    void sendQuestion(q, userId);
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
    // Esc: đang làm sáng thì chỉ tắt lớp phủ (Spotlight tự xử lý), không đóng khung chat.
    if (e.key === 'Escape' && !useGenStore.getState().spotlight) setOpen(userId, false);
  };

  return (
    <aside className="gen-panel" aria-label="Gen — trợ lý quản trị">
      <div className="gen-panel__head">
        <span className="gen-panel__logo" aria-hidden>
          <Icon name="ph-fill ph-sparkle" size={14} />
        </span>
        <div className="gen-panel__titles">
          <div className="gen-panel__title">Gen</div>
          <div className="gen-panel__sub">Trợ lý quản trị · dẫn đường &amp; đề xuất có xác nhận</div>
        </div>
        <IconButton
          ref={historyBtnRef}
          icon="ph ph-clock-counter-clockwise"
          label="Hội thoại cũ"
          aria-expanded={historyOpen}
          onClick={() => setHistoryOpen((v) => !v)}
        />
        <IconButton icon="ph ph-plus" label="Hội thoại mới" onClick={reset} disabled={busy || opening} />
        <IconButton icon="ph ph-x" label="Đóng khung Gen" onClick={() => setOpen(userId, false)} />
      </div>
      {historyOpen ? <GenHistory userId={userId} onClose={closeHistory} /> : null}
      <div className="gen-panel__list" ref={listRef} aria-live="polite">
        {/* v0.1.54: thẻ "Hôm nay của Sếp" (Gen hướng dẫn) ở ĐẦU khung — chỉ Owner & Gen bật; không tạo hội thoại. */}
        <CoachTodayCard />
        {messages.length === 0 && opening ? (
          <p className="gen-empty" role="status">
            {restoring ? 'Đang mở lại hội thoại…' : 'Đang mở hội thoại…'}
          </p>
        ) : messages.length === 0 ? (
          <div className="gen-empty">
            <p>
              Chào {addr}, em là Gen. {addr} hỏi về tình hình hôm nay, nhờ em chỉ chỗ bấm, hoặc nhờ em soạn nháp tin, đặt
              nhắc việc, giao người, ghi nhớ một quy ước, ghi vào {khoName} — em chỉ đề xuất, {addr} xác nhận thì em mới làm.
            </p>
            <div className="gen-suggest">
              {examples.map((q) => (
                <button key={q} type="button" className="gh-btn gh-btn--secondary btn-24" onClick={() => submit(q)}>
                  {q}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((m) => <Message key={m.id} m={m} userId={userId} />)
        )}
      </div>
      <form
        className="gen-panel__input"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <textarea
          ref={inputRef}
          className="gh-input"
          rows={2}
          value={text}
          maxLength={4000}
          placeholder={`${addr} hỏi Gen…`}
          aria-label="Câu hỏi cho Gen"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
        />
        <IconButton icon="ph ph-paper-plane-right" label="Gửi" variant="primary" type="submit" disabled={busy || opening || !text.trim()} />
      </form>
      {/* v0.1.55 (G3): chọn model / mức suy nghĩ cho câu hỏi kế tiếp (nhớ theo hội thoại; hội thoại mới = Tự động). */}
      <ModelPicker />
    </aside>
  );
}
