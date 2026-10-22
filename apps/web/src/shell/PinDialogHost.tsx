import { ApiError } from '@gen-harness/contracts';
import { PinDialog, type PinVerifyResult } from '@gen-harness/ui';
import { api } from '../lib/api';
import { usePinStore } from '../lib/pinStore';
import { queryClient } from '../lib/queryClient';
import { qk } from '../lib/queries';

/** Mounted once; opened by the API client on 423 PIN_REQUIRED. */
export function PinDialogHost() {
  const open = usePinStore((s) => s.open);
  const lockedUntil = usePinStore((s) => s.lockedUntil);
  const finish = usePinStore((s) => s.finish);

  const onVerify = async (pin: string): Promise<PinVerifyResult> => {
    try {
      await api.auth.verifyPin(pin);
      void queryClient.invalidateQueries({ queryKey: qk.me });
      finish(true);
      return { ok: true };
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.code === 'PIN_LOCKED' || (e.status === 423 && e.lockedUntil)) {
          return { ok: false, lockedUntil: e.lockedUntil ?? new Date(Date.now() + 15 * 60_000).toISOString() };
        }
        if (e.code === 'PIN_INVALID') return { ok: false, attemptsLeft: e.attemptsLeft };
        return { ok: false, message: e.message };
      }
      return { ok: false, message: 'Không kết nối được máy chủ.' };
    }
  };

  return <PinDialog open={open} lockedUntil={lockedUntil} onVerify={onVerify} onCancel={() => finish(false)} />;
}
