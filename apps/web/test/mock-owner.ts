/**
 * Mock v0.1.55 (G5) + v0.1.59 (G1) — Mặt tiền Owner (`/api/v1/owner/*`, CHỈ ĐỌC), theo HỢP ĐỒNG của `apps/api/gh/owner/routes.py`:
 *
 * - `GET /owner/today` → {needs_review (≤ 10), kpis, briefing_latest, filter_value, suggestions, progress, nav}.
 * - `GET /owner/brief?id=` → {latest, live, alerts, history, next_at, ai_ready, channels_connected}.
 * - `GET /owner/feed?view=&offset=&limit=` → {items, next_offset, counts, filtered_7d, channels_connected}; view lạ ⇒ 422.
 * - `GET /owner/feed/{id}/context` → {center_event_id, messages ≤ 10}; id lạ ⇒ 404; chỉ tin Khẩn đầu tiên có hội thoại 10 tin.
 * - `GET /owner/relations?list=hot|cooling|bridges|matches|care&limit=20` → {list, items:[{id,name,subtitle,metric_text,to,…}]};
 *   `list` lạ ⇒ 422 VALIDATION; `limit` bị chặn trong [1, 50] (không lỗi).
 * - `GET /owner/insights?range=week|month&only=` → 9 chỉ số + breakdown; `GET /owner/proposals` → {items}.
 * - `GET /owner/tasks` → {groups:[{key: inbox|desk|tasks, title, count, to, items ≤ 5}]}.
 * - CHỈ Owner: vai trò khác 403 FORBIDDEN. Không có POST/PUT/PATCH/DELETE trên `/owner/*` (405 NOT_ALLOWED).
 *
 * Dữ liệu hư cấu (tên giả, KHÔNG địa chỉ Gen-hub / Kho hay danh tính thật). Dùng đúng mã người của `mock-p3-relations` (p-hau,
 * p-bao…) và mã gợi ý ghép của `mock-p3-market` (match-1…) nên bấm một dòng mở được Hồ sơ sống / Giới thiệu được.
 *
 * NHÁP CHỜ DUYỆT lấy từ trạng thái nháp của mock core (`opts.drafts()`): duyệt bằng `POST /drafts/{id}/approve` (mã PIN 246810)
 * thì nháp biến khỏi bản tin, dòng tin và số đếm `nav`. Để các luồng "tại chỗ" chạy trọn trên mock, mock này còn trả lời
 * (chỉ cho mã của chính nó): `GET /inbox/{fd-…}`, `POST /inbox/{fd-…}/act|silence` (Trả lời ⇒ tạo NHÁP thật ở mock core) và
 * `POST /gen/proposals/{prop-…}/confirm|cancel` (kho_* cần mã PIN, 404 sau khi đã xử lý). Phải đứng TRƯỚC `gen`/`core` trong `phase3`.
 *
 * Chế độ dữ liệu `MOCK_OWNER_DATA=empty|collecting|nochannel` (mặc định `full`) — `vite.config.ts` chỉ đọc biến này rồi truyền
 * `createMockApi({ ownerData })`:
 *   full — đủ dữ liệu, có 1 kênh nhận tin · empty — chưa có tin nào (insights.state='empty'), vẫn có kênh ·
 *   collecting — insights.state='collecting', days_until_compare=5 · nochannel — channels_connected=0 ở brief/feed/insights.
 *
 * Hook e2e `POST /api/v1/__mock/p3/owner/{hook}`:
 *   data {mode: 'full'|'empty'|'collecting'|'nochannel'} — đổi chế độ dữ liệu lúc chạy (gọi bằng `p3Hook(request, 'owner', 'data', { mode })`);
 *   scenario {mode: 'data'|'empty'|'error', only?: 'today'|'brief'|'feed'|'relations'|'insights'|'proposals'|'tasks'} — kịch bản dữ liệu /
 *                                  rỗng / lỗi 500 (mặc định áp cho mọi đường; `only` chỉ áp cho một đường, còn lại về 'data');
 *   state {} — đọc số lần gọi + lời gọi bị 403 của vai trò khác Owner (kiểm tra); reset {} — về dữ liệu mẫu.
 *
 * Nối vào `phase3` của `test/mock-api.ts`, TRƯỚC `gen`:
 *   `owner: createOwner({ fresh, emit, boss, ownerData, drafts, pushDraft, matches }),`.
 */
import type {
  BriefSection,
  FeedContext,
  FeedImportance,
  FeedItem,
  FeedView,
  InsightKey,
  InsightMetric,
  InsightRange,
  OwnerBrief,
  OwnerFeed,
  OwnerInsights,
  OwnerProposal,
  OwnerRelationList,
  OwnerRelationRow,
  OwnerRelations,
  OwnerTasks,
  OwnerToday,
} from '../../../packages/contracts/src/owner';
import type { BossOverview } from '../../../packages/contracts/src/bossChecks';
import type { DraftDetail, PersonRef } from '../../../packages/contracts/src/p3-core';
import type { Match } from '../../../packages/contracts/src/p3-market';
import { USER_IDS } from './mock-ids';
import type { P2Ctx } from './mock-phase2';

export type OwnerDataMode = 'full' | 'empty' | 'collecting' | 'nochannel';
export const OWNER_DATA_MODES: readonly OwnerDataMode[] = ['full', 'empty', 'collecting', 'nochannel'];

export function parseOwnerDataMode(v: unknown): OwnerDataMode {
  return OWNER_DATA_MODES.includes(v as OwnerDataMode) ? (v as OwnerDataMode) : 'full';
}

interface Opts {
  fresh: boolean;
  emit: (type: string, data: unknown) => void;
  /** Bảng "Việc Sếp cần làm" hiện tại — `progress` lấy từ đây (không ghi cứng); thiếu thì dùng 2/6. */
  boss?: () => BossOverview;
  /** Chế độ dữ liệu ban đầu (`MOCK_OWNER_DATA`); mặc định 'full'. */
  ownerData?: OwnerDataMode;
  /** Nháp hiện có của mock core — nháp `pending` là nguồn của "Việc phải quyết" (duyệt xong thì biến mất). */
  drafts?: () => DraftDetail[];
  /** Tạo nháp thật ở mock core (Trả lời tin ⇒ nháp chờ duyệt). */
  pushDraft?: (d: DraftDetail) => unknown;
  /** Gợi ý ghép Cung ↔ Cầu của mock market (mã `match-1`…): hàng "Cung–cầu" lấy từ đây. */
  matches?: () => Match[];
}

type Mode = 'data' | 'empty' | 'error';
type Endpoint = 'today' | 'brief' | 'feed' | 'relations' | 'insights' | 'proposals' | 'tasks';
const ENDPOINTS: readonly Endpoint[] = ['today', 'brief', 'feed', 'relations', 'insights', 'proposals', 'tasks'];

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;
const ago = (ms: number) => new Date(Date.now() - ms).toISOString();
const inFuture = (ms: number) => new Date(Date.now() + ms).toISOString();

// ── người dùng chung với mock-p3-relations (mở được Hồ sơ sống) ──────────────────────────────────────────────────────

const row = (
  id: string,
  name: string,
  subtitle: string,
  metric: string,
  to = `/profile?id=${id}`,
  extra: Partial<OwnerRelationRow> = {},
): OwnerRelationRow => ({ id, name, subtitle, metric_text: metric, to, ...extra });

const MATCH_FALLBACK = [
  { id: 'match-1', demand: 'Ván MDF E1 17mm', supply: 'Ván MDF E1 17mm', who: 'Bùi Đức Thắng cần · Lâm Văn Được có', score: 94, person: 'p-thang' },
  { id: 'match-2', demand: 'Kho lạnh 200 pallet', supply: 'Kho lạnh Tân Cảng', who: 'Lê Thị Bích cần · Hoàng Thị Lan có', score: 81, person: 'p-bich' },
];

function relationItems(list: OwnerRelationList, matches: Match[] | null): OwnerRelationRow[] {
  switch (list) {
    case 'hot':
      return [
        row('p-hau', 'Trần Văn Hậu', 'Khách hàng · Xưởng gỗ Bình Dương', 'Độ nóng 91 · đang tăng', undefined, { person_id: 'p-hau', heat: 91, last_contact_days: 1, open_value_vnd: 1_200_000_000 }),
        row('p-bao', 'Nguyễn Văn Bảo', 'Khách hàng · Công ty in Thành Phát', 'Độ nóng 87', undefined, { person_id: 'p-bao', heat: 87, last_contact_days: 0, open_value_vnd: 84_000_000 }),
        row('p-duoc', 'Lâm Văn Được', 'Nhà cung cấp · Kho ván Bình Dương', 'Độ nóng 84', undefined, { person_id: 'p-duoc', heat: 84, last_contact_days: 2 }),
      ];
    case 'cooling':
      return [
        row('rel-1', 'Hoàng Thị Lan và Phạm Quốc Minh', 'Hay trao đổi về MDF E1', '42 ngày chưa liên lạc', '/profile?id=p-lan', { person_id: 'p-lan', last_contact_days: 42 }),
        row('rel-2', 'Đặng Hữu Trí và Trịnh Mỹ Duyên', 'Hay trao đổi cùng nhóm', '35 ngày chưa liên lạc', '/profile?id=p-tri', { person_id: 'p-tri', last_contact_days: 35 }),
      ];
    case 'bridges':
      return [
        row('p-khoa', 'Trần Minh Khoa', 'Có mặt ở 3 nhóm · Nhân viên', 'Nối 5 cặp nhóm', undefined, { person_id: 'p-khoa' }),
        row('p-thang', 'Bùi Đức Thắng', 'Có mặt ở 2 nhóm · Khách hàng · Gỗ Trường Thành Mới', 'Nối 2 cặp nhóm', undefined, { person_id: 'p-thang' }),
      ];
    case 'care':
      return [
        // "Chưa phân loại" = người mới từ Zalo còn ở loại `unknown` — vẫn là khách cần chăm.
        row('p-thang', 'Bùi Đức Thắng', 'Chưa phân loại', 'Có thể sắp rời · 6 ngày chưa liên lạc', undefined, { person_id: 'p-thang', reason: 'churn', churn_risk: 82, heat: 72, last_contact_days: 6, open_value_vnd: 880_000_000 }),
        row('p-duyen', 'Trịnh Mỹ Duyên', 'Khách hàng · Bao bì Sài Gòn Mới', 'Có thể sắp rời · 21 ngày chưa liên lạc', undefined, { person_id: 'p-duyen', reason: 'churn', churn_risk: 74, heat: 18, last_contact_days: 21, open_value_vnd: 510_000_000 }),
        row('p-tri', 'Đặng Hữu Trí', 'Khách hàng · Gỗ Đông Phương', '33 ngày chưa liên lạc', undefined, { person_id: 'p-tri', reason: 'silent', churn_risk: 38, heat: 22, last_contact_days: 33, open_value_vnd: 540_000_000 }),
        row('p-bich', 'Lê Thị Bích', 'Khách hàng · Kho lạnh Tân Cảng', '16 ngày chưa liên lạc', undefined, { person_id: 'p-bich', reason: 'silent', heat: 58, last_contact_days: 16 }),
      ];
    default: {
      const live = (matches ?? []).filter((m) => m.status === 'suggested' || m.status === 'introduced').slice(0, 3);
      if (live.length > 0) {
        return live.map((m) =>
          row(m.id, `${m.demand.item} ↔ ${m.supply.item}`, `${m.demand.person?.name ?? m.demand.group?.name ?? 'Bên cần'} cần · ${m.supply.person?.name ?? m.supply.group?.name ?? 'Bên có'} có`, `Khớp ${Math.round(m.score)}%`, m.demand.person ? `/profile?id=${m.demand.person.id}` : '/supply', {
            match_id: m.id,
            ...(m.demand.person ? { person_id: m.demand.person.id } : {}),
          }),
        );
      }
      return MATCH_FALLBACK.map((m) => row(m.id, `${m.demand} ↔ ${m.supply}`, m.who, `Khớp ${m.score}%`, `/profile?id=${m.person}`, { match_id: m.id, person_id: m.person }));
    }
  }
}

const REQUIRED_TOTAL_DEFAULT = 6;

// ── dòng tin (Thông tin) ─────────────────────────────────────────────────────────────────────────────────────────────

const GROUP_TP = { id: 'g-thanhphat', name: 'Nhóm Thành Phát', channel: 'zalo' };
const GROUP_GO = { id: 'g-nganhgo', name: 'Group Ngành gỗ Miền Nam', channel: 'zalo' };
const rank = (i: FeedImportance) => (i === 'Khẩn' ? 0 : i === 'Quan trọng' ? 1 : 2);
const OPPORTUNITY = new Set(['AskedPrice', 'RequestedSample', 'SentQuotation', 'RequestedPartnership', 'OfferedSupply', 'DealWon']);

function feedSeed(): FeedItem[] {
  const unit = (id: string, event_type: string, title: string, importance: FeedImportance, summary: string, mins: number, person: FeedItem['person'], group: FeedItem['group']): FeedItem => ({
    id, item_type: 'unit', title, summary, importance, event_type, created_at: ago(mins * MIN), person, group, suggested_action: null,
  });
  return [
    unit('fd-1', 'Complained', 'Than phiền', 'Khẩn', 'Giao ván chậm hai ngày, khách đòi hoàn lại tiền cọc nếu hôm nay chưa giao.', 25, { id: 'p-bao', name: 'Nguyễn Văn Bảo' }, GROUP_TP),
    {
      id: 'fd-2', item_type: 'alert', title: 'Khách có thể sắp rời', summary: 'Trần Văn Hậu đã 20 ngày chưa nhắn tin, trong khi còn cơ hội 1,2 tỷ ₫ đang mở.', importance: 'Khẩn',
      event_type: null, created_at: ago(2 * HOUR), person: { id: 'p-hau', name: 'Trần Văn Hậu' }, group: null, suggested_action: 'Gọi hỏi thăm trong hôm nay',
    },
    unit('fd-3', 'AskedPrice', 'Hỏi giá', 'Quan trọng', 'Hỏi giá 3 container ván MDF E1 17mm, cần giao trong tháng 10.', 48, { id: 'p-hau', name: 'Trần Văn Hậu' }, GROUP_GO),
    unit('fd-4', 'OfferedSupply', 'Chào bán', 'Quan trọng', 'Chào 6 container ván E1 tồn kho tại Bình Dương, giá sỉ theo container.', 95, { id: 'p-duoc', name: 'Lâm Văn Được' }, GROUP_GO),
    unit('fd-5', 'PromisedDelivery', 'Cam kết giao hàng', 'Quan trọng', 'Hẹn giao mẫu ván melamine 12mm trước thứ Sáu.', 3 * 60, { id: 'p-lan', name: 'Hoàng Thị Lan' }, null),
    {
      id: 'fd-6', item_type: 'alert', title: 'Phản hồi chậm bất thường', summary: 'Tin của khách Lê Thị Bích chưa có ai trả lời sau 6 giờ.', importance: 'Quan trọng',
      event_type: null, created_at: ago(5 * HOUR), person: { id: 'p-bich', name: 'Lê Thị Bích' }, group: null, suggested_action: 'Nhờ Gen soạn tin trả lời',
    },
    unit('fd-7', 'Greeting', 'Tin khác', 'Thường', 'Chào buổi sáng anh, em hỏi thăm sức khoẻ anh và gia đình.', 70, { id: 'p-minh', name: 'Phạm Quốc Minh' }, null),
    unit('fd-8', 'ScheduledMeeting', 'Hẹn gặp', 'Thường', 'Hẹn gặp trực tiếp tại xưởng vào chiều thứ Tư.', 4 * 60, { id: 'p-thang', name: 'Bùi Đức Thắng' }, null),
    unit('fd-9', 'Greeting', 'Tin khác', 'Thường', 'Cảm ơn anh đã hỗ trợ đợt hàng vừa rồi.', 26 * 60, { id: 'p-tri', name: 'Đặng Hữu Trí' }, null),
  ];
}

/** Hội thoại 10 tin quanh tin Khẩn đầu tiên (fd-1): 5 trước + tin chính + 4 sau. */
function conversationFor(centerId: string): FeedContext {
  const rows: Array<[string, 'inbound' | 'outbound', string, string, number]> = [
    ['ev-1', 'inbound', 'Nguyễn Văn Bảo', 'Chào em, đơn ván hôm thứ Hai của anh đâu rồi?', 190],
    ['ev-2', 'outbound', 'Bên mình', 'Dạ em kiểm tra với kho rồi báo anh ngay ạ.', 185],
    ['ev-3', 'inbound', 'Nguyễn Văn Bảo', 'Hẹn thứ Tư mà giờ thứ Sáu vẫn chưa thấy xe.', 170],
    ['ev-4', 'outbound', 'Bên mình', 'Dạ xe bị chậm do mưa lớn, em xin lỗi anh.', 165],
    ['ev-5', 'inbound', 'Nguyễn Văn Bảo', 'Mưa thì phải báo trước chứ, xưởng anh đang chờ hàng.', 120],
    ['ev-6', 'inbound', 'Nguyễn Văn Bảo', 'Giao ván chậm hai ngày, anh đòi hoàn lại tiền cọc nếu hôm nay chưa giao.', 25],
    ['ev-7', 'outbound', 'Bên mình', 'Dạ em đã báo anh quản lý kho, sẽ có xe lúc 15h chiều nay ạ.', 20],
    ['ev-8', 'inbound', 'Nguyễn Văn Bảo', 'Nếu 15h chưa tới thì anh huỷ đơn nhé.', 15],
    ['ev-9', 'outbound', 'Bên mình', 'Dạ em xin ghi nhận và sẽ cập nhật anh trước 14h30.', 10],
    ['ev-10', 'inbound', 'Nguyễn Văn Bảo', 'Ok, anh chờ tin em.', 5],
  ];
  void centerId;
  return {
    center_event_id: 'ev-6',
    messages: rows.map(([id, direction, sender, text, mins]) => ({ id, at: ago(mins * MIN), direction, sender, text, is_center: id === 'ev-6' })),
  };
}

function nextBriefAt(now = new Date()): string {
  // 07:30 / 17:30 giờ VN (UTC+7) kế tiếp.
  const vn = new Date(now.getTime() + 7 * HOUR);
  const base = Date.UTC(vn.getUTCFullYear(), vn.getUTCMonth(), vn.getUTCDate());
  for (let d = 0; d < 2; d += 1) {
    for (const [h, m] of [[7, 30], [17, 30]]) {
      const at = base + d * DAY + (h * 60 + m) * MIN - 7 * HOUR;
      if (at > now.getTime()) return new Date(at).toISOString();
    }
  }
  return new Date(now.getTime() + DAY).toISOString();
}

// ── phân tích ───────────────────────────────────────────────────────────────────────────────────────────────────────

const INSIGHT_DEFS: Array<{ key: InsightKey; label: string; unit: InsightMetric['unit']; good: 'up' | 'down'; base: number; gain: number }> = [
  { key: 'messages_in', label: 'Tin nhận', unit: 'count', good: 'up', base: 42, gain: 1.18 },
  { key: 'signals', label: 'Tín hiệu đáng chú ý', unit: 'count', good: 'up', base: 9, gain: 1.1 },
  { key: 'new_contacts', label: 'Người mới liên hệ', unit: 'count', good: 'up', base: 3, gain: 1.4 },
  { key: 'opps_new', label: 'Cơ hội mới', unit: 'count', good: 'up', base: 2, gain: 1.25 },
  { key: 'opps_won', label: 'Cơ hội thắng', unit: 'count', good: 'up', base: 1, gain: 1.5 },
  { key: 'reply_fast_pct', label: 'Trả lời nhanh', unit: 'pct', good: 'up', base: 78, gain: 1.06 },
  { key: 'unanswered', label: 'Tin khách chờ trả lời', unit: 'count', good: 'down', base: 3, gain: 0.8 },
  { key: 'ai_cost_vnd', label: 'Chi phí trợ lý Gen', unit: 'vnd', good: 'down', base: 4_200, gain: 1.12 },
  { key: 'filtered', label: 'Gen lọc giúp', unit: 'count', good: 'up', base: 0, gain: 1 },
];

/** Ngày giờ VN `YYYY-MM-DD` cách hôm nay `back` ngày. */
const vnDay = (back: number) => new Date(Date.now() + 7 * HOUR - back * DAY).toISOString().slice(0, 10);

function insightMetric(def: (typeof INSIGHT_DEFS)[number], n: number, mode: OwnerDataMode): InsightMetric {
  const empty = mode === 'empty' || mode === 'nochannel';
  const wave = (i: number, scale: number) => Math.max(0, Math.round(def.base * scale * (1 + 0.35 * Math.sin(i * 0.9 + def.base))));
  const cur = Array.from({ length: n }, (_, i) => ({ d: vnDay(n - 1 - i), v: empty ? 0 : wave(i, 1) }));
  const prev = Array.from({ length: n }, (_, i) => ({ d: vnDay(2 * n - 1 - i), v: empty || mode === 'collecting' ? 0 : wave(i + 3, 1 / def.gain) }));
  const sum = (xs: Array<{ v: number }>) => xs.reduce((s, p) => s + p.v, 0);
  const pct = def.unit === 'pct';
  let total = pct ? (empty ? 0 : Math.round(sum(cur) / n)) : sum(cur);
  let prevTotal = pct ? (empty || mode === 'collecting' ? 0 : Math.round(sum(prev) / n)) : sum(prev);
  let series = cur;
  let prevSeries = prev;
  if (def.key === 'filtered') {
    total = empty ? 0 : n === 7 ? 124 : 480;
    prevTotal = empty || mode === 'collecting' ? 0 : n === 7 ? 98 : 410;
    series = [];
    prevSeries = [];
  }
  const out: InsightMetric = {
    key: def.key, label: def.label, unit: def.unit, total, prev_total: prevTotal,
    delta_pct: prevTotal === 0 ? null : Math.round(((total - prevTotal) / prevTotal) * 1000) / 10,
    good: def.good, series, prev_series: prevSeries,
  };
  if (def.key === 'opps_won') out.value_vnd = empty ? 0 : n === 7 ? 1_200_000_000 : 3_450_000_000;
  return out;
}

function insightsBody(range: InsightRange, only: InsightKey[] | null, mode: OwnerDataMode): OwnerInsights {
  const n = range === 'week' ? 7 : 30;
  const empty = mode === 'empty' || mode === 'nochannel';
  const wantsCost = !only || only.includes('ai_cost_vnd');
  return {
    range, from: vnDay(n - 1), to: vnDay(0), prev_from: vnDay(2 * n - 1), prev_to: vnDay(n), generated_at: new Date().toISOString(),
    state: empty ? 'empty' : mode === 'collecting' ? 'collecting' : 'ok',
    days_until_compare: mode === 'collecting' ? 5 : null,
    channels_connected: mode === 'nochannel' ? 0 : 1,
    metrics: INSIGHT_DEFS.filter((d) => !only || only.includes(d.key)).map((d) => insightMetric(d, n, mode)),
    breakdown: only
      ? null
      : {
          event_types: empty ? [] : [
            { event_type: 'AskedPrice', label: 'Hỏi giá', units: n === 7 ? 41 : 168 },
            { event_type: 'OfferedSupply', label: 'Chào bán', units: n === 7 ? 22 : 90 },
            { event_type: 'Complained', label: 'Than phiền', units: n === 7 ? 9 : 31 },
            { event_type: 'RequestedPartnership', label: 'Đề nghị hợp tác', units: n === 7 ? 7 : 24 },
            { event_type: 'ScheduledMeeting', label: 'Hẹn gặp', units: n === 7 ? 5 : 19 },
          ],
          funnel: empty ? [] : [
            { stage: 'raw_signal', label: 'Tín hiệu mới', n: 14, value_vnd: 0 },
            { stage: 'validated', label: 'Đã xác thực', n: 9, value_vnd: 640_000_000 },
            { stage: 'matched', label: 'Đã ghép cung cầu', n: 6, value_vnd: 920_000_000 },
            { stage: 'negotiating', label: 'Đang đàm phán', n: 4, value_vnd: 1_480_000_000 },
            { stage: 'won', label: 'Thắng', n: n === 7 ? 2 : 6, value_vnd: n === 7 ? 1_200_000_000 : 3_450_000_000 },
          ],
        },
    // Có lượt chưa đặt giá ⇒ web phải ghi "12 lượt chưa đặt giá", không hiện "0 ₫".
    ai_cost: wantsCost && !empty ? { calls: n === 7 ? 142 : 610, unpriced_calls: n === 7 ? 12 : 41, budget_vnd: 50_000 } : { calls: 0, unpriced_calls: 0, budget_vnd: null },
    feedback: range === 'week' && wantsCost ? (empty ? { helpful: 0, not_helpful: 0 } : { helpful: 9, not_helpful: 2 }) : null,
  };
}

// ── máy chủ giả ─────────────────────────────────────────────────────────────────────────────────────────────────────

export function createMock(opts: Opts) {
  const initialData = parseOwnerDataMode(opts.ownerData);
  const state = {
    mode: Object.fromEntries(ENDPOINTS.map((e) => [e, 'data'])) as Record<Endpoint, Mode>,
    calls: Object.fromEntries(ENDPOINTS.map((e) => [e, 0])) as Record<Endpoint, number>,
    nonOwnerCalls: [] as string[],
    data: initialData,
  };
  let feed = feedSeed();
  const silenced = new Set<string>();
  const acked = new Set<string>();
  let props = proposalSeed();
  let seq = 0;

  function proposalSeed(): OwnerProposal[] {
    return [
      {
        id: 'prop-1', type: 'reminder', type_label: 'Tạo nhắc việc', summary: 'Nhắc Sếp gọi lại chị Hoàng Thị Lan về hợp đồng vào 9h sáng mai.',
        needs_pin: false, created_at: ago(40 * MIN), expires_at: inFuture(23 * HOUR), conversation_id: 'gc-owner-1',
      },
      {
        id: 'prop-2', type: 'kho_create', type_label: 'Ghi vào Kho dữ liệu (tạo mới)', summary: 'Ghi phiên làm việc hôm nay vào Kho dữ liệu của Sếp. Chỉ ghi khi Sếp bấm Xác nhận và nhập mã PIN.',
        needs_pin: true, created_at: ago(2 * HOUR), expires_at: inFuture(22 * HOUR), conversation_id: 'gc-owner-2',
      },
    ];
  }

  const progress = () => {
    const o = opts.boss?.();
    return o ? { required_done: o.required_done, required_total: o.required_total } : { required_done: 2, required_total: REQUIRED_TOTAL_DEFAULT };
  };

  /** Chưa có tin nào để hiện: kịch bản "rỗng" của đường đó, hoặc chế độ dữ liệu empty / nochannel (không kênh ⇒ không có tin). */
  const noData = (ep: Endpoint) => state.mode[ep] === 'empty' || state.data === 'empty' || state.data === 'nochannel';
  const channels = () => (state.data === 'nochannel' ? 0 : 1);

  /** Nháp chờ duyệt thật của mock core. Chế độ `empty` = chưa có gì ⇒ coi như không có nháp. */
  function pendingDrafts(ep: Endpoint): DraftDetail[] {
    if (state.data === 'empty' || state.mode[ep] === 'empty') return [];
    return (opts.drafts?.() ?? []).filter((d) => d.status === 'pending');
  }

  function liveProposals(ep: Endpoint): OwnerProposal[] {
    return state.data === 'empty' || state.mode[ep] === 'empty' ? [] : props;
  }

  function feedItems(ep: Endpoint): FeedItem[] {
    const units = noData(ep) ? [] : feed.filter((f) => !silenced.has(f.id) && !acked.has(f.id));
    const drafts: FeedItem[] = pendingDrafts(ep).map((d) => ({
      id: d.id, item_type: 'draft', title: d.title, summary: d.text.slice(0, 240), importance: 'Quan trọng', event_type: null,
      created_at: d.created_at, person: null, group: d.target?.group ? { id: d.target.group.id, name: d.target.group.name, channel: d.target.group.channel } : null,
      suggested_action: null,
    }));
    return [...units, ...drafts].sort((a, b) => rank(a.importance) - rank(b.importance) || Date.parse(b.created_at ?? '') - Date.parse(a.created_at ?? ''));
  }

  const counts = (items: FeedItem[]) => ({
    important: items.filter((i) => i.importance !== 'Thường').length,
    all: items.length,
    opportunity: items.filter((i) => i.item_type === 'unit' && i.event_type && OPPORTUNITY.has(i.event_type)).length,
    alert: items.filter((i) => i.item_type === 'alert').length,
    approval: items.filter((i) => i.item_type === 'draft').length,
    urgent_open: items.filter((i) => i.importance === 'Khẩn').length,
  });

  function today(ep: Endpoint): OwnerToday {
    if (state.mode[ep] === 'empty') {
      return {
        needs_review: [],
        kpis: { hot: 0, cooling: 0, open_opps: 0, open_value_vnd: 0, overdue_promises: 0 },
        briefing_latest: null,
        filter_value: { filtered: 0, spam_blocked: 0, calls_saved: 0, jev_on: false },
        suggestions: [],
        progress: progress(),
        nav: { urgent_open: 0, decide: 0 },
      };
    }
    const items = feedItems('feed');
    return {
      needs_review: [
        { kind: 'proposal', title: 'Gen đề xuất: Soạn nháp tin gửi đi (cần mã PIN)', to: '/owner/gen', at: ago(12 * MIN) },
        { kind: 'draft', title: 'Trả lời anh Hậu về giá ván MDF', to: '/workbench?id=draft-1', at: ago(35 * MIN) },
        { kind: 'draft', title: 'Báo giá chờ duyệt', to: '/workbench?id=draft-2', at: ago(3 * 60 * MIN) },
        { kind: 'overdue_task', title: 'Gọi lại chị Lan về hợp đồng', to: '/tasks?overdue=true', at: ago(2 * 24 * 60 * MIN) },
      ],
      kpis: { hot: 3, cooling: 2, open_opps: 12, open_value_vnd: 2_400_000_000, overdue_promises: 1 },
      briefing_latest: {
        title: 'Bản tin Gen · sáng 10/10',
        at: ago(3 * 60 * MIN),
        summary_text: 'Sáng nay có 3 bản nháp chờ Sếp duyệt và 2 mối quan hệ đang nguội dần.',
        to: '/owner/gen',
      },
      filter_value: { filtered: 128, spam_blocked: 31, calls_saved: 94, jev_on: true },
      suggestions: [
        {
          key: 'apply_standard',
          title: 'Dùng cấu hình chuẩn cho Gen',
          body: 'Em đề xuất áp cấu hình chuẩn theo vai để Gen chạy ổn định hơn — Sếp xem lại rồi bấm áp dụng.',
          to: '/system?tab=brain#chuan',
        },
        {
          key: 'background_key_missing',
          title: 'Cần 1 khoá API để chạy lọc tin và bản tin',
          body: 'Lọc tin và bản tin cần một khoá riêng để chạy ngầm. Sếp thêm khoá ở Bộ não AI nhé.',
          to: '/system?tab=brain',
        },
      ],
      progress: progress(),
      nav: { urgent_open: counts(items).urgent_open, decide: pendingDrafts('today').length + liveProposals('today').length },
    };
  }

  const section = (key: string, title: string, count: number, lines: string[], to: string, group: BriefSection['group'], extra: Partial<BriefSection> = {}): BriefSection => ({
    key, title, count, lines: lines.slice(0, 5), to, group, ...(count === 0 && lines.length === 0 ? { state: 'empty' as const } : {}), ...extra,
  });

  function liveSections(ep: Endpoint): BriefSection[] {
    const drafts = pendingDrafts(ep);
    const none = noData(ep);
    return [
      section('tasks_due', 'Việc đến hạn', none ? 0 : 2, none ? [] : ['Gọi lại chị Lan về hợp đồng — hạn 09:00 (quá hạn)', 'Gửi báo giá cho anh Hậu — hạn 16:00'], '/owner/viec', 'decide'),
      section('hot_customers', 'Khách nóng', none ? 0 : 3, none ? [] : ['Trần Văn Hậu — độ nóng 91', 'Nguyễn Văn Bảo — độ nóng 87', 'Lâm Văn Được — độ nóng 84'], '/owner/quan-he', 'key'),
      section('drafts_pending', 'Nháp chờ duyệt', drafts.length, drafts.map((d) => `${d.code} · ${d.title}`), '/owner/viec', 'decide'),
      section('incidents', 'Sự cố cần Sếp', none ? 0 : 1, none ? [] : ['Ổ đĩa sắp đầy (82%)'], '/system?tab=storage&focus=health', 'alert'),
      ...(none ? [] : [section('facebook', 'Facebook mới', 4, ['Trang bán hàng: 3 thông báo, 1 hội thoại chưa đọc'], '/social', 'key')]),
    ];
  }

  /** Mục Gen-hub (lịch, mail, việc Google) — chỉ có ở bản tin ĐÃ LƯU, không có ở `live`. */
  function hubSections(): BriefSection[] {
    if (noData('brief')) return [];
    return [
      section('calendar_today', 'Lịch hôm nay', 2, ['09:00 · Họp giao ban đầu tuần', '14:00 · Gặp khách tại xưởng'], '/connections#genhub', 'key'),
      section('mail_reply', 'Mail cần trả lời', 3, ['Phạm Thu Trang — Báo giá đợt 2 gửi lại giúp', 'Kế toán Thành Phát — Xác nhận công nợ tháng 9'], '/connections#genhub', 'decide'),
      section('gtasks_open', 'Việc Google đang mở', 1, ['Chuẩn bị hợp đồng mẫu — hạn 12/10'], '/connections#genhub', 'decide'),
    ];
  }

  const HISTORY = [
    { id: 'brief-3', slot: 'sáng' as const, mins: 3 * 60, title: 'Bản tin Gen · sáng 11/10', summary: 'Sáng nay Sếp có nháp chờ duyệt và một khách có thể sắp rời. Em đã gom việc đến hạn ở bên dưới.' },
    { id: 'brief-2', slot: 'chiều' as const, mins: 17 * 60, title: 'Bản tin Gen · chiều 10/10', summary: 'Chiều qua có 4 tin hỏi giá mới và 1 lời hứa giao hàng sắp đến hạn.' },
    { id: 'brief-1', slot: 'sáng' as const, mins: 27 * 60, title: 'Bản tin Gen · sáng 10/10', summary: 'Sáng qua có 3 bản nháp chờ Sếp duyệt và 2 mối quan hệ đang nguội dần.' },
  ];

  function brief(id: string | null): OwnerBrief | null {
    const ep: Endpoint = 'brief';
    const none = noData(ep);
    const pick = id ? HISTORY.find((h) => h.id === id) : HISTORY[0];
    if (id && !pick) return null;
    const live = liveSections(ep);
    return {
      latest: none || !pick ? null : {
        id: pick.id, slot: pick.slot, at: ago(pick.mins * MIN), title: pick.title, summary: pick.summary, needs_api_key: false,
        sections: [...live.slice(0, 4), ...hubSections(), ...live.slice(4)],
      },
      // như máy chủ thật: xem một bản CŨ (?id=) không chạy phần đọc lúc này ⇒ live = null
      live: id ? null : { at: new Date().toISOString(), sections: live },
      alerts: none ? [] : [
        { key: 'backup.failed', title: 'Bản sao lưu đêm qua chưa chạy được', severity: 'bad', raised_at: ago(9 * HOUR), to: '/system?tab=storage' },
        { key: 'disk.low', title: 'Ổ đĩa sắp đầy (82%)', severity: 'warn', raised_at: ago(3 * HOUR), to: '/system?tab=storage&focus=health' },
      ],
      history: none ? [] : HISTORY.map((h) => ({ id: h.id, slot: h.slot, at: ago(h.mins * MIN), title: h.title })),
      next_at: nextBriefAt(),
      ai_ready: true,
      channels_connected: channels(),
    };
  }

  function tasks(m: Mode): OwnerTasks {
    const empty = m === 'empty';
    return {
      groups: [
        {
          key: 'inbox',
          title: 'Hộp thư đã lọc',
          count: empty ? 0 : 7,
          to: '/inbox',
          items: empty
            ? []
            : [
                { title: 'Hỏi giá: Khách hỏi MDF E1 17mm, 3 container', at: ago(20 * MIN), to: '/inbox' },
                { title: 'Than phiền: Giao hàng trễ hai ngày', at: ago(2 * 60 * MIN), to: '/inbox' },
              ],
        },
        {
          key: 'desk',
          title: 'Bàn làm việc',
          count: empty ? 0 : 3,
          to: '/workbench',
          items: empty ? [] : [{ title: 'Trả lời anh Hậu về giá ván MDF', at: ago(35 * MIN), to: '/workbench?id=draft-1' }],
        },
        {
          key: 'tasks',
          title: 'Việc & Nhắc hẹn',
          count: empty ? 0 : 5,
          to: '/tasks',
          items: empty ? [] : [{ title: 'Gọi lại chị Lan về hợp đồng', at: ago(2 * 24 * 60 * MIN), to: '/tasks' }],
        },
      ],
    };
  }

  function fail(ctx: P2Ctx, ep: Endpoint): boolean {
    return ctx.problem(500, 'INTERNAL', 'Hệ thống gặp lỗi khi xử lý yêu cầu — đã ghi nhật ký', { error_id: `ERR-OWNER-${ep.toUpperCase()}` });
  }

  const asDraftPerson = (p: FeedItem['person']): PersonRef | null => (p ? { id: p.id, code: `PER-${p.id}`, name: p.name, type: 'customer', org_name: null } : null);

  /** "Trả lời" một tin ⇒ NHÁP chờ duyệt thật ở mock core (không có đường gửi thẳng). */
  function makeReplyDraft(item: FeedItem, text: string, who: string): DraftDetail {
    seq += 1;
    const person = asDraftPerson(item.person);
    return {
      id: `draft-owner-${seq}`, code: `ACT-0${500 + seq}`, kind: 'message', kind_label: 'Tin nhắn', title: `Trả lời ${item.person?.name ?? item.title}`,
      agent: null, created_by: { id: USER_IDS.owner, name: who }, created_at: new Date().toISOString(), status: 'pending',
      hold_reason: 'ghi ra ngoài', subject: person,
      paragraphs: text.split(/\n\s*\n/).filter(Boolean), text, lang: 'vi',
      target: { channel: 'zalo', thread_type: 'user', group: null, person },
      amount_vnd: null, autonomy_level: 4,
      flags: { writes_external: true, personnel_related: false, over_threshold: false },
      approve_label: 'Duyệt và gửi qua Zalo', sources: [],
      context: [{ key: 'Khách', value: item.person?.name ?? 'Khách', ref: null }, { key: 'Tin gốc', value: item.summary, ref: null }],
      side_actions: [], decision: null, send_result: null, versions: [],
    };
  }

  /** Các đường NGOÀI `/owner/*` mà mock này trả lời — chỉ cho mã của chính nó (`fd-…`, `prop-…`); còn lại nhường mock khác. */
  function handleOwned(ctx: P2Ctx): boolean {
    const { method: m, path: p } = ctx;
    if (ctx.role !== 'owner') return false;
    const inbox = /^\/inbox\/(fd-[\w-]+)(?:\/(act|silence))?$/.exec(p);
    if (inbox) {
      const item = feed.find((f) => f.id === inbox[1]);
      if (!item) return false;
      if (!inbox[2] && m === 'GET') {
        return ctx.reply(200, {
          id: item.id, code: item.id.toUpperCase(), item_type: item.item_type, tab: item.item_type === 'alert' ? 'alert' : 'reply', title: item.title, summary: item.summary,
          priority: item.importance === 'Khẩn' ? 'P1' : item.importance === 'Quan trọng' ? 'P2' : 'P3', created_at: item.created_at ?? new Date().toISOString(),
          score: item.item_type === 'unit' ? 0.82 : null, confidence_band: item.item_type === 'unit' ? 'cao' : null, subject: asDraftPerson(item.person), group: null, agent: null,
          alert_type: null, alert_type_label: null, suggested_action: item.suggested_action, units: [], status: item.item_type === 'alert' ? 'open' : undefined,
        });
      }
      if (inbox[2] === 'silence' && m === 'POST') {
        silenced.add(item.id);
        return ctx.reply(200, { ok: true });
      }
      if (inbox[2] === 'act' && m === 'POST') {
        if (item.item_type === 'alert') {
          if (acked.has(item.id)) return ctx.problem(409, 'ALERT_DECIDED', 'Cảnh báo này đã được xử lý');
          acked.add(item.id);
          return ctx.reply(200, { ok: true, status: 'acknowledged' });
        }
        const text = String((ctx.body as { text?: unknown }).text ?? '').trim();
        if (!text) return ctx.problem(422, 'VALIDATION', 'Cần nội dung để soạn trả lời', { errors: { text: 'Không được để trống' } });
        const draft = makeReplyDraft(item, text, ctx.userLabel);
        opts.pushDraft?.(draft);
        return ctx.reply(200, { ok: true, draft: { id: draft.id, code: draft.code, status: 'pending' } });
      }
      return false;
    }
    const gen = /^\/gen\/proposals\/(prop-[\w-]+)\/(confirm|cancel)$/.exec(p);
    if (gen && m === 'POST') {
      const prop = props.find((x) => x.id === gen[1]);
      if (!prop) return ctx.problem(404, 'NOT_FOUND', 'Đề xuất (có thể đã hết hạn) không tồn tại hoặc ngoài phạm vi của bạn');
      if (gen[2] === 'confirm' && prop.needs_pin && ctx.needPin()) {
        return ctx.problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN', { detail: { operation: 'hub.write' } });
      }
      props = props.filter((x) => x.id !== prop.id);
      return ctx.reply(200, {
        id: prop.id, type: prop.type, fields: {}, summary: prop.summary, labels: {}, target: 'tasks.new', requires_pin: prop.needs_pin,
        status: gen[2] === 'confirm' ? 'confirmed' : 'cancelled',
      });
    }
    return false;
  }

  function handle(ctx: P2Ctx): boolean {
    const { method: m, path: p } = ctx;
    if (p !== '/owner' && !p.startsWith('/owner/')) return handleOwned(ctx);
    if (ctx.role !== 'owner') {
      state.nonOwnerCalls.push(`${ctx.role} ${m} ${p}`);
      return ctx.problem(403, 'FORBIDDEN', 'Vai trò của bạn không có quyền thao tác này');
    }
    if (m !== 'GET') return ctx.problem(405, 'NOT_ALLOWED', 'Mặt tiền chỉ để xem — thao tác ghi đi qua luồng sẵn có');
    const q = ctx.url.searchParams;

    if (p === '/owner/today') {
      state.calls.today += 1;
      return state.mode.today === 'error' ? fail(ctx, 'today') : ctx.reply(200, today('today'));
    }
    if (p === '/owner/tasks') {
      state.calls.tasks += 1;
      return state.mode.tasks === 'error' ? fail(ctx, 'tasks') : ctx.reply(200, tasks(state.mode.tasks));
    }
    if (p === '/owner/brief') {
      state.calls.brief += 1;
      if (state.mode.brief === 'error') return fail(ctx, 'brief');
      const body = brief(q.get('id'));
      return body ? ctx.reply(200, body) : ctx.problem(404, 'NOT_FOUND', 'Bản tin không tồn tại hoặc ngoài phạm vi của bạn');
    }
    if (p === '/owner/proposals') {
      state.calls.proposals += 1;
      return state.mode.proposals === 'error' ? fail(ctx, 'proposals') : ctx.reply(200, { items: liveProposals('proposals') });
    }
    if (p === '/owner/insights') {
      state.calls.insights += 1;
      const range = q.get('range') ?? 'week';
      if (range !== 'week' && range !== 'month') {
        return ctx.problem(422, 'VALIDATION', 'Dữ liệu chưa hợp lệ', { detail: null, errors: { range: 'Chỉ nhận week hoặc month' } });
      }
      const rawOnly = q.get('only');
      const only = rawOnly ? rawOnly.split(',').map((k) => k.trim()).filter(Boolean) : null;
      const bad = (only ?? []).filter((k) => !INSIGHT_DEFS.some((d) => d.key === k));
      if (bad.length > 0) return ctx.problem(422, 'VALIDATION', 'Dữ liệu chưa hợp lệ', { detail: null, errors: { only: `Chỉ số không có: ${bad.join(', ')}` } });
      if (state.mode.insights === 'error') return fail(ctx, 'insights');
      const dm: OwnerDataMode = state.mode.insights === 'empty' ? 'empty' : state.data;
      return ctx.reply(200, insightsBody(range, only as InsightKey[] | null, dm));
    }
    const fctx = /^\/owner\/feed\/([^/]+)\/context$/.exec(p);
    if (fctx) {
      state.calls.feed += 1;
      if (state.mode.feed === 'error') return fail(ctx, 'feed');
      const id = decodeURIComponent(fctx[1]);
      const item = feedItems('feed').find((f) => f.id === id);
      if (!item) return ctx.problem(404, 'NOT_FOUND', 'Tin này không tồn tại hoặc ngoài phạm vi của bạn');
      const firstUrgent = feedItems('feed').find((f) => f.importance === 'Khẩn' && f.item_type === 'unit');
      const body: FeedContext = item.id === firstUrgent?.id ? conversationFor(item.id) : { center_event_id: null, messages: [] };
      return ctx.reply(200, body);
    }
    if (p === '/owner/feed') {
      state.calls.feed += 1;
      if (state.mode.feed === 'error') return fail(ctx, 'feed');
      const view = q.get('view') ?? 'important';
      if (!['important', 'all', 'opportunity', 'alert', 'approval'].includes(view)) {
        return ctx.problem(422, 'VALIDATION', 'Dữ liệu chưa hợp lệ', { detail: null, errors: { view: 'Chỉ nhận important, all, opportunity, alert hoặc approval' } });
      }
      const all = feedItems('feed');
      const pick = (v: FeedView) =>
        all.filter((i) => (v === 'all' ? true : v === 'important' ? i.importance !== 'Thường' : v === 'opportunity' ? i.item_type === 'unit' && !!i.event_type && OPPORTUNITY.has(i.event_type) : v === 'alert' ? i.item_type === 'alert' : i.item_type === 'draft'));
      const rawLimit = Number(q.get('limit') ?? 30);
      const limit = Math.max(1, Math.min(Number.isFinite(rawLimit) ? Math.trunc(rawLimit) : 30, 50));
      const rawOff = Number(q.get('offset') ?? 0);
      const offset = Math.max(0, Number.isFinite(rawOff) ? Math.trunc(rawOff) : 0);
      const rows = pick(view as FeedView);
      const body: OwnerFeed = {
        items: rows.slice(offset, offset + limit),
        next_offset: rows.length > offset + limit ? offset + limit : null,
        counts: counts(all),
        filtered_7d: noData('feed') ? { filtered: 0, spam_blocked: 0 } : { filtered: 124, spam_blocked: 31 },
        channels_connected: channels(),
      };
      return ctx.reply(200, body);
    }
    if (p === '/owner/relations') {
      state.calls.relations += 1;
      const list = q.get('list') ?? 'hot';
      if (!['hot', 'cooling', 'bridges', 'matches', 'care'].includes(list)) {
        return ctx.problem(422, 'VALIDATION', 'Dữ liệu chưa hợp lệ', { detail: null, errors: { list: 'Chỉ nhận hot, cooling, bridges, matches hoặc care' } });
      }
      if (state.mode.relations === 'error') return fail(ctx, 'relations');
      const raw = Number(q.get('limit') ?? 20);
      const limit = Math.max(1, Math.min(Number.isFinite(raw) ? Math.trunc(raw) : 20, 50));
      const items = noData('relations') ? [] : relationItems(list as OwnerRelationList, opts.matches?.() ?? null).slice(0, limit);
      const body: OwnerRelations = { list: list as OwnerRelationList, items };
      return ctx.reply(200, body);
    }
    return ctx.problem(404, 'NOT_FOUND', 'Không tồn tại');
  }

  const hooks = {
    data: (b: unknown) => {
      state.data = parseOwnerDataMode((b as { mode?: unknown } | null)?.mode);
      return { mode: state.data };
    },
    scenario: (b: unknown) => {
      const x = (b ?? {}) as { mode?: Mode; only?: Endpoint };
      const mode: Mode = x.mode === 'empty' || x.mode === 'error' ? x.mode : 'data';
      const eps: Endpoint[] = x.only && ENDPOINTS.includes(x.only) ? [x.only] : [...ENDPOINTS];
      for (const ep of ENDPOINTS) state.mode[ep] = eps.includes(ep) ? mode : 'data';
      return { ...state.mode };
    },
    state: () => ({ mode: { ...state.mode }, calls: { ...state.calls }, nonOwnerCalls: [...state.nonOwnerCalls], data: state.data }),
    reset: () => {
      for (const ep of ENDPOINTS) {
        state.mode[ep] = 'data';
        state.calls[ep] = 0;
      }
      state.nonOwnerCalls = [];
      state.data = initialData;
      feed = feedSeed();
      silenced.clear();
      acked.clear();
      props = proposalSeed();
      return null;
    },
  } as Record<string, (...args: never[]) => unknown>;

  return { handle, hooks, dispose: () => {} };
}
