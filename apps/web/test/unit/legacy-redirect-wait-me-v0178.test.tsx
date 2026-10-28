import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { LegacyRedirect } from '../../src/shell/LegacyRedirect';

/**
 * v0.1.78 — mở thẳng `/system` (không ghi tab) chọn Sao lưu hay Nhật ký theo quyền `system.read`. Phải chờ `/auth/me`:
 * lúc quyền chưa tải, Owner từng bị chuyển nhầm sang Nhật ký (e2e "21. … thẻ Cập nhật chỉ ở …").
 */
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const me = (permissions: Record<string, string>) => ({
  id: 'u', email: 'x@genesis.local', display_name: 'X', role: { code: 'owner', name: 'owner' },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions,
});

function Where() {
  const l = useLocation();
  return <div data-testid="where">{l.pathname}</div>;
}

async function open(permissions: Record<string, string>): Promise<string> {
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => {
    if (String(url).includes('/auth/me')) {
      await new Promise((r) => setTimeout(r, 30)); // quyền về SAU lần vẽ đầu
      return json(200, me(permissions));
    }
    return json(404, { title: 'Không tìm thấy', status: 404 });
  }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/system']}>
        <Routes>
          <Route path="/system" element={<LegacyRedirect />} />
          <Route path="*" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return (await screen.findByTestId('where')).textContent ?? '';
}

afterEach(() => vi.unstubAllGlobals());

describe('LegacyRedirect — /system chờ quyền', () => {
  it('Owner (system.read) → Sao lưu & cập nhật, không phải Nhật ký', async () => {
    expect(await open({ 'system.read': 'all', 'audit.read': 'all' })).toBe('/admin/backup');
  });
  it('Quản lý chỉ có audit.read → Nhật ký', async () => {
    expect(await open({ 'system.read': 'none', 'audit.read': 'team' })).toBe('/admin/log');
  });
});
