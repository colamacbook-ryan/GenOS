/**
 * v0.1.64 gói B6: Sếp tự đánh dấu nhân viên / sửa loại người ngay ở hồ sơ (F045, F141); câu hướng dẫn bằng lời thường;
 * dải pipeline hiện số tin "Lỗi xử lý" (F055).
 */
import type { ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { Pipeline, Profile } from '@gen-harness/contracts';
import { queryClient } from '../../src/lib/queryClient';
import { qk } from '../../src/lib/queries';
import { useUrlStateStore } from '../../src/lib/uiStore';
import { pipelineCards } from '../../src/screens/data/dataModel';
import { PERSON_TYPE_LABEL, STAFF_HOWTO, personTypeLabel, personTypePatch, staffPatch } from '../../src/screens/people/peopleModel';
import { ProfileScreen } from '../../src/screens/relations/ProfileScreen';
import { TeamScreen } from '../../src/screens/team/TeamScreen';
import { buildNavigation } from '../mock-api';

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

interface Call {
  url: string;
  method: string;
  body: unknown;
}

function mockFetch(handler: (c: Call) => Response | Promise<Response>) {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const c = { url: String(url), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined };
      calls.push(c);
      return handler(c);
    }),
  );
  return calls;
}

function renderScreen(ui: ReactElement, qc: QueryClient = queryClient) {
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/']}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

const PROFILE = (type: Profile['person']['type'], relation = 'stranger'): Profile => ({
  person: { id: 'p1', code: 'PER-0042', name: 'Trần Thị Mai', type, org_name: null, title: null, relation_to_owner: relation, owner: null },
  autonomy_level: null, bot: null, owner_note: null, identities: [], scores: [], summary: [], timeline: [], documents: [],
  touchpoints: [], merge_history: [],
});

afterEach(() => {
  vi.unstubAllGlobals();
  queryClient.clear();
  useUrlStateStore.setState({ params: {} });
});

describe('peopleModel — loại người', () => {
  it('câu hướng dẫn bằng lời thường, không còn chữ lập trình', () => {
    expect(STAFF_HOWTO).not.toMatch(/person_type|staff|quy tắc/i);
    expect(STAFF_HOWTO).toContain('Đánh dấu là nhân viên');
    expect(STAFF_HOWTO).toContain('Khách & Nhóm');
  });

  it('nhãn tiếng Việt cho mọi loại, loại lạ không lộ nguyên mã kỹ thuật tiếng Anh', () => {
    expect(personTypeLabel('staff')).toBe('Nhân viên');
    expect(personTypeLabel('candidate')).toBe('Ứng viên');
    expect(personTypeLabel(null)).toBe('Chưa phân loại');
    expect(personTypeLabel('unknown')).toBe('Chưa phân loại');
    expect(Object.keys(PERSON_TYPE_LABEL).sort()).toEqual(['candidate', 'customer', 'learner', 'partner', 'staff', 'supplier', 'unknown']);
  });

  it('staffPatch đặt loại và quan hệ "nhân sự của Sếp"; personTypePatch chỉ gửi trường đã đổi', () => {
    expect(staffPatch()).toEqual({ person_type: 'staff', relation_to_owner: 'staff' });
    expect(personTypePatch({ type: 'customer', relation: 'direct' }, null, null)).toEqual({});
    expect(personTypePatch({ type: 'customer', relation: 'direct' }, 'customer', 'direct')).toEqual({});
    expect(personTypePatch({ type: 'customer', relation: 'direct' }, 'partner', null)).toEqual({ person_type: 'partner' });
    expect(personTypePatch({ type: 'customer', relation: 'direct' }, null, 'via_staff')).toEqual({ relation_to_owner: 'via_staff' });
  });
});

describe('Dải pipeline — tin Lỗi xử lý (F055)', () => {
  const base = { channels_live: 1, groups_listening: 2, raw_total: 100, raw_pending: 3, interval_seconds: 900, count_threshold: 500, clean_total: 50, raw_error: 0 };
  it('có tin lỗi → ghi rõ số tin và báo hệ thống tự thử lại, thẻ đổi sang màu báo lỗi', () => {
    const raw = pipelineCards({ ...base, raw_error: 4 }, 'raw')[1];
    expect(raw.note).toContain('4 bản ghi lỗi xử lý');
    expect(raw.note).toContain('tự thử lại');
    expect(raw.tone).toBe('var(--color-bad)');
  });
  it('không lỗi (hoặc máy chủ cũ không trả raw_error) → giữ câu cũ', () => {
    const cu = { ...base } as Partial<Pipeline>;
    delete cu.raw_error;
    expect(pipelineCards(cu as Pipeline, 'raw')[1].note).not.toContain('lỗi xử lý');
    expect(pipelineCards({ ...base, raw_error: 0 }, 'raw')[1].note).not.toContain('lỗi xử lý');
  });
});

describe('Đội ngũ — hướng dẫn đánh dấu nhân viên', () => {
  it('chưa có nhân viên: nói bằng lời thường, trỏ tới Khách & Nhóm (không còn Quy tắc sàng lọc)', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    qc.setQueryData(qk.navigation, buildNavigation(new Set(), true, { hasStaff: false }));
    mockFetch(() => json(200, []));
    renderScreen(<TeamScreen />, qc);
    const note = await screen.findByRole('note');
    expect(note).toHaveTextContent('Đánh dấu là nhân viên');
    expect(note).not.toHaveTextContent('person_type');
    expect(within(note).getByRole('link', { name: /Khách & Nhóm/ })).toHaveAttribute('href', '/directory');
    expect(within(note).queryByRole('link', { name: /Quy tắc sàng lọc/ })).toBeNull();
  });
});

describe('Hồ sơ — Đánh dấu là nhân viên / sửa loại người', () => {
  const canWrite = () => queryClient.setQueryData(qk.me, { id: 'u', permissions: { 'profile.read': 'all', 'profile.write': 'all' } });

  it('hiện nhãn tiếng Việt của loại người và mức "Liên quan Sếp"', async () => {
    canWrite();
    mockFetch((c) => (c.url.includes('/profile/p1') ? json(200, PROFILE('customer', 'direct')) : json(404)));
    useUrlStateStore.setState({ params: { id: 'p1' } });
    renderScreen(<ProfileScreen />);
    expect(await screen.findByText('Trần Thị Mai')).toBeInTheDocument();
    expect(screen.getByText('Khách hàng')).toBeInTheDocument();
    expect(screen.getByText('Trực tiếp với Sếp')).toBeInTheDocument();
    expect(screen.queryByText('customer')).toBeNull();
  });

  it('bấm "Đánh dấu là nhân viên" → PATCH loại staff + quan hệ staff', async () => {
    canWrite();
    const calls = mockFetch((c) => {
      if (c.method === 'PATCH') return json(200, PROFILE('staff', 'staff'));
      return c.url.includes('/profile/p1') ? json(200, PROFILE('unknown')) : json(404);
    });
    useUrlStateStore.setState({ params: { id: 'p1' } });
    const user = userEvent.setup();
    renderScreen(<ProfileScreen />);
    await user.click(await screen.findByRole('button', { name: 'Đánh dấu là nhân viên' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    expect(calls.find((c) => c.method === 'PATCH')!.body).toEqual({ person_type: 'staff', relation_to_owner: 'staff' });
  });

  it('đã là nhân viên hoặc vai trò chỉ đọc → không có nút "Đánh dấu là nhân viên"', async () => {
    canWrite();
    mockFetch((c) => (c.url.includes('/profile/p1') ? json(200, PROFILE('staff', 'staff')) : json(404)));
    useUrlStateStore.setState({ params: { id: 'p1' } });
    const { unmount } = renderScreen(<ProfileScreen />);
    expect(await screen.findByText('Nhân viên')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Đánh dấu là nhân viên' })).toBeNull();
    unmount();
    queryClient.clear();
    queryClient.setQueryData(qk.me, { id: 'u', permissions: { 'profile.read': 'all' } });
    mockFetch((c) => (c.url.includes('/profile/p1') ? json(200, PROFILE('unknown')) : json(404)));
    renderScreen(<ProfileScreen />);
    expect(await screen.findByText('Chưa phân loại')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Đánh dấu là nhân viên' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Sửa loại người' })).toBeNull();
  });

  it('hộp "Sửa loại người": không đổi thì Lưu tắt; chọn Ứng viên → chỉ PATCH person_type', async () => {
    canWrite();
    const calls = mockFetch((c) => {
      if (c.method === 'PATCH') return json(200, PROFILE('candidate', 'stranger'));
      return c.url.includes('/profile/p1') ? json(200, PROFILE('unknown', 'stranger')) : json(404);
    });
    useUrlStateStore.setState({ params: { id: 'p1' } });
    const user = userEvent.setup();
    renderScreen(<ProfileScreen />);
    await user.click(await screen.findByRole('button', { name: 'Sửa loại người' }));
    const dlg = await screen.findByRole('dialog', { name: 'Loại người và quan hệ với Sếp' });
    expect(within(dlg).getByRole('button', { name: 'Lưu' })).toBeDisabled();
    await user.selectOptions(within(dlg).getByLabelText('Loại người'), 'candidate');
    await user.click(within(dlg).getByRole('button', { name: 'Lưu' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    expect(calls.find((c) => c.method === 'PATCH')!.body).toEqual({ person_type: 'candidate' });
  });
});
