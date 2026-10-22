/** Hợp đồng API giai đoạn 3 · Con người & Chất lượng (docs/api/phase-3-people.md). */
import type { ApiClient } from './client';
import type { EvidenceRef, PersonRef, Trend, UserRef } from './p3-core';

// ─── Đánh giá con người (people) ─────────────────────────────────────────────
/** 4 board (F2 §6) ánh xạ từ `core.persons.person_type`: employee→staff, customer→customer, candidate→candidate, student→learner. */
export type ReviewBoard = 'employee' | 'customer' | 'candidate' | 'student';
export const REVIEW_BOARDS: ReviewBoard[] = ['employee', 'customer', 'candidate', 'student'];

/** Nhánh hiển thị theo Q4 (`docs/PLAN.md`): `full` Owner (hoặc vai trò Owner tự cấp thêm) — đủ nội dung;
 * `log` Auditor — chỉ id/person/period/has_content/dispute_count + nhật ký ai đã xem; `none` — 403, ẩn hẳn. */
export type ReviewAccessMode = 'full' | 'log' | 'none';

export interface PeopleReviewFull {
  id: string;
  person: PersonRef;
  period_start: string;
  period_end: string;
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
  /** v0.1.45 (F-60): tin nhân viên gửi giống lệnh cho AI / xin điểm → cờ 'Đáng ngờ' ĐANG hiệu lực (chỉ cảnh báo,
   *  điểm không đổi). Owner đã bỏ cờ → false (xem `suspicious_cleared`). */
  suspicious: boolean;
  /** Lý do ngắn lúc gắn cờ (không chứa nguyên tin) — null khi chưa từng gắn cờ; giữ lại cả khi đã bỏ cờ. */
  suspicious_reason: string | null;
  /** Ai bỏ cờ, lúc nào, vì sao — null khi chưa bỏ. Optional để tương thích máy chủ cũ. */
  suspicious_cleared?: PeopleReviewSuspiciousCleared | null;
}
export interface PeopleReviewSuspiciousCleared {
  by: UserRef | null;
  at: string;
  reason: string;
}
/** PATCH /people/reviews/{id}/suspicious — bỏ cờ 'Đáng ngờ' sau khi đã xem chứng cứ (cần mã PIN, ghi Nhật ký). */
export interface PeopleReviewSuspiciousClearBody {
  cleared_reason: string;
}
export interface PeopleReviewHistoryItem {
  id: string;
  score: number;
  trend: Trend | null;
  created_at: string;
  overridden_by: UserRef | null;
  override_reason: string | null;
}
export type DisputeStatus = 'open' | 'resolved' | 'rejected';
export interface PeopleReviewDisputeItem {
  id: string;
  review_id: string;
  raised_by: UserRef;
  body: string;
  status: DisputeStatus;
  resolution: string | null;
  resolved_by: UserRef | null;
  resolved_at: string | null;
  created_at: string;
}
export interface PeopleReviewFullDetail extends PeopleReviewFull {
  history: PeopleReviewHistoryItem[];
  disputes: PeopleReviewDisputeItem[];
}

/** Nhật ký "ai đã xem" — chỉ Auditor nhìn thấy (nhánh `log`), tối đa 10 dòng gần nhất. */
export interface ReviewViewedEntry {
  at: string;
  user: UserRef | null;
  action: 'people_review.viewed' | 'people_review.explained' | 'people_review.audit_viewed' | string;
}
/** Hình dạng nhánh `log` (Q4): KHÔNG có score/trend/signal/recommendation/evidence. */
export interface PeopleReviewLogItem {
  id: string;
  person: PersonRef;
  period_start: string;
  period_end: string;
  created_at: string;
  has_content: boolean;
  dispute_count: number;
  viewed_by: ReviewViewedEntry[];
}

export type PeopleReviewItem = PeopleReviewFull | PeopleReviewLogItem;
export type PeopleReviewDetail = PeopleReviewFullDetail | PeopleReviewLogItem;
/** Type guard: nhánh `full` có trường `score`, nhánh `log` thì không. Generic để giữ nguyên `history`/`disputes`
 * khi gọi trên `PeopleReviewDetail` (không thu hẹp về mỗi `PeopleReviewFull`, làm mất hai trường đó). */
export function isFullReview<T extends PeopleReviewItem | PeopleReviewDetail>(
  x: T,
): x is Extract<T, PeopleReviewFull | PeopleReviewFullDetail> {
  return 'score' in x;
}

export interface PeopleReviewPage {
  items: PeopleReviewItem[];
  next_cursor: string | null;
  total: number;
}
export interface PeopleReviewQuery {
  board?: ReviewBoard;
  person_id?: string;
  period_start?: string;
  period_end?: string;
  cursor?: string;
  limit?: number;
}
/** `PATCH /people/reviews/{id}` — sửa điểm tay, giữ lịch sử (không UPDATE, chèn dòng mới `supersedes_id`). */
export interface PeopleReviewPatchBody {
  score: number;
  reason: string;
  evidence: EvidenceRef[];
  trend?: Trend | null;
  signal?: string;
  recommendation?: string;
}

export interface DisputeCreateBody {
  body: string;
}
export interface DisputeResolveBody {
  status: Exclude<DisputeStatus, 'open'>;
  resolution: string;
}

// ─── Chất lượng chăm sóc (care) ───────────────────────────────────────────────
export interface CareResponseRow {
  staff: PersonRef;
  fast: number;
  normal: number;
  slow: number;
  total_answered: number;
  fast_pct: number;
  avg_minutes: number;
}
export interface CareResponseTimes {
  from: string;
  to: string;
  items: CareResponseRow[];
  totals: { fast: number; normal: number; slow: number; total_answered: number; fast_pct: number };
  /** Tin chưa có ai trả lời — đứng riêng, không phải cột của nhân viên nào. */
  unattended: number;
}
export interface CareResponseQuery {
  date_from?: string;
  date_to?: string;
  person_id?: string;
}

export type CareIssueKind = 'broken_promise' | 'abandoned_customer';
export interface CareIssueRow {
  kind: CareIssueKind;
  subject: PersonRef;
  count: number;
  /** `count >= 2` (REPEAT_THRESHOLD). */
  repeated: boolean;
  last_at: string;
}
export interface CareIssuesPage {
  items: CareIssueRow[];
  next_cursor: null;
  total: number;
}
export interface CareIssuesQuery {
  date_from?: string;
  date_to?: string;
  issue_type?: CareIssueKind;
  limit?: number;
}

export type CareScenarioStatus = 'won' | 'lost';
export interface CareScenarioDeal {
  id: string;
  code: string;
  amount_vnd: number;
  status: CareScenarioStatus;
  won_at: string | null;
  opportunity_id: string | null;
}
export interface CareScenarioResponse {
  fast: number;
  normal: number;
  slow: number;
  unanswered: number;
  fast_pct: number;
  avg_minutes: number;
}
export interface CareScenarioItem {
  deal: CareScenarioDeal;
  person: PersonRef | null;
  response: CareScenarioResponse | null;
  broken_promises: number;
  note: string;
}
export interface CareScenarioSummarySide {
  count: number;
  avg_fast_pct: number;
  avg_broken_promises: number;
}
export interface CareScenariosPage {
  items: CareScenarioItem[];
  next_cursor: string | null;
  total: number;
  /** So sánh won/lost — tính trên trang hiện tại, không quét lại toàn tổ chức mỗi lượt gọi. */
  summary: { won: CareScenarioSummarySide; lost: CareScenarioSummarySide };
}
export interface CareScenariosQuery {
  status?: CareScenarioStatus;
  cursor?: string;
  limit?: number;
}

type Q = Record<string, string | number | boolean | null | undefined>;
const enc = encodeURIComponent;

/** Endpoint của cụm — gộp vào `createEndpoints` dưới khoá `people` (vd `api.people.reviews.list(...)`, `api.people.care.responseTimes(...)`). */
export function peopleEndpoints(r: ApiClient['request']) {
  return {
    reviews: {
      list: (q: PeopleReviewQuery = {}, signal?: AbortSignal) => r<PeopleReviewPage>('/people/reviews', { query: q as Q, signal }),
      get: (id: string, signal?: AbortSignal) => r<PeopleReviewDetail>(`/people/reviews/${enc(id)}`, { signal }),
      update: (id: string, body: PeopleReviewPatchBody) => r<PeopleReviewFullDetail>(`/people/reviews/${enc(id)}`, { method: 'PATCH', body }),
      clearSuspicious: (id: string, body: PeopleReviewSuspiciousClearBody) =>
        r<PeopleReviewFullDetail>(`/people/reviews/${enc(id)}/suspicious`, { method: 'PATCH', body }),
      disputes: {
        create: (reviewId: string, body: DisputeCreateBody) =>
          r<PeopleReviewDisputeItem>(`/people/reviews/${enc(reviewId)}/disputes`, { method: 'POST', body }),
        resolve: (id: string, body: DisputeResolveBody) =>
          r<PeopleReviewDisputeItem>(`/people/reviews/disputes/${enc(id)}`, { method: 'PATCH', body }),
      },
    },
    care: {
      responseTimes: (q: CareResponseQuery = {}, signal?: AbortSignal) => r<CareResponseTimes>('/care/response-times', { query: q as Q, signal }),
      repeatedIssues: (q: CareIssuesQuery = {}, signal?: AbortSignal) => r<CareIssuesPage>('/care/repeated-issues', { query: q as Q, signal }),
      scenarios: (q: CareScenariosQuery = {}, signal?: AbortSignal) => r<CareScenariosPage>('/care/scenarios', { query: q as Q, signal }),
    },
  };
}
