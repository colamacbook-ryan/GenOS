/**
 * v0.1.50 (F-81, QD-18) — Kết nối › Gen-hub: khối "Quyền ghi Kho (tuỳ chọn)" dưới "Quyền đọc thêm": 2 dòng kho_create / kho_update
 * với Có / Chưa / Chưa kiểm (nguồn: kết quả Kiểm tra xanh, không thì link.write_scopes), câu hướng dẫn khi thiếu, câu "Gen chỉ ghi khi
 * Sếp bấm Xác nhận + nhập mã PIN"; thiếu quyền ghi KHÔNG làm Kiểm tra đỏ; quyền ghi Kho không bị nhắc "nên tắt".
 */
import type { ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { HubLink, HubLinkTestResult } from '@gen-harness/contracts';
import { HubLinkCard } from '../../src/screens/mcp/HubLinkCard';
import {
  HUB_KICKER,
  WRITE_CONFIRM_ONLY_TEXT,
  WRITE_ENOUGH_TEXT,
  WRITE_HIDDEN_TEXT,
  WRITE_MISSING_TEXT,
  otherWriteTools,
  testedToolsText,
  writeHidden,
  writeScopeRows,
  writeScopesMessage,
} from '../../src/screens/mcp/mcpModel';
import { qk } from '../../src/lib/queries';
import { createMock } from '../mock-p4-mcp';
import type { P2Ctx } from '../mock-phase2';

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const me = (role: string) => ({
  id: 'u', email: 'x@genesis.local', display_name: 'Anh Cơ', role: { code: role, name: role },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null,
  permissions: { 'system.read': 'all', 'system.manage': role === 'owner' ? 'all' : 'none' },
});

const SAVED: HubLink = {
  configured: true, enabled: true, status: 'ok', server_id: 's1', endpoint: 'https://hub.example.test/mcp', has_token: true,
  allow_public_network: true, token_expires_at: '2026-12-28T16:59:00Z', days_left: 80, last_ok_at: '2026-10-09T01:00:00Z', last_error: null, health: 'healthy',
};
const FULL_READ = { calendar: true, mail: true, tasks: true, drive: true };

const testOut = (over: Partial<HubLinkTestResult> = {}): HubLinkTestResult => ({
  ok: true, error: null, latency_ms: 240, exposed_tools: ['a__kho_tom_tat', 'a__kho_search', 'a__kho_find_by_id'], missing_tools: [],
  link: SAVED, ...over,
});

function stubHub(link: HubLink, test: HubLinkTestResult) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const u = String(url);
      const method = init?.method ?? 'GET';
      if (u.includes('/hub/link/test') && method === 'POST') return json(200, test);
      if (u.includes('/hub/link') && method === 'GET') return json(200, link);
      return json(404, { code: 'NOT_FOUND', title: 'Không tồn tại' });
    }),
  );
}

function renderCard(ui: ReactElement, role = 'owner') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(qk.me, me(role));
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('mcpModel — quyền ghi Kho (hàm thuần)', () => {
  it('writeScopeRows: 2 dòng kho_create / kho_update đúng nhãn, cùng trạng thái theo write_scopes.kho', () => {
    const yes = writeScopeRows({ kho: true });
    expect(yes.map((r) => r.key)).toEqual(['kho_create', 'kho_update']);
    expect(yes.map((r) => r.label)).toEqual(['Tạo bản ghi Phiên/Việc', 'Sửa bản ghi Phiên/Việc']);
    expect(yes.map((r) => r.text)).toEqual(['Có', 'Có']);
    expect(writeScopeRows({ kho: false }).map((r) => r.text)).toEqual(['Chưa', 'Chưa']);
    expect(writeScopeRows({ kho: false }).map((r) => r.state)).toEqual(['no', 'no']);
  });

  it('writeScopeRows: MỖI dòng theo đúng tool của nó (chỉ tick kho_create ⇒ kho_create "Có", kho_update "Chưa"); thiếu cờ riêng ⇒ theo `kho`', () => {
    const one = writeScopeRows({ kho: false, kho_create: true, kho_update: false });
    expect(one.map((r) => [r.key, r.text])).toEqual([
      ['kho_create', 'Có'],
      ['kho_update', 'Chưa'],
    ]);
    expect(writeScopeRows({ kho: false, kho_create: false, kho_update: true }).map((r) => r.text)).toEqual(['Chưa', 'Có']);
    // Máy chủ cũ chỉ gửi `kho` ⇒ hai dòng theo `kho`; cờ riêng sai kiểu ⇒ cũng theo `kho`.
    expect(writeScopeRows({ kho: true }).map((r) => r.text)).toEqual(['Có', 'Có']);
    expect(writeScopeRows({ kho: false, kho_create: 'x' } as never).map((r) => r.text)).toEqual(['Chưa', 'Chưa']);
    expect(writeScopeRows({ kho_create: true } as never).map((r) => r.text)).toEqual(['Có', 'Chưa kiểm']);
  });

  it('writeScopesMessage: chỉ thiếu kho_update ⇒ nhắc tick; Owner tự đóng ở MCP Hub (write_hidden) ⇒ câu riêng, không giục tick ở Gen-hub', () => {
    expect(writeScopesMessage(undefined, { kho: false, kho_create: true, kho_update: false })).toEqual({ tone: 'warn', text: WRITE_MISSING_TEXT });
    expect(writeScopesMessage([], { kho: false, kho_create: false, kho_update: true }, ['kho_create'])).toEqual({ tone: 'warn', text: WRITE_HIDDEN_TEXT(['kho_create']) });
    expect(WRITE_HIDDEN_TEXT(['kho_create'])).toBe(
      'Sếp đã tự đóng kho_create ở MCP Hub — Kiểm tra không mở lại. Muốn Gen ghi lại thì mở ở MCP Hub; muốn tắt hẳn thì bỏ tick ở Gen-hub.',
    );
    // Vừa thiếu ở Gen-hub vừa tự đóng ⇒ ưu tiên câu tick quyền (máy chủ báo write_missing).
    expect(writeScopesMessage(['ghi Kho (kho_update)'], { kho: false, kho_create: false, kho_update: false }, ['kho_create'])?.text).toBe(WRITE_MISSING_TEXT);
    expect(writeHidden(['kho_create', 'gmail_send', 3, null])).toEqual(['kho_create']);
    expect(writeHidden('kho_create')).toEqual([]);
  });

  it('testedToolsText: đếm RIÊNG tool đọc và tool ghi Kho (máy chủ gửi exposed_write_tools, máy chủ cũ ⇒ tách theo tên)', () => {
    const tools = ['p__kho_tom_tat', 'p__kho_search', 'p__kho_find_by_id', 'p__kho_create', 'p__kho_update'];
    expect(testedToolsText({ exposed_tools: tools, exposed_write_tools: ['p__kho_create', 'p__kho_update'] })).toBe('mở 3 tool đọc + 2 tool ghi Kho cho Gen');
    expect(testedToolsText({ exposed_tools: tools })).toBe('mở 3 tool đọc + 2 tool ghi Kho cho Gen');
    expect(testedToolsText({ exposed_tools: tools.slice(0, 3), exposed_write_tools: [] })).toBe('mở 3 tool đọc cho Gen');
    expect(testedToolsText({ exposed_tools: [...tools.slice(0, 3), 'p__kho_create'], exposed_write_tools: ['p__kho_create'] })).toBe('mở 3 tool đọc + 1 tool ghi Kho cho Gen');
  });

  it('writeScopeRows: không có dữ liệu / sai kiểu ⇒ "Chưa kiểm"', () => {
    for (const bad of [undefined, null, {}, 'x', 5, { kho: 'yes' }, { kho: 1 }, { kho: null }]) {
      expect(writeScopeRows(bad as never).map((r) => r.text)).toEqual(['Chưa kiểm', 'Chưa kiểm']);
    }
  });

  it('writeScopesMessage: thiếu ⇒ hướng dẫn tick kho_create, kho_update; đủ ⇒ câu đủ quyền; chưa biết ⇒ null', () => {
    expect(WRITE_MISSING_TEXT).toBe('Vào Gen-hub tick quyền ghi Kho (tạo và sửa bản ghi — tên trong Gen-hub: kho_create, kho_update) cho token của Gen-Harness rồi bấm Kiểm tra.');
    expect(writeScopesMessage(['ghi Kho (kho_create, kho_update)'], { kho: false })).toEqual({ tone: 'warn', text: WRITE_MISSING_TEXT });
    expect(writeScopesMessage(undefined, { kho: false })).toEqual({ tone: 'warn', text: WRITE_MISSING_TEXT });
    expect(writeScopesMessage([], { kho: true })).toEqual({ tone: 'ok', text: WRITE_ENOUGH_TEXT });
    expect(writeScopesMessage(undefined, { kho: true })?.tone).toBe('ok');
    expect(writeScopesMessage(undefined, undefined)).toBeNull();
    expect(writeScopesMessage([{ x: 1 }], undefined)).toBeNull();
  });

  it('otherWriteTools: bỏ kho_create / kho_update (kể cả có tiền tố), giữ quyền ghi khác, chỉ lấy chuỗi', () => {
    expect(otherWriteTools(['kho_create', 'kho_update'])).toEqual([]);
    expect(otherWriteTools(['mcp-58450__kho_create', 'gmail_send', { x: 1 }, 3, '', 'kho_update'])).toEqual(['gmail_send']);
    expect(otherWriteTools(undefined)).toEqual([]);
    expect(otherWriteTools('kho_create')).toEqual([]);
  });
});

describe('Thẻ Gen-hub — Quyền ghi Kho (tuỳ chọn)', () => {
  it('tiêu đề + dòng phụ KHÔNG còn "chỉ đọc": Gen đọc; ghi Kho khi Sếp xác nhận + mã PIN (cùng lời thẻ Trợ giúp)', async () => {
    stubHub(SAVED, testOut());
    const { container } = renderCard(<HubLinkCard />);
    await screen.findByTestId('hub-write-scopes');
    expect(container).toHaveTextContent('Gen-hub — Gen đọc và ghi Kho dữ liệu');
    expect(container).toHaveTextContent(`· ${HUB_KICKER}`);
    expect(HUB_KICKER).toBe('Gen đọc; ghi Kho khi Sếp xác nhận + mã PIN · chỉ Sếp · tắt tới khi Kiểm tra xanh');
    // "chỉ đọc" chỉ còn ở khối "Quyền đọc thêm" (lịch, mail, việc, Drive — đúng là chỉ đọc), không còn ở tiêu đề / dòng phụ của thẻ.
    expect(container.textContent?.toLowerCase()).not.toContain('chỉ đọc · chỉ sếp');
    expect(container.textContent?.toLowerCase()).not.toContain('gen đọc kho tri thức');
  });

  it('v0.1.70 (F117): tiêu đề thẻ theo Tên Kho Sếp tự đặt (một nguồn tên), không còn "Kho tri thức"', async () => {
    stubHub({ ...SAVED, kho_label: 'Kho của Ryan', kho_label_custom: true }, testOut());
    const { container } = renderCard(<HubLinkCard />);
    await screen.findByTestId('hub-write-scopes');
    expect(container).toHaveTextContent('Gen-hub — Gen đọc và ghi Kho của Ryan');
    expect(container.textContent).not.toContain('Kho tri thức');
  });

  it('Kiểm tra xanh có tool ghi: "mở 3 tool đọc + 2 tool ghi Kho cho Gen" (không gộp 2 tool ghi vào "tool đọc")', async () => {
    const tools = ['a__kho_tom_tat', 'a__kho_search', 'a__kho_find_by_id', 'a__kho_create', 'a__kho_update'];
    stubHub(SAVED, testOut({ exposed_tools: tools, exposed_write_tools: ['a__kho_create', 'a__kho_update'], write_scopes: { kho: true, kho_create: true, kho_update: true }, write_missing: [] }));
    renderCard(<HubLinkCard />);
    await userEvent.click(await screen.findByRole('button', { name: 'Kiểm tra' }));
    expect(await screen.findByText('Đã nối Kho · 240 ms · mở 3 tool đọc + 2 tool ghi Kho cho Gen')).toBeInTheDocument();
  });

  it('chỉ tick kho_create: dòng kho_create "Có", kho_update "Chưa" + nhắc tick; Owner tự đóng kho_create ở MCP Hub ⇒ câu riêng', async () => {
    stubHub(SAVED, testOut({ write_scopes: { kho: false, kho_create: true, kho_update: false }, write_missing: ['ghi Kho (kho_update)'], write_hidden: [] }));
    const first = renderCard(<HubLinkCard />);
    await userEvent.click(await screen.findByRole('button', { name: 'Kiểm tra' }));
    let box = screen.getByTestId('hub-write-scopes');
    await within(box).findByText(WRITE_MISSING_TEXT);
    let items = within(box).getAllByRole('listitem');
    expect(items[0]).toHaveTextContent('Tạo bản ghi Phiên/Việc (kho_create)Có');
    expect(items[1]).toHaveTextContent('Sửa bản ghi Phiên/Việc (kho_update)Chưa');
    first.unmount();
    vi.unstubAllGlobals();

    stubHub(SAVED, testOut({ write_scopes: { kho: false, kho_create: false, kho_update: true }, write_missing: [], write_hidden: ['kho_create'] }));
    renderCard(<HubLinkCard />);
    await userEvent.click(await screen.findByRole('button', { name: 'Kiểm tra' }));
    box = screen.getByTestId('hub-write-scopes');
    expect(await within(box).findByText(WRITE_HIDDEN_TEXT(['kho_create']))).toBeInTheDocument();
    expect(within(box).queryByText(WRITE_MISSING_TEXT)).toBeNull();
    items = within(box).getAllByRole('listitem');
    expect(items[0]).toHaveTextContent('Chưa');
    expect(items[1]).toHaveTextContent('Có');
  });

  it('tải lại trang (chưa bấm Kiểm tra): tool Sếp tự đóng lấy từ GET /hub/link (`write_hidden`) ⇒ câu "Sếp đã tự đóng…", không giục tick', async () => {
    const link: HubLink = { ...SAVED, write_scopes: { kho: false, kho_create: false, kho_update: true }, write_hidden: ['kho_create'] };
    stubHub(link, testOut());
    renderCard(<HubLinkCard />);
    const box = await screen.findByTestId('hub-write-scopes');
    expect(await within(box).findByText(WRITE_HIDDEN_TEXT(['kho_create']))).toBeInTheDocument();
    expect(within(box).queryByText(WRITE_MISSING_TEXT)).toBeNull();
    const items = within(box).getAllByRole('listitem');
    expect(items[0]).toHaveTextContent('Chưa');
    expect(items[1]).toHaveTextContent('Có');
  });

  it('tải lại trang, máy chủ cũ (không có `write_hidden`) và thiếu kho_create ⇒ vẫn nhắc tick ở Gen-hub như trước', async () => {
    stubHub({ ...SAVED, write_scopes: { kho: false, kho_create: false, kho_update: true } }, testOut());
    renderCard(<HubLinkCard />);
    const box = await screen.findByTestId('hub-write-scopes');
    expect(await within(box).findByText(WRITE_MISSING_TEXT)).toBeInTheDocument();
  });

  it('chưa có dữ liệu: 2 dòng "Chưa kiểm", nằm dưới "Quyền đọc thêm", ghi rõ Gen chỉ ghi khi Sếp Xác nhận + mã PIN, không cảnh báo', async () => {
    stubHub(SAVED, testOut());
    renderCard(<HubLinkCard />);
    const read = await screen.findByTestId('hub-scopes');
    const box = screen.getByTestId('hub-write-scopes');
    expect(within(box).getByText('Quyền ghi Kho (tuỳ chọn)')).toBeInTheDocument();
    expect(within(box).getAllByText('Chưa kiểm')).toHaveLength(2);
    const items = within(box).getAllByRole('listitem');
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent('Tạo bản ghi Phiên/Việc (kho_create)');
    expect(items[1]).toHaveTextContent('Sửa bản ghi Phiên/Việc (kho_update)');
    expect(within(box).getByText('Gen chỉ ghi khi Sếp bấm Xác nhận + nhập mã PIN trên thẻ đề xuất.')).toBe(within(box).getByText(WRITE_CONFIRM_ONLY_TEXT));
    expect(box.compareDocumentPosition(read) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy(); // khối ghi đứng sau khối đọc
    // Khối đọc vẫn đúng 4 dòng "Chưa kiểm" của riêng nó (không lẫn dòng ghi).
    expect(within(read).getAllByText('Chưa kiểm')).toHaveLength(4);
    expect(within(box).queryByText(/Vào Gen-hub tick quyền ghi Kho/)).toBeNull();
    expect(within(box).queryByText(/Đủ quyền ghi Kho/)).toBeNull();
  });

  it('link.write_scopes (lần kiểm gần nhất) hiện trước khi bấm Kiểm tra: thiếu ⇒ "Chưa" ×2 + hướng dẫn; đủ ⇒ "Có" ×2', async () => {
    stubHub({ ...SAVED, write_scopes: { kho: false } }, testOut());
    const first = renderCard(<HubLinkCard />);
    const box = await screen.findByTestId('hub-write-scopes');
    expect(within(box).getAllByText('Chưa')).toHaveLength(2);
    expect(within(box).getByText('Vào Gen-hub tick quyền ghi Kho (tạo và sửa bản ghi — tên trong Gen-hub: kho_create, kho_update) cho token của Gen-Harness rồi bấm Kiểm tra.')).toBeInTheDocument();
    first.unmount();
    vi.unstubAllGlobals();

    stubHub({ ...SAVED, write_scopes: { kho: true } }, testOut());
    renderCard(<HubLinkCard />);
    const ok = await screen.findByTestId('hub-write-scopes');
    expect(within(ok).getAllByText('Có')).toHaveLength(2);
    expect(within(ok).getByText('Đủ quyền ghi Kho (Phiên, Việc).')).toBeInTheDocument();
  });

  it('Kiểm tra xanh nhưng thiếu quyền ghi: vẫn "Đã nối Kho" (không đỏ), nhắc tick kho_create, kho_update', async () => {
    stubHub(SAVED, testOut({ read_scopes: FULL_READ, read_missing: [], write_tools: [], write_scopes: { kho: false }, write_missing: ['ghi Kho (kho_create, kho_update)'] }));
    renderCard(<HubLinkCard />);
    await userEvent.click(await screen.findByRole('button', { name: 'Kiểm tra' }));
    expect(await screen.findByText(/Đã nối Kho · 240 ms/)).toBeInTheDocument();
    expect(screen.getByText(/Đã nối Kho/).closest('.apm-test-result')).toHaveClass('apm-test-result--ok');
    const box = screen.getByTestId('hub-write-scopes');
    expect(await within(box).findByText('Vào Gen-hub tick quyền ghi Kho (tạo và sửa bản ghi — tên trong Gen-hub: kho_create, kho_update) cho token của Gen-Harness rồi bấm Kiểm tra.')).toBeInTheDocument();
    expect(within(box).getAllByText('Chưa')).toHaveLength(2);
    // Phần đọc không bị ảnh hưởng.
    expect(within(screen.getByTestId('hub-scopes')).getAllByText('Có')).toHaveLength(4);
    expect(document.body.textContent).not.toContain('[object Object]');
  });

  it('Kiểm tra xanh và đủ quyền ghi: "Có" ×2 + "Đủ quyền ghi Kho (Phiên, Việc)."', async () => {
    stubHub(SAVED, testOut({ read_scopes: FULL_READ, read_missing: [], write_tools: [], write_scopes: { kho: true }, write_missing: [] }));
    renderCard(<HubLinkCard />);
    await userEvent.click(await screen.findByRole('button', { name: 'Kiểm tra' }));
    const box = screen.getByTestId('hub-write-scopes');
    expect(await within(box).findByText('Đủ quyền ghi Kho (Phiên, Việc).')).toBeInTheDocument();
    expect(within(box).getAllByText('Có')).toHaveLength(2);
    expect(screen.queryByText(/Vào Gen-hub tick quyền ghi Kho/)).toBeNull();
  });

  it('write_tools có kho_create / kho_update ⇒ KHÔNG nhắc "nên tắt"; quyền ghi khác (gmail_send) vẫn bị nhắc', async () => {
    stubHub(SAVED, testOut({ read_scopes: FULL_READ, read_missing: [], write_scopes: { kho: true }, write_missing: [], write_tools: ['kho_create', 'kho_update'] }));
    const first = renderCard(<HubLinkCard />);
    await userEvent.click(await screen.findByRole('button', { name: 'Kiểm tra' }));
    await screen.findByText('Đủ quyền ghi Kho (Phiên, Việc).');
    expect(screen.queryByText(/Token đang có thêm quyền GHI/)).toBeNull();
    first.unmount();
    vi.unstubAllGlobals();

    stubHub(SAVED, testOut({ read_scopes: FULL_READ, read_missing: [], write_scopes: { kho: true }, write_missing: [], write_tools: ['kho_create', 'gmail_send', 'kho_update'] }));
    renderCard(<HubLinkCard />);
    await userEvent.click(await screen.findByRole('button', { name: 'Kiểm tra' }));
    expect(await screen.findByText(/Token đang có thêm quyền GHI \(gmail_send\)/)).toBeInTheDocument();
  });

  it('Kiểm tra đỏ (không nối được): không nói gì về quyền ghi; dòng ghi theo lần kiểm xanh gần nhất (hoặc "Chưa kiểm")', async () => {
    stubHub({ ...SAVED, last_ok_at: null }, testOut({ ok: false, error: 'Gen-hub từ chối token', error_code: 'HUB_TOKEN_REJECTED', exposed_tools: [], write_scopes: { kho: false }, write_missing: ['ghi Kho (kho_create, kho_update)'] }));
    renderCard(<HubLinkCard />);
    await userEvent.click(await screen.findByRole('button', { name: 'Kiểm tra' }));
    expect(await screen.findByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    const box = screen.getByTestId('hub-write-scopes');
    expect(within(box).getAllByText('Chưa kiểm')).toHaveLength(2);
    expect(within(box).queryByText(/Vào Gen-hub tick quyền ghi Kho/)).toBeNull();
  });

  it('chưa nối / chưa từng kiểm xanh: write_scopes toàn false (máy chủ cũ) vẫn "Chưa kiểm", không giục tick quyền', async () => {
    const OFF: HubLink = {
      configured: false, enabled: false, status: 'off', server_id: null, endpoint: null, has_token: false, allow_public_network: false,
      token_expires_at: null, days_left: null, last_ok_at: null, last_error: null, health: null, write_scopes: { kho: false },
    };
    stubHub(OFF, testOut());
    renderCard(<HubLinkCard />);
    const box = await screen.findByTestId('hub-write-scopes');
    expect(within(box).getAllByText('Chưa kiểm')).toHaveLength(2);
    expect(within(box).queryByText('Chưa')).toBeNull();
  });

  it('vai trò khác Owner: không có khối quyền ghi (chỉ Sếp cấu hình)', async () => {
    stubHub(SAVED, testOut());
    renderCard(<HubLinkCard />, 'manager');
    expect(await screen.findByText('Chỉ Sếp (Owner) cấu hình và dùng Gen-hub.')).toBeInTheDocument();
    expect(screen.queryByTestId('hub-write-scopes')).toBeNull();
  });

  it('hướng dẫn tạo token: vẫn KHÔNG bật quyền ghi lịch/mail/Drive; quyền ghi Kho là tuỳ chọn (kho_create, kho_update)', async () => {
    stubHub(SAVED, testOut());
    renderCard(<HubLinkCard />);
    const step = await screen.findByText(/Bật quyền ĐỌC Kho/);
    expect(step.textContent).toMatch(/KHÔNG bật quyền ghi/);
    expect(step.textContent).toMatch(/kho_create, kho_update/);
    expect(step.textContent).toMatch(/không bắt buộc/);
  });
});

describe('mock-p4-mcp — quyền ghi Kho (token chứa "khongghi"), hubSim và /hub/kho/write', () => {
  function call(mock: ReturnType<typeof createMock>, method: string, path: string, body: Record<string, unknown> = {}, needPin = false) {
    let out: { status: number; body: unknown } | null = null;
    const ctx = {
      method, path, url: new URL(`http://x/api/v1${path}`), body, perms: { 'system.read': 'all', 'system.manage': 'all' },
      reply: (status: number, b?: unknown) => ((out = { status, body: b }), true as const),
      problem: (status: number, code: string, title: string) => ((out = { status, body: { code, title } }), true as const),
      text: () => true as const, needPin: () => needPin, userLabel: 'Sếp', owner: true, role: 'owner',
    } as unknown as P2Ctx;
    expect(mock.handle(ctx)).toBe(true);
    return out as unknown as { status: number; body: Record<string, unknown> };
  }
  const newMock = () => createMock({ fresh: true, emit: () => undefined, getAgents: () => [], pushDraft: () => undefined });

  it('Kiểm tra xanh trả write_scopes {kho:true}, write_missing []; token "khongghi" ⇒ vẫn xanh, kho false + write_missing', () => {
    const mock = newMock();
    call(mock, 'PATCH', '/hub/link', { endpoint: 'https://hub.example.test/mcp', token: 'ghtok_binh_thuong_1', allow_public_network: true });
    // Như máy chủ: chưa kiểm xanh ⇒ write_scopes null ("Chưa kiểm").
    expect((call(mock, 'GET', '/hub/link').body as { write_scopes?: unknown }).write_scopes).toBeNull();
    expect(call(mock, 'POST', '/hub/link/test').body).toMatchObject({ ok: true, write_scopes: { kho: true }, write_missing: [] });
    expect(call(mock, 'GET', '/hub/link').body).toMatchObject({ write_scopes: { kho: true } });

    call(mock, 'PATCH', '/hub/link', { token: 'ghtok_khongghi_2' });
    expect((call(mock, 'GET', '/hub/link').body as { write_scopes?: unknown }).write_scopes).toBeNull(); // đổi token ⇒ chưa kiểm lại
    expect(call(mock, 'POST', '/hub/link/test').body).toMatchObject({ ok: true, write_scopes: { kho: false }, write_missing: ['ghi Kho (kho_create, kho_update)'] });
  });

  it('POST /hub/kho/write: cần PIN (423), permit hợp lệ, đếm mọi lời gọi; thành công trả mã PHIEN-12 rồi PHIEN-13', () => {
    const mock = newMock();
    const sim = mock.hooks.hubSim as unknown as (b: unknown) => unknown;
    const calls = mock.hooks.khoCalls as unknown as () => { calls: number; writes: Array<{ code: string | null }> };
    sim({ link: 'on' });
    const body = { proposal_id: 'p1', tool: 'kho_create', args: { bang: 'Phiên', 'Chủ đề': 'Thử' }, permit: 'permit-abc' };
    expect(call(mock, 'POST', '/hub/kho/write', body, true).status).toBe(423);
    expect(calls().calls).toBe(0); // chưa nhập PIN ⇒ chưa có lời gọi ghi
    expect(call(mock, 'POST', '/hub/kho/write', { ...body, tool: 'kho_delete' }).status).toBe(403);
    const first = call(mock, 'POST', '/hub/kho/write', body);
    expect(first).toMatchObject({ status: 200, body: { code: 'PHIEN-12', bang: 'Phiên' } });
    expect(call(mock, 'POST', '/hub/kho/write', { ...body, proposal_id: 'p2' }).body).toMatchObject({ code: 'PHIEN-13' });
    expect(calls().calls).toBe(2);
    expect(calls().writes.map((w) => w.code)).toEqual(['PHIEN-12', 'PHIEN-13']);
  });

  it('lỗi theo mã: permit sai, liên kết tắt, bộ ngắt, thiếu quyền ghi, bị từ chối, không chắc — mỗi lần đều được đếm, không ghi', () => {
    const mock = newMock();
    const sim = mock.hooks.hubSim as unknown as (b: unknown) => unknown;
    const calls = mock.hooks.khoCalls as unknown as () => { calls: number; writes: unknown[] };
    const write = (permit = 'permit-abc') => call(mock, 'POST', '/hub/kho/write', { proposal_id: 'p', tool: 'kho_update', args: { ma: 'VIEC-12', 'Trạng thái': 'Xong' }, permit });
    expect(write().body).toMatchObject({ code: 'HUB_LINK_OFF' }); // chưa nối
    sim({ link: 'on' });
    expect(write('bậy').body).toMatchObject({ code: 'HUB_WRITE_PERMIT' });
    sim({ breaker: true });
    expect(write().body).toMatchObject({ code: 'HUB_BREAKER_OPEN' });
    sim({ breaker: false, write: 'missing' });
    expect(write().body).toMatchObject({ code: 'HUB_WRITE_MISSING' });
    sim({ write: 'ok', kho: 'rejected' });
    expect(write().body).toMatchObject({ code: 'HUB_WRITE_REJECTED' });
    sim({ kho: 'uncertain' });
    expect(write().body).toMatchObject({ code: 'HUB_WRITE_UNCERTAIN' });
    expect(calls().calls).toBe(6);
    expect(calls().writes).toHaveLength(0);
    sim({ kho: 'ok' });
    expect(write().body).toMatchObject({ code: 'VIEC-12' }); // sửa ⇒ trả lại mã bản ghi
    expect(calls().writes).toHaveLength(1);
  });
});
