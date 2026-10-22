package hostlink

import (
	"encoding/json"
	"path/filepath"
)

// AutostartStatusFile (v0.1.37, F-73): genh ghi khi status/doctor/update — Docker
// và linger (systemd --user) có tự chạy lại khi bật máy không, để Console nhắc
// Owner trước khi máy khởi động lại mà dịch vụ không tự lên. Không chứa lệnh hay
// bí mật: API tự ghép lệnh sửa từ chuỗi cố định.
const AutostartStatusFile = "autostart-status.json"

// AutostartStatus là nội dung run/autostart-status.json (hợp đồng với apps/api —
// giữ đúng tên khoá).
type AutostartStatus struct {
	OS             string `json:"os"`              // linux | darwin | windows
	Linger         string `json:"linger"`          // yes | no | unknown | not_applicable
	LingerRequired bool   `json:"linger_required"` // có thứ gì cần systemd --user chạy khi chưa đăng nhập
	DockerEnabled  string `json:"docker_enabled"`  // yes | no | unknown | not_applicable
	DockerMode     string `json:"docker_mode"`     // system | rootless | desktop | unknown
	CheckedAt      string `json:"checked_at"`      // RFC3339
}

// AutostartStatusPath là đường dẫn tệp trong hộp thư.
func AutostartStatusPath(installDir string) string {
	return filepath.Join(Dir(installDir), AutostartStatusFile)
}

// WriteAutostartStatus ghi nguyên tử run/autostart-status.json (CheckedAt rỗng
// → giờ hiện tại).
func WriteAutostartStatus(installDir string, st AutostartStatus) error {
	if err := EnsureDir(installDir); err != nil {
		return err
	}
	if st.CheckedAt == "" {
		st.CheckedAt = now()
	}
	return writeJSON(AutostartStatusPath(installDir), st)
}

// ReadAutostartStatus đọc run/autostart-status.json AN TOÀN (readStateFile).
func ReadAutostartStatus(installDir string) (AutostartStatus, error) {
	var st AutostartStatus
	b, err := readStateFile(AutostartStatusPath(installDir), false)
	if err != nil {
		return st, err
	}
	return st, json.Unmarshal(b, &st)
}
