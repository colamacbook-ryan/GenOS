import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { HelpPage } from '../../src/help/HelpPage';
import { GUIDE } from '../../src/guide/guideContent';
import { queryClient } from '../../src/lib/queryClient';

/**
 * v0.1.50 (QD-18, F-81): Trợ giúp và Hướng dẫn bước 14 không còn ghi "Gen chỉ đọc" tuyệt đối — Gen ghi Kho (Phiên, Việc)
 * khi Sếp Xác nhận + mã PIN; Gmail/Lịch/Drive vẫn chỉ đọc.
 */
const owner = {
  id: 'u1', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A (Chủ)', role: { code: 'owner', name: 'Owner — Sếp' },
  org: { id: 'o1', name: 'Genesis Trading', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null,
  permissions: { 'system.read': 'all', 'system.manage': 'all' }, must_change_password: false, features: { gen: false },
};

beforeEach(() => {
  queryClient.clear();
  vi.stubGlobal('WebSocket', undefined);
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const body = url.endsWith('/auth/me') ? owner : url.endsWith('/system/about') ? { version: 'v0.1.50', org_name: 'Genesis Trading' } : {};
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe('Gen-hub ghi Kho có xác nhận — chữ trên Trợ giúp và Hướng dẫn (v0.1.50)', () => {
  it('thẻ Trợ giúp: dòng đầu chỉ nói Gen đọc gì (không còn "chỉ ĐỌC" đá với dòng ghi Kho), lịch/mail vẫn không ghi, ghi Kho cần Xác nhận + mã PIN', async () => {
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <HelpPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const card = await screen.findByTestId('help-genhub-reads');
    expect(card.textContent).toContain('Gen đọc: Kho dữ liệu, lịch, mail (tìm và đọc), việc Google Tasks, tệp Drive.');
    // Cùng một danh sách không được vừa nói "chỉ ĐỌC" vừa nói "Ghi duy nhất: Phiên, Việc vào Kho dữ liệu".
    expect(card.textContent).not.toMatch(/chỉ đọc/i);
    expect(card.textContent).toContain('Gen KHÔNG gửi mail');
    // "việc" ở dòng KHÔNG là việc Google Tasks — không đá nhau với dòng "Ghi duy nhất: Phiên, Việc vào Kho dữ liệu".
    expect(card.textContent).toContain('KHÔNG tạo hay sửa lịch, việc Google Tasks, tệp Drive.');
    expect(card.textContent).toMatch(/Phiên, Việc vào Kho dữ liệu — chỉ khi Sếp bấm Xác nhận và nhập mã PIN/);
    expect(card.textContent).toContain('kho_create, kho_update');
    expect(card.textContent).not.toContain('Gen chỉ đọc');
  });

  it('Hướng dẫn bước 14: quyền ghi Kho là tuỳ chọn, không còn "chỉ đọc" tuyệt đối', () => {
    const step = GUIDE.find((g) => g.n === 14);
    expect(step).toBeDefined();
    const text = [step!.why, ...step!.steps].join('\n');
    expect(text).not.toContain('(chỉ đọc)');
    expect(text).toMatch(/KHÔNG bật quyền ghi lịch, mail hay Drive/);
    expect(text).toMatch(/Tuỳ chọn: tick quyền ghi Kho/);
    expect(step!.why).toMatch(/Xác nhận và nhập mã PIN/);
  });
});
