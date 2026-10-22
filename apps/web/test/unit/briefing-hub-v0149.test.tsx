/**
 * v0.1.49 (F-8, QD-16) — Bản tin Gen có thêm 3 mục đọc từ Gen-hub (chỉ đọc): Lịch hôm nay, Mail cần trả lời, Việc Google đang mở.
 * Mục ẩn (chưa nối / thiếu quyền) không có trong `sections`; lỗi = câu thân thiện + "Chi tiết kỹ thuật"; không render object.
 */
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { GenBriefingSection, GenMessage } from '@gen-harness/contracts';
import { BriefingHubSections } from '../../src/gen/BriefingHubSections';
import { GenPanel } from '../../src/gen/GenPanel';
import { stopAll } from '../../src/gen/genClient';
import { useGenStore } from '../../src/gen/genStore';
import { setNavigator } from '../../src/lib/navigation';
import { qk } from '../../src/lib/queries';
import { queryClient } from '../../src/lib/queryClient';
import { briefingContent, hubSections } from '../mock-gen';

const OK_SECTIONS = hubSections('ok').sections;

const sec = (over: Partial<GenBriefingSection> & { key: string }): GenBriefingSection => ({
  title: 'Mục', count: 0, lines: [], link: '/connections', external: true, state: 'ok', ...over,
});

function noObjectText(root: HTMLElement = document.body) {
  expect(root.textContent).not.toContain('[object Object]');
}

describe('BriefingHubSections — vẽ mục Gen-hub', () => {
  it('3 mục ok: đúng tiêu đề, số đếm và từng dòng', () => {
    render(<BriefingHubSections sections={OK_SECTIONS} />);
    expect(screen.getByRole('heading', { name: 'Lịch hôm nay (2)' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Mail cần trả lời (3)' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Việc Google đang mở (1)' })).toBeInTheDocument();
    expect(screen.getByText('Lịch hôm nay')).toBeInTheDocument();
    expect(screen.getByText('Mail cần trả lời')).toBeInTheDocument();
    expect(screen.getByText('Việc Google đang mở')).toBeInTheDocument();
    expect(screen.getByText('09:00 · Họp với nhà cung cấp ván MDF')).toBeInTheDocument();
    expect(screen.getByText('Công ty Hải Long — Xác nhận lịch giao hàng')).toBeInTheDocument();
    expect(screen.getByText('Gọi nhà cung cấp keo dán')).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(6);
    expect(screen.queryByText('Chi tiết kỹ thuật')).toBeNull();
    noObjectText();
  });

  it('chạm trần (more: true) ⇒ số đếm "10+" (không phải 10)', () => {
    render(<BriefingHubSections sections={[sec({ key: 'mail_reply', title: 'Mail cần trả lời', count: 10, more: true, lines: ['A — B'] })]} />);
    expect(screen.getByRole('heading', { name: 'Mail cần trả lời (10+)' })).toBeInTheDocument();
  });

  it('empty: câu thân thiện theo khoá mục', () => {
    render(
      <BriefingHubSections
        sections={[
          sec({ key: 'calendar_today', title: 'Lịch hôm nay', state: 'empty' }),
          sec({ key: 'mail_reply', title: 'Mail cần trả lời', state: 'empty' }),
          sec({ key: 'gtasks_open', title: 'Việc Google đang mở', state: 'empty' }),
        ]}
      />,
    );
    expect(screen.getByText('Hôm nay Sếp không có lịch.')).toBeInTheDocument();
    expect(screen.getByText('Không có mail cần trả lời.')).toBeInTheDocument();
    expect(screen.getByText('Không có việc Google đang mở.')).toBeInTheDocument();
    expect(screen.queryAllByRole('listitem')).toHaveLength(0);
  });

  it('ok nhưng không có dòng nào ⇒ vẫn hiện câu "không có", không để trống', () => {
    render(<BriefingHubSections sections={[sec({ key: 'mail_reply', title: 'Mail cần trả lời', state: 'ok', count: 0, lines: [] })]} />);
    expect(screen.getByText('Không có mail cần trả lời.')).toBeInTheDocument();
  });

  it('error: câu thân thiện + <details> "Chi tiết kỹ thuật" chứa chuỗi detail', async () => {
    render(<BriefingHubSections sections={[sec({ key: 'calendar_today', title: 'Lịch hôm nay', state: 'error', detail: 'HUB_UNAVAILABLE: 502 calendar_list_events' })]} />);
    expect(screen.getByText(/Chưa đọc được mục này lần này/)).toBeInTheDocument();
    const summary = screen.getByText('Chi tiết kỹ thuật');
    const details = summary.closest('details');
    expect(details).not.toBeNull();
    expect(details).toHaveClass('tech-detail');
    expect(within(details as HTMLElement).getByText('HUB_UNAVAILABLE: 502 calendar_list_events').tagName).toBe('CODE');
    await userEvent.click(summary);
    expect((details as HTMLDetailsElement).open).toBe(true);
  });

  it('breaker: "Gen-hub tạm không trả lời — bản tin sau Gen thử lại." + Chi tiết kỹ thuật', () => {
    render(<BriefingHubSections sections={[sec({ key: 'mail_reply', title: 'Mail cần trả lời', state: 'breaker', detail: 'HUB_BREAKER_OPEN: 3 lỗi liên tiếp' })]} />);
    expect(screen.getByText(/Gen-hub tạm không trả lời — bản tin sau Gen thử lại\./)).toBeInTheDocument();
    expect(screen.getByText('Chi tiết kỹ thuật').closest('details')).not.toBeNull();
    expect(screen.getByText('HUB_BREAKER_OPEN: 3 lỗi liên tiếp')).toBeInTheDocument();
  });

  it('error/breaker không có detail (hoặc detail không phải chuỗi) ⇒ không có <details>, không vỡ, không "[object Object]"', () => {
    const bad = { key: 'calendar_today', title: 'Lịch hôm nay', count: 0, lines: [], link: '/x', external: true, state: 'error', detail: { code: 'X', nested: { a: 1 } } } as unknown as GenBriefingSection;
    const bad2 = { ...sec({ key: 'mail_reply', title: 'Mail cần trả lời', state: 'breaker' }), detail: ['a', 'b'] } as unknown as GenBriefingSection;
    const none = sec({ key: 'gtasks_open', title: 'Việc Google đang mở', state: 'error', detail: null });
    const { container } = render(<BriefingHubSections sections={[bad, bad2, none]} />);
    expect(screen.getAllByText(/Chưa đọc được mục này lần này|Gen-hub tạm không trả lời/)).toHaveLength(3);
    expect(container.querySelector('details')).toBeNull();
    noObjectText(container);
  });

  it('lines có phần tử không phải chuỗi ⇒ bỏ phần tử đó, không in "[object Object]"', () => {
    const weird = sec({
      key: 'mail_reply', title: 'Mail cần trả lời', count: 4,
      lines: ['Anh Bảo — Báo giá', { subject: 'x' }, null, 42, ['a'], 'Chị Mai — Hỏi bảo hành', ''] as unknown as string[],
    });
    const { container } = render(<BriefingHubSections sections={[weird]} />);
    expect(screen.getAllByRole('listitem').map((li) => li.textContent)).toEqual(['Anh Bảo — Báo giá', 'Chị Mai — Hỏi bảo hành']);
    noObjectText(container);
  });

  it('title/count/state lạ ⇒ không vỡ (rơi về khoá mục, đếm theo số dòng, coi như ok)', () => {
    const odd = { key: 'gtasks_open', title: { x: 1 }, count: 'nhiều', lines: ['Một việc'], link: '/c', external: true, state: 'khac' } as unknown as GenBriefingSection;
    const { container } = render(<BriefingHubSections sections={[odd]} />);
    expect(screen.getByText('Một việc')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'gtasks_open (1)' })).toBeInTheDocument();
    noObjectText(container);
  });

  it('mục KHÔNG external không vẽ trong khối này; không có mục external ⇒ không vẽ gì', () => {
    const internal = sec({ key: 'tasks_due', title: 'Việc tới hạn hôm nay', count: 2, lines: ['TSK-0998 · Gọi lại anh Bảo (P1)'], external: undefined });
    const noFlag = sec({ key: 'hot_customers', title: 'Khách đang nóng', count: 1, lines: ['Anh Bảo'], external: false });
    const { container, rerender } = render(<BriefingHubSections sections={[internal, noFlag]} />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText('Việc tới hạn hôm nay')).toBeNull();
    rerender(<BriefingHubSections sections={[internal, ...OK_SECTIONS.slice(0, 1), noFlag]} />);
    expect(screen.getByText('Lịch hôm nay')).toBeInTheDocument();
    expect(screen.queryByText('Việc tới hạn hôm nay')).toBeNull();
    expect(screen.queryByText('Khách đang nóng')).toBeNull();
    rerender(<BriefingHubSections sections={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('dữ liệu hỏng (không phải mảng / phần tử null hoặc không phải object) ⇒ không vỡ, không vẽ gì', () => {
    for (const bad of [undefined, null, 'x', 7, {}] as unknown as GenBriefingSection[][]) {
      const { container, unmount } = render(<BriefingHubSections sections={bad} />);
      expect(container).toBeEmptyDOMElement();
      unmount();
    }
    const { container } = render(<BriefingHubSections sections={[null, 3, 'x', sec({ key: 'calendar_today', title: 'Lịch hôm nay', state: 'empty' })] as unknown as GenBriefingSection[]} />);
    expect(screen.getByText('Lịch hôm nay')).toBeInTheDocument();
    expect(screen.getByText('Hôm nay Sếp không có lịch.')).toBeInTheDocument();
    noObjectText(container);
  });
});

// ── Tích hợp: khung Gen mở bản tin từ máy chủ ─────────────────────────────────────────────────────────────────

const ME = {
  id: 'u1', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A', role: { code: 'owner', name: 'Owner — Sếp' },
  org: { id: 'o1', name: 'Genesis', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: {}, must_change_password: false,
  features: { gen: true },
};
const BID = '0b8e7d6c-5a4b-4c3d-9e2f-1a0b9c8d7e6f';

let served: GenMessage[] = [];
const navigations: string[] = [];

function briefingMsg(content: GenMessage['content']): GenMessage[] {
  return [{ id: 'b1', role: 'assistant', turn_id: 'tb1', content, created_at: '2026-10-09T00:30:00Z', feedback: null }];
}

function wrap(ui: ReactNode) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(qk.me, ME);
  navigations.length = 0;
  window.localStorage.clear();
  useGenStore.setState({ openByUser: {}, conversationId: BID, conversationOwner: 'u1', messages: [], busy: false, restoring: false, loadingConversation: false, spotlight: null });
  setNavigator((to) => navigations.push(to));
  vi.stubGlobal('WebSocket', undefined);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/auth/me')) return new Response(JSON.stringify(ME), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.endsWith(`/gen/conversations/${BID}/messages`)) return new Response(JSON.stringify(served), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response(JSON.stringify({ status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' }), { status: 404, headers: { 'Content-Type': 'application/problem+json' } });
    }),
  );
});
afterEach(() => {
  stopAll();
  cleanup();
  vi.unstubAllGlobals();
});

describe('Khung Gen — Bản tin có mục Gen-hub', () => {
  it('hub=ok: 3 mục Gen-hub hiện sau các bước; mục nội bộ vẫn chỉ ở bước say (không lặp)', async () => {
    served = briefingMsg(briefingContent('sáng 09/10', '2026-10-09T07:30:00+07:00', false, 'ok'));
    wrap(<GenPanel userId="u1" />);
    const box = await screen.findByTestId('briefing-hub-sections');
    expect(within(box).getByText('Lịch hôm nay')).toBeInTheDocument();
    expect(within(box).getByText('Mail cần trả lời')).toBeInTheDocument();
    expect(within(box).getByText('Việc Google đang mở')).toBeInTheDocument();
    expect(within(box).getByText('Chị Mai — Hỏi bảo hành')).toBeInTheDocument();
    // Mục nội bộ chỉ có ở bước say, không bị vẽ lại trong khối Gen-hub.
    expect(within(box).queryByText(/Việc tới hạn hôm nay/)).toBeNull();
    expect(screen.getAllByText(/Việc tới hạn hôm nay \(2\)/)).toHaveLength(1);
    noObjectText();
  });

  it('hub=missing: không có mục mail; có dòng "tick thêm quyền" và nút "Mở thẻ Gen-hub" làm sáng thẻ Gen-hub ở Kết nối', async () => {
    served = briefingMsg(briefingContent('sáng 09/10', '2026-10-09T07:30:00+07:00', false, 'missing'));
    wrap(<GenPanel userId="u1" />);
    expect(await screen.findByText(/tick thêm quyền/)).toBeInTheDocument();
    expect(screen.queryByText('Mail cần trả lời')).toBeNull();
    const box = screen.getByTestId('briefing-hub-sections');
    expect(within(box).getByText('Lịch hôm nay')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Mở thẻ Gen-hub' }));
    expect(navigations.some((p) => p.startsWith('/connections'))).toBe(true);
    expect(useGenStore.getState().spotlight).toMatchObject({ target: 'mcp.hub_link' });
  });

  it('thẻ Gen-hub nằm ngay sau các mục nội bộ — TRƯỚC lời nhắc "tick thêm quyền" / "Dán khoá…" và các nút', async () => {
    served = briefingMsg(briefingContent('sáng 09/10', '2026-10-09T07:30:00+07:00', true, 'missing'));
    wrap(<GenPanel userId="u1" />);
    const box = await screen.findByTestId('briefing-hub-sections');
    const before = (a: Element, b: Element) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    expect(before(screen.getByText(/Việc tới hạn hôm nay \(2\)/), box)).toBe(true);
    expect(before(box, screen.getByText(/tick thêm quyền/))).toBe(true);
    expect(before(box, screen.getByRole('button', { name: 'Mở thẻ Gen-hub' }))).toBe(true);
    expect(before(box, screen.getByText('Dán khoá OpenRouter/Gemini để Gen tóm tắt'))).toBe(true);
    expect(before(box, screen.getByRole('button', { name: 'Mở nơi dán khoá' }))).toBe(true);
  });

  it('hub_at từ máy chủ được tôn trọng; bản tin cũ không có hub_at ⇒ chèn trước cặp lời nhắc + nút đầu tiên', async () => {
    const content = briefingContent('sáng 09/10', '2026-10-09T07:30:00+07:00', true, 'ok');
    // Máy chủ đặt thẻ sau bước tóm tắt (chỉ số 2): thẻ phải đứng TRƯỚC "Việc tới hạn hôm nay".
    served = briefingMsg({ ...content, hub_at: 2 });
    const first = wrap(<GenPanel userId="u1" />);
    let box = await screen.findByTestId('briefing-hub-sections');
    expect(!!(box.compareDocumentPosition(screen.getByText(/Việc tới hạn hôm nay \(2\)/)) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);
    first.unmount();
    stopAll();
    useGenStore.setState({ messages: [], conversationId: BID, conversationOwner: 'u1' });
    const { hub_at: _drop, ...old } = content;
    void _drop;
    served = briefingMsg(old);
    wrap(<GenPanel userId="u1" />);
    box = await screen.findByTestId('briefing-hub-sections');
    const hint = screen.getByText('Dán khoá OpenRouter/Gemini để Gen tóm tắt');
    expect(!!(box.compareDocumentPosition(hint) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);
  });

  it('hub=off (bản tin cũ, không có mục external): không có khối Gen-hub', async () => {
    served = briefingMsg(briefingContent('sáng 09/10', '2026-10-09T07:30:00+07:00', true));
    wrap(<GenPanel userId="u1" />);
    expect(await screen.findByText(/Việc tới hạn hôm nay \(2\)/)).toBeInTheDocument();
    expect(screen.queryByTestId('briefing-hub-sections')).toBeNull();
  });

  it('hub=breaker / error: câu thân thiện + Chi tiết kỹ thuật; sections hỏng từ máy chủ không làm vỡ khung', async () => {
    const content = briefingContent('sáng 09/10', '2026-10-09T07:30:00+07:00', false, 'breaker');
    // Máy chủ gửi lẫn phần tử hỏng — genClient bỏ phần tử không phải object.
    content.sections = [null, 7, 'x', ...(content.sections ?? [])] as unknown as GenBriefingSection[];
    served = briefingMsg(content);
    wrap(<GenPanel userId="u1" />);
    const box = await screen.findByTestId('briefing-hub-sections');
    expect(within(box).getAllByText(/Gen-hub tạm không trả lời — bản tin sau Gen thử lại\./)).toHaveLength(3);
    expect(within(box).getAllByText('Chi tiết kỹ thuật')).toHaveLength(3);
    expect(within(box).getAllByText(/HUB_BREAKER_OPEN/)).toHaveLength(3);
    noObjectText();
  });

  it('tin KHÔNG phải bản tin không bao giờ vẽ khối Gen-hub (dù content có sections)', async () => {
    served = [
      { id: 'm1', role: 'assistant', turn_id: 't9', content: { steps: [{ kind: 'say', text: 'Dạ, em là Gen.' }], sections: OK_SECTIONS }, created_at: '2026-10-09T01:00:00Z', feedback: null },
    ];
    wrap(<GenPanel userId="u1" />);
    expect(await screen.findByText('Dạ, em là Gen.')).toBeInTheDocument();
    expect(screen.queryByTestId('briefing-hub-sections')).toBeNull();
  });
});
