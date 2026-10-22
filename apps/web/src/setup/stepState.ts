import type { SetupState, SetupStep, SetupStepStatus } from '@gen-harness/contracts';
import { SETUP_STEPS, type StepMeta } from './steps';

export type MergedStep = StepMeta & { status: SetupStepStatus; available: boolean };

/**
 * Merge the server's step list with the doc-06 metadata (server wins on
 * title/required). A step is `available` when the server says so, else when
 * the web has built it — and only then is it rendered as a real form.
 */
export function mergeSteps(state: SetupState | undefined): MergedStep[] {
  return SETUP_STEPS.map((m) => {
    const s: SetupStep | undefined = state?.steps.find((x) => x.n === m.n);
    let status: SetupStepStatus = s?.status ?? 'todo';
    if (status === 'todo' && state?.current_step === m.n) status = 'doing';
    const available = m.built && (s?.available ?? true);
    return { ...m, title: s?.title ?? m.title, required: s?.required ?? m.required, status, available };
  });
}

const settled = (s: SetupStep | undefined) => s?.status === 'done' || s?.status === 'skipped';

/** Whether step n is available (server flag, else the local `built`). */
export function isAvailable(n: number, state: SetupState | undefined): boolean {
  const meta = SETUP_STEPS.find((m) => m.n === n);
  const s = state?.steps.find((x) => x.n === n);
  return !!meta?.built && (s?.available ?? true);
}

/**
 * A step can be opened when it is settled, not beyond the server's current
 * step, or reached by passing only steps that are not available yet (phase 2:
 * 5–11 are optional — "Để sau" moves on and they show in "Việc thiết lập tiếp" on Tổng quan).
 */
export function isReachable(n: number, state: SetupState | undefined): boolean {
  if (!state) return n === 1;
  const s = state.steps.find((x) => x.n === n);
  if (n <= state.current_step || settled(s)) return true;
  for (let k = state.current_step; k < n; k++) {
    const sk = state.steps.find((x) => x.n === k);
    if (!settled(sk) && isAvailable(k, state)) return false;
  }
  return true;
}
