package install

import (
	"context"
	"errors"
	"net/http"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
)

// failingHTTP là genhruntime.HTTPDoer giả trả lỗi ngay lập tức — dùng để
// test đường "không có Docker, tự tải thất bại" mà không đụng mạng thật.
type failingHTTP struct{}

func (failingHTTP) Do(req *http.Request) (*http.Response, error) {
	return nil, errors.New("mạng giả lập: không kết nối được (test)")
}

func TestRuntimeStep_SkipsWhenDockerAlreadyValid(t *testing.T) {
	r := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("compose", "version"), Output: []byte(`{"version":"v2.29.1"}`)},
		{Match: fake.MatchArgsContain("version"), Output: []byte(`{"Server":{"Version":"27.1.1"}}`)},
	}}

	step := runtimeStep{runner: r}
	env := &Env{InstallDir: t.TempDir()}

	var last Progress
	rep := ReporterFunc(func(p Progress) { last = p })

	if err := step.Run(context.Background(), env, rep); err != nil {
		t.Fatalf("Run: %v", err)
	}
	if last.Status != StatusSkipped {
		t.Errorf("Status = %v, muốn StatusSkipped (Docker đã có sẵn)", last.Status)
	}
	// Không được có lệnh nào khác ngoài 2 lệnh phát hiện — đặc biệt không
	// được cố tải/cài gì khi runtime đã hợp lệ.
	if len(r.Calls) != 2 {
		t.Errorf("số lệnh gọi Runner = %d, muốn đúng 2 (docker version + compose version)", len(r.Calls))
	}
}

func TestRuntimeStep_NoDocker_LinuxDownloadFails_ReturnsStructuredError(t *testing.T) {
	r := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("version"), Err: exec.ErrNotFound},
	}}

	step := runtimeStep{runner: r, http: failingHTTP{}}
	env := &Env{InstallDir: t.TempDir()}

	var progresses []Progress
	rep := ReporterFunc(func(p Progress) { progresses = append(progresses, p) })

	err := step.Run(context.Background(), env, rep)
	if err == nil {
		t.Fatal("muốn lỗi: không có Docker và không có mạng thật để tự tải trong test")
	}
	se, ok := err.(*StepError)
	if !ok {
		t.Fatalf("lỗi trả về phải là *StepError, được %T", err)
	}
	if se.Code == "" {
		t.Error("StepError.Code không được rỗng")
	}

	sawRunning := false
	for _, p := range progresses {
		if p.Status == StatusRunning {
			sawRunning = true
		}
	}
	if !sawRunning {
		t.Error("phải báo StatusRunning trước khi thử tự cài runtime")
	}
}

func TestRuntimeDirFor_DefaultsUnderInstallDir(t *testing.T) {
	dir := t.TempDir()
	got, err := runtimeDirFor(&Env{InstallDir: dir})
	if err != nil {
		t.Fatalf("runtimeDirFor: %v", err)
	}
	want := filepath.Join(dir, "runtime")
	if got != want {
		t.Errorf("runtimeDirFor = %q, muốn %q", got, want)
	}
}
