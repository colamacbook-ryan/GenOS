/** B5: mã lỗi ngắn cho người dùng đọc/chép (`ERR-<thời điểm base36>-<ngẫu nhiên>`), ghi kèm vào console. */
export function newErrorId(now: number = Date.now(), rand: () => number = Math.random): string {
  const t = now.toString(36).toUpperCase().slice(-5);
  const r = Math.floor(rand() * 36 ** 4)
    .toString(36)
    .toUpperCase()
    .padStart(4, '0');
  return `ERR-${t}-${r}`;
}
