import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { ProfileScreen } from '../../src/screens/relations/ProfileScreen';

/** v0.1.42 (F-65): Hồ sơ sống không còn trên thanh bên — chỉ mở từ danh sách Khách & Nhóm. */
describe('Hồ sơ sống chưa chọn hồ sơ', () => {
  it('/profile không có id → nút "Mở Khách & Nhóm" trỏ /directory', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/profile']}>
          <ProfileScreen />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByText('Chưa chọn hồ sơ')).toBeInTheDocument();
    expect(screen.getByText('Mở một hồ sơ từ Khách & Nhóm.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Mở Khách & Nhóm/ })).toHaveAttribute('href', '/directory');
  });
});
