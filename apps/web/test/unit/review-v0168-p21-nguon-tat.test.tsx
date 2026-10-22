/**
 * Review v0.1.68 P2-1 — màn "Nối nguồn AI" không còn ngõ cụt khi nguồn đã TẮT: dòng nguồn nói "Đã tắt — bật lại ở API & Model"
 * (có link /api); khi MỌI nguồn tắt, câu chặn nói đúng điều đó thay vì bảo "đăng nhập / dán khoá".
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { Provider } from '@gen-harness/contracts';
import { StepAiSource } from '../../src/setup/one/StepAiSource';
import { allSourcesOff } from '../../src/setup/phase2Model';
import { queryClient } from '../../src/lib/queryClient';

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
const P = (over: Partial<Provider>): Provider =>
  ({ id: 'p', kind: 'gemini', name: 'Gemini API', endpoint: null, failover_rank: 1, enabled: true, auth_state: 'ok', keys: [{ id: 'k', last4: 'abcd', enabled: true }], models: [{ id: 'm', model_name: 'gemini-x' }], last_test: null, ...over }) as unknown as Provider;

function renderStep(providers: Provider[]) {
  queryClient.clear();
  queryClient.setDefaultOptions({ queries: { retry: false, staleTime: Infinity } });
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/providers') && (init?.method ?? 'GET') === 'GET') return json(providers);
      return json([]);
    }),
  );
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <StepAiSource onSaved={() => {}} onLater={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe('P2-1 — nguồn đã tắt trên màn Nguồn AI', () => {
  it('allSourcesOff: có nguồn và tất cả tắt', () => {
    expect(allSourcesOff([])).toBe(false);
    expect(allSourcesOff([P({ enabled: false })])).toBe(true);
    expect(allSourcesOff([P({ enabled: false }), P({ id: 'q' })])).toBe(false);
  });

  it('mọi nguồn tắt: dòng "Đã tắt — bật lại ở API & Model" + link /api, câu chặn nói đúng, Tiếp tục khoá', async () => {
    renderStep([P({ enabled: false })]);
    const row = await screen.findByTestId('setup-ai-provider');
    expect(within(row).getByTestId('setup-ai-provider-off')).toHaveTextContent('Đã tắt — bật lại ở API & Model');
    expect(within(row).getByRole('link', { name: /API & Model/ })).toHaveAttribute('href', '/api');
    expect(await screen.findByText(/Mọi nguồn AI đang tắt/)).toBeInTheDocument();
    expect(screen.queryByText(/Đăng nhập Google hoặc dán một khoá API/)).toBeNull();
    expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeDisabled();
  });

  it('còn nguồn bật: nguồn tắt vẫn có dòng chú thích, nhưng Tiếp tục mở và không có câu "mọi nguồn tắt"', async () => {
    renderStep([P({ id: 'a', enabled: false, name: 'Tắt' }), P({ id: 'b', name: 'Bật', failover_rank: 2 })]);
    expect(await screen.findAllByTestId('setup-ai-provider')).toHaveLength(2);
    expect(screen.getAllByTestId('setup-ai-provider-off')).toHaveLength(1);
    expect(screen.queryByText(/Mọi nguồn AI đang tắt/)).toBeNull();
    expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeEnabled();
  });
});
