/** Trợ giúp / Giới thiệu (v0.1.22, Đợt B3) — nội dung tĩnh + "Báo lỗi". */
import type { AboutInfo, Me } from '@gen-harness/contracts';

/** Lệnh genh Sếp hay cần — chạy trên máy chủ cài Gen-Harness (cửa sổ dòng lệnh). */
export const GENH_COMMANDS: Array<{ cmd: string; what: string }> = [
  { cmd: 'genh update', what: 'Cập nhật lên bản mới nhất (tự sao lưu trước, lỗi thì tự quay lại). Trong Console có nút "Cập nhật ngay".' },
  { cmd: 'genh reset-password', what: 'Quên mật khẩu Owner: in ra mật khẩu tạm, đăng nhập rồi đặt mật khẩu mới.' },
  { cmd: 'genh trust-ca', what: 'Trình duyệt báo "không an toàn": cho máy này tin chứng chỉ HTTPS nội bộ.' },
  { cmd: 'genh backup', what: 'Sao lưu ngay (thêm --to <thư mục> để chép ra ngoài). Trong Console: Cài đặt › Sao lưu & cập nhật › Sao lưu ngay.' },
  { cmd: 'genh status', what: 'Xem các dịch vụ đang chạy, phiên bản và dung lượng dữ liệu đang dùng.' },
  { cmd: 'genh stop', what: 'Dừng toàn bộ dịch vụ (không mất dữ liệu); trực canh máy chủ tạm nghỉ (không tự khởi động lại, không báo Telegram) tới khi genh start. Chạy tiếp genh start để khởi động lại (vd. khi Bộ xử lý nền đã ngừng).' },
  { cmd: 'genh start', what: 'Bật lại toàn bộ dịch vụ sau genh stop (trực canh máy chủ chạy lại).' },
  { cmd: 'genh doctor', what: 'Tạo gói chẩn đoán đã lọc bí mật để gửi người hỗ trợ (trong Console: Trợ giúp › Gói chẩn đoán).' },
  { cmd: 'genh watchdog status', what: 'Xem trực canh máy chủ: lịch, lần chạy gần nhất, sự cố đang mở.' },
  { cmd: 'genh auto-update status', what: 'Xem lịch tự cập nhật đêm có đang chạy không: lần chạy gần nhất, lần kế tiếp, linger. Có cảnh báo thì làm đúng lệnh nó in ra.' },
  { cmd: 'genh logs worker', what: 'Xem lỗi gần nhất của Bộ xử lý nền (đổi "worker" thành api, bridge… cho dịch vụ khác).' },
];

/**
 * v0.1.45 (F-60): nói rõ giới hạn của mã PIN — PIN chặn người ngồi nhờ máy/phiên đang mở, KHÔNG phải lớp bảo vệ thứ
 * hai (ai biết mật khẩu đăng nhập thì đặt lại được PIN); kèm lưu ý điểm đánh giá nhân sự có thể bị lách.
 * `PIN_LIMITS` là bản cho Owner (gọi "Sếp"); vai trò khác dùng `pinLimitsFor` (gọi "bạn"), và chỉ người xem được
 * đánh giá nhân sự mới thấy câu về cờ 'Đáng ngờ' — không chỉ cho nhân viên cách lách điểm.
 */
export const PIN_LIMITS: { title: string; kicker: string; points: string[] } = {
  title: 'Mã PIN bảo vệ được gì',
  kicker: 'Giới hạn của mã PIN — đọc một lần cho chắc',
  points: [
    'Mã PIN chặn người nhờ máy hoặc phiên đăng nhập đang mở của Sếp để đổi cấu hình nhạy cảm: mức tự trị, tool MCP, tài khoản CLI, nhà cung cấp AI, mời người.',
    'Mã PIN KHÔNG phải lớp bảo vệ thứ hai: ai biết mật khẩu đăng nhập thì đặt lại được PIN.',
    'Vì vậy hãy giữ mật khẩu đăng nhập riêng, không dùng chung với chỗ khác, và đăng xuất ngay khi dùng xong trên máy lạ; nghi lộ mật khẩu thì đổi mật khẩu ngay.',
    "Điểm đánh giá nhân sự có thể bị nhân viên lách bằng cách chèn câu lệnh cho AI hoặc câu xin điểm vào tin nhắn — dòng có chip 'Đáng ngờ' cần Sếp xem chứng cứ trước khi tin.",
  ],
};

const PIN_POINTS_MEMBER = [
  'Mã PIN chặn người nhờ máy hoặc phiên đăng nhập đang mở của bạn để làm các thao tác nhạy cảm mà vai trò của bạn được phép.',
  'Mã PIN KHÔNG phải lớp bảo vệ thứ hai: ai biết mật khẩu đăng nhập thì đặt lại được PIN.',
  'Vì vậy hãy giữ mật khẩu đăng nhập riêng, không dùng chung với chỗ khác, và đăng xuất ngay khi dùng xong trên máy lạ; nghi lộ mật khẩu thì đổi mật khẩu ngay hoặc báo Owner.',
];
const PIN_POINT_REVIEWER = "Dòng đánh giá nhân sự có chip 'Đáng ngờ' cần xem chứng cứ trước khi tin vào điểm.";

/** Thẻ "Mã PIN bảo vệ được gì" theo vai trò: Owner → `PIN_LIMITS`; vai trò khác gọi "bạn", câu về cờ 'Đáng ngờ'
 *  chỉ hiện khi vai trò xem được đánh giá nhân sự (`people_review.read` khác `none`). */
export function pinLimitsFor(me: Pick<Me, 'role' | 'permissions'> | undefined | null): { title: string; kicker: string; points: string[] } {
  if (me?.role?.code === 'owner') return PIN_LIMITS;
  const review = me?.permissions?.['people_review.read'];
  const canReview = !!review && review !== 'none';
  return { ...PIN_LIMITS, points: canReview ? [...PIN_POINTS_MEMBER, PIN_POINT_REVIEWER] : PIN_POINTS_MEMBER };
}

/** Nội dung "Báo lỗi" — không có bí mật (không cookie, không khoá), chỉ đủ để dev tái hiện. */
export function diagnosticText(about: AboutInfo | undefined, me: Me | undefined, now = new Date()): string {
  const w = typeof window !== 'undefined' ? window : undefined;
  return [
    'Gen-Harness — thông tin báo lỗi',
    `Phiên bản: ${about?.version ?? 'bản phát triển (không rõ)'}`,
    `Tổ chức: ${about?.org_name ?? me?.org.name ?? '—'} · múi giờ ${about?.timezone ?? me?.org.timezone ?? '—'}`,
    `Vai trò: ${me?.role.name ?? about?.role.name ?? '—'}`,
    `Trang: ${w ? w.location.pathname + w.location.search : '—'}`,
    `Thời điểm: ${now.toISOString()}`,
    `Trình duyệt: ${typeof navigator !== 'undefined' ? navigator.userAgent : '—'}`,
    `Màn hình: ${w ? `${w.innerWidth}×${w.innerHeight}` : '—'} · ngôn ngữ ${typeof navigator !== 'undefined' ? navigator.language : '—'}`,
    'Mô tả lỗi (Sếp ghi thêm): ',
  ].join('\n');
}

/**
 * v0.1.36 (F-46): thêm phiên bản máy chủ + phiên bản công cụ cài đặt (genh) vào thông tin báo lỗi (ngay sau dòng
 * "Phiên bản:" của `diagnosticText`) — hai số có thể lệch nhau (genh cũ, máy chủ mới) và người hỗ trợ cần cả hai. Cùng
 * chữ với thẻ "Giới thiệu".
 */
export const SERVER_VERSION_LABEL = 'phiên bản máy chủ';
export const GENH_VERSION_LABEL = 'phiên bản công cụ cài đặt (genh)';

export function withVersions(text: string, about: AboutInfo | undefined): string {
  const extra = `Phiên bản máy chủ: ${about?.image_version || '—'} · ${GENH_VERSION_LABEL}: ${about?.genh_version ?? '—'}`;
  const lines = text.split('\n');
  const i = lines.findIndex((l) => l.startsWith('Phiên bản:'));
  lines.splice(i < 0 ? 1 : i + 1, 0, extra);
  return lines.join('\n');
}
