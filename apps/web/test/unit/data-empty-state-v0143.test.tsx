/**
 * v0.1.43 (F-29) — danh sách trống vì chưa có nguồn dữ liệu thì dẫn đường: chưa nối kênh → "Nối kênh" (/guide/5),
 * kênh đã nối nhưng mất phiên → "Quét lại QR" (/connections),
 * đã nối nhưng chưa nghe nhóm → "Chọn nhóm để nghe" (/guide/6). Vai trò khác Owner: "Nhờ Owner …", không nút.
 */
import type { ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { EmptyState } from '@gen-harness/ui';
import { DataEmptyState, dataEmptyReason } from '../../src/screens/DataEmptyState';
import { InboxScreen } from '../../src/screens/queue/InboxScreen';
import { OpportunityScreen } from '../../src/screens/market/OpportunityScreen';
import { qk } from '../../src/lib/queries';

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function me(code: string) {
  return {
    id: 'u', email: `${code}@genesis.local`, display_name: 'Anh Nguyễn Văn A (Chủ)', role: { code, name: code },
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
    addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: {},
  };
}

function renderWith(
  ui: ReactElement,
  header: { channels_live: number; channels_connected?: number; groups_listening: number } | null,
  role: string | null = 'owner',
) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  if (role) qc.setQueryData(qk.me, me(role));
  if (header) qc.setQueryData(qk.header, { ...header, autonomy_level: 4, data_confidence: null });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

const FALLBACK = <EmptyState icon="ph ph-tray" title="Trạng thái trống cũ" />;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('dataEmptyReason', () => {
  it('các ca biên', () => {
    expect(dataEmptyReason(null)).toBeNull();
    expect(dataEmptyReason(undefined)).toBeNull();
    expect(dataEmptyReason({ channels_live: 0, groups_listening: 0 })).toBe('no-channel');
    // Không có kênh sống thì "chưa nối kênh" đứng trước, kể cả khi số nhóm cũ còn lại.
    expect(dataEmptyReason({ channels_live: 0, groups_listening: 3 })).toBe('no-channel');
    expect(dataEmptyReason({ channels_live: 2, groups_listening: 0 })).toBe('no-group');
    expect(dataEmptyReason({ channels_live: 1, groups_listening: 1 })).toBeNull();
    expect(dataEmptyReason({ channels_live: 4, groups_listening: 42 })).toBeNull();
    // Đã từng nối nhưng phiên không còn sống → mất kết nối (quét lại QR), không phải "chưa nối kênh".
    expect(dataEmptyReason({ channels_live: 0, channels_connected: 1, groups_listening: 3 })).toBe('channel-down');
    expect(dataEmptyReason({ channels_live: 0, channels_connected: 0, groups_listening: 0 })).toBe('no-channel');
  });
});

describe('<DataEmptyState>', () => {
  it('header {0,0} + Owner → "Nối kênh" dẫn /guide/5', () => {
    renderWith(<DataEmptyState fallback={FALLBACK} />, { channels_live: 0, groups_listening: 0 });
    expect(screen.getByTestId('data-empty-state')).toHaveAttribute('data-reason', 'no-channel');
    expect(screen.getByText('Chưa có dữ liệu vì chưa nối kênh')).toBeInTheDocument();
    expect(screen.getByText('Nối Zalo hoặc WhatsApp để tin nhắn bắt đầu về đây.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Nối kênh' })).toHaveAttribute('href', '/guide/5');
    expect(screen.queryByText('Trạng thái trống cũ')).toBeNull();
  });

  it('kênh đã nối nhưng mất phiên + Owner → "Quét lại QR" dẫn /connections', () => {
    renderWith(<DataEmptyState fallback={FALLBACK} />, { channels_live: 0, channels_connected: 1, groups_listening: 5 });
    expect(screen.getByTestId('data-empty-state')).toHaveAttribute('data-reason', 'channel-down');
    expect(screen.getByText('Kênh mất kết nối — quét lại QR')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Quét lại QR' })).toHaveAttribute('href', '/connections');
    expect(screen.queryByText('Chưa có dữ liệu vì chưa nối kênh')).toBeNull();
  });

  it('đang lọc + chưa có kênh sống → fallback "không khớp bộ lọc", không dẫn nối kênh', () => {
    renderWith(<DataEmptyState filtered fallback={FALLBACK} />, { channels_live: 0, groups_listening: 0 });
    expect(screen.getByText('Trạng thái trống cũ')).toBeInTheDocument();
    expect(screen.queryByTestId('data-empty-state')).toBeNull();
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('me đang tải → fallback (Owner không thấy thoáng "Nhờ Owner …")', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {})));
    renderWith(<DataEmptyState fallback={FALLBACK} />, { channels_live: 0, groups_listening: 0 }, null);
    expect(screen.getByText('Trạng thái trống cũ')).toBeInTheDocument();
    expect(screen.queryByText(/Nhờ Owner/)).toBeNull();
  });

  it('header {2,0} + Owner → "Chọn nhóm để nghe" dẫn /guide/6', () => {
    renderWith(<DataEmptyState fallback={FALLBACK} />, { channels_live: 2, groups_listening: 0 });
    expect(screen.getByTestId('data-empty-state')).toHaveAttribute('data-reason', 'no-group');
    expect(screen.getByText('Chưa chọn nhóm nào để nghe')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Chọn nhóm để nghe' })).toHaveAttribute('href', '/guide/6');
  });

  it('header {0,0} + vai trò manager → "Nhờ Owner …", không có nút', () => {
    renderWith(<DataEmptyState fallback={FALLBACK} />, { channels_live: 0, groups_listening: 0 }, 'manager');
    expect(screen.getByText('Chưa có dữ liệu vì chưa nối kênh')).toBeInTheDocument();
    expect(screen.getByText(/Nhờ Owner/)).toBeInTheDocument();
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('header {2,0} + vai trò khác → "Nhờ Owner chọn nhóm để nghe ở Kết nối."', () => {
    renderWith(<DataEmptyState fallback={FALLBACK} />, { channels_live: 2, groups_listening: 0 }, 'staff');
    expect(screen.getByText('Nhờ Owner chọn nhóm để nghe ở Kết nối.')).toBeInTheDocument();
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('header {4,42} → fallback (trạng thái trống cũ của màn)', () => {
    renderWith(<DataEmptyState fallback={FALLBACK} />, { channels_live: 4, groups_listening: 42 });
    expect(screen.getByText('Trạng thái trống cũ')).toBeInTheDocument();
    expect(screen.queryByTestId('data-empty-state')).toBeNull();
  });

  it('header lỗi → fallback, không đoán lý do', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(500, { error: { code: 'GH-E500', message: 'x' } })));
    renderWith(<DataEmptyState fallback={FALLBACK} />, null);
    expect(await screen.findByText('Trạng thái trống cũ')).toBeInTheDocument();
    expect(screen.queryByTestId('data-empty-state')).toBeNull();
  });
});

describe('Áp vào màn', () => {
  it('Hộp thư trống + chưa nối kênh → dẫn "Nối kênh"', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: RequestInfo | URL) =>
        String(url).includes('/inbox')
          ? json(200, { items: [], next_cursor: null, total: 0, counts: { all: 0, opportunity: 0, alert: 0, approval: 0, reply: 0, candidate: 0 } })
          : json(404),
      ),
    );
    renderWith(<InboxScreen />, { channels_live: 0, groups_listening: 0 });
    expect(await screen.findByRole('link', { name: 'Nối kênh' })).toHaveAttribute('href', '/guide/5');
    expect(screen.queryByText('Hộp thư đang trống')).toBeNull();
  });

  it('Bảng cơ hội trống + đã có kênh/nhóm → trạng thái trống cũ', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: RequestInfo | URL) => {
        const u = String(url);
        if (u.includes('/pipeline')) return json(200, { stages: [], open_pipeline_value_vnd: 0, open_pipeline_count: 0 });
        if (u.includes('/opportunities')) return json(200, { items: [], next_cursor: null, total: 0 });
        return json(404);
      }),
    );
    renderWith(<OpportunityScreen />, { channels_live: 4, groups_listening: 42 });
    expect(await screen.findByText('Chưa có cơ hội nào')).toBeInTheDocument();
    expect(screen.queryByTestId('data-empty-state')).toBeNull();
  });
});
