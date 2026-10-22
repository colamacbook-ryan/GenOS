import { useCallback, useSyncExternalStore } from 'react';

/** Điểm gãy điện thoại (B4) — khớp `@media (max-width: 760px)` trong shell.css / gen.css. */
export const MOBILE_QUERY = '(max-width: 760px)';

function media(query: string): MediaQueryList | null {
  try {
    return typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(query) : null;
  } catch {
    return null;
  }
}

export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (cb: () => void) => {
      const mq = media(query);
      mq?.addEventListener?.('change', cb);
      return () => mq?.removeEventListener?.('change', cb);
    },
    [query],
  );
  return useSyncExternalStore(subscribe, () => !!media(query)?.matches, () => false);
}

export const useIsMobile = () => useMediaQuery(MOBILE_QUERY);
