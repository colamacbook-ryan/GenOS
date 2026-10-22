package ops

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
)

const fakeBackupLogLine = "INFO:gh.backup:Backup mới: backups/20260925T120000Z-abcd1234.pgcustom.enc (123 byte, CSDL gen_harness)"

func TestRunBackup_HappyPath_ParsesKeyFromLog(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)

	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("exec", "-T", "api", "python", "-m", "gh.backup", "run"), Lines: []string{fakeBackupLogLine}},
	}}

	var out strings.Builder
	if err := RunBackup(context.Background(), env, "", fr, &out); err != nil {
		t.Fatalf("RunBackup: %v", err)
	}
	if !strings.Contains(out.String(), "backups/20260925T120000Z-abcd1234.pgcustom.enc") {
		t.Errorf("output phải chứa khoá backup, được %q", out.String())
	}
}

func TestRunBackup_ContainerCommandFails_ReturnsOpError(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)

	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("exec", "-T", "api", "python", "-m", "gh.backup", "run"), Err: errors.New("db down")},
	}}

	err := RunBackup(context.Background(), env, "", fr, &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T", err)
	}
	if opErr.Code != ErrCodeBackupFailed {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeBackupFailed)
	}
}

func TestRunBackup_WithToPath_CopiesBytesToHost(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	toPath := filepath.Join(t.TempDir(), "out.enc")

	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("exec", "-T", "api", "python", "-m", "gh.backup", "run"), Lines: []string{fakeBackupLogLine}},
		{Match: fake.MatchArgsContain("python", "-c"), Output: []byte("fake-encrypted-bytes")},
	}}

	if err := RunBackup(context.Background(), env, toPath, fr, &strings.Builder{}); err != nil {
		t.Fatalf("RunBackup: %v", err)
	}
	data, err := os.ReadFile(toPath)
	if err != nil {
		t.Fatalf("đọc lại %s: %v", toPath, err)
	}
	if string(data) != "fake-encrypted-bytes" {
		t.Errorf("nội dung ghi ra host = %q, muốn %q", data, "fake-encrypted-bytes")
	}
}

const restoreTestKey = "backups/20260920T020000Z-0badc0de.pgcustom.enc"

func restoreDeps(runner *fake.Runner) RestoreDeps {
	return RestoreDeps{Runner: runner, Timeout: 200 * time.Millisecond, PollEvery: 5 * time.Millisecond}
}

func restoreHappyRunner() *fake.Runner {
	return &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("gh.backup", "list"), Lines: []string{"2026-09-20T02:00:00+00:00  " + restoreTestKey + "  1 byte  CSDL=gh"}},
		{Match: fake.MatchArgsContain("gh.backup", "run", "GH_BACKUP_TRIGGER=pre-restore"), Lines: []string{fakeBackupLogLine}},
		{Match: fake.MatchArgsContain("stop", "api", "worker"), Output: []byte("")},
		{Match: fake.MatchArgsContain("run", "--rm", "--no-deps", "-T", "api", "python", "-m", "gh.backup", "restore", "--key"), Output: []byte("")},
		{Match: fake.MatchArgsContain("run", "--rm", "--no-deps", "-T", "migrate"), Lines: []string{}},
		{Match: fake.MatchArgsContain("up", "-d"), Output: []byte("")},
	}}
}

func callOrder(fr *fake.Runner) []string {
	var out []string
	for _, c := range fr.Calls {
		j := strings.Join(c.Cmd.Args, " ")
		switch {
		case strings.Contains(j, "gh.backup run"):
			out = append(out, "backup")
		case strings.Contains(j, "stop api worker"):
			out = append(out, "stop")
		case strings.Contains(j, "gh.backup restore --key "+restoreTestKey):
			out = append(out, "restore")
		case strings.Contains(j, "gh.backup restore --key backups/20260925T120000Z-abcd1234"):
			out = append(out, "rollback")
		case strings.Contains(j, "migrate"):
			out = append(out, "migrate")
		case strings.Contains(j, " up -d"):
			out = append(out, "up")
		}
	}
	return out
}

// Khôi phục an toàn: sao lưu an toàn → dừng api/worker → restore bằng container
// tạm → migrate → khởi động lại + chờ /ready.
func TestRunRestore_HappyPath_SafeFlow(t *testing.T) {
	env := testEnv(t, testComposePath(t, ""))
	_, port := listenReadyServer(t, true)
	env.Port = port
	fr := restoreHappyRunner()
	var out strings.Builder
	safety, err := RunRestore(context.Background(), env, restoreTestKey, restoreDeps(fr), &out)
	if err != nil {
		t.Fatalf("RunRestore: %v", err)
	}
	if safety != "backups/20260925T120000Z-abcd1234.pgcustom.enc" {
		t.Errorf("safety = %q", safety)
	}
	if got := strings.Join(callOrder(fr), ","); got != "backup,stop,restore,migrate,up" {
		t.Errorf("thứ tự lệnh = %s", got)
	}
	if !strings.Contains(out.String(), restoreTestKey) {
		t.Errorf("output phải xác nhận khoá đã khôi phục, được %q", out.String())
	}
}

func TestRunRestore_ArbitraryHostFile_ReturnsClearLimitationError(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)

	hostFile := filepath.Join(t.TempDir(), "some-backup.enc")
	if err := os.WriteFile(hostFile, []byte("data"), 0o600); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	_, err := RunRestore(context.Background(), env, hostFile, restoreDeps(&fake.Runner{}), &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T", err)
	}
	if opErr.Code != ErrCodeRestoreFailed {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeRestoreFailed)
	}
	if !strings.Contains(opErr.Why, "restore_backup()") {
		t.Errorf("Why phải giải thích giới hạn restore_backup(), được %q", opErr.Why)
	}
}

func TestRunRestore_InvalidKey_NoDockerCalls(t *testing.T) {
	env := testEnv(t, testComposePath(t, ""))
	fr := &fake.Runner{}
	for _, k := range []string{"backups/x.enc", "backups/../../etc/passwd", "--help", ""} {
		if _, err := RunRestore(context.Background(), env, k, restoreDeps(fr), &strings.Builder{}); err == nil {
			t.Errorf("khoá %q phải bị từ chối", k)
		}
	}
	if len(fr.Calls) != 0 {
		t.Errorf("khoá sai không được gọi docker: %+v", fr.Calls)
	}
}

func TestRunRestore_SafetyBackupFails_StopsBeforeTouchingAnything(t *testing.T) {
	env := testEnv(t, testComposePath(t, ""))
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("gh.backup", "list"), Lines: []string{restoreTestKey}},
		{Match: fake.MatchArgsContain("gh.backup", "run", "GH_BACKUP_TRIGGER"), Err: errors.New("db down")},
	}}
	_, err := RunRestore(context.Background(), env, restoreTestKey, restoreDeps(fr), &strings.Builder{})
	if opErr, ok := err.(*OpError); !ok || opErr.Code != ErrCodeRestoreFailed {
		t.Fatalf("err = %v", err)
	}
	if got := strings.Join(callOrder(fr), ","); got != "backup" {
		t.Errorf("chỉ được chạy sao lưu an toàn, được %s", got)
	}
}

// Khoá không có trong danh mục ⇒ lỗi rõ, không sao lưu/dừng gì.
func TestRunRestore_KeyMissing_NothingDestructive(t *testing.T) {
	env := testEnv(t, testComposePath(t, ""))
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("gh.backup", "list"), Lines: []string{"2026-09-20T02:00:00+00:00  backups/20260101T000000Z-aaaaaaaa.pgcustom.enc  1 byte"}},
	}}
	_, err := RunRestore(context.Background(), env, restoreTestKey, restoreDeps(fr), &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok || opErr.Code != ErrCodeRestoreFailed || !strings.Contains(opErr.What, "Không tìm thấy") {
		t.Fatalf("err = %v", err)
	}
	if len(fr.Calls) != 1 {
		t.Errorf("chỉ được chạy list, được %d lệnh", len(fr.Calls))
	}
}

// Bản an toàn phải mang GH_BACKUP_KEEP=<khoá> để prune không xoá bản đang khôi phục.
func TestRunRestore_SafetyBackupPinsRequestedKey(t *testing.T) {
	env := testEnv(t, testComposePath(t, ""))
	_, port := listenReadyServer(t, true)
	env.Port = port
	fr := restoreHappyRunner()
	if _, err := RunRestore(context.Background(), env, restoreTestKey, restoreDeps(fr), &strings.Builder{}); err != nil {
		t.Fatal(err)
	}
	for _, c := range fr.Calls {
		if strings.Contains(strings.Join(c.Cmd.Args, " "), "gh.backup run") {
			if !strings.Contains(strings.Join(c.Cmd.Args, " "), "-e GH_BACKUP_KEEP="+restoreTestKey) {
				t.Errorf("thiếu -e GH_BACKUP_KEEP: %v", c.Cmd.Args)
			}
			return
		}
	}
	t.Error("không thấy lệnh sao lưu an toàn")
}

// Restore lỗi giữa chừng → quay về bản an toàn rồi khởi động lại.
func TestRunRestore_RestoreFails_RollsBackToSafetyAndRestarts(t *testing.T) {
	env := testEnv(t, testComposePath(t, ""))
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("gh.backup", "list"), Lines: []string{restoreTestKey}},
		{Match: fake.MatchArgsContain("gh.backup", "run", "GH_BACKUP_TRIGGER"), Lines: []string{fakeBackupLogLine}},
		{Match: fake.MatchArgsContain("stop", "api", "worker"), Output: []byte("")},
		{Match: fake.MatchArgsContain("restore", "--key", restoreTestKey), Err: errors.New("khoá không tồn tại")},
		{Match: fake.MatchArgsContain("restore", "--key", "backups/20260925T120000Z-abcd1234"), Output: []byte("")},
		{Match: fake.MatchArgsContain("up", "-d"), Output: []byte("")},
	}}
	safety, err := RunRestore(context.Background(), env, restoreTestKey, restoreDeps(fr), &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok || opErr.Code != ErrCodeRestoreFailed || safety == "" {
		t.Fatalf("err = %v, safety = %q", err, safety)
	}
	if !strings.Contains(opErr.Next, "quay về như trước") {
		t.Errorf("Next phải báo đã quay về bản an toàn: %q", opErr.Next)
	}
	if got := strings.Join(callOrder(fr), ","); got != "backup,stop,restore,rollback,up" {
		t.Errorf("thứ tự lệnh = %s", got)
	}
}

// Yêu cầu từ Console: đọc + xoá request/restore.json, ghi restore-status.json.
func TestRunRestoreRequest_ConsumesRequestAndWritesStatus(t *testing.T) {
	env := testEnv(t, testComposePath(t, ""))
	_, port := listenReadyServer(t, true)
	env.Port = port

	handled, err := RunRestoreRequest(context.Background(), env, restoreDeps(&fake.Runner{}), &strings.Builder{})
	if handled || err != nil {
		t.Fatalf("hộp thư trống: handled=%v err=%v", handled, err)
	}

	if err := hostlink.EnsureDir(env.InstallDir); err != nil {
		t.Fatal(err)
	}
	_ = os.WriteFile(hostlink.RestoreRequestPath(env.InstallDir), []byte(`{"id":"r1","key":"`+restoreTestKey+`"}`), 0o666)
	fr := restoreHappyRunner()
	handled, err = RunRestoreRequest(context.Background(), env, restoreDeps(fr), &strings.Builder{})
	if !handled || err != nil {
		t.Fatalf("handled=%v err=%v", handled, err)
	}
	if hostlink.HasRestoreRequest(env.InstallDir) {
		t.Fatal("yêu cầu phải bị xoá để watcher không kích lặp")
	}
	st, _ := hostlink.ReadRestoreStatus(env.InstallDir)
	if st.State != "done" || st.Key != restoreTestKey || st.SafetyKey == "" || st.FinishedAt == "" {
		t.Fatalf("status = %+v", st)
	}

	// Yêu cầu chứa khoá lạ → failed, không gọi docker.
	_ = os.WriteFile(hostlink.RestoreRequestPath(env.InstallDir), []byte(`{"key":"backups/../x"}`), 0o666)
	fr2 := &fake.Runner{}
	handled, err = RunRestoreRequest(context.Background(), env, restoreDeps(fr2), &strings.Builder{})
	if !handled || err == nil || len(fr2.Calls) != 0 {
		t.Fatalf("khoá lạ: handled=%v err=%v calls=%d", handled, err, len(fr2.Calls))
	}
	if st, _ := hostlink.ReadRestoreStatus(env.InstallDir); st.State != "failed" || st.Message == "" {
		t.Fatalf("status = %+v", st)
	}
}

// Máy cài dở (api chưa từng chạy): `exec` báo "is not running" → backup phải
// chạy lại bằng container tạm `run --rm --no-deps` thay vì chặn `genh update`.
func TestRunBackup_APINotRunning_FallsBackToOneOffContainer(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)

	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("exec", "-T", "api", "python", "-m", "gh.backup", "run"), Err: errors.New(`exit status 1 — service "api" is not running`)},
		{Match: fake.MatchArgsContain("run", "--rm", "--no-deps", "-T", "api", "python", "-m", "gh.backup", "run"), Lines: []string{fakeBackupLogLine}},
	}}

	var out strings.Builder
	if err := RunBackup(context.Background(), env, "", fr, &out); err != nil {
		t.Fatalf("RunBackup: %v", err)
	}
	if !strings.Contains(out.String(), "backups/20260925T120000Z-abcd1234.pgcustom.enc") {
		t.Errorf("output phải chứa khoá backup, được %q", out.String())
	}
	if len(fr.Calls) != 2 {
		t.Errorf("muốn 2 lệnh (exec rồi run), được %d", len(fr.Calls))
	}
}

// Lỗi khác "not running" (vd db hỏng) không được chạy lại bằng container tạm.
func TestRunBackup_OtherError_NoOneOffRetry(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)

	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("exec", "-T", "api"), Err: errors.New("db down")},
	}}
	if err := RunBackup(context.Background(), env, "", fr, &strings.Builder{}); err == nil {
		t.Fatal("muốn lỗi")
	}
	if len(fr.Calls) != 1 {
		t.Errorf("muốn đúng 1 lệnh, được %d", len(fr.Calls))
	}
}
