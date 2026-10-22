/**
 * v0.1.35 (F-1): id cố định dạng UUID cho người dùng seed và agent của mock — thay mọi 'u-…' / 'agent-…' cũ, để mock
 * kiểm id NHƯ API THẬT (FastAPI `uuid.UUID`): id không phải UUID → 422 VALIDATION; UUID không tồn tại → 404.
 * Tên hiển thị giữ nguyên ở các mock dùng id này (ảnh visual không đổi).
 */

/** Người dùng seed của `mock-api.ts` (Owner + 3 tài khoản vai trò khác). */
export const USER_IDS = {
  /** 'Anh Nguyễn Văn A (Chủ)' — Owner. */
  owner: '0190f1a0-0000-7000-8000-000000000001',
  /** 'Chị Lan Phạm' — Operator. */
  lan: '0190f1a0-0000-7000-8000-000000000002',
  /** 'Anh Minh Kiểm' — Auditor. */
  minh: '0190f1a0-0000-7000-8000-000000000003',
  /** 'Chị Hồng Quản' — Manager. */
  hong: '0190f1a0-0000-7000-8000-000000000004',
} as const;

/** Người chỉ xuất hiện như tên trong dữ liệu mẫu (điểm chạm hồ sơ) — KHÔNG phải tài khoản đăng nhập. */
export const STAFF_IDS = {
  /** 'Nguyễn Thu Hà' */
  ha: '0190f1a0-0000-7000-8000-0000000000b1',
  /** 'Trần Minh Khoa' */
  khoa: '0190f1a0-0000-7000-8000-0000000000b2',
} as const;

/** Agent: tls/hc/cs/mascot có trong `mock-p4-agents` (Danh tính Agent); ka/thk/rc chỉ còn là BOT cũ đã gán
 * trong dữ liệu mẫu Nhóm & Con người (không có trong `/pickers/agents` → hiện "(đã tắt)"). */
export const AGENT_IDS = {
  /** 'Trợ lý thương mại' */
  tls: '0190f1a0-0000-7000-8000-0000000000a1',
  /** 'Key Account junior' */
  ka: '0190f1a0-0000-7000-8000-0000000000a2',
  /** 'Admin hậu cần' */
  hc: '0190f1a0-0000-7000-8000-0000000000a3',
  /** 'Thư ký cá nhân' */
  thk: '0190f1a0-0000-7000-8000-0000000000a4',
  /** 'CSKH' */
  cs: '0190f1a0-0000-7000-8000-0000000000a5',
  /** 'Bé Heo' */
  mascot: '0190f1a0-0000-7000-8000-0000000000a6',
  /** 'Recruiter' */
  rc: '0190f1a0-0000-7000-8000-0000000000a7',
} as const;

const AGENT_BY_NAME: Record<string, string> = {
  'Trợ lý thương mại': AGENT_IDS.tls, 'Key Account junior': AGENT_IDS.ka, 'Admin hậu cần': AGENT_IDS.hc,
  'Thư ký cá nhân': AGENT_IDS.thk, CSKH: AGENT_IDS.cs, 'Bé Heo': AGENT_IDS.mascot, Recruiter: AGENT_IDS.rc,
};

/** UUID của agent theo tên trong `docs/design/seed-data.json`; tên lạ → UUID tất định băm từ tên (ổn định giữa
 * các lần chạy, để ảnh visual và test không đổi). */
export function agentIdByName(name: string): string {
  const known = AGENT_BY_NAME[name];
  if (known) return known;
  let h = 0x811c9dc5;
  for (const ch of name) h = Math.imul(h ^ (ch.codePointAt(0) ?? 0), 0x01000193) >>> 0;
  return `0190f1a0-0000-7000-8000-${h.toString(16).padStart(12, '0')}`;
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Giống pydantic `uuid.UUID` ở mức đủ dùng cho mock: chuỗi UUID 8-4-4-4-12 (chữ hoa/thường). */
export function isUuid(v: unknown): v is string {
  return typeof v === 'string' && UUID_RE.test(v);
}

/** Thân 422 VALIDATION như `gh.errors` (detail luôn null — lời lỗi nằm ở `errors[trường]`). */
export function uuidProblem(field: string) {
  return { code: 'VALIDATION' as const, title: 'Dữ liệu chưa hợp lệ', detail: null, errors: { [field]: 'Không hợp lệ' } };
}

/** Trả 422 qua `ctx.problem` của mock (P2Ctx) khi `value` không phải UUID; trả `null` nếu hợp lệ. */
export function rejectNonUuid(
  problem: (status: number, code: string, title: string, extra?: Record<string, unknown>) => true,
  field: string,
  value: unknown,
): true | null {
  if (isUuid(value)) return null;
  const b = uuidProblem(field);
  return problem(422, b.code, b.title, { detail: b.detail, errors: b.errors });
}
