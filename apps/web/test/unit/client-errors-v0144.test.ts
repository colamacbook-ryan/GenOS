/**
 * v0.1.44 (F-4b) — báo lỗi giao diện (`POST /api/v1/client-errors`): thân đúng hợp đồng, cắt độ dài, khử trùng theo
 * errorId, tối đa 5 lần/phút, fetch lỗi không bao giờ ném; không đi qua apiClient (không chuyển /login khi 401).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, CLIENT_ERROR_LIMITS } from '@gen-harness/contracts';
import {
  CLIENT_ERRORS_PER_MINUTE,
  MAX_MESSAGE,
  MAX_STACK,
  clientErrorBody,
  reportClientError,
  resetClientErrorsForTest,
} from '../../src/lib/clientErrors';

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  resetClientErrorsForTest();
  fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true, request_id: 'srv' }), { status: 202 }));
  vi.stubGlobal('fetch', fetchMock);
  document.cookie = 'gh_csrf=csrf-123; path=/';
  window.history.pushState({}, '', '/connections?token=bi-mat');
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  document.cookie = 'gh_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
});

const bodyOf = (i = 0) => JSON.parse(String((fetchMock.mock.calls[i][1] as RequestInit).body)) as Record<string, unknown>;

describe('reportClientError', () => {
  it('POST /api/v1/client-errors, keepalive, credentials include, CSRF header; thân đúng hợp đồng', () => {
    const err = new TypeError('x is undefined');
    expect(reportClientError({ errorId: 'ERR-ABCDE-1234', error: err, componentStack: '\n    at Card\n    at Page' })).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/client-errors');
    expect(init.method).toBe('POST');
    expect(init.keepalive).toBe(true);
    expect(init.credentials).toBe('include');
    expect((init.headers as Record<string, string>)['X-CSRF-Token']).toBe('csrf-123');
    const b = bodyOf();
    expect(b.error_id).toBe('ERR-ABCDE-1234');
    expect(b.message).toBe('x is undefined');
    expect(b.name).toBe('TypeError');
    expect(typeof b.stack).toBe('string');
    expect(b.component_stack).toContain('at Card');
    // Đường dẫn KHÔNG kèm query (có thể chứa mã/khoá).
    expect(b.path).toBe('/connections');
    expect(b).not.toHaveProperty('request_id');
    for (const k of Object.keys(b)) expect(['error_id', 'message', 'name', 'stack', 'component_stack', 'path', 'request_id', 'app_version']).toContain(k);
  });

  it('request_id lấy từ ApiError (Mã yêu cầu)', () => {
    const e = new ApiError(500, { code: 'INTERNAL', request_id: 'r0123456789abcde' } as never);
    reportClientError({ errorId: 'ERR-ABCDE-0001', error: e });
    expect(bodyOf().request_id).toBe('r0123456789abcde');
  });

  it('cắt độ dài message/stack/path', () => {
    const e = new Error('m'.repeat(5000));
    e.stack = 's'.repeat(50_000);
    const b = clientErrorBody({ errorId: 'ERR-ABCDE-0002', error: e, path: '/' + 'p'.repeat(1000) });
    expect(b.message.length).toBe(MAX_MESSAGE);
    expect(b.stack!.length).toBe(MAX_STACK);
    // Không vượt giới hạn server (ClientErrorIn: stack ≤ 4000) — vượt là 422, mất cả báo lỗi.
    expect(MAX_STACK).toBe(CLIENT_ERROR_LIMITS.stack);
    expect(b.stack!.length).toBeLessThanOrEqual(4000);
    for (const [k, n] of Object.entries(CLIENT_ERROR_LIMITS)) {
      const v = (b as unknown as Record<string, unknown>)[k];
      if (typeof v === 'string') expect(v.length, k).toBeLessThanOrEqual(n);
    }
    expect(b.path.length).toBeLessThanOrEqual(300);
    // Không phải Error: chuỗi / đối tượng → message là chuỗi.
    expect(clientErrorBody({ errorId: 'ERR-A-0003', error: 'hỏng', path: '/' }).message).toBe('hỏng');
    expect(clientErrorBody({ errorId: 'ERR-A-0004', error: { a: 1 }, path: '/' }).message).toBe('{"a":1}');
    const cyc: Record<string, unknown> = {};
    cyc.self = cyc;
    expect(typeof clientErrorBody({ errorId: 'ERR-A-0005', error: cyc, path: '/' }).message).toBe('string');
  });

  it('khử trùng theo errorId (và cùng một đối tượng lỗi)', () => {
    const e = new Error('một lần');
    expect(reportClientError({ errorId: 'ERR-ABCDE-1111', error: e })).toBe(true);
    expect(reportClientError({ errorId: 'ERR-ABCDE-1111', error: new Error('khác') })).toBe(false);
    expect(reportClientError({ errorId: 'ERR-ABCDE-2222', error: e })).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it(`tối đa ${CLIENT_ERRORS_PER_MINUTE} lần/phút; qua phút sau gửi lại được`, () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-10-03T01:00:00Z'));
    for (let i = 0; i < 8; i += 1) reportClientError({ errorId: `ERR-ABCDE-${String(i).padStart(4, '0')}`, error: new Error(`lỗi ${i}`) });
    expect(fetchMock).toHaveBeenCalledTimes(5);
    vi.setSystemTime(new Date('2026-10-03T01:01:01Z'));
    expect(reportClientError({ errorId: 'ERR-ABCDE-9999', error: new Error('sau một phút') })).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(6);
  });

  it('fetch ném đồng bộ / promise bị từ chối / không có fetch → không ném, không vòng lặp', async () => {
    vi.stubGlobal('fetch', vi.fn(() => {
      throw new Error('mạng hỏng');
    }));
    expect(() => reportClientError({ errorId: 'ERR-ABCDE-3333', error: new Error('a') })).not.toThrow();
    const rejected = vi.fn(() => Promise.reject(new TypeError('Failed to fetch')));
    vi.stubGlobal('fetch', rejected);
    const onUnhandled = vi.fn();
    process.on('unhandledRejection', onUnhandled);
    expect(reportClientError({ errorId: 'ERR-ABCDE-4444', error: new Error('b') })).toBe(true);
    await new Promise((r) => setTimeout(r, 10));
    process.off('unhandledRejection', onUnhandled);
    expect(onUnhandled).not.toHaveBeenCalled();
    expect(rejected).toHaveBeenCalledTimes(1);
    vi.stubGlobal('fetch', undefined);
    expect(() => reportClientError({ errorId: 'ERR-ABCDE-5555', error: new Error('c') })).not.toThrow();
    expect(reportClientError({ errorId: '', error: new Error('d') })).toBe(false);
  });
});
