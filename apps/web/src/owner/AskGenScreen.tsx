/**
 * Hỏi Gen trên Mặt tiền. NHÚNG nguyên `GenPanel` của Console (không đổi props, không chép logic). Hai chỗ nhúng:
 *
 * 1. NGĂN KÉO `AskGenDrawer` (v0.1.59, G2) — đường chính: nút "Hỏi Gen" ở đầu mọi màn (owner-ask-gen) mở ngăn bên PHẢI rộng
 *    480px (≥ 760px) hoặc toàn màn hình (< 760px); Esc đóng. Mở/đóng bằng `openAskGen(prefill?)` / `closeAskGen()` — HỢP ĐỒNG
 *    cho FirstRunCard ("Xem Gen làm được gì") và G4 ("Nhờ Gen soạn tin" mở ngăn với câu điền sẵn). `prefill` chỉ ĐIỀN SẴN ô nhập của
 *    Gen (`genStore.composerDraft`) — không bao giờ tự gửi.
 * 2. TRANG `/owner/gen` (`AskGenScreen`, v0.1.55) — giữ cho link cũ: `?gen=<mã hội thoại>` (Bản tin từ chuông, qua
 *    useOpenGenFromUrl) vẫn đáp ở đây. Máy tính: bên trái vài câu gợi ý (bấm chỉ điền sẵn), bên phải khung chat; điện thoại: Gen
 *    chiếm cả màn.
 *
 * Gen tắt ⇒ giải thích + link Bộ não AI. Gen chỉ đề xuất; thao tác ghi vẫn cần Xác nhận (+ mã PIN) trên thẻ đề xuất.
 */
import { useEffect, useRef } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { create } from 'zustand';
import { DetailSheet, EmptyState, Icon, Skeleton } from '@gen-harness/ui';
import { GenPanel } from '../gen/GenPanel';
import { useGenStore } from '../gen/genStore';
import { useMe } from '../lib/queries';
import { CardError, Panel } from '../screens/common';
import { ASK_GEN_PROMPTS, OWNER_PATHS } from './ownerModel';

// ── ngăn kéo: trạng thái + hợp đồng mở/đóng ─────────────────────────────────────────────────────────────────────────

interface AskGenState {
  open: boolean;
}
/** Trạng thái ngăn Hỏi Gen (không lưu máy — tải lại trang thì ngăn đóng). */
// eslint-disable-next-line react-refresh/only-export-components
export const useAskGenStore = create<AskGenState>(() => ({ open: false }));

/** Mở ngăn Hỏi Gen; `prefill` (nếu có) điền sẵn ô nhập của Gen — KHÔNG gửi. */
// eslint-disable-next-line react-refresh/only-export-components
export function openAskGen(prefill?: string): void {
  if (typeof prefill === 'string' && prefill !== '') useGenStore.getState().setComposerDraft(prefill);
  useAskGenStore.setState({ open: true });
}

// eslint-disable-next-line react-refresh/only-export-components
export function closeAskGen(): void {
  useAskGenStore.setState({ open: false });
}

/** Đang ở trang Hỏi Gen toàn trang: nút "Hỏi Gen" chỉ đưa con trỏ vào ô nhập (không mở ngăn thứ hai). */
// eslint-disable-next-line react-refresh/only-export-components
export function focusGenInput(): void {
  document.querySelector<HTMLTextAreaElement>('textarea[aria-label="Câu hỏi cho Gen"]')?.focus();
}

// Không import parts.tsx: FirstRunCard (parts) gọi openAskGen ở đây — tránh vòng phụ thuộc.
function GenOffNotice({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <div className="owner-empty" data-testid="owner-empty">
      <EmptyState
        icon="ph ph-sparkle"
        title="Gen đang tắt"
        description="Sếp bật Gen lại ở Bộ não AI để hỏi em ngay tại đây."
        actions={
          <Link to="/system?tab=brain" className="kit-btn kit-btn--secondary" onClick={onNavigate}>
            Mở Bộ não AI
          </Link>
        }
      />
    </div>
  );
}

/**
 * Ngăn kéo Hỏi Gen — gắn MỘT lần trong OwnerShell. Dùng DetailSheet (Esc đóng, tiêu điểm vào ngăn, bẫy Tab) ở dạng `bare`: thanh
 * tiêu đề + nút đóng là của chính GenPanel. GenPanel đóng khung (nút ×, Esc trong ô nhập, bước dẫn đường trên điện thoại) ⇒
 * ngăn đóng theo. Gen đang chỉ một phần tử trên màn (Spotlight) ⇒ ngăn tạm ẩn để Sếp thấy và bấm được phần tử đó.
 */
export function AskGenDrawer({ userId, genOn }: { userId: string; genOn: boolean }) {
  const open = useAskGenStore((s) => s.open);
  const genOpen = useGenStore((s) => !!s.openByUser[userId]);
  const spotlight = useGenStore((s) => !!s.spotlight);
  const { pathname } = useLocation();
  const onGenPage = pathname.replace(/\/+$/, '') === OWNER_PATHS.gen;
  const armed = useRef(false);

  useEffect(() => {
    if (!open) {
      armed.current = false;
      return;
    }
    if (onGenPage) {
      closeAskGen(); // trang /owner/gen đã là khung Gen — không mở ngăn thứ hai
      return;
    }
    if (!genOn) return;
    if (genOpen) armed.current = true;
    else if (armed.current) closeAskGen(); // GenPanel tự đóng khung
    else useGenStore.getState().setOpen(userId, true);
  }, [open, onGenPage, genOn, genOpen, userId]);

  return (
    <DetailSheet
      open={open && !onGenPage}
      title="Hỏi Gen"
      bare={genOn}
      onClose={closeAskGen}
      className="owner-gen-sheet"
      data-testid="owner-ask-drawer"
      data-spot={spotlight ? 'on' : undefined}
      escapeEnabled={() => !useGenStore.getState().spotlight}
    >
      {genOn ? (
        <section className="owner-gen owner-gen--sheet" aria-label="Khung chat với Gen" data-testid="owner-gen-drawer-panel">
          <GenPanel userId={userId} />
        </section>
      ) : (
        <GenOffNotice onNavigate={closeAskGen} />
      )}
    </DetailSheet>
  );
}

// ── trang /owner/gen (giữ cho link cũ) ──────────────────────────────────────────────────────────────────────────────

export function AskGenScreen() {
  const me = useMe();
  useEffect(() => {
    document.title = 'Hỏi Gen · Gen-Harness';
  }, []);

  if (me.isPending) {
    return (
      <div className="owner-screen" aria-busy="true" data-testid="owner-loading">
        <Skeleton width="100%" height={360} radius={8} />
      </div>
    );
  }
  if (me.isError) {
    return (
      <div className="owner-screen">
        <div className="owner-error" data-testid="owner-error">
          <CardError error={me.error} onRetry={() => void me.refetch()} retrying={me.isFetching} />
        </div>
      </div>
    );
  }
  if (!me.data.features?.gen) {
    return (
      <div className="owner-screen">
        <Panel title="Hỏi Gen" flush>
          <GenOffNotice />
        </Panel>
      </div>
    );
  }
  return (
    <div className="owner-ask" data-testid="owner-ask">
      <aside className="owner-ask__side" aria-label="Gợi ý câu hỏi">
        <h2 className="owner-ask__title">Hỏi Gen gì?</h2>
        <p className="owner-muted">Em xem số liệu, chỉ chỗ bấm và soạn nháp giúp Sếp. Em chỉ đề xuất — Sếp xác nhận (và nhập mã PIN khi cần) em mới làm.</p>
        <div className="owner-ask__prompts">
          {ASK_GEN_PROMPTS.map((q) => (
            <button key={q} type="button" className="kit-btn kit-btn--secondary" onClick={() => useGenStore.getState().setComposerDraft(q)}>
              <Icon name="ph ph-chat-text" size={16} /> {q}
            </button>
          ))}
        </div>
      </aside>
      <section className="owner-gen" aria-label="Khung chat với Gen" data-testid="owner-gen-panel">
        <GenPanel userId={me.data.id} />
      </section>
    </div>
  );
}
