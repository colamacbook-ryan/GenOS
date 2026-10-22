import type { GenDoneEvent, GenStepEvent } from './gen';
import type { NotificationItem } from './notifications';
import type { ListenEventSignal } from './listenLog';
/**
 * Phase-2 API types, hand-written from docs/api/phase-2.md (tầng dữ liệu,
 * kênh, bộ não AI, trình thiết lập 4–7 + 12, WebSocket). Numbers are raw —
 * the web formats them (18.412, 0,94, 15 phút). Times are ISO 8601 UTC.
 *
 * Where the contract leaves a shape open (rule outputs, history parties) the
 * type is kept permissive and the gap is noted next to it.
 */

// ── shared ────────────────────────────────────────────────────────────────
export type ChannelType = 'zalo' | 'whatsapp' | 'telegram' | 'linkedin';

/** Every object carries both `id` (uuid) and its public `code` (GRP-ZL-0114, PER-0042…). */
export interface Ref {
  id: string;
  code: string;
  name: string;
}

/** Cursor page `?cursor=&limit=` → `{items, next_cursor, total}`. */
export interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
  total: number;
}

export type Since = '24h' | '7d' | '30d' | 'all';

// ── pipeline strip ────────────────────────────────────────────────────────
export interface Pipeline {
  channels_live: number;
  groups_listening: number;
  raw_total: number;
  raw_pending: number;
  interval_seconds: number;
  count_threshold: number;
  clean_total: number;
  /** v0.1.64 (F055): số tin ở trạng thái "Lỗi xử lý" (model trả sai định dạng đủ 3 lần). */
  raw_error: number;
}

// ── raw lake ──────────────────────────────────────────────────────────────
export type RawState = 'pending' | 'processing' | 'clean' | 'lowconf' | 'discarded' | 'error';

export interface RawItem {
  id: string;
  code: string;
  received_at: string;
  occurred_at: string;
  channel: { type: ChannelType | string; name: string };
  group: Ref | null;
  person: Ref | null;
  direction: 'inbound' | 'outbound';
  kind: string;
  text: string;
  label: string | null;
  confidence: number | null;
  state: RawState;
}

export interface RawDetail extends RawItem {
  payload: Record<string, unknown>;
  meaning_units: Array<{ id: string; event_type: string; conclusion: string }>;
}

export interface RawQuery {
  cursor?: string;
  limit?: number;
  channel?: string;
  group_id?: string;
  state?: RawState | '';
  since?: Since;
  label?: string;
  min_confidence?: number;
}

export interface RawByGroup {
  /** `null` for an aggregated "other groups" row, if the server sends one. */
  group: Ref | null;
  n: number;
}

/** WS `raw.state`. */
export interface RawStateEvent {
  id: string;
  state: RawState;
  label: string | null;
  confidence: number | null;
}

// ── refinery ──────────────────────────────────────────────────────────────
export interface RefineryScheduleConfig {
  interval_seconds: number;
  count_threshold: number;
  batch_size: number;
  min_confidence: number;
}

export interface RefinerySchedule extends RefineryScheduleConfig {
  pending: number;
  next_run_at: string;
  next_trigger: 'interval' | 'count';
}

export type RefineryTrigger = 'schedule' | 'threshold' | 'manual' | 'fast';

export interface RefineryRun {
  id: string;
  trigger: RefineryTrigger;
  started_at: string;
  finished_at: string | null;
  input_count: number;
  clean_count: number;
  lowconf_count: number;
  noise_count: number;
  error_count: number;
  /** `queued`: a manual run waiting for the worker (shown like running). */
  status: 'queued' | 'running' | 'done' | 'failed';
}

/** WS `refinery.progress`. */
export interface RefineryProgress {
  run_id: string;
  processed: number;
  total: number;
  clean: number;
  lowconf: number;
  noise: number;
  errors: number;
  status: 'queued' | 'running' | 'done' | 'failed';
}

// ── rules ─────────────────────────────────────────────────────────────────
export type RuleKind = 'intent' | 'risk' | 'competition' | 'hr' | 'hygiene' | 'custom';

export type RuleConditionType =
  | 'keyword_any'
  | 'keyword_all'
  | 'regex'
  | 'has_entity'
  | 'min_words'
  | 'max_words'
  | 'is_question'
  | 'kind_in'
  | 'repeat_unanswered'
  | 'llm';

export interface RuleCondition {
  type: RuleConditionType;
  values?: string[];
  pattern?: string;
  entity?: string;
  n?: number;
  hint?: string;
  /** R-06 "dưới 4 từ và không có thực thể" (API presets). */
  no_entity?: boolean;
  /** Human label shown on the card ("có từ khoá số lượng + đơn vị"). */
  label?: string;
}

/**
 * Contract: `set {field, value}`, `add {field, value}`, `discard`, `alert {priority}`,
 * example `{"set": "intent", "value": "AskedPrice", "label": "…"}`. The exact
 * encoding of discard/alert is not spelled out; the API presets use
 * `{"discard": true}` and `{"alert": "P1"}`, which the web writes too.
 */
export interface RuleOutput {
  set?: string;
  add?: string;
  discard?: boolean;
  alert?: boolean | string;
  priority?: string;
  value?: string | number;
  label?: string;
}

export interface Rule {
  id: string;
  code: string;
  name: string;
  kind: RuleKind | string;
  kind_label: string;
  enabled: boolean;
  version: number;
  threshold: number;
  hits_24h: number;
  conditions: RuleCondition[];
  outputs: RuleOutput[];
  prompt_hint: string | null;
  updated_at: string;
}

export interface RuleBody {
  name: string;
  kind: RuleKind | string;
  conditions: RuleCondition[];
  outputs: RuleOutput[];
  threshold: number;
  prompt_hint: string | null;
}

export interface RuleVersion {
  version: number;
  conditions: RuleCondition[];
  outputs: RuleOutput[];
  threshold: number;
  created_at: string;
  created_by: string | { id?: string; label?: string; name?: string; display_name?: string } | null;
}

export interface Weight {
  dimension: string;
  label: string;
  /** Integer percent; all weights sum to 100. */
  value: number;
}

export type RuleTestBody = { raw_event_id: string } | { text: string; group_id?: string; person_id?: string };

export interface RuleTestResult {
  input: { code: string | null; text: string };
  output: Array<{ key: string; value: string }>;
  matched_rules: string[];
  discarded_by: string | null;
  confidence: number | null;
  would_write: 'clean' | 'lowconf' | 'discarded';
}

export interface RuleTestBatchResult {
  n: number;
  clean: number;
  lowconf: number;
  discarded: number;
  by_rule: Array<{ code: string; hits: number }>;
}

// ── clean store ───────────────────────────────────────────────────────────
export interface CleanItem {
  id: string;
  observed_at: string;
  group: Ref | null;
  person: Ref | null;
  event_type: string;
  conclusion: string;
  /** Weighted total 0–100. */
  score: number;
  confidence: number;
  cycle_at: string;
  raw_event_ids: string[];
}

export interface CleanQuery {
  cursor?: string;
  limit?: number;
  group_id?: string;
  person_id?: string;
  since?: Since;
}

export interface CleanEvidence {
  raw: RawItem;
  quote: string;
}

export interface AgentParam {
  key: string;
  label: string;
  value: string;
  /** Phosphor class. */
  icon: string;
}

// ── notebooks (core) ──────────────────────────────────────────────────────
export type NotebookSubjectType = 'person' | 'group';
export type NotebookSectionKey = 'attention_now' | 'rolling_context' | 'guardrails' | 'preferences' | 'open_threads';

export interface NotebookEntry {
  id: string;
  body: string;
  refs: Array<{ type: string; id: string; code: string }>;
  author: { type: 'agent' | 'user'; label: string };
  pinned: boolean;
  created_at: string;
}

export interface NotebookSection {
  key: NotebookSectionKey | string;
  title: string;
  updated_at: string | null;
  entries: NotebookEntry[];
}

export interface Notebook {
  id: string;
  subject: { type: NotebookSubjectType; id: string; code: string; name: string };
  token_used: number;
  token_budget: number;
  compaction_no: number;
  last_compacted_at: string | null;
  sections: NotebookSection[];
}

export interface NotebookCompaction {
  compaction_no: number;
  at: string;
  tokens_before: number;
  tokens_after: number;
  archived: number;
  summary: string;
}

// ── identity ──────────────────────────────────────────────────────────────
export interface IdentityStats {
  merged_people: number;
  live_profiles: number;
  pending_pairs: number;
  manual_splits: number;
  unlinked_accounts: number;
}

export interface IdentitySide {
  identity_id: string;
  person: Ref;
  channel: ChannelType | string;
  meta: string;
}

export interface IdentityCandidate {
  id: string;
  confidence: number;
  level: 'high' | 'mid' | 'low';
  basis: string;
  basis_detail: Record<string, unknown>;
  a: IdentitySide;
  b: IdentitySide;
}

export interface IdentityEvidence {
  raw: RawItem;
  note: string;
}

/**
 * History parties are `{…}` in the contract. The web reads `id/code/name`, and
 * — only when present — `identities` to offer a manual split (gap: no endpoint
 * lists a person's channel identities).
 */
export interface IdentityHistoryParty {
  id?: string;
  code?: string;
  name?: string;
  identities?: Array<{ identity_id: string; channel?: string; meta?: string }>;
}

export interface IdentityHistoryItem {
  id: string;
  op: 'merge' | 'split';
  at: string;
  actor: string | { label?: string; name?: string; display_name?: string } | null;
  from: IdentityHistoryParty;
  to: IdentityHistoryParty;
  identities: number;
  reverted: boolean;
}

// ── channels & groups ─────────────────────────────────────────────────────
export type ChannelState =
  | 'active'
  | 'pending_qr'
  | 'expired'
  | 'logged_out'
  | 'error'
  | 'not_installed'
  | 'identity_only';

export interface ChannelQr {
  session_id: string;
  image: string;
  expires_at: string;
  scanned: boolean;
}

export interface ChannelStats {
  msgs_24h: number | null;
  tagged_24h: number | null;
  latency_ms: number | null;
  uptime_pct: number | null;
  /** LinkedIn (`identity_only`): linked identities / identities merged into a person. */
  identities?: number | null;
  merged?: number | null;
}

export interface Channel {
  type: ChannelType;
  name: string;
  installed: boolean;
  id: string | null;
  state: ChannelState;
  account_label: string | null;
  started_at: string | null;
  groups_listening: number;
  outbound_queued: number;
  last_heartbeat_at: string | null;
  stats: ChannelStats | null;
  qr: ChannelQr | null;
  /** QR channels: 1-1 messages are stored (`direct_mode` ≠ off). `PATCH /channels/{type}` 🔒 `policy.change`. */
  listen_direct?: boolean;
  /** v0.1.65: tin riêng 1-1 — `silent` (mặc định) lưu nhưng không tự trả lời · `reply` agent trực được trả lời · `off` không lưu. */
  direct_mode?: DirectMode;
  /** v0.1.65: số nhóm mới phát hiện đang CHỜ Sếp chọn (`listen_mode = 'pending'`, chưa rời). Máy chủ cũ không gửi. */
  groups_pending?: number;
  /** v0.1.65 (F1d), QD 17/10: chế độ cho nhóm MỚI phát hiện sau này — `silent` (MẶC ĐỊNH: vào Lắng nghe im lặng ngay, không bao giờ trả lời kể cả khi được tag) · `ask` (chờ Sếp chọn) · `off` (vào Không nghe). Máy chủ cũ không gửi. `PATCH /channels/{type}` 🔒 `policy.change`. */
  new_group_mode?: NewGroupMode;
  /** v0.1.65 (F1d): Sếp đã chọn ở hộp hỏi / thẻ kênh kể từ lần nối kênh gần nhất. Chỉ còn để tương thích — ô «Áp dụng cho các nhóm mới sau này» không còn bật sẵn. */
  new_group_asked?: boolean;
  /** QD 17/10: số nhóm mới vừa tự vào Lắng nghe im lặng mà Sếp chưa bấm «Đã biết» (`POST /channels/{type}/groups/seen`). Máy chủ cũ không gửi. */
  new_groups_silent?: number;
  session_id?: string | null;
  ended_at?: string | null;
  /** Last bridge error for this channel, if any. */
  error?: string | null;
  /** v0.1.62 (F046): phiên 'Lỗi kết nối' còn credential ⇒ `POST /channels/{type}/resume` nối lại được, không cần quét QR. Máy chủ cũ không gửi. */
  resumable?: boolean;
  /** v0.1.62 (F051): bộ nhận tin (bridge) còn nhịp tim; `false` ⇒ "Mất liên lạc"; null/vắng = chưa biết. */
  bridge_online?: boolean | null;
}

/** Chế độ nghe của nhóm. `pending` = nhóm mới phát hiện, CHỜ Sếp chọn (chưa lưu tin, vẫn thu thành viên); Sếp không tự đặt lại về `pending`. Khớp `ALL_LISTEN_MODES` ở api/gh/data/common.py. */
export type ListenMode = 'pending' | 'off' | 'tagged_only' | 'silent' | 'proactive' | 'paused';
/** Chế độ Sếp được CHỌN cho một nhóm (khớp `SETTABLE_LISTEN_MODES`). */
export type SettableListenMode = Exclude<ListenMode, 'pending'>;
export type DirectMode = 'off' | 'silent' | 'reply';
/** Chế độ cho nhóm mới sau này (F1d). */
export type NewGroupMode = 'ask' | 'silent' | 'off';
export type ViewScope = 'owner' | 'manager' | 'all_members';
export type GroupKind = 'internal' | 'market' | 'partner' | 'customer' | 'private';

export interface ChannelGroup {
  id: string;
  code: string;
  name: string;
  members: number;
  kind: GroupKind | string;
  listen_mode: ListenMode;
  view_scope: ViewScope;
  /** v0.1.65: Sếp đã rời nhóm (danh bạ đầy đủ gần nhất không còn nhóm này) — không đếm "đang lắng nghe". */
  left?: boolean;
}

/** WS `group.discovered` (v0.1.65): nhóm mới phát hiện: `listen_mode` = `silent` (mặc định, QD 17/10) / `pending` (Sếp đặt Hỏi Sếp, chờ chọn) / `off`. */
export interface GroupDiscoveredEvent {
  id: string;
  code: string;
  name: string;
  channel: ChannelType;
  listen_mode: ListenMode;
}

/** WS `channel.qr`. */
export interface ChannelQrEvent {
  type: ChannelType;
  session_id: string;
  image: string;
  expires_at: string;
}

/** WS `channel.status`. */
export interface ChannelStatusEvent {
  type: ChannelType;
  state: ChannelState;
  account_label: string | null;
  scanned: boolean;
  /** Mã lỗi bridge khi phiên kết thúc `expired` | `error` (vd. `QR_EXPIRED`, `WA_NO_QR_405`); web đổi sang câu thân thiện. */
  error?: string | null;
}

// ── AI brain ──────────────────────────────────────────────────────────────
/** `system_one` = Jev (TypeSafe System One, v0.1.21): bộ quyết định nhanh cho Gen — KHÔNG nằm trong chuỗi sinh chữ. */
export type ProviderKind = 'antigravity_cli' | 'claude_code_cli' | 'gemini' | 'deepseek' | 'openai_compat' | 'system_one';
/** v0.1.31: nguồn dùng phiên đăng nhập CLI (không khoá API): Antigravity (Google) và Claude Code (gói Claude). */
export type CliKind = 'antigravity_cli' | 'claude_code_cli';

export interface ProviderKey {
  id: string;
  label: string;
  last4: string;
  enabled: boolean;
  cooldown_until: string | null;
  quota_left_pct: number | null;
}

export interface ProviderModel {
  id: string;
  model_name: string;
  daily_quota: number | null;
  /** v0.1.66: giới hạn lượt/phút (null = không giới hạn) — đặt ở thẻ nhà cung cấp, "Quản lý khoá và model". */
  rate_limit_per_min?: number | null;
  used_today: number;
  /** Review v0.1.66 (P2-2): model đang bật; `false` = đã "Tắt model" (ngừng dùng, giữ lịch sử chi phí). Thiếu = bật. */
  enabled?: boolean;
  /** v0.1.31: model mặc định của nguồn ("Dùng model này"). */
  is_default?: boolean;
  /** v0.1.32: mức suy nghĩ đã lưu (nguồn CLI); null = để CLI tự chọn. */
  effort?: Effort | null;
}

/** v0.1.32: mức suy nghĩ — agy `--effort low|medium|high`; Claude Code thêm `xhigh`, `max`. */
export type Effort = 'low' | 'medium' | 'high' | 'xhigh' | 'max';

/** v0.1.31: một model trong danh sách chọn — nhóm theo họ (Gemini, Claude…), kèm gợi ý nhanh/rẻ hay mạnh. */
export interface ModelOption {
  id: string;
  label: string;
  group: string;
  tier: 'fast' | 'balanced' | 'strong';
  hint: string;
  /** `cli` = CLI liệt kê được; `catalog` = danh mục dự phòng; `saved` = model đã lưu mà CLI không liệt kê. */
  source: 'cli' | 'catalog' | 'saved';
  /** v0.1.32: mức suy nghĩ model này nhận (rỗng = không chỉnh được, CLI tự chọn). */
  efforts?: Effort[];
  /** v0.1.32: mức CLI đang dùng cho model này (nếu CLI cho biết). */
  default_effort?: Effort | null;
  /** v0.1.32: false = chưa xác minh bằng CLI / tài liệu chính thức (Console ghi "chưa xác minh"). */
  verified?: boolean;
  /** v0.1.32: nguồn dữ liệu (lệnh CLI hoặc URL tài liệu + ngày kiểm). */
  source_ref?: string | null;
}

export interface ModelGroup {
  label: string;
  models: ModelOption[];
}

export interface Provider {
  id: string;
  kind: ProviderKind;
  name: string;
  endpoint: string | null;
  failover_rank: number;
  enabled: boolean;
  auth_state: 'ok' | 'expiring' | 'expired' | 'error' | 'unconfigured';
  keys: ProviderKey[];
  models: ProviderModel[];
  /** Kết quả lần gọi thử gần nhất (lưu ở máy chủ) — bước 4 dùng để còn nút "Dùng model này" sau khi tải lại trang. */
  last_test?: ProviderTestResult | null;
}

export interface ProviderCreateBody {
  kind: ProviderKind;
  name: string;
  endpoint?: string;
  keys: string[];
  models?: string[];
}

export interface ProviderTestResult {
  ok: boolean;
  latency_ms: number | null;
  models: string[];
  error: string | null;
  /** v0.1.31: danh sách nhóm model (CLI và khoá API). */
  model_groups?: ModelGroup[];
  models_source?: 'cli' | 'catalog';
  /** v0.1.31 (nguồn CLI): model đã gọi thật thành công trong lần kiểm tra này. */
  probe_model?: string | null;
  /** v0.1.32: mức suy nghĩ của lượt gọi thật đó. */
  probe_effort?: Effort | null;
  /** v0.1.32: giờ của lần gọi thật (ISO) — Console hiện "lúc …" để biết kết quả cũ hay mới. */
  at?: string;
  /** v0.1.32: lỗi gốc đã che bí mật, cho mục "Chi tiết kỹ thuật". */
  error_detail?: string | null;
  /** v0.1.32: đầu ra thô (đã che) của `agy models`. */
  models_raw?: string | null;
}

/** v0.1.32 — "Chẩn đoán" nguồn CLI (chỉ Owner): đầu ra thô đã che token/email. */
export interface DiagnosisStep {
  label: string;
  command: string;
  exit_code: number | null;
  stdout: string;
  stderr: string;
  ms: number;
  note: string | null;
}

export interface ProviderDiagnosis {
  provider: string;
  kind: CliKind;
  model: string | null;
  effort: Effort | null;
  steps: DiagnosisStep[];
  at: string;
}

export interface Credential {
  icon: string;
  name: string;
  meta: string;
  state: 'ok' | 'warn' | 'bad';
  state_label: string;
}

export interface CliProfile {
  id: string;
  /** v0.1.31: loại CLI của hồ sơ (mặc định Antigravity). */
  kind?: CliKind;
  /** v0.1.31: còn refresh token — CLI tự gia hạn, token ngắn hạn quá giờ vẫn dùng được. */
  refreshable?: boolean;
  /** null khi tệp phiên không cho biết email (id_token thiếu, userinfo lỗi) — UI hiện "Tài khoản Google". */
  email: string | null;
  plan_label: string | null;
  active: boolean;
  expires_at: string | null;
  state: 'ok' | 'expiring' | 'expired';
}

/**
 * WS `cli.login` — URL + paste-back-code flow (contract addendum): at
 * `waiting_code` the Console shows `url` and a "Mã xác thực" field, then
 * `POST /cli/login/{login_id}/code {code}`.
 */
export type CliLoginStatus = 'starting' | 'waiting_code' | 'verifying' | 'done' | 'failed';

export interface CliLoginEvent {
  login_id: string;
  kind?: CliKind;
  status: CliLoginStatus;
  url?: string | null;
  message?: string | null;
  profile?: CliProfile | null;
}

// ── setup 4–7, 12 ─────────────────────────────────────────────────────────
export interface SetupStep6Body {
  groups: Array<{ id: string; listen_mode: SettableListenMode; view_scope: ViewScope }>;
}

export interface SetupStep7Body {
  interval_seconds: number;
  count_threshold: number;
  min_confidence: number;
  rule_codes: string[];
  weights: Array<{ dimension: string; value: number }>;
}

export interface FirstRun {
  raw_collected: number;
  classifying: number;
  clean: number;
  lowconf: number;
  discarded: number;
  run: RefineryRun | null;
}

// ── WebSocket /ws ─────────────────────────────────────────────────────────
export interface HeaderEvent {
  channels_live: number;
  groups_listening: number;
  autonomy_level: number;
  data_confidence: number | null;
}

export interface RealtimeEventMap {
  'raw.new': RawItem;
  'raw.state': RawStateEvent;
  'refinery.progress': RefineryProgress;
  'refinery.run': RefineryRun;
  'channel.qr': ChannelQrEvent;
  'channel.status': ChannelStatusEvent;
  'group.discovered': GroupDiscoveredEvent;
  /** v0.1.68: sự kiện kênh cho Nhật ký lắng nghe — chỉ tín hiệu (xem listenLog.ts). */
  'listen.event': ListenEventSignal;
  'cli.login': CliLoginEvent;
  header: HeaderEvent;
  'gen.step': GenStepEvent;
  'gen.done': GenDoneEvent;
  /** v0.1.23 (B6): chỉ người nhận nhận được. */
  'notification.new': NotificationItem;
  /** v0.1.69: sự cố đóng ⇒ chuông của nó đã đọc / "Đã xử lý" — mang `NotificationItem` đã cập nhật; chỉ chủ chuông nhận. */
  'notification.resolved': NotificationItem;
  pong: Record<string, never>;
}

export type RealtimeEventType = keyof RealtimeEventMap;

/** Server frame: `{"type": "…", "data": {…}, "at": "…"}`. */
export type RealtimeEvent = {
  [K in RealtimeEventType]: { type: K; data: RealtimeEventMap[K]; at?: string };
}[RealtimeEventType];

/** Close codes. */
export const WS_CLOSE_UNAUTHENTICATED = 4401;
export const WS_CLOSE_SETUP_REQUIRED = 4428;
