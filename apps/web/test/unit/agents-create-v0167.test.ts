import { describe, expect, it } from 'vitest';
import { agentCreateBody, lastSpoke, modelLine } from '../../src/screens/agents/agentsModel';

/** v0.1.67 (B7): thân tạo agent (F078, F082), dòng model (F022), lần lên tiếng (F083). */
const FORM = { name: ' Tên ', roleDesc: 'Vai', voice: 'Giọng', speakWhen: 'Khi', forbidden: 'A\n\n B ', picked: null, scopeIds: new Set(['c1']) };

describe('agentCreateBody', () => {
  it('chưa chọn mức ⇒ mức mặc định chung 4 (không còn mức 2), chuẩn hoá chữ', () => {
    const b = agentCreateBody(FORM, null);
    expect(b.autonomy_level).toBe(4);
    expect(b).toMatchObject({ name: 'Tên', forbidden: ['A', 'B'], template: null, is_enabled: true, channel_scopes: [{ channel_id: 'c1' }] });
  });
  it('đã chọn mức thì tôn trọng, kể cả mức 0', () => {
    expect(agentCreateBody({ ...FORM, picked: 0 }, null).autonomy_level).toBe(0);
  });
  it('mẫu tắt sẵn ⇒ is_enabled=false; mẫu thường ⇒ true', () => {
    expect(agentCreateBody(FORM, { code: 'mascot', default_enabled: false }).is_enabled).toBe(false);
    expect(agentCreateBody(FORM, { code: 'cs', default_enabled: true }).is_enabled).toBe(true);
  });
});

describe('modelLine / lastSpoke', () => {
  it('ưu tiên dòng máy chủ dựng; máy chủ cũ thì suy từ binding', () => {
    expect(modelLine({ model: { custom: false, ok: false, label: 'Chuẩn: cần khoá API' }, binding: null })).toEqual({ text: 'Chuẩn: cần khoá API', ok: false });
    const b = { model_id: 'm', model_name: 'x', provider_name: 'P', temperature: 0, context_tokens: 1, rule_codes: [], inactive_reason: 'Nguồn đã tắt — agent đang dùng Chuẩn' };
    expect(modelLine({ binding: b })).toEqual({ text: 'Nguồn đã tắt — agent đang dùng Chuẩn', ok: false });
    expect(modelLine({ binding: null })).toEqual({ text: 'Chuẩn (tự chọn theo vai)', ok: true });
  });
  it('quyết định send chỉ là nháp chờ duyệt ⇒ không bao giờ "Đã gửi"', () => {
    expect(lastSpoke({ last_spoke_at: null, last_spoke_decision: null })).toBe('Chưa từng lên tiếng');
    expect(lastSpoke({ last_spoke_at: new Date().toISOString(), last_spoke_decision: 'send' })).toMatch(/^Soạn nháp · /);
    expect(lastSpoke({ last_spoke_at: new Date().toISOString(), last_spoke_decision: 'suggest' })).toMatch(/^Đề xuất · /);
  });
});
