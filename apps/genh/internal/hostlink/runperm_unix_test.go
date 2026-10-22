//go:build !windows

package hostlink

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
)

// v0.1.45 (F-5x "run-pg-secrets"): hộp thư run/ không còn 0777; genh ghi trạng
// thái không theo symlink; tệp yêu cầu phải là tệp thường do api/genh sở hữu.

// secretTarget tạo một tệp "bí mật" NGOÀI run/ (giả secrets.json) để bẫy symlink trỏ tới.
func secretTarget(t *testing.T) (string, string) {
	t.Helper()
	p := filepath.Join(t.TempDir(), "secrets.json")
	const body = `{"POSTGRES_PASSWORD":"khong-duoc-doi"}`
	if err := os.WriteFile(p, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return p, body
}

func assertUnchanged(t *testing.T, path, want string) {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil || string(b) != want {
		t.Fatalf("tệp mục tiêu %s bị đổi: %q, %v", path, b, err)
	}
}

func assertRegular(t *testing.T, path string) {
	t.Helper()
	fi, err := os.Lstat(path)
	if err != nil || !fi.Mode().IsRegular() {
		t.Fatalf("%s phải là tệp thường: %v, %v", path, fi, err)
	}
}

// (1) run/update-status.json là symlink tới tệp ngoài run/ → Start/Finish thay
// đường dẫn (rename), không ghi vào tệp mục tiêu.
func TestStatusWriteDoesNotFollowSymlink(t *testing.T) {
	root := t.TempDir()
	if err := EnsureDir(root); err != nil {
		t.Fatal(err)
	}
	target, body := secretTarget(t)
	status := filepath.Join(Dir(root), StatusFile)
	if err := os.Symlink(target, status); err != nil {
		t.Fatal(err)
	}
	if err := Start(root, "v0.1.44"); err != nil {
		t.Fatal(err)
	}
	assertUnchanged(t, target, body)
	assertRegular(t, status)

	// Cài lại symlink giữa Start và Finish.
	_ = os.Remove(status)
	if err := os.Symlink(target, status); err != nil {
		t.Fatal(err)
	}
	if err := Finish(root, "done", "v0.1.45", ""); err != nil {
		t.Fatal(err)
	}
	assertUnchanged(t, target, body)
	assertRegular(t, status)
	if st, err := ReadStatus(root); err != nil || st.State != "done" {
		t.Fatalf("ReadStatus = %+v, %v", st, err)
	}
	fi, _ := os.Lstat(status)
	if fi.Mode().Perm() != 0o644 {
		t.Fatalf("tệp trạng thái genh ghi phải 0644, có %v", fi.Mode().Perm())
	}
}

// (2) Cài sẵn symlink ở mọi tên tạm đoán được → không ảnh hưởng (CreateTemp O_EXCL tên ngẫu nhiên).
func TestStatusWriteIgnoresPlantedTempSymlinks(t *testing.T) {
	root := t.TempDir()
	if err := EnsureDir(root); err != nil {
		t.Fatal(err)
	}
	target, body := secretTarget(t)
	names := []string{StatusFile + ".tmp", "." + StatusFile + ".tmp"}
	for i := 0; i < 50; i++ {
		names = append(names, StatusFile+"."+itoa(i)+".tmp")
	}
	for _, n := range names {
		if err := os.Symlink(target, filepath.Join(Dir(root), n)); err != nil {
			t.Fatal(err)
		}
	}
	if err := Start(root, "v0.1.44"); err != nil {
		t.Fatal(err)
	}
	if err := Finish(root, "failed", "", "x"); err != nil {
		t.Fatal(err)
	}
	assertUnchanged(t, target, body)
	assertRegular(t, filepath.Join(Dir(root), StatusFile))
	for _, n := range names {
		fi, err := os.Lstat(filepath.Join(Dir(root), n))
		if err != nil || fi.Mode()&os.ModeSymlink == 0 {
			t.Fatalf("symlink bẫy %s bị đụng: %v, %v", n, fi, err)
		}
	}
}

func itoa(i int) string {
	const digits = "0123456789"
	if i < 10 {
		return digits[i : i+1]
	}
	return itoa(i/10) + digits[i%10:i%10+1]
}

// (3) request/restore.json là symlink tới tệp HỢP LỆ → từ chối, không theo, dọn symlink.
func TestConsumeRestoreRequestRejectsSymlink(t *testing.T) {
	root := t.TempDir()
	if err := EnsureDir(root); err != nil {
		t.Fatal(err)
	}
	valid := filepath.Join(t.TempDir(), "restore.json")
	body := `{"key":"backup-20260101T000000Z"}`
	if err := os.WriteFile(valid, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(valid, RestoreRequestPath(root)); err != nil {
		t.Fatal(err)
	}
	if !HasRestoreRequest(root) {
		t.Fatal("symlink vẫn phải tính là có yêu cầu để genh dọn")
	}
	r, err := ConsumeRestoreRequest(root)
	if err == nil || r.Key != "" {
		t.Fatalf("symlink phải bị từ chối, có %+v, %v", r, err)
	}
	if _, err := os.Lstat(RestoreRequestPath(root)); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("symlink yêu cầu phải bị xoá: %v", err)
	}
	assertUnchanged(t, valid, body)
}

// withOwnerAllowed thay ownerAllowed trong một test.
func withOwnerAllowed(t *testing.T, f func(string, uint32) bool) {
	t.Helper()
	old := ownerAllowed
	ownerAllowed = f
	t.Cleanup(func() { ownerAllowed = old })
}

// (4) Tệp yêu cầu do uid lạ → bỏ qua, xoá tệp, update-status failed.
func TestForeignOwnerRequestsIgnored(t *testing.T) {
	root := t.TempDir()
	if err := EnsureDir(root); err != nil {
		t.Fatal(err)
	}
	withOwnerAllowed(t, func(string, uint32) bool { return false })

	write := func(path, body string) {
		t.Helper()
		if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	// update.json — HasRequest (watcher) và ConsumeRequest (lịch đêm) đều bỏ qua.
	write(RequestPath(root), `{"requested_at":"x"}`)
	if HasRequest(root) {
		t.Fatal("HasRequest phải bỏ qua tệp yêu cầu của uid lạ")
	}
	if _, err := os.Lstat(RequestPath(root)); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("tệp yêu cầu lạ phải bị xoá: %v", err)
	}
	st, err := ReadStatus(root)
	if err != nil || st.State != "failed" || st.Message != BadUpdateRequestMessage {
		t.Fatalf("update-status = %+v, %v", st, err)
	}
	write(RequestPath(root), `{"requested_at":"x"}`)
	if c, err := ConsumeRequest(root); c || err != nil {
		t.Fatalf("ConsumeRequest phải bỏ qua tệp yêu cầu của uid lạ: consumed=%v err=%v", c, err)
	}
	if _, err := os.Lstat(RequestPath(root)); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("tệp yêu cầu lạ phải bị xoá: %v", err)
	}

	// restore / doctor / offsite / watchdog.
	write(RestoreRequestPath(root), `{"key":"backup-1"}`)
	if _, err := ConsumeRestoreRequest(root); !errors.Is(err, errForeignRequest) {
		t.Fatalf("restore: %v", err)
	}
	write(DoctorRequestPath(root), `{"schema":1,"request_id":"0123456789abcdef"}`)
	if _, err := ConsumeDoctorRequest(root); !errors.Is(err, errForeignRequest) {
		t.Fatalf("doctor: %v", err)
	}
	write(OffsiteRequestPath(root), `{"action":"run"}`)
	if _, err := ReadOffsiteRequest(root); !errors.Is(err, errForeignRequest) {
		t.Fatalf("offsite: %v", err)
	}
	write(WatchdogRequestPath(root), `{"action":"test"}`)
	if _, err := ConsumeWatchdogRequest(root); !errors.Is(err, errForeignRequest) {
		t.Fatalf("watchdog: %v", err)
	}
	for _, p := range []string{RestoreRequestPath(root), DoctorRequestPath(root), WatchdogRequestPath(root)} {
		if _, err := os.Lstat(p); !errors.Is(err, os.ErrNotExist) {
			t.Fatalf("%s phải bị xoá: %v", p, err)
		}
	}

	// Chủ hợp lệ (mặc định: người chạy genh) → nhận.
	withOwnerAllowed(t, func(_ string, uid uint32) bool { return uid == uint32(os.Getuid()) })
	write(RequestPath(root), `{"requested_at":"x"}`)
	if !HasRequest(root) {
		t.Fatal("tệp yêu cầu hợp lệ phải được nhận")
	}
	if c, err := ConsumeRequest(root); !c || err != nil {
		t.Fatalf("tệp yêu cầu hợp lệ phải được nhận: consumed=%v err=%v", c, err)
	}
}

// update.json là symlink / hard link → bỏ qua như tệp lạ.
func TestUpdateRequestSymlinkAndHardlinkIgnored(t *testing.T) {
	root := t.TempDir()
	if err := EnsureDir(root); err != nil {
		t.Fatal(err)
	}
	target, body := secretTarget(t)
	if err := os.Symlink(target, RequestPath(root)); err != nil {
		t.Fatal(err)
	}
	if HasRequest(root) {
		t.Fatal("symlink update.json không phải yêu cầu hợp lệ")
	}
	assertUnchanged(t, target, body)
	other := filepath.Join(Dir(root), "x.json")
	if err := os.WriteFile(other, []byte(`{}`), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Link(other, RequestPath(root)); err != nil {
		t.Fatal(err)
	}
	if c, _ := ConsumeRequest(root); c {
		t.Fatal("hard link update.json không phải yêu cầu hợp lệ")
	}
}

// APIUID: GENH_API_UID > config/hostlink.json > 10001.
func TestAPIUIDSources(t *testing.T) {
	root := t.TempDir()
	t.Setenv(EnvAPIUID, "")
	if got := APIUID(root); got != DefaultAPIUID {
		t.Fatalf("mặc định = %d", got)
	}
	if err := WriteAPIUID(root, 165536); err != nil {
		t.Fatal(err)
	}
	fi, err := os.Stat(hostlinkConfigPath(root))
	if err != nil || fi.Mode().Perm() != 0o600 {
		t.Fatalf("config/hostlink.json phải 0600: %v, %v", fi, err)
	}
	if strings.HasPrefix(hostlinkConfigPath(root), Dir(root)) {
		t.Fatal("hostlink.json không được nằm trong run/")
	}
	if got := APIUID(root); got != 165536 {
		t.Fatalf("từ hostlink.json = %d", got)
	}
	t.Setenv(EnvAPIUID, "200000")
	if got := APIUID(root); got != 200000 {
		t.Fatalf("từ biến môi trường = %d", got)
	}
}

// (5) EnsureDir: thư mục mới không mở cho "người khác"; thư mục đã 2770 không bị mở lại.
func TestEnsureDirDoesNotReopen(t *testing.T) {
	root := t.TempDir()
	if err := EnsureDir(root); err != nil {
		t.Fatal(err)
	}
	for _, d := range []string{Dir(root), RequestDirPath(root)} {
		fi, err := os.Stat(d)
		if err != nil {
			t.Fatal(err)
		}
		if runtime.GOOS == "linux" && fi.Mode().Perm()&0o007 != 0 {
			t.Fatalf("%s mới tạo = %v, không được mở cho người khác", d, fi.Mode().Perm())
		}
		if runtime.GOOS == "darwin" && fi.Mode().Perm() != 0o777 {
			t.Fatalf("macOS giữ 0777 như cũ, có %v", fi.Mode().Perm())
		}
	}
	if runtime.GOOS != "linux" {
		return
	}
	for _, d := range []string{Dir(root), RequestDirPath(root)} {
		if err := os.Chmod(d, os.ModeSetgid|0o770); err != nil {
			t.Fatal(err)
		}
	}
	// Ghi trạng thái nhiều lần (mỗi lần gọi EnsureDir) — không được chmod 0777 lại.
	if err := Start(root, "v1"); err != nil {
		t.Fatal(err)
	}
	if err := WriteInfo(root, "v1", "systemd", nil); err != nil {
		t.Fatal(err)
	}
	for _, d := range []string{Dir(root), RequestDirPath(root)} {
		fi, _ := os.Stat(d)
		if fi.Mode()&os.ModeSetgid == 0 || fi.Mode().Perm() != 0o770 {
			t.Fatalf("%s bị đổi quyền: %v", d, fi.Mode())
		}
	}
}

// EnsureRunPerms (container phụ) — ngoài Linux trả n/a, không gọi docker.
func TestEnsureRunPermsNonLinux(t *testing.T) {
	r := &fake.Runner{}
	mode, err := EnsureRunPerms(context.Background(), RunPermSpec{InstallDir: t.TempDir(), Runner: r, GOOS: "darwin"})
	if mode != RunModeNA || err != nil || len(r.Calls) != 0 {
		t.Fatalf("darwin: %q, %v, %d lệnh", mode, err, len(r.Calls))
	}
}

// docker lỗi: thư mục ĐÃ siết từ lần trước (2770) thì giữ nguyên; mới tạo (0770, chưa nhóm 10001) thì mở 0777.
func TestEnsureRunPermsDockerErrorKeepsRestricted(t *testing.T) {
	if runtime.GOOS != "linux" {
		return // dấu setgid chỉ đáng tin trên Linux (xem dirsRestricted)
	}
	r := &fake.Runner{Responses: []fake.Response{{Err: errors.New("Cannot connect to the Docker daemon")}}}
	spec := func(root string) RunPermSpec {
		return RunPermSpec{InstallDir: root, Image: "img", Runner: r, GOOS: "linux", Getuid: func() int { return 1000 }}
	}
	fresh := t.TempDir()
	if mode, err := EnsureRunPerms(context.Background(), spec(fresh)); mode != RunModeOpen || err == nil {
		t.Fatalf("thư mục mới + docker lỗi: %q, %v", mode, err)
	}
	done := t.TempDir()
	if err := EnsureDir(done); err != nil {
		t.Fatal(err)
	}
	for _, d := range []string{Dir(done), RequestDirPath(done)} {
		_ = os.Chmod(d, os.ModeSetgid|0o770)
	}
	if mode, err := EnsureRunPerms(context.Background(), spec(done)); mode != RunModeRestricted || err != nil {
		t.Fatalf("đã siết + docker lỗi phải giữ: %q, %v", mode, err)
	}
	if fi, _ := os.Stat(Dir(done)); fi.Mode().Perm() != 0o770 {
		t.Fatalf("không được mở lại: %v", fi.Mode())
	}
}

// Container phụ báo ảnh cũ (gid khác 10001) → mở 0777 kể cả khi đã 2770.
func TestEnsureRunPermsOldImageReopens(t *testing.T) {
	root := t.TempDir()
	if err := EnsureDir(root); err != nil {
		t.Fatal(err)
	}
	for _, d := range []string{Dir(root), RequestDirPath(root)} {
		_ = os.Chmod(d, os.ModeSetgid|0o770)
	}
	r := &fake.Runner{Responses: []fake.Response{{Match: func(c dockercli.Cmd) bool { return c.Args[0] == "run" }, Output: []byte(runPermOldImage)}}}
	mode, err := EnsureRunPerms(context.Background(), RunPermSpec{
		InstallDir: root, Image: "ghcr.io/o/gen-harness-api:v0.1.44", Runner: r, GOOS: "linux",
		Getuid: func() int { return 1000 },
	})
	if mode != RunModeOpen || !errors.Is(err, errRunPermOldImage) {
		t.Fatalf("ảnh cũ: %q, %v", mode, err)
	}
	fi, _ := os.Stat(Dir(root))
	if fi.Mode().Perm() != 0o777 || fi.Mode()&os.ModeSetgid != 0 {
		t.Fatalf("ảnh cũ phải mở lại 0777, có %v", fi.Mode())
	}
}

// Sửa review v0.1.45: genh chạy bằng root + ảnh api cũ (vd quay về 0.1.44 sau cập nhật lỗi) → vẫn hỏi container
// phụ, ảnh báo GH_RUNPERM_OLD_IMAGE ⇒ mở 0777, KHÔNG chown nhóm 10001.
func TestEnsureRunPermsRootOldImageReopens(t *testing.T) {
	root := t.TempDir()
	if err := EnsureDir(root); err != nil {
		t.Fatal(err)
	}
	r := &fake.Runner{Responses: []fake.Response{{Match: func(c dockercli.Cmd) bool { return c.Args[0] == "run" }, Output: []byte(runPermOldImage)}}}
	chowned := 0
	mode, err := EnsureRunPerms(context.Background(), RunPermSpec{
		InstallDir: root, Image: "ghcr.io/o/gen-harness-api:v0.1.44", Runner: r, GOOS: "linux",
		Getuid: func() int { return 0 },
		Chown:  func(string, int, int) error { chowned++; return nil },
	})
	if mode != RunModeOpen || !errors.Is(err, errRunPermOldImage) {
		t.Fatalf("root + ảnh cũ: %q, %v", mode, err)
	}
	if chowned != 0 || len(r.Calls) != 1 {
		t.Fatalf("root + ảnh cũ: không được chown (%d lần), phải gọi container phụ đúng 1 lần (%d)", chowned, len(r.Calls))
	}
	if fi, _ := os.Stat(Dir(root)); fi.Mode().Perm() != 0o777 {
		t.Fatalf("ảnh cũ phải mở lại 0777, có %v", fi.Mode())
	}
}

// root không có docker (không xác định được ảnh) → siết trực tiếp như trước: chown nhóm 10001 + 2770.
func TestEnsureRunPermsRootWithoutDockerChownsDirectly(t *testing.T) {
	root := t.TempDir()
	var gids []int
	mode, err := EnsureRunPerms(context.Background(), RunPermSpec{
		InstallDir: root, GOOS: "linux",
		Getuid: func() int { return 0 },
		Chown:  func(_ string, _ int, gid int) error { gids = append(gids, gid); return nil },
	})
	if mode != RunModeRestricted || err != nil || len(gids) != 2 || gids[0] != APIGID {
		t.Fatalf("root không docker: %q, %v, gids=%v", mode, err, gids)
	}
}
