import { describe, expect, it, vi } from 'vitest';
import { ApiError, createApiClient } from '@gen-harness/contracts';
import { errorText } from '../../src/lib/errorText';

/** v0.1.35 (F-43/F-14): `detail` luôn là chuỗi; PIN_LOCKED đọc `locked_until` ở khoá ngoài; 500 INTERNAL thân thiện. */

function problem(status: number, body: unknown): typeof fetch {
  return vi.fn(
    async () =>
      new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/problem+json' } }),
  ) as unknown as typeof fetch;
}

describe('ApiError v0.1.35', () => {
  it('đọc locked_until từ khoá ngoài khi detail là chuỗi', async () => {
    const until = '2026-10-02T08:15:00Z';
    const fetch = problem(423, {
      type: 'https://gen-harness.local/errors/pin_locked',
      title: 'Mã PIN đang bị khoá do nhập sai nhiều lần',
      status: 423,
      code: 'PIN_LOCKED',
      detail: `Thử lại sau ${until}`,
      locked_until: until,
    });
    const err = (await createApiClient({ fetch }).request('/auth/pin/verify').catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe('PIN_LOCKED');
    expect(err.lockedUntil).toBe(until);
    expect(err.message).toBe(`Thử lại sau ${until}`);
    // errorText: nói rõ PIN bị khoá + giờ địa phương theo múi giờ tổ chức, KHÔNG phải ISO UTC thô.
    const text = errorText(err, 'Asia/Ho_Chi_Minh');
    expect(text).toBe('Mã PIN đang bị khoá do nhập sai nhiều lần. Thử lại sau 02/10 15:15:00.');
    expect(text).not.toContain('T08:15');
  });

  it('PIN_LOCKED máy chủ mới (detail "Thử lại sau ít phút") → title + giờ từ locked_until; thiếu giờ → "ít phút"', () => {
    const e = new ApiError(423, {
      code: 'PIN_LOCKED',
      title: 'Mã PIN đang bị khoá do nhập sai nhiều lần',
      detail: 'Thử lại sau ít phút',
      locked_until: '2026-10-02T08:15:00Z',
    });
    expect(errorText(e, 'UTC')).toBe('Mã PIN đang bị khoá do nhập sai nhiều lần. Thử lại sau 02/10 08:15:00.');
    const noTime = new ApiError(423, { code: 'PIN_LOCKED', detail: 'Thử lại sau ít phút' });
    expect(errorText(noTime)).toBe('Mã PIN đang bị khoá do nhập sai nhiều lần. Thử lại sau ít phút.');
  });

  it('ưu tiên khoá ngoài hơn detail (kể cả detail cũ dạng đối tượng)', () => {
    const outer = '2026-10-02T09:00:00Z';
    const e = new ApiError(423, {
      code: 'PIN_LOCKED',
      title: 'Mã PIN đang bị khoá',
      detail: { locked_until: '2020-01-01T00:00:00Z' },
      locked_until: outer,
    });
    expect(e.lockedUntil).toBe(outer);
    // Máy chủ cũ: chỉ có detail đối tượng/chuỗi — vẫn đọc được.
    expect(new ApiError(423, { code: 'PIN_LOCKED', detail: { locked_until: outer } }).lockedUntil).toBe(outer);
    expect(new ApiError(423, { code: 'PIN_LOCKED', detail: `Thử lại sau ${outer}` }).lockedUntil).toBe(outer);
    expect(new ApiError(423, { code: 'PIN_LOCKED', detail: null }).lockedUntil).toBeNull();
  });

  it('500 INTERNAL có detail chuỗi → errorText thân thiện, có mã lỗi, không [object Object]', async () => {
    const fetch = problem(500, {
      type: 'https://gen-harness.local/errors/internal',
      title: 'Hệ thống gặp lỗi khi xử lý yêu cầu — đã ghi nhật ký',
      status: 500,
      code: 'INTERNAL',
      detail: 'Mã lỗi 3fa9c0de — gửi mã này cho người hỗ trợ',
      error_id: '3fa9c0de',
    });
    const err = await createApiClient({ fetch })
      .request('/users')
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe('INTERNAL');
    const text = errorText(err);
    expect(typeof text).toBe('string');
    // Có CẢ câu dễ hiểu (title) lẫn mã lỗi — detail chỉ chứa mã nên không được thay chỗ title.
    expect(text).toBe('Hệ thống gặp lỗi khi xử lý yêu cầu — đã ghi nhật ký. Mã lỗi 3fa9c0de — gửi mã này cho người hỗ trợ.');
    expect(text).not.toContain('[object Object]');
  });

  it('500 INTERNAL không có detail → dùng title, vẫn là chuỗi', () => {
    const e = new ApiError(500, { code: 'INTERNAL', title: 'Hệ thống gặp lỗi khi xử lý yêu cầu — đã ghi nhật ký', detail: null });
    expect(errorText(e)).toBe('Hệ thống gặp lỗi khi xử lý yêu cầu — đã ghi nhật ký');
  });
});
