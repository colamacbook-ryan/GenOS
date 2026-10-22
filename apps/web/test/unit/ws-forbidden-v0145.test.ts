import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { WS_ORIGIN_TEXT, handleWsForbidden } from '../../src/lib/realtime';
import { resetClientErrorsForTest } from '../../src/lib/clientErrors';
import { useToasts } from '../../src/lib/toast';

/** Sửa review v0.1.45 (F-55): WS đóng 4403 'origin' không còn im lặng — toast kèm Mã lỗi + báo lỗi về máy chủ. */
describe('WebSocket 4403', () => {
  let posts: Array<{ url: string; body: Record<string, unknown> }> = [];
  beforeEach(() => {
    posts = [];
    resetClientErrorsForTest();
    useToasts.setState({ toasts: [] });
    vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      posts.push({ url: String(url), body: JSON.parse(String(init?.body ?? '{}')) });
      return new Response(null, { status: 204 });
    }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("'origin': toast cảnh báo có Mã lỗi và gửi POST /client-errors cùng mã đó", () => {
    handleWsForbidden('origin');
    const toasts = useToasts.getState().toasts;
    expect(toasts).toHaveLength(1);
    expect(toasts[0].tone).toBe('warn');
    expect(toasts[0].text).toContain(WS_ORIGIN_TEXT);
    const id = /Mã lỗi: (ERR-[A-Z0-9-]+)/.exec(toasts[0].text)?.[1];
    expect(id).toBeTruthy();
    expect(posts).toHaveLength(1);
    expect(posts[0].url).toContain('/client-errors');
    expect(JSON.stringify(posts[0].body)).toContain(id as string);
    expect(JSON.stringify(posts[0].body)).toContain('4403 origin');
  });

  it("'password_change_required': không toast, không báo lỗi (HTTP kế tiếp tự đưa tới màn đổi mật khẩu)", () => {
    handleWsForbidden('password_change_required');
    expect(useToasts.getState().toasts).toHaveLength(0);
    expect(posts).toHaveLength(0);
  });
});
