/**
 * v0.1.55 (G2 Thiết lập gọn) — trình thiết lập chỉ hỏi 4 thứ (mã thiết lập, Owner + PIN, tên tổ chức, nguồn AI). Hình dạng payload
 * PUT /setup/steps/1..12 giữ nguyên; mặc định điền sẵn:
 *  - bước 3: hai ô xưng hô điền sẵn "Sếp";
 *  - bước 7: chỉ hỏi "Sếp làm ngành nào?" (bộ quy tắc) — lịch 900 s / 500 / tin cậy 0,6 dùng mặc định, chi tiết ở "Nâng cao";
 *  - bước 8: chọn mẫu là xong, bỏ tin thử trò chuyện;
 *  - bước 9: một dòng ghi chú thay ô tích (gửi ack_boundaries: true), mức 4 + ngưỡng 50.000.000 ₫;
 *  - bước 10: thẻ gợi ý có nút "Để sau" ("Mời ngay" mới hiện form mời);
 *  - bước 11: sao lưu tự bật hằng ngày 02:00, giữ 7 bản.
 * v0.1.59 (g5-thiet-lap): màn Tài khoản gom bước 1–3 (xưng hô nằm trong "Tuỳ chỉnh"); các bước 5–12 chỉ còn ở /guide/n nên test bước
 * 10 chạy trên khung thử `LegacyWizard`.
 */
import { createRef } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { SetupState } from '@gen-harness/contracts';
import { SetupPage } from '../../src/setup/SetupPage';
import { LegacyWizard } from './legacy-wizard';
import { Step7Refinery } from '../../src/setup/Step7Refinery';
import { Step8Agent } from '../../src/setup/Step8Agent';
import { Step9Autonomy } from '../../src/setup/Step9Autonomy';
import { Step11Backup } from '../../src/setup/Step11Backup';
import { DEFAULT_INDUSTRY, INDUSTRIES, industryCodes, industryOf } from '../../src/setup/phase2Model';
import { SETUP_STEPS } from '../../src/setup/steps';
import { DEFAULT_ADDRESSING } from '../../src/setup/validation';
import { queryClient } from '../../src/lib/queryClient';

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const stateAt = (current: number, done: number[] = [], skipped: number[] = []): SetupState => ({
  finished: false,
  current_step: current,
  steps: Array.from({ length: 12 }, (_, i) => ({
    n: i + 1, key: `s${i + 1}`, title: `Bước ${i + 1}`, required: i + 1 <= 3 || i + 1 === 12,
    status: done.includes(i + 1) ? 'done' : skipped.includes(i + 1) ? 'skipped' : i + 1 === current ? 'doing' : 'todo',
  })),
});

interface Call {
  url: string;
  method: string;
  body: unknown;
}
function stub(handler: (c: Call) => Response | undefined) {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const c = { url: String(input), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined };
      calls.push(c);
      return handler(c) ?? json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
    }),
  );
  return calls;
}
const meta = (n: number) => SETUP_STEPS.find((s) => s.n === n)!;
const baseProps = (n: number) => ({ meta: meta(n), description: '', status: 'doing' as const, token: '', setToken: () => {}, onNext: () => {}, formRef: createRef<HTMLFormElement>() });

beforeEach(() => {
  queryClient.clear();
  sessionStorage.clear();
});
afterEach(() => vi.unstubAllGlobals());

describe('bước 3 (màn Tài khoản) — xưng hô điền sẵn "Sếp"', () => {
  function renderAccount() {
    return render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/setup']}>
          <SetupPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it('hai ô xưng hô có sẵn "Sếp" (trong "Tuỳ chỉnh"); chỉ cần gõ tên doanh nghiệp là Tiếp tục được; PUT 3 gửi đủ năm trường', async () => {
    const user = userEvent.setup();
    const calls = stub((c) => {
      if (c.url.endsWith('/setup/steps/3') && c.method === 'PUT') return json(200, stateAt(4, [1, 2, 3]));
      if (c.url.includes('/setup/')) return json(200, stateAt(3, [1, 2]));
      return undefined;
    });
    renderAccount();
    await screen.findByRole('heading', { name: 'Tạo tài khoản Sếp' });
    expect(DEFAULT_ADDRESSING).toBe('Sếp');
    // Bước 1–2 đã xong ở máy chủ ⇒ chỉ còn tên doanh nghiệp + "Tuỳ chỉnh" (đóng sẵn).
    expect(screen.getByTestId('setup-account-done')).toBeInTheDocument();
    expect(screen.queryByLabelText('Mật khẩu')).toBeNull();
    expect(screen.getByText('Tuỳ chỉnh').closest('details')).not.toHaveAttribute('open');
    expect(screen.getByLabelText('Sếp tự xưng là')).toHaveValue('Sếp');
    expect(screen.getByLabelText('Gen gọi Sếp là')).toHaveValue('Sếp');
    expect(screen.getByLabelText('Múi giờ')).toHaveValue('Asia/Ho_Chi_Minh');
    expect(screen.getByLabelText('Tiền tệ')).toHaveValue('VND');
    const next = screen.getByRole('button', { name: /Tiếp tục/ });
    expect(next).toBeDisabled(); // chưa có tên doanh nghiệp
    await user.type(screen.getByLabelText('Tên doanh nghiệp'), 'Genesis Việt');
    expect(next).toBeEnabled();
    await user.click(next);
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/setup/steps/3') && c.method === 'PUT')).toBe(true));
    const put = calls.find((c) => c.url.endsWith('/setup/steps/3') && c.method === 'PUT')!;
    expect(put.body).toEqual({ org_name: 'Genesis Việt', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND', self_name: 'Sếp', bot_calls_me: 'Sếp' });
    // Bước 1–2 đã xong: không gọi lại PUT 1/2 (tải lại giữa chừng vẫn đi tiếp).
    expect(calls.some((c) => (c.url.endsWith('/setup/steps/1') || c.url.endsWith('/setup/steps/2')) && c.method === 'PUT')).toBe(false);
  });

  it('xoá trắng ô xưng hô vẫn bị chặn (rỗng ≠ mặc định)', async () => {
    const user = userEvent.setup();
    stub((c) => (c.url.includes('/setup/') ? json(200, stateAt(3, [1, 2])) : undefined));
    renderAccount();
    await screen.findByRole('heading', { name: 'Tạo tài khoản Sếp' });
    await user.type(screen.getByLabelText('Tên doanh nghiệp'), 'Genesis Việt');
    await user.clear(screen.getByLabelText('Sếp tự xưng là'));
    expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeDisabled();
  });
});

const PRESETS = ['R-01', 'R-02', 'R-03', 'R-04', 'R-05', 'R-06'].map((code) => ({
  id: `p-${code}`, code, name: `Quy tắc ${code}`, kind: 'intent', kind_label: 'Ý định', enabled: true, version: 1, threshold: 0.6,
  hits_24h: 0, conditions: [], outputs: [], prompt_hint: null, updated_at: '2026-10-10T00:00:00Z',
}));
const WEIGHTS = [{ dimension: 'a', label: 'A', value: 60 }, { dimension: 'b', label: 'B', value: 40 }];

describe('bước 7 — chỉ hỏi "Sếp làm ngành nào?"', () => {
  function setup7() {
    const calls = stub((c) => {
      if (c.url.includes('/rules/weights')) return json(200, WEIGHTS);
      if (c.url.includes('/setup/rule-presets')) return json(200, PRESETS);
      if (c.url.includes('/refinery/schedule')) return json(200, { interval_seconds: 900, count_threshold: 500, batch_size: 250, min_confidence: 0.6, pending: 0, next_run_at: '2026-10-10T00:00:00Z', next_trigger: 'interval' });
      if (c.url.endsWith('/setup/steps/7') && c.method === 'PUT') return json(200, stateAt(8, [1, 2, 3, 4, 7]));
      return undefined;
    });
    const onSaved = vi.fn();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <Step7Refinery {...baseProps(7)} onSaved={onSaved} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    return { calls, onSaved };
  }

  it('một câu hỏi ngành, mặc định "Khác — bật cả bộ khởi đầu"; Tiếp tục gửi 900 s / 500 / 0,6 + cả bộ quy tắc', async () => {
    const user = userEvent.setup();
    const { calls, onSaved } = setup7();
    const q = await screen.findByTestId('step7-industry');
    expect(within(q).getByText('Sếp làm ngành nào?')).toBeInTheDocument();
    const group = within(q).getByRole('radiogroup', { name: 'Ngành của Sếp' });
    await waitFor(() => expect(within(group).getByRole('radio', { name: 'Khác — bật cả bộ khởi đầu' })).toHaveAttribute('aria-checked', 'true'));
    // Lịch + từng quy tắc nằm trong "Nâng cao" (đóng sẵn).
    const adv = screen.getByText('Nâng cao — lịch sàng lọc và từng quy tắc').closest('details')!;
    expect(adv).not.toHaveAttribute('open');
    await waitFor(() => expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    const put = calls.find((c) => c.url.endsWith('/setup/steps/7') && c.method === 'PUT')!;
    expect(put.body).toEqual({
      interval_seconds: 900, count_threshold: 500, min_confidence: 0.6,
      rule_codes: ['R-01', 'R-02', 'R-03', 'R-04', 'R-05', 'R-06'],
      weights: [{ dimension: 'a', value: 60 }, { dimension: 'b', value: 40 }],
    });
  });

  it('chọn ngành "Tuyển dụng, nhân sự" ⇒ gửi đúng bộ quy tắc của ngành đó', async () => {
    const user = userEvent.setup();
    const { calls } = setup7();
    const group = await screen.findByRole('radiogroup', { name: 'Ngành của Sếp' });
    await waitFor(() => expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeEnabled());
    await user.click(within(group).getByRole('radio', { name: 'Tuyển dụng, nhân sự' }));
    expect(within(group).getByRole('radio', { name: 'Tuyển dụng, nhân sự' })).toHaveAttribute('aria-checked', 'true');
    await user.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    expect((calls.find((c) => c.method === 'PUT')!.body as { rule_codes: string[] }).rule_codes).toEqual(['R-03', 'R-05', 'R-06']);
  });

  it('mô hình thuần: industryCodes chỉ giữ mã máy chủ có; industryOf nhận ra bộ khớp, bộ chỉnh tay ⇒ null', () => {
    const all = PRESETS.map((p) => p.code);
    expect(INDUSTRIES.map((i) => i.value)).toEqual(['trade', 'service', 'hr', 'other']);
    expect(DEFAULT_INDUSTRY).toBe('other');
    expect(industryCodes('hr', all)).toEqual(['R-03', 'R-05', 'R-06']);
    expect(industryCodes('hr', ['R-05'])).toEqual(['R-05']);
    expect(industryCodes('khong-co', all)).toEqual([]);
    expect(industryOf(all, all)).toBe('other');
    expect(industryOf(['R-06', 'R-03', 'R-05'], all)).toBe('hr');
    expect(industryOf(['R-01'], all)).toBeNull();
    expect(industryOf([], all)).toBeNull();
  });
});

describe('bước 8 — chọn mẫu là xong, bỏ tin thử', () => {
  it('mở ra đã chọn sẵn một mẫu, Tiếp tục bấm được ngay; không còn ô "Câu thử trò chuyện"; PUT không gửi try_message', async () => {
    const user = userEvent.setup();
    const calls = stub((c) => {
      if (c.url.endsWith('/setup/steps/8') && c.method === 'PUT') {
        return json(200, { ...stateAt(9, [1, 2, 3, 4, 8]), agent: { id: 'a1', name: 'Trợ lý Kinh doanh', try_reply: null, try_error: null, try_error_code: null, try_reasons: [] } });
      }
      return undefined;
    });
    const onSaved = vi.fn();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <Step8Agent {...baseProps(8)} onSaved={onSaved} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByLabelText('Mẫu')).toHaveValue('commercial');
    expect(screen.getByLabelText('Tên agent')).toHaveValue('Trợ lý Kinh doanh');
    expect(screen.queryByLabelText('Câu thử trò chuyện')).toBeNull();
    expect(screen.getByTestId('step8-note')).toHaveTextContent('Chọn mẫu là xong');
    await user.selectOptions(screen.getByLabelText('Mẫu'), 'secretary');
    expect(screen.getByLabelText('Tên agent')).toHaveValue('Thư ký');
    await user.selectOptions(screen.getByLabelText('Mẫu'), '');
    expect(screen.getByLabelText('Tên agent')).toHaveValue('Trợ lý'); // "Tạo trống" cũng có tên + vai trò mặc định
    expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeEnabled();
    await user.selectOptions(screen.getByLabelText('Mẫu'), 'commercial');
    await user.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    expect(await screen.findByText(/Đã tạo agent/)).toBeInTheDocument();
    const put = calls.find((c) => c.method === 'PUT')!;
    expect(put.body).toEqual({ name: 'Trợ lý Kinh doanh', role_desc: expect.any(String), template: 'commercial' });
    expect(Object.keys(put.body as object)).not.toContain('try_message');
    // Không có tin thử ⇒ không hiện "trả lời thử" lẫn khối lỗi thử.
    expect(screen.queryByText(/trả lời thử/)).toBeNull();
    expect(screen.queryByText(/chưa trò chuyện thử được/)).toBeNull();
    await user.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    expect(onSaved).toHaveBeenCalled();
  });
});

describe('bước 9 — một dòng ghi chú thay ô tích', () => {
  it('không còn ô "Tôi đã đọc…"; có ghi chú + ngưỡng 50.000.000 ₫; Tiếp tục gửi mức 4 và ack_boundaries:true ngay khi tải xong', async () => {
    const user = userEvent.setup();
    const puts: unknown[] = [];
    stub((c) => {
      if (c.url.endsWith('/setup/steps/9') && c.method === 'PUT') {
        puts.push(c.body);
        return json(200, { ...stateAt(10, [1, 2, 3, 4, 8, 9]), hard_boundaries: [], agent: { id: 'a1', name: 'Trợ lý', autonomy_level: 4 } });
      }
      if (c.url.endsWith('/setup/steps/9')) return json(200, { agent: { id: 'a1', name: 'Trợ lý', autonomy_level: 4 } });
      if (c.url.endsWith('/setup/hard-boundaries')) return json(200, ['Chỉ lưu tin của nhóm Sếp đã chọn nghe']);
      if (c.url.endsWith('/setup/state')) return json(200, stateAt(9, [1, 2, 3, 4, 8]));
      return undefined;
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <Step9Autonomy {...baseProps(9)} onSaved={vi.fn()} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const note = await screen.findByTestId('step9-ack-note');
    expect(note).toHaveTextContent('Bấm Tiếp tục nghĩa là Sếp đã đọc các ranh giới trên');
    expect(note).toHaveTextContent('Agent sẽ ở mức Soạn sẵn chờ duyệt');
    expect(note).toHaveTextContent('50.000.000 ₫');
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(screen.queryByLabelText(/Tôi đã đọc/)).toBeNull();
    expect(await screen.findByText('Chỉ lưu tin của nhóm Sếp đã chọn nghe')).toBeInTheDocument();
    const next = screen.getByRole('button', { name: /Tiếp tục/ });
    await waitFor(() => expect(next).toBeEnabled());
    // Ghi chú nói đúng mức Sếp đang chọn (không cố định "Soạn sẵn chờ duyệt").
    await user.click(screen.getByRole('radio', { name: 'Gợi ý' }));
    expect(note).toHaveTextContent('Agent sẽ ở mức Gợi ý');
    expect(note).not.toHaveTextContent('Soạn sẵn chờ duyệt');
    await user.click(screen.getByRole('radio', { name: 'Soạn sẵn chờ duyệt' }));
    expect(note).toHaveTextContent('Agent sẽ ở mức Soạn sẵn chờ duyệt');
    await user.click(next);
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toEqual({ autonomy_level: 4, ack_boundaries: true });
  });
});

describe('bước 10 — thẻ gợi ý có nút "Để sau"', () => {
  function renderAt10() {
    const state = stateAt(10, [1, 2, 3, 4, 5, 6, 7, 8, 9]);
    const calls = stub((c) => {
      if (c.url.endsWith('/setup/steps/10/skip') && c.method === 'POST') return json(200, stateAt(11, [1, 2, 3, 4, 5, 6, 7, 8, 9], [10]));
      if (c.url.includes('/setup/')) return json(200, state);
      return undefined;
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/guide']}>
          <LegacyWizard />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    return calls;
  }

  it('mở ra là thẻ gợi ý: có "Để sau" (nút chính) + "Mời ngay"; chưa có form, không có "Tiếp tục"', async () => {
    renderAt10();
    await screen.findByRole('heading', { name: 'Bước 10' });
    const card = await screen.findByTestId('step10-suggestion');
    expect(card).toHaveTextContent('Gợi ý: mời đội ngũ cùng dùng');
    expect(card).toHaveTextContent('Không bắt buộc');
    expect(screen.getByRole('button', { name: 'Để sau' })).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: 'Mời ngay' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Tiếp tục/ })).toBeNull();
    expect(screen.queryByLabelText('Tên hiển thị')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Thêm người' })).toBeNull();
  });

  it('"Để sau" ⇒ POST /setup/steps/10/skip rồi sang bước 11', async () => {
    const user = userEvent.setup();
    const calls = renderAt10();
    await user.click(await screen.findByRole('button', { name: 'Để sau' }));
    expect(await screen.findByRole('heading', { name: 'Bước 11' })).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith('/setup/steps/10/skip') && c.method === 'POST')).toBe(true);
  });

  it('"Mời ngay" hiện form mời (Thêm người…), Tiếp tục xuất hiện; "Để sau" vẫn còn', async () => {
    const user = userEvent.setup();
    renderAt10();
    await user.click(await screen.findByRole('button', { name: 'Mời ngay' }));
    expect(await screen.findByRole('button', { name: 'Thêm người' })).toBeInTheDocument();
    // v0.1.68 (F038): chưa có người nào thì "Tiếp tục" tắt (danh sách rỗng không phải "đã mời"); "Để sau" vẫn là lối ra.
    expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Để sau' })).toBeInTheDocument();
    expect(screen.queryByTestId('step10-suggestion')).toBeNull();
  });
});

describe('bước 11 — sao lưu tự bật hằng ngày 02:00, giữ 7 bản', () => {
  it.each(['done', 'skipped'] as const)('đã %s ⇒ lịch đã ghi ⇒ "Sao lưu đã tự bật"', (status) => {
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <Step11Backup {...baseProps(11)} status={status} onSaved={vi.fn()} onSkip={() => {}} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByTestId('step11-default')).toHaveTextContent('Sao lưu đã tự bật');
  });

  it('ghi chú "đã tự bật"; lịch đổi nằm trong "Đổi lịch" (đóng sẵn); Tiếp tục gửi mặc định daily/02:00/7/local', async () => {
    const user = userEvent.setup();
    const calls = stub((c) => (c.url.endsWith('/setup/steps/11') && c.method === 'PUT' ? json(200, { ...stateAt(12, [1, 2, 3, 4, 11]), backup: { frequency: 'daily', time_of_day: '02:00', retention_count: 7, destination: 'local' } }) : undefined));
    const onSaved = vi.fn();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <Step11Backup {...baseProps(11)} onSaved={onSaved} onSkip={() => {}} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const note = screen.getByTestId('step11-default');
    // v0.1.69 (F103): chưa bấm Tiếp tục/Để sau thì lịch CHƯA được ghi ⇒ không nói "đã tự bật".
    expect(note).toHaveTextContent('Sao lưu sẽ tự bật khi Sếp đi tiếp');
    expect(note).not.toHaveTextContent('Sao lưu đã tự bật');
    expect(note).toHaveTextContent('Hằng ngày lúc 02:00, giữ 7 bản theo ngày, 4 theo tuần, 12 theo tháng');
    expect(screen.getByText('Đổi lịch (không bắt buộc)').closest('details')).not.toHaveAttribute('open');
    expect(screen.getByLabelText('Giờ chạy (HH:MM)')).toHaveValue('02:00');
    expect(screen.getByRole('button', { name: 'Để sau' })).toBeInTheDocument(); // "Để sau" vẫn ghi lịch mặc định (máy chủ)
    await user.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(calls.find((c) => c.method === 'PUT')!.body).toEqual({ frequency: 'daily', time_of_day: '02:00', retention_count: 7, destination: 'local' });
  });
});
