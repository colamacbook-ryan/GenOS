package install

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/secretgen"
)

// detectTestComposeYAML là compose.yaml tối thiểu đủ để compose.Locate tìm
// thấy — nội dung không quan trọng, DetectExistingInstall chỉ cần biết
// đường dẫn để gọi `docker compose ps`.
const detectTestComposeYAML = "name: gen-harness\nservices:\n  api:\n    image: x\n"

func setupDetectInstallDir(t *testing.T, withSecrets bool) string {
	t.Helper()
	installDir := t.TempDir()
	deployDir := filepath.Join(installDir, "deploy")
	if err := os.MkdirAll(deployDir, 0o755); err != nil {
		t.Fatalf("MkdirAll: %v", err)
	}
	if err := os.WriteFile(filepath.Join(deployDir, "compose.yaml"), []byte(detectTestComposeYAML), 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}
	if withSecrets {
		if _, err := secretgen.Ensure(filepath.Join(installDir, "config")); err != nil {
			t.Fatalf("secretgen.Ensure: %v", err)
		}
	}
	return installDir
}

func TestDetectExistingInstall_NoSecrets_ReturnsFalse(t *testing.T) {
	installDir := setupDetectInstallDir(t, false)
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("ps", "-a"), Output: []byte(`[{"Service":"api","State":"running","Health":""}]`)},
	}}

	if DetectExistingInstall(context.Background(), fr, installDir) {
		t.Error("chưa có secrets.json — PHẢI trả về false (chưa cài xong), không được chặn genh install")
	}
	if len(fr.Calls) != 0 {
		t.Errorf("không cần gọi docker khi chưa có secrets.json, Calls=%+v", fr.Calls)
	}
}

func TestDetectExistingInstall_SecretsButNoApiContainer_ReturnsFalse(t *testing.T) {
	installDir := setupDetectInstallDir(t, true)
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("ps", "-a"), Output: []byte(`[]`)},
	}}

	if DetectExistingInstall(context.Background(), fr, installDir) {
		t.Error("có secrets.json nhưng CHƯA có container api (dừng giữa Bước 4 và Bước 5) — PHẢI trả về false, không chặn `genh install` chạy tiếp bước dở")
	}
}

func TestDetectExistingInstall_SecretsAndApiContainerExists_ReturnsTrue(t *testing.T) {
	installDir := setupDetectInstallDir(t, true)
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("ps", "-a"), Output: []byte(`[{"Service":"api","State":"exited","Health":""}]`)},
	}}

	if !DetectExistingInstall(context.Background(), fr, installDir) {
		t.Error("có secrets.json VÀ container api đã tồn tại (dù đã exited) — PHẢI trả về true (đã cài xong), `genh install` phải dừng lại")
	}
}

func TestDetectExistingInstall_DockerPsFails_ReturnsFalse(t *testing.T) {
	installDir := setupDetectInstallDir(t, true)
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("ps", "-a"), Err: dockerNotAvailableErr{}},
	}}

	if DetectExistingInstall(context.Background(), fr, installDir) {
		t.Error("docker/daemon lỗi khi kiểm — PHẢI trả về false (không chặn nhầm), để chính 8 Bước tự báo lỗi rõ hơn")
	}
}

func TestDetectExistingInstall_EmptyInstallDir_ReturnsFalse(t *testing.T) {
	if DetectExistingInstall(context.Background(), &fake.Runner{}, "") {
		t.Error("installDir rỗng — PHẢI trả về false")
	}
}

// dockerNotAvailableErr mô phỏng lỗi bất kỳ khi gọi docker (daemon chưa
// chạy, không có docker compose…) — chỉ cần khác nil.
type dockerNotAvailableErr struct{}

func (dockerNotAvailableErr) Error() string { return "docker: daemon not running" }
