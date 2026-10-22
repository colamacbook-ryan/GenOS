package ops

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
)

func TestRunResetPassword_HappyPath_PrintsEmailAndTempPassword(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("exec", "-T", "api", "python", "-m", "gh.auth.reset_owner"),
			Output: []byte(`{"email": "owner@example.vn", "temp_password": "Tm9p-Ab12Cd34Ef"}` + "\n")},
	}}
	var out strings.Builder
	if err := RunResetPassword(context.Background(), env, fr, &out); err != nil {
		t.Fatalf("RunResetPassword: %v", err)
	}
	for _, want := range []string{"Email đăng nhập: owner@example.vn", "Mật khẩu tạm: Tm9p-Ab12Cd34Ef", "/login"} {
		if !strings.Contains(out.String(), want) {
			t.Errorf("output thiếu %q: %q", want, out.String())
		}
	}
}

func TestRunResetPassword_APINotRunning_FallsBackToOneOff(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("exec", "-T", "api"), Err: errors.New(`service "api" is not running`)},
		{Match: fake.MatchArgsContain("run", "--rm", "--no-deps", "-T", "api", "python", "-m", "gh.auth.reset_owner"),
			Output: []byte(`{"email": "a@b.vn", "temp_password": "x"}`)},
	}}
	var out strings.Builder
	if err := RunResetPassword(context.Background(), env, fr, &out); err != nil {
		t.Fatalf("RunResetPassword: %v", err)
	}
	if len(fr.Calls) != 2 || !strings.Contains(out.String(), "a@b.vn") {
		t.Errorf("calls=%d out=%q", len(fr.Calls), out.String())
	}
}

func TestRunResetPassword_Failures_ReturnOpError(t *testing.T) {
	cases := map[string]fake.Response{
		"no-owner": {Err: errors.New("thoát mã 2 — Chưa có tài khoản Owner — hãy hoàn tất bước 2")},
		"db-down":  {Err: errors.New("connection refused")},
		"garbage":  {Output: []byte("Traceback…\n")},
	}
	for name, resp := range cases {
		composePath := testComposePath(t, "")
		env := testEnv(t, composePath)
		resp.Match = fake.MatchArgsContain("gh.auth.reset_owner")
		fr := &fake.Runner{Responses: []fake.Response{resp}}
		err := RunResetPassword(context.Background(), env, fr, &strings.Builder{})
		var opErr *OpError
		if !errors.As(err, &opErr) || opErr.Code != ErrCodeResetPasswordFailed {
			t.Errorf("%s: muốn OpError %s, được %v", name, ErrCodeResetPasswordFailed, err)
			continue
		}
		if name == "no-owner" && !strings.Contains(opErr.What, "Chưa có tài khoản Owner") {
			t.Errorf("no-owner: What = %q", opErr.What)
		}
	}
}
