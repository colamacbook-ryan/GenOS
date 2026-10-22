/**
 * v0.1.50 (F-81, QD-18) — "Gen nhớ":
 * - thẻ đề xuất "Ghi nhớ" (memory_note) trong khung Gen: hàng Ghi nhớ + Lý do, KHÔNG có nhãn "Cần mã PIN", Sửa = 2 ô có bộ đếm
 *   (0/280, 0/200), "Xác nhận ghi nhớ" gọi confirm, xong → "Đã ghi nhớ" + "Xem ở Cài đặt" (→ /system?tab=brain#gen-memory), lỗi theo mã
 *   (GEN_MEMORY_FULL / GEN_MEMORY_DUPLICATE / GEN_PROPOSAL_DECIDED) bằng câu thân thiện + "Chi tiết kỹ thuật";
 * - thẻ GenMemoryCard (Cài đặt › Bộ não AI): chỉ Owner (vai trò khác không vẽ, không gọi /gen/memory), danh sách, Sửa tại chỗ (PATCH),
 *   Xoá có hộp xác nhận (DELETE), trạng thái rỗng, lỗi thân thiện;
 * - hợp đồng: GEN_TARGETS có 'system.brain.memory'; GEN_PROPOSAL_EDITABLE; client api.gen.memory.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { GEN_PROPOSAL_EDITABLE, GEN_TARGET_BY_ID, type GenMemoryNote, type GenProposal, type GenStepEvent } from '@gen-harness/contracts';
import { GenPanel } from '../../src/gen/GenPanel';
import { changedFields } from '../../src/gen/proposalModel';
import { useGenStore } from '../../src/gen/genStore';
import { charCount, countText, fmtNoteDate, memoryErrorText, memoryItems, memoryLimits, sourceLabel } from '../../src/gen/genMemoryModel';
import { applyEvent } from '../../src/lib/realtime';
import { setNavigator } from '../../src/lib/navigation';
import { qk } from '../../src/lib/queries';
import { queryClient } from '../../src/lib/queryClient';
import { ApiError } from '../../src/lib/api';
import { GenMemoryCard } from '../../src/screens/system/GenMemoryCard';

const ME = (role: string) => ({
  id: 'u1', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A', role: { code: role, name: role },
  org: { id: 'o1', name: 'Genesis', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: { 'system.read': 'all', 'system.manage': role === 'owner' ? 'all' : 'none' },
  features: { gen: role === 'owner' },
});

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

// ── Thẻ đề xuất "Ghi nhớ" ─────────────────────────────────────────────────────────────────────────────────

const MEMORY: GenProposal = {
  id: 'm1',
  type: 'memory_note',
  fields: { text: 'Báo giá luôn ghi rõ VAT 8% và thời hạn hiệu lực 7 ngày.', reason: 'Sếp dặn khi soạn báo giá ván MDF.' },
  summary: 'Ghi nhớ quy ước: báo giá luôn ghi rõ VAT 8% và thời hạn hiệu lực 7 ngày.',
  labels: { count: '3/30' },
  target: 'gen.memory',
  requires_pin: false,
  status: 'pending',
};

type Call = { method: string; url: string; body: unknown };
const calls: Call[] = [];
let confirmReply: { status: number; body: unknown } | null = null;
const navigations: string[] = [];

function stubGenApi() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ method, url, body });
      const m = url.match(/\/gen\/proposals\/(\w+)\/(confirm|cancel)$/);
      if (m && method === 'POST') {
        if (confirmReply) return json(confirmReply.status, confirmReply.body);
        if (m[2] === 'cancel') return json(200, { ...MEMORY, status: 'cancelled' });
        return json(200, {
          ...MEMORY, fields: { ...MEMORY.fields, ...(body?.fields ?? {}) }, status: 'confirmed',
          result: { type: 'memory_note', id: 'n1', code: null, screen: 'system' },
        });
      }
      return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
    }),
  );
}

function showProposal(p: GenProposal) {
  useGenStore.setState({
    messages: [
      { id: 'u-1', role: 'user', text: 'nhớ giúp em', steps: [] },
      { id: 'a-t1', role: 'assistant', turnId: 't1', steps: [{ kind: 'say', text: 'Em đề xuất:' }], status: 'running' },
    ],
  });
  const ev: GenStepEvent = { turn_id: 't1', conversation_id: 'c1', seq: 1, step: { kind: 'proposal', proposal: p } };
  act(() => applyEvent(queryClient, { type: 'gen.step', data: ev }));
}

function renderPanel() {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <GenPanel userId="u1" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const writes = () => calls.filter((c) => c.method !== 'GET');
const cardOf = (title: string) => screen.getByRole('group', { name: `Đề xuất: ${title}` });

describe('Thẻ đề xuất "Ghi nhớ" (memory_note)', () => {
  beforeEach(() => {
    queryClient.clear();
    queryClient.setQueryData(qk.me, ME('owner'));
    calls.length = 0;
    navigations.length = 0;
    confirmReply = null;
    useGenStore.setState({ openByUser: {}, conversationId: 'c1', messages: [], busy: false, spotlight: null });
    setNavigator((to) => navigations.push(to));
    stubGenApi();
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('hiện hàng "Ghi nhớ" (nội dung) + "Lý do", biểu tượng não, KHÔNG có nhãn "Cần mã PIN"; chưa ghi gì', () => {
    const { container } = renderPanel();
    showProposal(MEMORY);
    const card = cardOf('Ghi nhớ');
    expect(card).toHaveTextContent('Đề xuất · Ghi nhớ');
    expect(card).toHaveTextContent('Báo giá luôn ghi rõ VAT 8% và thời hạn hiệu lực 7 ngày.');
    expect(card).toHaveTextContent('Lý do');
    expect(card).toHaveTextContent('Sếp dặn khi soạn báo giá ván MDF.');
    expect(card).toHaveTextContent('3/30');
    expect(card).not.toHaveTextContent('Cần mã PIN');
    expect(card).not.toHaveTextContent('Ghi thẳng vào Kho dữ liệu');
    expect(card.querySelector('[data-icon="brain"]')).not.toBeNull();
    expect(within(card).getByRole('button', { name: 'Xác nhận ghi nhớ' })).toBeEnabled();
    expect(writes()).toEqual([]);
    expect(container.textContent).not.toContain('[object Object]');
  });

  it('lý do rỗng ⇒ không vẽ hàng "Lý do"', () => {
    renderPanel();
    showProposal({ ...MEMORY, fields: { text: 'Gọi khách bằng "anh/chị"', reason: '' } } as GenProposal);
    const card = cardOf('Ghi nhớ');
    expect(card).not.toHaveTextContent('Lý do');
    expect(card).toHaveTextContent('Gọi khách bằng "anh/chị"');
  });

  it('Xác nhận ghi nhớ gọi đúng confirm (không PIN) → "Đã ghi nhớ" + "Xem ở Cài đặt" dẫn tới thẻ Gen nhớ', async () => {
    renderPanel();
    showProposal(MEMORY);
    const card = cardOf('Ghi nhớ');
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận ghi nhớ' }));
    await waitFor(() => expect(card).toHaveTextContent('Đã ghi nhớ'));
    expect(writes()).toHaveLength(1);
    expect(writes()[0].url).toMatch(/\/gen\/proposals\/m1\/confirm$/);
    expect(writes()[0].body).toEqual({ fields: {} });
    expect(within(card).queryByRole('button', { name: 'Xác nhận ghi nhớ' })).toBeNull();
    expect(card).not.toHaveTextContent('Đã xác nhận');
    await userEvent.click(within(card).getByRole('button', { name: 'Xem ở Cài đặt' }));
    expect(navigations).toEqual(['/system?tab=brain#gen-memory']);
  });

  it('Sửa: hai ô nhiều dòng có bộ đếm 0/280 và 0/200 (điền sẵn), giới hạn độ dài; chỉ gửi trường đã đổi', async () => {
    expect(GEN_PROPOSAL_EDITABLE.memory_note).toEqual(['text', 'reason']);
    expect(changedFields(MEMORY, { text: 'Mới', reason: MEMORY.type === 'memory_note' ? MEMORY.fields.reason : '' })).toEqual({ text: 'Mới' });
    expect(changedFields(MEMORY, { text: 'Mới', reason: '' })).toEqual({ text: 'Mới', reason: '' });

    renderPanel();
    showProposal({ ...MEMORY, fields: { text: '', reason: '' } } as GenProposal);
    const card = cardOf('Ghi nhớ');
    await userEvent.click(within(card).getByRole('button', { name: 'Sửa' }));
    const text = within(card).getByLabelText('Ghi nhớ');
    const reason = within(card).getByLabelText('Lý do (không bắt buộc)');
    expect(text.tagName).toBe('TEXTAREA');
    expect(reason.tagName).toBe('TEXTAREA');
    expect(text).toHaveAttribute('maxlength', '280');
    expect(reason).toHaveAttribute('maxlength', '200');
    expect(within(card).getByTestId('gen-mem-count-text')).toHaveTextContent('0/280');
    expect(within(card).getByTestId('gen-mem-count-reason')).toHaveTextContent('0/200');
    expect(within(card).getByRole('button', { name: 'Xác nhận ghi nhớ' })).toBeDisabled(); // nội dung trống
    await userEvent.type(text, 'Xưng em, gọi Sếp');
    await userEvent.type(reason, 'Sếp thích vậy');
    expect(within(card).getByTestId('gen-mem-count-text')).toHaveTextContent('16/280');
    expect(within(card).getByTestId('gen-mem-count-reason')).toHaveTextContent('13/200');
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận ghi nhớ' }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0].body).toEqual({ fields: { text: 'Xưng em, gọi Sếp', reason: 'Sếp thích vậy' } });
  });

  it('Lý do KHÔNG bắt buộc khi Sếp xác nhận (cùng quy tắc ô "Lý do (không bắt buộc)" ở Cài đặt › Gen nhớ): xoá trắng vẫn bấm được, gửi reason rỗng', async () => {
    renderPanel();
    showProposal(MEMORY);
    const card = cardOf('Ghi nhớ');
    await userEvent.click(within(card).getByRole('button', { name: 'Sửa' }));
    const reason = within(card).getByLabelText('Lý do (không bắt buộc)');
    expect(within(card).queryByText('Bắt buộc')).toBeNull();
    await userEvent.clear(reason);
    const confirmBtn = within(card).getByRole('button', { name: 'Xác nhận ghi nhớ' });
    expect(confirmBtn).toBeEnabled();
    await userEvent.click(confirmBtn);
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0].body).toEqual({ fields: { reason: '' } });
    expect(card).not.toHaveTextContent('không được trống');
  });

  it('Huỷ → "Đã huỷ — không ghi nhớ", không gọi confirm', async () => {
    renderPanel();
    showProposal(MEMORY);
    const card = cardOf('Ghi nhớ');
    await userEvent.click(within(card).getByRole('button', { name: 'Huỷ' }));
    await waitFor(() => expect(card).toHaveTextContent('Đã huỷ — không ghi nhớ'));
    expect(writes().map((c) => c.url.split('/').pop())).toEqual(['cancel']);
  });

  it('lỗi GEN_MEMORY_FULL → câu thân thiện + "Chi tiết kỹ thuật" (mã) + nút "Xem ở Cài đặt"; vẫn bấm lại được', async () => {
    confirmReply = { status: 409, body: { status: 409, code: 'GEN_MEMORY_FULL', title: 'Gen nhớ đã đầy' } };
    const { container } = renderPanel();
    showProposal(MEMORY);
    const card = cardOf('Ghi nhớ');
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận ghi nhớ' }));
    const alert = await within(card).findByRole('alert');
    expect(alert).toHaveTextContent('Gen nhớ đã đủ 30 ghi chú — Sếp xoá bớt ở Cài đặt › Bộ não AI rồi xác nhận lại.');
    expect(within(alert).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(alert).toHaveTextContent('GEN_MEMORY_FULL');
    expect(container.textContent).not.toContain('[object Object]');
    expect(within(card).getByRole('button', { name: 'Xác nhận ghi nhớ' })).toBeEnabled();
    await userEvent.click(within(card).getByRole('button', { name: 'Xem ở Cài đặt' }));
    expect(navigations).toEqual(['/system?tab=brain#gen-memory']);
  });

  it('GEN_MEMORY_DUPLICATE và GEN_PROPOSAL_DECIDED → câu tiếng Việt thân thiện, không lộ mã ngoài "Chi tiết kỹ thuật"', async () => {
    confirmReply = { status: 409, body: { status: 409, code: 'GEN_MEMORY_DUPLICATE', title: 'duplicate' } };
    renderPanel();
    showProposal(MEMORY);
    const card = cardOf('Ghi nhớ');
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận ghi nhớ' }));
    await waitFor(() => expect(card).toHaveTextContent('Ghi chú này đã có trong Gen nhớ — không cần ghi lại.'));

    confirmReply = { status: 409, body: { status: 409, code: 'GEN_PROPOSAL_DECIDED', title: 'decided' } };
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận ghi nhớ' }));
    await waitFor(() => expect(card).toHaveTextContent('Đề xuất này đã được xác nhận hoặc đã huỷ ở nơi khác'));
    expect(within(card).getByRole('alert').querySelector('details code')?.textContent).toContain('GEN_PROPOSAL_DECIDED');
  });
});

// ── Thẻ Gen nhớ ở Cài đặt › Bộ não AI ─────────────────────────────────────────────────────────────────────

const NOTE_A: GenMemoryNote = {
  id: 'n1', text: 'Báo giá luôn ghi rõ VAT 8%.', reason: 'Sếp dặn khi soạn báo giá', source: 'gen',
  created_at: '2026-10-08T15:30:00Z', updated_at: '2026-10-08T15:30:00Z',
};
const NOTE_B: GenMemoryNote = {
  id: 'n2', text: 'Gọi khách là anh/chị, xưng em.', reason: null, source: 'owner',
  created_at: '2026-10-05T01:00:00Z', updated_at: '2026-10-09T03:00:00Z',
};

let notes: GenMemoryNote[] = [];
let memReply: ((method: string, id: string | null, body: Record<string, unknown> | null) => Response | null) | null = null;
const memCalls: Call[] = [];

function stubMemoryApi() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
      memCalls.push({ method, url, body });
      const m = url.match(/\/gen\/memory(?:\/(\w+))?$/);
      if (!m) return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
      const id = m[1] ?? null;
      const forced = memReply?.(method, id, body);
      if (forced) return forced;
      if (!id && method === 'GET') return json(200, { items: notes, limit: 30, max_len: 280, reason_max: 200 });
      if (id && method === 'PATCH') {
        const cur = notes.find((n) => n.id === id);
        if (!cur) return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
        const next = { ...cur, ...(body ?? {}), source: 'owner' as const } as GenMemoryNote;
        notes = notes.map((n) => (n.id === id ? next : n));
        return json(200, next);
      }
      if (id && method === 'DELETE') {
        notes = notes.filter((n) => n.id !== id);
        return json(204 as number, undefined);
      }
      return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
    }),
  );
}

function renderCard(role: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(qk.me, ME(role));
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <GenMemoryCard />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const memCallsTo = (method: string) => memCalls.filter((c) => c.method === method);
/** Chỉ các lời gọi tới /gen/memory (bỏ /auth/me do useMe tự tải lại). */
const memoryRequests = () => memCalls.filter((c) => c.url.includes('/gen/memory'));

describe('Thẻ "Gen nhớ" (GenMemoryCard)', () => {
  beforeEach(() => {
    notes = [NOTE_A, NOTE_B];
    memReply = null;
    memCalls.length = 0;
    stubMemoryApi();
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('Owner: tiêu đề, mô tả, đếm n/30, danh sách (nội dung, lý do mờ, nguồn, ngày dd/mm/yyyy), gắn data-gen-target', async () => {
    const { container } = renderCard('owner');
    const items = await screen.findAllByTestId('gen-memory-row');
    expect(items).toHaveLength(2);
    expect(screen.getByText('Gen nhớ')).toBeInTheDocument();
    expect(screen.getByText('Quy ước, sở thích Sếp đã xác nhận — Gen đọc khi trả lời và khi soạn Bản tin')).toBeInTheDocument();
    expect(screen.getByTestId('gen-memory-count')).toHaveTextContent('2/30');
    expect(container.querySelector('[data-gen-target="system.brain.memory"]')).not.toBeNull();
    expect(container.querySelector('#gen-memory')).not.toBeNull();
    // Mới sửa nhất đứng theo thứ tự máy chủ trả (không sắp lại).
    expect(items[0]).toHaveTextContent('Báo giá luôn ghi rõ VAT 8%.');
    expect(items[0]).toHaveTextContent('Sếp dặn khi soạn báo giá');
    expect(items[0]).toHaveTextContent('Gen đề xuất · 08/10/2026');
    expect(items[1]).toHaveTextContent('Gọi khách là anh/chị, xưng em.');
    expect(items[1]).toHaveTextContent('Sếp sửa · 09/10/2026');
    expect(items[1].querySelector('.gen-mem__reason')).toBeNull(); // lý do null ⇒ không có dòng lý do
    expect(items[0].querySelector('.gen-mem__reason')).not.toBeNull();
    expect(memoryRequests().map((c) => `${c.method} ${c.url}`)).toEqual(['GET /api/v1/gen/memory']);
    expect(container.textContent).not.toContain('[object Object]');
  });

  it('vai trò khác Owner (Manager): không vẽ thẻ và KHÔNG gọi /gen/memory', async () => {
    const { container } = renderCard('manager');
    await new Promise((r) => setTimeout(r, 50));
    expect(container.querySelector('#gen-memory')).toBeNull();
    expect(screen.queryByText('Gen nhớ')).toBeNull();
    expect(screen.queryByTestId('gen-memory-row')).toBeNull();
    expect(memoryRequests()).toEqual([]);
  });

  it('trạng thái rỗng: câu hướng dẫn dặn Gen "nhớ giúp em …"; đếm 0/30', async () => {
    notes = [];
    renderCard('owner');
    expect(await screen.findByText('Chưa có ghi chú — dặn Gen “nhớ giúp em …” để Gen đề xuất')).toBeInTheDocument();
    expect(screen.getByTestId('gen-memory-count')).toHaveTextContent('0/30');
  });

  it('Sửa tại chỗ: ô điền sẵn + bộ đếm ký tự; Lưu gọi PATCH /gen/memory/{id} chỉ với trường đã đổi → nguồn "Sếp sửa"; Huỷ không gọi gì', async () => {
    renderCard('owner');
    const rows = await screen.findAllByTestId('gen-memory-row');
    await userEvent.click(within(rows[0]).getByRole('button', { name: 'Sửa' }));
    const text = within(rows[0]).getByLabelText('Ghi nhớ');
    expect(text.tagName).toBe('TEXTAREA');
    expect(text).toHaveValue('Báo giá luôn ghi rõ VAT 8%.');
    expect(text).toHaveAttribute('maxlength', '280');
    expect(within(rows[0]).getByTestId('gen-memory-chars-text')).toHaveTextContent('27/280');
    expect(within(rows[0]).getByTestId('gen-memory-chars-reason')).toHaveTextContent('24/200');
    expect(within(rows[0]).getByRole('button', { name: 'Lưu' })).toBeDisabled(); // chưa đổi gì

    // Huỷ: không gọi PATCH.
    await userEvent.click(within(rows[0]).getByRole('button', { name: 'Huỷ' }));
    expect(memCallsTo('PATCH')).toEqual([]);

    const again = (await screen.findAllByTestId('gen-memory-row'))[0];
    await userEvent.click(within(again).getByRole('button', { name: 'Sửa' }));
    const box = within(again).getByLabelText('Ghi nhớ');
    await userEvent.clear(box);
    expect(within(again).getByRole('button', { name: 'Lưu' })).toBeDisabled(); // nội dung trống
    await userEvent.type(box, 'Báo giá ghi rõ VAT 10%.');
    await userEvent.click(within(again).getByRole('button', { name: 'Lưu' }));
    await waitFor(() => expect(memCallsTo('PATCH')).toHaveLength(1));
    expect(memCallsTo('PATCH')[0].url).toMatch(/\/gen\/memory\/n1$/);
    expect(memCallsTo('PATCH')[0].body).toEqual({ text: 'Báo giá ghi rõ VAT 10%.' });
    // Tải lại danh sách: dòng đã đổi, nguồn thành "Sếp sửa".
    await waitFor(() => expect(screen.getAllByTestId('gen-memory-row')[0]).toHaveTextContent('Báo giá ghi rõ VAT 10%.'));
    expect(screen.getAllByTestId('gen-memory-row')[0]).toHaveTextContent('Sếp sửa');
    expect(memCallsTo('DELETE')).toEqual([]);
  });

  it('Xoá: hộp xác nhận (Huỷ không xoá) → "Xoá ghi chú" gọi DELETE /gen/memory/{id}, dòng biến mất', async () => {
    renderCard('owner');
    const rows = await screen.findAllByTestId('gen-memory-row');
    await userEvent.click(within(rows[1]).getByRole('button', { name: 'Xoá' }));
    const dlg = await screen.findByRole('dialog', { name: /Xoá ghi chú này\?/ });
    expect(dlg).toHaveTextContent('Gọi khách là anh/chị, xưng em.');
    await userEvent.click(within(dlg).getByRole('button', { name: 'Huỷ' }));
    expect(memCallsTo('DELETE')).toEqual([]);
    expect(screen.getAllByTestId('gen-memory-row')).toHaveLength(2);

    await userEvent.click(within((await screen.findAllByTestId('gen-memory-row'))[1]).getByRole('button', { name: 'Xoá' }));
    await userEvent.click(within(await screen.findByRole('dialog', { name: /Xoá ghi chú này\?/ })).getByRole('button', { name: 'Xoá ghi chú' }));
    await waitFor(() => expect(memCallsTo('DELETE')).toHaveLength(1));
    expect(memCallsTo('DELETE')[0].url).toMatch(/\/gen\/memory\/n2$/);
    await waitFor(() => expect(screen.getAllByTestId('gen-memory-row')).toHaveLength(1));
    expect(screen.getByTestId('gen-memory-count')).toHaveTextContent('1/30');
  });

  it('lỗi khi sửa: 409 GEN_MEMORY_DUPLICATE ⇒ câu thân thiện + "Chi tiết kỹ thuật"; 404 ⇒ "không còn nữa"; không render object', async () => {
    memReply = (method, id) => (method === 'PATCH' && id === 'n1' ? json(409, { status: 409, code: 'GEN_MEMORY_DUPLICATE', title: 'duplicate' }) : null);
    const { container } = renderCard('owner');
    const rows = await screen.findAllByTestId('gen-memory-row');
    await userEvent.click(within(rows[0]).getByRole('button', { name: 'Sửa' }));
    await userEvent.type(within(rows[0]).getByLabelText('Ghi nhớ'), ' Thêm');
    await userEvent.click(within(rows[0]).getByRole('button', { name: 'Lưu' }));
    const alert = await within(rows[0]).findByRole('alert');
    expect(alert).toHaveTextContent('Ghi chú này trùng với một ghi chú đã có trong Gen nhớ.');
    expect(within(alert).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(alert).toHaveTextContent('GEN_MEMORY_DUPLICATE');
    expect(container.textContent).not.toContain('[object Object]');
    expect(screen.getByRole('button', { name: 'Lưu' })).toBeEnabled(); // giữ nguyên ô đang sửa để chỉnh lại
  });

  it('danh sách lỗi tải ⇒ khung lỗi thân thiện có "Thử lại"', async () => {
    memReply = (method) => (method === 'GET' ? json(500, { status: 500, code: 'INTERNAL', title: 'Hệ thống gặp lỗi khi xử lý yêu cầu' }) : null);
    renderCard('owner');
    expect(await screen.findByText(/Hệ thống gặp lỗi khi xử lý yêu cầu/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Thử lại/ })).toBeInTheDocument();
    expect(screen.queryByTestId('gen-memory-row')).toBeNull();
  });
});

// ── Hàm thuần + hợp đồng ──────────────────────────────────────────────────────────────────────────────────

describe('genMemoryModel + hợp đồng', () => {
  it('sourceLabel / fmtNoteDate / charCount / countText / memoryLimits', () => {
    expect(sourceLabel('gen')).toBe('Gen đề xuất');
    expect(sourceLabel('owner')).toBe('Sếp sửa');
    expect(sourceLabel('lạ')).toBe('Gen đề xuất');
    expect(fmtNoteDate('2026-10-08T17:30:00Z')).toBe('09/10/2026'); // 00:30 ngày 09 giờ VN
    expect(fmtNoteDate('2026-10-08T17:30:00Z', 'UTC')).toBe('08/10/2026');
    expect(fmtNoteDate('hỏng')).toBe('—');
    expect(fmtNoteDate(null)).toBe('—');
    expect(charCount('Việt Nam', 280)).toBe('8/280');
    expect(countText(7, 30)).toBe('7/30');
    expect(memoryLimits(undefined)).toEqual({ limit: 30, maxLen: 280, reasonMax: 200 });
    expect(memoryLimits({ limit: 12, max_len: 100, reason_max: 50 })).toEqual({ limit: 12, maxLen: 100, reasonMax: 50 });
    expect(memoryLimits({ limit: 'x', max_len: -1, reason_max: 0 } as never)).toEqual({ limit: 30, maxLen: 280, reasonMax: 200 });
  });

  it('memoryItems ép mọi trường về chuỗi, bỏ mục không có id, lý do rỗng ⇒ null', () => {
    const view = memoryItems({
      items: [
        { id: 'a', text: 'ok', reason: '  ', source: 'gen', created_at: '2026-10-08T15:30:00Z', updated_at: '2026-10-08T15:30:00Z' },
        { id: 'b', text: { x: 1 }, reason: { y: 2 }, source: 'owner', created_at: 'x', updated_at: 'x' },
        { text: 'không id' },
      ],
      limit: 30, max_len: 280, reason_max: 200,
    } as never);
    expect(view).toHaveLength(2);
    expect(view[0]).toMatchObject({ id: 'a', text: 'ok', reason: null, source: 'Gen đề xuất', date: '08/10/2026' });
    expect(view[1]).toMatchObject({ id: 'b', text: '', reason: null, source: 'Sếp sửa', date: '—' });
    expect(memoryItems(undefined)).toEqual([]);
  });

  it('memoryErrorText: mã Gen nhớ ⇒ câu thân thiện; 404 / 403 ⇒ câu riêng; còn lại ⇒ errorText chung', () => {
    const e = (status: number, code: string) => new ApiError(status, { code, title: 'Hệ thống gặp lỗi khi xử lý yêu cầu' });
    expect(memoryErrorText(e(409, 'GEN_MEMORY_FULL'))).toBe('Gen nhớ đã đủ 30 ghi chú — xoá bớt một ghi chú rồi thử lại.');
    expect(memoryErrorText(e(409, 'GEN_MEMORY_DUPLICATE'))).toBe('Ghi chú này trùng với một ghi chú đã có trong Gen nhớ.');
    expect(memoryErrorText(e(404, 'NOT_FOUND'))).toContain('không còn nữa');
    expect(memoryErrorText(e(403, 'FORBIDDEN'))).toBe('Chỉ Sếp (Owner) dùng được Gen nhớ.');
    expect(memoryErrorText(e(500, 'INTERNAL'))).toContain('Hệ thống gặp lỗi');
    expect(typeof memoryErrorText({ weird: true })).toBe('string');
  });

  it('GEN_TARGETS có "system.brain.memory" (Cài đặt, tab brain, quyền system.manage)', () => {
    expect(GEN_TARGET_BY_ID['system.brain.memory']).toMatchObject({ screen: 'system', params: { tab: 'brain' }, permission: 'system.manage' });
  });
});
