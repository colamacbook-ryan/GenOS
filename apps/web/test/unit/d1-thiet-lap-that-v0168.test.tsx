/**
 * v0.1.68 (gói D1) — thiết lập nói thật: F003 (nguồn đã tắt không "sẵn sàng"), F038 (mời đội: danh sách rỗng không phải "xong"),
 * F079 (agent bước 8 nghe sẵn kênh đã nối — câu nói thật), F145 (vai không phải Owner mở /guide/n thấy khung "do Owner làm").
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { Provider, SetupState } from '@gen-harness/contracts';
import { GuideStepPage } from '../../src/guide/GuideStepPage';
import { providerReady } from '../../src/setup/phase2Model';
import { qk } from '../../src/lib/queries';
import { permissionsOf, type RoleCode } from '../mock-api';

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
const me = (role: RoleCode) => ({
  id: 'u', email: `${role}@genesis.local`, display_name: 'Anh Cơ', role: { code: role, name: role },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: permissionsOf(role),
});
const finished: SetupState = {
  finished: true, current_step: 12,
  steps: Array.from({ length: 12 }, (_, i) => ({ n: i + 1, key: `s${i + 1}`, title: `Bước ${i + 1}`, required: i + 1 <= 4 || i + 1 === 12, status: i + 1 <= 4 || i + 1 === 12 ? 'done' : 'skipped' })),
} as SetupState;

afterEach(() => vi.unstubAllGlobals());

function renderStep(n: number, role: RoleCode, handler: (url: string, init?: RequestInit) => unknown, seedMe = true) {
  const calls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(`${init?.method ?? 'GET'} ${url}`);
      if (url.includes('/auth/me')) return json(me(role));
      return json(handler(url, init));
    }),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (seedMe) qc.setQueryData(qk.me, me(role));
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/guide/${n}`]}>
        <Routes>
          <Route path="/guide" element={<p>Trang hướng dẫn</p>} />
          <Route path="/guide/:n" element={<GuideStepPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return calls;
}

describe('F003 — providerReady', () => {
  const P = (over: Partial<Provider>): Provider =>
    ({ id: 'p', kind: 'gemini', name: 'G', endpoint: null, failover_rank: 1, enabled: true, auth_state: 'ok', keys: [], models: [], ...over }) as Provider;
  it('nguồn đã tắt không bao giờ sẵn sàng (kể cả vừa gọi thử xanh hoặc CLI còn phiên)', () => {
    const okTest = { p: { ok: true, latency_ms: 5, error: null, models: ['m'] } } as never;
    expect(providerReady(P({}), okTest, false)).toBe(true);
    expect(providerReady(P({ enabled: false }), okTest, false)).toBe(false);
    expect(providerReady(P({ enabled: false, auth_state: 'ok' }), {}, false)).toBe(false);
    expect(providerReady(P({ kind: 'antigravity_cli', enabled: false }), {}, true)).toBe(false);
    expect(providerReady(P({ kind: 'antigravity_cli' }), {}, true)).toBe(true);
  });
});

describe('F145 — /guide/n cho vai không phải Owner', () => {
  it('Operator: khung "Việc này do Owner làm", không có "Thử lại", không gọi API chỉ Owner dùng', async () => {
    const calls = renderStep(5, 'operator', () => ({}));
    expect(await screen.findByText('Việc này do Owner làm')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Thử lại/ })).toBeNull();
    expect(screen.queryByText(/Không tải được dữ liệu/)).toBeNull();
    expect(screen.getByRole('link', { name: /Hướng dẫn thiết lập/ })).toHaveAttribute('href', '/guide');
    await new Promise((r) => setTimeout(r, 30));
    expect(calls.filter((c) => /\/setup\/|\/channels|\/refinery|\/rules/.test(c))).toEqual([]);
  });
});

describe('Review v0.1.68 (P3) — /guide/n không dựa vào bộ nhớ đệm /auth/me có sẵn', () => {
  it('Operator, bộ nhớ đệm vai TRỐNG (tải thẳng /guide/5): vẫn thấy khung "do Owner làm", không gọi API chỉ Owner dùng', async () => {
    const calls = renderStep(5, 'operator', () => ({}), false);
    expect(await screen.findByText('Việc này do Owner làm')).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 30));
    expect(calls.filter((c) => /\/setup\/|\/channels|\/refinery|\/rules/.test(c))).toEqual([]);
    expect(screen.queryByText(/Không tải được dữ liệu/)).toBeNull();
  });

  it('Owner, bộ nhớ đệm vai trống: chờ biết vai rồi mới tải form (không gọi /setup/state trước khi biết)', async () => {
    const calls = renderStep(10, 'owner', () => finished, false);
    expect(await screen.findByRole('button', { name: /Tiếp tục/ })).toBeInTheDocument();
    const firstSetup = calls.findIndex((c) => /\/setup\//.test(c));
    const meCall = calls.findIndex((c) => /\/auth\/me/.test(c));
    expect(meCall).toBeGreaterThanOrEqual(0);
    expect(firstSetup).toBeGreaterThan(meCall);
  });
});

describe('F038 / F079 — bước 10 và bước 8 nói thật', () => {
  it('bước 10: danh sách rỗng KHÔNG cho "Tiếp tục"; điền đủ một người thì được', async () => {
    const user = userEvent.setup();
    renderStep(10, 'owner', () => finished);
    const next = await screen.findByRole('button', { name: /Tiếp tục/ });
    expect(next).toBeDisabled();
    expect(screen.getByText(/Thêm ít nhất một người/)).toBeInTheDocument();
    // Hướng dẫn không có nút "Để sau" ⇒ gợi ý chỉ lối ra thật là "Quay lại" (không chỉ vào nút không tồn tại).
    expect(screen.getByText(/chưa muốn mời thì bấm "Quay lại"/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Để sau' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Thêm người/ }));
    expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeDisabled();           // dòng trống vẫn không tính
    await user.type(screen.getByLabelText('Tên hiển thị'), 'Lan');
    await user.type(screen.getByLabelText('Email'), 'lan@example.vn');
    await waitFor(() => expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeEnabled());
  });

  it('bước 8: nói đúng số kênh agent nghe (có kênh / chưa có kênh)', async () => {
    const user = userEvent.setup();
    for (const [listening, want] of [[2, /nghe sẵn 2 kênh/], [0, /chưa nghe kênh nào/]] as const) {
      const { unmount } = (() => {
        renderStep(8, 'owner', (_url, init) =>
          init?.method === 'PUT' ? { ...finished, agent: { id: 'a', name: 'Trợ lý', try_reply: null, try_error: null, listening_channels: listening } } : finished);
        return { unmount: () => undefined };
      })();
      await user.click(await screen.findByRole('button', { name: /Tiếp tục/ }));
      expect(await screen.findByTestId('step8-listening')).toHaveTextContent(want);
      unmount();
      document.body.innerHTML = '';
    }
  });
});
