/**
 * Hợp đồng v0.1.68 — Nhật ký lắng nghe: MỘT dòng thời gian gộp tin nghe được (Kho thô) + sự kiện trên mọi kênh, kèm
 * "hệ thống đã làm gì" (sàng lọc, quyết định của agent trực, bản nháp ở Bàn làm việc). Chỉ đọc, quyền `data.read`.
 *
 * `GET /listen-log` (con trỏ: `next_cursor` là chuỗi mờ, đưa nguyên vào `cursor`) · `GET /listen-log/stats`.
 * Quyền/che do MÁY CHỦ quyết: tin 1-1 chỉ Owner hoặc người phụ trách đúng người đó; vai dưới Owner bị che số dài trong
 * `text`/tên; `decisions` chỉ có khi `decisions_visible`; `draft` chỉ có khi bản nháp nằm trong phạm vi người xem.
 * WebSocket `listen.event` chỉ là TÍN HIỆU (không có câu chữ) — màn tải lại qua API.
 */
import type { ApiClient } from './client';
import type { ChannelType, ListenMode, RawState, Ref, Since } from './phase2';

export type ListenLogKind = 'all' | 'message' | 'event';

export interface ListenLogQuery {
  cursor?: string;
  limit?: number;
  channel?: string;
  group_id?: string;
  since?: Since;
  /** Chỉ tin có tag agent. */
  tagged?: boolean;
  /** Chỉ tin agent đã làm gì đó (ghi chú / gợi ý / nháp — không tính im lặng). */
  acted?: boolean;
  /** Tìm chữ phía máy chủ (nội dung, tên người gửi, tên nhóm). */
  q?: string;
  kind?: ListenLogKind;
}

export type AgentDecisionKind = 'silent' | 'note' | 'suggest' | 'draft' | 'send';

export interface ListenLogDecision {
  id: string;
  at: string;
  agent: { id: string; name: string };
  decision: AgentDecisionKind;
  outcome: string | null;
  /** Câu tiếng Việt sẵn dùng: "Nháp trả lời: chờ duyệt", "Gợi ý hành động"… */
  label: string;
  /** Bản nháp ở Bàn làm việc (`/workbench?id=<draft.id>`); null khi không có hoặc ngoài phạm vi người xem. */
  draft: { id: string; code: string; status: string } | null;
}

export interface ListenLogMessage {
  kind: 'message';
  id: string;
  at: string;
  code: string;
  channel: { type: ChannelType | string; name: string };
  /** null = tin riêng 1-1. */
  group: Ref | null;
  direct: boolean;
  sender: { id: string; code: string | null; name: string | null } | null;
  direction: 'inbound' | 'outbound';
  msg_kind: string;
  text: string | null;
  tagged: boolean;
  /** Chế độ nghe LÚC tin vào kho; `listen_mode_known: false` = tin cũ, đang hiện chế độ hiện tại của nhóm. */
  listen_mode: ListenMode | 'reply' | null;
  listen_mode_label: string | null;
  listen_mode_known: boolean;
  screening: { state: RawState; label: string | null; confidence: number | null; note: string };
  decisions: ListenLogDecision[];
  /** Vì sao hệ thống không làm gì thêm (khi biết được). */
  note: string | null;
}

export type ListenEventType =
  | 'group_new'
  | 'group_left'
  | 'group_rejoined'
  | 'group_renamed'
  | 'members_changed'
  | 'groups_synced'
  | 'channel_connected'
  | 'channel_error'
  | 'channel_ended'
  | 'direct_new'
  // v0.1.72 (bot tài khoản riêng)
  | 'group_added_by_other'
  | 'bot_removed'
  | 'owner_called'
  | 'owner_paired'
  | 'owner_unpaired'
  | 'support_on'
  | 'support_off'
  | 'support_escalated';

export interface ListenLogEvent {
  kind: 'event';
  id: string;
  at: string;
  event_type: ListenEventType | string;
  icon: string;
  tone: 'ok' | 'warn' | 'bad' | 'accent' | 'neutral' | string;
  /** Câu tiếng Việt dễ hiểu, đã che theo quyền. */
  text: string;
  channel: { type: ChannelType | string; name: string } | null;
  group: Ref | null;
  person: { id: string; code: string | null; name: string | null } | null;
  direct: boolean;
  listen_mode: ListenMode | null;
  listen_mode_label: string | null;
  raw_event_id: string | null;
}

export type ListenLogItem = ListenLogMessage | ListenLogEvent;

export interface ListenLogPage {
  items: ListenLogItem[];
  next_cursor: string | null;
  decisions_visible: boolean;
}

export interface ListenLogStats {
  by_channel: Array<{ type: ChannelType | string; name: string; messages_24h: number; events_24h: number; groups_listening: number }>;
  messages_24h: number;
  events_24h: number;
  groups_listening: number;
  /** Nhóm đang nghe trong phạm vi người xem — cho ô lọc Nhóm. */
  groups: Array<{ id: string; code: string; name: string; channel: ChannelType | string; listen_mode: ListenMode }>;
}

/** Tín hiệu WebSocket `listen.event` (cần `data.read`; sự kiện 1-1 chỉ tới Owner). */
export interface ListenEventSignal {
  id: string;
  at: string;
  event_type: ListenEventType | string;
  channel_type: ChannelType | string | null;
  group_id: string | null;
  owner_only: boolean;
}

type Q = Record<string, string | number | boolean | null | undefined>;

export function listenLogEndpoints(r: ApiClient['request']) {
  return {
    list: (q: ListenLogQuery = {}, signal?: AbortSignal) => r<ListenLogPage>('/listen-log', { query: q as Q, signal }),
    stats: (signal?: AbortSignal) => r<ListenLogStats>('/listen-log/stats', { signal }),
  };
}
