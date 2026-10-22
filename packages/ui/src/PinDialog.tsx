import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import { Button } from './Button';
import { PinInput, type PinInputHandle } from './PinInput';

export type PinVerifyResult =
  | { ok: true }
  | { ok: false; attemptsLeft?: number | null; lockedUntil?: string | null; message?: string };

export interface PinDialogProps {
  open: boolean;
  /** Verify the PIN with the server. Never throws — map errors to a result. */
  onVerify: (pin: string) => Promise<PinVerifyResult>;
  onCancel: () => void;
  /** Locked state known before opening (e.g. 423 PIN_LOCKED). */
  lockedUntil?: string | null;
  /** Clock injection for tests. */
  now?: () => number;
}

function formatClock(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${hh}:${mm}`;
}

function minutesLeft(iso: string, now: number): number {
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? 0 : Math.max(0, Math.ceil((t - now) / 60000));
}

/**
 * Global PIN prompt (423 PIN_REQUIRED). Six digit boxes, attempts left and
 * lock state announced through an aria-live region; Esc cancels.
 */
export function PinDialog({ open, onVerify, onCancel, lockedUntil: lockedProp = null, now = Date.now }: PinDialogProps) {
  const [pin, setPin] = useState('');
  const [busy, setBusy] = useState(false);
  const [attemptsLeft, setAttemptsLeft] = useState<number | null>(null);
  const [lockedUntil, setLockedUntil] = useState<string | null>(lockedProp);
  const [message, setMessage] = useState<string | null>(null);
  const [, tick] = useState(0);
  const pinRef = useRef<PinInputHandle>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (open) {
      returnFocus.current = (document.activeElement as HTMLElement) ?? null;
      setPin('');
      setAttemptsLeft(null);
      setMessage(null);
      setLockedUntil(lockedProp);
      setTimeout(() => pinRef.current?.focus(), 0);
    } else if (returnFocus.current) {
      returnFocus.current.focus?.();
      returnFocus.current = null;
    }
  }, [open, lockedProp]);

  const locked = !!lockedUntil && minutesLeft(lockedUntil, now()) > 0;

  // refresh the lock countdown once a minute
  useEffect(() => {
    if (!open || !lockedUntil) return;
    const id = setInterval(() => tick((n) => n + 1), 30_000);
    return () => clearInterval(id);
  }, [open, lockedUntil]);

  if (!open || typeof document === 'undefined') return null;

  const submit = async (value = pin) => {
    if (value.length !== 6 || busy || locked) return;
    setBusy(true);
    setMessage(null);
    const res = await onVerify(value);
    setBusy(false);
    if (res.ok) return;
    setPin('');
    if (res.lockedUntil) {
      setLockedUntil(res.lockedUntil);
    } else {
      setAttemptsLeft(res.attemptsLeft ?? null);
      setMessage(res.message ?? null);
      setTimeout(() => pinRef.current?.focus(), 0);
    }
  };

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      onCancel();
    } else if (e.key === 'Tab' && dialogRef.current) {
      const f = dialogRef.current.querySelectorAll<HTMLElement>('input:not([disabled]), button:not([disabled])');
      if (!f.length) return;
      const first = f[0];
      const last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  };

  let status: { text: string; tone: 'bad' | 'warn' | 'neutral' } = {
    text: 'Phiên PIN hết hạn sau 30 phút không thao tác.',
    tone: 'neutral',
  };
  if (locked && lockedUntil) {
    status = {
      text: `PIN đang bị khoá tới ${formatClock(lockedUntil)} — còn ${minutesLeft(lockedUntil, now())} phút. Mọi lần nhập đều được ghi nhật ký.`,
      tone: 'bad',
    };
  } else if (attemptsLeft !== null) {
    status = {
      text: `Mã PIN không đúng — còn ${attemptsLeft} lần thử trước khi khoá 15 phút.`,
      tone: attemptsLeft <= 2 ? 'bad' : 'warn',
    };
  } else if (message) {
    status = { text: message, tone: 'bad' };
  }

  return createPortal(
    <div className="gh-dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onCancel()}>
      <div
        ref={dialogRef}
        className="gh-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="gh-pin-title"
        aria-describedby="gh-pin-desc"
        onKeyDown={onKeyDown}
      >
        <div className="gh-card__header">
          <div>
            <div className="gh-card__title" id="gh-pin-title">
              Mã PIN xác nhận thao tác
            </div>
            <div className="gh-card__kicker">6 chữ số · bảo vệ mọi thao tác nhạy cảm</div>
          </div>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <div className="gh-dialog__body">
            <p className="gh-dialog__text" id="gh-pin-desc">
              Thao tác này cần phiên PIN còn hạn. Nhập mã PIN 6 số để tiếp tục.
            </p>
            <PinInput
              ref={pinRef}
              label="Mã PIN"
              value={pin}
              onChange={setPin}
              onComplete={(v) => void submit(v)}
              invalid={attemptsLeft !== null || locked}
              disabled={busy || locked}
              describedBy="gh-pin-status"
            />
            <div
              id="gh-pin-status"
              className={`gh-dialog__status${status.tone !== 'neutral' ? ` gh-dialog__status--${status.tone}` : ''}`}
              aria-live="polite"
              role="status"
            >
              {status.text}
            </div>
          </div>
          <div className="gh-dialog__actions">
            <Button variant="secondary" onClick={onCancel}>
              Huỷ
            </Button>
            <Button variant="primary" type="submit" icon="ph ph-password" disabled={pin.length !== 6 || locked} loading={busy}>
              Xác nhận
            </Button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  );
}
