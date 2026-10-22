/**
 * v0.1.62: luật v0.1.38 (F-22) "agy chỉ cho Gen của Sếp" đã gỡ ⇒ máy chủ không còn trả 503 MODEL_UNAVAILABLE với
 * title/detail riêng cho chuỗi chỉ có Antigravity CLI. Web chỉ còn MỘT cách hiện lỗi "không model nào chạy được":
 * câu chung (MODEL_UNAVAILABLE_TEXT) + nút "Chọn model" → màn Thiết lập nguồn AI, lý do kỹ thuật trong "Chi tiết kỹ
 * thuật". Áp dụng cho bước 8 (thử trò chuyện), dịch và soạn lại bản nháp. Các dấu nhận diện AGY_ONLY_* đã bỏ khỏi web.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ApiError, type DraftDetail, type SetupState } from '@gen-harness/contracts';
import { GuideStepPage } from '../../src/guide/GuideStepPage';
import * as errorTextModule from '../../src/lib/errorText';
import { errorText } from '../../src/lib/errorText';
import * as friendlyErrorModule from '../../src/lib/friendlyError';
import { MODEL_UNAVAILABLE_TEXT } from '../../src/lib/friendlyError';
import { queryClient } from '../../src/lib/queryClient';
import { ActionError, CardError } from '../../src/screens/common';
import { WorkbenchScreen } from '../../src/screens/queue/WorkbenchScreen';

// Chuỗi THẬT của máy chủ v0.1.62 (gh/errors.py::model_unavailable): title theo việc + MODEL_UNAVAILABLE_HINT chung.
const TITLE = 'Chưa có model nào chạy được để dịch';
const HINT = 'Chưa có model AI nào hoạt động — vào API & Model (Hướng dẫn bước 4) để chọn hoặc sửa model.';
const REASON = 'Antigravity CLI: lỗi tạm';
const AI_SOURCE_HREF = '/setup?buoc=nguon-ai';

const unavailable = (reasons: string[] = [REASON]) => new ApiError(503, { title: TITLE, detail: HINT, code: 'MODEL_UNAVAILABLE', reasons } as never);
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

beforeEach(() => {
  document.cookie = 'gh_csrf=test-csrf';
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe('dấu nhận diện "chỉ có Antigravity CLI" đã bỏ khỏi web', () => {
  it('friendlyError / errorText không còn xuất AGY_ONLY_* / isAgyOnly* / agyOnly*', () => {
    const names = [...Object.keys(friendlyErrorModule), ...Object.keys(errorTextModule)];
    expect(names.filter((n) => /agy/i.test(n))).toEqual([]);
  });
});

describe('errorText / thẻ lỗi — không model nào chạy được (kể cả khi lý do là Antigravity CLI)', () => {
  it('errorText: luôn là câu chung, kể cả khi máy chủ cũ còn trả title "chỉ dành cho Gen của Sếp"', () => {
    expect(errorText(unavailable())).toBe(MODEL_UNAVAILABLE_TEXT);
    expect(errorText(unavailable([]))).toBe(MODEL_UNAVAILABLE_TEXT);
    const legacy = new ApiError(503, { title: 'Agent cần nguồn AI khác Antigravity CLI (chỉ dành cho Gen của Sếp)', code: 'MODEL_UNAVAILABLE', reasons: [] } as never);
    expect(errorText(legacy)).toBe(MODEL_UNAVAILABLE_TEXT);
  });

  it('CardError/ActionError: nút "Chọn model" → màn Thiết lập nguồn AI, lý do trong "Chi tiết kỹ thuật"', () => {
    for (const ui of [<CardError key="c" error={unavailable()} />, <ActionError key="a" error={unavailable()} />]) {
      const { unmount } = render(<MemoryRouter>{ui}</MemoryRouter>);
      const box = screen.getByTestId('model-unavailable');
      expect(box).toHaveTextContent(MODEL_UNAVAILABLE_TEXT);
      expect(within(box).getByRole('link', { name: /Chọn model/ })).toHaveAttribute('href', AI_SOURCE_HREF);
      expect(within(box).queryByRole('link', { name: /Thêm nguồn AI/ })).toBeNull();
      expect(within(box).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
      expect(box).toHaveTextContent(REASON);
      unmount();
    }
  });

  it('ActionError lỗi thường → dòng InlineError', () => {
    render(<MemoryRouter><ActionError error={new ApiError(409, { title: 'Bản nháp đã được duyệt', code: 'CONFLICT' } as never)} /></MemoryRouter>);
    expect(screen.getByRole('alert')).toHaveTextContent('Bản nháp đã được duyệt');
  });
});

// ── /guide/8 ─────────────────────────────────────────────────────────────
const state: SetupState = {
  finished: true,
  current_step: 12,
  steps: Array.from({ length: 12 }, (_, i) => ({
    n: i + 1, key: `s${i + 1}`, title: i + 1 === 8 ? 'Agent đầu tiên' : `Bước ${i + 1}`,
    required: i + 1 <= 4 || i + 1 === 12, status: i + 1 <= 4 || i + 1 === 12 ? 'done' : 'todo', available: true,
  })),
};

describe('/guide/8 — thử trò chuyện lỗi vì Antigravity CLI tạm lỗi', () => {
  it('hiện câu chung (không câu riêng "chỉ cho Gen"), nút Chọn model và lý do trong "Chi tiết kỹ thuật"', async () => {
    queryClient.clear();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
        const u = String(url);
        if (u.endsWith('/setup/steps/8') && init?.method === 'PUT') {
          const body = JSON.parse(String(init.body)) as { name: string };
          return json(200, { ...state, agent: { id: 'a1', name: body.name, try_reply: null, try_error: HINT, try_error_code: 'MODEL_UNAVAILABLE', try_reasons: [REASON] } });
        }
        if (u.includes('/auth/me')) return json(200, { role: { code: 'owner' } }); // GuideStepPage đọc /auth/me thật (review v0.1.68)
        if (u.includes('/setup/state')) return json(200, state);
        return json(200, []);
      }),
    );
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/guide/8']}>
          <Routes>
            <Route path="/guide/:n" element={<GuideStepPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText('Tên agent'), 'Thư ký');
    await user.type(screen.getByLabelText(/Vai trò/), 'Nhắc lịch');
    await user.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    const box = await screen.findByTestId('model-unavailable');
    expect(box).toHaveTextContent(MODEL_UNAVAILABLE_TEXT);
    expect(box).toHaveTextContent(REASON);
    expect(box).not.toHaveTextContent('chỉ dành cho Gen');
    expect(within(box).getByRole('link', { name: /Chọn model/ })).toHaveAttribute('href', AI_SOURCE_HREF);
  });
});

// ── Bàn duyệt: dịch / soạn lại ─────────────────────────────────────────
const DRAFT: DraftDetail = {
  id: 'd1', code: 'DR-1', kind: 'message', kind_label: 'Tin nhắn', title: 'Trả lời khách', agent: null, created_by: null,
  created_at: '2026-10-01T08:00:00Z', status: 'pending', hold_reason: null, subject: null,
  paragraphs: ['Chào anh'], text: 'Chào anh', lang: 'vi', target: null, amount_vnd: null, autonomy_level: 4,
  flags: { writes_external: true, personnel_related: false, over_threshold: false }, approve_label: 'Duyệt & gửi',
  sources: [], context: [], side_actions: [], decision: null, send_result: null, versions: [],
};

function renderWorkbench() {
  const body = { type: 'about:blank', title: TITLE, status: 503, detail: HINT, code: 'MODEL_UNAVAILABLE', reasons: [REASON] };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const u = String(url);
      if (u.includes('/drafts/d1/translate') || u.includes('/drafts/d1/regenerate')) return json(503, body);
      if (u.includes('/drafts/d1') && (init?.method ?? 'GET') === 'GET') return json(200, DRAFT);
      if (u.includes('/drafts')) return json(200, { items: [DRAFT], next_cursor: null, total: 1 });
      return json(200, []);
    }),
  );
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={['/workbench?id=d1']}>
        <WorkbenchScreen />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('Bàn duyệt — dịch / soạn lại lỗi khi chưa có model chạy được', () => {
  it('Dịch lỗi → câu chung + nút Chọn model', async () => {
    renderWorkbench();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /Dịch/ }));
    await user.click(screen.getByRole('radio', { name: 'EN' }));
    const box = await screen.findByTestId('model-unavailable');
    expect(box).toHaveTextContent(MODEL_UNAVAILABLE_TEXT);
    expect(within(box).getByRole('link', { name: /Chọn model/ })).toHaveAttribute('href', AI_SOURCE_HREF);
  });

  it('Soạn lại lỗi → không còn im lặng: hiện câu chung', async () => {
    renderWorkbench();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /Soạn lại/ }));
    const box = await screen.findByTestId('model-unavailable');
    expect(box).toHaveTextContent(MODEL_UNAVAILABLE_TEXT);
    expect(document.body.textContent).not.toContain('[object Object]');
  });
});
