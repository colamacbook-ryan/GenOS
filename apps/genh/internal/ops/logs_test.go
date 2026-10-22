package ops

import (
	"context"
	"errors"
	"io"
	"strings"
	"testing"
)

// fakeInheritRunner ghi lại lời gọi Run cuối cùng, không thực thi gì thật.
type fakeInheritRunner struct {
	gotName string
	gotArgs []string
	err     error
}

func (f *fakeInheritRunner) Run(ctx context.Context, name string, args []string, dir string, env []string, stdout, stderr io.Writer, stdin io.Reader) error {
	f.gotName = name
	f.gotArgs = args
	return f.err
}

func TestRunLogs_HappyPath_BuildsExpectedArgs(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)

	fr := &fakeInheritRunner{}
	opts := LogsOptions{Services: []string{"api", "worker"}, Follow: true}

	if err := RunLogs(context.Background(), env, opts, fr, &strings.Builder{}, &strings.Builder{}, strings.NewReader("")); err != nil {
		t.Fatalf("RunLogs: %v", err)
	}
	if fr.gotName != "docker" {
		t.Errorf("gotName = %q, muốn docker", fr.gotName)
	}
	joined := strings.Join(fr.gotArgs, " ")
	for _, want := range []string{"compose", "logs", "--tail=200", "--follow", "api", "worker"} {
		if !strings.Contains(joined, want) {
			t.Errorf("args %v thiếu %q", fr.gotArgs, want)
		}
	}
}

func TestRunLogs_NoFollow_DoesNotPassFollowFlag(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)

	fr := &fakeInheritRunner{}
	opts := LogsOptions{}

	if err := RunLogs(context.Background(), env, opts, fr, &strings.Builder{}, &strings.Builder{}, strings.NewReader("")); err != nil {
		t.Fatalf("RunLogs: %v", err)
	}
	for _, a := range fr.gotArgs {
		if a == "--follow" {
			t.Errorf("không được truyền --follow khi Follow=false, args=%v", fr.gotArgs)
		}
	}
}

func TestRunLogs_CommandFails_ReturnsOpError(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)

	fr := &fakeInheritRunner{err: errors.New("exit status 1")}

	err := RunLogs(context.Background(), env, LogsOptions{}, fr, &strings.Builder{}, &strings.Builder{}, strings.NewReader(""))
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T", err)
	}
	if opErr.Code != ErrCodeLogsFailed {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeLogsFailed)
	}
}
