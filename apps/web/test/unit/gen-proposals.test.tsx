/**
 * v0.1.24 — Gen v2 (A4): thẻ đề xuất có xác nhận trong khung Gen. Gen không tự ghi: chỉ khi bấm Xác nhận web mới gọi
 * `POST /gen/proposals/{id}/confirm`; Sửa gửi đúng các trường đã đổi; Huỷ gọi `/cancel`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { GenProposal, GenStepEvent } from '@gen-harness/contracts';
import { GenPanel } from '../../src/gen/GenPanel';
import { changedFields, fromLocalInput, toLocalInput } from '../../src/gen/proposalModel';
import { useGenStore } from '../../src/gen/genStore';
import { applyEvent } from '../../src/lib/realtime';
import { setNavigator } from '../../src/lib/navigation';
import { qk } from '../../src/lib/queries';
import { queryClient } from '../../src/lib/queryClient';

const ME = {
  id: 'u1', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A', role: { code: 'owner', name: 'Owner — Sếp' },
  org: { id: 'o1', name: 'Genesis', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: { 'action.approve': 'all' }, must_change_password: false,
  features: { gen: true },
};

const REMIND = '2026-09-30T08:00:00Z';

const REMINDER: GenProposal = {
  id: 'p1',
  type: 'reminder',
  fields: { title: 'Gọi lại anh Bình', remind_at: REMIND, due_at: null, priority: 'P2', assignee_user_id: 'u1' },
  summary: 'Tạo nhắc việc “Gọi lại anh Bình” (P2), nhắc lúc 15:00 30/09/2026, giao cho Anh Nguyễn Văn A.',
  labels: { user: 'Anh Nguyễn Văn A' },
  target: 'tasks.new',
  requires_pin: false,
  status: 'pending',
};

const DRAFT: GenProposal = {
  id: 'p2',
  type: 'draft_message',
  fields: { title: 'Báo giá MDF', text: 'Chào anh, em gửi báo giá.' },
  summary: 'Soạn bản nháp tin “Báo giá MDF”. Bản nháp vào Bàn làm việc chờ duyệt — chưa gửi đi.',
  labels: {},
  target: 'workbench.drafts',
  requires_pin: true,
  status: 'pending',
};

type Call = { method: string; url: string; body: unknown };
const calls: Call[] = [];
let confirmReply: { status: number; body: unknown } | null = null;
let draftResultId: string | null = 'd-7f/1';
let draftSendable: boolean | undefined = true;
const navigations: string[] = [];

function stubApi() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ method, url, body });
      const json = (status: number, b: unknown) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
      if (url.endsWith('/gen/assignees'))
        return json(200, { items: [{ id: 'u1', name: 'Anh Nguyễn Văn A', role: 'Owner', me: true }, { id: 'u2', name: 'Chị Lan', role: 'Vận hành', me: false }] });
      const m = url.match(/\/gen\/proposals\/(\w+)\/(confirm|cancel)$/);
      if (m && method === 'POST') {
        if (confirmReply) return json(confirmReply.status, confirmReply.body);
        const base = m[1] === 'p1' ? REMINDER : DRAFT;
        if (m[2] === 'cancel') return json(200, { ...base, status: 'cancelled' });
        const result =
          m[1] === 'p1'
            ? { type: 'task', id: 't9', code: 'TSK-0412', screen: 'tasks' }
            : { type: 'draft', id: draftResultId, code: 'ACT-0999', screen: 'workbench', ...(draftSendable === undefined ? {} : { sendable: draftSendable }) };
        return json(200, { ...base, fields: { ...base.fields, ...(body?.fields ?? {}) }, status: 'confirmed', result });
      }
      return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
    }),
  );
}

function showProposal(p: GenProposal) {
  useGenStore.setState({
    messages: [
      { id: 'u-1', role: 'user', text: 'Nhắc tôi', steps: [] },
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

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(qk.me, ME);
  calls.length = 0;
  navigations.length = 0;
  confirmReply = null;
  draftResultId = 'd-7f/1';
  draftSendable = true;
  useGenStore.setState({ openByUser: {}, conversationId: 'c1', messages: [], busy: false, spotlight: null });
  setNavigator((to) => navigations.push(to));
  stubApi();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('Gen proposal card', () => {
  it('renders the proposal from a streamed step without writing anything', () => {
    renderPanel();
    showProposal(REMINDER);
    const card = screen.getByRole('group', { name: 'Đề xuất: Tạo nhắc việc' });
    expect(card).toHaveTextContent('Đề xuất · Tạo nhắc việc');
    expect(card).toHaveTextContent(REMINDER.summary);
    expect(card).toHaveTextContent('Gọi lại anh Bình');
    expect(card).toHaveTextContent('Anh Nguyễn Văn A');
    for (const name of ['Xác nhận', 'Sửa', 'Huỷ']) expect(within(card).getByRole('button', { name })).toBeEnabled();
    expect(within(card).queryByText('Cần mã PIN')).toBeNull();
    expect(writes()).toEqual([]);
  });

  it('confirms as-is, then shows the result and a link to the screen', async () => {
    renderPanel();
    showProposal(REMINDER);
    const card = screen.getByRole('group', { name: 'Đề xuất: Tạo nhắc việc' });
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận' }));
    await waitFor(() => expect(card).toHaveTextContent('Đã xác nhận · TSK-0412'));
    expect(writes()).toHaveLength(1);
    expect(writes()[0].url).toMatch(/\/gen\/proposals\/p1\/confirm$/);
    expect(writes()[0].body).toEqual({ fields: {} });
    expect(within(card).queryByRole('button', { name: 'Xác nhận' })).toBeNull();
    // Trạng thái ghi vào store (mở lại khung vẫn đúng).
    const stored = useGenStore.getState().messages[1].steps[1];
    expect(stored?.kind === 'proposal' && stored.proposal.status).toBe('confirmed');
    await userEvent.click(within(card).getByRole('button', { name: /Mở Việc & Nhắc hẹn/ }));
    expect(navigations).toEqual(['/tasks']);
  });

  it('edit sends only the changed editable fields', async () => {
    renderPanel();
    showProposal(REMINDER);
    const card = screen.getByRole('group', { name: 'Đề xuất: Tạo nhắc việc' });
    await userEvent.click(within(card).getByRole('button', { name: 'Sửa' }));
    const title = within(card).getByLabelText('Việc');
    await userEvent.clear(title);
    await userEvent.type(title, 'Gọi lại anh Bình lúc 4 giờ');
    await userEvent.selectOptions(within(card).getByLabelText('Ưu tiên'), 'P1');
    await waitFor(() => expect(within(card).getByRole('option', { name: 'Chị Lan' })).toBeInTheDocument());
    await userEvent.selectOptions(within(card).getByLabelText('Giao cho'), 'u2');
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận' }));
    await waitFor(() => expect(card).toHaveTextContent('Đã xác nhận'));
    expect(writes()[0].body).toEqual({ fields: { title: 'Gọi lại anh Bình lúc 4 giờ', priority: 'P1', assignee_user_id: 'u2' } });
  });

  it('cancel calls the cancel endpoint and leaves nothing to confirm', async () => {
    renderPanel();
    showProposal(REMINDER);
    const card = screen.getByRole('group', { name: 'Đề xuất: Tạo nhắc việc' });
    await userEvent.click(within(card).getByRole('button', { name: 'Huỷ' }));
    await waitFor(() => expect(card).toHaveTextContent('Đã huỷ — không làm gì'));
    expect(writes().map((c) => c.url.replace(/^.*\/gen/, '/gen'))).toEqual(['/gen/proposals/p1/cancel']);
  });

  it('draft proposal: flags PIN, blocks empty edit, shows server denial', async () => {
    confirmReply = { status: 403, body: { status: 403, code: 'FORBIDDEN', title: 'Vai trò của bạn không có quyền thao tác này' } };
    renderPanel();
    showProposal(DRAFT);
    const card = screen.getByRole('group', { name: 'Đề xuất: Soạn nháp tin gửi đi' });
    expect(card).toHaveTextContent('Cần mã PIN');
    expect(card).toHaveTextContent('chưa gửi đi');
    await userEvent.click(within(card).getByRole('button', { name: 'Sửa' }));
    await userEvent.clear(within(card).getByLabelText('Nội dung'));
    expect(within(card).getByRole('button', { name: 'Xác nhận' })).toBeDisabled();
    await userEvent.click(within(card).getByRole('button', { name: 'Bỏ sửa' }));
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận' }));
    await waitFor(() => expect(within(card).getByRole('alert')).toHaveTextContent('không có quyền'));
    expect(within(card).getByRole('button', { name: 'Xác nhận' })).toBeEnabled(); // vẫn chờ, thử lại được
  });

  it('v0.1.43 (F-24): nháp tin đã xác nhận → "Đã lưu nháp — chưa gửi" + nút "Mở để duyệt và gửi" tới đúng nháp', async () => {
    const { container } = renderPanel();
    showProposal(DRAFT);
    const card = screen.getByRole('group', { name: 'Đề xuất: Soạn nháp tin gửi đi' });
    expect(card.querySelector('[data-icon="paper-plane-tilt"]')).toBeNull();
    expect(card.querySelector('[data-icon="note-pencil"]')).not.toBeNull();
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận' }));
    await waitFor(() => expect(card).toHaveTextContent('Đã lưu nháp — chưa gửi · ACT-0999'));
    expect(card).not.toHaveTextContent('Đã xác nhận');
    expect(card.querySelector('[data-icon="floppy-disk"]')).not.toBeNull();
    expect(card.querySelector('[data-icon="paper-plane-tilt"]')).toBeNull();
    expect(within(card).queryByRole('button', { name: /Mở Bàn làm việc/ })).toBeNull();
    expect(writes()).toHaveLength(1);
    await userEvent.click(within(card).getByRole('button', { name: 'Mở để duyệt và gửi' }));
    expect(navigations).toEqual([`/workbench?id=${encodeURIComponent('d-7f/1')}`]);
    expect(writes()).toHaveLength(1); // điều hướng thôi — không gửi gì thêm
    expect(container.textContent).not.toContain('[object Object]');
  });

  it('v0.1.43 (F-24): nháp không có nơi gửi (đối tượng là người/không có) → không hứa "Mở để duyệt và gửi", chỉ mở nháp', async () => {
    draftSendable = false;
    renderPanel();
    showProposal(DRAFT);
    const card = screen.getByRole('group', { name: 'Đề xuất: Soạn nháp tin gửi đi' });
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận' }));
    await waitFor(() => expect(card).toHaveTextContent('Đã lưu nháp — chưa gửi'));
    expect(within(card).queryByRole('button', { name: 'Mở để duyệt và gửi' })).toBeNull();
    expect(card).toHaveTextContent('Nháp chưa có nơi gửi');
    await userEvent.click(within(card).getByRole('button', { name: /Mở nháp ở Bàn làm việc/ }));
    expect(navigations).toEqual([`/workbench?id=${encodeURIComponent('d-7f/1')}`]);
  });

  it('v0.1.43 (F-24): kết quả cũ không có `sendable` → chỉ chip "Mở nháp", không khẳng định "chưa có nơi gửi"', async () => {
    draftSendable = undefined;
    renderPanel();
    showProposal(DRAFT);
    const card = screen.getByRole('group', { name: 'Đề xuất: Soạn nháp tin gửi đi' });
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận' }));
    await waitFor(() => expect(card).toHaveTextContent('Đã lưu nháp — chưa gửi'));
    expect(within(card).queryByRole('button', { name: 'Mở để duyệt và gửi' })).toBeNull();
    expect(card).not.toHaveTextContent('Nháp chưa có nơi gửi');
    expect(card).not.toHaveTextContent('Chờ Sếp duyệt rồi mới gửi');
    expect(card.querySelector('.gen-prop__note')).toBeNull();
    await userEvent.click(within(card).getByRole('button', { name: 'Mở nháp' }));
    expect(navigations).toEqual([`/workbench?id=${encodeURIComponent('d-7f/1')}`]);
  });

  it('v0.1.43 (F-24): vai trò không có quyền duyệt (Operator) → "Mở nháp" + "Chờ Sếp duyệt rồi mới gửi"', async () => {
    queryClient.setQueryData(qk.me, { ...ME, role: { code: 'operator', name: 'Vận hành' }, permissions: { 'action.draft': 'all' } });
    renderPanel();
    showProposal(DRAFT);
    const card = screen.getByRole('group', { name: 'Đề xuất: Soạn nháp tin gửi đi' });
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận' }));
    await waitFor(() => expect(card).toHaveTextContent('Đã lưu nháp — chưa gửi'));
    expect(within(card).queryByRole('button', { name: 'Mở để duyệt và gửi' })).toBeNull();
    expect(card).toHaveTextContent('Chờ Sếp duyệt rồi mới gửi');
    await userEvent.click(within(card).getByRole('button', { name: 'Mở nháp' }));
    expect(navigations).toEqual([`/workbench?id=${encodeURIComponent('d-7f/1')}`]);
  });

  it('v0.1.43 (F-24): nháp tin không có id kết quả → giữ nút "Mở Bàn làm việc"', async () => {
    draftResultId = null;
    renderPanel();
    showProposal(DRAFT);
    const card = screen.getByRole('group', { name: 'Đề xuất: Soạn nháp tin gửi đi' });
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận' }));
    await waitFor(() => expect(card).toHaveTextContent('Đã lưu nháp — chưa gửi'));
    expect(within(card).queryByRole('button', { name: 'Mở để duyệt và gửi' })).toBeNull();
    await userEvent.click(within(card).getByRole('button', { name: /Mở Bàn làm việc/ }));
    expect(navigations).toEqual(['/workbench']);
  });

  it('helpers convert datetime-local and diff fields', () => {
    expect(fromLocalInput(toLocalInput(REMIND))).toBe('2026-09-30T08:00:00.000Z');
    expect(fromLocalInput('')).toBeNull();
    expect(toLocalInput('không phải ngày')).toBe('');
    expect(changedFields(REMINDER, { title: 'Gọi lại anh Bình', remind_at: toLocalInput(REMIND), due_at: '', priority: 'P2', assignee_user_id: 'u1' })).toEqual({});
    expect(changedFields(REMINDER, { title: 'x', remind_at: toLocalInput(REMIND), due_at: '', priority: 'P2', assignee_user_id: '' })).toEqual({ title: 'x', assignee_user_id: null });
  });
});
