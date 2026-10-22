package install

// Mã lỗi tra được (GH-E0xx) theo đúng yêu cầu docs/handoff/05-installer.md
// (mục "Lỗi"): mỗi lỗi hiện chuyện gì xảy ra · vì sao · làm gì tiếp, kèm mã
// này để tra cứu/hỗ trợ. Đánh số theo bước: 0xx dành cho Bước 1 (Kiểm tra
// máy), 1xx cho Bước 4 (Sinh bí mật & cấu hình). Các dải còn lại (2xx Runtime,
// 3xx Tải image, 5xx Khởi động dữ liệu, 6xx Migration, 7xx Khởi động dịch
// vụ, 8xx Hoàn tất) dành cho phiên B khi cài các Step tương ứng — chỉ cần
// thêm hằng số mới, không đụng tới các mã đã có ở đây.
const (
	ErrCodeUnsupportedPlatform = "GH-E001"
	ErrCodeInsufficientRAM     = "GH-E002"
	ErrCodeInsufficientDisk    = "GH-E003"
	ErrCodePortInUse           = "GH-E004"
	ErrCodeNetworkUnreachable  = "GH-E005"

	ErrCodeSecretsWriteFailed = "GH-E010"

	// ErrCodeAlreadyInstalled: `genh install` tự phát hiện máy ĐÃ có một bản
	// cài hoàn chỉnh (xem DetectExistingInstall trong detect.go) — dừng lại
	// TRƯỚC KHI chạy bất kỳ Bước nào, trừ khi có cờ --force. Không thuộc dải
	// nào ở trên (không phải lỗi CỦA một Bước cụ thể — xảy ra trước cả Bước
	// 1) nhưng dùng chung dải 0xx vì cùng là kiểm tra trước khi cài.
	ErrCodeAlreadyInstalled = "GH-E006"

	// 2x — Bước 2 (Chuẩn bị container runtime).
	ErrCodeRuntimeUnsupportedOS      = "GH-E020" // GOOS không nằm trong 3 nền tảng có tự cài runtime
	ErrCodeRuntimeDownloadFailed     = "GH-E021" // tải Docker Engine tĩnh/Colima/Lima/rootfs WSL thất bại
	ErrCodeRuntimeChecksumMismatch   = "GH-E022" // SHA-256 không khớp — dừng, không chạy binary
	ErrCodeRuntimeServiceStartFailed = "GH-E023" // systemd user service / colima start / wsl --import thất bại
	ErrCodeRuntimeNeedsApproval      = "GH-E024" // cần Owner đồng ý (--yes) trước khi tự cài runtime/sudo/UAC

	// 3x — Bước 3 (Tải image).
	ErrCodePullFailed = "GH-E030" // một hoặc nhiều image tải thất bại

	// 5x — Bước 5 (Khởi động dữ liệu).
	ErrCodeComposeNotFound = "GH-E050" // không tìm thấy deploy/compose.yaml
	ErrCodeComposeUpFailed = "GH-E051" // `docker compose up -d` thất bại
	ErrCodeDataNotHealthy  = "GH-E052" // db/redis/objects không healthy trước khi hết thời gian chờ
	ErrCodeComposeParse    = "GH-E053" // không đọc/parse được compose.yaml

	// 6x — Bước 6 (Tạo cấu trúc dữ liệu / migration).
	ErrCodeMigrateFailed = "GH-E060" // `alembic upgrade heads` thất bại trong container api

	// 7x — Bước 7 (Khởi động dịch vụ).
	ErrCodeServicesUpFailed = "GH-E070" // `docker compose up -d` cho api/worker/bridge/web/proxy thất bại
	ErrCodeServiceNotReady  = "GH-E071" // /api/v1/ready không trả 200 trước khi hết thời gian chờ

	// 8x — Bước 8 (Hoàn tất).
	ErrCodeTrustCAFailed  = "GH-E080" // không cài được CA vào kho tin cậy hệ điều hành
	ErrCodeShortcutFailed = "GH-E081" // không tạo được lối tắt desktop/.app/Start Menu
)
