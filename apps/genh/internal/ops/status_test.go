package ops

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
)

func TestRunStatus_HappyPath_PrintsServicesReadyAndVersion(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)

	fr := &fake.Runner{Responses: []fake.Response{
		{
			Match:  fake.MatchArgsContain("ps", "--format", "json"),
			Output: []byte(`[{"Service":"api","State":"running","Health":"healthy"},{"Service":"db","State":"running","Health":"healthy"}]`),
		},
		{
			Match:  fake.MatchArgsContain("system", "df", "-v"),
			Output: []byte("VOLUME NAME    LINKS   SIZE\ngen-harness_pg_data   1  10MB\n"),
		},
	}}

	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]string{"db": "ok", "redis": "ok", "objects": "skip", "bridge": "ok"})
	}))
	defer srv.Close()

	var out strings.Builder
	err := RunStatus(context.Background(), env, "v2.2.0-test", StatusDeps{Runner: fr, Client: srv.Client()}, &out)
	// Gọi tới httptest server thật (URL khác 127.0.0.1:port), nên RunStatus
	// tự dựng URL riêng qua localURL — probe /ready sẽ lỗi kết nối (đúng dự
	// kiến, không phải lỗi test): kiểm RunStatus vẫn KHÔNG trả lỗi (readiness
	// là best-effort trong status), và bảng service/dung lượng vẫn in đúng.
	if err != nil {
		t.Fatalf("RunStatus: %v", err)
	}
	got := out.String()
	if !strings.Contains(got, "v2.2.0-test") {
		t.Errorf("thiếu phiên bản trong output:\n%s", got)
	}
	if !strings.Contains(got, "api") || !strings.Contains(got, "healthy") {
		t.Errorf("thiếu dòng service api/healthy trong output:\n%s", got)
	}
	if !strings.Contains(got, "pg_data") {
		t.Errorf("thiếu dòng dung lượng pg_data trong output:\n%s", got)
	}
}

func TestRunStatus_ComposePsFails_ReturnsOpError(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)

	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("ps", "--format", "json"), Err: errPsFailed},
	}}

	err := RunStatus(context.Background(), env, "dev", StatusDeps{Runner: fr}, &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T (%v)", err, err)
	}
	if opErr.Code != ErrCodeStatusFailed {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeStatusFailed)
	}
}

func TestRunStatus_NotInstalled_ReturnsOpError(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnvNoSecrets(t, composePath)

	err := RunStatus(context.Background(), env, "dev", StatusDeps{Runner: &fake.Runner{}}, &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T (%v)", err, err)
	}
	if opErr.Code != ErrCodeNotInstalled {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeNotInstalled)
	}
}

var errPsFailed = &fakeErr{"docker compose ps: exit status 1"}

type fakeErr struct{ s string }

func (e *fakeErr) Error() string { return e.s }

// v0.1.37 (F-73): genh status in "Tự chạy lại khi bật máy" + ghi run/autostart-status.json.
func TestRunStatus_TuChayLaiKhiBatMay(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	fr := &fake.Runner{Responses: append([]fake.Response{
		{Match: fake.MatchArgsContain("ps", "--format", "json"), Output: []byte(`[{"Service":"api","State":"running","Health":"healthy"}]`)},
		{Match: fake.MatchArgsContain("system", "df", "-v"), Output: []byte("VOLUME NAME\n")},
	}, autostartFakeResponses()...)}
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	defer srv.Close()

	var out strings.Builder
	if err := RunStatus(context.Background(), env, "v0.1.37", StatusDeps{Runner: fr, Client: srv.Client(), GOOS: "linux", UID: "1000"}, &out); err != nil {
		t.Fatalf("RunStatus: %v", err)
	}
	got := out.String()
	for _, want := range []string{"Tự chạy lại khi bật máy:", "sudo systemctl enable docker", "sudo loginctl enable-linger $USER"} {
		if !strings.Contains(got, want) {
			t.Errorf("thiếu %q:\n%s", want, got)
		}
	}
	raw := checkAutostartFile(t, env.InstallDir)
	if raw["os"] != "linux" || raw["docker_enabled"] != "no" || raw["docker_mode"] != "system" || raw["linger"] != "no" || raw["linger_required"] != true {
		t.Errorf("autostart-status.json sai: %v", raw)
	}
}
