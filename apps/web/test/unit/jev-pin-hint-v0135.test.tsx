import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { JevCard } from '../../src/screens/system/JevCard';
import { qk } from '../../src/lib/queries';

/** v0.1.35 (F-20): «Lưu & kiểm tra» của Jev gọi POST /providers (cần PIN ai.route_change) — báo trước như Agent & Model. */

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('<JevCard> gợi ý PIN', () => {
  it('form cấu hình Jev hiện «Cần mã PIN 6 số» cạnh nút Bật Jev và nút Lưu & kiểm tra', async () => {
    const me = {
      id: 'u', email: 'owner@genesis.local', display_name: 'Owner', role: { code: 'owner', name: 'Owner' },
      org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
      addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null,
      permissions: { 'system.read': 'all', 'system.manage': 'all' },
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const body = String(input).includes('/auth/me') ? me : String(input).includes('/providers') ? [] : {};
        return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }),
    );
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    qc.setQueryData(qk.me, me);
    render(
      <QueryClientProvider client={qc}>
        <JevCard />
      </QueryClientProvider>,
    );
    // v0.1.55: "Bật Jev 1 chạm" giữ NGUYÊN rào PIN của việc tạo nguồn — báo trước cạnh nút.
    const batJev = await screen.findByRole('button', { name: /Bật Jev$/ });
    expect(within(batJev.parentElement as HTMLElement).getByText(/Cần mã PIN 6 số/)).toBeInTheDocument();
    // Khối "Nâng cao" (tự chọn địa chỉ/model) vẫn đi đường tạo nguồn cũ, cũng báo PIN.
    const save = await screen.findByRole('button', { name: /Lưu & kiểm tra/ });
    const actions = save.parentElement as HTMLElement;
    expect(within(actions).getByText(/Cần mã PIN 6 số/)).toBeInTheDocument();
  });
});
