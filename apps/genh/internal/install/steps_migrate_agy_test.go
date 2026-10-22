package install

import (
	"encoding/base64"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

// v0.1.63 (H3): cài mới sinh secrets/gh_agy_key — khoá HMAC api/worker ↔ agy-box:
// 32 byte base64, KHÁC các khoá khác, quyền 0644 (box chạy uid khác), không ghi đè.
func TestEnsureComposeSecretFiles_SinhAgyKey(t *testing.T) {
	composePath := testComposePath(t)
	secretsDir := filepath.Join(filepath.Dir(composePath), "..", "secrets")
	res, err := secretsResult(testEnvWithSecrets(t))
	if err != nil {
		t.Fatalf("secretsResult: %v", err)
	}
	if err := ensureComposeSecretFiles(composePath, res); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(secretsDir, "gh_agy_key")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("thiếu gh_agy_key sau cài mới: %v", err)
	}
	if k, err := base64.StdEncoding.DecodeString(string(raw)); err != nil || len(k) != 32 {
		t.Fatalf("gh_agy_key phải là 32 byte base64: %q (%v)", raw, err)
	}
	for _, other := range []string{"gh_bridge_key", "gh_browser_key"} {
		o, _ := os.ReadFile(filepath.Join(secretsDir, other))
		if string(o) == string(raw) {
			t.Fatalf("gh_agy_key trùng %s", other)
		}
	}
	if runtime.GOOS != "windows" {
		if fi, _ := os.Stat(path); fi.Mode().Perm() != 0o644 {
			t.Fatalf("quyền gh_agy_key = %v, muốn 0644", fi.Mode().Perm())
		}
	}
	// Chạy lại (thử lại Bước 6/7) không đổi khoá đang được box dùng.
	if err := ensureComposeSecretFiles(composePath, res); err != nil {
		t.Fatal(err)
	}
	if again, _ := os.ReadFile(path); string(again) != string(raw) {
		t.Fatal("gh_agy_key không được sinh lại ở lần chạy sau")
	}
}

// Cài lại đè lên bản dở (tệp khoá rỗng hoặc thư mục rỗng do Docker tạo) vẫn ra khoá dùng được.
func TestEnsureComposeSecretFiles_AgyKeyDoDang(t *testing.T) {
	for name, prep := range map[string]func(string) error{
		"tệp rỗng":     func(p string) error { return os.WriteFile(p, nil, 0o644) },
		"thư mục rỗng": func(p string) error { return os.Mkdir(p, 0o755) },
	} {
		t.Run(name, func(t *testing.T) {
			composePath := testComposePath(t)
			secretsDir := filepath.Join(filepath.Dir(composePath), "..", "secrets")
			if err := os.MkdirAll(secretsDir, 0o700); err != nil {
				t.Fatal(err)
			}
			if err := prep(filepath.Join(secretsDir, "gh_agy_key")); err != nil {
				t.Fatal(err)
			}
			res, err := secretsResult(testEnvWithSecrets(t))
			if err != nil {
				t.Fatal(err)
			}
			if err := ensureComposeSecretFiles(composePath, res); err != nil {
				t.Fatal(err)
			}
			raw, err := os.ReadFile(filepath.Join(secretsDir, "gh_agy_key"))
			if err != nil {
				t.Fatalf("gh_agy_key phải là tệp đọc được: %v", err)
			}
			if k, err := base64.StdEncoding.DecodeString(string(raw)); err != nil || len(k) != 32 {
				t.Fatalf("gh_agy_key phải được sinh lại 32 byte base64: %q (%v)", raw, err)
			}
		})
	}
}
