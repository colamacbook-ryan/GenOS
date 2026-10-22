import { useCallback, useSyncExternalStore } from 'react';

/**
 * v0.1.59 (G2) — media query theo `window.matchMedia`, dùng cho các thành phần kit đổi CẤU TRÚC theo cỡ màn (DataTable thành
 * danh sách xếp chồng dưới 760px, thanh trái ↔ thanh dưới của Mặt tiền). Chỉ đổi cấu trúc ở đây; mọi thứ còn lại do CSS lo.
 *
 * Không có `matchMedia` (jsdom, máy chủ) ⇒ `false` — tức là "chưa đạt điểm gãy" (bố cục điện thoại). Đọc đồng bộ ngay lần
 * vẽ đầu nên không nháy bố cục. Mọi truy cập `matchMedia` bọc try/catch (trình duyệt riêng tư, môi trường test lạ).
 */
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

/**
 * Điều hướng trong ứng dụng cho các thành phần kit có `to` (StatTile, ListRow…): `@gen-harness/ui` KHÔNG phụ thuộc
 * react-router, nên ứng dụng đăng ký hàm chuyển trang MỘT lần lúc tạo router (apps/web/src/router.tsx →
 * `setKitNavigator((to) => router.navigate(to))`). Chưa đăng ký (test, trang rời) ⇒ liên kết chạy như thẻ <a> thường.
 */
type KitNavigate = (to: string) => void;
let kitNavigator: KitNavigate | null = null;

export function setKitNavigator(fn: KitNavigate | null): void {
  kitNavigator = fn;
}

/** `true` nếu đã chuyển trang trong ứng dụng (người gọi nên `preventDefault`); `false` ⇒ để trình duyệt tự xử lý. */
export function kitNavigate(to: string): boolean {
  if (!kitNavigator) return false;
  kitNavigator(to);
  return true;
}
