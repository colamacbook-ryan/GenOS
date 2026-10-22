import { useCallback, useState } from 'react';

/**
 * Validation-on-blur bookkeeping: an error shows once its field was left
 * (or after a submit attempt); server 422 errors show until the field changes.
 */
export function useFieldErrors<K extends string>(clientErrors: Partial<Record<K, string>>) {
  const [touched, setTouched] = useState<Partial<Record<K, boolean>>>({});
  const [server, setServer] = useState<Partial<Record<K, string>>>({});
  const [submitted, setSubmitted] = useState(false);

  const blur = useCallback((k: K) => setTouched((t) => ({ ...t, [k]: true })), []);
  const changed = useCallback(
    (k: K) =>
      setServer((s) => {
        if (!(k in s)) return s;
        const next = { ...s };
        delete next[k];
        return next;
      }),
    [],
  );
  const errorOf = (k: K): string | null =>
    server[k] ?? ((touched[k] || submitted) && clientErrors[k] ? (clientErrors[k] as string) : null);

  return { blur, changed, errorOf, setServer, setSubmitted, hasServerErrors: Object.keys(server).length > 0 };
}
