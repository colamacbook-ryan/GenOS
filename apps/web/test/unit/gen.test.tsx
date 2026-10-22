import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { DataToolName, GenStepEvent } from '@gen-harness/contracts';
import { GenPanel } from '../../src/gen/GenPanel';
import { GenToggle } from '../../src/gen/GenToggle';
import { Spotlight } from '../../src/gen/Spotlight';
import { currentScreenKey, executeUiAction, screenHref, visibleTargets, waitForTarget } from '../../src/gen/director';
import { useGenStore } from '../../src/gen/genStore';
import { applyEvent } from '../../src/lib/realtime';
import { setNavigator } from '../../src/lib/navigation';
import { qk } from '../../src/lib/queries';
import { queryClient } from '../../src/lib/queryClient';

const ME = {
  id: 'u1', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A', role: { code: 'owner', name: 'Owner — Sếp' },
  org: { id: 'o1', name: 'Genesis', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: {}, must_change_password: false,
  features: { gen: true },
};

type Call = { method: string; url: string; body: unknown };
const calls: Call[] = [];
let turnReply: unknown = null;
let hubLink: unknown = null;

function stubApi() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : null });
      const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
      if (url.endsWith('/gen/turns') && method === 'POST') return json(202, { turn_id: 't1', conversation_id: 'c1' });
      if (url.includes('/gen/turns/t1/ack')) return new Response(null, { status: 204 });
      if (url.endsWith('/gen/turns/t1')) return json(200, turnReply ?? { turn_id: 't1', conversation_id: 'c1', status: 'running', steps: [] });
      if (url.endsWith('/hub/link') && hubLink) return json(200, hubLink);
      return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
    }),
  );
}

const navigations: string[] = [];

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(qk.me, ME);
  calls.length = 0;
  navigations.length = 0;
  turnReply = null;
  hubLink = null;
  useGenStore.setState({ openByUser: {}, conversationId: null, messages: [], busy: false, spotlight: null });
  setNavigator((to) => navigations.push(to));
  stubApi();
  window.history.pushState({}, '', '/overview');
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  document.body.innerHTML = '';
});

function wrap(ui: React.ReactNode) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function ws(ev: GenStepEvent) {
  act(() => applyEvent(queryClient, { type: 'gen.step', data: ev }));
}

describe('Gen director helpers', () => {
  it('maps screens and collects visible targets', () => {
    expect(currentScreenKey('/system')).toBe('system');
    expect(currentScreenKey('/guide/5')).toBe('guide');
    expect(currentScreenKey('/khong-co')).toBeNull();
    expect(screenHref('system', { tab: 'brain' })).toBe('/system?tab=brain');
    const host = document.createElement('div');
    host.innerHTML = '<div data-gen-target="overview.kpis"></div><div data-gen-target="overview.kpis"></div><span data-gen-target="overview.queue"></span>';
    document.body.appendChild(host);
    expect(visibleTargets()).toEqual(['overview.kpis', 'overview.queue']);
  });

  it('waitForTarget resolves when the element appears and null on timeout', async () => {
    const later = waitForTarget('overview.health', 1000);
    const el = document.createElement('div');
    el.setAttribute('data-gen-target', 'overview.health');
    setTimeout(() => document.body.appendChild(el), 10);
    await expect(later).resolves.toBe(el);
    await expect(waitForTarget('overview.nope', 30)).resolves.toBeNull();
  });
});

describe('GenPanel', () => {
  it('toggle remembers the open state per user', async () => {
    wrap(<GenToggle />);
    await userEvent.click(screen.getByRole('button', { name: 'Hỏi Gen — trợ lý quản trị' }));
    expect(useGenStore.getState().openByUser).toEqual({ u1: true });
    expect(JSON.parse(window.localStorage.getItem('gh-gen') ?? '{}').state.openByUser).toEqual({ u1: true });
    await userEvent.click(screen.getByRole('button', { name: 'Đóng Gen' }));
    expect(useGenStore.getState().openByUser.u1).toBe(false);
  });

  const seedToolMessage = (name: DataToolName) =>
    useGenStore.setState({
      messages: [{ id: 'a1', role: 'assistant', status: 'done', steps: [{ kind: 'tool', name, args: {} }] }],
    });

  it('chip "Đã tra …" lấy tên Kho thật từ máy chủ ngay cả khi cache thẻ Gen-hub còn trống (mở lại hội thoại, không có câu chào)', async () => {
    hubLink = { configured: true, enabled: true, kho_label: 'Kho Heo' };
    queryClient.setQueryData(qk.me, { ...ME, permissions: { 'system.read': 'all' } });
    seedToolMessage('hub.kho_search');
    wrap(<GenPanel userId="u1" />);
    await waitFor(() => expect(screen.getByText(/Đã tra Kho Heo \(Gen-hub\)/)).toBeInTheDocument());
    expect(calls.filter((c) => c.url.endsWith('/hub/link'))).toHaveLength(1);
  });

  it('chip tool không nhắc tên Kho (vd "Hôm nay") không gọi /hub/link; người không có system.read cũng không gọi', async () => {
    hubLink = { configured: true, enabled: true, kho_label: 'Kho Heo' };
    queryClient.setQueryData(qk.me, { ...ME, permissions: { 'system.read': 'all' } });
    seedToolMessage('overview.summary');
    wrap(<GenPanel userId="u1" />);
    expect(await screen.findByText(/Đã tra Hôm nay/)).toBeInTheDocument();
    cleanup();
    queryClient.setQueryData(qk.me, { ...ME, permissions: {} });
    seedToolMessage('hub.kho_search');
    wrap(<GenPanel userId="u1" />);
    expect(await screen.findByText(/Đã tra Kho dữ liệu \(Gen-hub\)/)).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith('/hub/link'))).toBe(false);
  });

  it('hides the toggle when Gen is off for this user', () => {
    queryClient.setQueryData(qk.me, { ...ME, features: { gen: false } });
    wrap(<GenToggle />);
    expect(screen.queryByRole('button', { name: /Gen/ })).toBeNull();
  });

  it('sends a question with screen context and renders streamed steps', async () => {
    const host = document.createElement('div');
    host.innerHTML = '<div data-gen-target="overview.kpis"></div>';
    document.body.appendChild(host);
    wrap(<GenPanel userId="u1" />);
    expect(screen.getByText(/Chào Sếp, em là Gen/)).toBeInTheDocument();
    // v0.1.43 (F-30): ví dụ là câu hỏi việc thật, không còn gợi ý "khoá Jev".
    expect(screen.getByRole('button', { name: 'Khách nào hỏi giá hôm nay?' })).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/khoá Jev/i);
    await userEvent.type(screen.getByLabelText('Câu hỏi cho Gen'), 'Hôm nay có gì gấp?{Enter}');
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/gen/turns'))).toBe(true));
    const post = calls.find((c) => c.method === 'POST')!;
    expect(post.body).toMatchObject({ text: 'Hôm nay có gì gấp?', conversation_id: null, context: { screen_key: 'overview', visible_targets: ['overview.kpis'] } });
    expect(screen.getByText('Hôm nay có gì gấp?')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/Gen đang nghĩ/)).toBeInTheDocument());
    ws({ turn_id: 't1', conversation_id: 'c1', seq: 0, step: { kind: 'tool', name: 'overview.summary', args: {} } });
    ws({ turn_id: 't1', conversation_id: 'c1', seq: 1, step: { kind: 'say', text: 'Có 3 việc cần Sếp xem.' } });
    ws({ turn_id: 't1', conversation_id: 'c1', seq: 1, step: { kind: 'say', text: 'trùng seq — bỏ qua' } });
    ws({ turn_id: 't1', conversation_id: 'c1', seq: 2, step: { kind: 'suggest', items: [{ label: 'Mở hộp thư', action: { type: 'navigate', screen: 'inbox' } }] } });
    expect(screen.getByText('Có 3 việc cần Sếp xem.')).toBeInTheDocument();
    expect(screen.queryByText('trùng seq — bỏ qua')).toBeNull();
    expect(screen.getByText(/Đã tra Hôm nay/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Mở hộp thư' }));
    expect(navigations).toContain('/inbox');
    turnReply = { turn_id: 't1', conversation_id: 'c1', status: 'done', steps: [] };
    act(() => applyEvent(queryClient, { type: 'gen.done', data: { turn_id: 't1', conversation_id: 'c1', status: 'done' } }));
    await waitFor(() => expect(screen.queryByText(/Gen đang nghĩ/)).toBeNull());
    expect(useGenStore.getState().conversationId).toBe('c1');
    expect(useGenStore.getState().busy).toBe(false);
  });
});

describe('Spotlight', () => {
  function page(targets: string[]) {
    for (const t of targets) {
      const b = document.createElement('button');
      b.setAttribute('data-gen-target', t);
      b.textContent = t;
      document.body.appendChild(b);
    }
  }

  it('highlights a target with a glow ring and message; Esc closes', async () => {
    page(['overview.kpis']);
    wrap(<Spotlight />);
    await act(() => executeUiAction({ type: 'highlight', target: 'overview.kpis', message: 'Các chỉ số chính ở đây' }));
    const dlg = await screen.findByRole('dialog', { name: 'Gen đang chỉ' });
    expect(dlg).toHaveTextContent('Các chỉ số chính ở đây');
    expect(document.querySelector('.gen-spot__ring')).not.toBeNull();
    expect(navigations).toEqual([]); // đã ở đúng màn
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('dialog', { name: 'Gen đang chỉ' })).toBeNull();
  });

  it('v0.1.43: target inside closed <details> (lồng nhiều cấp) → mở các khối gập rồi mới đo', async () => {
    const outer = document.createElement('details');
    const inner = document.createElement('details');
    const b = document.createElement('button');
    b.setAttribute('data-gen-target', 'system.brain.jev');
    inner.appendChild(b);
    outer.appendChild(inner);
    document.body.appendChild(outer);
    expect(outer.open || inner.open).toBe(false);
    wrap(<Spotlight />);
    await act(() => executeUiAction({ type: 'highlight', target: 'system.brain.jev', message: 'Thẻ Jev' }));
    expect(await screen.findByRole('dialog', { name: 'Gen đang chỉ' })).toHaveTextContent('Thẻ Jev');
    expect(outer.open).toBe(true);
    expect(inner.open).toBe(true);
    outer.remove();
  });

  it('navigates to the screen/tab a target needs, then says when it is missing', async () => {
    wrap(<Spotlight />);
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const p = executeUiAction({ type: 'highlight', target: 'system.brain.jev', message: 'Thẻ Jev' });
    expect(navigations).toEqual(['/system?tab=brain']);
    await act(async () => {
      vi.advanceTimersByTime(4100);
      await p;
    });
    expect(useGenStore.getState().spotlight?.missing).toBe(true);
    vi.useRealTimers();
    expect(await screen.findByText(/Em không thấy phần này/)).toBeInTheDocument();
  });

  it('runs a tour step by step with Next/Back and acks each step', async () => {
    window.history.pushState({}, '', '/system?tab=brain');
    page(['system.tab.brain', 'system.brain.jev', 'system.brain.jev.test']);
    wrap(<Spotlight />);
    await act(() =>
      executeUiAction(
        {
          type: 'tour',
          steps: [
            { screen: 'system', target: 'system.tab.brain', message: 'Mở tab Bộ não AI' },
            { target: 'system.brain.jev', message: 'Thẻ Jev' },
            { target: 'system.brain.jev.test', message: 'Bấm Kiểm tra' },
          ],
        },
        't1',
      ),
    );
    expect(await screen.findByText('Bước 1/3')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Quay lại' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Tiếp' }));
    expect(await screen.findByText('Bước 2/3')).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toHaveTextContent('Thẻ Jev');
    await userEvent.click(screen.getByRole('button', { name: 'Quay lại' }));
    expect(await screen.findByText('Bước 1/3')).toBeInTheDocument();
    // Bấm thẳng vào phần tử được chỉ cũng sang bước kế.
    await userEvent.click(screen.getByText('system.tab.brain'));
    expect(await screen.findByText('Bước 2/3')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Tiếp' }));
    expect(await screen.findByText('Bước 3/3')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Xong' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    const acks = calls.filter((c) => c.url.includes('/gen/turns/t1/ack')).map((c) => c.body);
    expect(acks).toEqual([
      { step: 0, outcome: 'done' },
      { step: 0, outcome: 'done' },
      { step: 1, outcome: 'done' },
      { step: 2, outcome: 'done' },
    ]);
    expect(navigations).toEqual([]);
  });
});
