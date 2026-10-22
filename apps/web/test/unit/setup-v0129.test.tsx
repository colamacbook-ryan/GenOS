import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { NeedsBossStrip } from '../../src/screens/queue/NeedsBossStrip';
import { NoModelNotice } from '../../src/screens/queue/NoModelBanner';
import { SkipBrainDialog } from '../../src/setup/Step4Brain';
import { AI_SOURCE_PATH, SETUP_STEPS } from '../../src/setup/steps';

/** v0.1.29 (Boss 30/09, V2): bước 4 "Để sau" được — hộp cảnh báo + dải "Chưa có model" có nút sửa. */

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function renderUi(ui: JSX.Element) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

/** `/auth/me` tối thiểu (không có quyền nào). */
const meAs = (code: string) => ({
  id: 'u1', email: `${code}@genesis.local`, display_name: code, role: { code, name: code },
  org: { id: 'o1', name: 'Genesis', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: {}, must_change_password: false,
});

afterEach(() => vi.unstubAllGlobals());

describe('Bước 4 để sau', () => {
  it('bước 4 không còn bắt buộc (chỉ 1–3 + Hoàn tất)', () => {
    expect(SETUP_STEPS.filter((s) => s.required).map((s) => s.n)).toEqual([1, 2, 3, 12]);
  });

  it('hộp cảnh báo nói rõ Gen/sàng lọc sẽ không chạy; "Vẫn để sau" mới bỏ qua', async () => {
    const user = userEvent.setup();
    const confirm = vi.fn();
    const close = vi.fn();
    renderUi(<SkipBrainDialog open hasTestedModel={false} onClose={close} onConfirm={confirm} />);
    const dlg = screen.getByRole('dialog');
    expect(dlg).toHaveTextContent('Gen (trợ lý) sẽ không trả lời và sàng lọc tin sẽ không chạy');
    expect(dlg).toHaveTextContent('Chưa có model');
    await user.click(screen.getByRole('button', { name: 'Quay lại chọn model' }));
    expect(close).toHaveBeenCalled();
    expect(confirm).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Vẫn để sau' }));
    expect(confirm).toHaveBeenCalled();
  });

  it('thẻ "Chưa có model" (NoModelNotice) có nút "Chọn model" mở /setup?buoc=nguon-ai (v0.1.59); dải ở Tổng quan hiện/ẩn theo mục 4 — xem needs-boss-v0136', async () => {
    renderUi(<NoModelNotice to={AI_SOURCE_PATH} />);
    const banner = await screen.findByTestId('no-model');
    expect(banner).toHaveTextContent('Chưa có model');
    expect(screen.getByRole('link', { name: /Chọn model/ })).toHaveAttribute('href', '/setup?buoc=nguon-ai');
  });

  it('v0.1.42 (F-26): vai trò không có system.manage → dải Cần Sếp xử lý không gọi /setup/follow-up, không render gì', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) =>
      String(input).includes('/auth/me')
        ? json(200, meAs('operator'))
        : json(200, [{ n: 4, key: 'brain', title: 'Bộ não AI', status: 'skipped', done: false }]),
    );
    vi.stubGlobal('fetch', fetchMock);
    const { container } = renderUi(<NeedsBossStrip />);
    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/auth/me'))).toBe(true));
    await new Promise((r) => setTimeout(r, 50));
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/setup/follow-up'))).toBe(false);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('v0.1.40 — bước 11 Sao lưu', () => {
  it('không còn hứa nơi lưu S3 (API chỉ nhận local); nhắc bản sao ra ổ USB/NAS', () => {
    const backup = SETUP_STEPS.find((s) => s.key === 'backup');
    expect(backup?.content).not.toMatch(/S3/);
    expect(backup?.content).toContain('nơi lưu trên máy chủ (bản sao ra ổ USB/NAS chọn ở Cài đặt › Sao lưu & cập nhật)');
  });
});
