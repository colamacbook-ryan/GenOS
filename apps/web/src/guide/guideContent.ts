import { KHO_LABEL } from '@gen-harness/contracts';
import { AI_SOURCE_PATH } from '../setup/steps';

/**
 * Hướng dẫn thiết lập: các việc tuỳ chọn 5–11 của trình thiết lập + (v0.1.39) 13 Kết nối Facebook, 14 Nối Gen-hub. Chữ viết cho Owner không rành kỹ
 * thuật: vì sao cần, chuẩn bị gì, bấm gì theo đúng nhãn nút trên màn hình, và làm sao biết đã xong. Nút "Làm bước
 * này" mở đúng form của trình thiết lập ngay trong Console (`/guide/:n`), kể cả sau khi đã bấm Hoàn tất.
 */
/** Khoá cache của `GET /setup/follow-up` — dùng chung cho thẻ Tổng quan và trang Hướng dẫn thiết lập. */
export const FOLLOW_UP_KEY = ['setup', 'follow-up'] as const;

export interface GuideItem {
  n: number;
  title: string;
  /** Một câu: làm việc này để được gì. */
  why: string;
  /** Cần chuẩn bị trước khi bắt đầu. */
  prepare: string[];
  /** Các bước bấm theo thứ tự, dùng đúng nhãn nút trên màn hình. */
  steps: string[];
  /** Dấu hiệu đã xong (hệ thống tự nhận ra, không cần bấm tay). */
  doneWhen: string;
  /** Làm ở màn Console đầy đủ (chỉnh chi tiết hơn form hướng dẫn). */
  console: { label: string; to: string };
  /** Việc nên làm trước (số bước). */
  after?: number;
  /** v0.1.39: việc không có form trình thiết lập — nút chính mở thẳng màn này (không mở `/guide/:n`). */
  doTo?: string;
  /** Nhãn nút chính khi có `doTo`. */
  doLabel?: string;
}

export const GUIDE: GuideItem[] = [
  {
    n: 5,
    title: 'Kết nối Zalo / WhatsApp',
    why: 'Để agent đọc được tin nhắn trong các nhóm chat của Sếp — không có kênh thì hệ thống chưa có dữ liệu để làm việc.',
    prepare: ['Điện thoại đang đăng nhập Zalo hoặc WhatsApp của chính Sếp.', 'Máy tính và điện thoại đều có mạng.'],
    steps: [
      'Bấm "Làm bước này" bên dưới.',
      'Ở thẻ Zalo (hoặc WhatsApp), bấm "Tạo mã QR".',
      'Đọc cảnh báo, tích "Tôi hiểu rủi ro…" rồi bấm "Tôi hiểu, hiện mã QR".',
      'Trên điện thoại: Zalo → biểu tượng quét QR (góc trên) · WhatsApp → Cài đặt → Thiết bị đã liên kết → Liên kết thiết bị. Quét mã trên màn hình.',
      'Chờ thẻ chuyển sang "Đang kết nối" và đồng bộ xong danh sách nhóm. Mã QR tự làm mới nếu chưa kịp quét (Zalo 60 giây; WhatsApp 20–60 giây). Mã WhatsApp dày: để điện thoại cách màn hình khoảng 20–30 cm, giữ yên cho tới khi quét xong; nếu điện thoại báo chưa liên kết được, đợi mã mới hiện rồi quét lại.',
      'Bấm "Tiếp tục" để lưu.',
    ],
    doneWhen: 'Có ít nhất một kênh ở trạng thái Đang kết nối.',
    console: { label: 'Kết nối', to: '/connections' },
  },
  {
    n: 6,
    title: 'Chọn nhóm cho agent lắng nghe',
    why: 'Nhóm mới mặc định vào “Lắng nghe im lặng”: Gen lưu hết tin nhưng không bao giờ trả lời, kể cả khi được tag — Sếp chọn nhóm nào cho agent trả lời khi được tag, hoặc tắt hẳn.',
    prepare: ['Đã kết nối ít nhất một kênh (việc 01) và đồng bộ xong danh sách nhóm.'],
    steps: [
      'Bấm "Làm bước này".',
      'Trong bảng nhóm, ở cột "Chế độ lắng nghe", mỗi nhóm mới đã ở "Lắng nghe im lặng — lưu hết, không bao giờ trả lời (kể cả khi được tag)". Muốn Gen trả lời một nhóm, đổi sang "Nghe hết, chỉ trả lời khi được tag" (vẫn lưu mọi tin, chỉ trả lời khi được gọi tên) hoặc "Chủ động bắt tín hiệu"; muốn ngừng lưu thì chọn "Không nghe". Thiếu nhóm vừa vào? Bấm "Cập nhật danh sách nhóm".',
      'Ở cột "Phạm vi xem", ghi lại ý Sếp muốn ai được xem nhóm đó (mặc định "Chỉ Sếp"). Cột này hiện mới là ghi chú — quyền xem thật vẫn theo vai trò (Cài đặt › Quyền hạn).',
      'Bấm "Tiếp tục".',
    ],
    doneWhen: 'Có ít nhất một nhóm đã được bật nghe (không còn ở “Chờ Sếp chọn”, “Không nghe” hay “Tạm dừng”).',
    console: { label: 'Khách & Cơ hội › Khách & Nhóm', to: '/directory' },
    after: 5,
  },
  {
    n: 7,
    title: 'Bật sàng lọc dữ liệu',
    why: 'Tin nhắn thô được lọc, phân loại và chấm điểm trước khi vào kho sạch — agent chỉ dùng dữ liệu đã lọc.',
    prepare: ['Không cần chuẩn bị gì — để mặc định là dùng được ngay.'],
    steps: [
      'Bấm "Làm bước này".',
      'Trả lời một câu "Sếp làm ngành nào?" — Gen chọn sẵn bộ quy tắc hợp ngành; lịch lọc mặc định là mỗi 15 phút hoặc đủ 500 tin, giữ tin có độ tin cậy từ 0,6.',
      'Muốn chỉnh chi tiết: mở "Nâng cao — lịch sàng lọc và từng quy tắc" (hoặc "Nâng cao — trọng số chấm điểm"); không cần thì bỏ qua.',
      'Bấm "Tiếp tục".',
    ],
    doneWhen: 'Có ít nhất một quy tắc sàng lọc đang bật.',
    console: { label: 'Quy tắc sàng lọc', to: '/rules' },
  },
  {
    n: 8,
    title: 'Tạo agent đầu tiên',
    why: 'Agent là "nhân viên AI" làm việc thay Sếp trên các kênh — có tên, vai trò và giọng nói riêng.',
    // v0.1.70 (F029): Console mở sau bước 1–3 — bước 4 có thể đang "Để sau", nên KHÔNG nói "đã xong nếu Sếp ở Console".
    prepare: ['Có ít nhất một nguồn AI chạy được (bước 4 của trình thiết lập — nếu Sếp đã để sau thì làm ở Thiết lập nguồn AI trước).'],
    steps: [
      'Bấm "Làm bước này".',
      'Chọn một "Mẫu" (ví dụ Trợ lý thương mại, CSKH, Thư ký cá nhân) — tên và vai trò được điền sẵn, chọn mẫu là xong.',
      'Sửa "Tên agent" và "Vai trò — agent làm gì cho Sếp" nếu muốn (sửa lại ở Danh tính Agent bất cứ lúc nào).',
      'Bấm "Tiếp tục" để lưu. Gen không gọi model thử ở bước này; muốn trò chuyện thử với agent, làm ở Danh tính Agent.',
    ],
    doneWhen: 'Có ít nhất một agent.',
    console: { label: 'Danh tính Agent', to: '/agents' },
  },
  {
    n: 9,
    title: 'Đặt mức tự trị cho agent',
    why: 'Quyết định agent được tự làm tới đâu: chỉ gợi ý, hay soạn sẵn chờ Sếp duyệt rồi mới gửi.',
    prepare: ['Đã có agent (việc 04).'],
    steps: [
      'Bấm "Làm bước này".',
      'Ở "Mức tự trị", chọn 4 (agent soạn sẵn, Sếp duyệt rồi mới gửi — khuyên dùng) hoặc 3 (agent chỉ gợi ý).',
      'Đọc danh sách "Ranh giới khoá cứng" — các việc agent không bao giờ được tự làm.',
      'Bấm "Tiếp tục" — bấm nghĩa là Sếp đã đọc các ranh giới trên (không còn ô tích riêng).',
    ],
    doneWhen: 'Agent đã có mức tự trị (tạo agent là có mức mặc định; bước này để Sếp chọn lại cho chắc).',
    console: { label: 'Danh tính Agent', to: '/agents' },
    after: 8,
  },
  {
    n: 10,
    title: 'Mời người trong đội',
    why: 'Cho quản lý, nhân viên cùng dùng Console với quyền hạn riêng — mỗi người chỉ thấy phần được giao.',
    prepare: ['Tên và email của từng người.'],
    steps: [
      'Bấm "Làm bước này". Chưa cần mời ai thì bấm "Để sau" — mời lúc nào cũng được ở Đội ngũ › Người dùng.',
      'Muốn mời ngay: bấm "Mời ngay", rồi "Thêm người", điền "Tên hiển thị", "Email" và chọn "Vai trò" (Quản lý, Vận hành, Nhân viên phụ trách, Kiểm soát).',
      'Thêm đủ người rồi bấm "Tiếp tục".',
      'Hệ thống hiện mật khẩu tạm của từng người — chép lại và tự gửi cho họ qua Zalo/email riêng (chưa có gửi thư tự động).',
      'Nhân viên ở máy khác hoặc dùng điện thoại cần Truy cập từ xa: Sếp chạy genh remote tailscale (khuyên dùng) trên máy chủ trước khi gửi. Lời nhắn kèm địa chỉ đăng nhập có ở Đội ngũ › Người dùng (nút "Mời người dùng" hoặc "Đặt lại mật khẩu") — hộp đó cảnh báo đỏ nếu địa chỉ chỉ mở được trên máy chủ.',
      'Bấm "Đã lưu, sang bước sau".',
    ],
    doneWhen: 'Có thêm ít nhất một tài khoản ngoài Sếp.',
    console: { label: 'Đội ngũ › Người dùng', to: '/team' },
  },
  {
    n: 11,
    title: 'Đặt lịch sao lưu',
    why: 'Máy hỏng hay lỡ tay xoá vẫn khôi phục được dữ liệu — bản sao lưu được mã hoá và giữ ngay trong máy.',
    prepare: ['Không cần chuẩn bị gì — sao lưu đã tự bật: hằng ngày lúc 02:00, giữ 7 bản theo ngày, 4 theo tuần, 12 theo tháng.'],
    steps: [
      'Bấm "Làm bước này".',
      'Đọc dòng "Sao lưu đã tự bật"; muốn giữ nguyên thì bấm "Tiếp tục" (hoặc "Để sau" — lịch này vẫn chạy).',
      'Muốn đổi: mở "Đổi lịch (không bắt buộc)", chọn "Tần suất" rồi nhập "Giờ chạy (HH:MM)", ví dụ 03:30, và bấm "Tiếp tục".',
    ],
    doneWhen: 'Đã có lịch sao lưu.',
    console: { label: 'Cài đặt › Sao lưu & cập nhật', to: '/system?tab=storage' },
  },
  {
    n: 13,
    title: 'Kết nối Facebook',
    why: 'Để Gen đọc thông báo, tin nhắn và — khi Sếp bấm Xác nhận + nhập mã PIN — trả lời bình luận / nhắn tin thay Sếp; không đăng bài.',
    prepare: ['Tài khoản Facebook thật của chính Sếp (không dùng tài khoản phụ hay nick ảo).', 'Điện thoại để nhận mã xác minh nếu Facebook hỏi.'],
    steps: [
      'Bấm "Mở trang Tài khoản mạng xã hội" bên dưới.',
      'Bấm "Thêm tài khoản", đặt tên dễ nhận ra.',
      'Đọc kỹ cảnh báo rủi ro, tích hai ô xác nhận rồi lưu.',
      'Bấm "Đăng nhập" — Sếp tự đăng nhập ngay trong app (mật khẩu, mã 2FA không lưu lại).',
      'Khi tài khoản báo Đang kết nối, bấm "Đọc ngay" để thử đọc thông báo.',
    ],
    doneWhen: 'Đã đăng nhập Facebook ít nhất một lần.',
    console: { label: 'Tài khoản mạng xã hội', to: '/social' },
    doTo: '/social',
    doLabel: 'Mở trang Tài khoản mạng xã hội',
  },
  {
    n: 14,
    title: 'Nối Gen-hub',
    why: `Để Gen đọc ${KHO_LABEL} của Sếp trên Gen-hub — trả lời có căn cứ từ việc, quyết định, bài học đã ghi. Gen chỉ ghi vào ${KHO_LABEL} (Phiên, Việc) khi Sếp bấm Xác nhận và nhập mã PIN.`,
    prepare: ['Tài khoản Gen-hub của Sếp.', 'Địa chỉ Gen-hub của Sếp (dạng https://…/mcp, lấy trong Gen-hub).'],
    steps: [
      'Trong Gen-hub: tạo trợ lý mới, chọn thẻ truy cập (token) 90 ngày, bật quyền ĐỌC Kho.',
      'Tuỳ chọn: tick thêm quyền ĐỌC lịch, đọc mail, đọc việc (Google Tasks) và tìm Drive để bản tin Gen có lịch hôm nay, mail cần trả lời, việc đang mở — KHÔNG bật quyền ghi lịch, mail hay Drive.',
      'Tuỳ chọn: tick quyền ghi Kho (tạo và sửa bản ghi — tên trong Gen-hub: kho_create, kho_update) nếu muốn Gen ghi Phiên, Việc vào Kho — mỗi lần ghi Sếp vẫn bấm Xác nhận và nhập mã PIN.',
      'Chép token vừa tạo.',
      'Bấm "Mở thẻ Gen-hub" bên dưới, dán địa chỉ và token vào thẻ Gen-hub.',
      'Bấm "Lưu & kiểm tra" (nhập mã PIN khi được hỏi) và chờ báo Đã nối Kho.',
    ],
    doneWhen: 'Kiểm tra xanh ít nhất một lần.',
    console: { label: 'Kết nối › Gen-hub', to: '/connections#genhub' },
    doTo: '/connections#genhub',
    doLabel: 'Mở thẻ Gen-hub',
  },
];

/**
 * v0.1.29: bước 4 "Để sau" được. Không nằm trong danh sách 5–11 của trang Hướng dẫn — Tổng quan có dải "Chưa có model"
 * riêng mở thẳng `/guide/4` (cùng form bước 4 của trình thiết lập, lưu được cả sau Hoàn tất).
 */
export const MODEL_GUIDE: GuideItem = {
  n: 4,
  title: 'Chọn model AI (bộ não)',
  why: 'Chưa có model thì Gen không trả lời được và tin nhắn chưa được sàng lọc — chỉ được gom về kho thô.',
  prepare: ['Tài khoản Google (đăng nhập Antigravity CLI) hoặc một khoá API (Gemini, DeepSeek, tương thích OpenAI).'],
  steps: [
    'Đăng nhập Google, hoặc dán khoá API rồi bấm "Thêm & kiểm tra".',
    'Khi nguồn báo Hoạt động, chọn model rồi bấm "Dùng model này" (không chọn thì hệ thống dùng model đầu tiên).',
    'Bấm "Tiếp tục".',
  ],
  doneWhen: 'Gen hoặc Sàng lọc đã được gán một model.',
  // v0.1.70 (F029): cùng đích với mọi nút "Chọn model" / `readiness.FIX_AI` — màn Thiết lập nguồn AI.
  console: { label: 'Thiết lập nguồn AI', to: AI_SOURCE_PATH },
};

export const GUIDE_BY_N: Record<number, GuideItem> = Object.fromEntries([MODEL_GUIDE, ...GUIDE].map((g) => [g.n, g]));

/** v0.1.39: số thứ tự hiển thị (01, 02…) của việc n trong danh sách Hướng dẫn thiết lập — không phải số bước. */
export function guideOrdinal(n: number): string {
  const i = GUIDE.findIndex((g) => g.n === n);
  return String(i >= 0 ? i + 1 : n).padStart(2, '0');
}

/** v0.1.30: thẻ ẩn khi người dùng đã bấm "Ẩn" và KHÔNG có bước dở nào mới so với lúc ẩn. */
export function followUpHidden(pending: number[], hidden: number[] | undefined): boolean {
  if (!hidden) return false;
  return pending.every((n) => hidden.includes(n));
}
