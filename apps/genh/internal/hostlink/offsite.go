package hostlink

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
)

// ─── Bản sao ngoài máy (v0.1.40, F-12) ──────────────────────────────────────
//
//	run/offsite-status.json   ← genh ghi (nguyên tử) mỗi lần `genh offsite run|set|disable`
//	run/request/offsite.json  ← api ghi khi Owner chọn nơi lưu / bấm "Sao lưu ra ổ ngoài
//	                            ngay" / tắt; genh XOÁ trước khi làm
//
// Cấu hình thật (đường dẫn đích, allow_same_disk) KHÔNG nằm ở đây mà ở
// <gốc cài đặt>/config/offsite.json (0600, chỉ genh đọc) — api ghi được run/ nên
// mọi thứ đọc từ run/ đều coi là KHÔNG tin cậy.

// OffsiteStatusFile là tệp trạng thái bản sao ngoài máy trong hộp thư.
const OffsiteStatusFile = "offsite-status.json"

// OffsiteRequestFile là tệp yêu cầu từ Console trong run/request/.
const OffsiteRequestFile = "offsite.json"

// OffsiteStatusSchema là phiên bản khuôn offsite-status.json (hợp đồng với apps/api).
const OffsiteStatusSchema = 1

// Các giá trị "state" của offsite-status.json (hợp đồng với apps/api — giữ đúng chữ).
const (
	OffsiteStateOK            = "ok"
	OffsiteStateFailed        = "failed"
	OffsiteStateNotMounted    = "not_mounted"
	OffsiteStateNotConfigured = "not_configured"
	OffsiteStateRunning       = "running"
	OffsiteStateSkippedBusy   = "skipped_busy"
)

// OffsiteStatus là nội dung run/offsite-status.json — KHÔNG omitempty: API đọc
// đủ mọi khoá (chuỗi rỗng = chưa có). Không chứa khoá khôi phục, chỉ key_id
// (8 ký tự hex đầu sha256 của khoá).
type OffsiteStatus struct {
	Schema        int    `json:"schema"`
	Configured    bool   `json:"configured"`
	Dest          string `json:"dest"`
	State         string `json:"state"`
	ErrorCode     string `json:"error_code"`
	LastAttemptAt string `json:"last_attempt_at"`
	LastSuccessAt string `json:"last_success_at"`
	LastFile      string `json:"last_file"`
	LastSizeBytes int64  `json:"last_size_bytes"`
	Verified      bool   `json:"verified"`
	Kept          int    `json:"kept"`
	Schedule      string `json:"schedule"` // systemd | cron | launchd | schtasks | ""
	KeyID         string `json:"key_id"`
}

// OffsiteStatusPath là đường dẫn run/offsite-status.json.
func OffsiteStatusPath(installDir string) string {
	return filepath.Join(Dir(installDir), OffsiteStatusFile)
}

// WriteOffsiteStatus ghi nguyên tử run/offsite-status.json (như
// WriteAutostartStatus — tệp tạm tên ngẫu nhiên rồi rename, 0644 để api đọc).
func WriteOffsiteStatus(installDir string, st OffsiteStatus) error {
	if err := EnsureDir(installDir); err != nil {
		return err
	}
	st.Schema = OffsiteStatusSchema
	return writeJSON(OffsiteStatusPath(installDir), st)
}

// ReadOffsiteStatus đọc run/offsite-status.json AN TOÀN (readStateFile — không
// theo symlink, giới hạn kích thước). Chỉ dùng để hiển thị/cảnh báo.
func ReadOffsiteStatus(installDir string) (OffsiteStatus, error) {
	var st OffsiteStatus
	b, err := readStateFile(OffsiteStatusPath(installDir), false)
	if err != nil {
		return st, err
	}
	return st, json.Unmarshal(b, &st)
}

// OffsiteRequest là nội dung run/request/offsite.json do api ghi. MỌI trường
// đều không tin cậy (api ghi được run/) — genh kiểm lại path bằng đúng bộ kiểm của CLI.
type OffsiteRequest struct {
	ID          string `json:"id,omitempty"`
	Action      string `json:"action"` // set | run | disable
	Path        string `json:"path,omitempty"`
	RequestedAt string `json:"requested_at,omitempty"`
	By          string `json:"by,omitempty"`
}

// OffsiteRequestPath là đường dẫn run/request/offsite.json.
func OffsiteRequestPath(installDir string) string {
	return filepath.Join(RequestDirPath(installDir), OffsiteRequestFile)
}

// HasOffsiteRequest báo Console có đang yêu cầu việc bản sao ngoài máy không
// (Lstat — symlink cũng tính là "có" để genh dọn nó đi).
func HasOffsiteRequest(installDir string) bool {
	_, err := os.Lstat(OffsiteRequestPath(installDir))
	return err == nil
}

// ReadOffsiteRequest đọc run/request/offsite.json AN TOÀN (readRequestFile). Lỗi
// nếu thiếu, không phải tệp thường, quá lớn, sai chủ sở hữu hoặc JSON hỏng.
func ReadOffsiteRequest(installDir string) (OffsiteRequest, error) {
	var r OffsiteRequest
	b, err := readRequestFile(installDir, OffsiteRequestPath(installDir))
	if err != nil {
		return r, err
	}
	if err := json.Unmarshal(b, &r); err != nil {
		return r, err
	}
	return r, nil
}

// ClearOffsiteRequest xoá tệp yêu cầu (Remove không đi theo symlink). Không có
// tệp không phải lỗi.
func ClearOffsiteRequest(installDir string) error {
	err := os.Remove(OffsiteRequestPath(installDir))
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return nil
}
