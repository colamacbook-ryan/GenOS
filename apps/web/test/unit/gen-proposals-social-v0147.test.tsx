/**
 * v0.1.47 (F-79) — thẻ đề xuất GỬI Facebook (trả lời bình luận / nhắn tin) trong khung Gen: tóm tắt tài khoản/đích/nội dung,
 * nhãn "Cần mã PIN" + cảnh báo gửi ngay, Sửa chỉ ô Nội dung, cổng khoá, lỗi thân thiện + "Chi tiết kỹ thuật", theo dõi việc gửi
 * (đang chờ → đang gửi → đã gửi + ảnh chụp / lỗi / đã dừng).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { BrowserJob, GenProposal, GenStepEvent } from '@gen-harness/contracts';
import { GenPanel } from '../../src/gen/GenPanel';
import { changedFields } from '../../src/gen/proposalModel';
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

const TARGET_URL = 'https://www.facebook.com/permalink.php?story_fbid=1&comment_id=2';

const REPLY: GenProposal = {
  id: 'w1',
  type: 'social_reply',
  fields: { account_id: 'a1', target_url: TARGET_URL, text: 'Cảm ơn bạn! Bên em báo giá qua tin nhắn ạ.' },
  summary: 'Trả lời bình luận trên Facebook (Facebook của Sếp) — gửi ngay khi Sếp xác nhận và nhập mã PIN.',
  labels: { account: 'Facebook của Sếp', target: 'Bình luận của chị Lan: "Giá bao nhiêu vậy anh?"', write_gate: 'open' },
  target: 'social.write:a1',
  requires_pin: true,
  status: 'pending',
};
const LOCKED: GenProposal = { ...REPLY, id: 'w2', labels: { ...REPLY.labels, write_gate: 'locked' } } as GenProposal;
const SUSPICIOUS: GenProposal = { ...REPLY, id: 'w3', labels: { ...REPLY.labels, suspicious: '1' } } as GenProposal;
const DM: GenProposal = {
  ...REPLY, id: 'w4', type: 'social_dm',
  fields: { account_id: 'a1', target_url: 'https://www.facebook.com/messages/t/1001/', text: 'Dạ em xác nhận lịch giao ạ.' },
  labels: { account: 'Facebook của Sếp', target: 'Cuộc trò chuyện với Shop Mai', write_gate: 'open' },
} as GenProposal;

type Call = { method: string; url: string; body: unknown };
const calls: Call[] = [];
let confirmReply: { status: number; body: unknown } | null = null;
let gateOpen = false;
let jobReplies: Array<Partial<BrowserJob>> = [];
const navigations: string[] = [];

const baseJob = (over: Partial<BrowserJob>): BrowserJob => ({
  id: 'j1', account_id: 'a1', kind: 'write', action: 'reply_comment', has_proof: false, status: 'queued', via: 'gen', error: null, error_text: null,
  result: { action: 'reply_comment', target_url: TARGET_URL, text: 'x', sent: false, confirmed: false, trace: [] },
  created_at: '2026-10-03T01:00:00Z', started_at: null, finished_at: null, ...over,
});

function stubApi() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ method, url, body });
      const json = (status: number, b: unknown) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
      if (url.endsWith('/social/write-gate')) {
        return json(200, { sandbox: { enabled: false, reason: null, checked_at: null }, worker_online: true, consent: gateOpen ? { accepted_at: '2026-10-03T01:00:00Z', accepted_by_name: 'Sếp', version: 'v' } : null, open: gateOpen, risk: [], version: 'v' });
      }
      const job = url.match(/\/social\/jobs\/([\w-]+)$/);
      if (job && method === 'GET') {
        const next = jobReplies.length > 1 ? jobReplies.shift()! : (jobReplies[0] ?? {});
        return json(200, baseJob(next));
      }
      const m = url.match(/\/gen\/proposals\/(\w+)\/(confirm|cancel)$/);
      if (m && method === 'POST') {
        if (confirmReply) return json(confirmReply.status, confirmReply.body);
        const base = [REPLY, LOCKED, SUSPICIOUS, DM].find((x) => x.id === m[1]) ?? REPLY;
        if (m[2] === 'cancel') return json(200, { ...base, status: 'cancelled' });
        return json(200, {
          ...base, id: m[1], fields: { ...base.fields, ...(body?.fields ?? {}) }, status: 'confirmed',
          result: { type: 'social_write', id: 'j1', screen: 'social', status: 'queued' },
        });
      }
      return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
    }),
  );
}

function showProposal(p: GenProposal) {
  useGenStore.setState({
    messages: [
      { id: 'u-1', role: 'user', text: 'Trả lời bình luận', steps: [] },
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

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(qk.me, ME);
  calls.length = 0;
  navigations.length = 0;
  confirmReply = null;
  gateOpen = false;
  jobReplies = [{ status: 'queued' }];
  useGenStore.setState({ openByUser: {}, conversationId: 'c1', messages: [], busy: false, spotlight: null });
  setNavigator((to) => navigations.push(to));
  stubApi();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('Thẻ đề xuất gửi Facebook (v0.1.47)', () => {
  it('trả lời bình luận: hiện tài khoản / đích / nội dung nguyên văn, nhãn "Cần mã PIN" và câu cảnh báo gửi ngay; chưa ghi gì', () => {
    const { container } = renderPanel();
    showProposal(REPLY);
    const card = cardOf('Trả lời bình luận Facebook');
    expect(card).toHaveTextContent('Đề xuất · Trả lời bình luận Facebook');
    expect(card).toHaveTextContent('Tài khoản');
    expect(card).toHaveTextContent('Facebook của Sếp');
    expect(card).toHaveTextContent('Trả lời vào');
    expect(card).toHaveTextContent('Bình luận của chị Lan: "Giá bao nhiêu vậy anh?"');
    expect(card).toHaveTextContent('Nội dung');
    expect(card).toHaveTextContent('Cảm ơn bạn! Bên em báo giá qua tin nhắn ạ.');
    expect(card).toHaveTextContent('Cần mã PIN');
    expect(card).toHaveTextContent('Bấm Xác nhận là GỬI NGAY lên Facebook của Sếp (cần mã PIN). Hệ thống không tự thu hồi được.');
    expect(within(card).getByRole('button', { name: 'Xác nhận và gửi' })).toBeEnabled();
    expect(within(card).queryByRole('button', { name: 'Đọc cảnh báo & đồng ý' })).toBeNull();
    expect(card.querySelector('[data-icon="chat-circle-text"]')).not.toBeNull();
    expect(writes()).toEqual([]);
    expect(container.textContent).not.toContain('[object Object]');
  });

  it('nhắn tin: tiêu đề + nhãn "Nhắn cho" + biểu tượng máy bay giấy', () => {
    renderPanel();
    showProposal(DM);
    const card = cardOf('Nhắn tin Facebook');
    expect(card).toHaveTextContent('Nhắn cho');
    expect(card).toHaveTextContent('Cuộc trò chuyện với Shop Mai');
    expect(card.querySelector('[data-icon="paper-plane-tilt"]')).not.toBeNull();
  });

  it('Sửa: chỉ có ô Nội dung (tối đa 2000 ký tự); changedFields chỉ gửi text; gửi đúng trường đã sửa', async () => {
    expect(changedFields(REPLY, { text: 'Chào bạn' })).toEqual({ text: 'Chào bạn' });
    expect(changedFields(REPLY, { text: REPLY.fields.text })).toEqual({});
    expect(changedFields(REPLY, { text: 'Chào bạn', account_id: 'zzz', target_url: 'https://evil.example' })).toEqual({ text: 'Chào bạn' });

    renderPanel();
    showProposal(REPLY);
    const card = cardOf('Trả lời bình luận Facebook');
    await userEvent.click(within(card).getByRole('button', { name: 'Sửa' }));
    const text = within(card).getByLabelText('Nội dung');
    expect(text.tagName).toBe('TEXTAREA');
    expect(text).toHaveAttribute('maxlength', '2000');
    expect(card.querySelectorAll('textarea, input, select')).toHaveLength(1);
    await userEvent.clear(text);
    expect(within(card).getByRole('button', { name: 'Xác nhận và gửi' })).toBeDisabled();
    await userEvent.type(text, 'Dạ vâng ạ');
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và gửi' }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0].url).toMatch(/\/gen\/proposals\/w1\/confirm$/);
    expect(writes()[0].body).toEqual({ fields: { text: 'Dạ vâng ạ' } });
  });

  it('cổng khoá: nút Xác nhận vô hiệu + nút "Đọc cảnh báo & đồng ý" mở trang cảnh báo', async () => {
    renderPanel();
    showProposal(LOCKED);
    const card = cardOf('Trả lời bình luận Facebook');
    expect(within(card).getByRole('button', { name: 'Xác nhận và gửi' })).toBeDisabled();
    await userEvent.click(within(card).getByRole('button', { name: 'Đọc cảnh báo & đồng ý' }));
    expect(navigations).toEqual(['/social/ghi-facebook']);
    expect(writes()).toEqual([]);
  });

  it('cổng khoá lúc đề xuất nhưng Sếp vừa đồng ý ở trang cảnh báo → thẻ tự mở lại, Xác nhận dùng được', async () => {
    gateOpen = true;
    renderPanel();
    showProposal(LOCKED);
    const card = cardOf('Trả lời bình luận Facebook');
    await waitFor(() => expect(within(card).getByRole('button', { name: 'Xác nhận và gửi' })).toBeEnabled());
    expect(within(card).queryByRole('button', { name: 'Đọc cảnh báo & đồng ý' })).toBeNull();
  });

  it('dấu hiệu lừa đảo → cảnh báo vàng', () => {
    renderPanel();
    showProposal(SUSPICIOUS);
    const card = cardOf('Trả lời bình luận Facebook');
    expect(within(card).getByTestId('gen-write-suspicious')).toHaveTextContent('Mục này có dấu hiệu lừa đảo — đọc kỹ trước khi trả lời.');
    cleanup();
    renderPanel();
    showProposal(REPLY);
    expect(screen.queryByTestId('gen-write-suspicious')).toBeNull();
  });

  it('xác nhận lỗi 409 SOCIAL_HALTED → câu thân thiện từ API + "Chi tiết kỹ thuật" (mã), không "[object Object]"', async () => {
    confirmReply = { status: 409, body: { status: 409, code: 'SOCIAL_HALTED', title: 'Đang dừng tất cả việc trình duyệt — Owner bấm "Bật lại" trước' } };
    const { container } = renderPanel();
    showProposal(REPLY);
    const card = cardOf('Trả lời bình luận Facebook');
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và gửi' }));
    const alert = await within(card).findByRole('alert');
    expect(alert).toHaveTextContent('Đang dừng tất cả việc trình duyệt');
    expect(within(alert).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(alert).toHaveTextContent('SOCIAL_HALTED');
    expect(container.textContent).not.toContain('[object Object]');
    expect(within(card).getByRole('button', { name: 'Xác nhận và gửi' })).toBeEnabled(); // vẫn chờ, thử lại được
    expect(within(card).queryByRole('button', { name: 'Đọc cảnh báo & đồng ý' })).toBeNull();
  });

  it('SOCIAL_WRITE_LOCKED khi xác nhận → câu thân thiện + nút "Đọc cảnh báo & đồng ý"; 429 SOCIAL_WRITE_LIMIT → câu giới hạn', async () => {
    confirmReply = { status: 409, body: { status: 409, code: 'SOCIAL_WRITE_LOCKED', title: 'Gửi lên Facebook đang khoá: trình duyệt chưa bật được sandbox và Sếp chưa đồng ý rủi ro' } };
    renderPanel();
    showProposal(REPLY);
    const card = cardOf('Trả lời bình luận Facebook');
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và gửi' }));
    await waitFor(() => expect(card).toHaveTextContent('Gửi lên Facebook đang khoá'));
    expect(card).toHaveTextContent('SOCIAL_WRITE_LOCKED');
    await userEvent.click(within(card).getByRole('button', { name: 'Đọc cảnh báo & đồng ý' }));
    expect(navigations).toEqual(['/social/ghi-facebook']);

    confirmReply = { status: 429, body: { status: 429, code: 'SOCIAL_WRITE_LIMIT', title: 'Đã gửi 10 lượt trong 24 giờ (giới hạn để giảm rủi ro khoá tài khoản) — thử lại sau hoặc nâng Giới hạn gửi/ngày ở trang Tài khoản mạng xã hội' } };
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và gửi' }));
    await waitFor(() => expect(card).toHaveTextContent('Đã gửi 10 lượt trong 24 giờ'));
    expect(card).toHaveTextContent('SOCIAL_WRITE_LIMIT');
    expect(within(card).queryByRole('button', { name: 'Đọc cảnh báo & đồng ý' })).toBeNull();
  });

  it('đã xác nhận → theo dõi việc: xong hiện "Đã gửi" + "Xem ảnh chụp" mở ảnh bằng chứng', async () => {
    jobReplies = [{ status: 'done', has_proof: true, finished_at: '2026-10-03T01:01:00Z', result: { sent: true, confirmed: true } }];
    renderPanel();
    showProposal(REPLY);
    const card = cardOf('Trả lời bình luận Facebook');
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và gửi' }));
    const st = await within(card).findByTestId('gen-write-status');
    await waitFor(() => expect(st).toHaveAttribute('data-status', 'done'));
    expect(st).toHaveTextContent('Đã gửi');
    expect(st).not.toHaveTextContent('Đã gửi trước khi kịp dừng');
    expect(card).not.toHaveTextContent('Bấm Xác nhận là GỬI NGAY');
    await userEvent.click(within(st).getByRole('button', { name: 'Xem ảnh chụp' }));
    const img = await screen.findByAltText('Ảnh chụp bằng chứng lần gửi');
    expect(img).toHaveAttribute('src', '/api/v1/social/jobs/j1/proof');
  });

  it('done nhưng after_halt / chưa thấy hiện trên trang → ghi chú tương ứng', async () => {
    jobReplies = [{ status: 'done', has_proof: true, result: { sent: true, confirmed: false, after_halt: true } }];
    renderPanel();
    showProposal(REPLY);
    const card = cardOf('Trả lời bình luận Facebook');
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và gửi' }));
    await waitFor(() => expect(card).toHaveTextContent('Đã gửi trước khi kịp dừng'));
    expect(card).toHaveTextContent('Đã bấm gửi nhưng chưa thấy hiện trên trang — xem ảnh chụp');
  });

  it('done nhưng không có ảnh chụp → câu thân thiện, mã PROOF_MISSING chỉ trong "Chi tiết kỹ thuật"; hết chip "Cần mã PIN"', async () => {
    jobReplies = [{ status: 'done', has_proof: false, result: { sent: true, confirmed: true, proof_error: 'PROOF_MISSING' } }];
    renderPanel();
    showProposal(REPLY);
    const card = cardOf('Trả lời bình luận Facebook');
    expect(card).toHaveTextContent('Cần mã PIN');
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và gửi' }));
    const st = await within(card).findByTestId('gen-write-status');
    await waitFor(() => expect(st).toHaveAttribute('data-status', 'done'));
    expect(st).toHaveTextContent('Đã gửi nhưng không chụp được ảnh bằng chứng — mở Facebook để kiểm tra.');
    expect(st).not.toHaveTextContent('Không có ảnh chụp: PROOF_MISSING');
    expect(within(st).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(within(st).getByText('Mã lỗi PROOF_MISSING').closest('details')).not.toBeNull();
    expect(within(st).queryByRole('button', { name: 'Xem ảnh chụp' })).toBeNull();
    expect(card).not.toHaveTextContent('Cần mã PIN');
  });

  it('queued → "Đang chờ trình duyệt…", running → "Đang gửi trên Facebook…" (hỏi lại mỗi 2 giây)', async () => {
    jobReplies = [{ status: 'queued' }, { status: 'running' }];
    renderPanel();
    showProposal(REPLY);
    const card = cardOf('Trả lời bình luận Facebook');
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và gửi' }));
    await waitFor(() => expect(card).toHaveTextContent('Đang chờ trình duyệt…'));
    await waitFor(() => expect(card).toHaveTextContent('Đang gửi trên Facebook…'), { timeout: 6000 });
    expect(within(card).queryByRole('button', { name: 'Xem ảnh chụp' })).toBeNull();
  }, 15_000);

  it('failed → câu lỗi của việc + "Chi tiết kỹ thuật" (mã); halted → "Đã dừng bằng Dừng tất cả — chưa gửi gì"', async () => {
    jobReplies = [{ status: 'failed', error: 'PERMIT_INVALID', error_text: 'Giấy phép gửi không hợp lệ hoặc đã quá 5 phút — không gửi gì. Hỏi Gen soạn lại để gửi lần nữa.' }];
    renderPanel();
    showProposal(REPLY);
    let card = cardOf('Trả lời bình luận Facebook');
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và gửi' }));
    await waitFor(() => expect(card).toHaveTextContent('Giấy phép gửi không hợp lệ hoặc đã quá 5 phút — không gửi gì.'));
    expect(within(card).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(card).toHaveTextContent('Mã lỗi PERMIT_INVALID');
    // Không còn nút Xác nhận ⇒ chỉ đường làm lại bằng Gen, không bảo "Bấm Xác nhận lại".
    expect(card).toHaveTextContent('Hỏi Gen soạn lại để gửi lần nữa.');
    expect(card.textContent?.match(/Hỏi Gen/g)).toHaveLength(1);              // không lặp lời khuyên hai lần liền
    expect(card).not.toHaveTextContent('Xác nhận lại');
    expect(within(card).queryByRole('button', { name: 'Xác nhận và gửi' })).toBeNull();

    cleanup();
    queryClient.clear();
    queryClient.setQueryData(qk.me, ME);
    jobReplies = [{ status: 'halted', error: 'HALTED' }];
    renderPanel();
    showProposal({ ...REPLY, id: 'w5' } as GenProposal);
    card = cardOf('Trả lời bình luận Facebook');
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và gửi' }));
    await waitFor(() => expect(card).toHaveTextContent('Đã dừng bằng Dừng tất cả — chưa gửi gì'));
    expect(within(card).queryByRole('button', { name: 'Xem ảnh chụp' })).toBeNull();
  });

  it('WORKER_TIMEOUT sau khi đã chạy → "Không rõ tin đã đi hay chưa", bảo kiểm tra Facebook, KHÔNG bảo soạn lại/thử lại', async () => {
    jobReplies = [{
      status: 'failed', error: 'WORKER_TIMEOUT', started_at: '2026-10-03T01:00:05Z',
      error_text: 'Không rõ tin đã đi hay chưa (trình duyệt mất liên lạc giữa chừng) — mở Facebook kiểm tra trước khi gửi lại.',
    }];
    renderPanel();
    showProposal(REPLY);
    const card = cardOf('Trả lời bình luận Facebook');
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và gửi' }));
    const st = await within(card).findByTestId('gen-write-status');
    await waitFor(() => expect(st).toHaveAttribute('data-status', 'failed'));
    expect(st).toHaveTextContent('Không rõ tin đã đi hay chưa');
    expect(st).toHaveTextContent('mở Facebook kiểm tra trước khi gửi lại');
    expect(st).not.toHaveTextContent('Hỏi Gen');
    expect(st).not.toHaveTextContent('thử lại');
  });

  it('dừng khi việc đang chạy → không khẳng định "chưa gửi gì"; ảnh đã xoá theo hạn lưu ≠ "không chụp được ảnh"', async () => {
    jobReplies = [{ status: 'cancelled', error: 'CANCELLED', started_at: '2026-10-03T01:00:05Z' }];
    renderPanel();
    showProposal(REPLY);
    let card = cardOf('Trả lời bình luận Facebook');
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và gửi' }));
    let st = await within(card).findByTestId('gen-write-status');
    await waitFor(() => expect(st).toHaveAttribute('data-status', 'cancelled'));
    expect(st).not.toHaveTextContent('chưa gửi gì');
    expect(st).toHaveTextContent('Nếu tin kịp đi trước khi dừng, mục này sẽ tự chuyển sang "Đã gửi"');

    cleanup();
    queryClient.clear();
    queryClient.setQueryData(qk.me, ME);
    jobReplies = [{ status: 'done', has_proof: false, result: { sent: true, confirmed: true } }];
    renderPanel();
    showProposal({ ...REPLY, id: 'w6' } as GenProposal);
    card = cardOf('Trả lời bình luận Facebook');
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và gửi' }));
    st = await within(card).findByTestId('gen-write-status');
    await waitFor(() => expect(st).toHaveAttribute('data-status', 'done'));
    expect(st).not.toHaveTextContent('không chụp được ảnh');
  });
});
