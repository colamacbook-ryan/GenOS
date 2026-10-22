import { useEffect, useState } from 'react';

/** Giá trị trễ `ms` mili giây sau lần đổi cuối — để ô tìm kiếm không gọi máy chủ ở từng phím gõ. */
export function useDebouncedValue<T>(value: T, ms = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}
