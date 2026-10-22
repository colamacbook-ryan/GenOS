import { KHO_LABEL, relabelKho, type DataToolName } from '@gen-harness/contracts';

/**
 * Nhãn tiếng Việt của bước `tool` trong khung Gen ("Đã tra …"). Phải phủ MỌI tên trong `DATA_TOOL_NAMES`
 * (apps/api/gh/gen/envelope.py) — test `gen-tool-labels-v0149` đối chiếu, để Sếp không bao giờ thấy tên kỹ thuật
 * kiểu "Đã tra hub.calendar".
 */
export const TOOL_LABEL: Record<DataToolName, string> = {
  'overview.summary': 'Hôm nay',
  'queue.list': 'Hộp thư',
  'draft.list': 'bản nháp',
  'draft.get': 'bản nháp',
  'profile.search': 'tìm kiếm',
  'profile.get': 'hồ sơ',
  'opportunity.list': 'cơ hội',
  'people.care': 'chất lượng chăm sóc',
  'audit.list': 'nhật ký',
  'system.health': 'sức khoẻ hệ thống',
  'guide.list': 'hướng dẫn kết nối',
  'screens.list': 'danh mục màn',
  'task.list': 'việc & nhắc hẹn',
  'staff.list': 'danh sách người',
  'refinery.summary': 'lọc tin',
  'hub.kho_summary': 'Kho dữ liệu (Gen-hub)',
  'hub.kho_search': 'Kho dữ liệu (Gen-hub)',
  'hub.kho_get': 'Kho dữ liệu (Gen-hub)',
  'social.accounts': 'tài khoản mạng xã hội',
  'social.read': 'đọc mạng xã hội',
  // v0.1.49 (QD-16): Tài liệu / Deal / Vụ việc nội bộ + lịch / việc / mail / Drive Google qua Gen-hub (chỉ đọc).
  'document.list': 'tài liệu',
  'document.get': 'tài liệu',
  'deal.list': 'deal',
  'deal.get': 'deal',
  'case.list': 'vụ việc',
  'case.get': 'vụ việc',
  'hub.calendar': 'lịch (Gen-hub)',
  'hub.tasks': 'việc Google (Gen-hub)',
  'hub.mail_search': 'mail (Gen-hub)',
  'hub.mail_read': 'mail (Gen-hub)',
  'hub.drive_search': 'Drive (Gen-hub)',
  // v0.1.41 (F-8): bước đầu của Bản tin Gen.
  'briefing.sources': 'việc, khách, nháp, sự cố…',
  // v0.1.54: Gen hướng dẫn — việc cần làm & bài học hôm nay của Sếp.
  'coach.status': 'Việc cần làm & bài học',
  // v0.1.74: danh bạ nhóm / người + tin nhóm gần đây (chỉ đọc).
  'group.list': 'danh bạ nhóm',
  'group.recent': 'tin nhóm gần đây',
  'person.list': 'danh bạ người',
};

/**
 * Nhãn của một bước tool; tên lạ (máy chủ mới hơn web) ⇒ câu chung, không lộ tên kỹ thuật. `khoLabel` = tên Kho Sếp tự đặt
 * (`HubLink.kho_label`, v0.1.57) — nhãn "Kho dữ liệu (Gen-hub)" đi qua `relabelKho` như mọi chữ tên Kho khác (F117).
 */
export function toolLabel(name: string, khoLabel?: string | null): string {
  return relabelKho((TOOL_LABEL as Record<string, string>)[name] ?? 'dữ liệu', khoLabel);
}

/** Nhãn của bước này có chứa tên Kho (nên cần tên Kho Sếp tự đặt)? */
export function toolLabelHasKho(name: string): boolean {
  return ((TOOL_LABEL as Record<string, string>)[name] ?? '').includes(KHO_LABEL);
}
