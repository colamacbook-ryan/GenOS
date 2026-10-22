/** Tham số URL hiện tại → `filters` của góc nhìn (bộ lọc, tab, mục mở rộng của màn). */
export function searchToFilters(search: string): Record<string, string> {
  const out: Record<string, string> = {};
  new URLSearchParams(search).forEach((v, k) => {
    out[k] = v;
  });
  return out;
}

export function viewHref(screen: string, filters: Record<string, string>): string {
  const q = new URLSearchParams(filters).toString();
  return `/${screen}${q ? `?${q}` : ''}`;
}
