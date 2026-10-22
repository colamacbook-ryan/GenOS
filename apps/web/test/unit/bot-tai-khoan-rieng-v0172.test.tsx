/**
 * v0.1.72 (G8 console) — Bot tài khoản riêng: Kênh quản trị Zalo/WhatsApp (mã ghép 6 số + đếm ngược 10 phút, đã ghép/huỷ
 * ghép), «Loại tài khoản» ở thẻ kênh, «Chế độ hỗ trợ» của nhóm (lý do không bật được, 423 → hộp PIN), nhãn sự kiện mới
 * của Nhật ký lắng nghe, nhãn Nhật ký hành động, chỉ số «Tin bot tự gửi hôm nay». Bot CHỈ nhắn Sếp khi có việc đặc biệt
 * (QĐ Sếp 18/10): giao diện không có tuỳ chọn tóm tắt/gộp. Lỗi luôn là chuỗi + «Chi tiết kỹ thuật», không render object.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { ApiError, type Channel, type GroupSupportView, type OwnerChannelView } from '@gen-harness/contracts';
import { OwnerChannelCard } from '../../src/screens/connections/OwnerChannelCard';
import { ownerChannelTypes } from '../../src/screens/connections/connectionsModel';
import {
  ACCOUNT_KIND_WARNING,
  ONLY_SPECIAL_NOTE,
  OWNER_ERROR_TEXT,
  accountKindChangeText,
  addedByLabel,
  autoSendTodayText,
  basisLabel,
  ownerChannelKey,
  ownerErrorText,
  ownerPairPollMs,
  ownerStatusText,
  pairCountdown,
  pairInstruction,
  parseLimit,
  parseMaxPerHour,
  quietText,
  spacedCode,
} from '../../src/screens/connections/ownerChannelModel';
import { ChannelCard } from '../../src/screens/system/ChannelCard';
import { GroupSupportDialog } from '../../src/screens/relations/GroupSupportDialog';
import { EVENT_KIND_LABEL, autoSendsForChannel, eventKindLabel } from '../../src/screens/data/listenLogModel';
import { ACTION_LABEL, actionLabel } from '../../src/screens/system/auditLabels';
import { usePinStore } from '../../src/lib/pinStore';
import { queryClient } from '../../src/lib/queryClient';
import { qk2 } from '../../src/lib/dataQueries';
import { qk } from '../../src/lib/queries';

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json', 'X-Request-ID': 'req0123456789abc' },
  });
const PIN_423 = () => json(423, { status: 423, code: 'PIN_REQUIRED', title: 'Cần PIN', request_id: 'req0123456789abc' });

const PREFS = { quiet_start: 21, quiet_end: 7, max_per_hour: 6, min_severity: 'important' as const };
const UNPAIRED: OwnerChannelView = {
  channel: 'zalo', session: { id: 'sess-1', account_kind: 'bot' }, bot_account: true, paired: false, enabled: false,
  owner_masked: null, owner_display: null, paired_at: null, pair_pending: false, pair_expires_at: null, report_prefs: PREFS,
  support_groups: 0,
};
const PAIRED: OwnerChannelView = {
  ...UNPAIRED, paired: true, enabled: true, owner_masked: '•••6789', owner_display: 'Anh Cơ', paired_at: '2026-10-18T02:00:00Z',
  support_groups: 2,
};
const PERSONAL: OwnerChannelView = { ...UNPAIRED, session: { id: 'sess-1', account_kind: 'owner_self' }, bot_account: false };

interface Call {
  path: string;
  method: string;
  body: unknown;
}

/** Máy chủ giả: mọi thao tác ghi trả 423 lần đầu (hộp PIN tự xác nhận) rồi mới chạy. */
function server(handler: (c: Call) => Response | null) {
  const calls: Call[] = [];
  let pinOk = false;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(url), 'http://x').pathname.replace('/api/v1', '');
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      const c = { path, method, body };
      calls.push(c);
      if (method !== 'GET' && !pinOk) return PIN_423();
      return handler(c) ?? json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
    }),
  );
  const unsub = usePinStore.subscribe((s, prev) => {
    if (s.open && !prev.open) {
      pinOk = true;
      queueMicrotask(() => usePinStore.getState().finish(true));
    }
  });
  unsubs.push(unsub);
  return calls;
}

function asOwner() {
  queryClient.setQueryData(qk.me, {
    id: 'u', email: 'o@x', display_name: 'Sếp', role: { code: 'owner', name: 'Owner' },
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' }, addressing: { self: 'Anh', bot_calls_me: 'Sếp' },
    pin_verified_until: null, permissions: { 'system.manage': 'all' },
  });
}

function wrap(ui: React.ReactNode) {
  asOwner();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

let unsubs: Array<() => void> = [];
beforeEach(() => {
  queryClient.clear();
  queryClient.setDefaultOptions({ queries: { retry: false } });
});
afterEach(() => {
  unsubs.forEach((u) => u());
  unsubs = [];
  usePinStore.setState({ open: false, waiters: [] });
  vi.unstubAllGlobals();
  queryClient.clear();
});
const writes = (calls: Call[]) => calls.filter((c) => c.method !== 'GET');

describe('mô hình (hàm thuần)', () => {
  it('mã ghép: câu hướng dẫn theo kênh, mã 6 số tách đôi, đếm ngược 10 phút', () => {
    expect(pairInstruction('zalo', '123456')).toBe('Từ Zalo của Sếp nhắn cho bot: 123456');
    expect(pairInstruction('whatsapp', '654321')).toBe('Từ WhatsApp của Sếp nhắn cho bot: 654321');
    expect(spacedCode('123456')).toBe('123 456');
    expect(spacedCode('12')).toBe('12');
    const t0 = Date.parse('2026-10-18T02:00:00Z');
    const exp = '2026-10-18T02:10:00Z';
    expect(pairCountdown(exp, t0)).toMatchObject({ label: '10:00', pct: 100 });
    expect(pairCountdown(exp, t0 + 61_000).label).toBe('08:59');
    expect(pairCountdown(exp, t0 + 600_000).ms).toBe(0);
    expect(pairCountdown(exp, t0 + 700_000)).toMatchObject({ label: '00:00', pct: 0 });
    expect(pairCountdown('rác', t0).ms).toBe(0);
    expect(ownerPairPollMs({ ...UNPAIRED, pair_pending: true })).toBeGreaterThan(0);
    expect(ownerPairPollMs(PAIRED)).toBe(false);
    expect(ownerPairPollMs(null)).toBe(false);
  });

  it('cài đặt báo cáo: chỉ việc đặc biệt, giờ yên lặng, số tin/giờ 1–60', () => {
    expect(ONLY_SPECIAL_NOTE).toContain('Bot chỉ nhắn Sếp khi có việc đặc biệt');
    expect(ONLY_SPECIAL_NOTE).toContain('Không có báo cáo định kỳ');
    expect(quietText({ quiet_start: 21, quiet_end: 7 })).toBe('Từ 21:00 đến 07:00 bot chỉ nhắn việc khẩn; việc khác đợi tới 07:00.');
    expect(quietText({ quiet_start: 0, quiet_end: 0 })).toContain('Không đặt giờ yên lặng');
    expect(parseMaxPerHour('6')).toBe(6);
    expect(parseMaxPerHour('60')).toBe(60);
    expect(parseMaxPerHour('0')).toBeNull();
    expect(parseMaxPerHour('61')).toBeNull();
    expect(parseMaxPerHour('a')).toBeNull();
    expect(parseLimit('0', 50)).toBe(0);
    expect(parseLimit('51', 50)).toBeNull();
    expect(parseLimit('-1', 50)).toBeNull();
  });

  it('trạng thái + lỗi luôn là chuỗi tiếng Việt; mã lạ / detail object không lộ object', () => {
    const f = (iso: string) => `T(${iso})`;
    expect(ownerStatusText(PERSONAL, f)).toContain('tài khoản cá nhân của Sếp');
    expect(ownerStatusText(UNPAIRED, f)).toContain('Chưa ghép');
    expect(ownerStatusText(PAIRED, f)).toBe('Đã ghép với Anh Cơ · •••6789 · từ T(2026-10-18T02:00:00Z) · đang bật');
    for (const [code, text] of Object.entries(OWNER_ERROR_TEXT)) {
      expect(ownerErrorText(new ApiError(409, { code, title: 'x' }))).toBe(text);
    }
    const odd = ownerErrorText(new ApiError(400, { code: 'MA_LA', title: 'Lỗi lạ', detail: { a: 1 } as unknown as string }));
    expect(typeof odd).toBe('string');
    expect(odd).not.toContain('[object Object]');
  });

  it('loại tài khoản: cảnh báo cá nhân không bật được kênh quản trị/hỗ trợ; đổi khỏi bot ⇒ huỷ ghép + tắt hỗ trợ', () => {
    expect(ACCOUNT_KIND_WARNING).toContain('không bật được Kênh quản trị và Chế độ hỗ trợ');
    const off = accountKindChangeText('bot', 'owner_self', PAIRED);
    expect(off).toContain('Kênh quản trị sẽ bị huỷ ghép');
    expect(off).toContain('2 nhóm đang hỗ trợ sẽ tắt');
    expect(accountKindChangeText('bot', 'owner_self', UNPAIRED)).toContain('mọi Chế độ hỗ trợ sẽ tắt');
    expect(accountKindChangeText('owner_self', 'bot', PERSONAL)).toContain('tài khoản RIÊNG cho bot');
  });

  it('ai thêm bot, nhãn trường hợp, chỉ số hôm nay, kênh có kênh quản trị', () => {
    expect(addedByLabel('owner')).toBe('Sếp');
    expect(addedByLabel('other')).toBe('Người khác');
    expect(addedByLabel('unknown')).toBe('Không rõ');
    expect(addedByLabel(undefined)).toBe('—');
    expect(addedByLabel(null)).toBe('—');
    expect(basisLabel('owner_dm')).toBe('Kênh quản trị');
    expect(basisLabel('owner_tag')).toBe('Sếp gọi');
    expect(basisLabel('support')).toBe('Hỗ trợ');
    expect(autoSendTodayText({ owner_dm: 1, owner_tag: 2, support: 3, escalated: 4, total: 6 })).toBe(
      'Tin bot tự gửi hôm nay: 6 (Sếp gọi 2 · Hỗ trợ 3 · Chuyển Sếp 4)',
    );
    expect(autoSendTodayText(null)).toBe('Tin bot tự gửi hôm nay: 0');
    expect(
      ownerChannelTypes([
        { type: 'whatsapp', installed: true },
        { type: 'telegram', installed: true },
        { type: 'zalo', installed: false },
      ]),
    ).toEqual(['whatsapp']);
    expect(ownerChannelTypes(undefined)).toEqual([]);
  });

  it('nhãn sự kiện mới của Nhật ký lắng nghe; loại cũ giữ «Sự kiện»; lọc tin bot tự gửi theo kênh', () => {
    const kinds = ['group_added_by_other', 'bot_removed', 'owner_called', 'owner_paired', 'owner_unpaired', 'support_on', 'support_off', 'support_escalated'];
    for (const k of kinds) expect(EVENT_KIND_LABEL[k]).toBeTruthy();
    expect(eventKindLabel('owner_called')).toBe('Sếp gọi');
    expect(eventKindLabel('support_escalated')).toBe('Chuyển Sếp');
    expect(eventKindLabel('group_new')).toBe('Sự kiện');
    expect(autoSendsForChannel([{ channel: 'zalo' }, { channel: 'whatsapp' }], 'zalo')).toEqual([{ channel: 'zalo' }]);
    expect(autoSendsForChannel([{ channel: 'zalo' }], '')).toHaveLength(1);
  });

  it('Nhật ký hành động: mọi action owner_channel.* / autosend.* / channel.account_kind có nhãn tiếng Việt', () => {
    const actions = [
      'owner_channel.paired', 'owner_channel.unpaired', 'owner_channel.pair_start', 'owner_channel.update', 'owner_channel.command',
      'owner_channel.nghe', 'owner_channel.bo', 'owner_channel.ho_tro_bat', 'owner_channel.ho_tro_tat', 'owner_channel.support_on',
      'owner_channel.support_off', 'autosend.issued', 'autosend.sent', 'autosend.failed', 'autosend.refused', 'channel.account_kind',
    ];
    for (const a of actions) {
      expect(ACTION_LABEL[a], a).toBeTruthy();
      expect(actionLabel(a)).not.toContain('thao tác khác');
    }
    expect(actionLabel('owner_channel.moi_them')).toBe('Kênh quản trị — thao tác khác');
    expect(actionLabel('autosend.moi_them')).toBe('Tin bot tự gửi — thao tác khác');
  });
});

describe('thẻ Kênh quản trị (Zalo)', () => {
  it('chưa ghép: Lấy mã ghép (PIN) → 6 số + đếm ngược + «Từ Zalo của Sếp nhắn cho bot: 123456»; mã KHÔNG vào cache', async () => {
    let view: OwnerChannelView = UNPAIRED;
    const expires = new Date(Date.now() + 600_000).toISOString();
    const calls = server((c) => {
      if (c.path === '/owner-channel/zalo' && c.method === 'GET') return json(200, view);
      if (c.path === '/owner-channel/zalo/pair/start' && c.method === 'POST') {
        view = { ...view, pair_pending: true, pair_expires_at: expires };
        return json(200, { ...view, pair: { code: '123456', expires_at: expires } });
      }
      return null;
    });
    wrap(<OwnerChannelCard ch="zalo" />);
    const card = within(await screen.findByTestId('owner-channel-zalo'));
    expect(card.getByRole('switch', { name: 'Bật Kênh quản trị' })).toHaveAttribute('aria-disabled', 'true');
    expect(card.queryByRole('button', { name: 'Huỷ ghép' })).toBeNull();
    await userEvent.setup().click(card.getByRole('button', { name: 'Lấy mã ghép' }));
    expect(await card.findByTestId('owner-pair-code')).toHaveTextContent('123 456');
    expect(card.getByTestId('owner-pair-instruction')).toHaveTextContent('Từ Zalo của Sếp nhắn cho bot: 123456');
    expect(card.getByTestId('owner-pair-countdown').textContent).toMatch(/^còn (10:00|09:5\d)$/);
    expect(writes(calls).filter((c) => c.path === '/owner-channel/zalo/pair/start')).toHaveLength(2); // 423 rồi gửi lại
    expect(JSON.stringify(queryClient.getQueryData(ownerChannelKey('zalo')))).not.toContain('123456');
  });

  it('mã đã hết hạn ⇒ báo hết hạn, gợi ý lấy mã mới (không còn hiện 6 số)', async () => {
    const past = new Date(Date.now() - 1000).toISOString();
    server((c) => {
      if (c.path === '/owner-channel/zalo' && c.method === 'GET') return json(200, UNPAIRED);
      if (c.path === '/owner-channel/zalo/pair/start') return json(200, { ...UNPAIRED, pair: { code: '123456', expires_at: past } });
      return null;
    });
    wrap(<OwnerChannelCard ch="zalo" />);
    const card = within(await screen.findByTestId('owner-channel-zalo'));
    await userEvent.setup().click(card.getByRole('button', { name: 'Lấy mã ghép' }));
    expect(await card.findByTestId('owner-pair-expired')).toHaveTextContent('Mã ghép đã hết hạn');
    expect(card.queryByTestId('owner-pair-code')).toBeNull();
  });

  it('đã ghép: hiện ID che, Huỷ ghép qua hộp xác nhận (PIN) → DELETE → về chưa ghép', async () => {
    let view: OwnerChannelView = PAIRED;
    const calls = server((c) => {
      if (c.path === '/owner-channel/zalo' && c.method === 'GET') return json(200, view);
      if (c.path === '/owner-channel/zalo/pair' && c.method === 'DELETE') {
        view = { ...UNPAIRED };
        return json(200, view);
      }
      return null;
    });
    wrap(<OwnerChannelCard ch="zalo" />);
    const card = within(await screen.findByTestId('owner-channel-zalo'));
    expect(card.getByTestId('owner-status')).toHaveTextContent('•••6789');
    expect(card.getByRole('switch', { name: 'Bật Kênh quản trị' })).toHaveAttribute('aria-checked', 'true');
    const user = userEvent.setup();
    await user.click(card.getByRole('button', { name: 'Huỷ ghép' }));
    const dlg = await screen.findByRole('dialog');
    expect(dlg).toHaveTextContent('2 nhóm đang hỗ trợ sẽ tắt chế độ hỗ trợ');
    await user.click(within(dlg).getByRole('button', { name: 'Huỷ ghép' }));
    await waitFor(() => expect(card.getByTestId('owner-status')).toHaveTextContent('Chưa ghép'));
    expect(writes(calls).filter((c) => c.path === '/owner-channel/zalo/pair' && c.method === 'DELETE')).toHaveLength(2);
  });

  it('báo cáo chủ động CHỈ có mức tối thiểu, giờ yên lặng, số tin/giờ — không có tóm tắt/gộp; Lưu gửi đúng 4 khoá (PIN)', async () => {
    const calls = server((c) => {
      if (c.path === '/owner-channel/zalo' && c.method === 'GET') return json(200, PAIRED);
      if (c.path === '/owner-channel/zalo' && c.method === 'PUT') return json(200, { ...PAIRED, report_prefs: { ...PREFS, ...(c.body as { report_prefs: object }).report_prefs } });
      return null;
    });
    wrap(<OwnerChannelCard ch="zalo" />);
    const prefs = within(await screen.findByTestId('owner-prefs'));
    expect(prefs.getByTestId('owner-only-special')).toHaveTextContent('Bot chỉ nhắn Sếp khi có việc đặc biệt');
    expect(screen.getByTestId('owner-channel-zalo').textContent ?? '').not.toMatch(/tóm tắt|gộp|định kỳ hằng/i);
    const user = userEvent.setup();
    const max = prefs.getByLabelText(/Số tin tối đa mỗi giờ/);
    await user.clear(max);
    await user.type(max, '99');
    await user.click(prefs.getByRole('button', { name: 'Lưu cài đặt báo cáo' }));
    expect(prefs.getByText('Nhập số từ 1 đến 60.')).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(0);
    await user.clear(max);
    await user.type(max, '3');
    await user.selectOptions(prefs.getByLabelText('Mức tối thiểu để bot nhắn'), 'urgent');
    await user.click(prefs.getByRole('button', { name: 'Lưu cài đặt báo cáo' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(2));
    expect(writes(calls)[1]!.body).toEqual({ report_prefs: { min_severity: 'urgent', quiet_start: 21, quiet_end: 7, max_per_hour: 3 } });
  });

  it('kênh chạy bằng tài khoản cá nhân: nói rõ chưa dùng được, không có nút lấy mã / công tắc', async () => {
    server((c) => (c.path === '/owner-channel/zalo' ? json(200, PERSONAL) : null));
    wrap(<OwnerChannelCard ch="zalo" />);
    const card = within(await screen.findByTestId('owner-channel-zalo'));
    expect(card.getByTestId('owner-status')).toHaveTextContent('tài khoản cá nhân của Sếp');
    expect(card.queryByRole('button', { name: 'Lấy mã ghép' })).toBeNull();
    expect(card.queryByRole('switch')).toBeNull();
  });

  it('bật khi máy chủ từ chối (409 OWNER_NOT_PAIRED): câu thân thiện + Chi tiết kỹ thuật, không lộ object', async () => {
    server((c) => {
      if (c.method === 'GET') return json(200, { ...PAIRED, enabled: false });
      return json(409, { status: 409, code: 'OWNER_NOT_PAIRED', title: 'Chưa ghép', detail: { lop: 'x' } });
    });
    wrap(<OwnerChannelCard ch="zalo" />);
    const card = within(await screen.findByTestId('owner-channel-zalo'));
    await userEvent.setup().click(card.getByRole('switch', { name: 'Bật Kênh quản trị' }));
    const alert = await card.findByRole('alert');
    expect(alert).toHaveTextContent(OWNER_ERROR_TEXT.OWNER_NOT_PAIRED!);
    expect(alert.textContent).not.toContain('[object Object]');
  });
});

describe('thẻ kênh › Loại tài khoản', () => {
  const zalo: Channel = {
    type: 'zalo', name: 'Zalo', installed: true, id: 'c1', state: 'active', account_label: 'Bot', session_id: 'sess-1',
    started_at: new Date().toISOString(), groups_listening: 3, outbound_queued: 0, last_heartbeat_at: new Date().toISOString(),
    stats: null, qr: null,
  };

  it('đổi «Tài khoản riêng của bot» → «Tài khoản cá nhân của Sếp»: hộp cảnh báo huỷ ghép + tắt hỗ trợ, PUT (PIN) đúng phiên', async () => {
    let view: OwnerChannelView = PAIRED;
    const calls = server((c) => {
      if (c.path === '/owner-channel/zalo' && c.method === 'GET') return json(200, view);
      if (c.path === '/channels/zalo/sessions/sess-1/account-kind' && c.method === 'PUT') {
        view = { ...PERSONAL };
        return json(200, view);
      }
      if (c.path === '/system/health') return json(200, { issues: [] });
      return null;
    });
    queryClient.setQueryData(qk2.channels, [zalo]);
    wrap(<ChannelCard channel={zalo} canManage />);
    const row = within(await screen.findByTestId('account-kind-zalo'));
    expect(row.getByTestId('account-kind-hint')).toHaveTextContent('không bật được Kênh quản trị và Chế độ hỗ trợ');
    const select = row.getByLabelText('Loại tài khoản Zalo') as HTMLSelectElement;
    expect(select.value).toBe('bot');
    const user = userEvent.setup();
    await user.selectOptions(select, 'owner_self');
    const dlg = await screen.findByRole('dialog');
    expect(within(dlg).getByTestId('account-kind-confirm')).toHaveTextContent('Kênh quản trị sẽ bị huỷ ghép và 2 nhóm đang hỗ trợ sẽ tắt.');
    expect(writes(calls)).toHaveLength(0); // chưa xác nhận thì chưa gửi
    await user.click(within(dlg).getByRole('button', { name: 'Đổi loại tài khoản' }));
    await waitFor(() => expect((row.getByLabelText('Loại tài khoản Zalo') as HTMLSelectElement).value).toBe('owner_self'));
    const puts = writes(calls).filter((c) => c.path === '/channels/zalo/sessions/sess-1/account-kind');
    expect(puts).toHaveLength(2); // 423 PIN rồi gửi lại
    expect(puts[1]!.body).toEqual({ account_kind: 'owner_self' });
  });
});

describe('Danh bạ nhóm › Chế độ hỗ trợ', () => {
  const base: GroupSupportView = {
    group: { id: 'g1', code: 'GRP-ZL-0001', name: 'Đại lý miền Trung', channel: 'zalo', listen_mode: 'silent', added_by_kind: 'owner' },
    enabled: false, since: null, via: null, until_at: null,
    limits: { per_group_hour: 20, per_member_hour: 5, min_gap_s: 20, per_channel_day: 150, max_len: 600, max_age_s: 300 },
    can_enable: true, blocked_reasons: [], history: [],
  };

  it('chưa đủ điều kiện: công tắc khoá, hiện đúng lý do máy chủ gửi; không gửi gì', async () => {
    const calls = server((c) =>
      c.path === '/groups/g1/support'
        ? json(200, { ...base, group: { ...base.group, added_by_kind: 'other' }, can_enable: false, blocked_reasons: ['Kênh chưa chạy bằng tài khoản riêng của bot', 'Nhóm không do Sếp thêm bot và Sếp chưa duyệt nghe'] })
        : null,
    );
    wrap(<GroupSupportDialog groupId="g1" groupName="Đại lý miền Trung" onClose={() => undefined} />);
    const blocked = await screen.findByTestId('support-blocked');
    expect(blocked).toHaveTextContent('Kênh chưa chạy bằng tài khoản riêng của bot');
    expect(blocked).toHaveTextContent('Nhóm không do Sếp thêm bot và Sếp chưa duyệt nghe');
    expect(screen.getByTestId('support-added-by')).toHaveTextContent('Ai thêm bot vào nhóm: Người khác');
    const sw = screen.getByRole('switch', { name: 'Chế độ hỗ trợ' });
    expect(sw).toHaveAttribute('aria-disabled', 'true');
    await userEvent.setup().click(sw);
    expect(writes(calls)).toHaveLength(0);
  });

  it('bật: 423 → hộp PIN → gửi lại với hết hạn (giờ) + chỉ giới hạn đã đổi; thành «Đang bật»', async () => {
    let view: GroupSupportView = base;
    const calls = server((c) => {
      if (c.path !== '/groups/g1/support') return null;
      if (c.method === 'GET') return json(200, view);
      view = { ...base, enabled: true, since: '2026-10-18T02:00:00Z', via: 'console', until_at: '2026-10-18T10:00:00Z' };
      return json(200, view);
    });
    wrap(<GroupSupportDialog groupId="g1" groupName="Đại lý miền Trung" onClose={() => undefined} />);
    await screen.findByTestId('support-dialog');
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText('Tự tắt sau'), '8');
    const perMember = screen.getByLabelText('Tin/giờ cho mỗi người');
    await user.clear(perMember);
    await user.type(perMember, '3');
    await user.click(screen.getByRole('button', { name: 'Bật chế độ hỗ trợ' }));
    await waitFor(() => expect(screen.getByTestId('support-state')).toHaveTextContent('Đang bật'));
    const puts = writes(calls);
    expect(puts).toHaveLength(2);
    expect(puts[0]!.body).toEqual(puts[1]!.body);
    expect(puts[1]!.body).toEqual({ enabled: true, until_hours: 8, limits: { per_member_hour: 3 } });
  });

  it('409 SUPPORT_NOT_ALLOWED: câu thân thiện + «Chi tiết kỹ thuật» có lý do; không render object', async () => {
    server((c) => {
      if (c.path !== '/groups/g1/support') return null;
      if (c.method === 'GET') return json(200, base);
      return json(409, { status: 409, code: 'SUPPORT_NOT_ALLOWED', title: 'Chưa bật được chế độ hỗ trợ cho nhóm này', detail: 'Bot không còn trong nhóm' });
    });
    wrap(<GroupSupportDialog groupId="g1" groupName="Đại lý miền Trung" onClose={() => undefined} />);
    await screen.findByTestId('support-dialog');
    await userEvent.setup().click(screen.getByRole('button', { name: 'Bật chế độ hỗ trợ' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(OWNER_ERROR_TEXT.SUPPORT_NOT_ALLOWED!);
    expect(within(alert).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(alert.textContent).not.toContain('[object Object]');
  });

  it('đang bật: tắt luôn được (PIN), gửi {enabled:false}', async () => {
    let view: GroupSupportView = { ...base, enabled: true, since: '2026-10-18T02:00:00Z', via: 'console', until_at: null };
    const calls = server((c) => {
      if (c.path !== '/groups/g1/support') return null;
      if (c.method === 'GET') return json(200, view);
      view = base;
      return json(200, view);
    });
    wrap(<GroupSupportDialog groupId="g1" groupName="Đại lý miền Trung" onClose={() => undefined} />);
    expect(await screen.findByTestId('support-state')).toHaveTextContent('không hết hạn');
    await userEvent.setup().click(screen.getByRole('switch', { name: 'Chế độ hỗ trợ' }));
    await waitFor(() => expect(screen.getByTestId('support-state')).toHaveTextContent('Đang tắt'));
    expect(writes(calls).at(-1)!.body).toEqual({ enabled: false });
  });
});
