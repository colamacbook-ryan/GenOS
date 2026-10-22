/**
 * v0.1.41 (F-8a, F-8, F-86): khung Gen giữ hội thoại qua tải lại trang, danh sách "Hội thoại cũ", mở Bản tin Gen từ
 * chuông (`?gen=<id>`), nút Hữu ích / Không hữu ích.
 */
import { StrictMode, type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, RouterProvider, createMemoryRouter } from 'react-router-dom';
import type { GenConversation, GenMessage } from '@gen-harness/contracts';
import { GenPanel } from '../../src/gen/GenPanel';
import { loadConversation, sendQuestion, stopAll } from '../../src/gen/genClient';
import { useGenStore } from '../../src/gen/genStore';
import { setNavigator } from '../../src/lib/navigation';
import { qk } from '../../src/lib/queries';
import { queryClient } from '../../src/lib/queryClient';
import { useToasts } from '../../src/lib/toast';
import { AppShell } from '../../src/shell/AppShell';
import { briefingContent } from '../mock-gen';

const ME = {
  id: 'u1', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A', role: { code: 'owner', name: 'Owner — Sếp' },
  org: { id: 'o1', name: 'Genesis', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: {}, must_change_password: false,
  features: { gen: true },
};

const CID = '6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b';
const BID = '0b8e7d6c-5a4b-4c3d-9e2f-1a0b9c8d7e6f';

const CHAT: GenMessage[] = [
  { id: 'm1', role: 'user', turn_id: 't9', content: { text: 'Hôm nay có gì gấp?' }, created_at: '2026-10-02T01:00:00Z' },
  { id: 'm2', role: 'assistant', turn_id: 't9', content: { steps: [{ kind: 'say', text: 'Có 3 việc cần Sếp xem.' }] }, created_at: '2026-10-02T01:00:05Z', feedback: null },
];
const BRIEFING: GenMessage[] = [
  { id: 'b1', role: 'assistant', turn_id: 'tb1', content: briefingContent('sáng 02/10', '2026-10-02T07:30:00+07:00', true), created_at: '2026-10-02T00:30:00Z', feedback: null },
];
const LIST: GenConversation[] = [
  { id: BID, title: 'Bản tin Gen · sáng 02/10', created_at: '2026-10-02T00:30:00Z', last_at: '2026-10-02T00:30:00Z', kind: 'briefing' },
  { id: CID, title: 'Hôm nay có gì gấp?', created_at: '2026-10-01T01:00:00Z', last_at: '2026-10-01T01:00:05Z', kind: 'chat' },
];

type Reply = { status: number; body?: unknown };
type Handler = (url: string, method: string, body: unknown) => Reply | Promise<Reply> | undefined;
const calls: Array<{ method: string; url: string; body: unknown }> = [];
let handler: Handler = () => undefined;

function defaults(url: string, method: string): Reply | undefined {
  if (url.endsWith('/auth/me')) return { status: 200, body: ME };
  if (url.endsWith(`/gen/conversations/${CID}/messages`)) return { status: 200, body: CHAT };
  if (url.endsWith(`/gen/conversations/${BID}/messages`)) return { status: 200, body: BRIEFING };
  if (url.endsWith('/gen/conversations') && method === 'GET') return { status: 200, body: LIST };
  if (url.endsWith('/gen/feedback') && method === 'PUT') return { status: 200, body: { turn_id: 't9', rating: 'helpful', kind: 'reply' } };
  if (url.includes('/gen/feedback/') && method === 'DELETE') return { status: 204 };
  return undefined;
}

function stubApi() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ method, url, body });
      const r = (await handler(url, method, body)) ?? defaults(url, method) ?? { status: 404, body: { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' } };
      if (r.status === 204) return new Response(null, { status: 204 });
      return new Response(JSON.stringify(r.body ?? {}), {
        status: r.status,
        headers: { 'Content-Type': r.status >= 400 ? 'application/problem+json' : 'application/json' },
      });
    }),
  );
}

const navigations: string[] = [];

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(qk.me, ME);
  calls.length = 0;
  navigations.length = 0;
  handler = () => undefined;
  window.localStorage.clear();
  useToasts.setState({ toasts: [] });
  useGenStore.setState({ openByUser: {}, conversationId: null, conversationOwner: null, messages: [], busy: false, restoring: false, loadingConversation: false, spotlight: null });
  setNavigator((to) => navigations.push(to));
  vi.stubGlobal('WebSocket', undefined);
  stubApi();
});
afterEach(() => {
  stopAll();
  cleanup();
  vi.unstubAllGlobals();
});

function wrap(ui: ReactNode) {
  return render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>{ui}</MemoryRouter>
      </QueryClientProvider>
    </StrictMode>,
  );
}

const messageCalls = (id: string) => calls.filter((c) => c.method === 'GET' && c.url.endsWith(`/gen/conversations/${id}/messages`));

describe('Giữ hội thoại qua tải lại trang (F-8a)', () => {
  it('mã đã lưu + messages rỗng ⇒ gọi messages đúng 1 lần và hiện tin', async () => {
    useGenStore.setState({ conversationId: CID, conversationOwner: 'u1' });
    wrap(<GenPanel userId="u1" />);
    expect(await screen.findByText('Có 3 việc cần Sếp xem.')).toBeInTheDocument();
    expect(screen.getByText('Hôm nay có gì gấp?')).toBeInTheDocument();
    expect(messageCalls(CID)).toHaveLength(1);
  });

  it('mã của người khác ⇒ không gọi API, reset', async () => {
    useGenStore.setState({ conversationId: CID, conversationOwner: 'u2' });
    wrap(<GenPanel userId="u1" />);
    await waitFor(() => expect(useGenStore.getState().conversationId).toBeNull());
    expect(useGenStore.getState().conversationOwner).toBeNull();
    expect(messageCalls(CID)).toHaveLength(0);
    expect(screen.getByText(/Chào Sếp, em là Gen/)).toBeInTheDocument();
  });

  it('404 (đã xoá / quá hạn lưu) ⇒ reset im lặng, không hiện lỗi', async () => {
    handler = (url) => (url.endsWith(`/gen/conversations/${CID}/messages`) ? { status: 404, body: { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' } } : undefined);
    useGenStore.setState({ conversationId: CID, conversationOwner: 'u1' });
    wrap(<GenPanel userId="u1" />);
    await waitFor(() => expect(useGenStore.getState().conversationId).toBeNull());
    expect(messageCalls(CID)).toHaveLength(1);
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByText(/Chưa tải lại được/)).toBeNull();
    expect(screen.getByText(/Chào Sếp, em là Gen/)).toBeInTheDocument();
  });

  it('lỗi khác ⇒ câu thân thiện + Chi tiết kỹ thuật + Thử lại; câu hỏi mới không rơi vào hội thoại cũ', async () => {
    let fail = true;
    handler = (url) =>
      url.endsWith(`/gen/conversations/${CID}/messages`) && fail
        ? { status: 500, body: { status: 500, code: 'INTERNAL', title: 'Hệ thống gặp lỗi', error_id: 'E-1' } }
        : undefined;
    useGenStore.setState({ conversationId: CID, conversationOwner: 'u1' });
    wrap(<GenPanel userId="u1" />);
    expect(await screen.findByText('Chưa tải lại được hội thoại trước — Sếp thử lại sau ít phút.')).toBeInTheDocument();
    expect(screen.getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(screen.getByText(/HTTP 500 · INTERNAL · error_id E-1/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('[object Object]');
    // Mã cũ được bỏ ⇒ câu hỏi kế tiếp mở hội thoại mới (không vào hội thoại Sếp không thấy).
    expect(useGenStore.getState().conversationId).toBeNull();
    fail = false;
    await userEvent.click(screen.getByRole('button', { name: /Thử lại/ }));
    expect(await screen.findByText('Có 3 việc cần Sếp xem.')).toBeInTheDocument();
    expect(screen.queryByText(/Chưa tải lại được/)).toBeNull();
    expect(useGenStore.getState().conversationId).toBe(CID);
    expect(messageCalls(CID)).toHaveLength(2);
  });

  it('đang tải lại ⇒ hiện "Đang mở lại hội thoại…", không hiện lời chào/ví dụ, chưa cho gửi', async () => {
    let release!: () => void;
    handler = (url) =>
      url.endsWith(`/gen/conversations/${CID}/messages`)
        ? new Promise<Reply>((r) => {
            release = () => r({ status: 200, body: CHAT });
          })
        : undefined;
    useGenStore.setState({ conversationId: CID, conversationOwner: 'u1' });
    wrap(<GenPanel userId="u1" />);
    expect(await screen.findByText('Đang mở lại hội thoại…')).toBeInTheDocument();
    expect(screen.queryByText(/Chào Sếp, em là Gen/)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Hôm nay có gì cần tôi xử lý?' })).toBeNull();
    await waitFor(() => expect(release).toBeTypeOf('function'));
    act(() => release());
    expect(await screen.findByText('Có 3 việc cần Sếp xem.')).toBeInTheDocument();
    expect(useGenStore.getState().restoring).toBe(false);
  });

  it('tải lại còn chờ ⇒ gửi câu hỏi ⇒ tải xong KHÔNG xoá câu trả lời đang viết, busy vẫn true', async () => {
    let release!: () => void;
    handler = (url, method) => {
      if (url.endsWith(`/gen/conversations/${CID}/messages`))
        return new Promise<Reply>((r) => {
          release = () => r({ status: 200, body: CHAT });
        });
      if (url.endsWith('/gen/turns') && method === 'POST') return { status: 202, body: { turn_id: 't2', conversation_id: CID } };
      if (url.endsWith('/gen/turns/t2')) return { status: 200, body: { turn_id: 't2', status: 'running', steps: [] } };
      return undefined;
    };
    useGenStore.setState({ conversationId: CID, conversationOwner: 'u1' });
    wrap(<GenPanel userId="u1" />);
    await waitFor(() => expect(release).toBeTypeOf('function'));
    await act(() => sendQuestion('Còn gì nữa?', 'u1'));
    expect(useGenStore.getState().messages.some((m) => m.turnId === 't2' && m.status === 'running')).toBe(true);
    act(() => release());
    await waitFor(() => expect(useGenStore.getState().restoring).toBe(false));
    const st = useGenStore.getState();
    expect(st.busy).toBe(true);
    expect(st.messages.find((m) => m.turnId === 't2' && m.role === 'assistant')?.status).toBe('running');
    // Tin cũ được chèn lên trước, câu vừa hỏi vẫn ở cuối.
    expect(st.messages.map((m) => m.text ?? m.steps[0]?.kind)).toEqual(['Hôm nay có gì gấp?', 'say', 'Còn gì nữa?', undefined]);
    expect(await screen.findByText('Có 3 việc cần Sếp xem.')).toBeInTheDocument();
  });

  it('câu hỏi mới lưu chủ hội thoại = người hỏi', async () => {
    handler = (url, method) => (url.endsWith('/gen/turns') && method === 'POST' ? { status: 202, body: { turn_id: 't1', conversation_id: CID } } : undefined);
    wrap(<GenPanel userId="u1" />);
    await userEvent.type(screen.getByLabelText('Câu hỏi cho Gen'), 'Chào Gen{Enter}');
    await waitFor(() => expect(useGenStore.getState().conversationId).toBe(CID));
    expect(useGenStore.getState().conversationOwner).toBe('u1');
    expect(JSON.parse(window.localStorage.getItem('gh-gen') ?? '{}').state).toMatchObject({ conversationId: CID, conversationOwner: 'u1' });
  });
});

describe('Hội thoại cũ (F-8a)', () => {
  it('liệt kê, nhãn Bản tin, bấm mở đúng hội thoại; Esc đóng và trả tiêu điểm', async () => {
    const user = userEvent.setup();
    wrap(<GenPanel userId="u1" />);
    const btn = screen.getByRole('button', { name: 'Hội thoại cũ' });
    await user.click(btn);
    const dlg = await screen.findByRole('dialog', { name: 'Hội thoại cũ' });
    const items = await within(dlg).findAllByRole('button');
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent('Bản tin');
    expect(items[0]).toHaveTextContent('Bản tin Gen · sáng 02/10');
    expect(items[1]).not.toHaveTextContent('Bản tin');
    await user.click(items[1]);
    expect(await screen.findByText('Có 3 việc cần Sếp xem.')).toBeInTheDocument();
    expect(useGenStore.getState().conversationId).toBe(CID);
    expect(useGenStore.getState().conversationOwner).toBe('u1');
    expect(screen.queryByRole('dialog', { name: 'Hội thoại cũ' })).toBeNull();

    await user.click(btn);
    await screen.findByRole('dialog', { name: 'Hội thoại cũ' });
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog', { name: 'Hội thoại cũ' })).toBeNull();
    expect(btn).toHaveFocus();
    // Esc của danh sách không đóng cả khung Gen.
    expect(useGenStore.getState().openByUser.u1).not.toBe(false);
  });

  it('danh sách rỗng ⇒ "Chưa có hội thoại nào"', async () => {
    handler = (url, method) => (url.endsWith('/gen/conversations') && method === 'GET' ? { status: 200, body: [] } : undefined);
    wrap(<GenPanel userId="u1" />);
    await userEvent.click(screen.getByRole('button', { name: 'Hội thoại cũ' }));
    expect(await screen.findByText('Chưa có hội thoại nào')).toBeInTheDocument();
  });

  it('mở một dòng lỗi (không phải 404) ⇒ toast thân thiện + Chi tiết kỹ thuật', async () => {
    handler = (url) =>
      url.endsWith(`/gen/conversations/${CID}/messages`)
        ? { status: 503, body: { status: 503, code: 'UNAVAILABLE', title: 'Dịch vụ tạm bận', detail: { reason: 'db' } } }
        : undefined;
    const user = userEvent.setup();
    wrap(<GenPanel userId="u1" />);
    await user.click(screen.getByRole('button', { name: 'Hội thoại cũ' }));
    const dlg = await screen.findByRole('dialog', { name: 'Hội thoại cũ' });
    const items = await within(dlg).findAllByRole('button');
    await user.click(items[1]);
    await waitFor(() => expect(useToasts.getState().toasts).toHaveLength(1));
    const t = useToasts.getState().toasts[0];
    expect(t.tone).toBe('bad');
    expect(t.text).toMatch(/Chi tiết kỹ thuật: HTTP 503 · UNAVAILABLE/);
    expect(t.text).not.toContain('[object Object]');
  });

  it('Gen đang trả lời ⇒ có dòng giải thích vì sao các dòng bị khoá', async () => {
    useGenStore.setState({ busy: true });
    wrap(<GenPanel userId="u1" />);
    await userEvent.click(screen.getByRole('button', { name: 'Hội thoại cũ' }));
    const dlg = await screen.findByRole('dialog', { name: 'Hội thoại cũ' });
    expect(await within(dlg).findByTestId('gen-history-busy')).toHaveTextContent('Gen đang trả lời — đợi xong rồi mở hội thoại cũ nhé.');
    for (const b of within(dlg).getAllByRole('button')) expect(b).toBeDisabled();
  });

  it('nút "Hội thoại cũ" chỉ có aria-expanded (không aria-pressed)', async () => {
    wrap(<GenPanel userId="u1" />);
    const btn = screen.getByRole('button', { name: 'Hội thoại cũ' });
    expect(btn).toHaveAttribute('aria-expanded', 'false');
    expect(btn).not.toHaveAttribute('aria-pressed');
  });

  it('đang tải hội thoại (vd bản tin từ chuông) ⇒ "Đang mở hội thoại…", không lời chào/ví dụ, chưa cho gửi', async () => {
    let release!: () => void;
    handler = (url) =>
      url.endsWith(`/gen/conversations/${BID}/messages`)
        ? new Promise<Reply>((r) => {
            release = () => r({ status: 200, body: BRIEFING });
          })
        : undefined;
    wrap(<GenPanel userId="u1" />);
    let done!: Promise<unknown>;
    act(() => {
      done = loadConversation(BID, 'u1');
    });
    expect(await screen.findByText('Đang mở hội thoại…')).toBeInTheDocument();
    expect(screen.queryByText(/Chào Sếp, em là Gen/)).toBeNull();
    expect(screen.getByRole('button', { name: 'Gửi' })).toBeDisabled();
    await waitFor(() => expect(release).toBeTypeOf('function'));
    act(() => release());
    await act(async () => {
      expect(await done).toBe('opened');
    });
    expect(useGenStore.getState().loadingConversation).toBe(false);
    expect(await screen.findByText(/Đã tra việc, khách, nháp, sự cố…/)).toBeInTheDocument();
  });

  it('lỗi tải ⇒ câu thân thiện + Chi tiết kỹ thuật, không "[object Object]"', async () => {
    handler = (url, method) =>
      url.endsWith('/gen/conversations') && method === 'GET'
        ? { status: 503, body: { status: 503, code: 'UNAVAILABLE', title: 'Dịch vụ tạm bận', detail: { reason: 'db' } } }
        : undefined;
    wrap(<GenPanel userId="u1" />);
    await userEvent.click(screen.getByRole('button', { name: 'Hội thoại cũ' }));
    expect(await screen.findByText(/Chưa tải được danh sách hội thoại/)).toBeInTheDocument();
    expect(screen.getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(screen.getByText(/HTTP 503 · UNAVAILABLE/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Thử lại/ })).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('[object Object]');
  });
});

describe('Hữu ích / Không hữu ích (F-86)', () => {
  async function openChat() {
    useGenStore.setState({ conversationId: CID, conversationOwner: 'u1' });
    wrap(<GenPanel userId="u1" />);
    await screen.findByText('Có 3 việc cần Sếp xem.');
  }

  it('Hữu ích gọi PUT đúng turn_id, aria-pressed=true; bấm lại ⇒ DELETE', async () => {
    const user = userEvent.setup();
    await openChat();
    const good = screen.getByRole('button', { name: 'Hữu ích' });
    expect(good).toHaveAttribute('aria-pressed', 'false');
    await user.click(good);
    expect(good).toHaveAttribute('aria-pressed', 'true');
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.url.endsWith('/gen/feedback'))).toBe(true));
    expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ conversation_id: CID, turn_id: 't9', rating: 'helpful' });
    expect(screen.getByRole('button', { name: 'Không hữu ích' })).toHaveAttribute('aria-pressed', 'false');

    await user.click(good);
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.url.endsWith('/gen/feedback/t9'))).toBe(true));
    expect(good).toHaveAttribute('aria-pressed', 'false');
  });

  it('lỗi ⇒ hoàn tác + toast', async () => {
    handler = (url, method) =>
      url.endsWith('/gen/feedback') && method === 'PUT' ? { status: 500, body: { status: 500, code: 'INTERNAL', title: 'Hệ thống gặp lỗi khi xử lý yêu cầu' } } : undefined;
    const user = userEvent.setup();
    await openChat();
    const bad = screen.getByRole('button', { name: 'Không hữu ích' });
    await user.click(bad);
    await waitFor(() => expect(bad).toHaveAttribute('aria-pressed', 'false'));
    const toasts = useToasts.getState().toasts;
    expect(toasts).toHaveLength(1);
    expect(toasts[0].tone).toBe('bad');
    expect(toasts[0].text).toMatch(/Hệ thống gặp lỗi/);
    expect(toasts[0].text).toMatch(/Chi tiết kỹ thuật: HTTP 500 · INTERNAL/);
    expect(toasts[0].text).not.toContain('[object Object]');
  });

  it('tin lỗi cục bộ (không có turnId) không có nút đánh giá', async () => {
    useGenStore.setState({ messages: [{ id: 'e-1', role: 'assistant', status: 'failed', steps: [{ kind: 'say', text: 'Không gửi được câu hỏi.' }] }] });
    wrap(<GenPanel userId="u1" />);
    expect(screen.getByText('Không gửi được câu hỏi.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Hữu ích' })).toBeNull();
  });
});

describe('Bản tin Gen (F-8)', () => {
  it('tin bản tin: nhãn Bản tin, chip nguồn, câu nhắc dán khoá 1 lần, nút mở API & Model', async () => {
    const user = userEvent.setup();
    useGenStore.setState({ conversationId: BID, conversationOwner: 'u1' });
    wrap(<GenPanel userId="u1" />);
    expect(await screen.findByText(/Đã tra việc, khách, nháp, sự cố…/)).toBeInTheDocument();
    const msg = document.querySelector('.gen-msg--briefing') as HTMLElement;
    expect(msg).not.toBeNull();
    expect(within(msg).getByText('Bản tin')).toBeInTheDocument();
    expect(screen.getAllByText('Dán khoá OpenRouter/Gemini để Gen tóm tắt')).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: 'Mở nơi dán khoá' }));
    expect(navigations).toContain('/api');
    expect(screen.getByRole('button', { name: 'Hữu ích' })).toBeInTheDocument();
  });
});

describe('AppShell ?gen=<id> (mở bản tin từ chuông)', () => {
  function renderShell(path: string) {
    const router = createMemoryRouter(
      [{ path: '/', element: <AppShell />, children: [{ path: 'overview', element: <div>Tổng quan</div> }] }],
      { initialEntries: [path] },
    );
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    return router;
  }

  it('UUID hợp lệ ⇒ mở khung Gen, tải hội thoại, gỡ tham số gen khỏi URL', async () => {
    const router = renderShell(`/overview?gen=${BID}&x=1`);
    expect(await screen.findByText(/Đã tra việc, khách, nháp, sự cố…/)).toBeInTheDocument();
    expect(screen.getByRole('complementary', { name: /Gen — trợ lý quản trị/ })).toBeInTheDocument();
    expect(useGenStore.getState().openByUser.u1).toBe(true);
    expect(useGenStore.getState().conversationId).toBe(BID);
    expect(router.state.location.pathname).toBe('/overview');
    expect(router.state.location.search).toBe('?x=1');
    expect(messageCalls(BID)).toHaveLength(1);
  });

  it('bản tin đã quá hạn lưu (404) ⇒ toast báo', async () => {
    handler = (url) => (url.endsWith(`/gen/conversations/${BID}/messages`) ? { status: 404, body: { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' } } : undefined);
    renderShell(`/overview?gen=${BID}`);
    await waitFor(() => expect(useToasts.getState().toasts.map((t) => t.text)).toContain('Không mở được bản tin — có thể đã quá hạn lưu'));
  });

  it('Gen đang trả lời ⇒ giữ ?gen, không đè câu trả lời; xong lượt mới mở bản tin', async () => {
    const running = [{ id: 'a-t5', role: 'assistant' as const, turnId: 't5', steps: [], status: 'running' as const }];
    useGenStore.setState({ conversationId: CID, conversationOwner: 'u1', busy: true, messages: running });
    const router = renderShell(`/overview?gen=${BID}`);
    await waitFor(() => expect(useToasts.getState().toasts.map((t) => t.text)).toContain('Gen đang trả lời — bản tin sẽ mở khi xong'));
    expect(router.state.location.search).toBe(`?gen=${BID}`);
    expect(messageCalls(BID)).toHaveLength(0);
    expect(useGenStore.getState().messages).toEqual(running);
    act(() => useGenStore.setState({ busy: false, messages: [{ ...running[0], status: 'done' }] }));
    await waitFor(() => expect(useGenStore.getState().conversationId).toBe(BID));
    expect(router.state.location.search).toBe('');
    expect(messageCalls(BID)).toHaveLength(1);
  });

  it('lỗi khác 404 ⇒ toast nêu lỗi + chi tiết kỹ thuật, không nói "quá hạn lưu"', async () => {
    handler = (url) => (url.endsWith(`/gen/conversations/${BID}/messages`) ? { status: 500, body: { status: 500, code: 'INTERNAL', title: 'Hệ thống gặp lỗi', error_id: 'E-7' } } : undefined);
    renderShell(`/overview?gen=${BID}`);
    await waitFor(() => expect(useToasts.getState().toasts.some((t) => t.text.startsWith('Không mở được bản tin — Hệ thống gặp lỗi'))).toBe(true));
    const t = useToasts.getState().toasts.map((x) => x.text).join('\n');
    expect(t).toContain('HTTP 500');
    expect(t).not.toContain('quá hạn lưu');
  });

  it('?gen=abc ⇒ bỏ qua (không mở khung, không gọi API)', async () => {
    const router = renderShell('/overview?gen=abc');
    expect(await screen.findByText('Tổng quan')).toBeInTheDocument();
    await waitFor(() => expect(router.state.location.search).toBe(''));
    expect(useGenStore.getState().openByUser.u1).toBeUndefined();
    expect(calls.some((c) => c.url.includes('/gen/conversations/'))).toBe(false);
    expect(screen.queryByRole('complementary', { name: /Gen — trợ lý quản trị/ })).toBeNull();
  });
});
