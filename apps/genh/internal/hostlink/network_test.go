package hostlink

import (
	"encoding/json"
	"os"
	"runtime"
	"testing"
	"time"
)

func TestWriteNetworkStatus_Contract(t *testing.T) {
	dir := t.TempDir()
	if err := WriteNetworkStatus(dir, NetworkStatus{
		Mode: "tailscale", BindAddr: "127.0.0.1", SiteAddress: "a.ts.net", PublicURL: "https://a.ts.net", Port: 8443,
	}); err != nil {
		t.Fatal(err)
	}
	b, err := os.ReadFile(NetworkStatusPath(dir))
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"schema", "mode", "bind_addr", "site_address", "public_url", "port", "checked_at"} {
		if _, ok := m[k]; !ok {
			t.Errorf("thiếu khoá %q", k)
		}
	}
	if len(m) != 7 {
		t.Errorf("số khoá = %d, muốn 7: %v", len(m), m)
	}
	if m["schema"].(float64) != 1 || m["mode"] != "tailscale" || m["port"].(float64) != 8443 {
		t.Errorf("giá trị sai: %v", m)
	}
	if _, err := time.Parse(time.RFC3339, m["checked_at"].(string)); err != nil {
		t.Errorf("checked_at không RFC3339: %v", err)
	}
	// Windows không có bit quyền POSIX (file ghi được báo 0666).
	if runtime.GOOS != "windows" {
		if fi, _ := os.Stat(NetworkStatusPath(dir)); fi.Mode().Perm() != 0o644 {
			t.Errorf("quyền = %v", fi.Mode().Perm())
		}
	}

	// site_address rỗng vẫn có mặt (chuỗi rỗng, không bỏ khoá).
	if err := WriteNetworkStatus(dir, NetworkStatus{Mode: "local", BindAddr: "127.0.0.1", PublicURL: "https://localhost:8443", Port: 8443}); err != nil {
		t.Fatal(err)
	}
	got, err := ReadNetworkStatus(dir)
	if err != nil || got.Mode != "local" || got.SiteAddress != "" {
		t.Errorf("đọc lại = %+v %v", got, err)
	}
}
