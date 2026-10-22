/**
 * v0.1.47 (F-79/F-85) — trang cảnh báo rủi ro gửi Facebook (`/social/ghi-facebook`, chỉ Owner): ô sandbox, danh sách rủi ro từ API,
 * "Tôi hiểu rủi ro và đồng ý" (POST kèm version), "Rút lại đồng ý", sandbox bật thì không cần đồng ý, lỗi API thân thiện.
 * Cộng thẻ "Gửi trả lời & tin nhắn" ở trang Tài khoản mạng xã hội (cổng Mở/Khoá, giới hạn gửi/ngày, lần gửi gần đây).
 */
import type { ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { SocialAccount, SocialPlatforms, SocialWriteGate, SocialWriteItem } from '@gen-harness/contracts';
import { SocialPage } from '../../src/social/SocialPage';
import { SocialWriteRiskPage } from '../../src/social/SocialWriteRiskPage';
import { qk } from '../../src/lib/queries';
import { MAYBE_SENT_NOTE, RETRY_HINT, fmtConsentTime, shortTarget, writeStatusView } from '../../src/social/socialModel';

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

interface Call {
  url: string;
  method: string;
  body: unknown;
}

const me = (role: string) => ({
  id: 'u', email: 'x@genesis.local', display_name: 'Anh Cơ', role: { code: role, name: role },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null,
  permissions: { 'system.read': 'all', 'system.manage': 'all' },
});

function renderPage(ui: ReactElement, role: string | null = 'owner') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (role) qc.setQueryData(qk.me, me(role));
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const RISKS = ['Trình duyệt nền chưa chạy được trong sandbox.', 'Facebook có thể hạn chế hoặc khoá tài khoản khi gửi tự động.'];
const PLATFORMS: SocialPlatforms = {
  items: [{
    key: 'facebook_personal', name: 'Facebook cá nhân', mode: 'browser', read_kinds: ['notifications'], write_kinds: ['reply_comment', 'send_message'],
    risk: ['x'], will_do: ['Gửi CHỈ sau khi Sếp bấm Xác nhận và nhập PIN.'], wont_do: ['Không đăng bài.'], risk_version: '2026-09-30',
  }],
  hard_rules: ['Không tạo tài khoản giả.'],
  risk_version: '2026-09-30',
};
const gate = (over: Partial<SocialWriteGate> = {}): SocialWriteGate => ({
  sandbox: { enabled: false, reason: 'Máy chủ không cho bật vùng cách ly của trình duyệt.', checked_at: '2026-10-03T01:00:00Z' },
  worker_online: true, consent: null, open: false, risk: RISKS, version: '2026-10-03', ...over,
});
const CONSENT = { accepted_at: '2026-10-03T01:30:00Z', accepted_by_name: 'Anh Cơ', version: '2026-10-03' };

function stub(state: { gate: SocialWriteGate; acceptError?: Response }) {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const c = { url: String(url), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined };
      calls.push(c);
      if (c.url.endsWith('/social/write-gate')) return json(200, state.gate);
      if (c.url.endsWith('/social/platforms')) return json(200, PLATFORMS);
      if (c.url.endsWith('/social/write-consent') && c.method === 'POST') {
        if (state.acceptError) return state.acceptError;
        state.gate = gate({ consent: CONSENT, open: true });
        return json(200, state.gate);
      }
      if (c.url.endsWith('/social/write-consent') && c.method === 'DELETE') {
        state.gate = gate();
        return json(200, state.gate);
      }
      return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
    }),
  );
  return calls;
}

describe('SocialWriteRiskPage', () => {
  it('chưa sandbox + chưa đồng ý: ô sandbox (lý do), danh sách rủi ro, sẽ/không làm và nút đồng ý — bấm → POST đúng version', async () => {
    const calls = stub({ gate: gate() });
    renderPage(<SocialWriteRiskPage />);
    expect(await screen.findByRole('heading', { level: 2, name: 'Gửi trả lời & tin nhắn Facebook — cảnh báo rủi ro' })).toBeInTheDocument();
    const box = await screen.findByTestId('write-sandbox');
    expect(box).toHaveTextContent('Chưa bật');
    expect(box).toHaveTextContent('Máy chủ không cho bật vùng cách ly của trình duyệt.');
    const list = screen.getByRole('list', { name: 'Rủi ro khi gửi lên Facebook' });
    for (const r of RISKS) expect(within(list).getByText(r)).toBeInTheDocument();
    expect(await screen.findByText('Gửi CHỈ sau khi Sếp bấm Xác nhận và nhập PIN.')).toBeInTheDocument();
    expect(screen.getByText('Không đăng bài.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Rút lại đồng ý' })).toBeNull();

    await userEvent.click(screen.getByRole('button', { name: 'Tôi hiểu rủi ro và đồng ý' }));
    const post = calls.find((c) => c.method === 'POST');
    expect(post?.url).toMatch(/\/social\/write-consent$/);
    expect(post?.body).toEqual({ version: '2026-10-03' });
    // Sau khi đồng ý: hiện thời điểm + phiên bản và nút rút lại; nút đồng ý biến mất.
    const info = await screen.findByTestId('write-consent-info');
    expect(info).toHaveTextContent('Sếp đã đồng ý lúc 08:30 03/10/2026 (phiên bản cảnh báo 2026-10-03)');
    expect(screen.getByRole('button', { name: 'Rút lại đồng ý' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Tôi hiểu rủi ro và đồng ý' })).toBeNull();
  });

  it('đã đồng ý: hiện thời điểm + "Rút lại đồng ý" (có hộp xác nhận) → DELETE, nút đồng ý quay lại', async () => {
    const calls = stub({ gate: gate({ consent: CONSENT, open: true }) });
    renderPage(<SocialWriteRiskPage />);
    expect(await screen.findByTestId('write-consent-info')).toHaveTextContent('Sếp đã đồng ý lúc 08:30 03/10/2026 (phiên bản cảnh báo 2026-10-03)');
    await userEvent.click(screen.getByRole('button', { name: 'Rút lại đồng ý' }));
    const dlg = await screen.findByRole('dialog', { name: 'Rút lại đồng ý?' });
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false); // chưa làm gì cho tới khi xác nhận
    await userEvent.click(within(dlg).getByRole('button', { name: 'Rút lại đồng ý' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.url.endsWith('/social/write-consent'))).toBe(true));
    expect(await screen.findByRole('button', { name: 'Tôi hiểu rủi ro và đồng ý' })).toBeInTheDocument();
  });

  it('sandbox bật: "không cần đồng ý thêm", không có nút đồng ý', async () => {
    stub({ gate: gate({ sandbox: { enabled: true, reason: null, checked_at: '2026-10-03T01:00:00Z' }, open: true }) });
    renderPage(<SocialWriteRiskPage />);
    expect(await screen.findByTestId('write-sandbox-on')).toHaveTextContent('Trình duyệt đang chạy trong sandbox — không cần đồng ý thêm');
    expect(screen.getByTestId('write-sandbox')).toHaveTextContent('Đã bật');
    expect(screen.queryByRole('button', { name: 'Tôi hiểu rủi ro và đồng ý' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Rút lại đồng ý' })).toBeNull();
  });

  it('trình duyệt nền chưa chạy → nói rõ, vẫn cho Sếp quyết', async () => {
    stub({ gate: gate({ worker_online: false, sandbox: { enabled: null, reason: null, checked_at: null } }) });
    renderPage(<SocialWriteRiskPage />);
    expect(await screen.findByTestId('write-sandbox')).toHaveTextContent('Trình duyệt nền chưa chạy');
    expect(screen.getByRole('button', { name: 'Tôi hiểu rủi ro và đồng ý' })).toBeInTheDocument();
  });

  it('lỗi API khi đồng ý → câu thân thiện + "Chi tiết kỹ thuật" (mã), không "[object Object]"', async () => {
    stub({ gate: gate(), acceptError: json(409, { status: 409, code: 'SOCIAL_CONSENT_VERSION', title: 'Cảnh báo rủi ro đã được cập nhật — tải lại trang rồi đọc lại' }) });
    const { container } = renderPage(<SocialWriteRiskPage />);
    await userEvent.click(await screen.findByRole('button', { name: 'Tôi hiểu rủi ro và đồng ý' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Cảnh báo rủi ro đã được cập nhật');
    expect(within(alert).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(alert).toHaveTextContent('SOCIAL_CONSENT_VERSION');
    expect(container.textContent).not.toContain('[object Object]');
  });

  it('không tải được cổng → thẻ lỗi có nút thử lại; vai trò khác Owner → "Chỉ Owner dùng được"', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(500, { status: 500, code: 'INTERNAL', title: 'Hệ thống gặp lỗi' })));
    const { unmount } = renderPage(<SocialWriteRiskPage />);
    expect(await screen.findByRole('button', { name: /Thử lại/ })).toBeInTheDocument();
    unmount();
    renderPage(<SocialWriteRiskPage />, 'manager');
    expect(await screen.findByText('Chỉ Owner dùng được')).toBeInTheDocument();
  });

  it('/auth/me lỗi (mạng/5xx) → thẻ lỗi có nút thử lại, KHÔNG báo "Chỉ Owner dùng được"', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(503, { status: 503, code: 'UNAVAILABLE', title: 'Máy chủ đang bận' })));
    renderPage(<SocialWriteRiskPage />, null);
    expect(await screen.findByRole('button', { name: /Thử lại/ })).toBeInTheDocument();
    expect(screen.queryByText('Chỉ Owner dùng được')).toBeNull();
  });

  it('sandbox bật → ghi chú rủi ro thiếu sandbox không áp dụng (không mâu thuẫn ô "Sandbox: Đã bật")', async () => {
    stub({ gate: gate({ sandbox: { enabled: true, reason: null, checked_at: '2026-10-03T01:00:00Z' }, open: true }) });
    renderPage(<SocialWriteRiskPage />);
    expect(await screen.findByTestId('write-risk-sandbox-note')).toHaveTextContent('rủi ro về việc thiếu sandbox bên dưới hiện không áp dụng');
  });
});

describe('socialModel — trạng thái việc gửi', () => {
  it('nhãn tiếng Việt cho từng trạng thái', () => {
    expect(writeStatusView({ status: 'queued' }).label).toBe('Đang chờ trình duyệt…');
    expect(writeStatusView({ status: 'running' }).label).toBe('Đang gửi trên Facebook…');
    expect(writeStatusView({ status: 'done', confirmed: true })).toMatchObject({ label: 'Đã gửi', notes: [], terminal: true });
    expect(writeStatusView({ status: 'done', confirmed: false, after_halt: true }).notes).toEqual([
      'Đã gửi trước khi kịp dừng',
      'Đã bấm gửi nhưng chưa thấy hiện trên trang — xem ảnh chụp',
    ]);
    expect(writeStatusView({ status: 'done', confirmed: true, after_cancel: true, has_proof: false, proof_error: 'PROOF_MISSING' }).notes).toEqual([
      'Đã gửi trước khi kịp huỷ / tạm dừng',
      'Đã gửi nhưng không chụp được ảnh bằng chứng — mở Facebook để kiểm tra.',
    ]);
    // ảnh đã xoá theo hạn lưu (has_proof=false, KHÔNG có proof_error) → không gắn nhãn sai "không chụp được ảnh"
    expect(writeStatusView({ status: 'done', confirmed: true, has_proof: false }).notes).toEqual([]);
    expect(writeStatusView({ status: 'done', confirmed: true, has_proof: false, result: { proof_error: 'PROOF_MISSING' } }).notes).toEqual([
      'Đã gửi nhưng không chụp được ảnh bằng chứng — mở Facebook để kiểm tra.',
    ]);
    expect(writeStatusView({ status: 'done', confirmed: false, send_error: true })).toMatchObject({
      tone: 'warn',
      notes: ['Có lỗi ngay sau khi bấm gửi — tin có thể đã đi. Mở Facebook kiểm tra trước khi gửi lại.'],
    });
    expect(writeStatusView({ status: 'halted' }).label).toBe('Đã dừng bằng Dừng tất cả — chưa gửi gì');
    expect(writeStatusView({ status: 'cancelled' }).label).toBe('Đã huỷ — chưa gửi gì');
    expect(writeStatusView({ status: 'failed' })).toMatchObject({ tone: 'bad', chip: 'Lỗi', notes: ['Hỏi Gen soạn lại nếu muốn gửi lần nữa.'] });
  });
  it('huỷ / dừng khi việc ĐANG chạy → không khẳng định "chưa gửi gì" (API vẫn nhận "done" đến muộn)', () => {
    const halted = writeStatusView({ status: 'halted', started_at: '2026-10-03T01:00:00Z' });
    expect(halted.label).not.toContain('chưa gửi gì');
    expect(halted.notes).toEqual([MAYBE_SENT_NOTE]);
    const cancelled = writeStatusView({ status: 'cancelled', started_at: '2026-10-03T01:00:00Z' });
    expect(cancelled).toMatchObject({ label: 'Đã huỷ', tone: 'warn', notes: [MAYBE_SENT_NOTE] });
  });
  it('WORKER_TIMEOUT sau khi đã chạy → "Không rõ", KHÔNG gợi ý soạn lại/gửi lại (tránh gửi hai lần)', () => {
    const v = writeStatusView({ status: 'failed', error: 'WORKER_TIMEOUT', started_at: '2026-10-03T01:00:00Z', error_text: 'Không rõ tin đã đi hay chưa …' });
    expect(v).toMatchObject({ label: 'Không rõ tin đã đi hay chưa', chip: 'Không rõ', tone: 'warn', notes: [] });
    expect(v.notes).not.toContain(RETRY_HINT);
    // chưa từng chạy (còn trong hàng đợi rồi hết hạn) → chắc chắn chưa gửi: gợi ý soạn lại như lỗi thường
    expect(writeStatusView({ status: 'failed', error: 'WORKER_TIMEOUT', started_at: null }).notes).toEqual([RETRY_HINT]);
  });
  it('câu lỗi của API đã có "Hỏi Gen …" → không lặp RETRY_HINT ngay bên dưới', () => {
    const v = writeStatusView({
      status: 'failed', error: 'PERMIT_INVALID',
      error_text: 'Giấy phép gửi không hợp lệ hoặc đã quá 5 phút — không gửi gì. Hỏi Gen soạn lại để gửi lần nữa.',
    });
    expect(v.notes).toEqual([]);
    expect(shortTarget(null)).toBe('(đã xoá theo hạn lưu)');
  });
  it('định dạng thời điểm đồng ý HH:mm dd/MM/yyyy theo múi giờ tổ chức', () => {
    expect(fmtConsentTime('2026-10-03T01:30:00Z', 'Asia/Ho_Chi_Minh')).toBe('08:30 03/10/2026');
  });
});

describe('SocialPage — thẻ "Gửi trả lời & tin nhắn"', () => {
  const ACC: SocialAccount = {
    id: 'a1', platform: 'facebook_personal', platform_name: 'Facebook cá nhân', mode: 'browser', label: 'Facebook của Sếp',
    external_handle: null, status: 'active', pause_reason: null, has_session: true, session_updated_at: null, last_health: null,
    risk_accepted_at: '2026-09-30T01:00:00Z', risk_version: '2026-09-30', schedule: { enabled: false, times: ['08:00', '17:00'] },
    daily_read_limit: 6, daily_write_limit: 10, writes_today: 3, last_read_at: null, created_at: '2026-09-30T01:00:00Z', active_job: null,
  };
  const WRITES: SocialWriteItem[] = Array.from({ length: 10 }, (_, i) => ({
    job_id: `j${i}`, account_id: 'a1', account_label: 'Facebook của Sếp', action: i % 2 ? 'send_message' : 'reply_comment',
    target_url: `https://www.facebook.com/permalink.php?story_fbid=${i}`, text: 'x', status: i === 0 ? 'failed' : 'done', error: i === 0 ? 'TARGET_NOT_FOUND' : null,
    error_text: i === 0 ? 'Không tìm thấy đúng bình luận/hội thoại trên trang — không gửi gì.' : null, created_at: `2026-10-03T0${i}:00:00Z`, finished_at: null, has_proof: i > 0, confirmed: true, after_halt: false,
  }));

  function stubSocial(over: { gate?: SocialWriteGate; writes?: SocialWriteItem[] } = {}) {
    const calls: Call[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
        const c = { url: String(url), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined };
        calls.push(c);
        const u = c.url;
        if (u.endsWith('/social/status'))
          return json(200, { halted: false, halted_at: null, worker: { version: 'x', at: '', running: 0 }, hard_rules: [], limits: { reads_per_day_max: 6, read_min_interval_minutes: 10, quiet_hours: [23, 6], concurrency_per_account: 1 } });
        if (u.endsWith('/social/platforms')) return json(200, PLATFORMS);
        if (u.endsWith('/social/accounts') && c.method === 'GET') return json(200, { items: [ACC] });
        if (u.endsWith('/social/write-gate')) return json(200, over.gate ?? gate());
        if (/\/social\/writes/.test(u)) return json(200, { items: over.writes ?? WRITES });
        if (/\/social\/accounts\/a1$/.test(u) && c.method === 'PATCH') return json(200, { ...ACC, daily_write_limit: (c.body as { daily_write_limit: number }).daily_write_limit });
        return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
      }),
    );
    return calls;
  }

  it('cổng Khoá + lý do, link trang cảnh báo, "Đã dùng x/y lượt gửi (24 giờ qua)", chọn giới hạn → PATCH, 10 lần gửi gần đây có "Xem ảnh chụp"; mô tả công tắc dừng', async () => {
    const calls = stubSocial();
    renderPage(<SocialPage />);
    const card = await screen.findByTestId('social-write-gate');
    const state = await within(card).findByTestId('social-write-gate-state');
    expect(state).toHaveTextContent('Khoá');
    expect(state).toHaveTextContent('Máy chủ không cho bật vùng cách ly của trình duyệt.');
    expect(within(card).getByRole('link', { name: /Đọc cảnh báo rủi ro/ })).toHaveAttribute('href', '/social/ghi-facebook');
    expect(await within(card).findByText('Đã dùng 3/10 lượt gửi (24 giờ qua)')).toBeInTheDocument();
    const recent = await within(card).findByRole('list', { name: 'Lần gửi gần đây' });
    expect(within(recent).getAllByRole('listitem')).toHaveLength(10);
    expect(within(recent).getAllByRole('button', { name: 'Xem ảnh chụp' })).toHaveLength(9);
    expect(recent).toHaveTextContent('Không tìm thấy đúng bình luận/hội thoại trên trang — không gửi gì.');
    expect(recent).toHaveTextContent('Hỏi Gen soạn lại nếu muốn gửi lần nữa.');
    expect(screen.getByTestId('social-kill-switch')).toHaveTextContent('Đóng ngay mọi trình duyệt nền, chặn cả ĐỌC và GỬI. Bật lại cần mã PIN.');

    const select = within(card).getByLabelText('Giới hạn gửi/ngày');
    expect(within(select).getAllByRole('option')).toHaveLength(20);
    await userEvent.selectOptions(select, '5');
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ daily_write_limit: 5 }));

    await userEvent.click(within(recent).getAllByRole('button', { name: 'Xem ảnh chụp' })[0]);
    expect(await screen.findByAltText('Ảnh chụp bằng chứng lần gửi')).toHaveAttribute('src', '/api/v1/social/jobs/j1/proof');
  });

  it('lần gửi đã bị dọn theo hạn lưu (target_url/text null) và ghi chú trạng thái → trang không vỡ, hiện ghi chú', async () => {
    stubSocial({
      writes: [
        { ...WRITES[1], job_id: 'old', target_url: null, text: null, has_proof: false },
        { ...WRITES[2], job_id: 'late', confirmed: false, after_halt: true, has_proof: true },
      ],
    });
    renderPage(<SocialPage />);
    const recent = await screen.findByRole('list', { name: 'Lần gửi gần đây' });
    expect(recent).toHaveTextContent('(đã xoá theo hạn lưu)');
    // ảnh quá 90 ngày bị xoá (has_proof=false, không proof_error) ≠ "không chụp được ảnh"
    expect(recent).not.toHaveTextContent('Đã gửi nhưng không chụp được ảnh bằng chứng');
    expect(recent).toHaveTextContent('Đã gửi trước khi kịp dừng');
    expect(recent).toHaveTextContent('Đã bấm gửi nhưng chưa thấy hiện trên trang — xem ảnh chụp');
    expect(screen.getByTestId('social-kill-switch')).toBeInTheDocument();       // "Dừng tất cả" vẫn còn
  });

  it('không chụp được ảnh (proof_error PROOF_MISSING) / không rõ đã gửi (WORKER_TIMEOUT sau khi chạy) / lỗi đã có "Hỏi Gen"', async () => {
    stubSocial({
      writes: [
        { ...WRITES[1], job_id: 'nocap', has_proof: false, proof_error: 'PROOF_MISSING' },
        {
          ...WRITES[2], job_id: 'lost', status: 'failed', error: 'WORKER_TIMEOUT', started_at: '2026-10-03T02:00:01Z', confirmed: null,
          has_proof: false, error_text: 'Không rõ tin đã đi hay chưa (trình duyệt mất liên lạc giữa chừng) — mở Facebook kiểm tra trước khi gửi lại.',
        },
        {
          ...WRITES[0], job_id: 'permit', error: 'PERMIT_INVALID',
          error_text: 'Giấy phép gửi không hợp lệ hoặc đã quá 5 phút — không gửi gì. Hỏi Gen soạn lại để gửi lần nữa.',
        },
      ],
    });
    renderPage(<SocialPage />);
    const recent = await screen.findByRole('list', { name: 'Lần gửi gần đây' });
    expect(recent).toHaveTextContent('Đã gửi nhưng không chụp được ảnh bằng chứng — mở Facebook để kiểm tra.');
    const [, lost, permit] = within(recent).getAllByRole('listitem');
    expect(lost).toHaveTextContent('Không rõ');
    expect(lost).toHaveTextContent('mở Facebook kiểm tra trước khi gửi lại');
    expect(lost).not.toHaveTextContent('Hỏi Gen');
    expect(lost).not.toHaveTextContent('thử lại');
    expect(permit.textContent?.match(/Hỏi Gen/g)).toHaveLength(1);
  });

  it('cổng Mở (đã đồng ý) → "Mở"', async () => {
    stubSocial({ gate: gate({ consent: CONSENT, open: true }) });
    renderPage(<SocialPage />);
    const state = await screen.findByTestId('social-write-gate-state');
    await waitFor(() => expect(state).toHaveAttribute('data-open', 'true'));
    expect(state).toHaveTextContent('Mở');
    expect(state).toHaveTextContent('Sếp đã đồng ý rủi ro');
  });
});
