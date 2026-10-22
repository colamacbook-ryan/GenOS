package install

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/access"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/secretgen"
)

// runSecretsThenData chạy Bước 4 rồi Bước 5 trên installDir; trả nội dung .env
// ĐÚNG LÚC lệnh `docker compose up` đầu tiên được nhận.
func runSecretsThenData(t *testing.T, installDir string) (envAtFirstUp string, env *Env) {
	t.Helper()
	defer speedUpComposePolling(time.Millisecond)()
	composePath := filepath.Join(installDir, "deploy", "compose.yaml")
	if err := os.MkdirAll(filepath.Dir(composePath), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(composePath, []byte("name: gen-harness\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	env = &Env{InstallDir: installDir}
	rep := ReporterFunc(func(Progress) {})
	if err := (secretsStep{}).Run(context.Background(), env, rep); err != nil {
		t.Fatalf("secretsStep: %v", err)
	}

	captured := ""
	seen := false
	isUp := fake.MatchArgsContain("up", "-d")
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: func(c dockercli.Cmd) bool {
			if isUp(c) {
				if !seen {
					seen = true
					b, _ := os.ReadFile(filepath.Join(installDir, "deploy", ".env"))
					captured = string(b)
				}
				return true
			}
			return false
		}, Output: []byte("")},
		{Match: fake.MatchArgsContain("ps", "--format", "json"),
			Output: []byte(`[{"Service":"db","State":"running","Health":"healthy"},{"Service":"redis","State":"running","Health":"healthy"}]`)},
	}}
	step := dataStep{runner: fr, locate: func(string) (string, error) { return composePath, nil }, timeout: time.Second}
	if err := step.Run(context.Background(), env, rep); err != nil {
		t.Fatalf("dataStep: %v", err)
	}
	if !seen {
		t.Fatal("không thấy lệnh up")
	}
	return captured, env
}

func TestInstall_FreshInstallBindsLocalBeforeFirstUp(t *testing.T) {
	dir := t.TempDir()
	got, env := runSecretsThenData(t, dir)
	if !env.FreshInstall {
		t.Error("không có secrets.json trước đó thì FreshInstall phải true")
	}
	if !strings.Contains(got, "GH_BIND_ADDR=127.0.0.1\n") || !strings.Contains(got, "GH_ACCESS_MODE=local\n") {
		t.Errorf(".env lúc up đầu tiên = %q", got)
	}
}

func TestInstall_FreshInstallFixesLegacyWrittenByInterleavedOp(t *testing.T) {
	dir := t.TempDir()
	composePath := filepath.Join(dir, "deploy", "compose.yaml")
	_ = os.MkdirAll(filepath.Dir(composePath), 0o755)
	// Lệnh vận hành chạy chen giữa lúc cài mới đã ghi lan_legacy.
	if _, _, err := access.Ensure(composePath, false); err != nil {
		t.Fatal(err)
	}
	got, _ := runSecretsThenData(t, dir)
	if !strings.Contains(got, "GH_BIND_ADDR=127.0.0.1\n") || !strings.Contains(got, "GH_ACCESS_MODE=local\n") {
		t.Errorf(".env lúc up đầu tiên = %q", got)
	}
}

func TestInstall_ResumeKeepsLegacyNotForcedLocal(t *testing.T) {
	dir := t.TempDir()
	if _, err := secretgen.Ensure(filepath.Join(dir, "config")); err != nil { // đã có secrets.json
		t.Fatal(err)
	}
	got, env := runSecretsThenData(t, dir)
	if env.FreshInstall {
		t.Error("đã có secrets.json thì FreshInstall phải false")
	}
	if !strings.Contains(got, "GH_BIND_ADDR=0.0.0.0\n") || !strings.Contains(got, "GH_ACCESS_MODE=lan_legacy\n") {
		t.Errorf("tiếp tục cài phải giữ lan_legacy, .env = %q", got)
	}
}

func TestInstall_ResumeKeepsOwnerChoice(t *testing.T) {
	dir := t.TempDir()
	_, _ = secretgen.Ensure(filepath.Join(dir, "config"))
	composePath := filepath.Join(dir, "deploy", "compose.yaml")
	_ = os.MkdirAll(filepath.Dir(composePath), 0o755)
	if err := access.Write(composePath, access.State{Mode: access.ModeLocal, BindAddr: access.BindLocal, PublicURL: "https://localhost:8443"}); err != nil {
		t.Fatal(err)
	}
	got, _ := runSecretsThenData(t, dir)
	if !strings.Contains(got, "GH_BIND_ADDR=127.0.0.1\n") {
		t.Errorf("lựa chọn cũ bị đổi: %q", got)
	}
}
