/**
 * Trạng thái khung chat Gen (docs/design/gen-v1.md §3.1). Mở/đóng nhớ THEO NGƯỜI DÙNG (localStorage, khoá theo id) —
 * đổi tài khoản trên cùng máy không kéo theo trạng thái của người khác.
 *
 * v0.1.41 (F-8a): máy chỉ nhớ MÃ hội thoại đang mở (`conversationId`) và người sở hữu (`conversationOwner`) để tải lại
 * trang vẫn thấy hội thoại; NỘI DUNG tin luôn lấy lại từ server (`GET /gen/conversations/{id}/messages`), không lưu máy.
 * Người khác đăng nhập trên cùng máy ⇒ mã không khớp chủ ⇒ bỏ (genClient.restoreIfNeeded).
 */
import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import type { GenBriefingSection, GenProposal, GenRating, GenStep, ModelChoice, TourStep } from '@gen-harness/contracts';
import { AUTO_CHOICE, loadChoice, saveChoice } from './modelChoice';

export interface GenChatMessage {
  id: string;
  role: 'user' | 'assistant';
  text?: string;
  /** Bước của câu trả lời theo `seq` (WS và polling có thể tới trùng — ghép theo seq). */
  steps: GenStep[];
  turnId?: string;
  status?: 'running' | 'done' | 'failed';
  /** v0.1.41 (F-86): đánh giá của người xem cho lượt này. */
  feedback?: GenRating | null;
  /** v0.1.41 (F-8): tin Bản tin Gen (hiện nhãn "Bản tin"). */
  kind?: 'briefing';
  /** v0.1.49 (F-8, QD-16): các mục của bản tin (chỉ tin Bản tin) — khung Gen vẽ riêng mục lấy từ Gen-hub (`external`). */
  sections?: GenBriefingSection[];
  /** v0.1.49: chỉ số trong `steps` để chèn thẻ mục Gen-hub (`content.hub_at`); không có ⇒ trước lời nhắc + nút đầu tiên. */
  hubAt?: number;
  /** v0.1.41: dòng lỗi có "Chi tiết kỹ thuật" (luôn là chuỗi). */
  detail?: string | null;
  /** v0.1.41 (F-8a): tin báo "chưa tải lại được hội thoại" — mã hội thoại để nút "Thử lại" mở lại. */
  retryConversation?: string;
}

export interface SpotlightState {
  target: string;
  message: string;
  /** Tour đang chạy: bước hiện tại `index` trong `steps`. */
  tour?: { steps: TourStep[]; index: number; turnId?: string };
  /** Không tìm thấy phần tử sau thời gian chờ. */
  missing?: boolean;
  waitFor?: 'click' | 'none';
}

interface GenState {
  openByUser: Record<string, boolean>;
  conversationId: string | null;
  /** v0.1.41 (F-8a): id người dùng sở hữu `conversationId` (đổi tài khoản trên cùng máy ⇒ không mở lại). */
  conversationOwner: string | null;
  messages: GenChatMessage[];
  busy: boolean;
  /** v0.1.41 (F-8a): đang tải lại hội thoại đã lưu sau khi tải lại trang (hiện "Đang mở lại hội thoại…"). */
  restoring: boolean;
  /** Đang tải nội dung một hội thoại (mở từ "Hội thoại cũ" hoặc bản tin từ chuông) ⇒ khoá gửi, hiện "Đang mở hội thoại…". */
  loadingConversation: boolean;
  spotlight: SpotlightState | null;
  /**
   * v0.1.54 (Gen hướng dẫn): `/overview?gen=coach` (link chuông) ⇒ đặt cờ để thẻ "Hôm nay của Sếp" cuộn tới và mở rộng.
   * Không lưu máy; thẻ tự hạ cờ sau khi xử lý.
   */
  coachFocus: boolean;
  /** v0.1.54: câu điền sẵn vào ô nhập của khung Gen (nút "Hỏi Gen thêm") — KHÔNG gửi; ô nhập lấy rồi hạ về null. */
  composerDraft: string | null;
  /**
   * v0.1.55 (G3): lựa chọn model / mức suy nghĩ của hội thoại đang mở (Tự động (chuẩn) khi là hội thoại mới). Nhớ THEO MÃ
   * HỘI THOẠI ở localStorage `gh-gen-model-choice` (gen/modelChoice.ts) — KHÔNG nằm trong `gh-gen`, không rò sang hội thoại khác.
   */
  modelChoice: ModelChoice;
  setModelChoice: (c: ModelChoice) => void;
  setCoachFocus: (on: boolean) => void;
  setComposerDraft: (text: string | null) => void;
  setOpen: (userId: string, open: boolean) => void;
  /** v0.1.41 (F-8a): ghi hội thoại đang mở kèm chủ của nó (được lưu máy để tải lại trang). */
  setConversation: (userId: string | null, id: string | null) => void;
  reset: () => void;
  setSpotlight: (s: SpotlightState | null) => void;
}

/** Phần được lưu máy (localStorage `gh-gen`) — chỉ mở/đóng và MÃ hội thoại, không có nội dung tin. */
export type GenPersisted = Pick<GenState, 'openByUser' | 'conversationId' | 'conversationOwner'>;

/** v0.1.41 (F-8a): bản 0 chỉ có `openByUser` ⇒ bổ sung mã hội thoại rỗng. */
export function migrateGen(persisted: unknown, version: number): GenPersisted {
  const p = (persisted && typeof persisted === 'object' ? persisted : {}) as Partial<GenPersisted>;
  const openByUser = p.openByUser && typeof p.openByUser === 'object' ? p.openByUser : {};
  if (version < 1) return { openByUser, conversationId: null, conversationOwner: null };
  return {
    openByUser,
    conversationId: typeof p.conversationId === 'string' ? p.conversationId : null,
    conversationOwner: typeof p.conversationOwner === 'string' ? p.conversationOwner : null,
  };
}

/**
 * localStorage bị chặn (chế độ riêng tư) hoặc ném lỗi khi ghi (đầy, hỏng) ⇒ chuyển sang bộ nhớ tạm của tab — khung
 * Gen vẫn chạy, chỉ không nhớ qua tải lại trang.
 */
function memoryFallbackStorage(): Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> {
  const mem = new Map<string, string>();
  let ls: Storage | null = null;
  try {
    ls = window.localStorage;
  } catch {
    ls = null;
  }
  const guard = <T>(fn: (s: Storage) => T, fallback: () => T): T => {
    if (!ls) return fallback();
    try {
      return fn(ls);
    } catch {
      ls = null;
      return fallback();
    }
  };
  return {
    getItem: (k) => guard((s) => s.getItem(k), () => mem.get(k) ?? null),
    setItem: (k, v) => guard((s) => s.setItem(k, v), () => void mem.set(k, v)),
    removeItem: (k) => guard((s) => s.removeItem(k), () => void mem.delete(k)),
  };
}

const safeStorage = createJSONStorage<GenPersisted>(memoryFallbackStorage);

export const useGenStore = create<GenState>()(
  persist(
    (set) => ({
      openByUser: {},
      conversationId: null,
      conversationOwner: null,
      messages: [],
      busy: false,
      restoring: false,
      loadingConversation: false,
      spotlight: null,
      coachFocus: false,
      composerDraft: null,
      modelChoice: AUTO_CHOICE,
      setModelChoice: (modelChoice) =>
        set((s) => {
          saveChoice(s.conversationId, modelChoice); // hội thoại mới (chưa có mã) ⇒ giữ trong store, lưu máy khi có mã
          return { modelChoice };
        }),
      setCoachFocus: (coachFocus) => set({ coachFocus }),
      setComposerDraft: (composerDraft) => set({ composerDraft }),
      setOpen: (userId, open) => set((s) => ({ openByUser: { ...s.openByUser, [userId]: open } })),
      setConversation: (userId, id) => set({ conversationId: id, conversationOwner: id ? userId : null, modelChoice: loadChoice(id) }),
      reset: () => set({ conversationId: null, conversationOwner: null, messages: [], busy: false, modelChoice: AUTO_CHOICE }),
      setSpotlight: (spotlight) => set({ spotlight }),
    }),
    {
      name: 'gh-gen',
      storage: safeStorage,
      version: 1,
      migrate: migrateGen,
      partialize: (s): GenPersisted => ({ openByUser: s.openByUser, conversationId: s.conversationId, conversationOwner: s.conversationOwner }),
    },
  ),
);

/** Ghép bước mới vào tin trả lời của lượt `turnId`; trả `true` nếu bước là mới (chưa thấy seq này). */
export function mergeStep(turnId: string, seq: number, step: GenStep): boolean {
  let fresh = false;
  useGenStore.setState((s) => ({
    messages: s.messages.map((m) => {
      if (m.role !== 'assistant' || m.turnId !== turnId) return m;
      if (m.steps[seq] !== undefined) return m;
      fresh = true;
      const steps = [...m.steps];
      steps[seq] = step;
      return { ...m, steps };
    }),
  }));
  return fresh;
}

/** Gen v2: cập nhật thẻ đề xuất (đã xác nhận / đã huỷ + kết quả) ở mọi tin đang hiện. */
export function patchProposal(next: GenProposal): void {
  useGenStore.setState((s) => ({
    messages: s.messages.map((m) => {
      if (!m.steps.some((st) => st?.kind === 'proposal' && st.proposal.id === next.id)) return m;
      return { ...m, steps: m.steps.map((st) => (st?.kind === 'proposal' && st.proposal.id === next.id ? { kind: 'proposal' as const, proposal: next } : st)) };
    }),
  }));
}
