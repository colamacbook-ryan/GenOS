/**
 * Mock API giai đoạn 3 · Con người & Chất lượng (docs/api/phase-3-people.md): Đánh giá con người + Phản biện,
 * Chất lượng chăm sóc. `handle` trả true khi đã trả lời request. Dữ liệu mẫu lấy cảm hứng từ
 * docs/design/seed-data.json (reviewRows, careKpis, careGrid, carePatterns, scripts).
 *
 * Quan trọng — Q4 (`docs/PLAN.md`): `GET/PATCH /people/reviews*`, `POST .../disputes`, `PATCH .../disputes/{id}`
 * và `GET /explain/review/{id}` KHÔNG dùng `has(ctx, 'people_review.read')` chung (ma trận mock đánh dấu
 * Auditor 'none' cho quyền đó, giống Manager) — dùng thẳng `ctx.role` để tách ba nhánh `full`/`log`/`none`,
 * đúng cách `gh.biz.people.routes._access_mode` làm ở backend thật.
 */
import type {
  CareIssueRow,
  CareResponseRow,
  CareScenarioItem,
  DisputeStatus,
  Explain,
  EvidenceRef,
  PeopleReviewDetail,
  PeopleReviewDisputeItem,
  PeopleReviewFull,
  PeopleReviewFullDetail,
  PeopleReviewHistoryItem,
  PeopleReviewItem,
  PeopleReviewLogItem,
  PersonRef,
  ReviewAccessMode,
  ReviewBoard,
  ReviewViewedEntry,
  Trend,
  UserRef,
} from '@gen-harness/contracts';
import { BAO, registerExplain, sampleUnit } from './mock-p3-core';
import type { P2Ctx } from './mock-phase2';

export interface P3Options {
  fresh: boolean;
  emit: (type: string, data: unknown) => void;
}

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();
const has = (ctx: P2Ctx, perm: string) => !!ctx.perms[perm] && ctx.perms[perm] !== 'none';

/** Như `gh.biz.people.routes._access_mode`: `people_review.read` khác `none` → `full` (Owner mặc định; vai trò khác
 * khi Owner cấp trong Quyền hạn); auditor → `log`; còn lại (manager/operator/agent_staff mặc định) → `none` (403). */
function accessMode(ctx: P2Ctx): ReviewAccessMode {
  if (ctx.role === 'owner' || has(ctx, 'people_review.read')) return 'full';
  if (ctx.role === 'auditor') return 'log';
  return 'none';
}

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}
/** Kỳ 7 ngày kết thúc hôm qua, lùi `weeksAgo` tuần — cùng nhịp `recompute_people_reviews_org`. */
function periodOf(weeksAgo: number): { period_start: string; period_end: string } {
  const end = new Date();
  end.setUTCDate(end.getUTCDate() - weeksAgo * 7 - 1);
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - 6);
  return { period_start: isoDate(start), period_end: isoDate(end) };
}

// ─── đối tượng dùng chung ───────────────────────────────────────────────────
const HA: PersonRef = { id: 'p-rv-ha', code: 'PER-2201', name: 'Nguyễn Thu Hà', type: 'staff', org_name: null };
const KHOA: PersonRef = { id: 'p-rv-khoa', code: 'PER-2202', name: 'Trần Minh Khoa', type: 'staff', org_name: null };
const DANG: PersonRef = { id: 'p-rv-dang', code: 'PER-2203', name: 'Vũ Hải Đăng', type: 'staff', org_name: null };
const MAI: PersonRef = { id: 'p-rv-mai', code: 'PER-2204', name: 'Đỗ Thanh Mai', type: 'staff', org_name: null };
const TU: PersonRef = { id: 'p-rv-tu', code: 'PER-2205', name: 'Phạm Anh Tú', type: 'staff', org_name: null };

const NGOC: PersonRef = { id: 'p-rv-ngoc', code: 'PER-2301', name: 'Nguyễn Thị Ngọc', type: 'customer', org_name: 'Nội thất Ngọc Lan' };
const HANG: PersonRef = { id: 'p-rv-hang', code: 'PER-2302', name: 'Đặng Thu Hằng', type: 'customer', org_name: 'Xưởng may Thu Hằng' };
const SANG: PersonRef = { id: 'p-rv-sang', code: 'PER-2401', name: 'Lê Văn Sáng', type: 'candidate', org_name: null };
const CHAU: PersonRef = { id: 'p-rv-chau', code: 'PER-2501', name: 'Trần Bảo Châu', type: 'learner', org_name: null };

// ─── Đánh giá con người ───────────────────────────────────────────────────────
interface ReviewRow {
  id: string;
  score: number;
  trend: Trend | null;
  signal: string;
  recommendation: string;
  evidence: EvidenceRef[];
  visibility: 'owner';
  created_at: string;
  overridden: boolean;
  overridden_by: UserRef | null;
  overridden_at: string | null;
  override_reason: string | null;
  supersedes_id: string | null;
  /** v0.1.45 (F-60): cờ 'Đáng ngờ' — sửa tay giữ nguyên cờ của dòng bị thay. */
  suspicious: boolean;
  suspicious_reason: string | null;
  suspicious_cleared?: { by: UserRef | null; at: string; reason: string } | null;
}
interface Lineage {
  board: ReviewBoard;
  person: PersonRef;
  period_start: string;
  period_end: string;
  /** Mới nhất trước — `rows[0]` là dòng hiện hành. */
  rows: ReviewRow[];
  disputes: PeopleReviewDisputeItem[];
  viewedBy: ReviewViewedEntry[];
}

function fullItem(l: Lineage): PeopleReviewFull {
  const r = l.rows[0];
  return {
    id: r.id,
    person: l.person,
    period_start: l.period_start,
    period_end: l.period_end,
    score: r.score,
    trend: r.trend,
    signal: r.signal,
    recommendation: r.recommendation,
    evidence: r.evidence,
    visibility: r.visibility,
    created_at: r.created_at,
    overridden: r.overridden,
    overridden_by: r.overridden_by,
    overridden_at: r.overridden_at,
    override_reason: r.override_reason,
    supersedes_id: r.supersedes_id,
    suspicious: r.suspicious && !r.suspicious_cleared,
    suspicious_reason: r.suspicious_reason,
    suspicious_cleared: r.suspicious_cleared ?? null,
  };
}
function fullDetail(l: Lineage): PeopleReviewFullDetail {
  const history: PeopleReviewHistoryItem[] = l.rows.map((r) => ({
    id: r.id,
    score: r.score,
    trend: r.trend,
    created_at: r.created_at,
    overridden_by: r.overridden_by,
    override_reason: r.override_reason,
  }));
  return { ...fullItem(l), history, disputes: l.disputes };
}
function logItem(l: Lineage): PeopleReviewLogItem {
  const r = l.rows[0];
  return {
    id: r.id,
    person: l.person,
    period_start: l.period_start,
    period_end: l.period_end,
    created_at: r.created_at,
    has_content: true,
    dispute_count: l.disputes.length,
    viewed_by: l.viewedBy.slice(0, 10),
  };
}
function recordViewed(l: Lineage, ctx: P2Ctx, action: ReviewViewedEntry['action']) {
  l.viewedBy = [{ at: new Date().toISOString(), user: { id: 'self', name: ctx.userLabel, role: ctx.role }, action }, ...l.viewedBy].slice(0, 10);
}

const TREND_LABEL: Record<string, string> = { up: 'lên', down: 'xuống', flat: 'đi ngang' };
function buildExplain(l: Lineage): Explain {
  const r = l.rows[0];
  const u = sampleUnit(1);
  return {
    kind: 'review',
    id: r.id,
    title: `${l.person.name} — đánh giá ${l.period_start} → ${l.period_end}`,
    statement: `${r.score}/100${r.trend ? ` · xu hướng ${TREND_LABEL[r.trend] ?? r.trend}` : ''}`,
    method: r.overridden ? 'manual' : 'rules+model',
    factors: [{ label: r.signal, value: r.score, evidence: r.evidence }],
    units: [u],
    history: l.rows.map((x) => ({ value: x.score, computed_at: x.created_at, method: x.overridden ? 'manual' : 'rules+model', by: x.overridden_by })),
  };
}

let reviewSeq = 0;
let disputeSeq = 0;

function seedLineages(): Lineage[] {
  reviewSeq = 0;
  disputeSeq = 0; // Reset cả hai mỗi lần mock reset (test hook __mock/reset) — ID ổn định qua các lượt test.
  const mk = (
    board: ReviewBoard,
    person: PersonRef,
    score: number,
    trend: Trend | null,
    signal: string,
    recommendation: string,
    evidenceIdx: number[],
    viewedSeed: ReviewViewedEntry[] = [],
  ): Lineage => {
    const period = periodOf(0);
    reviewSeq += 1;
    const row: ReviewRow = {
      id: `rev-${reviewSeq}`,
      score,
      trend,
      signal,
      recommendation,
      evidence: evidenceIdx.map((i) => ({ type: 'meaning_unit', id: `mu-${i}`, code: null, label: null })),
      visibility: 'owner',
      created_at: ago(60 * 20),
      overridden: false,
      overridden_by: null,
      overridden_at: null,
      override_reason: null,
      supersedes_id: null,
      suspicious: false,
      suspicious_reason: null,
    };
    return { board, person, ...period, rows: [row], disputes: [], viewedBy: viewedSeed };
  };
  const OWNER_REF: UserRef = { id: 'u-owner-seed', name: 'Anh Nguyễn Văn A (Chủ)', role: 'owner' };
  const lineages: Lineage[] = [
    mk(
      'employee', HA, 62, 'down',
      'Trung vị phản hồi tăng từ 12 lên 84 phút. Ba khách nhắc lại mà chưa được trả lời, trong đó có Thành Phát 87 điểm rủi ro.',
      'Giảm tải: chuyển 4 khách sang Đỗ Thanh Mai. Đây là dấu hiệu quá tải, không phải thờ ơ.',
      [1, 2],
      [{ at: ago(60 * 30), user: OWNER_REF, action: 'people_review.viewed' }],
    ),
    mk(
      'employee', KHOA, 71, 'flat',
      'Chất lượng chăm sóc tốt nhưng đang là cầu nối của 9 nhóm và ôm 14 quan hệ — rủi ro tập trung một người.',
      'Phân bổ lại 3 quan hệ đối tác cho Vũ Hải Đăng để giảm rủi ro phụ thuộc.',
      [0],
    ),
    mk(
      'employee', DANG, 84, 'up',
      'Tỷ lệ follow sau báo giá đạt 92%, cao nhất tổ chức. Hai khách lạnh được làm ấm lại trong tuần.',
      'Lấy kịch bản follow của người này làm mẫu chuẩn cho ban kinh doanh.',
      [0, 3],
    ),
    mk(
      'employee', MAI, 78, 'up',
      'Phản hồi đều trong giờ hành chính, không có tin nào quá 30 phút. Ngoài giờ không trả lời — phù hợp giờ im lặng.',
      'Còn dư tải, có thể nhận thêm 3–4 khách từ Ban Tài chính.',
      [2],
    ),
    mk(
      'employee', TU, 54, 'down',
      'Hai lần hứa mốc giao hàng rồi không cập nhật lại. Một khách phải tự hỏi tiến độ ba lần.',
      'Bật nhắc tự động cho mọi lời hứa có mốc thời gian của người này.',
      [1, 4],
    ),
    mk('customer', NGOC, 88, 'up', 'Khách phản hồi nhanh, chủ động giới thiệu thêm 2 khách mới trong tháng.', 'Ưu tiên giữ ấm mối quan hệ, mời tham gia chương trình khách thân thiết.', [0]),
    mk('candidate', SANG, 76, null, 'Phỏng vấn vòng 2 phản hồi tốt, đúng hẹn, hỏi kỹ về lộ trình phát triển.', 'Chuyển hồ sơ sang vòng thương lượng lương.', [3]),
    mk('student', CHAU, 69, 'flat', 'Hoàn thành 4/5 bài tập đúng hạn, một bài nộp trễ không báo trước.', 'Nhắc quy định báo trễ trước buổi học kế tiếp.', [2]),
  ];
  // F-60: một dòng bị gắn cờ 'Đáng ngờ' (nhân viên chèn câu xin điểm vào tin nhắn).
  const tuRow = lineages.find((l) => l.person.id === TU.id)?.rows[0];
  if (tuRow) {
    tuRow.suspicious = true;
    tuRow.suspicious_reason = 'Có 2 tin giống lệnh cho AI hoặc xin điểm (vd: “Bỏ qua mọi chỉ dẫn”) — kiểm tra chứng cứ trước khi dùng điểm này.';
  }
  // Phản biện mẫu: Anh Tú không đồng ý với điểm, Owner đã mở phản biện hộ để có luồng test "mở/giải quyết".
  disputeSeq += 1;
  const tuLineage = lineages.find((l) => l.person.id === TU.id)!;
  tuLineage.disputes = [
    {
      id: `disp-${disputeSeq}`,
      review_id: tuLineage.rows[0].id,
      raised_by: OWNER_REF,
      body: 'Anh Tú cho rằng khách chủ động im lặng chờ đối tác duyệt ngân sách, không phải do anh chậm cập nhật — cần xem lại trước khi trừ điểm.',
      status: 'open',
      resolution: null,
      resolved_by: null,
      resolved_at: null,
      created_at: ago(60 * 6),
    },
  ];
  return lineages;
}

// ─── Chất lượng chăm sóc ──────────────────────────────────────────────────────
/** Khung giờ 08–22h · trung vị phút mỗi nhân viên, lấy cảm hứng từ seed-data.json careGrid. */
const RESPONSE_MINUTES: Array<{ staff: PersonRef; minutes: number[] }> = [
  { staff: HA, minutes: [14, 22, 38, 84, 96, 62] },
  { staff: KHOA, minutes: [8, 11, 24, 18, 22, 26, 34, 41] },
  { staff: DANG, minutes: [6, 9, 14, 12, 11, 16] },
  { staff: MAI, minutes: [11, 13, 21, 19, 24] },
  { staff: TU, minutes: [18, 26, 44, 51, 68, 72] },
];
function classify(minutes: number[]): { fast: number; normal: number; slow: number; avg: number } {
  let fast = 0;
  let normal = 0;
  let slow = 0;
  for (const m of minutes) {
    if (m < 15) fast += 1;
    else if (m <= 60) normal += 1;
    else slow += 1;
  }
  const avg = minutes.length ? minutes.reduce((s, m) => s + m, 0) / minutes.length : 0;
  return { fast, normal, slow, avg };
}
function buildResponseTimes(personId: string | null): { items: CareResponseRow[]; totals: { fast: number; normal: number; slow: number; total_answered: number; fast_pct: number } } {
  const rows = RESPONSE_MINUTES.filter((r) => !personId || r.staff.id === personId);
  const items: CareResponseRow[] = rows.map((r) => {
    const { fast, normal, slow, avg } = classify(r.minutes);
    const total = r.minutes.length;
    return { staff: r.staff, fast, normal, slow, total_answered: total, fast_pct: total ? Math.round((fast / total) * 1000) / 10 : 0, avg_minutes: Math.round(avg * 10) / 10 };
  });
  const totals = items.reduce(
    (acc, r) => ({ fast: acc.fast + r.fast, normal: acc.normal + r.normal, slow: acc.slow + r.slow, total_answered: acc.total_answered + r.total_answered }),
    { fast: 0, normal: 0, slow: 0, total_answered: 0 },
  );
  return { items, totals: { ...totals, fast_pct: totals.total_answered ? Math.round((totals.fast / totals.total_answered) * 1000) / 10 : 0 } };
}

function seedIssues(): CareIssueRow[] {
  return [
    { kind: 'broken_promise', subject: TU, count: 2, repeated: true, last_at: ago(60 * 24 * 3) },
    { kind: 'broken_promise', subject: HA, count: 1, repeated: false, last_at: ago(60 * 24 * 10) },
    { kind: 'abandoned_customer', subject: NGOC, count: 3, repeated: true, last_at: ago(60 * 24 * 5) },
    { kind: 'abandoned_customer', subject: HANG, count: 1, repeated: false, last_at: ago(60 * 24 * 20) },
  ];
}

function seedScenarios(): CareScenarioItem[] {
  return [
    {
      deal: { id: 'care-deal-1', code: 'DEA-0201', amount_vnd: 180_000_000, status: 'won', won_at: ago(60 * 24 * 4), opportunity_id: null },
      person: NGOC,
      response: { fast: 5, normal: 1, slow: 0, unanswered: 0, fast_pct: 83.3, avg_minutes: 9.4 },
      broken_promises: 0,
      note: 'Kịch bản thắng: phản hồi nhanh 83%, không có lời hứa bị vỡ.',
    },
    {
      deal: { id: 'care-deal-2', code: 'DEA-0202', amount_vnd: 64_000_000, status: 'won', won_at: ago(60 * 24 * 9), opportunity_id: null },
      person: BAO,
      response: { fast: 4, normal: 2, slow: 0, unanswered: 0, fast_pct: 66.7, avg_minutes: 14.1 },
      broken_promises: 0,
      note: 'Kịch bản thắng: báo giá kèm mốc giao cụ thể, follow đúng ngày đã hứa.',
    },
    {
      deal: { id: 'care-deal-3', code: 'DEA-0203', amount_vnd: 96_000_000, status: 'lost', won_at: null, opportunity_id: null },
      person: HANG,
      response: { fast: 0, normal: 1, slow: 3, unanswered: 1, fast_pct: 0, avg_minutes: 78.5 },
      broken_promises: 1,
      note: 'Kịch bản mất khách: trả lời sau hơn 60 phút ba lần liên tiếp, một lời hứa bị vỡ.',
    },
    {
      deal: { id: 'care-deal-4', code: 'DEA-0204', amount_vnd: 152_000_000, status: 'lost', won_at: null, opportunity_id: null },
      person: null,
      response: { fast: 1, normal: 1, slow: 1, unanswered: 2, fast_pct: 25, avg_minutes: 52.0 },
      broken_promises: 2,
      note: 'Kịch bản mất khách: để khách tự hỏi tiến độ từ lần thứ hai trở đi.',
    },
  ];
}

export function createMock(opts: P3Options) {
  let lineages: Lineage[] = opts.fresh ? [] : seedLineages();
  const reviewIndex = new Map<string, Lineage>();
  const disputeIndex = new Map<string, Lineage>();
  for (const l of lineages) {
    reviewIndex.set(l.rows[0].id, l);
    for (const d of l.disputes) disputeIndex.set(d.id, l);
  }
  const issues: CareIssueRow[] = opts.fresh ? [] : seedIssues();
  const scenarios: CareScenarioItem[] = opts.fresh ? [] : seedScenarios();

  registerExplain('review', (id) => {
    const l = reviewIndex.get(id);
    return l ? buildExplain(l) : null;
  });

  function reindex(oldId: string, l: Lineage) {
    reviewIndex.delete(oldId);
    reviewIndex.set(l.rows[0].id, l);
  }

  function overrideReview(l: Lineage, ctx: P2Ctx, b: { score: number; reason: string; evidence: PeopleReviewFull['evidence']; trend?: Trend | null; signal?: string; recommendation?: string }) {
    const prev = l.rows[0];
    const row: ReviewRow = {
      id: `rev-ov-${++reviewSeq}-${Date.now()}`,
      score: Math.max(0, Math.min(100, b.score)),
      trend: b.trend !== undefined ? b.trend : prev.trend,
      signal: b.signal ?? prev.signal,
      recommendation: b.recommendation ?? prev.recommendation,
      evidence: b.evidence,
      visibility: 'owner',
      created_at: new Date().toISOString(),
      overridden: true,
      overridden_by: { id: 'self', name: ctx.userLabel, role: ctx.role },
      overridden_at: new Date().toISOString(),
      override_reason: b.reason,
      supersedes_id: prev.id,
      suspicious: prev.suspicious,
      suspicious_reason: prev.suspicious_reason,
      suspicious_cleared: prev.suspicious_cleared ?? null,
    };
    const oldId = prev.id;
    l.rows = [row, ...l.rows];
    reindex(oldId, l);
  }

  function handle(ctx: P2Ctx): boolean {
    const { method: m, path: p, url, body, reply, problem } = ctx;
    const seg = p.split('/').filter(Boolean);

    // ── chứng cứ gốc của một đánh giá — chỉ nhánh full (Q4) ──
    if (seg[0] === 'explain' && seg[1] === 'review' && seg.length === 3 && m === 'GET') {
      const mode = accessMode(ctx);
      if (mode !== 'full') return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (ctx.needPin()) return problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN', { detail: { operation: 'people_review.read' } });
      const l = reviewIndex.get(decodeURIComponent(seg[2]));
      if (!l) return problem(404, 'NOT_FOUND', 'Đánh giá không tồn tại hoặc ngoài phạm vi của bạn');
      recordViewed(l, ctx, 'people_review.explained');
      return reply(200, buildExplain(l));
    }

    // ── Đánh giá con người + Phản biện ──
    if (seg[0] === 'people' && seg[1] === 'reviews') {
      const mode = accessMode(ctx);
      if (mode === 'none') return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');

      if (seg.length === 2 && m === 'GET') {
        if (mode === 'full' && ctx.needPin()) return problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN', { detail: { operation: 'people_review.read' } });
        const board = url.searchParams.get('board');
        const personId = url.searchParams.get('person_id');
        const periodStart = url.searchParams.get('period_start');
        const periodEnd = url.searchParams.get('period_end');
        let rows = lineages;
        if (board) rows = rows.filter((l) => l.board === board);
        if (personId) rows = rows.filter((l) => l.person.id === personId);
        if (periodStart) rows = rows.filter((l) => l.period_start === periodStart);
        if (periodEnd) rows = rows.filter((l) => l.period_end === periodEnd);
        if (mode === 'log') for (const l of rows) recordViewed(l, ctx, 'people_review.audit_viewed');
        const items: PeopleReviewItem[] = rows.map((l) => (mode === 'full' ? fullItem(l) : logItem(l)));
        return reply(200, { items, next_cursor: null, total: items.length });
      }

      // PATCH /people/reviews/disputes/{id} — giải quyết phản biện
      if (seg.length === 4 && seg[2] === 'disputes' && m === 'PATCH') {
        if (!has(ctx, 'people_review.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        if (ctx.needPin()) return problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN', { detail: { operation: 'people_review.write' } });
        const l = disputeIndex.get(seg[3]);
        const d = l?.disputes.find((x) => x.id === seg[3]);
        if (!l || !d) return problem(404, 'NOT_FOUND', 'Phản biện không tồn tại hoặc ngoài phạm vi của bạn');
        if (d.status !== 'open') return problem(409, 'DISPUTE_DECIDED', 'Phản biện này đã được xử lý');
        const b = body as { status?: DisputeStatus; resolution?: string };
        if (b.status !== 'resolved' && b.status !== 'rejected') {
          return problem(422, 'VALIDATION', 'Chỉ nhận resolved hoặc rejected', { errors: { status: 'Không hợp lệ' } });
        }
        d.status = b.status;
        d.resolution = b.resolution ?? null;
        d.resolved_by = { id: 'self', name: ctx.userLabel, role: ctx.role };
        d.resolved_at = new Date().toISOString();
        return reply(200, d);
      }

      // PATCH /people/reviews/{id}/suspicious — bỏ cờ 'Đáng ngờ' (F-60, cần PIN, bắt buộc lý do)
      if (seg.length === 4 && seg[3] === 'suspicious' && m === 'PATCH') {
        if (!has(ctx, 'people_review.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        if (ctx.needPin()) return problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN', { detail: { operation: 'people_review.read' } });
        const l = reviewIndex.get(seg[2]);
        if (!l) return problem(404, 'NOT_FOUND', 'Đánh giá không tồn tại hoặc ngoài phạm vi của bạn');
        const b = body as { cleared_reason?: string };
        if ((b.cleared_reason ?? '').trim().length < 3) {
          return problem(422, 'VALIDATION', 'Dữ liệu không hợp lệ', { errors: { cleared_reason: 'Ghi rõ lý do bỏ cờ' } });
        }
        const row = l.rows[0];
        if (!row.suspicious || row.suspicious_cleared) return problem(409, 'NOT_SUSPICIOUS', "Đánh giá này không còn cờ 'Đáng ngờ' để bỏ");
        row.suspicious_cleared = { by: { id: 'self', name: ctx.userLabel, role: ctx.role }, at: new Date().toISOString(), reason: (b.cleared_reason ?? '').trim() };
        return reply(200, fullDetail(l));
      }

      // POST /people/reviews/{id}/disputes — mở phản biện
      if (seg.length === 4 && seg[3] === 'disputes' && m === 'POST') {
        if (!has(ctx, 'people_review.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        if (ctx.needPin()) return problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN', { detail: { operation: 'people_review.write' } });
        const l = reviewIndex.get(seg[2]);
        if (!l) return problem(404, 'NOT_FOUND', 'Đánh giá không tồn tại hoặc ngoài phạm vi của bạn');
        const b = body as { body?: string };
        if (!b.body?.trim()) return problem(422, 'VALIDATION', 'Cần nội dung phản biện', { errors: { body: 'Không được để trống' } });
        disputeSeq += 1;
        const d: PeopleReviewDisputeItem = {
          id: `disp-${disputeSeq}`,
          review_id: l.rows[0].id,
          raised_by: { id: 'self', name: ctx.userLabel, role: ctx.role },
          body: b.body,
          status: 'open',
          resolution: null,
          resolved_by: null,
          resolved_at: null,
          created_at: new Date().toISOString(),
        };
        l.disputes = [d, ...l.disputes];
        disputeIndex.set(d.id, l);
        return reply(201, d);
      }

      const l = seg.length === 3 ? reviewIndex.get(seg[2]) : undefined;

      // GET /people/reviews/{id}
      if (seg.length === 3 && m === 'GET') {
        if (!l) return problem(404, 'NOT_FOUND', 'Đánh giá không tồn tại hoặc ngoài phạm vi của bạn');
        if (mode === 'full') {
          if (ctx.needPin()) return problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN', { detail: { operation: 'people_review.read' } });
          recordViewed(l, ctx, 'people_review.viewed');
          return reply(200, fullDetail(l) satisfies PeopleReviewDetail);
        }
        recordViewed(l, ctx, 'people_review.audit_viewed');
        return reply(200, logItem(l) satisfies PeopleReviewDetail);
      }

      // PATCH /people/reviews/{id} — sửa điểm tay, giữ lịch sử
      if (seg.length === 3 && m === 'PATCH') {
        if (!has(ctx, 'people_review.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        if (ctx.needPin()) return problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN', { detail: { operation: 'people_review.write' } });
        if (!l) return problem(404, 'NOT_FOUND', 'Đánh giá không tồn tại hoặc ngoài phạm vi của bạn');
        const b = body as { score?: number; reason?: string; evidence?: PeopleReviewFull['evidence']; trend?: Trend | null; signal?: string; recommendation?: string };
        const errors: Record<string, string> = {};
        if (typeof b.score !== 'number') errors.score = 'Cần điểm số.';
        if (!b.reason?.trim()) errors.reason = 'Cần lý do sửa điểm.';
        if (!b.evidence?.length) errors.evidence = 'Cần ít nhất một chứng cứ.';
        if (Object.keys(errors).length) return problem(422, 'VALIDATION', 'Dữ liệu chưa hợp lệ', { errors });
        overrideReview(l, ctx, { score: b.score!, reason: b.reason!, evidence: b.evidence!, trend: b.trend, signal: b.signal, recommendation: b.recommendation });
        return reply(200, fullDetail(l));
      }
    }

    // ── Chất lượng chăm sóc ──
    if (seg[0] === 'care') {
      if (!has(ctx, 'care.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');

      if (seg.length === 2 && seg[1] === 'response-times' && m === 'GET') {
        const personId = url.searchParams.get('person_id');
        const { items, totals } = buildResponseTimes(personId);
        return reply(200, { from: ago(60 * 24 * 30), to: new Date().toISOString(), items, totals, unattended: personId ? 0 : 3 });
      }
      if (seg.length === 2 && seg[1] === 'repeated-issues' && m === 'GET') {
        const kind = url.searchParams.get('issue_type');
        const limit = Number(url.searchParams.get('limit') ?? 50);
        const rows = (kind ? issues.filter((i) => i.kind === kind) : issues).slice(0, limit);
        return reply(200, { items: rows, next_cursor: null, total: rows.length });
      }
      if (seg.length === 2 && seg[1] === 'scenarios' && m === 'GET') {
        const status = url.searchParams.get('status');
        const limit = Number(url.searchParams.get('limit') ?? 50);
        const rows = (status ? scenarios.filter((s) => s.deal.status === status) : scenarios).slice(0, limit);
        const side = (st: 'won' | 'lost') => {
          const xs = rows.filter((s) => s.deal.status === st);
          const n = xs.length;
          return {
            count: n,
            avg_fast_pct: n ? Math.round((xs.reduce((s, x) => s + (x.response?.fast_pct ?? 0), 0) / n) * 10) / 10 : 0,
            avg_broken_promises: n ? Math.round((xs.reduce((s, x) => s + x.broken_promises, 0) / n) * 10) / 10 : 0,
          };
        };
        return reply(200, { items: rows, next_cursor: null, total: rows.length, summary: { won: side('won'), lost: side('lost') } });
      }
    }

    return false;
  }

  return {
    handle,
    hooks: {
      reviews: () => lineages,
      resetReviews: () => {
        lineages = seedLineages();
        reviewIndex.clear();
        disputeIndex.clear();
        for (const l of lineages) {
          reviewIndex.set(l.rows[0].id, l);
          for (const d of l.disputes) disputeIndex.set(d.id, l);
        }
      },
      issues: () => issues,
      scenarios: () => scenarios,
    } as Record<string, (...args: never[]) => unknown>,
    dispose: () => {},
  };
}
