package hostlink

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// run/offsite-status.json: ghi nguyên tử (không để lại tệp tạm), đủ mọi khoá của
// hợp đồng với API (kể cả giá trị rỗng/false), schema = 1, quyền 0644.
func TestOffsiteStatus_RoundTripAtomic(t *testing.T) {
	root := t.TempDir()
	if _, err := ReadOffsiteStatus(root); err == nil {
		t.Fatal("chưa có tệp thì phải lỗi")
	}
	st := OffsiteStatus{Configured: true, Dest: "/media/usb", State: OffsiteStateOK, LastSuccessAt: "2026-10-04T05:41:00Z",
		LastFile: "/media/usb/gen-harness-offsite/gen-harness-20261004T054100Z.ghbundle", LastSizeBytes: 1234, Verified: true, Kept: 2,
		Schedule: "systemd", KeyID: "abcd1234"}
	if err := WriteOffsiteStatus(root, st); err != nil {
		t.Fatal(err)
	}
	got, err := ReadOffsiteStatus(root)
	if err != nil {
		t.Fatal(err)
	}
	st.Schema = 1
	if got != st {
		t.Fatalf("đọc lại = %+v, muốn %+v", got, st)
	}
	raw, _ := os.ReadFile(OffsiteStatusPath(root))
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"schema", "configured", "dest", "state", "error_code", "last_attempt_at", "last_success_at",
		"last_file", "last_size_bytes", "verified", "kept", "schedule", "key_id"} {
		if _, ok := m[k]; !ok {
			t.Errorf("thiếu khoá %q (hợp đồng API — không omitempty): %s", k, raw)
		}
	}
	entries, _ := os.ReadDir(Dir(root))
	for _, e := range entries {
		if strings.Contains(e.Name(), ".tmp") {
			t.Errorf("còn sót tệp tạm %s", e.Name())
		}
	}
	if runtime.GOOS != "windows" {
		fi, _ := os.Stat(OffsiteStatusPath(root))
		if fi.Mode().Perm() != 0o644 {
			t.Errorf("quyền = %v, muốn 0644 (api uid khác phải đọc được)", fi.Mode().Perm())
		}
	}
}

// Đọc trạng thái KHÔNG theo symlink (run/ 0777 — ai ghi được run/ cũng cài được symlink).
func TestReadOffsiteStatus_RefusesSymlink(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink cần quyền đặc biệt trên Windows")
	}
	root := t.TempDir()
	if err := EnsureDir(root); err != nil {
		t.Fatal(err)
	}
	secret := filepath.Join(t.TempDir(), "secret.json")
	_ = os.WriteFile(secret, []byte(`{"state":"ok","key_id":"lo-bi-mat"}`), 0o600)
	if err := os.Symlink(secret, OffsiteStatusPath(root)); err != nil {
		t.Fatal(err)
	}
	if _, err := ReadOffsiteStatus(root); err == nil {
		t.Fatal("phải từ chối symlink")
	}
}

func TestOffsiteRequest_ReadClear(t *testing.T) {
	root := t.TempDir()
	if err := EnsureDir(root); err != nil {
		t.Fatal(err)
	}
	if HasOffsiteRequest(root) {
		t.Fatal("chưa có yêu cầu")
	}
	if err := ClearOffsiteRequest(root); err != nil {
		t.Fatalf("xoá khi không có tệp không được lỗi: %v", err)
	}
	_ = os.WriteFile(OffsiteRequestPath(root), []byte(`{"id":"o1","action":"set","path":"/media/usb","by":"owner"}`), 0o666)
	if !HasOffsiteRequest(root) {
		t.Fatal("phải thấy yêu cầu")
	}
	req, err := ReadOffsiteRequest(root)
	if err != nil || req.Action != "set" || req.Path != "/media/usb" || req.ID != "o1" {
		t.Fatalf("ReadOffsiteRequest = %+v, %v", req, err)
	}
	if err := ClearOffsiteRequest(root); err != nil || HasOffsiteRequest(root) {
		t.Fatalf("ClearOffsiteRequest phải xoá tệp: %v", err)
	}
	_ = os.WriteFile(OffsiteRequestPath(root), []byte(`không phải json`), 0o666)
	if _, err := ReadOffsiteRequest(root); err == nil {
		t.Fatal("JSON hỏng phải lỗi")
	}
}

// Thứ tự xử lý của watcher: update > restore > offsite.
func TestPending_OffsiteSauCung(t *testing.T) {
	root := t.TempDir()
	if err := EnsureDir(root); err != nil {
		t.Fatal(err)
	}
	_ = os.WriteFile(OffsiteRequestPath(root), []byte(`{"action":"run"}`), 0o666)
	if got := Pending(root); got != "offsite" {
		t.Fatalf("Pending = %q, muốn offsite", got)
	}
	_ = os.WriteFile(RestoreRequestPath(root), []byte(`{"key":"k"}`), 0o666)
	if got := Pending(root); got != "restore" {
		t.Fatalf("restore trước offsite, Pending = %q", got)
	}
	_ = os.WriteFile(RequestPath(root), []byte(`{}`), 0o666)
	if got := Pending(root); got != "update" {
		t.Fatalf("update trước tất cả, Pending = %q", got)
	}
}
