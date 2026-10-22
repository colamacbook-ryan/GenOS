import type { Me } from '@gen-harness/contracts';
import { ALL_ONLY_PERMS } from '@gen-harness/contracts';
import { useMe } from './queries';

/**
 * Capability check from `Me.permissions` ({perm: all|team|assigned|none}).
 * The backend is the authority; the UI only hides or disables write actions.
 * F-58: permissions in ALL_ONLY_PERMS (system.manage) count only at scope `all` — same rule as the server.
 */
export function can(me: Pick<Me, 'permissions'> | undefined | null, perm: string): boolean {
  const scope = me?.permissions?.[perm];
  if (ALL_ONLY_PERMS.includes(perm)) return scope === 'all';
  return !!scope && scope !== 'none';
}

export function useCan(perm: string): boolean {
  const me = useMe();
  return can(me.data, perm);
}

/** Org timezone for time formatting (falls back to Asia/Ho_Chi_Minh). */
export function useOrgTimezone(): string {
  const me = useMe();
  return me.data?.org?.timezone || 'Asia/Ho_Chi_Minh';
}
