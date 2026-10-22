/**
 * Mock API giai đoạn 3 · Cơ hội & Thị trường (docs/api/phase-3-market.md): Bảng cơ hội, Cung ↔ Cầu, Kho hội
 * thoại, Deal & Vụ việc. `handle` trả true khi đã trả lời request. Dữ liệu mẫu lấy cảm hứng từ
 * docs/design/seed-data.json (oppColumns, marketSides, matches, searchResults, searchFacets) đúng cách
 * mock-p3-queue.ts đã làm cho cụm Hàng đợi & Hành động.
 */
import type {
  CaseItem,
  Deal,
  DraftDetail,
  GroupRef,
  Match,
  MatchStatus,
  MarketSignal,
  Opportunity,
  OpportunityDetail,
  OppStage,
  PersonRef,
  SearchFacetValue,
} from '@gen-harness/contracts';
import { BAO, GROUP_TP, registerExplain } from './mock-p3-core';
import { USER_IDS, rejectNonUuid } from './mock-ids';
import { maskText, type P2Ctx } from './mock-phase2';

/**
 * Cùng 9 giai đoạn của `gh.biz.market.service.STAGES` (docs/api/phase-3-market.md) — chép tay thay vì import
 * runtime `OPP_STAGES` từ `@gen-harness/contracts`: mock này bị `vite.config.ts` bundle tĩnh (qua
 * `import('./test/mock-api')`), và một import runtime (không phải `import type`) vào gói workspace đó làm
 * bước bundle cấu hình của Vite cố resolve thật cả gói — lỗi `ERR_MODULE_NOT_FOUND` với loader ESM gốc của
 * Node. Mọi mock khác trong thư mục này chỉ dùng `import type` từ `@gen-harness/contracts` đúng vì lý do này.
 */
const OPP_STAGES: OppStage[] = ['raw_signal', 'validated', 'matched', 'approaching', 'negotiating', 'handed_off', 'won', 'lost', 'dormant'];

export interface P3Options {
  fresh: boolean;
  emit: (type: string, data: unknown) => void;
  /** Tạo bản nháp thật ở cụm nền chung (Bàn làm việc) — dùng khi "Giới thiệu hai bên". */
  pushDraft?: (d: DraftDetail) => unknown;
  /** v0.1.35: người dùng đang hoạt động theo id (mock-api `users`) — gán người xử lý kiểm như API (UUID lạ → 404). */
  findUser?: (id: string) => { id: string; display_name: string } | undefined;
}

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();
const has = (ctx: P2Ctx, perm: string) => !!ctx.perms[perm] && ctx.perms[perm] !== 'none';

// ─── đối tượng dùng chung ───────────────────────────────────────────────────
const HAU: PersonRef = { id: 'p-hau', code: 'PER-0311', name: 'Trần Văn Hậu', type: 'customer', org_name: 'Xưởng gỗ Bình Dương' };
const DUOC: PersonRef = { id: 'p-duoc', code: 'PER-0402', name: 'Lâm Văn Được', type: 'supplier', org_name: 'Kho ván Bình Dương' };
const THANG: PersonRef = { id: 'p-thang', code: 'PER-0733', name: 'Bùi Đức Thắng', type: 'customer', org_name: 'Gỗ Trường Thành Mới' };
const PHAT: PersonRef = { id: 'p-phat', code: 'PER-0455', name: 'Nguyễn Hữu Phát', type: 'supplier', org_name: 'Gỗ Phát Đạt' };
const BICH: PersonRef = { id: 'p-bich', code: 'PER-0844', name: 'Lê Thị Bích', type: 'customer', org_name: 'Kho lạnh Tân Cảng' };
const KHANG: PersonRef = { id: 'p-khang-logistics', code: 'PER-0119', name: 'Nguyễn Anh Khoa', type: 'supplier', org_name: 'An Khang Logistics' };
const TRI: PersonRef = { id: 'p-tri', code: 'PER-0688', name: 'Đặng Hữu Trí', type: 'customer', org_name: 'Gỗ Đông Phương' };
const MINH: PersonRef = { id: 'p-minh', code: 'PER-0512', name: 'Phạm Quốc Minh', type: 'customer', org_name: 'Nội thất Minh Long' };
const VAN: PersonRef = { id: 'p-van', code: 'PER-0921', name: 'Lý Thị Vân', type: 'customer', org_name: 'Nội thất Hoà Bình' };
const DUYEN: PersonRef = { id: 'p-duyen', code: 'PER-0733b', name: 'Trịnh Mỹ Duyên', type: 'customer', org_name: 'Bao bì Sài Gòn Mới' };
const NAM: PersonRef = { id: 'p-nam', code: 'PER-0577', name: 'Ngô Hoàng Nam', type: 'customer', org_name: 'Nội thất An Cư' };
const LOC: PersonRef = { id: 'p-loc', code: 'PER-0349', name: 'Hà Văn Lộc', type: 'customer', org_name: 'Gỗ Phú Thịnh' };
const GROUP_GO: GroupRef = { id: 'g-nganhgo', code: 'GRP-ZL-0231', name: 'Group Ngành gỗ Miền Nam', channel: 'zalo' };
const GROUP_IN: GroupRef = { id: 'g-nganhin', code: 'GRP-ZL-0602', name: 'Group Ngành in', channel: 'zalo' };

type OppRow = OpportunityDetail;

function seedOpportunities(): OppRow[] {
  const mk = (
    code: string,
    stage: OppStage,
    need: string,
    person: PersonRef | null,
    group: GroupRef | null,
    valueVnd: number | null,
    confidence: Opportunity['confidence'],
    heat: number | null,
    ageMin: number,
    opts: Partial<OppRow> = {},
  ): OppRow => ({
    id: `opp-${code.toLowerCase()}`,
    code,
    need,
    stage,
    value_vnd: valueVnd,
    confidence,
    heat,
    person,
    group,
    owner: null,
    first_signal_at: ago(ageMin),
    first_contact_at: stage === 'raw_signal' ? null : ago(Math.max(0, ageMin - 20)),
    closed_at: stage === 'won' || stage === 'lost' || stage === 'dormant' ? ago(Math.max(0, ageMin - 200)) : null,
    created_at: ago(ageMin),
    updated_at: ago(Math.max(0, ageMin - 5)),
    suggested_match: null,
    risk_note: null,
    stage_history: [{ from_stage: null, to_stage: 'raw_signal', actor: null, at: ago(ageMin) }],
    ...opts,
  });
  return [
    mk('OPP-1851', 'raw_signal', 'Cần kho lạnh 400 pallet khu vực Tân Cảng', BICH, null, null, 'low', 58, 40),
    mk('OPP-1849', 'raw_signal', 'Hỏi nguồn ván phủ melamine giá sỉ', null, GROUP_GO, 200_000_000, 'low', 44, 90),
    mk('OPP-1842', 'validated', '3 container ván MDF E1 17mm, giao tháng 10', HAU, GROUP_GO, 1_200_000_000, 'high', 91, 18, {
      suggested_match: { item: '6 cont ván E1 17mm tồn kho Bình Dương', score: 94, person: DUOC, group: null },
    }),
    mk('OPP-1839', 'validated', 'Ứng viên Key Account ngành lạnh', null, GROUP_IN, null, 'medium', 74, 360),
    mk('OPP-1836', 'matched', 'Ván MDF 540 triệu ₫ cho đơn nội thất', TRI, null, 540_000_000, 'high', 22, 74 * 1440, {
      suggested_match: { item: '6 cont ván E1 17mm tồn kho Bình Dương', score: 52, person: DUOC, group: null },
      risk_note: 'Đứng yên hơn 7 ngày ở giai đoạn này — nên chủ động follow lại',
    }),
    mk('OPP-1828', 'approaching', 'Báo giá nội thất văn phòng trọn gói', MINH, null, 310_000_000, 'high', 64, 1440),
    mk('OPP-1815', 'negotiating', 'Hợp đồng in ấn quý 4', BAO, GROUP_TP, 84_000_000, 'high', 87, 47, {
      risk_note: 'Chưa tiếp cận sau 24 giờ kể từ tín hiệu đầu tiên — dễ mất vào tay đối thủ',
    }),
    mk('OPP-1809', 'negotiating', 'Tuyến vận chuyển lạnh Bình Dương – Tân Cảng', KHANG, null, 128_000_000, 'medium', 78, 300),
    mk('OPP-1794', 'handed_off', 'Đơn giấy in catalogue 5.000 bản', null, GROUP_IN, 96_000_000, 'medium', 41, 5 * 1440),
    mk('OPP-1780', 'won', 'Nội thất phòng họp — đã chốt hợp đồng', VAN, null, 620_000_000, 'high', 57, 20 * 1440),
    mk('OPP-1772', 'lost', 'Bao bì carton in offset — chọn nhà cung khác', DUYEN, null, 510_000_000, 'low', 31, 30 * 1440),
    mk('OPP-1765', 'dormant', 'Ván ép công nghiệp — im lặng quá lâu', NAM, null, 740_000_000, 'medium', 49, 45 * 1440),
  ];
}

function seedSignals(): MarketSignal[] {
  const mk = (
    id: string,
    side: MarketSignal['side'],
    item: string,
    category: string | null,
    quantity: number | null,
    unit: string | null,
    valueVnd: number | null,
    location: string | null,
    heat: number | null,
    status: MarketSignal['status'],
    ageMin: number,
    person: PersonRef | null,
    group: GroupRef | null = null,
  ): MarketSignal => ({
    id,
    side,
    item,
    category,
    quantity,
    unit,
    value_vnd: valueVnd,
    location,
    needed_by: null,
    heat,
    status,
    created_at: ago(ageMin),
    person,
    group,
  });
  return [
    mk('sig-d-311', 'demand', '3 cont ván MDF E1 17mm 1220x2440', 'gỗ công nghiệp', 3, 'container', 1_200_000_000, 'Bình Dương', 91, 'matched', 18, HAU),
    mk('sig-d-733', 'demand', 'Ván phủ melamine giá sỉ, số lượng lớn', 'gỗ công nghiệp', 12, 'container', 880_000_000, 'Bình Dương', 72, 'matched', 120, THANG),
    mk('sig-d-512', 'demand', 'Nội thất văn phòng trọn gói', 'nội thất', null, null, 310_000_000, 'TP.HCM', 64, 'open', 1440, MINH),
    mk('sig-d-844', 'demand', 'Kho lạnh 400 pallet khu Tân Cảng', null, 400, 'pallet', null, 'Tân Cảng', 58, 'open', 2880, BICH),
    mk('sig-d-688', 'demand', 'Ván MDF cho đơn nội thất quý 4', 'gỗ công nghiệp', 6, 'container', 540_000_000, 'TP.HCM', 22, 'open', 74 * 1440, TRI),
    mk('sig-d-902', 'demand', 'Giấy C300 in catalogue 5.000 bản', 'in ấn', 5000, 'bản', 96_000_000, null, 41, 'open', 300, null, GROUP_IN),
    mk('sig-s-402', 'supply', '6 cont ván E1 17mm tồn kho Bình Dương', 'gỗ công nghiệp', 6, 'container', 2_300_000_000, 'Bình Dương', 84, 'open', 12, DUOC),
    mk('sig-s-455', 'supply', 'Ván phủ melamine 12mm, giá sỉ theo container', 'gỗ công nghiệp', 14, 'container', 1_100_000_000, 'Đồng Nai', 76, 'open', 180, PHAT),
    mk('sig-s-119', 'supply', 'Tuyến vận chuyển lạnh Bình Dương – Tân Cảng', null, null, null, 128_000_000, 'Tân Cảng', 78, 'open', 60, KHANG),
    mk('sig-s-601', 'supply', 'Dịch vụ in bao bì công suất lớn', 'in ấn', null, null, null, null, 52, 'open', 240, null, GROUP_IN),
  ];
}

type MatchRow = Match;

function seedMatches(signals: MarketSignal[]): MatchRow[] {
  const find = (id: string) => signals.find((s) => s.id === id)!;
  const ref = (s: MarketSignal) => ({ id: s.id, item: s.item, person: s.person, group: s.group });
  const mk = (id: string, demandId: string, supplyId: string, score: number, reasons: string[], ageMin: number): MatchRow => ({
    id,
    score,
    reasons,
    status: 'suggested',
    opportunity_id: null,
    created_at: ago(ageMin),
    demand: ref(find(demandId)),
    supply: ref(find(supplyId)),
  });
  return [
    mk('match-1', 'sig-d-311', 'sig-s-402', 94, [
      'Cùng mặt hàng: "3 cont ván MDF E1 17mm 1220x2440" ~ "6 cont ván E1 17mm tồn kho Bình Dương" (+50)',
      'Số lượng khớp 50%: cầu 3 container · cung 6 container (+10)',
      'Trong ngân sách: cung 1.200.000.000₫ ≤ ngân sách 1.200.000.000₫ (+20)',
      'Cùng khu vực: Bình Dương (+10)',
    ], 15),
    mk('match-2', 'sig-d-844', 'sig-s-119', 81, [
      'Cùng ngành hàng: null (+30)',
      'Cùng khu vực: Tân Cảng (+10)',
    ], 55),
    mk('match-3', 'sig-d-902', 'sig-s-601', 76, ['Cùng ngành hàng: in ấn (+30)'], 200),
    mk('match-4', 'sig-d-733', 'sig-s-455', 68, [
      'Cùng mặt hàng: "Ván phủ melamine giá sỉ, số lượng lớn" ~ "Ván phủ melamine 12mm, giá sỉ theo container" (+50)',
    ], 100),
    mk('match-5', 'sig-d-688', 'sig-s-402', 52, [
      'Cùng mặt hàng: "Ván MDF cho đơn nội thất quý 4" ~ "6 cont ván E1 17mm tồn kho Bình Dương" (+50)',
    ], 74 * 1440),
  ];
}

function seedSearch(): Array<{
  person: PersonRef;
  match_count: number;
  last_at: string;
  last_snippet: string;
  last_event_type: string;
  channel: string;
}> {
  return [
    { person: TRI, match_count: 3, last_at: ago(200), last_snippet: 'Hỏi giá ván MDF 540 triệu ₫ cho đơn nội thất quý 4', last_event_type: 'AskedPrice', channel: 'zalo' },
    { person: MINH, match_count: 2, last_at: ago(1440), last_snippet: 'Hỏi giá nội thất văn phòng trọn gói 310 triệu ₫', last_event_type: 'AskedPrice', channel: 'zalo' },
    { person: HAU, match_count: 3, last_at: ago(18), last_snippet: 'Hỏi giá 3 container ván MDF E1 17mm, giao tháng 10', last_event_type: 'AskedPrice', channel: 'zalo' },
    { person: VAN, match_count: 1, last_at: ago(3000), last_snippet: 'Yêu cầu mẫu nội thất phòng họp 620 triệu ₫', last_event_type: 'RequestedSample', channel: 'whatsapp' },
    { person: THANG, match_count: 4, last_at: ago(120), last_snippet: 'Hỏi giá ván phủ melamine, sốt ruột vì chưa có báo giá', last_event_type: 'AskedPrice', channel: 'zalo' },
    { person: DUYEN, match_count: 2, last_at: ago(40_000), last_snippet: 'Im lặng sau khi nhận báo giá bao bì carton', last_event_type: 'WentSilent', channel: 'zalo' },
    { person: NAM, match_count: 2, last_at: ago(50_000), last_snippet: 'So sánh giá với nhà cung cấp khác cho nội thất An Cư', last_event_type: 'ComparedVendor', channel: 'whatsapp' },
    { person: LOC, match_count: 1, last_at: ago(80_000), last_snippet: 'Hỏi giá ván công nghiệp 560 triệu ₫', last_event_type: 'AskedPrice', channel: 'zalo' },
  ];
}

function seedDeals(): Deal[] {
  return [
    { id: 'deal-91', code: 'DEA-0091', opportunity_id: 'opp-opp-1815', person: BAO, amount_vnd: 84_000_000, status: 'won', won_at: ago(200), erp_ref: 'ERP-2026-0091', created_at: ago(2000), updated_at: ago(200) },
    { id: 'deal-92', code: 'DEA-0092', opportunity_id: 'opp-opp-1842', person: HAU, amount_vnd: 1_200_000_000, status: 'open', won_at: null, erp_ref: null, created_at: ago(500), updated_at: ago(500) },
    { id: 'deal-93', code: 'DEA-0093', opportunity_id: 'opp-opp-1772', person: DUYEN, amount_vnd: 510_000_000, status: 'lost', won_at: null, erp_ref: null, created_at: ago(40_000), updated_at: ago(38_000) },
  ];
}

function seedCases(): CaseItem[] {
  return [
    {
      id: 'case-18', code: 'CAS-0018', kind: 'complaint', priority: 'P1', title: 'Giao hàng trễ hẹn 3 ngày, khách đang gay gắt',
      status: 'in_progress', assignee: { id: USER_IDS.lan, name: 'Chị Lan Phạm' }, subject: BAO, opened_at: ago(300), resolved_at: null, updated_at: ago(60),
    },
    {
      id: 'case-17', code: 'CAS-0017', kind: 'complaint', priority: 'P2', title: 'Sai quy cách ván MDF so với báo giá',
      status: 'open', assignee: null, subject: TRI, opened_at: ago(1000), resolved_at: null, updated_at: ago(1000),
    },
    {
      id: 'case-15', code: 'CAS-0015', kind: 'complaint', priority: 'P3', title: 'Hoá đơn ghi sai địa chỉ công ty',
      status: 'resolved', assignee: { id: USER_IDS.owner, name: 'Anh Nguyễn Văn A (Chủ)' }, subject: MINH, opened_at: ago(6000), resolved_at: ago(5000), updated_at: ago(5000),
    },
  ];
}

export function createMock(opts: P3Options) {
  let opportunities: OppRow[] = opts.fresh ? [] : seedOpportunities();
  const signals: MarketSignal[] = opts.fresh ? [] : seedSignals();
  const matches: MatchRow[] = opts.fresh ? [] : seedMatches(signals);
  let deals: Deal[] = opts.fresh ? [] : seedDeals();
  let cases: CaseItem[] = opts.fresh ? [] : seedCases();
  const searchRows = opts.fresh ? [] : seedSearch();
  let draftSeq = 0;

  registerExplain('opportunity', (id) => {
    const o = opportunities.find((x) => x.id === id);
    if (!o) return null;
    return {
      kind: 'opportunity',
      id,
      title: `${o.code} · ${o.need}`,
      statement: o.heat !== null ? `Độ nóng ${Math.round(o.heat)}/100` : 'Chưa có điểm nóng',
      method: 'rules+model',
      factors: [],
      units: [],
      history: [],
    };
  });

  function stripHistory(o: OppRow): Opportunity {
    const { stage_history: _h, ...rest } = o;
    return rest;
  }

  function pipeline() {
    const stages = OPP_STAGES.map((stage) => {
      const rows = opportunities.filter((o) => o.stage === stage);
      return { stage, count: rows.length, value_vnd: rows.reduce((s, o) => s + (o.value_vnd ?? 0), 0) };
    });
    const CLOSED = new Set(['won', 'lost', 'dormant']);
    const open = stages.filter((s) => !CLOSED.has(s.stage));
    return {
      stages,
      open_pipeline_value_vnd: open.reduce((s, x) => s + x.value_vnd, 0),
      open_pipeline_count: open.reduce((s, x) => s + x.count, 0),
    };
  }

  function makeDraftFromMatch(m: MatchRow, opportunityId: string): DraftDetail {
    draftSeq += 1;
    const person = m.demand.person;
    const target = person
      ? { channel: 'zalo' as const, thread_type: 'user' as const, group: null, person }
      : null;
    const text = `Chào anh/chị, bên em có nguồn cung phù hợp với nhu cầu "${m.demand.item}":\n\n${m.supply.item}\n\nAnh/chị có muốn em gửi thêm chi tiết không ạ?`;
    return {
      id: `draft-intro-${draftSeq}-${Date.now()}`,
      code: `ACT-05${draftSeq}`,
      kind: target ? 'message' : 'report',
      kind_label: target ? 'Tin nhắn' : 'Báo cáo',
      title: `Giới thiệu nguồn cung cho ${person?.name ?? m.demand.group?.name ?? 'khách'}`,
      agent: null,
      created_by: { id: USER_IDS.owner, name: 'Anh Nguyễn Văn A (Chủ)' },
      created_at: new Date().toISOString(),
      status: 'pending',
      hold_reason: null,
      subject: person,
      paragraphs: text.split(/\n\s*\n/),
      text,
      lang: 'vi',
      target,
      amount_vnd: null,
      autonomy_level: 4,
      flags: { writes_external: !!target, personnel_related: false, over_threshold: false },
      approve_label: target ? 'Duyệt và gửi qua Zalo' : 'Duyệt và thực hiện',
      sources: [{ label: `Tín hiệu cầu · ${m.demand.item}`, ref: { type: 'opportunity', id: opportunityId } }],
      context: [],
      side_actions: [],
      decision: null,
      send_result: null,
      versions: [],
    };
  }

  function handle(ctx: P2Ctx): boolean {
    const { method: m, path: p, url, body, reply, problem } = ctx;
    const seg = p.split('/').filter(Boolean);

    // ── Bảng cơ hội ──
    if (seg[0] === 'opportunities') {
      if (!has(ctx, 'opportunity.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (seg.length === 1 && m === 'GET') {
        const stage = url.searchParams.get('stage');
        const owner = url.searchParams.get('owner_user_id');
        const confidence = url.searchParams.get('confidence');
        let rows = opportunities;
        if (stage) rows = rows.filter((o) => o.stage === stage);
        if (owner) rows = rows.filter((o) => o.owner?.id === owner);
        if (confidence) rows = rows.filter((o) => o.confidence === confidence);
        return reply(200, { items: rows.map(stripHistory), next_cursor: null, total: rows.length });
      }
      if (seg.length === 2 && seg[1] === 'pipeline' && m === 'GET') return reply(200, pipeline());
      const row = opportunities.find((o) => o.id === seg[1]);
      if (seg.length === 1 && m === 'POST') {
        if (!has(ctx, 'opportunity.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        const b = body as { person_id: string; need: string; value_vnd?: number | null; confidence?: Opportunity['confidence'] };
        if (!b.need?.trim()) return problem(422, 'VALIDATION', 'Cần mô tả nhu cầu', { errors: { need: 'Không được để trống' } });
        const o: OppRow = {
          id: `opp-new-${Date.now()}`,
          code: `OPP-1${900 + opportunities.length}`,
          need: b.need,
          stage: 'raw_signal',
          value_vnd: b.value_vnd ?? null,
          confidence: b.confidence ?? 'medium',
          heat: null,
          person: null,
          group: null,
          owner: null,
          first_signal_at: new Date().toISOString(),
          first_contact_at: null,
          closed_at: null,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          suggested_match: null,
          risk_note: null,
          stage_history: [{ from_stage: null, to_stage: 'raw_signal', actor: null, at: new Date().toISOString() }],
        };
        opportunities = [o, ...opportunities];
        return reply(201, o);
      }
      if (seg.length === 2 && m === 'GET') {
        if (!row) return problem(404, 'NOT_FOUND', 'Cơ hội không tồn tại hoặc ngoài phạm vi của bạn');
        return reply(200, row);
      }
      if (seg.length === 3 && seg[2] === 'stage' && m === 'PATCH') {
        if (!has(ctx, 'opportunity.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        if (!row) return problem(404, 'NOT_FOUND', 'Cơ hội không tồn tại hoặc ngoài phạm vi của bạn');
        const toStage = (body as { to_stage: string }).to_stage;
        if (!OPP_STAGES.includes(toStage as OppStage)) {
          return problem(422, 'VALIDATION', `Chỉ nhận một trong: ${OPP_STAGES.join(', ')}`, { errors: { to_stage: 'Giai đoạn không hợp lệ' } });
        }
        if (row.stage === toStage) return reply(200, row);
        const fromStage = row.stage;
        row.stage = toStage as OppStage;
        row.updated_at = new Date().toISOString();
        if (!row.first_contact_at && fromStage === 'raw_signal' && toStage !== 'raw_signal') row.first_contact_at = new Date().toISOString();
        if (['won', 'lost', 'dormant'].includes(toStage)) row.closed_at = new Date().toISOString();
        row.stage_history = [{ from_stage: fromStage, to_stage: toStage as OppStage, actor: ctx.userLabel, at: new Date().toISOString() }, ...row.stage_history];
        return reply(200, row);
      }
    }

    // ── Cung ↔ Cầu ──
    if (seg[0] === 'supply') {
      if (!has(ctx, 'opportunity.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (seg.length === 1 && m === 'GET') {
        const side = url.searchParams.get('side');
        const status = url.searchParams.get('status');
        const category = url.searchParams.get('category');
        let rows = signals;
        if (side) rows = rows.filter((s) => s.side === side);
        if (status) rows = rows.filter((s) => s.status === status);
        if (category) rows = rows.filter((s) => s.category === category);
        return reply(200, { items: rows, next_cursor: null, total: rows.length });
      }
      if (seg.length === 2 && m === 'GET') {
        const row = signals.find((s) => s.id === seg[1]);
        if (!row) return problem(404, 'NOT_FOUND', 'Tín hiệu không tồn tại hoặc ngoài phạm vi của bạn');
        const otherSide = row.side === 'demand' ? 'supply' : 'demand';
        const related = matches
          .filter((mt) => (row.side === 'demand' ? mt.demand.id === row.id : mt.supply.id === row.id))
          .sort((a, b) => b.score - a.score)
          .map((mt) => {
            const other = mt[otherSide];
            return { id: mt.id, score: mt.score, reasons: mt.reasons, status: mt.status, item: other.item, person: other.person, group: other.group };
          });
        return reply(200, { ...row, matches: related });
      }
    }

    // ── Cặp ghép ──
    if (seg[0] === 'matches') {
      if (!has(ctx, 'opportunity.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (seg.length === 1 && m === 'GET') {
        const status = url.searchParams.get('status') as MatchStatus | null;
        const minScore = Number(url.searchParams.get('min_score') ?? 0);
        let rows = matches;
        if (status) rows = rows.filter((mt) => mt.status === status);
        rows = rows.filter((mt) => mt.score >= minScore);
        return reply(200, { items: rows, next_cursor: null, total: rows.length });
      }
      if (seg.length === 2 && seg[1] === 'recompute' && m === 'POST') {
        if (!has(ctx, 'opportunity.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        return reply(200, { ok: true, matches: matches.length });
      }
      const row = matches.find((mt) => mt.id === seg[1]);
      if (seg.length === 3 && seg[2] === 'introduce' && m === 'POST') {
        if (!has(ctx, 'opportunity.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        if (!row) return problem(404, 'NOT_FOUND', 'Gợi ý ghép không tồn tại hoặc ngoài phạm vi của bạn');
        if (row.status !== 'suggested') return problem(409, 'MATCH_DECIDED', 'Gợi ý ghép này đã được quyết định');
        let oppId = row.opportunity_id;
        if (!oppId) {
          const code = `OPP-1${900 + opportunities.length}`;
          const o: OppRow = {
            id: `opp-from-match-${row.id}`,
            code,
            need: row.demand.item,
            stage: 'matched',
            value_vnd: null,
            confidence: 'medium',
            heat: null,
            person: row.demand.person,
            group: row.demand.group,
            owner: null,
            first_signal_at: new Date().toISOString(),
            first_contact_at: new Date().toISOString(),
            closed_at: null,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            suggested_match: null,
            risk_note: null,
            stage_history: [{ from_stage: null, to_stage: 'matched', actor: ctx.userLabel, at: new Date().toISOString() }],
          };
          opportunities = [o, ...opportunities];
          oppId = o.id;
        } else {
          const o = opportunities.find((x) => x.id === oppId);
          if (o && (o.stage === 'raw_signal' || o.stage === 'validated')) {
            o.stage_history = [{ from_stage: o.stage, to_stage: 'matched', actor: ctx.userLabel, at: new Date().toISOString() }, ...o.stage_history];
            o.stage = 'matched';
            o.updated_at = new Date().toISOString();
          }
        }
        row.status = 'introduced';
        row.opportunity_id = oppId;
        const draft = makeDraftFromMatch(row, oppId);
        (opts.pushDraft ?? (() => draft))(draft);
        return reply(200, { ok: true, opportunity_id: oppId, draft: { id: draft.id, code: draft.code, status: draft.status, outcome: 'held', hold_reason: null } });
      }
      if (seg.length === 3 && seg[2] === 'reject' && m === 'POST') {
        if (!has(ctx, 'opportunity.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        if (!row) return problem(404, 'NOT_FOUND', 'Gợi ý ghép không tồn tại hoặc ngoài phạm vi của bạn');
        if (row.status !== 'suggested') return problem(409, 'MATCH_DECIDED', 'Gợi ý ghép này đã được quyết định');
        row.status = 'rejected';
        return reply(200, { ok: true });
      }
    }

    // ── Kho hội thoại ──
    if (seg[0] === 'search') {
      if (seg.length === 1 && m === 'GET') {
        if (!has(ctx, 'opportunity.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        const q = (url.searchParams.get('q') ?? '').trim().toLocaleLowerCase('vi');
        const eventType = url.searchParams.get('event_type');
        const channel = url.searchParams.get('channel');
        const dateFrom = url.searchParams.get('date_from');
        const dateTo = url.searchParams.get('date_to');
        let rows = searchRows;
        if (eventType) rows = rows.filter((r) => r.last_event_type === eventType);
        if (channel) rows = rows.filter((r) => r.channel === channel);
        if (dateFrom) rows = rows.filter((r) => new Date(r.last_at).getTime() >= new Date(dateFrom).getTime());
        if (dateTo) rows = rows.filter((r) => new Date(r.last_at).getTime() <= new Date(dateTo).getTime());
        const eventFacet = new Map<string, number>();
        const channelFacet = new Map<string, number>();
        for (const r of rows) {
          eventFacet.set(r.last_event_type, (eventFacet.get(r.last_event_type) ?? 0) + 1);
          channelFacet.set(r.channel, (channelFacet.get(r.channel) ?? 0) + 1);
        }
        if (q) {
          rows = rows.filter(
            (r) =>
              r.person.name.toLocaleLowerCase('vi').includes(q) ||
              (r.person.org_name ?? '').toLocaleLowerCase('vi').includes(q) ||
              r.last_snippet.toLocaleLowerCase('vi').includes(q),
          );
        }
        const owner = ctx.owner;
        const toFacet = (map: Map<string, number>): SearchFacetValue[] =>
          Array.from(map.entries())
            .sort((a, b) => b[1] - a[1])
            .map(([value, count]) => ({ value, count }));
        return reply(200, {
          items: rows.map((r) => ({
            person: { ...r.person, name: maskText(r.person.name) ?? r.person.name },
            match_count: r.match_count,
            last_at: r.last_at,
            last_snippet: owner ? r.last_snippet : maskText(r.last_snippet),
            last_event_type: r.last_event_type,
            evidence: { type: 'meaning_unit', id: `mu-search-${r.person.id}` },
          })),
          next_cursor: null,
          total: rows.length,
          facets: { event_type: toFacet(eventFacet), channel: toFacet(channelFacet) },
        });
      }
      if (seg.length === 2 && seg[1] === 'bulk' && m === 'POST') {
        if (!has(ctx, 'opportunity.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        const b = body as { person_ids: string[]; action: 'tag' | 'task'; text: string };
        if (!b.person_ids?.length) return problem(422, 'VALIDATION', 'Cần chọn ít nhất một người', { errors: { person_ids: 'Không được để trống' } });
        if (!b.text?.trim()) return problem(422, 'VALIDATION', 'Cần nội dung hành động', { errors: { text: 'Không được để trống' } });
        return reply(200, { ok: true, count: b.person_ids.length });
      }
    }

    // ── Deal ──
    if (seg[0] === 'deals') {
      if (!has(ctx, 'opportunity.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (seg.length === 1 && m === 'GET') {
        const status = url.searchParams.get('status');
        const personId = url.searchParams.get('person_id');
        let rows = deals;
        if (status) rows = rows.filter((d) => d.status === status);
        if (personId) rows = rows.filter((d) => d.person?.id === personId);
        return reply(200, { items: rows, next_cursor: null, total: rows.length });
      }
      if (seg.length === 1 && m === 'POST') {
        if (!has(ctx, 'opportunity.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        const b = body as { person_id: string; amount_vnd: number; opportunity_id?: string; erp_ref?: string | null };
        const person = [BAO, HAU, DUOC, THANG, PHAT, BICH, KHANG, TRI, MINH, VAN, DUYEN, NAM, LOC].find((p) => p.id === b.person_id) ?? null;
        const d: Deal = {
          id: `deal-new-${Date.now()}`,
          code: `DEA-00${90 + deals.length + 1}`,
          opportunity_id: b.opportunity_id ?? null,
          person,
          amount_vnd: b.amount_vnd,
          status: 'open',
          won_at: null,
          erp_ref: b.erp_ref ?? null,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        };
        deals = [d, ...deals];
        return reply(201, d);
      }
      const row = deals.find((d) => d.id === seg[1]);
      if (seg.length === 2 && m === 'GET') {
        if (!row) return problem(404, 'NOT_FOUND', 'Deal không tồn tại hoặc ngoài phạm vi của bạn');
        return reply(200, row);
      }
      if (seg.length === 2 && m === 'PATCH') {
        if (!has(ctx, 'opportunity.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        if (!row) return problem(404, 'NOT_FOUND', 'Deal không tồn tại hoặc ngoài phạm vi của bạn');
        const b = body as { status?: Deal['status']; amount_vnd?: number; erp_ref?: string | null };
        if (b.status !== undefined) {
          row.status = b.status;
          row.won_at = b.status === 'won' ? new Date().toISOString() : null;
          if (b.status === 'won' || b.status === 'lost') {
            const o = row.opportunity_id ? opportunities.find((x) => x.id === row.opportunity_id) : undefined;
            if (o && o.stage !== 'won' && o.stage !== 'lost') {
              o.stage_history = [{ from_stage: o.stage, to_stage: b.status as OppStage, actor: ctx.userLabel, at: new Date().toISOString() }, ...o.stage_history];
              o.stage = b.status as OppStage;
              o.closed_at = new Date().toISOString();
              o.updated_at = new Date().toISOString();
            }
          }
        }
        if (b.amount_vnd !== undefined) row.amount_vnd = b.amount_vnd;
        if ('erp_ref' in b) row.erp_ref = b.erp_ref ?? null;
        row.updated_at = new Date().toISOString();
        return reply(200, row);
      }
    }

    // ── Vụ việc ──
    if (seg[0] === 'cases') {
      // Như gh.biz.market.routes._ensure_assignee (v0.1.35): không phải UUID → 422; UUID lạ → 404 'Người dùng'.
      // Trả `false` khi đã trả lời lỗi.
      const assigneeRef = (id: unknown): { id: string; name: string } | false => {
        if (rejectNonUuid(problem, 'assignee_user_id', id)) return false;
        const u = opts.findUser?.(String(id));
        if (!u) {
          problem(404, 'NOT_FOUND', 'Người dùng không tồn tại hoặc ngoài phạm vi của bạn');
          return false;
        }
        return { id: u.id, name: u.display_name };
      };
      if (!has(ctx, 'opportunity.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (seg.length === 1 && m === 'GET') {
        const status = url.searchParams.get('status');
        const assignee = url.searchParams.get('assignee_user_id');
        const priority = url.searchParams.get('priority');
        let rows = cases;
        if (status) rows = rows.filter((c) => c.status === status);
        if (assignee) rows = rows.filter((c) => c.assignee?.id === assignee);
        if (priority) rows = rows.filter((c) => c.priority === priority);
        return reply(200, { items: rows, next_cursor: null, total: rows.length });
      }
      if (seg.length === 1 && m === 'POST') {
        if (!has(ctx, 'opportunity.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        const b = body as { title: string; priority?: CaseItem['priority']; subject?: { type: 'person' | 'group'; id: string }; assignee_user_id?: string };
        if (!b.title?.trim()) return problem(422, 'VALIDATION', 'Cần tiêu đề vụ việc', { errors: { title: 'Không được để trống' } });
        const assignee = b.assignee_user_id == null ? null : assigneeRef(b.assignee_user_id);
        if (assignee === false) return true;
        const c: CaseItem = {
          id: `case-new-${Date.now()}`,
          code: `CAS-00${18 + cases.length + 1}`,
          kind: 'complaint',
          priority: b.priority ?? 'P2',
          title: b.title,
          status: 'open',
          assignee,
          subject: b.subject?.type === 'person' ? BAO : null,
          opened_at: new Date().toISOString(),
          resolved_at: null,
          updated_at: new Date().toISOString(),
        };
        cases = [c, ...cases];
        return reply(201, c);
      }
      const row = cases.find((c) => c.id === seg[1]);
      if (seg.length === 2 && m === 'GET') {
        if (!row) return problem(404, 'NOT_FOUND', 'Vụ việc không tồn tại hoặc ngoài phạm vi của bạn');
        return reply(200, row);
      }
      if (seg.length === 2 && m === 'PATCH') {
        if (!has(ctx, 'opportunity.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        if (!row) return problem(404, 'NOT_FOUND', 'Vụ việc không tồn tại hoặc ngoài phạm vi của bạn');
        const b = body as { status?: CaseItem['status']; assignee_user_id?: string | null; priority?: CaseItem['priority'] };
        const assignee = 'assignee_user_id' in b && b.assignee_user_id != null ? assigneeRef(b.assignee_user_id) : null;
        if (assignee === false) return true;
        if (b.status !== undefined) {
          row.status = b.status;
          row.resolved_at = b.status === 'resolved' || b.status === 'closed' ? new Date().toISOString() : null;
        }
        if ('assignee_user_id' in b) row.assignee = assignee;
        if (b.priority !== undefined) row.priority = b.priority;
        row.updated_at = new Date().toISOString();
        return reply(200, row);
      }
    }

    return false;
  }

  return {
    handle,
    hooks: {
      opportunities: () => opportunities,
      signals: () => signals,
      matches: () => matches,
      deals: () => deals,
      cases: () => cases,
    } as Record<string, (...args: never[]) => unknown>,
    dispose: () => {},
  };
}
