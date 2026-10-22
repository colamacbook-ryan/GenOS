import { create } from 'zustand';

export type ToastTone = 'ok' | 'warn' | 'bad' | 'neutral';

export interface Toast {
  id: number;
  text: string;
  tone: ToastTone;
}

interface ToastState {
  toasts: Toast[];
  push: (text: string, tone?: ToastTone, ms?: number) => void;
  dismiss: (id: number) => void;
}

let seq = 0;

/** Short-lived confirmations ("Đã lưu trọng số"), announced via aria-live. */
export const useToasts = create<ToastState>((set, get) => ({
  toasts: [],
  push: (text, tone = 'ok', ms = 4000) => {
    const id = ++seq;
    set((s) => ({ toasts: [...s.toasts.slice(-3), { id, text, tone }] }));
    if (ms > 0) setTimeout(() => get().dismiss(id), ms);
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

export const toast = (text: string, tone: ToastTone = 'ok') => useToasts.getState().push(text, tone);
