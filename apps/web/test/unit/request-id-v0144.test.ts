/**
 * v0.1.44 (F-4b) — "Mã yêu cầu" (X-Request-ID) cạnh "Mã lỗi": ApiError.requestId đọc từ thân problem+json hoặc từ
 * header (kể cả 502 không phải JSON từ proxy); errorDetail/errorText INTERNAL có mã yêu cầu; ErrorView hiện mã.
 */
import { createElement } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ApiError, createApiClient } from '@gen-harness/contracts';
import { errorDetail, errorText } from '../../src/lib/errorText';
import { ErrorView } from '../../src/shell/ErrorPage';

function client(res: () => Response) {
  return createApiClient({ fetch: vi.fn(async () => res()) as unknown as typeof fetch, getCsrfToken: () => null });
}

async function failOf(res: () => Response): Promise<ApiError> {
  try {
    await client(res).request('/x');
  } catch (e) {
    if (e instanceof ApiError) return e;
    throw e;
  }
  throw new Error('không ném lỗi');
}

describe('ApiError.requestId', () => {
  it('đọc request_id trong thân problem+json (ưu tiên hơn header)', async () => {
    const e = await failOf(
      () =>
        new Response(JSON.stringify({ status: 409, code: 'CONFLICT', title: 'Trùng', request_id: 'body0123456789ab' }), {
          status: 409,
          headers: { 'Content-Type': 'application/problem+json', 'X-Request-ID': 'head0123456789ab' },
        }),
    );
    expect(e.requestId).toBe('body0123456789ab');
  });

  it('thân không có request_id → lấy từ header X-Request-ID', async () => {
    const e = await failOf(
      () => new Response(JSON.stringify({ status: 404, code: 'NOT_FOUND', title: 'Không có' }), { status: 404, headers: { 'X-Request-ID': 'a1b2c3d4e5f60718' } }),
    );
    expect(e.code).toBe('NOT_FOUND');
    expect(e.requestId).toBe('a1b2c3d4e5f60718');
  });

  it('502 từ proxy (thân HTML, không JSON) vẫn có mã yêu cầu từ header; thân rỗng cũng vậy', async () => {
    const e = await failOf(
      () => new Response('<html><body>502 Bad Gateway</body></html>', { status: 502, statusText: 'Bad Gateway', headers: { 'Content-Type': 'text/html', 'X-Request-ID': 'proxy-req-0001' } }),
    );
    expect(e.status).toBe(502);
    expect(e.requestId).toBe('proxy-req-0001');
    expect(typeof e.message).toBe('string');
    const empty = await failOf(() => new Response(null, { status: 503, headers: { 'X-Request-ID': 'empty0123456789a' } }));
    expect(empty.requestId).toBe('empty0123456789a');
  });

  it('header sai khuôn hoặc không có → null (không đưa chuỗi lạ lên giao diện)', async () => {
    const bad = await failOf(() => new Response('{}', { status: 500, headers: { 'X-Request-ID': '<script>x</script>' } }));
    expect(bad.requestId).toBeNull();
    const none = await failOf(() => new Response('{}', { status: 500 }));
    expect(none.requestId).toBeNull();
    expect(new ApiError(500, { request_id: 42 } as never).requestId).toBeNull();
  });
});

describe('errorDetail / errorText với mã yêu cầu', () => {
  const internal = new ApiError(500, { code: 'INTERNAL', title: 'Hệ thống gặp lỗi', error_id: 'E-77', request_id: 'r0123456789abcde' } as never);

  it('errorDetail thêm "request_id <id>"', () => {
    expect(errorDetail(internal)).toBe('HTTP 500 · INTERNAL · error_id E-77 · request_id r0123456789abcde');
    const plain = new ApiError(409, { code: 'X', request_id: 'r0123456789abcde' } as never);
    expect(errorDetail(plain)).toBe('HTTP 409 · X · request_id r0123456789abcde');
  });

  it('INTERNAL: "Mã lỗi <error_id> · Mã yêu cầu <request_id>"', () => {
    expect(errorText(internal)).toBe('Hệ thống gặp lỗi. Mã lỗi E-77 · Mã yêu cầu r0123456789abcde — gửi các mã này cho người hỗ trợ.');
    const onlyReq = new ApiError(500, { code: 'INTERNAL', title: 'Hệ thống gặp lỗi', request_id: 'r0123456789abcde' } as never);
    expect(errorText(onlyReq)).toBe('Hệ thống gặp lỗi. Mã yêu cầu r0123456789abcde — gửi mã này cho người hỗ trợ.');
    const old = new ApiError(500, { code: 'INTERNAL', title: 'Hệ thống gặp lỗi', error_id: 'E-77' } as never);
    expect(errorText(old)).toBe('Hệ thống gặp lỗi. Mã lỗi E-77 — gửi mã này cho người hỗ trợ.');
  });
});

describe('ErrorView', () => {
  it('lỗi API có mã yêu cầu → hiện "Mã yêu cầu" cạnh "Mã lỗi" (data-testid=request-id), chỉ chuỗi', () => {
    const err = new ApiError(502, { detail: { nested: true }, request_id: 'proxy-req-0001' } as never, 'Bad Gateway');
    const { container } = render(createElement(ErrorView, { errorId: 'ERR-ABCDE-1234', error: err }));
    expect(screen.getByTestId('error-id')).toHaveTextContent('ERR-ABCDE-1234');
    expect(screen.getByTestId('request-id')).toHaveTextContent('proxy-req-0001');
    expect(screen.getByTestId('error-id').parentElement).toContainElement(screen.getByTestId('request-id'));
    expect(container.textContent).toContain('Mã yêu cầu: proxy-req-0001');
    expect(container.textContent).not.toContain('[object Object]');
  });

  it('lỗi thường (không phải ApiError) → không có dòng mã yêu cầu', () => {
    render(createElement(ErrorView, { errorId: 'ERR-ABCDE-1234', error: new TypeError('x is undefined') }));
    expect(screen.queryByTestId('request-id')).toBeNull();
  });
});
