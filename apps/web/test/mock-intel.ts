/**
 * Mock v0.1.79 (G1) — trang Tình báo, theo HỢP ĐỒNG của `apps/api/gh/biz/intel/routes.py` + `console.py`:
 *
 * - `GET /signals?kind=&level=&status=&limit=` → {items, hidden_pin_n}; `GET /signals/{id}` → tín hiệu + `evidence` (đã che); id lạ ⇒ 404.
 * - `POST /signals/{id}/feedback {verdict, reason?}` → {ok, status, level}; `POST /signals/{id}/transition {to}` → {ok, status, level, delivery}.
 * - `GET /asks` → {items, hidden_pin_n}; `GET /intel/console` (ramp R4 + dòng bóng); `GET /intel/setup` (đã xong ⇒ không dải nhắc).
 * - CHỈ Owner: vai khác 403 FORBIDDEN. Sự cố hệ thống KHÔNG nằm trong `/signals` (máy chủ lọc kind `system`/`legacy`).
 *
 * Dữ liệu hư cấu. Trạng thái trong bộ nhớ (mỗi `POST /__mock/reset` dựng lại). Test e2e riêng (intel-p4) vẫn có thể ghi đè bằng `page.route`.
 */
import type { P2Ctx } from './mock-phase2';

interface Sig {
  id: string; code: string; kind: string; level: 'red' | 'amber' | 'grey'; status: string; title: string; summary: string;
  suggested_action: string | null; confidence_level: 'high' | 'medium' | 'low'; reasons: string[]; created_at: string;
  feedback_verdict: string | null; evidence: Array<{ type: string; id: string; quote_status: string; quote_display: string; received_at: string }>;
}

const iso = (minAgo: number) => new Date(Date.now() - minAgo * 60_000).toISOString();

function seed(): Sig[] {
  const ev = (n: number, q: string) => ({ type: 'raw_event', id: `ev-${n}`, quote_status: 'ok', quote_display: q, received_at: iso(n * 3) });
  return [
    { id: 'sg-1', code: 'SG-0001', kind: 'rui_ro_lua_dao', level: 'red', status: 'open', title: 'Người mới gửi STK lạ, yêu cầu chuyển khoản', summary: 'Người mới vào nhóm gửi số tài khoản, thúc giục chuyển khoản gấp.', suggested_action: 'Gọi số cũ xác minh, chưa chuyển tiền', confidence_level: 'high', reasons: [], created_at: iso(20), feedback_verdict: null, evidence: [ev(2, 'Em chuyển khoản vào •••456 giúp anh nhé'), ev(3, 'Gấp lắm, anh chuyển ngay')] },
    { id: 'sg-2', code: 'SG-0002', kind: 'xung_dot_khieu_nai', level: 'red', status: 'open', title: 'Khách Anh Tuấn doạ huỷ đơn', summary: 'Anh Tuấn nói sẽ huỷ đơn nếu hôm nay chưa giao.', suggested_action: 'Gọi lại trong hôm nay, báo giờ giao', confidence_level: 'medium', reasons: ['key_customer'], created_at: iso(18), feedback_verdict: null, evidence: [ev(4, 'Hôm nay không giao là tôi huỷ đơn')] },
    { id: 'sg-3', code: 'SG-0003', kind: 'nghia_vu_loi_hua', level: 'amber', status: 'open', title: 'Lời hứa giao 20 bao xi măng trước 15h', summary: 'Nhân viên đã hứa giao trước 15h chiều nay.', suggested_action: 'Nhắc nhân viên giao đúng giờ', confidence_level: 'high', reasons: ['milestone_due_soon'], created_at: iso(90), feedback_verdict: null, evidence: [ev(5, 'Chiều nay 3 giờ em giao tới nơi')] },
    { id: 'sg-4', code: 'SG-0004', kind: 'co_hoi_ket_noi', level: 'amber', status: 'open', title: 'Khách hỏi 50 tấn thép, cần gấp', summary: 'Khách hỏi giá lô thép lớn.', suggested_action: 'Báo giá trong ngày', confidence_level: 'medium', reasons: [], created_at: iso(150), feedback_verdict: null, evidence: [ev(6, 'Bên em có 50 tấn thép tấm không?')] },
    { id: 'sg-5', code: 'SG-0005', kind: 'su_kien_dip', level: 'grey', status: 'open', title: 'Sinh nhật chị Lan ngày mai', summary: 'Chị Lan là khách thân thiết.', suggested_action: null, confidence_level: 'low', reasons: [], created_at: iso(300), feedback_verdict: 'correct', evidence: [] },
    { id: 'sg-6', code: 'SG-0006', kind: 'viec_han', level: 'grey', status: 'resolved', title: 'Gửi báo giá cho cô Hà', summary: 'Đã gửi.', suggested_action: null, confidence_level: 'high', reasons: [], created_at: iso(1500), feedback_verdict: null, evidence: [] },
  ];
}

export function createMock(opts: { fresh: boolean }) {
  const sigs = opts.fresh ? [] : seed();
  const item = (s: Sig) => ({ id: s.id, code: s.code, kind: s.kind, level: s.level, gate: null, candidate: null, delivery: 'digest', status: s.status, lane: null, privacy: 'chat_ok', title: s.title, summary: s.summary, suggested_action: s.suggested_action, impact: 2, urgency: 2, confidence_level: s.confidence_level, reasons: s.reasons, created_at: s.created_at, pushed_at: null, would_push_at: null, capped_at: null, due_at: null, group_id: null, owner_fact_ids: [], evidence_n: s.evidence.length, feedback_verdict: s.feedback_verdict });
  const asks = opts.fresh ? [] : [
    { id: 'ask-1', code: 'AS-0012', source: 'gen', decision_kind: 'internal', summary: 'Gộp "Anh Tuấn (Zalo)" với "Tuấn Hoà Phát"?', signal_id: null, expires_at: null },
    { id: 'ask-2', code: 'AS-0013', source: 'gen', decision_kind: 'outbound', summary: 'Nháp trả lời chị Lan về giá thép', signal_id: null, expires_at: null },
  ];

  function handle(ctx: P2Ctx): boolean {
    const { method: m, path: p, reply, problem, url, body } = ctx;
    const one = /^\/signals\/([^/]+)(?:\/(feedback|transition))?$/.exec(p);
    const known = p === '/signals' || p === '/asks' || p === '/intel/console' || p === '/intel/setup' || !!one;
    if (!known) return false;
    if (ctx.role !== 'owner') return problem(403, 'FORBIDDEN', 'Vai trò của bạn không có quyền thao tác này');
    if (p === '/signals' && m === 'GET') {
      const q = url.searchParams;
      const status = q.get('status') ?? 'open';
      const items = sigs.filter((s) => s.status === status && (!q.get('level') || s.level === q.get('level')) && (!q.get('kind') || s.kind === q.get('kind')));
      return reply(200, { items: items.map(item), hidden_pin_n: 0 });
    }
    if (one) {
      const s = sigs.find((x) => x.id === decodeURIComponent(one[1]));
      if (!s) return problem(404, 'NOT_FOUND', 'Không tìm thấy tín hiệu');
      if (!one[2] && m === 'GET') return reply(200, { ...item(s), evidence: s.evidence, facts: [] });
      if (one[2] === 'feedback' && m === 'POST') {
        const verdict = String(body.verdict ?? '');
        if (!['correct', 'wrong', 'unimportant', 'missed'].includes(verdict)) return problem(422, 'VALIDATION', 'Phản hồi không hợp lệ');
        s.feedback_verdict = verdict;
        return reply(200, { ok: true, status: s.status, level: s.level });
      }
      if (one[2] === 'transition' && m === 'POST') {
        const to = String(body.to ?? '');
        if (!['acknowledged', 'resolved', 'dismissed'].includes(to)) return problem(422, 'VALIDATION', 'Trạng thái không hợp lệ');
        if (s.status !== 'open' && s.status !== 'acknowledged') return problem(409, 'SIGNAL_STATE', 'Tín hiệu này đã được xử lý');
        s.status = to;
        return reply(200, { ok: true, status: s.status, level: s.level, delivery: 'none' });
      }
    }
    if (p === '/asks' && m === 'GET') return reply(200, { items: asks, hidden_pin_n: 0 });
    if (p === '/intel/setup' && m === 'GET') {
      return reply(200, { ready: true, groups: { done: true, total: 2, unlabeled_count: 0, unlabeled: [] }, staff: { done: true, confirmed_at: iso(60), staff_count: 1, people: [] }, key_customers: { done: true, confirmed_at: iso(60), key_count: 1, customers: [] }, contexts: ['kinh_doanh', 'khac'], gated_kinds: [] });
    }
    if (p === '/intel/console' && m === 'GET') {
      return reply(200, {
        pin_active: false, mode: 'live', ramp_gates: 'digest_top', regular_push_enabled: true,
        assistant: { locked: false, locked_at: null, locked_via: null }, outbound: { unlocked_today: false, locked_until: null },
        backup_channel: { paired: true, kind: 'telegram' }, mutes: [], payees: [], groups: [], contexts: ['kinh_doanh', 'khac'],
        ramp: { started_at: iso(2 * 1440), ends_at: iso(-12 * 1440), days: 14, days_left: 12, in_trial: true, passed_gates: [], m1: { n: 4, k: 3, p: 0.75, lo95: 0.3, ok: false, min_n: 10, wrong_by_reason: {}, low_precision_gates: [] } },
        shadow_items: [{ id: 'sg-4', kind: 'co_hoi_ket_noi', gate: 'co_hoi_lon_sap_troi', title: 'Khách hỏi 50 tấn thép, cần gấp', reasons: ['shadow'], would_push_at: iso(150), verdict: null }],
      });
    }
    return problem(405, 'METHOD_NOT_ALLOWED', 'Không hỗ trợ thao tác này');
  }

  return { handle, dispose: () => {}, hooks: { state: () => ({ signals: sigs }) } };
}
