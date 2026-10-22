package ops

import (
	"context"
	"errors"
	"os"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
)

func TestRunStop_HappyPath(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	fr := &fake.Runner{Responses: []fake.Response{{Match: fake.MatchArgsContain("stop"), Output: []byte("")}}}

	var out strings.Builder
	if err := RunStop(context.Background(), env, fr, &out); err != nil {
		t.Fatalf("RunStop: %v", err)
	}
	if len(fr.Calls) != 1 {
		t.Fatalf("Calls = %d, muốn 1", len(fr.Calls))
	}
}

func TestRunStop_Fails_ReturnsOpError(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	fr := &fake.Runner{Responses: []fake.Response{{Match: fake.MatchArgsContain("stop"), Err: errors.New("boom")}}}

	err := RunStop(context.Background(), env, fr, &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T", err)
	}
	if opErr.Code != ErrCodeStopFailed {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeStopFailed)
	}
}

func TestRunStart_HappyPath(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	fr := &fake.Runner{Responses: []fake.Response{{Match: fake.MatchArgsContain("up", "-d"), Output: []byte("")}}}

	var out strings.Builder
	if err := RunStart(context.Background(), env, fr, &out); err != nil {
		t.Fatalf("RunStart: %v", err)
	}
}

func TestRunStart_Fails_ReturnsOpError(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	fr := &fake.Runner{Responses: []fake.Response{{Match: fake.MatchArgsContain("up", "-d"), Err: errors.New("boom")}}}

	err := RunStart(context.Background(), env, fr, &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T", err)
	}
	if opErr.Code != ErrCodeStartFailed {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeStartFailed)
	}
}

// v0.1.44 (F-6b): genh stop ghi đánh dấu "Owner chủ động dừng" (trực canh không
// tự khởi động lại), genh start xoá; stop lỗi thì không ghi.
func TestStopStart_DanhDauTamDung(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	bad := &fake.Runner{Responses: []fake.Response{{Match: fake.MatchArgsContain("stop"), Err: errors.New("boom")}}}
	_ = RunStop(context.Background(), env, bad, &strings.Builder{})
	if OwnerPaused(env.InstallDir) {
		t.Fatal("stop lỗi không được ghi đánh dấu tạm dừng")
	}
	fr := &fake.Runner{Responses: []fake.Response{{Output: []byte("")}}}
	if err := RunStop(context.Background(), env, fr, &strings.Builder{}); err != nil {
		t.Fatal(err)
	}
	if !OwnerPaused(env.InstallDir) {
		t.Fatal("genh stop phải ghi config/paused-by-owner.json")
	}
	b, err := os.ReadFile(OwnerPausePath(env.InstallDir))
	if err != nil || !strings.Contains(string(b), `"at"`) {
		t.Fatalf("nội dung %s, %v", b, err)
	}
	// Windows không có bit quyền POSIX — chỉ kiểm 0600 trên Unix.
	if fi, _ := os.Stat(OwnerPausePath(env.InstallDir)); runtime.GOOS != "windows" && fi.Mode().Perm() != 0o600 {
		t.Fatalf("quyền %v, muốn 0600", fi.Mode().Perm())
	}
	if err := RunStart(context.Background(), env, fr, &strings.Builder{}); err != nil {
		t.Fatal(err)
	}
	if OwnerPaused(env.InstallDir) {
		t.Fatal("genh start phải xoá đánh dấu tạm dừng")
	}
	if err := ClearOwnerPause(env.InstallDir); err != nil {
		t.Fatalf("xoá lần hai không lỗi: %v", err)
	}
}

// Đánh dấu tạm dừng phải có TRƯỚC `docker compose stop` (stop có thể mất hàng
// chục giây — lượt trực canh trong khe đó không được `up -d` lại dịch vụ), và
// stop phải chờ lượt trực canh đang chạy xong.
func TestRunStop_TamDungTruocKhiStop_VaChoLuotTrucCanh(t *testing.T) {
	env := testEnv(t, testComposePath(t, ""))
	pausedAtStop, lockFreeAtStop := false, false
	fr := &fake.Runner{Responses: []fake.Response{{Match: func(cmd dockercli.Cmd) bool {
		if strings.Contains(strings.Join(cmd.Args, " "), "stop") {
			pausedAtStop = OwnerPaused(env.InstallDir)
			if l, err := hostlink.AcquireWatchdogLock(env.InstallDir); err == nil {
				lockFreeAtStop = true
				l.Release()
			}
			return true
		}
		return false
	}}}}
	held, err := hostlink.AcquireWatchdogLock(env.InstallDir)
	if err != nil {
		t.Fatal(err)
	}
	go func() {
		time.Sleep(50 * time.Millisecond)
		held.Release()
	}()
	if err := RunStop(context.Background(), env, fr, &strings.Builder{}); err != nil {
		t.Fatal(err)
	}
	if !pausedAtStop || !lockFreeAtStop {
		t.Fatalf("lúc stop: paused=%v, lượt trực canh đã xong=%v", pausedAtStop, lockFreeAtStop)
	}

	// Đã tạm dừng từ trước + stop lỗi ⇒ giữ nguyên đánh dấu cũ.
	bad := &fake.Runner{Responses: []fake.Response{{Match: fake.MatchArgsContain("stop"), Err: errors.New("boom")}}}
	if err := RunStop(context.Background(), env, bad, &strings.Builder{}); err == nil {
		t.Fatal("stop lỗi phải trả lỗi")
	}
	if !OwnerPaused(env.InstallDir) {
		t.Fatal("đã tạm dừng từ trước thì stop lỗi không được xoá đánh dấu")
	}
}
