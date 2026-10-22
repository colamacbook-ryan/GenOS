import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

// jsdom không có URL.createObjectURL / revokeObjectURL. lib/download.ts gọi revokeObjectURL qua setTimeout 1 giây,
// nên nếu chỉ stub URL trong 1 test thì sau vi.unstubAllGlobals() hẹn giờ vẫn chạy (ở test sau) trên URL thật
// ⇒ "URL.revokeObjectURL is not a function" (lỗi chưa bắt, CI đỏ tuỳ tốc độ máy). Bù hẳn trên URL thật cho mọi test.
if (typeof URL.createObjectURL !== 'function') {
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, writable: true, value: () => 'blob:mock' });
}
if (typeof URL.revokeObjectURL !== 'function') {
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, writable: true, value: () => undefined });
}

afterEach(() => {
  cleanup();
});
