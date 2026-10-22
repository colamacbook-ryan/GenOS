/**
 * GenDirector (docs/design/gen-v1.md §3.1): thực thi hành động UI ĐÃ ĐƯỢC SERVER KIỂM — mở trang, làm sáng phần tử
 * `data-gen-target`, dẫn tour từng bước. Chờ phần tử xuất hiện (MutationObserver, tối đa 4 s); không thấy → báo
 * `target_missing` cho server (số đo) và hiện lời nhắn thay vì treo.
 */
import { GEN_SCREEN_BY_KEY, GEN_TARGET_BY_ID, splitTargetId, type TourOutcome, type TourStep, type UiAction } from '@gen-harness/contracts';
import { api } from '../lib/api';
import { navigateTo } from '../lib/navigation';
import { useGenStore } from './genStore';

export const WAIT_MS = 4000;

export function targetSelector(id: string): string {
  return `[data-gen-target="${id.replace(/["\\]/g, '\\$&')}"]`;
}

/**
 * v0.1.43: phần tử đích nằm trong `<details>` đang gập (vd thẻ Jev trong "Nâng cao" của Bộ não AI) có khung 0×0 —
 * vòng sáng sẽ lệch về góc màn. Mở mọi `<details>` tổ tiên (lặp qua các cấp) trước khi đo.
 */
export function revealTarget(el: Element): void {
  let d = el.parentElement?.closest('details') ?? null;
  while (d) {
    if (!d.open) d.open = true;
    d = d.parentElement?.closest('details') ?? null;
  }
}

/** Chờ phần tử có `data-gen-target=id` xuất hiện trong `root`; hết giờ → null. */
export function waitForTarget(id: string, timeoutMs = WAIT_MS, root: ParentNode = document): Promise<Element | null> {
  const sel = targetSelector(id);
  const now = root.querySelector(sel);
  if (now) return Promise.resolve(now);
  return new Promise((resolve) => {
    const obs = new MutationObserver(() => {
      const el = root.querySelector(sel);
      if (el) {
        obs.disconnect();
        clearTimeout(timer);
        resolve(el);
      }
    });
    const timer = setTimeout(() => {
      obs.disconnect();
      resolve(null);
    }, timeoutMs);
    obs.observe(root === document ? document.body : (root as Node), { childList: true, subtree: true, attributes: true, attributeFilter: ['data-gen-target'] });
  });
}

/** Khoá màn đang mở, theo đường dẫn (`/system?tab=brain` → `system`). */
export function currentScreenKey(pathname = window.location.pathname): string | null {
  const seg = pathname.split('/').filter(Boolean)[0] ?? 'overview';
  return GEN_SCREEN_BY_KEY[seg] ? seg : null;
}

/** Các mục tiêu đang hiện trên màn (gửi kèm câu hỏi làm ngữ cảnh). */
export function visibleTargets(root: ParentNode = document): string[] {
  return [...new Set([...root.querySelectorAll('[data-gen-target]')].map((e) => e.getAttribute('data-gen-target') ?? ''))].filter(Boolean).slice(0, 200);
}

export function screenHref(screen: string, params?: Record<string, string>): string {
  const base = GEN_SCREEN_BY_KEY[screen]?.path ?? `/${screen}`;
  const qs = params && Object.keys(params).length ? `?${new URLSearchParams(params).toString()}` : '';
  return base + qs;
}

/** Đưa trình duyệt tới đúng màn + tham số (tab) để mục tiêu hiện ra; không làm gì nếu đã đúng. */
function ensureLocation(targetId: string, screen?: string): void {
  const reg = GEN_TARGET_BY_ID[splitTargetId(targetId).base];
  const key = screen ?? reg?.screen;
  if (!key) return;
  const path = GEN_SCREEN_BY_KEY[key]?.path ?? `/${key}`;
  const here = new URLSearchParams(window.location.search);
  const needParams = reg?.params ?? {};
  const samePath = window.location.pathname === path;
  const paramsOk = Object.entries(needParams).every(([k, v]) => here.get(k) === v);
  if (samePath && paramsOk) return;
  const merged = samePath ? Object.fromEntries(here) : {};
  navigateTo(screenHref(key, { ...merged, ...needParams }));
}

function ack(turnId: string | undefined, step: number, outcome: TourOutcome): void {
  if (!turnId) return;
  void api.gen.ack(turnId, { step, outcome }).catch(() => undefined);
}

async function showTarget(target: string, message: string, extra: { screen?: string; waitFor?: 'click' | 'none'; tour?: { steps: TourStep[]; index: number; turnId?: string } } = {}): Promise<boolean> {
  ensureLocation(target, extra.screen);
  const { setSpotlight } = useGenStore.getState();
  setSpotlight({ target, message, tour: extra.tour, waitFor: extra.waitFor });
  const el = await waitForTarget(target);
  const cur = useGenStore.getState().spotlight;
  if (!el && cur?.target === target) setSpotlight({ ...cur, missing: true });
  return !!el;
}

export async function runTourStep(steps: TourStep[], index: number, turnId?: string): Promise<void> {
  const step = steps[index];
  if (!step) {
    useGenStore.getState().setSpotlight(null);
    return;
  }
  // Màn của bước: của chính bước, hoặc bước gần nhất trước nó có ghi màn.
  const screen = step.screen ?? [...steps.slice(0, index)].reverse().find((s) => s.screen)?.screen;
  const ok = await showTarget(step.target, step.message, { screen, tour: { steps, index, turnId } });
  if (!ok) ack(turnId, index, 'target_missing');
}

export function tourNext(): void {
  const s = useGenStore.getState().spotlight;
  if (!s?.tour) return;
  ack(s.tour.turnId, s.tour.index, 'done');
  if (s.tour.index + 1 >= s.tour.steps.length) {
    useGenStore.getState().setSpotlight(null);
    return;
  }
  void runTourStep(s.tour.steps, s.tour.index + 1, s.tour.turnId);
}

export function tourBack(): void {
  const s = useGenStore.getState().spotlight;
  if (!s?.tour || s.tour.index === 0) return;
  void runTourStep(s.tour.steps, s.tour.index - 1, s.tour.turnId);
}

export function closeSpotlight(): void {
  const s = useGenStore.getState().spotlight;
  if (s?.tour && s.tour.index < s.tour.steps.length - 1) ack(s.tour.turnId, s.tour.index, 'skipped');
  useGenStore.getState().setSpotlight(null);
}

/** Thực thi một hành động UI đã kiểm (từ bước `ui` hoặc khi Sếp bấm thẻ đề xuất). */
export async function executeUiAction(action: UiAction, turnId?: string): Promise<void> {
  if (action.type === 'navigate') {
    useGenStore.getState().setSpotlight(null);
    navigateTo(screenHref(action.screen, action.params));
    return;
  }
  if (action.type === 'highlight') {
    await showTarget(action.target, action.message, { waitFor: action.waitFor });
    return;
  }
  await runTourStep(action.steps, 0, turnId);
}
