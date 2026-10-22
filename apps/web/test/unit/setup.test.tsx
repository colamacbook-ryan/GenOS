import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { SetupState } from '@gen-harness/contracts';
import {
  addressingPreview,
  isComplete,
  passwordStrength,
  step1Errors,
  step2Errors,
  step3Errors,
  validateEmail,
  validatePassword,
  validatePin,
  validatePinConfirm,
} from '../../src/setup/validation';
import { isAvailable, isReachable, mergeSteps } from '../../src/setup/stepState';
import { missingRequiredSteps } from '../../src/setup/phase2Model';
import { SetupPage } from '../../src/setup/SetupPage';
import { queryClient } from '../../src/lib/queryClient';
import { LegacyWizard } from './legacy-wizard';

describe('setup validation', () => {
  it('step 1 needs a token', () => {
    expect(isComplete(step1Errors({ token: '', language: 'vi', mode: 'empty' }))).toBe(false);
    expect(isComplete(step1Errors({ token: '  ', language: 'vi', mode: 'empty' }))).toBe(false);
    expect(isComplete(step1Errors({ token: 'GH-1', language: 'en', mode: 'sample' }))).toBe(true);
  });

  it('passwords need ≥ 12 characters', () => {
    expect(validatePassword('short')).toMatch(/12 ký tự/);
    expect(validatePassword('a'.repeat(11))).not.toBeNull();
    expect(validatePassword('đủ-mười-hai!')).toBeNull();
  });

  it('PIN is exactly 6 digits and both entries must match', () => {
    expect(validatePin('12345')).not.toBeNull();
    expect(validatePin('12345a')).not.toBeNull();
    expect(validatePin('246810')).toBeNull();
    expect(validatePinConfirm('246810', '246811')).toMatch(/chưa khớp/);
    expect(validatePinConfirm('246810', '2468')).not.toBeNull();
    expect(validatePinConfirm('246810', '246810')).toBeNull();
  });

  it('email format', () => {
    expect(validateEmail('')).not.toBeNull();
    expect(validateEmail('sep@')).not.toBeNull();
    expect(validateEmail('sep@genesis.vn')).toBeNull();
  });

  it('step 2 completes only when every field is valid', () => {
    const ok = { token: 't', display_name: 'Anh Nguyễn Văn A', email: 'a@b.vn', password: 'mat-khau-dai-lam', pin: '246810', pin_confirm: '246810' };
    expect(isComplete(step2Errors(ok))).toBe(true);
    expect(Object.keys(step2Errors({ ...ok, pin_confirm: '000000' }))).toEqual(['pin_confirm']);
    expect(Object.keys(step2Errors({ ...ok, password: 'ngan' }))).toEqual(['password']);
    expect(Object.keys(step2Errors({ ...ok, token: '' }))).toEqual(['token']);
  });

  it('step 3 requires all five fields', () => {
    const ok = { org_name: 'Genesis', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND', self_name: 'Anh', bot_calls_me: 'Sếp' };
    expect(isComplete(step3Errors(ok))).toBe(true);
    expect(Object.keys(step3Errors({ ...ok, org_name: ' ' }))).toEqual(['org_name']);
  });

  it('strength meter grows with length and variety, capped below 12 chars', () => {
    expect(passwordStrength('').score).toBe(0);
    expect(passwordStrength('Ab1!Ab1!').score).toBeLessThanOrEqual(1);
    expect(passwordStrength('mot-cau-rat-dai-de-nho-2026').score).toBeGreaterThanOrEqual(3);
    expect(passwordStrength('aaaaaaaaaaaa').tone).toBe('bad');
  });

  it('addressing preview uses both names live', () => {
    expect(addressingPreview('Chị', 'Chị Hai')).toContain('Dạ Chị Hai');
    expect(addressingPreview('Chị', 'Chị Hai')).toContain('“Sáng nay chị cần xem gì?”');
    expect(addressingPreview('', '')).toContain('Dạ Sếp');
  });
});

const stateAt = (current: number, done: number[] = [], skipped: number[] = []): SetupState => ({
  finished: false,
  current_step: current,
  steps: Array.from({ length: 12 }, (_, i) => ({
    n: i + 1,
    key: `s${i + 1}`,
    title: `Bước ${i + 1}`,
    required: i + 1 <= 4 || i + 1 === 12,
    status: done.includes(i + 1) ? 'done' : skipped.includes(i + 1) ? 'skipped' : i + 1 === current ? 'doing' : 'todo',
  })),
});

describe('step state', () => {
  it('marks the current step as doing and keeps server titles', () => {
    const s = stateAt(2, [1]);
    s.steps[1].status = 'todo';
    const merged = mergeSteps(s);
    expect(merged[1].status).toBe('doing');
    expect(merged[0].status).toBe('done');
    expect(merged[3].title).toBe('Bước 4');
  });

  it('only settled or not-yet-passed steps are reachable', () => {
    const s = stateAt(3, [1, 2]);
    expect(isReachable(1, s)).toBe(true);
    expect(isReachable(3, s)).toBe(true);
    expect(isReachable(4, s)).toBe(false);
  });

  it('phase 2: steps 4–7 and 12 are available; 8–11 are passable to reach 12', () => {
    const s = stateAt(8, [1, 2, 3, 4, 5, 6, 7]);
    s.steps.forEach((x) => (x.available = [1, 2, 3, 4, 5, 6, 7, 12].includes(x.n)));
    const merged = mergeSteps(s);
    expect(merged.filter((m) => m.available).map((m) => m.n)).toEqual([1, 2, 3, 4, 5, 6, 7, 12]);
    expect(isAvailable(8, s)).toBe(false);
    expect(isReachable(12, s)).toBe(true);
    // A built step that is not settled blocks what comes after it.
    const at5 = stateAt(5, [1, 2, 3, 4]);
    expect(isReachable(5, at5)).toBe(true);
    expect(isReachable(6, at5)).toBe(false);
    expect(isReachable(12, at5)).toBe(false);
  });

  it('missingRequiredSteps lists what blocks PUT /setup/steps/12', () => {
    // Chỉ 1–4 bắt buộc: thiếu bước 4 là bị chặn, còn 5–11 để sau không chặn hoàn tất.
    const s = stateAt(12, [1, 2, 3], [5, 6, 7, 8, 9, 10, 11]);
    expect(missingRequiredSteps(s).map((m) => m.n)).toEqual([4]);
    expect(missingRequiredSteps(stateAt(12, [1, 2, 3, 4], [5, 6, 7, 8, 9, 10, 11]))).toEqual([]);
    expect(missingRequiredSteps(stateAt(12, [1, 2, 3, 4, 5, 6, 7, 8, 9], [10, 11]))).toEqual([]);
  });
});

// ── the wizard against a stubbed API ──────────────────────────────────────
function mockFetch(handler: (url: string, init: RequestInit) => Response | Promise<Response>) {
  const spy = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => handler(String(url), init ?? {}));
  vi.stubGlobal('fetch', spy);
  return spy;
}
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function renderSetup(path = '/setup') {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <SetupPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Khung thử cho bước 5–12 của máy chủ (SetupPage nay chỉ còn 3 bước) — xem legacy-wizard.tsx. */
function renderLegacy() {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/guide']}>
        <LegacyWizard />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Điền đủ form Tài khoản (bước 1 của Thiết lập một màn). */
async function fillAccount(user: ReturnType<typeof userEvent.setup>, opts: { org?: boolean } = {}) {
  await user.type(await screen.findByLabelText('Tên Sếp'), 'Anh Nguyễn Văn A');
  await user.type(screen.getByLabelText('Email'), 'owner@example.test');
  await user.type(screen.getByLabelText('Mật khẩu'), 'mot-cau-rat-dai-de-nho-2026');
  await user.click(screen.getByLabelText('Mã PIN (6 số) — chữ số 1/6'));
  await user.keyboard('246810');
  await user.click(screen.getByLabelText('Nhập lại mã PIN — chữ số 1/6'));
  await user.keyboard('246810');
  if (opts.org !== false) await user.type(screen.getByLabelText('Tên doanh nghiệp'), 'Genesis Trading');
}

describe('<SetupPage> — Thiết lập một màn, 3 bước', () => {
  beforeEach(() => {
    queryClient.clear();
    sessionStorage.clear();
    vi.unstubAllGlobals();
  });

  it('loads progress from GET /setup/state and shows the 3-step bar (no 12-step rail)', async () => {
    mockFetch(() => json(200, stateAt(1)));
    renderSetup();
    expect(await screen.findByRole('heading', { name: 'Tạo tài khoản Sếp' })).toBeInTheDocument();
    const bar = screen.getByRole('list', { name: 'Các bước thiết lập' });
    expect(within(bar).getAllByRole('listitem')).toHaveLength(3);
    expect(bar).toHaveTextContent('Tài khoản');
    expect(bar).toHaveTextContent('Nguồn AI');
    expect(bar).toHaveTextContent('Xong');
    expect(within(bar).getByText('Tài khoản').closest('li')).toHaveAttribute('aria-current', 'step');
    expect(screen.queryByText('Bước 1/12')).toBeNull();
    expect(screen.queryByLabelText('Các bước thiết lập', { selector: 'aside' })).toBeNull();
  });

  it('step 1: token prefilled from ?token= (no token field), Tiếp tục enabled only when the form is complete, PUT 1 sends the token', async () => {
    const user = userEvent.setup();
    const state = stateAt(1);
    const fetchSpy = mockFetch((url, init) => {
      if (url.endsWith('/setup/steps/1') && init.method === 'PUT') return json(200, stateAt(2, [1]));
      if (url.endsWith('/setup/steps/2') && init.method === 'PUT') return json(422, { status: 422, code: 'VALIDATION_ERROR', title: 'Dữ liệu chưa hợp lệ', errors: { email: 'Email đã được dùng.' } });
      return json(200, state);
    });
    renderSetup('/setup?token=GH-SETUP-7Q4K');
    await screen.findByRole('heading', { name: 'Tạo tài khoản Sếp' });
    expect(screen.queryByLabelText('Mã thiết lập')).toBeNull(); // mã đã có từ ?token= — chỉ hiện ô khi thiếu
    const next = screen.getByRole('button', { name: /Tiếp tục/ });
    expect(next).toBeDisabled();
    // v0.1.28 (UX V3): không còn lựa chọn English; v0.1.43 (F-23): không còn 'Cách bắt đầu' (dữ liệu mẫu).
    expect(screen.queryByRole('radio', { name: 'English' })).toBeNull();
    expect(screen.queryByText('Cách bắt đầu')).toBeNull();
    await fillAccount(user);
    expect(next).toBeEnabled();
    await user.click(next);

    const err = await screen.findByText('Email đã được dùng.');
    expect(err).toHaveClass('gh-field__error'); // lỗi gắn đúng ô Email
    const put = fetchSpy.mock.calls.find(([u, i]) => String(u).endsWith('/setup/steps/1') && i?.method === 'PUT')!;
    expect(JSON.parse(String(put[1]!.body))).toEqual({ token: 'GH-SETUP-7Q4K', language: 'vi', mode: 'empty' });
    expect(sessionStorage.getItem('gh_setup_token')).toBe('GH-SETUP-7Q4K');
  });

  it('step 1: 403 SETUP_TOKEN_INVALID shows an inline error on the (now visible) token field', async () => {
    const user = userEvent.setup();
    mockFetch((url, init) =>
      url.endsWith('/setup/steps/1') && init.method === 'PUT'
        ? json(403, { status: 403, code: 'SETUP_TOKEN_INVALID' })
        : json(200, stateAt(1)),
    );
    renderSetup('/setup?token=WRONG');
    await fillAccount(user);
    await user.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    const err = await screen.findByText(/không hợp lệ hoặc đã hết hạn/);
    expect(err).toHaveClass('gh-field__error');
    expect(screen.getByLabelText('Mã thiết lập')).toHaveAttribute('aria-invalid', 'true');
  });

  it('step 2 fields: Tiếp tục stays disabled until password ≥ 12 and both PINs match', async () => {
    const user = userEvent.setup();
    sessionStorage.setItem('gh_setup_token', 'GH-1');
    mockFetch(() => json(200, stateAt(2, [1])));
    renderSetup();
    await screen.findByRole('heading', { name: 'Tạo tài khoản Sếp' });
    const next = screen.getByRole('button', { name: /Tiếp tục/ });
    await user.type(screen.getByLabelText('Tên Sếp'), 'Anh Nguyễn Văn A');
    await user.type(screen.getByLabelText('Email'), 'owner@genesis.vn');
    await user.type(screen.getByLabelText('Tên doanh nghiệp'), 'Genesis Trading');
    await user.type(screen.getByLabelText('Mật khẩu'), 'ngan');
    await user.tab();
    expect(await screen.findByText(/ít nhất 12 ký tự \(hiện 4\)/)).toBeInTheDocument();
    await user.type(screen.getByLabelText('Mật khẩu'), '-nhung-da-du-dai');

    await user.click(screen.getByLabelText('Mã PIN (6 số) — chữ số 1/6'));
    await user.keyboard('246810');
    await user.click(screen.getByLabelText('Nhập lại mã PIN — chữ số 1/6'));
    await user.keyboard('246811');
    expect(next).toBeDisabled();
    await user.tab();
    expect(await screen.findByText('Hai lần nhập mã PIN chưa khớp.')).toBeInTheDocument();

    await user.click(screen.getByLabelText('Nhập lại mã PIN — chữ số 6/6'));
    await user.keyboard('{Backspace}0');
    expect(next).toBeEnabled();
  });

  it('optional steps (10–11, phase 4.6) can be skipped via POST /setup/steps/{n}/skip; required ones cannot', async () => {
    const user = userEvent.setup();
    let state = stateAt(10, [1, 2, 3, 4, 5, 6, 7, 8, 9]);
    mockFetch((url, init) => {
      if (url.endsWith('/setup/steps/10/skip') && init.method === 'POST') {
        state = stateAt(11, [1, 2, 3, 4, 5, 6, 7, 8, 9], [10]);
      }
      if (url.includes('/setup/')) return json(200, state);
      return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
    });
    renderLegacy();
    await screen.findByRole('heading', { name: 'Bước 10' });
    // v0.1.55 (G2): bước 10 là thẻ gợi ý — "Để sau" (bỏ qua) hoặc "Mời ngay" (mở form thật); không có nút Tiếp tục.
    expect(screen.getByTestId('step10-suggestion')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Mời ngay' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Tiếp tục/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Để sau' }));
    expect(await screen.findByRole('heading', { name: 'Bước 11' })).toBeInTheDocument();
    // Bước 11 "Sao lưu" có form thật, mặc định hằng ngày 02:00 — vẫn bỏ qua được.
    expect(screen.getByLabelText('Giờ chạy (HH:MM)')).toHaveValue('02:00');
    expect(screen.getByRole('button', { name: 'Để sau' })).toBeInTheDocument();
    // Bước 12 is required: no skip button
    queryClient.setQueryData(['setup', 'state'], stateAt(12, [1, 2, 3, 4, 5, 6, 7, 8, 9, 11], [10]));
    expect(await screen.findByRole('heading', { name: 'Bước 12' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Để sau' })).not.toBeInTheDocument();
  });

  it('bước 10: thêm người mời gửi đủ display_name/email/role, hiện mật khẩu tạm rồi mới sang bước 11', async () => {
    const user = userEvent.setup();
    const state = stateAt(10, [1, 2, 3, 4, 5, 6, 7, 8, 9]);
    let putBody: unknown = null;
    mockFetch((url, init) => {
      if (url.endsWith('/setup/steps/10') && init.method === 'PUT') {
        putBody = JSON.parse(String(init.body));
        // Backend thật đã đánh dấu xong và sang bước 11 ngay trong response này — trang chỉ giữ lại màn tóm
        // tắt mật khẩu tạm cho tới khi Sếp bấm "Đã lưu, sang bước sau".
        return json(200, { ...stateAt(11, [1, 2, 3, 4, 5, 6, 7, 8, 9], [10]), invited: [{ id: 'u1', display_name: 'Chị Hồng Quản', email: 'hong@genesis.vn', role: 'manager', temp_password: 'tmp-abc123' }] });
      }
      if (url.includes('/setup/')) return json(200, state);
      return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
    });
    renderLegacy();
    await screen.findByRole('heading', { name: 'Bước 10' });
    await user.click(screen.getByRole('button', { name: 'Mời ngay' }));
    // Form mời thật (giai đoạn 4.6). v0.1.68 (F038): danh sách rỗng KHÔNG phải "đã mời" — Tiếp tục tắt tới khi có người đủ tên + email.
    expect(screen.getByText('Chưa mời ai')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Thêm người' }));
    await user.type(screen.getByLabelText('Tên hiển thị'), 'Chị Hồng Quản');
    await user.type(screen.getByLabelText('Email'), 'hong@genesis.vn');
    expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: /Tiếp tục/ }));

    expect(await screen.findByText('tmp-abc123')).toBeInTheDocument();
    expect(putBody).toEqual({ invites: [{ display_name: 'Chị Hồng Quản', email: 'hong@genesis.vn', role: 'operator' }] });

    await user.click(screen.getByRole('button', { name: /Đã lưu, sang bước sau/ }));
    expect(await screen.findByRole('heading', { name: 'Bước 11' })).toBeInTheDocument();
  });

  it('step 5: 409 STEP_INCOMPLETE shows the server message, not a generic conflict', async () => {
    const user = userEvent.setup();
    const state = stateAt(5, [1, 2, 3, 4]);
    mockFetch((url, init) => {
      if (url.endsWith('/setup/steps/5') && init.method === 'PUT') {
        return json(409, { status: 409, code: 'STEP_INCOMPLETE', title: 'Cần kết nối ít nhất một kênh (quét mã QR) trước khi tiếp tục' });
      }
      if (url.includes('/channels')) {
        return json(200, [
          { type: 'zalo', name: 'Zalo', installed: true, id: 'c1', state: 'active', account_label: 'iPhone', started_at: null, groups_listening: 0, outbound_queued: 0, last_heartbeat_at: null, stats: null, qr: null },
        ]);
      }
      if (url.includes('/setup/')) return json(200, state);
      return json(200, []);
    });
    renderLegacy();
    const next = await screen.findByRole('button', { name: /Tiếp tục/ });
    await waitFor(() => expect(next).toBeEnabled());
    await user.click(next);
    expect(await screen.findByText('Cần kết nối ít nhất một kênh (quét mã QR) trước khi tiếp tục')).toBeInTheDocument();
  });

  it('shows an error state with Thử lại when the state cannot load', async () => {
    mockFetch(() => json(503, { status: 503, title: 'Service unavailable' }));
    queryClient.setDefaultOptions({ queries: { retry: false } });
    renderSetup();
    expect(await screen.findByText('Không tải được trạng thái thiết lập', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Thử lại/ })).toBeInTheDocument();
  });

  it('bước 8–9 có form thật: tạo agent, nghe thử trả lời, rồi chọn mức tự trị và xác nhận ranh giới', async () => {
    const user = userEvent.setup();
    let state = stateAt(8, [1, 2, 3, 4], [5, 6, 7]);
    const calls: Array<{ url: string; body: unknown }> = [];
    mockFetch((url, init) => {
      const body = init.body ? JSON.parse(String(init.body)) : undefined;
      if (url.endsWith('/setup/steps/8') && init.method === 'PUT') {
        calls.push({ url, body });
        state = stateAt(9, [1, 2, 3, 4, 8], [5, 6, 7]);
        return json(200, { ...state, agent: { id: 'a1', name: body.name, try_reply: 'Chào Sếp, em là Thư ký.', try_error: null } });
      }
      if (url.endsWith('/setup/hard-boundaries')) return json(200, ['Chỉ lưu tin của nhóm Sếp đã chọn nghe']);
      if (url.endsWith('/setup/steps/9') && init.method === 'PUT') {
        calls.push({ url, body });
        state = stateAt(10, [1, 2, 3, 4, 8, 9], [5, 6, 7]);
        return json(200, { ...state, hard_boundaries: [] });
      }
      if (url.includes('/setup/')) return json(200, state);
      return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
    });
    renderLegacy();
    await screen.findByRole('heading', { name: 'Bước 8' });
    await user.selectOptions(screen.getByLabelText('Mẫu'), 'secretary');
    expect(screen.getByLabelText('Tên agent')).toHaveValue('Thư ký');
    await user.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    expect(await screen.findByText('Chào Sếp, em là Thư ký.')).toBeInTheDocument();
    expect(calls[0].body).toMatchObject({ name: 'Thư ký', template: 'secretary' });
    await user.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    await screen.findByRole('heading', { name: 'Bước 9' });
    expect(await screen.findByText('Chỉ lưu tin của nhóm Sếp đã chọn nghe')).toBeInTheDocument();
    // v0.1.55 (G2): không còn ô tích — một dòng ghi chú, bấm Tiếp tục là đã đọc ranh giới (gửi ack_boundaries: true).
    expect(screen.getByTestId('step9-ack-note')).toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: /Tiếp tục/ }));
    await screen.findByRole('heading', { name: 'Bước 10' });
    expect(calls[1].body).toEqual({ autonomy_level: 4, ack_boundaries: true });
  });
});
