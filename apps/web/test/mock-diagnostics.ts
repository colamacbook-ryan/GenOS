/**
 * Mock v0.1.44 (F-4b) — "Gói chẩn đoán cho người hỗ trợ" (`/system/diagnostics*`, CHỈ Owner) và `POST /client-errors`
 * (không cần đăng nhập), đúng hợp đồng HTTP api ↔ web của v0.1.44 (api làm song song).
 *
 * - POST /system/diagnostics (PIN `diagnostics.download`) → 202 pending; GET sau 0,4 s → running; sau 1,2 s → done
 *   (hoặc failed khi `seed {diag:'fail'}`; `seed {diag:'stale'}` ⇒ pending quá 15 phút, `stale:true`, POST lại được). Đang tạo mà POST → 409 DIAG_BUSY; genh cũ (`seed {diag:'unsupported'}`) →
 *   GET `supported:false` + POST 409 DIAG_UNSUPPORTED.
 * - GET /system/diagnostics/download (PIN) → application/zip; chưa xong → 404 DIAG_NOT_READY.
 * - POST /client-errors: kiểm khuôn `error_id`, ≥ 30 lần/phút → 429 CLIENT_ERRORS_RATE_LIMITED; lưu để e2e đọc lại.
 *
 * Hook e2e: `POST /api/v1/__mock/p3/diagnostics/seed {diag}`, `…/diagnostics/clientErrors {}`.
 */
import { randomBytes } from 'node:crypto';
import type { ClientErrorBody, DiagnosticsState } from '@gen-harness/contracts';
import type { P2Ctx } from './mock-phase2';

interface Opts {
  fresh: boolean;
  emit: (type: string, data: unknown) => void;
}

const hex16 = () => randomBytes(8).toString('hex');
export const DIAG_FILE = 'genh-doctor-20261003T010203Z.zip';

export function createMock(_opts: Opts) {
  const now = () => new Date().toISOString();
  const d = {
    supported: true,
    fail: false,
    state: 'idle' as DiagnosticsState['state'],
    requestId: null as string | null,
    requestedAt: 0,
    startedAt: null as string | null,
    finishedAt: null as string | null,
    /** `seed {diag:'stale'}`: genh không nhận yêu cầu — nằm "pending" mãi (máy chủ báo stale sau 15 phút). */
    stuck: false,
  };
  const BUSY_MS = 15 * 60 * 1000;
  const clientErrors: Array<ClientErrorBody & { received_at: string; request_id_server: string }> = [];

  const view = (): DiagnosticsState => {
    const age = Date.now() - d.requestedAt;
    if (d.state === 'pending' && age >= 400 && !d.stuck) {
      d.state = 'running';
      d.startedAt = now();
    }
    if (d.state === 'running' && age >= 1200) {
      d.state = d.fail ? 'failed' : 'done';
      d.finishedAt = now();
    }
    const done = d.state === 'done';
    const failed = d.state === 'failed';
    return {
      supported: d.supported,
      state: d.supported ? d.state : 'idle',
      request_id: d.requestId,
      requested_at: d.requestedAt ? new Date(d.requestedAt).toISOString() : null,
      started_at: d.startedAt,
      finished_at: d.finishedAt,
      file_name: done ? DIAG_FILE : null,
      size_bytes: done ? 48_213 : null,
      sha256: done ? 'a3f1c2d4e5b6978877665544332211ffeeddccbbaa99887766554433221100aa' : null,
      error_code: failed ? 'DOCTOR_FAILED' : null,
      message: failed ? 'genh doctor dừng giữa chừng: không đọc được nhật ký docker' : null,
      command: 'genh doctor',
      stale: (d.state === 'pending' || d.state === 'running') && age >= BUSY_MS,
    };
  };

  const needPin = (ctx: P2Ctx) =>
    ctx.needPin() ? ctx.problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN', { detail: { operation: 'diagnostics.download' } }) : false;

  function handle(ctx: P2Ctx): boolean {
    const { method: m, path: p, reply, problem } = ctx;
    if (p !== '/system/diagnostics' && !p.startsWith('/system/diagnostics/')) return false;
    if (ctx.role !== 'owner') return problem(403, 'FORBIDDEN', 'Vai trò của bạn không có quyền thao tác này');
    if (p === '/system/diagnostics' && m === 'GET') return reply(200, view());
    if (p === '/system/diagnostics' && m === 'POST') {
      if (needPin(ctx)) return true;
      if (!d.supported) return problem(409, 'DIAG_UNSUPPORTED', 'genh trên máy chủ chưa hỗ trợ tạo gói chẩn đoán', { command: 'genh doctor' });
      const cur = view();
      if ((cur.state === 'pending' || cur.state === 'running') && !cur.stale) return problem(409, 'DIAG_BUSY', 'Đang tạo một gói chẩn đoán');
      Object.assign(d, { stuck: false, state: 'pending', requestId: hex16(), requestedAt: Date.now(), startedAt: null, finishedAt: null });
      return reply(202, view());
    }
    if (p === '/system/diagnostics/download' && m === 'GET') {
      if (needPin(ctx)) return true;
      if (view().state !== 'done') return problem(404, 'DIAG_NOT_READY', 'Chưa có gói chẩn đoán để tải');
      return ctx.text(200, 'application/zip', 'PK\u0003\u0004mock-genh-doctor', DIAG_FILE);
    }
    return false;
  }

  /** `POST /client-errors` (không cần đăng nhập) — mock-api gọi TRƯỚC cổng đăng nhập/thiết lập. */
  const recordClientError = (body: Record<string, unknown>): { status: number; body: Record<string, unknown> } => {
    const recent = clientErrors.filter((e) => Date.now() - Date.parse(e.received_at) < 60_000).length;
    if (recent >= 30) return { status: 429, body: { code: 'CLIENT_ERRORS_RATE_LIMITED', title: 'Gửi báo lỗi quá nhiều — thử lại sau' } };
    if (typeof body.error_id !== 'string' || !/^ERR-[A-Z0-9]{1,8}-[A-Z0-9]{4}$/.test(body.error_id) || typeof body.path !== 'string') {
      return { status: 422, body: { code: 'VALIDATION', title: 'Dữ liệu chưa hợp lệ', errors: { error_id: 'Mã lỗi chưa đúng dạng' } } };
    }
    const rid = hex16();
    clientErrors.push({ ...(body as unknown as ClientErrorBody), received_at: now(), request_id_server: rid });
    return { status: 202, body: { ok: true, request_id: rid } };
  };

  const seed = (b: { diag?: 'unsupported' | 'idle' | 'fail' | 'ok' | 'stale' } = {}) => {
    if (b.diag) {
      d.supported = b.diag !== 'unsupported';
      d.fail = b.diag === 'fail';
    }
    if (b.diag === 'stale') {
      Object.assign(d, { stuck: true, state: 'pending', requestId: hex16(), requestedAt: Date.now() - BUSY_MS - 60_000, startedAt: null, finishedAt: null });
    }
    return view();
  };

  return {
    handle,
    recordClientError,
    hooks: { seed, view, clientErrors: () => clientErrors } as Record<string, (...args: never[]) => unknown>,
    dispose: () => {},
  };
}
