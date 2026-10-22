/**
 * Navigation bridge so non-React code (the API client callbacks) can route.
 * The router registers its navigate function at startup.
 */
type Navigate = (to: string, opts?: { replace?: boolean }) => void;

let navigateImpl: Navigate = (to) => {
  window.location.assign(to);
};

export function setNavigator(fn: Navigate): void {
  navigateImpl = fn;
}

export function navigateTo(to: string, opts?: { replace?: boolean }): void {
  navigateImpl(to, opts);
}

export function currentPath(): string {
  return window.location.pathname + window.location.search;
}
