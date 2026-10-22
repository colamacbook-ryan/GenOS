package hostlink

import (
	"encoding/json"
	"path/filepath"
)

// NetworkStatusFile (v0.1.46, F-21/F-27): genh ghi mỗi khi cấu hình truy cập từ
// xa đổi hoặc được kiểm (genh remote/status/update) — Console đọc để hiện "Truy
// cập từ xa" và mở chuông "Cổng đang mở cho cả mạng". Không chứa bí mật.
const NetworkStatusFile = "network-status.json"

// NetworkStatus là nội dung run/network-status.json (HỢP ĐỒNG với apps/api —
// giữ đúng tên khoá).
type NetworkStatus struct {
	Schema      int    `json:"schema"`       // luôn 1
	Mode        string `json:"mode"`         // local | lan | lan_legacy | tailscale | cloudflare
	BindAddr    string `json:"bind_addr"`    // 127.0.0.1 | 0.0.0.0
	SiteAddress string `json:"site_address"` // rỗng nếu không có
	PublicURL   string `json:"public_url"`   // https://…
	Port        int    `json:"port"`
	CheckedAt   string `json:"checked_at"` // RFC3339 UTC
}

// NetworkStatusPath là đường dẫn tệp trong hộp thư.
func NetworkStatusPath(installDir string) string {
	return filepath.Join(Dir(installDir), NetworkStatusFile)
}

// WriteNetworkStatus ghi nguyên tử run/network-status.json (Schema 0 → 1,
// CheckedAt rỗng → giờ hiện tại).
func WriteNetworkStatus(installDir string, st NetworkStatus) error {
	if err := EnsureDir(installDir); err != nil {
		return err
	}
	if st.Schema == 0 {
		st.Schema = 1
	}
	if st.CheckedAt == "" {
		st.CheckedAt = now()
	}
	return writeJSON(NetworkStatusPath(installDir), st)
}

// ReadNetworkStatus đọc run/network-status.json AN TOÀN (readStateFile).
func ReadNetworkStatus(installDir string) (NetworkStatus, error) {
	var st NetworkStatus
	b, err := readStateFile(NetworkStatusPath(installDir), false)
	if err != nil {
		return st, err
	}
	return st, json.Unmarshal(b, &st)
}
