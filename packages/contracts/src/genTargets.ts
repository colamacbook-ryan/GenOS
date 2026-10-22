/**
 * Gen v1 (docs/design/gen-v1.md §3.5) — nguồn sự thật cho màn Gen được mở và phần tử Gen được làm sáng.
 *
 * - `GEN_SCREENS`: khoá màn Gen được `navigate` tới (khoá screens.ts + trang `guide`, `account`) → đường dẫn.
 * - `GEN_TARGETS`: mỗi mục tiêu gắn bằng `data-gen-target="<id>"` trên đúng một phần tử React của màn `screen`.
 *   `dynamic: 'row'` = mục tiêu theo dòng, id thật là `<id>:<row id>`; row id PHẢI đến từ kết quả tool của chính lượt
 *   đó (server kiểm, chống bịa). `params` = trạng thái URL cần có để phần tử hiện ra (vd tab của Cài đặt).
 *   v0.1.42: id GIỮ NGUYÊN khi phần tử chuyển chỗ (chỉ đổi `screen`/`params`) để hội thoại cũ và test API không gãy.
 *
 * Kiểm chéo: `apps/web/test/unit/gen-targets.test.ts` quét `apps/web/src` (mỗi id có chỗ gắn và ngược lại) và so
 * với bản xuất cho API `apps/api/gh/gen/registry.json` (chạy `GEN_WRITE=1 npx vitest run gen-targets` để ghi lại).
 */
import { KHO_LABEL } from './gen';
import { SCREENS } from './screens';

export interface GenTarget {
  id: string;
  /** Khoá màn (GenScreenKey). */
  screen: string;
  /** Nhãn ngắn Gen dùng khi nói với Sếp. */
  label: string;
  /** Mô tả cho model: phần tử này làm gì. */
  description: string;
  dynamic?: 'row';
  /** Tham số URL cần có để phần tử hiện ra (vd `{ tab: 'brain' }`). */
  params?: Record<string, string>;
  /** Mục tiêu nhạy cảm (huỷ dữ liệu / bảo mật): server thay lời model bằng `GEN_SAFE_MESSAGE` cố định. */
  sensitive?: boolean;
  /** Quyền cần có (vd `roles.manage`) để Gen được chỉ vào mục tiêu này; server validator từ chối nếu thiếu. */
  permission?: string;
}

/** Lời nhắn cố định khi làm sáng mục tiêu nhạy cảm — chống prompt injection qua lời model. */
export const GEN_SAFE_MESSAGE = 'Đây là thao tác nhạy cảm — Sếp tự xem kỹ và tự quyết định, Gen không làm thay.';

export interface GenScreen {
  key: string;
  path: string;
  title: string;
}

/** Trang ngoài screens.ts mà Gen cũng mở được. */
const EXTRA_SCREENS: GenScreen[] = [
  { key: 'guide', path: '/guide', title: 'Hướng dẫn thiết lập' },
  { key: 'account', path: '/account', title: 'Tài khoản của tôi' },
  { key: 'help', path: '/help', title: 'Trợ giúp' },
  // v0.1.39 (F-32): trang Tài khoản mạng xã hội (Facebook) — chỉ Owner; v0.1.42 mở từ thẻ Facebook ở Kết nối.
  { key: 'social', path: '/social', title: 'Tài khoản mạng xã hội' },
  // v0.1.39 (F-74): "Việc Sếp cần làm" — 5 dòng kết nối chạy thật (trang con của Hướng dẫn thiết lập) — chỉ Owner.
  { key: 'boss_checks', path: '/guide/viec-sep', title: 'Việc Sếp cần làm' },
];

/** v0.1.42 (F-41): Plugin & Tiện ích đóng băng (ẩn khỏi thanh bên, route giữ) — Gen không mở tới đó. */
const GEN_EXCLUDED_SCREENS = new Set(['plugins']);

export const GEN_SCREENS: GenScreen[] = [
  ...SCREENS.filter((s) => !GEN_EXCLUDED_SCREENS.has(s.key)).map((s) => ({ key: s.key, path: `/${s.key}`, title: s.title })),
  ...EXTRA_SCREENS,
];

export const GEN_SCREEN_BY_KEY: Record<string, GenScreen> = Object.fromEntries(GEN_SCREENS.map((s) => [s.key, s]));

export const GEN_TARGETS: GenTarget[] = [
  // ── Hôm nay (overview) ──
  { id: 'overview.needs_boss', screen: 'overview', label: 'Dải "Cần Sếp xử lý"', description: 'Đầu trang Hôm nay: các sự cố Sếp cần tự tay xử lý (kênh rớt, model hết đăng nhập, cập nhật lỗi, sao lưu quá hạn…)', permission: 'system.manage' },
  { id: 'overview.setup_followup', screen: 'overview', label: 'Thẻ "Việc thiết lập tiếp"', description: 'Thẻ ở Tổng quan liệt kê các việc thiết lập tuỳ chọn còn lại (Zalo/WhatsApp, nhóm lắng nghe, sàng lọc, agent, đội, sao lưu, Facebook, Gen-hub); nút "Để sau 7 ngày" ẩn thẻ cùng các việc P3 của Gen', permission: 'system.manage' },
  { id: 'overview.kpis', screen: 'overview', label: 'Hàng chỉ số chính', description: 'Các ô số liệu đầu trang Hôm nay (cơ hội, cảnh báo, chờ duyệt…)' },
  { id: 'overview.queue', screen: 'overview', label: 'Hàng đợi cần xử lý', description: 'Khung liệt kê cơ hội, cảnh báo, bản nháp chờ duyệt, việc đến hạn' },
  { id: 'overview.queue.row', screen: 'overview', label: 'Một dòng hàng đợi', description: 'Một mục cụ thể trong Hàng đợi cần xử lý', dynamic: 'row' },
  { id: 'overview.queue.open_inbox', screen: 'overview', label: 'Nút "Mở hộp thư"', description: 'Mở Hộp thư để xem toàn bộ hàng đợi' },
  { id: 'overview.spotlight', screen: 'overview', label: '5 đối tượng đáng chú ý', description: 'Năm người/khách nổi bật nhất hôm nay' },
  { id: 'overview.health', screen: 'overview', label: 'Sức khoẻ hệ thống', description: 'Tình trạng kênh, nhóm lắng nghe, sự kiện hôm nay, độ trễ xử lý và tin chờ sàng lọc' },
  // v0.1.54 (Gen hướng dẫn): thẻ ở ĐẦU khung Gen — việc cần làm ngay, mẹo, bài học hôm nay (chỉ Owner). `screen: overview` để
  // vòng sáng đưa Sếp về Tổng quan; khung Gen vẫn mở nên thẻ hiện ngay.
  { id: 'gen.coach.card', screen: 'overview', label: 'Thẻ "Hôm nay của Sếp"', description: 'Đầu khung Gen: Việc cần làm ngay (tối đa 3), Sếp biết chưa? (một mẹo) và Bài học hôm nay (k/19) của Gen hướng dẫn', permission: 'system.manage' },
  // ── Hướng dẫn thiết lập ──
  { id: 'guide.progress', screen: 'guide', label: 'Thanh tiến độ', description: 'Bao nhiêu việc thiết lập đã xong' },
  { id: 'guide.item', screen: 'guide', label: 'Một việc thiết lập', description: 'Thẻ một việc thiết lập (theo số việc n)', dynamic: 'row' },
  { id: 'guide.item.do', screen: 'guide', label: 'Nút "Làm bước này"', description: 'Mở form làm việc thiết lập n', dynamic: 'row' },
  // v0.1.54: 9 dòng của "Việc Sếp cần làm" (/guide/viec-sep) — mỗi dòng một mục tiêu để Gen hướng dẫn chỉ đúng dòng.
  { id: 'boss_checks.row.ai', screen: 'boss_checks', label: 'Dòng 0 "Nguồn AI" (bắt buộc duy nhất)', description: 'Dòng 0 của Việc Sếp cần làm: có ít nhất 1 nguồn AI chạy được — bấm Kiểm tra hoặc mở Bộ não AI để thêm nguồn' },
  { id: 'boss_checks.row.hub', screen: 'boss_checks', label: 'Dòng 1 "Nối Gen-hub"', description: 'Dòng 1 của Việc Sếp cần làm: dán địa chỉ + token Gen-hub rồi bấm Kiểm tra (cần mã PIN)' },
  { id: 'boss_checks.row.facebook', screen: 'boss_checks', label: 'Dòng 2 "Kết nối Facebook"', description: 'Dòng 2 của Việc Sếp cần làm: chọn tài khoản Facebook đã đăng nhập rồi bấm Đọc ngay' },
  { id: 'boss_checks.row.agy', screen: 'boss_checks', label: 'Dòng 3 "Google (Antigravity)"', description: 'Dòng 3 của Việc Sếp cần làm: đăng nhập tài khoản Google rồi bấm Gọi thử' },
  { id: 'boss_checks.row.claude', screen: 'boss_checks', label: 'Dòng 4 "Claude Code CLI"', description: 'Dòng 4 của Việc Sếp cần làm: đăng nhập Claude Code rồi gọi thử' },
  { id: 'boss_checks.row.jev', screen: 'boss_checks', label: 'Dòng 5 "Jev" (không bắt buộc)', description: 'Dòng 5 của Việc Sếp cần làm: kiểm tra khoá Jev — không bắt buộc' },
  { id: 'boss_checks.row.telegram', screen: 'boss_checks', label: 'Dòng 6 "Telegram"', description: 'Dòng 6 của Việc Sếp cần làm: Gửi thử một tin Telegram báo động và bản tin' },
  { id: 'boss_checks.row.remote', screen: 'boss_checks', label: 'Dòng 7 "Truy cập từ xa"', description: 'Dòng 7 của Việc Sếp cần làm: mở Console từ điện thoại bằng địa chỉ truy cập từ xa rồi bấm Kiểm tra' },
  { id: 'boss_checks.row.facebook_reply', screen: 'boss_checks', label: 'Dòng 8 "Facebook trả lời" (không bắt buộc)', description: 'Dòng 8 của Việc Sếp cần làm: thử gửi một câu trả lời bình luận Facebook thật bằng Gen — không bắt buộc' },
  { id: 'boss_checks.row.kho_write', screen: 'boss_checks', label: 'Dòng 9 "Gen ghi Kho" (không bắt buộc)', description: `Dòng 9 của Việc Sếp cần làm: duyệt đề xuất ghi Phiên đầu tiên của Gen vào ${KHO_LABEL} — không bắt buộc` },
  // ── Cài đặt (v0.1.42; trước là Điều khiển hệ thống) ──
  { id: 'system.tab.brain', screen: 'system', label: 'Tab "Bộ não AI"', description: 'Chuyển sang tab nhà cung cấp model, hạn mức, Jev' },
  { id: 'system.tab.org', screen: 'system', label: 'Tab "Tổ chức"', description: 'Chuyển sang tab sửa tên tổ chức, múi giờ, tiền tệ, xưng hô' },
  { id: 'system.tab.storage', screen: 'system', label: 'Tab "Sao lưu & cập nhật"', description: 'Chuyển sang tab sao lưu & khôi phục, cập nhật phần mềm, hạn lưu dữ liệu' },
  // v0.1.42 (F-7): kênh và Facebook ở Kết nối (id giữ nguyên); thẻ mã PIN chỉ còn ở Tài khoản của tôi (account.pin).
  { id: 'system.channels.list', screen: 'connections', label: 'Danh sách kênh', description: 'Thẻ các kênh Zalo/WhatsApp/Telegram và nút tạo mã QR' },
  { id: 'system.channels.facebook', screen: 'connections', label: 'Thẻ Facebook', description: 'Mở trang Tài khoản mạng xã hội (chỉ Owner)', permission: 'roles.manage' },
  // v0.1.54: Telegram (báo động & bản tin) ở Kết nối — thẻ và nút Gửi thử (chỉ Owner thấy thẻ).
  { id: 'system.channels.telegram', screen: 'connections', label: 'Thẻ Telegram', description: 'Telegram nhận báo động sự cố và bản tin 07:30/17:30: token bot, chat_id, công tắc bản tin và nhắc việc (chỉ Owner)', permission: 'system.manage' },
  { id: 'system.channels.telegram.test', screen: 'connections', label: 'Nút "Gửi thử" Telegram', description: 'Gửi thử một tin Telegram để chắc tin tới được điện thoại', permission: 'system.manage' },
  { id: 'connections.brain', screen: 'connections', label: 'Thẻ "Bộ não AI"', description: 'Trạng thái bộ não AI (model, khoá API, tài khoản CLI) và nút mở Bộ não AI ở Cài đặt' },
  { id: 'system.brain.quota', screen: 'system', label: 'Hạn mức theo model', description: 'Bảng dùng trong ngày / còn lại của từng model', params: { tab: 'brain' } },
  // v0.1.50 (F-81, QD-18): "Gen nhớ" — quy ước, sở thích Sếp đã xác nhận (chỉ Owner thấy thẻ).
  { id: 'system.brain.memory', screen: 'system', label: 'Thẻ "Gen nhớ"', description: 'Các quy ước, sở thích Sếp đã xác nhận để Gen nhớ (tối đa 30 ghi chú): xem, sửa tại chỗ, xoá', params: { tab: 'brain' }, permission: 'system.manage' },
  { id: 'system.brain.chain', screen: 'system', label: 'Chuỗi chuyển hướng', description: 'Thứ tự nhà cung cấp model khi một nơi lỗi', params: { tab: 'brain' } },
  { id: 'system.brain.open_api', screen: 'system', label: 'Nút "Mở API & Model"', description: 'Sang màn thêm nhà cung cấp, khoá API, gán model', params: { tab: 'brain' } },
  { id: 'system.ai_cost', screen: 'system', label: 'Thẻ "Chi phí & trần ngân sách"', description: 'Trần chi phí AI mỗi ngày (₫) và bảng giá theo model', params: { tab: 'brain' } },
  // v0.1.54 (Gen hướng dẫn): công tắc, chuông nhắc, số bài mỗi ngày, giờ yên lặng, danh sách việc đã chọn không dùng (chỉ Owner).
  { id: 'system.brain.coach', screen: 'system', label: 'Thẻ "Gen hướng dẫn"', description: 'Bật/tắt Gen hướng dẫn, chuông nhắc, số bài học mỗi ngày (0–2), giờ yên lặng và danh sách việc Sếp đã chọn không dùng (có nút Bật lại)', params: { tab: 'brain' }, permission: 'system.manage' },
  { id: 'system.brain.jev', screen: 'system', label: 'Thẻ Jev (System One)', description: 'Cấu hình nguồn model quyết định nhanh Jev', params: { tab: 'brain' } },
  { id: 'system.brain.jev.test', screen: 'system', label: 'Nút "Kiểm tra 1 lần" Jev', description: 'Gọi thử Jev một lần để biết khoá và địa chỉ đúng chưa', params: { tab: 'brain' } },
  { id: 'system.brain.jev.enable', screen: 'system', label: 'Nút "Bật Jev" (1 chạm)', description: 'Bật Jev bằng khoá OpenRouter đang có hoặc dán khoá mới (cần mã PIN); sau đó bấm "Thử 12 câu mẫu"', params: { tab: 'brain' }, permission: 'system.manage' },
  { id: 'system.brain.triage', screen: 'system', label: 'Thẻ "Lọc tin"', description: 'Bật/tắt lọc tin, chọn mức Thấp/Vừa/Cao; Jev và ngưỡng số ở Nâng cao (chỉ Owner sửa)', params: { tab: 'brain' } },
  { id: 'system.storage.health', screen: 'system', label: 'Sức khoẻ hệ thống', description: 'Bộ xử lý nền, Trình duyệt nền, hàng lỗi, sao lưu, cập nhật, ổ đĩa; chi tiết kỹ thuật lịch chạy', params: { tab: 'storage' } },
  { id: 'system.storage.retention', screen: 'system', label: 'Hạn lưu dữ liệu', description: 'Mỗi tập dữ liệu giữ bao lâu', params: { tab: 'storage' } },
  { id: 'system.remote_access', screen: 'system', label: 'Thẻ "Truy cập từ xa"', description: 'Cách nhân viên và điện thoại mở Console: địa chỉ truy cập, chế độ (Tailscale / mạng nội bộ) và lệnh genh remote', params: { tab: 'storage' } },
  { id: 'system.backup.panel', screen: 'system', label: 'Sao lưu & khôi phục', description: 'Danh sách bản sao lưu, tải về, khôi phục', params: { tab: 'storage' }, sensitive: true },
  { id: 'system.backup.now', screen: 'system', label: 'Nút "Sao lưu ngay"', description: 'Tạo bản sao lưu ngay lúc này', params: { tab: 'storage' }, sensitive: true },
  { id: 'system.backup.schedule', screen: 'system', label: 'Lịch sao lưu tự động', description: 'Đổi tần suất và giờ sao lưu tự động', params: { tab: 'storage' }, sensitive: true },
  { id: 'system.storage.offsite', screen: 'system', label: 'Bản sao ngoài máy', description: 'Bản sao dữ liệu ra ổ USB/NAS cắm vào máy chủ: lần gần nhất, cảnh báo quá 7 ngày, nơi lưu, lịch hằng tuần', params: { tab: 'storage' }, sensitive: true },
  { id: 'system.offsite.choose', screen: 'system', label: 'Nút "Chọn nơi lưu bản sao ngoài máy"', description: 'Chọn đường dẫn ổ USB/NAS trên máy chủ để lưu bản sao ngoài máy (chỉ Owner, cần mã PIN)', params: { tab: 'storage' }, sensitive: true, permission: 'roles.manage' },
  { id: 'system.offsite.run', screen: 'system', label: 'Nút "Sao lưu ra ổ ngoài ngay"', description: 'Xuất bản sao ra ổ ngoài ngay lúc này', params: { tab: 'storage' }, sensitive: true, permission: 'system.manage' },
  { id: 'system.offsite.portable', screen: 'system', label: 'Nút "Tải gói mang đi"', description: 'Tải gói dữ liệu mã hoá về máy đang dùng (chỉ Owner, cần mã PIN)', params: { tab: 'storage' }, sensitive: true, permission: 'roles.manage' },
  { id: 'system.offsite.kit', screen: 'system', label: 'Nút "Bộ khôi phục"', description: 'Xem/in khoá khôi phục để mở bản sao ngoài máy (chỉ Owner, cần mã PIN)', params: { tab: 'storage' }, sensitive: true, permission: 'roles.manage' },
  // v0.1.42 (F-7): người dùng ở Đội ngũ (id giữ nguyên).
  { id: 'system.users.list', screen: 'team', label: 'Danh sách người dùng', description: 'Tên, email, vai trò, trạng thái, lần đăng nhập gần nhất; nút đặt lại mật khẩu, khoá/mở khoá', sensitive: true, permission: 'roles.manage' },
  { id: 'system.users.invite', screen: 'team', label: 'Nút "Mời người dùng"', description: 'Tạo tài khoản mới với mật khẩu tạm (cần mã PIN)', permission: 'roles.manage' },
  { id: 'system.users.temp_password', screen: 'team', label: 'Mật khẩu tạm vừa tạo', description: 'Hộp hiện mật khẩu tạm một lần sau khi mời / đặt lại mật khẩu', sensitive: true, permission: 'roles.manage' },
  { id: 'system.org.form', screen: 'system', label: 'Thông tin tổ chức', description: 'Tên tổ chức, múi giờ, tiền tệ, Sếp tự xưng là, Agent gọi Sếp là', params: { tab: 'org' } },
  { id: 'system.org.save', screen: 'system', label: 'Nút "Lưu thông tin tổ chức"', description: 'Lưu thay đổi thông tin tổ chức và xưng hô', params: { tab: 'org' } },
  // ── Việc & Nhắc hẹn / Hộp thư / Bàn làm việc (Gen v2 — A4: đề xuất có xác nhận gắn với các mục tiêu này) ──
  { id: 'tasks.new', screen: 'tasks', label: 'Nút "Tạo việc mới"', description: 'Tạo việc hoặc nhắc hẹn mới (tiêu đề, ưu tiên, hạn)', permission: 'queue.act' },
  { id: 'tasks.row', screen: 'tasks', label: 'Một dòng việc', description: 'Một việc cụ thể trong Danh sách việc (theo id việc)', dynamic: 'row' },
  { id: 'inbox.hide_junk', screen: 'inbox', label: 'Bộ lọc "Ẩn rác & trùng"', description: 'Ẩn các mục trùng, rác hoặc điểm dưới ngưỡng khỏi Hộp thư' },
  { id: 'inbox.row', screen: 'inbox', label: 'Một mục hộp thư', description: 'Một thẻ trong Hộp thư (theo id mục)', dynamic: 'row' },
  { id: 'workbench.drafts', screen: 'workbench', label: 'Bản nháp chờ duyệt', description: 'Danh sách bản nháp tin gửi đi đang chờ Sếp duyệt', permission: 'action.draft', sensitive: true },
  // ── API & Model ──
  { id: 'api.add_provider', screen: 'api', label: 'Nút "Thêm nhà cung cấp"', description: 'Thêm Gemini/DeepSeek/API tương thích OpenAI' },
  { id: 'api.bindings', screen: 'api', label: 'Gán model cho từng agent', description: 'Chọn model cho từng mục đích, gồm core.gen của Gen' },
  // ── Gen-hub (v0.1.26, Đợt D1: Gen đọc Kho dữ liệu, chỉ Owner; v0.1.50 ghi Kho qua Xác nhận + PIN) — v0.1.42: thẻ ở Kết nối (id giữ nguyên) ──
  { id: 'mcp.hub_link', screen: 'connections', label: 'Thẻ "Gen-hub"', description: `Nối Gen-hub để Gen đọc ${KHO_LABEL}, lịch, mail, việc, Drive (đọc; ghi Kho chỉ khi Sếp Xác nhận + mã PIN; chỉ Sếp thao tác): địa chỉ, token, hạn token, trạng thái`, permission: 'system.manage' },
  { id: 'mcp.hub_link.token', screen: 'connections', label: 'Ô "Token Gen-hub"', description: 'Dán token agent tạo trong Gen-hub (chỉ ghi — không bao giờ hiện lại)', permission: 'system.manage', sensitive: true },
  { id: 'mcp.hub_link.test', screen: 'connections', label: 'Nút "Kiểm tra" Gen-hub', description: 'Thử kết nối, mở đúng tool đọc Kho cho Gen rồi bật liên kết (cần PIN)', permission: 'system.manage' },
  // ── Tài khoản của tôi ──
  { id: 'account.profile', screen: 'account', label: 'Hồ sơ', description: 'Đổi tên hiển thị, email' },
  { id: 'account.password', screen: 'account', label: 'Đổi mật khẩu', description: 'Đặt mật khẩu mới', sensitive: true },
  { id: 'account.pin', screen: 'account', label: 'Đổi mã PIN', description: 'Đặt mã PIN 6 số mới', sensitive: true },
  { id: 'account.sessions', screen: 'account', label: 'Phiên đăng nhập', description: 'Thiết bị đang đăng nhập, đăng xuất thiết bị khác', sensitive: true },
  // ── Trợ giúp ──
  { id: 'help.version', screen: 'help', label: 'Phiên bản đang chạy', description: 'Phiên bản Gen-Harness, tổ chức, múi giờ, vai trò' },
  { id: 'help.ask_gen', screen: 'help', label: 'Cách hỏi Gen', description: 'Hướng dẫn mở khung Gen và câu hỏi mẫu' },
  { id: 'help.guide', screen: 'help', label: 'Nút "Mở Hướng dẫn thiết lập"', description: 'Sang trang hướng dẫn các việc thiết lập để sau (Owner)' },
  { id: 'help.curriculum', screen: 'help', label: 'Thẻ "Lộ trình học cùng Gen"', description: '19 bài học của Gen hướng dẫn kèm trạng thái: xem nội dung, Làm thử, Học lại (chỉ Owner)', permission: 'system.manage' },
  { id: 'help.genh', screen: 'help', label: 'Lệnh genh hay dùng', description: 'genh update, reset-password, trust-ca, backup, status — chạy trên máy chủ' },
  { id: 'help.report', screen: 'help', label: 'Nút "Báo lỗi"', description: 'Chép thông tin chẩn đoán (phiên bản, trang, trình duyệt) để gửi người hỗ trợ' },
  // ── Quy tắc sàng lọc ──
  { id: 'rules.add', screen: 'rules', label: 'Nút "Thêm quy tắc"', description: 'Tạo quy tắc sàng lọc mới' },
  { id: 'rules.batch', screen: 'rules', label: 'Nút "Chạy thử trên 100 bản ghi"', description: 'Thử bộ quy tắc trên dữ liệu gần nhất' },
  { id: 'rules.list', screen: 'rules', label: 'Danh sách quy tắc', description: 'Các quy tắc sàng lọc đang có' },
  { id: 'rules.weights', screen: 'rules', label: 'Trọng số chấm điểm', description: 'Kéo thanh để đổi trọng số các chiều chấm điểm' },
  { id: 'rules.tryone', screen: 'rules', label: 'Chạy thử một bản ghi', description: 'Dán một tin để xem quy tắc nào khớp' },
  // ── Danh tính agent ──
  { id: 'agents.add', screen: 'agents', label: 'Nút "Tạo agent mới"', description: 'Tạo danh tính agent mới' },
  { id: 'agents.clone', screen: 'agents', label: 'Nút "Nhân bản"', description: 'Nhân bản một agent có sẵn' },
  { id: 'agents.list', screen: 'agents', label: 'Danh sách agent', description: 'Các agent đang có, mức tự trị, kênh' },
  { id: 'agents.diplomat', screen: 'agents', label: 'Ô "Agent ngoại giao"', description: 'Chọn agent nói thay Gen ở mọi kênh ngoài Console' },
  { id: 'agents.decisions', screen: 'agents', label: 'Agent đã nói gì', description: 'Nhật ký quyết định của agent' },
  { id: 'agents.templates', screen: 'agents', label: 'Mẫu có sẵn', description: 'Mẫu agent tạo nhanh' },
];

export const GEN_TARGET_BY_ID: Record<string, GenTarget> = Object.fromEntries(GEN_TARGETS.map((t) => [t.id, t]));

/** `overview.queue.row:0192…` → { base: 'overview.queue.row', row: '0192…' }; id tĩnh → row null. */
export function splitTargetId(id: string): { base: string; row: string | null } {
  const i = id.indexOf(':');
  return i < 0 ? { base: id, row: null } : { base: id.slice(0, i), row: id.slice(i + 1) };
}

/** Mục tiêu (tĩnh hoặc động) có trong registry không. */
export function resolveTarget(id: string): GenTarget | null {
  const { base, row } = splitTargetId(id);
  const t = GEN_TARGET_BY_ID[base];
  if (!t) return null;
  if ((t.dynamic === 'row') !== (row !== null)) return null;
  return t;
}
