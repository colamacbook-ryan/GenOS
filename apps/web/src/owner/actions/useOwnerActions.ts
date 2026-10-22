/**
 * v0.1.59 (G1) — thao tác GHI tại chỗ trên Mặt tiền Owner. Mọi thao tác đi qua luồng sẵn có: Gen chỉ ĐỀ XUẤT, không có
 * đường gửi thẳng — gửi tin / ghi ra ngoài luôn qua bản nháp + Xác nhận + mã PIN. Khi API trả 423 PIN_REQUIRED, client
 * (`createApiClient.requestPin`, packages/contracts/src/client.ts) tự mở hộp mã PIN rồi gửi lại — ở đây KHÔNG dựng hộp PIN.
 *
 * Sau MỌI lần ghi thành công: tải lại `['owner']`, `['drafts']`, cụm hàng đợi `['queue']` (hộp thư / việc / lời hứa sẵn có,
 * khoá `qkQueue` ở screens/queue/queries.ts) và cụm thị trường `['market']` (cơ hội / ghép cung–cầu, `qkMarket` ở
 * screens/market/queries.ts) — số đếm trên thanh điều hướng, bản tin và dòng tin luôn khớp. Mọi khoá ở đây PHẢI là gốc của một
 * khoá truy vấn thật (test `owner-actions-v0159` đối chiếu); khoá không ai dùng chỉ là code chết.
 *
 * Hook: `useDraftDecision(draftId)` (Duyệt / Sửa rồi gửi / Từ chối) · `useProposalDecision()` (Xác nhận / Huỷ đề xuất Gen) ·
 * `useTaskDone()` · `useTaskPostpone()` · `useCreateReminder()` · `useReplyToItem(itemId?)` (tạo NHÁP trả lời, trả
 * `draft_id`) · `useAckItem(itemId?)` · `useSilenceItem(itemId?)` · `useIntroduceMatch()` · `useRejectMatch()`.
 */
import { useMutation, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { ApiError, type DraftDecide as DraftDecideBody, type DraftDetail, type Task, type TaskCreateBody } from '@gen-harness/contracts';
import { api } from '../../lib/api';

/** Khoá phải tải lại sau khi ghi. `['owner']` là gốc của mọi truy vấn Mặt tiền (owner/queries.ts). */
export const ACTION_INVALIDATE_KEYS: readonly (readonly string[])[] = [
  ['owner'],
  ['drafts'],
  ['queue'],
  ['market'],
];

export function invalidateAfterWrite(qc: QueryClient): void {
  for (const queryKey of ACTION_INVALIDATE_KEYS) void qc.invalidateQueries({ queryKey: [...queryKey] });
}

/** Mọi hook ghi dùng chung: sau khi xong (thành công) tải lại các khoá trên. */
function useAfterWrite() {
  const qc = useQueryClient();
  return () => invalidateAfterWrite(qc);
}

// ── tiện ích thuần ─────────────────────────────────────────────────────────────────────────────────────────────────

/** "Dời sang mai": 09:00 sáng ngày mai theo giờ máy của Sếp. */
export function tomorrowMorning(now: Date = new Date()): string {
  return new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 9, 0, 0, 0).toISOString();
}

/** "Hết hạn sau 23 giờ" / "Sắp hết hạn — còn 40 phút" / "Đã hết hạn"; thiếu giờ ⇒ rỗng. */
export function expiresText(expiresAt: unknown, now: number = Date.now()): string {
  if (typeof expiresAt !== 'string') return '';
  const t = Date.parse(expiresAt);
  if (Number.isNaN(t)) return '';
  const ms = t - now;
  if (ms <= 0) return 'Đã hết hạn';
  const minutes = Math.ceil(ms / 60_000);
  if (minutes < 60) return `Sắp hết hạn — còn ${minutes} phút`;
  return `Hết hạn sau ${Math.round(minutes / 60)} giờ`;
}

// ── tiện ích hiển thị dùng chung của DraftDecide / ProposalDecide ──────────────────────────────────────────────────

const CHANNEL_LABEL: Record<string, string> = { zalo: 'Zalo', whatsapp: 'WhatsApp', telegram: 'Telegram', linkedin: 'LinkedIn' };

const text = (v: unknown): string => (typeof v === 'string' ? v : typeof v === 'number' || typeof v === 'boolean' ? String(v) : '');

/** "Zalo · Nhóm Gỗ Việt" / "Zalo · Chị Lan"; thiếu thông tin ⇒ rỗng. Chuỗi lạ (object) bị bỏ — không bao giờ render object. */
export function draftTargetText(d: Pick<DraftDetail, 'target'>): string {
  const t = d.target;
  if (!t) return '';
  const channel = text(t.channel);
  const who = text(t.group?.name) || text(t.person?.name);
  return [CHANNEL_LABEL[channel] ?? channel, who].filter(Boolean).join(' · ');
}

export const PROPOSAL_EXPIRED_TEXT = 'Đề xuất đã hết hạn — hỏi Gen lại';

/** 404 (không còn ở Redis) hoặc 410 ⇒ đề xuất đã hết hạn. */
export function isProposalExpired(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 404 || e.status === 410);
}

const idOf = (bound: string | undefined, v: unknown): string => {
  const id = typeof v === 'string' ? v : bound;
  if (!id) throw new Error('Thiếu mã mục cần thao tác');
  return id;
};

// ── nháp (Bàn làm việc ngay trên Mặt tiền) ───────────────────────────────────────────────────────────────────────

/** Duyệt / Sửa rồi gửi / Từ chối một nháp — cả ba cần mã PIN `draft.decide` (client tự hỏi khi gặp 423). */
export function useDraftDecision(draftId: string) {
  const done = useAfterWrite();
  const approve = useMutation({
    mutationFn: (body: DraftDecideBody = {}) => api.drafts.approve(draftId, body),
    onSuccess: done,
  });
  const editSend = useMutation({
    mutationFn: (body: DraftDecideBody & { text: string }) => api.drafts.editSend(draftId, body),
    onSuccess: done,
  });
  const reject = useMutation({
    mutationFn: (reason: string | null = null) => api.drafts.reject(draftId, { reason }),
    onSuccess: done,
  });
  return { approve, editSend, reject };
}

// ── đề xuất của Gen ─────────────────────────────────────────────────────────────────────────────────────────────

/** Xác nhận / Huỷ đề xuất Gen (kho_*, social_* cần mã PIN). 404/410 ⇒ đề xuất đã hết hạn — hỏi Gen lại. */
export function useProposalDecision() {
  const done = useAfterWrite();
  // Đề xuất đã hết hạn (404/410): tải lại danh sách để thẻ biến mất, dù thao tác không thành công.
  const expired = (e: unknown) => {
    if (isProposalExpired(e)) done();
  };
  const confirm = useMutation({
    mutationFn: (id: string) => api.gen.confirmProposal(id),
    onSuccess: done,
    onError: expired,
  });
  const cancel = useMutation({
    mutationFn: (id: string) => api.gen.cancelProposal(id),
    onSuccess: done,
    onError: expired,
  });
  return { confirm, cancel };
}

// ── việc & nhắc hẹn ─────────────────────────────────────────────────────────────────────────────────────────────

/** Tick "Xong": `PATCH /tasks/{id} {status:'done'}`. */
export function useTaskDone() {
  const done = useAfterWrite();
  return useMutation({
    mutationFn: (taskId: string): Promise<Task> => api.queue.tasks.update(taskId, { status: 'done' }),
    onSuccess: done,
  });
}

/** "Dời sang mai": `PATCH /tasks/{id} {due_at}` — mặc định 09:00 sáng mai. */
export function useTaskPostpone() {
  const done = useAfterWrite();
  return useMutation({
    mutationFn: ({ id, due_at }: { id: string; due_at?: string }): Promise<Task> =>
      api.queue.tasks.update(id, { due_at: due_at ?? tomorrowMorning() }),
    onSuccess: done,
  });
}

export interface ReminderInput {
  title: string;
  /** Người (hoặc nhóm) mà nhắc hẹn nói về — hiện ở hồ sơ. */
  subject?: { type: 'person' | 'group'; id: string };
  due_at: string;
}

/** "Tạo nhắc": `POST /tasks` — đến hạn thì nhắc (`remind_at` = `due_at`). */
export function useCreateReminder() {
  const done = useAfterWrite();
  return useMutation({
    mutationFn: ({ title, subject, due_at }: ReminderInput): Promise<Task> => {
      const body: TaskCreateBody = { title, due_at, remind_at: due_at, ...(subject ? { subject } : {}) };
      return api.queue.tasks.create(body);
    },
    onSuccess: done,
  });
}

// ── tin trong Thông tin / Quan hệ ──────────────────────────────────────────────────────────────────────────

export interface ReplyResult {
  /** Mã bản nháp vừa tạo (`InboxActResult.draft.id`) — mở `<DraftDecide draftId>` để "Duyệt và gửi" (cần mã PIN). */
  draft_id: string | null;
}

/**
 * "Trả lời": `POST /inbox/{id}/act {text}` chỉ TẠO NHÁP (không gửi). Gọi `mutate(text)` (khi đã truyền `itemId` vào hook)
 * hoặc `mutate({ itemId, text })`. Trả `{ draft_id }`.
 */
export function useReplyToItem(itemId?: string) {
  const done = useAfterWrite();
  return useMutation({
    mutationFn: async (v: string | { itemId?: string; text: string }): Promise<ReplyResult> => {
      const text = typeof v === 'string' ? v : v.text;
      const id = idOf(itemId, typeof v === 'string' ? undefined : v.itemId);
      const r = await api.queue.inbox.act(id, { text });
      return { draft_id: r.draft?.id ?? null };
    },
    onSuccess: done,
  });
}

/** "Ghi nhận" một cảnh báo: `POST /inbox/{id}/act {}` (tuỳ chọn `create_task`). */
export function useAckItem(itemId?: string) {
  const done = useAfterWrite();
  return useMutation({
    mutationFn: (v?: string | { itemId?: string; create_task?: boolean }) =>
      api.queue.inbox.act(idOf(itemId, typeof v === 'string' ? v : v?.itemId), {
        ...(typeof v === 'object' && v?.create_task ? { create_task: true } : {}),
      }),
    onSuccess: done,
  });
}

/** "Tắt tiếng": `POST /inbox/{id}/silence` — tin biến khỏi dòng tin (kèm lý do / hạn tuỳ chọn). */
export function useSilenceItem(itemId?: string) {
  const done = useAfterWrite();
  return useMutation({
    mutationFn: (v?: string | { itemId?: string; reason?: string | null; until?: string | null }) =>
      api.queue.inbox.silence(idOf(itemId, typeof v === 'string' ? v : v?.itemId), {
        reason: typeof v === 'object' ? (v?.reason ?? null) : null,
        until: typeof v === 'object' ? (v?.until ?? null) : null,
      }),
    onSuccess: done,
  });
}

/** Cung–cầu "Giới thiệu": `POST /matches/{id}/introduce` — chỉ tạo NHÁP chờ duyệt (`draft.id`), không gửi gì. */
export function useIntroduceMatch() {
  const done = useAfterWrite();
  return useMutation({
    mutationFn: (matchId: string) => api.market.matches.introduce(matchId),
    onSuccess: done,
  });
}

/** Cung–cầu "Bỏ qua": `POST /matches/{id}/reject`. */
export function useRejectMatch() {
  const done = useAfterWrite();
  return useMutation({
    mutationFn: (matchId: string) => api.market.matches.reject(matchId),
    onSuccess: done,
  });
}
