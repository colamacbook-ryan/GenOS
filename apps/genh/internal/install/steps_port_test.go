package install

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/machine"
)

func TestInstall_CustomPortWrittenToEnvBeforeFirstUp(t *testing.T) {
	dir := t.TempDir()
	composePath := filepath.Join(dir, "deploy", "compose.yaml")
	_ = os.MkdirAll(filepath.Dir(composePath), 0o755)
	_ = os.WriteFile(composePath, []byte("name: gen-harness\n"), 0o644)
	env := &Env{InstallDir: dir, Port: 18478, FreshInstall: true}
	// chỉ cần tới lệnh up đầu tiên: db/redis không healthy cũng được
	fr := &fake.Runner{Responses: []fake.Response{{Match: fake.MatchArgsContain("up", "-d"), Err: errors.New("dừng ở đây")}}}
	_ = secretsEnvOverlayPrep(t, env)
	step := dataStep{runner: fr, locate: func(string) (string, error) { return composePath, nil }}
	_ = step.Run(context.Background(), env, ReporterFunc(func(Progress) {}))
	b, _ := os.ReadFile(filepath.Join(dir, "deploy", ".env"))
	if !strings.Contains(string(b), "GH_PORT=18478\n") || !strings.Contains(string(b), "https://localhost:18478") {
		t.Errorf(".env = %q", b)
	}
}

func secretsEnvOverlayPrep(t *testing.T, env *Env) error {
	t.Helper()
	if err := (secretsStep{}).Run(context.Background(), env, ReporterFunc(func(Progress) {})); err != nil {
		t.Fatalf("secretsStep: %v", err)
	}
	return nil
}

func portFail(port int) []machine.CheckResult {
	return []machine.CheckResult{{Name: "Cổng " + strconv.Itoa(port), Status: machine.StatusFail, Detail: "đang bị tiến trình khác dùng"}}
}

func TestMachine_OwnStackPortIsAccepted(t *testing.T) {
	dir := t.TempDir()
	cp := filepath.Join(dir, "deploy", "compose.yaml")
	fr := &fake.Runner{Responses: []fake.Response{{Match: fake.MatchArgsContain("ps", "publish=8443"), Output: []byte(cp + "\n")}}}
	step := machineCheckStep{runner: fr, locate: func(string) (string, error) { return cp, nil }}
	got := step.acceptOwnPort(context.Background(), &Env{InstallDir: dir}, 8443, portFail(8443))
	if got[0].Status != machine.StatusOK {
		t.Fatalf("cổng do stack của chính mình giữ phải OK, nhận %+v", got[0])
	}
	if machine.Overall(got) == machine.StatusFail || firstFailureCode(got) != "" {
		t.Error("không được còn GH-E004")
	}
}

func TestMachine_ForeignProcessStillE004(t *testing.T) {
	dir := t.TempDir()
	cp := filepath.Join(dir, "deploy", "compose.yaml")
	cases := map[string]fake.Response{
		"container của install-dir khác": {Match: fake.MatchArgsContain("ps"), Output: []byte("/other/deploy/compose.yaml\n")},
		"không phải container docker":    {Match: fake.MatchArgsContain("ps"), Output: []byte("")},
		"docker lỗi":                     {Match: fake.MatchArgsContain("ps"), Err: errors.New("daemon down")},
	}
	for name, resp := range cases {
		t.Run(name, func(t *testing.T) {
			step := machineCheckStep{runner: &fake.Runner{Responses: []fake.Response{resp}}, locate: func(string) (string, error) { return cp, nil }}
			got := step.acceptOwnPort(context.Background(), &Env{InstallDir: dir}, 8443, portFail(8443))
			if got[0].Status != machine.StatusFail || firstFailureCode(got) != ErrCodePortInUse {
				t.Fatalf("phải giữ GH-E004, nhận %+v", got[0])
			}
		})
	}
}

var _ dockercli.Runner = (*fake.Runner)(nil)
