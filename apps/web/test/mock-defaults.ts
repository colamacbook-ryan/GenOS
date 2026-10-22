/**
 * Mock v0.1.55 (G1) — "Chế độ tiêu chuẩn" + "Về mặc định" (`/api/v1/defaults*`), theo HỢP ĐỒNG của `apps/api/gh/defaults/routes.py`:
 *
 * - `GET /defaults` — CHỈ Owner (vai trò khác 403 FORBIDDEN) → `{items, customized_count, suggestions}`; mỗi mục có `default_text` /
 *   `current_text` là CHỮ (không object thô), `customized`, `resettable`. Gợi ý `apply_standard` khi ≥ 2 dòng gán model lõi "Đã đổi".
 * - `POST /defaults/{key}/reset` {confirm: true} — thiếu `confirm` ⇒ 422; khoá lạ ⇒ 404 DEFAULTS_KEY_UNKNOWN; `jev.preset` (chỉ xem) ⇒
 *   409 DEFAULTS_NOT_RESETTABLE.
 * - `POST /defaults/apply-standard` {confirm: true} — bỏ 4 dòng gán lõi (core.gen/briefing/refinery/reply).
 * - `POST /defaults/reset-all` {confirm: true} — cần mã PIN (423 PIN_REQUIRED khi chưa có phiên PIN). Mục `autonomy` (mức tự trị của tổ chức)
 *   là mục sổ riêng; Về mặc định riêng mục này cũng cần PIN.
 *
 * Trạng thái ở trong bộ nhớ (mỗi lần `createMock`, tức mỗi `POST /__mock/reset`, về lại tình trạng ban đầu). Mục `triage` MẶC ĐỊNH
 * "Đã đổi" (ngưỡng 55) để e2e bấm Về mặc định ngay; các mục khác "Mặc định".
 *
 * Hook e2e `POST /api/v1/__mock/p3/defaults/{hook}`:
 *   customize {keys: string[]} — đặt các mục thành "Đã đổi"; state {} — đọc trạng thái (kiểm tra); resets {} — số lần reset đã nhận.
 *
 * Nối vào `phase3` của `test/mock-api.ts`: `import { createMock as createDefaults } from './mock-defaults';`
 * và `defaults: createDefaults({ fresh: opts.setup === 'fresh', emit: broadcast }),` TRƯỚC `core`.
 */
import type { DefaultItem, DefaultSuggestion, DefaultsResponse } from '../../../packages/contracts/src/defaults';
import type { P2Ctx } from './mock-phase2';
import { KHO_DEFAULT, khoLabelState, resetKhoLabel } from './mock-kho-label';

interface Opts {
  fresh: boolean;
  emit: (type: string, data: unknown) => void;
}

interface Row extends DefaultItem {
  customized_text: string;
}

const khoText = (label: string) => `Kho nối qua Gen-hub gọi là “${label}”`;

const CORE_BINDINGS = ['binding:core.gen', 'binding:core.briefing', 'binding:core.refinery', 'binding:core.reply'];

function seedRows(): Row[] {
  const mk = (key: string, label: string, group: string, default_text: string, customized_text: string, over: Partial<Row> = {}): Row => ({
    key, label, scope: 'org', group, default_text, current_text: default_text, customized: false, resettable: true, customized_text, ...over,
  });
  return [
    mk('gen', 'Gen — vai trò dùng và số ngày giữ hội thoại', 'Gen', 'Gen dùng được cho chỉ Owner · giữ hội thoại 90 ngày', 'Gen dùng được cho vai trò: owner, manager · giữ hội thoại 30 ngày'),
    mk('coach', 'Gen hướng dẫn — tuỳ chọn của Sếp', 'Gen', 'Gen hướng dẫn bật · chuông bật · 1 bài mỗi ngày · yên lặng 21h–7h', 'Gen hướng dẫn bật · chuông tắt · 2 bài mỗi ngày · yên lặng 21h–7h', { scope: 'user' }),
    // e2e: bấm "Về mặc định" ở thẻ Lọc tin ngay từ đầu.
    mk('triage', 'Lọc tin', 'Bộ não AI', 'Lọc tin bật · ngưỡng điểm 30 · có dùng Jev', 'Lọc tin bật · ngưỡng điểm 55 · có dùng Jev', { customized: true, current_text: 'Lọc tin bật · ngưỡng điểm 55 · có dùng Jev' }),
    mk('refinery.schedule', 'Lịch sàng lọc tin', 'Bộ não AI', 'Sàng lọc mỗi 15 phút hoặc khi đủ 500 tin · mỗi lô 250 tin · độ tin cậy tối thiểu 0,6', 'Sàng lọc mỗi 5 phút hoặc khi đủ 100 tin · mỗi lô 50 tin · độ tin cậy tối thiểu 0,8'),
    mk('jev.preset', 'Jev — nguồn model', 'Bộ não AI', 'Jev dùng model typesafe/jev-1.13 qua openrouter.ai', 'Jev dùng model tự host qua jev.local', { resettable: false }),
    mk('ai_cost', 'Trần chi phí AI', 'Chi phí AI', 'Không đặt trần chi phí AI', 'Trần chi phí AI 500.000 ₫ mỗi ngày'),
    mk('backup', 'Lịch sao lưu', 'Sao lưu', 'Sao lưu hằng ngày lúc 02:00 · giữ 7 bản theo ngày, 4 theo tuần, 12 theo tháng · lưu trên máy chủ này', 'Sao lưu hằng tuần lúc 03:30 · giữ 7 bản theo ngày, 4 theo tuần, 12 theo tháng · lưu trên máy chủ này'),
    mk('autonomy', 'Mức tự trị của tổ chức', 'Gen', 'Soạn sẵn chờ duyệt (mức 4)', 'Gợi ý hành động (mức 3)'),
    // v0.1.57 (Nợ #30): Tên Kho — trạng thái thật nằm ở `mock-kho-label` (chung với `GET/PATCH /hub/link`); `view()` ghi đè dòng này.
    mk('kho_label', 'Tên Kho nối qua Gen-hub', 'Kết nối', khoText(KHO_DEFAULT), khoText('Sổ tay Công ty')),
    mk('binding:core.gen', 'Model cho Gen — trợ lý quản trị', 'Gán model', 'Chuẩn: sonnet (tự chọn) · mức suy nghĩ Vừa', 'gemini-2.5-pro (Antigravity Brain) — Sếp đã chọn'),
    mk('binding:core.briefing', 'Model cho Bản tin Gen', 'Gán model', 'Chuẩn: gemini-2.5-flash-lite (tự chọn)', 'gemini-2.5-pro (Antigravity Brain) — Sếp đã chọn'),
    mk('binding:core.refinery', 'Model cho Sàng lọc & suy luận chính', 'Gán model', 'Chuẩn: gemini-2.5-flash-lite (tự chọn)', 'gemini-2.5-pro (Antigravity Brain) — Sếp đã chọn'),
    mk('binding:core.reply', 'Model cho Soạn lại / dịch nháp', 'Gán model', 'Chuẩn: sonnet (tự chọn) · mức suy nghĩ Thấp', 'gemini-2.5-pro (Antigravity Brain) — Sếp đã chọn'),
  ];
}

export function createMock(_opts: Opts) {
  resetKhoLabel();
  const rows = seedRows();
  let resets = 0;
  const find = (key: string) => rows.find((r) => r.key === key);

  const view = (): DefaultsResponse => {
    const items: DefaultItem[] = rows.map(({ customized_text: _t, ...item }) =>
      item.key === 'kho_label'
        ? { ...item, customized: khoLabelState.label !== '', current_text: khoText(khoLabelState.label || KHO_DEFAULT) }
        : { ...item },
    );
    const suggestions: DefaultSuggestion[] =
      CORE_BINDINGS.filter((k) => find(k)?.customized).length >= 2
        ? [{
            key: 'apply_standard',
            title: 'Áp model chuẩn theo vai? (đang dùng 1 model cho mọi việc)',
            body: 'Em thấy Gen, lọc tin và soạn nháp đang dùng chung một model. Áp chuẩn thì em tự chọn model hợp từng việc.',
            to: '/system?tab=brain#chuan',
          }]
        : [];
    return { items, customized_count: items.filter((i) => i.customized && i.resettable).length, suggestions };
  };

  const setCustomized = (r: Row, on: boolean) => {
    r.customized = on;
    r.current_text = on ? r.customized_text : r.default_text;
  };

  function handle(ctx: P2Ctx): boolean {
    const { method: m, path: p, reply, problem } = ctx;
    if (p !== '/defaults' && !p.startsWith('/defaults/')) return false;
    if (ctx.role !== 'owner') return problem(403, 'FORBIDDEN', 'Vai trò của bạn không có quyền thao tác này');
    if (p === '/defaults' && m === 'GET') return reply(200, view());
    if (m !== 'POST') return problem(405, 'METHOD_NOT_ALLOWED', 'Không hỗ trợ thao tác này');
    const confirmed = ctx.body.confirm === true;
    const needConfirm = () => problem(422, 'VALIDATION', 'Dữ liệu chưa hợp lệ', { errors: { confirm: 'Sếp bấm Xác nhận giúp em trước khi Về mặc định' } });

    if (p === '/defaults/apply-standard') {
      if (!confirmed) return needConfirm();
      const hit = CORE_BINDINGS.map(find).filter((r): r is Row => !!r && r.customized);
      hit.forEach((r) => setCustomized(r, false));
      resets += 1;
      return reply(200, { removed: hit.length });
    }
    if (p === '/defaults/reset-all') {
      if (ctx.needPin()) return problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN', { detail: { operation: 'defaults.reset_all' } });
      if (!confirmed) return needConfirm();
      const hit = rows.filter((r) => r.resettable);
      hit.forEach((r) => setCustomized(r, false));
      resetKhoLabel();
      resets += 1;
      return reply(200, { reset: hit.length });
    }
    const one = /^\/defaults\/([^/]+)\/reset$/.exec(p);
    if (!one) return problem(404, 'NOT_FOUND', 'Không tìm thấy');
    const key = decodeURIComponent(one[1]);
    const r = find(key);
    if (!r) return problem(404, 'DEFAULTS_KEY_UNKNOWN', 'Em không có mục mặc định này — Sếp tải lại trang rồi thử lại');
    if (!r.resettable) return problem(409, 'DEFAULTS_NOT_RESETTABLE', 'Mục này chỉ để xem — Sếp đổi ở thẻ của nó (đổi nguồn model cần mã PIN)');
    if (!confirmed) return needConfirm();
    // Như API: nâng mức tự trị của tổ chức luôn cần phiên mã PIN, kể cả khi chỉ về mặc định riêng mục này.
    if (key === 'autonomy' && ctx.needPin()) return problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN', { detail: { operation: 'defaults.reset_all' } });
    if (key === 'kho_label') resetKhoLabel();
    setCustomized(r, false);
    resets += 1;
    return reply(200, { key, reset: true, customized: false, current_text: key === 'kho_label' ? khoText(KHO_DEFAULT) : r.current_text });
  }

  return {
    handle,
    hooks: {
      customize: (b: { keys?: string[] } = {}) => {
        for (const k of b.keys ?? []) {
          const r = find(k);
          if (r) setCustomized(r, true);
          if (k === 'kho_label') khoLabelState.label = 'Sổ tay Công ty';
        }
        return view();
      },
      state: () => ({ ...view(), resets }),
      resets: () => resets,
    } as unknown as Record<string, (...args: never[]) => unknown>,
    dispose: () => {},
  };
}
