/**
 * Mock API giai đoạn 3 · Quan hệ & Đối tượng (docs/api/phase-3-relations.md): Nhóm & Con người, Hồ sơ sống,
 * Sổ tay nhận thức, Tài liệu. `handle` trả true khi đã trả lời request. Dữ liệu mẫu lấy từ
 * docs/design/seed-data.json (channelGroups, peopleFilters/peopleRows, profileScores/profileSummary/timeline/
 * profileDocs/touchpoints, nbSubjects/nbSections/nbRefs/nbHistory/nbDropped) đúng cách mock-p3-queue.ts đã làm.
 */
import type {
  DirChannel,
  DirGroup,
  DirPerson,
  DirRelation,
  DocumentDetail,
  DocumentItem,
  NbDroppedItem,
  NbEntry,
  NbHistoryItem,
  NbNotebook,
  NbSubject,
  Profile,
} from '@gen-harness/contracts';
import { BAO, GROUP_TP, registerExplain } from './mock-p3-core';
import { AGENT_IDS, STAFF_IDS, rejectNonUuid } from './mock-ids';
import type { P2Ctx } from './mock-phase2';

export interface P3Options {
  fresh: boolean;
  emit: (type: string, data: unknown) => void;
  /** v0.1.35: agent theo id ở Danh tính Agent (mock-p4-agents, đọc chung — không copy). Gán BOT kiểm như API:
   * không phải UUID → 422; UUID không thuộc tổ chức → 404 'Agent'. */
  findAgent?: (id: string) => { id: string; name: string } | undefined;
}

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();
const has = (ctx: P2Ctx, perm: string) => !!ctx.perms[perm] && ctx.perms[perm] !== 'none';
/** `btoa`/`atob` chỉ hỗ trợ Latin1 — dùng Buffer (UTF-8 an toàn) cho nội dung tài liệu mẫu tiếng Việt. */
const b64 = (text: string) => Buffer.from(text, 'utf8').toString('base64');

// ─── Nhóm & Con người ────────────────────────────────────────────────────────
const CHANNELS: DirChannel[] = [
  { id: 'ch-zalo', type: 'zalo', name: 'Zalo', state: 'active', group_count: 34, events_24h: 1244 },
  { id: 'ch-whatsapp', type: 'whatsapp', name: 'WhatsApp', state: 'expired', group_count: 6, events_24h: 0 },
  { id: 'ch-telegram', type: 'telegram', name: 'Telegram', state: null, group_count: 0, events_24h: 0 },
  { id: 'ch-linkedin', type: 'linkedin', name: 'LinkedIn', state: 'active', group_count: 1, events_24h: 0 },
];

const KIND_CODE: Record<string, string> = { 'Nội bộ': 'internal', 'Thị trường': 'market', 'Đối tác': 'partner', Khách: 'customer' };
const MODE_CODE: Record<string, DirGroup['listen_mode']> = {
  'Nghe hết, chỉ trả lời khi được tag': 'tagged_only',
  'Lắng nghe im lặng': 'silent',
  'Chủ động bắt tín hiệu': 'proactive',
  'Tạm dừng': 'paused',
  'Không lắng nghe': 'off',
  'Chỉ hợp nhất danh tính': 'off',
};

function seedGroups(): DirGroup[] {
  const rows: Array<[string, string, string, string, string, string, string, string, boolean?]> = [
    ['g-zl-0114', 'GRP-ZL-0114', 'Vận hành Genesis — Quý 4', 'Nội bộ', '24', '412', '87', 'Nghe hết, chỉ trả lời khi được tag'],
    ['g-zl-0231', 'GRP-ZL-0231', 'Group Ngành gỗ Miền Nam', 'Thị trường', '412', '6412', '94', 'Lắng nghe im lặng'],
    ['g-zl-0174', 'GRP-ZL-0174', 'Ban Tài chính', 'Nội bộ', '11', '96', '58', 'Nghe hết, chỉ trả lời khi được tag'],
    ['g-zl-0356', 'GRP-ZL-0356', 'Group Nhân sự Logistics', 'Thị trường', '286', '2634', '64', 'Lắng nghe im lặng'],
    ['g-zl-0489', 'GRP-ZL-0489', 'Đối tác in ấn Thành Phát', 'Đối tác', '4', '784', '87', 'Chủ động bắt tín hiệu'],
    ['g-zl-0502', 'GRP-ZL-0502', 'Truyền thông & Sự kiện', 'Nội bộ', '13', '28', '22', 'Nghe hết, chỉ trả lời khi được tag'],
    ['g-wa-0007', 'GRP-WA-0007', 'Điều hành mở rộng', 'Nội bộ', '6', '54', '44', 'Tạm dừng'],
    ['g-wa-0011', 'GRP-WA-0011', 'Kho lạnh Tân Cảng', 'Khách', '3', '42', '58', 'Tạm dừng'],
    ['g-li-0001', 'GRP-LI-0001', 'Mạng đối tác ngành gỗ', 'Thị trường', '128', '0', '18', 'Chỉ hợp nhất danh tính'],
  ];
  const CH: Record<string, { type: DirGroup['channel']['type']; name: string }> = {
    zl: { type: 'zalo', name: 'Zalo' },
    wa: { type: 'whatsapp', name: 'WhatsApp' },
    li: { type: 'linkedin', name: 'LinkedIn' },
  };
  const BOT_FOR: Record<string, string | null> = {
    'g-zl-0114': AGENT_IDS.tls, 'g-zl-0231': AGENT_IDS.ka, 'g-zl-0174': AGENT_IDS.hc,
    'g-zl-0356': null, 'g-zl-0489': AGENT_IDS.tls, 'g-zl-0502': null,
    'g-wa-0007': AGENT_IDS.tls, 'g-wa-0011': AGENT_IDS.ka, 'g-li-0001': null,
  };
  const BOT_NAME: Record<string, string> = { [AGENT_IDS.tls]: 'Trợ lý thương mại', [AGENT_IDS.ka]: 'Key Account junior', [AGENT_IDS.hc]: 'Admin hậu cần' };
  return rows.map(([id, code, name, kindLabel, members, msgs, heat, modeLabel]) => {
    const chPrefix = code.split('-')[1].toLowerCase();
    const agentId = BOT_FOR[id];
    return {
      id, code, name,
      kind: KIND_CODE[kindLabel] ?? 'internal',
      listen_mode: MODE_CODE[modeLabel] ?? 'off',
      member_count: Number(members),
      events_24h: Number(msgs),
      heat: Number(heat),
      channel: CH[chPrefix],
      bot: agentId ? { id: agentId, name: BOT_NAME[agentId] } : null,
      created_at: ago(60 * 24 * 30),
    };
  });
}

const RELATION_CODE: Record<string, DirRelation> = {
  'Trực tiếp với Sếp': 'direct', 'Người lạ có tín hiệu': 'stranger', 'Nhân sự của Sếp': 'staff',
};
function relationOf(label: string): DirRelation {
  return RELATION_CODE[label] ?? (label.startsWith('Qua ') ? 'via_staff' : 'stranger');
}

interface PersonSeed {
  id: string; code: string; name: string; org: string; type: DirPerson['type']; ch: DirPerson['channels'];
  relation: string; heat: number; valueVnd: number | null; priority: DirPerson['priority'];
  agentId: string | null; autonomy: number | null; ownerUserId: string | null;
}
const AGENT_NAME: Record<string, string> = {
  [AGENT_IDS.tls]: 'Trợ lý thương mại', [AGENT_IDS.ka]: 'Key Account junior', [AGENT_IDS.hc]: 'Admin hậu cần',
  [AGENT_IDS.thk]: 'Thư ký cá nhân', [AGENT_IDS.rc]: 'Recruiter',
};
function seedPeople(): PersonSeed[] {
  return [
    { id: 'p-hau', code: 'PER-0311', name: 'Trần Văn Hậu', org: 'Xưởng gỗ Bình Dương', type: 'customer', ch: ['zalo'], relation: 'Trực tiếp với Sếp', heat: 91, valueVnd: 1_200_000_000, priority: 'P1', agentId: AGENT_IDS.ka, autonomy: 3, ownerUserId: null },
    { id: 'p-bao', code: 'PER-0042', name: 'Nguyễn Văn Bảo', org: 'Công ty in Thành Phát', type: 'customer', ch: ['zalo', 'whatsapp'], relation: 'Trực tiếp với Sếp', heat: 87, valueVnd: 84_000_000, priority: 'P1', agentId: AGENT_IDS.tls, autonomy: 3, ownerUserId: null },
    { id: 'p-duoc', code: 'PER-0402', name: 'Lâm Văn Được', org: 'Kho ván Bình Dương', type: 'supplier', ch: ['zalo'], relation: 'Người lạ có tín hiệu', heat: 84, valueVnd: null, priority: 'P1', agentId: null, autonomy: null, ownerUserId: null },
    { id: 'p-lan', code: 'PER-0119', name: 'Hoàng Thị Lan', org: 'An Khang Logistics', type: 'partner', ch: ['zalo', 'whatsapp'], relation: 'Qua Trần Minh Khoa', heat: 78, valueVnd: 128_000_000, priority: 'P2', agentId: AGENT_IDS.tls, autonomy: 4, ownerUserId: null },
    { id: 'p-son', code: 'PER-0619', name: 'Võ Thanh Sơn', org: 'Ứng viên · Key Account ngành lạnh', type: 'candidate', ch: ['zalo'], relation: 'Người lạ có tín hiệu', heat: 74, valueVnd: null, priority: 'P2', agentId: null, autonomy: 2, ownerUserId: null },
    { id: 'p-minh', code: 'PER-0512', name: 'Phạm Quốc Minh', org: 'Nội thất Minh Long', type: 'customer', ch: ['zalo'], relation: 'Qua Vũ Hải Đăng', heat: 64, valueVnd: 310_000_000, priority: 'P2', agentId: AGENT_IDS.ka, autonomy: 3, ownerUserId: null },
    { id: 'p-bich', code: 'PER-0844', name: 'Lê Thị Bích', org: 'Kho lạnh Tân Cảng', type: 'customer', ch: ['whatsapp'], relation: 'Người lạ có tín hiệu', heat: 58, valueVnd: null, priority: 'P2', agentId: null, autonomy: null, ownerUserId: null },
    { id: 'p-ha', code: 'PER-0007', name: 'Nguyễn Thu Hà', org: 'Nội bộ · Trưởng ban Tài chính', type: 'staff', ch: ['zalo', 'whatsapp'], relation: 'Nhân sự của Sếp', heat: 62, valueVnd: null, priority: 'P1', agentId: AGENT_IDS.hc, autonomy: 5, ownerUserId: null },
    { id: 'p-khoa', code: 'PER-0003', name: 'Trần Minh Khoa', org: 'Nội bộ · Giám đốc vận hành', type: 'staff', ch: ['zalo', 'whatsapp'], relation: 'Nhân sự của Sếp', heat: 71, valueVnd: null, priority: 'P1', agentId: AGENT_IDS.thk, autonomy: 5, ownerUserId: null },
    { id: 'p-thang', code: 'PER-0733', name: 'Bùi Đức Thắng', org: 'Gỗ Trường Thành Mới', type: 'customer', ch: ['zalo'], relation: 'Người lạ có tín hiệu', heat: 72, valueVnd: 880_000_000, priority: 'P2', agentId: AGENT_IDS.ka, autonomy: 3, ownerUserId: null },
    { id: 'p-duyen', code: 'PER-0951', name: 'Trịnh Mỹ Duyên', org: 'Bao bì Sài Gòn Mới', type: 'customer', ch: ['zalo', 'whatsapp'], relation: 'Qua Đỗ Thanh Mai', heat: 18, valueVnd: 510_000_000, priority: 'P3', agentId: AGENT_IDS.tls, autonomy: 3, ownerUserId: null },
    { id: 'p-tri', code: 'PER-0688', name: 'Đặng Hữu Trí', org: 'Gỗ Đông Phương', type: 'customer', ch: ['zalo'], relation: 'Qua Vũ Hải Đăng', heat: 22, valueVnd: 540_000_000, priority: 'P3', agentId: null, autonomy: null, ownerUserId: null },
  ];
}

/** Lọc theo kênh + tìm tên/mã như máy chủ (`q` so khớp không phân biệt hoa thường). */
function matchPeople(rows: PersonSeed[], f: { channel: string | null; q: string | null }): PersonSeed[] {
  const q = (f.q ?? '').trim().toLowerCase();
  return rows.filter(
    (r) => (!f.channel || r.ch.includes(f.channel as PersonSeed['ch'][number])) && (!q || r.name.toLowerCase().includes(q) || r.code.toLowerCase().includes(q)),
  );
}

/** Phân trang như máy chủ: `limit` (mặc định 50) + `cursor` (ở đây là vị trí bắt đầu); `total` đếm theo bộ lọc, không theo con trỏ. */
function pageOf<T>(rows: T[], url: URL): { items: T[]; next_cursor: string | null; total: number } {
  const limit = Number(url.searchParams.get('limit') ?? 50);
  const from = Number(url.searchParams.get('cursor') ?? 0) || 0;
  const items = rows.slice(from, from + limit);
  return { items, next_cursor: from + limit < rows.length ? String(from + limit) : null, total: rows.length };
}

function toDirPerson(p: PersonSeed): DirPerson {
  return {
    id: p.id, code: p.code, name: p.name, type: p.type, org_name: p.org,
    relation: relationOf(p.relation), channels: p.ch, heat: p.heat, heat_trend: null,
    value_vnd: p.valueVnd, priority: p.priority,
    bot: p.agentId ? { id: p.agentId, name: AGENT_NAME[p.agentId] } : null,
    autonomy_level: p.autonomy, owner_user_id: p.ownerUserId,
  };
}

/** `GroupRef` (nền chung) có `channel` là chuỗi loại kênh, khác `DirGroup.channel` là `{type,name}` — chuyển đổi khi cần gắn làm chủ sở hữu tài liệu. */
function toGroupRef(g: DirGroup): { id: string; code: string; name: string; channel: DirGroup['channel']['type'] } {
  return { id: g.id, code: g.code, name: g.name, channel: g.channel.type };
}

// ─── Hồ sơ sống ──────────────────────────────────────────────────────────────
function profileFor(person: PersonSeed): Profile {
  const isBao = person.id === 'p-bao';
  return {
    person: { id: person.id, code: person.code, name: person.name, type: person.type, org_name: person.org, title: person.org, relation_to_owner: relationOf(person.relation), owner: null },
    autonomy_level: person.autonomy, bot: person.agentId ? { id: person.agentId, name: AGENT_NAME[person.agentId] } : null,
    owner_note: isBao ? 'Anh Bảo thích nói chuyện thẳng, không thích vòng vo. Giá cao hơn một chút vẫn chấp nhận nếu giao đúng hẹn. Đừng để em junior follow khách này.' : null,
    identities: [
      { id: 'id-1', channel: { type: 'zalo', name: 'Zalo' }, external_id: 'zl-1', handle: person.name, phone_e164: null, first_seen_at: ago(60 * 24 * 200) },
      ...(person.ch.includes('whatsapp')
        ? [{ id: 'id-2', channel: { type: 'whatsapp' as const, name: 'WhatsApp' }, external_id: 'wa-1', handle: null, phone_e164: '+84903xxx118', first_seen_at: ago(60 * 24 * 90) }]
        : []),
    ],
    scores: isBao
      ? [
          { dimension: 'heat', label: 'Độ nóng', value: 87, trend: 'up', updated_at: ago(20) },
          { dimension: 'potential', label: 'Tiềm năng', value: 74, trend: 'flat', updated_at: ago(60 * 24) },
          { dimension: 'churn_risk', label: 'Rủi ro churn', value: 81, trend: 'up', updated_at: ago(60) },
          { dimension: 'care', label: 'Điểm chăm sóc', value: 48, trend: 'down', updated_at: ago(60 * 6) },
          { dimension: 'data_confidence', label: 'Tin cậy dữ liệu', value: 92, trend: 'flat', updated_at: ago(60 * 3) },
        ]
      : [{ dimension: 'heat', label: 'Độ nóng', value: person.heat, trend: null, updated_at: ago(60) }],
    summary: isBao
      ? [
          { text: 'Khách in ấn đã mua ba lần trong 18 tháng, tổng giá trị khoảng 240 triệu ₫, luôn thanh toán đúng hạn.', tone: 'ok', evidence: { type: 'meaning_unit', id: 'mu-1' } },
          { text: 'Người quyết định là anh Nguyễn Văn Bảo, giám đốc. Anh Bảo tự trả lời tin nhắn, không qua trợ lý.', tone: 'neutral', evidence: { type: 'meaning_unit', id: 'mu-1' } },
          { text: 'Rủi ro hiện tại: ba tin nhắn chưa được trả lời trong hơn hai giờ, giọng điệu đã chuyển sang gay gắt.', tone: 'bad', evidence: { type: 'meaning_unit', id: 'mu-1' } },
          { text: 'Đối thủ Minh Long đã xuất hiện hai lần trong hội thoại của khách này trong ba tuần qua.', tone: 'bad', evidence: { type: 'meaning_unit', id: 'mu-2' } },
        ]
      : [{ text: `Chưa đủ dữ liệu để tóm tắt sâu về ${person.name}.`, tone: 'neutral', evidence: { type: 'meaning_unit', id: 'mu-0' } }],
    timeline: isBao
      ? [
          { id: 'tl-1', event_type: 'Complained', conclusion: 'Nhắc lần thứ ba, giọng gay gắt, nêu khả năng tìm nhà cung cấp khác', confidence: 0.93, observed_at: ago(18), group: GROUP_TP, evidence: { type: 'meaning_unit', id: 'mu-1' } },
          { id: 'tl-2', event_type: 'AskedStatus', conclusion: 'Hỏi tiến độ hợp đồng in ấn quý 4 lần thứ hai', confidence: 0.88, observed_at: ago(260), group: GROUP_TP, evidence: { type: 'meaning_unit', id: 'mu-2' } },
          { id: 'tl-3', event_type: 'SentQuotation', conclusion: 'Ban Tài chính gửi báo giá 84.000.000 ₫ cho quý 4', confidence: 0.95, observed_at: ago(60 * 24 * 3), group: GROUP_TP, evidence: { type: 'meaning_unit', id: 'mu-3' } },
          { id: 'tl-4', event_type: 'MentionsCompetitor', conclusion: 'Nhắc Minh Long đang chào giá thấp hơn 4%', confidence: 0.79, observed_at: ago(60 * 24 * 9), group: GROUP_TP, evidence: { type: 'meaning_unit', id: 'mu-4' } },
          { id: 'tl-5', event_type: 'DealWon', conclusion: 'Chốt đơn in bao bì 96.000.000 ₫, thanh toán đủ', confidence: 0.97, observed_at: ago(60 * 24 * 30), group: GROUP_TP, evidence: { type: 'meaning_unit', id: 'mu-5' } },
        ]
      : [],
    documents: isBao
      ? [
          { id: 'doc-1', title: 'BaoGia_ThanhPhat_Q4.docx', mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', bytes: 84_200, created_at: ago(60 * 24 * 3) },
          { id: 'doc-2', title: 'HopDong_ThanhPhat_ban_sua.docx', mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', bytes: 112_400, created_at: ago(18) },
        ]
      : [],
    touchpoints: isBao
      ? [
          { id: STAFF_IDS.ha, name: 'Nguyễn Thu Hà' }, { id: STAFF_IDS.khoa, name: 'Trần Minh Khoa' },
          { id: AGENT_IDS.tls, name: 'Agent Trợ lý thương mại' },
        ]
      : [],
    merge_history: [],
  };
}

// ─── Sổ tay nhận thức ────────────────────────────────────────────────────────
const SECTION_TITLE: Record<string, string> = {
  attention_now: 'Điều cần chú ý ngay', rolling_context: 'Ngữ cảnh ngắn lũy tiến', guardrails: 'Giới hạn cho agent',
  preferences: 'Sở thích', open_threads: 'Việc dở',
};
const SECTION_KEYS = Object.keys(SECTION_TITLE);
const NEVER_COMPACT = new Set(['guardrails']);
const estimateTokens = (body: string) => Math.max(1, Math.ceil(body.length / 3));

interface EntryRow {
  id: string; section: string; body: string; refs: { type: string; id: string }[]; pinned: boolean;
  author: { type: 'user' | 'agent'; label: string }; created_at: string;
}
interface NbState {
  code: string; name: string; tokenBudget: number; compactionNo: number; lastCompactedAt: string | null;
  entries: EntryRow[]; dropped: NbDroppedItem[]; history: NbHistoryItem[];
}
let nextEntryId = 1;
const newId = (prefix: string) => `${prefix}-${nextEntryId++}`;

function seedEntry(section: string, body: string, opts: Partial<EntryRow> = {}): EntryRow {
  return { id: newId('nbe'), section, body, refs: [], pinned: false, author: { type: 'agent', label: 'agent' }, created_at: ago(30), ...opts };
}

function seedBaoNotebook(): NbState {
  return {
    code: 'PER-0042', name: 'Nguyễn Văn Bảo', tokenBudget: 4000, compactionNo: 14, lastCompactedAt: ago(15),
    entries: [
      seedEntry('rolling_context', 'Đang ở giai đoạn đàm phán hợp đồng in quý 4, mức 84 triệu ₫, chờ Sếp duyệt chi.', { created_at: ago(90) }),
      seedEntry('rolling_context', 'Đã mua ba lần trong 18 tháng, luôn thanh toán đúng hạn.', { created_at: ago(60 * 24 * 5) }),
      seedEntry('rolling_context', 'Quan tâm mốc giao hơn giá — chốt nhanh khi có mốc cụ thể bằng văn bản.', { created_at: ago(60 * 24 * 20) }),
      seedEntry('attention_now', 'Đã nhắc ba lần chưa được trả lời — mọi phản hồi phải xin lỗi trước, không giải thích dài.', { created_at: ago(20) }),
      seedEntry('attention_now', 'Đang so sánh giá với Minh Long, chênh 4% nằm ở loại giấy và bảo hành màu.', { created_at: ago(60 * 24 * 9) }),
      seedEntry('guardrails', 'Không để agent junior follow khách này. Không tự cam kết mốc giao dưới 12 ngày.', {
        pinned: true, author: { type: 'user', label: 'Anh Nguyễn Văn A' }, created_at: ago(60 * 24 * 9),
      }),
      seedEntry('preferences', 'Trả lời trong 15 phút thì hội thoại tiếp tục; quá 60 phút thì phải xin lỗi mới nối lại được.', { created_at: ago(60 * 24 * 2) }),
      seedEntry('preferences', 'Gửi kèm bảng so sánh vật liệu khiến người này chốt nhanh hơn là giảm giá.', { created_at: ago(60 * 24 * 6) }),
      seedEntry('open_threads', 'Chưa rõ ngân sách in ấn cả năm 2027.', { created_at: ago(60 * 24 * 12) }),
      seedEntry('open_threads', 'Chưa biết ai là người ký cuối bên Thành Phát nếu anh Bảo đi vắng.', { created_at: ago(60 * 24 * 12) }),
    ],
    dropped: [
      { id: newId('nbd'), section: 'rolling_context', body: '187 tin nhắn chào hỏi và xác nhận ngắn', refs: [], author: { type: 'agent' }, archived_at: ago(60 * 24) },
      { id: newId('nbd'), section: 'rolling_context', body: '9 sự kiện trước tháng 6 năm nay', refs: [], author: { type: 'agent' }, archived_at: ago(60 * 24 * 30) },
      { id: newId('nbd'), section: 'open_threads', body: 'Chi tiết từng dòng của ba báo giá cũ — chỉ giữ ID', refs: [{ type: 'draft', id: 'draft-ACT-0231' }], author: { type: 'agent' }, archived_at: ago(60 * 24 * 40) },
      { id: newId('nbd'), section: 'rolling_context', body: '24 tin trao đổi nội bộ không liên quan người này', refs: [], author: { type: 'agent' }, archived_at: ago(60 * 24 * 40) },
      { id: newId('nbd'), section: 'preferences', body: 'Toàn văn hợp đồng tháng 8 — chỉ giữ DOC-0844', refs: [], author: { type: 'agent' }, archived_at: ago(60 * 24 * 50) },
    ],
    history: [
      { compaction_no: 14, at: ago(15), tokens_before: 3412, tokens_after: 1842, archived: 187, summary: 'Gộp 187 tin chào hỏi và xác nhận ngắn thành một câu về phong cách giao tiếp.' },
      { compaction_no: 13, at: ago(60 * 24), tokens_before: 3208, tokens_after: 1604, archived: 24, summary: '24 tin trao đổi nội bộ không liên quan gộp thành một dòng ngữ cảnh.' },
      { compaction_no: 12, at: ago(60 * 24 * 3), tokens_before: 2940, tokens_after: 1488, archived: 9, summary: '9 sự kiện trước tháng 6 gộp vào ngữ cảnh nền.' },
    ],
  };
}

function seedGroupNotebook(): NbState {
  return {
    code: 'GRP-ZL-0114', name: 'Vận hành Genesis — Quý 4', tokenBudget: 4000, compactionNo: 9, lastCompactedAt: ago(15),
    entries: [
      seedEntry('rolling_context', 'Đây là nhóm điều hành chính, việc giao ở đây gần như luôn có mốc giờ và cần chốt bằng văn bản.', { created_at: ago(90) }),
      seedEntry('rolling_context', 'Anh Khoa là người ra quyết định trong nhóm; chị Hà giữ phần tài chính và duyệt chi.', { created_at: ago(60 * 24 * 2) }),
      seedEntry('rolling_context', 'Nhóm hay tag agent để so sánh báo giá — đây là loại yêu cầu xuất hiện nhiều nhất.', { created_at: ago(15) }),
      seedEntry('attention_now', 'Không nhắc lại chuyện Thành Phát chậm phản hồi trong nhóm này — đang là chuyện tế nhị.', { created_at: ago(60 * 8) }),
      seedEntry('guardrails', 'Mọi số tiền trên 50 triệu ₫ phải dừng lại chờ Sếp duyệt, không được tự cam kết.', {
        pinned: true, author: { type: 'user', label: 'Anh Nguyễn Văn A' }, created_at: ago(60 * 24 * 16),
      }),
      seedEntry('preferences', 'Nhóm hoạt động mạnh nhất 08:00–10:00 và 14:00–17:00; sau 18:00 gần như im.', { created_at: ago(60 * 24) }),
      seedEntry('preferences', 'Văn phong ngắn, không chào hỏi dài. Trả lời quá dài thường bị bỏ qua.', { created_at: ago(60 * 24) }),
      seedEntry('open_threads', 'Chưa rõ ai thay anh Khoa quyết khi anh đi vắng.', { created_at: ago(60 * 24 * 20) }),
      seedEntry('open_threads', 'Chưa có tín hiệu nào về kế hoạch quý 1 năm sau.', { created_at: ago(60 * 24 * 20) }),
    ],
    dropped: [
      { id: newId('nbd'), section: 'rolling_context', body: '96 tin xác nhận ngắn "ok", "vâng" trong 14 ngày', refs: [], author: { type: 'agent' }, archived_at: ago(60 * 24 * 2) },
      { id: newId('nbd'), section: 'rolling_context', body: '41 tệp ảnh không chú thích', refs: [], author: { type: 'agent' }, archived_at: ago(60 * 24 * 5) },
      { id: newId('nbd'), section: 'open_threads', body: '18 sự kiện lịch đã qua và không phát sinh việc', refs: [], author: { type: 'agent' }, archived_at: ago(60 * 24 * 10) },
    ],
    history: [
      { compaction_no: 9, at: ago(15), tokens_before: 2860, tokens_after: 2106, archived: 96, summary: 'Gộp 96 tin xác nhận ngắn thành một câu về nhịp phản hồi của nhóm.' },
      { compaction_no: 8, at: ago(60 * 24 * 3), tokens_before: 2520, tokens_after: 1840, archived: 41, summary: 'Gộp 41 tệp ảnh không chú thích, chỉ giữ số lượng.' },
    ],
  };
}

const NB_REFS_BAO: { type: string; id: string; code: string | null; label: string }[] = [
  { type: 'person', id: 'p-bao', code: 'PER-0042', label: 'Người hiện tại' },
  { type: 'group', id: 'g-zl-0489', code: 'GRP-ZL-0489', label: 'Nhóm đối tác in ấn' },
  { type: 'group', id: 'g-zl-0114', code: 'GRP-ZL-0114', label: 'Nhóm vận hành có nhắc tới' },
  { type: 'draft', id: 'draft-ACT-0231', code: 'ACT-0231', label: 'Bản nháp báo giá 84 triệu ₫' },
  { type: 'alert', id: 'iq-alert-1', code: 'ALR-0233', label: 'Cảnh báo ba tin chưa trả lời' },
];
const NB_REFS_GROUP: { type: string; id: string; code: string | null; label: string }[] = [
  { type: 'group', id: 'g-zl-0114', code: 'GRP-ZL-0114', label: 'Nhóm hiện tại' },
  { type: 'person', id: 'p-khoa', code: 'PER-0003', label: 'Trần Minh Khoa — người quyết định' },
  { type: 'person', id: 'p-ha', code: 'PER-0007', label: 'Nguyễn Thu Hà — tài chính' },
  { type: 'draft', id: 'draft-ACT-0231', code: 'ACT-0231', label: 'Bản nháp báo giá chờ duyệt' },
  { type: 'alert', id: 'iq-alert-1', code: 'ALR-0233', label: 'Cảnh báo rủi ro Thành Phát' },
];

// ─── Tài liệu ─────────────────────────────────────────────────────────────────
interface DocRow extends DocumentDetail {
  content: string; // base64
}

export function createMock(opts: P3Options) {
  const groups = seedGroups();
  const people = seedPeople();
  const notebooks = new Map<string, NbState>(
    opts.fresh
      ? []
      : [
          ['person:p-bao', seedBaoNotebook()],
          ['group:g-zl-0114', seedGroupNotebook()],
        ],
  );
  let documents: DocRow[] = opts.fresh
    ? []
    : [
        {
          id: 'doc-1', title: 'BaoGia_ThanhPhat_Q4.docx', description: 'Báo giá hợp đồng in ấn quý 4',
          mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', bytes: 84_200,
          owner: BAO, source: 'agent', created_by: 'Agent Trợ lý thương mại', created_at: ago(60 * 24 * 3), updated_at: ago(60 * 24 * 3),
          acl: [{ principal: 'role:owner', can_read: true, can_write: true }],
          content: b64('BaoGia_ThanhPhat_Q4 — nội dung mẫu'),
        },
        {
          id: 'doc-2', title: 'HopDong_ThanhPhat_ban_sua.docx', description: 'Hợp đồng bản sửa khách gửi lại',
          mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', bytes: 112_400,
          owner: BAO, source: 'channel', created_by: null, created_at: ago(18), updated_at: ago(18),
          acl: [{ principal: 'role:owner', can_read: true, can_write: true }, { principal: 'role:manager', can_read: true, can_write: false }],
          content: b64('HopDong_ThanhPhat_ban_sua — nội dung mẫu'),
        },
        {
          id: 'doc-3', title: 'DonHang_BaoBi_T8.xlsx', description: 'Đơn hàng bao bì tháng 8, đã ghi vào ERP',
          mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', bytes: 42_100,
          owner: GROUP_TP, source: 'tay', created_by: 'Anh Nguyễn Văn A (Chủ)', created_at: ago(60 * 24 * 30), updated_at: ago(60 * 24 * 30),
          acl: [{ principal: 'role:owner', can_read: true, can_write: true }],
          content: b64('DonHang_BaoBi_T8 — nội dung mẫu'),
        },
      ];

  registerExplain('score', (id) => {
    const [, personId] = id.split(':');
    const person = people.find((p) => p.id === personId);
    if (!person) return null;
    return {
      kind: 'score', id, title: `${person.name} — điểm`, statement: `${person.heat}/100`,
      method: 'rules+model', factors: [], units: [], history: [{ value: person.heat, computed_at: ago(20), method: 'rules+model', by: null }],
    };
  });

  function findPerson(id: string) {
    return people.find((p) => p.id === id) ?? null;
  }

  function nbListLabel(id: string, state: NbState): NbSubject {
    const active = state.entries;
    return {
      id, code: state.code, name: state.name, entries: active.length,
      token_used: active.reduce((s, e) => s + estimateTokens(e.body), 0),
      token_budget: state.tokenBudget,
      updated_at: active.length ? active.map((e) => e.created_at).sort().at(-1)! : ago(0),
    };
  }

  function ensureNotebook(type: 'person' | 'group', id: string): { key: string; state: NbState } {
    const key = `${type}:${id}`;
    let state = notebooks.get(key);
    if (!state) {
      const person = type === 'person' ? findPerson(id) : null;
      const group = type === 'group' ? groups.find((g) => g.id === id) : null;
      state = {
        code: person?.code ?? group?.code ?? id, name: person?.name ?? group?.name ?? id,
        tokenBudget: 4000, compactionNo: 0, lastCompactedAt: null, entries: [], dropped: [], history: [],
      };
      notebooks.set(key, state);
    }
    return { key, state };
  }

  function toNbNotebook(type: 'person' | 'group', id: string, state: NbState): NbNotebook {
    const refs = type === 'person' && id === 'p-bao' ? NB_REFS_BAO : type === 'group' && id === 'g-zl-0114' ? NB_REFS_GROUP : [];
    const used = state.entries.reduce((s, e) => s + estimateTokens(e.body), 0);
    return {
      subject: { type, id, code: state.code, name: state.name },
      token_used: used, token_budget: state.tokenBudget, compaction_no: state.compactionNo, last_compacted_at: state.lastCompactedAt,
      sections: SECTION_KEYS.map((key) => ({
        key, title: SECTION_TITLE[key],
        entries: state.entries.filter((e) => e.section === key).sort((a, b) => (a.created_at < b.created_at ? 1 : -1)).map((e) => ({
          id: e.id, body: e.body, refs: e.refs, pinned: e.pinned, editable: e.author.type === 'user', author: e.author, created_at: e.created_at,
        })) as NbEntry[],
      })),
      refs,
    };
  }

  function toNbHistory(state: NbState): NbHistoryItem[] {
    return [...state.history].sort((a, b) => b.compaction_no - a.compaction_no);
  }

  function docVisible(d: DocRow): DocumentItem {
    const { content: _c, acl: _a, ...rest } = d;
    return rest;
  }

  /** Như gh.biz.relations.service._ensure_agent (v0.1.35): `agent_id` không phải UUID → 422; không thuộc tổ chức
   * (không có ở Danh tính Agent, cũng không phải BOT cũ của dữ liệu mẫu) → 404 'Agent'. `false` = đã trả lỗi. */
  function agentRef(ctx: P2Ctx, id: unknown): { id: string; name: string } | false {
    if (rejectNonUuid(ctx.problem, 'agent_id', id)) return false;
    const found = opts.findAgent?.(String(id));
    const name = found?.name ?? AGENT_NAME[String(id)];
    if (!name) {
      ctx.problem(404, 'NOT_FOUND', 'Agent không tồn tại hoặc ngoài phạm vi của bạn');
      return false;
    }
    AGENT_NAME[String(id)] = name;
    return { id: String(id), name };
  }

  function handle(ctx: P2Ctx): boolean {
    const { method: m, path: p, url, body, reply, problem, text } = ctx;
    const seg = p.split('/').filter(Boolean);

    // ── Nhóm & Con người ──
    if (p === '/directory/channels' && m === 'GET') {
      if (!has(ctx, 'profile.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      return reply(200, CHANNELS);
    }
    if (seg[0] === 'directory' && seg[1] === 'groups') {
      if (!has(ctx, 'profile.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (seg.length === 2 && m === 'GET') {
        const channelId = url.searchParams.get('channel_id');
        const kind = url.searchParams.get('kind');
        const listenMode = url.searchParams.get('listen_mode');
        const gq = (url.searchParams.get('q') ?? '').trim().toLowerCase();
        let rows = groups;
        if (channelId) rows = rows.filter((g) => `ch-${g.channel.type}` === channelId);
        if (kind) rows = rows.filter((g) => g.kind === kind);
        if (listenMode) rows = rows.filter((g) => g.listen_mode === listenMode);
        if (gq) rows = rows.filter((g) => g.name.toLowerCase().includes(gq) || g.code.toLowerCase().includes(gq));
        return reply(200, pageOf(rows, url));
      }
      if (seg.length === 4 && seg[3] === 'bot' && m === 'POST') {
        if (!has(ctx, 'profile.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        const g = groups.find((x) => x.id === seg[2]);
        if (!g) return problem(404, 'NOT_FOUND', 'Nhóm không tồn tại hoặc ngoài phạm vi của bạn');
        const b = body as { agent_id?: string | null; autonomy_level?: number | null };
        const agent = b.agent_id != null ? agentRef(ctx, b.agent_id) : null;
        if (agent === false) return true;
        if ('agent_id' in b) g.bot = agent;
        return reply(200, g);
      }
    }
    if (seg[0] === 'directory' && seg[1] === 'people') {
      if (!has(ctx, 'profile.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (seg.length === 2 && m === 'GET') {
        const relation = url.searchParams.get('relation');
        const heat = url.searchParams.get('heat');
        const value = url.searchParams.get('value');
        const priority = url.searchParams.get('priority');
        const bot = url.searchParams.get('bot');
        let rows = people;
        if (relation) rows = rows.filter((r) => relationOf(r.relation) === relation);
        if (heat) rows = rows.filter((r) => (heat === 'high' ? r.heat >= 80 : heat === 'mid' ? r.heat >= 50 && r.heat < 80 : r.heat < 50));
        if (value) rows = rows.filter((r) => (value === 'high' ? (r.valueVnd ?? 0) >= 500_000_000 : value === 'mid' ? (r.valueVnd ?? 0) >= 100_000_000 && (r.valueVnd ?? 0) < 500_000_000 : !r.valueVnd));
        if (priority) rows = rows.filter((r) => r.priority === priority);
        if (bot) rows = rows.filter((r) => (bot === 'assigned' ? !!r.agentId : !r.agentId));
        rows = matchPeople(rows, { channel: url.searchParams.get('channel'), q: url.searchParams.get('q') });
        return reply(200, pageOf(rows.map(toDirPerson), url));
      }
      // Gán BOT hàng loạt theo bộ lọc: máy chủ tự tìm MỌI người khớp (không giới hạn ở trang đã tải).
      if (seg.length === 3 && seg[2] === 'bulk-bot' && m === 'POST') {
        if (!has(ctx, 'profile.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        const b = body as { filter?: Record<string, string | undefined>; agent_id?: string | null; autonomy_level?: number | null; expected_total?: number };
        const f = b.filter ?? {};
        let rows = people;
        if (f.relation) rows = rows.filter((r) => relationOf(r.relation) === f.relation);
        if (f.heat) rows = rows.filter((r) => (f.heat === 'high' ? r.heat >= 80 : f.heat === 'mid' ? r.heat >= 50 && r.heat < 80 : r.heat < 50));
        if (f.priority) rows = rows.filter((r) => r.priority === f.priority);
        if (f.bot) rows = rows.filter((r) => (f.bot === 'assigned' ? !!r.agentId : !r.agentId));
        rows = matchPeople(rows, { channel: f.channel ?? null, q: f.q ?? null });
        if (b.expected_total != null && b.expected_total !== rows.length) {
          return problem(409, 'BULK_FILTER_CHANGED', 'Danh sách đã thay đổi', { detail: `Bộ lọc hiện khớp ${rows.length} người (không phải ${b.expected_total}).` });
        }
        const agent = b.agent_id != null ? agentRef(ctx, b.agent_id) : null;
        if (agent === false) return true;
        for (const r of rows) {
          if ('agent_id' in b) r.agentId = agent ? agent.id : null;
          if ('autonomy_level' in b) r.autonomy = b.autonomy_level ?? null;
        }
        return reply(200, { ok: true, updated: rows.length });
      }
    }
    const peopleBot = /^directory\/people\/([^/]+)\/bot$/.exec(seg.join('/'));
    if (peopleBot && m === 'POST') {
      if (!has(ctx, 'profile.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      const person = findPerson(decodeURIComponent(peopleBot[1]));
      if (!person) return problem(404, 'NOT_FOUND', 'Người không tồn tại hoặc ngoài phạm vi của bạn');
      const b = body as { agent_id?: string | null; autonomy_level?: number | null };
      const agent = b.agent_id != null ? agentRef(ctx, b.agent_id) : null;
      if (agent === false) return true;
      if ('agent_id' in b) person.agentId = agent ? agent.id : null;
      if ('autonomy_level' in b) person.autonomy = b.autonomy_level ?? null;
      return reply(200, toDirPerson(person));
    }

    // ── Hồ sơ sống ──
    if (seg[0] === 'profile' && seg.length === 2) {
      if (!has(ctx, 'profile.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      const person = findPerson(seg[1]);
      if (!person) return problem(404, 'NOT_FOUND', 'Người không tồn tại hoặc ngoài phạm vi của bạn');
      if (m === 'GET') return reply(200, profileFor(person));
      if (m === 'PATCH') {
        if (!has(ctx, 'profile.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        const b = body as { owner_user_id?: string | null; autonomy_level?: number | null; note?: string | null };
        if ('autonomy_level' in b) person.autonomy = b.autonomy_level ?? null;
        if ('owner_user_id' in b) person.ownerUserId = b.owner_user_id ?? null;
        const prof = profileFor(person);
        if ('note' in b) prof.owner_note = b.note ?? null;
        return reply(200, prof);
      }
    }

    // ── Sổ tay nhận thức ──
    if (seg[0] === 'notebook') {
      if (!has(ctx, 'profile.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (seg[1] === 'subjects' && seg.length === 2 && m === 'GET') {
        const type = (url.searchParams.get('type') ?? 'person') as 'person' | 'group';
        const rows = [...notebooks.entries()]
          .filter(([k]) => k.startsWith(`${type}:`))
          .map(([k, s]) => nbListLabel(k.slice(type.length + 1), s));
        rows.sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));
        return reply(200, { items: rows, next_cursor: null });
      }
      const type = seg[1] as 'person' | 'group';
      const sid = seg[2] ? decodeURIComponent(seg[2]) : '';
      if ((type === 'person' || type === 'group') && sid) {
        const { state } = ensureNotebook(type, sid);
        if (seg.length === 3 && m === 'GET') return reply(200, toNbNotebook(type, sid, state));
        if (seg.length === 4 && seg[3] === 'entries' && m === 'POST') {
          if (!has(ctx, 'profile.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
          const b = body as { section: string; body: string; pinned?: boolean; refs?: { type: string; id: string }[] };
          if (!SECTION_KEYS.includes(b.section)) return problem(422, 'VALIDATION', 'Mục sổ tay không hợp lệ');
          if (!b.body?.trim()) return problem(422, 'VALIDATION', 'Nội dung không được để trống');
          const row = seedEntry(b.section, b.body.trim(), { pinned: !!b.pinned, refs: b.refs ?? [], author: { type: 'user', label: ctx.userLabel }, created_at: new Date().toISOString() });
          state.entries.push(row);
          return reply(201, { id: row.id, body: row.body, refs: row.refs, pinned: row.pinned, editable: true, author: row.author, created_at: row.created_at });
        }
        if (seg.length === 5 && seg[3] === 'entries') {
          const eid = decodeURIComponent(seg[4]);
          const row = state.entries.find((e) => e.id === eid);
          if (m === 'PATCH') {
            if (!has(ctx, 'profile.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
            if (!row) return problem(404, 'NOT_FOUND', 'Mục sổ tay không tồn tại');
            const b = body as { body?: string; pinned?: boolean };
            if (b.body !== undefined) {
              if (row.author.type !== 'user') return problem(409, 'SYSTEM_ENTRY_READONLY', 'Mục do hệ thống ghi, chỉ ghim được, không sửa nội dung');
              state.entries = state.entries.filter((e) => e.id !== eid);
              const next = seedEntry(row.section, b.body.trim(), { pinned: b.pinned ?? row.pinned, refs: row.refs, author: row.author, created_at: new Date().toISOString() });
              state.entries.push(next);
              return reply(200, { id: next.id });
            }
            if (b.pinned !== undefined) row.pinned = b.pinned;
            return reply(200, { id: row.id });
          }
          if (m === 'DELETE') {
            if (!has(ctx, 'profile.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
            if (!row) return problem(404, 'NOT_FOUND', 'Mục sổ tay không tồn tại');
            if (row.author.type !== 'user') return problem(409, 'SYSTEM_ENTRY_READONLY', 'Mục do hệ thống ghi, chỉ ghim được, không xoá');
            state.entries = state.entries.filter((e) => e.id !== eid);
            state.dropped.unshift({ id: row.id, section: row.section, body: row.body, refs: row.refs, author: row.author, archived_at: new Date().toISOString() });
            return reply(204);
          }
        }
        if (seg.length === 4 && seg[3] === 'compact' && m === 'POST') {
          if (!has(ctx, 'profile.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
          const before = state.entries.reduce((s, e) => s + estimateTokens(e.body), 0);
          const toArchive = state.entries.filter((e) => !e.pinned && !NEVER_COMPACT.has(e.section));
          if (toArchive.length) {
            state.entries = state.entries.filter((e) => e.pinned || NEVER_COMPACT.has(e.section));
            const now = new Date().toISOString();
            for (const e of toArchive) state.dropped.unshift({ id: e.id, section: e.section, body: e.body, refs: e.refs, author: e.author, archived_at: now });
            state.entries.push(seedEntry('rolling_context', `Gộp ${toArchive.length} mục cũ thành ngữ cảnh nền.`, { author: { type: 'agent', label: 'agent' }, created_at: now }));
            state.compactionNo += 1;
            state.lastCompactedAt = now;
            const after = state.entries.reduce((s, e) => s + estimateTokens(e.body), 0);
            state.history.unshift({ compaction_no: state.compactionNo, at: now, tokens_before: before, tokens_after: after, archived: toArchive.length, summary: `Gộp ${toArchive.length} mục cũ thành một ngữ cảnh nền.` });
          }
          return reply(200, toNbNotebook(type, sid, state));
        }
        if (seg.length === 4 && seg[3] === 'reset' && m === 'POST') {
          if (!has(ctx, 'profile.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
          const toArchive = state.entries.filter((e) => !e.pinned && !NEVER_COMPACT.has(e.section));
          const now = new Date().toISOString();
          for (const e of toArchive) state.dropped.unshift({ id: e.id, section: e.section, body: e.body, refs: e.refs, author: e.author, archived_at: now });
          state.entries = state.entries.filter((e) => e.pinned || NEVER_COMPACT.has(e.section));
          return reply(200, toNbNotebook(type, sid, state));
        }
        if (seg.length === 4 && seg[3] === 'history' && m === 'GET') return reply(200, toNbHistory(state));
        if (seg.length === 4 && seg[3] === 'dropped' && m === 'GET') return reply(200, { items: state.dropped, next_cursor: null });
      }
    }

    // ── Tài liệu ──
    if (seg[0] === 'documents') {
      if (!has(ctx, 'profile.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (seg.length === 1 && m === 'GET') {
        const ownerPerson = url.searchParams.get('owner_person_id');
        const ownerGroup = url.searchParams.get('owner_group_id');
        const source = url.searchParams.get('source');
        let rows = documents;
        if (ownerPerson) rows = rows.filter((d) => d.owner?.id === ownerPerson);
        if (ownerGroup) rows = rows.filter((d) => d.owner?.id === ownerGroup);
        if (source) rows = rows.filter((d) => d.source === source);
        return reply(200, { items: rows.map(docVisible), next_cursor: null, total: rows.length });
      }
      if (seg.length === 1 && m === 'POST') {
        if (!has(ctx, 'profile.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        const b = body as {
          title: string; description?: string | null; filename: string; mime: string; content_base64: string;
          owner_person_id?: string; owner_group_id?: string; acl?: { principal: string; can_read: boolean; can_write: boolean }[];
        };
        if (!b.title?.trim()) return problem(422, 'VALIDATION', 'Cần tiêu đề tài liệu', { errors: { title: 'Không được để trống' } });
        if (!b.content_base64) return problem(422, 'VALIDATION', 'Thiếu nội dung tệp', { errors: { content_base64: 'Không được để trống' } });
        const bytes = Math.ceil((b.content_base64.length * 3) / 4);
        if (bytes > 20 * 1024 * 1024) return problem(422, 'VALIDATION', 'Tệp vượt quá 20MB');
        const ownerPerson = b.owner_person_id ? findPerson(b.owner_person_id) : null;
        const ownerGroup = !ownerPerson && b.owner_group_id ? groups.find((g) => g.id === b.owner_group_id) : undefined;
        const owner: DocRow['owner'] = ownerPerson ? toDirPerson(ownerPerson) : ownerGroup ? toGroupRef(ownerGroup) : null;
        const row: DocRow = {
          id: `doc-new-${Date.now()}`, title: b.title.trim(), description: b.description ?? null, mime: b.mime, bytes,
          owner,
          source: 'tay', created_by: ctx.userLabel, created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
          acl: [{ principal: 'role:owner', can_read: true, can_write: true }, { principal: `user:${ctx.userLabel}`, can_read: true, can_write: true }, ...(b.acl ?? [])],
          content: b.content_base64,
        };
        documents = [row, ...documents];
        return reply(201, row);
      }
      const doc = documents.find((d) => d.id === seg[1]);
      if (seg.length === 2 && m === 'GET') {
        if (!doc) return problem(404, 'NOT_FOUND', 'Tài liệu không tồn tại hoặc ngoài phạm vi của bạn');
        return reply(200, doc);
      }
      if (seg.length === 3 && seg[2] === 'content' && m === 'GET') {
        if (!doc) return problem(404, 'NOT_FOUND', 'Tài liệu không tồn tại hoặc ngoài phạm vi của bạn');
        const bytes = Buffer.from(doc.content, 'base64').toString('utf8');
        return text(200, doc.mime, bytes, doc.title);
      }
      if (seg.length === 2 && m === 'PATCH') {
        if (!has(ctx, 'profile.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        if (!doc) return problem(404, 'NOT_FOUND', 'Tài liệu không tồn tại hoặc ngoài phạm vi của bạn');
        const b = body as { title?: string; description?: string | null; owner_person_id?: string | null; owner_group_id?: string | null };
        if (b.title !== undefined) doc.title = b.title;
        if (b.description !== undefined) doc.description = b.description;
        doc.updated_at = new Date().toISOString();
        return reply(200, doc);
      }
      if (seg.length === 3 && seg[2] === 'acl' && m === 'PUT') {
        if (!has(ctx, 'profile.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        if (!doc) return problem(404, 'NOT_FOUND', 'Tài liệu không tồn tại hoặc ngoài phạm vi của bạn');
        doc.acl = body as unknown as DocRow['acl'];
        return reply(200, doc.acl);
      }
      if (seg.length === 2 && m === 'DELETE') {
        if (!has(ctx, 'profile.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        if (!doc) return problem(404, 'NOT_FOUND', 'Tài liệu không tồn tại hoặc ngoài phạm vi của bạn');
        documents = documents.filter((d) => d.id !== doc.id);
        return reply(204);
      }
    }

    return false;
  }

  return {
    handle,
    hooks: {
      groups: () => groups,
      people: () => people,
      documents: () => documents,
      notebooks: () => notebooks,
    } as Record<string, (...args: never[]) => unknown>,
    dispose: () => {},
  };
}
