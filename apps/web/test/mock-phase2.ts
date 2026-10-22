/**
 * Phase-2 part of the mock API (docs/api/phase-2.md): data layer, channels,
 * AI brain, setup steps 4–7 and 12, and the realtime events the web expects.
 * Seeded from docs/design/seed-data.json so the Console renders the design's
 * rows; values the seed file only carries as display strings are parsed back
 * to raw numbers (the web formats them again).
 */
import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type {
  AgentParam,
  Channel,
  ChannelGroup,
  ChannelState,
  CleanItem,
  CliLoginEvent,
  CliProfile,
  Credential,
  IdentityCandidate,
  IdentityHistoryItem,
  IdentityStats,
  ListenMode,
  Notebook,
  NotebookCompaction,
  Pipeline,
  Provider,
  RawItem,
  RawState,
  Ref,
  RefineryRun,
  RefineryScheduleConfig,
  Rule,
  RuleCondition,
  RuleOutput,
  RuleVersion,
  ViewScope,
  Weight,
} from '../../../packages/contracts/src/phase2';
import type { ListenLogDecision, ListenLogEvent, ListenLogItem, ListenLogMessage } from '../../../packages/contracts/src/listenLog';

// ── seed file ─────────────────────────────────────────────────────────────
type SeedRow = Record<string, unknown>;
function loadSeed(): Record<string, SeedRow[]> {
  const candidates: string[] = [];
  try {
    candidates.push(resolve(dirname(fileURLToPath(import.meta.url)), '../../../docs/design/seed-data.json'));
  } catch {
    /* bundled config: fall back to cwd */
  }
  candidates.push(resolve(process.cwd(), '../../docs/design/seed-data.json'), resolve(process.cwd(), 'docs/design/seed-data.json'));
  for (const p of candidates) {
    try {
      return JSON.parse(readFileSync(p, 'utf8')) as Record<string, SeedRow[]>;
    } catch {
      /* try next */
    }
  }
  return {};
}
const SEED = loadSeed();
/** Dòng thô của một mảng seed theo khoá (`docs/design/seed-data.json`) — dùng lại ở `mock-api.ts` cho `auditLog`. */
export const seedRows = (k: string): SeedRow[] => (Array.isArray(SEED[k]) ? SEED[k] : []);
/** "1.244" → 1244, "0,94" → 0.94, "99,9%" → 99.9 */
const num = (s: unknown): number => Number(String(s ?? '').replace(/[%\s]/g, '').replace(/\./g, '').replace(',', '.'));

// ── clock: pin the design's wall-clock times to the latest 15:11:44 (GMT+7) ─
const TZ_OFFSET_MS = 7 * 3600_000;
function anchorBase(now = Date.now()): number {
  const local = new Date(now + TZ_OFFSET_MS);
  let base = Date.UTC(local.getUTCFullYear(), local.getUTCMonth(), local.getUTCDate(), 15, 11, 44) - TZ_OFFSET_MS;
  if (base > now) base -= 86_400_000;
  return base;
}
const iso = (ms: number) => new Date(ms).toISOString();

// ── helpers ───────────────────────────────────────────────────────────────
export interface P2Ctx {
  method: string;
  path: string;
  url: URL;
  body: Record<string, unknown>;
  perms: Record<string, string>;
  /** Both return `true` so handlers can `return reply(...)`. */
  reply: (status: number, body?: unknown) => true;
  problem: (status: number, code: string, title: string, extra?: Record<string, unknown>) => true;
  /** Non-JSON body (CSV export). */
  text: (status: number, contentType: string, body: string, filename?: string) => true;
  /** true when the session has no live PIN verification. */
  needPin: () => boolean;
  userLabel: string;
  /** Owner sees raw text unmasked (khoá cứng 8). */
  owner: boolean;
  /** Mã vai trò (`owner|manager|operator|agent_staff|auditor`) — cụm `people` cần role thật cho nhánh Q4, generic `perms` không đủ (Auditor có `people_review.read = none` trong ma trận chung nhưng vẫn phải nhận 200 nhánh `log`). */
  role: string;
}

/** gh/data/common.py mask_text: long digit runs (phones, accounts) keep only the last 3 digits. */
export function maskText(text: string | null): string | null {
  if (text == null) return text;
  return text.replace(/(?<!\d)(\d[\d .-]{7,22}\d)(?!\d)/g, (m) => {
    const digits = m.replace(/\D/g, '');
    return digits.length < 8 ? m : '•'.repeat(digits.length - 3) + digits.slice(-3);
  });
}

type Emit = (type: string, data: unknown) => void;

export interface Phase2Options {
  fresh: boolean;
  simulate: boolean;
  emit: Emit;
}

const PEOPLE: Array<[string, string]> = [
  ['PER-0042', 'Nguyễn Văn Bảo'],
  ['PER-0311', 'Trần Văn Hậu'],
  ['PER-0402', 'Lê Minh Tâm'],
  ['PER-0118', 'Hoàng Thị Lan'],
  ['PER-0007', 'Phạm Thu Hà'],
  ['PER-0512', 'Võ Thanh Tùng'],
  ['PER-0619', 'Đặng Quốc Huy'],
  ['PER-0733', 'Bùi Ngọc Anh'],
  ['PER-0844', 'Ngô Đức Long'],
  ['PER-0951', 'Phạm Quốc Minh'],
  ['PER-1002', 'Trịnh Hoài Nam'],
  ['PER-1017', 'Lý Thị Mai'],
];

interface GroupSeed {
  code: string;
  name: string;
  members: number;
  kind: string;
  listen_mode: ListenMode;
  view_scope: ViewScope;
  raw24h: number;
}

const NAMED_GROUPS: Record<'zalo' | 'whatsapp', GroupSeed[]> = {
  zalo: [
    { code: 'GRP-ZL-0114', name: 'Vận hành Genesis — Quý 4', members: 24, kind: 'internal', listen_mode: 'tagged_only', view_scope: 'all_members', raw24h: 4108 },
    { code: 'GRP-ZL-0231', name: 'Group Ngành gỗ Miền Nam', members: 412, kind: 'market', listen_mode: 'silent', view_scope: 'owner', raw24h: 6412 },
    { code: 'GRP-ZL-0356', name: 'Group Nhân sự Logistics', members: 286, kind: 'market', listen_mode: 'silent', view_scope: 'owner', raw24h: 2634 },
    { code: 'GRP-ZL-0174', name: 'Ban Tài chính', members: 11, kind: 'internal', listen_mode: 'tagged_only', view_scope: 'manager', raw24h: 1802 },
    { code: 'GRP-ZL-0620', name: 'Nhóm Kỹ thuật hạ tầng', members: 9, kind: 'internal', listen_mode: 'tagged_only', view_scope: 'manager', raw24h: 1216 },
    { code: 'GRP-ZL-0489', name: 'Đối tác in ấn Thành Phát', members: 4, kind: 'partner', listen_mode: 'proactive', view_scope: 'all_members', raw24h: 784 },
    { code: 'GRP-ZL-0502', name: 'Truyền thông & Sự kiện', members: 13, kind: 'internal', listen_mode: 'tagged_only', view_scope: 'manager', raw24h: 28 },
    { code: 'GRP-ZL-0611', name: 'Nhóm riêng của Sếp', members: 1, kind: 'private', listen_mode: 'off', view_scope: 'owner', raw24h: 0 },
  ],
  whatsapp: [
    { code: 'GRP-WA-0007', name: 'Điều hành mở rộng', members: 6, kind: 'internal', listen_mode: 'paused', view_scope: 'manager', raw24h: 54 },
    { code: 'GRP-WA-0011', name: 'Kho lạnh Tân Cảng', members: 3, kind: 'customer', listen_mode: 'paused', view_scope: 'all_members', raw24h: 42 },
    { code: 'GRP-WA-0015', name: 'Đại lý phía Bắc', members: 18, kind: 'customer', listen_mode: 'paused', view_scope: 'manager', raw24h: 0 },
    { code: 'GRP-WA-0019', name: 'Nhà cung cấp ván ép', members: 7, kind: 'partner', listen_mode: 'paused', view_scope: 'owner', raw24h: 0 },
    { code: 'GRP-WA-0023', name: 'Gia đình', members: 5, kind: 'private', listen_mode: 'off', view_scope: 'owner', raw24h: 0 },
    { code: 'GRP-WA-0027', name: 'Hội cựu sinh viên', members: 140, kind: 'private', listen_mode: 'off', view_scope: 'owner', raw24h: 0 },
  ],
};
const PREFIX = ['Khách sỉ', 'Đại lý', 'Nhà cung cấp', 'Dự án', 'Đối tác vận tải', 'Chợ gỗ'];
const REGION = ['Miền Tây', 'Hà Nội', 'Đà Nẵng', 'Bình Dương', 'Long An', 'Cần Thơ', 'Hải Phòng'];

/** 31 more listening Zalo groups so "38 nhóm lắng nghe" / "42 nhóm" are real rows. */
function genericZaloGroups(): GroupSeed[] {
  const out: GroupSeed[] = [];
  for (let i = 0; i < 31; i++) {
    out.push({
      code: `GRP-ZL-${String(700 + i * 7).padStart(4, '0')}`,
      name: `${PREFIX[i % PREFIX.length]} ${REGION[(i * 3) % REGION.length]}${i >= 21 ? ` ${Math.floor(i / 7)}` : ''}`,
      members: 8 + ((i * 37) % 180),
      kind: i % 3 === 0 ? 'customer' : i % 3 === 1 ? 'market' : 'partner',
      listen_mode: i % 5 === 0 ? 'tagged_only' : 'silent',
      view_scope: 'manager',
      raw24h: 47,
    });
  }
  return out;
}

// Rules as the API presets encode them (apps/api gh/refinery/presets.py), hits from the design.
const RULES: Array<Omit<Rule, 'id' | 'version' | 'updated_at' | 'kind_label' | 'hits_24h'>> = [
  {
    code: 'R-01', name: 'Nhận diện nhu cầu mua', kind: 'intent', threshold: 0.7, enabled: true,
    conditions: [
      { type: 'has_entity', entity: 'qty', label: 'có từ khoá số lượng + đơn vị' },
      { type: 'has_entity', entity: 'price', label: 'có mức giá hoặc ngân sách' },
      { type: 'is_question', label: 'câu hỏi trực tiếp' },
    ],
    outputs: [
      { set: 'intent', value: 'AskedPrice', label: 'ý định: hỏi giá' },
      { set: 'side', value: 'demand', label: 'phía mua (cầu)' },
      { add: 'heat', value: 30, label: 'độ nóng += 30' },
    ],
    prompt_hint: 'Người nói cần mua / hỏi giá một mặt hàng cụ thể.',
  },
  {
    code: 'R-02', name: 'Nhận diện nguồn cung', kind: 'intent', threshold: 0.7, enabled: true,
    conditions: [
      { type: 'keyword_any', values: ['còn tồn', 'có sẵn', 'kho', 'sẵn hàng', 'cần bán'], label: 'có từ "còn tồn", "có sẵn", "kho"' },
      { type: 'has_entity', entity: 'product', label: 'nêu mặt hàng cụ thể' },
    ],
    outputs: [
      { set: 'intent', value: 'OfferedSupply', label: 'ý định: chào bán' },
      { set: 'side', value: 'supply', label: 'phía bán (cung)' },
      { add: 'potential', value: 20, label: 'vào danh sách cần bán' },
    ],
    prompt_hint: 'Người nói đang chào bán / có sẵn hàng.',
  },
  {
    code: 'R-03', name: 'Tín hiệu bất mãn', kind: 'risk', threshold: 0.75, enabled: true,
    conditions: [
      { type: 'repeat_unanswered', n: 2, label: 'nhắc lại ≥ 2 lần chưa được trả lời' },
      { type: 'keyword_any', values: ['không ai trả lời', 'chậm', 'trễ', 'thất vọng'], label: 'giọng điệu tiêu cực' },
      { type: 'keyword_any', values: ['chỗ khác', 'bên khác', 'nhà cung cấp khác'], label: 'nêu phương án thay thế' },
    ],
    outputs: [
      { set: 'intent', value: 'Complained', label: 'ý định: than phiền' },
      { add: 'churn_risk', value: 40, label: 'nguy cơ mất khách +40' },
      { alert: 'P1', label: 'báo ngay cho Sếp (ưu tiên cao)' },
    ],
    prompt_hint: 'Khách phàn nàn, bực bội hoặc doạ chuyển sang nhà cung cấp khác.',
  },
  {
    code: 'R-04', name: 'Đối thủ xuất hiện', kind: 'competition', threshold: 0.65, enabled: true,
    conditions: [
      { type: 'llm', hint: 'Tin nhắc tên một đối thủ cạnh tranh của tổ chức', label: 'có tên trong danh sách đối thủ' },
      { type: 'regex', pattern: '(rẻ|thấp|cao|tốt) hơn|\\d+\\s?%|so với|chào giá|điều khoản|bảo hành', label: 'kèm so sánh giá hoặc điều khoản' },
    ],
    outputs: [
      { set: 'intent', value: 'MentionsCompetitor', label: 'sự kiện: nhắc tới đối thủ' },
      { add: 'churn_risk', value: 15, label: 'nguy cơ mất khách +15' },
    ],
    prompt_hint: 'Có nhắc tới đối thủ và so sánh giá / điều khoản.',
  },
  {
    code: 'R-05', name: 'Tín hiệu tìm việc', kind: 'hr', threshold: 0.6, enabled: true,
    conditions: [
      { type: 'regex', pattern: '\\d+\\s*năm(\\s+kinh nghiệm)?|kinh nghiệm|từng làm', label: 'nêu kinh nghiệm + ngành' },
      { type: 'keyword_any', values: ['tìm việc', 'cơ hội mới', 'đổi hướng', 'chuyển ngành'], label: 'có ý đổi hướng hoặc tìm cơ hội' },
    ],
    outputs: [
      { set: 'person_type', value: 'candidate', label: 'loại = Ứng viên' },
      { add: 'fit', value: 20, label: 'độ phù hợp theo vị trí trống' },
    ],
    prompt_hint: 'Người nói giới thiệu kinh nghiệm và đang tìm cơ hội việc làm (chỉ ghi nhận tín hiệu).',
  },
  {
    code: 'R-06', name: 'Loại nhiễu', kind: 'hygiene', threshold: 0.4, enabled: true,
    conditions: [
      { type: 'max_words', n: 3, no_entity: true, label: 'dưới 4 từ và không có thực thể' },
      { type: 'kind_in', values: ['sticker', 'image', 'reaction', 'system'], label: 'sticker, ảnh không chú thích' },
      { type: 'regex', pattern: '^\\s*(chào|xin chào|ok|dạ|vâng|cảm ơn)[\\s!.,]*$', label: 'chào hỏi thuần' },
    ],
    outputs: [
      { set: 'label', value: 'Noise', label: 'đánh dấu là tin nhiễu' },
      { discard: true, label: 'không ghi vào kho sạch' },
    ],
    prompt_hint: null,
  },
];
const KIND_LABEL: Record<string, string> = {
  intent: 'Ý định', risk: 'Rủi ro', competition: 'Cạnh tranh', hr: 'Nhân sự', hygiene: 'Vệ sinh', custom: 'Tuỳ chỉnh',
};
const RULE_HITS: Record<string, number> = { 'R-01': 1842, 'R-02': 744, 'R-03': 96, 'R-04': 41, 'R-05': 18, 'R-06': 8204 };

const WEIGHT_DIMS: Array<[string, string]> = [
  ['heat', 'Độ nóng của tín hiệu'],
  ['potential', 'Tiềm năng giá trị'],
  ['churn_risk', 'Rủi ro mất khách'],
  ['fit', 'Mức độ phù hợp'],
  ['engagement', 'Độ gắn kết lịch sử'],
  ['data_confidence', 'Độ tin cậy dữ liệu'],
];

const RAW_TEMPLATES: Array<{ text: string; label: string | null; conf: number; state: RawState; kind?: string }> = [
  { text: 'Anh ơi bên mình còn MDF 18mm loại E1 không, em cần 2 cont giao Long An.', label: 'AskedPrice', conf: 0.93, state: 'clean' },
  { text: 'Hàng về chậm quá, tuần này mà chưa có chắc em phải tìm bên khác.', label: 'Complained', conf: 0.88, state: 'clean' },
  { text: 'Kho mình còn 12 kiện ván ép phủ phim, ai cần liên hệ mình nhé.', label: 'OfferedSupply', conf: 0.9, state: 'clean' },
  { text: 'dạ', label: 'Noise', conf: 0.2, state: 'discarded' },
  { text: 'Bên Minh Long báo giá thấp hơn 3% cho cùng loại giấy, anh xem lại nha.', label: 'MentionsCompetitor', conf: 0.81, state: 'clean' },
  { text: 'Em gửi file báo giá mới nhất trong nhóm nhé.', label: 'SentDocument', conf: 0.84, state: 'clean' },
  { text: 'cho hỏi giá ván melamine trắng tầm bao nhiêu', label: 'AskedPrice', conf: 0.57, state: 'lowconf' },
  { text: '[Hình ảnh]', label: 'Noise', conf: 0.1, state: 'discarded', kind: 'image' },
  { text: 'Mình có 6 năm làm kho vận, đang tìm cơ hội mới ở mảng gỗ.', label: 'JobSignal', conf: 0.72, state: 'clean' },
  { text: 'Đơn tuần trước giao thiếu 2 kiện, bên mình kiểm tra lại giúp.', label: 'Complained', conf: 0.86, state: 'clean' },
  { text: 'Ok cảm ơn anh', label: 'Noise', conf: 0.18, state: 'discarded' },
  { text: 'Cuối tháng này bên anh giao được 3 cont không, giá bao nhiêu một tấm?', label: 'AskedPrice', conf: 0.91, state: 'clean' },
];

const CLEAN_TEMPLATES: Array<{ event: string; conclusion: string; score: number }> = [
  { event: 'AskedPrice', conclusion: 'Hỏi giá MDF 18mm E1, 2 cont giao Long An', score: 78 },
  { event: 'Complained', conclusion: 'Than hàng về chậm, nêu khả năng chuyển nhà cung cấp', score: 72 },
  { event: 'OfferedSupply', conclusion: 'Có sẵn 12 kiện ván ép phủ phim tại kho', score: 64 },
  { event: 'MentionsCompetitor', conclusion: 'Minh Long báo giá thấp hơn 3% cùng loại giấy', score: 58 },
  { event: 'SentDocument', conclusion: 'Gửi báo giá mới nhất vào nhóm', score: 40 },
  { event: 'JobSignal', conclusion: '6 năm kho vận, đang tìm cơ hội ở mảng gỗ', score: 61 },
];

/** A QR-looking SVG (not a real code) as a data URL. */
function fakeQr(seed: string): string {
  const n = 25;
  let h = 0;
  for (const ch of seed) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  const rnd = () => {
    h ^= h << 13;
    h ^= h >>> 17;
    h ^= h << 5;
    return (h >>> 0) / 4294967296;
  };
  const cells: string[] = [];
  const finder = (x: number, y: number) =>
    (x < 7 && y < 7) || (x >= n - 7 && y < 7) || (x < 7 && y >= n - 7);
  for (let y = 0; y < n; y++)
    for (let x = 0; x < n; x++) {
      let on: boolean;
      if (finder(x, y)) {
        const fx = x < 7 ? x : x - (n - 7);
        const fy = y < 7 ? y : y - (n - 7);
        on = fx === 0 || fx === 6 || fy === 0 || fy === 6 || (fx >= 2 && fx <= 4 && fy >= 2 && fy <= 4);
      } else on = rnd() > 0.52;
      if (on) cells.push(`<rect x="${x + 2}" y="${y + 2}" width="1" height="1"/>`);
    }
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${n + 4} ${n + 4}" shape-rendering="crispEdges"><rect width="100%" height="100%" fill="#fff"/><g fill="#161826">${cells.join('')}</g></svg>`;
  return `data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}`;
}

// ── state ─────────────────────────────────────────────────────────────────
export function createPhase2(opts: Phase2Options) {
  const { emit, fresh } = opts;
  const timers = new Set<ReturnType<typeof setTimeout>>();
  const later = (ms: number, fn: () => void) => {
    const t = setTimeout(() => {
      timers.delete(t);
      fn();
    }, ms);
    timers.add(t);
    return t;
  };
  const base = anchorBase();
  const at = (hms: string) => {
    const [h, m, s = '0'] = hms.split(':');
    const secs = (15 * 3600 + 11 * 60 + 44) - (Number(h) * 3600 + Number(m) * 60 + Number(s));
    return base - secs * 1000;
  };

  const people = new Map<string, Ref>();
  const person = (code: string): Ref => {
    let p = people.get(code);
    if (!p) {
      const name = PEOPLE.find(([c]) => c === code)?.[1] ?? `Người ${code.slice(4)}`;
      p = { id: randomUUID(), code, name };
      people.set(code, p);
    }
    return p;
  };
  PEOPLE.forEach(([c]) => person(c));

  // Groups (per channel). Fresh installs sync groups only after a channel goes active, all "off".
  const groups: Array<ChannelGroup & { channel: 'zalo' | 'whatsapp'; raw24h: number }> = [];
  const syncGroups = (type: 'zalo' | 'whatsapp', allOff: boolean) => {
    if (groups.some((g) => g.channel === type)) return;
    const seeds = type === 'zalo' ? [...NAMED_GROUPS.zalo, ...genericZaloGroups()] : NAMED_GROUPS.whatsapp;
    for (const s of seeds) {
      groups.push({
        id: randomUUID(),
        code: s.code,
        name: s.name,
        members: s.members,
        kind: s.kind,
        listen_mode: allOff ? newGroupListenMode(type) : s.listen_mode, // QD 17/10: nhóm mới theo `new_group_mode` của kênh (mặc định silent), như API thật
        view_scope: s.view_scope,
        channel: type,
        raw24h: allOff ? 0 : s.raw24h,
      });
    }
  };
  /** QD 17/10 (như API thật): nhóm mới của kênh vào `silent` (mặc định) / `pending` (Hỏi Sếp) / `off`; silent ⇒ cộng bộ đếm thông báo. */
  const newSilent: Record<string, number> = {};
  const newGroupListenMode = (type: string): ListenMode => {
    const mode = channelOf(type)?.new_group_mode ?? 'silent';
    if (mode === 'silent') newSilent[type] = (newSilent[type] ?? 0) + 1;
    return mode === 'ask' ? 'pending' : mode;
  };
  const groupByCode = (code: string) => groups.find((g) => g.code === code);
  const groupRef = (code: string): Ref | null => {
    const g = groupByCode(code);
    return g ? { id: g.id, code: g.code, name: g.name } : null;
  };
  const isListening = (g: { listen_mode: ListenMode }) => ['tagged_only', 'silent', 'proactive'].includes(g.listen_mode); // như LISTENING_MODES của API
  const listening = (type?: string) => groups.filter((g) => isListening(g) && (!type || g.channel === type)).length;

  // Channels
  const channels: Channel[] = [
    {
      type: 'zalo', name: 'Zalo', installed: true, id: randomUUID(), state: fresh ? 'logged_out' : 'active',
      account_label: fresh ? null : 'iPhone của Sếp',
      started_at: fresh ? null : iso(Date.now() - (14 * 86_400_000 + 6 * 3600_000 + 41 * 60_000)),
      groups_listening: 0, outbound_queued: 0, last_heartbeat_at: fresh ? null : iso(Date.now() - 20_000),
      stats: fresh ? null : { msgs_24h: 1244, tagged_24h: 31, latency_ms: 420, uptime_pct: 99.9 },
      qr: null, listen_direct: true, direct_mode: 'silent', new_group_mode: 'silent', new_group_asked: false,
    },
    {
      type: 'whatsapp', name: 'WhatsApp', installed: true, id: randomUUID(), state: fresh ? 'logged_out' : 'expired',
      account_label: fresh ? null : 'Android của Sếp', started_at: null,
      groups_listening: 0, outbound_queued: fresh ? 0 : 3, last_heartbeat_at: fresh ? null : iso(at('15:05:00')),
      stats: fresh ? null : { msgs_24h: 96, tagged_24h: 9, latency_ms: 510, uptime_pct: 71.2 },
      qr: null, listen_direct: true, direct_mode: 'silent', new_group_mode: 'silent', new_group_asked: false,
    },
    {
      type: 'telegram', name: 'Telegram', installed: false, id: null, state: 'not_installed', account_label: null,
      started_at: null, groups_listening: 0, outbound_queued: 0, last_heartbeat_at: null, stats: null, qr: null,
    },
    {
      type: 'linkedin', name: 'LinkedIn', installed: true, id: randomUUID(), state: 'identity_only', account_label: null,
      started_at: null, groups_listening: 0, outbound_queued: 0, last_heartbeat_at: null, qr: null,
      stats: { msgs_24h: null, tagged_24h: null, latency_ms: null, uptime_pct: null, identities: fresh ? 0 : 34, merged: fresh ? 0 : 12 },
    },
  ];
  if (!fresh) {
    syncGroups('zalo', false);
    syncGroups('whatsapp', false);
    // v0.1.65: MOCK_PENDING_GROUPS=1 ⇒ vài nhóm Zalo MỚI đang chờ Sếp chọn (xem hộp "Nhóm mới" bằng tay / chụp ảnh).
  }
  /** v0.1.65: bridge vừa thấy nhóm MỚI ⇒ vào `pending`; `announce` phát WS `group.discovered` như API thật. */
  let discoverSeq = 0;
  const discoverGroups = (type: 'zalo' | 'whatsapp', announce: boolean, ask = false): number => {
    if (ask) {
      const c = channelOf(type);
      if (c) c.new_group_mode = 'ask'; // e2e/chụp ảnh hộp "Nhóm mới": Sếp đã đặt «Nhóm mới: Hỏi Sếp»
    }
    const sample: Array<[string, number]> = [['Khách sỉ Hà Nội — nhóm mới', 40], ['Đối tác kho Bình Dương', 9], ['Cựu sinh viên K12', 212]];
    for (const [name, members] of sample) {
      discoverSeq += 1;
      // F1d + QD 17/10: như API thật — `new_group_mode` của kênh quyết định nhóm mới vào silent (mặc định) / pending (ask) / off.
      const listen_mode = newGroupListenMode(type);
      const g = { id: randomUUID(), code: `GRP-${type === 'zalo' ? 'ZL' : 'WA'}-09${discoverSeq}0`, name, members, kind: 'internal', listen_mode, view_scope: 'owner' as ViewScope, channel: type, raw24h: 0 };
      groups.push(g);
      if (announce) emit('group.discovered', { id: g.id, code: g.code, name: g.name, channel: type, listen_mode });
    }
    return sample.length;
  };
  if (!fresh && process.env.MOCK_PENDING_GROUPS === '1') discoverGroups('zalo', false);
  const channelView = (c: Channel): Channel => ({
    ...c,
    groups_listening: c.type === 'zalo' || c.type === 'whatsapp' ? listening(c.type) : 0,
    ...(c.type === 'zalo' || c.type === 'whatsapp' ? { new_groups_silent: newSilent[c.type] ?? 0 } : {}),
    groups_pending: c.type === 'zalo' || c.type === 'whatsapp' ? groups.filter((g) => g.channel === c.type && g.listen_mode === 'pending').length : 0,
  });
  const channelOf = (type: string) => channels.find((c) => c.type === type);

  // Raw lake
  const raw: RawItem[] = [];
  const rawEvidence = new Map<string, string>(); // raw id → person code (for evidence lookups)
  let rawCode = 918431;
  const mkRaw = (p: {
    at: number; ch: 'zalo' | 'whatsapp'; gid: string; pid: string; text: string; label: string | null; conf: number | null; state: RawState; kind?: string;
  }): RawItem => {
    const item: RawItem = {
      id: randomUUID(),
      code: `RAW-${rawCode--}`,
      received_at: iso(p.at),
      occurred_at: iso(p.at - 1500),
      channel: { type: p.ch, name: p.ch === 'zalo' ? 'Zalo' : 'WhatsApp' },
      group: groupRef(p.gid),
      person: person(p.pid),
      direction: 'inbound',
      kind: p.kind ?? 'text',
      text: p.text,
      label: p.label,
      confidence: p.conf,
      state: p.state,
    };
    rawEvidence.set(item.id, p.pid);
    return item;
  };
  const stateOf = (st: string): RawState => (st === 'raw' ? 'pending' : (['pending', 'processing', 'clean', 'lowconf', 'discarded', 'error'].includes(st) ? st : 'pending') as RawState);
  let rawTotal = fresh ? 0 : 18412;
  let rawPending = fresh ? 0 : 4204;
  if (!fresh) {
    for (const r of seedRows('rawRows')) {
      raw.push(
        mkRaw({
          at: at(String(r.time)),
          ch: r.ch === 'w' ? 'whatsapp' : 'zalo',
          gid: String(r.gid),
          pid: String(r.pid),
          text: String(r.text),
          label: r.label ? String(r.label) : null,
          conf: r.confidence ? num(r.confidence) : null,
          state: stateOf(String(r.st)),
          kind: r.text === 'Sticker' ? 'sticker' : 'text',
        }),
      );
    }
    // Older rows so the table can page (infinite scroll).
    const gids = [...NAMED_GROUPS.zalo.slice(0, 6).map((g) => g.code), 'GRP-WA-0007', 'GRP-WA-0011'];
    let t = at('14:41:38');
    for (let i = 0; i < 150; i++) {
      t -= (90 + ((i * 53) % 260)) * 1000;
      const tpl = RAW_TEMPLATES[i % RAW_TEMPLATES.length];
      const gid = gids[(i * 5) % gids.length];
      raw.push(
        mkRaw({
          at: t, ch: gid.startsWith('GRP-WA') ? 'whatsapp' : 'zalo', gid, pid: PEOPLE[(i * 7) % PEOPLE.length][0],
          text: tpl.text, label: tpl.label, conf: tpl.conf, state: tpl.state, kind: tpl.kind,
        }),
      );
    }
  }

  // Refinery
  const schedule: RefineryScheduleConfig & { next_run_at: number } = {
    interval_seconds: 900, count_threshold: 500, batch_size: 250, min_confidence: 0.6,
    next_run_at: Date.now() + (4 * 60 + 12) * 1000,
  };
  const runs: RefineryRun[] = fresh
    ? []
    : ([
        ['15:00:00', 486, 0, 'schedule'],
        ['14:45:00', 512, 2, 'schedule'],
        ['14:30:00', 448, 0, 'schedule'],
        ['14:15:00', 604, 0, 'threshold'],
        ['14:00:00', 396, 0, 'schedule'],
      ] as Array<[string, number, number, RefineryRun['trigger']]>).map(([hms, n, low, trigger]) => ({
        id: randomUUID(), trigger, started_at: iso(at(hms)), finished_at: iso(at(hms) + 48_000),
        input_count: n, clean_count: Math.round(n * 0.62), lowconf_count: low, noise_count: n - Math.round(n * 0.62) - low,
        error_count: 0, status: 'done' as const,
      }));
  let cleanTotal = fresh ? 0 : 14208;
  let manualRun: string | null = null;

  // Rules
  const rules: Rule[] = [];
  const versions = new Map<string, RuleVersion[]>();
  const addRule = (r: (typeof RULES)[number], enabled = r.enabled) => {
    const rule: Rule = {
      ...r, id: randomUUID(), enabled, version: 1, kind_label: KIND_LABEL[r.kind] ?? r.kind,
      hits_24h: fresh ? 0 : (RULE_HITS[r.code] ?? 0), updated_at: iso(base - 86_400_000 * 3),
    };
    rules.push(rule);
    versions.set(rule.id, [{ version: 1, conditions: rule.conditions, outputs: rule.outputs, threshold: rule.threshold, created_at: rule.updated_at, created_by: { label: 'Anh Nguyễn Văn A (Chủ)' } }]);
  };
  if (!fresh) RULES.forEach((r) => addRule(r));
  let weights: Weight[] = WEIGHT_DIMS.map(([dimension, label], i) => {
    const seeded = seedRows('weights')[i];
    return { dimension, label, value: seeded ? num(seeded.value) : [30, 25, 20, 12, 8, 5][i] };
  });

  // Clean store
  const clean: CleanItem[] = [];
  if (!fresh) {
    for (const r of seedRows('cleanRows')) {
      const cyc = at(`${String(r.cycle)}:00`);
      clean.push({
        id: randomUUID(), observed_at: iso(cyc - 120_000), group: groupRef(String(r.gid)), person: person(String(r.pid)),
        event_type: String(r.event), conclusion: String(r.conclusion), score: num(r.score), confidence: 0.8 + (num(r.score) % 17) / 100,
        cycle_at: iso(cyc), raw_event_ids: raw.filter((x) => x.person?.code === r.pid).slice(0, 2).map((x) => x.id),
      });
    }
    let cyc = at('13:45:00');
    const gids = NAMED_GROUPS.zalo.slice(0, 6).map((g) => g.code);
    for (let i = 0; i < 60; i++) {
      if (i % 6 === 0) cyc -= 15 * 60_000;
      const tpl = CLEAN_TEMPLATES[i % CLEAN_TEMPLATES.length];
      const pid = PEOPLE[(i * 5 + 2) % PEOPLE.length][0];
      clean.push({
        id: randomUUID(), observed_at: iso(cyc - 60_000), group: groupRef(gids[i % gids.length]), person: person(pid),
        event_type: tpl.event, conclusion: tpl.conclusion, score: Math.max(20, tpl.score - (i % 9) * 3), confidence: 0.7 + (i % 3) / 10,
        cycle_at: iso(cyc), raw_event_ids: [],
      });
    }
  }

  // Notebooks (lõi)
  const notebooks = new Map<string, Notebook>();
  const compactions = new Map<string, NotebookCompaction[]>();
  const SECTION_TITLES: Array<[string, string]> = [
    ['attention_now', 'Điều cần chú ý ngay'],
    ['rolling_context', 'Ngữ cảnh ngắn lũy tiến'],
    ['guardrails', 'Giới hạn cho agent'],
    ['preferences', 'Sở thích'],
    ['open_threads', 'Việc dở'],
  ];
  const notebookFor = (type: 'person' | 'group', id: string): Notebook | null => {
    const key = `${type}:${id}`;
    const existing = notebooks.get(key);
    if (existing) return existing;
    if (type !== 'person') return null;
    const p = [...people.values()].find((x) => x.id === id);
    if (!p) return null;
    const memory = seedRows('memory');
    const isBao = p.code === 'PER-0042';
    const entry = (body: string, author: 'agent' | 'user', ageMs: number, pinned = false) => ({
      id: randomUUID(), body, refs: [], author: { type: author, label: author === 'user' ? 'Anh Nguyễn Văn A (Chủ)' : 'Core agent' }, pinned, created_at: iso(Date.now() - ageMs),
    });
    const mine = clean.filter((c) => c.person?.id === id);
    const sections = SECTION_TITLES.map(([k, title], i) => {
      let entries: Notebook['sections'][number]['entries'] = [];
      let updated: string | null = null;
      if (isBao && i < 3) {
        const m = memory[i];
        const lines = Array.isArray(m?.lines) ? (m.lines as string[]) : [];
        const guard = k === 'guardrails';
        const pinnedAt = guard ? Date.now() - (Date.now() - Date.UTC(new Date(base).getUTCFullYear(), 8, 12, 3)) : 0;
        entries = lines.map((l) => (guard ? { ...entry(l, 'user', 0, true), created_at: iso(pinnedAt) } : entry(l, 'agent', 120_000)));
        updated = iso(k === 'attention_now' ? Date.now() - 120_000 : at('15:00:00'));
      } else if (!isBao && k === 'rolling_context' && mine.length) {
        entries = mine.slice(0, 2).map((c) => entry(c.conclusion, 'agent', 3600_000));
        updated = mine[0].cycle_at;
      }
      return { key: k, title, updated_at: updated, entries };
    });
    const nb: Notebook = {
      id: randomUUID(), subject: { type: 'person', id: p.id, code: p.code, name: p.name },
      token_used: isBao ? 1842 : 220 + mine.length * 90, token_budget: 4000,
      compaction_no: isBao ? 14 : 0, last_compacted_at: isBao ? iso(at('15:00:00')) : null, sections,
    };
    notebooks.set(key, nb);
    const dropped = seedRows('memory')[3];
    compactions.set(
      key,
      isBao
        ? [
            { compaction_no: 14, at: iso(at('15:00:00')), tokens_before: 3920, tokens_after: 1842, archived: 196, summary: Array.isArray(dropped?.lines) ? String((dropped.lines as string[])[0]) : '187 tin nhắn chào hỏi và xác nhận ngắn, 9 sự kiện trước tháng 6.' },
            { compaction_no: 13, at: iso(at('09:00:00') - 86_400_000), tokens_before: 3710, tokens_after: 1690, archived: 142, summary: '142 tin xác nhận lịch giao, 3 báo giá cũ đã được thay thế.' },
          ]
        : [],
    );
    return nb;
  };

  // Identity
  const idStats: IdentityStats = fresh
    ? { merged_people: 0, live_profiles: 0, pending_pairs: 0, manual_splits: 0, unlinked_accounts: 0 }
    : (() => {
        const s = seedRows('idStats');
        const v = (i: number, d: number) => (s[i] ? num(s[i].value) : d);
        return { merged_people: v(0, 136), live_profiles: 148, pending_pairs: v(1, 12), manual_splits: v(2, 4), unlinked_accounts: v(3, 31) };
      })();
  const chOf = (c: unknown) => (c === 'w' ? 'whatsapp' : c === 'l' ? 'linkedin' : 'zalo');
  const candidates: IdentityCandidate[] = fresh
    ? []
    : seedRows('idPairs').map((p) => ({
        id: randomUUID(),
        confidence: num(p.confidence) / 100,
        level: String(p.conf) as IdentityCandidate['level'],
        basis: String(p.basis),
        basis_detail: { rule: 'phone_or_name', score: num(p.confidence) / 100 },
        a: { identity_id: randomUUID(), person: { id: randomUUID(), code: `PER-${String(100 + num(p.confidence)).padStart(4, '0')}`, name: String(p.aName) }, channel: chOf(p.aCh), meta: String(p.aMeta) },
        b: { identity_id: randomUUID(), person: { id: randomUUID(), code: `PER-${String(200 + num(p.confidence)).padStart(4, '0')}`, name: String(p.bName) }, channel: chOf(p.bCh), meta: String(p.bMeta) },
      }));
  const bao = person('PER-0042');
  const history: IdentityHistoryItem[] = fresh
    ? []
    : [
        {
          id: randomUUID(), op: 'merge', at: iso(base - 2 * 86_400_000), actor: { label: 'Anh Nguyễn Văn A (Chủ)' },
          from: { id: randomUUID(), code: 'PER-0877', name: 'Bảo Thành Phát' },
          to: {
            id: bao.id, code: bao.code, name: bao.name,
            identities: [
              { identity_id: randomUUID(), channel: 'zalo', meta: 'Zalo · +84 903 xxx 118' },
              { identity_id: randomUUID(), channel: 'whatsapp', meta: 'WhatsApp · +84 903 xxx 118' },
            ],
          },
          identities: 2, reverted: false,
        },
        {
          id: randomUUID(), op: 'split', at: iso(base - 5 * 86_400_000), actor: { label: 'Anh Nguyễn Văn A (Chủ)' },
          from: { id: randomUUID(), code: 'PER-0512', name: 'Võ Thanh Tùng' }, to: { id: randomUUID(), code: 'PER-0960', name: 'Tùng (kho Bình Dương)' },
          identities: 1, reverted: false,
        },
      ];

  // AI brain
  const providers: Provider[] = fresh
    ? []
    : [
        {
          id: randomUUID(), kind: 'antigravity_cli', name: 'Antigravity Brain', endpoint: null, failover_rank: 1, enabled: true, auth_state: 'ok',
          keys: [], models: [{ id: randomUUID(), model_name: 'gemini-2.5-pro', daily_quota: 3200, used_today: 1204 }],
        },
        {
          id: randomUUID(), kind: 'gemini', name: 'Gemini API', endpoint: null, failover_rank: 2, enabled: true, auth_state: 'ok',
          keys: [1, 2, 3, 4].map((i) => ({ id: randomUUID(), label: `GEM-KEY-0${i}`, last4: ['x9Qa', 'Lm2P', '7tRe', 'Qw0z'][i - 1], enabled: true, cooldown_until: null, quota_left_pct: 82 })),
          models: [{ id: randomUUID(), model_name: 'gemini-2.5-flash', daily_quota: 16000, used_today: 8412 }],
        },
        {
          id: randomUUID(), kind: 'deepseek', name: 'DeepSeek API', endpoint: null, failover_rank: 3, enabled: true, auth_state: 'expiring',
          keys: [{ id: randomUUID(), label: 'DS-KEY-01', last4: 'd81K', enabled: true, cooldown_until: null, quota_left_pct: 18 }],
          models: [{ id: randomUUID(), model_name: 'deepseek-reasoner', daily_quota: 3900, used_today: 3184 }],
        },
      ];
  const cliProfiles: CliProfile[] = fresh
    ? []
    : [
        { id: randomUUID(), email: 'a@example.test', plan_label: 'Google AI Pro · token 0 ₫', active: true, expires_at: iso(Date.now() + 23 * 3600_000 + 20 * 60_000), state: 'ok' },
        { id: randomUUID(), email: 'b@example.test', plan_label: 'Google AI · miễn phí', active: false, expires_at: iso(Date.now() + 5 * 86_400_000), state: 'ok' },
      ];
  // v0.1.31: hồ sơ Claude Code CLI (gói Claude) — tách khỏi hồ sơ Google của Antigravity, mặc định trống (TẮT).
  const claudeProfiles: CliProfile[] = [];
  const profilesOf = (kind: string | null) => (kind === 'claude_code_cli' ? claudeProfiles : cliProfiles);
  const cliLogins = new Map<string, CliLoginEvent>();
  // Như máy chủ (gh.providers.catalog): danh sách model GỐC theo nhóm + gợi ý nhanh/rẻ hay mạnh; v0.1.32: mức suy nghĩ
  // (effort) tách riêng — agy `--effort low|medium|high`, Claude Code thêm xhigh, max (haiku không chỉnh mức).
  type Eff = 'low' | 'medium' | 'high' | 'xhigh' | 'max';
  const opt = (id: string, label: string, group: string, tier: 'fast' | 'balanced' | 'strong', source: 'cli' | 'catalog' = 'cli', efforts: Eff[] = [], extra: { default_effort?: Eff; verified?: boolean; source_ref?: string } = {}) => ({
    id, label, group, tier, hint: tier === 'fast' ? 'nhanh, rẻ' : tier === 'balanced' ? 'cân bằng' : 'mạnh, chậm hơn, tốn hạn mức hơn', source, efforts,
    default_effort: extra.default_effort ?? null, verified: extra.verified ?? true, source_ref: extra.source_ref ?? null,
  });
  const CC: Eff[] = ['low', 'medium', 'high', 'xhigh', 'max'];
  const CC_SRC = '`claude --help` 2.1.285 + https://code.claude.com/docs/en/model-config (đọc 2026-10-01)';
  const MODEL_GROUPS: Record<string, Array<{ label: string; models: ReturnType<typeof opt>[] }>> = {
    antigravity_cli: [
      { label: 'Gemini', models: [opt('gemini-3.8-flash', 'Gemini 3.8 Flash', 'Gemini', 'balanced', 'cli', ['low', 'medium', 'high'], { default_effort: 'high' }), opt('gemini-3.1-pro', 'Gemini 3.1 Pro', 'Gemini', 'strong', 'cli', ['low', 'high'])] },
      { label: 'Claude (qua Antigravity)', models: [opt('claude-sonnet-4-6-thinking', 'Claude Sonnet 4.6 (Thinking)', 'Claude (qua Antigravity)', 'balanced')] },
    ],
    claude_code_cli: [
      { label: 'Claude', models: [opt('haiku', 'Haiku (bản mới nhất)', 'Claude', 'fast', 'catalog', [], { source_ref: CC_SRC }), opt('sonnet', 'Sonnet (bản mới nhất)', 'Claude', 'balanced', 'catalog', CC, { source_ref: CC_SRC }), opt('opus', 'Opus (bản mới nhất)', 'Claude', 'strong', 'catalog', CC, { source_ref: CC_SRC }), opt('fable', 'Fable (bản mới nhất)', 'Claude', 'strong', 'catalog', CC, { source_ref: CC_SRC })] },
    ],
  };

  const credentials = (): Credential[] => {
    const out: Credential[] = [];
    for (const p of providers) {
      if (p.kind === 'antigravity_cli' || p.kind === 'claude_code_cli') continue;
      if (p.kind === 'gemini' && p.keys.length > 1) {
        out.push({ icon: 'ph ph-key', name: `${p.name} — ${p.keys.length} khoá xoay vòng`, meta: `${p.keys[0].label} … ${p.keys[p.keys.length - 1].label.slice(-2)} · làm mới 00:00`, state: p.auth_state === 'ok' ? 'ok' : 'warn', state_label: p.auth_state === 'ok' ? 'Hoạt động' : 'Sắp cạn' });
      } else {
        const k = p.keys[0];
        const low = (k?.quota_left_pct ?? 100) < 25;
        out.push({ icon: 'ph ph-key', name: p.name, meta: k ? `${k.label}${k.quota_left_pct != null ? ` · còn ${k.quota_left_pct}% hạn mức` : ''}` : 'chưa có khoá', state: p.auth_state === 'ok' && !low ? 'ok' : p.auth_state === 'error' || p.auth_state === 'expired' ? 'bad' : 'warn', state_label: p.auth_state === 'ok' && !low ? 'Hoạt động' : p.auth_state === 'unconfigured' ? 'Chưa kiểm tra' : 'Sắp cạn' });
      }
    }
    // v0.1.66 (F134): máy chủ không còn trả dòng "khoá phiên QR" của Zalo / WhatsApp (kênh có thẻ riêng ở Kết nối).
    return out;
  };

  // ── derived views ──
  const pipeline = (): Pipeline => ({
    channels_live: fresh ? channels.filter((c) => c.state === 'active').length : 4,
    groups_listening: listening(),
    raw_total: rawTotal,
    raw_pending: rawPending,
    interval_seconds: schedule.interval_seconds,
    count_threshold: schedule.count_threshold,
    clean_total: cleanTotal,
    raw_error: 0,
  });
  const scheduleView = () => {
    if (schedule.next_run_at < Date.now()) schedule.next_run_at = Date.now() + schedule.interval_seconds * 1000;
    return {
      interval_seconds: schedule.interval_seconds, count_threshold: schedule.count_threshold, batch_size: schedule.batch_size,
      min_confidence: schedule.min_confidence, pending: rawPending, next_run_at: iso(schedule.next_run_at),
      next_trigger: rawPending >= schedule.count_threshold ? ('count' as const) : ('interval' as const),
    };
  };

  // ── realtime simulation ──
  let simSeq = 0;

  // ── Nhật ký lắng nghe (v0.1.68): dòng thời gian gộp tin Kho thô + sự kiện kênh, dựng từ chính `raw` ở trên ──
  const MODE_LABEL: Record<string, string> = {
    pending: 'Chờ Sếp chọn', off: 'Không nghe', tagged_only: 'Nghe hết, chỉ trả lời khi được tag', silent: 'Lắng nghe im lặng',
    proactive: 'Chủ động bắt tín hiệu', paused: 'Tạm dừng',
  };
  const EVENT_ICON: Record<string, [string, string]> = {
    group_new: ['ph ph-users-three', 'accent'], group_left: ['ph ph-sign-out', 'warn'], group_rejoined: ['ph ph-sign-in', 'ok'],
    group_renamed: ['ph ph-pencil-simple', 'neutral'], members_changed: ['ph ph-user-plus', 'neutral'],
    channel_connected: ['ph ph-plugs-connected', 'ok'], channel_error: ['ph ph-wifi-slash', 'warn'], channel_ended: ['ph ph-plugs', 'bad'],
    direct_new: ['ph ph-user-circle-plus', 'accent'], groups_synced: ['ph ph-address-book', 'neutral'],
  };
  const logEvents: ListenLogEvent[] = [];
  const mkEvent = (at: number, type: string, text: string, o: { ch?: 'zalo' | 'whatsapp'; gid?: string; direct?: boolean; mode?: ListenMode } = {}): ListenLogEvent => {
    const [icon, tone] = EVENT_ICON[type] ?? ['ph ph-info', 'neutral'];
    const g = o.gid ? groupRef(o.gid) : null;
    return {
      kind: 'event', id: randomUUID(), at: iso(at), event_type: type, icon, tone, text,
      channel: o.ch ? { type: o.ch, name: o.ch === 'zalo' ? 'Zalo' : 'WhatsApp' } : null, group: g, person: null, direct: !!o.direct,
      listen_mode: o.mode ?? null, listen_mode_label: o.mode ? MODE_LABEL[o.mode] : null, raw_event_id: null,
    };
  };
  if (!fresh) {
    const t0 = anchorBase();
    logEvents.push(
      mkEvent(t0 - 3 * 60_000, 'group_new', 'Tài khoản Zalo của Sếp vừa được thêm vào nhóm «Đại lý Tây Nguyên». Nhóm tự vào chế độ: Lắng nghe im lặng.', { ch: 'zalo', mode: 'silent' }),
      mkEvent(t0 - 41 * 60_000, 'members_changed', 'Nhóm «Vận hành Genesis — Quý 4»: 2 thành viên mới (Nguyễn Văn Bảo, Lê Minh Tâm), 1 thành viên rời (Phạm Thu Hà).', { ch: 'zalo', gid: 'GRP-ZL-0114' }),
      mkEvent(t0 - 95 * 60_000, 'channel_error', 'Kênh WhatsApp mất kết nối, đang tự nối lại.', { ch: 'whatsapp' }),
      mkEvent(t0 - 92 * 60_000, 'channel_connected', 'Kênh WhatsApp đã nối lại (WhatsApp Sếp).', { ch: 'whatsapp' }),
      mkEvent(t0 - 3 * 3600_000, 'group_renamed', 'Nhóm «Truyền thông» đổi tên thành «Truyền thông & Sự kiện».', { ch: 'zalo', gid: 'GRP-ZL-0502' }),
      mkEvent(t0 - 5 * 3600_000, 'group_left', 'Tài khoản WhatsApp của Sếp không còn trong nhóm «Hội cựu sinh viên» (đã rời hoặc bị mời ra).', { ch: 'whatsapp', gid: 'GRP-WA-0027' }),
      mkEvent(t0 - 26 * 3600_000, 'channel_ended', 'Kênh Zalo đã đăng xuất.', { ch: 'zalo' }),
      mkEvent(t0 - 25 * 3600_000, 'channel_connected', 'Kênh Zalo đã đăng nhập (Zalo Sếp).', { ch: 'zalo' }),
    );
    // Người mới nhắn riêng: chỉ Owner thấy (quy tắc 1-1).
    logEvents.push({ ...mkEvent(t0 - 12 * 60_000, 'direct_new', 'Người mới nhắn riêng lần đầu trên Zalo: Trịnh Hoài Nam.', { ch: 'zalo', direct: true }), person: { id: 'p-nam', code: 'PER-1002', name: 'Trịnh Hoài Nam' } });
  }
  const dmRows = (): ListenLogMessage[] =>
    fresh
      ? []
      : [
          {
            kind: 'message', id: 'dm-1', at: iso(anchorBase() - 11 * 60_000), code: 'RAW-918900', channel: { type: 'zalo', name: 'Zalo' }, group: null, direct: true,
            sender: { id: 'p-nam', code: 'PER-1002', name: 'Trịnh Hoài Nam' }, direction: 'inbound', msg_kind: 'text',
            text: 'Chào anh, em muốn hỏi báo giá 3 container thép cuộn, số của em 0912345678.', tagged: false, listen_mode: 'silent', listen_mode_label: 'Tin riêng — lưu im lặng',
            listen_mode_known: true, screening: { state: 'clean', label: 'AskedPrice', confidence: 0.91, note: 'Đã vào kho sạch' }, decisions: [],
            note: 'Tin riêng ở chế độ lưu im lặng — không đưa cho agent trực',
          },
        ];
  const decisionsFor = (r: RawItem, idx: number): ListenLogDecision[] => {
    if (r.state !== 'clean' || !r.group) return [];
    const base = { at: iso(new Date(r.received_at).getTime() + 40_000), agent: { id: 'agent-mai', name: 'Trợ lý Mai' } };
    if (idx % 6 === 0)
      return [{ ...base, id: `dec-${r.id}`, decision: 'draft', outcome: 'held', label: 'Nháp trả lời: chờ duyệt', draft: { id: `draft-${idx}`, code: `ACT-${String(231 + idx).padStart(4, '0')}`, status: 'pending' } }];
    if (idx % 6 === 2) return [{ ...base, id: `dec-${r.id}`, decision: 'note', outcome: 'noted', label: 'Ghi chú vào sổ tay', draft: null }];
    if (idx % 6 === 4) return [{ ...base, id: `dec-${r.id}`, decision: 'silent', outcome: 'none', label: 'Im lặng', draft: null }];
    return [];
  };
  const logMessages = (owner: boolean): ListenLogMessage[] => {
    const rows: ListenLogMessage[] = [];
    raw.forEach((r, idx) => {
      const g = groups.find((x) => x.code === r.group?.code);
      if (!g || !isListening(g)) return;
      const decisions = decisionsFor(r, idx);
      const quiet =
        decisions.length === 0 && r.state === 'clean'
          ? g.listen_mode === 'silent'
            ? 'Nhóm ở chế độ Lắng nghe im lặng — agent chỉ trả lời khi được tag'
            : 'Chưa có agent nào quyết định cho tin này'
          : null;
      rows.push({
        kind: 'message', id: r.id, at: r.received_at, code: r.code, channel: r.channel, group: r.group, direct: false,
        sender: r.person ? { id: r.person.id, code: r.person.code, name: r.person.name } : null, direction: r.direction, msg_kind: r.kind,
        text: owner ? r.text : (maskText(r.text) ?? r.text), tagged: g.listen_mode === 'tagged_only' && idx % 4 === 0,
        listen_mode: g.listen_mode, listen_mode_label: MODE_LABEL[g.listen_mode] ?? null, listen_mode_known: idx % 9 !== 8,
        screening: {
          state: r.state, label: r.label, confidence: r.confidence,
          note: r.state === 'discarded' ? 'Đã loại khi sàng lọc: Trùng hẳn một tin đã có' : r.state === 'clean' ? 'Đã vào kho sạch' : 'Chờ lượt sàng lọc tới',
        },
        decisions, note: quiet,
      });
    });
    return owner ? [...rows, ...dmRows()] : rows;
  };
  const logAll = (ctx: P2Ctx): ListenLogItem[] => {
    const evs = logEvents.filter((e) => ctx.owner || !e.direct);
    return [...logMessages(ctx.owner), ...evs].sort((a, b) => b.at.localeCompare(a.at));
  };
  const pushEvent = () => {
    const g = groups.filter((x) => isListening(x))[simSeq % 3];
    if (!g) return null;
    const ev = mkEvent(Date.now(), 'members_changed', `Nhóm «${g.name}»: 1 thành viên mới (${PEOPLE[simSeq % PEOPLE.length][1]}).`, { ch: g.channel, gid: g.code });
    logEvents.unshift(ev);
    emit('listen.event', { id: ev.id, at: ev.at, event_type: ev.event_type, channel_type: g.channel, group_id: g.id, owner_only: false });
    return ev;
  };
  const pushRaw = () => {
    const tpl = RAW_TEMPLATES[simSeq % RAW_TEMPLATES.length];
    const listeningGroups = groups.filter((g) => isListening(g));
    if (!listeningGroups.length) return null;
    const g = listeningGroups[(simSeq * 7) % listeningGroups.length];
    simSeq++;
    const item = mkRaw({
      at: Date.now(), ch: g.channel, gid: g.code, pid: PEOPLE[(simSeq * 3) % PEOPLE.length][0],
      text: tpl.text, label: null, conf: null, state: 'pending', kind: tpl.kind,
    });
    raw.unshift(item);
    rawTotal++;
    rawPending++;
    g.raw24h++;
    emit('raw.new', item);
    if (simSeq % 4 === 0) pushEvent();
    later(2500, () => {
      item.state = 'processing';
      emit('raw.state', { id: item.id, state: 'processing', label: null, confidence: null });
      later(2500, () => {
        item.state = tpl.state;
        item.label = tpl.label;
        item.confidence = tpl.conf;
        rawPending = Math.max(0, rawPending - 1);
        if (tpl.state === 'clean') cleanTotal++;
        emit('raw.state', { id: item.id, state: tpl.state, label: tpl.label, confidence: tpl.conf });
      });
    });
    return item;
  };
  let simTimer: ReturnType<typeof setInterval> | null = null;
  const setSimulation = (on: boolean) => {
    if (simTimer) clearInterval(simTimer);
    simTimer = on ? setInterval(pushRaw, 7000) : null;
  };
  setSimulation(opts.simulate);

  const startRun = (trigger: RefineryRun['trigger'], total: number): RefineryRun => {
    const run: RefineryRun = {
      id: randomUUID(), trigger, started_at: iso(Date.now()), finished_at: null, input_count: total,
      clean_count: 0, lowconf_count: 0, noise_count: 0, error_count: 0, status: 'queued',
    };
    runs.unshift(run);
    emit('refinery.run', run);
    emit('refinery.progress', { run_id: run.id, processed: 0, total, clean: 0, lowconf: 0, noise: 0, errors: 0, status: 'queued' });
    let processed = 0;
    const step = () => {
      processed = Math.min(total, processed + Math.max(1, Math.ceil(total / 6)));
      run.status = processed >= total ? 'done' : 'running';
      run.clean_count = Math.round(processed * 0.62);
      run.lowconf_count = Math.round(processed * 0.06);
      run.noise_count = processed - run.clean_count - run.lowconf_count;
      emit('refinery.progress', {
        run_id: run.id, processed, total, clean: run.clean_count, lowconf: run.lowconf_count, noise: run.noise_count, errors: 0, status: run.status,
      });
      if (run.status === 'done') {
        run.finished_at = iso(Date.now());
        rawPending = Math.max(0, rawPending - total);
        cleanTotal += run.clean_count;
        if (manualRun === run.id) manualRun = null;
        // Rows still pending in the visible lake move on too.
        for (const r of raw.filter((x) => x.state === 'pending' || x.state === 'processing').slice(0, 12)) {
          r.state = 'clean';
          r.label = r.label ?? 'AskedPrice';
          r.confidence = r.confidence ?? 0.82;
          emit('raw.state', { id: r.id, state: r.state, label: r.label, confidence: r.confidence });
        }
        emit('refinery.run', run);
      } else later(450, step);
    };
    later(500, step);
    return run;
  };

  // Channel login / QR
  let bridgeOnline = true;
  const qrSessions = new Map<string, { type: string; refreshes: number }>();
  const issueQr = (c: Channel, sessionId: string) => {
    const expires = Date.now() + 60_000;
    c.state = 'pending_qr';
    c.qr = { session_id: sessionId, image: fakeQr(`${sessionId}-${Date.now()}`), expires_at: iso(expires), scanned: false };
    emit('channel.qr', { type: c.type, session_id: sessionId, image: c.qr.image, expires_at: c.qr.expires_at });
    later(60_000, () => {
      const s = qrSessions.get(sessionId);
      if (!s || c.qr?.session_id !== sessionId || c.qr.scanned) return;
      if (s.refreshes++ < 4) issueQr(c, sessionId);
    });
  };
  const scan = (type: string) => {
    const c = channelOf(type);
    if (!c || !c.qr) return false;
    c.qr.scanned = true;
    emit('channel.status', { type: c.type, state: 'pending_qr', account_label: c.account_label, scanned: true });
    later(1500, () => {
      c.state = 'active';
      c.qr = null;
      c.started_at = iso(Date.now());
      c.last_heartbeat_at = iso(Date.now());
      c.outbound_queued = 0;
      c.account_label = c.account_label ?? 'iPhone của Sếp';
      c.stats = c.stats ?? { msgs_24h: 0, tagged_24h: 0, latency_ms: 380, uptime_pct: 100 };
      syncGroups(c.type as 'zalo' | 'whatsapp', true);
      if (!fresh && c.type === 'whatsapp') groups.filter((g) => g.channel === 'whatsapp' && g.listen_mode === 'paused').forEach((g) => (g.listen_mode = 'silent'));
      emit('channel.status', { type: c.type, state: 'active', account_label: c.account_label, scanned: true });
    });
    return true;
  };

  // CLI login
  /** v0.1.39 — `mock-boss-checks` ghi kết quả "Đăng nhập" trong LUỒNG đăng nhập (như `_boss_check` của api thật). */
  let cliLoginListener: ((kind: string, ok: boolean, email: string | null) => void) | null = null;
  const cliEmit = (e: CliLoginEvent) => {
    const kind = e.kind ?? cliLogins.get(e.login_id)?.kind ?? 'antigravity_cli';
    // Như api thật: kết quả kiểm được ghi TRƯỚC khi phiên báo done/failed.
    if (e.status === 'done' || e.status === 'failed') cliLoginListener?.(kind, e.status === 'done', e.profile?.email ?? null);
    cliLogins.set(e.login_id, { ...e, kind });
    emit('cli.login', e);
  };

  // Setup first run
  const firstRun = { run: null as RefineryRun | null };
  const kickFirstRun = () => {
    // Bridge starts collecting, then the first refinery pass runs.
    for (let i = 0; i < 40; i++) {
      const tpl = RAW_TEMPLATES[i % RAW_TEMPLATES.length];
      const g = groups.find((x) => x.listen_mode !== 'off');
      if (!g) break;
      raw.unshift(mkRaw({ at: Date.now() - i * 20_000, ch: g.channel, gid: g.code, pid: PEOPLE[i % PEOPLE.length][0], text: tpl.text, label: null, conf: null, state: 'pending' }));
      rawTotal++;
      rawPending++;
    }
    later(1500, () => {
      firstRun.run = startRun('fast', Math.max(40, rawPending));
    });
  };

  // ── HTTP ──
  const forbidden = (ctx: P2Ctx) => ctx.problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
  const has = (ctx: P2Ctx, perm: string) => !!ctx.perms[perm] && ctx.perms[perm] !== 'none';

  function handle(ctx: P2Ctx): boolean {
    const { method: m, path: p, url, body, reply, problem } = ctx;
    const q = url.searchParams;
    const need = (perm: string) => {
      if (has(ctx, perm)) return true;
      forbidden(ctx);
      return false;
    };
    const pin = (operation: string) => {
      if (!ctx.needPin()) return true;
      problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN', { detail: { operation } });
      return false;
    };
    const seg = p.split('/').filter(Boolean);

    // pipeline
    if (p === '/data/pipeline' && m === 'GET') return need('data.read') ? reply(200, pipeline()) : true;

    // Nhật ký lắng nghe (v0.1.68)
    if (seg[0] === 'listen-log') {
      if (!need('data.read')) return true;
      if (seg[1] === 'stats' && m === 'GET') {
        const day = Date.now() - 86_400_000;
        const items = logAll(ctx).filter((i) => new Date(i.at).getTime() >= day);
        const by = (['zalo', 'whatsapp'] as const).map((t) => ({
          type: t, name: t === 'zalo' ? 'Zalo' : 'WhatsApp',
          messages_24h: items.filter((i) => i.kind === 'message' && i.channel.type === t).length,
          events_24h: items.filter((i) => i.kind === 'event' && i.channel?.type === t).length,
          groups_listening: groups.filter((g) => g.channel === t && isListening(g)).length,
        }));
        reply(200, {
          by_channel: by, messages_24h: by.reduce((n, c) => n + c.messages_24h, 0), events_24h: by.reduce((n, c) => n + c.events_24h, 0),
          groups_listening: groups.filter((g) => isListening(g)).length,
          groups: groups.filter((g) => isListening(g)).map((g) => ({ id: g.id, code: g.code, name: g.name, channel: g.channel, listen_mode: g.listen_mode })),
        });
        return true;
      }
      if (seg.length === 1 && m === 'GET') {
        const ch = q.get('channel');
        const gid = q.get('group_id');
        const kind = q.get('kind') ?? 'all';
        const search = (q.get('q') ?? '').toLowerCase();
        const since = q.get('since') ?? '7d';
        const sinceMs = since === '24h' ? 86_400_000 : since === '7d' ? 7 * 86_400_000 : since === '30d' ? 30 * 86_400_000 : Infinity;
        const tagged = q.get('tagged') === 'true';
        const acted = q.get('acted') === 'true';
        const items = logAll(ctx).filter((i) => {
          if (kind !== 'all' && i.kind !== kind) return false;
          if ((tagged || acted) && i.kind === 'event') return false;
          if (ch && (i.kind === 'message' ? i.channel.type : i.channel?.type) !== ch) return false;
          if (gid && i.group?.id !== gid) return false;
          if (Date.now() - new Date(i.at).getTime() > sinceMs + 86_400_000) return false;
          if (i.kind === 'message' && tagged && !i.tagged) return false;
          if (i.kind === 'message' && acted && !i.decisions.some((d) => d.decision !== 'silent')) return false;
          if (search) {
            const hay = i.kind === 'message' ? `${i.text ?? ''} ${i.sender?.name ?? ''} ${i.group?.name ?? ''}` : `${i.text} ${i.group?.name ?? ''}`;
            if (!hay.toLowerCase().includes(search)) return false;
          }
          return true;
        });
        const limit = Math.min(100, Number(q.get('limit') ?? 50));
        const off = Number(q.get('cursor') ?? 0);
        reply(200, { items: items.slice(off, off + limit), next_cursor: off + limit < items.length ? String(off + limit) : null, decisions_visible: has(ctx, 'system.read') });
        return true;
      }
    }

    // raw
    if (seg[0] === 'raw') {
      if (!need('data.read')) return true;
      const filtered = () => {
        const ch = q.get('channel');
        const gid = q.get('group_id');
        const st = q.get('state');
        const lb = q.get('label');
        const mc = q.get('min_confidence');
        const since = q.get('since') ?? '24h';
        const sinceMs = since === '24h' ? 86_400_000 : since === '7d' ? 7 * 86_400_000 : since === '30d' ? 30 * 86_400_000 : Infinity;
        const items = raw.filter(
          (r) =>
            (!ch || r.channel.type === ch) &&
            (!gid || r.group?.id === gid) &&
            (!st || r.state === st) &&
            (!lb || r.label === lb) &&
            (!mc || (r.confidence ?? 0) >= Number(mc)) &&
            Date.now() - new Date(r.received_at).getTime() <= sinceMs + 86_400_000,
        );
        const unfiltered = !ch && !gid && !st && !lb && !mc;
        return { items, total: unfiltered ? rawTotal : items.length };
      };
      if (seg.length === 1 && m === 'GET') {
        const { items, total } = filtered();
        const limit = Math.min(200, Number(q.get('limit') ?? 50));
        const off = Number(q.get('cursor') ?? 0);
        const page = items.slice(off, off + limit).map((r) => (ctx.owner ? r : { ...r, text: maskText(r.text) ?? '' }));
        reply(200, { items: page, next_cursor: off + limit < items.length ? String(off + limit) : null, total });
        return true;
      }
      if (seg[1] === 'by-group' && m === 'GET') {
        const limit = Number(q.get('limit') ?? 7);
        const sorted = [...groups].filter((g) => g.raw24h > 0).sort((a, b) => b.raw24h - a.raw24h);
        const top = sorted.slice(0, Math.max(0, limit - 1));
        const rest = sorted.slice(top.length).reduce((n, g) => n + g.raw24h, 0);
        const rows: Array<{ group: Ref | null; n: number }> = top.map((g) => ({ group: { id: g.id, code: g.code, name: g.name }, n: g.raw24h }));
        if (rest > 0) rows.push({ group: null, n: fresh ? rest : 1456 });
        reply(200, rows);
        return true;
      }
      if (seg[1] === 'export' && m === 'GET') {
        if (!need('data.manage') || !pin('data.export')) return true;
        const { items } = filtered();
        const lines = ['code,received_at,channel,group,person,label,confidence,state,text'];
        for (const r of items) lines.push([r.code, r.received_at, r.channel.type, r.group?.code ?? '', r.person?.code ?? '', r.label ?? '', r.confidence ?? '', r.state, `"${r.text.replace(/"/g, '""')}"`].join(','));
        ctx.text(200, 'text/csv; charset=utf-8', `\uFEFF${lines.join('\n')}`, 'raw-export.csv');
        return true;
      }
      if (seg.length === 2 && m === 'GET') {
        const r = raw.find((x) => x.id === seg[1]);
        if (!r) return problem(404, 'NOT_FOUND', 'Không tồn tại');
        reply(200, { ...r, payload: { text: r.text }, meaning_units: [] });
        return true;
      }
    }

    // refinery
    if (seg[0] === 'refinery') {
      if (!need('data.read')) return true;
      if (seg[1] === 'schedule' && m === 'GET') return reply(200, scheduleView());
      if (seg[1] === 'schedule' && m === 'PUT') {
        if (!need('data.manage')) return true;
        const v = { interval_seconds: Number(body.interval_seconds), count_threshold: Number(body.count_threshold), batch_size: Number(body.batch_size), min_confidence: Number(body.min_confidence) };
        const errors: Record<string, string> = {};
        if (!(v.interval_seconds >= 60 && v.interval_seconds <= 86400)) errors.interval_seconds = 'Chu kỳ từ 60 đến 86400 giây';
        if (!(v.count_threshold >= 1 && v.count_threshold <= 100000)) errors.count_threshold = 'Ngưỡng từ 1 đến 100000';
        if (!(v.batch_size >= 1 && v.batch_size <= 2000)) errors.batch_size = 'Mỗi lượt từ 1 đến 2000';
        if (!(v.min_confidence >= 0 && v.min_confidence <= 1)) errors.min_confidence = 'Từ 0 đến 1';
        if (Object.keys(errors).length) return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors });
        Object.assign(schedule, v);
        schedule.next_run_at = Date.now() + v.interval_seconds * 1000;
        reply(200, scheduleView());
        return true;
      }
      if (seg[1] === 'run' && m === 'POST') {
        if (!need('data.manage')) return true;
        if (manualRun) return problem(409, 'REFINERY_BUSY', 'Đang có một lượt chạy thủ công');
        const run = startRun('manual', Math.max(1, Math.min(schedule.batch_size, rawPending || schedule.batch_size)));
        manualRun = run.id;
        reply(202, { run_id: run.id });
        return true;
      }
      if (seg[1] === 'runs' && m === 'GET') return reply(200, runs.slice(0, Number(q.get('limit') ?? 5)));
    }

    // rules
    if (seg[0] === 'rules') {
      if (!need('data.read')) return true;
      if (seg.length === 1 && m === 'GET') return reply(200, rules);
      if (seg[1] === 'weights') {
        if (m === 'GET') return reply(200, weights);
        if (m === 'PUT') {
          if (!need('data.manage')) return true;
          const arr = (Array.isArray(body) ? body : []) as Array<{ dimension: string; value: number }>;
          const sum = arr.reduce((n, w) => n + Number(w.value), 0);
          if (sum !== 100 || arr.some((w) => !Number.isInteger(w.value))) return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { _: 'Tổng trọng số phải bằng 100%' } });
          weights = weights.map((w) => ({ ...w, value: arr.find((x) => x.dimension === w.dimension)?.value ?? w.value }));
          reply(200, weights);
          return true;
        }
      }
      if (seg[1] === 'test' && m === 'POST') {
        const r = typeof body.raw_event_id === 'string' ? raw.find((x) => x.id === body.raw_event_id) : null;
        const text = r ? r.text : String(body.text ?? '');
        reply(200, testRules(text, r ?? null));
        return true;
      }
      if (seg[1] === 'test-batch' && m === 'POST') {
        reply(200, { n: Number(body.n ?? 100), clean: 61, lowconf: 9, discarded: 30, by_rule: [
          { code: 'R-01', hits: 22 }, { code: 'R-02', hits: 9 }, { code: 'R-03', hits: 4 }, { code: 'R-04', hits: 3 }, { code: 'R-05', hits: 1 }, { code: 'R-06', hits: 30 },
        ] });
        return true;
      }
      if (seg.length === 1 && m === 'POST') {
        if (!need('data.manage')) return true;
        const code = `R-${String(rules.length + 1).padStart(2, '0')}`;
        const b = body as unknown as Rule;
        addRule({ code, name: String(b.name), kind: String(b.kind), threshold: Number(b.threshold), enabled: true, conditions: b.conditions ?? [], outputs: b.outputs ?? [], prompt_hint: b.prompt_hint ?? null });
        reply(201, rules[rules.length - 1]);
        return true;
      }
      const rule = rules.find((x) => x.id === seg[1]);
      if (seg.length >= 2 && !rule) return problem(404, 'NOT_FOUND', 'Không tồn tại');
      if (rule && seg[2] === 'versions' && m === 'GET') return reply(200, [...(versions.get(rule.id) ?? [])].reverse());
      if (rule && seg.length === 2 && m === 'PUT') {
        if (!need('data.manage')) return true;
        const b = body as unknown as Rule;
        Object.assign(rule, {
          name: String(b.name), kind: String(b.kind), kind_label: KIND_LABEL[String(b.kind)] ?? String(b.kind), threshold: Number(b.threshold),
          conditions: b.conditions as RuleCondition[], outputs: b.outputs as RuleOutput[], prompt_hint: b.prompt_hint ?? null,
          version: rule.version + 1, updated_at: iso(Date.now()),
        });
        versions.get(rule.id)?.push({ version: rule.version, conditions: rule.conditions, outputs: rule.outputs, threshold: rule.threshold, created_at: rule.updated_at, created_by: { label: ctx.userLabel } });
        reply(200, rule);
        return true;
      }
      if (rule && seg.length === 2 && m === 'PATCH') {
        if (!need('data.manage')) return true;
        rule.enabled = Boolean(body.enabled);
        reply(200, rule);
        return true;
      }
    }

    // clean
    if (seg[0] === 'clean') {
      if (!need('data.read')) return true;
      if (seg.length === 1 && m === 'GET') {
        const gid = q.get('group_id');
        const pid = q.get('person_id');
        const since = q.get('since');
        const items = clean.filter((c) => (!gid || c.group?.id === gid) && (!pid || c.person?.id === pid) && (since !== '24h' || Date.now() - new Date(c.cycle_at).getTime() < 2 * 86_400_000));
        const limit = Number(q.get('limit') ?? 50);
        const off = Number(q.get('cursor') ?? 0);
        reply(200, { items: items.slice(off, off + limit), next_cursor: off + limit < items.length ? String(off + limit) : null, total: !gid && !pid && !since ? cleanTotal : items.length });
        return true;
      }
      if (seg[1] === 'agent-params' && m === 'GET') {
        const g = groups.find((x) => x.id === q.get('group_id'));
        const pr = [...people.values()].find((x) => x.id === q.get('person_id'));
        const nb = pr ? notebookFor('person', pr.id) : null;
        const n = pr ? clean.filter((c) => c.person?.id === pr.id).length : 0;
        const params: AgentParam[] = [
          { key: 'group', label: 'ID nhóm đang trực', value: g?.code ?? '—', icon: 'ph ph-users-three' },
          { key: 'person', label: 'ID người đang nói', value: pr?.code ?? '—', icon: 'ph ph-user' },
          { key: 'clean_read', label: 'Bản ghi sạch được đọc', value: `${pr?.code === 'PER-0042' ? 214 : n * 12 + 8} sự kiện`, icon: 'ph ph-check-circle' },
          { key: 'window', label: 'Cửa sổ thời gian', value: '180 ngày', icon: 'ph ph-calendar-blank' },
          { key: 'memory', label: 'Trí nhớ tạm', value: `${(nb?.token_used ?? 0).toLocaleString('vi-VN')} token`, icon: 'ph ph-brain' },
          { key: 'threads', label: 'Lịch sử nội dung tương quan', value: `${pr?.code === 'PER-0042' ? 9 : Math.max(1, n)} luồng liên quan`, icon: 'ph ph-flow-arrow' },
        ];
        reply(200, params);
        return true;
      }
      const item = clean.find((c) => c.id === seg[1]);
      if (item && seg[2] === 'evidence' && m === 'GET') {
        let ev = raw.filter((r) => item.raw_event_ids.includes(r.id));
        if (!ev.length) ev = raw.filter((r) => r.person?.id === item.person?.id).slice(0, 2);
        reply(200, ev.map((r) => ({ raw: r, quote: r.text.length > 70 ? `${r.text.slice(0, 68)}…` : r.text })));
        return true;
      }
    }

    // notebooks
    if (seg[0] === 'notebooks') {
      const type = seg[1] as 'person' | 'group';
      if (!need('profile.read')) return true;
      const nb = notebookFor(type, seg[2]);
      if (!nb) return problem(404, 'NOT_FOUND', 'Chưa có sổ tay');
      if (seg.length === 3 && m === 'GET') return reply(200, nb);
      if (seg[3] === 'compactions' && m === 'GET') return reply(200, compactions.get(`${type}:${seg[2]}`) ?? []);
      if (seg[3] === 'entries' && seg.length === 4 && m === 'POST') {
        if (!need('profile.write')) return true;
        const sec = nb.sections.find((s) => s.key === body.section);
        if (!sec || !String(body.body ?? '').trim()) return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { body: 'Nhập nội dung' } });
        const e = { id: randomUUID(), body: String(body.body), refs: [], author: { type: 'user' as const, label: ctx.userLabel }, pinned: Boolean(body.pinned), created_at: iso(Date.now()) };
        sec.entries.push(e);
        sec.updated_at = e.created_at;
        nb.token_used += Math.ceil(e.body.length / 3);
        reply(201, e);
        return true;
      }
    }

    // identity
    if (seg[0] === 'identity') {
      if (!need('data.read')) return true;
      if (seg[1] === 'stats' && m === 'GET') return reply(200, idStats);
      if (seg[1] === 'history' && seg.length === 2 && m === 'GET') return reply(200, history);
      if (seg[1] === 'history' && seg[3] === 'revert' && m === 'POST') {
        if (!need('data.manage') || !pin('identity.merge')) return true;
        const h = history.find((x) => x.id === seg[2]);
        if (!h) return problem(404, 'NOT_FOUND', 'Không tồn tại');
        if (h.reverted) return problem(409, 'CONFLICT', 'Đã hoàn tác');
        h.reverted = true;
        if (h.op === 'merge') idStats.merged_people = Math.max(0, idStats.merged_people - 1);
        reply(204);
        return true;
      }
      if (seg[1] === 'split' && m === 'POST') {
        if (!need('data.manage') || !pin('identity.merge')) return true;
        const ids = Array.isArray(body.identity_ids) ? (body.identity_ids as string[]) : [];
        if (!ids.length) return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { identity_ids: 'Chọn ít nhất một tài khoản' } });
        const np = person(`PER-${1100 + history.length}`);
        const src = history.find((h) => h.to.id === body.person_id);
        if (src?.to.identities) src.to.identities = src.to.identities.filter((i) => !ids.includes(i.identity_id));
        history.unshift({ id: randomUUID(), op: 'split', at: iso(Date.now()), actor: { label: ctx.userLabel }, from: src?.to ? { id: src.to.id, code: src.to.code, name: src.to.name } : {}, to: { id: np.id, code: np.code, name: np.name }, identities: ids.length, reverted: false });
        idStats.manual_splits++;
        reply(200, np);
        return true;
      }
      if (seg[1] === 'candidates' && seg.length === 2 && m === 'GET') return reply(200, candidates);
      const c = candidates.find((x) => x.id === seg[2]);
      if (seg[1] === 'candidates' && !c) return problem(404, 'NOT_FOUND', 'Không tồn tại');
      if (c && seg[3] === 'evidence' && m === 'GET') {
        const ev = raw.slice(0, 2).map((r, i) => ({ raw: { ...r, person: i === 0 ? c.a.person : c.b.person, channel: { type: i === 0 ? c.a.channel : c.b.channel, name: i === 0 ? 'Zalo' : c.b.channel === 'linkedin' ? 'LinkedIn' : 'WhatsApp' } }, note: i === 0 ? `Cùng ${c.basis}` : 'Nhắc cùng tên công ty trong chữ ký' }));
        reply(200, ev);
        return true;
      }
      if (c && seg[3] === 'merge' && m === 'POST') {
        if (!need('data.manage') || !pin('identity.merge')) return true;
        candidates.splice(candidates.indexOf(c), 1);
        idStats.pending_pairs = Math.max(0, idStats.pending_pairs - 1);
        idStats.merged_people++;
        const log = { id: randomUUID(), op: 'merge' as const, at: iso(Date.now()), actor: { label: ctx.userLabel }, from: { ...c.b.person }, to: { ...c.a.person, identities: [{ identity_id: c.a.identity_id, channel: c.a.channel, meta: c.a.meta }, { identity_id: c.b.identity_id, channel: c.b.channel, meta: c.b.meta }] }, identities: 2, reverted: false };
        history.unshift(log);
        reply(200, { person: c.a.person, log_id: log.id });
        return true;
      }
      if (c && seg[3] === 'reject' && m === 'POST') {
        if (!need('data.manage')) return true;
        candidates.splice(candidates.indexOf(c), 1);
        idStats.pending_pairs = Math.max(0, idStats.pending_pairs - 1);
        reply(204);
        return true;
      }
    }

    // channels & groups
    if (seg[0] === 'channels') {
      if (!need('system.read')) return true;
      if (seg.length === 1 && m === 'GET') return reply(200, channels.map(channelView));
      const c = channelOf(seg[1]);
      if (!c) return problem(404, 'NOT_FOUND', 'Không tồn tại');
      if (seg[2] === 'groups' && seg.length === 3 && m === 'GET') {
        const onlyPending = url.searchParams.get('pending') === '1';
        reply(200, groups.filter((g) => g.channel === c.type && (!onlyPending || g.listen_mode === 'pending')).map(({ channel: _c, raw24h: _r, ...g }) => g));
        return true;
      }
      // v0.1.65: "Cập nhật danh sách nhóm" — bridge quét lại; mock chỉ trả 202 (nhóm không đổi).
      if (seg[2] === 'groups' && seg[3] === 'refresh' && m === 'POST') {
        if (!need('system.manage')) return true;
        if (!bridgeOnline) return problem(503, 'BRIDGE_OFFLINE', 'Bộ nhận tin Zalo/WhatsApp đang tắt — thử lại sau ít phút; nếu vẫn lỗi, gửi Gói chẩn đoán cho người hỗ trợ');
        if (c.state !== 'active') return problem(409, 'CHANNEL_NOT_ACTIVE', 'Kênh chưa đăng nhập — đăng nhập xong mới cập nhật được danh sách nhóm');
        return reply(202, { session_id: randomUUID(), requested: true });
      }
      // QD 17/10: «Đã biết» — đặt lại bộ đếm «N nhóm mới đã vào Lắng nghe im lặng».
      if (seg[2] === 'groups' && seg[3] === 'seen' && m === 'POST') {
        if (!need('system.manage')) return true;
        newSilent[c.type] = 0;
        return reply(200, channelView(c));
      }
      if (seg[2] === 'groups' && seg[4] === 'mode' && m === 'PATCH') {
        if (!need('system.manage')) return true;
        const g = groups.find((x) => x.id === seg[3] && x.channel === c.type);
        if (!g) return problem(404, 'NOT_FOUND', 'Không tìm thấy Nhóm');
        // F1d: «Áp dụng cho các nhóm mới sau này» — chỉ silent/off, cần PIN (không đổi gì khi lỗi), lưu new_group_mode của kênh.
        if (body.apply_to_new === true) {
          if (body.mode !== 'silent' && body.mode !== 'off') return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { apply_to_new: 'Chỉ áp dụng cho nhóm mới được với Im lặng hoặc Không nghe' } });
          if (!pin('policy.change')) return true;
          c.new_group_mode = body.mode as Channel['new_group_mode'];
        }
        c.new_group_asked = true;
        g.listen_mode = body.mode as ListenMode;
        const { channel: _c, raw24h: _r, ...view } = g;
        return reply(200, view);
      }
      if (seg[2] === 'login' && m === 'POST') {
        if (!need('system.manage') || !pin('channel.login')) return true;
        if (body.accept_risk !== true) return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { accept_risk: 'Phải xác nhận rủi ro tài khoản cá nhân' } });
        if (c.type !== 'zalo' && c.type !== 'whatsapp') return problem(404, 'NOT_FOUND', 'Không tìm thấy Kênh');
        if (!bridgeOnline) return problem(503, 'BRIDGE_OFFLINE', 'Bộ nhận tin Zalo/WhatsApp đang tắt — thử lại sau ít phút; nếu vẫn lỗi, gửi Gói chẩn đoán cho người hỗ trợ');
        if (typeof body.account_label === 'string' && body.account_label.trim()) c.account_label = body.account_label.trim();
        c.new_group_asked = false; // F1d: nối kênh = lần hỏi đầu lại bật sẵn «Áp dụng cho các nhóm mới sau này»
        const sessionId = randomUUID();
        qrSessions.set(sessionId, { type: c.type, refreshes: 0 });
        reply(202, { session_id: sessionId });
        later(700, () => issueQr(c, sessionId));
        if (opts.simulate) later(9000, () => c.qr?.session_id === sessionId && scan(c.type));
        return true;
      }
      if (seg.length === 2 && m === 'PATCH') {
        if (!need('system.manage')) return true;
        if (c.type !== 'zalo' && c.type !== 'whatsapp') return problem(404, 'NOT_FOUND', 'Không tìm thấy Kênh');
        if (!pin('policy.change')) return true;
        const hasNew = ['ask', 'silent', 'off'].includes(String(body.new_group_mode));
        if (typeof body.listen_direct !== 'boolean' && !['off', 'silent', 'reply'].includes(String(body.direct_mode)) && !hasNew) {
          return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { direct_mode: 'Bắt buộc' } });
        }
        if (hasNew) {
          c.new_group_mode = body.new_group_mode as Channel['new_group_mode'];
          c.new_group_asked = true;
        }
        if (['off', 'silent', 'reply'].includes(String(body.direct_mode))) c.direct_mode = body.direct_mode as Channel['direct_mode'];
        else if (typeof body.listen_direct === 'boolean') c.direct_mode = body.listen_direct ? 'reply' : 'off';
        c.listen_direct = c.direct_mode !== 'off';
        return reply(200, channelView(c));
      }
      if (seg[2] === 'logout' && m === 'POST') {
        if (!need('system.manage') || !pin('channel.logout')) return true;
        c.state = 'logged_out';
        c.qr = null;
        emit('channel.status', { type: c.type, state: 'logged_out', account_label: c.account_label, scanned: false });
        reply(204);
        return true;
      }
    }
    if (seg[0] === 'groups' && seg.length === 2 && m === 'PATCH') {
      if (!need('system.manage')) return true;
      const g = groups.find((x) => x.id === seg[1]);
      if (!g) return problem(404, 'NOT_FOUND', 'Không tồn tại');
      if (body.listen_mode) g.listen_mode = body.listen_mode as ListenMode;
      if (body.view_scope) g.view_scope = body.view_scope as ViewScope;
      if (body.kind) g.kind = String(body.kind);
      const { channel: _c, raw24h: _r, ...view } = g;
      reply(200, view);
      return true;
    }

    // providers
    if (seg[0] === 'providers') {
      // PLAN 4.2 (`gh.system_api.routes.patch_chain`): kéo-thả sắp lại toàn bộ chuỗi một lượt — xử lý ở
      // `mock-p4-api.ts` (phase3) vì màn đó cần trả lại đúng hình `Provider[]` sau khi đổi `failover_rank`
      // trên CHÍNH mảng `providers` dùng chung này (`hooks.providers()` bên dưới), không phải một bản sao.
      if (seg[1] === 'chain') return false;
      // v0.1.41 (F-86): `/providers/background` ở `mock-p4-api.ts` — phải nhường TRƯỚC mẫu `/providers/{id}` bên dưới.
      if (seg[1] === 'background' && seg.length === 2) return false;
      if (!need('system.read')) return true;
      if (seg[1] === 'credentials' && m === 'GET') return reply(200, credentials());
      if (seg.length === 1 && m === 'GET') return reply(200, [...providers].sort((a, b) => a.failover_rank - b.failover_rank));
      if (seg.length === 1 && m === 'POST') {
        if (!need('system.manage')) return true;
        // v0.1.35 (F-20): như API thật — PIN `ai.route_change` kiểm SAU quyền (403 trước 423), detail null.
        if (ctx.needPin()) return problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN');
        const keys = Array.isArray(body.keys) ? (body.keys as string[]) : [];
        if (!keys.length || keys.some((k) => String(k).length < 8)) return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { keys: 'Khoá API không hợp lệ' } });
        const kind = String(body.kind) as Provider['kind'];
        const prefix = kind === 'gemini' ? 'GEM' : kind === 'deepseek' ? 'DS' : kind === 'system_one' ? 'JEV' : 'KEY';
        // v0.1.21 Jev (system_one): như gh/system_api/routes.py — địa chỉ + model mặc định OpenRouter.
        const endpoint = body.endpoint ? String(body.endpoint) : kind === 'system_one' ? 'https://openrouter.ai/api/v1' : null;
        const pv: Provider = {
          id: randomUUID(), kind, name: String(body.name ?? kind), endpoint,
          failover_rank: providers.length + 1, enabled: true, auth_state: 'unconfigured',
          keys: keys.map((k, i) => ({ id: randomUUID(), label: `${prefix}-KEY-0${i + 1}`, last4: String(k).slice(-4), enabled: true, cooldown_until: null, quota_left_pct: null })),
          models: kind === 'system_one' && Array.isArray(body.models) ? (body.models as string[]).map((n) => ({ id: randomUUID(), model_name: String(n), daily_quota: null, used_today: 0 })) : [],
        };
        if (kind === 'system_one' && !pv.models.length) pv.models = [{ id: randomUUID(), model_name: 'typesafe/jev-1.13', daily_quota: null, used_today: 0 }];
        (pv as Provider & { _secret: string })._secret = keys[0];
        providers.push(pv);
        reply(201, stripSecret(pv));
        return true;
      }
      if (seg.length === 2 && m === 'PATCH') {
        // v0.1.35 (F-20): bật/tắt, đổi thứ tự — PIN `ai.route_change` (sau quyền, trước 404 như phụ thuộc FastAPI).
        if (!need('system.manage')) return true;
        if (ctx.needPin()) return problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN');
      }
      const pv = providers.find((x) => x.id === seg[1]);
      if (!pv) return problem(404, 'NOT_FOUND', 'Không tồn tại');
      if (seg[2] === 'test' && m === 'POST') {
        const secret = (pv as Provider & { _secret?: string })._secret ?? '';
        const ok = !/bad|sai/i.test(secret);
        // Như máy chủ (catalog.build, v0.1.32): model đã lưu mà CLI không liệt kê vẫn có trong danh sách (nguồn "saved").
        const base = MODEL_GROUPS[pv.kind];
        const listed = new Set(base?.flatMap((g) => g.models.map((x) => x.id)) ?? []);
        const extra = base ? pv.models.filter((x) => !listed.has(x.model_name)).map((x) => opt(x.model_name, x.model_name, base[0].label, 'balanced', 'catalog', x.effort ? [x.effort] : [], { verified: false, source_ref: 'model đã lưu' })) : [];
        const groups = base ? base.map((g, i) => (i === 0 ? { ...g, models: [...g.models, ...extra.map((x) => ({ ...x, source: 'saved' as 'cli' }))] } : g)) : undefined;
        const models = groups ? groups.flatMap((g) => g.models.map((x) => x.id)) : pv.kind === 'system_one' ? [pv.models[0]?.model_name ?? 'typesafe/jev-1.13'] : pv.kind === 'gemini' ? ['gemini-2.5-flash', 'gemini-2.5-flash-lite'] : pv.kind === 'deepseek' ? ['deepseek-chat', 'deepseek-reasoner'] : ['gpt-4o-mini'];
        pv.auth_state = ok ? 'ok' : 'error';
        // Như máy chủ thật (v0.1.28): gọi thử KHÔNG tự thêm model — lưu `last_test`; bước 4 tự lấy model đầu tiên
        // khi Owner chưa bấm "Dùng model này". Jev (system_one) có model mặc định từ lúc tạo.
        const result = ok
          ? {
              ok: true, latency_ms: pv.kind === 'system_one' ? 164 : 812, models, error: null,
              ...(groups
                ? (() => {
                    const cur = pv.models.find((x) => x.is_default);
                    const first = groups[0].models[0];
                    return { model_groups: groups, models_source: first.source, probe_model: cur?.model_name ?? models[0], probe_effort: cur ? (cur.effort ?? null) : first.default_effort };
                  })()
                : {}),
              at: iso(Date.now()),
            }
          : { ok: false, latency_ms: null, models: [], error: /endpoint|127\.0\.0\.1|localhost/i.test(pv.endpoint ?? '') ? 'mạng: All connection attempts failed' : 'HTTP 401: API key not valid', at: iso(Date.now()) };
        pv.last_test = result;
        // Như api thật (v0.1.58, mrouter.adopt_probed_model): nguồn CLI gọi thử xanh mà chưa có dòng model ⇒ lưu cặp vừa chạy được.
        if (ok && (pv.kind === 'antigravity_cli' || pv.kind === 'claude_code_cli') && !pv.models.length) {
          const probe = (result as { probe_model?: string }).probe_model ?? models[0];
          if (probe) pv.models.push({ id: randomUUID(), model_name: probe, daily_quota: null, used_today: 0 });
          // v0.1.66 (F132): Antigravity lưu sẵn cả các model `agy models` liệt kê thật (source 'cli'); Claude Code chỉ model thăm dò.
          if (pv.kind === 'antigravity_cli')
            for (const g of groups ?? [])
              for (const x of g.models)
                if (x.source === 'cli' && !pv.models.some((y) => y.model_name === x.id)) pv.models.push({ id: randomUUID(), model_name: x.id, daily_quota: null, used_today: 0 });
        }
        reply(200, result);
        return true;
      }
      if (seg[2] === 'diagnose' && m === 'POST') {
        // v0.1.32: chỉ Owner (máy chủ: require_owner) — đầu ra thô đã che token/email.
        if (ctx.role !== 'owner') return problem(403, 'FORBIDDEN', 'Chỉ Owner');
        if (pv.kind !== 'antigravity_cli' && pv.kind !== 'claude_code_cli') return problem(409, 'NOT_CLI', 'Chỉ chẩn đoán được nguồn CLI');
        const cur = pv.models.find((x) => x.is_default) ?? pv.models[0];
        const agy = pv.kind === 'antigravity_cli';
        const model = cur?.model_name ?? (agy ? 'gemini-3.8-flash' : 'haiku');
        const eff = cur?.effort ?? null;
        const call = agy ? `agy -p Trả lời đúng một chữ: OK --model ${model}${eff ? ` --effort ${eff}` : ''} --output-format json` : `claude -p --model=${model}${eff ? ` --effort=${eff}` : ''} --output-format json`;
        return reply(200, {
          provider: pv.name, kind: pv.kind, model, effort: eff, at: iso(Date.now()),
          steps: [
            { label: 'Phiên bản', command: agy ? 'agy --version' : 'claude --version', exit_code: 0, stdout: agy ? '1.2.9\n' : '2.1.285 (Claude Code)\n', stderr: '', ms: 41, note: null },
            agy
              ? { label: 'Danh sách model', command: 'agy models', exit_code: 0, stdout: 'Fetching available models...\nAvailable models:\n  gemini-3.8-flash-high (current)\n  gemini-3.8-flash-low\n  gemini-3.1-pro-high\n', stderr: '', ms: 930, note: null }
              : { label: 'Đăng nhập (Claude Code không có lệnh liệt kê model)', command: 'claude auth status --json', exit_code: 0, stdout: '{"loggedIn": true, "email": "r***@gmail.com"}\n', stderr: '', ms: 210, note: null },
            // v0.1.32 (review): agy -p "/model", "/effort" — bản ghi tab-separated, không tốn lượt (changelog agy 1.1.11).
            ...(agy
              ? [
                  { label: 'Model của tài khoản (/model)', command: 'agy -p /model', exit_code: 0, stdout: 'gemini-3.8-flash\tlow,medium,high\tcurrent\ngemini-3.1-pro\tlow,high\t\n', stderr: '', ms: 640, note: null },
                  { label: 'Mức suy nghĩ (/effort)', command: `agy -p /effort --model ${model}`, exit_code: 0, stdout: 'low\t\nmedium\t\nhigh\tcurrent\n', stderr: '', ms: 610, note: null },
                ]
              : []),
            { label: 'Gọi thử 1 lượt', command: call, exit_code: 0, stdout: '{"response":"OK","usage":{"input_tokens":9,"output_tokens":1}}\n', stderr: '', ms: 3120, note: null },
          ],
        });
      }
      if (seg.length === 2 && m === 'DELETE') {
        if (!need('system.manage')) return true;
        if (pv.kind === 'antigravity_cli' || pv.kind === 'claude_code_cli') return problem(409, 'CLI_PROVIDER', `${pv.name} gỡ bằng cách xoá tài khoản ở thẻ tài khoản CLI`);
        providers.splice(providers.indexOf(pv), 1);
        return reply(204);
      }
      // v0.1.66 (F008): gỡ một model đã thêm nhầm — như máy chủ: 204, gỡ luôn dòng gán trỏ vào nó; không thuộc nguồn ⇒ 404.
      if (seg[2] === 'models' && seg.length === 4 && m === 'DELETE') {
        if (!need('system.manage')) return true;
        const mi = pv.models.findIndex((x) => x.id === seg[3]);
        if (mi < 0) return problem(404, 'NOT_FOUND', 'Không tồn tại');
        pv.models.splice(mi, 1);
        return reply(204);
      }
      if (seg[2] === 'models' && m === 'POST') {
        if (!need('system.manage')) return true;
        const name = String(body.model_name ?? '').trim();
        if (!name) return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { model_name: 'Bắt buộc' } });
        const exists = pv.models.find((x) => x.model_name === name);
        const effort = (typeof body.effort === 'string' ? body.effort : null) as Eff | null;
        // v0.1.31: nguồn CLI gọi thử model MỚI trước khi lưu — CLI không nhận thì 422, không lưu.
        // v0.1.32: gọi thử đúng --model <gốc> --effort <mức>; mức model không có → 422 kèm lỗi gốc ("Chi tiết kỹ thuật").
        const cliGroups = MODEL_GROUPS[pv.kind];
        const known = cliGroups?.flatMap((g) => g.models).find((x) => x.id === name);
        if (cliGroups && (!exists || (exists.effort ?? null) !== effort)) {
          if (/bad|sai/i.test(name) || !known)
            return problem(422, 'VALIDATION', 'Dữ liệu chưa hợp lệ', {
              errors: { model_name: `CLI không nhận model “${name}” — chọn model khác trong danh sách` },
              technical: `ModelRejected: Invalid model "${name}" (available: ${cliGroups.flatMap((g) => g.models.map((x) => x.id)).join(', ')})`,
            });
          if (effort && !(known.efforts as string[]).includes(effort))
            return problem(422, 'VALIDATION', 'Dữ liệu chưa hợp lệ', {
              errors: { model_name: `CLI không nhận mức suy nghĩ “${effort}” cho model “${name}” — chọn mức khác` },
              technical: `ModelRejected: invalid --effort "${effort}" (valid: ${known.efforts.join(', ')})`,
            });
        }
        const quota = (v: unknown) => (typeof v === 'number' ? v : null);
        if (!exists) pv.models.push({ id: randomUUID(), model_name: name, effort, daily_quota: quota(body.daily_quota), rate_limit_per_min: quota(body.rate_limit_per_min), used_today: 0 });
        else {
          if ('effort' in body) exists.effort = effort;
          // v0.1.66 (F009): như máy chủ — không gửi trường hạn mức thì giữ hạn mức đã lưu; gửi null thì bỏ.
          if ('daily_quota' in body) exists.daily_quota = quota(body.daily_quota);
          if ('rate_limit_per_min' in body) exists.rate_limit_per_min = quota(body.rate_limit_per_min);
        }
        if (body.make_default === true) pv.models.forEach((x) => (x.is_default = x.model_name === name));
        return reply(cliGroups ? 201 : 200, stripSecret(pv));
      }
      if (seg.length === 2 && m === 'PATCH') {
        if (!need('system.manage')) return true;
        if (typeof body.enabled === 'boolean') pv.enabled = body.enabled;
        if (typeof body.failover_rank === 'number') pv.failover_rank = body.failover_rank;
        reply(200, stripSecret(pv));
        return true;
      }
    }

    // CLI
    if (seg[0] === 'cli') {
      if (seg[1] === 'profiles' && seg.length === 2 && m === 'GET') return need('system.read') ? reply(200, profilesOf(q.get('kind'))) : true;
      if (seg[1] === 'profiles' && seg[3] === 'activate' && m === 'POST') {
        if (!need('system.manage') || !pin('cli.switch_account')) return true;
        const list = cliProfiles.some((x) => x.id === seg[2]) ? cliProfiles : claudeProfiles;
        const pr = list.find((x) => x.id === seg[2]);
        if (!pr) return problem(404, 'NOT_FOUND', 'Không tồn tại');
        list.forEach((x) => (x.active = x.id === pr.id));
        reply(200, pr);
        return true;
      }
      if (seg[1] === 'profiles' && seg.length === 3 && m === 'DELETE') {
        if (!need('system.manage') || !pin('cli.switch_account')) return true;
        const list = cliProfiles.some((x) => x.id === seg[2]) ? cliProfiles : claudeProfiles;
        const i = list.findIndex((x) => x.id === seg[2]);
        if (i < 0) return problem(404, 'NOT_FOUND', 'Không tồn tại');
        if (list[i].active) return problem(409, 'CONFLICT', 'Không xoá được hồ sơ đang dùng');
        list.splice(i, 1);
        reply(204);
        return true;
      }
      if (seg[1] === 'login' && seg.length === 2 && m === 'POST') {
        // v0.1.45 (F-20): như API — thêm tài khoản CLI cần phiên PIN `cli.switch_account` (403 trước 423).
        if (!need('system.manage') || !pin('cli.switch_account')) return true;
        const loginId = randomUUID();
        const kind = q.get('kind') === 'claude_code_cli' ? 'claude_code_cli' : 'antigravity_cli';
        cliLogins.set(loginId, { login_id: loginId, kind, status: 'starting' });
        reply(202, { login_id: loginId });
        later(150, () => cliEmit({ login_id: loginId, kind, status: 'starting' }));
        later(900, () =>
          cliEmit({
            login_id: loginId, kind, status: 'waiting_code',
            url: kind === 'claude_code_cli'
              ? `https://claude.com/cai/oauth/authorize?code=true&client_id=claude-code&response_type=code&state=${loginId.slice(0, 8)}`
              : `https://accounts.google.com/o/oauth2/v2/auth?client_id=antigravity-cli&response_type=code&state=${loginId.slice(0, 8)}`,
            message: 'Mở trang đăng nhập, rồi dán mã xác thực vào đây.',
          }),
        );
        return true;
      }
      const lg = cliLogins.get(seg[2]);
      if (seg[1] === 'login' && seg.length === 3 && m === 'GET') {
        if (!need('system.manage')) return true;
        return lg ? reply(200, lg) : problem(404, 'NOT_FOUND', 'Phiên đăng nhập không tồn tại hoặc nằm ngoài phạm vi của bạn');
      }
      if (seg[1] === 'login' && seg[3] === 'code' && m === 'POST') {
        if (!need('system.manage')) return true;
        if (!lg || lg.status !== 'waiting_code') return problem(409, 'CLI_LOGIN_NOT_WAITING', 'Phiên đăng nhập không còn chờ mã');
        const code = String(body.code ?? '').trim();
        cliEmit({ ...lg, status: 'verifying', url: null });
        reply(202, {});
        later(900, () => {
          if (code.length < 4 || /sai|bad/i.test(code)) {
            cliEmit({ login_id: lg.login_id, status: 'failed', message: 'Mã xác thực không đúng hoặc đã hết hạn.' });
            return;
          }
          if (lg.kind === 'claude_code_cli') {
            const cp: CliProfile = {
              id: randomUUID(), kind: 'claude_code_cli', email: claudeProfiles.length ? `claude.ops${claudeProfiles.length}@gmail.com` : 'c@example.test',
              plan_label: 'Claude Max', active: true, expires_at: iso(Date.now() - 60_000), refreshable: true, state: 'ok',
            };
            claudeProfiles.forEach((x) => (x.active = false));
            claudeProfiles.push(cp);
            if (!providers.some((x) => x.kind === 'claude_code_cli'))
              providers.push({ id: randomUUID(), kind: 'claude_code_cli', name: 'Claude Code CLI', endpoint: null, failover_rank: providers.length + 1, enabled: true, auth_state: 'ok', keys: [], models: [] });
            cliEmit({ login_id: lg.login_id, kind: 'claude_code_cli', status: 'done', profile: cp });
            return;
          }
          const profile: CliProfile = {
            id: randomUUID(), email: cliProfiles.length ? `genesis.ops${cliProfiles.length}@gmail.com` : 'a@example.test',
            plan_label: 'Google AI Pro · token 0 ₫', active: true, expires_at: iso(Date.now() + 24 * 3600_000), state: 'ok',
          };
          // Như api thật (gh.providers.cli._finish): tài khoản vừa đăng nhập thành tài khoản đang dùng; nguồn CLI mới tạo có 0 dòng model
          // và chưa có last_test — dòng model chỉ xuất hiện sau lượt gọi thử xanh (POST /providers/{id}/test → adopt_probed_model).
          cliProfiles.forEach((x) => (x.active = false));
          cliProfiles.push(profile);
          if (!providers.some((x) => x.kind === 'antigravity_cli')) {
            providers.unshift({ id: randomUUID(), kind: 'antigravity_cli', name: 'Antigravity Brain', endpoint: null, failover_rank: 0, enabled: true, auth_state: 'ok', keys: [], models: [] });
          }
          cliEmit({ login_id: lg.login_id, status: 'done', profile });
        });
        return true;
      }
      if (seg[1] === 'login' && seg[3] === 'cancel' && m === 'POST') {
        if (!need('system.manage')) return true;
        if (lg) cliLogins.delete(lg.login_id);
        reply(204);
        return true;
      }
    }

    return false;
  }

  function stripSecret(p: Provider): Provider {
    const { _secret: _s, ...rest } = p as Provider & { _secret?: string };
    return rest;
  }

  function testRules(text: string, r: RawItem | null) {
    const t = text.toLowerCase();
    const words = t.split(/\s+/).filter(Boolean).length;
    const input = { code: r?.code ?? null, text };
    if (words < 4 && !/\d/.test(t)) {
      return { input, output: [{ key: 'label', value: 'Noise' }, { key: 'confidence', value: '0,18 — dưới ngưỡng, không ghi vào kho sạch' }], matched_rules: ['R-06'], discarded_by: 'R-06', confidence: 0.18, would_write: 'discarded' };
    }
    const pick =
      /tồn|có sẵn|kho .*ai cần|cần bán/.test(t) ? { rule: 'R-02', intent: 'OfferedSupply · side = CUNG', conf: 0.91 }
      : /trả lời|chỗ khác|bên khác|chậm|trễ/.test(t) ? { rule: 'R-03', intent: 'Complained', conf: 0.94 }
      : /minh long|thấp hơn|so với/.test(t) ? { rule: 'R-04', intent: 'MentionsCompetitor', conf: 0.79 }
      : /năm|kinh nghiệm|đổi hướng/.test(t) ? { rule: 'R-05', intent: 'JobSignal · loại = Ứng viên', conf: 0.64 }
      : /giá|ngân sách|cần|cont|bao nhiêu/.test(t) ? { rule: 'R-01', intent: 'AskedPrice · side = CẦU', conf: 0.96 }
      : { rule: null, intent: '—', conf: 0.42 };
    const conf = pick.conf;
    const write = conf >= schedule.min_confidence ? 'clean' : 'lowconf';
    const mdf = /mdf/.test(t) && /bình dương/.test(t);
    const confStr = conf.toFixed(2).replace('.', ',');
    const thr = schedule.min_confidence.toFixed(2).replace('.', ',');
    return {
      input,
      output: [
        { key: 'intent', value: pick.intent },
        { key: 'person_id', value: r?.person ? `${r.person.code} · ${r.person.name}` : mdf ? 'PER-0311 · Trần Văn Hậu (mới tạo)' : '— · chưa gắn người' },
        { key: 'group_id', value: r?.group ? `${r.group.code} · ${r.group.name}` : '—' },
        { key: 'entities', value: mdf ? 'MDF E1 17mm · 1220×2440 · 3 container · 1,2 tỷ ₫ · Bình Dương · tháng 10' : (text.match(/\d+[^\s,.]*/g) ?? []).slice(0, 4).join(' · ') || '—' },
        { key: 'scores', value: pick.rule === 'R-03' ? 'độ nóng 62 · tiềm năng 48 · rủi ro 87' : 'độ nóng 91 · tiềm năng 74 · rủi ro 12' },
        { key: 'confidence', value: `${confStr} — ${write === 'clean' ? `trên ngưỡng ${thr}, được ghi vào kho sạch` : `dưới ngưỡng ${thr}, giữ lại chờ Sếp xem`}` },
        { key: 'actions', value: pick.rule === 'R-01' ? 'tạo OPP-1842 · gợi ý ráp 2 nhà cung cấp · đẩy vào hàng đợi' : pick.rule === 'R-03' ? 'đẩy cảnh báo P1 · xin lỗi trước khi trả lời' : 'ghi vào hồ sơ sống' },
      ],
      matched_rules: pick.rule ? [pick.rule] : [],
      discarded_by: null,
      confidence: conf,
      would_write: write,
    };
  }

  // ── setup 4–7, 12 (called from the phase-1 setup router) ──
  function setupStep(n: number, body: Record<string, unknown>): { ok: true } | { status: number; code: string; title: string; extra?: Record<string, unknown> } {
    if (n === 4) {
      const ids = Array.isArray(body.provider_ids) ? (body.provider_ids as string[]) : [];
      const okIds = ids.filter((id) => providers.find((p) => p.id === id)?.auth_state === 'ok');
      const cliOk = cliProfiles.some((p) => p.active && p.state !== 'expired');
      if (!okIds.length && !cliOk) return { status: 409, code: 'STEP_INCOMPLETE', title: 'Cần ít nhất một nhà cung cấp đã gọi thử thành công, hoặc Antigravity CLI đã đăng nhập' };
      // v0.1.28 (UX C1): như `gh.setup.routes.step4` — nguồn OK chưa có model thì lấy model đầu tiên khi gọi thử;
      // không nguồn nào có model → chưa cho qua.
      for (const id of ids) {
        const p = providers.find((x) => x.id === id);
        const first = p?.last_test?.ok ? p.last_test.models.find((x) => !/embed/i.test(x)) : undefined;
        if (p && !p.models.length && first) p.models.push({ id: randomUUID(), model_name: first, daily_quota: null, used_today: 0 });
      }
      if (!ids.some((id) => (providers.find((x) => x.id === id)?.models.length ?? 0) > 0)) {
        return { status: 409, code: 'STEP_INCOMPLETE', title: 'Chưa có model nào để dùng — bấm "Kiểm tra" ở một nguồn rồi chọn "Dùng model này"' };
      }
      const rest = [...providers].sort((a, b) => a.failover_rank - b.failover_rank).filter((p) => !ids.includes(p.id));
      [...ids.map((id) => providers.find((x) => x.id === id)!), ...rest].forEach((p, i) => {
        if (p) p.failover_rank = i + 1;
      });
      return { ok: true };
    }
    if (n === 5) {
      if (!channels.some((c) => c.state === 'active')) return { status: 409, code: 'STEP_INCOMPLETE', title: 'Cần kết nối ít nhất một kênh (quét mã QR) trước khi tiếp tục' };
      return { ok: true };
    }
    if (n === 6) {
      const list = Array.isArray(body.groups) ? (body.groups as Array<{ id: string; listen_mode: ListenMode; view_scope: ViewScope }>) : [];
      for (const x of list) {
        const g = groups.find((y) => y.id === x.id);
        if (g) {
          g.listen_mode = x.listen_mode;
          g.view_scope = x.view_scope;
        }
      }
      if (!groups.some((g) => g.listen_mode !== 'off')) return { status: 409, code: 'STEP_INCOMPLETE', title: 'Cần bật lắng nghe ít nhất một nhóm' };
      return { ok: true };
    }
    if (n === 7) {
      const ws = Array.isArray(body.weights) ? (body.weights as Array<{ dimension: string; value: number }>) : [];
      if (ws.reduce((s, w) => s + Number(w.value), 0) !== 100) return { status: 422, code: 'VALIDATION_ERROR', title: 'Dữ liệu chưa hợp lệ', extra: { errors: { weights: 'Tổng trọng số phải bằng 100%' } } };
      schedule.interval_seconds = Number(body.interval_seconds) || 900;
      schedule.count_threshold = Number(body.count_threshold) || 500;
      schedule.min_confidence = Number(body.min_confidence) || 0.6;
      schedule.next_run_at = Date.now() + schedule.interval_seconds * 1000;
      weights = weights.map((w) => ({ ...w, value: ws.find((x) => x.dimension === w.dimension)?.value ?? w.value }));
      const codes = Array.isArray(body.rule_codes) ? (body.rule_codes as string[]) : [];
      rules.splice(0, rules.length);
      RULES.forEach((r) => addRule(r, codes.includes(r.code)));
      kickFirstRun();
      return { ok: true };
    }
    return { ok: true };
  }

  const rulePresets = (): Rule[] =>
    RULES.map((r) => ({ ...r, id: `preset-${r.code}`, version: 1, kind_label: KIND_LABEL[r.kind] ?? r.kind, hits_24h: 0, updated_at: iso(Date.now()) }));

  const firstRunView = () => {
    const run = firstRun.run ?? runs[0] ?? null;
    return {
      raw_collected: rawTotal,
      classifying: run && run.status !== 'done' ? Math.max(0, run.input_count - run.clean_count - run.lowconf_count - run.noise_count) : 0,
      clean: run?.clean_count ?? 0,
      lowconf: run?.lowconf_count ?? 0,
      discarded: run?.noise_count ?? 0,
      run,
    };
  };

  /** v0.1.28 (UX N3): "Để sau" ở bước 7 = nạp bộ quy tắc khởi đầu khi chưa có quy tắc nào. */
  function seedDefaultRules(): void {
    if (!rules.length) RULES.forEach((r) => addRule(r));
  }

  return {
    handle,
    setupStep,
    seedDefaultRules,
    rulePresets,
    firstRunView,
    /** Test hooks. */
    hooks: {
      rulesEnabled: () => rules.some((r) => r.enabled),
      pushRaw,
      pushEvent,
      scan,
      setSimulation,
      setBridge: (on: boolean) => {
        bridgeOnline = on;
      },
      /** `__mock/discover` — 3 nhóm mới chờ chọn + WS `group.discovered`. */
      discover: (type: 'zalo' | 'whatsapp', ask = false) => discoverGroups(type, true, ask),
      channelState: (type: string, state: ChannelState) => {
        const c = channelOf(type);
        if (c) c.state = state;
      },
      /** PLAN 4.2 — cho `mock-p4-api.ts` đọc/sắp lại chuỗi chuyển hướng trên chính mảng dùng chung này. */
      providers: () => providers,
      /** PLAN 4.1 — cho `mock-p4-agents.ts` suy ra `channel_type` từ `channel_id` khi lưu phạm vi nghe. */
      channels: () => channels,
      /** PLAN 4.5 — cho `mock-p4-system.ts` đọc `/listening-groups` trên chính mảng dùng chung này. */
      groups: () => groups,
      /** v0.1.39 — cho `mock-boss-checks.ts`: nghe lượt đăng nhập CLI kết thúc (done/failed). */
      onCliLogin: (fn: (kind: string, ok: boolean, email: string | null) => void) => {
        cliLoginListener = fn;
      },
      /** v0.1.39 — cho `mock-boss-checks.ts`: chính mảng hồ sơ CLI dùng chung (sửa tại chỗ = đổi luôn `/cli/profiles`). */
      cliProfiles: (kind: string | null = 'antigravity_cli') => profilesOf(kind),
      /** v0.1.39 — như `POST /cli/profiles/{id}/activate` (không PIN — mock-boss-checks tự gác PIN); false nếu không có. */
      activateCli: (id: string) => {
        const list = cliProfiles.some((x) => x.id === id) ? cliProfiles : claudeProfiles;
        const pr = list.find((x) => x.id === id);
        if (!pr) return false;
        list.forEach((x) => (x.active = x.id === pr.id));
        return true;
      },
    },
    dispose: () => {
      setSimulation(false);
      timers.forEach((t) => clearTimeout(t));
      timers.clear();
    },
  };
}

export type Phase2 = ReturnType<typeof createPhase2>;
