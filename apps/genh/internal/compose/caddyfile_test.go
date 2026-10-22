package compose

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

// Bản nhúng phải khớp deploy/proxy/Caddyfile — sửa Caddyfile ở deploy/ mà quên
// chép sang embedded_Caddyfile thì test này đỏ.
func TestEmbeddedCaddyfileMatchesRepo(t *testing.T) {
	repo, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "deploy", "proxy", "Caddyfile"))
	if err != nil {
		t.Fatalf("đọc deploy/proxy/Caddyfile: %v", err)
	}
	if !bytes.Equal(repo, embeddedCaddyfile) {
		t.Fatal("apps/genh/internal/compose/embedded_Caddyfile lệch deploy/proxy/Caddyfile — chép lại cho khớp")
	}
}

func isolateLocate(t *testing.T) string {
	t.Helper()
	t.Setenv(EnvOverrideVar, "")
	// t.Chdir khôi phục cwd cũ trong Cleanup đăng ký SAU TempDir của chính nó
	// ⇒ chạy TRƯỚC khi thư mục tạm bị xoá (Cleanup chạy ngược thứ tự). Trước
	// đây Cleanup Chdir đăng ký trước TempDir nên lúc xoá, cwd vẫn nằm trong
	// thư mục tạm — Windows từ chối xoá thư mục đang là cwd của tiến trình
	// ("being used by another process").
	t.Chdir(t.TempDir())
	return t.TempDir()
}

// Lỗi e2e release v0.1.7: genh chỉ ghi compose.yaml, thiếu Caddyfile
// → Docker tạo thư mục rỗng thay thế → proxy không khởi động.
func TestLocate_WritesCaddyfileNextToEmbeddedCompose(t *testing.T) {
	installDir := isolateLocate(t)
	if _, err := Locate(installDir); err != nil {
		t.Fatalf("Locate: %v", err)
	}
	got, err := os.ReadFile(filepath.Join(installDir, "deploy", "proxy", "Caddyfile"))
	if err != nil {
		t.Fatalf("không có proxy/Caddyfile cạnh compose.yaml: %v", err)
	}
	if !bytes.Equal(got, embeddedCaddyfile) {
		t.Error("Caddyfile ghi ra khác bản nhúng")
	}
}

// Máy đã cài lỗi bằng bản cũ: compose.yaml có sẵn, Caddyfile là thư mục rỗng
// do Docker tạo. LocateAndSync (install/update) phải tự sửa.
func TestLocateAndSync_ReplacesDockerCreatedCaddyfileDir(t *testing.T) {
	installDir := isolateLocate(t)
	deployDir := filepath.Join(installDir, "deploy")
	if err := os.MkdirAll(filepath.Join(deployDir, "proxy", "Caddyfile"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(deployDir, "compose.yaml"), embeddedComposeYAML, 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := LocateAndSync(installDir); err != nil {
		t.Fatalf("LocateAndSync: %v", err)
	}
	path := filepath.Join(deployDir, "proxy", "Caddyfile")
	info, err := os.Stat(path)
	if err != nil || info.IsDir() {
		t.Fatalf("Caddyfile phải là tệp sau khi sửa (err=%v)", err)
	}
}

// Owner tự sửa Caddyfile: lệnh thường (Locate) không đụng; install/update
// (LocateAndSync) ghi bản mới và giữ bản cũ ở .bak.
func TestCaddyfile_EditedOnlyReplacedBySyncWithBackup(t *testing.T) {
	installDir := isolateLocate(t)
	if _, err := Locate(installDir); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(installDir, "deploy", "proxy", "Caddyfile")
	custom := []byte("# sửa tay\n")
	if err := os.WriteFile(path, custom, 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := Locate(installDir); err != nil {
		t.Fatal(err)
	}
	if got, _ := os.ReadFile(path); !bytes.Equal(got, custom) {
		t.Fatal("Locate không được ghi đè Caddyfile Owner đã sửa")
	}
	if _, err := LocateAndSync(installDir); err != nil {
		t.Fatal(err)
	}
	if got, _ := os.ReadFile(path); !bytes.Equal(got, embeddedCaddyfile) {
		t.Error("LocateAndSync phải đồng bộ Caddyfile về bản nhúng")
	}
	if got, _ := os.ReadFile(path + ".bak"); !bytes.Equal(got, custom) {
		t.Error("LocateAndSync phải giữ bản Owner đã sửa ở .bak")
	}
}
