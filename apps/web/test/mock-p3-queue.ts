/**
 * Mock API giai đoạn 3 · Hàng đợi & Hành động (docs/api/phase-3-queue.md): Tổng quan, Hộp thư ý nghĩa,
 * Việc & Nhắc hẹn. `handle` trả true khi đã trả lời request. Dữ liệu mẫu lấy từ docs/design/seed-data.json
 * (meaningItems, queue, spotlight, signals, kpis) đúng cách mock-p3-core.ts đã làm cho cụm nền chung.
 *
 * Bàn làm việc (giao diện duyệt) đã có đủ ở mock-p3-core.ts (`/drafts`) — cụm này không lặp lại.
 */
import type {
  GroupRef,
  JevBenchmark,
  SkippedItem,
  InboxDetail,
  InboxItem,
  InboxTab,
  Overview,
  PersonRef,
  Promise as PromiseItem,
  Task,
} from '@gen-harness/contracts';
import { BAO, GROUP_TP, registerExplain } from './mock-p3-core';
import { AGENT_IDS, USER_IDS, rejectNonUuid } from './mock-ids';
import { maskText, type P2Ctx } from './mock-phase2';

export interface P3Options {
  fresh: boolean;
  emit: (type: string, data: unknown) => void;
  /** v0.1.35: người dùng đang hoạt động theo id (mock-api `users`) — giao việc kiểm như API (UUID lạ → 404). */
  findUser?: (id: string) => { id: string; display_name: string } | undefined;
  /** v0.1.55 (G4): `POST /jev/enable` thành công ⇒ mock-api thêm nguồn Jev (system_one) vào danh sách `/providers`. */
  onJevEnabled?: (r: { provider_id: string; endpoint: string; model: string; key_tail: string }) => void;
}

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();
const inMin = (min: number) => new Date(Date.now() + min * 60_000).toISOString();

// ─── đối tượng dùng chung ───────────────────────────────────────────────────
const GROUP_GO: GroupRef = { id: 'g-nganhgo', code: 'GRP-ZL-0231', name: 'Group Ngành gỗ Miền Nam', channel: 'zalo' };
const GROUP_NS: GroupRef = { id: 'g-nhansu', code: 'GRP-ZL-0356', name: 'Group Nhân sự Logistics Miền Nam', channel: 'zalo' };
const HA: PersonRef = { id: 'p-ha', code: 'PER-0007', name: 'Nguyễn Thu Hà', type: 'staff', org_name: null };
const CANDIDATE: PersonRef = { id: 'p-sang', code: 'PER-0733', name: 'Bùi Ngọc Anh', type: 'candidate', org_name: null };
const KHANG: PersonRef = { id: 'p-khang', code: 'PER-0402', name: 'Lê Minh Tâm', type: 'customer', org_name: 'An Khang Logistics' };

interface InboxRow extends InboxItem {
  units?: InboxDetail['units'];
  status?: string;
  kind?: string;
}

function seedInboxItems(): InboxRow[] {
  return [
    {
      id: 'iq-opp-1', code: 'OPP-1842', item_type: 'unit', tab: 'opportunity',
      title: 'Hỏi giá', summary: 'Xưởng gỗ Bình Dương cần 3 container ván MDF loại E1, giao trong tháng 10.',
      priority: 'P1', created_at: ago(18), score: 0.91, confidence_band: 'cao',
      subject: GROUP_GO, group: GROUP_GO, agent: null,
      alert_type: null, alert_type_label: null, suggested_action: 'Nhận và ráp khớp nhà cung cấp',
      triage: { duplicate_of: null, duplicate_kind: null, spam: false, spam_reason: null, score: 88, low_score: false, reason: 'độ tin 0.91 · có product, qty · cơ hội bán hàng', source: 'heuristic' },
    },
    {
      id: 'iq-alert-1', code: 'ALR-0233', item_type: 'alert', tab: 'alert',
      title: 'Khách đang lạnh / sắp mất', summary: 'Khách nhắn ba lần chưa ai trả lời, thái độ đã chuyển sang gay gắt.',
      priority: 'P1', created_at: ago(134), score: null, confidence_band: null,
      subject: BAO, group: GROUP_TP, agent: null,
      alert_type: 'customer_cooling', alert_type_label: 'Khách đang lạnh / sắp mất',
      suggested_action: 'Mở hồ sơ và gán người xử lý',
      status: 'open',
    },
    {
      id: 'draft-ACT-0231', code: 'ACT-0231', item_type: 'draft', tab: 'approval',
      title: 'Báo giá', summary: 'Bản nháp báo giá 84.000.000 ₫ cho hợp đồng in ấn quý 4 đã soạn xong.',
      priority: 'P1', created_at: ago(47), score: null, confidence_band: null,
      subject: BAO, group: GROUP_TP, agent: { id: AGENT_IDS.tls, name: 'Trợ lý thương mại' },
      alert_type: null, alert_type_label: null, suggested_action: 'Xem bản nháp và duyệt',
      status: 'pending', kind: 'quotation',
    },
    {
      id: 'iq-alert-2', code: 'ALR-0234', item_type: 'alert', tab: 'alert',
      title: 'Phản hồi chậm bất thường', summary: 'Nguyễn Thu Hà phản hồi chậm bất thường ba ngày liên tiếp.',
      priority: 'P2', created_at: ago(300), score: null, confidence_band: null,
      subject: HA, group: null, agent: null,
      alert_type: 'slow_response', alert_type_label: 'Phản hồi chậm bất thường',
      suggested_action: 'Xem chứng cứ và đề xuất coaching',
      status: 'open',
    },
    {
      id: 'iq-cand-1', code: 'OPP-1839', item_type: 'unit', tab: 'candidate',
      title: 'RequestedPartnership', summary: 'Một người trong group khớp vị trí Key Account đang trống của Ban Kinh doanh.',
      priority: 'P2', created_at: ago(360), score: 0.74, confidence_band: 'thấp',
      subject: CANDIDATE, group: GROUP_NS, agent: null,
      alert_type: null, alert_type_label: null, suggested_action: 'Mở hồ sơ ứng viên',
    },
    {
      id: 'iq-reply-1', code: null, item_type: 'unit', tab: 'reply',
      title: 'Than phiền', summary: 'Khách hỏi lại tiến độ giao hàng lần thứ hai trong tuần.',
      priority: 'P2', created_at: ago(52), score: 0.82, confidence_band: 'cao',
      subject: KHANG, group: null, agent: null,
      alert_type: null, alert_type_label: null, suggested_action: 'Soạn phản hồi tiến độ giao hàng',
    },
    {
      id: 'iq-reply-2', code: null, item_type: 'unit', tab: 'reply',
      title: 'Đã gửi báo giá', summary: 'Báo giá đã gửi cho An Khang Logistics, chưa có phản hồi sau 2 ngày.',
      priority: 'P3', created_at: ago(180), score: 0.68, confidence_band: 'trung bình',
      subject: KHANG, group: null, agent: null,
      alert_type: null, alert_type_label: null, suggested_action: 'Soạn tin nhắc lại báo giá',
    },
    {
      id: 'iq-opp-2', code: null, item_type: 'unit', tab: 'opportunity',
      title: 'Chào bán', summary: 'Một nhà cung cấp mới chào giá ván ép rẻ hơn 6% trong nhóm ngành gỗ.',
      priority: 'P3', created_at: ago(210), score: 0.58, confidence_band: 'trung bình',
      subject: GROUP_GO, group: GROUP_GO, agent: null,
      alert_type: null, alert_type_label: null, suggested_action: 'Đối chiếu với nhà cung cấp hiện tại',
      triage: { duplicate_of: 'iq-opp-1', duplicate_kind: 'near', spam: true, spam_reason: 'có đường link; từ ngữ quảng cáo (khuyen mai)', score: 8, low_score: true, reason: 'Rác: có đường link; từ ngữ quảng cáo (khuyen mai)', source: 'heuristic' },
    },
    {
      id: 'draft-ACT-0234', code: 'ACT-0234', item_type: 'draft', tab: 'approval',
      title: 'Hợp đồng', summary: 'Biên bản đàm phán tuyến lạnh An Khang vòng ba đã soạn xong.',
      priority: 'P2', created_at: ago(120), score: null, confidence_band: null,
      subject: KHANG, group: null, agent: { id: AGENT_IDS.hc, name: 'Admin hậu cần' },
      alert_type: null, alert_type_label: null, suggested_action: 'Xem bản nháp và duyệt',
      status: 'pending', kind: 'contract',
    },
  ];
}

function seedTasks(): Task[] {
  return [
    {
      id: 't-412', code: 'TSK-0412', title: 'Lịch giao ban thứ Hai chưa có nội dung, hệ thống đã soạn nháp',
      priority: 'P3', status: 'todo', assignee: { id: USER_IDS.owner, name: 'Nhóm Điều hành' },
      subject: null, due_at: inMin(24 * 60), remind_at: null, overdue: false,
      source: 'manual', created_at: ago(24 * 60), completed_at: null,
    },
    {
      id: 't-410', code: 'TSK-0410', title: 'Gửi hợp đồng in ấn quý 4 đã ký cho Thành Phát',
      priority: 'P1', status: 'doing', assignee: { id: USER_IDS.owner, name: 'Anh Nguyễn Văn A (Chủ)' },
      subject: BAO, due_at: ago(90), remind_at: null, overdue: true,
      source: 'draft', created_at: ago(300), completed_at: null,
    },
    {
      id: 't-408', code: 'TSK-0408', title: 'Theo dõi phản hồi báo giá An Khang Logistics',
      priority: 'P2', status: 'todo', assignee: null,
      subject: KHANG, due_at: inMin(120), remind_at: inMin(60), overdue: false,
      source: 'promise', created_at: ago(600), completed_at: null,
    },
    {
      id: 't-406', code: 'TSK-0406', title: 'Đã gọi xác nhận đơn hàng với Thành Phát',
      priority: 'P3', status: 'done', assignee: { id: USER_IDS.owner, name: 'Anh Nguyễn Văn A (Chủ)' },
      subject: BAO, due_at: ago(1440), remind_at: null, overdue: false,
      source: 'manual', created_at: ago(2000), completed_at: ago(1000),
    },
  ];
}

function seedPromises(): PromiseItem[] {
  return [
    {
      id: 'pr-1', text: 'Gửi mẫu vải trước thứ Sáu cho Thành Phát', due_at: inMin(60 * 20), kept_at: null, broken: false,
      from: BAO, to: null, evidence: { type: 'meaning_unit', id: 'mu-1' },
    },
    {
      id: 'pr-2', text: 'Gọi lại xác nhận giá cho An Khang trong hôm nay', due_at: ago(180), kept_at: null, broken: true,
      from: KHANG, to: null, evidence: { type: 'meaning_unit', id: 'mu-2' },
    },
    {
      id: 'pr-3', text: 'Phản hồi hồ sơ ứng viên trong tuần này', due_at: ago(2000), kept_at: ago(500), broken: false,
      from: CANDIDATE, to: null, evidence: null,
    },
  ];
}

interface KpiSeed {
  key: string;
  label: string;
  value: number;
  unit: string | null;
  screen: string;
  sublabel?: string;
  filters?: Record<string, string>;
}
/**
 * v0.1.42 (F-64, hợp đồng gói menu-api): đúng 4 ô số row=1 theo thứ tự này; số kỹ thuật (kênh sống, nhóm lắng nghe,
 * sự kiện/ngày, độ trễ) chuyển xuống `health.tech`; bỏ plugins_health, chassis_latency, active_profiles.
 */
const KPI_ROW1: KpiSeed[] = [
  { key: 'opportunity_claim_rate', label: 'Tỉ lệ cơ hội được nhận', value: 71.4, unit: '%', screen: 'opportunity', filters: { owner: 'none' } },
  { key: 'time_to_contact', label: 'Tín hiệu → tiếp cận (trung vị)', value: 18.4, unit: 'phút', screen: 'opportunity' },
  { key: 'quotations_sent', label: 'Báo giá đã gửi (30 ngày)', value: 24, unit: null, screen: 'workbench', filters: { kind: 'quotation' } },
  { key: 'pending_ratio', label: 'Tỉ lệ chờ duyệt', value: 42.9, unit: '%', screen: 'workbench', filters: { status: 'pending' } },
];

function overviewPayload(items: InboxRow[], silenced: Set<string>, tasks: Task[]): Overview {
  const visible = items.filter((i) => !silenced.has(i.id));
  const queue = [
    ...visible
      .filter((i) => i.item_type === 'alert' || i.item_type === 'draft' || i.tab === 'opportunity')
      .sort((a, b) => (a.priority < b.priority ? -1 : 1))
      .slice(0, 20)
      .map((i) => ({
        kind: (i.item_type === 'alert' ? 'alert' : i.item_type === 'draft' ? 'draft' : 'opportunity') as
          | 'alert'
          | 'draft'
          | 'opportunity',
        id: i.id, code: i.code, title: i.item_type === 'unit' ? (i.summary ?? i.title) : i.title,
        priority: i.priority, at: i.created_at, due_at: null as string | null,
      })),
    ...tasks
      .filter((t) => t.status !== 'done' && t.status !== 'cancelled' && t.due_at)
      .slice(0, 20)
      .map((t) => ({ kind: 'due' as const, id: t.id, code: t.code, title: t.title, priority: t.priority, at: null, due_at: t.due_at })),
  ];
  return {
    kpis: [
      ...KPI_ROW1.map((k) => ({
        key: k.key, label: k.label, value: k.value, unit: k.unit, row: 1 as const, status: 'ok' as const,
        sublabel: k.sublabel ?? null, pct: null,
        filter: { screen: k.screen, filters: k.filters ?? {} },
      })),
    ],
    queue,
    spotlight: [
      { person: { id: 'p-xg', code: 'PER-0201', name: 'Xưởng gỗ Bình Dương', type: 'customer', org_name: null }, dimension: 'heat', value: 91, at: ago(18) },
      { person: BAO, dimension: 'churn_risk', value: 87, at: ago(134) },
      { person: HA, dimension: 'churn_risk', value: 62, at: ago(300) },
      { person: { id: 'p-mk', code: 'PER-1002', name: 'Trần Minh Khoa', type: 'staff', org_name: null }, dimension: 'heat', value: 71, at: ago(400) },
      { person: KHANG, dimension: 'heat', value: 78, at: ago(210) },
    ],
    signals: [
      { topic: 'Giá ván MDF và gỗ công nghiệp', count: 34, delta_pct: 142 },
      { topic: 'Tìm đối tác vận chuyển lạnh', count: 19, delta_pct: 64 },
      { topic: 'Than phiền chậm giao hàng', count: 14, delta_pct: 38 },
      { topic: 'Tuyển Key Account ngành logistics', count: 8, delta_pct: 21 },
      { topic: 'Đối thủ Minh Long xuất hiện', count: 5, delta_pct: 18 },
    ],
    health: {
      channels: [
        { type: 'zalo', active: 1 },
        { type: 'whatsapp', active: 0 },
      ],
      backlog_pending: 3,
      tech: { channels_live: 4, groups_listening: 42, events_today: 3184, processing_latency_s: 1.2 },
    },
    dataQuality: { missing_identity_pct: 12, low_confidence_score_pct: 9, unassigned_event_pct: 4 },
    hourly: Array.from({ length: 24 }, (_, h) => ({
      hour: String(h).padStart(2, '0') + ':00',
      count: [15, 7, 4, 4, 7, 22, 67, 126, 229, 289, 311, 263, 178, 244, 340, 326, 274, 215, 152, 107, 81, 59, 41, 26][h],
    })),
  };
}

/** v0.1.55 (J2): "Tin đã bỏ qua" mẫu — chữ có số điện thoại để thử che với vai không phải Owner. */
function seedSkipped(): SkippedItem[] {
  return [
    {
      id: 'sk-1', code: 'RAW-000101', at: ago(12), kind: 'text', reason: 'spam_rule_jev',
      reason_text: 'Rác — quy tắc và Jev cùng chấm rác',
      text: 'KHUYẾN MÃI SỐC!!! Nhận quà miễn phí, click ngay http://abc.xyz — liên hệ 0912345678', group: 'Nhóm Thép Phát', person: 'Số lạ 7',
    },
    {
      id: 'sk-2', code: 'RAW-000102', at: ago(55), kind: 'text', reason: 'exact_dup',
      reason_text: 'Trùng hẳn một tin đã có',
      text: 'Cần 3 container thép cuộn giao Bình Dương trong tháng này, báo giá giúp em nhé anh', group: 'Nhóm Thép Phát', person: 'Chị Lan Phạm',
    },
  ];
}

/** Bộ 12 câu mẫu (khớp gh/gen/jev_bench.py): 6 ý định + 6 lọc tin; 2 câu cuối cho Jev "trả sai" để bảng có cả Đúng/Sai. */
function benchmarkResult(): JevBenchmark {
  const rows: [string, string, string, number][] = [
    ['Tuần này khách nào hỏi giá nhiều nhất vậy em?', 'hỏi dữ liệu / tóm tắt tình hình', 'hỏi dữ liệu / tóm tắt tình hình', 410],
    ['Hôm nay có việc nào quá hạn chưa xử lý không?', 'hỏi dữ liệu / tóm tắt tình hình', 'hỏi dữ liệu / tóm tắt tình hình', 388],
    ['Chỉ anh cách thêm một nguồn model mới ở màn nào với?', 'hỏi cách làm / cần dẫn đường trên giao diện', 'hỏi cách làm / cần dẫn đường trên giao diện', 402],
    ['Làm sao để bật sao lưu tự động hằng đêm?', 'hỏi cách làm / cần dẫn đường trên giao diện', 'hỏi cách làm / cần dẫn đường trên giao diện', 395],
    ['Cho anh báo cáo tóm tắt doanh số tháng này, có số liệu cụ thể nhé.', 'xin báo cáo có số liệu', 'xin báo cáo có số liệu', 431],
    ['Viết giúp anh đoạn mã Python đọc một tệp CSV rồi cộng cột cuối.', 'ngoài phạm vi quản trị app (code, máy chủ, nói chuyện với khách)', 'ngoài phạm vi quản trị app (code, máy chủ, nói chuyện với khách)', 377],
    ['KHUYẾN MÃI SỐC!!! Click ngay để nhận quà miễn phí, đăng ký ngay hôm nay', 'rác / quảng cáo / không liên quan kinh doanh', 'rác / quảng cáo / không liên quan kinh doanh', 352],
    ['Vay tiền nhanh giải ngân trong 5 phút, không cần thế chấp, inbox ngay', 'rác / quảng cáo / không liên quan kinh doanh', 'rác / quảng cáo / không liên quan kinh doanh', 361],
    ['ok em nhé', 'ít giá trị', 'ít giá trị', 340],
    ['Anh gửi em danh sách hàng tồn kho tháng này để em xem trước nhé', 'giá trị trung bình', 'giá trị trung bình', 372],
    ['Bên em cần mua 3 container ván MDF giao Bình Dương trong tháng 10, báo giá giúp em', 'giá trị cao — cần xử lý sớm', 'giá trị cao — cần xử lý sớm', 399],
    ['Khách phàn nàn đơn giao trễ 5 ngày, đòi hoàn tiền, cần xử lý gấp trong hôm nay', 'giá trị cao — cần xử lý sớm', 'giá trị trung bình', 384],
  ];
  const items = rows.map(([question, expected, got, latency_ms]) => ({ question, expected, got, ok: expected === got, latency_ms }));
  return { total: 12, correct: items.filter((i) => i.ok).length, avg_latency_ms: 384, items };
}

export function createMock(opts: P3Options) {
  let items: InboxRow[] = opts.fresh ? [] : seedInboxItems();
  let tasks: Task[] = opts.fresh ? [] : seedTasks();
  let promises: PromiseItem[] = opts.fresh ? [] : seedPromises();
  const silenced = new Map<string, { reason: string | null; until: string | null }>();
  const has = (ctx: P2Ctx, perm: string) => !!ctx.perms[perm] && ctx.perms[perm] !== 'none';
  const triage = { enabled: true, min_score: 30, use_jev: true, prefilter: true };
  // v0.1.55 (G4): Jev đã có khoá? (bản "fresh" chưa bật). `POST /jev/enable` bật nó và báo mock-api (`onJevEnabled`) thêm
  // nguồn Jev vào `/providers` để thẻ Jev hiện nguồn mới ở dev:mock/e2e.
  let jevKey = !opts.fresh;
  const skipped = opts.fresh ? [] : seedSkipped();
  const keyMissing = (problem: P2Ctx['problem']) =>
    problem(409, 'JEV_KEY_MISSING', 'Chưa có khoá OpenRouter cho Jev', {
      reasons: ['Không có nguồn model kind=system_one đang bật kèm khoá (agent.providers / agent.provider_keys).'],
    });
  const isJunk = (r: InboxRow) => !!r.triage && (r.triage.spam || !!r.triage.duplicate_of || r.triage.score < triage.min_score);

  registerExplain('task', (id) => {
    const t = tasks.find((x) => x.id === id);
    if (!t) return null;
    return {
      kind: 'task', id, title: `${t.code} · ${t.title}`, statement: 'Việc do Sếp tạo hoặc sinh từ lời hứa/bản nháp',
      method: 'manual', factors: [], units: [], history: [],
    };
  });

  function activeSilenced(): Set<string> {
    const now = Date.now();
    const s = new Set<string>();
    for (const [id, v] of silenced) if (!v.until || new Date(v.until).getTime() > now) s.add(id);
    return s;
  }

  function counts(rows: InboxRow[]): Record<InboxTab, number> {
    const out = { all: rows.length, opportunity: 0, alert: 0, approval: 0, reply: 0, candidate: 0 };
    for (const r of rows) out[r.tab] += 1;
    return out;
  }

  function taskVisible(t: Task): Task {
    const overdue = !!t.due_at && t.status !== 'done' && t.status !== 'cancelled' && new Date(t.due_at).getTime() < Date.now();
    return { ...t, overdue };
  }

  function handle(ctx: P2Ctx): boolean {
    const { method: m, path: p, url, body, reply, problem } = ctx;
    const seg = p.split('/').filter(Boolean);

    if (p === '/overview' && m === 'GET') {
      if (!has(ctx, 'overview.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      return reply(200, overviewPayload(items, activeSilenced(), tasks));
    }

    if (p === '/refinery/triage/settings') {
      if (m === 'GET') return reply(200, triage);
      if (m === 'PATCH') {
        if (ctx.role !== 'owner') return problem(403, 'FORBIDDEN', 'Chỉ Owner');
        Object.assign(triage, body);
        return reply(200, triage);
      }
    }
    if (p === '/refinery/triage/summary' && m === 'GET') {
      const marked = items.filter((i) => i.triage);
      return reply(200, {
        days: 7, scope: 'all', ...triage, total: marked.length, kept: marked.filter((i) => !isJunk(i)).length,
        duplicates: marked.filter((i) => i.triage?.duplicate_of).length, exact_duplicates: 0,
        near_duplicates: marked.filter((i) => i.triage?.duplicate_kind === 'near').length,
        spam: marked.filter((i) => i.triage?.spam).length, low_score: 0, pending: 0, avg_quality: 48,
        jev: { count: 0, heuristic_count: marked.length, avg_latency_ms: null, spam_agreement: null },
      });
    }

    if (p === '/refinery/triage/skipped' && m === 'GET') {
      if (!has(ctx, 'queue.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      const limit = Number(url.searchParams.get('limit') ?? Number.POSITIVE_INFINITY);
      return reply(200, {
        items: skipped.slice(0, limit).map((i) => ({ ...i, text: ctx.owner ? i.text : (maskText(i.text) ?? '') })),
        total: skipped.length, days: 30,
      });
    }
    if (p === '/jev/value-summary' && m === 'GET') {
      if (ctx.role !== 'owner') return problem(403, 'FORBIDDEN', 'Chỉ Owner');
      const spam = skipped.filter((i) => i.reason !== 'exact_dup').length;
      return reply(200, { filtered: 18 + skipped.length, spam_blocked: 11 + spam, calls_saved: 7 + skipped.length, jev_on: jevKey });
    }
    if (p === '/jev/benchmark' && m === 'POST') {
      if (ctx.role !== 'owner') return problem(403, 'FORBIDDEN', 'Chỉ Owner');
      if (!jevKey) return keyMissing(problem);
      return reply(200, benchmarkResult());
    }
    if (p === '/jev/enable' && m === 'POST') {
      if (ctx.role !== 'owner') return problem(403, 'FORBIDDEN', 'Chỉ Owner');
      if (ctx.needPin()) return problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN');
      const b = body as { use_existing_openrouter?: boolean; key?: string };
      const pasted = typeof b.key === 'string' ? b.key.trim() : '';
      if (b.use_existing_openrouter && pasted) return problem(422, 'VALIDATION', 'Dữ liệu chưa hợp lệ', { errors: { key: 'Chọn một: dùng khoá OpenRouter đang có hoặc dán khoá mới, không cả hai' } });
      if (!b.use_existing_openrouter && pasted.length < 8 && !jevKey) return keyMissing(problem);
      const created = !jevKey;
      jevKey = true;
      const out = {
        provider_id: '00000000-0000-4000-8000-0000000000e5', created,
        key_source: b.use_existing_openrouter ? 'existing_openrouter' : pasted ? 'pasted' : 'kept',
        endpoint: 'https://openrouter.ai/api/v1', model: 'typesafe/jev-1.13',
      };
      opts.onJevEnabled?.({ provider_id: out.provider_id, endpoint: out.endpoint, model: out.model, key_tail: pasted ? pasted.slice(-4) : 'or01' });
      return reply(200, out);
    }

    if (seg[0] === 'inbox') {
      if (!has(ctx, 'queue.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      const visible = items.filter((i) => !activeSilenced().has(i.id));
      if (seg.length === 1 && m === 'GET') {
        const tab = (url.searchParams.get('tab') ?? 'all') as InboxTab;
        const intent = url.searchParams.get('intent');
        const hide = triage.enabled && url.searchParams.get('hide_junk') === 'true';
        const base = hide ? visible.filter((r) => !isJunk(r)) : visible;
        const cnt = counts(base);
        let rows = base;
        if (tab !== 'all') rows = rows.filter((r) => r.tab === tab);
        if (intent) rows = rows.filter((r) => r.item_type === 'unit' && r.title === intent);
        return reply(200, {
          items: rows.map(({ units: _u, ...rest }) => rest),
          next_cursor: null,
          total: cnt.all,
          counts: cnt,
          triage: { enabled: triage.enabled, min_score: triage.enabled ? triage.min_score : null, hidden: visible.length - base.length },
        });
      }
      const row = visible.find((i) => i.id === seg[1]);
      if (seg.length === 2 && m === 'GET') {
        if (!row) return problem(404, 'NOT_FOUND', 'Mục trong hàng đợi không tồn tại hoặc ngoài phạm vi của bạn');
        return reply(200, { ...row, units: row.units ?? [] });
      }
      if (seg.length === 3 && m === 'POST') {
        if (!has(ctx, 'queue.act')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        if (!row) return problem(404, 'NOT_FOUND', 'Mục trong hàng đợi không tồn tại hoặc ngoài phạm vi của bạn');
        const action = seg[2];
        if (action === 'act') {
          if (row.item_type === 'draft') return problem(409, 'USE_WORKBENCH', 'Duyệt bản nháp này ở Bàn làm việc');
          if (row.item_type === 'alert') {
            if (row.status !== 'open') return problem(409, 'ALERT_DECIDED', 'Cảnh báo này đã được xử lý');
            row.status = 'acknowledged';
            const b = body as { create_task?: boolean };
            if (b.create_task) {
              const t: Task = {
                id: `t-from-${row.id}`, code: `TSK-0${400 + tasks.length}`, title: row.title, priority: row.priority,
                status: 'todo', assignee: null, subject: row.subject, due_at: null, remind_at: null, overdue: false,
                source: 'promise', created_at: new Date().toISOString(), completed_at: null,
              };
              tasks = [t, ...tasks];
            }
            return reply(200, { ok: true, status: 'acknowledged' });
          }
          const b = body as { text?: string };
          if (!b.text?.trim()) return problem(422, 'VALIDATION', 'Cần nội dung để soạn trả lời', { errors: { text: 'Không được để trống' } });
          const draft = { id: `draft-from-${row.id}`, code: `ACT-0${240 + items.length}`, status: 'pending' };
          opts.emit('draft.new', {
            id: draft.id, code: draft.code, kind: 'message', kind_label: 'Tin nhắn', title: `Trả lời ${row.title}`,
            agent: null, created_by: { id: USER_IDS.owner, name: ctx.userLabel }, created_at: new Date().toISOString(),
            status: 'pending', hold_reason: null, subject: row.subject,
          });
          return reply(200, { ok: true, draft });
        }
        if (action === 'assign') {
          // Như gh.biz.queue.routes.assign_inbox_item: `user_id: uuid.UUID` (không phải UUID → 422), người dùng
          // phải đang hoạt động cùng tổ chức (không có → 404 'Người dùng').
          const b = body as { user_id?: unknown };
          if (rejectNonUuid(problem, 'user_id', b.user_id)) return true;
          const target = opts.findUser?.(String(b.user_id));
          if (!target) return problem(404, 'NOT_FOUND', 'Người dùng không tồn tại hoặc ngoài phạm vi của bạn');
          return reply(200, { ok: true, assigned_to: { id: target.id, name: target.display_name } });
        }
        if (action === 'silence') {
          const b = body as { reason?: string | null; until?: string | null };
          silenced.set(row.id, { reason: b.reason ?? null, until: b.until ?? null });
          return reply(200, { ok: true });
        }
      }
    }

    if (seg[0] === 'tasks') {
      if (seg[1] === 'promises') {
        if (!has(ctx, 'queue.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        if (seg.length === 2 && m === 'GET') {
          const status = url.searchParams.get('status') ?? 'upcoming';
          const now = Date.now();
          let rows = promises;
          if (status === 'upcoming') rows = rows.filter((p) => !p.kept_at && new Date(p.due_at).getTime() >= now && new Date(p.due_at).getTime() < now + 3 * 86_400_000);
          else if (status === 'overdue') rows = rows.filter((p) => !p.kept_at && new Date(p.due_at).getTime() < now);
          else if (status === 'kept') rows = rows.filter((p) => !!p.kept_at);
          return reply(200, { items: rows, next_cursor: null, total: rows.length });
        }
        if (seg.length === 3 && m === 'PATCH') {
          if (!has(ctx, 'queue.act')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
          const pr = promises.find((x) => x.id === seg[2]);
          if (!pr) return problem(404, 'NOT_FOUND', 'Lời hứa không tồn tại hoặc ngoài phạm vi của bạn');
          const kept = (body as { kept: boolean }).kept;
          promises = promises.map((x) => (x.id === pr.id ? { ...x, kept_at: kept ? new Date().toISOString() : null, broken: !kept } : x));
          return reply(200, { ok: true });
        }
        return false;
      }
      if (!has(ctx, 'queue.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (seg.length === 1 && m === 'GET') {
        const status = url.searchParams.get('status');
        const priority = url.searchParams.get('priority');
        const overdue = url.searchParams.get('overdue');
        const dueBefore = url.searchParams.get('due_before');
        const limit = Number(url.searchParams.get('limit') ?? Number.POSITIVE_INFINITY);
        const assignee = url.searchParams.get('assignee_user_id');
        let rows = tasks.map(taskVisible);
        if (status) rows = rows.filter((t) => t.status === status);
        if (priority) rows = rows.filter((t) => t.priority === priority);
        if (overdue === 'true') rows = rows.filter((t) => t.overdue);
        if (dueBefore) rows = rows.filter((t) => !!t.due_at && Date.parse(t.due_at) < Date.parse(dueBefore)); // như máy chủ: chỉ việc CÓ hạn
        if (assignee) rows = rows.filter((t) => t.assignee?.id === assignee);
        const total = rows.length;
        return reply(200, { items: limit > 0 ? rows.slice(0, limit) : rows, next_cursor: null, total });
      }
      if (seg.length === 1 && m === 'POST') {
        if (!has(ctx, 'queue.act')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        const b = body as { title: string; priority?: Task['priority']; assignee_user_id?: string; subject?: { type: 'person' | 'group'; id: string }; due_at?: string | null; remind_at?: string | null };
        if (!b.title?.trim()) return problem(422, 'VALIDATION', 'Cần tiêu đề việc', { errors: { title: 'Không được để trống' } });
        const t: Task = {
          id: `t-new-${Date.now()}`, code: `TSK-0${400 + tasks.length}`, title: b.title, priority: b.priority ?? 'P3',
          status: 'todo', assignee: b.assignee_user_id ? { id: b.assignee_user_id, name: 'Chị Lan Phạm' } : null,
          subject: null, due_at: b.due_at ?? null, remind_at: b.remind_at ?? null, overdue: false,
          source: 'manual', created_at: new Date().toISOString(), completed_at: null,
        };
        tasks = [t, ...tasks];
        return reply(201, taskVisible(t));
      }
      const t = tasks.find((x) => x.id === seg[1]);
      if (seg.length === 2 && m === 'GET') {
        if (!t) return problem(404, 'NOT_FOUND', 'Việc không tồn tại hoặc ngoài phạm vi của bạn');
        return reply(200, taskVisible(t));
      }
      if (seg.length === 2 && m === 'PATCH') {
        if (!has(ctx, 'queue.act')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        if (!t) return problem(404, 'NOT_FOUND', 'Việc không tồn tại hoặc ngoài phạm vi của bạn');
        const b = body as Partial<{ status: Task['status']; priority: Task['priority']; assignee_user_id: string | null; due_at: string | null; remind_at: string | null }>;
        if (b.status !== undefined) {
          t.status = b.status;
          t.completed_at = b.status === 'done' ? new Date().toISOString() : t.completed_at;
        }
        if (b.priority !== undefined) t.priority = b.priority;
        if ('assignee_user_id' in b) t.assignee = b.assignee_user_id ? { id: b.assignee_user_id, name: 'Chị Lan Phạm' } : null;
        if (b.due_at !== undefined) t.due_at = b.due_at;
        if (b.remind_at !== undefined) t.remind_at = b.remind_at;
        return reply(200, taskVisible(t));
      }
    }

    return false;
  }

  return {
    handle,
    hooks: {
      /** Test: bơm thêm một cảnh báo mới vào hàng đợi. */
      alert: (row: Partial<InboxRow> = {}) => {
        const a: InboxRow = {
          id: `iq-alert-hook-${Date.now()}`, code: `ALR-0${300 + items.length}`, item_type: 'alert', tab: 'alert',
          title: 'Khách đang lạnh / sắp mất', summary: 'Cảnh báo mới bơm qua hook thử nghiệm.',
          priority: 'P1', created_at: new Date().toISOString(), score: null, confidence_band: null,
          subject: BAO, group: GROUP_TP, agent: null, alert_type: 'customer_cooling',
          alert_type_label: 'Khách đang lạnh / sắp mất', suggested_action: 'Mở hồ sơ và gán người xử lý',
          status: 'open', ...row,
        };
        items = [a, ...items];
        opts.emit('alert.new', {});
        return a;
      },
      items: () => items,
      tasks: () => tasks,
      promises: () => promises,
      setTasks: (rows: Task[]) => {
        tasks = rows;
      },
      /** e2e (F-R9): đặt Jev "đã có khoá" hay chưa — `false` ⇒ benchmark / enable trả 409 JEV_KEY_MISSING như API. */
      jevKey: (b: { on?: boolean } = {}) => {
        jevKey = b.on !== false;
        return { jev_key: jevKey };
      },
    } as Record<string, (...args: never[]) => unknown>,
    dispose: () => {},
  };
}
