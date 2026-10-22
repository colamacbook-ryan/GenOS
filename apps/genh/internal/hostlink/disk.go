package hostlink

import (
	"encoding/json"
	"os"
	"path/filepath"
)

// DiskStatusFile ghi kết quả kiểm chỗ trống trên đĩa mỗi lần `genh update`
// chạy (F-11). Chuông "đĩa sắp đầy" của Console (v0.1.36) đọc tệp này — GIỮ
// NGUYÊN tên khoá JSON.
const DiskStatusFile = "disk-status.json"

// DiskStatus là nội dung disk-status.json (không chứa bí mật).
type DiskStatus struct {
	State        string `json:"state"` // ok | low
	FreeBytes    uint64 `json:"free_bytes"`
	MinBytes     uint64 `json:"min_bytes"`
	Path         string `json:"path"`
	PrunedImages int    `json:"pruned_images,omitempty"`
	CheckedAt    string `json:"checked_at"`
}

func diskStatusPath(installDir string) string {
	return filepath.Join(Dir(installDir), DiskStatusFile)
}

// WriteDiskStatus ghi disk-status.json (CheckedAt rỗng → thời điểm hiện tại).
func WriteDiskStatus(installDir string, s DiskStatus) error {
	if err := EnsureDir(installDir); err != nil {
		return err
	}
	if s.CheckedAt == "" {
		s.CheckedAt = now()
	}
	return writeJSON(diskStatusPath(installDir), s)
}

// ReadDiskStatus đọc disk-status.json (lỗi nếu chưa có).
func ReadDiskStatus(installDir string) (DiskStatus, error) {
	var s DiskStatus
	raw, err := os.ReadFile(diskStatusPath(installDir))
	if err != nil {
		return s, err
	}
	return s, json.Unmarshal(raw, &s)
}
