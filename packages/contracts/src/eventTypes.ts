/**
 * v0.1.69 (D5, F062) — MỘT bảng nhãn loại sự kiện (15 loại, tiếng Việt) cho mọi màn của web.
 *
 * Nguồn là `core.lookup` (kind = 'event_type', db/sql/0003_phase2.sql). Máy chủ giữ bản sao ở
 * `apps/api/gh/biz/queue/service.py` (`EVENT_LABELS`); `apps/api/tests/test_event_labels_v016x.py` kiểm
 * lookup == máy chủ == tệp này (cùng chữ, cùng thứ tự) — đổi một nơi mà quên nơi kia thì CI đỏ.
 * KHÔNG khai bảng nhãn thứ hai ở màn nào: dùng `eventTypeLabel` / `EVENT_INTENT_OPTIONS`.
 */
export const EVENT_TYPES: ReadonlyArray<{ code: string; label: string }> = [
  { code: 'AskedPrice', label: 'Hỏi giá / nhu cầu mua' },
  { code: 'OfferedSupply', label: 'Chào nguồn cung' },
  { code: 'RequestedPartnership', label: 'Đề nghị hợp tác' },
  { code: 'Complained', label: 'Phàn nàn' },
  { code: 'PromisedDelivery', label: 'Hứa giao hàng' },
  { code: 'ScheduledMeeting', label: 'Hẹn gặp' },
  { code: 'SentQuotation', label: 'Gửi báo giá' },
  { code: 'SentDocument', label: 'Gửi tài liệu' },
  { code: 'MentionsCompetitor', label: 'Nhắc đối thủ' },
  { code: 'ComparedVendor', label: 'So sánh nhà cung cấp' },
  { code: 'RequestedSample', label: 'Xin mẫu' },
  { code: 'AskedStatus', label: 'Hỏi tiến độ' },
  { code: 'JobSignal', label: 'Tín hiệu tìm việc' },
  { code: 'DealWon', label: 'Chốt đơn' },
  { code: 'WentSilent', label: 'Im lặng' },
];

/** Nhãn "Noise" của Kho thô (tin bị bộ lọc trước loại ra) — không phải loại sự kiện nên không có trong `core.lookup`. */
export const NOISE_CODE = 'Noise';
export const NOISE_LABEL = 'Tin rác';
export const UNKNOWN_EVENT_LABEL = 'Tin khác';

const BY_CODE: Record<string, string> = Object.fromEntries(EVENT_TYPES.map((e) => [e.code, e.label]));

/** Nhãn tiếng Việt của một loại sự kiện; mã lạ/rỗng ⇒ "Tin khác" (không bao giờ lộ mã tiếng Anh cho Sếp). */
export function eventTypeLabel(code: string | null | undefined): string {
  if (!code) return UNKNOWN_EVENT_LABEL;
  if (code === NOISE_CODE) return NOISE_LABEL;
  return BY_CODE[code] ?? UNKNOWN_EVENT_LABEL;
}

/** Bộ lọc "Ý định" (Hộp thư): "Tất cả" + cả 15 loại theo thứ tự của `core.lookup`. */
export const EVENT_INTENT_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '', label: 'Tất cả' },
  ...EVENT_TYPES.map((e) => ({ value: e.code, label: e.label })),
];
