/**
 * Nhật ký hành động — lớp dịch mã kỹ thuật sang câu tiếng Việt cho Sếp đọc (F105).
 *
 * Máy chủ chỉ lưu MÃ hành động (`provider.model_set`) và mã tác nhân (`system:worker`); màn Nhật ký không in thẳng chúng.
 * Mã chưa có trong bảng vẫn hiện được: nhãn nhóm theo tiền tố ("Nhà cung cấp AI — thao tác khác"), còn mã gốc luôn nằm
 * ở `title` của ô để người cần tra cứu rê chuột xem. Bài test `test_audit_search.py` quét mã hành động trong mã máy chủ
 * và đòi mỗi mã có nhãn ở đây — thêm hành động mới mà quên nhãn thì CI đỏ.
 */
import { ROLE_LABEL } from './systemModel';

/** Mã hành động → câu ngắn (viết hoa chữ đầu, không có chủ ngữ — cột "Ai" đã nói ai làm). */
export const ACTION_LABEL: Record<string, string> = {
  // Đăng nhập, tài khoản, mã PIN
  'auth.login': 'Đăng nhập',
  'auth.logout': 'Đăng xuất',
  'auth.login_failed': 'Đăng nhập không thành công',
  'auth.login_rate_limited': 'Đăng nhập bị chặn tạm thời vì thử quá nhiều lần',
  'auth.password_reset': 'Đặt lại mật khẩu Owner bằng lệnh trên máy chủ',
  'auth.pin_verified': 'Nhập đúng mã PIN',
  'auth.pin_failed': 'Nhập sai mã PIN',
  'auth.pin_locked': 'Mã PIN bị khoá tạm thời do nhập sai nhiều lần',
  'auth.pin_attempt_while_locked': 'Thử nhập mã PIN khi đang bị khoá',
  'auth.pin_not_set': 'Cần mã PIN nhưng chưa đặt',
  'account.password_changed': 'Đổi mật khẩu',
  'account.password_check_failed': 'Nhập sai mật khẩu khi xác nhận',
  'account.pin_set': 'Đặt mã PIN lần đầu',
  'account.pin_changed': 'Đổi mã PIN',
  'account.profile_updated': 'Sửa thông tin tài khoản',
  'account.session_revoked': 'Đăng xuất một thiết bị',
  'account.sessions_revoked': 'Đăng xuất các thiết bị khác',
  // Người dùng, vai trò, quyền
  'user.invited': 'Mời thêm người vào hệ thống',
  'user.role_changed': 'Đổi vai trò của một người',
  'user.deactivated': 'Khoá tài khoản một người',
  'user.reactivated': 'Mở lại tài khoản một người',
  'user.password_reset': 'Đặt lại mật khẩu cho một người',
  'permission.changed': 'Sửa bảng quyền hạn',
  'boundary.changed': 'Sửa một ranh giới an toàn',
  'org.updated': 'Sửa thông tin tổ chức',
  'defaults.reset': 'Đặt cài đặt về mặc định',
  'group.updated': 'Sửa nhóm',
  // Thiết lập ban đầu
  'setup.step_saved': 'Lưu một bước thiết lập',
  'setup.step_skipped': 'Bỏ qua một bước thiết lập',
  'setup.owner_created': 'Tạo tài khoản Owner',
  'setup.member_invited': 'Mời thành viên lúc thiết lập',
  // Nhà cung cấp AI, mô hình, chi phí
  'provider.created': 'Thêm nhà cung cấp AI',
  'provider.updated': 'Sửa nhà cung cấp AI',
  'provider.deleted': 'Xoá nhà cung cấp AI',
  'provider.tested': 'Thử kết nối nhà cung cấp AI',
  'provider.diagnosed': 'Chẩn đoán nhà cung cấp AI',
  'provider.key_added': 'Thêm khoá truy cập nhà cung cấp AI',
  'provider.key_deleted': 'Xoá khoá truy cập nhà cung cấp AI',
  'provider.model_set': 'Chọn mô hình AI',
  'provider.model_deleted': 'Gỡ mô hình AI',
  'provider.model_rejected': 'Mô hình AI bị từ chối',
  'provider.model_auto': 'Hệ thống tự chọn lại mô hình AI',
  'provider.chain_reordered': 'Đổi thứ tự nhà cung cấp AI dự phòng',
  'ai.price_changed': 'Sửa đơn giá mô hình AI',
  'ai.budget_changed': 'Sửa hạn mức chi phí AI',
  'ai.background_cli_changed': 'Sửa nguồn AI chạy nền',
  'cli.login_started': 'Bắt đầu đăng nhập công cụ AI trên máy',
  'cli.logged_in': 'Đăng nhập công cụ AI trên máy',
  'cli.login_failed': 'Đăng nhập công cụ AI trên máy không thành công',
  'cli.account_switched': 'Đổi tài khoản công cụ AI trên máy',
  'cli.profile_deleted': 'Xoá hồ sơ đăng nhập công cụ AI',
  // Gen
  'gen.turn': 'Hỏi đáp với Gen',
  'gen.decide': 'Gen xử lý một mục theo luật',
  'gen.briefing': 'Gen soạn bản tin tổng hợp',
  'gen.kho_release_proposed': 'Gen đề xuất ghi vào Kho dữ liệu',
  'gen.propose': 'Gen soạn một đề xuất',
  'gen.answer': 'Gen trả lời',
  'gen.proposal_confirmed': 'Xác nhận một đề xuất của Gen',
  'gen.proposal_chat_confirmed': 'Sếp trả lời OK trong chat — xác nhận một đề xuất',
  'gen.proposal_chat_cancelled': 'Sếp huỷ một đề xuất trong chat',
  'gen.proposal_cancelled': 'Huỷ một đề xuất của Gen',
  'gen.memory_saved': 'Ghi nhớ một điều cho Gen',
  'gen.memory_updated': 'Sửa một điều Gen nhớ',
  'gen.memory_deleted': 'Xoá một điều Gen nhớ',
  'gen.conversation_deleted': 'Xoá cuộc trò chuyện với Gen',
  'gen.settings_changed': 'Sửa cài đặt Gen',
  'gen.coach_prefs_changed': 'Sửa cách Gen gợi ý việc nên làm',
  'gen.coach_item_dismissed': 'Ẩn một gợi ý của Gen',
  'gen.coach_item_restored': 'Hiện lại một gợi ý của Gen',
  'gen.tour_step': 'Làm một bước hướng dẫn',
  'agent.decided': 'Agent đưa ra quyết định',
  // Agent
  'agent.created': 'Tạo agent',
  'agent.cloned': 'Nhân bản agent',
  'agent.diplomat_set': 'Đổi agent ngoại giao',
  'agent.updated': 'Sửa agent',
  'agent.enabled': 'Bật agent',
  'agent.disabled': 'Tắt agent',
  'agent.archived': 'Lưu trữ agent',
  'agent.bound': 'Gắn model cho agent',
  'agent.unbound': 'Gỡ model khỏi agent',
  'directory.bot_assigned': 'Giao bộ nhận tin cho một người',
  'directory.group_bot_set': 'Chọn bộ nhận tin cho nhóm chat',
  'directory.bulk_bot_set': 'Chọn bộ nhận tin hàng loạt cho nhiều người',
  // Tin nhắn, bản nháp
  'message.sent': 'Gửi tin nhắn',
  'message.send_failed': 'Gửi tin nhắn không thành công',
  'draft.created': 'Tạo bản nháp chờ duyệt',
  'draft.approved': 'Duyệt bản nháp',
  'draft.edited': 'Sửa bản nháp',
  'draft.rejected': 'Từ chối bản nháp',
  'draft.regenerated': 'Soạn lại bản nháp',
  'draft.sent': 'Gửi bản nháp đã duyệt',
  'draft.failed': 'Bản nháp không gửi được',
  'action.auto_executed': 'Agent tự làm việc trong mức tự trị',
  // Kênh chat
  'channel.login': 'Đăng nhập kênh chat',
  'channel.logout': 'Đăng xuất kênh chat',
  'channel.session_active': 'Kênh chat đã kết nối',
  'channel.session_ended': 'Kênh chat mất kết nối',
  'channel.resume': 'Nối lại kênh chat bằng phiên đã lưu (không quét lại mã QR)',
  'channel.session_reconnecting': 'Kênh chat bị ngắt, đang chờ nối lại',
  'channel.listen_direct_changed': 'Đổi chế độ tin nhắn riêng (1-1)',
  'channel.new_group_mode_changed': 'Đổi cách xử lý nhóm mới của kênh chat',
  'channel.new_group_mode_reset': 'Nhóm mới của kênh chat về "Lắng nghe im lặng" (đổi tài khoản)',
  'channel.groups_refresh': 'Làm mới danh sách nhóm của kênh chat',
  'notify.telegram.save': 'Lưu kết nối thông báo Telegram',
  'notify.telegram.test': 'Thử gửi thông báo Telegram',
  'notify.telegram.delete': 'Xoá kết nối thông báo Telegram',
  // v0.1.64: Sếp nhắn Gen qua Telegram (chỉ tài khoản đã ghép; mọi tin lạ bị bỏ im lặng)
  'telegram.pair_start': 'Bắt đầu ghép Telegram của Sếp',
  'telegram.paired': 'Đã ghép tài khoản Telegram của Sếp',
  'telegram.unpaired': 'Gỡ ghép tài khoản Telegram của Sếp',
  'telegram.inbound_toggle': 'Bật/tắt nhận tin Telegram từ Sếp',
  'telegram.command': 'Sếp nhắn Gen qua Telegram',
  'telegram.rejected': 'Bỏ một tin Telegram không phải của Sếp',
  // v0.1.72: bot tài khoản riêng — loại tài khoản, kênh quản trị Zalo/WhatsApp, chế độ hỗ trợ, tin bot tự gửi
  'channel.account_kind': 'Đổi loại tài khoản của kênh chat (tài khoản riêng của bot / tài khoản cá nhân của Sếp)',
  'owner_channel.pair_start': 'Lấy mã ghép kênh quản trị',
  'owner_channel.paired': 'Đã ghép kênh quản trị với Sếp',
  'owner_channel.unpaired': 'Huỷ ghép kênh quản trị',
  'owner_channel.update': 'Sửa kênh quản trị (bật/tắt, cài đặt báo cáo)',
  'owner_channel.command': 'Sếp ra lệnh cho bot qua kênh quản trị',
  'owner_channel.nghe': 'Sếp duyệt nghe một nhóm qua kênh quản trị',
  'owner_channel.bo': 'Sếp bỏ nghe một nhóm qua kênh quản trị',
  'owner_channel.ho_tro_bat': 'Sếp bật chế độ hỗ trợ qua kênh quản trị',
  'owner_channel.ho_tro_tat': 'Sếp tắt chế độ hỗ trợ qua kênh quản trị',
  'owner_channel.support_on': 'Bật chế độ hỗ trợ cho một nhóm',
  'owner_channel.support_off': 'Tắt chế độ hỗ trợ của một nhóm',
  'autosend.issued': 'Bot được phép tự gửi một tin',
  'autosend.sent': 'Bot đã tự gửi một tin',
  'autosend.failed': 'Bot tự gửi tin không thành công',
  'autosend.refused': 'Chặn một tin bot định tự gửi',
  // Dữ liệu, tri thức
  'raw.exported': 'Xuất dữ liệu thô',
  'audit_log.exported': 'Xuất Nhật ký hành động',
  'retention_policy.changed': 'Sửa hạn lưu dữ liệu',
  'refinery.run_requested': 'Chạy lọc dữ liệu thủ công',
  'refinery.schedule_changed': 'Sửa lịch lọc dữ liệu',
  'refinery.triage_settings_changed': 'Sửa cài đặt phân loại tin',
  'person.type_inferred': 'Gen tự nhận loại người từ quy tắc sàng lọc',
  'rule.created': 'Tạo luật lọc',
  'rule.updated': 'Sửa luật lọc',
  'rule.enabled': 'Bật luật lọc',
  'rule.disabled': 'Tắt luật lọc',
  'rule.weights_changed': 'Sửa trọng số chấm điểm',
  'identity.merged': 'Gộp hai danh tính thành một',
  'identity.split': 'Tách một danh tính',
  'identity.reverted': 'Hoàn tác việc gộp danh tính',
  'identity.rejected': 'Từ chối gợi ý gộp danh tính',
  'graph.layout_saved': 'Lưu bố cục sơ đồ quan hệ',
  'graph.recomputed': 'Tính lại sơ đồ quan hệ',
  'match.introduced': 'Giới thiệu hai bên làm quen',
  'match.rejected': 'Bỏ qua một gợi ý kết nối',
  'matches.recomputed': 'Tính lại gợi ý kết nối',
  'search.bulk_action': 'Thao tác hàng loạt từ kết quả tìm kiếm',
  'notebook.entry_added': 'Thêm vào Sổ tay nhận thức',
  'notebook.entry_edited': 'Sửa Sổ tay nhận thức',
  'notebook.entry_archived': 'Cất một mục Sổ tay nhận thức',
  'notebook.compacted': 'Gọn lại Sổ tay nhận thức',
  'notebook.reset': 'Xoá sạch Sổ tay nhận thức',
  // Công việc, khách, cơ hội
  'task.created': 'Tạo việc',
  'task.updated': 'Sửa việc',
  'case.created': 'Tạo hồ sơ vụ việc',
  'case.updated': 'Sửa hồ sơ vụ việc',
  'deal.created': 'Tạo thương vụ',
  'deal.updated': 'Sửa thương vụ',
  'opportunity.created': 'Tạo cơ hội',
  'opportunity.stage_changed': 'Chuyển giai đoạn cơ hội',
  'profile.updated': 'Sửa hồ sơ khách',
  'promise.kept': 'Đánh dấu đã giữ lời hứa',
  'promise.broken': 'Đánh dấu lỡ lời hứa',
  'queue.assigned': 'Giao việc trong hàng đợi',
  'queue.silenced': 'Tạm ẩn một việc trong hàng đợi',
  'queue.replayed': 'Phát lại các tin khách nằm trong hàng việc lỗi',
  'queue.cleared': 'Xoá các việc lỗi đã xem khỏi hàng việc lỗi',
  'alert.acknowledged': 'Đã xem một cảnh báo',
  'document.uploaded': 'Tải tài liệu lên',
  'document.updated': 'Sửa tài liệu',
  'document.deleted': 'Xoá tài liệu',
  'document.downloaded': 'Tải tài liệu xuống',
  'document.acl_updated': 'Sửa quyền xem tài liệu',
  'people_review.viewed': 'Xem đánh giá con người',
  'people_review.read': 'Đọc đánh giá con người',
  'people_review.audit_viewed': 'Xem nhật ký đánh giá con người',
  'people_review.explained': 'Xem lý do một đánh giá',
  'people_review.score_edited': 'Sửa điểm đánh giá',
  'people_review.suspicious_cleared': 'Bỏ cờ nghi vấn của một đánh giá',
  'review_dispute.created': 'Gửi khiếu nại một đánh giá',
  'review_dispute.resolved': 'Xử lý khiếu nại một đánh giá',
  // Kho dữ liệu của Sếp
  'hub.link_tested': 'Thử kết nối Kho dữ liệu',
  'hub.link_disabled': 'Tắt kết nối Kho dữ liệu',
  'hub.link_updated': 'Sửa kết nối Kho dữ liệu',
  'hub.kho_label_changed': 'Sửa tên gọi Kho dữ liệu',
  'hub.kho_written': 'Ghi vào Kho dữ liệu',
  'hub.kho_write_blocked': 'Việc ghi vào Kho dữ liệu bị chặn',
  'hub.kho_write_failed': 'Ghi vào Kho dữ liệu không thành công',
  'hub.token_expiry_notified': 'Báo sắp hết hạn khoá Kho dữ liệu',
  // Công cụ ngoài (MCP), plugin
  'mcp.server_created': 'Thêm máy chủ công cụ ngoài',
  'mcp.server_updated': 'Sửa máy chủ công cụ ngoài',
  'mcp.server_deleted': 'Xoá máy chủ công cụ ngoài',
  'mcp.server_discovered': 'Dò công cụ của máy chủ ngoài',
  'mcp.server_blocked': 'Chặn một máy chủ công cụ ngoài',
  'mcp.grant_added': 'Cấp quyền dùng công cụ ngoài cho agent',
  'mcp.grant_removed': 'Thu quyền dùng công cụ ngoài của agent',
  'mcp.tool_exposed': 'Mở một công cụ ngoài cho agent',
  'mcp.tool_hidden': 'Ẩn một công cụ ngoài',
  'mcp.tool_access_changed': 'Sửa quyền dùng một công cụ ngoài',
  'mcp.call_ok': 'Agent gọi công cụ ngoài',
  'mcp.call_error': 'Agent gọi công cụ ngoài bị lỗi',
  'mcp.call_blocked': 'Lệnh gọi công cụ ngoài bị chặn',
  'plugin.enable': 'Bật plugin',
  'plugin.disable': 'Tắt plugin',
  'plugin.uninstall': 'Gỡ plugin',
  'plugin.install_requested': 'Yêu cầu cài plugin',
  'plugin.install_rejected': 'Từ chối cài plugin',
  'plugin.breaker_reset': 'Mở lại plugin đã bị ngắt tự động',
  'jev.enable': 'Bật Jev (quyết định nhanh cho Gen)',
  'jev.benchmark': 'Chạy thử Jev',
  // Mạng xã hội
  'social.account_created': 'Thêm tài khoản mạng xã hội',
  'social.account_updated': 'Sửa tài khoản mạng xã hội',
  'social.login_started': 'Bắt đầu đăng nhập tài khoản mạng xã hội',
  'social.login_ok': 'Đăng nhập tài khoản mạng xã hội thành công',
  'social.login_failed': 'Đăng nhập tài khoản mạng xã hội không thành công',
  'social.login_cancelled': 'Huỷ đăng nhập tài khoản mạng xã hội',
  'social.check': 'Kiểm tra tài khoản mạng xã hội',
  'social.session_check': 'Hệ thống kiểm tra phiên đăng nhập mạng xã hội',
  'social.paused': 'Tạm dừng tài khoản mạng xã hội',
  'social.resumed': 'Chạy lại tài khoản mạng xã hội',
  'social.auto_paused': 'Hệ thống tự tạm dừng tài khoản mạng xã hội',
  'social.revoked': 'Thu hồi tài khoản mạng xã hội',
  'social.session_unreadable': 'Không đọc được phiên đăng nhập mạng xã hội',
  'social.job_failed': 'Việc trên mạng xã hội không thành công',
  'social.read_requested': 'Yêu cầu đọc trên mạng xã hội',
  'social.read': 'Đọc nội dung trên mạng xã hội',
  'social.write_requested': 'Yêu cầu đăng lên mạng xã hội',
  'social.write': 'Đăng lên mạng xã hội',
  'social.write_risk_accepted': 'Nhận rủi ro khi đăng lên mạng xã hội',
  'social.write_risk_revoked': 'Rút lại việc nhận rủi ro khi đăng',
  'social.halt': 'Dừng khẩn cấp mạng xã hội',
  'social.halt_released': 'Gỡ dừng khẩn cấp mạng xã hội',
  // Hệ thống: sao lưu, cập nhật, chẩn đoán
  'backup.requested': 'Yêu cầu sao lưu ngay',
  'backup.schedule_changed': 'Sửa lịch sao lưu',
  'backup.downloaded': 'Tải bản sao lưu về',
  'backup.restore_requested': 'Yêu cầu khôi phục từ bản sao lưu',
  'offsite.recovery_kit_viewed': 'Xem bộ khôi phục bản sao ngoài máy',
  'offsite.portable_downloaded': 'Tải bản sao mang đi',
  'offsite.destination_requested': 'Yêu cầu đổi nơi lưu bản sao ngoài máy',
  'offsite.run_requested': 'Yêu cầu chép bản sao ra ngoài máy',
  'offsite.disable_requested': 'Yêu cầu tắt bản sao ngoài máy',
  'system.update_checked': 'Kiểm tra bản mới',
  'system.update_requested': 'Yêu cầu cập nhật hệ thống',
  'system.diagnostics.request': 'Yêu cầu gói chẩn đoán',
  'system.diagnostics.download': 'Tải gói chẩn đoán',
  'audit.chain_verified': 'Kiểm tra tính toàn vẹn của nhật ký',
};

/** Tiền tố mã → tên nhóm, dùng khi gặp mã chưa có trong `ACTION_LABEL` (hành động mới thêm ở máy chủ). */
const GROUP_LABEL: Record<string, string> = {
  auth: 'Đăng nhập',
  account: 'Tài khoản',
  user: 'Người dùng',
  provider: 'Nhà cung cấp AI',
  ai: 'Chi phí & nguồn AI',
  cli: 'Công cụ AI trên máy',
  gen: 'Gen',
  agent: 'Agent',
  draft: 'Bản nháp',
  message: 'Tin nhắn',
  channel: 'Kênh chat',
  owner_channel: 'Kênh quản trị',
  autosend: 'Tin bot tự gửi',
  notify: 'Thông báo',
  hub: 'Kho dữ liệu',
  mcp: 'Công cụ ngoài',
  plugin: 'Plugin',
  social: 'Mạng xã hội',
  backup: 'Sao lưu',
  offsite: 'Bản sao ngoài máy',
  system: 'Hệ thống',
  defaults: 'Cài đặt mặc định',
  notebook: 'Sổ tay nhận thức',
  task: 'Việc',
  rule: 'Luật lọc',
  identity: 'Danh tính',
};

/** Câu cho Sếp đọc; `detail` (nếu có) dùng cho vài mẫu có chỗ trống như "đổi vai trò từ … sang …". */
export function actionLabel(action: string, detail?: unknown): string {
  if (action.startsWith('http.')) return 'Thao tác chung (chưa có tên riêng)';
  if (action.startsWith('person_data.')) return 'Xem dữ liệu cá nhân của một người';
  const d = detail && typeof detail === 'object' ? (detail as Record<string, unknown>) : null;
  if (action === 'user.role_changed' && d && typeof d.from === 'string' && typeof d.to === 'string') {
    const name = (code: string) => ROLE_LABEL[code]?.name ?? code;
    return `Đổi vai trò từ ${name(d.from)} sang ${name(d.to)}`;
  }
  const known = ACTION_LABEL[action];
  if (known) return known;
  const group = GROUP_LABEL[action.split('.')[0] ?? ''];
  return group ? `${group} — thao tác khác` : 'Thao tác khác';
}

/** Dòng "ồn" mà Nhật ký ẩn mặc định (khớp `NOISE_SQL` ở máy chủ). */
export function isNoiseAction(action: string, result: string): boolean {
  return action.startsWith('http.') || (action === 'audit.chain_verified' && result === 'ok');
}

/** Tác nhân hệ thống / bộ nhận tin → tên dễ hiểu. Mã không biết vẫn ra "Hệ thống", không bao giờ in `system:xxx`. */
const SYSTEM_ACTOR_LABEL: Record<string, string> = {
  'system:worker': 'Hệ thống',
  'system:auth': 'Hệ thống',
  'system:setup': 'Hệ thống (thiết lập ban đầu)',
  'system:genh': 'Hệ thống (lệnh trên máy chủ)',
  'system:social': 'Hệ thống (mạng xã hội)',
  'system:social-schedule': 'Hệ thống (lịch mạng xã hội)',
  'system:browser-worker': 'Hệ thống (trình duyệt nền)',
  'system:drafts': 'Hệ thống (bản nháp)',
};

const BRIDGE_NAME: Record<string, string> = { zalo: 'Zalo', telegram: 'Telegram', messenger: 'Messenger', facebook: 'Facebook' };

export interface AuditActorLike {
  actor_type: string;
  actor_id: string | null;
  actor_label: string | null;
}

/** Tên tác nhân cho cột "Ai / agent nào". */
export function actorLabel(a: AuditActorLike): string {
  const id = a.actor_id ?? '';
  const named = a.actor_label && a.actor_label !== id ? a.actor_label : null;
  if (named) return named;
  const sys = SYSTEM_ACTOR_LABEL[id];
  if (sys) return sys;
  if (id.startsWith('bridge:')) {
    const kind = id.slice('bridge:'.length);
    return `Bộ nhận tin ${BRIDGE_NAME[kind] ?? kind}`;
  }
  if (a.actor_type === 'system' || id.startsWith('system:')) return 'Hệ thống';
  if (a.actor_type === 'agent') return 'Agent (đã xoá)';
  if (a.actor_type === 'plugin') return 'Plugin';
  if (a.actor_type === 'user') return 'Người dùng (đã xoá hoặc không rõ tên)';
  return a.actor_label ?? '—';
}

/** Đối tượng: tên đã có; dòng chung `http.*` thì hiện đường dẫn; mã thô (uuid) không hiện. */
export function targetText(a: { target_type: string | null; target_id: string | null; target_label: string | null }): string {
  if (a.target_label) return a.target_label;
  if (a.target_type === 'route' && a.target_id) return a.target_id;
  return '—';
}

/** Bỏ dấu + hạ chữ thường để Sếp gõ "dang nhap" vẫn ra "Đăng nhập". */
export function fold(s: string): string {
  return s
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/đ/g, 'd')
    .replace(/Đ/g, 'd')
    .toLowerCase();
}

/** Mã hành động có nhãn tiếng Việt chứa chữ Sếp gõ — gửi kèm ô tìm vì máy chủ chỉ biết mã. */
export function matchingActionCodes(text: string): string[] {
  const t = fold(text.trim());
  if (!t) return [];
  const out: string[] = [];
  for (const [code, label] of Object.entries(ACTION_LABEL)) if (fold(label).includes(t)) out.push(code);
  return out;
}

/** Chênh lệch (ms) giữa giờ địa phương của `tz` và UTC tại thời điểm `t`. */
function tzOffsetMs(t: number, tz: string): number {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: tz,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).formatToParts(new Date(t));
  const g = (k: string) => Number(parts.find((p) => p.type === k)?.value);
  return Date.UTC(g('year'), g('month') - 1, g('day'), g('hour'), g('minute'), g('second')) - Math.floor(t / 1000) * 1000;
}

/**
 * Ngày "YYYY-MM-DD" (lịch của múi giờ tổ chức) → thời điểm UTC (ISO) lúc 0 giờ ngày đó, cộng `plusDays` ngày.
 * Lọc "đến hết ngày 20" gửi `until` = 0 giờ ngày 21 (máy chủ lọc `at < until`). Ngày sai khuôn → undefined.
 */
export function localDayStartIso(day: string, tz: string, plusDays = 0): string | undefined {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  if (!m) return undefined;
  const guess = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]) + plusDays);
  try {
    const first = guess - tzOffsetMs(guess, tz);
    return new Date(guess - tzOffsetMs(first, tz)).toISOString();
  } catch {
    return new Date(guess).toISOString(); // múi giờ lạ: coi như UTC thay vì làm hỏng màn
  }
}

/** Mã tác nhân hệ thống / bộ nhận tin có tên tiếng Việt chứa chữ Sếp gõ. */
export function matchingActorIds(text: string): string[] {
  const t = fold(text.trim());
  if (!t) return [];
  const out: string[] = [];
  for (const id of Object.keys(SYSTEM_ACTOR_LABEL)) if (fold(SYSTEM_ACTOR_LABEL[id] ?? '').includes(t)) out.push(id);
  for (const kind of Object.keys(BRIDGE_NAME)) {
    const id = `bridge:${kind}`;
    if (fold(actorLabel({ actor_type: 'system', actor_id: id, actor_label: null })).includes(t)) out.push(id);
  }
  return out;
}
