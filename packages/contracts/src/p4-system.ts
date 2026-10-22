/**
 * Hợp đồng API giai đoạn 4 · Điều khiển hệ thống (PLAN 4.5/4.6, ARCHITECTURE §7.4/§8.3/§11,
 * `apps/api/gh/system_api/routes.py` + `apps/api/gh/setup/routes.py`).
 *
 * Chỉ phần MỚI của 4.5/4.6: ma trận quyền (`/permissions`), nhóm đang lắng nghe (`/listening-groups`), ranh
 * giới có trách nhiệm (`/boundaries*`), nhật ký hệ thống (`/audit-log*` — cùng đường đọc `gh.audit.routes`
 * với `/audit` đã có, chỉ thêm bộ lọc theo đối tượng/thời gian nên tái dùng `AuditItem`/`AuditPage` của
 * `schema.ts`), Dữ liệu & lưu trữ (`/retention-policies`, `/persons/{id}/data-requests` — spec I), và bước
 * thiết lập 10–11 (`/setup/steps/10`, `/setup/steps/11`). "Bộ não AI" (`/providers*`, `/cli/*`,
 * `/failover-rules`, `PATCH /providers/chain`) đã có hợp đồng từ giai đoạn 2/4.2 ở `endpoints.ts`/`p4-api.ts`
 * — màn `system` chỉ ĐỌC LẠI các hook đó (`useProviders`, `useCliProfiles`, `useFailoverRules`), KHÔNG lặp lại
 * ở đây.
 */
import type { ApiClient } from './client';
import { accessEndpoints } from './access';
import type { AuditPage } from './schema';
import type { GroupKind, ListenMode, ViewScope } from './phase2';

// ── Quyền hạn: ma trận ───────────────────────────────────────────────────────

export type PermScope = 'all' | 'team' | 'assigned' | 'none';

/** F-58 (v0.1.45): quyền chỉ có nghĩa ở phạm vi `all` (gương `gh.auth.deps.ALL_ONLY`) — team/assigned coi như không có. */
export const ALL_ONLY_PERMS: readonly string[] = ['system.manage'];

export interface PermissionColumn {
  key: string;
  label: string;
  permissions: string[];
}

export interface PermissionRole {
  code: 'owner' | 'manager' | 'operator' | 'agent_staff' | 'auditor';
  name: string;
  meta: string;
  permissions: Record<string, PermScope>;
}

export interface PermissionsPage {
  columns: PermissionColumn[];
  roles: PermissionRole[];
}

export interface PermissionPatchBody {
  role: PermissionRole['code'];
  permission: string;
  scope: PermScope;
}

// ── Quyền hạn: nhóm đang lắng nghe ───────────────────────────────────────────

/** `/listening-groups` — cùng hình `ChannelGroup` (phase2) + `channel`/`msgs_24h` (thiết kế `listenGroups`). */
export interface ListeningGroup {
  id: string;
  code: string;
  name: string;
  members: number;
  kind: GroupKind | string;
  listen_mode: ListenMode;
  view_scope: ViewScope;
  channel: string;
  msgs_24h: number;
}

// ── Quyền hạn: ranh giới có trách nhiệm ──────────────────────────────────────

export interface Boundary {
  code: string;
  label: string;
  enabled: boolean;
  locked: boolean;
  params: Record<string, unknown>;
}

export interface BoundaryPatchBody {
  enabled?: boolean;
  params?: Record<string, unknown>;
}

/**
 * 2 trong 8 khoá cứng ARCHITECTURE §7.4 không có dòng `ops.policy_boundaries` (bất biến ở tầng mã nguồn/CSDL,
 * không phải công tắc) — hiển thị tĩnh cạnh 6 dòng thật từ `/boundaries` để đủ "8 khoá cứng" thiết kế đòi hỏi.
 */
export const STATIC_HARD_BOUNDARIES: ReadonlyArray<{ label: string; hint: string }> = [
  { label: 'Kho thô và Nhật ký hành động chỉ được ghi thêm, không sửa/xoá', hint: 'Bất biến tầng CSDL — không có công tắc, kể cả Owner.' },
  { label: 'PIN cho thao tác nhạy cảm; bí mật được mã hoá', hint: 'Bất biến tầng mã nguồn — không có công tắc.' },
];

// ── Nhật ký hệ thống ──────────────────────────────────────────────────────────

export interface SystemAuditQuery {
  cursor?: string;
  limit?: number;
  actor_type?: string;
  action?: string;
  target_type?: string;
  target_id?: string;
  since?: string;
  until?: string;
  /** Ô tìm (người/agent, hành động, đối tượng) — máy chủ tìm trên TOÀN BỘ nhật ký. */
  q?: string;
  /** Mã hành động (cách nhau dấu phẩy) mà nhãn tiếng Việt khớp ô tìm — máy chủ chỉ lưu mã. */
  q_actions?: string;
  /** Mã tác nhân hệ thống/bộ nhận tin (cách nhau dấu phẩy) mà tên tiếng Việt khớp ô tìm. */
  q_actors?: string;
  /** Ẩn dòng chung `http.*` và lượt kiểm chuỗi băm bình thường (mặc định của màn Nhật ký). */
  hide_noise?: boolean;
}

// ── Dữ liệu & lưu trữ (spec I) ───────────────────────────────────────────────

/** 5 tập dữ liệu sửa được hạn (PATCH /retention-policies). */
export type RetentionEditableDataset = 'raw.events' | 'clean.meaning_units' | 'ops.action_log' | 'memory.entries' | 'agent.model_calls';
/** v0.1.40 (F-2): thêm dòng chỉ đọc `agent.browser_jobs.result` (kết quả việc trình duyệt nền, cố định 14 ngày). */
export type RetentionDataset = RetentionEditableDataset | 'agent.browser_jobs.result';

/**
 * v0.1.40 (F-2): cách việc nền dọn tập dữ liệu — `partition` = xoá theo cả phân vùng tháng (raw.events,
 * clean.meaning_units, agent.model_calls); `batch` = xoá từng lô dòng quá hạn; `not_applicable` = không dọn
 * (ops.action_log chỉ ghi thêm).
 */
export type RetentionMode = 'partition' | 'batch' | 'not_applicable';

export interface RetentionPolicy {
  dataset: RetentionDataset;
  keep_days: number | null;
  /** Cấu hình cũ — hệ thống CHƯA thi hành ẩn danh; web không hiện, chỉ gửi lại nguyên giá trị khi PATCH. */
  anonymize_after_days: number | null;
  /** v0.1.40 — thiếu ở api cũ. */
  mode?: RetentionMode;
  /** v0.1.40: false ⇒ không có nút Sửa (ops.action_log, agent.browser_jobs.result). Thiếu ở api cũ. */
  editable?: boolean;
  /** v0.1.40: câu giải thích cho Sếp (vd "Xoá theo cả tháng"); null = không có. */
  note?: string | null;
  /** v0.1.40: lần việc nền dọn tập này gần nhất (ISO); null = chưa dọn. */
  last_run_at?: string | null;
  /** v0.1.40: số đã xoá ở lần dọn gần nhất (bảng phân vùng: số THÁNG; còn lại: số dòng); null = chưa dọn / không áp dụng. */
  last_deleted?: number | null;
  /** v0.1.40: false ⇒ lượt dọn gần nhất của tập này LỖI (last_deleted = 0 không có nghĩa là xong); null = chưa dọn. */
  last_ok?: boolean | null;
  /**
   * v0.1.40 (F-2): hạn đặt TRƯỚC v0.1.40 (lúc chỉ để hiển thị) — việc dọn CHƯA thi hành cho tới khi Owner lưu lại có
   * xác nhận xoá vĩnh viễn. Thiếu ở api cũ.
   */
  needs_confirm?: boolean;
}

export interface RetentionPatchBody {
  dataset: RetentionEditableDataset;
  keep_days?: number | null;
  anonymize_after_days?: number | null;
  /**
   * v0.1.40 (F-2): bắt buộc `true` khi đặt số ngày cho tập dữ liệu bị xoá thật (mọi tập trừ ops.action_log) — người
   * dùng đã đồng ý dữ liệu quá hạn bị XOÁ VĨNH VIỄN ở lượt dọn kế tiếp. Thiếu ⇒ 422 RETENTION_CONFIRM_REQUIRED.
   * Bảng phân vùng (raw.events, clean.meaning_units, agent.model_calls) chỉ Owner đổi được (403 với vai trò khác).
   */
  confirm_delete?: boolean;
}

export type DataRequestKind = 'export' | 'erase' | 'restrict';

export interface DataRequestResult {
  id: string;
  kind: DataRequestKind;
  status: string;
  result: unknown;
}

export interface DataRequestRecord {
  id: string;
  kind: DataRequestKind;
  status: string;
  requested_at: string;
  completed_at: string | null;
}

// ── Trình thiết lập bước 10–11 (giai đoạn 4.6) ───────────────────────────────

export interface Step10Invite {
  display_name: string;
  email: string;
  role: 'manager' | 'operator' | 'agent_staff' | 'auditor';
}

export interface Step10Body {
  invites: Step10Invite[];
}

export interface Step10Invited extends Step10Invite {
  id: string;
  temp_password: string;
}

export interface Step11Body {
  frequency: 'daily' | 'weekly' | 'monthly';
  time_of_day: string;
  retention_count: number;
  /** v0.1.40: chỉ còn 'local' — bản sao ra ngoài máy đi qua `offsite` (ổ USB/NAS), không qua S3/MinIO. */
  destination: 'local';
}

export type BackupConfig = Step11Body;

// ── Trình thiết lập bước 8–9 + việc thiết lập tiếp ───────────────────────────

export interface Step8Body {
  name: string;
  role_desc: string;
  voice?: string;
  speak_when?: string;
  template?: string | null;
  /**
   * v0.1.55: tuỳ chọn — CÓ thì máy chủ trả lời thử một lượt (gọi model), KHÔNG có thì không thử (bước 8 chỉ cần chọn mẫu).
   * Thiếu `name` / `role_desc` thì máy chủ điền mặc định, nhưng web luôn gửi cả hai (mock-api.ts đòi).
   */
  try_message?: string;
}

export interface Step8Agent {
  id: string;
  name: string;
  try_reply: string | null;
  /** Câu chữ cho người đọc. Máy chủ ≤ v0.1.29 có thể trả đối tượng `{reasons}` — web phải qua `friendlyError`. */
  try_error: string | null;
  /** v0.1.30: mã lỗi (vd `MODEL_UNAVAILABLE`) + lý do kỹ thuật. */
  try_error_code?: string | null;
  try_reasons?: string[];
  /** v0.1.68 (F079): số kênh agent nghe (agent mới nghe sẵn mọi kênh đã nối; 0 = chưa nối kênh nào). Máy chủ cũ không có trường. */
  listening_channels?: number;
}

export interface Step9Body {
  /** null = giữ nguyên mức hiện tại của agent (mở lại sau Hoàn tất chỉ để xác nhận ranh giới — không đòi PIN). */
  autonomy_level: 3 | 4 | null;
  ack_boundaries: boolean;
}

/** `GET /setup/steps/9` — agent tạo ở bước 8 (bước 9 đặt mức cho đúng agent này) + mức tự trị hiện tại. */
export interface Step9State {
  agent: { id: string; name: string; autonomy_level: number } | null;
}

/** `GET /setup/follow-up` — bước tuỳ chọn chưa xong trong trình thiết lập; `done` suy từ dữ liệu thật. */
export interface SetupFollowUpItem {
  /** 4–11 = bước tuỳ chọn của trình thiết lập; v0.1.39: 13 Kết nối Facebook, 14 Nối Gen-hub (không phải bước). */
  n: number;
  key: string;
  title: string;
  /** Trạng thái trong trình thiết lập; `done` bên dưới còn tính cả dữ liệu thật làm ở Console. */
  status: 'todo' | 'doing' | 'skipped' | 'done';
  done: boolean;
}

/** `GET/POST /system/update` — nút "Cập nhật ngay" (genh trên máy chủ làm việc thật, xem gh/system_api/update.py). */
export type SystemUpdateState = 'idle' | 'requested' | 'running' | 'done' | 'failed' | 'stalled';
export type SystemUpdateStalledReason = 'not_picked_up' | 'process_gone' | 'linger_off' | 'watcher_failed' | null;
/** Giá trị `linger` api chỉ nhận trong tập này (genh ghi run/autostart-status.json, run/nightly-status.json); lạ ⇒ 'unknown'. */
export type SystemLinger = 'yes' | 'no' | 'unknown' | 'not_applicable';
/**
 * v0.1.53 (F-99): lịch tự cập nhật đêm (genh ghi run/nightly-status.json, api đã lọc kiểu/tập giá trị). `mechanism`
 * 'unknown' = giá trị lạ trong tệp; các trường bool/ngày null = không rõ. Mọi trường là chuỗi/bool/null.
 */
export interface SystemUpdateNightly {
  mechanism: 'systemd' | 'cron' | 'launchd' | 'schtasks' | '' | 'unknown';
  enabled: boolean | null;
  active: boolean | null;
  /** Sếp đã chủ động tắt lịch đêm (`genh auto-update disable`). */
  opted_out: boolean | null;
  /** v0.1.53: lịch đêm dùng chung của máy thuộc một bản cài KHÁC còn sống (genh cũ không ghi ⇒ null). */
  owned_by_other?: boolean | null;
  last_run_at: string | null;
  next_run_at: string | null;
}
/**
 * v0.1.37: lần cập nhật 'failed' vì genh nhận tín hiệu dừng (GH-E94B — máy tắt/khởi động lại/bị dừng tay) mà KHÔNG để
 * máy dở dang: `rolled_back` = chưa đụng gì / đã tự quay về bản cũ; `resume` = máy tắt sau khi đã đổi CSDL — giữ bản
 * mới, chạy lại để đi tiếp. null = không phải trường hợp này (hoặc quay về chưa trọn); thiếu ở api cũ.
 */
export type SystemUpdateInterrupted = 'rolled_back' | 'resume' | null;
/**
 * v0.1.55 (G2): vì sao KHÔNG hiện nút "Cập nhật ngay" (null ⇔ hiện được nút). Thứ tự ưu tiên máy chủ xét:
 * `not_owner` (người gọi không phải Owner) → `in_progress` (đang requested/running) → `genh_unlinked` (không có trình nhận yêu
 * cầu / hộp thư request không ghi được — cùng điều kiện `can_request = false`) → `watcher_stalled` (người gác cập nhật lỗi) →
 * `up_to_date` (không có bản mới). Mã cố định — câu lý do + việc cần làm do web ghép (`blockReasonCopy`).
 */
export type SystemUpdateBlockReason = 'not_owner' | 'in_progress' | 'genh_unlinked' | 'watcher_stalled' | 'up_to_date';
export interface SystemUpdate {
  /** Phiên bản đang chạy (genh ghi vào hộp thư); null ở dev/test. */
  current: string | null;
  latest: string | null;
  update_available: boolean;
  /** Cơ chế nhận yêu cầu trên máy chủ (systemd/cron/launchd); null ⇒ Owner tự chạy lệnh một lần. */
  updater: string | null;
  /** Có hộp thư với máy chủ (cài bằng genh) hay không. */
  linked: boolean;
  can_request: boolean;
  /** v0.1.55: xem `SystemUpdateBlockReason`; thiếu ở api cũ ⇒ web dựa vào `can_request` / `update_available` như trước. */
  request_block_reason?: SystemUpdateBlockReason | null;
  state: SystemUpdateState;
  /**
   * v0.1.37 (F-34): lý do 'stalled' — `not_picked_up` = yêu cầu nằm quá 15 phút (watcher không chạy); `process_gone` =
   * 'running' mà tiến trình genh đã chết (máy khởi động lại / quá 60 phút không còn nhịp sống). null khi không 'stalled';
   * thiếu ở api cũ.
   * v0.1.53 (F-99): `linger_off` = yêu cầu quá 15 phút mà linger đang tắt (tiến trình nền chỉ chạy khi có người đăng
   * nhập); `watcher_failed` = trình nhận yêu cầu (gen-harness-update-request.path/.service) báo lỗi.
   */
  stalled_reason?: SystemUpdateStalledReason;
  /**
   * v0.1.37: yêu cầu đang xếp hàng sau một lần cập nhật/khôi phục khác đang chạy trên máy chủ (vd lịch đêm — genh còn
   * nhịp sống); khi đó yêu cầu nằm quá 15 phút vẫn là 'requested', không phải `not_picked_up`. Thiếu ở api cũ.
   */
  host_busy?: boolean;
  /** v0.1.37: xem `SystemUpdateInterrupted`. */
  interrupted?: SystemUpdateInterrupted;
  message: string | null;
  from: string | null;
  to: string | null;
  started_at: string | null;
  finished_at: string | null;
  requested_at: string | null;
  release_url: string | null;
  release_notes: string | null;
  /**
   * v0.1.33: lúc bản `latest` thành bản chính thức (ISO — dấu promote trong ghi chú Release, không có thì
   * published_at); lịch tự cập nhật đêm (~03:00) chỉ cài bản đã là bản chính thức ≥ 24 giờ. null = không rõ.
   */
  published_at?: string | null;
  /**
   * v0.1.33: lịch tự cập nhật đêm đang bật (genh ghi vào genh.json khi cài / `genh auto-update enable|disable`);
   * null = genh cũ chưa ghi hoặc không đọc được — Console không hứa "Tự cài".
   */
  auto_update_enabled?: boolean | null;
  /**
   * v0.1.34: bản đã lỗi ở lần cập nhật trước và đã quay về bản cũ (genh ghi run/update-blocked.json) — lịch đêm không
   * tự cài lại đúng bản này; null = không có bản nào bị chặn.
   */
  blocked_version?: string | null;
  /**
   * v0.1.34: `rollback_failed` trong run/update-blocked.json của bản bị chặn — tự quay về bản cũ CŨNG thất bại, máy cần
   * xử lý tay. null = không có bản bị chặn (hoặc api cũ) — Console rơi về dò chữ trong thông điệp genh.
   */
  blocked_rollback_failed?: boolean | null;
  /**
   * v0.1.53 (F-96): bản chính thức CÓ dấu promote, mới hơn bản đang chạy (tăng dần theo semver, ≤ 10), mỗi bản kèm
   * `eligible_at` = lúc đủ thời gian chín 24 giờ (ISO) — lịch đêm cài bản cao nhất đã đủ hạn, không nhất thiết là
   * `latest`. Thiếu ở api cũ ⇒ Console rơi về cách tính theo `published_at`.
   */
  nightly_candidates?: Array<{ tag: string; eligible_at: string }>;
  /** v0.1.53 (F-99): trạng thái lịch tự cập nhật đêm; null = genh cũ chưa ghi run/nightly-status.json. */
  nightly?: SystemUpdateNightly | null;
  /** v0.1.30: lần hỏi GitHub gần nhất thành công (ISO); null = chưa hỏi được. */
  checked_at?: string | null;
  /** v0.1.30: `POST /system/update/check` bị giới hạn (≤ 1 lần / 30 giây) — trả kết quả đang đệm. */
  throttled?: boolean;
}

/** `GET /system/backups` (v0.1.20, gh/system_api/backups.py) — Điều khiển hệ thống › Dữ liệu & lưu trữ. */
export type BackupTrigger = 'manual' | 'scheduled' | 'pre-update' | 'pre-restore' | 'pre-import';
export interface BackupItem {
  /** Khoá ObjectStore, ví dụ `backups/20260928T020000Z-1a2b3c4d.pgcustom.enc`. */
  key: string;
  taken_at: string;
  size_bytes: number;
  /** Nguồn tạo; null với bản ghi trước v0.1.20. */
  trigger: BackupTrigger | null;
  encrypted: boolean;
  key_id: 'backup' | 'master';
}
export type BackupJobState = 'queued' | 'running' | 'done' | 'failed' | 'stalled';
export interface BackupJob {
  id?: string;
  state: BackupJobState;
  requested_at?: string;
  started_at?: string;
  finished_at?: string;
  key?: string;
  /** Câu thân thiện cho Sếp (F106). */
  message?: string | null;
  /** Lỗi gốc (stderr pg_dump, đã che mật khẩu) — chỉ hiện trong "Chi tiết kỹ thuật". */
  technical?: string | null;
}
export type RestoreState = 'idle' | 'requested' | 'running' | 'done' | 'failed' | 'stalled';
export interface BackupRestoreStatus {
  /** Máy chủ có watcher nhận yêu cầu khôi phục (genh ≥ v0.1.20). */
  can_request: boolean;
  state: RestoreState;
  key: string | null;
  /** Bản sao lưu an toàn genh chụp ngay trước khi khôi phục (đường lui). */
  safety_key: string | null;
  message: string | null;
  started_at: string | null;
  finished_at: string | null;
  requested_at: string | null;
}
export interface BackupSchedule {
  frequency: 'daily' | 'weekly' | 'monthly';
  time_of_day: string;
}
export interface BackupsPage {
  items: BackupItem[];
  /** null ⇒ chưa đặt lịch (bỏ qua bước 11). */
  schedule: BackupSchedule | null;
  timezone: string;
  retention: { daily: number; weekly: number; monthly: number; recent_hours: number };
  job: BackupJob | null;
  restore: BackupRestoreStatus;
}

/**
 * v0.1.36 (F-6): `GET /system/health` (quyền `system.read`; KHÔNG thuộc `/ready` — genh dùng `/ready` để quyết quay về
 * bản cũ). Tổng hợp sức khoẻ: Bộ xử lý nền (arq), Trình duyệt nền, hàng lỗi (DLQ), lịch chạy, sao lưu, cập nhật, ổ đĩa,
 * cộng các sự cố đang mở ở `ops.health_alerts` (`issues` — nguồn của dải "Cần Sếp xử lý" đầu Tổng quan). Mọi trường là
 * chuỗi/số/bool — web không bao giờ render object.
 */
export type HealthSeverity = 'bad' | 'warn';
export interface HealthIssue {
  /** Khoá khử trùng lặp, vd `channel.down:zalo`, `update.failed`. */
  key: string;
  /** Cùng `kind` của chuông: channel.down, model.auth_expired, update.failed, backup.stale, worker.silent, disk.low. */
  kind: string;
  severity: HealthSeverity;
  title: string;
  body: string;
  /** Đường dẫn trong Console để xử lý (vd `/system?tab=channels`); null = không có. */
  link: string | null;
  /** Nhãn nút, vd "Đăng nhập lại". */
  action: string;
  raised_at: string;
}
export interface QueueReplayResult {
  stream: string;
  replayed: number;
  remaining: number;
}
export interface QueueClearResult {
  stream: string;
  cleared: number;
  remaining: number;
}
export interface SystemHealth {
  checked_at: string;
  overall: 'ok' | 'warn' | 'bad';
  worker: { state: 'ok' | 'silent' | 'unknown'; alive: boolean; last_seen_at: string | null; silent_minutes: number | null };
  browser: { state: 'ok' | 'silent' | 'off'; last_heartbeat_at: string | null };
  /**
   * v0.1.62 (F051): cầu nối tin nhắn (Zalo/WhatsApp). CHỈ có khi có nhịp hoặc có kênh đang "kết nối" (chưa dùng cầu nối
   * ⇒ vắng khối). 'silent' = nhịp mất > 2 phút mà kênh vẫn ghi "Đang kết nối" (kèm sự cố bridge.silent).
   */
  bridge?: { state: 'ok' | 'silent'; last_seen_at: string | null; silent_minutes: number | null };
  /**
   * v0.1.63 (H1): hộp riêng Antigravity (`agy-box`). CHỈ có khi hộp đã được cấu hình (chưa cài / dev ⇒ vắng khối).
   * 'down' = đã cấu hình mà không trả lời kiểm tra sống (kèm sự cố agy_box.down sau 2 phút liên tục).
   */
  agy_box?: { state: 'ok' | 'down'; checked_at: string | null; agy_version: string | null };
  /** `stream` là tên stream gốc (không có `.dlq`). */
  queues: Array<{ stream: string; dlq: number }>;
  crons: Array<{ name: string; last_at: string | null; ok: boolean | null }>;
  /**
   * `frequency`/`stale_after` (v0.1.36): hạn sao lưu theo tần suất bước 11 — `stale_after` là chữ hiện cho Sếp ("36 giờ",
   * "một tuần", "một tháng"); null khi chưa cấu hình.
   */
  backup: {
    configured: boolean;
    latest_at: string | null;
    age_hours: number | null;
    stale: boolean;
    frequency?: 'daily' | 'weekly' | 'monthly' | null;
    stale_after?: string | null;
  };
  /** `failed` = lần cập nhật lỗi trong 24 giờ qua (cùng điều kiện thẻ cập nhật) — quá hạn thì false dù `state` vẫn 'failed'. */
  update: {
    state: SystemUpdateState | 'unknown' | string;
    stalled_reason?: SystemUpdateStalledReason;
    failed: boolean;
    /** v0.1.37: `failed` mà do tín hiệu dừng (GH-E94B), không dở dang ⇒ "bị dừng giữa chừng" (vàng), không đỏ. */
    interrupted?: SystemUpdateInterrupted;
    blocked_version: string | null;
    finished_at: string | null;
  };
  disk: { state: 'ok' | 'low' | 'unknown'; free_bytes: number | null; min_bytes: number | null; checked_at: string | null };
  issues: HealthIssue[];
  /**
   * v0.1.37 (F-73): máy chủ có tự chạy lại Gen-Harness khi bật máy không (genh ghi run/autostart-status.json). Chỉ có
   * khi api có hộp thư với genh; 'warn' khi Docker chưa bật tự chạy hoặc thiếu linger (kèm sự cố host.autostart).
   */
  /**
   * v0.1.40 (F-12): bản sao ngoài máy (ổ USB/NAS — genh ghi run/offsite-status.json). CHỈ có khi api có hộp thư với
   * genh (giữ khuôn cũ khi không có) ⇒ vắng khối thì thẻ Sức khoẻ không hiện dòng.
   */
  offsite?: SystemHealthOffsite;
  autostart?: {
    state: 'ok' | 'warn' | 'unknown';
    linger: 'yes' | 'no' | 'unknown' | 'not_applicable';
    linger_required: boolean | null;
    docker_enabled: 'yes' | 'no' | 'unknown' | 'not_applicable';
    docker_mode: 'system' | 'rootless' | 'desktop' | 'unknown';
    checked_at: string | null;
  };
  /**
   * v0.1.53 (F-99): lịch tự cập nhật đêm (~03:00) có đang chạy không (genh ghi run/nightly-status.json). Chỉ có khi api
   * có hộp thư với genh. 'warn' = có việc Sếp cần biết (kèm sự cố host.nightly, lý do ở `reason`); 'off' = Sếp đã chủ
   * động tắt VÀ lịch không còn bật; 'other' = lịch đêm của máy do một bản cài KHÁC (còn sống) quản lý — bản cài này
   * không đổi được lịch đó, không cảnh báo; 'unknown' = genh cũ chưa ghi tệp. `days_since` = số ngày tròn kể từ mốc
   * max(lần chạy cuối, lúc bật). `linger` = 'not_applicable' khi lịch là crontab/LaunchAgent/Task Scheduler (linger
   * không chi phối, không nhắc `enable-linger`).
   */
  nightly?: {
    state: 'ok' | 'warn' | 'off' | 'other' | 'unknown';
    /**
     * Lý do 'warn' (null ở trạng thái khác; thiếu ở api cũ): 'disabled' = lịch đang tắt (không do Sếp tắt); 'stale' =
     * bật mà im quá 36 giờ; 'opted_out_running' = Sếp đã tắt nhưng lịch VẪN bật (máy vẫn tự lên bản mới ~03:00).
     */
    reason?: 'disabled' | 'stale' | 'opted_out_running' | null;
    last_run_at: string | null;
    next_run_at: string | null;
    days_since: number | null;
    opted_out: boolean | null;
    linger: SystemLinger;
    checked_at: string | null;
    /**
     * v0.1.54: người gác yêu cầu (`gen-harness-update-request.path`) tự chữa. CHỈ có khi KHÔNG ok (thiếu = ok / api cũ):
     * 'fallback' = người gác lỗi (vd hết hạn mức inotify) nhưng timer dự phòng quét mỗi phút đang nhận yêu cầu thay;
     * 'failed' = lỗi và chưa bật được dự phòng (nút Cập nhật ngay chưa có người nhận). `reason` là mã ('inotify' |
     * 'resources' | 'other'); `hint` là câu gợi ý cố định do api ghép (không lấy chữ từ tệp trên máy chủ).
     */
    watcher?: {
      state: 'fallback' | 'failed';
      reason: 'inotify' | 'resources' | 'other' | null;
      hint: string;
    };
  };
}

/** Trạng thái lần xuất bản sao ngoài máy (genh ghi `state` vào run/offsite-status.json; 'unknown' = đọc không được). */
export type OffsiteRunState = 'ok' | 'failed' | 'not_mounted' | 'not_configured' | 'running' | 'skipped_busy' | 'unknown';

/** Khối `offsite` của `GET /system/health` — tập con của `OffsiteState`. Mọi trường là chuỗi/số/bool/null. */
export interface SystemHealthOffsite {
  configured: boolean;
  state: OffsiteRunState | string;
  /** Mã lỗi genh `GH-EBxx`; '' / null = không lỗi. */
  error_code?: string | null;
  last_success_at: string | null;
  age_days: number | null;
  /** > 7 ngày chưa có bản sao ngoài máy (hoặc chưa có bản nào). */
  stale: boolean;
}

/** Yêu cầu Console gửi genh qua hộp thư run/request/offsite.json. */
export interface OffsiteRequest {
  state: 'idle' | 'requested' | 'running' | 'done' | 'failed' | 'stalled' | string;
  action: 'set' | 'run' | 'disable' | string | null;
  requested_at: string | null;
}

/**
 * v0.1.40 (F-12): `GET /system/offsite` — Bản sao ngoài máy (ổ USB/NAS cắm vào máy chủ). genh trên máy chủ làm việc thật
 * (lịch Chủ nhật ~05:30), api chỉ đọc run/offsite-status.json + ghi run/request/offsite.json. Mọi trường là
 * chuỗi/số/bool/null — web không render object.
 */
export interface OffsiteState {
  configured: boolean;
  /** Đường dẫn đích trên MÁY CHỦ (vd `/media/sep/USB`); '' / null khi chưa chọn. */
  dest: string | null;
  state: OffsiteRunState | string;
  /** Mã lỗi genh: GH-EB00 chưa chọn nơi lưu · GH-EB01 chưa thấy ổ USB/NAS · … ; '' / null = không lỗi. */
  error_code: string | null;
  /** Câu thân thiện cho Sếp (api dựng theo mã lỗi); null = không có. */
  message: string | null;
  last_attempt_at: string | null;
  last_success_at: string | null;
  age_days: number | null;
  stale: boolean;
  last_size_bytes: number | null;
  /** Bản gần nhất đã được kiểm đọc lại được (`gh.bundle verify`). */
  verified: boolean;
  /** 8 ký tự hex đầu sha256(khoá khôi phục) — để đối chiếu với Bộ khôi phục đã in; không phải khoá. */
  key_id: string | null;
  /** Cơ chế lịch tuần trên máy chủ (systemd/cron/launchd/schtasks); '' / null = chưa bật lịch. */
  schedule: string | null;
  request: OffsiteRequest;
  /** Máy chủ có watcher nhận yêu cầu từ Console. */
  can_request: boolean;
  /** Lệnh Owner tự chạy trên máy chủ khi Console chưa gửi được yêu cầu; null = không cần. */
  manual_command: string | null;
  /** Khoá khôi phục đã có trên máy chủ (secret gh_offsite_key). */
  key_present: boolean;
}

/** `GET /system/offsite/recovery-kit` (Owner + PIN `offsite.recovery_kit`, no-store). KHÔNG lưu/ghi log khoá. */
export interface RecoveryKit {
  key: string;
  key_id: string;
  /** Ngày tạo khoá (YYYY-MM-DD) để Owner đối chiếu bản đã cất còn đúng; null = không đọc được. Thiếu ở api cũ. */
  created_hint?: string | null;
  steps: string[];
  warning: string;
}

/** `GET /system/offsite/portable` — tải bằng điều hướng trình duyệt (gói có thể rất lớn), KHÔNG fetch→blob. */
export const OFFSITE_PORTABLE_URL = '/api/v1/system/offsite/portable';

/**
 * v0.1.41 (F-84): `GET /system/ai-cost?date=YYYY-MM-DD` (quyền `system.read`; ngày theo giờ VN) — chi phí AI trong ngày
 * theo agent/model, trần ngân sách, 7 ngày gần nhất và đánh giá "Hữu ích" 7 ngày. Tiền là số nguyên VND; CLI trả theo gói
 * nên `price_source='subscription'` và cost 0. Mọi trường là chuỗi/số/bool/null — web không render object.
 */
export interface AiCostAgent {
  agent_key: string;
  label: string;
  calls: number;
  tokens_in: number;
  tokens_out: number;
  cost_vnd: number;
  unpriced_calls: number;
}
export type AiPriceSource = 'owner' | 'subscription' | 'none';
export interface AiCostModel {
  model_id: string;
  provider_name: string;
  provider_kind: string;
  model_name: string;
  in_vnd_per_mtok: number | null;
  out_vnd_per_mtok: number | null;
  price_source: AiPriceSource | string;
  calls_today: number;
}
export interface AiCostFeedback {
  helpful: number;
  not_helpful: number;
  briefing_helpful: number;
  briefing_not_helpful: number;
}
export interface AiCost {
  date: string;
  timezone: string;
  total_vnd: number;
  /** null = chưa đặt trần (không giới hạn). */
  budget_vnd: number | null;
  over_budget: boolean;
  unpriced_calls: number;
  agents: AiCostAgent[];
  models: AiCostModel[];
  last_7_days: Array<{ date: string; total_vnd: number }>;
  feedback_7d: AiCostFeedback;
}
export interface AiBudgetBody {
  daily_budget_vnd: number | null;
}
export interface AiPriceBody {
  in_vnd_per_mtok: number | null;
  out_vnd_per_mtok: number | null;
}

const enc = encodeURIComponent;

/** `/permissions`, `/listening-groups`, `/boundaries*`, `/audit-log*`, `/retention-policies`, `/persons/{id}/data-requests`. */
export function systemEndpoints(r: ApiClient['request']) {
  return {
    permissions: {
      get: (signal?: AbortSignal) => r<PermissionsPage>('/permissions', { signal }),
      patch: (body: PermissionPatchBody) => r<PermissionsPage>('/permissions', { method: 'PATCH', body }),
    },
    listeningGroups: (signal?: AbortSignal) => r<ListeningGroup[]>('/listening-groups', { signal }),
    boundaries: {
      list: (signal?: AbortSignal) => r<Boundary[]>('/boundaries', { signal }),
      patch: (code: string, body: BoundaryPatchBody) => r<Boundary>(`/boundaries/${enc(code)}`, { method: 'PATCH', body }),
    },
    systemAuditLog: {
      /**
       * Đọc qua `/audit` (cùng `query_log` với `/audit-log`) — chỉ `/audit` nhận ô tìm phía máy chủ `q`/`q_actions`/
       * `q_actors` và `hide_noise` (A8, F099); `/audit-log` giữ nguyên cho bản cũ và xuất CSV.
       */
      list: (q: SystemAuditQuery = {}, signal?: AbortSignal) => r<AuditPage>('/audit', { query: { ...q }, signal }),
      /** CSV — `data.manage` + PIN (`data.export`), cùng khuôn `raw.exportCsv`. */
      exportCsv: (q: Omit<SystemAuditQuery, 'cursor' | 'limit'> = {}) =>
        r<string>('/audit-log/export', { query: q as Record<string, string | undefined>, responseType: 'text' }),
    },
    retentionPolicies: {
      list: (signal?: AbortSignal) => r<RetentionPolicy[]>('/retention-policies', { signal }),
      patch: (body: RetentionPatchBody) => r<RetentionPolicy[]>('/retention-policies', { method: 'PATCH', body }),
    },
    backups: {
      list: (signal?: AbortSignal) => r<BackupsPage>('/system/backups', { signal }),
      runNow: () => r<BackupsPage>('/system/backups', { method: 'POST' }),
      /** Tệp ĐÃ MÃ HOÁ — chỉ Owner, cần PIN (`backup.download`). */
      download: (key: string) => r<Blob>('/system/backups/download', { query: { key }, responseType: 'blob' }),
      /** Chỉ Owner, cần PIN (`backup.restore`) + gõ đúng "KHÔI PHỤC". */
      restore: (key: string, confirm: string) => r<BackupsPage>('/system/backups/restore', { method: 'POST', body: { key, confirm } }),
      schedule: (body: BackupSchedule) => r<BackupsPage>('/system/backups/schedule', { method: 'PUT', body }),
    },
    systemUpdate: {
      get: (signal?: AbortSignal) => r<SystemUpdate>('/system/update', { signal }),
      request: () => r<SystemUpdate>('/system/update', { method: 'POST' }),
      /** v0.1.30: "Kiểm tra bản mới" — hỏi GitHub ngay, bỏ qua bộ đệm (≤ 1 lần / 30 giây). */
      check: () => r<SystemUpdate>('/system/update/check', { method: 'POST' }),
    },
    /** v0.1.40 (F-12): Bản sao ngoài máy (ổ USB/NAS). */
    offsite: {
      get: (signal?: AbortSignal) => r<OffsiteState>('/system/offsite', { signal }),
      /** Owner + PIN (`offsite.destination`). 409 OFFSITE_UNAVAILABLE kèm `manual_command`. */
      setDestination: (body: { path: string }) => r<OffsiteState>('/system/offsite/destination', { method: 'PUT', body }),
      /** `system.manage`. */
      runNow: () => r<OffsiteState>('/system/offsite/run', { method: 'POST' }),
      /** Owner + PIN. */
      disable: () => r<OffsiteState>('/system/offsite/disable', { method: 'POST' }),
      /** Owner + PIN (`offsite.recovery_kit`) — gọi qua useMutation, không để trong bộ đệm truy vấn. */
      recoveryKit: () => r<RecoveryKit>('/system/offsite/recovery-kit'),
      /** Owner + PIN (`offsite.portable`): tệp .ghbundle — trình duyệt tự tải bằng điều hướng tới URL này. */
      portableUrl: OFFSITE_PORTABLE_URL,
    },
    /** v0.1.46 (F-21): `api.system.access()` — Truy cập từ xa / địa chỉ đăng nhập. */
    ...accessEndpoints(r),
    /** v0.1.36 (F-6): sức khoẻ hệ thống + sự cố cần Sếp xử lý. */
    systemHealth: {
      get: (signal?: AbortSignal) => r<SystemHealth>('/system/health', { signal }),
      /** v0.1.62 (F064): `system.manage` + PIN. Chỉ `gh.bridge.inbound` phát lại được (hàng khác 409 QUEUE_NOT_REPLAYABLE). */
      replayQueue: (stream: string) => r<QueueReplayResult>(`/system/queues/${enc(stream)}/replay`, { method: 'POST' }),
      /** v0.1.62 (F102): "Đã xem, xoá" — `system.manage` + PIN; ghi Action Log. */
      clearQueue: (stream: string) => r<QueueClearResult>(`/system/queues/${enc(stream)}/clear`, { method: 'POST' }),
    },
    /** v0.1.41 (F-84): Chi phí AI hôm nay + trần ngân sách + giá model (sửa cần `system.manage`). */
    aiCost: {
      get: (date?: string, signal?: AbortSignal) =>
        r<AiCost>('/system/ai-cost', { signal, ...(date ? { query: { date } } : {}) }),
      setBudget: (body: AiBudgetBody) => r<AiCost>('/system/ai-cost/budget', { method: 'PUT', body }),
      setPrice: (modelId: string, body: AiPriceBody) =>
        r<AiCost>(`/system/ai-cost/prices/${enc(modelId)}`, { method: 'PUT', body }),
    },
    personDataRequests: {
      create: (personId: string, kind: DataRequestKind) =>
        r<DataRequestResult>(`/persons/${enc(personId)}/data-requests`, { method: 'POST', body: { kind } }),
      list: (personId: string, signal?: AbortSignal) => r<DataRequestRecord[]>(`/persons/${enc(personId)}/data-requests`, { signal }),
    },
  };
}
