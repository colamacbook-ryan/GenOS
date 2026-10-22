import type { ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { KHO_LABEL, KHO_LABEL_MAX, relabelKho, type DefaultItem, type HubLink } from '@gen-harness/contracts';
import { HubLinkCard } from '../../src/screens/mcp/HubLinkCard';
import { GenPanel } from '../../src/gen/GenPanel';
import { useGenStore } from '../../src/gen/genStore';
import { proposalErrorView } from '../../src/gen/khoWriteModel';
import { ApiError } from '../../src/lib/api';
import { qk } from '../../src/lib/queries';

/**
 * v0.1.57 (Nợ #30) — ô "Tên Kho (tuỳ chọn)" ở thẻ Gen-hub: Owner tự đặt tên Kho (≤ 40 ký tự, trống = "Kho dữ liệu").
 * - đổi tên khi CHƯA nối Gen-hub vẫn lưu được và PATCH chỉ mang `kho_label` (không đụng địa chỉ/token); quá 40 ký tự bị chặn ngay;
 * - đã đổi ⇒ chip "Đã đổi" + "Về mặc định" (sổ mặc định `kho_label`) gọi POST /defaults/kho_label/reset; ô điền sẵn tên đã lưu.
 */

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

interface Call {
  url: string;
  method: string;
  body: unknown;
}
function mockFetch(handler: (c: Call) => Response | Promise<Response>) {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const c = { url: String(url), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined };
      calls.push(c);
      return handler(c);
    }),
  );
  return calls;
}

const me = {
  id: 'u', email: 'owner@example.test', display_name: 'Anh Cơ', role: { code: 'owner', name: 'owner' },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null,
  permissions: { 'system.read': 'all', 'system.manage': 'all' },
};

function renderCard(ui: ReactElement, user: object = me) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(qk.me, user);
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const NAME = 'Sổ tay Công ty';
const OFF: HubLink = {
  configured: false, enabled: false, status: 'off', server_id: null, endpoint: null, has_token: false,
  allow_public_network: false, token_expires_at: null, days_left: null, last_ok_at: null, last_error: null, health: null,
  kho_label: KHO_LABEL, kho_label_custom: false, kho_label_default: KHO_LABEL, kho_label_max: KHO_LABEL_MAX,
};
const khoItem = (customized: boolean): DefaultItem => ({
  key: 'kho_label', label: 'Tên Kho nối qua Gen-hub', scope: 'org', group: 'Kết nối', resettable: true, customized,
  default_text: `Kho nối qua Gen-hub gọi là “${KHO_LABEL}”`, current_text: `Kho nối qua Gen-hub gọi là “${customized ? NAME : KHO_LABEL}”`,
});
const defaults = (customized: boolean) => ({ items: [khoItem(customized)], customized_count: customized ? 1 : 0, suggestions: [] });

describe('Tên Kho (tuỳ chọn) ở thẻ Gen-hub', () => {
  it('mặc định để trống; đổi tên khi chưa nối Gen-hub chỉ gửi kho_label; quá 40 ký tự bị chặn', async () => {
    let state: HubLink = OFF;
    const calls = mockFetch((c) => {
      if (c.url.includes('/defaults')) return json(200, defaults(state.kho_label_custom === true));
      if (c.url.includes('/hub/link') && c.method === 'GET') return json(200, state);
      if (c.url.includes('/hub/link') && c.method === 'PATCH') {
        state = { ...OFF, kho_label: NAME, kho_label_custom: true };
        return json(200, state);
      }
      return json(404);
    });
    renderCard(<HubLinkCard />);
    const field = await screen.findByLabelText('Tên Kho (tuỳ chọn)');
    expect(field).toHaveValue('');
    expect(field).toHaveAttribute('placeholder', KHO_LABEL);
    expect(await screen.findByText('Mặc định')).toBeInTheDocument();
    const user = userEvent.setup();
    const save = screen.getByRole('button', { name: 'Lưu' });
    expect(save).toBeDisabled();

    await user.type(field, 'x'.repeat(KHO_LABEL_MAX + 1));
    expect(await screen.findByText(`Tên Kho tối đa ${KHO_LABEL_MAX} ký tự`)).toBeInTheDocument();
    expect(save).toBeDisabled();

    await user.clear(field);
    await user.type(field, `  ${NAME}  `);
    expect(save).toBeEnabled();                                            // chưa nối Gen-hub nhưng chỉ đổi tên ⇒ không đòi địa chỉ/token
    await user.click(save);
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    expect(calls.find((c) => c.method === 'PATCH')!.body).toEqual({ kho_label: NAME });   // đã cắt khoảng trắng, không kèm địa chỉ/token
    expect(await screen.findByLabelText('Tên Kho (tuỳ chọn)')).toHaveValue(NAME);
  });

  it('đã đổi: ô điền sẵn tên, chip "Đã đổi", Về mặc định gọi POST /defaults/kho_label/reset', async () => {
    let state: HubLink = { ...OFF, kho_label: NAME, kho_label_custom: true };
    const calls = mockFetch((c) => {
      if (c.url.includes('/defaults/kho_label/reset') && c.method === 'POST') {
        state = OFF;
        return json(200, { key: 'kho_label', reset: true, customized: false, current_text: khoItem(false).current_text });
      }
      if (c.url.includes('/defaults')) return json(200, defaults(state.kho_label_custom === true));
      if (c.url.includes('/hub/link') && c.method === 'GET') return json(200, state);
      return json(404);
    });
    renderCard(<HubLinkCard />);
    expect(await screen.findByLabelText('Tên Kho (tuỳ chọn)')).toHaveValue(NAME);
    expect(await screen.findByText('Đã đổi')).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId('reset-kho_label'));
    await user.click(await screen.findByRole('button', { name: 'Xác nhận' }));
    await waitFor(() => expect(calls.some((c) => c.url.includes('/defaults/kho_label/reset') && c.method === 'POST')).toBe(true));
    expect(calls.find((c) => c.url.includes('/defaults/kho_label/reset'))!.body).toEqual({ confirm: true });
    await waitFor(() => expect(screen.getByLabelText('Tên Kho (tuỳ chọn)')).toHaveValue(''));
  });

  it('relabelKho thay tên mặc định bằng tên hiệu lực, giữ "Kho dữ liệu thô"', () => {
    expect(relabelKho(`Ghi vào ${KHO_LABEL}`, NAME)).toBe(`Ghi vào ${NAME}`);
    expect(relabelKho('Kho dữ liệu thô và Kho dữ liệu', NAME)).toBe(`Kho dữ liệu thô và ${NAME}`);
    expect(relabelKho(`Ghi vào ${KHO_LABEL}`, undefined)).toBe(`Ghi vào ${KHO_LABEL}`);
    expect(relabelKho(`Ghi vào ${KHO_LABEL}`, KHO_LABEL)).toBe(`Ghi vào ${KHO_LABEL}`);
  });
});

describe('Tên Kho ở câu chạy thật của khung Gen (F-R2)', () => {
  it('proposalErrorView: câu HUB_OWNER_ONLY dùng tên Kho đã đặt; thiếu tên / tên mặc định ⇒ "Kho dữ liệu"', () => {
    const e = new ApiError(403, { code: 'HUB_OWNER_ONLY', title: 'x' });
    expect(proposalErrorView(e, NAME)?.text).toBe(`Chỉ Sếp (Owner) được ghi vào ${NAME} — chưa ghi gì.`);
    expect(proposalErrorView(e)?.text).toBe(`Chỉ Sếp (Owner) được ghi vào ${KHO_LABEL} — chưa ghi gì.`);
    expect(proposalErrorView(e, KHO_LABEL)?.text).toBe(`Chỉ Sếp (Owner) được ghi vào ${KHO_LABEL} — chưa ghi gì.`);
    expect(proposalErrorView(new ApiError(409, { code: 'HUB_WRITE_REJECTED', title: 'x', detail: 'Kho dữ liệu thô lỗi' }), NAME)?.text)
      .toContain('Kho dữ liệu thô lỗi');                                     // "Kho dữ liệu thô" (tầng khác) không bị đổi
  });

  it('GenPanel: câu chào "ghi vào …" lấy Tên Kho từ GET /hub/link; chưa tải / lỗi ⇒ tên mặc định', async () => {
    let link: HubLink = { ...OFF, kho_label: NAME, kho_label_custom: true };
    mockFetch((c) => (c.url.includes('/hub/link') && c.method === 'GET' ? json(200, link) : json(404)));
    useGenStore.setState({ openByUser: {}, conversationId: null, messages: [], busy: false, spotlight: null });
    const first = renderCard(<GenPanel userId="u1" />, { ...me, features: { gen: true } });
    expect(await screen.findByText(new RegExp(`ghi vào ${NAME} — em chỉ đề xuất`))).toBeInTheDocument();
    expect(first.container.textContent).not.toContain('ghi vào Kho dữ liệu —');
    first.unmount();

    link = OFF;                                                              // mặc định: câu chào như cũ
    renderCard(<GenPanel userId="u1" />, { ...me, features: { gen: true } });
    expect(await screen.findByText(new RegExp(`ghi vào ${KHO_LABEL} — em chỉ đề xuất`))).toBeInTheDocument();
  });
});
