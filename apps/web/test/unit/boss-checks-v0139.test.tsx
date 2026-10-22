import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { BossCheck, BossCheckKey, BossOverview, CliProfile, HubLink, SocialAccount } from '@gen-harness/contracts';
import { BossChecksPage } from '../../src/guide/BossChecksPage';
import { queryClient } from '../../src/lib/queryClient';
import { qk } from '../../src/lib/queries';
import { TELEGRAM_KEY } from '../../src/screens/connections/telegramModel';

/**
 * v0.1.39 (F-74) — trang "Việc Sếp cần làm": ô kết quả ngay cạnh, kết quả đọc từ `GET /boss-checks`. v0.1.44 (F-8c):
 * dòng 6 Telegram. v0.1.46 (F-21): dòng 7 Truy cập từ xa. v0.1.55 (Thiết lập gọn): thêm dòng 0 "nguồn AI" — dòng BẮT BUỘC DUY NHẤT
 * (đếm x/1, `required_total` do máy chủ trả); mọi dòng kết nối khác "Không bắt buộc"; dòng Google bỏ bài kiểm đổi qua lại hai tài khoản.
 */

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

interface Call {
  path: string;
  method: string;
  body: unknown;
}

const me = (role: string) => ({
  id: 'u', email: 'x@genesis.local', display_name: 'Anh Cơ', role: { code: role, name: role },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null,
  permissions: { 'system.read': 'all', 'system.manage': role === 'owner' ? 'all' : 'none' },
});

const EMPTY = { hub: null, facebook: null, agy_login: null, agy_call: null, agy_switch: null, claude_login: null, claude_call: null, jev: null, telegram: null, remote_access: null, ai_source: null };
const ROWS: BossOverview['rows'] = [
  { row: 0, key: 'ai', title: 'Có ít nhất 1 nguồn AI chạy được', optional: false, checks: ['ai_source'], done: false },
  { row: 1, key: 'hub', title: 'Nối Gen-hub', optional: true, checks: ['hub'], done: false },
  { row: 2, key: 'facebook', title: 'Kết nối Facebook', optional: true, checks: ['facebook'], done: false },
  { row: 3, key: 'agy', title: 'Google', optional: true, checks: ['agy_login', 'agy_call'], done: false },
  { row: 4, key: 'claude', title: 'Claude Code', optional: true, checks: ['claude_login', 'claude_call'], done: false },
  { row: 5, key: 'jev', title: 'Jev', optional: true, checks: ['jev'], done: false },
  { row: 6, key: 'telegram', title: 'Telegram (báo động & bản tin)', optional: true, checks: ['telegram'], done: false },
  { row: 7, key: 'remote', title: 'Truy cập từ xa', optional: true, checks: ['remote_access'], done: false },
];
const SAVED: HubLink = {
  configured: true, enabled: false, status: 'off', server_id: 's1', endpoint: 'https://hub.example.test/mcp', has_token: true,
  allow_public_network: true, token_expires_at: null, days_left: null, last_ok_at: null, last_error: null, health: null,
};
const AN: CliProfile = { id: 'p-an', email: 'an@genesis.vn', plan_label: null, active: false, expires_at: null, state: 'ok' };
const BINH: CliProfile = { id: 'p-binh', email: 'binh@genesis.vn', plan_label: null, active: true, expires_at: null, state: 'ok' };
const FB = { id: 'fb-1', platform: 'facebook_personal', label: 'Facebook của Sếp', status: 'active' } as SocialAccount;

const check = (key: BossCheckKey, status: BossCheck['status'], extra: Partial<BossCheck> = {}): BossCheck => ({
  key, status, error_code: null, message: null, detail: {}, checked_at: '2026-10-02T07:05:00Z', runs: 1, ...extra,
});

interface World {
  results: Record<string, BossCheck | null>;
  link: HubLink;
  accounts: SocialAccount[];
  agy: CliProfile[];
  claude: CliProfile[];
  /** Số lần agy_switch 'pass' (v0.1.68: máy chủ không còn trả `switch_passes`; giữ để dựng bài kiểm cũ). */
  switchPasses: number;
  providers: Array<{ id: string; kind: string; name: string }>;
  run: (key: string, body: Record<string, unknown>) => BossCheck;
  /** Gọi trước mỗi GET /boss-checks (đổi pending → pass). */
  onList?: (n: number) => void;
  /** GET /providers trả lỗi. */
  failProviders?: boolean;
  /** GET /auth/me trả lỗi (dùng với `renderPage(role, false)`). */
  failMe?: boolean;
}

function setup(w: Partial<World> = {}) {
  const world: World = {
    results: { ...EMPTY }, link: SAVED, accounts: [], agy: [], claude: [], switchPasses: 0, providers: [],
    run: (key) => check(key as BossCheckKey, 'pass'), ...w,
  };
  const calls: Call[] = [];
  let lists = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const u = new URL(String(url), 'http://x');
      const path = u.pathname.replace('/api/v1', '');
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ path, method, body });
      if (path === '/boss-checks' && method === 'GET') {
        world.onList?.(++lists);
        return json(200, { rows: ROWS, results: world.results, required_done: 0, required_total: 1 });
      }
      const run = /^\/boss-checks\/([a-z_]+)\/run$/.exec(path);
      if (run && method === 'POST') {
        const c = world.run(run[1], body ?? {});
        // Như máy chủ: lỗi tạm không được ghi.
        if (!c.transient) world.results[c.key] = c;
        if (c.key === 'agy_switch' && c.status === 'pass') world.switchPasses += 1;
        return json(200, c);
      }
      if (path === '/hub/link' && method === 'GET') return json(200, world.link);
      if (path === '/hub/link' && method === 'PATCH') {
        world.link = { ...world.link, ...body, has_token: true };
        delete (world.link as unknown as Record<string, unknown>).token;
        return json(200, world.link);
      }
      if (path === '/social/accounts') return json(200, { items: world.accounts });
      if (path === '/cli/profiles') return json(200, u.searchParams.get('kind') === 'claude_code_cli' ? world.claude : world.agy);
      if (path === '/providers') return world.failProviders ? json(409, { code: 'PROVIDERS_DOWN', title: 'Không đọc được danh sách nguồn AI' }) : json(200, world.providers);
      if (path === '/auth/me') return world.failMe ? json(409, { code: 'ME_DOWN', title: 'Không đọc được tài khoản' }) : json(200, me('owner'));
      return json(404, { code: 'NOT_FOUND', title: 'Không tồn tại' });
    }),
  );
  return { world, calls };
}

function renderPage(role = 'owner', prefillMe = true) {
  if (prefillMe) queryClient.setQueryData(qk.me, me(role));
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/guide/viec-sep']}>
        <BossChecksPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const row = (name: string) => screen.getByRole('region', { name });

beforeEach(() => queryClient.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  queryClient.clear();
});

describe('Việc Sếp cần làm (/guide/viec-sep)', () => {
  it('đúng 8 dòng: chỉ dòng "nguồn AI" bắt buộc, 7 dòng kết nối còn lại "Không bắt buộc"; mỗi dòng có ô kết quả "Chưa kiểm"', async () => {
    setup();
    renderPage();
    expect(await screen.findByText('Đã đạt 0/1 dòng bắt buộc')).toBeInTheDocument();
    const names = ['Có ít nhất 1 nguồn AI chạy được', 'Nối Gen-hub', 'Kết nối Facebook', 'Google (Antigravity)', 'Claude Code CLI', 'Jev', 'Telegram (báo động & bản tin)', 'Truy cập từ xa'];
    expect(screen.getAllByRole('region').map((r) => r.getAttribute('aria-label'))).toEqual(names);
    expect(within(row('Có ít nhất 1 nguồn AI chạy được')).queryByText('Không bắt buộc')).toBeNull();
    for (const n of names.slice(1)) expect(within(row(n)).getByText('Không bắt buộc'), n).toBeInTheDocument();
    expect(within(row('Nối Gen-hub')).getByText('Chưa kiểm')).toBeInTheDocument();
    expect(within(row('Có ít nhất 1 nguồn AI chạy được')).getByText('Chưa kiểm')).toBeInTheDocument();
    expect(screen.getByText('Kết quả được lưu lại — Claude tự đọc, Sếp không cần chụp màn hình.')).toBeInTheDocument();
    expect(document.title).toBe('Việc Sếp cần làm · Hướng dẫn thiết lập · Gen-Harness');
  });

  it('Gen-hub: nhập token → Kiểm tra = PATCH /hub/link rồi POST /boss-checks/hub/run; "Đạt" hiện ngay cạnh dòng', async () => {
    const { calls } = setup();
    renderPage();
    const user = userEvent.setup();
    const hub = await screen.findByRole('region', { name: 'Nối Gen-hub' });
    const token = await within(hub).findByLabelText('Token mới (bỏ trống để giữ)');
    await user.type(token, 'ghtok_Moi_123456789');
    await user.click(within(hub).getByRole('button', { name: 'Kiểm tra' }));
    await waitFor(() => expect(within(hub).getByText(/^Đạt · \d\d:\d\d \d\d\/\d\d$/)).toBeInTheDocument());
    const writes = calls.filter((c) => c.method !== 'GET').map((c) => `${c.method} ${c.path}`);
    expect(writes).toEqual(['PATCH /hub/link', 'POST /boss-checks/hub/run']);
    const patch = calls.find((c) => c.method === 'PATCH')!.body as { token: string; token_expires_at: string };
    expect(patch.token).toBe('ghtok_Moi_123456789');
    // Token mới = token 90 ngày → gửi kèm hạn (nhắc trước 14 ngày chạy đúng ngày, không giữ hạn cũ).
    const days = (Date.parse(patch.token_expires_at) - Date.now()) / 86_400_000;
    expect(days).toBeGreaterThan(89.9);
    expect(days).toBeLessThan(90.1);
    expect(Object.keys(patch).sort()).toEqual(['token', 'token_expires_at']);
    expect(token).toHaveValue('');
    expect(document.body.innerHTML).not.toContain('ghtok_Moi_123456789');
  });

  it('Gen-hub lỗi → câu thân thiện + "Chi tiết kỹ thuật" có mã lỗi, không render object', async () => {
    setup({
      run: (key) => check(key as BossCheckKey, 'fail', { error_code: 'HUB_TOKEN_REJECTED', message: 'HTTP 401 Unauthorized', detail: { http: { status: 401 } } }),
    });
    const { container } = renderPage();
    const user = userEvent.setup();
    const hub = await screen.findByRole('region', { name: 'Nối Gen-hub' });
    await user.click(await within(hub).findByRole('button', { name: 'Kiểm tra' }));
    const res = await within(hub).findByText(/^Lỗi · Gen-hub từ chối token/);
    expect(res).toBeInTheDocument();
    expect(within(hub).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(within(hub).getByText('Mã lỗi HUB_TOKEN_REJECTED')).toBeInTheDocument();
    expect(container.innerHTML).not.toContain('[object Object]');
  });

  it('Facebook: "Đọc ngay" → "Đang chạy…" rồi "Đạt" sau khi tải lại', async () => {
    let fbRuns = 0;
    const { world, calls } = setup({
      accounts: [FB],
      run: (key) => {
        fbRuns += 1;
        return check(key as BossCheckKey, 'pending', { checked_at: new Date().toISOString() });
      },
    });
    let pendingLists = 0;
    world.onList = () => {
      if (world.results.facebook?.status === 'pending' && ++pendingLists > 1) world.results.facebook = check('facebook', 'pass');
    };
    renderPage();
    const user = userEvent.setup();
    const fb = await screen.findByRole('region', { name: 'Kết nối Facebook' });
    await user.click(await within(fb).findByRole('button', { name: 'Đọc ngay' }));
    expect(await within(fb).findByText('Đang chạy…')).toBeInTheDocument();
    await waitFor(() => expect(within(fb).getByText(/^Đạt · /)).toBeInTheDocument(), { timeout: 6000 });
    expect(fbRuns).toBe(1);
    expect(calls.find((c) => c.path === '/boss-checks/facebook/run')!.body).toEqual({ account_id: 'fb-1' });
  }, 10_000);

  it('Facebook chưa có tài khoản → nút mở trang Tài khoản mạng xã hội', async () => {
    setup();
    renderPage();
    const fb = await screen.findByRole('region', { name: 'Kết nối Facebook' });
    expect(await within(fb).findByRole('link', { name: /Mở trang Tài khoản mạng xã hội/ })).toHaveAttribute('href', '/social');
    expect(within(fb).getByText(/Đăng nhập ngay trong app/)).toBeInTheDocument();
    expect(within(fb).queryByRole('button', { name: 'Đọc ngay' })).toBeNull();
  });

  it('Google: Gọi thử báo tài khoản đang dùng; KHÔNG còn bài kiểm đổi qua lại (đổi tài khoản làm ở Kết nối)', async () => {
    const { calls } = setup({
      agy: [BINH, AN],
      switchPasses: 1,
      run: (key) => check(key as BossCheckKey, 'pass', { account: 'binh@genesis.vn' }),
    });
    renderPage();
    const user = userEvent.setup();
    const agy = await screen.findByRole('region', { name: 'Google (Antigravity)' });
    await user.click(await within(agy).findByRole('button', { name: 'Gọi thử' }));
    expect(await within(agy).findByText('Đạt · đang dùng binh@genesis.vn')).toBeInTheDocument();
    for (const label of ['Đăng nhập', 'Gọi thử']) expect(within(agy).getAllByText(label).length).toBeGreaterThan(0);
    expect(within(agy).queryByText('Đổi tài khoản')).toBeNull();
    expect(within(agy).queryByRole('button', { name: /^Đổi sang / })).toBeNull();
    expect(within(agy).queryByText(/Đã đổi qua lại/)).toBeNull();
    expect(within(agy).getByText(/Đang dùng: binh@genesis.vn/)).toBeInTheDocument();
    expect(within(agy).getByRole('link', { name: /Thêm \/ đổi tài khoản ở Thiết lập nguồn AI/ })).toHaveAttribute('href', '/setup?buoc=nguon-ai');
    expect(calls.some((c) => c.path === '/boss-checks/agy_switch/run')).toBe(false);
    // Có 2 hồ sơ: không còn mời đăng nhập lần đầu.
    expect(within(agy).queryByRole('button', { name: 'Đăng nhập Google' })).toBeNull();
  });

  it('Google chưa có hồ sơ → "Đăng nhập Google"; đã có hồ sơ → "Gọi thử" (không còn mời thêm tài khoản thứ hai)', async () => {
    setup();
    const { unmount } = renderPage();
    const agy = await screen.findByRole('region', { name: 'Google (Antigravity)' });
    expect(await within(agy).findByRole('button', { name: 'Đăng nhập Google' })).toBeInTheDocument();
    unmount();
    queryClient.clear();
    setup({ agy: [BINH] });
    renderPage();
    const agy2 = await screen.findByRole('region', { name: 'Google (Antigravity)' });
    expect(await within(agy2).findByRole('button', { name: 'Gọi thử' })).toBeInTheDocument();
    expect(within(agy2).queryByRole('button', { name: 'Thêm tài khoản thứ hai' })).toBeNull();
    expect(within(agy2).queryByRole('button', { name: 'Đăng nhập Google' })).toBeNull();
  });

  it('Claude Code: cảnh báo rủi ro + "Đăng nhập Claude Code" + Gọi thử', async () => {
    setup();
    renderPage();
    const cl = await screen.findByRole('region', { name: 'Claude Code CLI' });
    expect(within(cl).getByTestId('claude-risk')).toBeInTheDocument();
    expect(await within(cl).findByRole('button', { name: 'Đăng nhập Claude Code' })).toBeInTheDocument();
    expect(within(cl).getByRole('button', { name: 'Gọi thử' })).toBeInTheDocument();
  });

  it('Jev: chưa cấu hình → link "Nhập khoá Jev"; đã có kết quả → không còn nút "Kiểm tra 1 lần"', async () => {
    setup();
    const { unmount } = renderPage();
    const jev = await screen.findByRole('region', { name: 'Jev' });
    expect(await within(jev).findByRole('link', { name: /Nhập khoá Jev/ })).toHaveAttribute('href', '/system?tab=brain#jev');
    unmount();
    queryClient.clear();

    setup({ providers: [{ id: 'j', kind: 'system_one', name: 'Jev' }] });
    const second = renderPage();
    const jev2 = await screen.findByRole('region', { name: 'Jev' });
    expect(await within(jev2).findByRole('button', { name: 'Kiểm tra 1 lần' })).toBeInTheDocument();
    second.unmount();
    queryClient.clear();

    setup({
      providers: [{ id: 'j', kind: 'system_one', name: 'Jev' }],
      results: { ...EMPTY, jev: check('jev', 'fail', { error_code: 'JEV_ERROR', message: 'upstream 500' }) },
    });
    renderPage();
    const jev3 = await screen.findByRole('region', { name: 'Jev' });
    expect(await within(jev3).findByText('Lỗi — thẻ Jev sẽ ẩn, không cần làm thêm')).toBeInTheDocument();
    expect(within(jev3).queryByRole('button', { name: 'Kiểm tra 1 lần' })).toBeNull();
  });

  it('tải lại: máy chủ chỉ trả email đã che (detail.account_masked) → vẫn báo đúng tài khoản; Jev lỗi khác vẫn ẩn thẻ', async () => {
    setup({
      agy: [BINH, AN],
      providers: [{ id: 'j', kind: 'system_one', name: 'Jev' }],
      results: {
        ...EMPTY,
        agy_call: check('agy_call', 'pass', { detail: { account_masked: 'b***@genesis.vn' } }),
        jev: check('jev', 'fail', { error_code: 'PROVIDER_ERROR', message: 'upstream 500' }),
      },
      switchPasses: 2,
    });
    renderPage();
    const agy = await screen.findByRole('region', { name: 'Google (Antigravity)' });
    expect(await within(agy).findByText('Đạt · đang dùng b***@genesis.vn')).toBeInTheDocument();
    const jev = screen.getByRole('region', { name: 'Jev' });
    expect(within(jev).getByText('Lỗi — thẻ Jev sẽ ẩn, không cần làm thêm')).toBeInTheDocument();
    expect(within(jev).getByText('Mã lỗi PROVIDER_ERROR')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('[object Object]');
  });

  it('kết quả agy_switch của máy chủ (bài kiểm cũ) không còn hiện ở dòng Google', async () => {
    setup({
      agy: [BINH, AN],
      switchPasses: 1,
      results: {
        ...EMPTY,
        agy_switch: check('agy_switch', 'fail', { runs: 2, error_code: 'PROBE_RATE_LIMITED', message: 'Gọi thử quá nhiều lần' }),
      },
    });
    renderPage();
    const agy = await screen.findByRole('region', { name: 'Google (Antigravity)' });
    expect(await within(agy).findByRole('button', { name: 'Gọi thử' })).toBeInTheDocument();
    expect(within(agy).queryByText(/Đã đổi qua lại/)).toBeNull();
    expect(within(agy).queryByText('Đổi tài khoản')).toBeNull();
    expect(within(agy).queryByText(/Gọi thử quá nhiều lần|PROBE_RATE_LIMITED/)).toBeNull();
  });

  it('Facebook: đang chạy → nút tắt; đã Đạt → "Đọc lại"', async () => {
    setup({ accounts: [FB], results: { ...EMPTY, facebook: check('facebook', 'pending', { checked_at: new Date().toISOString() }) } });
    const { unmount } = renderPage();
    const fb = await screen.findByRole('region', { name: 'Kết nối Facebook' });
    expect(await within(fb).findByRole('button', { name: 'Đọc ngay' })).toBeDisabled();
    unmount();
    queryClient.clear();
    setup({ accounts: [FB], results: { ...EMPTY, facebook: check('facebook', 'pass') } });
    renderPage();
    const fb2 = await screen.findByRole('region', { name: 'Kết nối Facebook' });
    expect(await within(fb2).findByRole('button', { name: 'Đọc lại' })).toBeEnabled();
    expect(within(fb2).queryByRole('button', { name: 'Đọc ngay' })).toBeNull();
  });

  it('Facebook lỗi tạm (hạn mức) → KHÔNG thay ô "Đạt", chỉ báo cạnh nút', async () => {
    setup({
      accounts: [FB],
      results: { ...EMPTY, facebook: check('facebook', 'pass') },
      run: (key) => check(key as BossCheckKey, 'fail', { error_code: 'SOCIAL_RATE_LIMIT', message: 'Vừa đọc xong', transient: true, runs: 0 }),
    });
    renderPage();
    const user = userEvent.setup();
    const fb = await screen.findByRole('region', { name: 'Kết nối Facebook' });
    await user.click(await within(fb).findByRole('button', { name: 'Đọc lại' }));
    expect(await within(fb).findByTestId('boss-transient')).toHaveTextContent('Đã đọc đủ số lần cho phép');
    expect(within(fb).getByTestId('boss-result')).toHaveTextContent(/Đạt · /);
  });

  it('Facebook đã thêm nhưng chưa đăng nhập (pending_login) → lối chính sang /social, không có "Đọc ngay"', async () => {
    setup({ accounts: [{ ...FB, status: 'pending_login', pause_reason: null, active_job: null }] });
    renderPage();
    const fb = await screen.findByRole('region', { name: 'Kết nối Facebook' });
    expect(await within(fb).findByRole('link', { name: /Đăng nhập ở trang Tài khoản mạng xã hội/ })).toHaveAttribute('href', '/social');
    expect(within(fb).getByText('Facebook của Sếp · Chưa đăng nhập')).toBeInTheDocument();
    expect(within(fb).queryByRole('button', { name: /Đọc/ })).toBeNull();
  });

  it('Google gọi thử AUTH_EXPIRED / AGY_ACCOUNT_MISMATCH → nút "Đăng nhập lại" + câu chỉ đăng nhập lại', async () => {
    setup({
      agy: [BINH, AN],
      results: {
        ...EMPTY,
        agy_call: check('agy_call', 'fail', { error_code: 'AGY_ACCOUNT_MISMATCH', message: 'x' }),
      },
    });
    const { unmount } = renderPage();
    const agy = await screen.findByRole('region', { name: 'Google (Antigravity)' });
    expect(await within(agy).findByRole('button', { name: 'Đăng nhập lại' })).toBeInTheDocument();
    expect(within(agy).getByText(/Lỗi · Gọi thử vẫn chạy bằng tài khoản khác.*Đăng nhập lại/)).toBeInTheDocument();
    // v0.1.68 (F029): MỘT đường thêm / đổi tài khoản — màn Thiết lập nguồn AI.
    for (const l of within(agy).getAllByRole('link', { name: /Thiết lập nguồn AI/ })) expect(l).toHaveAttribute('href', '/setup?buoc=nguon-ai');
    unmount();
    queryClient.clear();
    setup({ agy: [BINH], results: { ...EMPTY, agy_call: check('agy_call', 'fail', { error_code: 'AUTH_EXPIRED' }) } });
    renderPage();
    const agy2 = await screen.findByRole('region', { name: 'Google (Antigravity)' });
    expect(await within(agy2).findByRole('button', { name: 'Đăng nhập lại' })).toBeInTheDocument();
  });

  it('Claude Code gọi thử AUTH_EXPIRED → nút "Đăng nhập lại Claude Code"', async () => {
    setup({
      claude: [{ ...BINH, id: 'c1', email: 'a@example.test' }],
      results: { ...EMPTY, claude_call: check('claude_call', 'fail', { error_code: 'AUTH_EXPIRED' }) },
    });
    renderPage();
    const cl = await screen.findByRole('region', { name: 'Claude Code CLI' });
    expect(await within(cl).findByRole('button', { name: 'Đăng nhập lại Claude Code' })).toBeInTheDocument();
  });

  it('Gen-hub: lỗi địa chỉ → link "Sửa địa chỉ ở Kết nối"; chưa cấu hình mà thiếu ô → gợi ý vì sao chưa bấm được', async () => {
    setup({ results: { ...EMPTY, hub: check('hub', 'fail', { error_code: 'HUB_UNREACHABLE' }) } });
    const { unmount } = renderPage();
    const hub = await screen.findByRole('region', { name: 'Nối Gen-hub' });
    expect(await within(hub).findByRole('link', { name: 'Sửa địa chỉ ở Kết nối' })).toHaveAttribute('href', '/connections#genhub');
    unmount();
    queryClient.clear();
    setup({ link: { ...SAVED, configured: false, endpoint: null, has_token: false } });
    renderPage();
    const hub2 = await screen.findByRole('region', { name: 'Nối Gen-hub' });
    expect(await within(hub2).findByText(/Cần địa chỉ http\(s\) và token ít nhất 8 ký tự/)).toBeInTheDocument();
    expect(within(hub2).getByRole('button', { name: 'Kiểm tra' })).toBeDisabled();
    expect(within(hub2).getByText(/Nhập địa chỉ Gen-hub/)).toBeInTheDocument();
  });

  it('Claude: đã có phiên từ trước (chưa có bản đăng nhập) → ô Đăng nhập nói "Đã có phiên", Gọi thử đạt → dòng thành Đạt', async () => {
    const { world } = setup({
      claude: [{ ...BINH, id: 'c1', email: 'a@example.test' }],
      run: (key) => {
        // Như máy chủ: Gọi thử đạt mà chưa có claude_login đạt → ghi claude_login 'pass' (phiên có sẵn).
        if (key === 'claude_call') world.results.claude_login = check('claude_login', 'pass', { detail: { login_source: 'existing_session' } });
        return check(key as BossCheckKey, 'pass');
      },
    });
    renderPage();
    const user = userEvent.setup();
    const cl = await screen.findByRole('region', { name: 'Claude Code CLI' });
    expect(await within(cl).findByText('Đã có phiên (đăng nhập trước đây) — bấm Gọi thử để xác nhận')).toBeInTheDocument();
    expect(within(cl).queryByRole('button', { name: /Đăng nhập/ })).toBeNull();
    await user.click(within(cl).getByRole('button', { name: 'Gọi thử' }));
    expect(await within(cl).findByText(/Đạt · phiên có sẵn, đã xác nhận bằng Gọi thử/)).toBeInTheDocument();
  });

  it('Claude chưa có hồ sơ → "Gọi thử" tắt + gợi ý đăng nhập trước', async () => {
    const { calls } = setup();
    renderPage();
    const cl = await screen.findByRole('region', { name: 'Claude Code CLI' });
    expect(await within(cl).findByText('Đăng nhập trước rồi mới Gọi thử.')).toBeInTheDocument();
    expect(within(cl).getByRole('button', { name: 'Gọi thử' })).toBeDisabled();
    expect(calls.some((c) => c.path === '/boss-checks/claude_call/run')).toBe(false);
  });

  it('Google đã có hồ sơ mà chưa có bản đăng nhập → "Đã có phiên (đăng nhập trước đây)", không "Chưa kiểm"', async () => {
    setup({ agy: [BINH] });
    renderPage();
    const agy = await screen.findByRole('region', { name: 'Google (Antigravity)' });
    expect(await within(agy).findByText('Đã có phiên (đăng nhập trước đây)')).toBeInTheDocument();
  });

  it('Jev: GET /providers lỗi → báo lỗi + Thử lại, không mời "Nhập khoá Jev"', async () => {
    setup({ failProviders: true });
    renderPage();
    const jev = await screen.findByRole('region', { name: 'Jev' });
    expect(await within(jev).findByRole('button', { name: 'Thử lại' })).toBeInTheDocument();
    expect(within(jev).getByText(/Không đọc được danh sách nguồn AI/)).toBeInTheDocument();
    expect(within(jev).queryByRole('link', { name: /Nhập khoá Jev/ })).toBeNull();
  });

  it('không đọc được tài khoản (useMe lỗi) → báo lỗi + Thử lại, không khung chờ mãi', async () => {
    setup({ failMe: true });
    renderPage('owner', false);
    expect(await screen.findByRole('button', { name: 'Thử lại' })).toBeInTheDocument();
    expect(screen.getByText(/Không đọc được tài khoản/)).toBeInTheDocument();
  });

  it('Facebook đang chạy quá 15 phút → nút Đọc ngay mở lại + chỉ sang trang Tài khoản mạng xã hội', async () => {
    const old = new Date(Date.now() - 16 * 60_000).toISOString();
    setup({ accounts: [FB], results: { ...EMPTY, facebook: check('facebook', 'pending', { checked_at: old }) } });
    renderPage();
    const fb = await screen.findByRole('region', { name: 'Kết nối Facebook' });
    expect(await within(fb).findByRole('button', { name: 'Đọc ngay' })).toBeEnabled();
    expect(within(fb).getByTestId('boss-fb-stale')).toHaveTextContent('Lượt đọc chạy quá lâu');
    expect(within(fb).getByRole('link', { name: 'trang Tài khoản mạng xã hội' })).toHaveAttribute('href', '/social');
  });

  it('Facebook bị huỷ lẻ / treo → câu riêng, không nói "Dừng tất cả"', async () => {
    setup({ accounts: [FB], results: { ...EMPTY, facebook: check('facebook', 'fail', { error_code: 'SOCIAL_READ_CANCELLED' }) } });
    const { unmount } = renderPage();
    const fb = await screen.findByRole('region', { name: 'Kết nối Facebook' });
    expect(await within(fb).findByText(/Lượt đọc Facebook đã bị huỷ — bấm Đọc ngay lần nữa/)).toBeInTheDocument();
    expect(fb.textContent).not.toContain('Dừng tất cả');
    unmount();
    queryClient.clear();
    setup({ accounts: [FB], results: { ...EMPTY, facebook: check('facebook', 'fail', { error_code: 'WORKER_TIMEOUT' }) } });
    renderPage();
    const fb2 = await screen.findByRole('region', { name: 'Kết nối Facebook' });
    expect(await within(fb2).findByText(/chạy quá lâu nên đã dừng/)).toBeInTheDocument();
  });

  it('Truy cập từ xa (dòng 7): Kiểm tra → POST /boss-checks/remote_access/run; lỗi → câu hướng dẫn theo mã; gợi ý bấm trên điện thoại', async () => {
    const { calls } = setup({
      run: (key) => check(key as BossCheckKey, 'fail', { error_code: 'REMOTE_OPENED_ON_SERVER', message: 'Đang mở trên chính máy chủ' }),
    });
    const { container } = renderPage();
    const user = userEvent.setup();
    const r = await screen.findByRole('region', { name: 'Truy cập từ xa' });
    expect(within(r).getByText(/Bấm nút này TRÊN ĐIỆN THOẠI sau khi mở Console bằng địa chỉ từ xa/)).toBeInTheDocument();
    expect(within(r).getByText('Chưa kiểm')).toBeInTheDocument();
    await user.click(within(r).getByRole('button', { name: 'Kiểm tra' }));
    expect(await within(r).findByText(/^Lỗi · Đang mở trên chính máy chủ — mở Console trên điện thoại/)).toBeInTheDocument();
    expect(within(r).getByText('Mã lỗi REMOTE_OPENED_ON_SERVER')).toBeInTheDocument();
    expect(calls.filter((c) => c.method === 'POST').map((c) => c.path)).toEqual(['/boss-checks/remote_access/run']);
    expect(container.innerHTML).not.toContain('[object Object]');
  });

  it('Truy cập từ xa: Đạt → "đã mở Console từ <tên miền>"', async () => {
    setup({ results: { ...EMPTY, remote_access: check('remote_access', 'pass', { detail: { opened_from: 'gen.tail1234.ts.net', access_mode: 'tailscale' } }) } });
    renderPage();
    const r = await screen.findByRole('region', { name: 'Truy cập từ xa' });
    expect(await within(r).findByText(/Đạt · đã mở Console từ gen\.tail1234\.ts\.net/)).toBeInTheDocument();
  });

  it('Telegram (dòng 6): Gửi thử → POST /boss-checks/telegram/run; chưa cấu hình → câu theo mã + "Mở hướng dẫn" tới /connections#telegram', async () => {
    const { calls } = setup({
      run: (key) => check(key as BossCheckKey, 'fail', { error_code: 'TELEGRAM_NOT_CONFIGURED', message: 'Chưa cấu hình Telegram' }),
    });
    const { container } = renderPage();
    // Thẻ Kết nối › Telegram đã có trong cache (cùng phiên) — Gửi thử ở dòng 6 phải làm nó cũ đi.
    queryClient.setQueryData(TELEGRAM_KEY, { configured: false });
    const user = userEvent.setup();
    const tg = await screen.findByRole('region', { name: 'Telegram (báo động & bản tin)' });
    expect(within(tg).getByRole('link', { name: /Mở hướng dẫn/ })).toHaveAttribute('href', '/connections#telegram');
    expect(within(tg).getByText('Chưa kiểm')).toBeInTheDocument();
    expect(queryClient.getQueryState(TELEGRAM_KEY)?.isInvalidated).toBe(false);
    await user.click(within(tg).getByRole('button', { name: 'Gửi thử' }));
    expect(await within(tg).findByText(/^Lỗi · Chưa nối Telegram/)).toBeInTheDocument();
    await waitFor(() => expect(queryClient.getQueryState(TELEGRAM_KEY)?.isInvalidated).toBe(true));
    expect(within(tg).getByText('Mã lỗi TELEGRAM_NOT_CONFIGURED')).toBeInTheDocument();
    expect(calls.filter((c) => c.method === 'POST').map((c) => c.path)).toEqual(['/boss-checks/telegram/run']);
    expect(container.innerHTML).not.toContain('[object Object]');
  });

  it('Telegram: Đạt → "đã gửi tới @bot → chat •••…"; BOT_BLOCKED → hướng dẫn bấm Bắt đầu; RATE_LIMITED tạm → báo cạnh nút, giữ kết quả', async () => {
    setup({
      results: { ...EMPTY, telegram: check('telegram', 'pass', { detail: { bot_username: 'gen_harness_sep_bot', chat_masked: '•••4321' } }) },
      run: (key) => check(key as BossCheckKey, 'fail', { error_code: 'TELEGRAM_RATE_LIMITED', message: 'retry after 30', transient: true, runs: 0 }),
    });
    const { unmount } = renderPage();
    const user = userEvent.setup();
    const tg = await screen.findByRole('region', { name: 'Telegram (báo động & bản tin)' });
    expect(await within(tg).findByText('Đạt · đã gửi tới @gen_harness_sep_bot → chat •••4321')).toBeInTheDocument();
    await user.click(within(tg).getByRole('button', { name: 'Gửi thử' }));
    expect(await within(tg).findByTestId('boss-transient')).toHaveTextContent('Telegram đang giới hạn số tin');
    expect(within(tg).getByTestId('boss-result')).toHaveTextContent('Đạt · đã gửi tới');
    unmount();
    queryClient.clear();
    setup({ results: { ...EMPTY, telegram: check('telegram', 'fail', { error_code: 'TELEGRAM_BOT_BLOCKED' }) } });
    renderPage();
    const tg2 = await screen.findByRole('region', { name: 'Telegram (báo động & bản tin)' });
    expect(await within(tg2).findByText(/Lỗi · Sếp đã chặn bot hoặc chưa bấm Bắt đầu \(Start\) — mở bot trên Telegram, bấm Bắt đầu \(Start\)/)).toBeInTheDocument();
  });

  it('vai trò Vận hành → lời giải thích, không gọi /boss-checks', async () => {
    const { calls } = setup();
    renderPage('operator');
    expect(await screen.findByText('Việc kết nối do Owner làm')).toBeInTheDocument();
    expect(screen.queryByRole('region')).toBeNull();
    expect(calls.some((c) => c.path.startsWith('/boss-checks'))).toBe(false);
  });
});
