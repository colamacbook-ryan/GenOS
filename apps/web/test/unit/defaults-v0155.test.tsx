import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { AgentBindingSlot, BindingsPage, Provider } from '@gen-harness/contracts';
import type { DefaultItem, DefaultSuggestion, DefaultsResponse } from '../../src/defaults/defaultsModel';
import { DefaultBadge } from '../../src/defaults/DefaultBadge';
import { DefaultControls, ResetButton } from '../../src/defaults/ResetButton';
import { StandardModeStrip } from '../../src/defaults/StandardModeStrip';
import { asText, changedAutonomy, changedCount, confirmLines, customCoreBindingCount, standardModeText } from '../../src/defaults/defaultsModel';
import { ApiScreen } from '../../src/screens/api/ApiScreen';
import { bindingEffortText, bindingModelText, bindingParamsText, supportedEfforts } from '../../src/screens/api/apiModel';
import { refreshSettings } from '../../src/defaults/queries';
import { PinDialogHost } from '../../src/shell/PinDialogHost';
import { qk } from '../../src/lib/queries';
import { queryClient } from '../../src/lib/queryClient';
import { createMock } from '../mock-defaults';
import type { P2Ctx } from '../mock-phase2';

/**
 * v0.1.55 (G1) — "Chế độ tiêu chuẩn" + "Về mặc định": chip Mặc định/Đã đổi, hộp Xác nhận, lỗi = chuỗi + Chi tiết kỹ thuật (không
 * object), dải đếm N mục + Về mặc định tất cả (mã PIN), Áp model chuẩn theo vai; bảng gán model hiện "Chuẩn: <model> (tự chọn)".
 */

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json' } });

type Role = 'owner' | 'manager';
const meAs = (role: Role) => ({
  id: 'u', email: `${role}@genesis.local`, display_name: role, role: { code: role, name: role },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' }, addressing: { self: 'Anh', bot_calls_me: 'Sếp' },
  pin_verified_until: null, permissions: { 'system.read': 'all', 'system.manage': 'all' },
});

const item = (key: string, over: Partial<DefaultItem> = {}): DefaultItem => ({
  key, label: `Mục ${key}`, scope: 'org', group: 'Bộ não AI', default_text: `Mặc định của ${key}`, current_text: `Đang dùng của ${key}`,
  customized: true, resettable: true, ...over,
});
const SUGGEST: DefaultSuggestion = {
  key: 'apply_standard', title: 'Áp model chuẩn theo vai? (đang dùng 1 model cho mọi việc)', body: 'Em tự chọn model hợp từng việc.', to: '/system?tab=brain#chuan',
};
const defaults = (items: DefaultItem[], suggestions: DefaultSuggestion[] = []): DefaultsResponse => ({
  items, customized_count: items.filter((i) => i.customized && i.resettable).length, suggestions,
});

interface Call {
  url: string;
  method: string;
  body: unknown;
}
function mockFetch(handler: (c: Call) => Response | undefined, role: Role = 'owner') {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const c = { url: String(url), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined };
      calls.push(c);
      const r = handler(c);
      if (r) return r;
      if (c.url.endsWith('/auth/me')) return json(200, meAs(role));
      return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tìm thấy' });
    }),
  );
  return calls;
}

function renderUi(ui: React.ReactElement, role: Role = 'owner') {
  queryClient.setQueryData(qk.me, meAs(role));
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        {ui}
        <PinDialogHost />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  queryClient.clear();
  queryClient.setDefaultOptions({ queries: { retry: false, staleTime: Infinity } });
  document.cookie = 'gh_csrf=test-csrf';
});
afterEach(() => vi.unstubAllGlobals());

// ── mô hình thuần ─────────────────────────────────────────────────────────────

describe('defaultsModel (thuần)', () => {
  it('dải chữ: đang dùng / đã đổi N mục; số đếm tin máy chủ, thiếu thì đếm lại', () => {
    expect(standardModeText(0)).toBe('Chế độ tiêu chuẩn: đang dùng');
    expect(standardModeText(3)).toBe('Chế độ tiêu chuẩn: đã đổi 3 mục');
    const d = defaults([item('a'), item('b', { customized: false }), item('c', { resettable: false })]);
    expect(changedCount(d)).toBe(1);
    expect(changedCount({ ...d, customized_count: undefined as unknown as number })).toBe(1);
    expect(changedCount(undefined)).toBe(0);
  });

  it('hộp "Áp model chuẩn theo vai" chỉ đếm bốn dòng gán LÕI, không đếm dòng gán riêng của agent', () => {
    const d = defaults([
      item('binding:core.gen'), item('binding:core.reply'), item('binding:core.briefing', { customized: false }),
      item('binding:agent:a1'), item('binding:agent:a2'), item('triage'),
    ]);
    expect(customCoreBindingCount(d)).toBe(2);
    expect(customCoreBindingCount(defaults([item('binding:agent:a1')]))).toBe(0);
    expect(customCoreBindingCount(undefined)).toBe(0);
  });

  it('mức tự trị của tổ chức: chỉ nêu khi đã đổi khỏi mặc định', () => {
    expect(changedAutonomy(defaults([item('autonomy', { customized: false })]))).toBeUndefined();
    expect(changedAutonomy(defaults([item('triage')]))).toBeUndefined();
    expect(changedAutonomy(defaults([item('autonomy')]))?.key).toBe('autonomy');
    expect(changedAutonomy(undefined)).toBeUndefined();
  });

  it('Về mặc định / Áp model chuẩn làm tươi cả thẻ ở Hôm nay của Mặt tiền Owner (khoá owner)', () => {
    const spy = vi.spyOn(queryClient, 'invalidateQueries');
    refreshSettings(queryClient);
    const keys = spy.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey: unknown[] }).queryKey));
    expect(keys).toContain(JSON.stringify(['owner']));
    expect(keys).toContain(JSON.stringify(['defaults']));
    spy.mockRestore();
  });

  it('chuỗi an toàn: object/null không bao giờ thành chữ', () => {
    expect(asText({ x: 1 })).toBe('');
    expect(asText(null)).toBe('');
    expect(asText('ok')).toBe('ok');
    expect(confirmLines({ current_text: { a: 1 } as unknown as string, default_text: 'Chuẩn' })).toEqual({ current: '', standard: 'Chuẩn' });
  });
});

// ── chip ──────────────────────────────────────────────────────────────────────

describe('<DefaultBadge>', () => {
  it('"Mặc định" khi đang dùng chuẩn, "Đã đổi" khi Sếp đã đổi', () => {
    const { rerender } = render(<DefaultBadge customized={false} />);
    expect(screen.getByTestId('default-badge')).toHaveTextContent('Mặc định');
    expect(screen.getByTestId('default-badge')).toHaveAttribute('data-state', 'default');
    rerender(<DefaultBadge customized />);
    expect(screen.getByTestId('default-badge')).toHaveTextContent('Đã đổi');
    expect(screen.getByTestId('default-badge')).toHaveAttribute('data-state', 'changed');
  });
});

// ── nút Về mặc định + hộp Xác nhận ───────────────────────────────────────────

describe('<ResetButton> / <DefaultControls>', () => {
  it('Đã đổi ⇒ Về mặc định ⇒ hộp Xác nhận (chưa gọi API) ⇒ Xác nhận gọi POST {confirm:true} ⇒ chip về "Mặc định"', async () => {
    let customized = true;
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/defaults') && c.method === 'GET') return json(200, defaults([item('triage', { label: 'Lọc tin', customized })]));
      if (c.url.endsWith('/defaults/triage/reset') && c.method === 'POST') {
        customized = false;
        return json(200, { key: 'triage', reset: true, customized: false, current_text: 'Mặc định của triage' });
      }
      return undefined;
    });
    const user = userEvent.setup();
    renderUi(<DefaultControls itemKey="triage" />);
    expect(await screen.findByTestId('default-badge-triage')).toHaveTextContent('Đã đổi');
    await user.click(screen.getByRole('button', { name: /Về mặc định/ }));
    const dlg = await screen.findByRole('dialog', { name: /Về mặc định: Lọc tin/ });
    expect(within(dlg).getByTestId('reset-current')).toHaveTextContent('Đang dùng: Đang dùng của triage');
    expect(within(dlg).getByTestId('reset-default')).toHaveTextContent('Mặc định: Mặc định của triage');
    expect(calls.some((c) => c.method === 'POST')).toBe(false);               // chưa Xác nhận ⇒ chưa ghi gì
    await user.click(within(dlg).getByRole('button', { name: 'Xác nhận' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ confirm: true }));
    await waitFor(() => expect(screen.getByTestId('default-badge-triage')).toHaveTextContent('Mặc định'));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByRole('button', { name: /Về mặc định/ })).toBeNull();
  });

  it('Huỷ không gọi API', async () => {
    const calls = mockFetch((c) => (c.url.endsWith('/defaults') ? json(200, defaults([item('backup', { label: 'Lịch sao lưu' })])) : undefined));
    const user = userEvent.setup();
    renderUi(<ResetButton itemKey="backup" />);
    await user.click(await screen.findByRole('button', { name: /Về mặc định/ }));
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Huỷ' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
  });

  it('lỗi reset ⇒ câu thân thiện + "Chi tiết kỹ thuật" (mã HTTP/mã lỗi), không "[object Object]"', async () => {
    mockFetch((c) => {
      if (c.url.endsWith('/defaults') && c.method === 'GET') return json(200, defaults([item('triage', { label: 'Lọc tin' })]));
      if (c.url.endsWith('/defaults/triage/reset'))
        return json(500, { status: 500, code: 'INTERNAL', title: 'Hệ thống gặp lỗi khi xử lý yêu cầu', error_id: 'err-9', detail: { op: 'defaults.reset' } });
      return undefined;
    });
    const user = userEvent.setup();
    renderUi(<ResetButton itemKey="triage" />);
    await user.click(await screen.findByRole('button', { name: /Về mặc định/ }));
    const dlg = await screen.findByRole('dialog');
    await user.click(within(dlg).getByRole('button', { name: 'Xác nhận' }));
    const alert = await within(dlg).findByRole('alert');
    expect(alert).toHaveTextContent('Hệ thống gặp lỗi khi xử lý yêu cầu');
    expect(within(alert).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(alert).toHaveTextContent('HTTP 500 · INTERNAL · error_id err-9');
    expect(document.body).not.toHaveTextContent('[object Object]');
    expect(dlg).toBeInTheDocument();                                           // lỗi ⇒ hộp còn mở để thử lại
  });

  it('văn bản lạ từ máy chủ (object) không bị vẽ; mục "Mặc định" hoặc chỉ-xem không có nút', async () => {
    mockFetch((c) =>
      c.url.endsWith('/defaults')
        ? json(200, defaults([
            item('triage', { customized: false }),
            item('jev.preset', { customized: true, resettable: false }),
            item('gen', { label: { x: 1 } as unknown as string, current_text: { y: 2 } as unknown as string }),
          ]))
        : undefined,
    );
    const user = userEvent.setup();
    renderUi(
      <>
        <DefaultControls itemKey="triage" />
        <DefaultControls itemKey="jev.preset" />
        <DefaultControls itemKey="gen" />
      </>,
    );
    expect(await screen.findByTestId('default-badge-triage')).toHaveTextContent('Mặc định');
    expect(screen.getByTestId('default-badge-jev.preset')).toHaveTextContent('Đã đổi');
    expect(screen.queryByTestId('reset-triage')).toBeNull();
    expect(screen.queryByTestId('reset-jev.preset')).toBeNull();
    await user.click(screen.getByTestId('reset-gen'));
    expect(await screen.findByRole('dialog')).not.toHaveTextContent('[object Object]');
    expect(document.body).not.toHaveTextContent('[object Object]');
  });

  it('không phải Owner ⇒ không gọi /defaults và không vẽ gì', async () => {
    const calls = mockFetch(() => undefined, 'manager');
    renderUi(<DefaultControls itemKey="triage" />, 'manager');
    await new Promise((r) => setTimeout(r, 30));
    expect(calls.some((c) => c.url.includes('/defaults'))).toBe(false);
    expect(screen.queryByTestId('default-controls-triage')).toBeNull();
  });
});

// ── dải Chế độ tiêu chuẩn ────────────────────────────────────────────────────

describe('<StandardModeStrip>', () => {
  it('đếm N mục đã đổi; Về mặc định tất cả ⇒ Xác nhận ⇒ 423 ⇒ hộp PIN ⇒ gửi lại thành công', async () => {
    let pinOk = false;
    let reset = false;
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/defaults') && c.method === 'GET')
        return json(200, reset ? defaults([item('triage', { customized: false }), item('backup', { customized: false })]) : defaults([item('triage'), item('backup'), item('gen', { customized: false })]));
      if (c.url.endsWith('/defaults/reset-all')) {
        if (!pinOk) return json(423, { status: 423, code: 'PIN_REQUIRED', title: 'Thao tác này cần nhập mã PIN' });
        reset = true;
        return json(200, { reset: 7 });
      }
      if (c.url.endsWith('/auth/pin/verify')) {
        pinOk = true;
        return json(200, { pin_verified_until: new Date(Date.now() + 1800_000).toISOString() });
      }
      return undefined;
    });
    const user = userEvent.setup();
    renderUi(<StandardModeStrip />);
    expect(await screen.findByTestId('standard-strip-text')).toHaveTextContent('Chế độ tiêu chuẩn: đã đổi 2 mục');
    expect(screen.getByTestId('standard-strip-badge')).toHaveTextContent('Đã đổi');
    expect(screen.queryByTestId('apply-standard')).toBeNull();                 // chưa có gợi ý ⇒ chưa có nút
    await user.click(screen.getByRole('button', { name: 'Về mặc định tất cả' }));
    const dlg = await screen.findByRole('dialog', { name: /Về mặc định tất cả/ });
    expect(within(dlg).getByTestId('reset-all-body')).toHaveTextContent('Khoá API');
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
    await user.click(within(dlg).getByRole('button', { name: 'Xác nhận' }));
    const pin = await screen.findByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    await waitFor(() => expect(within(pin).getAllByLabelText(/Mã PIN — chữ số/)[0]).toHaveFocus());
    await user.keyboard('246810');
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith('/defaults/reset-all'))).toHaveLength(2));
    for (const c of calls.filter((x) => x.url.endsWith('/defaults/reset-all'))) expect(c.body).toEqual({ confirm: true });
    await waitFor(() => expect(screen.getByTestId('standard-strip-text')).toHaveTextContent('Chế độ tiêu chuẩn: đang dùng'));
    expect(screen.getByTestId('standard-strip-badge')).toHaveTextContent('Mặc định');
  });

  it('mức tự trị của tổ chức đã đổi ⇒ hộp Xác nhận nói thẳng, và nút không mờ dù chỉ mục này khác mặc định', async () => {
    const aut = item('autonomy', { label: 'Mức tự trị của tổ chức', current_text: 'Gợi ý hành động (mức 3)', default_text: 'Soạn sẵn chờ duyệt (mức 4)' });
    mockFetch((c) => (c.url.endsWith('/defaults') ? json(200, defaults([item('triage', { customized: false }), aut])) : undefined));
    const user = userEvent.setup();
    renderUi(<StandardModeStrip />);
    expect(await screen.findByTestId('standard-strip-text')).toHaveTextContent('Chế độ tiêu chuẩn: đã đổi 1 mục');
    const btn = screen.getByRole('button', { name: 'Về mặc định tất cả' });
    expect(btn).toBeEnabled();
    await user.click(btn);
    const dlg = await screen.findByRole('dialog', { name: /Về mặc định tất cả/ });
    const note = within(dlg).getByTestId('reset-all-autonomy');
    expect(note).toHaveTextContent('mức tự trị của tổ chức');
    expect(note).toHaveTextContent('Gợi ý hành động (mức 3)');
    expect(note).toHaveTextContent('Soạn sẵn chờ duyệt (mức 4)');
    expect(dlg).not.toHaveTextContent('[object Object]');
  });

  it('mức tự trị chưa đổi ⇒ hộp Xác nhận không nhắc tới nó', async () => {
    mockFetch((c) => (c.url.endsWith('/defaults') ? json(200, defaults([item('triage'), item('autonomy', { customized: false })])) : undefined));
    const user = userEvent.setup();
    renderUi(<StandardModeStrip />);
    await user.click(await screen.findByRole('button', { name: 'Về mặc định tất cả' }));
    const dlg = await screen.findByRole('dialog', { name: /Về mặc định tất cả/ });
    expect(within(dlg).queryByTestId('reset-all-autonomy')).toBeNull();
  });

  it('hộp "Áp model chuẩn theo vai" đếm đúng số dòng gán lõi (không tính dòng gán của agent)', async () => {
    mockFetch((c) =>
      c.url.endsWith('/defaults')
        ? json(200, defaults([item('binding:core.gen'), item('binding:core.reply'), item('binding:agent:a1'), item('binding:agent:a2'), item('binding:agent:a3')], [SUGGEST]))
        : undefined,
    );
    const user = userEvent.setup();
    renderUi(<StandardModeStrip />);
    await user.click(await screen.findByTestId('apply-standard'));
    const dlg = await screen.findByRole('dialog', { name: /Áp model chuẩn theo vai/ });
    expect(within(dlg).getByTestId('apply-standard-body')).toHaveTextContent('Em bỏ 2 dòng gán model');
  });

  it('chưa đổi gì ⇒ "đang dùng", Về mặc định tất cả mờ', async () => {
    mockFetch((c) => (c.url.endsWith('/defaults') ? json(200, defaults([item('triage', { customized: false })])) : undefined));
    renderUi(<StandardModeStrip />);
    expect(await screen.findByTestId('standard-strip-text')).toHaveTextContent('Chế độ tiêu chuẩn: đang dùng');
    expect(screen.getByRole('button', { name: 'Về mặc định tất cả' })).toBeDisabled();
  });

  it('có gợi ý apply_standard ⇒ nút "Áp model chuẩn theo vai" (neo #chuan) ⇒ Xác nhận gọi apply-standard', async () => {
    let applied = false;
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/defaults') && c.method === 'GET')
        return json(200, applied ? defaults([item('binding:core.gen', { customized: false })]) : defaults([item('binding:core.gen'), item('binding:core.reply')], [SUGGEST]));
      if (c.url.endsWith('/defaults/apply-standard')) {
        applied = true;
        return json(200, { removed: 2 });
      }
      return undefined;
    });
    const user = userEvent.setup();
    const { container } = renderUi(<StandardModeStrip />);
    await user.click(await screen.findByTestId('apply-standard'));
    expect(container.querySelector('#chuan')).not.toBeNull();
    expect(screen.getByTestId('apply-standard-hint')).toHaveTextContent('Áp model chuẩn theo vai? (đang dùng 1 model cho mọi việc)');
    const dlg = await screen.findByRole('dialog', { name: /Áp model chuẩn theo vai/ });
    expect(within(dlg).getByTestId('apply-standard-body')).toHaveTextContent('Khoá API và nguồn AI giữ nguyên');
    await user.click(within(dlg).getByRole('button', { name: 'Xác nhận' }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith('/defaults/apply-standard'))?.body).toEqual({ confirm: true }));
    await waitFor(() => expect(screen.queryByTestId('apply-standard')).toBeNull());
  });

  it('lỗi tải ⇒ câu thân thiện + Chi tiết kỹ thuật; máy chủ cũ (404) ⇒ không vẽ dải', async () => {
    mockFetch((c) =>
      c.url.endsWith('/defaults') ? json(500, { status: 500, code: 'INTERNAL', title: 'Hệ thống gặp lỗi khi xử lý yêu cầu', error_id: 'e1' }) : undefined,
    );
    const first = renderUi(<StandardModeStrip />);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Hệ thống gặp lỗi khi xử lý yêu cầu');
    expect(within(alert).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    first.unmount();
    queryClient.clear();
    vi.unstubAllGlobals();
    mockFetch((c) => (c.url.endsWith('/defaults') ? json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tìm thấy' }) : undefined));
    renderUi(<StandardModeStrip />);
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId('standard-strip')).toBeNull();
    expect(screen.queryByTestId('standard-strip-error')).toBeNull();
  });
});

// ── mock e2e đúng hợp đồng ───────────────────────────────────────────────────

describe('mock-defaults (hợp đồng của máy chủ)', () => {
  type Out = { status: number; body: unknown };
  function ctx(method: string, path: string, body: Record<string, unknown> = {}, over: Partial<P2Ctx> = {}): { c: P2Ctx; out: Out } {
    const out: Out = { status: 0, body: undefined };
    const c = {
      method, path, body, role: 'owner', perms: {}, owner: true, userLabel: 'x', url: new URL(`http://x${path}`),
      reply: (status: number, b?: unknown) => { out.status = status; out.body = b; return true as const; },
      problem: (status: number, code: string, title: string, extra?: Record<string, unknown>) => { out.status = status; out.body = { code, title, ...extra }; return true as const; },
      text: () => true as const,
      needPin: () => false,
      ...over,
    } as P2Ctx;
    return { c, out };
  }

  it('GET chỉ Owner; triage mặc định "Đã đổi"; reset cần confirm; mã lỗi đúng', () => {
    const m = createMock({ fresh: false, emit: () => {} });
    let r = ctx('GET', '/defaults', {}, { role: 'manager' });
    expect(m.handle(r.c)).toBe(true);
    expect(r.out.status).toBe(403);
    r = ctx('GET', '/defaults');
    m.handle(r.c);
    const view = r.out.body as DefaultsResponse;
    expect(view.items.find((i) => i.key === 'triage')?.customized).toBe(true);
    expect(view.customized_count).toBe(1);
    for (const i of view.items) expect([typeof i.current_text, typeof i.default_text]).toEqual(['string', 'string']);
    r = ctx('POST', '/defaults/triage/reset', {});
    m.handle(r.c);
    expect(r.out.status).toBe(422);
    r = ctx('POST', '/defaults/khong.co/reset', { confirm: true });
    m.handle(r.c);
    expect([r.out.status, (r.out.body as { code: string }).code]).toEqual([404, 'DEFAULTS_KEY_UNKNOWN']);
    r = ctx('POST', '/defaults/jev.preset/reset', { confirm: true });
    m.handle(r.c);
    expect([r.out.status, (r.out.body as { code: string }).code]).toEqual([409, 'DEFAULTS_NOT_RESETTABLE']);
    r = ctx('POST', '/defaults/triage/reset', { confirm: true });
    m.handle(r.c);
    expect(r.out.status).toBe(200);
    r = ctx('GET', '/defaults');
    m.handle(r.c);
    expect((r.out.body as DefaultsResponse).customized_count).toBe(0);
  });

  it('mục autonomy: Về mặc định riêng mục này cũng cần PIN (423); có PIN thì về mặc định', () => {
    const m = createMock({ fresh: false, emit: () => {} });
    (m.hooks.customize as (b: { keys: string[] }) => unknown)({ keys: ['autonomy'] });
    let r = ctx('GET', '/defaults');
    m.handle(r.c);
    expect((r.out.body as DefaultsResponse).items.find((i) => i.key === 'autonomy')?.customized).toBe(true);
    r = ctx('POST', '/defaults/autonomy/reset', { confirm: true }, { needPin: () => true });
    m.handle(r.c);
    expect([r.out.status, (r.out.body as { code: string }).code]).toEqual([423, 'PIN_REQUIRED']);
    r = ctx('POST', '/defaults/autonomy/reset', { confirm: true });
    m.handle(r.c);
    expect(r.out.status).toBe(200);
  });

  it('reset-all cần PIN (423); apply-standard xoá bốn dòng gán lõi; gợi ý apply_standard khi ≥ 2 dòng đổi', () => {
    const m = createMock({ fresh: false, emit: () => {} });
    (m.hooks.customize as (b: { keys: string[] }) => unknown)({ keys: ['binding:core.gen', 'binding:core.reply', 'backup'] });
    let r = ctx('GET', '/defaults');
    m.handle(r.c);
    expect((r.out.body as DefaultsResponse).suggestions.map((s) => s.key)).toEqual(['apply_standard']);
    r = ctx('POST', '/defaults/reset-all', { confirm: true }, { needPin: () => true });
    m.handle(r.c);
    expect([r.out.status, (r.out.body as { code: string }).code]).toEqual([423, 'PIN_REQUIRED']);
    r = ctx('POST', '/defaults/apply-standard', { confirm: true });
    m.handle(r.c);
    expect(r.out.body).toEqual({ removed: 2 });
    r = ctx('POST', '/defaults/reset-all', { confirm: true });
    m.handle(r.c);
    expect(r.out.status).toBe(200);
    r = ctx('GET', '/defaults');
    m.handle(r.c);
    expect((r.out.body as DefaultsResponse).customized_count).toBe(0);
    expect(m.handle(ctx('GET', '/system/health').c)).toBe(false);
  });
});

// ── bảng gán model: Chuẩn, mức suy nghĩ, Nâng cao ────────────────────────────

const STD = { model_name: 'sonnet', provider_name: 'Claude Code CLI', tier: 'balanced' as const, tier_label: 'Cân bằng', effort: 'medium' as const, temperature: 0.3, context_tokens: 6000 };
const slot = (over: Partial<AgentBindingSlot> = {}): AgentBindingSlot => ({ agent_key: 'core.gen', label: 'Gen — trợ lý quản trị', binding: null, ...over });

describe('apiModel (bảng gán model)', () => {
  it('v0.1.58: chưa có model chuẩn ⇒ "Chuẩn: <lý do>" của máy chủ; thiếu trường / lý do rỗng / không phải chuỗi ⇒ câu cũ', () => {
    const reason = 'cần khoá API hoặc đăng nhập Antigravity CLI';
    expect(bindingModelText(slot({ source: 'standard', standard: null, standard_reason: reason }))).toBe(`Chuẩn: ${reason}`);
    expect(bindingModelText(slot({ source: 'standard', standard_reason: 'chưa có model — bấm Kiểm tra kết nối ở Antigravity CLI' }))).toBe(
      'Chuẩn: chưa có model — bấm Kiểm tra kết nối ở Antigravity CLI',
    );
    // Có model chuẩn thì lý do (nếu có) bị bỏ qua; đã gán thì tên model.
    expect(bindingModelText(slot({ source: 'standard', standard: STD, standard_reason: reason }))).toBe('Chuẩn: sonnet (tự chọn)');
    // Máy chủ cũ (không có trường) / lý do rỗng / lý do không phải chuỗi ⇒ câu cũ, không bao giờ render object.
    expect(bindingModelText(slot({ source: 'standard', standard: null }))).toBe('Chuẩn: chưa có nguồn phù hợp');
    expect(bindingModelText(slot({ source: 'standard', standard: null, standard_reason: null }))).toBe('Chuẩn: chưa có nguồn phù hợp');
    expect(bindingModelText(slot({ source: 'standard', standard: null, standard_reason: '  ' }))).toBe('Chuẩn: chưa có nguồn phù hợp');
    expect(bindingModelText(slot({ source: 'standard', standard: null, standard_reason: { x: 1 } as unknown as string }))).toBe('Chuẩn: chưa có nguồn phù hợp');
    expect(bindingModelText(slot({ standard_reason: reason }))).toBe(`Chuẩn: ${reason}`);
  });

  it('chưa gán + có hồ sơ ⇒ "Chuẩn: <model> (tự chọn)"; máy chủ cũ ⇒ "chưa gán"; đã gán ⇒ tên model', () => {
    expect(bindingModelText(slot({ source: 'standard', standard: STD }))).toBe('Chuẩn: sonnet (tự chọn)');
    expect(bindingModelText(slot({ source: 'standard', standard: null }))).toBe('Chuẩn: chưa có nguồn phù hợp');
    expect(bindingModelText(slot())).toBe('chưa gán');
    const b = { model_id: 'm', model_name: 'opus', provider_name: 'X', temperature: 0.2, context_tokens: 8000, rule_codes: [], effort: 'high' as const };
    expect(bindingModelText(slot({ binding: b, source: 'custom' }))).toBe('opus');
    expect(bindingEffortText(slot({ binding: b, source: 'custom' }))).toBe('Cao');
    expect(bindingEffortText(slot({ source: 'standard', standard: STD }))).toBe('Vừa (chuẩn)');
    expect(bindingEffortText(slot())).toBe('—');
    // v0.1.66 (F021): vai lõi theo hồ sơ chuẩn (cố định), không còn "bộ quy tắc"; agent trực việc mới có số do Sếp đặt.
    expect(bindingParamsText(slot({ source: 'standard', standard: STD }))).toBe('Gen — trợ lý quản trị — nhiệt độ 0,30 · ngữ cảnh 6k token (theo hồ sơ chuẩn, cố định)');
    expect(bindingParamsText(slot({ agent_key: 'agent:a1', label: 'Trợ lý', source: 'standard', standard: STD }))).toBe('Trợ lý — nhiệt độ 0,30 · ngữ cảnh 6k token (chuẩn)');
    expect(bindingParamsText(slot({ agent_key: 'agent:a1', label: 'Trợ lý', binding: b, source: 'custom' }))).toBe('Trợ lý — nhiệt độ 0,20 · ngữ cảnh 8k token');
    expect(bindingParamsText(slot({ binding: b, source: 'custom' }))).toBe('Gen — trợ lý quản trị — nhiệt độ và ngữ cảnh theo hồ sơ chuẩn (cố định)');
  });

  it('mức suy nghĩ model nhận: CLI theo danh sách đã kiểm; haiku/khoá API không có', () => {
    const cc = { kind: 'claude_code_cli', last_test: null } as Pick<Provider, 'kind' | 'last_test'>;
    expect(supportedEfforts(cc, 'haiku')).toEqual([]);
    expect(supportedEfforts(cc, 'sonnet')).toEqual(['low', 'medium', 'high', 'xhigh', 'max']);
    expect(supportedEfforts({ kind: 'antigravity_cli', last_test: null } as Pick<Provider, 'kind' | 'last_test'>, 'gemini-3.8-flash')).toEqual(['low', 'medium', 'high']);
    expect(supportedEfforts({ kind: 'gemini', last_test: null } as Pick<Provider, 'kind' | 'last_test'>, 'gemini-2.5-flash')).toEqual([]);
    expect(supportedEfforts(undefined, 'x')).toEqual([]);
    const tested = {
      kind: 'antigravity_cli',
      last_test: { ok: true, latency_ms: 1, models: [], error: null, model_groups: [{ label: 'Gemini', models: [{ id: 'gemini-3.1-pro', label: 'Pro', group: 'Gemini', tier: 'strong', hint: '', source: 'cli', efforts: ['low', 'high'] }] }] },
    } as unknown as Pick<Provider, 'kind' | 'last_test'>;
    expect(supportedEfforts(tested, 'gemini-3.1-pro')).toEqual(['low', 'high']);
  });
});

const PROVIDERS: Provider[] = [
  {
    id: 'pv-cc', kind: 'claude_code_cli', name: 'Claude Code CLI', endpoint: null, failover_rank: 1, enabled: true, auth_state: 'ok', keys: [],
    models: [{ id: 'm-sonnet', model_name: 'sonnet', daily_quota: null, used_today: 0 }, { id: 'm-haiku', model_name: 'haiku', daily_quota: null, used_today: 0 }],
  },
  {
    id: 'pv-gem', kind: 'gemini', name: 'Gemini API', endpoint: null, failover_rank: 2, enabled: true, auth_state: 'ok',
    keys: [{ id: 'k1', label: 'GEM-KEY-01', last4: '9f2a', enabled: true, cooldown_until: null, quota_left_pct: null }],
    models: [{ id: 'm-flash', model_name: 'gemini-2.5-flash', daily_quota: null, used_today: 0 }],
  },
];

const BINDINGS: BindingsPage = {
  items: [
    slot({ agent_key: 'core.gen', label: 'Gen — trợ lý quản trị', source: 'standard', standard: STD }),
    slot({
      agent_key: 'core.refinery', label: 'Sàng lọc & suy luận chính', source: 'custom',
      binding: { model_id: 'm-flash', model_name: 'gemini-2.5-flash', provider_name: 'Gemini API', temperature: 0.2, context_tokens: 64000, rule_codes: ['R-01'], effort: null },
    }),
  ],
  models: [
    { id: 'm-sonnet', model_name: 'sonnet', provider_name: 'Claude Code CLI', enabled: true },
    { id: 'm-haiku', model_name: 'haiku', provider_name: 'Claude Code CLI', enabled: true },
    { id: 'm-flash', model_name: 'gemini-2.5-flash', provider_name: 'Gemini API', enabled: true },
  ],
};

function apiHandler(c: Call): Response | undefined {
  if (c.url.includes('/agents/bindings') && c.method === 'GET') return json(200, BINDINGS);
  if (c.url.endsWith('/failover-rules')) return json(200, []);
  if (c.url.endsWith('/providers') && c.method === 'GET') return json(200, PROVIDERS);
  if (c.url.includes('/providers/credentials') || c.url.includes('/cli/profiles')) return json(200, []);
  return undefined;
}

describe('<ApiScreen> bảng gán model ở chế độ tiêu chuẩn', () => {
  it('slot chưa gán hiện "Chuẩn: sonnet (tự chọn)" + chip Mặc định + mức "Vừa (chuẩn)"; slot đã gán hiện chip Đã đổi', async () => {
    mockFetch((c) => apiHandler(c));
    renderUi(<ApiScreen />);
    const row = (await screen.findByText('Gen — trợ lý quản trị', { selector: '.apm-table__agent' })).closest('tr') as HTMLElement;
    expect(within(row).getByRole('button', { name: /Chuẩn: sonnet \(tự chọn\)/ })).toBeInTheDocument();
    expect(within(row).getByTestId('binding-badge-core.gen')).toHaveTextContent('Mặc định');
    expect(within(row).getByTestId('binding-effort-core.gen')).toHaveTextContent('Vừa (chuẩn)');
    const done = screen.getByText('Sàng lọc & suy luận chính', { selector: '.apm-table__agent' }).closest('tr') as HTMLElement;
    expect(within(done).getByTestId('binding-badge-core.refinery')).toHaveTextContent('Đã đổi');
    expect(within(done).getByRole('button', { name: /gemini-2.5-flash/ })).toBeInTheDocument();
    expect(within(done).getByTestId('binding-effort-core.refinery')).toHaveTextContent('—');
  });

  it('v0.1.67 (F016): dòng gán có inactive_reason ⇒ chip nói thẳng "đang dùng Chuẩn"; dòng bình thường không có chip', async () => {
    const page: BindingsPage = {
      ...BINDINGS,
      items: BINDINGS.items.map((it) =>
        it.agent_key === 'core.refinery' && it.binding
          ? { ...it, binding: { ...it.binding, inactive_reason: 'Nguồn đã tắt — agent đang dùng Chuẩn' } }
          : it,
      ),
    };
    mockFetch((c) => (c.url.includes('/agents/bindings') && c.method === 'GET' ? json(200, page) : apiHandler(c)));
    renderUi(<ApiScreen />);
    const done = (await screen.findByText('Sàng lọc & suy luận chính', { selector: '.apm-table__agent' })).closest('tr') as HTMLElement;
    expect(within(done).getByTestId('binding-inactive-core.refinery')).toHaveTextContent('Nguồn đã tắt — agent đang dùng Chuẩn');
    expect(screen.queryByTestId('binding-inactive-core.gen')).toBeNull();
  });

  it('nhiệt độ / ngữ cảnh và hạn mức model nằm trong khối "Nâng cao" (đóng sẵn), không còn cột ở bảng chính', async () => {
    mockFetch((c) => apiHandler(c));
    renderUi(<ApiScreen />);
    const table = (await screen.findByText('Gen — trợ lý quản trị', { selector: '.apm-table__agent' })).closest('table') as HTMLElement;
    expect(within(table).queryByRole('columnheader', { name: 'Nhiệt độ' })).toBeNull();
    expect(within(table).queryByRole('columnheader', { name: 'Bộ quy tắc' })).toBeNull();
    expect(within(table).getByRole('columnheader', { name: 'Mức suy nghĩ' })).toBeInTheDocument();
    const adv = screen.getByTestId('api-advanced');
    expect(adv.tagName).toBe('DETAILS');
    expect(adv).not.toHaveAttribute('open');
    expect(within(adv).getByText(/^Nâng cao — nhiệt độ, ngữ cảnh và hạn mức model$/)).toBeInTheDocument();
    expect(within(adv).getByTestId('binding-params-core.refinery')).toHaveTextContent('nhiệt độ và ngữ cảnh theo hồ sơ chuẩn (cố định)');
    expect(within(adv).queryByText('Tham số sàng lọc')).toBeNull();                 // F018: thẻ trùng đã bỏ
    expect(within(adv).getByText('Giới hạn gọi API')).toBeInTheDocument();
  });

  it('chọn mức suy nghĩ theo vai: model CLI có ô "Mức suy nghĩ"; lưu gửi effort; haiku/khoá API không có ô', async () => {
    const calls = mockFetch((c) => (c.method === 'PUT' ? json(200, { agent_key: 'core.gen', label: 'Gen', binding: { ...BINDINGS.items[1].binding!, effort: 'high' }, source: 'custom', standard: null }) : apiHandler(c)));
    const user = userEvent.setup();
    renderUi(<ApiScreen />);
    const row = (await screen.findByText('Gen — trợ lý quản trị', { selector: '.apm-table__agent' })).closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: /Chuẩn: sonnet/ }));
    const dlg = await screen.findByRole('dialog');
    expect((within(dlg).getByLabelText('Model') as HTMLSelectElement).value).toBe('m-sonnet');   // chọn sẵn model Chuẩn
    const effort = await within(dlg).findByLabelText('Mức suy nghĩ');
    expect(within(effort).getAllByRole('option').map((o) => o.getAttribute('value'))).toEqual(['', 'low', 'medium', 'high', 'xhigh', 'max']);
    // v0.1.66 (F021): vai lõi (Gen) không có ô nhiệt độ / ngữ cảnh / bộ quy tắc — chỉ có câu "theo hồ sơ chuẩn (cố định)".
    expect(within(dlg).queryByTestId('binding-edit-advanced')).toBeNull();
    expect(within(dlg).getByTestId('binding-core-fixed-note')).toBeInTheDocument();
    await user.selectOptions(effort, 'high');
    await user.click(within(dlg).getByRole('button', { name: 'Lưu' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toMatchObject({ model_id: 'm-sonnet', effort: 'high' }));
  });

  it('chọn haiku hoặc model khoá API ⇒ không có ô mức suy nghĩ, có câu giải thích, gửi effort null', async () => {
    const calls = mockFetch((c) => (c.method === 'PUT' ? json(200, { agent_key: 'core.gen', label: 'Gen', binding: BINDINGS.items[1].binding, source: 'custom', standard: null }) : apiHandler(c)));
    const user = userEvent.setup();
    renderUi(<ApiScreen />);
    const row = (await screen.findByText('Gen — trợ lý quản trị', { selector: '.apm-table__agent' })).closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: /Chuẩn: sonnet/ }));
    const dlg = await screen.findByRole('dialog');
    await user.selectOptions(within(dlg).getByLabelText('Model'), 'm-haiku');
    expect(within(dlg).queryByLabelText('Mức suy nghĩ')).toBeNull();
    expect(within(dlg).getByTestId('binding-no-effort')).toBeInTheDocument();
    await user.selectOptions(within(dlg).getByLabelText('Model'), 'm-flash');
    expect(within(dlg).queryByLabelText('Mức suy nghĩ')).toBeNull();
    await user.click(within(dlg).getByRole('button', { name: 'Lưu' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toMatchObject({ model_id: 'm-flash', effort: null }));
  });
});
