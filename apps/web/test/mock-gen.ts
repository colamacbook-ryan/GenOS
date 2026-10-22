/**
 * Mock API Gen v1 (`apps/api/gh/gen/routes.py`) — trả lời theo KỊCH BẢN cố định để demo Console chạy offline
 * (`npm run dev:mock`) và cho Playwright. Bước đẩy qua WS `gen.step`/`gen.done` (cách nhau `stepMs`) và lưu cho
 * `GET /gen/turns/{id}` (đường dự phòng) — y như API thật. Chỉ Owner dùng được (cờ gen.enabled mặc định).
 *
 * Kịch bản theo từ khoá trong câu hỏi:
 *   "gấp" / "xử lý" / "hôm nay"   → tra Tổng quan, mở Tổng quan, làm sáng Hàng đợi, đề xuất
 *   "jev" / "khoá" / "model"      → tour 3 bước: tab Bộ não AI → thẻ Jev → nút Kiểm tra
 *   "sao lưu" / "backup"          → mở Dữ liệu & lưu trữ, làm sáng nút "Sao lưu ngay"
 *   "nhắc"                         → v2 (A4): thẻ đề xuất "Tạo nhắc việc" (Xác nhận / Sửa / Huỷ)
 *   "nháp"                         → v2 (A4): thẻ "Soạn nháp tin" CẦN PIN (xác nhận → 423 → hỏi PIN → gửi lại)
 *   "mạng xã hội" / "facebook"     → v0.1.39 (F-32): mở trang Tài khoản mạng xã hội (`navigate social`)
 *   "trả lời bình luận"            → v0.1.47 (F-79): thẻ `social_reply` CẦN PIN (nhãn account/target/write_gate; thêm "đáng ngờ" →
 *                                    suspicious='1'); "nhắn tin facebook" → thẻ `social_dm`. Xác nhận: 423 khi chưa PIN, rồi gọi
 *                                    mock-social `createWrite` (409 SOCIAL_HALTED / SOCIAL_WRITE_LOCKED, 429 SOCIAL_WRITE_LIMIT…) →
 *                                    `result {type:'social_write', id: job_id, screen:'social', status}`. Chưa có tài khoản đăng nhập
 *                                    → Gen chỉ mở trang Tài khoản mạng xã hội.
 *   "nhớ giúp" / "ghi nhớ"         → v0.1.50 (F-81): thẻ `memory_note`; "… vào Kho" → thẻ `kho_create`/`kho_update` (CẦN PIN) — kịch bản
 *                                    + xác nhận nằm ở `mock-gen-v0150.ts` (qua `opts.extra`), `/gen/memory` cũng ở đó.
 *
 * Hook e2e (v0.1.27): `POST /api/v1/__mock/p3/gen/fireReminders` = worker `task_reminder_scan` tới giờ — mỗi
 * nhắc việc đã xác nhận → chuông `task.reminder` cho các Owner (một lần).
 * v0.1.41 (F-8, F-86): `POST /api/v1/gen/__mock/briefing` (hoặc `__mock/p3/gen/briefing`) {"slot"?: "sang"|"chieu",
 * "needs_api_key"?: bool} = worker Bản tin Gen tới giờ — tạo hội thoại bản tin (content đúng hợp đồng) + chuông
 * `gen.briefing` link `/overview?gen=<id>`, trả {conversation_id}. v0.1.49 (QD-16): thêm `"hub"?: "ok"|"missing"|"breaker"|"error"|"off"`
 * (mặc định `off`) — mục Gen-hub: ok = Lịch hôm nay (2) + Mail cần trả lời (3) + Việc Google đang mở (1); missing = thiếu quyền mail ⇒ không có mục
 * mail, `hub_hint` + bước say/suggest "Mở thẻ Gen-hub"; breaker = cả 3 mục "Gen-hub tạm không trả lời" + detail; error = lịch lỗi (detail),
 * mail ok, việc trống. `PUT /gen/feedback`, `DELETE /gen/feedback/{turn}`
 * lưu đánh giá; tin trong `messages` có `feedback`, mục trong `conversations` có `kind`.
 *   còn lại                        → lời chào + gợi ý
 */
import { randomUUID } from 'node:crypto';
import type { GenBriefingSection, GenMessage, GenModelOptions, GenModelTier, GenProposal, GenRating, GenStep } from '../../../packages/contracts/src/gen';
import type { GenExtra } from './mock-gen-v0150';
import type { WriteOutcome, WriteRequest } from './mock-social';
import type { DraftDetail } from '../../../packages/contracts/src/p3-core';
import { BAO, GROUP_TP } from './mock-p3-core';
import type { P2Ctx } from './mock-phase2';
import { USER_IDS } from './mock-ids';

export interface MockGenOptions {
  emit: (type: string, data: unknown) => void;
  /** Khoảng cách giữa các bước (ms). */
  stepMs?: number;
  /** Chuông cho mọi Owner (như `notifications.notify` + `owner_ids` ở API thật). */
  notifyOwners?: (kind: string, title: string, body: string, link: string | null) => void;
  /**
   * v0.1.43 (F-24): kho nháp của Bàn làm việc (mock-p3-core `hooks.push` — cùng cơ chế `create_draft` dùng chung).
   * Xác nhận nháp tin tạo nháp THẬT ở đây (chờ duyệt) và trả `result.id` = id nháp đó.
   */
  pushDraft?: (d: DraftDetail) => unknown;
  /** v0.1.47 (F-79): nối sang mock-social — tài khoản đăng nhập đầu tiên + cổng ghi, và tạo việc gửi khi xác nhận. */
  social?: {
    writeContext: () => { account_id: string; account_label: string; gate: 'open' | 'locked' } | null;
    createWrite: (req: WriteRequest) => WriteOutcome;
  };
  /** v0.1.50 (F-81, QD-18): kịch bản + xác nhận cho đề xuất Ghi nhớ / Ghi vào Kho (mock-gen-v0150.ts). */
  extra?: GenExtra;
}

interface Turn {
  turn_id: string;
  conversation_id: string;
  status: 'running' | 'done' | 'failed';
  steps: Array<{ turn_id: string; conversation_id: string; seq: number; step: GenStep }>;
}

interface Conversation {
  id: string;
  title: string;
  created_at: string;
  last_at: string;
  kind: 'chat' | 'briefing';
  messages: Array<Omit<GenMessage, 'feedback'>>;
}

/** v0.1.49 (QD-16): chế độ mục Gen-hub của bản tin giả (xem ghi chú đầu tệp). */
export type BriefingHubMode = 'ok' | 'missing' | 'breaker' | 'error' | 'off';
export const BRIEFING_HUB_MODES: readonly BriefingHubMode[] = ['ok', 'missing', 'breaker', 'error', 'off'];

/** Đúng câu máy chủ (`briefing.HUB_HINT_SCOPE` với mục mail). */
export const HUB_HINT_MAIL = 'Bản tin chưa có mail cần trả lời: vào Gen-hub tick thêm quyền đọc mail cho token của Gen-Harness, rồi bấm Kiểm tra ở Kết nối › Gen-hub.';
/** Nút dưới lời nhắc (như `briefing.HUB_BUTTON` / `HUB_SPOT_SCOPE`): cuộn tới + làm sáng thẻ Gen-hub ở Kết nối. */
export const HUB_BUTTON = 'Mở thẻ Gen-hub';
export const HUB_SPOT_SCOPE = 'Thẻ Gen-hub: sau khi tick thêm quyền đọc trong Gen-hub, bấm Kiểm tra ở đây.';

/** Các mục `external` của bản tin theo chế độ; `hint` = câu nhắc khi có mục bị ẩn (chưa nối / thiếu quyền). */
export function hubSections(hub: BriefingHubMode): { sections: GenBriefingSection[]; hint: string | null } {
  const calendar: GenBriefingSection = {
    key: 'calendar_today', title: 'Lịch hôm nay', count: 2, link: '/connections#genhub', external: true, state: 'ok',
    lines: ['09:00 · Họp với nhà cung cấp ván MDF', '14:30 · Gặp anh Bảo tại showroom'],
  };
  const mail: GenBriefingSection = {
    key: 'mail_reply', title: 'Mail cần trả lời', count: 3, link: '/connections#genhub', external: true, state: 'ok',
    lines: ['Anh Bảo — Báo giá ván MDF E1 17mm', 'Công ty Hải Long — Xác nhận lịch giao hàng', 'Chị Mai — Hỏi bảo hành'],
  };
  const tasks: GenBriefingSection = {
    key: 'gtasks_open', title: 'Việc Google đang mở', count: 1, link: '/connections#genhub', external: true, state: 'ok',
    lines: ['Gọi nhà cung cấp keo dán'],
  };
  if (hub === 'ok') return { sections: [calendar, mail, tasks], hint: null };
  if (hub === 'missing') return { sections: [calendar, tasks], hint: HUB_HINT_MAIL };
  if (hub === 'breaker') {
    const detail = 'HUB_BREAKER_OPEN: 3 lỗi liên tiếp, Gen tạm dừng gọi Gen-hub 60 giây';
    return { sections: [calendar, mail, tasks].map((x): GenBriefingSection => ({ ...x, count: 0, lines: [], state: 'breaker', detail })), hint: null };
  }
  if (hub === 'error') {
    return {
      sections: [
        { ...calendar, count: 0, lines: [], state: 'error', detail: 'HUB_UNAVAILABLE: Gen-hub trả 502 khi gọi calendar_list_events' },
        mail,
        { ...tasks, count: 0, lines: [], state: 'empty' },
      ],
      hint: null,
    };
  }
  return { sections: [], hint: null };
}

/** v0.1.41 (F-8): nội dung Bản tin Gen đúng hợp đồng (bước tool `briefing.sources` đứng đầu). v0.1.49: `hub` = mục Gen-hub. */
export function briefingContent(slotLabel: string, slotIso: string, needsApiKey: boolean, hub: BriefingHubMode = 'off'): GenMessage['content'] {
  const ext = hubSections(hub);
  const sections: GenBriefingSection[] = [
    { key: 'tasks_due', title: 'Việc tới hạn hôm nay', count: 2, lines: ['TSK-0998 · Gọi lại anh Bảo (P1)', 'TSK-0999 · Gửi báo giá MDF (P2)'], link: '/tasks' },
    { key: 'hot_customers', title: 'Khách đang nóng', count: 1, lines: ['Anh Bảo — hỏi giá ván MDF E1 17mm'], link: '/inbox' },
    { key: 'drafts_pending', title: 'Nháp chờ duyệt', count: 1, lines: ['Báo giá ván MDF — chờ Sếp duyệt'], link: '/workbench' },
    { key: 'incidents', title: 'Sự cố', count: 0, lines: [], link: '/system' },
  ];
  const steps: GenStep[] = [
    { kind: 'tool', name: 'briefing.sources' },
    { kind: 'say', text: `Bản tin ${slotLabel}: 2 việc tới hạn, 1 khách đang nóng, 1 nháp chờ duyệt, không có sự cố.` },
    ...sections.filter((x) => x.count > 0).map((x): GenStep => ({ kind: 'say', text: `${x.title} (${x.count}): ${x.lines.join('; ')}` })),
  ];
  // Thẻ mục Gen-hub chèn ngay sau các mục nội bộ (như máy chủ: sau "Sự cố cần Sếp"), trước lời nhắc + nút.
  const hubAt = steps.length;
  if (ext.hint) {
    // Mục bị ẩn (thiếu quyền): một dòng nhắc + nút "Mở thẻ Gen-hub" (làm sáng thẻ Gen-hub ở Kết nối).
    steps.push({ kind: 'say', text: ext.hint });
    steps.push({ kind: 'suggest', items: [{ label: HUB_BUTTON, action: { type: 'highlight', target: 'mcp.hub_link', message: HUB_SPOT_SCOPE } }] });
  }
  if (needsApiKey) {
    steps.push({ kind: 'say', text: 'Dán khoá OpenRouter/Gemini để Gen tóm tắt' });
    steps.push({
      kind: 'suggest',
      items: [{ label: 'Mở nơi dán khoá', action: { type: 'navigate', screen: 'api' } }],
    });
  }
  const content: GenMessage['content'] = {
    kind: 'briefing', slot: slotIso, slot_label: slotLabel, summary_source: needsApiKey ? 'none' : 'model', needs_api_key: needsApiKey,
    sections: [...sections, ...ext.sections], steps,
  };
  if (hub !== 'off') content.hub_hint = ext.hint;
  if (ext.sections.length > 0) content.hub_at = hubAt;
  return content;
}

const OWNER_ID = USER_IDS.owner;

/**
 * v0.1.55 (G3): `GET /gen/settings` → `model_options` mẫu (hợp đồng `choice_options` của G1): bốn tầng dùng được, "Kỹ hơn" có đủ
 * ba mức suy nghĩ (Thấp/Vừa/Cao). Hook `modelOptions` ({deep?: false, balanced?: false}) khoá tầng để thử nút bị khoá + dòng hạ về Tự động.
 */
export function sampleModelOptions(off: { deep?: boolean; balanced?: boolean } = {}): GenModelOptions {
  return {
    tiers: [
      { tier: 'auto', available: true, efforts: [] },
      { tier: 'fast', available: true, efforts: [] },
      { tier: 'balanced', available: off.balanced !== false, efforts: [] },
      { tier: 'deep', available: off.deep !== false, efforts: off.deep === false ? [] : ['low', 'medium', 'high'] },
    ],
  };
}

const MODEL_TIERS: readonly string[] = ['auto', 'fast', 'balanced', 'deep'];
const MODEL_EFFORTS: readonly string[] = ['low', 'medium', 'high'];
/** Đúng câu máy chủ (`gh.gen.routes.MODEL_CHOICE_INVALID_TITLE`). */
export const MODEL_CHOICE_INVALID_TITLE = 'Lựa chọn model không hợp lệ — em dùng chế độ Tự động nhé';
const TIER_NAME: Record<string, string> = { fast: 'Nhanh', balanced: 'Cân bằng', deep: 'Kỹ hơn' };

/** Kiểm `model_choice` như `ModelChoiceIn` của máy chủ: tier ∈ auto|fast|balanced|deep, effort ∈ low|medium|high|null. */
export function modelChoiceValid(v: unknown): boolean {
  if (v === undefined || v === null) return true;
  if (typeof v !== 'object' || Array.isArray(v)) return false;
  const o = v as { tier?: unknown; effort?: unknown };
  if (o.tier !== undefined && !(typeof o.tier === 'string' && MODEL_TIERS.includes(o.tier))) return false;
  return o.effort === undefined || o.effort === null || (typeof o.effort === 'string' && MODEL_EFFORTS.includes(o.effort));
}

/** Thẻ gửi Facebook (v0.1.47): trả lời bình luận hoặc nhắn tin; `suspicious` → nhãn cảnh báo lừa đảo. */
export function socialWriteProposal(
  kind: 'social_reply' | 'social_dm',
  w: { account_id: string; account_label: string; gate: 'open' | 'locked' },
  suspicious: boolean,
): GenProposal {
  const reply = kind === 'social_reply';
  const target = reply ? 'Bình luận của chị Lan: "Giá bao nhiêu vậy anh?"' : 'Cuộc trò chuyện với Shop Mai';
  return {
    id: randomUUID(),
    type: kind,
    fields: {
      account_id: w.account_id,
      target_url: reply ? 'https://www.facebook.com/permalink.php?story_fbid=1&comment_id=2' : 'https://www.facebook.com/messages/t/1001/',
      text: reply ? 'Cảm ơn bạn! Bên em báo giá chi tiết qua tin nhắn ngay ạ.' : 'Dạ em cảm ơn chị Mai, em xác nhận lịch giao hàng ạ.',
    },
    summary: `${reply ? 'Trả lời bình luận' : 'Nhắn tin'} trên Facebook (${w.account_label}) — gửi ngay khi Sếp xác nhận và nhập mã PIN.`,
    labels: { account: w.account_label, target, write_gate: w.gate, ...(suspicious ? { suspicious: '1' } : {}) },
    target: `social.write:${w.account_id}`,
    requires_pin: true,
    status: 'pending',
  };
}

export function script(q: string, write?: { account_id: string; account_label: string; gate: 'open' | 'locked' } | null, extra?: GenExtra): GenStep[] {
  const t = q.toLowerCase();
  const added = extra?.script(q);
  if (added) return added;
  if (/trả lời bình luận|nhắn tin facebook/.test(t)) {
    if (!write) {
      return [
        { kind: 'say', text: 'Dạ, Sếp chưa có tài khoản Facebook nào đã đăng nhập — em mở trang Tài khoản mạng xã hội để Sếp kết nối trước.' },
        { kind: 'ui', action: { type: 'navigate', screen: 'social' } },
      ];
    }
    return [
      { kind: 'say', text: 'Dạ, em soạn sẵn nội dung — Sếp đọc kỹ, bấm Xác nhận và gửi (cần mã PIN) thì mới gửi lên Facebook nhé.' },
      { kind: 'proposal', proposal: socialWriteProposal(/nhắn tin/.test(t) ? 'social_dm' : 'social_reply', write, /đáng ngờ|lừa đảo/.test(t)) },
    ];
  }
  if (/mạng xã hội|facebook/.test(t)) {
    return [
      { kind: 'say', text: 'Dạ, em mở trang Tài khoản mạng xã hội — Sếp thêm và đăng nhập Facebook ngay trong app.' },
      { kind: 'ui', action: { type: 'navigate', screen: 'social' } },
    ];
  }
  if (/nháp/.test(t)) {
    // Như API thật (`plan_call`): đối tượng là NHÓM thì nháp có nơi gửi; là NGƯỜI thì chưa (duyệt sẽ NO_TARGET).
    const toGroup = /nhóm/.test(t);
    const proposal: GenProposal = toGroup
      ? {
          id: randomUUID(),
          type: 'draft_message',
          fields: { title: 'Báo giá ván MDF cho nhóm', text: 'Chào cả nhà, bên em gửi báo giá ván MDF E1 17mm ạ.', subject: { type: 'group', id: GROUP_TP.id } },
          summary: `Soạn nháp tin \u201cBáo giá ván MDF cho nhóm\u201d gửi nhóm ${GROUP_TP.name} — vào Bàn làm việc chờ duyệt, không gửi ngay.`,
          labels: { subject: GROUP_TP.name },
          target: 'workbench.drafts',
          requires_pin: true,
          status: 'pending',
        }
      : {
      id: randomUUID(),
      type: 'draft_message',
      fields: { title: 'Báo giá ván MDF', text: 'Chào anh Bảo, bên em gửi báo giá ván MDF E1 17mm như anh hỏi ạ.', subject: { type: 'person', id: BAO.id } },
      summary: 'Soạn nháp tin \u201cBáo giá ván MDF\u201d gửi anh Bảo — vào Bàn làm việc chờ duyệt, không gửi ngay.',
      labels: { subject: 'Anh Bảo' },
      target: 'workbench.drafts',
      requires_pin: true,
      status: 'pending',
    };
    return [
      { kind: 'say', text: 'Dạ, em soạn sẵn nháp tin — Sếp xem rồi bấm Xác nhận (cần mã PIN) nhé.' },
      { kind: 'proposal', proposal },
    ];
  }
  if (/nhắc/.test(t)) {
    const remind = new Date(Date.now() + 60 * 60 * 1000).toISOString();
    const proposal: GenProposal = {
      id: randomUUID(),
      type: 'reminder',
      fields: { title: 'Gọi lại khách', remind_at: remind, due_at: null, priority: 'P2', assignee_user_id: OWNER_ID },
      summary: 'Tạo nhắc việc \u201cGọi lại khách\u201d (P2), nhắc sau 1 giờ nữa, giao cho Sếp.',
      labels: { user: 'Sếp' },
      target: 'tasks.new',
      requires_pin: false,
      status: 'pending',
    };
    return [
      { kind: 'say', text: 'Dạ, em đề xuất tạo nhắc việc — Sếp xem, sửa nếu cần rồi bấm Xác nhận nhé.' },
      { kind: 'proposal', proposal },
    ];
  }
  if (/jev|khoá|khóa|model/.test(t)) {
    return [
      { kind: 'say', text: 'Dạ, em dẫn Sếp 3 bước để thêm nguồn Jev cho Gen — Sếp bấm "Tiếp" sau mỗi bước.' },
      {
        kind: 'ui',
        action: {
          type: 'tour',
          steps: [
            { screen: 'system', target: 'system.tab.brain', message: 'Bước 1: mở tab "Bộ não AI" — nơi quản lý các nguồn model.' },
            { target: 'system.brain.jev', message: 'Bước 2: thẻ Jev — chọn địa chỉ OpenRouter, giữ model typesafe/jev-1.13, dán khoá API.' },
            { target: 'system.brain.jev.test', message: 'Bước 3: bấm để lưu và gọi thử; báo xanh là xong.' },
          ],
        },
      },
    ];
  }
  if (/sao lưu|backup/.test(t)) {
    return [
      { kind: 'say', text: 'Sao lưu nằm ở Điều khiển hệ thống › Dữ liệu & lưu trữ. Em mở và chỉ nút cho Sếp.' },
      { kind: 'ui', action: { type: 'navigate', screen: 'system', params: { tab: 'storage' } } },
      { kind: 'ui', action: { type: 'highlight', target: 'system.backup.now', message: 'Bấm "Sao lưu ngay" để tạo bản sao lưu mã hoá ngay lúc này.' } },
    ];
  }
  if (/gấp|xử lý|hôm nay|tình hình/.test(t)) {
    return [
      { kind: 'tool', name: 'overview.summary', args: {} },
      {
        kind: 'say',
        text: 'Hôm nay có 3 việc cần Sếp xem: 1 báo giá 82 triệu vượt ngưỡng đang chờ duyệt, 2 cảnh báo khách phản hồi chậm, và 4 cơ hội mới từ nhóm Zalo.',
      },
      { kind: 'ui', action: { type: 'navigate', screen: 'overview' } },
      { kind: 'ui', action: { type: 'highlight', target: 'overview.queue', message: 'Hàng đợi cần xử lý — mục trên cùng là quan trọng nhất.' } },
      {
        kind: 'suggest',
        items: [
          { label: 'Mở hộp thư', action: { type: 'navigate', screen: 'inbox' } },
          { label: 'Chỉ tôi sao lưu', action: { type: 'tour', steps: [{ screen: 'system', target: 'system.tab.storage', message: 'Mở tab "Dữ liệu & lưu trữ".' }, { target: 'system.backup.now', message: 'Bấm "Sao lưu ngay".' }] } },
        ],
      },
    ];
  }
  return [
    { kind: 'say', text: 'Dạ, em là Gen. Sếp có thể hỏi "hôm nay có gì gấp?", hoặc nhờ em chỉ chỗ bấm, ví dụ "chỉ tôi chỗ sao lưu".' },
    { kind: 'suggest', items: [{ label: 'Mở Tổng quan', action: { type: 'navigate', screen: 'overview' } }] },
  ];
}

/**
 * Nháp tin Gen soạn sau khi Sếp xác nhận — dạng như `POST /drafts` (kind message, chờ duyệt). Như API thật: chỉ đối tượng
 * là NHÓM mới có nơi gửi (target + "Duyệt và gửi qua Zalo"); đối tượng là người → target null, "Duyệt và thực hiện".
 */
function makeGenDraft(code: string, title: string, text: string, userLabel: string, subject?: { type: 'person' | 'group'; id: string } | null): DraftDetail {
  const paragraphs = text.split(/\n\s*\n/);
  const toGroup = subject?.type === 'group';
  return {
    id: `draft-gen-${randomUUID()}`, code, kind: 'message', kind_label: 'Tin nhắn', title,
    agent: null, created_by: { id: OWNER_ID, name: userLabel }, created_at: new Date().toISOString(),
    status: 'pending', hold_reason: 'ghi ra ngoài', subject: subject?.type === 'person' ? BAO : toGroup ? GROUP_TP : null,
    paragraphs, text, lang: 'vi',
    target: toGroup ? { channel: 'zalo', thread_type: 'group', group: GROUP_TP, person: null } : null,
    amount_vnd: null, autonomy_level: 4,
    flags: { writes_external: true, personnel_related: false, over_threshold: false },
    approve_label: toGroup ? 'Duyệt và gửi qua Zalo' : 'Duyệt và thực hiện',
    sources: [{ label: 'Gen soạn theo yêu cầu của Sếp', ref: null }],
    context: [], side_actions: [], decision: null, send_result: null, versions: [],
  };
}

export function createMock(opts: MockGenOptions) {
  const stepMs = opts.stepMs ?? 350;
  const settings = { enabled: true, roles: ['owner'], retention_days: 90 };
  /** v0.1.55 (G3): `model_options` đang trả cho khung chat (hook `modelOptions` đổi được). */
  let modelOptions: GenModelOptions = sampleModelOptions();
  /** v0.1.55 (G3): thân `POST /gen/turns` gần nhất (đã qua kiểm `model_choice`) — e2e đọc qua hook `lastTurnBody`. */
  let lastTurnBody: { text: string; conversation_id: string | null; model_choice: unknown } | null = null;
  const conversations = new Map<string, Conversation>();
  const turns = new Map<string, Turn>();
  const proposals = new Map<string, GenProposal>();
  const reminders: Array<{ title: string; code: string; priority: string; fired: boolean }> = [];
  /** v0.1.41 (F-86): đánh giá theo lượt (turn_id → rating). */
  const feedback = new Map<string, GenRating>();

  /** Worker Bản tin Gen tới giờ: một hội thoại bản tin + chuông `gen.briefing` cho các Owner. */
  function makeBriefing(b: { slot?: unknown; needs_api_key?: unknown; hub?: unknown }): { conversation_id: string } {
    const hub = BRIEFING_HUB_MODES.find((x) => x === b.hub) ?? 'off';
    const afternoon = b.slot === 'chieu';
    const now = new Date();
    const ymd = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Ho_Chi_Minh' }).format(now); // YYYY-MM-DD
    const dm = `${ymd.slice(8, 10)}/${ymd.slice(5, 7)}`;
    const slotLabel = `${afternoon ? 'chiều' : 'sáng'} ${dm}`;
    const slotIso = `${ymd}T${afternoon ? '17:30' : '07:30'}:00+07:00`;
    const iso = now.toISOString();
    const conv: Conversation = { id: randomUUID(), title: `Bản tin Gen · ${slotLabel}`, created_at: iso, last_at: iso, kind: 'briefing', messages: [] };
    conv.messages.push({ id: randomUUID(), role: 'assistant', turn_id: randomUUID(), content: briefingContent(slotLabel, slotIso, b.needs_api_key !== false, hub), created_at: iso });
    conversations.set(conv.id, conv);
    opts.notifyOwners?.('gen.briefing', `Bản tin Gen ${slotLabel}`, '2 việc tới hạn · 1 khách đang nóng · 1 nháp chờ duyệt', `/overview?gen=${conv.id}`);
    return { conversation_id: conv.id };
  }
  const findTurn = (turnId: string) => {
    for (const c of conversations.values()) {
      const m = c.messages.find((x) => x.role === 'assistant' && x.turn_id === turnId);
      if (m) return { conv: c, msg: m };
    }
    return null;
  };
  let taskSeq = 998;
  const timers = new Set<ReturnType<typeof setTimeout>>();

  function run(turn: Turn, conv: Conversation, steps: GenStep[]) {
    steps.forEach((step, i) => {
      const t = setTimeout(() => {
        timers.delete(t);
        if (step.kind === 'proposal') proposals.set(step.proposal.id, step.proposal);
        const ev = { turn_id: turn.turn_id, conversation_id: turn.conversation_id, seq: i, step };
        turn.steps.push(ev);
        opts.emit('gen.step', ev);
        if (i === steps.length - 1) {
          turn.status = 'done';
          conv.messages.push({ id: randomUUID(), role: 'assistant', turn_id: turn.turn_id, content: { steps }, created_at: new Date().toISOString() });
          conv.last_at = new Date().toISOString();
          opts.emit('gen.done', { turn_id: turn.turn_id, conversation_id: turn.conversation_id, status: 'done' });
        }
      }, stepMs * (i + 1));
      timers.add(t);
    });
  }

  /** Như `engine.resolve_model_choice`: tầng đã chọn mà `model_options` báo không dùng được ⇒ hạ về Tự động + một dòng giải thích. */
  function modelChoiceNotice(choice: unknown): string | null {
    const tier = (choice as { tier?: GenModelTier } | null | undefined)?.tier;
    if (!tier || tier === 'auto') return null;
    const row = modelOptions.tiers.find((r) => r.tier === tier);
    return row?.available ? null : `Em dùng chế độ Tự động vì mức “${TIER_NAME[tier]}” chưa có nguồn AI nào phục vụ.`;
  }

  function handle(ctx: P2Ctx): boolean {
    const { method: m, path: p, body, reply, problem } = ctx;
    if (!p.startsWith('/gen/')) return false;
    const seg = p.split('/').filter(Boolean); // ['gen', ...]
    const available = settings.enabled && settings.roles.includes(ctx.role);
    if (seg[1] === 'settings') {
      if (m === 'PATCH') {
        if (ctx.role !== 'owner') return problem(403, 'FORBIDDEN', 'Chỉ Owner');
        if (typeof body.enabled === 'boolean') settings.enabled = body.enabled;
        if (typeof body.retention_days === 'number') settings.retention_days = body.retention_days;
      }
      return reply(200, { ...settings, available: settings.enabled && settings.roles.includes(ctx.role), decider: 'llm', model_options: modelOptions });
    }
    if (!available) return problem(403, 'GEN_DISABLED', 'Gen chưa bật cho vai trò này');
    if (seg[1] === '__mock' && seg[2] === 'briefing' && m === 'POST') return reply(200, makeBriefing(body));
    if (seg[1] === 'feedback' && seg.length === 2 && m === 'PUT') {
      const rating = body.rating;
      if (rating !== 'helpful' && rating !== 'not_helpful') {
        return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { rating: 'Chọn Hữu ích hoặc Không hữu ích' } });
      }
      const hit = findTurn(String(body.turn_id ?? ''));
      if (!hit || hit.conv.id !== String(body.conversation_id ?? '')) return problem(404, 'NOT_FOUND', 'Không tồn tại');
      feedback.set(String(body.turn_id), rating);
      return reply(200, { turn_id: body.turn_id, rating, kind: hit.conv.kind === 'briefing' ? 'briefing' : 'reply' });
    }
    if (seg[1] === 'feedback' && seg.length === 3 && m === 'DELETE') {
      feedback.delete(seg[2]);
      return reply(204);
    }
    if (seg[1] === 'conversations' && seg.length === 2 && m === 'GET') {
      return reply(200, [...conversations.values()].map(({ messages: _m, ...c }) => c).sort((a, b) => b.last_at.localeCompare(a.last_at)));
    }
    if (seg[1] === 'conversations' && seg[3] === 'messages' && m === 'GET') {
      const c = conversations.get(seg[2]);
      return c
        ? reply(200, c.messages.map((x) => ({ ...x, feedback: x.role === 'assistant' && x.turn_id ? (feedback.get(x.turn_id) ?? null) : null })))
        : problem(404, 'NOT_FOUND', 'Không tồn tại');
    }
    if (seg[1] === 'conversations' && seg.length === 3 && m === 'DELETE') {
      conversations.delete(seg[2]);
      return reply(204);
    }
    if (seg[1] === 'turns' && seg.length === 2 && m === 'POST') {
      const text = String(body.text ?? '').trim();
      if (!text) return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { text: 'Nhập câu hỏi' } });
      // v0.1.55 (G3): `model_choice` sai tập giá trị ⇒ 422 problem+json thân thiện (như `ModelChoiceIn` của máy chủ thật).
      if (!modelChoiceValid(body.model_choice)) {
        return problem(422, 'MODEL_CHOICE_INVALID', MODEL_CHOICE_INVALID_TITLE, {
          detail: 'Trường không hợp lệ: model_choice. tier ∈ auto | fast | balanced | deep; effort ∈ low | medium | high (hoặc bỏ trống).',
        });
      }
      lastTurnBody = { text, conversation_id: body.conversation_id ? String(body.conversation_id) : null, model_choice: body.model_choice ?? null };
      const now = new Date().toISOString();
      let conv = body.conversation_id ? conversations.get(String(body.conversation_id)) : undefined;
      if (body.conversation_id && !conv) return problem(404, 'NOT_FOUND', 'Không tồn tại');
      if (!conv) {
        conv = { id: randomUUID(), title: text.slice(0, 60), created_at: now, last_at: now, kind: 'chat', messages: [] };
        conversations.set(conv.id, conv);
      }
      const turn: Turn = { turn_id: randomUUID(), conversation_id: conv.id, status: 'running', steps: [] };
      conv.messages.push({ id: randomUUID(), role: 'user', turn_id: turn.turn_id, content: { text }, created_at: now });
      turns.set(turn.turn_id, turn);
      const scripted = script(text, opts.social?.writeContext(), opts.extra);
      const notice = modelChoiceNotice(body.model_choice);
      run(turn, conv, notice ? [{ kind: 'notice', text: notice }, ...scripted] : scripted);
      return reply(202, { turn_id: turn.turn_id, conversation_id: conv.id });
    }
    if (seg[1] === 'turns' && seg.length === 3 && m === 'GET') {
      const t = turns.get(seg[2]);
      return t ? reply(200, t) : problem(404, 'NOT_FOUND', 'Không tồn tại');
    }
    if (seg[1] === 'turns' && seg[3] === 'ack' && m === 'POST') return reply(204);
    if (seg[1] === 'assignees' && m === 'GET') {
      return reply(200, { items: [{ id: OWNER_ID, name: 'Sếp', role: 'Owner — Sếp', me: true }, { id: USER_IDS.lan, name: 'Chị Lan', role: 'Vận hành', me: false }] });
    }
    if (seg[1] === 'proposals' && m === 'POST' && (seg[3] === 'confirm' || seg[3] === 'cancel')) {
      const pr = proposals.get(seg[2]);
      if (!pr) return problem(404, 'NOT_FOUND', 'Đề xuất (có thể đã hết hạn) không tồn tại hoặc nằm ngoài phạm vi của bạn');
      if (pr.status !== 'pending') return problem(409, 'GEN_PROPOSAL_DECIDED', 'Đề xuất này đã được xác nhận hoặc đã huỷ');
      // v0.1.50: Ghi nhớ / Ghi vào Kho dữ liệu — mock-gen-v0150.ts xử lý (PIN 'hub.write' + một lời gọi ghi duy nhất).
      if (seg[3] === 'confirm' && opts.extra) {
        const r = opts.extra.confirm({ ...pr, fields: pr.fields } as GenProposal, ctx);
        if (r) {
          if ('error' in r) {
            const e = r.error;
            // Như confirm_proposal thật: 502 HUB_WRITE_UNCERTAIN gắn nhãn `uncertain` — Huỷ sau đó thẻ không nói "không ghi gì vào Kho".
            if (e.code === 'HUB_WRITE_UNCERTAIN') proposals.set(pr.id, { ...pr, labels: { ...pr.labels, uncertain: '1' } } as GenProposal);
            // Như confirm_proposal thật: chuyển nguyên `detail` (lý do Kho từ chối, lý do permit…) của lần ghi Kho.
            const extra = e.operation ? { detail: { operation: e.operation } } : { ...(e.errors ? { errors: e.errors } : {}), ...(e.detail ? { detail: e.detail } : {}) };
            return problem(e.status, e.code, e.title, Object.keys(extra).length ? extra : undefined);
          }
          proposals.set(pr.id, r.proposal);
          return reply(200, r.proposal);
        }
      }
      // Như API thật: thao tác nhạy cảm (nháp tin) → 423, web hỏi PIN rồi gửi lại.
      if (seg[3] === 'confirm' && pr.requires_pin && ctx.needPin()) {
        return problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN', { detail: { operation: pr.type === 'social_reply' || pr.type === 'social_dm' ? 'social.write' : 'draft.create' } });
      }
      if (seg[3] === 'confirm' && (pr.type === 'social_reply' || pr.type === 'social_dm')) {
        if (ctx.needPin()) return problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN', { detail: { operation: 'social.write' } });
        // Chỉ ô Nội dung sửa được; còn lại giữ nguyên như lúc đề xuất.
        const text = typeof (body.fields as { text?: unknown } | undefined)?.text === 'string' ? String((body.fields as { text: string }).text) : pr.fields.text;
        const fields = { ...pr.fields, text };
        const r = opts.social?.createWrite({
          account_id: fields.account_id, action: pr.type === 'social_reply' ? 'reply_comment' : 'send_message',
          target_url: fields.target_url, text, proposal_id: pr.id,
        });
        if (!r) return problem(503, 'SERVICE_UNAVAILABLE', 'Dịch vụ mạng xã hội chưa sẵn sàng');
        if ('error' in r) return problem(r.error.status, r.error.code, r.error.title, r.error.errors ? { errors: r.error.errors } : undefined);
        const done: GenProposal = { ...pr, fields, status: 'confirmed', result: { type: 'social_write', id: r.job.id, screen: 'social', status: r.job.status } };
        proposals.set(pr.id, done);
        return reply(200, done);
      }
      const code = pr.type === 'draft_message' ? 'ACT-0999' : `TSK-${String(++taskSeq).padStart(4, '0')}`;
      const fields = { ...pr.fields, ...((body.fields as object) ?? {}) };
      let result: NonNullable<GenProposal['result']> = { type: 'task', id: randomUUID(), code, screen: 'tasks' };
      if (seg[3] === 'confirm' && pr.type === 'draft_message') {
        // v0.1.43 (F-24): nháp THẬT ở Bàn làm việc (chờ duyệt, chưa gửi) — `result.id` mở đúng nháp qua /workbench?id=.
        const f = fields as { title: string; text: string; subject?: { type: 'person' | 'group'; id: string } | null };
        const draft = makeGenDraft(code, f.title, f.text, ctx.userLabel, f.subject);
        opts.pushDraft?.(draft);
        result = { type: 'draft', id: draft.id, code, screen: 'workbench', sendable: draft.target !== null };
      }
      const next: GenProposal =
        seg[3] === 'cancel'
          ? { ...pr, status: 'cancelled' }
          : ({ ...pr, fields, status: 'confirmed', result } as GenProposal);
      proposals.set(pr.id, next);
      if (next.status === 'confirmed' && next.type === 'reminder') {
        reminders.push({ title: next.fields.title, code, priority: next.fields.priority ?? 'P2', fired: false });
      }
      return reply(200, next);
    }
    return problem(404, 'NOT_FOUND', 'Không tồn tại');
  }

  return {
    handle,
    hooks: {
      settings: () => settings,
      script: (b: unknown) => script(String((b as { text?: string })?.text ?? '')),
      /** v0.1.55 (G3): thân `POST /gen/turns` gần nhất (`{text, conversation_id, model_choice}`; null nếu chưa có) — e2e/dev đọc. */
      lastTurnBody: () => lastTurnBody,
      /** v0.1.55 (G3): đổi `model_options` của `/gen/settings`: `{deep?: false, balanced?: false}` khoá tầng; `{}` trả về mẫu đầy đủ. */
      modelOptions: (b: unknown) => {
        const o = (b ?? {}) as { deep?: unknown; balanced?: unknown };
        modelOptions = sampleModelOptions({ deep: o.deep === false ? false : undefined, balanced: o.balanced === false ? false : undefined });
        return modelOptions;
      },
      /** v0.1.41 (F-8): worker Bản tin Gen tới giờ (như `POST /gen/__mock/briefing`). */
      briefing: (b: unknown) => makeBriefing((b ?? {}) as { slot?: unknown; needs_api_key?: unknown; hub?: unknown }),
      /**
       * v0.1.50 (F-87): job `gen_kho_release` — máy chủ vừa lên bản `version`: một hội thoại có thẻ đề xuất ghi Phiên (kho_create,
       * nhãn `release`) + chuông `gen.kho_proposal` (link `/overview?gen={cid}`) cho Owner. Job KHÔNG ghi Kho — chỉ khi Sếp Xác nhận
       * + nhập mã PIN (mock-gen-v0150 `confirm`). `closed` ⇒ thẻ đã bị đóng vì Owner khác đã ghi (như `_close_release_cards`).
       */
      khoRelease: (b: unknown) => {
        const body = (b ?? {}) as { version?: unknown; closed?: unknown };
        const version = typeof body.version === 'string' && /^v\d+\.\d+\.\d+$/.test(body.version) ? body.version : 'v0.1.50';
        const iso = new Date().toISOString();
        const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Ho_Chi_Minh' }).format(new Date());
        const proposal: GenProposal = {
          id: randomUUID(), type: 'kho_create',
          fields: {
            bang: 'Phiên',
            record: {
              'Chủ đề': `Gen-Harness lên bản ${version}`, Ngày: today,
              'Đã chốt': `Máy chủ Gen-Harness đã nâng lên ${version}. Ghi chú phát hành: https://github.com/genesis/gen-harness/releases/tag/${version}`,
            },
          },
          summary: 'Tạo bản ghi mới ở bảng Phiên của Kho dữ liệu — ghi thẳng qua Gen-hub khi Sếp xác nhận và nhập mã PIN.',
          labels: { bang: 'Phiên', target: 'Tạo mới ở bảng Phiên', write_scope: 'ok', release: version, ...(body.closed ? { closed: 'Owner khác đã ghi bản này vào Kho (PHIEN-12)' } : {}) },
          target: 'hub.kho_write:Phiên', requires_pin: true, status: body.closed ? 'cancelled' : 'pending',
        };
        proposals.set(proposal.id, proposal);
        const conv: Conversation = { id: randomUUID(), title: `Ghi Phiên ${version} vào Kho`, created_at: iso, last_at: iso, kind: 'chat', messages: [] };
        const say = `Máy chủ Gen-Harness vừa lên ${version}. Em đề xuất ghi một Phiên vào Kho dữ liệu để lưu mốc này — Sếp xem lại, sửa nếu cần rồi bấm Xác nhận và nhập mã PIN thì em mới ghi (qua Gen-hub).`;
        conv.messages.push({ id: randomUUID(), role: 'assistant', turn_id: randomUUID(), content: { steps: [{ kind: 'say', text: say }, { kind: 'proposal', proposal }] }, created_at: iso });
        conversations.set(conv.id, conv);
        opts.notifyOwners?.('gen.kho_proposal', `Gen đề xuất ghi Kho · Phiên ${version}`, `Gen-Harness đã lên ${version}. Xem thẻ đề xuất, Xác nhận và nhập mã PIN để ghi Phiên vào Kho dữ liệu.`, `/overview?gen=${conv.id}`);
        return { conversation_id: conv.id, proposal_id: proposal.id };
      },
      /** Worker nhắc việc tới giờ: chuông cho Owner, mỗi nhắc một lần. */
      fireReminders: () => {
        let n = 0;
        for (const r of reminders) {
          if (r.fired) continue;
          r.fired = true;
          n += 1;
          opts.notifyOwners?.('task.reminder', `Nhắc việc: ${r.title}`, `${r.code} · ${r.priority}`, '/tasks');
        }
        return { fired: n };
      },
    },
    dispose: () => {
      for (const t of timers) clearTimeout(t);
      timers.clear();
    },
  };
}
