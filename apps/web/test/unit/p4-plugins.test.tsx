import type { ReactElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { PluginItem } from '@gen-harness/contracts';
import { PluginsScreen } from '../../src/screens/plugins/PluginsScreen';
import { qk } from '../../src/lib/queries';
import { useUrlStateStore } from '../../src/lib/uiStore';

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

const ME = {
  id: 'u', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A (Chủ)',
  role: { code: 'owner', name: 'Owner — Sếp' },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null,
  permissions: { 'system.read': 'all', 'system.manage': 'all' },
};

function renderScreen(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(qk.me, ME);
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useUrlStateStore.setState({ params: {} });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const PLUGINS: PluginItem[] = [
  {
    package: '@gen/chassis-kernel', name: 'Kernel & Plugin Manager', layer: 'chassis', origin: 'core', version: '2.2.0',
    description: null, enabled: true, load_order: 1, removable: false, can_disable: false,
    sandbox: null, permissions: [], signature_ok: true, permissions_status: 'active', installed_at: '2025-01-01T00:00:00Z',
    dependencies: [], health: 'healthy', breaker: { state: 'closed', total_errors: 0, last_error: null },
  },
  {
    package: '@gen/provider-deepseek', name: 'Provider DeepSeek', layer: 'provider', origin: 'marketplace', version: '1.0.4',
    description: null, enabled: true, load_order: 9, removable: true, can_disable: true,
    sandbox: null, permissions: ['call:provider'], signature_ok: true, permissions_status: 'active', installed_at: '2026-06-01T00:00:00Z',
    dependencies: [], health: 'degraded', breaker: { state: 'half_open', total_errors: 7, last_error: 'HTTP 429 — hết hạn mức' },
  },
];

function baseHandler(c: Call): Response | null {
  if (c.url.endsWith('/plugins') && c.method === 'GET') return json(200, PLUGINS);
  return null;
}

describe('Plugin & Tiện ích', () => {
  it('hiện tab plugin nền, khoá nút bật/tắt và gỡ của plugin nền', async () => {
    mockFetch((c) => baseHandler(c) ?? json(404));
    renderScreen(<PluginsScreen />);

    expect(await screen.findByText('Kernel & Plugin Manager')).toBeInTheDocument();
    const kernelRow = screen.getByText('Kernel & Plugin Manager').closest('tr') as HTMLElement;
    expect(within(kernelRow).getByLabelText('Tắt Kernel & Plugin Manager')).toHaveAttribute('aria-disabled', 'true');
    expect(within(kernelRow).getByText(/không tắt được/)).toBeInTheDocument();
    expect(within(kernelRow).getByText(/Không gỡ được/)).toBeInTheDocument();
  });

  it('chuyển tab "Plugin cài thêm" hiện provider suy giảm và cho reset breaker', async () => {
    const calls = mockFetch((c) => {
      if (c.url.includes('/plugins/%40gen%2Fprovider-deepseek/breaker/reset') && c.method === 'POST') {
        return json(200, { ...PLUGINS[1], breaker: { state: 'closed', total_errors: 7, last_error: 'HTTP 429 — hết hạn mức' } });
      }
      return baseHandler(c) ?? json(404);
    });
    renderScreen(<PluginsScreen />);
    await screen.findByText('Kernel & Plugin Manager');
    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', { name: /Plugin cài thêm/ }));
    expect(await screen.findByText('Provider DeepSeek')).toBeInTheDocument();
    const row = screen.getByText('Provider DeepSeek').closest('tr') as HTMLElement;
    expect(within(row).getByText('Nửa mở — đang thử')).toBeInTheDocument();
    await user.click(within(row).getByRole('button', { name: 'Reset' }));
    await waitFor(() => expect(calls.some((c) => c.url.includes('/breaker/reset'))).toBe(true));
  });

  it('nạp plugin từ tệp: hiện quyền xin từ manifest, gửi đủ manifest+sha256+chữ ký tới POST /plugins/local', async () => {
    // Tính sha256 thật từ tệp (crypto.subtle qua File.arrayBuffer) chỉ chạy được trong trình duyệt thật —
    // phủ ở e2e (flows.spec.ts); ở đây gõ thẳng vào ô sha256 (vẫn là input thường, sửa được) để test luồng
    // chữ ký + quyền xin + gọi API không phụ thuộc API tệp của jsdom.
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/plugins/local') && c.method === 'POST') {
        const b = c.body as { manifest: { package: string; name: string; permissions: string[] } };
        return json(201, { id: 'new-1', package: b.manifest.package, name: b.manifest.name, version: '1.0.0', origin: 'local_file', is_enabled: false, permissions_status: 'pending', signature_ok: true, permissions: b.manifest.permissions, installed_at: new Date().toISOString() });
      }
      return baseHandler(c) ?? json(404);
    });
    renderScreen(<PluginsScreen />);
    await screen.findByText('Kernel & Plugin Manager');
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Nạp plugin từ tệp' }));
    const dialog = await screen.findByRole('dialog');

    expect(within(dialog).getByText('read:clean')).toBeInTheDocument();
    expect(within(dialog).getByText('write:notes')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Nạp plugin' })).toBeDisabled();

    await user.type(within(dialog).getByLabelText(/sha256 mã nguồn/), 'a'.repeat(64));
    await user.type(within(dialog).getByLabelText(/Chữ ký/), 'c2ln-hop-le-base64');
    expect(within(dialog).getByRole('button', { name: 'Nạp plugin' })).toBeEnabled();
    await user.click(within(dialog).getByRole('button', { name: 'Nạp plugin' }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/plugins/local'))).toBe(true));
    const installCall = calls.find((c) => c.url.endsWith('/plugins/local'));
    expect((installCall?.body as { manifest: { package: string }; code_sha256: string; signature: string }).manifest.package).toBe('@ext/vi-du');
    expect((installCall?.body as { code_sha256: string }).code_sha256).toBe('a'.repeat(64));
  });
});
