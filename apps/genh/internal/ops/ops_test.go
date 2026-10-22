package ops

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/secretgen"
)

// testComposePath dựng một cây thư mục deploy/compose.yaml tối thiểu dưới
// t.TempDir() — đủ để env.LocatePath() tìm thấy, không cần nội dung YAML
// thật trừ khi test cụ thể cần compose.Load đọc service.
func testComposePath(t *testing.T, yaml string) string {
	t.Helper()
	dir := t.TempDir()
	deployDir := filepath.Join(dir, "deploy")
	if err := os.MkdirAll(deployDir, 0o755); err != nil {
		t.Fatalf("MkdirAll: %v", err)
	}
	if yaml == "" {
		yaml = "name: gen-harness\n"
	}
	path := filepath.Join(deployDir, "compose.yaml")
	if err := os.WriteFile(path, []byte(yaml), 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}
	return path
}

// testEnv dựng một *Env có bí mật thật (secretgen.Ensure) và locate trỏ tới
// composePath giả — InstallDir = thư mục cha của composePath/deploy để
// ConfigDir() ("<InstallDir>/config") đúng nơi secretgen.Ensure đã ghi.
func testEnv(t *testing.T, composePath string) *Env {
	t.Helper()
	installDir := filepath.Dir(filepath.Dir(composePath)) // .../deploy/compose.yaml -> ...
	if _, err := secretgen.Ensure(filepath.Join(installDir, "config")); err != nil {
		t.Fatalf("secretgen.Ensure: %v", err)
	}
	return &Env{
		InstallDir: installDir,
		Port:       18443,
		locate:     func(string) (string, error) { return composePath, nil },
	}
}

// testEnvNoSecrets dựng một *Env SẠCH — thư mục cài đặt tồn tại nhưng chưa
// từng chạy secretgen.Ensure, dùng để test "chưa cài" (genh open, mọi lệnh
// LoadSecrets).
func testEnvNoSecrets(t *testing.T, composePath string) *Env {
	t.Helper()
	installDir := filepath.Dir(filepath.Dir(composePath))
	return &Env{
		InstallDir: installDir,
		Port:       18443,
		locate:     func(string) (string, error) { return composePath, nil },
	}
}
