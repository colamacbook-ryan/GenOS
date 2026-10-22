/**
 * v0.1.55 (G3): chọn model / mức suy nghĩ trong khung chat Gen — Tự động (chuẩn) · Nhanh · Kỹ hơn (Cân bằng ẩn) + Mức suy nghĩ
 * Thấp/Vừa/Cao chỉ khi tầng hỗ trợ; nhớ theo hội thoại (localStorage `gh-gen-model-choice`), hội thoại mới = Tự động, không rò
 * sang hội thoại khác, localStorage ném lỗi vẫn chạy; lỗi 422 hiện chuỗi thân thiện + "Chi tiết kỹ thuật" (không render object).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { GenMessage, GenModelOptions, GenStepEvent } from '@gen-harness/contracts';
import { GenPanel } from '../../src/gen/GenPanel';
import { finishTurn, loadConversation, stopAll } from '../../src/gen/genClient';
import { useGenStore } from '../../src/gen/genStore';
import {
  AUTO_CHOICE,
  MODEL_CHOICE_KEY,
  MODEL_CHOICE_MAX,
  effortsOf,
  forgetChoice,
  loadChoice,
  normalizeChoice,
  resetModelChoiceMemory,
  saveChoice,
  tierAvailable,
  toBody,
  unavailableReason,
  visibleTiers,
  withEffort,
  withTier,
} from '../../src/gen/modelChoice';
import { applyEvent } from '../../src/lib/realtime';
import { setNavigator } from '../../src/lib/navigation';
import { qk } from '../../src/lib/queries';
import { queryClient } from '../../src/lib/queryClient';
import { modelChoiceValid, sampleModelOptions } from '../mock-gen';

const ME = {
  id: 'u1', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A', role: { code: 'owner', name: 'Owner — Sếp' },
  org: { id: 'o1', name: 'Genesis', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: {}, must_change_password: false,
  features: { gen: true },
};
const STAFF = { ...ME, id: 'u2', role: { code: 'manager', name: 'Quản lý' } };

const OPTIONS: GenModelOptions = sampleModelOptions();
const msgs = (text: string): GenMessage[] => [
  { id: `m-${text}`, role: 'user', turn_id: `t-${text}`, content: { text }, created_at: '2026-10-02T01:00:00Z' },
  { id: `a-${text}`, role: 'assistant', turn_id: `t-${text}`, content: { steps: [{ kind: 'say', text: `Trả lời ${text}` }] }, created_at: '2026-10-02T01:00:05Z', feedback: null },
];

type Reply = { status: number; body?: unknown };
type Call = { method: string; url: string; body: unknown };
const calls: Call[] = [];
let settingsBody: unknown;
let turnReply: Reply;

function stubApi() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : null });
      const json = (r: Reply) =>
        r.status === 204
          ? new Response(null, { status: 204 })
          : new Response(JSON.stringify(r.body ?? {}), { status: r.status, headers: { 'Content-Type': r.status >= 400 ? 'application/problem+json' : 'application/json' } });
      if (url.endsWith('/gen/settings')) return json({ status: 200, body: settingsBody });
      if (url.endsWith('/gen/turns') && method === 'POST') return json(turnReply);
      if (/\/gen\/turns\/t1$/.test(url)) return json({ status: 200, body: { turn_id: 't1', conversation_id: 'c1', status: 'running', steps: [] } });
      for (const id of ['c1', 'c2']) if (url.endsWith(`/gen/conversations/${id}/messages`)) return json({ status: 200, body: msgs(id) });
      return json({ status: 404, body: { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' } });
    }),
  );
}

const settings = (options?: GenModelOptions) => ({ enabled: true, roles: ['owner'], retention_days: 90, available: true, decider: 'llm', ...(options ? { model_options: options } : {}) });
const posts = () => calls.filter((c) => c.method === 'POST' && c.url.endsWith('/gen/turns'));
const stored = () => JSON.parse(window.localStorage.getItem(MODEL_CHOICE_KEY) ?? '{}') as Record<string, unknown>;
const btn = (name: string | RegExp) => screen.getByRole('button', { name });

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(qk.me, ME);
  calls.length = 0;
  settingsBody = settings(OPTIONS);
  turnReply = { status: 202, body: { turn_id: 't1', conversation_id: 'c1' } };
  window.localStorage.clear();
  resetModelChoiceMemory();
  useGenStore.setState({ openByUser: {}, conversationId: null, conversationOwner: null, messages: [], busy: false, spotlight: null, modelChoice: AUTO_CHOICE });
  setNavigator(() => undefined);
  stubApi();
  window.history.pushState({}, '', '/overview');
});
afterEach(() => {
  stopAll();
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  document.body.innerHTML = '';
});

function wrap(ui: React.ReactNode = <GenPanel userId="u1" />) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

async function ask(q: string) {
  await userEvent.type(screen.getByLabelText('Câu hỏi cho Gen'), `${q}{Enter}`);
  await waitFor(() => expect(posts().length).toBeGreaterThan(0));
}

/** Chờ thẻ chọn model nhận xong `model_options` từ `GET /gen/settings`. */
async function optionsLoaded() {
  await waitFor(() => expect(calls.some((c) => c.url.endsWith('/gen/settings'))).toBe(true));
  await waitFor(() => expect(queryClient.getQueryData(['gen', 'settings'])).toBeTruthy());
}

// ─── modelChoice.ts — hàm thuần ────────────────────────────────────────────────

describe('modelChoice (thuần)', () => {
  it('normalizeChoice: rác ⇒ Tự động; effort chỉ giữ khi hợp lệ và tầng khác Tự động', () => {
    for (const bad of [null, undefined, 5, 'deep', [], {}, { tier: 'turbo' }, { tier: 'strong' }, { tier: 'auto', effort: 'high' }]) {
      expect(normalizeChoice(bad)).toEqual({ tier: 'auto' });
    }
    expect(normalizeChoice({ tier: 'deep', effort: 'high' })).toEqual({ tier: 'deep', effort: 'high' });
    expect(normalizeChoice({ tier: 'deep', effort: 'extreme' })).toEqual({ tier: 'deep' });
    expect(normalizeChoice({ tier: 'fast', effort: null })).toEqual({ tier: 'fast' });
  });

  it('toBody bỏ hẳn khi Tự động; tập giá trị khớp mock/máy chủ', () => {
    expect(toBody(AUTO_CHOICE)).toBeUndefined();
    expect(toBody(undefined)).toBeUndefined();
    expect(toBody({ tier: 'fast' })).toEqual({ tier: 'fast' });
    expect(toBody({ tier: 'deep', effort: 'low' })).toEqual({ tier: 'deep', effort: 'low' });
    expect(modelChoiceValid({ tier: 'deep', effort: 'high' })).toBe(true);
    expect(modelChoiceValid({ tier: 'strong' })).toBe(false);
    expect(modelChoiceValid({ tier: 'deep', effort: 'extreme' })).toBe(false);
    expect(modelChoiceValid('deep')).toBe(false);
  });

  it('visibleTiers: Tự động · Nhanh · Kỹ hơn; Cân bằng chỉ hiện khi đang chọn', () => {
    expect(visibleTiers({ tier: 'auto' })).toEqual(['auto', 'fast', 'deep']);
    expect(visibleTiers({ tier: 'deep' })).toEqual(['auto', 'fast', 'deep']);
    expect(visibleTiers({ tier: 'balanced' })).toEqual(['auto', 'fast', 'balanced', 'deep']);
  });

  it('tierAvailable / effortsOf theo model_options; chưa biết ⇒ cho phép tầng, ẩn mức suy nghĩ', () => {
    const off = sampleModelOptions({ deep: false });
    expect(tierAvailable(off, 'deep')).toBe(false);
    expect(tierAvailable(off, 'fast')).toBe(true);
    expect(tierAvailable(off, 'auto')).toBe(true);
    for (const unknown of [undefined, null, { tiers: [] }]) {
      expect(tierAvailable(unknown, 'deep')).toBe(true);
      expect(effortsOf(unknown, 'deep')).toEqual([]);
    }
    expect(effortsOf(OPTIONS, 'deep')).toEqual(['low', 'medium', 'high']);
    expect(effortsOf(OPTIONS, 'fast')).toEqual([]);
    expect(effortsOf(OPTIONS, 'auto')).toEqual([]);
    expect(effortsOf({ tiers: [{ tier: 'deep', available: true, efforts: ['high', 'bogus', 'low'] as never }] }, 'deep')).toEqual(['low', 'high']);
  });

  it('withTier giữ mức suy nghĩ chỉ khi tầng mới hỗ trợ; withEffort bỏ chọn bằng null', () => {
    expect(withTier({ tier: 'deep', effort: 'high' }, 'fast', OPTIONS)).toEqual({ tier: 'fast' });
    expect(withTier({ tier: 'deep', effort: 'high' }, 'deep', OPTIONS)).toEqual({ tier: 'deep', effort: 'high' });
    expect(withTier({ tier: 'deep', effort: 'high' }, 'auto', OPTIONS)).toEqual({ tier: 'auto' });
    expect(withEffort({ tier: 'deep' }, 'medium')).toEqual({ tier: 'deep', effort: 'medium' });
    expect(withEffort({ tier: 'deep', effort: 'medium' }, null)).toEqual({ tier: 'deep' });
    expect(withEffort({ tier: 'auto' }, 'high')).toEqual({ tier: 'auto' });
  });

  it('unavailableReason: chữ tooltip khác nhau cho Sếp và nhân viên, không dùng chữ API/token', () => {
    expect(unavailableReason('deep', true)).toMatch(/Kỹ hơn.*chưa dùng được/);
    expect(unavailableReason('deep', false)).toMatch(/chỉ dành cho Sếp/);
    for (const t of ['fast', 'balanced', 'deep'] as const) expect(unavailableReason(t, true) + unavailableReason(t, false)).not.toMatch(/token/i);
  });
});

describe('modelChoice — nhớ theo hội thoại', () => {
  it('lưu / đọc theo mã hội thoại, không rò sang hội thoại khác, Tự động xoá mục', () => {
    expect(loadChoice(null)).toEqual(AUTO_CHOICE);
    expect(loadChoice('c1')).toEqual(AUTO_CHOICE);
    saveChoice('c1', { tier: 'deep', effort: 'high' });
    saveChoice('c2', { tier: 'fast' });
    expect(stored()).toEqual({ c1: { tier: 'deep', effort: 'high' }, c2: { tier: 'fast' } });
    expect(loadChoice('c1')).toEqual({ tier: 'deep', effort: 'high' });
    expect(loadChoice('c2')).toEqual({ tier: 'fast' });
    expect(loadChoice('c3')).toEqual(AUTO_CHOICE);
    saveChoice(null, { tier: 'deep' }); // hội thoại mới (chưa có mã): không ghi gì
    expect(Object.keys(stored())).toEqual(['c1', 'c2']);
    saveChoice('c1', AUTO_CHOICE);
    expect(stored()).toEqual({ c2: { tier: 'fast' } });
    forgetChoice('c2');
    expect(window.localStorage.getItem(MODEL_CHOICE_KEY)).toBeNull();
  });

  it('đọc lại sau tải lại trang (quên bộ nhớ tạm) từ localStorage; mục hỏng bị bỏ', () => {
    saveChoice('c1', { tier: 'deep', effort: 'low' });
    resetModelChoiceMemory();
    expect(loadChoice('c1')).toEqual({ tier: 'deep', effort: 'low' });
    window.localStorage.setItem(MODEL_CHOICE_KEY, JSON.stringify({ c1: { tier: 'turbo' }, c2: 'deep', c3: { tier: 'fast', effort: 'x' } }));
    resetModelChoiceMemory();
    expect(loadChoice('c1')).toEqual(AUTO_CHOICE);
    expect(loadChoice('c2')).toEqual(AUTO_CHOICE);
    expect(loadChoice('c3')).toEqual({ tier: 'fast' });
    window.localStorage.setItem(MODEL_CHOICE_KEY, '{không phải json');
    resetModelChoiceMemory();
    expect(loadChoice('c3')).toEqual(AUTO_CHOICE);
    window.localStorage.setItem(MODEL_CHOICE_KEY, '[1,2]');
    expect(loadChoice('c1')).toEqual(AUTO_CHOICE);
  });

  it('chỉ nhớ tối đa MODEL_CHOICE_MAX hội thoại gần nhất', () => {
    for (let i = 0; i < MODEL_CHOICE_MAX + 5; i++) saveChoice(`c${i}`, { tier: 'fast' });
    const ids = Object.keys(stored());
    expect(ids).toHaveLength(MODEL_CHOICE_MAX);
    expect(ids).not.toContain('c0');
    expect(ids).toContain(`c${MODEL_CHOICE_MAX + 4}`);
  });

  it('localStorage ném lỗi (đọc / ghi / truy cập) ⇒ không ném, nhớ tạm trong tab', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(() => saveChoice('c1', { tier: 'deep', effort: 'high' })).not.toThrow();
    expect(loadChoice('c1')).toEqual({ tier: 'deep', effort: 'high' });
    expect(loadChoice('c2')).toEqual(AUTO_CHOICE);
    expect(() => saveChoice('c1', AUTO_CHOICE)).not.toThrow();
    expect(loadChoice('c1')).toEqual(AUTO_CHOICE);
    vi.restoreAllMocks();
    // Truy cập `window.localStorage` cũng ném (chế độ riêng tư chặn hẳn).
    const desc = Object.getOwnPropertyDescriptor(window, 'localStorage');
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() {
        throw new Error('SecurityError');
      },
    });
    try {
      expect(() => saveChoice('c9', { tier: 'fast' })).not.toThrow();
      expect(loadChoice('c9')).toEqual({ tier: 'fast' });
    } finally {
      if (desc) Object.defineProperty(window, 'localStorage', desc);
    }
  });
});

// ─── khung chat ────────────────────────────────────────────────────────────────

describe('ModelPicker trong khung Gen', () => {
  it('hiện 3 lựa chọn Tự động (chuẩn) · Nhanh · Kỹ hơn, mặc định Tự động, Cân bằng ẩn', async () => {
    wrap();
    await optionsLoaded();
    const group = screen.getByRole('group', { name: 'Chế độ trả lời của Gen' });
    expect(within(group).getAllByRole('button').map((b) => b.textContent)).toEqual(['Tự động (chuẩn)', 'Nhanh', 'Kỹ hơn']);
    expect(within(group).queryByRole('button', { name: 'Cân bằng' })).toBeNull();
    expect(btn('Tự động (chuẩn)')).toHaveAttribute('aria-pressed', 'true');
    expect(btn('Nhanh')).toHaveAttribute('aria-pressed', 'false');
    expect(btn('Kỹ hơn')).toHaveAttribute('aria-pressed', 'false');
    expect(screen.queryByRole('group', { name: 'Mức suy nghĩ' })).toBeNull();
    expect(group.closest('.gen-model')?.textContent).not.toMatch(/token|\bAPI\b/i);
    expect(document.body.textContent).not.toContain('[object Object]');
  });

  it('Mức suy nghĩ Thấp/Vừa/Cao CHỈ hiện khi tầng đang chọn hỗ trợ', async () => {
    wrap();
    await optionsLoaded();
    await userEvent.click(btn('Nhanh')); // Nhanh: efforts rỗng
    expect(btn('Nhanh')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('group', { name: 'Mức suy nghĩ' })).toBeNull();
    await userEvent.click(btn('Kỹ hơn')); // Kỹ hơn: đủ ba mức
    const eff = screen.getByRole('group', { name: 'Mức suy nghĩ' });
    expect(eff).toHaveTextContent('Mức suy nghĩ:');
    expect(within(eff).getAllByRole('button').map((b) => b.textContent)).toEqual(['Thấp', 'Vừa', 'Cao']);
    await userEvent.click(within(eff).getByRole('button', { name: 'Cao' }));
    expect(useGenStore.getState().modelChoice).toEqual({ tier: 'deep', effort: 'high' });
    expect(within(eff).getByRole('button', { name: 'Cao' })).toHaveAttribute('aria-pressed', 'true');
    await userEvent.click(within(eff).getByRole('button', { name: 'Cao' })); // bấm lại ⇒ bỏ chọn
    expect(useGenStore.getState().modelChoice).toEqual({ tier: 'deep' });
    await userEvent.click(within(eff).getByRole('button', { name: 'Vừa' }));
    await userEvent.click(btn('Nhanh')); // đổi sang tầng không hỗ trợ ⇒ mức suy nghĩ biến mất, không còn giữ lại
    expect(useGenStore.getState().modelChoice).toEqual({ tier: 'fast' });
    expect(screen.queryByRole('group', { name: 'Mức suy nghĩ' })).toBeNull();
    await userEvent.click(btn('Tự động (chuẩn)'));
    expect(useGenStore.getState().modelChoice).toEqual({ tier: 'auto' });
  });

  it('tầng Kỹ hơn có nhưng không hỗ trợ mức nào ⇒ không hiện hàng Mức suy nghĩ', async () => {
    settingsBody = settings({ tiers: OPTIONS.tiers.map((t) => (t.tier === 'deep' ? { ...t, efforts: [] } : t)) });
    wrap();
    await optionsLoaded();
    await userEvent.click(btn('Kỹ hơn'));
    expect(btn('Kỹ hơn')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('group', { name: 'Mức suy nghĩ' })).toBeNull();
  });

  it('tầng không dùng được ⇒ nút khoá kèm tooltip chữ (nhân viên: chỉ dành cho Sếp)', async () => {
    settingsBody = settings(sampleModelOptions({ deep: false }));
    queryClient.setQueryData(qk.me, STAFF);
    wrap(<GenPanel userId="u2" />);
    await optionsLoaded();
    await waitFor(() => expect(btn('Kỹ hơn')).toHaveAttribute('aria-disabled', 'true'));
    expect(btn('Kỹ hơn')).toHaveAttribute('title', unavailableReason('deep', false));
    expect(btn('Kỹ hơn').getAttribute('title')).toMatch(/chỉ dành cho Sếp/);
    expect(btn('Nhanh')).not.toHaveAttribute('aria-disabled');
    // Màn cảm ứng không xem được tooltip: bấm vào nút mờ hiện CÂU GIẢI THÍCH ngay dưới hàng nút, không đổi lựa chọn.
    expect(screen.queryByTestId('gen-model-reason')).toBeNull();
    await userEvent.click(btn('Kỹ hơn'));
    expect(useGenStore.getState().modelChoice).toEqual({ tier: 'auto' });
    expect(screen.getByTestId('gen-model-reason')).toHaveTextContent(unavailableReason('deep', false));
    expect(btn('Kỹ hơn')).toHaveAttribute('aria-describedby', 'gen-model-reason');
    await userEvent.click(btn('Nhanh'));                                     // chọn tầng dùng được ⇒ câu giải thích biến mất
    expect(screen.queryByTestId('gen-model-reason')).toBeNull();
    expect(useGenStore.getState().modelChoice).toEqual({ tier: 'fast' });
    cleanup();
    queryClient.setQueryData(qk.me, ME);
    queryClient.removeQueries({ queryKey: ['gen', 'settings'] });
    wrap();
    await waitFor(() => expect(btn('Kỹ hơn')).toHaveAttribute('aria-disabled', 'true'));
    expect(btn('Kỹ hơn').getAttribute('title')).toMatch(/Sếp thêm nguồn/);
    await userEvent.click(btn('Kỹ hơn'));
    expect(screen.getByTestId('gen-model-reason')).toHaveTextContent(/Sếp thêm nguồn ở màn API & Model/);
    expect(document.body).not.toHaveTextContent('[object Object]');
  });

  it('máy chủ cũ không gửi model_options (hoặc lỗi) ⇒ cả 3 tầng bấm được, không có hàng Mức suy nghĩ', async () => {
    settingsBody = settings();
    wrap();
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/gen/settings'))).toBe(true));
    for (const n of ['Tự động (chuẩn)', 'Nhanh', 'Kỹ hơn']) expect(btn(n)).toBeEnabled();
    await userEvent.click(btn('Kỹ hơn'));
    expect(screen.queryByRole('group', { name: 'Mức suy nghĩ' })).toBeNull();
  });

  it('Cân bằng hiện khi đang được chọn (vd lựa chọn đã nhớ)', async () => {
    useGenStore.setState({ modelChoice: { tier: 'balanced' } });
    wrap();
    await optionsLoaded();
    expect(btn('Cân bằng')).toHaveAttribute('aria-pressed', 'true');
  });

  it('lựa chọn đã nhớ mà tầng nay không còn dùng được ⇒ tự về Tự động', async () => {
    useGenStore.setState({ modelChoice: { tier: 'deep', effort: 'high' } });
    settingsBody = settings(sampleModelOptions({ deep: false }));
    wrap();
    await waitFor(() => expect(useGenStore.getState().modelChoice).toEqual({ tier: 'auto' }));
    expect(btn('Tự động (chuẩn)')).toHaveAttribute('aria-pressed', 'true');
  });
});

describe('gửi câu hỏi kèm lựa chọn', () => {
  it('Tự động ⇒ BỎ model_choice; Kỹ hơn + Cao ⇒ model_choice {tier:"deep", effort:"high"}', async () => {
    wrap();
    await optionsLoaded();
    await ask('Câu một');
    expect(posts()[0].body).not.toHaveProperty('model_choice');
    expect(posts()[0].body).toMatchObject({ text: 'Câu một', conversation_id: null });
    act(() => finishTurn('t1', 'done'));
    await userEvent.click(btn('Kỹ hơn'));
    await userEvent.click(screen.getByRole('button', { name: 'Cao' }));
    await ask('Câu hai');
    await waitFor(() => expect(posts()).toHaveLength(2));
    expect(posts()[1].body).toMatchObject({ text: 'Câu hai', conversation_id: 'c1', model_choice: { tier: 'deep', effort: 'high' } });
    expect(stored()).toEqual({ c1: { tier: 'deep', effort: 'high' } });
  });

  it('chọn Kỹ hơn ở hội thoại MỚI ⇒ gửi kèm, rồi nhớ theo mã hội thoại server trả về', async () => {
    wrap();
    await optionsLoaded();
    await userEvent.click(btn('Kỹ hơn'));
    expect(stored()).toEqual({}); // chưa có mã hội thoại ⇒ chưa lưu máy
    await ask('Phân tích sâu giúp em');
    expect(posts()[0].body).toMatchObject({ model_choice: { tier: 'deep' } });
    await waitFor(() => expect(useGenStore.getState().conversationId).toBe('c1'));
    expect(stored()).toEqual({ c1: { tier: 'deep' } });
  });

  it('nhớ theo hội thoại: Hội thoại mới = Tự động; mở lại hội thoại cũ thấy lại lựa chọn; không rò sang hội thoại khác', async () => {
    wrap();
    await optionsLoaded();
    await userEvent.click(btn('Kỹ hơn'));
    await userEvent.click(screen.getByRole('button', { name: 'Vừa' }));
    await ask('Câu cho hội thoại một');
    await waitFor(() => expect(useGenStore.getState().conversationId).toBe('c1'));
    act(() => finishTurn('t1', 'done'));

    await userEvent.click(screen.getByRole('button', { name: 'Hội thoại mới' }));
    expect(useGenStore.getState().conversationId).toBeNull();
    expect(btn('Tự động (chuẩn)')).toHaveAttribute('aria-pressed', 'true');
    expect(btn('Kỹ hơn')).toHaveAttribute('aria-pressed', 'false');
    expect(screen.queryByRole('group', { name: 'Mức suy nghĩ' })).toBeNull();

    await act(async () => void (await loadConversation('c2', 'u1'))); // hội thoại khác, chưa từng chọn
    expect(useGenStore.getState().modelChoice).toEqual({ tier: 'auto' });
    expect(btn('Tự động (chuẩn)')).toHaveAttribute('aria-pressed', 'true');

    await act(async () => void (await loadConversation('c1', 'u1'))); // quay lại hội thoại một
    expect(btn('Kỹ hơn')).toHaveAttribute('aria-pressed', 'true');
    expect(within(screen.getByRole('group', { name: 'Mức suy nghĩ' })).getByRole('button', { name: 'Vừa' })).toHaveAttribute('aria-pressed', 'true');

    // Tải lại trang (mất bộ nhớ tạm): vẫn nhớ từ localStorage; hội thoại khác vẫn Tự động.
    resetModelChoiceMemory();
    useGenStore.setState({ modelChoice: AUTO_CHOICE });
    await act(async () => void (await loadConversation('c1', 'u1')));
    expect(useGenStore.getState().modelChoice).toEqual({ tier: 'deep', effort: 'medium' });
    await act(async () => void (await loadConversation('c2', 'u1')));
    expect(useGenStore.getState().modelChoice).toEqual({ tier: 'auto' });
  });

  it('localStorage ném lỗi ⇒ khung vẫn chọn được và gửi được', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    wrap();
    await optionsLoaded();
    await userEvent.click(btn('Kỹ hơn'));
    await ask('Vẫn gửi được');
    expect(posts()[0].body).toMatchObject({ model_choice: { tier: 'deep' } });
    await waitFor(() => expect(useGenStore.getState().conversationId).toBe('c1'));
    expect(loadChoice('c1')).toEqual({ tier: 'deep' }); // nhớ tạm trong tab
    expect(screen.queryByText(/\[object Object\]/)).toBeNull();
  });

  it('422 MODEL_CHOICE_INVALID ⇒ câu thân thiện + "Chi tiết kỹ thuật" (chuỗi), lựa chọn về Tự động, không render object', async () => {
    turnReply = {
      status: 422,
      body: {
        type: 'x', status: 422, code: 'MODEL_CHOICE_INVALID', title: 'Lựa chọn model không hợp lệ — em dùng chế độ Tự động nhé',
        detail: 'Trường không hợp lệ: tier. tier ∈ auto | fast | balanced | deep; effort ∈ low | medium | high (hoặc bỏ trống).',
      },
    };
    wrap();
    await optionsLoaded();
    await userEvent.click(btn('Kỹ hơn'));
    await ask('Gửi lựa chọn hỏng');
    await waitFor(() => expect(screen.getByText('Lựa chọn model không hợp lệ — em dùng chế độ Tự động nhé')).toBeInTheDocument());
    const details = document.querySelector('details.tech-detail');
    expect(details).not.toBeNull();
    expect(details?.querySelector('summary')).toHaveTextContent('Chi tiết kỹ thuật');
    expect(details?.querySelector('code')?.textContent).toMatch(/HTTP 422 · MODEL_CHOICE_INVALID/);
    expect(details?.querySelector('code')?.textContent).toMatch(/tier ∈ auto/);
    expect(document.body.textContent).not.toContain('[object Object]');
    expect(useGenStore.getState().modelChoice).toEqual({ tier: 'auto' });
    expect(btn('Tự động (chuẩn)')).toHaveAttribute('aria-pressed', 'true');
    expect(useGenStore.getState().busy).toBe(false);
  });

  it('lỗi khác (500) cũng có câu thân thiện + Chi tiết kỹ thuật dạng chuỗi', async () => {
    turnReply = { status: 500, body: { status: 500, code: 'INTERNAL', title: 'Hệ thống gặp lỗi khi xử lý yêu cầu — đã ghi nhật ký', detail: 'Mã lỗi ab12cd34 — gửi mã này cho người hỗ trợ', error_id: 'ab12cd34' } };
    wrap();
    await ask('Gửi rồi lỗi');
    await waitFor(() => expect(document.querySelector('details.tech-detail code')?.textContent).toMatch(/HTTP 500 · INTERNAL/));
    expect(document.body.textContent).not.toContain('[object Object]');
  });
});

describe('bước notice', () => {
  const ev = (seq: number, step: GenStepEvent['step']): GenStepEvent => ({ turn_id: 't1', conversation_id: 'c1', seq, step });

  it('notice hiện thành một dòng chữ; không bao giờ render object', async () => {
    wrap();
    await optionsLoaded();
    await ask('Hỏi gì đó');
    await waitFor(() => expect(useGenStore.getState().messages.some((m) => m.turnId === 't1')).toBe(true));
    const text = 'Em dùng chế độ Tự động vì mức “Kỹ hơn” chưa có nguồn AI nào phục vụ.';
    act(() => applyEvent(queryClient, { type: 'gen.step', data: ev(0, { kind: 'notice', text }) }));
    act(() => applyEvent(queryClient, { type: 'gen.step', data: ev(1, { kind: 'say', text: 'Dạ, em trả lời đây.' }) }));
    const note = screen.getByText(text);
    expect(note.closest('[role="note"]')).not.toBeNull();
    expect(screen.getByText('Dạ, em trả lời đây.')).toBeInTheDocument();
    // Bước notice hỏng (text không phải chuỗi) ⇒ không vẽ [object Object].
    act(() => applyEvent(queryClient, { type: 'gen.step', data: ev(2, { kind: 'notice', text: { a: 1 } as unknown as string }) }));
    expect(document.body.textContent).not.toContain('[object Object]');
  });
});
