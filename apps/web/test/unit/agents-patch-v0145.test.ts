import { describe, expect, it } from 'vitest';
import type { AgentIdentity } from '@gen-harness/contracts';
import { agentPatchBody, normalizeForbidden, type AgentFormValues } from '../../src/screens/agents/agentsModel';

/** v0.1.45 (F-20): sửa agent chỉ gửi rào chắn (mức tự trị / điều cấm / phạm vi kênh) khi chúng đổi → đổi tên không hỏi PIN. */

const AGENT: AgentIdentity = {
  id: 'a-1',
  name: 'Trợ lý thương mại',
  role_desc: 'Theo dõi cơ hội',
  template: 'commercial',
  addressing: {},
  voice: 'Thân thiện',
  speak_when: 'Khi được hỏi',
  forbidden: ['Cam kết giá', 'Tự ý huỷ đơn'],
  autonomy_level: 4,
  is_enabled: true,
  limits: { decisions_per_min: 20, drafts_per_hour: 30 },
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
  channel_scopes: [
    { channel_id: 'ch-zalo', channel_type: 'zalo', group_id: 'g-1', group_name: 'Nhóm TP' },
    { channel_id: 'ch-tg', channel_type: 'telegram', group_id: null, group_name: null },
  ],
  binding: null,
};

const form = (patch: Partial<AgentFormValues> = {}): AgentFormValues => ({
  name: AGENT.name,
  roleDesc: AGENT.role_desc,
  voice: AGENT.voice,
  speakWhen: AGENT.speak_when,
  forbidden: AGENT.forbidden.join('\n'),
  picked: null,
  scopeIds: new Set(['ch-tg', 'ch-zalo']),
  ...patch,
});

describe('agentPatchBody (v0.1.45)', () => {
  it('đổi tên: không có forbidden / channel_scopes / autonomy_level', () => {
    const body = agentPatchBody(AGENT, form({ name: '  Trợ lý mới  ' }));
    expect(body.name).toBe('Trợ lý mới');
    expect(body).not.toHaveProperty('forbidden');
    expect(body).not.toHaveProperty('channel_scopes');
    expect(body).not.toHaveProperty('autonomy_level');
  });

  it('điều cấm chỉ khác khoảng trắng / dòng rỗng: coi như không đổi', () => {
    const body = agentPatchBody(AGENT, form({ forbidden: '  Cam kết giá \n\n Tự ý huỷ đơn\n' }));
    expect(body).not.toHaveProperty('forbidden');
  });

  it('đổi kênh: có channel_scopes (theo tập kênh đã tick)', () => {
    const body = agentPatchBody(AGENT, form({ scopeIds: new Set(['ch-zalo']) }));
    // v0.1.67 (F090): kênh còn tick giữ phạm vi theo nhóm của nó.
    expect(body.channel_scopes).toEqual([{ channel_id: 'ch-zalo', group_id: 'g-1' }]);
    expect(body).not.toHaveProperty('forbidden');
  });

  it('đổi điều cấm và mức tự trị: gửi cả hai', () => {
    const body = agentPatchBody(AGENT, form({ forbidden: 'Cam kết giá', picked: 3 }));
    expect(body.forbidden).toEqual(['Cam kết giá']);
    expect(body.autonomy_level).toBe(3);
    expect(body).not.toHaveProperty('channel_scopes');
  });

  it('chọn lại đúng mức đang lưu: không gửi autonomy_level', () => {
    expect(agentPatchBody(AGENT, form({ picked: 4 }))).not.toHaveProperty('autonomy_level');
  });

  it('normalizeForbidden bỏ khoảng trắng hai đầu và dòng rỗng', () => {
    expect(normalizeForbidden(' a \n\n b ')).toEqual(['a', 'b']);
    expect(normalizeForbidden([' x ', ''])).toEqual(['x']);
  });
});

describe('agentPatchBody — phạm vi theo nhóm (v0.1.67, F090)', () => {
  it('bỏ tick một kênh khác: kênh còn tick giữ nguyên dòng phạm vi theo nhóm (không mở rộng ra cả kênh)', () => {
    const body = agentPatchBody(AGENT, form({ scopeIds: new Set(['ch-zalo']) }));
    expect(body.channel_scopes).toEqual([{ channel_id: 'ch-zalo', group_id: 'g-1' }]);
  });

  it('tick thêm kênh mới: kênh mới là cả kênh, kênh cũ giữ phạm vi nhóm', () => {
    const body = agentPatchBody(AGENT, form({ scopeIds: new Set(['ch-zalo', 'ch-tg', 'ch-wa']) }));
    expect(body.channel_scopes).toEqual([
      { channel_id: 'ch-zalo', group_id: 'g-1' },
      { channel_id: 'ch-tg' },
      { channel_id: 'ch-wa' },
    ]);
  });
});

