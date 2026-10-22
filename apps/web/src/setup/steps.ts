import type { SetupState } from '@gen-harness/contracts';

/** The 12 setup steps — docs/handoff/06 "12 bước". */
export interface StepMeta {
  n: number;
  key: string;
  title: string;
  required: boolean;
  /** Nội dung (what the step asks for). */
  content: string;
  /** Hoàn thành khi (completion condition). */
  doneWhen: string;
  /** Implemented in the web (phase 1: 1–3, phase 2: 4–7 and 12). */
  built: boolean;
}

export const SETUP_STEPS: StepMeta[] = [
  {
    n: 1, key: 'welcome', title: 'Chào mừng', required: true, built: true,
    content: 'Nhập mã thiết lập từ trình cài (tự điền nếu có trong đường dẫn).',
    doneWhen: 'Mã hợp lệ',
  },
  {
    n: 2, key: 'owner', title: 'Tài khoản Owner', required: true, built: true,
    content: 'Tên hiển thị, email, mật khẩu (ít nhất 12 ký tự) và mã PIN 6 số nhập hai lần. PIN bảo vệ mọi thao tác nhạy cảm.',
    doneWhen: 'Tài khoản tạo xong, đăng nhập tự động',
  },
  {
    n: 3, key: 'org', title: 'Tổ chức & xưng hô', required: true, built: true,
    content: 'Tên tổ chức (ô duy nhất phải gõ); múi giờ, tiền tệ và hai ô xưng hô điền sẵn — xem trước một câu trả lời mẫu cập nhật trực tiếp.',
    doneWhen: 'Lưu',
  },
  {
    n: 4, key: 'brain', title: 'Bộ não AI', required: false, built: true,
    content: 'Chọn nguồn AI cho hệ thống: Antigravity CLI (đăng nhập Google rồi dán mã xác thực) và/hoặc khoá API (Gemini, DeepSeek, tương thích OpenAI). Mỗi khoá có nút Kiểm tra gọi thử một lượt, hiện độ trễ và model khả dụng. Sắp thứ tự chuỗi chuyển hướng.',
    doneWhen: 'Ít nhất một provider kiểm tra OK',
  },
  {
    n: 5, key: 'channels', title: 'Kết nối kênh', required: false, built: true,
    content: 'Thẻ từng kênh. Zalo/WhatsApp: tạo mã QR, đếm ngược 60 giây tự làm mới, chờ quét → đã quét → đồng bộ danh sách nhóm. Cảnh báo rủi ro tài khoản cá nhân trước khi hiện QR. Telegram và kênh khác chưa có trong bản này — thẻ ghi rõ, không có nút cài.',
    doneWhen: 'Ít nhất một kênh đang hoạt động · hoặc Để sau (hiện ở Cài đặt › Việc nên làm tiếp)',
  },
  {
    n: 6, key: 'groups', title: 'Chọn nhóm lắng nghe', required: false, built: true,
    content: 'Bảng nhóm vừa đồng bộ. Mỗi nhóm chọn chế độ: Không nghe · Nghe hết, chỉ trả lời khi được tag · Lắng nghe im lặng · Chủ động bắt tín hiệu, và phạm vi xem. Nhóm mới mặc định vào Lắng nghe im lặng (lưu hết tin, không bao giờ trả lời, kể cả khi được tag) — Sếp đổi từng nhóm; có nút Cập nhật danh sách nhóm.',
    doneWhen: 'Ít nhất một nhóm được bật · hoặc Để sau (hiện ở Cài đặt › Việc nên làm tiếp)',
  },
  {
    n: 7, key: 'refinery', title: 'Sàng lọc dữ liệu', required: false, built: true,
    content: 'Chỉ hỏi một câu: Sếp làm ngành nào (chọn bộ quy tắc khởi đầu). Lịch sàng lọc 900 giây / 500 tin / lô 250 / tin cậy ≥ 0,6 đã điền sẵn; chu kỳ, ngưỡng, từng quy tắc và trọng số chấm điểm (tổng 100%) chỉnh ở mục Nâng cao.',
    doneWhen: 'Lưu · hoặc Để sau (hiện ở Cài đặt › Việc nên làm tiếp)',
  },
  {
    n: 8, key: 'agent', title: 'Agent đầu tiên', required: false, built: true,
    content: 'Chọn mẫu (Trợ lý thương mại, Khách hàng lớn, Hậu cần, Chăm sóc khách hàng, Tuyển dụng, Thư ký cá nhân) hoặc tạo trống — chọn mẫu là xong, tên và vai trò điền sẵn theo mẫu. Gán kênh/nhóm và chỉnh giọng nói làm sau ở Danh tính Agent.',
    doneWhen: 'Agent được lưu · hoặc Để sau (hiện ở Cài đặt › Việc nên làm tiếp)',
  },
  {
    n: 9, key: 'autonomy', title: 'Tự trị & ranh giới', required: false, built: true,
    content: 'Mức tự trị (Gợi ý hoặc Soạn sẵn chờ duyệt), mặc định Soạn sẵn chờ duyệt; ngưỡng tiền phải duyệt mặc định 50.000.000 ₫. Danh sách ranh giới khoá cứng kèm một dòng ghi chú "bấm Tiếp tục là đã đọc" — không còn ô tích.',
    doneWhen: 'Lưu · hoặc Để sau (hiện ở Cài đặt › Việc nên làm tiếp)',
  },
  {
    n: 10, key: 'team', title: 'Mời đội ngũ', required: false, built: true,
    content: 'Thẻ gợi ý có nút "Để sau": Sếp một mình dùng được ngay. Muốn mời luôn thì bấm "Mời ngay", thêm email và vai trò (Quản lý, Vận hành, Nhân viên phụ trách, Kiểm soát) — mật khẩu tạm hiện ngay để Sếp tự gửi.',
    doneWhen: 'Để sau được — hiện ở Cài đặt › Việc nên làm tiếp',
  },
  {
    n: 11, key: 'backup', title: 'Sao lưu', required: false, built: true,
    content: 'Sao lưu tự bật hằng ngày lúc 02:00, giữ 7 bản theo ngày, 4 theo tuần, 12 theo tháng; nơi lưu trên máy chủ (bản sao ra ổ USB/NAS chọn ở Cài đặt › Sao lưu & cập nhật). Đổi giờ hoặc tần suất ở mục "Đổi lịch" nếu muốn.',
    doneWhen: 'Đi qua hoặc Để sau đều bật lịch mặc định',
  },
  {
    n: 12, key: 'finish', title: 'Hoàn tất', required: true, built: true,
    content: 'Tóm tắt những gì đã bật. Tiến độ lần sàng lọc đầu tiên theo thời gian thực: bản ghi thô đã gom · đang phân loại · đã vào kho sạch. Nút Vào Console (mở trang chủ theo vai trò — Owner là Mặt tiền).',
    doneWhen: 'Bấm nút',
  },
];

/** Description line under each step title (right pane). */
export const STEP_DESCRIPTIONS: Record<number, string> = {
  1: 'Mã thiết lập chứng minh Sếp là người vừa chạy trình cài trên máy này.',
  2: 'Tài khoản Owner thấy toàn cảnh. Mật khẩu để đăng nhập, mã PIN để xác nhận thao tác nhạy cảm.',
  3: 'Thông tin tổ chức và cách agent xưng hô với Sếp trong mọi tin nhắn.',
  4: 'Hệ thống cần ít nhất một nguồn AI để đọc và hiểu tin nhắn: đăng nhập tài khoản Google (Antigravity CLI) hoặc dán khoá API. Bấm Kiểm tra, chọn model, rồi xếp thứ tự dùng khi một nguồn hết hạn mức. Để sau được, nhưng khi chưa có model thì Gen và sàng lọc tin chưa chạy.',
  5: 'Kết nối ít nhất một kênh để hệ thống bắt đầu gom tin. Zalo và WhatsApp đăng nhập bằng mã QR trên điện thoại của Sếp.',
  6: 'Mọi nhóm vừa đồng bộ mặc định vào “Lắng nghe im lặng” — Gen lưu hết tin nhưng không bao giờ trả lời, kể cả khi được tag. Sếp đổi nhóm nào cần Gen trả lời khi được tag, hoặc chọn Không nghe (cột “Phạm vi xem” hiện mới là ghi chú ý Sếp — quyền xem thật theo vai trò); thiếu nhóm vừa vào thì bấm Cập nhật danh sách nhóm.',
  7: 'Em dùng sẵn lịch lọc tin mặc định (mỗi 15 phút hoặc đủ 500 tin) — Sếp chỉ cần cho em biết ngành để chọn bộ quy tắc hợp. Chỉnh chi tiết ở mục Nâng cao hoặc ở Quy tắc sàng lọc sau.',
  8: 'Tạo trợ lý AI đầu tiên: chọn một mẫu là xong — tên và vai trò điền sẵn theo mẫu, sửa lại ở Danh tính Agent bất cứ lúc nào.',
  9: 'Mức trợ lý được tự làm đã chọn sẵn: Soạn sẵn chờ duyệt. Những giới hạn bên dưới luôn bật để bảo vệ Sếp và nhân viên — không ai tắt được.',
  10: 'Gợi ý, không bắt buộc — Sếp một mình dùng được ngay. Có người cùng làm thì mời lúc nào cũng được ở Đội ngũ › Người dùng; hệ thống chưa tự gửi email mời, mật khẩu tạm hiện ngay để Sếp tự gửi.',
  11: 'Sao lưu đã tự bật: hằng ngày lúc 02:00, giữ 7 bản theo ngày, 4 theo tuần, 12 theo tháng. Đi tiếp hay bấm Để sau thì lịch này vẫn chạy — đổi giờ ở mục "Đổi lịch" nếu muốn.',
  12: 'Kiểm tra lại những gì đã bật. Việc còn thiếu có liên kết để làm tiếp — làm ngay hoặc sau ở Cài đặt › Việc nên làm tiếp.',
};

/** When the PIN is asked for — design `pinRules` (Tài khoản của tôi › Mã PIN). */
export const PIN_RULES: Array<[string, string]> = [
  ['yêu cầu PIN khi', 'đăng xuất kênh, đổi tài khoản CLI, cài plugin, đổi quyền'],
  ['hết hạn phiên PIN', 'sau 30 phút không thao tác'],
  ['nhập sai 5 lần', 'khoá Console 15 phút và báo qua kênh chat khi đã kết nối'],
  ['ghi nhật ký', 'mọi lần nhập, kể cả sai, đều vào Nhật ký hành động'],
];

export const TIMEZONES = [
  { value: 'Asia/Ho_Chi_Minh', label: 'Asia/Ho_Chi_Minh (GMT+7)' },
  { value: 'Asia/Bangkok', label: 'Asia/Bangkok (GMT+7)' },
  { value: 'Asia/Singapore', label: 'Asia/Singapore (GMT+8)' },
  { value: 'Asia/Tokyo', label: 'Asia/Tokyo (GMT+9)' },
  { value: 'Australia/Sydney', label: 'Australia/Sydney (GMT+10/11)' },
  { value: 'Europe/London', label: 'Europe/London (GMT+0/1)' },
  { value: 'America/Los_Angeles', label: 'America/Los_Angeles (GMT−8/−7)' },
  { value: 'UTC', label: 'UTC' },
];

export const CURRENCIES = [
  { value: 'VND', label: 'VND — Đồng Việt Nam (₫)' },
  { value: 'USD', label: 'USD — Đô la Mỹ ($)' },
  { value: 'EUR', label: 'EUR — Euro (€)' },
  // v0.1.28 (UX V13): đủ 8 loại máy chủ nhận (gh.setup.routes.CURRENCIES) — bước 3 và tab Tổ chức dùng chung danh sách.
  { value: 'JPY', label: 'JPY — Yên Nhật (¥)' },
  { value: 'SGD', label: 'SGD — Đô la Singapore (S$)' },
  { value: 'THB', label: 'THB — Baht Thái (฿)' },
  { value: 'CNY', label: 'CNY — Nhân dân tệ (¥)' },
  { value: 'KRW', label: 'KRW — Won Hàn Quốc (₩)' },
];

// ─── v0.1.59 (g5-thiet-lap) — Thiết lập MỘT màn, 3 bước ────────────────────────────────────────────────────────────
// Máy chủ vẫn có 12 bước (chỉ 1, 2, 3 và 12 bắt buộc); giao diện gom thành: 1 Tài khoản (PUT 1 → 2 → 3) · 2 Nguồn AI (PUT 4)
// · 3 Xong (PUT 12). Bước 5–11 làm sau ở Hướng dẫn thiết lập (/guide/5…/guide/11). Mọi hàm dưới đây là hàm THUẦN.

export type SetupView = 'tai-khoan' | 'nguon-ai' | 'xong';

/** Thanh bước "1 Tài khoản · 2 Nguồn AI · 3 Xong". */
export const SETUP_VIEWS: ReadonlyArray<{ key: SetupView; n: 1 | 2 | 3; label: string }> = [
  { key: 'tai-khoan', n: 1, label: 'Tài khoản' },
  { key: 'nguon-ai', n: 2, label: 'Nguồn AI' },
  { key: 'xong', n: 3, label: 'Xong' },
];

/** Đích DUY NHẤT của mọi nút "Chọn model" / "Đổi nguồn AI" (kể cả việc nên làm tiếp của Gen hướng dẫn `model.missing`). */
export const AI_SOURCE_PATH = '/setup?buoc=nguon-ai';

/** Khoá sessionStorage: Sếp đã bấm "Làm sau" ở bước Nguồn AI (chỉ trong phiên trình duyệt này). */
export const AI_LATER_KEY = 'gh_setup_ai_later';

export const setupViewPath = (view: SetupView): string => `/setup?buoc=${view}`;

/** Giá trị `?buoc=` hợp lệ; còn lại ⇒ null. */
export function parseView(v: string | null | undefined): SetupView | null {
  return v === 'tai-khoan' || v === 'nguon-ai' || v === 'xong' ? v : null;
}

/**
 * Ánh xạ `?step=` cũ (12 bước): 1–3 → tai-khoan · 4 → nguon-ai · 12 → xong · 5–11 → 'guide' (mở /guide/n khi đã xong;
 * chưa xong thì về bước hiện tại). Giá trị ngoài 1–12 ⇒ null.
 */
export function legacyStepView(step: number): SetupView | 'guide' | null {
  if (!Number.isInteger(step)) return null;
  if (step >= 1 && step <= 3) return 'tai-khoan';
  if (step === 4) return 'nguon-ai';
  if (step >= 5 && step <= 11) return 'guide';
  return step === 12 ? 'xong' : null;
}

const stepStatus = (state: SetupState, n: number) => state.steps.find((s) => s.n === n)?.status;

/** Bước 1–3 (mã thiết lập, tài khoản Owner, tổ chức) đã xong. */
export function accountReady(state: SetupState): boolean {
  return state.finished || state.current_step >= 4 || [1, 2, 3].every((n) => stepStatus(state, n) === 'done');
}

/** Bước 4 (nguồn AI) đã xong THẬT (có nguồn chạy được) — khác `aiSettled` (cả "để sau"). Bước Xong dùng để nói "Gen đã sẵn sàng". */
export function aiRunning(state: SetupState): boolean {
  return stepStatus(state, 4) === 'done';
}

/** Bước 4 (nguồn AI) đã xong hoặc đã được để sau ở máy chủ. */
export function aiSettled(state: SetupState): boolean {
  const st = stepStatus(state, 4);
  return st === 'done' || st === 'skipped';
}

/**
 * Bước đang làm của một thiết lập CHƯA xong: 1–3 chưa đủ → tai-khoan; đủ 1–3 → nguon-ai, trừ khi bước 4 đã xong hoặc Sếp đã
 * chọn "Làm sau" (`aiLater`, sessionStorage) → xong.
 */
export function defaultView(state: SetupState, aiLater: boolean): SetupView {
  if (!accountReady(state)) return 'tai-khoan';
  return aiSettled(state) || aiLater ? 'xong' : 'nguon-ai';
}

export type SetupRoute = { kind: 'view'; view: SetupView } | { kind: 'guide'; n: number } | { kind: 'owner' };

/**
 * Nơi trang `/setup` phải ở, theo `?buoc=` (mới) hoặc `?step=` (cũ) và trạng thái máy chủ.
 * - Chưa xong: chỉ bước hiện tại (hoặc quay lại Nguồn AI từ bước Xong); bước xa hơn/không hợp lệ ⇒ bước hiện tại;
 *   `?step=5..11` ⇒ bước hiện tại.
 * - Đã xong: chỉ `nguon-ai` mở được (máy chủ cho lưu bước 4 sau Hoàn tất); `?step=5..11` ⇒ /guide/n; còn lại ⇒ /owner.
 */
export function resolveSetupRoute(i: { buoc?: string | null; step?: string | null; state: SetupState; aiLater: boolean }): SetupRoute {
  const stepNo = i.step ? Number(i.step) : NaN;
  const requested = parseView(i.buoc) ?? (i.step ? legacyStepView(stepNo) : null);
  if (i.state.finished) {
    if (requested === 'nguon-ai') return { kind: 'view', view: 'nguon-ai' };
    if (requested === 'guide') return { kind: 'guide', n: stepNo };
    return { kind: 'owner' };
  }
  const current = defaultView(i.state, i.aiLater);
  if (requested && requested !== 'guide' && (requested === current || (current === 'xong' && requested === 'nguon-ai'))) {
    return { kind: 'view', view: requested };
  }
  return { kind: 'view', view: current };
}
