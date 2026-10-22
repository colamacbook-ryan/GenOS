/**
 * v0.1.41 (F-8a): genStore lưu máy MÃ hội thoại + chủ của nó (không lưu nội dung tin); bản lưu cũ (version 0, chỉ có
 * openByUser) vẫn đọc được; localStorage hỏng / ném lỗi ⇒ vẫn chạy bằng bộ nhớ tạm.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { migrateGen, useGenStore } from '../../src/gen/genStore';
import { AUTO_CHOICE, MODEL_CHOICE_KEY, resetModelChoiceMemory } from '../../src/gen/modelChoice';

const KEY = 'gh-gen';

function saved(): { state: Record<string, unknown>; version: number } {
  return JSON.parse(window.localStorage.getItem(KEY) ?? '{}');
}

beforeEach(() => {
  window.localStorage.clear();
  useGenStore.setState({ openByUser: {}, conversationId: null, conversationOwner: null, messages: [], busy: false, spotlight: null });
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe('genStore v0.1.41 — lưu mã hội thoại', () => {
  it('partialize ghi conversationId + conversationOwner, KHÔNG ghi messages/busy/spotlight', () => {
    useGenStore.getState().setOpen('u1', true);
    useGenStore.getState().setConversation('u1', 'c-1');
    useGenStore.setState({
      busy: true,
      messages: [{ id: 'm1', role: 'user', text: 'câu hỏi bí mật', steps: [] }],
      spotlight: { target: 'overview.queue', message: 'x' },
    });
    const s = saved();
    expect(s.version).toBe(1);
    expect(s.state).toEqual({ openByUser: { u1: true }, conversationId: 'c-1', conversationOwner: 'u1' });
    expect(window.localStorage.getItem(KEY)).not.toContain('câu hỏi bí mật');
    expect(s.state).not.toHaveProperty('messages');
    expect(s.state).not.toHaveProperty('busy');
    expect(s.state).not.toHaveProperty('spotlight');
  });

  it('rehydrate từ bản cũ (version 0, chỉ openByUser) ⇒ conversationId null, không lỗi', async () => {
    window.localStorage.setItem(KEY, JSON.stringify({ state: { openByUser: { u1: true } }, version: 0 }));
    await useGenStore.persist.rehydrate();
    const st = useGenStore.getState();
    expect(st.openByUser).toEqual({ u1: true });
    expect(st.conversationId).toBeNull();
    expect(st.conversationOwner).toBeNull();
    expect(migrateGen({ openByUser: { u2: false } }, 0)).toEqual({ openByUser: { u2: false }, conversationId: null, conversationOwner: null });
    expect(migrateGen(null, 0)).toEqual({ openByUser: {}, conversationId: null, conversationOwner: null });
  });

  it('rehydrate bản 1 khôi phục mã hội thoại và chủ', async () => {
    window.localStorage.setItem(KEY, JSON.stringify({ state: { openByUser: {}, conversationId: 'c-9', conversationOwner: 'u1' }, version: 1 }));
    await useGenStore.persist.rehydrate();
    expect(useGenStore.getState().conversationId).toBe('c-9');
    expect(useGenStore.getState().conversationOwner).toBe('u1');
    expect(useGenStore.getState().messages).toEqual([]);
  });

  it('reset() xoá conversationId + conversationOwner (cả bản lưu máy)', () => {
    useGenStore.getState().setConversation('u1', 'c-1');
    useGenStore.setState({ messages: [{ id: 'm1', role: 'user', text: 'hi', steps: [] }], busy: true });
    useGenStore.getState().reset();
    const st = useGenStore.getState();
    expect(st.conversationId).toBeNull();
    expect(st.conversationOwner).toBeNull();
    expect(st.messages).toEqual([]);
    expect(st.busy).toBe(false);
    expect(saved().state).toMatchObject({ conversationId: null, conversationOwner: null });
  });

  it('bản lưu hỏng (JSON sai) ⇒ rehydrate không ném, giữ trạng thái mặc định', async () => {
    window.localStorage.setItem(KEY, '{hỏng');
    await expect(useGenStore.persist.rehydrate()).resolves.toBeUndefined();
    expect(useGenStore.getState().conversationId).toBeNull();
  });

  it('localStorage ném lỗi ⇒ vẫn chạy (bộ nhớ tạm)', async () => {
    vi.resetModules();
    const mod = await import('../../src/gen/genStore');
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError');
    });
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });
    expect(() => mod.useGenStore.getState().setConversation('u1', 'c-2')).not.toThrow();
    expect(mod.useGenStore.getState().conversationId).toBe('c-2');
    expect(() => mod.useGenStore.getState().setOpen('u1', true)).not.toThrow();
    expect(mod.useGenStore.getState().openByUser).toEqual({ u1: true });
    await expect(mod.useGenStore.persist.rehydrate()).resolves.toBeUndefined();
    // Bộ nhớ tạm giữ bản vừa ghi ⇒ đọc lại đúng mã hội thoại.
    expect(mod.useGenStore.getState().conversationId).toBe('c-2');
    mod.useGenStore.getState().reset();
    expect(mod.useGenStore.getState().conversationOwner).toBeNull();
  });
});

describe('genStore v0.1.55 (G3) — lựa chọn model không đụng bản lưu `gh-gen`', () => {
  it('modelChoice KHÔNG nằm trong `gh-gen`; reset() / setConversation() đưa về Tự động / lựa chọn đã nhớ của hội thoại', () => {
    window.localStorage.removeItem(MODEL_CHOICE_KEY);
    resetModelChoiceMemory();
    useGenStore.getState().setConversation('u1', 'c-7');
    useGenStore.getState().setModelChoice({ tier: 'deep', effort: 'high' });
    expect(useGenStore.getState().modelChoice).toEqual({ tier: 'deep', effort: 'high' });
    expect(saved().state).not.toHaveProperty('modelChoice');
    expect(JSON.parse(window.localStorage.getItem(MODEL_CHOICE_KEY) ?? '{}')).toEqual({ 'c-7': { tier: 'deep', effort: 'high' } });
    useGenStore.getState().reset();
    expect(useGenStore.getState().modelChoice).toEqual(AUTO_CHOICE);
    useGenStore.getState().setConversation('u1', 'c-7');
    expect(useGenStore.getState().modelChoice).toEqual({ tier: 'deep', effort: 'high' });
    useGenStore.getState().setConversation('u1', 'c-8');
    expect(useGenStore.getState().modelChoice).toEqual(AUTO_CHOICE);
    window.localStorage.removeItem(MODEL_CHOICE_KEY);
    resetModelChoiceMemory();
  });
});
