/**
 * v0.1.50 (F-81, QD-18) — thẻ đề xuất "Ghi vào Kho dữ liệu" (kho_create / kho_update) trong khung Gen:
 * - hàng Bảng + Bản ghi (khoá cứng) và BẢNG trường "Trường | (Hiện tại) | Sẽ ghi" — ĐÚNG fields.record, theo thứ tự KHO_FIELDS;
 * - nhãn "Cần mã PIN" + câu cảnh báo cố định; 423 ⇒ hộp PIN sẵn có rồi gửi lại; xong ⇒ "Đã ghi vào Kho: PHIEN-12";
 * - write_scope 'missing' ⇒ khoá nút Xác nhận + "Gen-hub chưa cấp quyền ghi Kho" + nút "Mở thẻ Gen-hub";
 * - Sửa = ô theo từng trường cho phép của bảng (select / date / textarea), bảng + mã khoá cứng;
 * - lỗi theo mã (HUB_WRITE_*, HUB_BREAKER_OPEN, HUB_LINK_OFF, GEN_PROPOSAL_DECIDED) = câu tiếng Việt + "Chi tiết kỹ thuật";
 * - KHO_FIELDS trong gen.ts khớp `KHO_FIELDS` ở apps/api/gh/hub_link/kho_write.py (đọc tệp như gen-tool-labels-v0149.test.ts).
 */
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import {
  GEN_PROPOSAL_EDITABLE,
  KHO_DATE_FIELDS,
  KHO_FIELDS,
  KHO_PRIORITY,
  KHO_REQUIRED,
  KHO_STATUS,
  KHO_TEXT_MAX,
  KHO_TITLE_MAX,
  KHO_WARNING_MAX,
  khoMaxLen,
  type GenProposal,
  type GenStepEvent,
} from '@gen-harness/contracts';
import { GenPanel } from '../../src/gen/GenPanel';
import { loadConversation } from '../../src/gen/genClient';
import { changedFields, initialDraft } from '../../src/gen/proposalModel';
import { useGenStore } from '../../src/gen/genStore';
import {
  KHO_CANCELLED_UNCERTAIN_TEXT,
  KHO_UNCERTAIN_TEXT,
  RELOAD_BUSY_TEXT,
  khoBangOf,
  khoCurrent,
  khoDraftRecord,
  khoDraftValid,
  khoFieldKind,
  khoLengthError,
  khoRows,
  khoTargetText,
  khoToolWritable,
  proposalErrorDetail,
  proposalErrorView,
} from '../../src/gen/khoWriteModel';
import { applyEvent } from '../../src/lib/realtime';
import { setNavigator } from '../../src/lib/navigation';
import { usePinStore } from '../../src/lib/pinStore';
import { qk } from '../../src/lib/queries';
import { queryClient } from '../../src/lib/queryClient';
import { ApiError } from '../../src/lib/api';
import { NotificationBell } from '../../src/shell/NotificationBell';

const ME = {
  id: 'u1', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A', role: { code: 'owner', name: 'Owner — Sếp' },
  org: { id: 'o1', name: 'Genesis', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: { 'action.approve': 'all', 'system.read': 'all' },
  features: { gen: true },
};

const WARNING = 'Ghi thẳng vào Kho dữ liệu qua Gen-hub khi Sếp bấm Xác nhận và nhập mã PIN — không tự hoàn tác.';

// record cố ý đặt LỘN thứ tự để kiểm bảng sắp theo KHO_FIELDS.
const PHIEN: GenProposal = {
  id: 'k1',
  type: 'kho_create',
  fields: {
    bang: 'Phiên',
    record: {
      'Việc tiếp': 'Sếp duyệt đề xuất PHIEN đầu tiên',
      'Chủ đề': 'Gen-Harness v0.1.50 — Gen nhớ và ghi Kho',
      'Đã chốt': 'Gen nhớ tối đa 30 ghi chú',
      Ngày: '2026-10-09',
    },
  },
  summary: 'Tạo bản ghi mới ở bảng Phiên của Kho dữ liệu — ghi thẳng qua Gen-hub khi Sếp xác nhận và nhập mã PIN.',
  labels: { bang: 'Phiên', target: 'Bản ghi mới', write_scope: 'ok' },
  target: 'hub.kho_write:Phiên',
  requires_pin: true,
  status: 'pending',
};
const VIEC: GenProposal = {
  ...PHIEN, id: 'k2',
  fields: { bang: 'Việc', record: { 'Ưu tiên': 'P2', 'Tiêu đề': 'Soạn báo giá ván MDF', 'Trạng thái': 'Đang làm', Hạn: '2026-10-15' } },
  labels: { bang: 'Việc', target: 'Bản ghi mới', write_scope: 'ok' },
  target: 'hub.kho_write:Việc',
} as GenProposal;
const UPDATE: GenProposal = {
  id: 'k3',
  type: 'kho_update',
  fields: { ma: 'VIEC-12', record: { 'Ngày xong': '2026-10-09', 'Trạng thái': 'Xong' } },
  summary: 'Sửa bản ghi VIEC-12 ở bảng Việc của Kho dữ liệu — ghi thẳng qua Gen-hub khi Sếp xác nhận và nhập mã PIN.',
  labels: { bang: 'Việc', target: 'VIEC-12 · Soạn báo giá ván MDF', write_scope: 'ok', 'cur:Trạng thái': 'Đang làm', 'cur:Ngày xong': '' },
  target: 'hub.kho_write:VIEC-12',
  requires_pin: true,
  status: 'pending',
};
const MISSING: GenProposal = { ...PHIEN, id: 'k4', labels: { ...PHIEN.labels, write_scope: 'missing' } } as GenProposal;
const MISSING_UPDATE: GenProposal = { ...UPDATE, id: 'k5', labels: { ...UPDATE.labels, write_scope: 'missing' } } as GenProposal;

type Call = { method: string; url: string; body: unknown };
const calls: Call[] = [];
const navigations: string[] = [];
/** Phản hồi xác nhận lần lượt (hết thì dùng mặc định: ghi thành công). */
let confirmQueue: Array<{ status: number; body: unknown }> = [];
let hubLink: Record<string, unknown> = { configured: true, enabled: true, status: 'ok', write_scopes: { kho: false } };
let notifications: unknown = { items: [], unread: 0 };
/** Tin của hội thoại khi web tải lại (GET /gen/conversations/{id}/messages). */
let conversationMessages: unknown[] = [];

const ALL = [PHIEN, VIEC, UPDATE, MISSING];

function stubApi() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ method, url, body });
      const json = (status: number, b: unknown) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
      if (url.endsWith('/hub/link') && method === 'GET') return json(200, hubLink);
      if (url.includes('/notifications') && method === 'GET') return json(200, notifications);
      if (/\/gen\/conversations\/\w+\/messages/.test(url) && method === 'GET') return json(200, conversationMessages);
      const m = url.match(/\/gen\/proposals\/(\w+)\/(confirm|cancel)$/);
      if (m && method === 'POST') {
        const queued = confirmQueue.shift();
        if (queued) return json(queued.status, queued.body);
        const base = ALL.find((x) => x.id === m[1]) ?? PHIEN;
        if (m[2] === 'cancel') return json(200, { ...base, status: 'cancelled' });
        const fields = base.type === 'kho_create' ? { ...base.fields, ...(body?.fields ?? {}) } : { ...base.fields, ...(body?.fields ?? {}) };
        return json(200, {
          ...base, fields, status: 'confirmed',
          result: { type: 'kho_record', id: null, code: base.type === 'kho_update' ? 'VIEC-12' : 'PHIEN-12', screen: null, bang: base.labels.bang },
        });
      }
      return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
    }),
  );
}

function showProposal(p: GenProposal) {
  useGenStore.setState({
    messages: [
      { id: 'u-1', role: 'user', text: 'ghi vào Kho', steps: [] },
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
const cardOf = () => screen.getByRole('group', { name: 'Đề xuất: Ghi vào Kho dữ liệu' });
const tableRows = (card: HTMLElement) => within(card).getByTestId('gen-kho-table').querySelectorAll('tbody tr');
const headers = (card: HTMLElement) => [...within(card).getByTestId('gen-kho-table').querySelectorAll('thead th')].map((h) => h.textContent);

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(qk.me, ME);
  calls.length = 0;
  navigations.length = 0;
  confirmQueue = [];
  hubLink = { configured: true, enabled: true, status: 'ok', write_scopes: { kho: false } };
  notifications = { items: [], unread: 0 };
  conversationMessages = [];
  usePinStore.setState({ open: false, waiters: [] });
  useGenStore.setState({ openByUser: {}, conversationId: 'c1', messages: [], busy: false, spotlight: null });
  setNavigator((to) => navigations.push(to));
  stubApi();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('Thẻ "Ghi vào Kho dữ liệu" — kho_create', () => {
  it('hiện Bảng + Bản ghi, BẢNG trường đúng fields.record theo thứ tự KHO_FIELDS, "Cần mã PIN", câu cảnh báo cố định; chưa ghi gì', () => {
    const { container } = renderPanel();
    showProposal(PHIEN);
    const card = cardOf();
    expect(card).toHaveTextContent('Đề xuất · Ghi vào Kho dữ liệu');
    const dl = card.querySelector('dl')!;
    expect(dl).toHaveTextContent('Bảng');
    expect(dl).toHaveTextContent('Phiên');
    expect(dl).toHaveTextContent('Bản ghi');
    expect(dl).toHaveTextContent('Bản ghi mới');
    expect(headers(card)).toEqual(['Trường', 'Sẽ ghi']); // tạo mới: không có cột "Hiện tại"
    const rows = [...tableRows(card)].map((r) => [...r.children].map((c) => c.textContent));
    // Đúng 4 khoá của record (không thêm trường rỗng), theo thứ tự KHO_FIELDS của Phiên — không theo thứ tự record.
    expect(rows).toEqual([
      ['Chủ đề', 'Gen-Harness v0.1.50 — Gen nhớ và ghi Kho'],
      ['Ngày', '2026-10-09'],
      ['Đã chốt', 'Gen nhớ tối đa 30 ghi chú'],
      ['Việc tiếp', 'Sếp duyệt đề xuất PHIEN đầu tiên'],
    ]);
    expect(card).toHaveTextContent('Cần mã PIN');
    expect(within(card).getByTestId('gen-kho-warning')).toHaveTextContent(WARNING);
    expect(card.querySelector('[data-icon="database"]')).not.toBeNull();
    expect(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' })).toBeEnabled();
    expect(within(card).queryByRole('button', { name: 'Xác nhận' })).toBeNull();
    expect(within(card).queryByText('Gen-hub chưa cấp quyền ghi Kho', { exact: false })).toBeNull();
    expect(writes()).toEqual([]);
    expect(container.textContent).not.toContain('[object Object]');
  });

  it('bảng Việc: thứ tự Tiêu đề, Trạng thái, Ưu tiên, Hạn; khoá lạ của máy chủ mới hơn vẫn hiện (cuối bảng) để Sếp thấy đủ', () => {
    renderPanel();
    showProposal({ ...VIEC, fields: { bang: 'Việc', record: { ...(VIEC.type === 'kho_create' ? VIEC.fields.record : {}), 'Ghi chú mới': 'khoá lạ' } } } as GenProposal);
    const card = cardOf();
    const names = [...tableRows(card)].map((r) => r.children[0].textContent);
    expect(names).toEqual(['Tiêu đề', 'Trạng thái', 'Ưu tiên', 'Hạn', 'Ghi chú mới']);
    expect(card.querySelector('dl')).toHaveTextContent('Việc');
  });

  it('mọi trường record của MỌI bảng xếp đúng KHO_FIELDS (kể cả khi record có đủ 6 / 7 trường)', () => {
    for (const bang of ['Phiên', 'Việc'] as const) {
      const record = Object.fromEntries([...KHO_FIELDS[bang]].reverse().map((f) => [f, `giá trị ${f}`]));
      const p = { ...PHIEN, fields: { bang, record }, labels: { bang, target: 'Bản ghi mới', write_scope: 'ok' } } as GenProposal;
      expect(khoRows(p as never).map((r) => r.field)).toEqual([...KHO_FIELDS[bang]]);
      expect(khoRows(p as never).every((r) => r.cur === null && r.next.startsWith('giá trị '))).toBe(true);
    }
  });

  it('Xác nhận → 423 PIN_REQUIRED → hộp PIN sẵn có → gửi lại đúng một lần nữa → "Đã ghi vào Kho: PHIEN-12"', async () => {
    confirmQueue = [{ status: 423, body: { status: 423, code: 'PIN_REQUIRED', title: 'Thao tác này cần nhập mã PIN', detail: { operation: 'hub.write' } } }];
    let opens = 0;
    const unsub = usePinStore.subscribe((s, prev) => {
      if (s.open && !prev.open) {
        opens += 1;
        queueMicrotask(() => usePinStore.getState().finish(true));
      }
    });
    renderPanel();
    showProposal(PHIEN);
    const card = cardOf();
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' }));
    await waitFor(() => expect(card).toHaveTextContent('Đã ghi vào Kho: PHIEN-12'));
    unsub();
    expect(opens).toBe(1);
    expect(writes().map((c) => c.url.replace(/^.*\/api\/v1/, ''))).toEqual(['/gen/proposals/k1/confirm', '/gen/proposals/k1/confirm']);
    expect(writes()[1].body).toEqual({ fields: {} });
    expect(within(card).queryByRole('button', { name: 'Xác nhận và ghi Kho' })).toBeNull();
    expect(card).not.toHaveTextContent('Ghi thẳng vào Kho dữ liệu qua Gen-hub khi Sếp bấm');
    expect(card).not.toHaveTextContent('Đã xác nhận');
  });

  it('huỷ hộp PIN ⇒ câu "Đã huỷ — thao tác cần mã PIN.", thẻ còn nguyên, chưa ghi (không có lần gửi lại)', async () => {
    confirmQueue = [{ status: 423, body: { status: 423, code: 'PIN_REQUIRED', title: 'Thao tác này cần nhập mã PIN' } }];
    const unsub = usePinStore.subscribe((s, prev) => {
      if (s.open && !prev.open) queueMicrotask(() => usePinStore.getState().finish(false));
    });
    renderPanel();
    showProposal(PHIEN);
    const card = cardOf();
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' }));
    await waitFor(() => expect(card).toHaveTextContent('Đã huỷ — thao tác cần mã PIN.'));
    unsub();
    expect(writes()).toHaveLength(1);
    expect(card).not.toHaveTextContent('Đã ghi vào Kho');
    expect(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' })).toBeEnabled();
  });

  it('sau khi ghi mà Gen-hub không trả mã ⇒ "Đã ghi vào Kho: bản ghi mới"', async () => {
    confirmQueue = [{
      status: 200,
      body: { ...PHIEN, status: 'confirmed', result: { type: 'kho_record', id: null, code: null, screen: null, bang: 'Phiên' } },
    }];
    renderPanel();
    showProposal(PHIEN);
    const card = cardOf();
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' }));
    await waitFor(() => expect(card).toHaveTextContent('Đã ghi vào Kho: bản ghi mới'));
  });

  it('thẻ ghi Phiên của bản mới (nhãn release) nói rõ: mỗi bản ghi MỘT lần cho cả tổ chức, Huỷ là huỷ cho mọi Owner; thẻ thường thì không', () => {
    renderPanel();
    showProposal({ ...PHIEN, id: 'k9', labels: { ...PHIEN.labels, release: 'v0.1.50' } } as GenProposal);
    expect(within(cardOf()).getByTestId('gen-kho-release-note')).toHaveTextContent(
      'Phiên của bản v0.1.50: mỗi bản chỉ ghi vào Kho một lần cho cả tổ chức — Owner khác đã ghi thì thẻ này tự đóng; bấm Huỷ là huỷ cho mọi Owner.',
    );
    cleanup();
    renderPanel();
    showProposal(PHIEN);
    expect(within(cardOf()).queryByTestId('gen-kho-release-note')).toBeNull();
  });

  it('Huỷ → "Đã huỷ — không ghi gì vào Kho"; chỉ gọi cancel', async () => {
    renderPanel();
    showProposal(PHIEN);
    const card = cardOf();
    await userEvent.click(within(card).getByRole('button', { name: 'Huỷ' }));
    await waitFor(() => expect(card).toHaveTextContent('Đã huỷ — không ghi gì vào Kho'));
    expect(writes().map((c) => c.url.split('/').pop())).toEqual(['cancel']);
  });
});

describe('Thẻ "Ghi vào Kho dữ liệu" — kho_update', () => {
  it('có cột "Hiện tại" (labels["cur:<trường>"], rỗng ⇒ —) và "Sẽ ghi"; Bản ghi = mã + tên', () => {
    renderPanel();
    showProposal(UPDATE);
    const card = cardOf();
    expect(headers(card)).toEqual(['Trường', 'Hiện tại', 'Sẽ ghi']);
    const rows = [...tableRows(card)].map((r) => [...r.children].map((c) => c.textContent));
    expect(rows).toEqual([
      ['Trạng thái', 'Đang làm', 'Xong'],
      ['Ngày xong', '—', '2026-10-09'],
    ]);
    const dl = card.querySelector('dl')!;
    expect(dl).toHaveTextContent('Việc');
    expect(dl).toHaveTextContent('VIEC-12 · Soạn báo giá ván MDF');
    expect(card).toHaveTextContent('Cần mã PIN');
    expect(within(card).getByTestId('gen-kho-warning')).toHaveTextContent(WARNING);
    expect(khoTargetText(UPDATE as never)).toBe('VIEC-12 · Soạn báo giá ván MDF');
  });

  it('xác nhận → "Đã ghi vào Kho: VIEC-12" (mã bản ghi đã sửa)', async () => {
    renderPanel();
    showProposal(UPDATE);
    const card = cardOf();
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' }));
    await waitFor(() => expect(card).toHaveTextContent('Đã ghi vào Kho: VIEC-12'));
    expect(writes()[0].url).toMatch(/\/gen\/proposals\/k3\/confirm$/);
  });
});

describe('Quyền ghi Kho của token (write_scope)', () => {
  it('write_scope "missing" ⇒ khoá nút Xác nhận, dòng "Gen-hub chưa cấp quyền ghi Kho", nút "Mở thẻ Gen-hub" (→ Kết nối › Gen-hub); chưa gọi confirm', async () => {
    renderPanel();
    showProposal(MISSING);
    const card = cardOf();
    expect(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' })).toBeDisabled();
    expect(within(card).getByTestId('gen-kho-missing')).toHaveTextContent('Gen-hub chưa cấp quyền ghi Kho');
    expect(within(card).getByTestId('gen-kho-missing')).toHaveTextContent('tick quyền kho_create, kho_update');
    await userEvent.click(within(card).getByRole('button', { name: 'Mở thẻ Gen-hub' }));
    expect(navigations).toEqual(['/connections#genhub']);
    expect(writes()).toEqual([]);
    // Sửa vẫn dùng được, nhưng Xác nhận vẫn khoá.
    await userEvent.click(within(card).getByRole('button', { name: 'Sửa' }));
    expect(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' })).toBeDisabled();
  });

  it('Sếp vừa tick quyền ở Gen-hub (liên kết báo write_scopes.kho = true) ⇒ thẻ tự mở khoá', async () => {
    hubLink = { configured: true, enabled: true, status: 'ok', write_scopes: { kho: true } };
    renderPanel();
    showProposal(MISSING);
    const card = cardOf();
    await waitFor(() => expect(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' })).toBeEnabled());
    expect(within(card).queryByTestId('gen-kho-missing')).toBeNull();
    expect(within(card).queryByRole('button', { name: 'Mở thẻ Gen-hub' })).toBeNull();
  });

  it('quyền THEO TỪNG tool: write_scopes {kho:false, kho_create:true, kho_update:false} ⇒ thẻ kho_create mở khoá, thẻ kho_update vẫn khoá', async () => {
    hubLink = { configured: true, enabled: true, status: 'ok', write_scopes: { kho: false, kho_create: true, kho_update: false } };
    renderPanel();
    showProposal(MISSING);
    let card = cardOf();
    // Sếp chỉ tick kho_create rồi Kiểm tra: Kết nối ghi "Tạo bản ghi Phiên/Việc: Có" ⇒ thẻ tạo mới phải mở (máy chủ cũng nhận).
    await waitFor(() => expect(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' })).toBeEnabled());
    expect(within(card).queryByTestId('gen-kho-missing')).toBeNull();
    cleanup();
    queryClient.clear();
    queryClient.setQueryData(qk.me, ME);
    calls.length = 0;
    renderPanel();
    showProposal(MISSING_UPDATE);
    card = cardOf();
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/hub/link'))).toBe(true));
    expect(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' })).toBeDisabled();
    expect(within(card).getByTestId('gen-kho-missing')).toHaveTextContent('Gen-hub chưa cấp quyền ghi Kho');
  });

  it('khoToolWritable: cờ riêng của tool trước, máy chủ cũ chỉ có `kho`; chỉ `true` mới là có quyền', () => {
    expect(khoToolWritable({ kho: false, kho_create: true, kho_update: false }, 'kho_create')).toBe(true);
    expect(khoToolWritable({ kho: false, kho_create: true, kho_update: false }, 'kho_update')).toBe(false);
    expect(khoToolWritable({ kho: true }, 'kho_update')).toBe(true);
    expect(khoToolWritable({ kho: false }, 'kho_create')).toBe(false);
    expect(khoToolWritable({ kho: true, kho_create: false }, 'kho_create')).toBe(false);
    expect(khoToolWritable({ kho: 'true', kho_create: 1 } as never, 'kho_create')).toBe(false);
    expect(khoToolWritable(null, 'kho_create')).toBe(false);
    expect(khoToolWritable(undefined, 'kho_update')).toBe(false);
  });

  it('Sếp TỰ đóng tool của thẻ ở MCP Hub (write_hidden) ⇒ câu riêng + nút "Mở MCP Hub", không giục tick ở Gen-hub', async () => {
    hubLink = { configured: true, enabled: true, status: 'ok', write_scopes: { kho: false, kho_create: false, kho_update: true }, write_hidden: ['kho_create'] };
    renderPanel();
    showProposal(MISSING);
    const card = cardOf();
    await waitFor(() => expect(within(card).getByTestId('gen-kho-missing')).toHaveTextContent('Sếp đã tự đóng kho_create ở MCP Hub'));
    expect(within(card).getByTestId('gen-kho-missing')).not.toHaveTextContent('tick quyền kho_create');
    expect(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' })).toBeDisabled();
    expect(within(card).queryByRole('button', { name: 'Mở thẻ Gen-hub' })).toBeNull();
    await userEvent.click(within(card).getByRole('button', { name: 'Mở MCP Hub' }));
    expect(navigations).toEqual(['/mcp']);
  });

  it('write_scope "ok" ⇒ không hỏi liên kết Gen-hub, không có dòng thiếu quyền', () => {
    // v0.1.57: câu chào của khung (lúc còn trống) hỏi `/hub/link` lấy Tên Kho nếu người xem đọc được cài đặt — nên ở ca này
    // người xem chỉ có quyền duyệt, để phép kiểm chỉ nói về THẺ đề xuất.
    queryClient.setQueryData(qk.me, { ...ME, permissions: { 'action.approve': 'all' } });
    renderPanel();
    showProposal(PHIEN);
    expect(calls.filter((c) => c.url.endsWith('/hub/link'))).toEqual([]);
    expect(within(cardOf()).queryByTestId('gen-kho-missing')).toBeNull();
  });
});

describe('Lỗi khi xác nhận ghi Kho — câu tiếng Việt + "Chi tiết kỹ thuật"', () => {
  it('HUB_WRITE_UNCERTAIN ⇒ "Chưa chắc đã ghi — Sếp mở Kho kiểm trước khi bấm lại"; vẫn bấm lại được; không có kết quả "Đã ghi"', async () => {
    confirmQueue = [{ status: 502, body: { status: 502, code: 'HUB_WRITE_UNCERTAIN', title: 'Không rõ Gen-hub đã ghi hay chưa' } }];
    const { container } = renderPanel();
    showProposal(PHIEN);
    const card = cardOf();
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' }));
    const alert = await within(card).findByRole('alert');
    expect(alert).toHaveTextContent('Chưa chắc đã ghi — Sếp mở Kho kiểm trước khi bấm lại');
    // Nói rõ cả hai đường (đúng ghi chú phát hành bước 2): Kho đã có ⇒ Huỷ; chưa có ⇒ Xác nhận lại.
    expect(alert.querySelector('.write-error__text')?.textContent).toBe(KHO_UNCERTAIN_TEXT);
    expect(alert).toHaveTextContent('Kho đã có bản ghi thì bấm Huỷ; chưa có thì bấm Xác nhận lại.');
    expect(within(alert).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(alert).toHaveTextContent('HUB_WRITE_UNCERTAIN');
    expect(card).not.toHaveTextContent('Đã ghi vào Kho');
    expect(container.textContent).not.toContain('[object Object]');
    expect(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' })).toBeEnabled();
  });

  it('ghi "chưa chắc" rồi Sếp bấm Huỷ (Kho đã có) ⇒ "Đã đóng — … Kho có thể đã có bản ghi", KHÔNG nói "không ghi gì vào Kho"', async () => {
    confirmQueue = [
      { status: 502, body: { status: 502, code: 'HUB_WRITE_UNCERTAIN', title: 'Chưa chắc đã ghi' } },
      // Máy chủ gắn nhãn `uncertain` cho thẻ có lần ghi chưa chắc; Huỷ trả thẻ đã đóng kèm nhãn đó.
      { status: 200, body: { ...PHIEN, status: 'cancelled', labels: { ...PHIEN.labels, uncertain: '1' } } },
    ];
    renderPanel();
    showProposal(PHIEN);
    const card = cardOf();
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' }));
    await within(card).findByRole('alert');
    await userEvent.click(within(card).getByRole('button', { name: 'Huỷ' }));
    await waitFor(() => expect(within(card).getByTestId('gen-prop-cancelled')).toHaveTextContent(KHO_CANCELLED_UNCERTAIN_TEXT));
    expect(card).not.toHaveTextContent('không ghi gì vào Kho');
    expect(within(card).queryByRole('alert')).toBeNull();
  });

  it('HUB_WRITE_HIDDEN (Sếp tự đóng tool ở MCP Hub) ⇒ câu riêng + nút "Mở MCP Hub"; không bảo tick ở Gen-hub', async () => {
    confirmQueue = [{ status: 409, body: { status: 409, code: 'HUB_WRITE_HIDDEN', title: 'thô', detail: 'Tool kho_create đã bị Owner đóng' } }];
    renderPanel();
    showProposal(PHIEN);
    const card = cardOf();
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' }));
    const alert = await within(card).findByRole('alert');
    expect(alert).toHaveTextContent('Sếp đã tự đóng tool ghi Kho này ở MCP Hub — chưa ghi gì vào Kho.');
    expect(alert).not.toHaveTextContent('tick kho_create');
    expect(alert.querySelector('details code')?.textContent).toBe('HTTP 409 · HUB_WRITE_HIDDEN · Tool kho_create đã bị Owner đóng');
    expect(within(card).queryByRole('button', { name: 'Mở Kết nối › Gen-hub' })).toBeNull();
    await userEvent.click(within(card).getByRole('button', { name: 'Mở MCP Hub' }));
    expect(navigations).toEqual(['/mcp']);
  });

  it('HUB_WRITE_MISSING ⇒ câu + nút "Mở Kết nối › Gen-hub"', async () => {
    confirmQueue = [{ status: 409, body: { status: 409, code: 'HUB_WRITE_MISSING', title: 'thiếu quyền' } }];
    renderPanel();
    showProposal(PHIEN);
    const card = cardOf();
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' }));
    const alert = await within(card).findByRole('alert');
    expect(alert).toHaveTextContent('Gen-hub chưa cấp quyền ghi Kho');
    expect(alert).toHaveTextContent('Chưa ghi gì vào Kho.');
    await userEvent.click(within(card).getByRole('button', { name: 'Mở Kết nối › Gen-hub' }));
    expect(navigations).toEqual(['/connections#genhub']);
  });

  it.each([
    ['HUB_WRITE_PERMIT', 403, 'Giấy phép ghi không hợp lệ hoặc đã quá 5 phút — chưa ghi gì vào Kho. Bấm Xác nhận lại (nhập mã PIN) để ghi.'],
    ['HUB_TOOL_NOT_ALLOWED', 403, 'Thao tác ghi này không nằm trong phạm vi Gen được phép — chưa ghi gì vào Kho.'],
    ['HUB_OWNER_ONLY', 403, 'Chỉ Sếp (Owner) được ghi vào Kho dữ liệu — chưa ghi gì.'],
    ['HUB_BREAKER_OPEN', 503, 'Gen-hub tạm không trả lời — Gen tự thử lại sau 1 phút.'],
    ['HUB_LINK_OFF', 409, 'Gen-hub đang tắt — vào Kết nối › Gen-hub bấm Kiểm tra để bật lại.'],
    ['HUB_BLOCKED', 409, 'Ghi Kho đang bị rào chắn MCP Hub chặn — chưa ghi gì vào Kho.'],
    ['HUB_TOKEN_REJECTED', 409, 'Token Gen-hub hết hạn hoặc đã bị thu hồi — chưa ghi gì vào Kho. Vào Kết nối › Gen-hub dán token mới rồi bấm Kiểm tra.'],
    ['GEN_PROPOSAL_DECIDED', 409, 'Đề xuất này đã được xác nhận hoặc đã huỷ ở nơi khác'],
  ])('%s ⇒ câu thân thiện (không phải câu 403 chung), mã chỉ trong "Chi tiết kỹ thuật"', async (code, status, sentence) => {
    confirmQueue = [{ status, body: { status, code, title: 'thô từ máy chủ' } }];
    const { container } = renderPanel();
    showProposal(PHIEN);
    const card = cardOf();
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' }));
    const alert = await within(card).findByRole('alert');
    expect(alert).toHaveTextContent(sentence);
    expect(alert).not.toHaveTextContent('Vai trò của bạn không có quyền');
    expect(alert).not.toHaveTextContent('thô từ máy chủ');
    expect(within(alert).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(alert.querySelector('details code')?.textContent).toContain(code);
    expect(container.textContent).not.toContain('[object Object]');
  });

  it('HUB_WRITE_REJECTED ⇒ nêu ĐÚNG lý do Kho trả (detail) + "Bấm Sửa"; lý do cũng có trong "Chi tiết kỹ thuật"', async () => {
    const why = "Giá trị 'Trạng thái' không hợp lệ (Bearer ***)";
    confirmQueue = [{ status: 409, body: { status: 409, code: 'HUB_WRITE_REJECTED', title: `Kho từ chối lần ghi này: ${why}`, detail: why } }];
    const { container } = renderPanel();
    showProposal(PHIEN);
    const card = cardOf();
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' }));
    const alert = await within(card).findByRole('alert');
    expect(alert.querySelector('.write-error__text')?.textContent).toBe(`Kho từ chối lần ghi này: ${why} — chưa ghi gì. Bấm Sửa để chỉnh các trường rồi Xác nhận lại.`);
    expect(alert.querySelector('details code')?.textContent).toBe(`HTTP 409 · HUB_WRITE_REJECTED · ${why}`);
    expect(within(card).queryByRole('button', { name: 'Mở Kết nối › Gen-hub' })).toBeNull();
    expect(container.textContent).not.toContain('[object Object]');
  });

  it('HUB_TOKEN_REJECTED (Gen-hub trả 401/403 lúc ghi) ⇒ KHÔNG bảo "Bấm Sửa" mà dẫn tới Kết nối › Gen-hub đổi token', async () => {
    confirmQueue = [{ status: 409, body: { status: 409, code: 'HUB_TOKEN_REJECTED', title: 'Token Gen-hub hết hạn', detail: '401: unauthorized' } }];
    renderPanel();
    showProposal(PHIEN);
    const card = cardOf();
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' }));
    const alert = await within(card).findByRole('alert');
    expect(alert).toHaveTextContent('Token Gen-hub hết hạn hoặc đã bị thu hồi — chưa ghi gì vào Kho.');
    expect(alert).not.toHaveTextContent('Bấm Sửa');
    expect(alert.querySelector('details code')?.textContent).toBe('HTTP 409 · HUB_TOKEN_REJECTED · 401: unauthorized');
    await userEvent.click(within(card).getByRole('button', { name: 'Mở Kết nối › Gen-hub' }));
    expect(navigations).toEqual(['/connections#genhub']);
  });

  it('lý do ở `detail` (permit EXPIRED, lỗi mạng) nằm trong "Chi tiết kỹ thuật"; câu permit = bấm Xác nhận lại, không bảo hỏi Gen', async () => {
    confirmQueue = [
      { status: 403, body: { status: 403, code: 'HUB_WRITE_PERMIT', title: 'Giấy phép ghi Kho không hợp lệ', detail: 'EXPIRED' } },
      { status: 502, body: { status: 502, code: 'HUB_WRITE_UNCERTAIN', title: 'Chưa chắc đã ghi', detail: 'Gen-hub không trả lời (hết giờ)' } },
    ];
    renderPanel();
    showProposal(PHIEN);
    const card = cardOf();
    const btn = () => within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' });
    await userEvent.click(btn());
    let alert = await within(card).findByRole('alert');
    expect(alert).toHaveTextContent('Bấm Xác nhận lại (nhập mã PIN) để ghi.');
    expect(alert).not.toHaveTextContent('Hỏi Gen đề xuất lại');
    expect(alert.querySelector('details code')?.textContent).toBe('HTTP 403 · HUB_WRITE_PERMIT · EXPIRED');
    await userEvent.click(btn());
    await waitFor(() => expect(within(card).getByRole('alert')).toHaveTextContent('Chưa chắc đã ghi'));
    alert = within(card).getByRole('alert');
    expect(alert.querySelector('details code')?.textContent).toBe('HTTP 502 · HUB_WRITE_UNCERTAIN · Gen-hub không trả lời (hết giờ)');
  });

  it('GEN_PROPOSAL_DECIDED (Owner khác đã ghi bản này) ⇒ nút "Tải lại hội thoại" → thẻ hiện "Thẻ đã đóng — …", hết nút Xác nhận', async () => {
    const closed = { ...PHIEN, status: 'cancelled', labels: { ...PHIEN.labels, closed: 'Owner khác đã ghi bản này vào Kho (PHIEN-12)' } };
    conversationMessages = [
      { id: 'm-u', role: 'user', content: { text: 'ghi vào Kho' }, turn_id: 't1', created_at: '2026-10-09T08:00:00Z' },
      { id: 'm-a', role: 'assistant', content: { steps: [{ kind: 'say', text: 'Em đề xuất:' }, { kind: 'proposal', proposal: closed }] }, turn_id: 't1', created_at: '2026-10-09T08:00:01Z' },
    ];
    confirmQueue = [{ status: 409, body: { status: 409, code: 'GEN_PROPOSAL_DECIDED', title: 'Bản này đã được ghi vào Kho hoặc đã huỷ — thẻ đã đóng' } }];
    renderPanel();
    showProposal(PHIEN);
    // Lượt Gen đã xong (tin không còn "đang nghĩ") — như khi Sếp mở thẻ từ chuông.
    useGenStore.setState({ conversationId: 'c1', messages: useGenStore.getState().messages.map((m) => ({ ...m, status: undefined })) });
    let card = cardOf();
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' }));
    const alert = await within(card).findByRole('alert');
    expect(alert).toHaveTextContent('Đề xuất này đã được xác nhận hoặc đã huỷ ở nơi khác');
    await userEvent.click(within(card).getByRole('button', { name: 'Tải lại hội thoại' }));
    await waitFor(() => expect(within(cardOf()).getByTestId('gen-prop-cancelled')).toHaveTextContent('Thẻ đã đóng — Owner khác đã ghi bản này vào Kho (PHIEN-12)'));
    card = cardOf();
    expect(within(card).queryByRole('button', { name: 'Xác nhận và ghi Kho' })).toBeNull();
    expect(card).not.toHaveTextContent('Đã huỷ — không ghi gì vào Kho');
    expect(calls.filter((c) => c.method === 'GET' && c.url.includes('/gen/conversations/c1/messages'))).toHaveLength(1);
  });

  it('mở thẻ từ chuông (tin đã lưu, CÙNG id sau khi tải lại) ⇒ Tải lại hội thoại: thẻ đóng VÀ hết dòng lỗi cũ; Gen đang trả lời ⇒ báo rõ, giữ nút', async () => {
    const msg = (proposal: unknown) => [
      { id: 'm-u', role: 'user', content: { text: 'ghi vào Kho' }, turn_id: 't1', created_at: '2026-10-09T08:00:00Z' },
      { id: 'm-a', role: 'assistant', content: { steps: [{ kind: 'say', text: 'Em đề xuất:' }, { kind: 'proposal', proposal }] }, turn_id: 't1', created_at: '2026-10-09T08:00:01Z' },
    ];
    // Sếp (Owner B) mở hội thoại từ chuông: tin của Gen nạp từ máy chủ với id 'm-a', thẻ còn chờ.
    conversationMessages = msg(PHIEN);
    renderPanel();
    await act(async () => {
      expect(await loadConversation('c1', 'u1')).toBe('opened');
    });
    const card = cardOf();
    // Owner A đã ghi bản này: máy chủ đóng thẻ (tin 'm-a' giữ nguyên id) và trả 409 khi B bấm Xác nhận.
    conversationMessages = msg({ ...PHIEN, status: 'cancelled', labels: { ...PHIEN.labels, closed: 'Owner khác đã ghi bản này vào Kho (PHIEN-12)' } });
    confirmQueue = [{ status: 409, body: { status: 409, code: 'GEN_PROPOSAL_DECIDED', title: 'Bản này đã được ghi vào Kho hoặc đã huỷ — thẻ đã đóng' } }];
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' }));
    expect(await within(card).findByRole('alert')).toHaveTextContent('Bấm Tải lại hội thoại để xem thẻ đã đóng.');
    const reloads = () => calls.filter((c) => c.method === 'GET' && c.url.includes('/gen/conversations/c1/messages')).length;
    const before = reloads();
    // Gen đang trả lời câu khác ⇒ không tải (không đè lượt đang chạy) nhưng nói rõ; nút Tải lại vẫn còn để bấm sau.
    act(() => useGenStore.setState({ busy: true }));
    await userEvent.click(within(card).getByRole('button', { name: 'Tải lại hội thoại' }));
    await waitFor(() => expect(within(card).getByRole('alert')).toHaveTextContent(RELOAD_BUSY_TEXT));
    expect(reloads()).toBe(before);
    expect(within(card).getByRole('button', { name: 'Tải lại hội thoại' })).toBeEnabled();
    act(() => useGenStore.setState({ busy: false }));
    await userEvent.click(within(card).getByRole('button', { name: 'Tải lại hội thoại' }));
    await waitFor(() => expect(within(card).getByTestId('gen-prop-cancelled')).toHaveTextContent('Thẻ đã đóng — Owner khác đã ghi bản này vào Kho (PHIEN-12)'));
    expect(cardOf()).toBe(card); // cùng thẻ (không gắn lại) — đúng trường hợp lỗi cũ từng còn lại
    expect(within(card).queryByRole('alert')).toBeNull();
    expect(card).not.toHaveTextContent('Bấm Tải lại hội thoại');
    expect(within(card).queryByRole('button', { name: 'Tải lại hội thoại' })).toBeNull();
    expect(reloads()).toBe(before + 1);
  });

  it('thẻ đã đóng vì Owner khác (labels.closed) — kể cả khi bấm Huỷ trên thẻ cũ — nói rõ lý do thay vì "Đã huỷ — không ghi gì"', async () => {
    confirmQueue = [{ status: 200, body: { ...PHIEN, status: 'cancelled', labels: { ...PHIEN.labels, closed: 'Owner khác đã huỷ ghi bản này' } } }];
    renderPanel();
    showProposal(PHIEN);
    const card = cardOf();
    await userEvent.click(within(card).getByRole('button', { name: 'Huỷ' }));
    await waitFor(() => expect(within(card).getByTestId('gen-prop-cancelled')).toHaveTextContent('Thẻ đã đóng — Owner khác đã huỷ ghi bản này'));
  });

  it('proposalErrorDetail: mã HTTP + mã lỗi + detail (chuỗi; đối tượng được ép chuỗi; không lặp lại)', () => {
    expect(proposalErrorDetail(new ApiError(409, { code: 'HUB_WRITE_REJECTED', title: 't', detail: 'Lý do' }))).toBe('HTTP 409 · HUB_WRITE_REJECTED · Lý do');
    expect(proposalErrorDetail(new ApiError(409, { code: 'HUB_LINK_OFF', title: 't' }))).toBe('HTTP 409 · HUB_LINK_OFF');
    expect(proposalErrorDetail(new ApiError(403, { code: 'HUB_WRITE_PERMIT', title: 't', detail: { reasons: ['USED'] } as never }))).not.toContain('[object Object]');
    expect(proposalErrorDetail(null)).toBeNull();
  });

  it('proposalErrorView: chỉ nhận ApiError có mã đã biết; mã lạ / lỗi thường ⇒ null (dùng errorText chung)', () => {
    expect(proposalErrorView(new ApiError(409, { code: 'HUB_WRITE_MISSING', title: 'x' }))?.action).toBe('open_hub');
    expect(proposalErrorView(new ApiError(409, { code: 'HUB_LINK_OFF', title: 'x' }))?.action).toBe('open_hub');
    expect(proposalErrorView(new ApiError(409, { code: 'GEN_MEMORY_FULL', title: 'x' }))?.action).toBe('open_memory');
    expect(proposalErrorView(new ApiError(502, { code: 'HUB_WRITE_UNCERTAIN', title: 'x' }))?.action).toBeNull();
    expect(proposalErrorView(new ApiError(409, { code: 'HUB_TOKEN_REJECTED', title: 'x' }))?.action).toBe('open_hub');
    expect(proposalErrorView(new ApiError(409, { code: 'GEN_PROPOSAL_DECIDED', title: 'x' }))?.action).toBe('reload');
    // Kho từ chối nhưng không gửi lý do ⇒ câu vẫn trọn vẹn (không có dấu hai chấm treo).
    expect(proposalErrorView(new ApiError(409, { code: 'HUB_WRITE_REJECTED', title: 'x' }))?.text).toBe('Kho từ chối lần ghi này — chưa ghi gì. Bấm Sửa để chỉnh các trường rồi Xác nhận lại.');
    expect(proposalErrorView(new ApiError(422, { code: 'HUB_WRITE_INVALID', title: 'x', detail: "Trường 'Hạn' phải là ngày" }))?.text).toBe(
      "Dữ liệu ghi Kho chưa hợp lệ (Trường 'Hạn' phải là ngày) — chưa ghi gì vào Kho. Bấm Sửa để chỉnh các trường rồi Xác nhận lại.",
    );
    expect(proposalErrorView(new ApiError(500, { code: 'INTERNAL', title: 'x' }))).toBeNull();
    expect(proposalErrorView(new Error('x'))).toBeNull();
    expect(proposalErrorView(null)).toBeNull();
  });
});

describe('Sửa thẻ Ghi vào Kho — ô theo từng trường cho phép của bảng', () => {
  it('Phiên: 6 ô (Ngày = ô ngày, 4 trường chữ dài = nhiều dòng, Chủ đề = 1 dòng, bắt buộc); KHÔNG có ô Bảng / Bản ghi', async () => {
    expect(GEN_PROPOSAL_EDITABLE.kho_create).toEqual(['record']);
    expect(GEN_PROPOSAL_EDITABLE.kho_update).toEqual(['record']);
    renderPanel();
    showProposal(PHIEN);
    const card = cardOf();
    await userEvent.click(within(card).getByRole('button', { name: 'Sửa' }));
    const form = within(card).getByTestId('gen-kho-form');
    expect(form.querySelectorAll('input, textarea, select')).toHaveLength(6);
    const topic = within(form).getByLabelText('Chủ đề');
    expect(topic.tagName).toBe('INPUT');
    expect(topic).toHaveValue('Gen-Harness v0.1.50 — Gen nhớ và ghi Kho');
    expect(within(form).getByText('Bắt buộc')).toBeInTheDocument();
    expect(within(form).getByLabelText('Ngày')).toHaveAttribute('type', 'date');
    expect(within(form).getByLabelText('Ngày')).toHaveValue('2026-10-09');
    for (const f of ['Đã chốt', 'Đang bàn', 'Việc tiếp', 'Cảnh báo']) expect(within(form).getByLabelText(f).tagName).toBe('TEXTAREA');
    expect(within(form).getByLabelText('Đang bàn')).toHaveValue(''); // trường chưa có trong record ⇒ ô trống
    expect(within(card).queryByLabelText('Bảng')).toBeNull();
    expect(within(card).queryByLabelText('Bản ghi')).toBeNull();
    // Xoá Chủ đề (bắt buộc) ⇒ khoá Xác nhận.
    await userEvent.clear(topic);
    expect(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' })).toBeDisabled();
    await userEvent.type(topic, 'Phiên sửa');
    await userEvent.type(within(form).getByLabelText('Đang bàn'), 'Có nên ghi Việc không');
    await userEvent.clear(within(form).getByLabelText('Việc tiếp'));
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    // Chỉ gửi `record` (trường không rỗng); không có bảng / mã.
    expect(writes()[0].body).toEqual({
      fields: { record: { 'Chủ đề': 'Phiên sửa', Ngày: '2026-10-09', 'Đã chốt': 'Gen nhớ tối đa 30 ghi chú', 'Đang bàn': 'Có nên ghi Việc không' } },
    });
  });

  it('Việc: Trạng thái / Ưu tiên là ô chọn (đúng giá trị), Hạn / Ngày bắt đầu / Ngày xong là ô ngày; link phải là https', async () => {
    renderPanel();
    showProposal(VIEC);
    const card = cardOf();
    await userEvent.click(within(card).getByRole('button', { name: 'Sửa' }));
    const form = within(card).getByTestId('gen-kho-form');
    expect(form.querySelectorAll('input, textarea, select')).toHaveLength(7);
    const status = within(form).getByLabelText('Trạng thái');
    const priority = within(form).getByLabelText('Ưu tiên');
    expect(status.tagName).toBe('SELECT');
    expect(priority.tagName).toBe('SELECT');
    expect([...status.querySelectorAll('option')].map((o) => o.value)).toEqual(['', ...KHO_STATUS]);
    expect([...priority.querySelectorAll('option')].map((o) => o.value)).toEqual(['', ...KHO_PRIORITY]);
    expect(status).toHaveValue('Đang làm');
    expect(priority).toHaveValue('P2');
    for (const f of ['Hạn', 'Ngày bắt đầu', 'Ngày xong']) expect(within(form).getByLabelText(f)).toHaveAttribute('type', 'date');
    const link = within(form).getByLabelText('Link Issue/PR');
    await userEvent.type(link, 'http://không-phải-https');
    expect(within(form).getByText('Link phải bắt đầu bằng https://')).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' })).toBeDisabled();
    await userEvent.clear(link);
    await userEvent.type(link, 'https://example.test/pull/50');
    await userEvent.selectOptions(status, 'Chờ duyệt');
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0].body).toEqual({
      fields: { record: { 'Tiêu đề': 'Soạn báo giá ván MDF', 'Trạng thái': 'Chờ duyệt', 'Ưu tiên': 'P2', Hạn: '2026-10-15', 'Link Issue/PR': 'https://example.test/pull/50' } },
    });
  });

  it('kho_update: Sửa điền sẵn trường sẽ ghi; không đổi gì ⇒ không gửi `record`; bỏ Sửa trả lại như cũ', async () => {
    renderPanel();
    showProposal(UPDATE);
    const card = cardOf();
    await userEvent.click(within(card).getByRole('button', { name: 'Sửa' }));
    const form = within(card).getByTestId('gen-kho-form');
    expect(within(form).getByLabelText('Trạng thái')).toHaveValue('Xong');
    expect(within(form).getByLabelText('Ngày xong')).toHaveValue('2026-10-09');
    expect(within(form).getByLabelText('Tiêu đề')).toHaveValue('');
    await userEvent.click(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0].body).toEqual({ fields: {} }); // không đổi ⇒ server giữ nguyên đề xuất
  });

  it('giới hạn ký tự ĐÚNG kho_write.py (Chủ đề 200, chữ dài 2000, Cảnh báo 1000) + bộ đếm; quá dài ⇒ báo lỗi, khoá Xác nhận', async () => {
    renderPanel();
    showProposal(PHIEN);
    const card = cardOf();
    await userEvent.click(within(card).getByRole('button', { name: 'Sửa' }));
    const form = within(card).getByTestId('gen-kho-form');
    expect(within(form).getByLabelText('Chủ đề')).toHaveAttribute('maxlength', String(KHO_TITLE_MAX));
    expect(within(form).getByLabelText('Đã chốt')).toHaveAttribute('maxlength', String(KHO_TEXT_MAX));
    expect(within(form).getByLabelText('Cảnh báo')).toHaveAttribute('maxlength', String(KHO_WARNING_MAX));
    expect(within(form).getByLabelText('Ngày')).not.toHaveAttribute('maxlength');
    expect(within(form).getByTestId('gen-kho-count-Chủ đề')).toHaveTextContent(`${[...'Gen-Harness v0.1.50 — Gen nhớ và ghi Kho'].length}/200`);
    expect(within(form).getByTestId('gen-kho-count-Cảnh báo')).toHaveTextContent('0/1000');
    expect(within(form).queryByTestId('gen-kho-count-Ngày')).toBeNull();
    // Dán vượt giới hạn (máy chủ cũ / sửa thẳng DOM) ⇒ báo lỗi ngay, không đợi 422 sau PIN.
    const warn = within(form).getByLabelText('Cảnh báo');
    warn.removeAttribute('maxlength');
    await userEvent.click(warn);
    await userEvent.paste('x'.repeat(KHO_WARNING_MAX + 1));
    expect(within(form).getByText('Tối đa 1000 ký tự')).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: 'Xác nhận và ghi Kho' })).toBeDisabled();
    expect(writes()).toEqual([]);
  });

  it('kho_update: dưới mỗi ô là giá trị HIỆN TẠI (— khi trống, "chưa đọc" khi Gen không đề xuất sửa) + "Để trống = giữ nguyên"', async () => {
    renderPanel();
    showProposal(UPDATE);
    const card = cardOf();
    await userEvent.click(within(card).getByRole('button', { name: 'Sửa' }));
    const form = within(card).getByTestId('gen-kho-form');
    expect(within(form).getByTestId('gen-kho-keep-note')).toHaveTextContent('Để trống = giữ nguyên trong Kho (không xoá giá trị cũ).');
    expect(within(form).getByText('Hiện tại: Đang làm')).toBeInTheDocument();
    expect(within(form).getByText('Hiện tại: —')).toBeInTheDocument(); // Ngày xong đang trống trong Kho
    expect(within(form).getAllByText('Hiện tại: chưa đọc (Gen không đề xuất sửa trường này)')).toHaveLength(KHO_FIELDS.Việc.length - 2);
    expect(within(form).queryByText('Bắt buộc')).toBeNull(); // sửa bản ghi: không có trường bắt buộc
    // Thẻ tạo mới không có dòng "Hiện tại".
    expect(khoCurrent(PHIEN as never, 'Chủ đề')).toBeNull();
    expect(khoCurrent(UPDATE as never, 'Trạng thái')).toBe('Đang làm');
    expect(khoCurrent(UPDATE as never, 'Ngày xong')).toBe('');
    expect(khoCurrent(UPDATE as never, 'Tiêu đề')).toBeNull();
  });

  it('hàm thuần: changedFields / initialDraft / khoDraftRecord / khoDraftValid / khoFieldKind / khoBangOf', () => {
    expect(initialDraft(PHIEN)).toEqual({ 'Chủ đề': 'Gen-Harness v0.1.50 — Gen nhớ và ghi Kho', Ngày: '2026-10-09', 'Đã chốt': 'Gen nhớ tối đa 30 ghi chú', 'Đang bàn': '', 'Việc tiếp': 'Sếp duyệt đề xuất PHIEN đầu tiên', 'Cảnh báo': '' });
    expect(changedFields(PHIEN, initialDraft(PHIEN))).toEqual({});
    expect(changedFields(PHIEN, { ...initialDraft(PHIEN), 'Chủ đề': 'Khác', evil: 'x', bang: 'Việc' })).toEqual({
      record: { 'Chủ đề': 'Khác', Ngày: '2026-10-09', 'Đã chốt': 'Gen nhớ tối đa 30 ghi chú', 'Việc tiếp': 'Sếp duyệt đề xuất PHIEN đầu tiên' },
    });
    expect(khoDraftRecord('Việc', { 'Tiêu đề': ' a ', 'Trạng thái': '', Hạn: '2026-10-01', 'Chủ đề': 'không thuộc bảng Việc' })).toEqual({ 'Tiêu đề': 'a', Hạn: '2026-10-01' });
    expect(khoDraftValid(PHIEN as never, { 'Chủ đề': '' })).toBe(false);
    expect(khoDraftValid(PHIEN as never, { 'Chủ đề': 'x' })).toBe(true);
    expect(khoDraftValid(UPDATE as never, { 'Trạng thái': 'Xong' })).toBe(true);
    expect(khoDraftValid(UPDATE as never, {})).toBe(false);
    expect(khoDraftValid(PHIEN as never, { 'Chủ đề': 'x'.repeat(KHO_TITLE_MAX) })).toBe(true);
    expect(khoDraftValid(PHIEN as never, { 'Chủ đề': 'x'.repeat(KHO_TITLE_MAX + 1) })).toBe(false);
    expect(khoDraftValid(PHIEN as never, { 'Chủ đề': 'x', 'Cảnh báo': 'y'.repeat(KHO_WARNING_MAX + 1) })).toBe(false);
    expect(khoDraftValid(UPDATE as never, { 'Trạng thái': 'Xong', 'Tiêu đề': 'ố'.repeat(KHO_TITLE_MAX) })).toBe(true); // đếm theo ký tự
    expect(khoLengthError('Phiên', 'Đã chốt', 'a'.repeat(KHO_TEXT_MAX + 1))).toBe('Tối đa 2000 ký tự');
    expect(khoLengthError('Phiên', 'Đã chốt', `  ${'a'.repeat(KHO_TEXT_MAX)}  `)).toBeNull();
    expect(khoMaxLen('Việc', 'Tiêu đề')).toBe(KHO_TITLE_MAX);
    expect(khoMaxLen('Phiên', 'Cảnh báo')).toBe(KHO_WARNING_MAX);
    expect(khoMaxLen('Việc', 'Link Issue/PR')).toBe(KHO_TEXT_MAX);
    expect(khoFieldKind('Việc', 'Trạng thái')).toBe('status');
    expect(khoFieldKind('Việc', 'Ưu tiên')).toBe('priority');
    expect(khoFieldKind('Việc', 'Ngày xong')).toBe('date');
    expect(khoFieldKind('Phiên', 'Ngày')).toBe('date');
    expect(khoFieldKind('Phiên', 'Việc tiếp')).toBe('long');
    expect(khoFieldKind('Phiên', 'Chủ đề')).toBe('text');
    expect(khoBangOf(UPDATE as never)).toBe('Việc');
    expect(khoBangOf({ ...UPDATE, labels: {}, fields: { ma: 'PHIEN-3', record: {} } } as never)).toBe('Phiên');
    expect(khoBangOf({ ...UPDATE, labels: {}, fields: { ma: 'XYZ-3', record: {} } } as never)).toBeNull();
  });
});

describe('Chuông: kind "gen.kho_proposal" (F-87)', () => {
  it('có biểu tượng cơ sở dữ liệu; bấm mở link /overview?gen=<hội thoại>', async () => {
    notifications = {
      items: [{ id: 'nt1', kind: 'gen.kho_proposal', title: 'Gen đề xuất ghi Kho · Phiên v0.1.50', body: 'Sếp xem và Xác nhận để ghi Phiên vào Kho dữ liệu', link: '/overview?gen=conv-1', created_at: '2026-10-09T01:00:00Z', read: false }],
      unread: 1,
    };
    queryClient.setQueryData(qk.notifications, notifications);
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <NotificationBell />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await userEvent.click(screen.getByRole('button', { name: /Thông báo/ }));
    const item = await screen.findByRole('button', { name: /Gen đề xuất ghi Kho · Phiên v0.1.50/ });
    expect(item.querySelector('[data-icon="database"]')).not.toBeNull();
    expect(item.querySelector('[data-icon="bell"]')).toBeNull();
  });
});

// ── KHO_FIELDS khớp tệp Python (nguồn sự thật) ─────────────────────────────────────────────────────────────

const KHO_PY = resolve(__dirname, '../../../api/gh/hub_link/kho_write.py');

/** Hằng mong đợi (hợp đồng v0.1.50) — dùng khi kho_write.py chưa có trong nhánh; sau khi gộp, test đọc tệp thật. */
const EXPECTED = {
  Phiên: ['Chủ đề', 'Ngày', 'Đã chốt', 'Đang bàn', 'Việc tiếp', 'Cảnh báo'],
  Việc: ['Tiêu đề', 'Trạng thái', 'Ưu tiên', 'Hạn', 'Link Issue/PR', 'Ngày bắt đầu', 'Ngày xong'],
} as const;

interface Tok {
  kind: 'str' | 'open' | 'close' | 'punct';
  value: string;
  depth: number;
}

/** Tách chuỗi / ngoặc / dấu `:` `,` `=` của mã Python (bỏ chú thích `#`); đủ cho khai báo hằng dạng dict/tuple. */
function tokenize(src: string): Tok[] {
  const out: Tok[] = [];
  let depth = 0;
  for (let i = 0; i < src.length; i += 1) {
    const c = src[i];
    if (c === '#') {
      while (i < src.length && src[i] !== '\n') i += 1;
    } else if (c === '"' || c === "'") {
      const triple = src.startsWith(c.repeat(3), i);
      const quote = triple ? c.repeat(3) : c;
      let j = i + quote.length;
      let val = '';
      while (j < src.length && !src.startsWith(quote, j)) {
        if (src[j] === '\\') j += 1;
        val += src[j];
        j += 1;
      }
      out.push({ kind: 'str', value: val, depth });
      i = j + quote.length - 1;
    } else if ('([{'.includes(c)) {
      out.push({ kind: 'open', value: c, depth });
      depth += 1;
    } else if (')]}'.includes(c)) {
      depth -= 1;
      out.push({ kind: 'close', value: c, depth });
    } else if (':,='.includes(c)) {
      out.push({ kind: 'punct', value: c, depth });
    }
  }
  return out;
}

/** `KHO_FIELDS` của kho_write.py → {bảng: [trường…]} (dict bảng → tuple/list tên trường, hoặc bảng → dict tên trường → …). */
function pythonKhoFields(src: string): Record<string, string[]> {
  const m = /^KHO_FIELDS\b[^=\n]*=/m.exec(src);
  expect(m, 'kho_write.py phải khai báo KHO_FIELDS = …').not.toBeNull();
  const toks = tokenize(src.slice((m as RegExpExecArray).index + (m as RegExpExecArray)[0].length));
  const first = toks[0];
  expect(first?.kind, 'KHO_FIELDS phải là dict {bảng: …}').toBe('open');
  const out: Record<string, string[]> = {};
  let bang: string | null = null;
  for (let i = 1; i < toks.length; i += 1) {
    const t = toks[i];
    if (t.kind === 'close' && t.depth === 0) break; // hết khai báo
    if (t.depth === 1 && t.kind === 'str' && toks[i + 1]?.value === ':') {
      bang = t.value;
      out[bang] = [];
    } else if (bang && t.kind === 'str' && t.depth === 2 && toks[i - 1]?.value !== ':') {
      out[bang].push(t.value); // phần tử tuple/list, hoặc khoá của dict tên trường
    }
  }
  return out;
}

describe('KHO_FIELDS (TS) ≡ KHO_FIELDS (kho_write.py)', () => {
  it('hằng TS đúng hợp đồng: Phiên 6 trường, Việc 7 trường, trường bắt buộc, trường ngày, trạng thái, ưu tiên', () => {
    expect({ Phiên: [...KHO_FIELDS.Phiên], Việc: [...KHO_FIELDS.Việc] }).toEqual({ Phiên: [...EXPECTED.Phiên], Việc: [...EXPECTED.Việc] });
    expect(KHO_REQUIRED).toEqual({ Phiên: 'Chủ đề', Việc: 'Tiêu đề' });
    expect(KHO_DATE_FIELDS).toEqual({ Phiên: ['Ngày'], Việc: ['Hạn', 'Ngày bắt đầu', 'Ngày xong'] });
    expect([...KHO_STATUS]).toEqual(['Chờ', 'Đang làm', 'Chờ duyệt', 'Xong']);
    expect([...KHO_PRIORITY]).toEqual(['P1', 'P2', 'P3']);
    for (const bang of ['Phiên', 'Việc'] as const) {
      expect(KHO_FIELDS[bang]).toContain(KHO_REQUIRED[bang]);
      for (const d of KHO_DATE_FIELDS[bang]) expect(KHO_FIELDS[bang]).toContain(d);
    }
  });

  it('tệp kho_write.py (nếu đã có trong nhánh) khai báo KHO_FIELDS y hệt bản TS — cùng tên, cùng thứ tự', () => {
    if (!existsSync(KHO_PY)) {
      // Gói api chưa gộp vào nhánh này: so với hằng mong đợi ở trên (đã kiểm). Sau khi gộp, nhánh này tự đọc tệp thật.
      expect(existsSync(KHO_PY)).toBe(false);
      return;
    }
    const src = readFileSync(KHO_PY, 'utf8');
    const py = pythonKhoFields(src);
    expect(Object.keys(py).sort()).toEqual(['Phiên', 'Việc']);
    expect(py.Phiên).toEqual([...KHO_FIELDS.Phiên]);
    expect(py.Việc).toEqual([...KHO_FIELDS.Việc]);
    // Giá trị chọn của hai trường: tệp Python phải nhắc đúng các chuỗi này (dạng chuỗi trong mã).
    const has = (s: string) => src.includes(`"${s}"`) || src.includes(`'${s}'`);
    for (const v of [...KHO_STATUS, ...KHO_PRIORITY]) expect(has(v), `kho_write.py thiếu giá trị "${v}"`).toBe(true);
    // Giới hạn độ dài: form Sửa chặn đúng như máy chủ (không để Sếp nhập mã PIN rồi mới nhận 422).
    const num = (name: string) => Number(new RegExp(`^${name}\\s*=\\s*(\\d+)`, 'm').exec(src)?.[1]);
    expect({ title: num('TITLE_MAX'), text: num('TEXT_MAX'), warning: num('WARNING_MAX') }).toEqual({ title: KHO_TITLE_MAX, text: KHO_TEXT_MAX, warning: KHO_WARNING_MAX });
    expect(src).toContain('WARNING_FIELD = "Cảnh báo"');
  });

  it('bộ phân tích Python: hiểu dict → tuple, dict → dict (bỏ giá trị chuỗi), bỏ chú thích', () => {
    const a = pythonKhoFields('X = 1\nKHO_FIELDS: dict[str, tuple[str, ...]] = {\n  "Phiên": ("Chủ đề", "Ngày"),  # chú thích "bậy"\n  "Việc": ("Tiêu đề",),\n}\nY = 2\n');
    expect(a).toEqual({ Phiên: ['Chủ đề', 'Ngày'], Việc: ['Tiêu đề'] });
    const b = pythonKhoFields('KHO_FIELDS = {"Phiên": {"Chủ đề": "text", "Ngày": "date"}, "Việc": {"Tiêu đề": "text"}}');
    expect(b).toEqual({ Phiên: ['Chủ đề', 'Ngày'], Việc: ['Tiêu đề'] });
  });
});
