package install

import (
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"testing"
)

// v0.1.40 (F-12): cài mới sinh secrets/gh_offsite_key ("Khoá khôi phục") đúng định
// dạng 6×5 base32 có '-', quyền 0644, không ghi đè ở lần chạy sau.
func TestEnsureComposeSecretFiles_SinhKhoaKhoiPhuc(t *testing.T) {
	composePath := testComposePath(t)
	secretsDir := filepath.Join(filepath.Dir(composePath), "..", "secrets")
	res, err := secretsResult(testEnvWithSecrets(t))
	if err != nil {
		t.Fatalf("secretsResult: %v", err)
	}
	if err := ensureComposeSecretFiles(composePath, res); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(secretsDir, "gh_offsite_key")
	key, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("thiếu gh_offsite_key: %v", err)
	}
	if !regexp.MustCompile(`^[A-Z2-7]{5}(-[A-Z2-7]{5}){5}$`).Match(key) {
		t.Fatalf("gh_offsite_key sai định dạng: %q", key)
	}
	if runtime.GOOS != "windows" {
		if fi, _ := os.Stat(path); fi.Mode().Perm() != 0o644 {
			t.Fatalf("quyền gh_offsite_key = %v, muốn 0644", fi.Mode().Perm())
		}
	}
	if err := ensureComposeSecretFiles(composePath, res); err != nil {
		t.Fatal(err)
	}
	if again, _ := os.ReadFile(path); string(again) != string(key) {
		t.Fatal("gh_offsite_key không được sinh lại ở lần chạy sau")
	}
}
