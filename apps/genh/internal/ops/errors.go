// Package ops cài các lệnh VẬN HÀNH của genh (status/open/logs/update/
// backup/restore/doctor/reset-setup/stop/start/uninstall — xem
// docs/handoff/05-installer.md mục "Lệnh vận hành"), khác với internal/
// install (8 bước của `genh install`). cmd/genh/main.go chỉ gọi vào các hàm
// Run* ở đây, giữ main.go mỏng đúng pattern đã có với runInstall.
package ops

// Mã lỗi tra được (GH-E9xx, GH-EAxx, GH-EBxx) cho các lệnh vận hành — dải MỚI, không đụng
// 0xx/1xx/2xx/3xx/5xx/6xx/7xx/8xx đã dùng cho 8 Step cài đặt (xem
// internal/install/errors.go). Đánh số theo lệnh, mỗi lệnh một dải 10 mã để
// còn chỗ mở rộng.
const (
	// 90x — chung cho mọi lệnh vận hành (không tìm compose.yaml, chưa cài,
	// chưa có bí mật…).
	ErrCodeNotInstalled    = "GH-E900" // chưa chạy `genh install` (thiếu bí mật/cấu hình)
	ErrCodeComposeNotFound = "GH-E901" // không tìm thấy deploy/compose.yaml

	// 91x — genh status.
	ErrCodeStatusFailed = "GH-E910"

	// 92x — genh open.
	ErrCodeOpenFailed = "GH-E920"

	// 93x — genh logs.
	ErrCodeLogsFailed = "GH-E930"

	// 94x — genh update (khung kiểm đĩa → tải bản mới → sao lưu → migrate →
	// restart → healthcheck; chỉ khôi phục CSDL khi đã đụng CSDL — xem update.go).
	ErrCodeUpdateBackupFailed         = "GH-E940"
	ErrCodeUpdatePullFailed           = "GH-E941" // tải bản mới thất bại (sau các lần thử) — CHƯA đụng gì, không rollback
	ErrCodeUpdateMigrateFailed        = "GH-E942"
	ErrCodeUpdateRestartFailed        = "GH-E943"
	ErrCodeUpdateNotReady             = "GH-E944"
	ErrCodeUpdateRolledBack           = "GH-E945" // một bước ở trên lỗi VÀ rollback đã tự chạy (thành công hoặc không)
	ErrCodeUpdateObjectsMigrateFailed = "GH-E946" // di trú /tmp/gh-objects (bản cài cũ) -> volume gh_objects thất bại (xem migrateobjects.go)
	ErrCodeUpdateComposeSyncFailed    = "GH-E947" // đồng bộ compose.yaml với bản genh mới thất bại SAU KHI backup đã xong — chưa đụng migrate/restart
	ErrCodeUpdateDiskLow              = "GH-E948" // ổ đĩa không đủ chỗ (sau khi đã dọn ảnh cũ) — dừng TRƯỚC khi tải, chưa đụng gì
	ErrCodeUpdateBlocked              = "GH-E949" // bản này đã lỗi từ bước migrate trở đi ở lần trước (có hoặc không đụng CSDL) — lịch đêm không thử lại; chỉ dùng cho thông điệp/log, không phải lỗi thoát
	ErrCodeUpdateLocked               = "GH-E94A" // đang có một lần cập nhật/khôi phục khác giữ khoá loại trừ (<gốc cài đặt>/genh.lock) — gõ tay thì thoát 1, lịch đêm thì bỏ qua (thoát 0); yêu cầu từ Console chờ khoá quá 30 phút (v0.1.53) thì xoá tệp yêu cầu + ghi failed
	ErrCodeUpdateInterrupted          = "GH-E94B" // genh nhận tín hiệu dừng giữa chừng (máy tắt/khởi động lại/Ctrl-C) — đã quay về bản cũ nếu kịp (máy tắt sau khi đã đụng CSDL: giữ bản mới để `genh update` đi tiếp); KHÔNG ghi update-blocked.json (bản không hỏng) trừ khi quay về chưa trọn, lịch đêm thử lại
	ErrCodeRequestUndeletable         = "GH-E94C" // v0.1.53 (F-97): không xoá được tệp yêu cầu trong run/request (quyền…) — genh KHÔNG làm yêu cầu này để tránh chạy lặp; ghi trạng thái failed (idempotent theo requested_at), thoát 0

	// 95x — genh backup / genh restore.
	ErrCodeBackupFailed  = "GH-E950"
	ErrCodeRestoreFailed = "GH-E951"

	// 96x — genh doctor.
	ErrCodeDoctorReportFailed = "GH-E960"
	ErrCodeWatchdogFailed     = "GH-E961" // v0.1.44 (F-6b): `genh doctor --notify` không chạy được (chưa cài/không thấy compose.yaml/không ghi được tệp trạng thái) — sự cố của máy chủ KHÔNG dùng mã này (đã báo qua Telegram/status)
	ErrCodeDoctorBundleFailed = "GH-E962" // v0.1.44 (F-4b): gói chẩn đoán qua hộp thư (run/request/doctor.json) thất bại — hợp đồng với apps/api (doctor-status.json "error_code")

	// 97x — genh reset-setup.
	ErrCodeResetSetupFailed    = "GH-E970"
	ErrCodeResetSetupCancelled = "GH-E971"

	// 98x — genh stop / genh start.
	ErrCodeStopFailed  = "GH-E980"
	ErrCodeStartFailed = "GH-E981"

	// 99x — genh uninstall.
	ErrCodeUninstallCancelled = "GH-E990"
	ErrCodeUninstallFailed    = "GH-E991"

	// A0x — genh export (xem docs mục "Gói hồ sơ .ghbundle" — hợp đồng với
	// `python -m gh.bundle export`, phía Python là việc của một agent khác,
	// đây chỉ là phía Go gọi vào nó qua docker compose exec).
	ErrCodeExportPasswordMismatch = "GH-EA00" // 2 lần gõ mật khẩu không khớp, hoặc mật khẩu < 12 ký tự
	ErrCodeExportFailed           = "GH-EA01" // `python -m gh.bundle export` thoát khác 0 (mã 1) hoặc lỗi tiến trình
	ErrCodeExportWriteFailed      = "GH-EA02" // ghi tệp tạm/rename ra --to thất bại

	// A1x — genh import (xem cùng mục trên).
	ErrCodeImportNotBundle       = "GH-EA10" // tệp không tồn tại, hoặc không bắt đầu bằng "GHBUNDLE1\n"
	ErrCodeImportCancelled       = "GH-EA11" // huỷ xác nhận GHI ĐÈ (không có --yes)
	ErrCodeImportPasswordInvalid = "GH-EA12" // mật khẩu < 12 ký tự
	ErrCodeImportBackupFailed    = "GH-EA13" // backup an toàn trước khi import thất bại — DỪNG LẠI, chưa đụng gì
	ErrCodeImportWrongPassword   = "GH-EA14" // `python -m gh.bundle import` thoát mã 2: sai mật khẩu hoặc gói hỏng
	ErrCodeImportIncompatible    = "GH-EA15" // `python -m gh.bundle import` thoát mã 3: gói không tương thích
	ErrCodeImportFailed          = "GH-EA16" // `python -m gh.bundle import` thoát mã 1 hoặc lỗi tiến trình khác
	ErrCodeImportRestartFailed   = "GH-EA17" // import xong nhưng `docker compose restart api worker` hoặc healthcheck thất bại

	// A2x — genh reset-password / genh trust-ca.
	ErrCodeResetPasswordFailed = "GH-EA20" // `python -m gh.auth.reset_owner` lỗi hoặc output không đọc được
	ErrCodeTrustCAFailed       = "GH-EA21" // không trích/ghi được CA nội bộ của Caddy

	// B0x — genh offsite (v0.1.40, F-12): bản sao ngoài máy (USB/NAS). HỢP ĐỒNG
	// với apps/api (run/offsite-status.json "error_code") — giữ đúng mã.
	ErrCodeOffsiteNotConfigured = "GH-EB00" // chưa chọn nơi lưu bản sao ngoài máy
	ErrCodeOffsiteNotMounted    = "GH-EB01" // chưa thấy ổ USB/NAS (đích không có/không phải thư mục/cùng ổ với máy chủ)
	ErrCodeOffsiteExportFailed  = "GH-EB02" // xuất gói .ghbundle lỗi
	ErrCodeOffsiteVerifyFailed  = "GH-EB03" // gói vừa ghi không đọc lại được — CHƯA có bản sao ngoài máy
	ErrCodeOffsiteWriteFailed   = "GH-EB04" // không ghi được vào đích (quyền/đầy)
	ErrCodeOffsiteBusy          = "GH-EB05" // đang bận cập nhật/khôi phục (khoá loại trừ)
	ErrCodeOffsiteNotRunning    = "GH-EB06" // dịch vụ (api) chưa chạy
	ErrCodeOffsiteInvalidDest   = "GH-EB07" // đích không hợp lệ khi chọn (không tuyệt đối/không phải thư mục/nằm trong thư mục cài)
)

// RequestUndeletableMessage là thông điệp Console (update-status/restore-status…) khi
// không xoá được tệp yêu cầu (GH-E94C): what = "yêu cầu cập nhật" | "yêu cầu khôi
// phục" | "yêu cầu sao lưu ra ổ ngoài" | "yêu cầu gói chẩn đoán" | "yêu cầu Gửi thử".
// Lỗi gốc (có đường dẫn) CHỈ in stderr/log, không đưa vào đây.
func RequestUndeletableMessage(what string) string {
	return "Không xoá được " + what + " trong run/request — máy chủ không làm yêu cầu này để tránh chạy lặp. " +
		"Kiểm quyền thư mục run/request trên máy chủ rồi bấm Thử lại (" + ErrCodeRequestUndeletable + ")"
}

// RequestBusyMessage là thông điệp Console khi yêu cầu chờ khoá loại trừ quá 30
// phút mà máy chủ vẫn bận (GH-E94A) — tệp yêu cầu đã bị xoá, chưa làm gì.
const RequestBusyMessage = "Máy chủ bận một lần cập nhật/khôi phục khác quá 30 phút — chưa làm yêu cầu. Bấm Thử lại sau (" + ErrCodeUpdateLocked + ")"

// OpError là lỗi có cấu trúc cho các lệnh vận hành, theo đúng tinh thần
// "chuyện gì xảy ra · vì sao · làm gì tiếp" của install.StepError (xem
// docs/handoff/05-installer.md mục "Lỗi") — KHÔNG dùng lại type đó vì các
// lệnh vận hành không chạy qua install.Runner/Reporter (không có khái niệm
// Percent/SubLines/StepID), một type riêng đơn giản hơn phù hợp hơn là ép
// dùng chung cho bằng được.
type OpError struct {
	Code string // ví dụ "GH-E941"
	What string // chuyện gì xảy ra
	Why  string // vì sao
	Next string // làm gì tiếp
	Err  error  // lỗi gốc, nếu có
}

func (e *OpError) Error() string {
	if e == nil {
		return ""
	}
	msg := e.What
	if e.Why != "" {
		msg += ": " + e.Why
	}
	if e.Code != "" {
		msg += " (" + e.Code + ")"
	}
	return msg
}

func (e *OpError) Unwrap() error { return e.Err }

// Report định dạng OpError đúng 3 dòng "chuyện gì · vì sao · làm gì tiếp"
// dùng chung cho mọi lệnh vận hành khi in ra stderr.
func (e *OpError) Report() string {
	s := "✕ " + e.What + "\n"
	if e.Why != "" {
		s += "  vì sao: " + e.Why + "\n"
	}
	if e.Next != "" {
		s += "  làm gì tiếp: " + e.Next + "\n"
	}
	s += "  " + e.Code + "\n"
	return s
}
