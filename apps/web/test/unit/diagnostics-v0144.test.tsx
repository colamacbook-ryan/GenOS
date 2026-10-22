/**
 * v0.1.44 (F-4b) — Trợ giúp › "Gói chẩn đoán cho người hỗ trợ": genh cũ (lệnh chạy tay), idle, đang tạo (thăm lại),
 * xong (nút Tải có kích thước + href cùng gốc + dòng đã lọc bí mật), lỗi (câu thân thiện + Chi tiết kỹ thuật); nút tạo
 * gói hỏi PIN (423).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { DIAGNOSTICS_DOWNLOAD_URL, type DiagnosticsState } from '@gen-harness/contracts';
import { DiagnosticsCard } from '../../src/help/DiagnosticsCard';
import {
  DIAG_ERROR_TEXT,
  DIAG_FILTERED_TEXT,
  DIAG_SLOW_AFTER_MS,
  DIAG_SLOW_TEXT,
  DIAG_STALE_TEXT,
  DIAG_WORKING_TEXT,
  diagPhase,
  diagSlow,
  downloadLabel,
  fmtBytes,
} from '../../src/help/diagnosticsModel';
import { usePinStore } from '../../src/lib/pinStore';
import { queryClient } from '../../src/lib/queryClient';
import { qk } from '../../src/lib/queries';

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json', 'X-Request-ID': 'req0123456789abc' },
  });

const IDLE: DiagnosticsState = {
  supported: true, state: 'idle', request_id: null, requested_at: null, started_at: null, finished_at: null, file_name: null,
  size_bytes: null, sha256: null, error_code: null, message: null, command: 'genh doctor',
};
const DONE: DiagnosticsState = {
  ...IDLE, state: 'done', request_id: '0123456789abcdef', requested_at: '2026-10-03T01:00:00Z', started_at: '2026-10-03T01:00:01Z',
  finished_at: '2026-10-03T01:00:30Z', file_name: 'genh-doctor-20261003T010030Z.zip', size_bytes: 48_213, sha256: 'ab'.repeat(32),
};

interface Call {
  path: string;
  method: string;
}

function setup(states: DiagnosticsState[], opts: { pinFirst?: boolean; postError?: { status: number; code: string }; downloadError?: boolean } = {}) {
  const calls: Call[] = [];
  let i = 0;
  let pinOk = !opts.pinFirst;
  let pinPrompts = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(url), 'http://x').pathname.replace('/api/v1', '');
      const method = init?.method ?? 'GET';
      calls.push({ path, method });
      if (path === '/system/diagnostics' && method === 'GET') {
        const s = states[Math.min(i, states.length - 1)];
        i += 1;
        return json(200, s);
      }
      if (path === '/system/diagnostics' && method === 'POST') {
        if (!pinOk) return json(423, { status: 423, code: 'PIN_REQUIRED', title: 'Cần PIN', request_id: 'req0123456789abc' });
        if (opts.postError) return json(opts.postError.status, { status: opts.postError.status, code: opts.postError.code, title: 'x' });
        return json(202, { ...IDLE, state: 'pending', request_id: '0123456789abcdef', requested_at: '2026-10-03T01:00:00Z' });
      }
      if (path === '/system/diagnostics/download') {
        if (!pinOk) return json(423, { status: 423, code: 'PIN_REQUIRED', title: 'Cần PIN' });
        if (opts.downloadError) return json(404, { status: 404, code: 'DIAG_NOT_READY', title: 'x', request_id: 'req0123456789abc' });
        return new Response(new Blob(['PK']), { status: 200, headers: { 'Content-Type': 'application/zip' } });
      }
      return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
    }),
  );
  const unsub = usePinStore.subscribe((s, prev) => {
    if (s.open && !prev.open) {
      pinPrompts += 1;
      pinOk = true;
      queueMicrotask(() => usePinStore.getState().finish(true));
    }
  });
  unsubs.push(unsub);
  return { calls, pins: () => pinPrompts };
}

function renderCard() {
  queryClient.setQueryData(qk.me, {
    id: 'u', email: 'o@x', display_name: 'Sếp', role: { code: 'owner', name: 'Owner' },
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' }, addressing: { self: 'Anh', bot_calls_me: 'Sếp' },
    pin_verified_until: null, permissions: { 'system.manage': 'all' },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <DiagnosticsCard />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

let unsubs: Array<() => void> = [];
beforeEach(() => queryClient.clear());
afterEach(() => {
  unsubs.forEach((u) => u());
  unsubs = [];
  usePinStore.setState({ open: false, waiters: [] });
  vi.unstubAllGlobals();
  queryClient.clear();
});

describe('diagnosticsModel', () => {
  it('diagPhase, fmtBytes, nhãn nút tải', () => {
    expect(diagPhase({ supported: false, state: 'done' })).toBe('unsupported');
    expect(diagPhase({ supported: true, state: 'pending' })).toBe('working');
    expect(diagPhase({ supported: true, state: 'running' })).toBe('working');
    expect(diagPhase({ supported: true, state: 'failed' })).toBe('failed');
    expect(diagPhase(undefined)).toBe('idle');
    expect(diagPhase({ supported: true, state: 'pending', stale: true })).toBe('stale');
    expect(diagPhase({ supported: true, state: 'running', stale: true })).toBe('stale');
    expect(diagPhase({ supported: true, state: 'pending', stale: false })).toBe('working');
    expect(fmtBytes(512)).toBe('512 B');
    expect(fmtBytes(48_213)).toBe('47 KB');
    expect(fmtBytes(1_300_000)).toBe('1,2 MB');
    expect(fmtBytes(null)).toBe('');
    expect(downloadLabel(DONE)).toBe('Tải gói chẩn đoán (47 KB)');
  });

  it('diagSlow: chỉ khi đang tạo và đã quá 3 phút kể từ lúc yêu cầu', () => {
    const now = Date.parse('2026-10-03T01:10:00Z');
    expect(diagSlow({ requested_at: '2026-10-03T01:00:00Z' }, 'working', now)).toBe(true);
    expect(diagSlow({ requested_at: '2026-10-03T01:08:00Z' }, 'working', now)).toBe(false);
    expect(diagSlow({ requested_at: '2026-10-03T01:00:00Z' }, 'stale', now)).toBe(false);
    expect(diagSlow({ requested_at: null }, 'working', now)).toBe(false);
    expect(DIAG_SLOW_AFTER_MS).toBe(180_000);
  });
});

describe('Gói chẩn đoán cho người hỗ trợ', () => {
  it('genh cũ (supported=false) → lệnh "genh doctor" chạy trên máy chủ + nút Chép, không có nút Tạo gói', async () => {
    setup([{ ...IDLE, supported: false }]);
    renderCard();
    const cmd = await screen.findByTestId('diagnostics-command');
    expect(within(cmd).getByText('genh doctor')).toBeInTheDocument();
    expect(within(cmd).getByRole('button', { name: 'Chép lệnh' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Tạo gói chẩn đoán' })).toBeNull();
  });

  it('idle → bấm Tạo gói chẩn đoán hỏi PIN (423) rồi gửi lại → đang tạo (thăm lại) → xong', async () => {
    const { calls, pins } = setup([IDLE, { ...IDLE, state: 'running', request_id: '0123456789abcdef' }, DONE], { pinFirst: true });
    renderCard();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Tạo gói chẩn đoán' }));
    expect(await screen.findByTestId('diagnostics-working')).toHaveTextContent(DIAG_WORKING_TEXT);
    expect(pins()).toBe(1);
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(2);
    expect(screen.queryByRole('button', { name: 'Tạo gói chẩn đoán' })).toBeNull();
    expect(screen.getByTestId('diagnostics-meta')).toHaveTextContent('Mã yêu cầu 0123456789abcdef');
    await waitFor(() => expect(screen.getByTestId('diagnostics-done')).toBeInTheDocument(), { timeout: 8000 });
  }, 12_000);

  it('pending → chữ đang tạo, không có nút; mới yêu cầu thì chưa hiện lệnh chạy tay', async () => {
    setup([{ ...IDLE, state: 'pending', request_id: '0123456789abcdef', requested_at: new Date().toISOString() }]);
    renderCard();
    expect(await screen.findByTestId('diagnostics-working')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Tạo gói chẩn đoán' })).toBeNull();
    expect(screen.queryByTestId('diagnostics-slow-command')).toBeNull();
  });

  it('đang tạo quá 3 phút (chưa tới mốc stale 15 phút) → thêm câu "Lâu hơn thường lệ" + lệnh genh doctor', async () => {
    const at = new Date(Date.now() - 4 * 60_000).toISOString();
    setup([{ ...IDLE, state: 'running', request_id: '0123456789abcdef', requested_at: at }]);
    renderCard();
    const slow = await screen.findByTestId('diagnostics-slow-command');
    expect(slow).toHaveTextContent(DIAG_SLOW_TEXT);
    expect(within(slow).getByText('genh doctor')).toBeInTheDocument();
    expect(screen.getByTestId('diagnostics-working')).toBeInTheDocument();
  });

  it('lỗi tải gói cũ biến mất khi bấm Tạo gói chẩn đoán lần nữa', async () => {
    setup([DONE, DONE, { ...IDLE, state: 'pending', request_id: '0123456789abcdef', requested_at: new Date().toISOString() }], { downloadError: true });
    renderCard();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('link', { name: 'Tải gói chẩn đoán (47 KB)' }));
    expect(await screen.findByText(DIAG_ERROR_TEXT.DIAG_NOT_READY)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Tạo gói chẩn đoán' }));
    await waitFor(() => expect(screen.queryByText(DIAG_ERROR_TEXT.DIAG_NOT_READY)).toBeNull());
  });

  it('pending quá 15 phút (stale) → câu "Máy chủ chưa nhận", lệnh genh doctor, nút Tạo gói lại; thôi thăm lại', async () => {
    const { calls } = setup([{ ...IDLE, state: 'pending', request_id: '0123456789abcdef', requested_at: '2026-10-03T01:00:00Z', stale: true }]);
    renderCard();
    const stale = await screen.findByTestId('diagnostics-stale');
    expect(stale).toHaveTextContent(DIAG_STALE_TEXT);
    expect(within(stale).getByText('genh doctor')).toBeInTheDocument();
    expect(screen.queryByTestId('diagnostics-working')).toBeNull();
    expect(screen.getByRole('button', { name: 'Tạo gói chẩn đoán' })).toBeInTheDocument();
    const gets = calls.filter((c) => c.method === 'GET').length;
    await new Promise((r) => setTimeout(r, 3500));
    expect(calls.filter((c) => c.method === 'GET').length).toBe(gets);
  }, 8000);

  it('done → nút "Tải gói chẩn đoán (47 KB)" là liên kết cùng gốc đúng href; dòng đã lọc; mã yêu cầu + giờ; bấm tải = fetch blob', async () => {
    const { calls } = setup([DONE]);
    const createUrl = vi.spyOn(URL, 'createObjectURL');
    renderCard();
    const link = await screen.findByRole('link', { name: 'Tải gói chẩn đoán (47 KB)' });
    expect(link).toHaveAttribute('href', DIAGNOSTICS_DOWNLOAD_URL);
    expect(link.getAttribute('href')).toBe('/api/v1/system/diagnostics/download');
    expect(link).toHaveAttribute('download', 'genh-doctor-20261003T010030Z.zip');
    expect(screen.getByText(DIAG_FILTERED_TEXT)).toBeInTheDocument();
    expect(screen.getByTestId('diagnostics-meta')).toHaveTextContent('Mã yêu cầu 0123456789abcdef · yêu cầu lúc 03/10 08:00:00 · xong lúc 03/10 08:00:30');
    expect(screen.getByRole('button', { name: 'Tạo gói chẩn đoán' })).toBeInTheDocument();
    await userEvent.setup().click(link);
    await waitFor(() => expect(createUrl).toHaveBeenCalled());
    expect(calls.some((c) => c.path === '/system/diagnostics/download')).toBe(true);
    createUrl.mockRestore();
  });

  it('failed → câu thân thiện + "Chi tiết kỹ thuật" (mã lỗi, request_id, lời genh) là chuỗi; vẫn tạo lại được', async () => {
    setup([{ ...IDLE, state: 'failed', request_id: '0123456789abcdef', error_code: 'DOCTOR_FAILED', message: 'không đọc được nhật ký docker', finished_at: '2026-10-03T01:01:00Z' }]);
    const { container } = renderCard();
    const failed = await screen.findByTestId('diagnostics-failed');
    expect(failed).toHaveTextContent('Chưa tạo được gói chẩn đoán');
    expect(within(failed).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    const code = failed.querySelector('code')!.textContent!;
    expect(code).toContain('Mã lỗi DOCTOR_FAILED');
    expect(code).toContain('request_id 0123456789abcdef');
    expect(code).toContain('không đọc được nhật ký docker');
    expect(screen.getByRole('button', { name: 'Tạo gói chẩn đoán' })).toBeInTheDocument();
    expect(container.textContent).not.toContain('[object Object]');
  });

  it('POST 409 DIAG_BUSY → câu thân thiện + Chi tiết kỹ thuật có request_id', async () => {
    setup([IDLE], { postError: { status: 409, code: 'DIAG_BUSY' } });
    renderCard();
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Tạo gói chẩn đoán' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Máy chủ đang tạo một gói chẩn đoán');
    expect(alert.querySelector('code')!.textContent).toContain('request_id req0123456789abc');
  });
});
