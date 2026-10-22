package install

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
)

// testComposePath dựng một cây thư mục deploy/compose.yaml (không cần nội
// dung thật — migrateStep/servicesStep không tự Load compose.yaml, chỉ dùng
// đường dẫn để suy ra thư mục chứa nó) dưới t.TempDir(), để
// ensureComposeSecretFiles có chỗ ghi secrets/ cạnh deploy/ mà không đụng
// hệ thống tệp thật ngoài sandbox test.
func testComposePath(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	deployDir := filepath.Join(dir, "deploy")
	if err := os.MkdirAll(deployDir, 0o755); err != nil {
		t.Fatalf("MkdirAll: %v", err)
	}
	path := filepath.Join(deployDir, "compose.yaml")
	if err := os.WriteFile(path, []byte("name: gen-harness\n"), 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}
	return path
}

func TestMigrateStep_HappyPath_ReportsAppliedMigrations(t *testing.T) {
	composePath := testComposePath(t)

	fr := &fake.Runner{Responses: []fake.Response{
		{
			Match: fake.MatchArgsContain("run", "--rm", "-T", "--no-deps", migrateServiceName),
			Lines: []string{
				"INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.",
				"INFO  [alembic.runtime.migration] Running upgrade  -> 0001, tạo bảng gốc",
				"INFO  [alembic.runtime.migration] Running upgrade 0001 -> 0002, thêm cột x",
			},
		},
	}}

	step := migrateStep{runner: fr, locate: func(string) (string, error) { return composePath, nil }, timeout: time.Second}

	var progresses []Progress
	rep := ReporterFunc(func(p Progress) { progresses = append(progresses, p) })

	if err := step.Run(context.Background(), testEnvWithSecrets(t), rep); err != nil {
		t.Fatalf("Run: %v", err)
	}

	last := progresses[len(progresses)-1]
	if last.Status != StatusOK || last.Percent != 100 {
		t.Errorf("progress cuối = %+v, muốn StatusOK/100", last)
	}
	if last.Detail != "đã áp dụng 2 migration" {
		t.Errorf("Detail cuối = %q, muốn đúng số migration đếm được từ log thật", last.Detail)
	}

	// Phải thấy ít nhất 2 mốc tiến độ trong lúc chạy (đang chạy -> xong).
	sawRunning := false
	for _, p := range progresses {
		if p.Status == StatusRunning {
			sawRunning = true
		}
	}
	if !sawRunning {
		t.Error("phải báo StatusRunning ít nhất một lần trước khi StatusOK")
	}

	// Lệnh run phải mang đúng env POSTGRES_PASSWORD (dùng chung với Bước 5).
	if len(fr.Calls) != 1 {
		t.Fatalf("số lệnh gọi Runner = %d, muốn đúng 1", len(fr.Calls))
	}
	foundPG := false
	for _, e := range fr.Calls[0].Cmd.Env {
		if e != "" && len(e) > len("POSTGRES_PASSWORD=") && e[:len("POSTGRES_PASSWORD=")] == "POSTGRES_PASSWORD=" {
			foundPG = true
		}
	}
	if !foundPG {
		t.Errorf("lệnh run phải mang POSTGRES_PASSWORD khác rỗng, Env=%v", fr.Calls[0].Cmd.Env)
	}

	// Phải ghi ra tệp Docker secret gh_master_key/gh_bridge_key cạnh deploy/.
	secretsDir := filepath.Join(filepath.Dir(composePath), "..", "secrets")
	for _, name := range []string{"gh_master_key", "gh_bridge_key", "gh_browser_key"} {
		if _, err := os.Stat(filepath.Join(secretsDir, name)); err != nil {
			t.Errorf("thiếu tệp secret %s: %v", name, err)
		}
	}
}

func TestMigrateStep_NoNewMigrations_StillOK(t *testing.T) {
	composePath := testComposePath(t)

	fr := &fake.Runner{Responses: []fake.Response{
		{
			Match: fake.MatchArgsContain("run", "--rm", "-T", "--no-deps", migrateServiceName),
			Lines: []string{"INFO  [alembic.runtime.migration] Context impl PostgresqlImpl."},
		},
	}}

	step := migrateStep{runner: fr, locate: func(string) (string, error) { return composePath, nil }, timeout: time.Second}
	var last Progress
	rep := ReporterFunc(func(p Progress) { last = p })

	if err := step.Run(context.Background(), testEnvWithSecrets(t), rep); err != nil {
		t.Fatalf("Run: %v", err)
	}
	if last.Status != StatusOK {
		t.Errorf("Status = %v, muốn StatusOK", last.Status)
	}
	if last.Detail != "đã ở phiên bản mới nhất (không có migration mới)" {
		t.Errorf("Detail = %q, muốn thông báo idempotent rõ ràng", last.Detail)
	}
}

func TestMigrateStep_RunFails_ReturnsStructuredError(t *testing.T) {
	composePath := testComposePath(t)

	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("run", "--rm"), Err: errors.New("container thoát mã 1")},
	}}

	step := migrateStep{runner: fr, locate: func(string) (string, error) { return composePath, nil }}
	err := step.Run(context.Background(), testEnvWithSecrets(t), ReporterFunc(func(Progress) {}))

	se, ok := err.(*StepError)
	if !ok {
		t.Fatalf("lỗi phải là *StepError, được %T", err)
	}
	if se.Code != ErrCodeMigrateFailed {
		t.Errorf("Code = %q, muốn %q", se.Code, ErrCodeMigrateFailed)
	}
}

func TestMigrateStep_MissingSecrets_ReturnsStructuredError(t *testing.T) {
	composePath := testComposePath(t)
	fr := &fake.Runner{}
	step := migrateStep{runner: fr, locate: func(string) (string, error) { return composePath, nil }}

	err := step.Run(context.Background(), &Env{}, ReporterFunc(func(Progress) {}))
	if err == nil {
		t.Fatal("muốn lỗi khi Env.Secrets rỗng")
	}
	se, ok := err.(*StepError)
	if !ok {
		t.Fatalf("lỗi phải là *StepError, được %T", err)
	}
	if se.Code != ErrCodeMigrateFailed {
		t.Errorf("Code = %q, muốn %q", se.Code, ErrCodeMigrateFailed)
	}
	if len(fr.Calls) != 0 {
		t.Error("không được gọi docker khi chưa có bí mật")
	}
}

func TestMigrateStep_ComposeNotFound_ReturnsStructuredError(t *testing.T) {
	step := migrateStep{locate: func(string) (string, error) { return "", errors.New("không thấy") }}
	err := step.Run(context.Background(), testEnvWithSecrets(t), ReporterFunc(func(Progress) {}))

	se, ok := err.(*StepError)
	if !ok {
		t.Fatalf("lỗi phải là *StepError, được %T", err)
	}
	if se.Code != ErrCodeComposeNotFound {
		t.Errorf("Code = %q, muốn %q", se.Code, ErrCodeComposeNotFound)
	}
}

func TestEnsureComposeSecretFiles_IdempotentDoesNotOverwrite(t *testing.T) {
	composePath := testComposePath(t)
	secretsDir := filepath.Join(filepath.Dir(composePath), "..", "secrets")

	env := testEnvWithSecrets(t)
	res, err := secretsResult(env)
	if err != nil {
		t.Fatalf("secretsResult: %v", err)
	}

	if err := ensureComposeSecretFiles(composePath, res); err != nil {
		t.Fatalf("ensureComposeSecretFiles (lần 1): %v", err)
	}
	bridgeBefore, err := os.ReadFile(filepath.Join(secretsDir, "gh_bridge_key"))
	if err != nil {
		t.Fatalf("đọc gh_bridge_key: %v", err)
	}

	if err := ensureComposeSecretFiles(composePath, res); err != nil {
		t.Fatalf("ensureComposeSecretFiles (lần 2): %v", err)
	}
	bridgeAfter, err := os.ReadFile(filepath.Join(secretsDir, "gh_bridge_key"))
	if err != nil {
		t.Fatalf("đọc gh_bridge_key lần 2: %v", err)
	}

	if string(bridgeBefore) != string(bridgeAfter) {
		t.Error("gh_bridge_key phải giữ nguyên giữa hai lần gọi (idempotent), không được sinh lại")
	}

	masterKey, err := os.ReadFile(filepath.Join(secretsDir, "gh_master_key"))
	if err != nil {
		t.Fatalf("đọc gh_master_key: %v", err)
	}
	if string(masterKey) != res.Bundle.MasterKey {
		t.Errorf("gh_master_key = %q, muốn đúng res.Bundle.MasterKey", string(masterKey))
	}
}

func TestMigrateProgressPercent_NeverReaches100(t *testing.T) {
	for _, n := range []int{1, 2, 5, 50, 1000} {
		if pct := migrateProgressPercent(n); pct >= 100 || pct <= 0 {
			t.Errorf("migrateProgressPercent(%d) = %v, muốn trong khoảng (0,100)", n, pct)
		}
	}
}
