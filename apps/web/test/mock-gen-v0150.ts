/**
 * Mock v0.1.50 (F-81, QD-18) — "Gen nhớ" + đề xuất GHI VÀO KHO DỮ LIỆU của Gen, theo HỢP ĐỒNG API (gói api làm thật):
 *
 * - `GET /gen/memory` → {items, limit: 30, max_len: 280, reason_max: 200}; `POST` (201, dùng nội bộ khi xác nhận đề xuất),
 *   `PATCH /gen/memory/{id}` (source → 'owner'), `DELETE` (204). CHỈ Owner (vai trò khác 403 FORBIDDEN), không PIN.
 *   Lỗi: 409 GEN_MEMORY_FULL / GEN_MEMORY_DUPLICATE, 422 field errors, 404.
 * - Kịch bản cho `mock-gen.ts` (qua `opts.extra`): "nhớ giúp …" → thẻ `memory_note`; "ghi … vào Kho" → thẻ `kho_create` (Phiên;
 *   thêm "việc" → bảng Việc), thêm "sửa/cập nhật" → `kho_update`; thêm "thiếu quyền" → nhãn `write_scope: 'missing'`.
 * - Xác nhận `memory_note`: tạo ghi chú (source 'gen'). Xác nhận `kho_*`: PIN 'hub.write' (423) rồi MỘT lời gọi ghi duy nhất
 *   `opts.kho.write({proposal_id, tool, args, permit})` (= `POST /hub/kho/write` của mock-p4-mcp) — bấm Huỷ thì KHÔNG có lời gọi nào.
 *   Lỗi theo mã: HUB_WRITE_PERMIT, HUB_WRITE_MISSING, HUB_WRITE_UNCERTAIN, HUB_WRITE_REJECTED, HUB_LINK_OFF, HUB_BREAKER_OPEN.
 * - Mock không ghi gì lên Kho / Gen-hub thật; token không bao giờ có mặt ở đây.
 *
 * Hook e2e `POST /api/v1/__mock/p3/genMemory/seed {text, reason?, source?}` → thêm một ghi chú (không qua đề xuất).
 */
import { randomUUID } from 'node:crypto';
import type { GenMemoryNote, GenProposal, GenStep, KhoBang } from '../../../packages/contracts/src/gen';
import { KHO_FIELDS, KHO_REQUIRED } from '../../../packages/contracts/src/gen';
import type { P2Ctx } from './mock-phase2';

export const MEMORY_LIMIT = 30;
export const MEMORY_MAX = 280;
export const REASON_MAX = 200;

export interface KhoWriteReq {
  proposal_id: string;
  tool: 'kho_create' | 'kho_update';
  args: Record<string, unknown>;
  permit: string;
}
export type KhoWriteOutcome =
  | { ok: true; code: string | null; bang: KhoBang }
  | { ok: false; status: number; code: string; title: string; /** Lý do thật (lời Kho từ chối, lý do permit…) như máy chủ đặt ở `detail`. */ detail?: string };

export interface GenV0150Options {
  /** Một lần ghi Kho đã được xác nhận (mock-p4-mcp `khoWrite`) — KHÔNG gọi khi Huỷ. */
  kho: { write: (req: KhoWriteReq) => KhoWriteOutcome };
}

/** Phần mock-gen.ts dùng: kịch bản câu hỏi + xử lý xác nhận các loại đề xuất mới. */
export interface GenExtra {
  script: (q: string) => GenStep[] | null;
  /** `null` = không phải loại của gói này (mock-gen xử lý tiếp); khác = phản hồi cuối. */
  confirm: (
    pr: GenProposal,
    ctx: P2Ctx,
  ) => { proposal: GenProposal } | { error: { status: number; code: string; title: string; detail?: string; operation?: string; errors?: Record<string, string> } } | null;
}

const trimSpaces = (v: string) => v.replace(/\s+/g, ' ').trim();

export function createMock(opts: GenV0150Options) {
  let notes: GenMemoryNote[] = [];
  const now = () => new Date().toISOString();

  const listOut = () => ({
    items: [...notes].sort((a, b) => b.updated_at.localeCompare(a.updated_at)),
    limit: MEMORY_LIMIT, max_len: MEMORY_MAX, reason_max: REASON_MAX,
  });

  type Verdict = { error: { status: number; code: string; title: string; errors?: Record<string, string> } };
  const checkFields = (text: unknown, reason: unknown): Verdict | null => {
    const errors: Record<string, string> = {};
    if (typeof text === 'string') {
      if (!text.trim()) errors.text = 'Không được để trống';
      else if ([...text.trim()].length > MEMORY_MAX) errors.text = `Tối đa ${MEMORY_MAX} ký tự`;
    }
    if (typeof reason === 'string' && [...reason.trim()].length > REASON_MAX) errors.reason = `Tối đa ${REASON_MAX} ký tự`;
    return Object.keys(errors).length ? { error: { status: 422, code: 'VALIDATION_ERROR', title: 'Dữ liệu chưa hợp lệ', errors } } : null;
  };
  const duplicate = (text: string, exceptId?: string) => notes.some((n) => n.id !== exceptId && trimSpaces(n.text).toLowerCase() === trimSpaces(text).toLowerCase());

  function addNote(text: string, reason: string | null, source: 'gen' | 'owner'): GenMemoryNote | Verdict {
    const bad = checkFields(text, reason);
    if (bad) return bad;
    if (!text.trim()) return { error: { status: 422, code: 'VALIDATION_ERROR', title: 'Dữ liệu chưa hợp lệ', errors: { text: 'Không được để trống' } } };
    if (notes.length >= MEMORY_LIMIT) return { error: { status: 409, code: 'GEN_MEMORY_FULL', title: `Gen nhớ đã đủ ${MEMORY_LIMIT} ghi chú — xoá bớt trước` } };
    if (duplicate(text)) return { error: { status: 409, code: 'GEN_MEMORY_DUPLICATE', title: 'Ghi chú này đã có trong Gen nhớ' } };
    const t = now();
    const note: GenMemoryNote = { id: randomUUID(), text: text.trim(), reason: reason && reason.trim() ? reason.trim() : null, source, created_at: t, updated_at: t };
    notes = [...notes, note];
    return note;
  }

  function handle(ctx: P2Ctx): boolean {
    const { method: m, path: p, body, reply, problem } = ctx;
    if (p !== '/gen/memory' && !p.startsWith('/gen/memory/')) return false;
    if (ctx.role !== 'owner') return problem(403, 'FORBIDDEN', 'Chỉ Owner dùng được Gen nhớ');
    const id = p.split('/').filter(Boolean)[2];
    if (!id && m === 'GET') return reply(200, listOut());
    if (!id && m === 'POST') {
      const r = addNote(String(body.text ?? ''), typeof body.reason === 'string' ? body.reason : null, 'gen');
      return 'error' in r ? problem(r.error.status, r.error.code, r.error.title, r.error.errors ? { errors: r.error.errors } : undefined) : reply(201, r);
    }
    const note = notes.find((n) => n.id === id);
    if (id && m === 'PATCH') {
      if (!note) return problem(404, 'NOT_FOUND', 'Ghi chú không tồn tại');
      const bad = checkFields(body.text, body.reason);
      if (bad) return problem(bad.error.status, bad.error.code, bad.error.title, { errors: bad.error.errors });
      if (typeof body.text === 'string' && duplicate(body.text, note.id)) return problem(409, 'GEN_MEMORY_DUPLICATE', 'Ghi chú này trùng với một ghi chú đã có');
      const next: GenMemoryNote = {
        ...note,
        text: typeof body.text === 'string' ? body.text.trim() : note.text,
        reason: 'reason' in body ? (typeof body.reason === 'string' && body.reason.trim() ? body.reason.trim() : null) : note.reason,
        source: 'owner',
        updated_at: now(),
      };
      notes = notes.map((n) => (n.id === note.id ? next : n));
      return reply(200, next);
    }
    if (id && m === 'DELETE') {
      if (!note) return problem(404, 'NOT_FOUND', 'Ghi chú không tồn tại');
      notes = notes.filter((n) => n.id !== note.id);
      return reply(204);
    }
    return problem(404, 'NOT_FOUND', 'Không tồn tại');
  }

  // ── Kịch bản + xác nhận cho mock-gen ──────────────────────────────────────────────────────────────────
  const PHIEN_RECORD: Record<string, string> = {
    'Chủ đề': 'Gen-Harness v0.1.50 — Gen nhớ và ghi Kho có mã PIN',
    Ngày: '2026-10-09',
    'Đã chốt': 'Gen nhớ tối đa 30 ghi chú; ghi Kho chỉ khi Sếp Xác nhận và nhập mã PIN',
    'Việc tiếp': 'Sếp duyệt đề xuất PHIEN đầu tiên của Gen',
  };
  const VIEC_RECORD: Record<string, string> = {
    'Tiêu đề': 'Soạn báo giá ván MDF E1 17mm cho anh Bảo',
    'Trạng thái': 'Đang làm',
    'Ưu tiên': 'P2',
    Hạn: '2026-10-15',
  };

  function khoProposal(q: string): GenProposal {
    const t = q.toLowerCase();
    const viec = /việc/.test(t);
    const update = /sửa|cập nhật|đổi/.test(t);
    const missing = /thiếu quyền/.test(t);
    const bang: KhoBang = viec ? 'Việc' : 'Phiên';
    const base = { id: randomUUID(), status: 'pending' as const, requires_pin: true };
    const scope = missing ? 'missing' : 'ok';
    if (update) {
      const ma = viec ? 'VIEC-12' : 'PHIEN-11';
      const record: Record<string, string> = viec ? { 'Trạng thái': 'Xong', 'Ngày xong': '2026-10-09' } : { 'Đã chốt': 'Đã chốt thêm: Gen ghi Kho có PIN' };
      const cur: Record<string, string> = viec ? { 'cur:Trạng thái': 'Đang làm', 'cur:Ngày xong': '' } : { 'cur:Đã chốt': 'Gen nhớ tối đa 30 ghi chú' };
      return {
        ...base, type: 'kho_update', fields: { ma, record },
        summary: `Sửa bản ghi ${ma} ở bảng ${bang} của Kho dữ liệu — ghi thẳng qua Gen-hub khi Sếp xác nhận và nhập mã PIN.`,
        labels: { bang, target: `${ma} · ${viec ? 'Soạn báo giá ván MDF E1' : 'Phiên v0.1.49'}`, write_scope: scope, ...cur },
        target: `hub.kho_write:${ma}`,
      };
    }
    return {
      ...base, type: 'kho_create', fields: { bang, record: viec ? VIEC_RECORD : PHIEN_RECORD },
      summary: `Tạo bản ghi mới ở bảng ${bang} của Kho dữ liệu — ghi thẳng qua Gen-hub khi Sếp xác nhận và nhập mã PIN.`,
      labels: { bang, target: 'Bản ghi mới', write_scope: scope },
      target: `hub.kho_write:${bang}`,
    };
  }

  function script(q: string): GenStep[] | null {
    const t = q.toLowerCase();
    if (/nhớ giúp|ghi nhớ|hãy nhớ/.test(t)) {
      const proposal: GenProposal = {
        id: randomUUID(), type: 'memory_note',
        fields: { text: 'Báo giá luôn ghi rõ VAT 8% và thời hạn hiệu lực 7 ngày.', reason: 'Sếp dặn khi soạn báo giá ván MDF.' },
        summary: 'Ghi nhớ quy ước: báo giá luôn ghi rõ VAT 8% và thời hạn hiệu lực 7 ngày.',
        labels: { count: `${notes.length}/${MEMORY_LIMIT}` },
        target: 'gen.memory', requires_pin: false, status: 'pending',
      };
      return [
        { kind: 'say', text: 'Dạ, em đề xuất ghi nhớ quy ước này — Sếp xem, sửa nếu cần rồi bấm Xác nhận ghi nhớ nhé.' },
        { kind: 'proposal', proposal },
      ];
    }
    if (/vào kho|ghi kho/.test(t)) {
      return [
        { kind: 'say', text: 'Dạ, em soạn sẵn bản ghi — Sếp đọc kỹ các trường, bấm Xác nhận và ghi Kho (cần mã PIN) thì mới ghi vào Kho dữ liệu nhé.' },
        { kind: 'proposal', proposal: khoProposal(q) },
      ];
    }
    return null;
  }

  type ConfirmResult = ReturnType<GenExtra['confirm']>;
  function confirm(pr: GenProposal, ctx: P2Ctx): ConfirmResult {
    if (pr.type === 'memory_note') {
      const edited = (ctx.body.fields ?? {}) as { text?: unknown; reason?: unknown };
      const text = typeof edited.text === 'string' ? edited.text : pr.fields.text;
      const reason = typeof edited.reason === 'string' ? edited.reason : pr.fields.reason;
      const r = addNote(text, reason, 'gen');
      if ('error' in r) return r;
      return { proposal: { ...pr, fields: { text: r.text, reason: r.reason ?? '' }, status: 'confirmed', result: { type: 'memory_note', id: r.id, code: null, screen: 'system' } } };
    }
    if (pr.type !== 'kho_create' && pr.type !== 'kho_update') return null;
    // Một mã PIN cho cả bước xác nhận lẫn lời gọi ghi (PIN 'hub.write'): chưa nhập ⇒ 423, web hỏi PIN rồi gửi lại.
    if (ctx.needPin()) return { error: { status: 423, code: 'PIN_REQUIRED', title: 'Thao tác này cần nhập mã PIN', operation: 'hub.write' } };
    const edited = (ctx.body.fields ?? {}) as { record?: unknown };
    const record: Record<string, string> =
      edited.record && typeof edited.record === 'object' && !Array.isArray(edited.record) ? (edited.record as Record<string, string>) : pr.fields.record;
    const bang: KhoBang = pr.type === 'kho_create' ? pr.fields.bang : pr.labels.bang === 'Việc' ? 'Việc' : 'Phiên';
    const allowed = KHO_FIELDS[bang];
    const bad = Object.keys(record).filter((k) => !allowed.includes(k));
    if (bad.length) return { error: { status: 422, code: 'VALIDATION_ERROR', title: 'Dữ liệu chưa hợp lệ', errors: { record: `Trường không hợp lệ: ${bad.join(', ')}` } } };
    if (pr.type === 'kho_create' && !String(record[KHO_REQUIRED[bang]] ?? '').trim()) {
      return { error: { status: 422, code: 'VALIDATION_ERROR', title: 'Dữ liệu chưa hợp lệ', errors: { record: `Thiếu trường bắt buộc: ${KHO_REQUIRED[bang]}` } } };
    }
    const args = pr.type === 'kho_create' ? { bang, ...record } : { ma: pr.fields.ma, ...record };
    // Giấy phép ký 5 phút, 1 lần, gắn proposal_id + tool + sha256 args (mock: chuỗi giả, không bí mật).
    const out = opts.kho.write({ proposal_id: pr.id, tool: pr.type, args, permit: `permit-${randomUUID()}` });
    if (!out.ok) return { error: { status: out.status, code: out.code, title: out.title, ...(out.detail ? { detail: out.detail } : {}) } };
    const code = pr.type === 'kho_update' ? pr.fields.ma : out.code;
    return {
      proposal: { ...pr, fields: pr.type === 'kho_create' ? { bang, record } : { ma: pr.fields.ma, record }, status: 'confirmed', result: { type: 'kho_record', id: null, code, screen: null, bang } } as GenProposal,
    };
  }

  return {
    handle,
    extra: { script, confirm } satisfies GenExtra,
    hooks: {
      /** Thêm ghi chú ngay (không qua đề xuất). */
      seed: (b: unknown) => {
        const body = (b ?? {}) as { text?: unknown; reason?: unknown; source?: unknown };
        const r = addNote(String(body.text ?? ''), typeof body.reason === 'string' ? body.reason : null, body.source === 'owner' ? 'owner' : 'gen');
        return 'error' in r ? r.error : r;
      },
      list: () => listOut(),
    } as Record<string, (...args: never[]) => unknown>,
    dispose: () => {},
  };
}
