/**
 * Khách & Nhóm (F053) + Yêu cầu dữ liệu một người (F100, F101):
 *  - nhóm/người phân trang bằng con trỏ của máy chủ ("Xem thêm"), tìm tên + lọc kênh Ở MÁY CHỦ;
 *  - gán BOT hàng loạt gửi BỘ LỌC cho máy chủ (một lần gọi) và hiện đúng số người khớp;
 *  - "Xuất dữ liệu" tải ngay thành tệp JSON; "Ẩn danh hoá" có hộp xác nhận nêu rõ không xoá tin gốc.
 */
import type { ReactElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { DirChannel, DirGroup, DirPerson } from '@gen-harness/contracts';
import { DirectoryScreen } from '../../src/screens/relations/DirectoryScreen';
import { ProfileScreen } from '../../src/screens/relations/ProfileScreen';
import { SystemScreen } from '../../src/screens/system/SystemScreen';
import { personExportFileName } from '../../src/screens/system/systemModel';
import { qk } from '../../src/lib/queries';
import { useToasts } from '../../src/lib/toast';
import { useUrlStateStore } from '../../src/lib/uiStore';
import { AGENT_IDS } from '../mock-ids';

const downloaded = vi.hoisted(() => ({ calls: [] as { blob: Blob; name: string }[], fail: false }));
vi.mock('../../src/lib/download', () => ({
  downloadBlob: (blob: Blob, name: string) => {
    if (downloaded.fail) throw new Error('khong tao duoc tep');
    downloaded.calls.push({ blob, name });
  },
  downloadText: () => undefined,
}));

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
const params = (c: Call) => new URL(c.url, 'http://x').searchParams;

function meWith(permissions: Record<string, string>) {
  return {
    id: 'u', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A (Chủ)', role: { code: 'owner', name: 'Owner — Sếp' },
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' }, addressing: { self: 'Anh', bot_calls_me: 'Sếp' },
    pin_verified_until: null, permissions,
  };
}
function renderScreen(ui: ReactElement, permissions: Record<string, string>, urlParams: Record<string, string> = {}) {
  useUrlStateStore.setState({ params: urlParams });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(qk.me, meWith(permissions));
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useUrlStateStore.setState({ params: {} });
  useToasts.setState({ toasts: [] });
  downloaded.calls = [];
  downloaded.fail = false;
});
afterEach(() => {
  vi.unstubAllGlobals();
});

// ─── Dữ liệu giả: 120 nhóm Zalo + 130 người ─────────────────────────────────
const CHANNELS: DirChannel[] = [
  { id: 'ch-zalo', type: 'zalo', name: 'Zalo', state: 'active', group_count: 120, events_24h: 5 },
  { id: 'ch-whatsapp', type: 'whatsapp', name: 'WhatsApp', state: 'active', group_count: 1, events_24h: 0 },
];
const group = (i: number, ch: 'zalo' | 'whatsapp' = 'zalo'): DirGroup => ({
  id: `g${ch}${i}`, code: `GRP-${ch}-${String(i).padStart(4, '0')}`, name: `Nhóm ${ch} số ${i}`, kind: 'customer', listen_mode: 'silent',
  member_count: 10, events_24h: 0, heat: null, channel: { type: ch, name: ch }, bot: null, created_at: '2026-01-01T00:00:00Z',
});
const ZALO_GROUPS = Array.from({ length: 120 }, (_, i) => group(i + 1));
const WA_GROUPS = [group(1, 'whatsapp')];

const person = (i: number): DirPerson => ({
  id: `p${i}`, code: `PER-${String(i).padStart(4, '0')}`, name: `Khách số ${i}`, type: 'customer', org_name: null, relation: 'direct',
  channels: ['zalo'], heat: null, heat_trend: null, value_vnd: null, priority: 'P3', bot: null, autonomy_level: null, owner_user_id: null,
});
const PEOPLE = Array.from({ length: 130 }, (_, i) => person(i + 1));

/** Máy chủ giả: lọc q/kênh, trang theo `cursor` = vị trí, `total` đếm theo bộ lọc (không theo con trỏ). */
function page<T extends { name: string; code: string }>(rows: T[], c: Call) {
  const p = params(c);
  const q = (p.get('q') ?? '').toLowerCase();
  const all = q ? rows.filter((r) => r.name.toLowerCase().includes(q) || r.code.toLowerCase().includes(q)) : rows;
  const limit = Number(p.get('limit') ?? 50);
  const from = Number(p.get('cursor') ?? 0);
  return { items: all.slice(from, from + limit), next_cursor: from + limit < all.length ? String(from + limit) : null, total: all.length };
}

function directoryServer(c: Call): Response {
  if (c.url.includes('/directory/channels')) return json(200, CHANNELS);
  if (c.url.includes('/directory/groups')) {
    const ch = params(c).get('channel_id');
    return json(200, page(ch === 'ch-whatsapp' ? WA_GROUPS : ZALO_GROUPS, c));
  }
  if (c.url.includes('/pickers/agents')) return json(200, { items: [{ id: AGENT_IDS.tls, name: 'Trợ lý thương mại' }] });
  if (c.url.includes('/directory/people/bulk-bot')) return json(200, { ok: true, updated: 130 });
  if (c.url.includes('/directory/people')) return json(200, page(PEOPLE, c));
  return json(404);
}

const WRITE = { 'profile.read': 'all', 'profile.write': 'all' };

describe('Khách & Nhóm — phân trang bằng con trỏ của máy chủ', () => {
  it('nhóm của một kênh: hiện 50 đầu rồi "Xem thêm" lấy trang kế, đủ 120 nhóm; thẻ kênh khác không bị lẫn', async () => {
    const calls = mockFetch(directoryServer);
    const user = userEvent.setup();
    renderScreen(<DirectoryScreen />, WRITE);

    const zalo = await screen.findByRole('region', { name: 'Zalo' });
    expect(await within(zalo).findByText('Đang hiện 50 / 120 nhóm')).toBeInTheDocument();
    expect(within(zalo).getAllByRole('row')).toHaveLength(51); // 1 hàng tiêu đề + 50 nhóm
    // Mỗi kênh lọc ở máy chủ theo channel_id (không lọc client từ 50 nhóm của mọi kênh).
    expect(calls.some((c) => c.url.includes('/directory/groups') && params(c).get('channel_id') === 'ch-zalo')).toBe(true);
    const wa = screen.getByRole('region', { name: 'WhatsApp' });
    expect(await within(wa).findByText('Nhóm whatsapp số 1')).toBeInTheDocument();

    await user.click(within(zalo).getByRole('button', { name: 'Xem thêm' }));
    expect(await within(zalo).findByText('Đang hiện 100 / 120 nhóm')).toBeInTheDocument();
    // Trang kế đi bằng con trỏ do máy chủ trả, không tự dựng.
    expect(calls.some((c) => c.url.includes('/directory/groups') && params(c).get('cursor') === '50')).toBe(true);
    await user.click(within(zalo).getByRole('button', { name: 'Xem thêm' }));
    expect(await within(zalo).findByText('Đang hiện 120 / 120 nhóm')).toBeInTheDocument();
    expect(within(zalo).queryByRole('button', { name: 'Xem thêm' })).toBeNull();
    expect(within(zalo).getByText('Nhóm zalo số 120')).toBeInTheDocument();
  });

  it('tìm nhóm theo tên gửi q cho máy chủ (nhóm thứ 100 vẫn tìm được)', async () => {
    const calls = mockFetch(directoryServer);
    const user = userEvent.setup();
    renderScreen(<DirectoryScreen />, WRITE);
    const zalo = await screen.findByRole('region', { name: 'Zalo' });
    await within(zalo).findByText('Đang hiện 50 / 120 nhóm');

    await user.type(screen.getByLabelText('Tìm nhóm (tên hoặc mã)'), 'số 100');
    expect(await within(zalo).findByText('Nhóm zalo số 100')).toBeInTheDocument();
    expect(within(zalo).getByText('Đang hiện 1 / 1 nhóm')).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes('/directory/groups') && params(c).get('q') === 'số 100')).toBe(true);
  });

  it('con người: "Xem thêm" lấy trang kế; tìm tên + lọc kênh do máy chủ làm', async () => {
    const calls = mockFetch(directoryServer);
    const user = userEvent.setup();
    renderScreen(<DirectoryScreen />, WRITE, { dt: 'people' });

    expect(await screen.findByText('Đang hiện 50 / 130 người')).toBeInTheDocument();
    expect(screen.getByText('130 người khớp')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Xem thêm' }));
    expect(await screen.findByText('Đang hiện 100 / 130 người')).toBeInTheDocument();

    await user.type(screen.getByLabelText('Tìm theo tên hoặc mã'), 'số 123');
    expect(await screen.findByText('Khách số 123')).toBeInTheDocument();
    expect(screen.getByText('1 người khớp')).toBeInTheDocument();
    await user.click(within(screen.getByRole('group', { name: 'Kênh' })).getByRole('button', { name: 'Zalo' }));
    await waitFor(() => expect(calls.some((c) => c.url.includes('/directory/people') && params(c).get('channel') === 'zalo')).toBe(true));
    expect(calls.some((c) => c.url.includes('/directory/people') && params(c).get('q') === 'số 123')).toBe(true);
  });
});

describe('Khách & Nhóm — gán BOT hàng loạt theo bộ lọc', () => {
  it('ghi đúng số người khớp (130, không phải 50 đã tải) và gửi BỘ LỌC trong một lần gọi duy nhất', async () => {
    const calls = mockFetch(directoryServer);
    const user = userEvent.setup();
    renderScreen(<DirectoryScreen />, WRITE, { dt: 'people', heat: 'cold' });
    await screen.findByText('Đang hiện 50 / 130 người');

    await user.click(screen.getByRole('button', { name: 'Thiết lập BOT cho nhóm đã lọc' }));
    const dlg = await screen.findByRole('dialog', { name: 'Thiết lập BOT cho nhóm đã lọc' });
    expect(within(dlg).getByText('130 người đang khớp bộ lọc hiện tại')).toBeInTheDocument();
    await user.click(within(dlg).getByText('Trợ lý thương mại'));
    await user.click(within(dlg).getByRole('button', { name: 'Áp dụng cho 130 người' }));

    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.includes('/directory/people/bulk-bot'))).toBe(true));
    const bulk = calls.filter((c) => c.method === 'POST' && c.url.includes('/directory/people/'));
    expect(bulk).toHaveLength(1); // không còn vòng lặp từng người ở trình duyệt
    expect(bulk[0].body).toMatchObject({ filter: { heat: 'cold' }, agent_id: AGENT_IDS.tls, expected_total: 130 });
    await waitFor(() => expect(useToasts.getState().toasts.some((t) => t.text === 'Đã cập nhật 130 người')).toBe(true));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Thiết lập BOT cho nhóm đã lọc' })).toBeNull());
  });

  it('bộ lọc gửi kèm cả ô tìm tên và kênh; chỉ đổi tự trị thì KHÔNG gửi agent_id (giữ nguyên BOT)', async () => {
    const calls = mockFetch(directoryServer);
    const user = userEvent.setup();
    renderScreen(<DirectoryScreen />, WRITE, { dt: 'people', chn: 'zalo', pq: 'Khách' });
    await screen.findByText('Đang hiện 50 / 130 người');

    await user.click(screen.getByRole('button', { name: 'Thiết lập BOT cho nhóm đã lọc' }));
    const dlg = await screen.findByRole('dialog', { name: 'Thiết lập BOT cho nhóm đã lọc' });
    expect(within(dlg).getByRole('button', { name: 'Áp dụng cho 130 người' })).toBeDisabled(); // chưa chọn gì để đổi
    await user.click(within(dlg).getByRole('button', { name: /Gợi ý/ }));
    await user.click(within(dlg).getByRole('button', { name: 'Áp dụng cho 130 người' }));
    await waitFor(() => expect(calls.some((c) => c.url.includes('/directory/people/bulk-bot'))).toBe(true));
    const body = calls.find((c) => c.url.includes('/directory/people/bulk-bot'))!.body as Record<string, unknown>;
    expect(body.filter).toMatchObject({ channel: 'zalo', q: 'Khách' });
    expect(body).not.toHaveProperty('agent_id');
    expect(body).toHaveProperty('autonomy_level');
  });

  it('số khớp đã đổi (409): hiện câu thân thiện, tải lại danh sách để thấy số mới, không báo thành công', async () => {
    let listCalls = 0;
    const calls = mockFetch((c) => {
      if (c.url.includes('/directory/people/bulk-bot')) {
        return json(409, { type: 'x', title: 'Danh sách đã thay đổi', status: 409, code: 'BULK_FILTER_CHANGED', detail: 'Bộ lọc hiện khớp 131 người (không phải 130).' });
      }
      if (c.url.includes('/directory/people') && c.method === 'GET') listCalls += 1;
      return directoryServer(c);
    });
    const user = userEvent.setup();
    renderScreen(<DirectoryScreen />, WRITE, { dt: 'people' });
    await screen.findByText('Đang hiện 50 / 130 người');
    const before = listCalls;

    await user.click(screen.getByRole('button', { name: 'Thiết lập BOT cho nhóm đã lọc' }));
    const dlg = await screen.findByRole('dialog', { name: 'Thiết lập BOT cho nhóm đã lọc' });
    await user.click(within(dlg).getByText('Trợ lý thương mại'));
    await user.click(within(dlg).getByRole('button', { name: 'Áp dụng cho 130 người' }));

    expect(await within(dlg).findByText(/Bộ lọc hiện khớp 131 người/)).toBeInTheDocument();
    // luật cứng: câu thân thiện kèm "Chi tiết kỹ thuật" (mã HTTP, mã lỗi)
    const err = within(dlg).getByRole('alert');
    expect(within(err).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(err.textContent).toContain('HTTP 409');
    expect(err.textContent).toContain('BULK_FILTER_CHANGED');
    await waitFor(() => expect(listCalls).toBeGreaterThan(before));
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1);
    expect(useToasts.getState().toasts.some((t) => t.text.startsWith('Đã cập nhật'))).toBe(false);
  });
});

describe('Hồ sơ đã gộp', () => {
  it('máy chủ chuyển hướng tới hồ sơ gốc ⇒ ?id= đổi sang hồ sơ gốc, thao tác sau đi vào hồ sơ sống', async () => {
    mockFetch((c) =>
      /\/profile\/(old|live)$/.test(c.url)
        ? json(200, {
            person: { id: 'live', code: 'PER-0001', name: 'Hồ sơ gốc', type: 'customer', org_name: null, title: null, relation_to_owner: 'direct', owner: null },
            autonomy_level: null, bot: null, owner_note: null, identities: [], scores: [], summary: [], timeline: [], documents: [], touchpoints: [], merge_history: [],
          })
        : json(404),
    );
    renderScreen(<ProfileScreen />, { 'profile.read': 'all' }, { id: 'old' });
    expect(await screen.findByText('Hồ sơ gốc')).toBeInTheDocument();
    await waitFor(() => expect(useUrlStateStore.getState().params.id).toBe('live'));
  });
});

// ─── Yêu cầu xuất / ẩn danh hoá dữ liệu một người ────────────────────────────
const SYS = { 'system.read': 'all', 'system.manage': 'all', 'roles.manage': 'all', 'audit.read': 'all', 'data.manage': 'all' };
const EXPORT_BUNDLE = {
  person: { id: 'p77', code: 'PER-0077', display_name: 'Khách số 77', attrs: {} },
  identities: [{ channel: 'zalo', external_id: 'zl-77', handle: 'khach77', phone_e164: null, first_seen_at: '2026-01-01T00:00:00Z' }],
  scores: [],
  notebook: [{ section: 'note', body: 'Thích gọi buổi sáng', author: 'gen', created_at: '2026-02-01T00:00:00Z' }],
};

function storageServer(opts: { exportResult?: unknown } = {}) {
  return (c: Call): Response => {
    if (c.url.includes('/directory/people')) return json(200, page(PEOPLE, c));
    if (c.url.includes('/persons/p77/data-requests') && c.method === 'POST') {
      const kind = (c.body as { kind: string }).kind;
      return json(201, { id: 'r1', kind, status: 'completed', result: kind === 'export' ? ('exportResult' in opts ? opts.exportResult : EXPORT_BUNDLE) : { erased: true } });
    }
    if (c.url.includes('/persons/p77/data-requests')) return json(200, []);
    return json(404);
  };
}

async function openPanelAndPick77(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText('Yêu cầu xuất / xoá dữ liệu một người');
  await user.type(screen.getByLabelText('Tìm người (tên hoặc mã)'), 'số 77');
  // người thứ 77 (ngoài 50 đầu) tìm ra được nhờ máy chủ lọc
  const opt = await screen.findByRole('option', { name: 'Khách số 77 · PER-0077' });
  await user.selectOptions(screen.getByLabelText('Người'), opt);
}

describe('Cài đặt › Yêu cầu dữ liệu một người', () => {
  it('chọn người bằng tìm kiếm ở máy chủ (gửi q), không chỉ 50 người đầu', async () => {
    const calls = mockFetch(storageServer());
    const user = userEvent.setup();
    renderScreen(<SystemScreen />, SYS, { tab: 'storage' });
    await openPanelAndPick77(user);
    expect(calls.some((c) => c.url.includes('/directory/people') && params(c).get('q') === 'số 77')).toBe(true);
    expect(calls.some((c) => c.url.includes('/directory/people') && params(c).get('limit') === '50')).toBe(false);
  });

  it('"Xuất dữ liệu" tải ngay tệp JSON có đủ nội dung gói; chỉ báo "đã tải" khi có tệp', async () => {
    const calls = mockFetch(storageServer());
    const user = userEvent.setup();
    renderScreen(<SystemScreen />, SYS, { tab: 'storage' });
    await openPanelAndPick77(user);
    await user.click(screen.getByRole('button', { name: 'Xuất dữ liệu' }));

    await waitFor(() => expect(downloaded.calls).toHaveLength(1));
    const { blob, name } = downloaded.calls[0];
    expect(name).toMatch(/^du-lieu-PER-0077-\d{4}-\d{2}-\d{2}\.json$/);
    expect(blob.type).toBe('application/json');
    expect(JSON.parse(await readBlob(blob))).toEqual(EXPORT_BUNDLE);
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ kind: 'export' });
    await waitFor(() => expect(useToasts.getState().toasts.some((t) => t.text === 'Đã tải tệp dữ liệu của Khách số 77' && t.tone === 'ok')).toBe(true));
  });

  it('máy chủ không trả dữ liệu ⇒ KHÔNG báo "đã tải", không tạo tệp rỗng', async () => {
    mockFetch(storageServer({ exportResult: null }));
    const user = userEvent.setup();
    renderScreen(<SystemScreen />, SYS, { tab: 'storage' });
    await openPanelAndPick77(user);
    await user.click(screen.getByRole('button', { name: 'Xuất dữ liệu' }));
    await waitFor(() => expect(useToasts.getState().toasts.some((t) => t.tone === 'bad' && /không trả dữ liệu/.test(t.text))).toBe(true));
    expect(downloaded.calls).toHaveLength(0);
    expect(useToasts.getState().toasts.some((t) => t.text.startsWith('Đã tải'))).toBe(false);
  });

  it('trình duyệt không tạo được tệp ⇒ báo lỗi thân thiện, không báo thành công', async () => {
    downloaded.fail = true;
    mockFetch(storageServer());
    const user = userEvent.setup();
    renderScreen(<SystemScreen />, SYS, { tab: 'storage' });
    await openPanelAndPick77(user);
    await user.click(screen.getByRole('button', { name: 'Xuất dữ liệu' }));
    await waitFor(() => expect(useToasts.getState().toasts.some((t) => t.tone === 'bad' && /không tạo được tệp/.test(t.text))).toBe(true));
    expect(useToasts.getState().toasts.some((t) => t.text.startsWith('Đã tải'))).toBe(false);
  });

  it('"Ẩn danh hoá" có hộp xác nhận nêu rõ không xoá tin gốc; Huỷ thì không gửi gì, Xác nhận mới gửi', async () => {
    const calls = mockFetch(storageServer());
    const user = userEvent.setup();
    renderScreen(<SystemScreen />, SYS, { tab: 'storage' });
    await openPanelAndPick77(user);
    expect(screen.queryByRole('button', { name: 'Xoá dữ liệu' })).toBeNull(); // nhãn cũ hứa hơn việc thật

    await user.click(screen.getByRole('button', { name: 'Ẩn danh hoá' }));
    const dlg = await screen.findByRole('dialog', { name: 'Ẩn danh hoá người này?' });
    expect(within(dlg).getByText(/Không xoá tin nhắn gốc/)).toBeInTheDocument();
    expect(within(dlg).getByText(/không hoàn tác được/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === 'POST')).toBe(false); // chưa gửi gì khi mới bấm

    await user.click(within(dlg).getByRole('button', { name: 'Huỷ' }));
    expect(screen.queryByRole('dialog', { name: 'Ẩn danh hoá người này?' })).toBeNull();
    expect(calls.some((c) => c.method === 'POST')).toBe(false);

    await user.click(screen.getByRole('button', { name: 'Ẩn danh hoá' }));
    const dlg2 = await screen.findByRole('dialog', { name: 'Ẩn danh hoá người này?' });
    await user.click(within(dlg2).getByRole('button', { name: 'Ẩn danh hoá' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ kind: 'erase' }));
    await waitFor(() => expect(useToasts.getState().toasts.some((t) => /tin nhắn gốc vẫn được giữ/.test(t.text))).toBe(true));
  });

  it('tên tệp xuất làm sạch mã người và gắn ngày', () => {
    expect(personExportFileName('PER-0042', new Date('2026-10-16T05:00:00Z'))).toBe('du-lieu-PER-0042-2026-10-16.json');
    expect(personExportFileName('../a b/c', new Date('2026-10-16T05:00:00Z'))).toBe('du-lieu-abc-2026-10-16.json');
    expect(personExportFileName('///', new Date('2026-10-16T05:00:00Z'))).toBe('du-lieu-nguoi-2026-10-16.json');
  });
});

/** jsdom chưa có `Blob.text()` — đọc nội dung tệp tải về bằng FileReader. */
function readBlob(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(r.error);
    r.readAsText(blob);
  });
}
