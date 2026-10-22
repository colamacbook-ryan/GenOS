package ops

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/secretgen"
)

// TestEnvOverlay_HasMinioPlaceholders_ForOldComposeInterpolation kiểm mục #4
// v0.1.3: compose.yaml v0.1.0 (giữ nguyên ở testdata/compose-v0.1.0.yaml,
// `git show v0.1.0:deploy/compose.yaml`) bắt buộc ${MINIO_ROOT_PASSWORD:?...}
// dù MinIO đã bị bỏ khỏi secretgen.Bundle từ v0.1.1 — thiếu biến này làm MỌI
// lệnh `docker compose` (kể cả genh status) trên một máy CHƯA đồng bộ
// compose.yaml lỗi ngay ở bước nội suy. EnvOverlay phải LUÔN thêm giá trị
// giữ chỗ không rỗng cho biến này (và MINIO_ROOT_USER, tuy có default trong
// compose.yaml, thêm cho chắc).
func TestEnvOverlay_HasMinioPlaceholders_ForOldComposeInterpolation(t *testing.T) {
	env := EnvOverlay(secretgen.Bundle{
		DBPassword:    "db-pass",
		AppDBPassword: "app-pass",
		SetupToken:    "setup",
		BackupKey:     "backup-key",
	})

	byKey := map[string]string{}
	for _, kv := range env {
		parts := strings.SplitN(kv, "=", 2)
		if len(parts) == 2 {
			byKey[parts[0]] = parts[1]
		}
	}

	if v, ok := byKey["MINIO_ROOT_PASSWORD"]; !ok || v == "" {
		t.Errorf("EnvOverlay phải luôn có MINIO_ROOT_PASSWORD không rỗng (giữ chỗ cho compose.yaml cũ), được %q (có=%v)", v, ok)
	}
	if v, ok := byKey["MINIO_ROOT_USER"]; !ok || v == "" {
		t.Errorf("EnvOverlay phải luôn có MINIO_ROOT_USER không rỗng, được %q (có=%v)", v, ok)
	}
}

// TestEnvOverlay_OldComposeV010_ParsesWithDockerComposeConfig kiểm mục #4
// v0.1.3 bằng chính `docker compose config` thật (không cần daemon) trên
// testdata/compose-v0.1.0.yaml — trước khi sửa, lệnh này báo lỗi thật
// "required variable MINIO_ROOT_PASSWORD is missing" (đã tái hiện trong lúc
// điều tra). Bỏ qua nếu máy chạy test không có docker CLI (sandbox CI có thể
// không có).
func TestEnvOverlay_OldComposeV010_ParsesWithDockerComposeConfig(t *testing.T) {
	if _, err := exec.LookPath("docker"); err != nil {
		t.Skip("không có docker CLI trên máy chạy test — bỏ qua kiểm bằng docker compose config thật")
	}

	composePath, err := filepath.Abs("testdata/compose-v0.1.0.yaml")
	if err != nil {
		t.Fatalf("filepath.Abs: %v", err)
	}

	envOverlay := EnvOverlay(secretgen.Bundle{
		DBPassword:    "db-pass",
		AppDBPassword: "app-pass",
		SetupToken:    "setup",
		BackupKey:     "backup-key",
	})

	cmd := exec.Command("docker", "compose", "-f", composePath, "config", "--quiet")
	// Kế thừa toàn bộ môi trường tiến trình test (PATH, DOCKER_HOST…) rồi
	// thêm overlay đè lên — dockercli.ExecRunner thật cũng làm vậy (xem
	// buildCmd), không phải môi trường rỗng-trừ-overlay.
	cmd.Env = append(os.Environ(), envOverlay...)

	out, err := cmd.CombinedOutput()
	if err != nil {
		if strings.Contains(string(out), "docker: 'compose' is not a docker command") {
			t.Skip("plugin `docker compose` không có trên máy chạy test — bỏ qua")
		}
		t.Fatalf("docker compose config thất bại dù đã có đủ placeholder MinIO — %v\n%s", err, out)
	}
}
