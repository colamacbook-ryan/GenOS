/**
 * v0.1.30 (hotfix) — "Minified React error #31 … object with keys {reasons}".
 *
 * Máy chủ ≤ v0.1.29 trả `try_error = {reasons: […]}` (đối tượng) ở PUT /setup/steps/8 khi không model nào chạy được,
 * và `detail = {reasons: […]}` ở 503 MODEL_UNAVAILABLE. Màn /guide/8 vẽ thẳng đối tượng làm React child → sập.
 * Giờ mọi `detail` không phải chuỗi đều thành chữ, và "không có model" có nút "Chọn model".
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ApiError, type SetupState } from '@gen-harness/contracts';
import { GuideStepPage } from '../../src/guide/GuideStepPage';
import { errorText } from '../../src/lib/errorText';
import { MODEL_UNAVAILABLE_TEXT, detailToText, friendlyError } from '../../src/lib/friendlyError';
import { queryClient } from '../../src/lib/queryClient';
import { CardError, FriendlyErrorText } from '../../src/screens/common';

const REASONS = ['gemini: 429', 'deepseek: đang ngắt mạch'];

describe('detail không phải chuỗi → chữ đọc được', () => {
  it('detailToText: {reasons}, mảng lỗi FastAPI, {code,message}, lồng nhau', () => {
    expect(detailToText({ reasons: REASONS })).toBe(`${MODEL_UNAVAILABLE_TEXT} (gemini: 429; deepseek: đang ngắt mạch)`);
    expect(detailToText([{ loc: ['body', 'name'], msg: 'Field required', type: 'missing' }])).toBe('name: Field required');
    expect(detailToText({ code: 'X', message: 'Không làm được' })).toBe('Không làm được');
    expect(detailToText({ detail: { msg: 'sâu' } })).toBe('sâu');
    expect(detailToText(null)).toBe('');
    expect(typeof detailToText({ a: 1 })).toBe('string');
  });

  it('friendlyError nhận đối tượng {reasons}: câu cố định + lý do trong chi tiết kỹ thuật', () => {
    expect(friendlyError({ reasons: REASONS })).toEqual({ message: MODEL_UNAVAILABLE_TEXT, detail: 'gemini: 429; deepseek: đang ngắt mạch' });
  });

  it('ApiError: message luôn là chuỗi; reasons đọc được ở khuôn mới lẫn khuôn cũ', () => {
    const legacy = new ApiError(503, { code: 'MODEL_UNAVAILABLE', title: 'Chưa có model nào chạy được để dịch', detail: { reasons: REASONS } });
    expect(legacy.message).toBe('Chưa có model nào chạy được để dịch');
    expect(legacy.reasons).toEqual(REASONS);
    const next = new ApiError(503, { code: 'MODEL_UNAVAILABLE', title: 't', detail: 'Chưa có model AI nào hoạt động', reasons: REASONS });
    expect(next.reasons).toEqual(REASONS);
    const fastapi = new ApiError(422, { detail: [{ loc: ['body', 'x'], msg: 'bad' }] as unknown as string });
    expect(fastapi.message).toBe('bad');
    expect(errorText(legacy)).toBe(MODEL_UNAVAILABLE_TEXT);
  });

  it('FriendlyErrorText vẽ đối tượng {reasons} không sập', () => {
    render(<FriendlyErrorText raw={{ reasons: REASONS }} />);
    expect(screen.getByText(MODEL_UNAVAILABLE_TEXT)).toBeInTheDocument();
  });

  it('CardError với MODEL_UNAVAILABLE → trạng thái "Chọn model" (link /setup?buoc=nguon-ai, v0.1.59) + chi tiết kỹ thuật', () => {
    const e = new ApiError(503, { code: 'MODEL_UNAVAILABLE', title: 't', detail: { reasons: REASONS } });
    render(
      <MemoryRouter>
        <CardError error={e} />
      </MemoryRouter>,
    );
    expect(screen.getByText(MODEL_UNAVAILABLE_TEXT)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Chọn model/ })).toHaveAttribute('href', '/setup?buoc=nguon-ai');
    expect(screen.getByText('gemini: 429; deepseek: đang ngắt mạch')).toBeInTheDocument();
  });
});

// ── /guide/8 — đường sập thật ─────────────────────────────────────────────
const state: SetupState = {
  finished: true,
  current_step: 12,
  steps: Array.from({ length: 12 }, (_, i) => ({
    n: i + 1,
    key: `s${i + 1}`,
    title: i + 1 === 8 ? 'Agent đầu tiên' : `Bước ${i + 1}`,
    required: i + 1 <= 4 || i + 1 === 12,
    status: i + 1 <= 4 || i + 1 === 12 ? 'done' : 'todo',
    available: true,
  })),
};
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function renderGuide8(agent: Record<string, unknown>) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const u = String(url);
      if (u.endsWith('/setup/steps/8') && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as { name: string };
        return json(200, { ...state, agent: { id: 'a1', name: body.name, try_reply: null, ...agent } });
      }
      if (u.includes('/auth/me')) return json(200, { role: { code: 'owner' } }); // GuideStepPage đọc /auth/me thật (review v0.1.68)
      if (u.includes('/setup/state')) return json(200, state);
      return json(200, []);
    }),
  );
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/guide/8']}>
        <Routes>
          <Route path="/guide/:n" element={<GuideStepPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function createAgent() {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText('Tên agent'), 'Thư ký');
  await user.type(screen.getByLabelText(/Vai trò/), 'Nhắc lịch');
  await user.click(screen.getByRole('button', { name: /Tiếp tục/ }));
}

describe('/guide/8 — thử trò chuyện khi không có model', () => {
  beforeEach(() => {
    queryClient.clear();
    vi.unstubAllGlobals();
  });

  it('máy chủ cũ trả try_error = {reasons} (đối tượng): không sập, hiện "Chọn model"', async () => {
    renderGuide8({ try_error: { reasons: REASONS } });
    await createAgent();
    expect(await screen.findByText(/Đã tạo agent/)).toBeInTheDocument();
    expect(screen.getByText(MODEL_UNAVAILABLE_TEXT)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Chọn model/ })).toHaveAttribute('href', '/setup?buoc=nguon-ai');
  });

  it('máy chủ v0.1.30: try_error chuỗi + try_error_code + try_reasons', async () => {
    renderGuide8({ try_error: 'Chưa có model AI nào hoạt động', try_error_code: 'MODEL_UNAVAILABLE', try_reasons: REASONS });
    await createAgent();
    expect(await screen.findByTestId('model-unavailable')).toBeInTheDocument();
    expect(screen.getByText('gemini: 429; deepseek: đang ngắt mạch')).toBeInTheDocument();
  });
});
