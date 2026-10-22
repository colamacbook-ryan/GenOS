//go:build !windows

// Bit quyền POSIX (0644) chỉ có nghĩa trên Linux/macOS: trên Windows,
// os.FileMode của tệp thường luôn là 0666 (hoặc 0444 nếu chỉ đọc) bất kể
// quyền đã xin khi tạo. Vì vậy phần kiểm quyền tách khỏi
// TestEnsureAuxSecrets_FillsMissingBrowserKeyOnly (chạy trên mọi hệ điều hành)
// sang tệp này.

package ops

import (
	"os"
	"path/filepath"
	"testing"
)

// gh_browser_key phải 0644: thư mục secrets/ 0700 chặn user khác trên host,
// còn tệp phải đọc được bởi tiến trình trong container (uid khác).
func TestEnsureAuxSecrets_BrowserKeyMode0644(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "deploy"), 0o755); err != nil {
		t.Fatal(err)
	}
	secrets := filepath.Join(root, "secrets")
	if err := os.MkdirAll(secrets, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := ensureAuxSecrets(filepath.Join(root, "deploy", "compose.yaml")); err != nil {
		t.Fatal(err)
	}
	st, err := os.Stat(filepath.Join(secrets, "gh_browser_key"))
	if err != nil {
		t.Fatalf("thiếu gh_browser_key: %v", err)
	}
	if st.Mode().Perm() != 0o644 {
		t.Fatalf("quyền gh_browser_key = %v, muốn 0644", st.Mode().Perm())
	}
}

// gh_agy_key (v0.1.63): cùng quy tắc 0644 như gh_browser_key — agy-box chạy uid khác phải đọc được.
func TestEnsureAuxSecrets_AgyKeyMode0644(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "deploy"), 0o755); err != nil {
		t.Fatal(err)
	}
	secrets := filepath.Join(root, "secrets")
	if err := os.MkdirAll(secrets, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := ensureAuxSecrets(filepath.Join(root, "deploy", "compose.yaml")); err != nil {
		t.Fatal(err)
	}
	st, err := os.Stat(filepath.Join(secrets, "gh_agy_key"))
	if err != nil {
		t.Fatalf("thiếu gh_agy_key: %v", err)
	}
	if st.Mode().Perm() != 0o644 {
		t.Fatalf("quyền gh_agy_key = %v, muốn 0644", st.Mode().Perm())
	}
}
