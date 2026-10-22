import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { request as httpRequest, createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import type { DraftDetail, OwnerBrief, OwnerFeed, OwnerInsights, OwnerProposal, OwnerRelations, OwnerToday } from '@gen-harness/contracts';
import { DraftDecide } from '../../src/owner/actions/DraftDecide';
import { ProposalDecide } from '../../src/owner/actions/ProposalDecide';
import {
  ACTION_INVALIDATE_KEYS,
  expiresText,
  invalidateAfterWrite,
  tomorrowMorning,
  useAckItem,
  useCreateReminder,
  useIntroduceMatch,
  useRejectMatch,
  useReplyToItem,
  useSilenceItem,
  useTaskDone,
  useTaskPostpone,
} from '../../src/owner/actions/useOwnerActions';
import {
  INSIGHTS_REFRESH_MS,
  OWNER_KEYS,
  useOwnerBrief,
  useOwnerFeed,
  useOwnerFeedContext,
  useOwnerInsights,
  useOwnerProposals,
  useOwnerRelations,
} from '../../src/owner/queries';
import { PinDialogHost } from '../../src/shell/PinDialogHost';
import { qk } from '../../src/lib/queries';
import { qk3 } from '../../src/screens/core/queries';
import { qkMarket } from '../../src/screens/market/queries';
import { qkQueue } from '../../src/screens/queue/queries';
import { queryClient } from '../../src/lib/queryClient';
import { createMockApi } from '../mock-api';
import { createMock as createOwnerMock, parseOwnerDataMode } from '../mock-owner';
import type { P2Ctx } from '../mock-phase2';

/**
 * v0.1.59 (G1) — thành phần HÀNH ĐỘNG của Mặt tiền: DraftDecide (Duyệt / Sửa rồi gửi / Từ chối, mã PIN do client tự hỏi khi
 * gặp 423), ProposalDecide (Xác nhận / Huỷ, 404/410 ⇒ "Đề xuất đã hết hạn"), các hook ghi (useReplyToItem trả draft_id…) và
 * mock Mặt tiền (7 GET, chế độ dữ liệu, nháp thật từ mock core, hook `owner/data`).
 */

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json' } });

const ME = {
  id: 'u', email: 'owner@genesis.local', display_name: 'Anh Chủ', role: { code: 'owner', name: 'owner' },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' }, addressing: { self: 'Anh', bot_calls_me: 'Sếp' },
  pin_verified_until: null, permissions: {},
};

const DRAFT = (over: Record<string, unknown> = {}): DraftDetail =>
  ({
    id: 'draft-1', code: 'ACT-0231', kind: 'message', kind_label: 'Tin nhắn', title: 'Trả lời anh Hậu về giá ván MDF', agent: null, created_by: null,
    created_at: '2026-10-11T02:00:00Z', status: 'pending', hold_reason: 'ghi ra ngoài', subject: null,
    paragraphs: ['Dạ em gửi báo giá ván MDF E1 17mm ạ.'], text: 'Dạ em gửi báo giá ván MDF E1 17mm ạ.', lang: 'vi',
    target: { channel: 'zalo', thread_type: 'group', group: { id: 'g1', code: 'GRP-1', name: 'Nhóm Gỗ Việt', channel: 'zalo' }, person: null },
    amount_vnd: null, autonomy_level: 4, flags: { writes_external: true, personnel_related: false, over_threshold: false },
    approve_label: 'Duyệt và gửi qua Zalo', sources: [], context: [{ key: 'độ nóng', value: '91 · đang tăng', ref: null }],
    side_actions: [], decision: null, send_result: null, versions: [], ...over,
  }) as unknown as DraftDetail;

const PROPOSAL = (over: Partial<OwnerProposal> = {}): OwnerProposal => ({
  id: 'prop-1', type: 'reminder', type_label: 'Tạo nhắc việc', summary: 'Nhắc Sếp gọi lại chị Lan vào 9h sáng mai.', needs_pin: false,
  created_at: new Date(Date.now() - 40 * 60_000).toISOString(), expires_at: new Date(Date.now() + 23 * 3600_000).toISOString(), conversation_id: 'c1', ...over,
});

interface Call {
  url: string;
  method: string;
  body: unknown;
}
function mockFetch(handler: (c: Call) => Response | undefined) {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const c = { url: String(url), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined };
      calls.push(c);
      const r = handler(c);
      if (r) return r;
      if (c.url.endsWith('/auth/me')) return json(200, ME);
      return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tìm thấy' });
    }),
  );
  return calls;
}

function renderUi(ui: React.ReactElement) {
  queryClient.setQueryData(qk.me, ME);
  return render(
    <QueryClientProvider client={queryClient}>
      {ui}
      <PinDialogHost />
    </QueryClientProvider>,
  );
}

const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;

beforeEach(() => {
  queryClient.clear();
  queryClient.setDefaultOptions({ queries: { retry: false, staleTime: Infinity } });
  document.cookie = 'gh_csrf=test-csrf';
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ── DraftDecide ─────────────────────────────────────────────────────────────────────────────────────────────────

describe('DraftDecide', () => {
  it('Duyệt và gửi: gặp 423 thì client hỏi mã PIN rồi gửi lại, xong gọi onDone và tải lại [owner]', async () => {
    let pinOk = false;
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/drafts/draft-1') && c.method === 'GET') return json(200, DRAFT());
      if (c.url.endsWith('/drafts/draft-1/approve')) {
        if (!pinOk) return json(423, { status: 423, code: 'PIN_REQUIRED', title: 'Thao tác này cần nhập mã PIN' });
        return json(200, DRAFT({ status: 'sent' }));
      }
      if (c.url.endsWith('/auth/pin/verify')) {
        pinOk = true;
        return json(200, { pin_verified_until: new Date(Date.now() + 1800_000).toISOString() });
      }
      return undefined;
    });
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    const onDone = vi.fn();
    const user = userEvent.setup();
    renderUi(<DraftDecide draftId="draft-1" onDone={onDone} />);

    expect(await screen.findByTestId('owner-draft-text')).toHaveTextContent('Dạ em gửi báo giá ván MDF E1 17mm ạ.');
    expect(screen.getByTestId('owner-draft-hold')).toHaveTextContent('ghi ra ngoài');
    expect(screen.getByTestId('owner-draft-context')).toHaveTextContent('độ nóng');
    expect(screen.getByRole('heading', { name: 'Trả lời anh Hậu về giá ván MDF' })).toBeInTheDocument();
    expect(screen.getByText(/Gửi cho Zalo · Nhóm Gỗ Việt/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Duyệt và gửi' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sửa rồi gửi' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Từ chối' })).toBeInTheDocument();
    expect(calls.some((c) => c.method === 'POST')).toBe(false);                 // chưa bấm ⇒ chưa ghi gì

    await user.click(screen.getByRole('button', { name: 'Duyệt và gửi' }));
    const pin = await screen.findByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });       // hộp PIN do CLIENT mở, không phải thành phần này
    await waitFor(() => expect(within(pin).getAllByLabelText(/Mã PIN — chữ số/)[0]).toHaveFocus());
    await user.keyboard('246810');

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(onDone.mock.calls[0][0]).toMatchObject({ id: 'draft-1', status: 'sent' });
    const approves = calls.filter((c) => c.url.endsWith('/drafts/draft-1/approve'));
    expect(approves).toHaveLength(2);                                            // lần 1 bị 423, lần 2 sau khi có phiên PIN
    expect(approves[0].method).toBe('POST');
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['owner'] });
    for (const k of ['drafts', 'queue', 'market']) expect(invalidate).toHaveBeenCalledWith({ queryKey: [k] });
  });

  it('Sửa rồi gửi gửi đúng nội dung đã sửa; Từ chối kèm lý do tuỳ chọn', async () => {
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/drafts/draft-1') && c.method === 'GET') return json(200, DRAFT());
      if (c.url.endsWith('/edit-send')) return json(200, DRAFT({ status: 'sent' }));
      if (c.url.endsWith('/reject')) return json(200, DRAFT({ status: 'rejected' }));
      return undefined;
    });
    const onDone = vi.fn();
    const user = userEvent.setup();
    const first = renderUi(<DraftDecide draftId="draft-1" onDone={onDone} />);
    await user.click(await screen.findByRole('button', { name: 'Sửa rồi gửi' }));
    const box = screen.getByTestId('owner-draft-edit-text');
    await user.clear(box);
    await user.type(box, 'Dạ em gửi giá mới ạ.');
    await user.click(screen.getByRole('button', { name: 'Gửi bản đã sửa' }));
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(calls.find((c) => c.url.endsWith('/edit-send'))?.body).toEqual({ text: 'Dạ em gửi giá mới ạ.' });

    first.unmount();
    onDone.mockClear();
    renderUi(<DraftDecide draftId="draft-1" onDone={onDone} />);
    await user.click(await screen.findByRole('button', { name: 'Từ chối' }));
    await user.type(screen.getByTestId('owner-draft-reason'), 'Giá chưa đúng');
    await user.click(screen.getByRole('button', { name: 'Xác nhận từ chối' }));
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(calls.find((c) => c.url.endsWith('/reject'))?.body).toEqual({ reason: 'Giá chưa đúng' });
    await user.click(screen.getByRole('button', { name: 'Quay lại' }));
  });

  it('lỗi hiện câu thân thiện + "Chi tiết kỹ thuật", không render object', async () => {
    mockFetch((c) => {
      if (c.url.endsWith('/drafts/draft-1') && c.method === 'GET') return json(200, DRAFT());
      if (c.url.endsWith('/approve')) return json(500, { status: 500, code: 'INTERNAL', title: 'Máy chủ gặp lỗi', error_id: 'ERR-TEST-7' });
      return undefined;
    });
    const onDone = vi.fn();
    const user = userEvent.setup();
    renderUi(<DraftDecide draftId="draft-1" onDone={onDone} />);
    await user.click(await screen.findByRole('button', { name: 'Duyệt và gửi' }));
    const err = await screen.findByTestId('owner-action-error');
    expect(err).toHaveTextContent('Máy chủ gặp lỗi');
    expect(within(err).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(err).toHaveTextContent('ERR-TEST-7');
    expect(document.body.textContent).not.toContain('[object Object]');
    expect(onDone).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Duyệt và gửi' })).toBeEnabled();      // bấm lại được
  });

  it('API trả object ở trường chuỗi ⇒ không render object; nháp đã xử lý ⇒ không còn nút', async () => {
    mockFetch((c) =>
      c.url.endsWith('/drafts/draft-1')
        ? json(200, DRAFT({ title: { x: 1 }, text: { y: 2 }, hold_reason: { z: 3 }, context: [{ key: { a: 1 }, value: { b: 2 } }, { key: 'ok', value: 'có' }], status: 'sent' }))
        : undefined,
    );
    renderUi(<DraftDecide draftId="draft-1" />);
    expect(await screen.findByTestId('owner-draft-decided')).toHaveTextContent('đã được xử lý');
    expect(document.body.textContent).not.toContain('[object Object]');
    expect(screen.queryByRole('button', { name: 'Duyệt và gửi' })).toBeNull();
    expect(screen.getByTestId('owner-draft-context')).toHaveTextContent('ok');
  });

  it('không tải được nháp ⇒ lỗi thân thiện có Chi tiết kỹ thuật và nút Thử lại', async () => {
    mockFetch((c) => (c.url.endsWith('/drafts/draft-1') ? json(404, { status: 404, code: 'NOT_FOUND', title: 'Bản nháp không tồn tại' }) : undefined));
    renderUi(<DraftDecide draftId="draft-1" />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Bản nháp không tồn tại');
    expect(screen.getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Thử lại' })).toBeInTheDocument();
  });
});

// ── ProposalDecide ──────────────────────────────────────────────────────────────────────────────────────────────

describe('ProposalDecide', () => {
  it('Xác nhận / Huỷ gọi đúng đường; xong gọi onDone và tải lại [owner]', async () => {
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/gen/proposals/prop-1/confirm')) return json(200, { id: 'prop-1', status: 'confirmed' });
      if (c.url.endsWith('/gen/proposals/prop-1/cancel')) return json(200, { id: 'prop-1', status: 'cancelled' });
      return undefined;
    });
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    const onDone = vi.fn();
    const user = userEvent.setup();
    const first = renderUi(<ProposalDecide proposal={PROPOSAL()} onDone={onDone} />);
    expect(screen.getByTestId('owner-proposal-summary')).toHaveTextContent('Nhắc Sếp gọi lại chị Lan');
    expect(screen.getByText('Tạo nhắc việc')).toBeInTheDocument();
    expect(screen.getByText(/Hết hạn sau 23 giờ/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Xác nhận' }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith('confirmed'));
    expect(calls.find((c) => c.url.endsWith('/confirm'))?.method).toBe('POST');
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['owner'] });
    first.unmount();
    onDone.mockClear();
    renderUi(<ProposalDecide proposal={PROPOSAL()} onDone={onDone} />);
    await user.click(screen.getByRole('button', { name: 'Huỷ' }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith('cancelled'));
  });

  it.each([404, 410])('%i ⇒ "Đề xuất đã hết hạn — hỏi Gen lại" kèm Chi tiết kỹ thuật, hết nút, danh sách tải lại', async (status) => {
    mockFetch((c) => (c.url.endsWith('/confirm') ? json(status, { status, code: status === 404 ? 'NOT_FOUND' : 'GONE', title: 'Không còn' }) : undefined));
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    const onDone = vi.fn();
    const user = userEvent.setup();
    renderUi(<ProposalDecide proposal={PROPOSAL()} onDone={onDone} />);
    await user.click(screen.getByRole('button', { name: 'Xác nhận' }));
    const err = await screen.findByTestId('owner-action-error');
    expect(err).toHaveTextContent('Đề xuất đã hết hạn — hỏi Gen lại');
    expect(within(err).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(onDone).toHaveBeenCalledWith('expired');
    expect(screen.queryByRole('button', { name: 'Xác nhận' })).toBeNull();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['owner'] });
    expect(document.body.textContent).not.toContain('[object Object]');
  });

  it('đề xuất cần mã PIN: nhắc trước, 423 ⇒ client hỏi PIN rồi gửi lại', async () => {
    let pinOk = false;
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/confirm')) return pinOk ? json(200, { id: 'prop-2', status: 'confirmed' }) : json(423, { status: 423, code: 'PIN_REQUIRED', title: 'Cần mã PIN' });
      if (c.url.endsWith('/auth/pin/verify')) {
        pinOk = true;
        return json(200, { pin_verified_until: new Date(Date.now() + 1800_000).toISOString() });
      }
      return undefined;
    });
    const onDone = vi.fn();
    const user = userEvent.setup();
    renderUi(<ProposalDecide proposal={PROPOSAL({ id: 'prop-2', type: 'kho_create', type_label: 'Ghi vào Kho dữ liệu (tạo mới)', needs_pin: true })} onDone={onDone} />);
    expect(screen.getByText(/Cần mã PIN của Sếp/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Xác nhận' }));
    await screen.findByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    await user.keyboard('246810');
    await waitFor(() => expect(onDone).toHaveBeenCalledWith('confirmed'));
    expect(calls.filter((c) => c.url.endsWith('/confirm'))).toHaveLength(2);
  });

  it('lỗi khác ⇒ câu thân thiện + Chi tiết kỹ thuật, vẫn bấm lại được', async () => {
    mockFetch((c) => (c.url.endsWith('/confirm') ? json(500, { status: 500, code: 'INTERNAL', title: 'Máy chủ gặp lỗi', error_id: 'ERR-9' }) : undefined));
    const user = userEvent.setup();
    renderUi(<ProposalDecide proposal={PROPOSAL()} />);
    await user.click(screen.getByRole('button', { name: 'Xác nhận' }));
    const err = await screen.findByTestId('owner-action-error');
    expect(err).toHaveTextContent('Máy chủ gặp lỗi');
    expect(err).toHaveTextContent('ERR-9');
    expect(screen.getByRole('button', { name: 'Xác nhận' })).toBeEnabled();
  });

  it('API trả object ở trường chuỗi ⇒ không render object', () => {
    renderUi(<ProposalDecide proposal={PROPOSAL({ summary: { x: 1 } as never, type_label: { y: 2 } as never, expires_at: { z: 3 } as never })} />);
    expect(document.body.textContent).not.toContain('[object Object]');
  });
});

// ── hook ghi ────────────────────────────────────────────────────────────────────────────────────────────────────

describe('hook ghi tại chỗ', () => {
  it('useReplyToItem tạo NHÁP và trả draft_id (cả hai cách gọi); không có nháp ⇒ null', async () => {
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/inbox/fd-3/act')) return json(200, { ok: true, draft: { id: 'draft-9', code: 'ACT-0240', status: 'pending' } });
      if (c.url.endsWith('/inbox/fd-4/act')) return json(200, { ok: true });
      return undefined;
    });
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    const bound = renderHook(() => useReplyToItem('fd-3'), { wrapper });
    await expect(bound.result.current.mutateAsync('Dạ em gửi báo giá ạ')).resolves.toEqual({ draft_id: 'draft-9' });
    expect(calls.find((c) => c.url.endsWith('/fd-3/act'))).toMatchObject({ method: 'POST', body: { text: 'Dạ em gửi báo giá ạ' } });
    const loose = renderHook(() => useReplyToItem(), { wrapper });
    await expect(loose.result.current.mutateAsync({ itemId: 'fd-4', text: 'Dạ vâng' })).resolves.toEqual({ draft_id: null });
    await expect(loose.result.current.mutateAsync({ text: 'thiếu mã' })).rejects.toThrow();
    for (const [root] of ACTION_INVALIDATE_KEYS.map((k) => k)) expect(invalidate).toHaveBeenCalledWith({ queryKey: [root] });
  });

  it('useTaskDone / useTaskPostpone / useCreateReminder gọi đúng PATCH/POST', async () => {
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/tasks/t-1') && c.method === 'PATCH') return json(200, { id: 't-1' });
      if (c.url.endsWith('/tasks') && c.method === 'POST') return json(201, { id: 't-2' });
      return undefined;
    });
    const done = renderHook(() => useTaskDone(), { wrapper });
    await done.result.current.mutateAsync('t-1');
    expect(calls.at(-1)).toMatchObject({ method: 'PATCH', body: { status: 'done' } });
    const later = renderHook(() => useTaskPostpone(), { wrapper });
    await later.result.current.mutateAsync({ id: 't-1' });
    const due = (calls.at(-1)?.body as { due_at: string }).due_at;
    expect(Date.parse(due)).toBeGreaterThan(Date.now());
    expect(new Date(due).getHours()).toBe(9);
    await later.result.current.mutateAsync({ id: 't-1', due_at: '2026-12-01T01:00:00.000Z' });
    expect(calls.at(-1)?.body).toEqual({ due_at: '2026-12-01T01:00:00.000Z' });
    const mk = renderHook(() => useCreateReminder(), { wrapper });
    await mk.result.current.mutateAsync({ title: 'Gọi lại chị Lan', subject: { type: 'person', id: 'p-lan' }, due_at: '2026-12-01T02:00:00.000Z' });
    expect(calls.at(-1)).toMatchObject({
      method: 'POST',
      body: { title: 'Gọi lại chị Lan', subject: { type: 'person', id: 'p-lan' }, due_at: '2026-12-01T02:00:00.000Z', remind_at: '2026-12-01T02:00:00.000Z' },
    });
    await mk.result.current.mutateAsync({ title: 'Không gắn người', due_at: '2026-12-01T02:00:00.000Z' });
    expect(calls.at(-1)?.body).not.toHaveProperty('subject');
  });

  it('useAckItem / useSilenceItem / useIntroduceMatch / useRejectMatch gọi đúng đường và tải lại', async () => {
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/introduce')) return json(200, { ok: true, opportunity_id: 'o1', draft: { id: 'draft-m1', code: 'ACT-1', status: 'pending', outcome: 'held', hold_reason: null } });
      return c.method === 'POST' ? json(200, { ok: true, status: 'acknowledged' }) : undefined;
    });
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    const ack = renderHook(() => useAckItem('fd-2'), { wrapper });
    await ack.result.current.mutateAsync(undefined);
    expect(calls.at(-1)).toMatchObject({ method: 'POST', url: expect.stringContaining('/inbox/fd-2/act'), body: {} });
    await ack.result.current.mutateAsync({ create_task: true });
    expect(calls.at(-1)?.body).toEqual({ create_task: true });
    const quiet = renderHook(() => useSilenceItem(), { wrapper });
    await quiet.result.current.mutateAsync({ itemId: 'fd-9', reason: 'Tin xã giao' });
    expect(calls.at(-1)).toMatchObject({ url: expect.stringContaining('/inbox/fd-9/silence'), body: { reason: 'Tin xã giao', until: null } });
    const intro = renderHook(() => useIntroduceMatch(), { wrapper });
    const r = await intro.result.current.mutateAsync('match-1');
    expect(r.draft.id).toBe('draft-m1');                                         // chỉ tạo NHÁP chờ duyệt
    expect(calls.at(-1)?.url).toContain('/matches/match-1/introduce');
    const rej = renderHook(() => useRejectMatch(), { wrapper });
    await rej.result.current.mutateAsync('match-2');
    expect(calls.at(-1)?.url).toContain('/matches/match-2/reject');
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['owner'] });
  });

  it('mọi khoá của Mặt tiền bắt đầu bằng ["owner", …] để một lần tải lại phủ hết', () => {
    const keys = [OWNER_KEYS.today, OWNER_KEYS.tasks, OWNER_KEYS.brief(), OWNER_KEYS.brief('b1'), OWNER_KEYS.feed('all', 30), OWNER_KEYS.feedContext('f1'), OWNER_KEYS.relations('care'), OWNER_KEYS.insights('week', 'messages_in'), OWNER_KEYS.proposals];
    for (const k of keys) expect(k[0]).toBe('owner');
    expect(ACTION_INVALIDATE_KEYS.map((k) => k[0])).toEqual(['owner', 'drafts', 'queue', 'market']);
  });

  it('ACTION_INVALIDATE_KEYS: mỗi khoá là gốc của một khoá truy vấn THẬT (không còn khoá chết "inbox"/"tasks"), và có "market"', () => {
    // khoá thật của từng cụm: Mặt tiền, Bàn làm việc (nháp), hàng đợi (hộp thư / việc / lời hứa), cơ hội & thị trường
    const real = [
      OWNER_KEYS.today, qk3.draftList('pending', undefined, true), qkQueue.inbox('all'), qkQueue.tasks({ status: 'todo' }), qkQueue.promises('overdue'),
      qkMarket.matches({}), qkMarket.opportunities({}), qkMarket.pipeline,
    ];
    const roots = new Set<string>(real.map((k) => k[0]));
    for (const k of ACTION_INVALIDATE_KEYS) expect(roots.has(k[0]), `khoá chết: ${k.join('/')}`).toBe(true);
    const roots2: readonly string[] = ACTION_INVALIDATE_KEYS.map((k) => k[0]);
    for (const root of ['inbox', 'tasks']) expect(roots2).not.toContain(root);
    expect(ACTION_INVALIDATE_KEYS.map((k) => k[0])).toContain('market');          // ghép cung–cầu / giới thiệu đổi bảng thị trường
  });

  it('invalidateAfterWrite: đánh dấu CŨ mọi truy vấn Mặt tiền, nháp, hàng đợi (hộp thư / việc / lời hứa) và thị trường; không đụng cụm khác', () => {
    const qc = new QueryClient();
    const touched = [
      OWNER_KEYS.today, OWNER_KEYS.tasks, qk3.draftList('pending'), qkQueue.inbox('all'), qkQueue.tasks({ status: 'todo' }), qkQueue.promises('overdue'),
      qkMarket.matches({}), qkMarket.opportunities({}),
    ];
    const untouched = [['system', 'about'], ['graph', 'people']] as const;
    for (const k of [...touched, ...untouched]) qc.setQueryData([...k], 1);
    invalidateAfterWrite(qc);
    for (const k of touched) expect(qc.getQueryState([...k])?.isInvalidated, k.join('/')).toBe(true);
    for (const k of untouched) expect(qc.getQueryState([...k])?.isInvalidated, k.join('/')).toBe(false);
  });

  it('tomorrowMorning / expiresText là hàm thuần đúng nghĩa', () => {
    const now = new Date(2026, 9, 11, 21, 30);
    const t = new Date(tomorrowMorning(now));
    expect([t.getFullYear(), t.getMonth(), t.getDate(), t.getHours(), t.getMinutes()]).toEqual([2026, 9, 12, 9, 0]);
    const at = Date.parse('2026-10-11T12:00:00Z');
    expect(expiresText('2026-10-12T12:00:00Z', at)).toBe('Hết hạn sau 24 giờ');
    expect(expiresText('2026-10-11T12:40:00Z', at)).toBe('Sắp hết hạn — còn 40 phút');
    expect(expiresText('2026-10-11T11:00:00Z', at)).toBe('Đã hết hạn');
    expect(expiresText({ x: 1 }, at)).toBe('');
    expect(expiresText('không phải giờ', at)).toBe('');
  });
});

// ── hook đọc ────────────────────────────────────────────────────────────────────────────────────────────────────

describe('hook đọc của Mặt tiền (queries.ts)', () => {
  const feedPage = (offset: number | null, ids: string[]) => ({ items: ids.map((id) => ({ id })), next_offset: offset, counts: {}, filtered_7d: {}, channels_connected: 1 });

  it('useOwnerFeed phân trang theo offset; useOwnerFeedContext chỉ gọi khi có id; useOwnerBrief/Insights/Relations/Proposals gọi đúng tham số', async () => {
    queryClient.setQueryData(qk.me, ME);
    const calls = mockFetch((c) => {
      const u = new URL(c.url, 'http://x');
      if (u.pathname.endsWith('/owner/feed') && u.searchParams.get('offset') === '0') return json(200, feedPage(2, ['a', 'b']));
      if (u.pathname.endsWith('/owner/feed') && u.searchParams.get('offset') === '2') return json(200, feedPage(null, ['c']));
      if (u.pathname.endsWith('/context')) return json(200, { center_event_id: null, messages: [] });
      if (u.pathname.endsWith('/owner/brief') || u.pathname.endsWith('/owner/insights') || u.pathname.endsWith('/owner/proposals') || u.pathname.endsWith('/owner/relations')) return json(200, { items: [] });
      return undefined;
    });
    const feed = renderHook(() => useOwnerFeed('all', 2), { wrapper });
    await waitFor(() => expect(feed.result.current.data?.pages).toHaveLength(1));
    expect(feed.result.current.hasNextPage).toBe(true);
    await feed.result.current.fetchNextPage();
    await waitFor(() => expect(feed.result.current.data?.pages).toHaveLength(2));
    expect(feed.result.current.hasNextPage).toBe(false);
    const feedCalls = calls.filter((c) => c.url.includes('/owner/feed'));
    expect(feedCalls.map((c) => new URL(c.url, 'http://x').search)).toEqual(['?view=all&offset=0&limit=2', '?view=all&offset=2&limit=2']);

    const none = renderHook(() => useOwnerFeedContext(null), { wrapper });
    expect(none.result.current.fetchStatus).toBe('idle');
    const ctx = renderHook(() => useOwnerFeedContext('fd 1'), { wrapper });
    await waitFor(() => expect(ctx.result.current.isSuccess).toBe(true));
    expect(calls.some((c) => c.url.endsWith('/owner/feed/fd%201/context'))).toBe(true);

    renderHook(() => useOwnerBrief('brief-2'), { wrapper });
    renderHook(() => useOwnerInsights('month', ['messages_in', 'unanswered']), { wrapper });
    renderHook(() => useOwnerRelations('care', 5), { wrapper });
    renderHook(() => useOwnerProposals(), { wrapper });
    await waitFor(() => expect(calls.some((c) => c.url.includes('/owner/proposals'))).toBe(true));
    await waitFor(() => expect(calls.some((c) => c.url.includes('/owner/relations'))).toBe(true));
    const urls = calls.map((c) => c.url);
    expect(urls).toContain('/api/v1/owner/brief?id=brief-2');
    expect(urls).toContain('/api/v1/owner/insights?range=month&only=messages_in%2Cunanswered');
    expect(urls).toContain('/api/v1/owner/relations?list=care&limit=20'); // care luôn tải MỘT khoá (20), nơi cần 5 cắt ở máy khách
    expect(INSIGHTS_REFRESH_MS).toBe(10 * 60_000);
  });

  it('vai trò khác Owner không gọi /owner/* (máy chủ sẽ trả 403)', async () => {
    queryClient.setQueryData(qk.me, { ...ME, role: { code: 'operator', name: 'operator' } });
    const calls = mockFetch(() => undefined);
    const r = renderHook(() => useOwnerInsights('week', ['messages_in']), { wrapper });
    expect(r.result.current.fetchStatus).toBe('idle');
    expect(calls.some((c) => c.url.includes('/owner/'))).toBe(false);
  });
});

// ── mock Mặt tiền ────────────────────────────────────────────────────────────────────────────────────────────────

interface Reply {
  status: number;
  body: unknown;
}
function callMock(m: ReturnType<typeof createOwnerMock>, url: string, over: { role?: string; method?: string; body?: Record<string, unknown>; needPin?: boolean } = {}): Reply {
  const u = new URL(url, 'http://x');
  let out: Reply | null = null;
  const role = over.role ?? 'owner';
  const ctx = {
    method: over.method ?? 'GET', path: u.pathname.replace(/^\/api\/v1/, ''), url: u, body: over.body ?? {}, perms: {},
    reply: (status: number, body?: unknown) => { out = { status, body }; return true; },
    problem: (status: number, code: string, title: string, extra?: Record<string, unknown>) => { out = { status, body: { status, code, title, ...extra } }; return true; },
    text: () => true, needPin: () => over.needPin ?? false, userLabel: 'Anh Chủ', owner: role === 'owner', role,
  } as unknown as P2Ctx;
  if (!m.handle(ctx)) return { status: -1, body: null };
  return out as unknown as Reply;
}

describe('mock-owner (Mặt tiền v2)', () => {
  const pendingDrafts = (): DraftDetail[] => [DRAFT({ id: 'draft-a', title: 'Nháp A' }), DRAFT({ id: 'draft-b', title: 'Nháp B' })];

  it('7 GET chỉ Owner (vai khác 403), kèm hình dạng đúng hợp đồng', () => {
    const m = createOwnerMock({ fresh: false, emit: () => undefined, drafts: pendingDrafts });
    for (const p of ['brief', 'today', 'feed', 'feed/fd-1/context', 'relations?list=care', 'insights', 'proposals']) {
      expect(callMock(m, `/api/v1/owner/${p}`, { role: 'operator' }).status, p).toBe(403);
      expect(callMock(m, `/api/v1/owner/${p}`).status, p).toBe(200);
    }
    expect(callMock(m, '/api/v1/owner/feed?view=bogus').status).toBe(422);
    expect(callMock(m, '/api/v1/owner/relations?list=zzz').status).toBe(422);
    expect(callMock(m, '/api/v1/owner/insights?range=year').status).toBe(422);
    expect(callMock(m, '/api/v1/owner/insights?only=khong_co').status).toBe(422);
    expect(callMock(m, '/api/v1/owner/feed/khong-co/context').status).toBe(404);
    expect(callMock(m, '/api/v1/owner/brief?id=khong-co').status).toBe(404);
    expect(callMock(m, '/api/v1/owner/brief', { method: 'POST' }).status).toBe(405);
  });

  it('đủ dữ liệu: nháp từ mock core, đề xuất, feed đủ 3 mức, hội thoại 10 tin, care có người chưa phân loại', () => {
    const m = createOwnerMock({ fresh: false, emit: () => undefined, drafts: pendingDrafts });
    const brief = callMock(m, '/api/v1/owner/brief').body as OwnerBrief;
    expect(brief.channels_connected).toBe(1);
    expect(brief.ai_ready).toBe(true);
    expect(brief.latest?.summary).toBeTruthy();
    expect(brief.live?.sections.find((s) => s.key === 'drafts_pending')).toMatchObject({ count: 2, group: 'decide', to: '/owner/viec' });
    expect(brief.latest?.sections.map((s) => s.key)).toEqual(expect.arrayContaining(['calendar_today', 'mail_reply', 'gtasks_open']));
    expect(brief.live?.sections.map((s) => s.key)).not.toContain('calendar_today');         // Gen-hub chỉ ở bản đã lưu
    expect(brief.history.length).toBeGreaterThan(1);
    expect(brief.alerts.length).toBeLessThanOrEqual(5);
    expect(Date.parse(brief.next_at)).toBeGreaterThan(Date.now());
    expect((callMock(m, '/api/v1/owner/brief?id=brief-2').body as OwnerBrief).latest?.id).toBe('brief-2');

    const feed = callMock(m, '/api/v1/owner/feed?view=all').body as OwnerFeed;
    expect(new Set(feed.items.map((i) => i.importance))).toEqual(new Set(['Khẩn', 'Quan trọng', 'Thường']));
    expect(feed.counts).toMatchObject({ all: feed.items.length, urgent_open: 2, approval: 2 });
    expect(feed.filtered_7d.filtered).toBeGreaterThan(0);
    expect(feed.items.filter((i) => i.item_type === 'draft').map((i) => i.id).sort()).toEqual(['draft-a', 'draft-b']);
    const ranks = feed.items.map((i) => ['Khẩn', 'Quan trọng', 'Thường'].indexOf(i.importance));
    expect(ranks).toEqual([...ranks].sort((a, b) => a - b));
    const page = callMock(m, '/api/v1/owner/feed?view=all&limit=3').body as OwnerFeed;
    expect(page.items).toHaveLength(3);
    expect(page.next_offset).toBe(3);
    expect((callMock(m, '/api/v1/owner/feed?view=all&limit=3&offset=3').body as OwnerFeed).items[0].id).toBe(feed.items[3].id);

    const firstUrgent = feed.items.find((i) => i.importance === 'Khẩn' && i.item_type === 'unit');
    const ctx = callMock(m, `/api/v1/owner/feed/${firstUrgent?.id}/context`).body as { center_event_id: string; messages: Array<{ is_center: boolean; text: string }> };
    expect(ctx.messages).toHaveLength(10);
    expect(ctx.messages.filter((x) => x.is_center)).toHaveLength(1);
    expect(ctx.messages.every((x) => x.text.length <= 280)).toBe(true);
    expect((callMock(m, '/api/v1/owner/feed/fd-3/context').body as { messages: unknown[] }).messages).toEqual([]);

    const care = (callMock(m, '/api/v1/owner/relations?list=care').body as OwnerRelations).items;
    expect(care.some((r) => r.subtitle === 'Chưa phân loại')).toBe(true);
    expect(care.map((r) => r.reason)).toEqual(expect.arrayContaining(['churn', 'silent']));
    expect(care.every((r) => r.person_id && typeof r.last_contact_days === 'number')).toBe(true);
    const matches = (callMock(m, '/api/v1/owner/relations?list=matches').body as OwnerRelations).items;
    expect(matches.every((r) => !!r.match_id)).toBe(true);
    expect((callMock(m, '/api/v1/owner/relations?list=hot').body as OwnerRelations).items).toHaveLength(3);
    expect((callMock(m, '/api/v1/owner/relations?list=bridges').body as OwnerRelations).items.length).toBeGreaterThan(0);

    const props = (callMock(m, '/api/v1/owner/proposals').body as { items: OwnerProposal[] }).items;
    expect(props).toHaveLength(2);
    expect(props.filter((p) => p.needs_pin)).toHaveLength(1);

    const today = callMock(m, '/api/v1/owner/today').body as OwnerToday;
    expect(today.nav).toEqual({ urgent_open: feed.counts.urgent_open, decide: 2 + 2 });
  });

  it('insights: 9 chỉ số tuần + tháng, breakdown, chi phí có lượt chưa đặt giá; only bỏ breakdown', () => {
    const m = createOwnerMock({ fresh: false, emit: () => undefined });
    for (const [range, n] of [['week', 7], ['month', 30]] as const) {
      const d = callMock(m, `/api/v1/owner/insights?range=${range}`).body as OwnerInsights;
      expect(d.state).toBe('ok');
      expect(d.metrics.map((x) => x.key)).toEqual(['messages_in', 'signals', 'new_contacts', 'opps_new', 'opps_won', 'reply_fast_pct', 'unanswered', 'ai_cost_vnd', 'filtered']);
      for (const x of d.metrics.filter((y) => y.key !== 'filtered')) {
        expect(x.series).toHaveLength(n);
        expect(x.prev_series).toHaveLength(n);
      }
      expect(d.breakdown?.event_types?.length).toBeGreaterThan(0);
      expect(d.breakdown?.funnel?.length).toBeGreaterThan(0);
      expect(d.ai_cost.unpriced_calls).toBeGreaterThan(0);
      expect(d.metrics.find((x) => x.key === 'unanswered')?.good).toBe('down');
      expect(d.feedback === null).toBe(range === 'month');
    }
    const only = callMock(m, '/api/v1/owner/insights?range=week&only=messages_in').body as OwnerInsights;
    expect(only.metrics.map((x) => x.key)).toEqual(['messages_in']);
    expect(only.breakdown).toBeNull();
  });

  it('chế độ dữ liệu: nochannel ⇒ channels_connected=0 ở brief/feed/insights; empty ⇒ insights rỗng; collecting ⇒ còn 5 ngày', () => {
    const m = createOwnerMock({ fresh: false, emit: () => undefined, drafts: pendingDrafts, ownerData: 'nochannel' });
    const ch = () => [(callMock(m, '/api/v1/owner/brief').body as OwnerBrief).channels_connected, (callMock(m, '/api/v1/owner/feed').body as OwnerFeed).channels_connected, (callMock(m, '/api/v1/owner/insights').body as OwnerInsights).channels_connected];
    expect(ch()).toEqual([0, 0, 0]);
    // ngày đầu vẫn có "Việc phải quyết" (nháp) nhưng không có tin / số liệu
    expect((callMock(m, '/api/v1/owner/brief').body as OwnerBrief).latest).toBeNull();
    expect((callMock(m, '/api/v1/owner/feed?view=all').body as OwnerFeed).items.every((i) => i.item_type === 'draft')).toBe(true);
    expect((callMock(m, '/api/v1/owner/insights').body as OwnerInsights).state).toBe('empty');
    expect((m.hooks.data as (b: unknown) => unknown)({ mode: 'full' })).toEqual({ mode: 'full' });
    expect(ch()).toEqual([1, 1, 1]);
    (m.hooks.data as (b: unknown) => unknown)({ mode: 'empty' });
    const empty = callMock(m, '/api/v1/owner/insights').body as OwnerInsights;
    expect(empty.state).toBe('empty');
    expect(empty.metrics.every((x) => x.total === 0 && x.delta_pct === null)).toBe(true);
    expect((callMock(m, '/api/v1/owner/feed').body as OwnerFeed).counts.all).toBe(0);
    (m.hooks.data as (b: unknown) => unknown)({ mode: 'collecting' });
    const col = callMock(m, '/api/v1/owner/insights').body as OwnerInsights;
    expect(col).toMatchObject({ state: 'collecting', days_until_compare: 5 });
    expect(col.metrics.every((x) => x.delta_pct === null)).toBe(true);
    (m.hooks.data as (b: unknown) => unknown)({ mode: 'không-có' });                   // lạ ⇒ về đủ dữ liệu
    expect((m.hooks.state as () => { data: string })().data).toBe('full');
    m.hooks.reset?.();
    expect((m.hooks.state as () => { data: string })().data).toBe('nochannel');         // reset về chế độ ban đầu
    expect(parseOwnerDataMode('empty')).toBe('empty');
    expect(parseOwnerDataMode(undefined)).toBe('full');
  });

  it('nháp lấy từ mock core: duyệt xong thì biến khỏi brief / feed / nav', () => {
    let list = pendingDrafts();
    const m = createOwnerMock({ fresh: false, emit: () => undefined, drafts: () => list });
    const nav = () => (callMock(m, '/api/v1/owner/today').body as OwnerToday).nav;
    expect(nav().decide).toBe(2 + 2);
    list = list.map((d) => (d.id === 'draft-a' ? { ...d, status: 'sent' } : d));
    expect(nav().decide).toBe(1 + 2);
    const live = (callMock(m, '/api/v1/owner/brief').body as OwnerBrief).live?.sections.find((s) => s.key === 'drafts_pending');
    expect(live?.count).toBe(1);
    const feedDrafts = (callMock(m, '/api/v1/owner/feed?view=approval').body as OwnerFeed).items;
    expect(feedDrafts.map((i) => i.id)).toEqual(['draft-b']);
  });

  it('Trả lời một tin tạo NHÁP thật ở mock core; đề xuất kho_* cần PIN và biến mất sau khi xử lý; mã lạ nhường mock khác', () => {
    const pushed: DraftDetail[] = [];
    const m = createOwnerMock({ fresh: false, emit: () => undefined, drafts: () => pushed, pushDraft: (d) => pushed.push(d) });
    const reply = callMock(m, '/api/v1/inbox/fd-3/act', { method: 'POST', body: { text: 'Dạ em gửi báo giá ạ' } });
    expect(reply.status).toBe(200);
    const draftId = (reply.body as { draft: { id: string } }).draft.id;
    expect(pushed).toHaveLength(1);
    expect(pushed[0]).toMatchObject({ id: draftId, status: 'pending', text: 'Dạ em gửi báo giá ạ' });
    expect((callMock(m, '/api/v1/owner/feed?view=approval').body as OwnerFeed).items.map((i) => i.id)).toEqual([draftId]);
    expect(callMock(m, '/api/v1/inbox/fd-3/act', { method: 'POST', body: { text: '  ' } }).status).toBe(422);
    expect(callMock(m, '/api/v1/inbox/fd-2/act', { method: 'POST' }).status).toBe(200);         // cảnh báo: ghi nhận
    expect(callMock(m, '/api/v1/inbox/fd-2/act', { method: 'POST' }).status).toBe(409);
    expect(callMock(m, '/api/v1/inbox/fd-9/silence', { method: 'POST', body: {} }).status).toBe(200);
    expect(callMock(m, '/api/v1/inbox/fd-3').status).toBe(200);
    expect((callMock(m, '/api/v1/owner/feed?view=all').body as OwnerFeed).items.map((i) => i.id)).not.toContain('fd-9');
    expect(callMock(m, '/api/v1/inbox/iq-opp-1/act', { method: 'POST' }).status).toBe(-1);       // không phải mã của mock này

    expect(callMock(m, '/api/v1/gen/proposals/prop-2/confirm', { method: 'POST', needPin: true })).toMatchObject({ status: 423, body: { code: 'PIN_REQUIRED' } });
    expect(callMock(m, '/api/v1/gen/proposals/prop-2/confirm', { method: 'POST' }).status).toBe(200);
    expect(callMock(m, '/api/v1/gen/proposals/prop-2/confirm', { method: 'POST' }).status).toBe(404);   // đã xử lý ⇒ hết hạn
    expect(callMock(m, '/api/v1/gen/proposals/prop-1/cancel', { method: 'POST' }).status).toBe(200);
    expect((callMock(m, '/api/v1/owner/proposals').body as { items: unknown[] }).items).toEqual([]);
    expect(callMock(m, '/api/v1/gen/proposals/other/confirm', { method: 'POST' }).status).toBe(-1);
    expect(callMock(m, '/api/v1/inbox/fd-3/act', { method: 'POST', role: 'operator' }).status).toBe(-1);
  });

  it('kịch bản rỗng / lỗi theo từng đường vẫn chạy cho đường mới', () => {
    const m = createOwnerMock({ fresh: false, emit: () => undefined, drafts: pendingDrafts });
    (m.hooks.scenario as (b: unknown) => unknown)({ mode: 'error', only: 'feed' });
    expect(callMock(m, '/api/v1/owner/feed').status).toBe(500);
    expect(callMock(m, '/api/v1/owner/insights').status).toBe(200);
    (m.hooks.scenario as (b: unknown) => unknown)({ mode: 'empty', only: 'proposals' });
    expect(callMock(m, '/api/v1/owner/proposals').body).toEqual({ items: [] });
    (m.hooks.scenario as (b: unknown) => unknown)({ mode: 'empty' });
    expect(callMock(m, '/api/v1/owner/today').body).toMatchObject({ nav: { urgent_open: 0, decide: 0 } });
  });
});

// ── nối vào mock-api: biến môi trường, hook p3, reset ────────────────────────────────────────────────────────────

describe('createMockApi: ownerData + __mock/p3/owner/data + nháp thật', () => {
  let server: Server | null = null;
  let port = 0;
  const jar = new Map<string, string>();

  async function start(ownerData?: 'empty' | 'collecting' | 'nochannel') {
    const mock = createMockApi({ simulate: false, ownerData });
    server = createServer((req, res) => {
      void mock.middleware(req, res, () => {
        res.statusCode = 404;
        res.end();
      });
    });
    await new Promise<void>((resolve) => server!.listen(0, '127.0.0.1', resolve));
    port = (server.address() as AddressInfo).port;
  }

  function call(method: string, path: string, body?: unknown): Promise<{ status: number; body: unknown }> {
    return new Promise((resolve, reject) => {
      const payload = body === undefined ? undefined : JSON.stringify(body);
      const req = httpRequest(
        {
          host: '127.0.0.1', port, path, method,
          headers: {
            'Content-Type': 'application/json',
            Cookie: [...jar].map(([k, v]) => `${k}=${v}`).join('; '),
            ...(jar.has('gh_csrf') ? { 'X-CSRF-Token': jar.get('gh_csrf')! } : {}),
            ...(payload ? { 'Content-Length': Buffer.byteLength(payload) } : {}),
          },
        },
        (res) => {
          for (const c of res.headers['set-cookie'] ?? []) {
            const [kv] = c.split(';');
            const i = kv.indexOf('=');
            const v = kv.slice(i + 1);
            if (v === '') jar.delete(kv.slice(0, i));
            else jar.set(kv.slice(0, i), v);
          }
          let text = '';
          res.on('data', (d) => (text += d));
          res.on('end', () => resolve({ status: res.statusCode ?? 0, body: text ? JSON.parse(text) : null }));
        },
      );
      req.on('error', reject);
      if (payload) req.write(payload);
      req.end();
    });
  }

  async function loginOwner() {
    jar.clear();
    await call('GET', '/api/v1/health');
    const r = await call('POST', '/api/v1/auth/login', { email: 'owner@genesis.local', password: 'matkhau-rat-dai-2026' });
    expect(r.status).toBe(200);
  }

  afterEach(async () => {
    await new Promise<void>((resolve) => (server ? server.close(() => resolve()) : resolve()));
    server = null;
  });

  it('MOCK_OWNER_DATA=nochannel (ownerData) ⇒ channels_connected=0; hook owner/data đổi chế độ; reset dựng lại theo biến môi trường', async () => {
    await start('nochannel');
    await loginOwner();
    const ch = async () => ({
      brief: ((await call('GET', '/api/v1/owner/brief')).body as OwnerBrief).channels_connected,
      feed: ((await call('GET', '/api/v1/owner/feed')).body as OwnerFeed).channels_connected,
      insights: ((await call('GET', '/api/v1/owner/insights')).body as OwnerInsights).channels_connected,
    });
    expect(await ch()).toEqual({ brief: 0, feed: 0, insights: 0 });
    expect((await call('POST', '/api/v1/__mock/p3/owner/data', { mode: 'full' })).body).toEqual({ mode: 'full' });
    expect(await ch()).toEqual({ brief: 1, feed: 1, insights: 1 });
    await call('POST', '/api/v1/__mock/p3/owner/data', { mode: 'collecting' });
    expect(((await call('GET', '/api/v1/owner/insights')).body as OwnerInsights)).toMatchObject({ state: 'collecting', days_until_compare: 5 });
    await call('POST', '/api/v1/__mock/p3/owner/data', { mode: 'empty' });
    expect(((await call('GET', '/api/v1/owner/insights')).body as OwnerInsights).state).toBe('empty');
    expect((await call('POST', '/api/v1/__mock/p3/owner/nope', {})).status).toBe(404);
    await call('POST', '/api/v1/__mock/reset', {});                                     // dựng lại theo ownerData ban đầu
    await loginOwner();
    expect(await ch()).toEqual({ brief: 0, feed: 0, insights: 0 });
  });

  it('mặc định đủ dữ liệu; duyệt nháp bằng PIN 246810 ⇒ nháp biến khỏi brief / feed / nav; Trả lời tạo nháp thật', async () => {
    await start();
    await loginOwner();
    const feed0 = (await call('GET', '/api/v1/owner/feed?view=approval')).body as OwnerFeed;
    expect(feed0.counts.approval).toBeGreaterThanOrEqual(2);                              // ≥ 2 nháp chờ duyệt từ mock core
    const nav0 = ((await call('GET', '/api/v1/owner/today')).body as OwnerToday).nav;
    expect(nav0.decide).toBe(feed0.counts.approval + 2);                                  // + 2 đề xuất Gen
    const target = feed0.items.find((i) => i.item_type === 'draft')!;
    expect((await call('POST', `/api/v1/drafts/${target.id}/approve`, {})).status).toBe(423);   // cần mã PIN
    expect((await call('POST', '/api/v1/auth/pin/verify', { pin: '246810' })).status).toBe(200);
    expect((await call('POST', `/api/v1/drafts/${target.id}/approve`, {})).status).toBe(200);
    const feed1 = (await call('GET', '/api/v1/owner/feed?view=approval')).body as OwnerFeed;
    expect(feed1.items.map((i) => i.id)).not.toContain(target.id);
    expect(feed1.counts.approval).toBe(feed0.counts.approval - 1);
    expect(((await call('GET', '/api/v1/owner/today')).body as OwnerToday).nav.decide).toBe(nav0.decide - 1);
    const live = ((await call('GET', '/api/v1/owner/brief')).body as OwnerBrief).live?.sections.find((s) => s.key === 'drafts_pending');
    expect(live?.count).toBe(feed0.counts.approval - 1);

    const reply = await call('POST', '/api/v1/inbox/fd-3/act', { text: 'Dạ em gửi báo giá ạ' });
    expect(reply.status).toBe(200);
    const draftId = (reply.body as { draft: { id: string } }).draft.id;
    const detail = await call('GET', `/api/v1/drafts/${draftId}`);                         // nháp có thật ở mock core ⇒ DraftDecide mở được
    expect(detail.status).toBe(200);
    expect(detail.body).toMatchObject({ id: draftId, status: 'pending', text: 'Dạ em gửi báo giá ạ' });
    expect(((await call('GET', '/api/v1/owner/feed?view=approval')).body as OwnerFeed).items.map((i) => i.id)).toContain(draftId);

    const props = ((await call('GET', '/api/v1/owner/proposals')).body as { items: OwnerProposal[] }).items;
    const pinProp = props.find((p) => p.needs_pin)!;
    expect((await call('POST', '/api/v1/auth/pin/verify', { pin: '246810' })).status).toBe(200);
    expect((await call('POST', `/api/v1/gen/proposals/${pinProp.id}/confirm`, {})).status).toBe(200);
    expect(((await call('GET', '/api/v1/owner/proposals')).body as { items: unknown[] }).items).toHaveLength(1);
  });
});
