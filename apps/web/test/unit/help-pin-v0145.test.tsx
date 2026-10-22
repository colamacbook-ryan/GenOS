import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { HelpPage } from '../../src/help/HelpPage';
import { PIN_LIMITS, pinLimitsFor } from '../../src/help/helpModel';
import { queryClient } from '../../src/lib/queryClient';
import { qk } from '../../src/lib/queries';

const me = (role: { code: string; name: string }, extra: Record<string, string> = {}) => ({
  id: 'u1', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A (Chủ)', role,
  org: { id: 'o1', name: 'Genesis Trading', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null,
  permissions: role.code === 'owner' ? { 'system.read': 'all', 'system.manage': 'all' } : extra, must_change_password: false,
  features: { gen: false },
});

function stub(meBody: unknown) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const body = url.endsWith('/auth/me')
      ? meBody
      : url.endsWith('/system/about')
        ? { version: 'v0.1.45', org_name: 'Genesis Trading', timezone: 'Asia/Ho_Chi_Minh', role: { code: 'owner', name: 'Owner' } }
        : {};
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  }));
}

beforeEach(() => {
  queryClient.clear();
  vi.stubGlobal('WebSocket', undefined);
});
afterEach(() => vi.unstubAllGlobals());

describe('Trợ giúp — giới hạn của mã PIN (v0.1.45, F-60)', () => {
  const renderHelp = () =>
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <HelpPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

  it('Owner thấy thẻ "Mã PIN bảo vệ được gì" (tiêu đề h2) gọi "Sếp", có câu về cờ Đáng ngờ', async () => {
    stub(me({ code: 'owner', name: 'Owner — Sếp' }));
    renderHelp();
    const heading = await screen.findByRole('heading', { level: 2, name: 'Mã PIN bảo vệ được gì' });
    const card = screen.getByTestId('help-pin-limits');
    expect(card).toContainElement(heading);
    await within(card).findByText(/Đáng ngờ/);
    expect(within(card).getByText(/ai biết mật khẩu đăng nhập thì đặt lại được PIN/)).toBeInTheDocument();
    expect(card.textContent).toContain('Đáng ngờ');
    expect(card.textContent).toMatch(/mức tự trị.*tool MCP.*tài khoản CLI.*nhà cung cấp AI.*mời người/);
  });

  it('nhân viên (operator) thấy thẻ gọi "bạn", KHÔNG thấy cách lách điểm đánh giá', async () => {
    stub(me({ code: 'operator', name: 'Operator' }));
    renderHelp();
    await screen.findByRole('heading', { level: 2, name: 'Mã PIN bảo vệ được gì' });
    await waitFor(() => expect(queryClient.getQueryData(qk.me)).toBeTruthy()); // /auth/me đã về
    const card = screen.getByTestId('help-pin-limits');
    expect(within(card).getByText(/ai biết mật khẩu đăng nhập thì đặt lại được PIN/)).toBeInTheDocument();
    expect(card.textContent).toContain('phiên đăng nhập đang mở của bạn');
    expect(card.textContent).not.toMatch(/Sếp/);
    expect(card.textContent).not.toMatch(/Đáng ngờ|xin điểm|câu lệnh cho AI/);
  });

  it('vai trò được Owner cho xem đánh giá nhân sự thấy lưu ý cờ Đáng ngờ (không kèm cách lách)', () => {
    const p = pinLimitsFor({ role: { code: 'manager', name: 'Manager' }, permissions: { 'people_review.read': 'team' } } as never);
    expect(p.points.join(' ')).toContain('Đáng ngờ');
    expect(p.points.join(' ')).not.toMatch(/xin điểm|câu lệnh cho AI|Sếp/);
    expect(pinLimitsFor({ role: { code: 'manager', name: 'Manager' }, permissions: { 'people_review.read': 'none' } } as never).points.join(' ')).not.toContain('Đáng ngờ');
  });

  it('PIN_LIMITS: 3–4 câu ngắn', () => {
    expect(PIN_LIMITS.title).toBe('Mã PIN bảo vệ được gì');
    expect(PIN_LIMITS.points.length).toBeGreaterThanOrEqual(3);
    expect(PIN_LIMITS.points.length).toBeLessThanOrEqual(4);
  });
});
