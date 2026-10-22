import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRef } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { Step9Autonomy } from '../../src/setup/Step9Autonomy';
import { SETUP_STEPS } from '../../src/setup/steps';
import { queryClient } from '../../src/lib/queryClient';
import { qk } from '../../src/lib/queries';

/** Sửa review v0.1.45: mở lại bước 9 sau Hoàn tất — điền sẵn mức hiện tại của agent bước 8, không đổi nhầm mức. */
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

let puts: unknown[] = [];
function stub(agentLevel: number, opts: { getFails?: boolean } = {}) {
  puts = [];
  let level = agentLevel;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith('/setup/steps/9') && init?.method === 'PUT') {
      const body = JSON.parse(String(init.body)) as { autonomy_level: number | null };
      puts.push(body);
      if (typeof body.autonomy_level === 'number') level = body.autonomy_level;
      return json(200, { finished: true, current_step: 12, steps: [], hard_boundaries: [],
                         agent: { id: 'a1', name: 'Trợ lý Mai', autonomy_level: level } });
    }
    if (url.endsWith('/setup/steps/9') && opts.getFails) return json(500, { status: 500, code: 'INTERNAL', title: 'Lỗi máy chủ' });
    if (url.endsWith('/setup/steps/9')) return json(200, { agent: { id: 'a1', name: 'Trợ lý Mai', autonomy_level: level } });
    if (url.endsWith('/setup/hard-boundaries')) return json(200, ['Chỉ lưu tin của nhóm Sếp đã chọn nghe']);
    if (url.endsWith('/setup/state')) return json(200, { finished: true, current_step: 12, steps: [] });
    return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
  }));
}

function renderStep(onSaved = vi.fn()) {
  const meta = SETUP_STEPS.find((s) => s.n === 9)!;
  return { onSaved, ...render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Step9Autonomy meta={meta} description="" status="done" token="" setToken={() => {}} onSaved={onSaved} onNext={() => {}} formRef={createRef()} />
      </MemoryRouter>
    </QueryClientProvider>,
  ) };
}

beforeEach(() => queryClient.clear());
afterEach(() => vi.unstubAllGlobals());

describe('Bước 9 mở lại sau Hoàn tất', () => {
  it('điền sẵn mức hiện tại + tên agent; không đổi mức thì không có lời nhắc PIN và gửi đúng mức cũ', async () => {
    stub(3);
    const { onSaved } = renderStep();
    expect(await screen.findByTestId('step9-agent')).toHaveTextContent('Trợ lý Mai · mức hiện tại: Gợi ý');
    // v0.1.55 (G2): không còn ô tích ranh giới — một dòng ghi chú, bấm Tiếp tục là đã đọc.
    expect(screen.getByTestId('step9-ack-note')).toHaveTextContent('Bấm Tiếp tục nghĩa là Sếp đã đọc các ranh giới trên');
    expect(screen.getByRole('radio', { name: 'Gợi ý' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.queryByText('Sau Hoàn tất, đổi mức tự trị cần mã PIN')).toBeNull();
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    await vi.waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(puts).toEqual([{ autonomy_level: 3, ack_boundaries: true }]);
  });

  it('đổi mức thì hiện lời nhắc PIN', async () => {
    stub(3);
    renderStep();
    await screen.findByTestId('step9-agent');
    await userEvent.setup().click(screen.getByRole('radio', { name: 'Soạn sẵn chờ duyệt' }));
    expect(screen.getByText('Sau Hoàn tất, đổi mức tự trị cần mã PIN')).toBeInTheDocument();
  });

  it('mức hiện tại ngoài 3/4 (đã đổi ở Danh tính Agent) và không chọn lại → gửi null (giữ nguyên)', async () => {
    stub(1);
    const { onSaved } = renderStep();
    expect(await screen.findByTestId('step9-agent')).toHaveTextContent('mức hiện tại: Chỉ ghi nhận');
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    await vi.waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(puts).toEqual([{ autonomy_level: null, ack_boundaries: true }]);
  });

  it('lưu mức 3 rồi mở lại (Quay lại / trong 30 s) → điền sẵn 3, không gửi lại mức cũ', async () => {
    stub(4);
    const { onSaved, unmount } = renderStep();
    await screen.findByTestId('step9-agent');
    const user = userEvent.setup();
    await user.click(screen.getByRole('radio', { name: 'Gợi ý' }));
    await user.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    await vi.waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(queryClient.getQueryData(qk.setupStep9)).toEqual({ agent: { id: 'a1', name: 'Trợ lý Mai', autonomy_level: 3 } });
    unmount();
    const again = renderStep();
    expect(await screen.findByTestId('step9-agent')).toHaveTextContent('mức hiện tại: Gợi ý');
    expect(screen.getByRole('radio', { name: 'Gợi ý' })).toHaveAttribute('aria-checked', 'true');
    await user.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    await vi.waitFor(() => expect(again.onSaved).toHaveBeenCalled());
    expect(puts).toEqual([{ autonomy_level: 3, ack_boundaries: true }, { autonomy_level: 3, ack_boundaries: true }]);
  });

  it('không đọc được mức hiện tại → báo lỗi + Thử lại, không cho Tiếp tục (không gửi mức mặc định)', async () => {
    stub(3, { getFails: true });
    renderStep();
    expect(await screen.findByRole('button', { name: /Thử lại/ }, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.queryByTestId('step9-agent')).toBeNull();
    expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeDisabled();
    expect(puts).toEqual([]);
  });

  it('mức ngoài 3/4 và chưa chọn → nói rõ "Không chọn = giữ nguyên"', async () => {
    stub(1);
    renderStep();
    expect(await screen.findByTestId('step9-keep')).toHaveTextContent('Không chọn = giữ nguyên mức hiện tại.');
    await userEvent.setup().click(screen.getByRole('radio', { name: 'Gợi ý' }));
    expect(screen.queryByTestId('step9-keep')).toBeNull();
  });
});
