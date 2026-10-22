import type { ReactElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router-dom';
import type { AgentIdentity, DraftDetail, DraftItem, DraftPage } from '@gen-harness/contracts';
import { WorkbenchScreen } from '../../src/screens/queue/WorkbenchScreen';
import { AgentsScreen } from '../../src/screens/agents/AgentsScreen';
import { qk3 } from '../../src/screens/core/queries';
import { qk } from '../../src/lib/queries';
import { applyEvent } from '../../src/lib/realtime';

// Gói A3 (v0.1.61): Bàn làm việc trung thực — lỗi gửi có chỗ xem + gửi lại, nhãn tiếng Việt, khoá nút theo vai,
// mcp_write không giả "đã làm", bản dịch dùng được; màn Agent chỉ đọc cho vai không quản lý.

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

const meWith = (permissions: Record<string, string>) => ({
  id: 'u', email: 'x@example.vn', display_name: 'Người dùng thử',
  role: { code: 'owner', name: 'Owner — Sếp' },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions,
});
const OWNER_ME = meWith({ 'action.draft': 'all', 'action.approve': 'all', 'system.read': 'all', 'system.manage': 'all' });
const STAFF_ME = meWith({ 'action.draft': 'assigned' });
const AUDITOR_ME = meWith({ 'system.read': 'all' });

const item = (o: Partial<DraftItem> & Pick<DraftItem, 'id' | 'status'>): DraftItem => ({
  code: `ACT-${o.id}`, kind: 'message', kind_label: 'Tin nhắn', title: `Tiêu đề ${o.id}`, agent: null, created_by: null,
  created_at: '2026-10-10T08:00:00Z', hold_reason: null, subject: null, ...o,
});
const detail = (o: Partial<DraftDetail> & Pick<DraftDetail, 'id' | 'status'>): DraftDetail => ({
  ...item(o), paragraphs: ['Dạ em gửi anh báo giá ạ.'], text: 'Dạ em gửi anh báo giá ạ.', lang: 'vi',
  target: { channel: 'zalo', thread_type: 'group', group: { id: 'g1', code: 'G-1', name: 'Nhóm khách', channel: 'zalo' }, person: null },
  amount_vnd: null, autonomy_level: 4, flags: { writes_external: true, personnel_related: false, over_threshold: false },
  approve_label: 'Duyệt và gửi qua Zalo', sources: [{ label: 'Hỏi giá 3 container', ref: { type: 'meaning_unit', id: 'mu1' } }],
  context: [], side_actions: [], decision: null, send_result: null, versions: [], ...o,
});

const FAILED = detail({
  id: 'f1', status: 'failed', title: 'Trả lời chị Lan',
  decision: { by: { id: 'u', name: 'Anh Chủ' }, at: '2026-10-10T08:05:00Z', reason: null },
  send_result: { ok: false, error: 'SESSION_NOT_ACTIVE', at: '2026-10-10T08:05:01Z', message: 'Kênh chat đang ngắt kết nối nên tin chưa đi. Đăng nhập lại kênh rồi gửi lại.' } as never,
});

let qc: QueryClient;
function renderAt(url: string, ui: ReactElement, me = OWNER_ME) {
  qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  qc.setQueryData(qk.me, me);
  const Loc = () => <span data-testid="loc">{useLocation().pathname + useLocation().search}</span>;
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[url]}>
        {ui}
        <Loc />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Máy chủ giả của Bàn làm việc: `pending`/`decided` theo tham số, chi tiết theo id. */
function workbenchApi(opts: { pending?: DraftItem[]; decided?: DraftItem[]; failed?: DraftItem[]; details?: DraftDetail[]; decidedPages?: DraftPage[] }) {
  const pending = opts.pending ?? [];
  const decided = opts.decided ?? [];
  const pages = opts.decidedPages;
  return (c: Call): Response => {
    const u = new URL(c.url, 'http://x');
    if (u.pathname.endsWith('/auth/me')) return json(200, OWNER_ME);
    const one = u.pathname.match(/\/drafts\/([^/]+)$/);
    if (one && c.method === 'GET') {
      const d = (opts.details ?? []).find((x) => x.id === one[1]);
      return d ? json(200, d) : json(404, { title: 'Không tìm thấy', status: 404, code: 'NOT_FOUND' });
    }
    if (u.pathname.endsWith('/drafts') && c.method === 'GET') {
      const status = u.searchParams.get('status');
      if (status === 'pending') return json(200, { items: pending, next_cursor: null, total: pending.length } satisfies DraftPage);
      if (status === 'failed') {
        // Máy chủ lọc: chỉ bản gửi lỗi CHƯA có bản gửi lại.
        const f = opts.failed ?? decided.filter((d) => d.status === 'failed' && !d.resent_by);
        return json(200, { items: f, next_cursor: null, total: f.length } satisfies DraftPage);
      }
      if (pages) return json(200, u.searchParams.get('cursor') ? pages[1] : pages[0]);
      return json(200, { items: decided, next_cursor: null, total: decided.length } satisfies DraftPage);
    }
    return json(404);
  };
}

beforeEach(() => {
  document.cookie = 'gh_csrf=test-csrf';
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Bàn làm việc — gửi lỗi có chỗ xem (F048)', () => {
  it('tab "Gửi lỗi" liệt kê bản nháp lỗi, nói lý do bằng tiếng Việt, mã gốc ở "Chi tiết kỹ thuật"', async () => {
    mockFetch(workbenchApi({
      decided: [item({ id: 'f1', status: 'failed', title: 'Trả lời chị Lan' }), item({ id: 's1', status: 'sent', title: 'Đã đi rồi' })],
      details: [FAILED],
    }));
    renderAt('/workbench?status=failed&id=f1', <WorkbenchScreen />);
    const list = await screen.findByRole('button', { name: /Trả lời chị Lan/ });
    expect(list).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Đã đi rồi/ })).toBeNull();           // tab này chỉ giữ bản gửi lỗi
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Kênh chat đang ngắt kết nối nên tin chưa đi');
    expect(within(alert).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(within(alert).getByText('SESSION_NOT_ACTIVE')).toBeInTheDocument();         // mã nằm trong phần chi tiết
    const screenText = document.querySelector('.screen')?.textContent ?? '';
    expect(screenText).not.toMatch(/\bfailed\b/);                                      // không in thẳng mã trạng thái
    expect(screenText).not.toContain('[object Object]');
  });

  it('bản nháp lỗi cũ chưa có câu của máy chủ vẫn được dịch từ mã', async () => {
    const legacy = detail({ ...FAILED, send_result: { ok: false, error: 'PERMIT_EXPIRED', at: '2026-10-10T08:05:01Z' } });
    mockFetch(workbenchApi({ decided: [item({ id: 'f1', status: 'failed' })], details: [legacy] }));
    renderAt('/workbench?status=failed&id=f1', <WorkbenchScreen />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Chưa nhận được xác nhận đã gửi từ kênh chat');
    // Mã lạ → câu chung, không để trống.
    qc.clear();
  });

  it('nút "Gửi lại" tạo bản nháp MỚI cùng nơi nhận và mở nó ở thẻ Chờ duyệt', async () => {
    const created = detail({ id: 'n1', status: 'pending', title: 'Trả lời chị Lan' });
    const calls = mockFetch((c) => {
      if (c.method === 'POST' && c.url.endsWith('/drafts')) return json(201, created);
      return workbenchApi({ decided: [item({ id: 'f1', status: 'failed', title: 'Trả lời chị Lan' })], pending: [item({ id: 'n1', status: 'pending' })], details: [FAILED, created] })(c);
    });
    renderAt('/workbench?status=failed&id=f1', <WorkbenchScreen />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Gửi lại' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/drafts'))).toBe(true));
    const post = calls.find((c) => c.method === 'POST' && c.url.endsWith('/drafts'));
    expect(post?.body).toMatchObject({
      kind: 'message', title: 'Trả lời chị Lan', text: 'Dạ em gửi anh báo giá ạ.',
      target: { channel: 'zalo', thread_type: 'group', group_id: 'g1' },
      resend_of: 'f1',                                                        // nối bản mới về bản lỗi cũ
    });
    await waitFor(() => expect(screen.getByTestId('loc')).toHaveTextContent('/workbench?id=n1'));   // sang thẻ Chờ duyệt
    expect(await screen.findByRole('button', { name: 'Duyệt và gửi qua Zalo' })).toBeInTheDocument();
  });

  it('không có nơi nhận (NO_TARGET) thì không bày nút Gửi lại mà nói rõ vì sao', async () => {
    const noTarget = detail({ ...FAILED, target: null, send_result: { ok: false, error: 'NO_TARGET', at: '2026-10-10T08:05:01Z' } });
    mockFetch(workbenchApi({ decided: [item({ id: 'f1', status: 'failed' })], details: [noTarget] }));
    renderAt('/workbench?status=failed&id=f1', <WorkbenchScreen />);
    expect(await screen.findByText(/không có nơi nhận nên không gửi lại được/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Gửi lại' })).toBeNull();
  });

  it('vai chỉ soạn nháp vẫn Gửi lại được (tạo nháp chờ duyệt), vai chỉ xem thì không', async () => {
    mockFetch(workbenchApi({ decided: [item({ id: 'f1', status: 'failed' })], details: [FAILED] }));
    renderAt('/workbench?status=failed&id=f1', <WorkbenchScreen />, STAFF_ME);
    expect(await screen.findByRole('button', { name: 'Gửi lại' })).toBeInTheDocument();
    qc.clear();
    document.body.innerHTML = '';
    mockFetch(workbenchApi({ decided: [item({ id: 'f1', status: 'failed' })], details: [FAILED] }));
    renderAt('/workbench?status=failed&id=f1', <WorkbenchScreen />, AUDITOR_ME);
    await screen.findByRole('alert');
    expect(screen.queryByRole('button', { name: 'Gửi lại' })).toBeNull();
  });
});

describe('Bàn làm việc — gửi lại nối về bản lỗi cũ và nơi nhận hiện rõ', () => {
  const RESENT = detail({ ...FAILED, resent_by: { id: 'n1', code: 'ACT-n1' } });

  it('bản lỗi đã có bản gửi lại: nói "Đã gửi lại bằng bản ACT-n1", không còn nút Gửi lại, mở được bản mới', async () => {
    const created = detail({ id: 'n1', status: 'pending', title: 'Trả lời chị Lan' });
    mockFetch(workbenchApi({
      decided: [item({ id: 'f1', status: 'failed', title: 'Trả lời chị Lan', resent_by: { id: 'n1', code: 'ACT-n1' } })],
      pending: [item({ id: 'n1', status: 'pending' })], details: [RESENT, created],
    }));
    renderAt('/workbench?status=decided&id=f1', <WorkbenchScreen />);
    expect(await screen.findByText(/Đã gửi lại bằng bản ACT-n1/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Gửi lại' })).toBeNull();
    expect(document.querySelector('.wb-list')?.textContent).toContain('Gửi lỗi · đã gửi lại');
    await userEvent.setup().click(screen.getByRole('button', { name: 'Mở bản gửi lại' }));
    await waitFor(() => expect(screen.getByTestId('loc')).toHaveTextContent('/workbench?id=n1'));
  });

  it('thẻ và bộ đếm "Gửi lỗi" bỏ bản đã gửi lại (máy chủ lọc) — gửi lại xong thì bộ đếm giảm', async () => {
    let resent = false;
    const created = detail({ id: 'n1', status: 'pending', title: 'Trả lời chị Lan' });
    mockFetch((c) => {
      const u = new URL(c.url, 'http://x');
      if (c.method === 'POST' && u.pathname.endsWith('/drafts')) {
        resent = true;
        return json(201, created);
      }
      if (u.pathname.endsWith('/drafts') && u.searchParams.get('status') === 'failed') {
        const items = resent ? [] : [item({ id: 'f1', status: 'failed', title: 'Trả lời chị Lan' })];
        return json(200, { items, next_cursor: null, total: items.length } satisfies DraftPage);
      }
      return workbenchApi({ pending: [item({ id: 'n1', status: 'pending' })], details: [FAILED, created] })(c);
    });
    renderAt('/workbench?status=failed&id=f1', <WorkbenchScreen />);
    expect(await screen.findByRole('radio', { name: 'Gửi lỗi (1)' })).toBeInTheDocument();
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Gửi lại' }));
    await waitFor(() => expect(screen.getByRole('radio', { name: 'Gửi lỗi' })).toBeInTheDocument());   // bộ đếm hết số
  });

  it('"Gửi tới" nói rõ nơi nhận và tài khoản nhận của tin 1-1 trước khi duyệt', async () => {
    const dm = detail({
      id: 'd1', status: 'pending', title: 'Trả lời Hoa',
      target: { channel: 'zalo', thread_type: 'user', group: null, person: { id: 'p1', code: 'PER-1', name: 'Chị Hoa', type: 'customer', org_name: null } as never, account: { label: 'Chị Hoa (số mới)' } },
    });
    mockFetch(workbenchApi({ pending: [item({ id: 'd1', status: 'pending' })], details: [dm] }));
    renderAt('/workbench?id=d1', <WorkbenchScreen />);
    const line = await screen.findByText(/Gửi tới/);
    expect(line.closest('.wb-target')).toHaveTextContent('Gửi tới Chị Hoa qua Zalo · tài khoản Chị Hoa (số mới)');
  });
});

describe('Bàn làm việc — nhãn trạng thái tiếng Việt (F086) và bộ lọc từ KPI (F084)', () => {
  const DECIDED = [
    item({ id: 'a', status: 'approved', title: 'Đã duyệt A' }),
    item({ id: 'e', status: 'edited', title: 'Đã sửa B' }),
    item({ id: 's', status: 'sent', title: 'Đã gửi C', kind: 'quotation', kind_label: 'Báo giá' }),
    item({ id: 'r', status: 'rejected', title: 'Đã huỷ D' }),
    item({ id: 'k', status: 'sent', title: 'Nhắc E', kind: 'reminder', kind_label: 'Nhắc việc' }),
    item({ id: 'm', status: 'sent', title: 'Ghi CRM F', kind: 'mcp_write', kind_label: 'Ghi hệ thống ngoài' }),
    item({ id: 'b', status: 'sent', title: 'Báo cáo G', kind: 'report', kind_label: 'Báo cáo' }),
  ];

  it('tab "Đã xử lý" dùng nhãn Việt, không in mã approved/edited/sent/rejected', async () => {
    mockFetch(workbenchApi({ decided: DECIDED }));
    renderAt('/workbench?status=decided', <WorkbenchScreen />);
    await screen.findByRole('button', { name: /Đã duyệt A/ });
    const text = document.querySelector('.wb-list')?.textContent ?? '';
    for (const label of ['Đã duyệt', 'Đã sửa, chờ gửi', 'Đã gửi', 'Đã huỷ', 'Đã thực hiện']) expect(text).toContain(label);
    for (const raw of ['approved', 'edited', 'rejected', 'sent', 'failed']) expect(text).not.toContain(raw);
    // `edited` mới là duyệt kèm chỉnh sửa, chưa có kết quả từ kênh chat ⇒ không được nói "đã gửi".
    expect(text).not.toContain('Đã sửa và gửi');
  });

  it('chỉ nhắc việc mới "Đã thực hiện"; mcp_write cũ và báo cáo không báo đã làm khi chưa làm gì', async () => {
    mockFetch(workbenchApi({ decided: DECIDED }));
    renderAt('/workbench?status=decided', <WorkbenchScreen />);
    await screen.findByRole('button', { name: /Đã duyệt A/ });
    const rowOf = (name: RegExp) => screen.getByRole('button', { name });
    expect(rowOf(/Nhắc E/)).toHaveTextContent('Đã thực hiện');
    expect(rowOf(/Ghi CRM F/)).toHaveTextContent('Đã duyệt — chưa thực hiện (bản cũ)');
    expect(rowOf(/Ghi CRM F/)).not.toHaveTextContent('Đã thực hiện');
    expect(rowOf(/Báo cáo G/)).not.toHaveTextContent('Đã thực hiện');
    expect(rowOf(/Báo cáo G/)).toHaveTextContent('Đã duyệt');
  });

  it('?kind=quotation (KPI "Báo giá đã gửi") mở tab Đã xử lý và lọc đúng loại', async () => {
    const calls = mockFetch(workbenchApi({ decided: [DECIDED[2]] }));
    renderAt('/workbench?kind=quotation', <WorkbenchScreen />);
    expect(await screen.findByRole('button', { name: /Đã gửi C/ })).toBeInTheDocument();
    const decidedCall = calls.find((c) => c.url.includes('status=decided') && c.url.includes('kind=quotation'));
    expect(decidedCall).toBeTruthy();
    expect(screen.getByRole('radio', { name: 'Đã xử lý' })).toHaveAttribute('aria-checked', 'true');
    const chip = screen.getByRole('button', { name: 'Bỏ lọc loại Báo giá' });
    await userEvent.setup().click(chip);
    await waitFor(() => expect(screen.getByTestId('loc')).not.toHaveTextContent('kind='));
  });

  it('?status=sent chỉ giữ bản đã gửi; ?status=pending (KPI "Tỉ lệ chờ duyệt") ở thẻ Chờ duyệt', async () => {
    mockFetch(workbenchApi({ decided: DECIDED, pending: [item({ id: 'p', status: 'pending', title: 'Đang chờ P' })] }));
    renderAt('/workbench?status=sent', <WorkbenchScreen />);
    await screen.findByRole('button', { name: /Đã gửi C/ });
    expect(screen.queryByRole('button', { name: /Đã duyệt A/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Đã huỷ D/ })).toBeNull();
    document.body.innerHTML = '';
    renderAt('/workbench?status=pending', <WorkbenchScreen />);
    expect(await screen.findByRole('button', { name: /Đang chờ P/ })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Chờ duyệt' })).toHaveAttribute('aria-checked', 'true');
  });

  it('phân trang: "Tải thêm" lấy trang kế bằng con trỏ và gộp vào danh sách', async () => {
    const page1: DraftPage = { items: [item({ id: 'x1', status: 'sent', title: 'Trang một' })], next_cursor: '2026-10-09T00:00:00Z', total: 2 };
    const page2: DraftPage = { items: [item({ id: 'x2', status: 'sent', title: 'Trang hai' })], next_cursor: null, total: 2 };
    const calls = mockFetch(workbenchApi({ decidedPages: [page1, page2] }));
    renderAt('/workbench?status=decided', <WorkbenchScreen />);
    await screen.findByRole('button', { name: /Trang một/ });
    await userEvent.setup().click(screen.getByRole('button', { name: 'Tải thêm' }));
    expect(await screen.findByRole('button', { name: /Trang hai/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Trang một/ })).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes('cursor='))).toBe(true);
    expect(screen.queryByRole('button', { name: 'Tải thêm' })).toBeNull();
  });

  it('tab "Gửi lỗi" lọc ở máy chủ (status=failed): bản lỗi nằm xa trong lịch sử vẫn hiện và được đếm đúng', async () => {
    const failed: DraftPage = { items: [item({ id: 'x2', status: 'failed', title: 'Lỗi từ tháng trước' })], next_cursor: null, total: 1 };
    const calls = mockFetch((c) => {
      const u = new URL(c.url, 'http://x');
      if (u.pathname.endsWith('/drafts') && u.searchParams.get('status') === 'failed') return json(200, failed);
      // Danh sách "đã xử lý" 50 dòng đầu toàn bản đã gửi — nếu lọc ở máy khách thì sẽ không thấy bản lỗi.
      return workbenchApi({ decided: [item({ id: 's9', status: 'sent', title: 'Đã đi' })], details: [FAILED] })(c);
    });
    renderAt('/workbench?status=failed', <WorkbenchScreen />);
    expect(await screen.findByRole('button', { name: /Lỗi từ tháng trước/ })).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes('status=failed'))).toBe(true);
    expect(screen.getByRole('radio', { name: 'Gửi lỗi (1)' })).toBeInTheDocument();
    expect(screen.queryByText('Không có tin nào gửi lỗi')).toBeNull();
  });

  it('bộ lọc "chỉ bản đã gửi" quét hết trần mà còn trang sau thì nói "Chưa quét hết" + Tải thêm, không nói "không có"', async () => {
    let n = 0;
    const calls = mockFetch((c) => {
      const u = new URL(c.url, 'http://x');
      if (u.pathname.endsWith('/drafts') && u.searchParams.get('status') === 'decided') {
        n += 1;
        return json(200, { items: [item({ id: `r${n}`, status: 'rejected', title: `Đã huỷ ${n}` })], next_cursor: `c${n}`, total: 999 } satisfies DraftPage);
      }
      return workbenchApi({})(c);
    });
    renderAt('/workbench?status=sent', <WorkbenchScreen />);
    expect(await screen.findByText('Chưa quét hết danh sách', undefined, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.queryByText('Chưa có bản nháp nào đã xử lý')).toBeNull();
    const before = calls.filter((c) => c.url.includes('status=decided')).length;
    await userEvent.setup().click(screen.getByRole('button', { name: 'Tải thêm' }));
    await waitFor(() => expect(calls.filter((c) => c.url.includes('status=decided')).length).toBeGreaterThan(before));
  });

  it('sự kiện draft.failed làm mới danh sách nháp', async () => {
    mockFetch(workbenchApi({ decided: [] }));
    renderAt('/workbench?status=failed', <WorkbenchScreen />);
    await screen.findByText('Không có tin nào gửi lỗi');
    const spy = vi.spyOn(qc, 'invalidateQueries');
    applyEvent(qc, { type: 'draft.failed', data: { id: 'f9', error: 'SEND_FAILED', message: 'x' } } as never);
    expect(spy).toHaveBeenCalledWith({ queryKey: qk3.drafts });
  });
});

describe('Bàn làm việc — nút theo quyền (F077), mcp_write (F074) và bản dịch (F093)', () => {
  const PENDING = detail({ id: 'p1', status: 'pending', title: 'Báo giá chờ' });

  it('vai không có action.approve thấy "Chờ Sếp/Quản lý duyệt", không có Duyệt/Sửa/Huỷ', async () => {
    mockFetch(workbenchApi({ pending: [item({ id: 'p1', status: 'pending', title: 'Báo giá chờ' })], details: [PENDING] }));
    renderAt('/workbench?id=p1', <WorkbenchScreen />, STAFF_ME);
    expect(await screen.findByText(/Chờ Sếp\/Quản lý duyệt/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Duyệt và gửi/ })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Sửa rồi gửi' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Huỷ bản nháp' })).toBeNull();
    expect(screen.getByRole('button', { name: /Dịch/ })).toBeInTheDocument();          // vẫn soạn/dịch được
  });

  it('vai có action.approve vẫn thấy đủ ba nút', async () => {
    mockFetch(workbenchApi({ pending: [item({ id: 'p1', status: 'pending' })], details: [PENDING] }));
    renderAt('/workbench?id=p1', <WorkbenchScreen />);
    expect(await screen.findByRole('button', { name: 'Duyệt và gửi qua Zalo' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sửa rồi gửi' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Huỷ bản nháp' })).toBeInTheDocument();
  });

  it('mcp_write: không có nút duyệt/sửa, nói thẳng "chưa hỗ trợ thực thi", vẫn huỷ được', async () => {
    const mcp = detail({
      id: 'm1', status: 'pending', kind: 'mcp_write', kind_label: 'Ghi hệ thống ngoài', title: 'Gọi tool send_email',
      target: null, approve_label: 'Duyệt và thực hiện',
    });
    const calls = mockFetch((c) => {
      if (c.method === 'POST' && c.url.includes('/reject')) return json(200, { ...mcp, status: 'rejected' });
      return workbenchApi({ pending: [item({ id: 'm1', status: 'pending', kind: 'mcp_write', kind_label: 'Ghi hệ thống ngoài' })], details: [mcp] })(c);
    });
    renderAt('/workbench?id=m1', <WorkbenchScreen />);
    const note = await screen.findByRole('status');
    expect(note).toHaveTextContent('Chưa hỗ trợ thực thi tool ghi — đã giữ yêu cầu');
    expect(screen.queryByRole('button', { name: /Duyệt và thực hiện/ })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Sửa rồi gửi' })).toBeNull();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Huỷ bản nháp' }));
    await waitFor(() => expect(calls.some((c) => c.url.includes('/drafts/m1/reject'))).toBe(true));
  });

  it('mcp_write đã duyệt từ bản cũ (trước v0.1.61) không bao giờ báo "Đã gửi" hay "Đã thực hiện" — hệ thống ngoài chưa hề được gọi', async () => {
    const done = detail({ id: 'm2', status: 'sent', kind: 'mcp_write', kind_label: 'Ghi hệ thống ngoài', title: 'Ghi CRM', target: null });
    mockFetch(workbenchApi({ decided: [item({ id: 'm2', status: 'sent', kind: 'mcp_write', kind_label: 'Ghi hệ thống ngoài', title: 'Ghi CRM' })], details: [done] }));
    renderAt('/workbench?status=decided&id=m2', <WorkbenchScreen />);
    await screen.findByRole('button', { name: /Ghi CRM/ });
    const listText = document.querySelector('.wb-list')?.textContent ?? '';
    expect(listText).toContain('Đã duyệt — chưa thực hiện (bản cũ)');
    expect(listText).not.toContain('Đã thực hiện');
    expect(listText).not.toContain('Đã gửi');
    expect(await screen.findByText(/hệ thống ngoài chưa hề được gọi/)).toBeInTheDocument();
  });

  it('"Dùng bản dịch này" chép bản dịch vào ô Sửa rồi gửi', async () => {
    const calls = mockFetch((c) => {
      if (c.method === 'POST' && c.url.includes('/translate')) return json(200, { lang: 'en', text: 'Dear sir, here is our quotation.' });
      return workbenchApi({ pending: [item({ id: 'p1', status: 'pending' })], details: [PENDING] })(c);
    });
    renderAt('/workbench?id=p1', <WorkbenchScreen />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /Dịch/ }));
    await user.click(screen.getByRole('radio', { name: 'EN' }));
    await user.click(await screen.findByRole('button', { name: 'Dùng bản dịch này' }));
    const box = await screen.findByLabelText('Sửa nội dung bản nháp');
    expect(box).toHaveValue('Dear sir, here is our quotation.');
    expect(calls.some((c) => c.url.includes('/translate'))).toBe(true);
    expect(screen.getByRole('button', { name: 'Gửi nội dung đã sửa' })).toBeEnabled();
  });

  it('vai không duyệt được thì không có nút "Dùng bản dịch này"', async () => {
    mockFetch((c) => {
      if (c.method === 'POST' && c.url.includes('/translate')) return json(200, { lang: 'en', text: 'Hello' });
      return workbenchApi({ pending: [item({ id: 'p1', status: 'pending' })], details: [PENDING] })(c);
    });
    renderAt('/workbench?id=p1', <WorkbenchScreen />, STAFF_ME);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /Dịch/ }));
    await user.click(screen.getByRole('radio', { name: 'EN' }));
    await screen.findByText('Hello');
    expect(screen.queryByRole('button', { name: 'Dùng bản dịch này' })).toBeNull();
  });
});

describe('Danh tính Agent — chế độ chỉ đọc (F077) và chữ Việt (F086)', () => {
  const AGENT: AgentIdentity = {
    id: 'agent-tls', name: 'Trợ lý thương mại', role_desc: 'Theo dõi cơ hội.', template: 'commercial', addressing: {},
    voice: 'Thân thiện', speak_when: 'Khi được hỏi', forbidden: ['Cam kết giá'], autonomy_level: 4, is_enabled: true, limits: {},
    created_at: '2026-08-01T00:00:00Z', updated_at: '2026-09-20T00:00:00Z',
    channel_scopes: [{ channel_id: 'c1', channel_type: 'zalo', group_id: null, group_name: null }], binding: null,
  };
  const api = (c: Call): Response => {
    if (c.url.includes('/agents/templates')) return json(200, [{ code: 'cs', name: 'CSKH', role_desc: 'Trả lời khách.', voice: 'Ấm', speak_when: 'Khi hỏi', forbidden: [], default_enabled: true }]);
    if (c.url.includes('/agents/decisions')) return json(200, { items: [], next_cursor: null, total: 0 });
    if (c.url.endsWith('/agents') && c.method === 'GET') return json(200, [AGENT]);
    if (c.url.includes('/channels')) return json(200, [{ type: 'zalo', name: 'Zalo', installed: true, id: 'c1', state: 'active', account_label: null, started_at: null, groups_listening: 0, outbound_queued: 0, last_heartbeat_at: null, stats: null, qr: null }]);
    return json(404);
  };

  it('vai chỉ xem: "Xem" mở biểu mẫu khoá, không có nút Lưu, không hỏi PIN', async () => {
    mockFetch(api);
    renderAt('/agents', <AgentsScreen />, AUDITOR_ME);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Xem agent Trợ lý thương mại' }));
    const dlg = await screen.findByRole('dialog');
    expect(within(dlg).getByText(/Chỉ xem/)).toBeInTheDocument();
    expect(within(dlg).queryByRole('button', { name: /Lưu thay đổi/ })).toBeNull();
    expect(within(dlg).getAllByRole('button', { name: 'Đóng' }).length).toBeGreaterThan(0);
    for (const label of ['Tên hiển thị', 'Vai trò', 'Giọng điệu', 'Khi nào được nói', 'Cấm làm gì (mỗi dòng một điều)']) {
      expect(within(dlg).getByLabelText(label)).toBeDisabled();
    }
    expect(within(dlg).getByRole('checkbox', { name: 'Zalo' })).toBeDisabled();
    expect(within(dlg).queryByText(/cần mã PIN/)).toBeNull();
    expect(within(dlg).queryByTestId('autonomy-select')).toBeNull();
  });

  it('Owner vẫn sửa được như trước', async () => {
    mockFetch(api);
    renderAt('/agents', <AgentsScreen />, OWNER_ME);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Sửa agent Trợ lý thương mại' }));
    const dlg = await screen.findByRole('dialog');
    expect(within(dlg).getByRole('button', { name: 'Lưu thay đổi' })).toBeInTheDocument();
    expect(within(dlg).getByLabelText('Giọng điệu')).toBeEnabled();
    expect(within(dlg).getByTestId('autonomy-select')).toBeInTheDocument();
  });

  it('không còn chữ Template / Prefill / persona trên màn', async () => {
    mockFetch(api);
    renderAt('/agents', <AgentsScreen />, OWNER_ME);
    const user = userEvent.setup();
    await screen.findByText('Trợ lý thương mại', { selector: '.ag-card__name' });
    await user.click(await screen.findByRole('button', { name: 'Dùng mẫu' }));
    const dlg = await screen.findByRole('dialog');
    expect(dlg).toHaveTextContent('Điền sẵn từ mẫu CSKH');
    const text = document.body.textContent ?? '';
    for (const word of ['Template', 'Prefill', 'persona']) expect(text).not.toContain(word);
    expect(text).toContain('Mẫu tuỳ chọn');
  });
});
