/**
 * v0.1.43 (F-30): bước 7 gập trọng số vào "Nâng cao". Khi trọng số lỗi tải hoặc tổng ≠ 100% thì "Tiếp tục" bị tắt —
 * lý do và nút Thử lại không được khuất trong khối đã gập (ngõ cụt của trình thiết lập).
 */
import { createRef } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { Step7Refinery } from '../../src/setup/Step7Refinery';
import { SETUP_STEPS } from '../../src/setup/steps';

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const PRESET = {
  id: 'r1', code: 'R-01', name: 'Hỏi giá', kind: 'demand', kind_label: 'Cầu', enabled: true, version: 1, threshold: 0.5,
  hits_24h: 0, conditions: [], outputs: [], prompt_hint: null, updated_at: '2026-10-01T00:00:00Z',
};

function setup(weights: { status: number; body: unknown }) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/rules/weights')) return json(weights.status, weights.body);
      if (url.includes('/setup/rule-presets')) return json(200, [PRESET]);
      if (url.includes('/refinery/schedule'))
        return json(200, { interval_seconds: 900, count_threshold: 500, batch_size: 50, min_confidence: 0.6, pending: 0, next_run_at: '2026-10-03T00:00:00Z', next_trigger: 'interval' });
      return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
    }),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const meta = SETUP_STEPS.find((s) => s.n === 7)!;
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Step7Refinery
          meta={meta}
          description="Bước 7"
          status="doing"
          token=""
          setToken={() => {}}
          onSaved={() => {}}
          onNext={() => {}}
          formRef={createRef<HTMLFormElement>()}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const advanced = () => screen.getByText('Nâng cao — trọng số chấm điểm').closest('details')!;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Bước 7 — trọng số trong "Nâng cao"', () => {
  it('trọng số lỗi tải → "Nâng cao" mở sẵn, thấy lỗi + nút Thử lại, có lý do cạnh "Tiếp tục"', async () => {
    const view = setup({ status: 500, body: { status: 500, code: 'GH-E500', title: 'Lỗi máy chủ' } });
    expect(await screen.findByRole('button', { name: /Thử lại/ })).toBeVisible();
    expect(advanced()).toHaveAttribute('open');
    expect(screen.getByText(/Chưa tải được trọng số chấm điểm/)).toBeVisible();
    expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeDisabled();
    expect(view.container.textContent).not.toContain('[object Object]');
  });

  it('tổng ≠ 100% → mở sẵn, thấy dòng báo tổng', async () => {
    setup({ status: 200, body: [{ dimension: 'a', label: 'A', value: 60 }, { dimension: 'b', label: 'B', value: 30 }] });
    await waitFor(() => expect(advanced()).toHaveAttribute('open'));
    expect(screen.getAllByText('Tổng trọng số phải bằng 100% — hiện 90% (thiếu 10%).')[0]).toBeVisible();
    expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeDisabled();
  });

  it('trọng số hợp lệ → "Nâng cao" vẫn gập sẵn', async () => {
    setup({ status: 200, body: [{ dimension: 'a', label: 'A', value: 60 }, { dimension: 'b', label: 'B', value: 40 }] });
    await waitFor(() => expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeEnabled());
    expect(advanced()).not.toHaveAttribute('open');
  });
});
