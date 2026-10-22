/**
 * Gửi câu hỏi cho Gen và nhận câu trả lời từng bước. Nguồn chính: WS `gen.step`/`gen.done` (chỉ người hỏi nhận);
 * song song hỏi `GET /gen/turns/{id}` mỗi 1,2 s tới khi xong — WS rớt thì câu trả lời vẫn tới. Hai nguồn ghép theo
 * `seq`, bước `ui` chỉ thực thi MỘT lần và tuần tự (mở trang xong mới làm sáng).
 */
import { useQuery } from '@tanstack/react-query';
import { ApiError, type GenBriefingSection, type GenDoneEvent, type GenMessage, type GenRating, GenStep, GenStepEvent, GenTurn } from '@gen-harness/contracts';
import { api } from '../lib/api';
import { errorDetail, errorText } from '../lib/errorText';
import { qk } from '../lib/queries';
import { queryClient } from '../lib/queryClient';
import { onRealtimeEvent } from '../lib/realtime';
import { toast } from '../lib/toast';
import { currentScreenKey, executeUiAction, visibleTargets } from './director';
import { mergeStep, useGenStore, type GenChatMessage } from './genStore';
import { AUTO_CHOICE, loadChoice, saveChoice, toBody } from './modelChoice';

export const POLL_MS = 1200;

/** v0.1.55 (G3): khoá cache của `GET /gen/settings` (có `model_options`) — một lần tải, dùng chung mọi lần mở khung Gen. */
export const GEN_SETTINGS_KEY = ['gen', 'settings'] as const;

/**
 * `model_options` của máy chủ (tầng nào dùng được cho người này, mức suy nghĩ nào tầng đó hỗ trợ) cho thẻ chọn model. Lỗi /
 * máy chủ cũ không gửi ⇒ `undefined` ⇒ khung chat coi mọi tầng là dùng được (máy chủ vẫn hạ về Tự động kèm một dòng giải
 * thích nếu không được).
 */
export function useGenModelOptions() {
  const q = useQuery({ queryKey: GEN_SETTINGS_KEY, queryFn: ({ signal }) => api.gen.settings(signal), staleTime: 60_000, retry: false });
  return q.data?.model_options;
}

let queue: Promise<void> = Promise.resolve();
const polling = new Set<string>();

function enqueue(fn: () => Promise<void>): void {
  queue = queue.then(fn).catch(() => undefined);
}

/** Bước WS tới trước khi `POST /gen/turns` trả về (chưa có tin trả lời để ghép) — giữ tạm rồi phát lại. */
const early = new Map<string, Array<Pick<GenStepEvent, 'turn_id' | 'seq' | 'step'>>>();

function hasTurn(turnId: string): boolean {
  return useGenStore.getState().messages.some((m) => m.role === 'assistant' && m.turnId === turnId);
}

export function applyStep(ev: Pick<GenStepEvent, 'turn_id' | 'seq' | 'step'>): void {
  if (!hasTurn(ev.turn_id)) {
    early.set(ev.turn_id, [...(early.get(ev.turn_id) ?? []), ev]);
    return;
  }
  if (!mergeStep(ev.turn_id, ev.seq, ev.step)) return;
  const step: GenStep = ev.step;
  if (step.kind === 'ui') enqueue(() => executeUiAction(step.action, ev.turn_id));
}

export function finishTurn(turnId: string, status: GenDoneEvent['status']): void {
  useGenStore.setState((s) => ({
    busy: s.messages.some((m) => m.turnId !== turnId && m.status === 'running'),
    messages: s.messages.map((m) => (m.turnId === turnId && m.role === 'assistant' ? { ...m, status } : m)),
  }));
  polling.delete(turnId);
}

function applyTurn(t: GenTurn): void {
  for (const ev of t.steps) applyStep(ev);
  if (t.status !== 'running') finishTurn(t.turn_id, t.status);
}

async function poll(turnId: string): Promise<void> {
  polling.add(turnId);
  while (polling.has(turnId)) {
    await new Promise((r) => setTimeout(r, POLL_MS));
    if (!polling.has(turnId)) return;
    try {
      applyTurn(await api.gen.turn(turnId));
    } catch {
      /* thử lại vòng sau; mất phiên thì api client tự chuyển trang đăng nhập */
    }
  }
}

/** Id người đang đăng nhập (đã có trong cache `/auth/me` khi khung Gen hiện). */
function currentUserId(): string | null {
  return queryClient.getQueryData<{ id?: string }>(qk.me)?.id ?? null;
}

/**
 * Gửi câu hỏi. v0.1.41 (F-8a): mã hội thoại server trả về được lưu máy kèm `userId` (chủ hội thoại) để tải lại trang
 * vẫn mở đúng hội thoại của đúng người.
 */
export async function sendQuestion(text: string, userId: string | null = currentUserId()): Promise<void> {
  const q = text.trim();
  if (!q) return;
  const st = useGenStore.getState();
  const localId = `u-${Date.now()}`;
  useGenStore.setState({ busy: true, messages: [...st.messages, { id: localId, role: 'user', text: q, steps: [] }] });
  // v0.1.55 (G3): lựa chọn model của hội thoại này; Tự động (chuẩn) ⇒ BỎ trường `model_choice`.
  const choice = st.modelChoice;
  const modelChoice = toBody(choice);
  try {
    const res = await api.gen.createTurn({
      conversation_id: st.conversationId,
      text: q,
      context: { route: window.location.pathname + window.location.search, screen_key: currentScreenKey(), visible_targets: visibleTargets() },
      ...(modelChoice ? { model_choice: modelChoice } : {}),
    });
    saveChoice(res.conversation_id, choice); // hội thoại mới vừa có mã ⇒ nhớ lựa chọn theo mã đó
    useGenStore.setState((s) => ({
      conversationId: res.conversation_id,
      conversationOwner: userId,
      messages: [...s.messages, { id: `a-${res.turn_id}`, role: 'assistant', turnId: res.turn_id, steps: [], status: 'running' }],
    }));
    for (const ev of early.get(res.turn_id) ?? []) applyStep(ev);
    early.delete(res.turn_id);
    void poll(res.turn_id);
  } catch (e) {
    let msg =
      e instanceof ApiError && e.status === 409
        ? 'Gen đang trả lời câu trước — đợi xong rồi hỏi tiếp nhé.'
        : e instanceof ApiError && e.status === 429
          ? 'Hỏi hơi nhanh rồi — đợi vài phút rồi hỏi tiếp nhé.'
          : errorText(e);
    let detail = errorDetail(e);
    if (e instanceof ApiError && e.code === 'MODEL_CHOICE_INVALID') {
      // 422 thân thiện: câu của máy chủ ("… em dùng chế độ Tự động nhé"); lý do kỹ thuật (chuỗi) nằm ở "Chi tiết kỹ thuật".
      const title = typeof e.problem.title === 'string' ? e.problem.title.trim() : '';
      const why = typeof e.problem.detail === 'string' ? e.problem.detail.trim() : '';
      msg = title || 'Lựa chọn model không hợp lệ — em dùng chế độ Tự động nhé';
      detail = [detail, why].filter(Boolean).join(' · ') || null;
      useGenStore.getState().setModelChoice(AUTO_CHOICE); // đúng như lời hứa: lượt sau dùng Tự động
    }
    useGenStore.setState((s) => ({
      busy: false,
      messages: [...s.messages, { id: `e-${localId}`, role: 'assistant', status: 'failed', steps: [{ kind: 'say', text: msg || 'Không gửi được câu hỏi — thử lại sau.' }], detail }],
    }));
  }
}

/** v0.1.49: `content.sections` từ máy chủ — bỏ phần tử không phải object (không để vỡ khung Gen vì một mục hỏng). */
function toSections(c: GenMessage['content']): GenBriefingSection[] | undefined {
  if (c.kind !== 'briefing' || !Array.isArray(c.sections)) return undefined;
  return c.sections.filter((x): x is GenBriefingSection => !!x && typeof x === 'object' && !Array.isArray(x));
}

function toChat(m: GenMessage): GenChatMessage {
  const c = m.content ?? {};
  return {
    id: m.id,
    role: m.role,
    text: c.text,
    steps: c.steps ?? [],
    turnId: m.turn_id ?? undefined,
    status: 'done' as const,
    feedback: m.feedback ?? null,
    kind: c.kind === 'briefing' ? 'briefing' : undefined,
    sections: toSections(c),
    hubAt: c.kind === 'briefing' && typeof c.hub_at === 'number' && Number.isInteger(c.hub_at) ? c.hub_at : undefined,
  };
}

/** Lần mở hội thoại mới nhất (mở từ chuông giữa lúc đang tải lại hội thoại cũ ⇒ chỉ lần sau cùng được hiện). */
let loadSeq = 0;
let loading = 0;

/** Kết quả mở hội thoại: đã mở · không còn (404) · bỏ qua vì Gen đang trả lời câu khác (không đè lượt đang chạy). */
export type LoadResult = 'opened' | 'missing' | 'busy';

/** Gen đang trả lời (cờ busy hoặc còn tin "đang nghĩ") ⇒ không được thay danh sách tin. */
export function genBusy(): boolean {
  const st = useGenStore.getState();
  return st.busy || st.messages.some((m) => m.status === 'running');
}

/**
 * Mở lại một hội thoại cũ (chỉ hiển thị, không chạy lại hành động UI). v0.1.41 (F-8a): 404 (hội thoại đã bị xoá / quá
 * hạn lưu) ⇒ bỏ mã đã lưu, trả `'missing'` không ném; lỗi khác ném lại để nơi gọi báo bằng `errorText`. Trong lúc chờ
 * server mà Sếp đã gửi câu hỏi mới (Gen đang trả lời) ⇒ KHÔNG đè tin/busy, trả `'busy'` — lượt đang chạy vẫn nhận bước.
 */
export async function loadConversation(id: string, userId: string | null = currentUserId()): Promise<LoadResult> {
  if (genBusy()) return 'busy';
  const seq = ++loadSeq;
  loading += 1;
  useGenStore.setState({ loadingConversation: true });
  let msgs: GenMessage[];
  try {
    msgs = await api.gen.messages(id);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      if (seq === loadSeq && !genBusy() && useGenStore.getState().conversationId === id) useGenStore.getState().reset();
      return 'missing';
    }
    throw e;
  } finally {
    loading -= 1;
    if (loading === 0) useGenStore.setState({ loadingConversation: false });
  }
  if (seq !== loadSeq) return 'opened'; // đã có lần mở khác mới hơn
  if (genBusy()) {
    // Câu hỏi mới gửi vào CHÍNH hội thoại này trong lúc chờ ⇒ chèn các tin cũ lên trước, giữ nguyên lượt đang chạy.
    useGenStore.setState((s) => {
      if (s.conversationId !== id) return {};
      const live = new Set(s.messages.map((m) => m.turnId).filter(Boolean));
      let older = msgs.filter((m) => !(m.turn_id && live.has(m.turn_id)));
      // Tin hỏi vừa gửi có thể đã là tin cuối của bản server (POST xong trước GET) ⇒ bỏ bản trùng.
      const last = older[older.length - 1];
      if (last?.role === 'user' && s.messages.some((m) => m.role === 'user' && m.id.startsWith('u-') && m.text === last.content?.text)) {
        older = older.slice(0, -1);
      }
      return { messages: [...older.map(toChat), ...s.messages.filter((m) => !m.retryConversation)] };
    });
    return 'busy';
  }
  useGenStore.setState({ conversationId: id, conversationOwner: userId, busy: false, messages: msgs.map(toChat), modelChoice: loadChoice(id) });
  return 'opened';
}

let restoring: Promise<void> | null = null;

/** Chỉ còn tin báo lỗi tải lại (hoặc trống) ⇒ được thay bằng kết quả tải lại / tin báo mới. */
function onlyRestoreErrors(msgs: GenChatMessage[]): boolean {
  return msgs.every((m) => !!m.retryConversation);
}

function runRestore(id: string, userId: string): Promise<void> {
  if (restoring) return restoring;
  useGenStore.setState({ restoring: true });
  restoring = loadConversation(id, userId)
    .then(() => undefined)
    .catch((e: unknown) => {
      useGenStore.setState((s) =>
        !onlyRestoreErrors(s.messages) || genBusy()
          ? {}
          : {
              // Bỏ mã đang lưu: câu hỏi kế tiếp mở hội thoại MỚI (không rơi vào hội thoại Sếp không nhìn thấy);
              // "Thử lại" mở lại đúng mã cũ.
              conversationId: s.conversationId === id ? null : s.conversationId,
              conversationOwner: s.conversationId === id ? null : s.conversationOwner,
              messages: [
                {
                  id: `e-restore-${id}`,
                  role: 'assistant',
                  status: 'failed',
                  steps: [{ kind: 'say', text: 'Chưa tải lại được hội thoại trước — Sếp thử lại sau ít phút.' }],
                  detail: errorDetail(e) ?? errorText(e),
                  retryConversation: id,
                },
              ],
            },
      );
    })
    .finally(() => {
      restoring = null;
      useGenStore.setState({ restoring: false });
    });
  return restoring;
}

/**
 * v0.1.41 (F-8a): mở khung Gen sau khi tải lại trang ⇒ tải lại hội thoại đã lưu (một lần). Mã thuộc người khác
 * (đổi tài khoản trên cùng máy) ⇒ bỏ, không gọi API. 404 ⇒ bỏ im lặng; lỗi khác ⇒ một dòng báo trong khung kèm
 * "Chi tiết kỹ thuật" và nút "Thử lại".
 */
export function restoreIfNeeded(userId: string): Promise<void> {
  const st = useGenStore.getState();
  if (!st.conversationId) return Promise.resolve();
  if (st.conversationOwner !== userId) {
    st.reset();
    return Promise.resolve();
  }
  // v0.1.55 (G3): tải lại trang ⇒ nút chọn model hiện đúng lựa chọn đã nhớ của hội thoại này ngay (trước khi tải xong tin).
  if (!genBusy()) useGenStore.setState({ modelChoice: loadChoice(st.conversationId) });
  if (st.messages.length > 0 || genBusy()) return Promise.resolve();
  if (restoring) return restoring;
  if (loading > 0) return Promise.resolve(); // đang mở hội thoại khác (vd bản tin từ chuông)
  return runRestore(st.conversationId, userId);
}

/** Nút "Thử lại" trên dòng báo lỗi tải lại hội thoại. */
export function retryRestore(id: string, userId: string): Promise<void> {
  if (genBusy() || !onlyRestoreErrors(useGenStore.getState().messages)) return Promise.resolve();
  return runRestore(id, userId);
}

function setFeedback(turnId: string, feedback: GenRating | null): void {
  useGenStore.setState((s) => ({
    messages: s.messages.map((m) => (m.role === 'assistant' && m.turnId === turnId ? { ...m, feedback } : m)),
  }));
}

/**
 * v0.1.41 (F-86): Hữu ích / Không hữu ích — đổi ngay trên màn (lạc quan), lỗi thì hoàn tác + báo. Bấm lại đúng nút
 * đang chọn ⇒ bỏ đánh giá (`DELETE /gen/feedback/{turn_id}`).
 */
export async function sendFeedback(m: GenChatMessage, rating: GenRating): Promise<void> {
  const turnId = m.turnId;
  const conversationId = useGenStore.getState().conversationId;
  if (!turnId || !conversationId) return;
  const prev = useGenStore.getState().messages.find((x) => x.role === 'assistant' && x.turnId === turnId)?.feedback ?? null;
  const next = prev === rating ? null : rating;
  setFeedback(turnId, next);
  try {
    if (next) await api.gen.feedback({ conversation_id: conversationId, turn_id: turnId, rating: next });
    else await api.gen.clearFeedback(turnId);
  } catch (e) {
    setFeedback(turnId, prev);
    const d = errorDetail(e);
    toast(`${errorText(e) || 'Chưa lưu được đánh giá — thử lại sau.'}${d ? ` (Chi tiết kỹ thuật: ${d})` : ''}`, 'bad');
  }
}

export function stopAll(): void {
  polling.clear();
}

onRealtimeEvent('gen.step', (_qc, data) => applyStep(data as GenStepEvent));
onRealtimeEvent('gen.done', (_qc, data) => {
  const d = data as GenDoneEvent;
  if (!hasTurn(d.turn_id)) return; // polling của lượt đó sẽ tự nhận trạng thái cuối
  // Đối chiếu lần cuối với server (WS có thể đã lỡ bước khi vừa kết nối lại) rồi mới đóng lượt.
  void api.gen
    .turn(d.turn_id)
    .then(applyTurn)
    .catch(() => finishTurn(d.turn_id, d.status));
});
