import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { ApiError } from '@gen-harness/contracts';
import type { AgentDecision, DraftDetail, DraftItem } from '@gen-harness/contracts';
import { WorkbenchScreen } from '../../src/screens/queue/WorkbenchScreen';
import { AgentsScreen } from '../../src/screens/agents/AgentsScreen';
import { decisionLabel, decisionTone, decisionWhat } from '../../src/screens/agents/agentsModel';
import { ActionError, ModelUnavailableNotice } from '../../src/screens/common';
import { errorText, isModelDown } from '../../src/lib/errorText';
import { MODEL_DOWN_TEXT, MODEL_UNAVAILABLE_TEXT } from '../../src/lib/friendlyError';
import { qk } from '../../src/lib/queries';

// Gói C5 (v0.1.67) — web nói thật: lỗi model đúng loại (F011), Soạn lại có ô yêu cầu + khôi phục bản trước (F081).

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
interface Call { url: string; method: string; body: unknown }
function mockFetch(handler: (c: Call) => Response | Promise<Response>) {
  const calls: Call[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const c = { url: String(url), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(c);
    return handler(c);
  }));
  return calls;
}
const ME = {
  id: 'u', email: 'x@example.vn', display_name: 'Anh Chủ', role: { code: 'owner', name: 'Owner — Sếp' },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' }, addressing: { self: 'Anh', bot_calls_me: 'Sếp' },
  pin_verified_until: null, permissions: { 'action.draft': 'all', 'action.approve': 'all', 'system.read': 'all', 'system.manage': 'all' },
};
const DOWN = new ApiError(503, { code: 'MODEL_UNAVAILABLE', title: 'Nguồn AI đang lỗi hoặc hết hạn mức — thử lại sau ít phút', detail: 'Model đã chọn vẫn còn…', reasons: ['gemini: 429'], model_down: true } as never);
const NONE = new ApiError(503, { code: 'MODEL_UNAVAILABLE', title: 'Chưa có model nào chạy được để dịch', detail: 'Chưa có model AI nào hoạt động', reasons: [], model_down: false } as never);
const wrap = (ui: JSX.Element) => render(<MemoryRouter>{ui}</MemoryRouter>);

describe('F011 — hết hạn mức / mạng ≠ chưa có model', () => {
  it('errorText phân biệt hai tình huống; máy chủ cũ (không cờ) giữ câu cũ', () => {
    expect(isModelDown(DOWN)).toBe(true);
    expect(isModelDown(NONE)).toBe(false);
    expect(errorText(DOWN)).toBe(MODEL_DOWN_TEXT);
    expect(errorText(NONE)).toBe(MODEL_UNAVAILABLE_TEXT);
    expect(errorText(new ApiError(503, { code: 'MODEL_UNAVAILABLE', title: 'x' } as never))).toBe(MODEL_UNAVAILABLE_TEXT);
    expect(MODEL_DOWN_TEXT).toMatch(/hết hạn mức/);
  });

  it('thẻ báo lỗi: nguồn lỗi ⇒ "Xem nguồn AI" (API & Model) + Chi tiết kỹ thuật, KHÔNG có "Chọn model"', () => {
    wrap(<ActionError error={DOWN} />);
    expect(screen.getByText(MODEL_DOWN_TEXT)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Xem nguồn AI/ })).toHaveAttribute('href', '/api');
    expect(screen.queryByRole('link', { name: /Chọn model/ })).toBeNull();
    expect(screen.getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
  });

  it('chưa có model nào ⇒ vẫn "Chọn model"; ModelUnavailableNotice down=true cũng đúng câu', () => {
    const a = wrap(<ActionError error={NONE} />);
    expect(screen.getByRole('link', { name: /Chọn model/ })).toHaveAttribute('href', '/setup?buoc=nguon-ai');
    a.unmount();
    wrap(<ModelUnavailableNotice reasons="x" down />);
    expect(screen.getByRole('link', { name: /Xem nguồn AI/ })).toBeInTheDocument();
  });
});

const item = (o: Partial<DraftItem> & Pick<DraftItem, 'id' | 'status'>): DraftItem => ({
  code: `ACT-${o.id}`, kind: 'message', kind_label: 'Tin nhắn', title: `Tiêu đề ${o.id}`, agent: null, created_by: null,
  created_at: '2026-10-10T08:00:00Z', hold_reason: null, subject: null, ...o,
});
const detail = (o: Partial<DraftDetail> & Pick<DraftDetail, 'id' | 'status'>): DraftDetail => ({
  ...item(o), paragraphs: ['Dạ em gửi anh báo giá ạ.'], text: 'Dạ em gửi anh báo giá ạ.', lang: 'vi',
  target: { channel: 'zalo', thread_type: 'group', group: { id: 'g1', code: 'G-1', name: 'Nhóm khách', channel: 'zalo' }, person: null },
  amount_vnd: null, autonomy_level: 4, flags: { writes_external: true, personnel_related: false, over_threshold: false },
  approve_label: 'Duyệt và gửi qua Zalo', sources: [], context: [], side_actions: [], decision: null, send_result: null,
  versions: [], ...o,
});

describe('F081 — Soạn lại có ô yêu cầu và khôi phục bản trước', () => {
  beforeEach(() => { document.cookie = 'gh_csrf=test-csrf'; });
  afterEach(() => vi.unstubAllGlobals());

  function renderWb(d: DraftDetail) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    qc.setQueryData(qk.me, ME);
    const calls = mockFetch((c) => {
      const u = new URL(c.url, 'http://x');
      if (u.pathname.endsWith('/auth/me')) return json(200, ME);
      if (u.pathname.endsWith(`/drafts/${d.id}/regenerate`)) return json(200, { ...d, text: 'Mới', paragraphs: ['Mới'], versions: [{ at: '2026-10-10T09:00:00Z', by: 'agent', text: d.text }] });
      if (u.pathname.endsWith(`/drafts/${d.id}`)) return json(200, d);
      if (u.pathname.endsWith('/drafts')) return json(200, { items: [item({ id: d.id, status: 'pending' })], next_cursor: null, total: 1 });
      return json(404);
    });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[`/workbench?id=${d.id}`]}>
          <WorkbenchScreen />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    return calls;
  }

  it('gõ yêu cầu rồi bấm Soạn lại ⇒ POST regenerate mang đúng instruction', async () => {
    const calls = renderWb(detail({ id: 'd1', status: 'pending' }));
    const box = await screen.findByLabelText('Yêu cầu khi soạn lại (không bắt buộc)');
    const user = userEvent.setup();
    await user.type(box, 'mềm hơn, ngắn hơn');
    await user.click(screen.getByRole('button', { name: 'Soạn lại' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.includes('/regenerate'))).toBe(true));
    expect(calls.find((c) => c.url.includes('/regenerate'))?.body).toEqual({ instruction: 'mềm hơn, ngắn hơn' });
  });

  it('bỏ trống ô yêu cầu ⇒ instruction null (máy chủ dùng câu mặc định)', async () => {
    const calls = renderWb(detail({ id: 'd1', status: 'pending' }));
    await screen.findByLabelText('Yêu cầu khi soạn lại (không bắt buộc)');
    await userEvent.setup().click(screen.getByRole('button', { name: 'Soạn lại' }));
    await waitFor(() => expect(calls.some((c) => c.url.includes('/regenerate'))).toBe(true));
    expect(calls.find((c) => c.url.includes('/regenerate'))?.body).toEqual({ instruction: null });
  });

  it('có bản cũ ⇒ nút "Khôi phục bản trước" đưa bản gần nhất vào ô sửa; chưa có thì không hiện nút', async () => {
    renderWb(detail({ id: 'd2', status: 'pending', text: 'Bản mới', paragraphs: ['Bản mới'], versions: [{ at: '2026-10-10T08:30:00Z', by: 'agent', text: 'Bản cũ hơn' }] }));
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /Khôi phục bản trước/ }));
    expect(within(document.body).getByLabelText('Sửa nội dung bản nháp')).toHaveValue('Bản cũ hơn');
  });

  it('bản cũ có số đã che (vai dưới Owner) ⇒ KHÔNG hiện "Khôi phục bản trước" (review v0.1.67 P3)', async () => {
    renderWb(detail({ id: 'd4', status: 'pending', text: 'Bản mới', paragraphs: ['Bản mới'], versions: [{ at: '2026-10-10T08:30:00Z', by: 'agent', text: 'STK •••••••678 ạ' }] }));
    await screen.findByLabelText('Yêu cầu khi soạn lại (không bắt buộc)');
    expect(screen.queryByRole('button', { name: /Khôi phục bản trước/ })).toBeNull();
    expect(screen.getByText(/số đã che/)).toBeInTheDocument();
  });

  it('chưa soạn lại lần nào ⇒ không có nút khôi phục', async () => {
    renderWb(detail({ id: 'd3', status: 'pending' }));
    await screen.findByLabelText('Yêu cầu khi soạn lại (không bắt buộc)');
    expect(screen.queryByRole('button', { name: /Khôi phục bản trước/ })).toBeNull();
  });
});

describe('F076 + F075 — bảng "Agent đã nói gì" nói đúng số phận của nháp và cho đọc câu gợi ý', () => {
  const base = { rationale: null, context_refs: [], at: '2026-10-10T08:00:00Z', agent: { id: 'a1', name: 'Trợ lý thương mại' } };
  const trig = { type: 'meaning_unit', id: 'mu', code: 'OPP-1', label: 'Hỏi giá' };
  const D = (o: Partial<AgentDecision> & Pick<AgentDecision, 'id' | 'decision'>): AgentDecision => ({ ...base, trigger: trig, draft: null, ...o });

  it('decisionWhat: thiếu cớ (nhãn/mã/loại) thì bỏ phần sau dấu «·», không hiện "undefined"', () => {
    expect(decisionWhat(D({ id: '1', decision: 'silent' }))).toBe('Im lặng · Hỏi giá');
    const noTrig = { type: undefined, id: 'x', code: undefined, label: undefined } as unknown as AgentDecision['trigger'];
    expect(decisionWhat(D({ id: '2', decision: 'silent', trigger: noTrig }))).toBe('Im lặng');
    expect(decisionWhat(D({ id: '3', decision: 'silent', trigger: { ...noTrig, type: 'meaning_unit' } }))).toBe('Im lặng · meaning_unit');
    const dr = D({ id: '4', decision: 'draft', outcome: 'held', trigger: noTrig, draft: { id: 'x', code: 'ACT-9', status: 'pending' } });
    expect(decisionWhat(dr)).toBe('Soạn sẵn chờ duyệt ACT-9');
    expect(decisionWhat(dr)).not.toMatch(/undefined|·\s*$/);
  });

  it('decisionLabel: send/draft chỉ "Đã gửi" khi nháp thật sự sent', () => {
    const held = D({ id: '1', decision: 'send', outcome: 'held', draft: { id: 'x', code: 'ACT-1', status: 'pending' } });
    const sent = D({ id: '2', decision: 'send', outcome: 'held', draft: { id: 'x', code: 'ACT-1', status: 'sent' } });
    const rejected = D({ id: '3', decision: 'draft', outcome: 'held', draft: { id: 'x', code: 'ACT-1', status: 'rejected' } });
    const failed = D({ id: '4', decision: 'send', outcome: 'held', draft: { id: 'x', code: 'ACT-1', status: 'failed' } });
    expect(decisionLabel(held)).toBe('Soạn sẵn chờ duyệt');
    expect(decisionLabel(sent)).toBe('Đã gửi');
    expect(decisionLabel(rejected)).toBe('Sếp đã huỷ nháp');
    expect(decisionLabel(failed)).toBe('Gửi lỗi');
    expect(decisionLabel(D({ id: '5', decision: 'send' }))).toBe('Soạn nháp');            // máy chủ cũ / không có nháp
    expect(decisionLabel(D({ id: '6', decision: 'send', outcome: 'held', draft: { id: 'x', code: 'ACT-1' } }))).toBe('Soạn sẵn chờ duyệt');
    expect(decisionTone(sent)).toContain('ok');
    expect(decisionTone(held)).not.toContain('ok');
  });

  it('màn Agent: không có dòng "Đã gửi" cho nháp chờ duyệt; câu gợi ý đọc được', async () => {
    const decisions = {
      items: [
        D({ id: 'd1', decision: 'send', outcome: 'held', draft: { id: 'x1', code: 'ACT-0005', status: 'pending' }, proposal_text: 'Dạ em báo giá 3 container ạ.' }),
        D({ id: 'd2', decision: 'suggest', outcome: 'suggested', proposal_text: 'Anh nên gọi khách chiều nay, nhắc lại báo giá hôm qua.' }),
      ],
      next_cursor: null,
      total: 2,
    };
    mockFetch((c) => {
      if (c.url.includes('/agents/decisions')) return json(200, decisions);
      if (c.url.includes('/agents/templates')) return json(200, []);
      if (c.url.endsWith('/agents') && c.method === 'GET') return json(200, []);
      if (c.url.includes('/channels')) return json(200, []);
      return json(404);
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(qk.me, ME);
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <AgentsScreen />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText(/Soạn sẵn chờ duyệt ACT-0005/)).toBeInTheDocument();
    expect(screen.queryByText(/Đã gửi/)).toBeNull();
    const props = await screen.findAllByTestId('agent-proposal');
    expect(props.map((p) => p.textContent)).toEqual([
      'Nội dung soạn: Dạ em báo giá 3 container ạ.',
      'Gợi ý: Anh nên gọi khách chiều nay, nhắc lại báo giá hôm qua.',
    ]);
  });
});
