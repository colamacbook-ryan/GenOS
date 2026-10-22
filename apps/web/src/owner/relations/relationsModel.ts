/**
 * v0.1.59 (G4) — màn Quan hệ (`/owner/quan-he`) + hồ sơ sống: hàm THUẦN định dạng + truy vấn hồ sơ. Không React.
 *
 * Không bịa số: thiếu giá trị thì nói "Chưa có". Điểm 0–100 luôn đi kèm lời ("Độ nóng 87 · đang tăng"). Số điện thoại luôn che bớt.
 * Mọi chuỗi từ máy chủ đi qua `asText` (không bao giờ render object).
 */
import { useQuery } from '@tanstack/react-query';
import type { OwnerRelationRow, Profile, ProfileScore } from '@gen-harness/contracts';
import { api } from '../../lib/api';
import { asText, moneyText } from '../ownerModel';
import { useIsOwnerRole } from '../queries';

// ── 4 danh sách của màn ─────────────────────────────────────────────────────────────────────────────────────────

export type RelationTabKey = 'care' | 'hot' | 'bridges' | 'matches';

export interface RelationTabDef {
  list: RelationTabKey;
  label: string;
  /** "khách cần chăm" — đứng sau số lớn ở đầu danh sách. */
  noun: string;
  /** Câu rỗng của riêng danh sách này (một câu, không chồng thêm khối khác). */
  empty: string;
  /** Chân danh sách: Gen chọn theo luật nào. */
  footnote: string;
}

export const RELATION_TABS: readonly RelationTabDef[] = [
  {
    list: 'care', label: 'Cần chăm', noun: 'khách cần chăm', empty: 'Không khách nào cần chăm gấp.',
    footnote: 'Gen chọn khách có nguy cơ rời đi hoặc từ 14 ngày chưa liên lạc.',
  },
  {
    list: 'hot', label: 'Khách nóng', noun: 'khách nóng', empty: 'Chưa có khách nóng. Khi có người nhắn nhiều và nhắn thường xuyên, họ sẽ hiện ở đây.',
    footnote: 'Những người đang quan tâm mạnh — nên hồi âm sớm.',
  },
  {
    list: 'bridges', label: 'Cầu nối', noun: 'người cầu nối', empty: 'Chưa thấy người cầu nối. Cần có người ở từ hai nhóm chat trở lên Gen mới nhận ra.',
    footnote: 'Người có mặt ở nhiều nhóm, nối các nhóm với nhau.',
  },
  {
    list: 'matches', label: 'Cung–cầu', noun: 'cặp cung–cầu', empty: 'Chưa có cặp nào hợp nhau. Khi có người hỏi mua và có người chào bán cùng mặt hàng, Gen sẽ ghép lại.',
    footnote: 'Người đang cần mua gặp người đang có hàng. Giới thiệu chỉ tạo bản nháp — Sếp duyệt rồi mới gửi.',
  },
];

/** Danh sách mặc định của màn. */
export const DEFAULT_RELATION_TAB: RelationTabKey = 'care';

/** Đọc `?list=` trên địa chỉ: 4 danh sách của màn; `cooling` (link cũ từ Hôm nay) về "Cần chăm"; lạ ⇒ "Cần chăm". */
export function parseListParam(v: unknown): RelationTabKey {
  if (v === 'hot' || v === 'bridges' || v === 'matches' || v === 'care') return v;
  return DEFAULT_RELATION_TAB;
}

export function relationTabDef(list: RelationTabKey): RelationTabDef {
  return RELATION_TABS.find((t) => t.list === list) ?? RELATION_TABS[0];
}

/** Khoá người của một dòng: `person_id` (nếu có) hoặc `id` (dòng của danh sách theo người — care, hot, bridges). */
export function personKey(row: Pick<OwnerRelationRow, 'id' | 'person_id'>): string {
  return asText(row.person_id) || asText(row.id);
}

/** Số đếm hiện trên nút lọc: đủ trang ("20") hay có thể còn nữa ("20+"). */
export function countLabel(n: number | undefined, limit: number): string {
  if (typeof n !== 'number') return '';
  return n >= limit ? `${n}+` : String(n);
}

export interface CareHeadline {
  total: number;
  /** "2 sắp rời, 3 lâu chưa liên lạc" — chỉ khi là danh sách Cần chăm và có lý do. */
  breakdown: string;
}

export function careHeadline(rows: readonly OwnerRelationRow[]): CareHeadline {
  const churn = rows.filter((r) => r.reason === 'churn').length;
  const silent = rows.filter((r) => r.reason === 'silent').length;
  const parts = [churn > 0 ? `${churn} sắp rời` : '', silent > 0 ? `${silent} lâu chưa liên lạc` : ''].filter(Boolean);
  return { total: rows.length, breakdown: parts.join(', ') };
}

// ── câu chữ của cột ─────────────────────────────────────────────────────────────────────────────────────────────

/** "Hôm nay" · "Hôm qua" · "18 ngày trước" · thiếu ⇒ "Chưa có". */
export function lastContactText(days: unknown): string {
  if (typeof days !== 'number' || !Number.isFinite(days) || days < 0) return 'Chưa có';
  const d = Math.floor(days);
  return d === 0 ? 'Hôm nay' : d === 1 ? 'Hôm qua' : `${d} ngày trước`;
}

/** Cơ hội còn mở (₫): "180 triệu ₫" · không có / bằng 0 ⇒ "Chưa có". */
export function openValueText(vnd: unknown): string {
  return typeof vnd === 'number' && Number.isFinite(vnd) && vnd > 0 ? moneyText(vnd) : 'Chưa có';
}

export interface ReasonBadge {
  label: string;
  tone: 'warn' | 'neutral';
}

/** Lý do cần chăm: sắp rời (nhấn) / lâu chưa liên lạc. Thiếu ⇒ null (không đoán). */
export function reasonBadge(reason: unknown): ReasonBadge | null {
  if (reason === 'churn') return { label: 'Sắp rời', tone: 'warn' };
  if (reason === 'silent') return { label: 'Lâu chưa liên lạc', tone: 'neutral' };
  return null;
}

export type Trend = 'up' | 'down' | 'flat';

/** Xu hướng đọc từ chữ của máy chủ ("Độ nóng 91 · đang tăng"). Không nói gì ⇒ null (danh sách Cần chăm không có xu hướng). */
export function trendFromMetric(metric: unknown): Trend | null {
  const s = asText(metric);
  if (s.includes('đang tăng')) return 'up';
  if (s.includes('đang giảm')) return 'down';
  return null;
}

export function trendWord(trend: unknown): string {
  return trend === 'up' ? 'đang tăng' : trend === 'down' ? 'đang giảm' : trend === 'flat' ? 'giữ nguyên' : '';
}

/** Số nguyên 0–100 để hiện; không phải số ⇒ null. */
export function scoreValue(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? Math.round(v) : null;
}

/** Câu điền sẵn khi nhờ Gen soạn tin hỏi thăm một khách (chỉ ĐIỀN SẴN ô nhập của Gen — không gửi). */
export function askGenPrompt(name: string): string {
  return `Soạn tin hỏi thăm ${name}`;
}

// ── Cung–cầu ────────────────────────────────────────────────────────────────────────────────────────────────────

export interface MatchView {
  /** Ai cần. */
  who_needs: string;
  /** Cần gì. */
  need: string;
  /** Ai có. */
  who_has: string;
  /** Mặt hàng bên có — chỉ khi khác với món cần (để dòng phụ). */
  supply: string;
  /** Khớp, phần trăm (0–100); không đọc được ⇒ null. */
  percent: number | null;
}

/**
 * Tách dòng Cung–cầu của máy chủ: `name` = "<cần gì> ↔ <bên có bán gì>", `subtitle` = "<ai cần> cần · <ai có> có",
 * `metric_text` = "Khớp 94%". Không đọc được thì dùng nguyên chữ (không bịa).
 */
export function parseMatch(row: Pick<OwnerRelationRow, 'name' | 'subtitle' | 'metric_text'>): MatchView {
  const [needRaw, ...rest] = asText(row.name).split(' ↔ ');
  const need = needRaw.trim() || asText(row.name);
  const supplyRaw = rest.join(' ↔ ').trim();
  const sub = /^(.*?)\s+cần\s+·\s+(.*?)\s+có$/.exec(asText(row.subtitle).trim());
  const pct = /(\d+(?:[.,]\d+)?)\s*%/.exec(asText(row.metric_text));
  return {
    who_needs: sub ? sub[1] : asText(row.subtitle),
    need,
    who_has: sub ? sub[2] : '',
    supply: supplyRaw && supplyRaw !== need ? supplyRaw : '',
    percent: pct ? Math.round(Number(pct[1].replace(',', '.'))) : null,
  };
}

// ── Cầu nối ─────────────────────────────────────────────────────────────────────────────────────────────────────

/** "Nối 5 cặp nhóm" → "5 cặp nhóm"; máy chủ nói chữ khác thì giữ nguyên chữ đó. */
export function bridgeCount(metric: unknown): string {
  const s = asText(metric).trim();
  const m = /^Nối\s+(\d+)\s+(.+)$/.exec(s);
  return m ? `${m[1]} ${m[2]}` : s;
}

// ── hồ sơ sống ──────────────────────────────────────────────────────────────────────────────────────────────────

const TYPE_LABEL: Record<string, string> = {
  customer: 'Khách hàng', supplier: 'Nhà cung cấp', partner: 'Đối tác', staff: 'Nhân sự', candidate: 'Ứng viên', learner: 'Học viên', unknown: 'Chưa phân loại',
};

export function personTypeLabel(type: unknown): string {
  return TYPE_LABEL[asText(type)] ?? '';
}

const RELATION_LABEL: Record<string, string> = {
  direct: 'Quen trực tiếp với Sếp', via_staff: 'Quen qua nhân viên', stranger: 'Chưa quen trực tiếp', staff: 'Nhân sự của Sếp',
};

/** Quan hệ với Sếp bằng chữ đời thường; chuỗi lạ (máy chủ cũ trả sẵn câu) thì giữ nguyên. */
export function relationLabel(rel: unknown): string {
  const s = asText(rel);
  return RELATION_LABEL[s] ?? s;
}

/** "quen 3 năm" · "quen 6 tháng" · "mới quen" — từ lần đầu thấy họ; thiếu ⇒ rỗng. */
export function tenureText(iso: unknown, now: number = Date.now()): string {
  const t = Date.parse(asText(iso));
  if (Number.isNaN(t) || t > now) return '';
  const days = Math.floor((now - t) / 86_400_000);
  if (days < 30) return 'mới quen';
  const months = Math.floor(days / 30);
  if (months < 12) return `quen ${months} tháng`;
  return `quen ${Math.floor(days / 365)} năm`;
}

/** Số điện thoại che bớt: "+84903xxx118" → "09•• ••• •18". Quá ngắn ⇒ che hết. */
export function maskPhone(raw: unknown): string {
  const s = asText(raw).trim();
  let d = s.replace(/\D/g, '');
  // +84… (hoặc 84… đủ 11 số) là số Việt Nam viết theo mã quốc gia ⇒ đọc theo dạng trong nước (0…).
  if (s.startsWith('+84') || (d.startsWith('84') && d.length >= 11)) d = `0${d.slice(2)}`;
  if (d.length < 6) return '••••••';
  return `${d.slice(0, 2)}•• ••• •${d.slice(-2)}`;
}

const SCORE_LABEL: Record<string, string> = { heat: 'Độ nóng', churn_risk: 'Rủi ro rời bỏ', potential: 'Tiềm năng' };
const SCORE_ORDER = ['heat', 'churn_risk', 'potential'] as const;
/** Chiều tăng là xấu (rủi ro rời bỏ): màu cảnh báo khi tăng, xanh khi giảm; còn lại ngược lại. */
const BAD_WHEN_UP = new Set(['churn_risk']);

export interface ScoreLine {
  dimension: string;
  label: string;
  value: number;
  trend: Trend | null;
  /** "đang tăng" / "đang giảm" / "giữ nguyên" / "". */
  word: string;
  tone: 'ok' | 'warn' | 'neutral';
}

function toneOf(dimension: string, trend: Trend | null): ScoreLine['tone'] {
  if (trend === null || trend === 'flat') return 'neutral';
  const bad = BAD_WHEN_UP.has(dimension) ? trend === 'up' : trend === 'down';
  return bad ? 'warn' : 'ok';
}

/**
 * 3 điểm của hồ sơ (độ nóng, rủi ro rời bỏ, tiềm năng) — nhãn đời thường theo mã (không dùng nhãn kỹ thuật của máy chủ), có kèm lời xu hướng.
 * Hồ sơ thiếu điểm nào thì bỏ điểm đó; thiếu cả hồ sơ thì lấy độ nóng / rủi ro của dòng danh sách (nếu có) để khối không trống.
 */
export function scoreLines(scores: readonly ProfileScore[] | undefined, row?: Pick<OwnerRelationRow, 'heat' | 'churn_risk'>): ScoreLine[] {
  const out: ScoreLine[] = [];
  for (const dim of SCORE_ORDER) {
    const s = (Array.isArray(scores) ? scores : []).find((x) => x?.dimension === dim);
    const v = scoreValue(s?.value);
    if (v === null) continue;
    const trend: Trend | null = s?.trend === 'up' || s?.trend === 'down' || s?.trend === 'flat' ? s.trend : null;
    out.push({ dimension: dim, label: SCORE_LABEL[dim], value: v, trend, word: trendWord(trend), tone: toneOf(dim, trend) });
  }
  if (out.length === 0 && row) {
    for (const [dim, raw] of [['heat', row.heat], ['churn_risk', row.churn_risk]] as const) {
      const v = scoreValue(raw);
      if (v !== null) out.push({ dimension: dim, label: SCORE_LABEL[dim], value: v, trend: null, word: '', tone: 'neutral' });
    }
  }
  return out;
}

/** "Độ nóng 87 · đang tăng" — câu đọc cho một điểm. */
export function scoreSentence(s: ScoreLine): string {
  return [`${s.label} ${s.value}`, s.word].filter(Boolean).join(' · ');
}

/** Tối đa 5 dòng tóm tắt, bỏ dòng rỗng. */
export function summaryLines(profile: Pick<Profile, 'summary'> | undefined): Array<{ text: string; tone: string }> {
  const out: Array<{ text: string; tone: string }> = [];
  for (const l of Array.isArray(profile?.summary) ? profile.summary : []) {
    const text = asText(l?.text).trim();
    if (text) out.push({ text, tone: asText(l?.tone) });
  }
  return out.slice(0, 5);
}

/** 5 sự việc gần nhất, mới trước. */
export function recentEvents(profile: Pick<Profile, 'timeline'> | undefined): Array<{ id: string; at: string; text: string }> {
  const rows = (Array.isArray(profile?.timeline) ? profile.timeline : [])
    .map((t) => ({ id: asText(t?.id), at: asText(t?.observed_at), text: asText(t?.conclusion).trim() }))
    .filter((t) => t.id && t.text);
  rows.sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  return rows.slice(0, 5);
}

/** Kênh liên lạc: "Zalo · 09•• ••• •12" (số che bớt); không có số thì chỉ tên kênh. */
export function channelLines(profile: Pick<Profile, 'identities'> | undefined): Array<{ id: string; text: string }> {
  const out: Array<{ id: string; text: string }> = [];
  for (const i of Array.isArray(profile?.identities) ? profile.identities : []) {
    const id = asText(i?.id);
    const name = asText(i?.channel?.name);
    if (!id || !name) continue;
    out.push({ id, text: i.phone_e164 ? `${name} · ${maskPhone(i.phone_e164)}` : name });
  }
  return out;
}

/** Dòng phụ dưới tên: "Khách hàng · quen 3 năm" — bỏ phần thiếu. (Quan hệ với Sếp có dòng riêng trong hồ sơ.) */
export function profileSubtitle(profile: Profile | undefined, fallback: string, now: number = Date.now()): string {
  if (!profile) return fallback;
  const firstSeen = (Array.isArray(profile.identities) ? profile.identities : [])
    .map((i) => asText(i?.first_seen_at))
    .filter(Boolean)
    .sort()[0];
  const parts = [personTypeLabel(profile.person?.type), tenureText(firstSeen, now)].filter(Boolean);
  return parts.length > 0 ? parts.join(' · ') : fallback;
}

// ── truy vấn hồ sơ ──────────────────────────────────────────────────────────────────────────────────────────────

/** Hồ sơ sống (`GET /profile/{id}`) — chỉ đọc. Khoá bắt đầu `['owner', …]` nên sau mỗi lần ghi tự tải lại. */
export function useOwnerProfile(personId: string | null) {
  const owner = useIsOwnerRole();
  return useQuery({
    queryKey: ['owner', 'profile', personId ?? ''] as const,
    queryFn: ({ signal }) => api.relations.profile.get(personId as string, signal),
    enabled: owner && !!personId,
  });
}

