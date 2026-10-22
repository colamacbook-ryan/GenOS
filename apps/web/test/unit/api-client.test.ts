import { describe, expect, it, vi } from 'vitest';
import { ApiError, createApiClient, createEndpoints, PinCancelledError, readCookie } from '@gen-harness/contracts';

type Call = { url: string; init: RequestInit };

function json(status: number, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** fetch stub answering from a queue; records every call. */
function stubFetch(...responses: Array<Response | (() => Response)>) {
  const calls: Call[] = [];
  const fn = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(url), init: init ?? {} });
    const next = responses.shift();
    if (!next) throw new Error('unexpected fetch ' + String(url));
    return typeof next === 'function' ? next() : next;
  });
  return { fetch: fn as unknown as typeof fetch, calls };
}

const header = (c: Call, name: string) => (c.init.headers as Record<string, string>)[name];

describe('readCookie', () => {
  it('reads and decodes a cookie by name', () => {
    expect(readCookie('gh_csrf', 'a=1; gh_csrf=abc%3D%3D; b=2')).toBe('abc==');
    expect(readCookie('gh_csrf', 'a=1')).toBeNull();
  });
});

describe('API client', () => {
  it('sends credentials and no CSRF header on GET', async () => {
    const { fetch, calls } = stubFetch(json(200, { status: 'ok' }));
    const api = createEndpoints(createApiClient({ fetch, getCsrfToken: () => 'tok' }));
    await expect(api.shell.health()).resolves.toEqual({ status: 'ok' });
    expect(calls[0].url).toBe('/api/v1/health');
    expect(calls[0].init.credentials).toBe('include');
    expect(header(calls[0], 'X-CSRF-Token')).toBeUndefined();
  });

  it('echoes the gh_csrf cookie in X-CSRF-Token on writes, with an Idempotency-Key', async () => {
    document.cookie = 'gh_csrf=csrf-from-cookie; path=/';
    const { fetch, calls } = stubFetch(json(200, { id: 'u1' }));
    const api = createEndpoints(createApiClient({ fetch }));
    await api.auth.login({ email: 'a@b.c', password: 'x' });
    expect(calls[0].init.method).toBe('POST');
    expect(header(calls[0], 'X-CSRF-Token')).toBe('csrf-from-cookie');
    expect(header(calls[0], 'Idempotency-Key')).toBeTruthy();
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ email: 'a@b.c', password: 'x' });
  });

  it('parses RFC 7807 problems into ApiError', async () => {
    const { fetch } = stubFetch(json(403, { status: 403, code: 'FORBIDDEN', title: 'Không có quyền' }));
    const client = createApiClient({ fetch });
    const err = (await client.request('/plugins').catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(403);
    expect(err.code).toBe('FORBIDDEN');
    expect(err.message).toBe('Không có quyền');
  });

  it('401 UNAUTHENTICATED calls onUnauthenticated (→ /login)', async () => {
    const onUnauthenticated = vi.fn();
    const { fetch } = stubFetch(json(401, { status: 401, code: 'UNAUTHENTICATED' }));
    const api = createEndpoints(createApiClient({ fetch, onUnauthenticated }));
    await expect(api.auth.me()).rejects.toMatchObject({ status: 401 });
    expect(onUnauthenticated).toHaveBeenCalledTimes(1);
  });

  it('401 INVALID_CREDENTIALS on login does not redirect', async () => {
    const onUnauthenticated = vi.fn();
    const { fetch } = stubFetch(json(401, { status: 401, code: 'INVALID_CREDENTIALS' }));
    const api = createEndpoints(createApiClient({ fetch, onUnauthenticated }));
    await expect(api.auth.login({ email: 'a@b.c', password: 'bad' })).rejects.toMatchObject({ code: 'INVALID_CREDENTIALS' });
    expect(onUnauthenticated).not.toHaveBeenCalled();
  });

  it('428 SETUP_REQUIRED calls onSetupRequired (→ /setup), except from the wizard itself', async () => {
    const onSetupRequired = vi.fn();
    const { fetch } = stubFetch(
      json(428, { status: 428, code: 'SETUP_REQUIRED' }),
      json(428, { status: 428, code: 'SETUP_REQUIRED' }),
    );
    const api = createEndpoints(createApiClient({ fetch, onSetupRequired }));
    await expect(api.shell.navigation()).rejects.toMatchObject({ status: 428 });
    expect(onSetupRequired).toHaveBeenCalledTimes(1);
    await expect(api.setup.state()).rejects.toMatchObject({ status: 428 });
    expect(onSetupRequired).toHaveBeenCalledTimes(1);
  });

  it('423 PIN_REQUIRED opens the PIN dialog, then retries the same request with the same Idempotency-Key', async () => {
    const requestPin = vi.fn(async () => true);
    const { fetch, calls } = stubFetch(
      json(423, { status: 423, code: 'PIN_REQUIRED' }),
      json(200, { package: '@gen/x', enabled: false }),
    );
    const api = createEndpoints(createApiClient({ fetch, requestPin, getCsrfToken: () => 't' }));
    await expect(api.plugins.toggle('@gen/x', false)).resolves.toMatchObject({ enabled: false });
    expect(requestPin).toHaveBeenCalledTimes(1);
    expect(calls).toHaveLength(2);
    expect(calls[1].url).toBe(calls[0].url);
    expect(calls[1].init.body).toBe(calls[0].init.body);
    expect(header(calls[1], 'Idempotency-Key')).toBe(header(calls[0], 'Idempotency-Key'));
    expect(header(calls[1], 'X-CSRF-Token')).toBe('t');
  });

  it('cancelling the PIN dialog rejects with PinCancelledError and does not retry', async () => {
    const requestPin = vi.fn(async () => false);
    const { fetch, calls } = stubFetch(json(423, { status: 423, code: 'PIN_REQUIRED' }));
    const api = createEndpoints(createApiClient({ fetch, requestPin }));
    await expect(api.plugins.remove('@gen/x')).rejects.toBeInstanceOf(PinCancelledError);
    expect(calls).toHaveLength(1);
  });

  it('concurrent 423s share one PIN prompt', async () => {
    let resolvePin!: (v: boolean) => void;
    const requestPin = vi.fn(() => new Promise<boolean>((r) => (resolvePin = r)));
    const { fetch } = stubFetch(
      json(423, { status: 423, code: 'PIN_REQUIRED' }),
      json(423, { status: 423, code: 'PIN_REQUIRED' }),
      json(200, { ok: 1 }),
      json(200, { ok: 2 }),
    );
    const client = createApiClient({ fetch, requestPin });
    const a = client.request('/a', { method: 'POST' });
    const b = client.request('/b', { method: 'POST' });
    await vi.waitFor(() => expect(requestPin).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));
    resolvePin(true);
    await expect(Promise.all([a, b])).resolves.toHaveLength(2);
    expect(requestPin).toHaveBeenCalledTimes(1);
  });

  it('PIN verify never loops into the PIN flow and exposes attempts_left / locked_until', async () => {
    const requestPin = vi.fn(async () => true);
    const onUnauthenticated = vi.fn();
    const until = '2026-09-23T15:30:00Z';
    const { fetch } = stubFetch(
      json(401, { status: 401, code: 'PIN_INVALID', attempts_left: 3 }),
      json(423, { status: 423, code: 'PIN_LOCKED', detail: { locked_until: until } }),
    );
    const api = createEndpoints(createApiClient({ fetch, requestPin, onUnauthenticated }));
    const e1 = await api.auth.verifyPin('000000').catch((e) => e);
    expect(e1.attemptsLeft).toBe(3);
    const e2 = await api.auth.verifyPin('000000').catch((e) => e);
    expect(e2.code).toBe('PIN_LOCKED');
    expect(e2.lockedUntil).toBe(until);
    expect(requestPin).not.toHaveBeenCalled();
    expect(onUnauthenticated).not.toHaveBeenCalled();
  });

  it('exposes 422 field errors', async () => {
    const { fetch } = stubFetch(json(422, { status: 422, errors: { password: 'ngắn quá' } }));
    const api = createEndpoints(createApiClient({ fetch }));
    const e = await api.setup
      .step2({ token: 't', display_name: 'a', email: 'a@b.c', password: 'x', pin: '1', pin_confirm: '1' })
      .catch((err) => err);
    expect(e.fieldErrors).toEqual({ password: 'ngắn quá' });
  });

  it('maps network failures to status 0', async () => {
    const fetch = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    }) as unknown as typeof globalThis.fetch;
    const e = (await createApiClient({ fetch }).request('/health').catch((err: unknown) => err)) as ApiError;
    expect(e).toBeInstanceOf(ApiError);
    expect(e.status).toBe(0);
  });

  it('returns undefined for 204', async () => {
    const { fetch } = stubFetch(new Response(null, { status: 204 }));
    await expect(createEndpoints(createApiClient({ fetch })).auth.logout()).resolves.toBeUndefined();
  });
});
