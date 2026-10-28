/** v0.1.78 (G0): HubPage — khung trang có tab: `?tab=`, lọc tab theo vai/quyền/khoá màn, đường con cho màn tự dùng ?tab=. */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { qk } from '../../src/lib/queries';
import { HubPage, type HubTab } from '../../src/shell/HubPage';
import { buildNavigation, hiddenScreens } from '../mock-api';

const TABS: HubTab[] = [
  { key: 'a', label: 'Tab A', render: () => <p>nội dung A</p> },
  { key: 'b', label: 'Tab B', screen: 'inbox', render: () => <p>nội dung B</p> },
  { key: 'o', label: 'Tab Sếp', ownerOnly: true, render: () => <p>nội dung Sếp</p> },
  { key: 'n', label: 'Tab vai khác', notOwner: true, render: () => <p>nội dung vai khác</p> },
  { key: 'p', label: 'Tab quyền', perm: 'system.manage', render: () => <p>nội dung quyền</p> },
  { key: 'x', label: 'Hộp thư', path: 'sub', render: () => <p>nội dung con</p> },
];

function Where() {
  const l = useLocation();
  return <div data-testid="where">{`${l.pathname}${l.search}`}</div>;
}

function mount(path: string, role: 'owner' | 'agent_staff' = 'owner') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  qc.setQueryData(qk.navigation, buildNavigation(role === 'owner' ? new Set() : hiddenScreens(role)));
  qc.setQueryData(qk.me, {
    id: 'u', email: 'a@b.c', display_name: 'A', role: { code: role, name: role },
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' }, addressing: { self: 'Anh', bot_calls_me: 'Sếp' },
    pin_verified_until: null, permissions: role === 'owner' ? { 'system.manage': 'all' } : { 'queue.read': 'assigned' },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="*" element={<><HubPage base="/hub" title="Hub" label="Hub" tabs={TABS} /><Where /></>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('HubPage', () => {
  it('không có ?tab= ⇒ tab đầu tiên; ?tab=b mở tab B; tab lạ ⇒ tab đầu', () => {
    mount('/hub');
    expect(screen.getByText('nội dung A')).toBeInTheDocument();
  });

  it('?tab=b mở tab B', () => {
    mount('/hub?tab=b');
    expect(screen.getByText('nội dung B')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Tab B' })).toHaveAttribute('aria-selected', 'true');
  });

  it('tab lạ ⇒ tab đầu', () => {
    mount('/hub?tab=zzz');
    expect(screen.getByText('nội dung A')).toBeInTheDocument();
  });

  it('Owner thấy tab ownerOnly và tab có quyền; không thấy tab notOwner', () => {
    mount('/hub');
    expect(screen.getByRole('tab', { name: 'Tab Sếp' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Tab quyền' })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Tab vai khác' })).toBeNull();
  });

  it('vai khác: thấy tab notOwner, không thấy tab Sếp/quyền; tab theo khoá màn cũ chỉ hiện khi vai có màn đó', () => {
    mount('/hub', 'agent_staff');
    expect(screen.getByRole('tab', { name: 'Tab vai khác' })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Tab Sếp' })).toBeNull();
    expect(screen.queryByRole('tab', { name: 'Tab quyền' })).toBeNull();
    expect(screen.getByRole('tab', { name: 'Tab B' })).toBeInTheDocument(); // inbox: queue.read
  });

  it('bấm tab đổi ?tab=; tab đầu về đường gốc; tab đường con đổi đường dẫn', async () => {
    const user = userEvent.setup();
    mount('/hub');
    await user.click(screen.getByRole('tab', { name: 'Tab B' }));
    expect(screen.getByTestId('where')).toHaveTextContent('/hub?tab=b');
    expect(screen.getByText('nội dung B')).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: 'Hộp thư' }));
    expect(screen.getByTestId('where')).toHaveTextContent('/hub/sub');
    expect(screen.getByText('nội dung con')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Hộp thư' })).toHaveAttribute('aria-selected', 'true');
    await user.click(screen.getByRole('tab', { name: 'Tab A' }));
    expect(screen.getByTestId('where')).toHaveTextContent(/^\/hub$/);
  });

  it('không tab nào được phép ⇒ thông báo khoá, không trang trống', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    qc.setQueryData(qk.navigation, buildNavigation());
    qc.setQueryData(qk.me, { id: 'u', email: 'a@b.c', display_name: 'A', role: { code: 'auditor', name: 'a' }, org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' }, addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: {} });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <HubPage base="/hub" title="Hub" label="Hub" tabs={[{ key: 'q', label: 'Q', perm: 'system.manage', render: () => <p>x</p> }]} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByText('Vai trò của bạn không có quyền xem trang này')).toBeInTheDocument();
  });
});
