import { create } from 'zustand';

interface PinState {
  open: boolean;
  lockedUntil: string | null;
  /** Resolvers of every request waiting on this PIN prompt. */
  waiters: Array<(ok: boolean) => void>;
  /** Open the dialog; resolves true once a PIN session exists, false on cancel. */
  request: (opts?: { lockedUntil?: string | null }) => Promise<boolean>;
  finish: (ok: boolean) => void;
}

export const usePinStore = create<PinState>((set, get) => ({
  open: false,
  lockedUntil: null,
  waiters: [],
  request: (opts) =>
    new Promise<boolean>((resolve) => {
      set((s) => ({ open: true, lockedUntil: opts?.lockedUntil ?? s.lockedUntil, waiters: [...s.waiters, resolve] }));
    }),
  finish: (ok) => {
    const { waiters } = get();
    set({ open: false, lockedUntil: null, waiters: [] });
    waiters.forEach((w) => w(ok));
  },
}));
