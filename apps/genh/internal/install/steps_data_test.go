package install

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/secretgen"
)

// speedUpComposePolling giảm compose.PollInterval trong lúc test chạy để
// WaitHealthy không tốn giây thật giữa mỗi lần thăm dò — trả về hàm khôi
// phục giá trị gốc, gọi qua defer.
func speedUpComposePolling(d time.Duration) func() {
	orig := compose.PollInterval
	compose.PollInterval = d
	return func() { compose.PollInterval = orig }
}

func testEnvWithSecrets(t *testing.T) *Env {
	t.Helper()
	res, err := secretgen.Ensure(t.TempDir())
	if err != nil {
		t.Fatalf("secretgen.Ensure: %v", err)
	}
	return &Env{Secrets: res}
}

// tempComposeLocator trả hàm locate trỏ tới compose.yaml trong t.TempDir():
// từ v0.1.46 dataStep ghi .env cạnh compose.yaml (access.Ensure), nên không
// được dùng đường dẫn cứng "/tmp/compose.yaml" — trên Windows "\tmp" không
// tồn tại (GH-E010), trên Linux test sẽ ghi bậy vào /tmp/.env thật của máy.
func tempComposeLocator(t *testing.T) func(string) (string, error) {
	t.Helper()
	p := filepath.Join(t.TempDir(), "compose.yaml")
	if err := os.WriteFile(p, []byte("services: {}\n"), 0o600); err != nil {
		t.Fatalf("ghi compose.yaml tạm: %v", err)
	}
	return func(string) (string, error) { return p, nil }
}

func TestDataStep_HappyPath_UpThenHealthy(t *testing.T) {
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("up", "-d"), Output: []byte("")},
		{
			Match: fake.MatchArgsContain("ps", "--format", "json"),
			OutputSeq: [][]byte{
				[]byte(`[{"Service":"db","State":"running","Health":"starting"},{"Service":"redis","State":"running","Health":"healthy"}]`),
				[]byte(`[{"Service":"db","State":"running","Health":"healthy"},{"Service":"redis","State":"running","Health":"healthy"}]`),
			},
		},
	}}

	origInterval := speedUpComposePolling(time.Millisecond)
	defer origInterval()

	locate := tempComposeLocator(t)
	composePath, _ := locate("")
	step := dataStep{runner: fr, locate: locate, timeout: time.Second}

	var progresses []Progress
	rep := ReporterFunc(func(p Progress) { progresses = append(progresses, p) })

	if err := step.Run(context.Background(), testEnvWithSecrets(t), rep); err != nil {
		t.Fatalf("Run: %v", err)
	}

	// .env phải được ghi cạnh compose.yaml tạm (không phải /tmp/.env thật).
	if _, err := os.Stat(filepath.Join(filepath.Dir(composePath), ".env")); err != nil {
		t.Errorf("muốn .env cạnh compose.yaml tạm: %v", err)
	}

	last := progresses[len(progresses)-1]
	if last.Status != StatusOK || last.Percent != 100 {
		t.Errorf("progress cuối = %+v, muốn StatusOK/100", last)
	}

	// Kiểm lệnh `up -d` mang đúng env POSTGRES_PASSWORD/GH_APP_DB_PASSWORD.
	var upCall *fake.Call
	for i := range fr.Calls {
		if strings.Contains(strings.Join(fr.Calls[i].Cmd.Args, " "), "up") {
			upCall = &fr.Calls[i]
			break
		}
	}
	if upCall == nil {
		t.Fatal("không thấy lệnh `docker compose up`")
	}
	foundPG := false
	for _, e := range upCall.Cmd.Env {
		if strings.HasPrefix(e, "POSTGRES_PASSWORD=") && e != "POSTGRES_PASSWORD=" {
			foundPG = true
		}
	}
	if !foundPG {
		t.Errorf("lệnh up phải mang POSTGRES_PASSWORD khác rỗng, Env=%v", upCall.Cmd.Env)
	}
}

func TestDataStep_ComposeUpFails_ReturnsStructuredError(t *testing.T) {
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("up", "-d"), Err: errors.New("cổng đã dùng")},
	}}

	step := dataStep{runner: fr, locate: tempComposeLocator(t)}
	err := step.Run(context.Background(), testEnvWithSecrets(t), ReporterFunc(func(Progress) {}))

	se, ok := err.(*StepError)
	if !ok {
		t.Fatalf("lỗi phải là *StepError, được %T", err)
	}
	if se.Code != ErrCodeComposeUpFailed {
		t.Errorf("Code = %q, muốn %q", se.Code, ErrCodeComposeUpFailed)
	}
}

func TestDataStep_MissingSecrets_ReturnsStructuredError(t *testing.T) {
	fr := &fake.Runner{}
	step := dataStep{runner: fr, locate: tempComposeLocator(t)}

	err := step.Run(context.Background(), &Env{}, ReporterFunc(func(Progress) {}))
	if err == nil {
		t.Fatal("muốn lỗi khi Env.Secrets rỗng")
	}
	if len(fr.Calls) != 0 {
		t.Error("không được gọi docker khi chưa có bí mật")
	}
}

func TestDataStep_HealthTimeout_ReturnsStructuredError(t *testing.T) {
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("up", "-d"), Output: []byte("")},
		{Match: fake.MatchArgsContain("ps", "--format", "json"), Output: []byte(`[{"Service":"db","State":"running","Health":"starting"}]`)},
	}}

	origInterval := speedUpComposePolling(time.Millisecond)
	defer origInterval()

	step := dataStep{runner: fr, locate: tempComposeLocator(t), timeout: 20 * time.Millisecond}
	err := step.Run(context.Background(), testEnvWithSecrets(t), ReporterFunc(func(Progress) {}))

	se, ok := err.(*StepError)
	if !ok {
		t.Fatalf("lỗi phải là *StepError, được %T", err)
	}
	if se.Code != ErrCodeDataNotHealthy {
		t.Errorf("Code = %q, muốn %q", se.Code, ErrCodeDataNotHealthy)
	}
}

func TestDataHealthPercent(t *testing.T) {
	if got := dataHealthPercent(nil); got != 20 {
		t.Errorf("dataHealthPercent(nil) = %v, muốn 20", got)
	}
	all2 := map[string]string{"db": "healthy", "redis": "healthy"}
	if got := dataHealthPercent(all2); got != 95 {
		t.Errorf("dataHealthPercent(2/2 healthy) = %v, muốn 95 (chưa phải 100 — Run tự đặt 100 sau)", got)
	}
}
