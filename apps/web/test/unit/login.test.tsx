import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { LoginPage } from '../../src/pages/LoginPage';

describe('LoginPage', () => {
  it('chỉ lệnh genh reset-password khi quên mật khẩu', () => {
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );
    expect(screen.getByText('Quên mật khẩu?')).toBeTruthy();
    // v0.1.28 (UX N9/V15): nhân viên được chỉ nhờ Owner, không phải chạy lệnh máy chủ.
    expect(screen.getByText(/Nhân viên: nhờ Owner bấm "Đặt lại mật khẩu"/)).toBeTruthy();
    expect(screen.getByText('~/.gen-harness/bin/genh reset-password').tagName).toBe('CODE');
  });
});
