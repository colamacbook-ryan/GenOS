/**
 * v0.1.70 (dọn sót F029): MỘT đích sửa/thêm nguồn AI (`readiness.FIX_AI` = /setup?buoc=nguon-ai; nguồn có nhưng tắt ⇒
 * `FIX_API` = /api) ở cả màn API & Model, tab Cài đặt › Bộ não AI và Hướng dẫn bước 4; chữ "bước 4" nói đúng.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { ApiScreen } from '../../src/screens/api/ApiScreen';
import { BrainTab } from '../../src/screens/system/BrainTab';
import { GUIDE, MODEL_GUIDE } from '../../src/guide/guideContent';
import { AI_SOURCE_PATH } from '../../src/setup/steps';
import { qk } from '../../src/lib/queries';
import { permissionsOf } from '../mock-api';

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
const ME = (manage: boolean) => ({
  id: 'u', email: 'owner@genesis.local', display_name: 'Anh Cơ', role: { code: manage ? 'owner' : 'auditor', name: 'x' },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null,
  permissions: permissionsOf(manage ? 'owner' : 'auditor'),
});

function renderEmpty(ui: React.ReactElement, manage: boolean) {
  vi.stubGlobal('WebSocket', undefined);
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/agents/bindings')) return json({ items: [], models: [] });
    if (url.includes('/providers/credentials') || url.includes('/cli/profiles')) return json([]);
    if (url.includes('/providers')) return json([]);
    return new Response(null, { status: 404 });   // /auth/me ... : giữ dữ liệu `me` đã nạp sẵn
  }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(qk.me, ME(manage));
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe('F029 — một đích "thêm nguồn AI"', () => {
  it('API & Model chưa có nguồn nào: Owner có nút tới màn Thiết lập nguồn AI', async () => {
    renderEmpty(<ApiScreen />, true);
    const link = await screen.findByTestId('api-add-source');
    expect(link).toHaveAttribute('href', AI_SOURCE_PATH);
  });

  it('API & Model chưa có nguồn nào: vai không quản lý không thấy nút', async () => {
    renderEmpty(<ApiScreen />, false);
    await screen.findAllByText('Owner chưa thêm nhà cung cấp nào.');
    expect(screen.queryByTestId('api-add-source')).toBeNull();
  });

  it('Cài đặt › Bộ não AI chưa có model: nút tới Thiết lập nguồn AI (không còn chỉ nói "ở màn API & Model")', async () => {
    renderEmpty(<BrainTab />, true);
    const link = await screen.findByTestId('brain-add-source');
    expect(link).toHaveAttribute('href', AI_SOURCE_PATH);
    expect(screen.queryByText(/Thêm nhà cung cấp và model ở màn API/)).toBeNull();
    await waitFor(() => expect(screen.getByRole('link', { name: /Mở API & Model/ })).toHaveAttribute('href', '/api'));
  });
});

describe('F029 — Hướng dẫn bước 4 nói đúng', () => {
  it('MODEL_GUIDE.console là màn Thiết lập nguồn AI, không còn /api', () => {
    expect(MODEL_GUIDE.console).toEqual({ label: 'Thiết lập nguồn AI', to: AI_SOURCE_PATH });
  });

  it('không chỗ nào nói "bước 4 đã xong nếu Sếp đang ở Console" (Console mở sau bước 1–3, bước 4 để sau được)', () => {
    const texts = [MODEL_GUIDE, ...GUIDE].flatMap((g) => [g.why, ...(g.prepare ?? []), ...g.steps, g.doneWhen]);
    expect(texts.filter((t) => /đã xong nếu Sếp đang ở Console/.test(t))).toEqual([]);
    const agent = GUIDE.find((g) => g.n === 8)!;
    expect(agent.prepare?.[0]).toContain('Có ít nhất một nguồn AI chạy được');
    expect(agent.prepare?.[0]).toContain('Thiết lập nguồn AI');
  });
});
