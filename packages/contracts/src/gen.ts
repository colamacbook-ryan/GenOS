/**
 * Gen v1 — trợ lý quản trị trong Console (docs/design/gen-v1.md §3.3). Envelope có kiểu dùng chung web + test; API có
 * Pydantic tương ứng (`apps/api/gh/gen/envelope.py`, `extra="forbid"`).
 *
 * Luồng: `POST /gen/turns` → 202 {turn_id}; mỗi bước đã được server kiểm tới qua WS `gen.step` (chỉ người hỏi nhận,
 * `to_user`), kết thúc bằng `gen.done`. `GET /gen/turns/{id}` là đường dự phòng (polling) khi WS rớt.
 */
import type { ApiClient } from './client';
import type { BrowserJobStatus } from './social';

export type DataToolName =
  | 'overview.summary'
  | 'queue.list'
  | 'draft.list'
  | 'draft.get'
  | 'profile.search'
  | 'profile.get'
  | 'opportunity.list'
  | 'people.care'
  | 'audit.list'
  | 'system.health'
  | 'guide.list'
  | 'screens.list'
  | 'task.list'
  | 'staff.list'
  | 'refinery.summary'
  | 'hub.kho_summary'
  | 'hub.kho_search'
  | 'hub.kho_get'
  | 'social.accounts'
  | 'social.read'
  /** v0.1.49 (QD-16): Tài liệu / Deal / Vụ việc nội bộ + lịch / việc / mail / Drive Google qua Gen-hub (chỉ Owner, chỉ đọc). */
  | 'document.list'
  | 'document.get'
  | 'deal.list'
  | 'deal.get'
  | 'case.list'
  | 'case.get'
  | 'hub.calendar'
  | 'hub.tasks'
  | 'hub.mail_search'
  | 'hub.mail_read'
  | 'hub.drive_search'
  /** v0.1.41 (F-8): bước đầu của Bản tin Gen — đánh dấu nội dung ngoài (việc, khách, nháp, sự cố…). */
  | 'briefing.sources'
  /** v0.1.54: tool chỉ-đọc của Gen hướng dẫn — việc cần làm & bài học hôm nay của Sếp (chỉ Owner). */
  | 'coach.status'
  | 'group.list'
  | 'group.recent'
  | 'person.list';

export type UiAction =
  | { type: 'navigate'; screen: string; params?: Record<string, string> }
  | { type: 'highlight'; target: string; message: string; waitFor?: 'click' | 'none' }
  | { type: 'tour'; steps: TourStep[] };

export interface TourStep {
  /** Màn của bước (bỏ trống = màn của bước trước / màn đang mở). */
  screen?: string;
  target: string;
  message: string;
}

export interface Suggestion {
  label: string;
  action: UiAction;
}

/**
 * Gen v2 (A4) — đề xuất thao tác có xác nhận. Gen KHÔNG tự ghi: server kiểm đề xuất của model (quyền, mục tiêu
 * registry, id phải vừa thấy trong kết quả tool) rồi gửi thẻ này; chỉ khi người dùng bấm Xác nhận, web gọi
 * `POST /gen/proposals/{id}/confirm` và server thực hiện nhân danh người đó qua endpoint sẵn có (Action Log
 * actor=user, via=gen). `requires_pin` = mục tiêu registry nhạy cảm → API trả 423, client tự hỏi PIN rồi gửi lại.
 */
export type GenProposalType =
  | 'draft_message'
  | 'reminder'
  | 'assign'
  | 'social_reply'
  | 'social_dm'
  /** v0.1.50 (F-81, QD-18): Gen đề xuất GHI NHỚ một quy ước / sở thích của Sếp (Gen nhớ) — không cần PIN, chỉ Owner. */
  | 'memory_note'
  /** v0.1.50 (F-81, QD-18): Gen đề xuất TẠO / SỬA một bản ghi Phiên · Việc ở Kho dữ liệu qua Gen-hub — Xác nhận + mã PIN, chỉ Owner. */
  | 'kho_create'
  | 'kho_update';
export type GenProposalStatus = 'pending' | 'confirmed' | 'cancelled';

export interface GenSubjectRef {
  type: 'person' | 'group';
  id: string;
}

export interface DraftMessageFields {
  title: string;
  text: string;
  subject?: GenSubjectRef | null;
}

export interface ReminderFields {
  title: string;
  /** ISO 8601 (UTC, hậu tố Z). */
  remind_at: string;
  due_at?: string | null;
  priority: 'P1' | 'P2' | 'P3';
  assignee_user_id?: string | null;
  subject?: GenSubjectRef | null;
}

export interface AssignFields {
  item_type: 'task' | 'inbox';
  item_id: string;
  user_id: string;
}

/**
 * v0.1.47 (F-79): gửi trả lời bình luận / nhắn tin Facebook. Gen KHÔNG tự gửi: chỉ khi Sếp bấm Xác nhận (+ mã PIN) máy chủ
 * mới xếp một việc `write` cho trình duyệt nền; `text` là nguyên văn sẽ được gửi.
 */
export interface SocialWriteFields {
  account_id: string;
  target_url: string;
  text: string;
}

/**
 * v0.1.50 (F-81): đề xuất GHI NHỚ. `text` ≤ 280 ký tự, `reason` ≤ 200 (rỗng nếu Gen không nêu lý do). Gen KHÔNG tự ghi: chỉ khi
 * Sếp bấm Xác nhận máy chủ mới lưu ghi chú vào "Gen nhớ" (Cài đặt › Bộ não AI). Không cần mã PIN.
 */
export interface MemoryNoteFields {
  text: string;
  reason: string;
}

/**
 * v0.1.56: tên hiển thị của Kho nối qua Gen-hub — MỘT chỗ ở web/contracts (bản sao API: `KHO_LABEL` ở
 * `apps/api/gh/hub_link/__init__.py`). Tên chung, không mang tên riêng của chủ Gen-hub. Nhãn ngắn trên nút / thẻ vẫn là "Kho".
 */
export const KHO_LABEL = 'Kho dữ liệu';

/** v0.1.57 (Nợ #30): Owner tự đặt "Tên Kho" (tối đa 40 ký tự; rỗng ⇒ KHO_LABEL). Bản sao API: `KHO_LABEL_MAX` ở `gh/hub_link/__init__.py`. */
export const KHO_LABEL_MAX = 40;

/**
 * Thay tên Kho MẶC ĐỊNH trong một chuỗi dựng sẵn từ KHO_LABEL bằng tên hiệu lực của tổ chức (`HubLink.kho_label`), như `relabel()` của
 * API. Giữ nguyên "Kho dữ liệu thô" (tầng dữ liệu khác). Tên rỗng / chính là mặc định ⇒ trả nguyên chuỗi.
 */
export function relabelKho(text: string, label?: string | null): string {
  const name = (label ?? '').trim();
  if (!name || name === KHO_LABEL) return text;
  return text.replace(/Kho dữ liệu(?! thô)/g, () => name);
}

/** Bảng của Kho dữ liệu mà Gen được đề xuất ghi (v0.1.50): chỉ Phiên và Việc. */
export type KhoBang = 'Phiên' | 'Việc';

/**
 * v0.1.50 (F-81, QD-18): đề xuất TẠO bản ghi Kho dữ liệu. `record` = các trường Kho (tên trường tiếng Việt, xem `KHO_FIELDS`) →
 * chuỗi; bảng cố định, KHÔNG sửa được trên thẻ. Chỉ khi Sếp bấm Xác nhận + nhập mã PIN, máy chủ mới ghi qua Gen-hub.
 */
export interface KhoCreateFields {
  bang: KhoBang;
  record: Record<string, string>;
}

/** v0.1.50: đề xuất SỬA bản ghi đã có (`ma` = 'PHIEN-n' | 'VIEC-n', cố định); `record` chỉ gồm trường sẽ đổi. */
export interface KhoUpdateFields {
  ma: string;
  record: Record<string, string>;
}

/**
 * v0.1.50 — trường Kho dữ liệu Gen được ghi, THEO THỨ TỰ hiển thị. Nguồn sự thật: `apps/api/gh/hub_link/kho_write.py`
 * (`KHO_FIELDS`); bản sao này được `kho-write-proposal-v0150.test.tsx` so khớp với tệp đó.
 */
export const KHO_FIELDS: Record<KhoBang, readonly string[]> = {
  Phiên: ['Chủ đề', 'Ngày', 'Đã chốt', 'Đang bàn', 'Việc tiếp', 'Cảnh báo'],
  Việc: ['Tiêu đề', 'Trạng thái', 'Ưu tiên', 'Hạn', 'Link Issue/PR', 'Ngày bắt đầu', 'Ngày xong'],
};
/** Trường bắt buộc khi tạo bản ghi (dấu * ở máy chủ). */
export const KHO_REQUIRED: Record<KhoBang, string> = { Phiên: 'Chủ đề', Việc: 'Tiêu đề' };
/** Trường kiểu ngày `YYYY-MM-DD` (ô chọn ngày khi Sửa). */
export const KHO_DATE_FIELDS: Record<KhoBang, readonly string[]> = { Phiên: ['Ngày'], Việc: ['Hạn', 'Ngày bắt đầu', 'Ngày xong'] };
/**
 * Độ dài tối đa (ký tự) mỗi trường Kho — bản sao `TITLE_MAX` / `TEXT_MAX` / `WARNING_MAX` của `kho_write.py` (test so khớp tệp đó):
 * Chủ đề / Tiêu đề 200, Cảnh báo 1000, các trường chữ khác 2000.
 */
export const KHO_TITLE_MAX = 200;
export const KHO_TEXT_MAX = 2000;
export const KHO_WARNING_MAX = 1000;
/** Độ dài tối đa của một trường Kho theo bảng (trường bắt buộc = tiêu đề; 'Cảnh báo'; còn lại chữ dài). */
export function khoMaxLen(bang: KhoBang, field: string): number {
  if (field === KHO_REQUIRED[bang]) return KHO_TITLE_MAX;
  return field === 'Cảnh báo' ? KHO_WARNING_MAX : KHO_TEXT_MAX;
}
export const KHO_STATUS = ['Chờ', 'Đang làm', 'Chờ duyệt', 'Xong'] as const;
export const KHO_PRIORITY = ['P1', 'P2', 'P3'] as const;

export interface GenProposalResult {
  /**
   * v0.1.47: `social_write` — `id` = job_id của việc gửi; web theo dõi qua `GET /social/jobs/{id}`. v0.1.50: `memory_note` —
   * `id` = id ghi chú Gen nhớ (`screen: 'system'`); `kho_record` — đã ghi vào Kho dữ liệu: `code` = mã bản ghi ('PHIEN-12'; null
   * khi Gen-hub không trả mã), `bang`, `id` = null, `screen` = null.
   */
  type: 'draft' | 'task' | 'inbox_item' | 'social_write' | 'memory_note' | 'kho_record';
  id: string | null;
  code?: string | null;
  /** Màn xem kết quả (khoá GEN_SCREENS); v0.1.50: `null` với `kho_record`. */
  screen: string | null;
  /** v0.1.50: chỉ với `kho_record` — bảng đã ghi. */
  bang?: KhoBang;
  /**
   * v0.1.43: chỉ với `type: 'draft'` — nháp có nơi gửi (target) nên duyệt ở Bàn làm việc sẽ gửi đi thật. Hiện API chỉ
   * gắn nơi gửi khi đối tượng là NHÓM; thiếu/false ⇒ web không ghi "Duyệt & gửi".
   */
  sendable?: boolean;
  /** v0.1.47: chỉ với `social_write` — trạng thái việc gửi lúc xác nhận (thường `queued`). */
  status?: BrowserJobStatus;
}

interface GenProposalBase {
  id: string;
  /** Tóm tắt do HỆ THỐNG viết từ các trường đã kiểm (không phải lời model). */
  summary: string;
  /**
   * Nhãn hiển thị: `user` (người được giao), `item` (việc/mục), `subject` (đối tượng). v0.1.47 (gửi Facebook): `account`
   * (tên tài khoản), `target` (bình luận/người nhận), `write_gate` (`open`|`locked`), `suspicious` (`'1'` = mục có dấu hiệu lừa đảo).
   * v0.1.50: `memory_note` có `count` ('n/30'); `kho_create`/`kho_update` có `bang`, `target` (mã bản ghi), `write_scope`
   * (`ok`|`missing` — token Gen-hub đã được cấp quyền ghi Kho chưa) và, với `kho_update`, `cur:<tên trường>` = giá trị hiện tại.
   * v0.1.57: `kho` = tên Kho Owner tự đặt (chỉ có khi khác mặc định "Kho dữ liệu"); thẻ dùng nó cho tiêu đề / cảnh báo (`relabelKho`).
   */
  labels: Record<string, string>;
  /** Mục tiêu registry gắn với đề xuất (quyền + cờ nhạy cảm lấy từ đây). */
  target: string;
  requires_pin: boolean;
  status: GenProposalStatus;
  result?: GenProposalResult;
}

export type GenProposal =
  | (GenProposalBase & { type: 'draft_message'; fields: DraftMessageFields })
  | (GenProposalBase & { type: 'reminder'; fields: ReminderFields })
  | (GenProposalBase & { type: 'assign'; fields: AssignFields })
  | (GenProposalBase & { type: 'social_reply'; fields: SocialWriteFields })
  | (GenProposalBase & { type: 'social_dm'; fields: SocialWriteFields })
  | (GenProposalBase & { type: 'memory_note'; fields: MemoryNoteFields })
  | (GenProposalBase & { type: 'kho_create'; fields: KhoCreateFields })
  | (GenProposalBase & { type: 'kho_update'; fields: KhoUpdateFields });

/** Trường người dùng được sửa trên thẻ trước khi xác nhận (còn lại giữ nguyên như lúc đề xuất). */
export const GEN_PROPOSAL_EDITABLE: Record<GenProposalType, readonly string[]> = {
  draft_message: ['title', 'text'],
  reminder: ['title', 'remind_at', 'due_at', 'priority', 'assignee_user_id'],
  assign: ['user_id'],
  social_reply: ['text'],
  social_dm: ['text'],
  // v0.1.50: ghi nhớ sửa được cả hai ô; ghi Kho chỉ sửa `record` (bảng / mã bản ghi khoá cứng).
  memory_note: ['text', 'reason'],
  kho_create: ['record'],
  kho_update: ['record'],
};

export interface GenAssignee {
  id: string;
  name: string;
  role: string | null;
  me: boolean;
}

export type GenStep =
  | { kind: 'say'; text: string }
  | { kind: 'tool'; name: DataToolName; args?: Record<string, unknown> }
  | { kind: 'ui'; action: UiAction }
  | { kind: 'suggest'; items: Suggestion[] }
  | { kind: 'proposal'; proposal: GenProposal }
  /**
   * v0.1.55 (G3): thông báo của HỆ THỐNG (không phải lời model), luôn là chuỗi — vd "Em dùng chế độ Tự động vì mức “Kỹ hơn” chưa
   * có nguồn AI phù hợp …" khi lựa chọn model của khung chat phải hạ về Tự động. Không vào lịch sử gửi cho model.
   */
  | { kind: 'notice'; text: string }
  | { kind: 'done' };

/** Bước server đẩy xuống web (đã kiểm). `tool` chỉ báo tên — kết quả không gửi về trình duyệt. */
export interface GenStepEvent {
  turn_id: string;
  conversation_id: string;
  seq: number;
  step: GenStep;
}

export type GenTurnStatus = 'running' | 'done' | 'failed';

export interface GenDoneEvent {
  turn_id: string;
  conversation_id: string;
  status: GenTurnStatus;
}

export interface GenTurn {
  turn_id: string;
  conversation_id: string;
  status: GenTurnStatus;
  steps: GenStepEvent[];
}

export interface GenTurnContext {
  route: string;
  screen_key: string | null;
  visible_targets: string[];
}

/** v0.1.55 (G3): tầng model của khung chat — Tự động (chuẩn) · Nhanh · Cân bằng · Kỹ hơn ('deep' ↔ tầng 'strong' của bộ định tuyến). */
export type GenModelTier = 'auto' | 'fast' | 'balanced' | 'deep';
/** Mức suy nghĩ: Thấp / Vừa / Cao. */
export type GenModelEffort = 'low' | 'medium' | 'high';

/**
 * v0.1.55 (G3): lựa chọn model của khung chat, gửi kèm `POST /gen/turns` (`GenTurnBody.model_choice`; web BỎ trường này khi
 * `tier === 'auto'`). Giá trị lạ ⇒ 422 MODEL_CHOICE_INVALID ("Lựa chọn model không hợp lệ — em dùng chế độ Tự động nhé").
 * `effort` chỉ có nghĩa khi tầng được chọn hỗ trợ (máy chủ bỏ mức không hỗ trợ).
 */
export interface ModelChoice {
  tier: GenModelTier;
  effort?: GenModelEffort;
}

/** Một tầng trong `GenSettings.model_options` (hợp đồng `gh.defaults.profiles.choice_options`, G1 → G3). */
export interface GenModelOption {
  tier: GenModelTier;
  /** Có dùng được cho NGƯỜI NÀY (nhân viên không dùng Antigravity CLI) trong hội thoại mới. */
  available: boolean;
  /** Mức suy nghĩ mà các model của tầng này hỗ trợ (⊆ low/medium/high); rỗng ⇒ ẩn hàng "Mức suy nghĩ". */
  efforts: GenModelEffort[];
}

export interface GenModelOptions {
  tiers: GenModelOption[];
}

export interface GenTurnBody {
  conversation_id?: string | null;
  text: string;
  context: GenTurnContext;
  /** v0.1.55 (G3): bỏ khi Tự động (chuẩn). */
  model_choice?: ModelChoice;
}

export interface GenConversation {
  id: string;
  title: string;
  created_at: string;
  last_at: string;
  /** v0.1.41 (F-8): `briefing` = hội thoại Bản tin Gen (07:30 / 17:30 giờ VN); máy chủ cũ không gửi ⇒ coi là `chat`. */
  kind?: 'chat' | 'briefing';
}

/** v0.1.41 (F-86): đánh giá câu trả lời / bản tin của Gen. */
export type GenRating = 'helpful' | 'not_helpful';

/** v0.1.41 (F-8): một mục của Bản tin Gen (việc tới hạn, khách nóng, nháp chờ duyệt, sự cố…). */
export interface GenBriefingSection {
  key: string;
  title: string;
  count: number;
  lines: string[];
  link: string;
  /**
   * v0.1.49 (F-8, QD-16): mục đọc từ Gen-hub (lịch hôm nay `calendar_today`, mail cần trả lời `mail_reply`, việc đang mở
   * `gtasks_open`). `state`: `ok` có dòng · `empty` không có gì · `error` chưa đọc được · `breaker` Gen-hub tạm không trả
   * lời. Máy chủ cũ không gửi ⇒ coi là `ok`. Mục chưa nối / thiếu quyền KHÔNG có trong danh sách (xem `hub_hint`).
   * Mục nội bộ đọc lỗi cũng mang `state: 'error'` (chỉ để chuông/Telegram không báo "Không có việc gì…"); web chỉ vẽ
   * thẻ cho mục `external`.
   */
  state?: 'ok' | 'empty' | 'error' | 'breaker';
  /** `true` = mục lấy từ Gen-hub (nội dung ngoài, đã che). */
  external?: boolean;
  /** Lỗi thô đã lọc bí mật (chỉ hiện trong "Chi tiết kỹ thuật"); luôn là chuỗi hoặc null. */
  detail?: string | null;
  /** v0.1.49: mục Gen-hub chạm trần số mục đọc (10) — có thể còn nhiều hơn `count` ⇒ hiện "10+". */
  more?: boolean;
}

export interface GenMessage {
  id: string;
  role: 'user' | 'assistant';
  turn_id: string | null;
  /**
   * user: `{text}`; assistant: `{steps: GenStep[]}`. v0.1.41 (F-8): bản tin thêm `kind: 'briefing'`, `slot_label`,
   * `needs_api_key`, `sections` (bước vẫn ở `steps`, bước tool `briefing.sources` đứng đầu).
   */
  content: {
    text?: string;
    steps?: GenStep[];
    kind?: 'briefing';
    slot?: string;
    slot_label?: string;
    summary_source?: 'model' | 'none';
    needs_api_key?: boolean;
    sections?: GenBriefingSection[];
    /** v0.1.49: một câu gợi ý khi mục Gen-hub bị ẩn (chưa nối / thiếu quyền) — null khi không có gì để nhắc. */
    hub_hint?: string | null;
    /** v0.1.49: vị trí trong `steps` để chèn thẻ mục Gen-hub (ngay sau "Sự cố cần Sếp"); máy chủ cũ không gửi. */
    hub_at?: number;
  };
  created_at: string;
  /** v0.1.41 (F-86): đánh giá của chính người xem cho lượt này (máy chủ cũ không gửi). */
  feedback?: GenRating | null;
}

export interface GenSettings {
  enabled: boolean;
  /** Vai trò được dùng Gen (v1: chỉ `owner`). */
  roles: string[];
  retention_days: number;
  /** Người đang xem có dùng được Gen không. */
  available: boolean;
  /** Có nguồn Jev (System One) đang bật — Gen dùng làm bộ quyết định nhanh. */
  decider: 'jev' | 'llm';
  /** v0.1.55 (G3): tầng + mức suy nghĩ khung chat được mời chọn; máy chủ cũ không gửi ⇒ khung chat coi mọi tầng là dùng được. */
  model_options?: GenModelOptions;
}

/** Tên gọi khác của cấu hình Gen (`GET /gen/settings`). */
export type GenConfig = GenSettings;

export type TourOutcome = 'done' | 'skipped' | 'target_missing';

/**
 * v0.1.50 (F-81, QD-18) — "Gen nhớ" (Cài đặt › Bộ não AI): quy ước, sở thích Sếp đã xác nhận; Gen đọc khi trả lời Sếp và khi
 * soạn Bản tin. CHỈ Owner (vai trò khác 403), không cần PIN. Ghi chú mới chỉ vào qua đề xuất `memory_note` + Xác nhận;
 * Sếp sửa / xoá trực tiếp ở đây (sửa ⇒ `source` thành `owner`).
 */
export interface GenMemoryNote {
  id: string;
  text: string;
  reason: string | null;
  /** `gen` = Gen đề xuất, Sếp xác nhận · `owner` = Sếp đã sửa. */
  source: 'gen' | 'owner';
  created_at: string;
  updated_at: string;
}

export interface GenMemoryList {
  items: GenMemoryNote[];
  /** Số ghi chú tối đa (30). */
  limit: number;
  /** Độ dài tối đa của `text` (280) và `reason` (200). */
  max_len: number;
  reason_max: number;
}

export interface GenMemoryPatchBody {
  text?: string;
  reason?: string | null;
}

/**
 * v0.1.54 — "Gen hướng dẫn" (Gen coach): thẻ "Hôm nay của Sếp" ở đầu khung Gen, Cài đặt › Bộ não AI › Gen hướng dẫn và
 * Trợ giúp › Lộ trình học cùng Gen. CHỈ Owner (vai trò khác 403 FORBIDDEN). 0 lời gọi model, 0 ghi `gen_messages` ở mọi
 * đường `/gen/coach/*`; khuyên chứ không ép. Máy chủ không bao giờ trả `detail`, `message`, email hay token.
 *
 * Mã lỗi (ApiError, title tiếng Việt thân thiện): 403 FORBIDDEN · 404 COACH_ITEM_UNKNOWN · 422 COACH_DISMISS_NOT_ALLOWED
 * (việc khẩn P0 không tắt được) · 422 COACH_CONFIRM_REQUIRED (tắt việc phải gửi `confirm: true`) · 422 VALIDATION.
 */
export type CoachLevel = 'P0' | 'P1' | 'P2' | 'P3';

/** Nút "Thử ngay" / "Làm thử": `target` là mục tiêu registry (có thể dạng dòng `guide.item.do:7`). */
export interface CoachTry {
  label: string;
  target: string;
}

/** Một việc cần làm ngay (≤ 3 mỗi lần). `target` (làm sáng phần tử) hoặc `link` (đường dẫn trong app) — hoặc cả hai. */
export interface CoachTodo {
  /** `health.<kind>` · `model.missing` · `boss.<key>` · `backup.unset` · `hub.token_expiring` · `drafts.pending` · `followup.<n>`. */
  key: string;
  level: CoachLevel;
  title: string;
  why: string;
  target?: string | null;
  link?: string | null;
  /** `false` với P0 (khẩn cấp không tắt được). */
  can_dismiss: boolean;
  /** Câu cảnh báo hiện trong hộp xác nhận khi Sếp chọn "Không dùng việc này". */
  dismiss_warning?: string | null;
}

/** "Sếp biết chưa?" — mẹo ngắn. */
export interface CoachTip {
  key: string;
  title: string;
  body: string;
  try?: CoachTry | null;
}

export type CoachLessonStatus = 'new' | 'shown' | 'understood' | 'snoozed' | 'done';

/** "Bài học hôm nay · k/19". */
export interface CoachLesson {
  /** `N01`..`N10` hoặc `G05`..`G14` (bài sinh từ Hướng dẫn thiết lập). */
  id: string;
  k: number;
  total: number;
  title: string;
  body: string;
  try?: CoachTry | null;
  status: CoachLessonStatus;
}

export interface CoachProgress {
  required_done: number;
  required_total: number;
  lessons_done: number;
  lessons_total: number;
  /** Đủ việc bắt buộc và không còn việc khẩn trong một thời gian — thẻ hiện "Hệ thống đã ổn định". */
  stable: boolean;
  stable_since: string | null;
}

/** `GET /gen/coach/today[?mark_shown=1]`. */
export interface CoachToday {
  date: string;
  /** `false` khi Sếp đã Tắt hướng dẫn — web không vẽ thẻ. */
  enabled: boolean;
  /** Đang "Hoãn tất cả" tới lúc này (ISO) — null khi không hoãn. */
  snoozed_until: string | null;
  todos: CoachTodo[];
  tip: CoachTip | null;
  lesson: CoachLesson | null;
  progress: CoachProgress;
  /** Có nội dung mới Sếp chưa được thấy trong khung (chấm đỏ ở nút Gen). */
  unseen: boolean;
}

export type CoachItemActionName = 'understood' | 'snooze' | 'done' | 'dismiss' | 'restore';

/**
 * `POST /gen/coach/items/{item_key}` → 204. `item_key`: `todo:<khoá>` | `tip:<key>` | `lesson:<N01..N10|G05..G14>` |
 * `card:setup_followup`. `snooze` kèm `days` 1|3|7; `dismiss` bắt buộc `confirm: true` (thiếu ⇒ 422 COACH_CONFIRM_REQUIRED,
 * việc P0 ⇒ 422 COACH_DISMISS_NOT_ALLOWED).
 */
export interface CoachItemAction {
  action: CoachItemActionName;
  days?: 1 | 3 | 7;
  confirm?: true;
}

/** Việc Sếp đã chọn không dùng (hiện ở Cài đặt, mỗi dòng có "Bật lại"). */
export interface CoachDismissedItem {
  key: string;
  level: CoachLevel;
  title: string;
}

/** `GET /gen/coach/prefs`. Giờ yên lặng theo múi giờ tổ chức. */
export interface CoachPrefs {
  enabled: boolean;
  bell: boolean;
  lessons_per_day: number;
  quiet_start: number;
  quiet_end: number;
  snooze_until: string | null;
  /** Thẻ "Việc thiết lập tiếp" đang được hoãn tới lúc này (ISO) — null khi không hoãn. */
  followup_snoozed_until: string | null;
  dismissed: CoachDismissedItem[];
}

/** `PATCH /gen/coach/prefs` → CoachPrefs. `lessons_per_day` 0..2; `quiet_*` 0..23; `snooze_all_days` 0|1|3|7 (0 = bỏ hoãn). */
export interface CoachPrefsPatch {
  enabled?: boolean;
  bell?: boolean;
  lessons_per_day?: number;
  quiet_start?: number;
  quiet_end?: number;
  snooze_all_days?: 0 | 1 | 3 | 7;
}

/** Một bài của Lộ trình học cùng Gen (`GET /gen/coach/curriculum`). */
export interface CurriculumLesson {
  id: string;
  k: number;
  title: string;
  body: string;
  try?: CoachTry | null;
  status: CoachLessonStatus;
}

export interface Curriculum {
  /** Tổng số bài (19). */
  total: number;
  lessons: CurriculumLesson[];
}

export function genEndpoints(r: ApiClient['request']) {
  return {
    gen: {
      settings: (signal?: AbortSignal) => r<GenSettings>('/gen/settings', { signal }),
      updateSettings: (body: { enabled?: boolean; retention_days?: number }) =>
        r<GenSettings>('/gen/settings', { method: 'PATCH', body }),
      conversations: (signal?: AbortSignal) => r<GenConversation[]>('/gen/conversations', { signal }),
      messages: (id: string, signal?: AbortSignal) =>
        r<GenMessage[]>(`/gen/conversations/${encodeURIComponent(id)}/messages`, { signal }),
      removeConversation: (id: string) =>
        r<void>(`/gen/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' }),
      createTurn: (body: GenTurnBody) =>
        r<{ turn_id: string; conversation_id: string }>('/gen/turns', { method: 'POST', body }),
      turn: (id: string, signal?: AbortSignal) => r<GenTurn>(`/gen/turns/${encodeURIComponent(id)}`, { signal }),
      ack: (id: string, body: { step: number; outcome: TourOutcome }) =>
        r<void>(`/gen/turns/${encodeURIComponent(id)}/ack`, { method: 'POST', body }),
      assignees: (signal?: AbortSignal) => r<{ items: GenAssignee[] }>('/gen/assignees', { signal }),
      confirmProposal: (id: string, fields: Record<string, unknown> = {}) =>
        r<GenProposal>(`/gen/proposals/${encodeURIComponent(id)}/confirm`, { method: 'POST', body: { fields } }),
      cancelProposal: (id: string) => r<GenProposal>(`/gen/proposals/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
      /** v0.1.41 (F-86): Hữu ích / Không hữu ích cho một lượt trả lời hoặc bản tin. */
      feedback: (body: { conversation_id: string; turn_id: string; rating: GenRating }) =>
        r<{ turn_id: string; rating: GenRating; kind: 'reply' | 'briefing' }>('/gen/feedback', { method: 'PUT', body }),
      clearFeedback: (turnId: string) => r<void>(`/gen/feedback/${encodeURIComponent(turnId)}`, { method: 'DELETE' }),
      /** v0.1.50 (F-81): Gen nhớ — chỉ Owner. 409 GEN_MEMORY_FULL / GEN_MEMORY_DUPLICATE, 422 field_errors, 404. */
      memory: {
        list: (signal?: AbortSignal) => r<GenMemoryList>('/gen/memory', { signal }),
        update: (id: string, body: GenMemoryPatchBody) =>
          r<GenMemoryNote>(`/gen/memory/${encodeURIComponent(id)}`, { method: 'PATCH', body }),
        remove: (id: string) => r<void>(`/gen/memory/${encodeURIComponent(id)}`, { method: 'DELETE' }),
      },
      /**
       * v0.1.54: Gen hướng dẫn — chỉ Owner. `today(true)` = `?mark_shown=1` (đánh dấu Sếp đã thấy thẻ ⇒ tắt chấm đỏ);
       * `itemAction` nhận `item_key` đầy đủ (`todo:boss.hub`, `tip:…`, `lesson:N01`, `card:setup_followup`).
       */
      coach: {
        today: (markShown?: boolean, signal?: AbortSignal) =>
          r<CoachToday>('/gen/coach/today', { signal, ...(markShown ? { query: { mark_shown: 1 } } : {}) }),
        itemAction: (itemKey: string, body: CoachItemAction) =>
          r<void>(`/gen/coach/items/${encodeURIComponent(itemKey)}`, { method: 'POST', body }),
        prefs: (signal?: AbortSignal) => r<CoachPrefs>('/gen/coach/prefs', { signal }),
        patchPrefs: (body: CoachPrefsPatch) => r<CoachPrefs>('/gen/coach/prefs', { method: 'PATCH', body }),
        curriculum: (signal?: AbortSignal) => r<Curriculum>('/gen/coach/curriculum', { signal }),
      },
    },
  };
}
