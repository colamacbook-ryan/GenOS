/**
 * Mock API giai đoạn 4 · Điều khiển hệ thống (PLAN 4.5/4.6, `apps/api/gh/system_api/routes.py` +
 * `apps/api/gh/setup/routes.py`):
 *   - Quyền hạn: ma trận (`/permissions`) trên CHÍNH `MATRIX` dùng chung với `Me.permissions`
 *     (`mock-api.ts`, KHÔNG copy) — sửa ô ở màn Quyền hạn đổi luôn năng lực thật của vai trò, giống hệt
 *     `core.role_permissions` là một bảng duy nhất ở backend thật; nhóm đang lắng nghe (`/listening-groups`)
 *     trên `groups` dùng chung của `mock-phase2.ts`; ranh giới có trách nhiệm (`/boundaries*`).
 *   - Nhật ký hệ thống (`/audit-log*`) trên `audit` dùng chung của `mock-api.ts` (cùng mảng `/audit` đã dùng).
 *   - Dữ liệu & lưu trữ — spec I (`/retention-policies`, `/persons/{id}/data-requests` trên `people` dùng
 *     chung của `mock-p3-relations.ts`).
 *   - Bước thiết lập 10–11 (`step10`/`step11`, gọi thẳng từ `mock-api.ts` vì `/setup/steps/*` không đi qua
 *     `handle(ctx)` chung như các route khác — xem `mock-phase2.ts` `setupStep`).
 *
 * "Bộ não AI" (`/providers*`, `/cli/*`, `/failover-rules`, `PATCH /providers/chain`) đã có mock đủ ở
 * `mock-phase2.ts`/`mock-p4-api.ts` — màn `system` chỉ đọc lại, KHÔNG lặp ở đây.
 */
import { randomUUID } from 'node:crypto';
import type { P2Ctx } from './mock-phase2';

export type RoleCode = 'owner' | 'manager' | 'operator' | 'agent_staff' | 'auditor';

export interface AuditRow {
  id: string;
  at: string;
  actor_type: string;
  actor_id: string | null;
  actor_label: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  target_label: string | null;
  autonomy_level: number | null;
  result: string;
  detail: unknown;
}

interface MockGroup {
  id: string;
  code: string;
  name: string;
  members: number;
  kind: string;
  listen_mode: string;
  view_scope: string;
  channel: string;
  raw24h: number;
}

interface MockPerson {
  id: string;
  code: string;
  name: string;
}

export interface P4SystemOptions {
  fresh: boolean;
  emit: (type: string, data: unknown) => void;
  /** `mock-phase2.ts` hooks.groups() — mảng dùng chung, KHÔNG copy. */
  getGroups: () => MockGroup[];
  /** `mock-api.ts` MATRIX — mảng dùng chung, KHÔNG copy (xem docstring đầu file). */
  matrix: Record<string, [string, string, string, string, string]>;
  roleOrder: RoleCode[];
  /** `mock-api.ts` audit — mảng dùng chung (unshift ghi mới lên đầu). */
  auditLog: AuditRow[];
  /** `mock-p3-relations.ts` hooks.people() — mảng dùng chung (erase ghi thẳng lên đây). */
  getPersons: () => MockPerson[];
}

// 7 cột ma trận thiết kế → các quyền thuộc cột đó (`gh.system_api.routes.PERMISSION_COLUMNS`, y hệt thứ tự).
const PERMISSION_COLUMNS: Array<[string, string, string[]]> = [
  ['overview', 'Hôm nay', ['overview.read']],
  ['queue', 'Hàng đợi', ['queue.read', 'queue.act']],
  ['profile', 'Hồ sơ khách', ['profile.read', 'profile.write']],
  ['people_review', 'Đánh giá nhân sự', ['people_review.read', 'people_review.write', 'care.read']],
  ['opportunity', 'Cơ hội', ['opportunity.read', 'opportunity.write']],
  ['action', 'Hành động', ['action.draft', 'action.approve']],
  ['audit', 'Nhật ký', ['audit.read']],
];
const EDITABLE_PERMISSIONS = new Set(PERMISSION_COLUMNS.flatMap(([, , codes]) => codes));
// Auditor không bao giờ có quyền ghi (`rbac.WRITE_PERMISSIONS`, khoá cứng).
const WRITE_PERMISSIONS = new Set(['queue.act', 'profile.write', 'people_review.write', 'opportunity.write', 'action.draft', 'action.approve']);

const ROLE_INFO: Record<RoleCode, { name: string; meta: string }> = {
  owner: { name: 'Owner — Sếp', meta: 'thấy toàn cảnh' },
  manager: { name: 'Manager', meta: 'thấy team mình' },
  operator: { name: 'Operator', meta: 'thấy hàng đợi việc' },
  agent_staff: { name: 'Agent nhân viên', meta: 'chỉ khách được phân' },
  auditor: { name: 'Auditor', meta: 'xem, không hành động' },
};

interface BoundaryRow {
  code: string;
  label: string;
  enabled: boolean;
  locked: boolean;
  params: Record<string, unknown>;
}

// 6/8 khoá cứng ARCHITECTURE §7.4 có dòng ở đây (`apps/api/gh/bootstrap.py` BOUNDARIES — cùng giá trị khởi
// tạo thật); 2 khoá còn lại (kho thô/nhật ký chỉ-INSERT, PIN+mã hoá bí mật) không có công tắc, hiển thị tĩnh
// ở client (`STATIC_HARD_BOUNDARIES`, `packages/contracts/src/p4-system.ts`).
function seedBoundaries(): BoundaryRow[] {
  return [
    { code: 'listen_authorized_only', label: 'Chỉ lưu tin của nhóm Sếp đã chọn nghe', enabled: true, locked: true, params: {} },
    { code: 'disclose_staff_observation', label: 'Công khai nội bộ khi dùng để đánh giá nhân sự', enabled: true, locked: false, params: {} },
    { code: 'hide_sensitive_below_owner', label: 'Ẩn dữ liệu nhạy cảm khỏi vai trò dưới Owner', enabled: true, locked: true, params: {} },
    { code: 'personnel_alert_requires_evidence', label: 'Điểm số, cảnh báo nhân sự phải có chứng cứ', enabled: true, locked: true, params: {} },
    { code: 'observe_external_market', label: 'Quan sát nhóm thị trường bên ngoài', enabled: true, locked: false, params: {} },
    { code: 'auto_personnel_decisions', label: 'Hệ thống tự ra quyết định nhân sự', enabled: false, locked: true, params: {} },
    {
      code: 'approval_gate',
      label: 'Gửi ra ngoài / vượt ngưỡng tiền / liên quan nhân sự luôn chờ duyệt',
      enabled: true,
      locked: true,
      params: { approval_threshold_vnd: 50_000_000 },
    },
    { code: 'mcp_write_requires_approval', label: 'Tool MCP loại ghi qua duyệt trước khi chạy (ngoại lệ duy nhất: Ghi vào Kho dữ liệu sau khi Sếp Xác nhận + nhập mã PIN)', enabled: true, locked: true, params: {} },
  ];
}

const RETENTION_DATASETS = ['raw.events', 'clean.meaning_units', 'ops.action_log', 'memory.entries', 'agent.model_calls'] as const;
type RetentionDataset = (typeof RETENTION_DATASETS)[number];
interface RetentionRow {
  keep_days: number | null;
  anonymize_after_days: number | null;
  /** v0.1.40 (F-2): như API — hạn đặt trước v0.1.40 chưa xác nhận ⇒ chưa thi hành. */
  needs_confirm?: boolean;
}
/** v0.1.40 (F-2): cách dọn từng tập (như gh/retention — bảng phân vùng xoá theo cả tháng; ops.action_log chỉ ghi thêm). */
const RETENTION_MODE: Record<string, { mode: 'partition' | 'batch' | 'not_applicable'; note: string }> = {
  'raw.events': { mode: 'partition', note: 'Xoá theo cả tháng khi cả tháng đã quá hạn' },
  'clean.meaning_units': { mode: 'partition', note: 'Xoá theo cả tháng khi cả tháng đã quá hạn' },
  'ops.action_log': { mode: 'not_applicable', note: 'Nhật ký hành động chỉ ghi thêm — không xoá theo hạn' },
  'memory.entries': { mode: 'batch', note: 'Xoá dần các dòng quá hạn mỗi đêm' },
  'agent.model_calls': { mode: 'partition', note: 'Xoá theo cả tháng khi cả tháng đã quá hạn' },
  'agent.browser_jobs.result': { mode: 'batch', note: 'Kết quả việc trình duyệt nền tự xoá sau 14 ngày' },
};

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;
const INVITE_ROLES = new Set(['manager', 'operator', 'agent_staff', 'auditor']);

export interface Step10Invited {
  id: string;
  display_name: string;
  email: string;
  role: string;
  temp_password: string;
}
export interface BackupConfig {
  frequency: 'daily' | 'weekly' | 'monthly';
  time_of_day: string;
  retention_count: number;
  destination: 'local';
}
type StepResult<T> = { ok: true; value: T } | { ok: false; status: number; code: string; title: string; extra?: Record<string, unknown> };

export function createMock(opts: P4SystemOptions) {
  const has = (ctx: P2Ctx, perm: string) => !!ctx.perms[perm] && ctx.perms[perm] !== 'none';
  const pin = (ctx: P2Ctx, operation: string) => {
    if (!ctx.needPin()) return true;
    ctx.problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN', { detail: { operation } });
    return false;
  };

  const boundaries = seedBoundaries();
  const retention = new Map<RetentionDataset, RetentionRow>(
    opts.fresh
      ? []
      : [
          ['raw.events', { keep_days: 365, anonymize_after_days: null }],
          ['clean.meaning_units', { keep_days: 730, anonymize_after_days: null }],
          ['ops.action_log', { keep_days: 2555, anonymize_after_days: null }],
          ['memory.entries', { keep_days: null, anonymize_after_days: null }],
          ['agent.model_calls', { keep_days: 90, anonymize_after_days: 30 }],
        ],
  );
  const dataRequests = new Map<string, Array<{ id: string; kind: string; status: string; requested_at: string; completed_at: string | null }>>();
  const restrictedPersons = new Set<string>();
  const backup: BackupConfig = { frequency: 'daily', time_of_day: '02:00', retention_count: 7, destination: 'local' };
  let backupConfigured = !opts.fresh;

  // ── Sao lưu & khôi phục (v0.1.20, gh/system_api/backups.py) — mock mô phỏng worker + genh trên máy chủ: mỗi lần
  // GET tiến một bước (job queued → running → done + thêm bản; restore requested → running → done).
  const DAY = 24 * 3600 * 1000;
  const backupKey = (t: number) => {
    const iso = new Date(t).toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
    return `backups/${iso}-${randomUUID().replace(/-/g, '').slice(0, 8)}.pgcustom.enc`;
  };
  type MockBackup = { key: string; taken_at: string; size_bytes: number; trigger: string | null; encrypted: true; key_id: 'backup' | 'master' };
  const mkBackup = (t: number, trigger: string | null, size: number, keyId: 'backup' | 'master' = 'backup'): MockBackup => ({
    key: backupKey(t), taken_at: new Date(t).toISOString(), size_bytes: size, trigger, encrypted: true, key_id: keyId,
  });
  const at2am = (daysAgo: number) => {
    const d = new Date(Date.now() - daysAgo * DAY);
    d.setUTCHours(19, 0, 0, 0); // 02:00 giờ Việt Nam
    return d.getTime();
  };
  const backups: MockBackup[] = opts.fresh
    ? []
    : [
        mkBackup(at2am(0), 'scheduled', 48_300_000),
        mkBackup(at2am(1) + 5 * 3600 * 1000, 'pre-update', 47_900_000),
        mkBackup(at2am(1), 'scheduled', 47_800_000),
        mkBackup(at2am(2), 'manual', 46_100_000),
        mkBackup(at2am(9), 'scheduled', 41_000_000),
        mkBackup(at2am(40), null, 30_500_000, 'master'),
      ];
  let backupJob: Record<string, unknown> | null = null;
  const restore = {
    can_request: process.env.MOCK_RESTORE_UNAVAILABLE !== '1', state: 'idle', key: null as string | null, safety_key: null as string | null,
    message: null as string | null, started_at: null as string | null, finished_at: null as string | null, requested_at: null as string | null,
  };
  function backupsPage() {
    return {
      items: [...backups].sort((a, b) => b.taken_at.localeCompare(a.taken_at)),
      schedule: backupConfigured ? { frequency: backup.frequency, time_of_day: backup.time_of_day } : null,
      timezone: 'Asia/Ho_Chi_Minh',
      retention: { daily: 7, weekly: 4, monthly: 12, recent_hours: 24 },
      job: backupJob,
      restore: { ...restore },
    };
  }
  function advanceBackups() {
    if (backupJob?.state === 'queued') backupJob = { ...backupJob, state: 'running', started_at: new Date().toISOString() };
    else if (backupJob?.state === 'running') {
      const b = mkBackup(Date.now(), 'manual', 48_400_000);
      backups.push(b);
      backupJob = { ...backupJob, state: 'done', key: b.key, finished_at: new Date().toISOString() };
    }
    if (restore.state === 'requested') Object.assign(restore, { state: 'running', started_at: new Date().toISOString() });
    else if (restore.state === 'running') {
      const safety = mkBackup(Date.now(), 'pre-restore', 48_500_000);
      backups.push(safety);
      Object.assign(restore, { state: 'done', safety_key: safety.key, finished_at: new Date().toISOString() });
    }
  }

  function permissionsPage() {
    const roles = opts.roleOrder.map((code) => {
      const idx = opts.roleOrder.indexOf(code);
      const permissions: Record<string, string> = {};
      for (const p of EDITABLE_PERMISSIONS) permissions[p] = opts.matrix[p]?.[idx] ?? 'none';
      return { code, name: ROLE_INFO[code].name, meta: ROLE_INFO[code].meta, permissions };
    });
    return { columns: PERMISSION_COLUMNS.map(([key, label, permissions]) => ({ key, label, permissions })), roles };
  }

  // v0.1.40 (F-2): gh:retention:last — lần dọn gần nhất (bản đã thiết lập có sẵn một lần dọn đêm qua).
  const retentionLast: { at: string | null; deleted: Record<string, number> } = opts.fresh
    ? { at: null, deleted: {} }
    : { at: new Date(Date.now() - 6 * 3600 * 1000).toISOString(), deleted: { 'raw.events': 0, 'clean.meaning_units': 0, 'memory.entries': 12, 'agent.model_calls': 3480, 'agent.browser_jobs.result': 7 } };
  function retentionRow(d: string, row: RetentionRow) {
    const info = RETENTION_MODE[d];
    const na = info.mode === 'not_applicable';
    return {
      dataset: d, ...row, needs_confirm: !!row.needs_confirm && !na && row.keep_days != null,
      mode: info.mode, editable: !na && d !== 'agent.browser_jobs.result', note: info.note,
      last_run_at: na ? null : retentionLast.at, last_deleted: na || !retentionLast.at ? null : (retentionLast.deleted[d] ?? 0),
    };
  }
  function retentionList() {
    return [
      ...RETENTION_DATASETS.map((d) => retentionRow(d, retention.get(d) ?? { keep_days: null, anonymize_after_days: null })),
      retentionRow('agent.browser_jobs.result', { keep_days: 14, anonymize_after_days: null }),
    ];
  }

  // ── v0.1.40 (F-12): Bản sao ngoài máy — mock mô phỏng genh trên máy chủ (run/offsite-status.json + hộp thư
  // run/request/offsite.json). Yêu cầu nằm 'requested' tới khi hook `offsite` đổi (e2e thấy "Đang chờ máy chủ nhận…").
  const OFFSITE_KEY_MOCK = 'ABCDE-FGHIJ-KLMN2-OPQR3-STUV4-WXYZ5';
  const OFFSITE_KEY_MISSING_TITLE = 'Chưa có Khoá khôi phục trên máy chủ — chạy `genh update` một lần trên máy chủ để tạo khoá';
  const offsite = {
    configured: !opts.fresh,
    dest: opts.fresh ? '' : '/media/sep/GEN-USB',
    state: opts.fresh ? 'not_configured' : 'ok',
    error_code: opts.fresh ? 'GH-EB00' : '',
    message: null as string | null,
    last_attempt_at: opts.fresh ? '' : new Date(Date.now() - 3 * DAY).toISOString(),
    last_success_at: opts.fresh ? '' : new Date(Date.now() - 3 * DAY).toISOString(),
    last_size_bytes: opts.fresh ? 0 : 1_288_490_189,
    verified: !opts.fresh,
    key_id: 'a1b2c3d4',
    schedule: opts.fresh ? '' : 'systemd',
    request: { state: 'idle', action: null as string | null, requested_at: null as string | null },
    can_request: process.env.MOCK_OFFSITE_UNAVAILABLE !== '1',
    manual_command: null as string | null,
    key_present: true,
  };
  // Như gh/system_api/offsite.manual_command: lệnh theo đúng việc; `set` chỉ khi có đường dẫn dùng được trong nháy kép.
  const MANUAL = (action: string | null, path?: string | null): string | null =>
    action === 'run'
      ? 'genh offsite run'
      : action === 'disable'
        ? 'genh offsite disable'
        : action === 'set' && path && ![...path].some((c) => '"$`'.includes(c) || c.charCodeAt(0) < 32)
          ? `genh offsite set "${path}"`
          : null;
  let offsiteRequestPath: string | null = null;
  /** Hook `portable_busy` ⇒ GET /system/offsite/portable trả 409 PORTABLE_IN_PROGRESS (như khoá Redis của API). */
  let portableBusy = false;
  const UNAVAILABLE_TITLE = 'Máy chủ chưa nhận lệnh từ Console — chạy lệnh sau một lần trên máy chủ';
  function offsiteView() {
    const last = offsite.last_success_at || null;
    const age = last ? Math.max(0, Math.round(((Date.now() - Date.parse(last)) / DAY) * 10) / 10) : null;
    return {
      ...offsite,
      manual_command: offsite.request.state === 'idle' ? null : MANUAL(offsite.request.action, offsiteRequestPath),
      dest: offsite.dest || null,
      error_code: offsite.error_code || null,
      last_attempt_at: offsite.last_attempt_at || null,
      last_success_at: last,
      age_days: age,
      // Như health.OFFSITE_STALE_AFTER: lịch tuần + 12 giờ ân hạn.
      stale: age == null || age > 7.5,
      last_size_bytes: offsite.last_size_bytes || null,
      schedule: offsite.schedule || null,
      request: { ...offsite.request },
    };
  }

  // ── v0.1.41 (F-84): Chi phí AI hôm nay — dữ liệu mẫu tính sẵn như gh/system_api/ai_cost.py: chi phí = token × giá
  // (₫/1M token) theo model; CLI trả theo gói (0 ₫); model chưa có giá ⇒ lượt "chưa có giá". Mẫu: 12.500 ₫.
  interface CostModel { model_id: string; provider_name: string; provider_kind: string; model_name: string; in_vnd_per_mtok: number | null; out_vnd_per_mtok: number | null; price_source: 'owner' | 'subscription' | 'none' }
  const costModels: CostModel[] = [
    { model_id: 'mc-gemini-flash', provider_name: 'Gemini API', provider_kind: 'gemini', model_name: 'gemini-2.5-flash', in_vnd_per_mtok: 7_500, out_vnd_per_mtok: 62_500, price_source: 'owner' },
    { model_id: 'mc-deepseek', provider_name: 'DeepSeek API', provider_kind: 'deepseek', model_name: 'deepseek-reasoner', in_vnd_per_mtok: null, out_vnd_per_mtok: null, price_source: 'none' },
    { model_id: 'mc-claude-cli', provider_name: 'Claude Code CLI', provider_kind: 'claude_code_cli', model_name: 'claude-sonnet', in_vnd_per_mtok: 0, out_vnd_per_mtok: 0, price_source: 'subscription' },
  ];
  const costCalls: Array<{ agent_key: string; label: string; model_id: string; calls: number; tokens_in: number; tokens_out: number }> = opts.fresh
    ? []
    : [
        { agent_key: 'core.gen', label: 'Gen — trợ lý quản trị', model_id: 'mc-gemini-flash', calls: 12, tokens_in: 400_000, tokens_out: 80_000 },
        { agent_key: 'core.gen', label: 'Gen — trợ lý quản trị', model_id: 'mc-claude-cli', calls: 5, tokens_in: 60_000, tokens_out: 9_000 },
        { agent_key: 'core.refinery', label: 'Sàng lọc & suy luận chính', model_id: 'mc-gemini-flash', calls: 30, tokens_in: 200_000, tokens_out: 48_000 },
        { agent_key: 'duty.decide', label: 'Trực việc', model_id: 'mc-deepseek', calls: 3, tokens_in: 30_000, tokens_out: 6_000 },
      ];
  let dailyBudget: number | null = opts.fresh ? null : 20_000;
  const vnDate = (t: number) => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Ho_Chi_Minh' }).format(new Date(t));
  function aiCostView(date: string) {
    const today = vnDate(Date.now());
    const calls = date === today ? costCalls : [];
    const agents = new Map<string, { agent_key: string; label: string; calls: number; tokens_in: number; tokens_out: number; cost_vnd: number; unpriced_calls: number }>();
    for (const c of calls) {
      const mdl = costModels.find((x) => x.model_id === c.model_id)!;
      const a = agents.get(c.agent_key) ?? { agent_key: c.agent_key, label: c.label, calls: 0, tokens_in: 0, tokens_out: 0, cost_vnd: 0, unpriced_calls: 0 };
      a.calls += c.calls;
      a.tokens_in += c.tokens_in;
      a.tokens_out += c.tokens_out;
      if (mdl.price_source === 'none' || mdl.in_vnd_per_mtok == null || mdl.out_vnd_per_mtok == null) a.unpriced_calls += c.calls;
      else a.cost_vnd += Math.round((c.tokens_in * mdl.in_vnd_per_mtok + c.tokens_out * mdl.out_vnd_per_mtok) / 1_000_000);
      agents.set(c.agent_key, a);
    }
    const agentList = [...agents.values()].sort((x, y) => y.cost_vnd - x.cost_vnd);
    const total = agentList.reduce((n, a) => n + a.cost_vnd, 0);
    const last7 = Array.from({ length: 7 }, (_, i) => {
      const d = vnDate(Date.now() - (6 - i) * DAY);
      return { date: d, total_vnd: d === today ? total : opts.fresh ? 0 : 8_000 + i * 700 };
    });
    return {
      date,
      timezone: 'Asia/Ho_Chi_Minh',
      total_vnd: total,
      budget_vnd: dailyBudget,
      over_budget: dailyBudget != null && total > dailyBudget,
      unpriced_calls: agentList.reduce((n, a) => n + a.unpriced_calls, 0),
      agents: agentList,
      models: costModels.map((mdl) => ({ ...mdl, calls_today: calls.filter((c) => c.model_id === mdl.model_id).reduce((n, c) => n + c.calls, 0) })),
      last_7_days: last7,
      feedback_7d: opts.fresh ? { helpful: 0, not_helpful: 0, briefing_helpful: 0, briefing_not_helpful: 0 } : { helpful: 14, not_helpful: 3, briefing_helpful: 5, briefing_not_helpful: 1 },
    };
  }
  const isVnd = (v: unknown) => typeof v === 'number' && Number.isFinite(v) && v >= 0;

  function handle(ctx: P2Ctx): boolean {
    const { method: m, path: p, url, reply, problem, body } = ctx;
    const q = url.searchParams;
    const seg = p.split('/').filter(Boolean);

    // ── Quyền hạn: ma trận ──
    if (p === '/permissions' && m === 'GET') {
      if (!has(ctx, 'system.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      return reply(200, permissionsPage());
    }
    if (p === '/permissions' && m === 'PATCH') {
      if (!has(ctx, 'roles.manage')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (!pin(ctx, 'roles.change')) return true;
      const b = body as { role?: string; permission?: string; scope?: string };
      const role = String(b.role ?? '') as RoleCode;
      const permission = String(b.permission ?? '');
      const scope = String(b.scope ?? '');
      if (!EDITABLE_PERMISSIONS.has(permission)) return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { permission: 'Quyền này không nằm trong ma trận sửa được ở Quyền hạn' } });
      if (role === 'owner' && scope !== 'all') return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { scope: 'Owner luôn toàn quyền ở mọi cột — khoá cứng, không sửa được' } });
      if (role === 'auditor' && WRITE_PERMISSIONS.has(permission) && scope !== 'none') return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { scope: 'Vai Kiểm soát không bao giờ có quyền ghi — khoá cứng, không sửa được' } });
      const idx = opts.roleOrder.indexOf(role);
      if (idx < 0 || !opts.matrix[permission]) return problem(404, 'NOT_FOUND', 'Ô ma trận');
      opts.matrix[permission][idx] = scope;
      return reply(200, permissionsPage());
    }

    // ── Quyền hạn: nhóm đang lắng nghe ──
    if (p === '/listening-groups' && m === 'GET') {
      if (!has(ctx, 'system.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      const rows = opts
        .getGroups()
        .filter((g) => g.listen_mode !== 'off')
        .map(({ raw24h, ...g }) => ({ ...g, msgs_24h: raw24h }));
      return reply(200, rows);
    }

    // ── Quyền hạn: ranh giới có trách nhiệm ──
    if (p === '/boundaries' && m === 'GET') {
      if (!has(ctx, 'system.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      return reply(200, boundaries);
    }
    if (seg[0] === 'boundaries' && seg.length === 2 && m === 'PATCH') {
      if (!has(ctx, 'system.manage')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (!pin(ctx, 'policy.change')) return true;
      const row = boundaries.find((b) => b.code === seg[1]);
      if (!row) return problem(404, 'NOT_FOUND', 'Ranh giới');
      const b = body as { enabled?: boolean; params?: Record<string, unknown> };
      if (typeof b.enabled === 'boolean' && b.enabled !== row.enabled && row.locked) {
        return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { enabled: 'Ranh giới này là khoá cứng — không tắt/bật được (ARCHITECTURE §7.4)' } });
      }
      if (row.code === 'approval_gate' && b.params && 'approval_threshold_vnd' in b.params) {
        const v = b.params.approval_threshold_vnd;
        if (typeof v !== 'number' || !Number.isInteger(v) || v < 0) {
          return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { 'params.approval_threshold_vnd': 'Ngưỡng tiền phải là số nguyên không âm' } });
        }
      }
      if (typeof b.enabled === 'boolean') row.enabled = b.enabled;
      if (b.params) row.params = { ...row.params, ...b.params };
      return reply(200, row);
    }

    // ── Nhật ký hệ thống ──
    // `/audit` (đường đọc của tab Nhật ký từ A8) và `/audit-log` (cũ) cùng một bộ lọc, như `gh.audit.routes.query_log`.
    if ((p === '/audit-log' || p === '/audit') && m === 'GET') {
      if (!has(ctx, 'audit.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      const actorType = q.get('actor_type');
      const action = q.get('action');
      const targetType = q.get('target_type');
      const targetId = q.get('target_id');
      const since = q.get('since');
      const until = q.get('until');
      const needle = (q.get('q') ?? '').trim().toLowerCase();
      const qActions = (q.get('q_actions') ?? '').split(',').filter(Boolean);
      const qActors = (q.get('q_actors') ?? '').split(',').filter(Boolean);
      const hideNoise = q.get('hide_noise') === 'true';
      const filtered = opts.auditLog.filter(
        (a) =>
          (!actorType || a.actor_type === actorType) &&
          (!action || a.action.startsWith(action)) &&
          (!hideNoise || !(a.action.startsWith('http.') || (a.action === 'audit.chain_verified' && a.result === 'ok'))) &&
          (!needle ||
            (a.actor_label ?? a.actor_id ?? '').toLowerCase().includes(needle) ||
            a.action.toLowerCase().includes(needle) ||
            (a.target_label ?? '').toLowerCase().includes(needle) ||
            qActions.includes(a.action) ||
            qActors.includes(a.actor_id ?? '')) &&
          (!targetType || a.target_type === targetType) &&
          (!targetId || a.target_id === targetId) &&
          (!since || a.at >= since) &&
          (!until || a.at <= until),
      );
      const limit = Math.min(200, Number(q.get('limit') ?? 50));
      const off = Number(q.get('cursor') ?? 0);
      const page = filtered.slice(off, off + limit);
      return reply(200, { items: page, next_cursor: off + limit < filtered.length ? String(off + limit) : null });
    }
    if (p === '/audit-log/export' && m === 'GET') {
      if (!has(ctx, 'data.manage')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (!pin(ctx, 'data.export')) return true;
      const actorType = q.get('actor_type');
      const action = q.get('action');
      const rows = opts.auditLog.filter((a) => (!actorType || a.actor_type === actorType) && (!action || a.action.startsWith(action)));
      const lines = ['at,actor_type,actor_id,actor_label,action,target_type,target_id,target_label,autonomy_level,result'];
      for (const r of rows) {
        lines.push(
          [r.at, r.actor_type, r.actor_id ?? '', r.actor_label ?? '', r.action, r.target_type ?? '', r.target_id ?? '', r.target_label ?? '', r.autonomy_level ?? '', r.result].join(','),
        );
      }
      const name = `nhat-ky-${new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '').replace(/^(\d{8})(\d{4})$/, '$1-$2')}.csv`;
      ctx.text(200, 'text/csv; charset=utf-8', `\uFEFF${lines.join('\n')}`, name);
      return true;
    }

    // ── Sao lưu & khôi phục (v0.1.20) ──
    if (p.startsWith('/system/backups')) {
      if (!has(ctx, 'system.manage')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (p === '/system/backups' && m === 'GET') {
        advanceBackups();
        return reply(200, backupsPage());
      }
      if (p === '/system/backups' && m === 'POST') {
        if (backupJob?.state === 'queued' || backupJob?.state === 'running') return problem(409, 'BACKUP_IN_PROGRESS', 'Đang sao lưu — chờ xong rồi thử lại');
        backupJob = { id: randomUUID(), state: 'queued', requested_at: new Date().toISOString() };
        return reply(202, backupsPage());
      }
      if (p === '/system/backups/schedule' && m === 'PUT') {
        const b = body as { frequency?: string; time_of_day?: string };
        if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(String(b.time_of_day ?? ''))) {
          return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { time_of_day: 'Giờ chạy sao lưu dạng HH:MM (00:00–23:59)' } });
        }
        Object.assign(backup, { frequency: b.frequency, time_of_day: b.time_of_day });
        backupConfigured = true;
        return reply(200, backupsPage());
      }
      if (!ctx.owner) return problem(403, 'FORBIDDEN', 'Chỉ Owner');
      if (p === '/system/backups/download' && m === 'GET') {
        if (!pin(ctx, 'backup.download')) return true;
        const item = backups.find((x) => x.key === q.get('key'));
        if (!item) return problem(404, 'NOT_FOUND', 'Không tìm thấy bản sao lưu');
        return ctx.text(200, 'application/octet-stream', `GHBACKUP-MOCK ${item.key}`, `gen-harness-${item.key.split('/').pop()}`);
      }
      if (p === '/system/backups/restore' && m === 'POST') {
        if (!pin(ctx, 'backup.restore')) return true;
        const b = body as { key?: string; confirm?: string };
        if (String(b.confirm ?? '').trim() !== 'KHÔI PHỤC') {
          return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { confirm: 'Gõ đúng "KHÔI PHỤC" để xác nhận' } });
        }
        if (!backups.some((x) => x.key === b.key)) return problem(404, 'NOT_FOUND', 'Không tìm thấy bản sao lưu');
        if (!restore.can_request) return problem(409, 'RESTORE_UNAVAILABLE', 'Máy chủ chưa bật nhận yêu cầu khôi phục từ Console');
        if (restore.state === 'requested' || restore.state === 'running') return problem(409, 'RESTORE_IN_PROGRESS', 'Đang khôi phục');
        Object.assign(restore, { state: 'requested', key: b.key, requested_at: new Date().toISOString(), safety_key: null, message: null });
        return reply(202, backupsPage());
      }
      return problem(404, 'NOT_FOUND', 'Không tìm thấy');
    }

    // ── v0.1.41 (F-84): Chi phí AI hôm nay + trần ngân sách + giá model ──
    if (p.startsWith('/system/ai-cost')) {
      if (p === '/system/ai-cost' && m === 'GET') {
        if (!has(ctx, 'system.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        const date = q.get('date') || vnDate(Date.now());
        if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { date: 'Ngày dạng YYYY-MM-DD' } });
        return reply(200, aiCostView(date));
      }
      if (p === '/system/ai-cost/budget' && m === 'PUT') {
        if (!has(ctx, 'system.manage')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        const v = (body as { daily_budget_vnd?: unknown }).daily_budget_vnd;
        if (v !== null && !(isVnd(v) && Number.isInteger(v))) {
          return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { daily_budget_vnd: 'Trần chi phí là số tiền nguyên (₫), không âm — để trống nếu không giới hạn' } });
        }
        dailyBudget = v as number | null;
        return reply(200, aiCostView(vnDate(Date.now())));
      }
      if (seg[0] === 'system' && seg[1] === 'ai-cost' && seg[2] === 'prices' && seg.length === 4 && m === 'PUT') {
        if (!has(ctx, 'system.manage')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        const mdl = costModels.find((x) => x.model_id === decodeURIComponent(seg[3]));
        if (!mdl) return problem(404, 'NOT_FOUND', 'Model không tồn tại');
        if (mdl.price_source === 'subscription') {
          return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { in_vnd_per_mtok: 'Nguồn CLI trả theo gói — không đặt giá theo token' } });
        }
        const b = body as { in_vnd_per_mtok?: unknown; out_vnd_per_mtok?: unknown };
        const errors: Record<string, string> = {};
        if (b.in_vnd_per_mtok !== null && !isVnd(b.in_vnd_per_mtok)) errors.in_vnd_per_mtok = 'Giá là số tiền (₫), không âm';
        if (b.out_vnd_per_mtok !== null && !isVnd(b.out_vnd_per_mtok)) errors.out_vnd_per_mtok = 'Giá là số tiền (₫), không âm';
        if (Object.keys(errors).length) return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors });
        mdl.in_vnd_per_mtok = b.in_vnd_per_mtok as number | null;
        mdl.out_vnd_per_mtok = b.out_vnd_per_mtok as number | null;
        mdl.price_source = mdl.in_vnd_per_mtok == null || mdl.out_vnd_per_mtok == null ? 'none' : 'owner';
        return reply(200, aiCostView(vnDate(Date.now())));
      }
      return problem(404, 'NOT_FOUND', 'Không tìm thấy');
    }

    // ── v0.1.40 (F-12): Bản sao ngoài máy ──
    if (p.startsWith('/system/offsite')) {
      if (p === '/system/offsite' && m === 'GET') {
        if (!has(ctx, 'system.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        return reply(200, offsiteView());
      }
      if (p === '/system/offsite/run' && m === 'POST') {
        if (!has(ctx, 'system.manage')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        if (!offsite.can_request) return problem(409, 'OFFSITE_UNAVAILABLE', UNAVAILABLE_TITLE, { manual_command: MANUAL('run') });
        if (offsite.request.state === 'requested' || offsite.state === 'running') return problem(409, 'OFFSITE_IN_PROGRESS', 'Đang sao lưu ra ổ ngoài');
        offsite.request = { state: 'requested', action: 'run', requested_at: new Date().toISOString() };
        return reply(202, offsiteView());
      }
      if (!ctx.owner) return problem(403, 'FORBIDDEN', 'Chỉ Owner');
      if (p === '/system/offsite/destination' && m === 'PUT') {
        if (!pin(ctx, 'offsite.destination')) return true;
        const path = String((body as { path?: unknown }).path ?? '').trim();
        if (!path) return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { path: 'Nhập đường dẫn ổ USB/NAS trên máy chủ' } });
        if (path === '/' || path.startsWith('/home') || path.startsWith('/root')) {
          return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { path: 'Đây là ổ chính của máy chủ — chọn ổ USB/NAS khác' } });
        }
        if (!offsite.can_request) return problem(409, 'OFFSITE_UNAVAILABLE', UNAVAILABLE_TITLE, { manual_command: MANUAL('set', path) });
        offsite.request = { state: 'requested', action: 'set', requested_at: new Date().toISOString() };
        offsiteRequestPath = path;
        return reply(202, offsiteView());
      }
      if (p === '/system/offsite/disable' && m === 'POST') {
        if (!pin(ctx, 'offsite.disable')) return true;
        if (!offsite.can_request) return problem(409, 'OFFSITE_UNAVAILABLE', UNAVAILABLE_TITLE, { manual_command: MANUAL('disable') });
        offsite.request = { state: 'requested', action: 'disable', requested_at: new Date().toISOString() };
        return reply(202, offsiteView());
      }
      if (p === '/system/offsite/recovery-kit' && m === 'GET') {
        if (!pin(ctx, 'offsite.recovery_kit')) return true;
        if (!offsite.key_present) return problem(409, 'OFFSITE_KEY_MISSING', OFFSITE_KEY_MISSING_TITLE);
        // Chép đúng gh/system_api/offsite.py RECOVERY_STEPS / RECOVERY_WARNING.
        return reply(200, {
          key: OFFSITE_KEY_MOCK, key_id: offsite.key_id, created_hint: '2026-10-01',
          steps: [
            'Cài Gen-Harness trên máy mới theo hướng dẫn cài đặt (chưa cần tạo dữ liệu gì).',
            'Cắm ổ USB (hoặc mở thư mục NAS) chứa bản sao ngoài máy, chọn tệp .ghbundle mới nhất.',
            'Chạy trên máy mới: genh import --yes <tệp .ghbundle>',
            "Khi được hỏi mật khẩu gói, nhập Khoá khôi phục này (gõ đủ cả dấu '-').",
            'Đăng nhập Console bằng tài khoản Owner cũ và kiểm tra dữ liệu.',
          ],
          warning: 'Cất Bộ khôi phục TÁCH khỏi ổ USB: ai có cả hai sẽ đọc được toàn bộ dữ liệu',
        });
      }
      if (p === '/system/offsite/portable' && m === 'GET') {
        if (!pin(ctx, 'offsite.portable')) return true;
        if (!offsite.key_present) return problem(409, 'OFFSITE_KEY_MISSING', OFFSITE_KEY_MISSING_TITLE);
        // Chép đúng gh/system_api/offsite.portable (conflict PORTABLE_IN_PROGRESS).
        if (portableBusy) return problem(409, 'PORTABLE_IN_PROGRESS', 'Đang tạo một gói mang đi khác — chờ xong rồi thử lại');
        return ctx.text(200, 'application/octet-stream', 'GHBUNDLE-MOCK', 'gen-harness-portable.ghbundle');
      }
      return problem(404, 'NOT_FOUND', 'Không tìm thấy');
    }

    // ── Dữ liệu & lưu trữ (spec I) ──
    if (p === '/retention-policies' && m === 'GET') {
      if (!has(ctx, 'system.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      return reply(200, retentionList());
    }
    if (p === '/retention-policies' && m === 'PATCH') {
      if (!has(ctx, 'system.manage')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (!pin(ctx, 'policy.change')) return true;
      const b = body as { dataset?: string; keep_days?: number | null; anonymize_after_days?: number | null };
      const dataset = b.dataset as RetentionDataset;
      if (!RETENTION_DATASETS.includes(dataset)) return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { dataset: 'Tập dữ liệu không hợp lệ' } });
      // v0.1.40 (F-2): như API — ops.action_log chỉ ghi thêm (RETENTION_NOT_APPLICABLE).
      if (dataset === 'ops.action_log' && b.keep_days != null) {
        return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { keep_days: 'Nhật ký hành động chỉ ghi thêm — không đặt hạn xoá được' } });
      }
      if (b.keep_days != null && (!Number.isInteger(b.keep_days) || b.keep_days < 1 || b.keep_days > 3650)) {
        return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { keep_days: 'Số ngày từ 1 đến 3650' } });
      }
      // v0.1.40 (F-2): như API — bảng phân vùng chỉ Owner; đặt số ngày cho tập bị xoá thật cần confirm_delete.
      const partition = RETENTION_MODE[dataset]?.mode === 'partition';
      if (partition && !ctx.owner) return problem(403, 'FORBIDDEN', 'Vai trò của bạn không có quyền thao tác này', { detail: 'Chỉ Owner đổi được hạn lưu của dữ liệu xoá theo tháng' });
      if (b.keep_days != null && (body as { confirm_delete?: boolean }).confirm_delete !== true) {
        return problem(422, 'RETENTION_CONFIRM_REQUIRED', 'Cần xác nhận xoá vĩnh viễn', {
          errors: { keep_days: `Cần xác nhận: ${partition ? 'cả tháng dữ liệu cũ hơn' : 'dữ liệu cũ hơn'} ${b.keep_days} ngày sẽ bị XOÁ VĨNH VIỄN ở lượt dọn kế tiếp (05:00 hằng ngày) — chỉ lấy lại được từ bản sao lưu` },
        });
      }
      retention.set(dataset, { keep_days: b.keep_days ?? null, anonymize_after_days: b.anonymize_after_days ?? null });
      return reply(200, retentionList());
    }
    if (seg[0] === 'persons' && seg[2] === 'data-requests') {
      const person = opts.getPersons().find((x) => x.id === seg[1]);
      if (!person) return problem(404, 'NOT_FOUND', 'Người');
      if (seg.length === 3 && m === 'GET') {
        if (!has(ctx, 'system.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        return reply(200, dataRequests.get(person.id) ?? []);
      }
      if (seg.length === 3 && m === 'POST') {
        if (!has(ctx, 'system.manage')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        if (!pin(ctx, 'data.export_delete')) return true;
        const kind = String((body as { kind?: string }).kind ?? '');
        if (!['export', 'erase', 'restrict'].includes(kind)) return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { kind: 'Loại yêu cầu không hợp lệ' } });
        const id = randomUUID();
        let result: unknown;
        if (kind === 'export') {
          result = { person: { id: person.id, code: person.code, display_name: person.name }, identities: [], scores: [], notebook: [] };
        } else if (kind === 'erase') {
          person.name = 'Người dùng đã xoá';
          result = { erased: true };
        } else {
          restrictedPersons.add(person.id);
          result = { restricted: true };
        }
        const list = dataRequests.get(person.id) ?? [];
        list.unshift({ id, kind, status: 'completed', requested_at: new Date().toISOString(), completed_at: new Date().toISOString() });
        dataRequests.set(person.id, list);
        return reply(201, { id, kind, status: 'completed', result });
      }
    }

    return false;
  }

  // ── Bước 10–11 (gọi thẳng từ `mock-api.ts`, không qua `handle`) ──
  function step10(body: Record<string, unknown>, existingEmails: Set<string>): StepResult<{ invited: Step10Invited[] }> {
    const invitesIn = Array.isArray(body.invites) ? (body.invites as Array<Record<string, unknown>>) : [];
    const errors: Record<string, string> = {};
    const seen = new Set<string>();
    const invited: Step10Invited[] = [];
    invitesIn.forEach((inv, idx) => {
      const email = String(inv.email ?? '').trim().toLowerCase();
      const name = String(inv.display_name ?? '').trim();
      const role = String(inv.role ?? '');
      if (!EMAIL_RE.test(email)) errors[`invites.${idx}.email`] = 'Email chưa đúng định dạng';
      else if (seen.has(email)) errors[`invites.${idx}.email`] = 'Email bị lặp trong danh sách';
      else if (existingEmails.has(email)) errors[`invites.${idx}.email`] = 'Email đã có tài khoản';
      if (!name) errors[`invites.${idx}.display_name`] = 'Nhập tên hiển thị';
      if (!INVITE_ROLES.has(role)) errors[`invites.${idx}.role`] = 'Chọn vai trò';
      if (errors[`invites.${idx}.email`] || errors[`invites.${idx}.display_name`] || errors[`invites.${idx}.role`]) return;
      seen.add(email);
      invited.push({ id: randomUUID(), display_name: name, email, role, temp_password: randomUUID().replace(/-/g, '').slice(0, 10) });
    });
    if (Object.keys(errors).length) return { ok: false, status: 422, code: 'VALIDATION_ERROR', title: 'Dữ liệu chưa hợp lệ', extra: { errors } };
    return { ok: true, value: { invited } };
  }

  function step11(body: Record<string, unknown>): StepResult<{ backup: BackupConfig }> {
    const time_of_day = String(body.time_of_day ?? '02:00');
    if (!TIME_RE.test(time_of_day)) {
      return { ok: false, status: 422, code: 'VALIDATION_ERROR', title: 'Dữ liệu chưa hợp lệ', extra: { errors: { time_of_day: 'Giờ chạy sao lưu dạng HH:MM (00:00–23:59)' } } };
    }
    const frequency = ['daily', 'weekly', 'monthly'].includes(String(body.frequency)) ? (body.frequency as BackupConfig['frequency']) : 'daily';
    // v0.1.40: chỉ còn 'local' (bản sao ra ngoài máy đi qua /system/offsite).
    const destination: BackupConfig['destination'] = 'local';
    const retention_count = Math.min(365, Math.max(1, Math.round(Number(body.retention_count ?? 7)) || 7));
    Object.assign(backup, { frequency, time_of_day, retention_count, destination });
    backupConfigured = true;
    return { ok: true, value: { backup: { ...backup } } };
  }

  /** v0.1.28 (UX N4): "Để sau" ở bước 11 = lịch mặc định hằng ngày 02:00 (như `gh.setup.routes.skip`). */
  function defaultBackup(): void {
    backupConfigured = true;
  }

  return {
    handle,
    step10,
    step11,
    defaultBackup,
    backupConfigured: () => backupConfigured,
    /** v0.1.36 (F-6): bản sao lưu mới nhất (mọi nguồn, cả pre-update) — `GET /system/health` (mock-api.ts) đọc. */
    latestBackupAt: (): string | null => backups.reduce<string | null>((max, b) => (!max || b.taken_at > max ? b.taken_at : max), null),
    /** v0.1.40 (F-12): khối `offsite` của `GET /system/health` (mock-api.ts đọc). */
    offsiteHealth: () => {
      const v = offsiteView();
      return { configured: v.configured, state: v.state, error_code: v.error_code, last_success_at: v.last_success_at, age_days: v.age_days, stale: v.stale };
    },
    hooks: {
      /** `__mock/p3/system/offsite` {…OffsiteState một phần; `days_ago` đặt lần thành công gần nhất (null = chưa có)}. */
      offsite: (b: Record<string, unknown>) => {
        const { days_ago: daysAgo, portable_busy: busy, ...rest } = b ?? {};
        if (typeof busy === 'boolean') portableBusy = busy;
        Object.assign(offsite, rest);
        if (typeof daysAgo === 'number') offsite.last_success_at = new Date(Date.now() - daysAgo * DAY).toISOString();
        if (daysAgo === null) offsite.last_success_at = '';
        return offsiteView();
      },
      /**
       * `__mock/p3/system/grant` {role, permission, scope} — đổi MỘT ô ma trận quyền kể cả quyền ngoài 7 cột sửa được (vd
       * cấp system.read/system.manage cho Manager như Owner tự cấp ở API thật). Trả giá trị cũ để test trả lại.
       */
      grant: (b: { role: RoleCode; permission: string; scope: string }) => {
        const idx = opts.roleOrder.indexOf(b.role);
        const row = opts.matrix[b.permission];
        if (idx < 0 || !row) return null;
        const prev = row[idx];
        row[idx] = b.scope;
        return { prev };
      },
    } as unknown as Record<string, (...args: never[]) => unknown>,
    dispose: () => {},
  };
}
