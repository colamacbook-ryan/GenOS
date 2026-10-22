import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { Provider, ProviderTestResult } from '@gen-harness/contracts';
import { BrainTab } from '../../src/screens/system/BrainTab';
import { qk } from '../../src/lib/queries';

/**
 * v0.1.39 (F-78): Jev không bắt buộc — nút "Kiểm tra 1 lần" (có kết quả thì ẩn), kiểm tra lỗi thì thẻ thu vào
 * "Nâng cao" thay vì để lỗi đỏ giữa tab Bộ não AI.
 * v0.1.43 (F-30): thẻ Jev LUÔN nằm trong "Nâng cao — Jev (không bắt buộc)"; lỗi kiểm tra thì summary đổi chữ.
 */

async function openAdvanced(): Promise<HTMLElement> {
  const summary = await screen.findByText(/Nâng cao — Jev/);
  const details = summary.closest('details')!;
  if (!details.hasAttribute('open')) await userEvent.setup().click(summary);
  return summary;
}

const TITLE = 'Jev — quyết định nhanh cho Gen';

const me = {
  id: 'u', email: 'owner@genesis.local', display_name: 'Owner', role: { code: 'owner', name: 'Owner' },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null,
  permissions: { 'system.read': 'all', 'system.manage': 'all' },
};

function jev(lastTest: ProviderTestResult | null, auth: Provider['auth_state'] = 'ok'): Provider {
  return {
    id: 'jev1', kind: 'system_one', name: 'Jev (System One)', endpoint: 'https://openrouter.ai/api/v1', failover_rank: 99,
    enabled: true, auth_state: auth, keys: [{ id: 'k', label: 'chính', last4: 'abcd' }] as Provider['keys'],
    models: [{ id: 'm', model_name: 'typesafe/jev-1.13', daily_quota: null, used_today: 0 }] as unknown as Provider['models'],
    last_test: lastTest,
  };
}

const FAIL: ProviderTestResult = { ok: false, latency_ms: null, models: [], error: 'JEV_ERROR: 401 khoá sai' };
const OK: ProviderTestResult = { ok: true, latency_ms: 420, models: ['typesafe/jev-1.13'], error: null };

function setup(initial: Provider, afterTest?: { result: ProviderTestResult; provider: Provider }, entry = '/system?tab=brain') {
  let current = initial;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    let body: unknown = [];
    // Thẻ Sàng lọc không thuộc phạm vi test này — trả lỗi để nó hiện trạng thái lỗi riêng.
    if (url.includes('/triage')) {
      return new Response(JSON.stringify({ error: { code: 'NOT_FOUND', message: 'không có' } }), { status: 404, headers: { 'Content-Type': 'application/json' } });
    }
    if (url.includes('/auth/me')) body = me;
    else if (url.includes('/providers/jev1/test') && method === 'POST' && afterTest) {
      current = afterTest.provider;
      body = afterTest.result;
    } else if (url.includes('/providers')) body = [current];
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  vi.stubGlobal('fetch', fetchMock);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(qk.me, me);
  const view = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[entry]}>
        <BrainTab />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { fetchMock, view };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('v0.1.39 — Jev "Kiểm tra 1 lần" và thu vào "Nâng cao" khi lỗi', () => {
  it('Jev đã kiểm lỗi: thẻ ẩn trong "Nâng cao"; bấm summary thì thấy lại', async () => {
    const { view } = setup(jev(FAIL, 'error'));
    const summary = await screen.findByText(/Nâng cao — Jev/);
    const details = summary.closest('details')!;
    expect(details).toHaveClass('brain-advanced');
    expect(details).not.toHaveAttribute('open');
    expect(screen.queryByText(TITLE)).not.toBeVisible();
    await userEvent.setup().click(summary);
    expect(details).toHaveAttribute('open');
    expect(screen.getByText(TITLE)).toBeVisible();
    expect(view.container.textContent).not.toContain('[object Object]');
  });

  it('Jev khoẻ: thẻ vẫn trong "Nâng cao — Jev (không bắt buộc)" (đóng sẵn); mở ra thấy nút "Kiểm tra 1 lần"', async () => {
    setup(jev(OK));
    const summary = await screen.findByText('Nâng cao — Jev (không bắt buộc)');
    const details = summary.closest('details')!;
    expect(details).toHaveClass('brain-advanced');
    expect(details).not.toHaveAttribute('open');
    expect(screen.queryByText(TITLE)).not.toBeVisible();
    await openAdvanced();
    expect(await screen.findByRole('button', { name: /Kiểm tra 1 lần/ })).toBeVisible();
    expect(screen.getByText(TITLE)).toBeVisible();
    expect(screen.queryByText(/đã ẩn vì kiểm tra lỗi/)).not.toBeInTheDocument();
  });

  it('bấm "Kiểm tra 1 lần" ra lỗi: nút biến mất, thẻ tự thu vào "Nâng cao"', async () => {
    const { fetchMock, view } = setup(jev(null), { result: FAIL, provider: jev(FAIL, 'error') });
    const summary = await openAdvanced();
    const btn = await screen.findByRole('button', { name: /Kiểm tra 1 lần/ });
    await userEvent.setup().click(btn);
    await screen.findByText('Nâng cao — Jev (đã ẩn vì kiểm tra lỗi, không bắt buộc)');
    expect(screen.queryByRole('button', { name: /Kiểm tra 1 lần/ })).not.toBeInTheDocument();
    // Danh sách providers được tải lại sau khi kiểm.
    const listCalls = fetchMock.mock.calls.filter(([u, i]) => String(u).endsWith('/providers') && (i?.method ?? 'GET') === 'GET');
    expect(listCalls.length).toBeGreaterThanOrEqual(2);
    // "Nâng cao" vẫn mở: còn kết quả của lần kiểm và dòng "Đã kiểm tra", không có nút kiểm lại.
    expect(summary.closest('details')).toHaveAttribute('open');
    expect(screen.getByText(TITLE)).toBeVisible();
    expect(screen.getByText(/Đã kiểm tra — Jev không bắt buộc, có thể bỏ qua/)).toBeVisible();
    expect(screen.queryByText(/không cần kiểm thêm/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Kiểm tra 1 lần/ })).not.toBeInTheDocument();
    expect(view.container.textContent).not.toContain('[object Object]');
  });

  it('bấm "Kiểm tra 1 lần" thành công: nút đổi thành "Đã kiểm tra", summary không báo lỗi', async () => {
    setup(jev(null), { result: OK, provider: jev(OK) });
    await openAdvanced();
    await userEvent.setup().click(await screen.findByRole('button', { name: /Kiểm tra 1 lần/ }));
    expect(await screen.findByText(/Đã kiểm tra — không cần kiểm thêm/)).toBeVisible();
    await waitFor(() => expect(screen.getByText(/Jev trả lời được/)).toBeVisible());
    expect(screen.queryByRole('button', { name: /Kiểm tra 1 lần/ })).not.toBeInTheDocument();
    expect(screen.getByText('Nâng cao — Jev (không bắt buộc)')).toBeInTheDocument();
  });

  it('v0.1.43: tới bằng /system?tab=brain#jev (nút "Nhập khoá Jev") → khối "Nâng cao" mở sẵn, thấy thẻ Jev, cuộn tới', async () => {
    const scrolled: Element[] = [];
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (this: Element) {
      scrolled.push(this);
    };
    try {
      setup(jev(OK), undefined, '/system?tab=brain#jev');
      const summary = await screen.findByText('Nâng cao — Jev (không bắt buộc)');
      expect(summary.closest('details')).toHaveAttribute('open');
      expect(screen.getByText(TITLE)).toBeVisible();
      await waitFor(() => expect(scrolled.map((el) => el.id)).toContain('jev'));
    } finally {
      Element.prototype.scrollIntoView = original;
    }
  });
});
