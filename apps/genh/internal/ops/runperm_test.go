package ops

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
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
)

// v0.1.45: siết hộp thư run/ về 2770 nhóm 10001 sau `docker compose up`.

const testAPIImage = "ghcr.io/o/gen-harness-api:v0.1.45"

// runPermEnv dựng thư mục cài đặt có compose.yaml (api: image cố định) và đặt
// điểm tiêm GOOS/uid/Chown cho test.
func runPermEnv(t *testing.T, uid int, chown func(string, int, int) error) (*Env, string) {
	t.Helper()
	root := t.TempDir()
	composePath := filepath.Join(root, "compose.yaml")
	if err := os.WriteFile(composePath, []byte("services:\n  api:\n    image: "+testAPIImage+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	oldGOOS, oldUID, oldChown := runPermGOOS, runPermGetuid, runPermChown
	runPermGOOS = "linux"
	runPermGetuid = func() int { return uid }
	if chown != nil {
		runPermChown = chown
	}
	t.Cleanup(func() { runPermGOOS, runPermGetuid, runPermChown = oldGOOS, oldUID, oldChown })
	env := &Env{InstallDir: root, locate: func(string) (string, error) { return composePath, nil }}
	return env, composePath
}

// skipOnDarwin: các test kiểm chế độ 2770 dựa vào nhánh Linux của
// hostlink.ensureRunDirs (theo runtime.GOOS thật, không theo runPermGOOS) — trên
// macOS EnsureDir/SetRunMode chmod lại 0777 nên không thể giả Linux ở đây.
// Windows vẫn chạy để kiểm nhánh "n/a".
func skipOnDarwin(t *testing.T) {
	t.Helper()
	if runtime.GOOS != "linux" && runtime.GOOS != "windows" {
		t.Skipf("kiểm siết 2770 chỉ có nghĩa trên Linux (GOOS=%s)", runtime.GOOS)
	}
}

func isRunPermCmd(c dockercli.Cmd) bool {
	return len(c.Args) > 0 && c.Args[0] == "run" && strings.Contains(strings.Join(c.Args, " "), ":/r")
}

func TestEnsureRunDirPermsHelperContainer(t *testing.T) {
	skipOnDarwin(t)
	env, _ := runPermEnv(t, 1000, nil)
	r := &fake.Runner{Responses: []fake.Response{{Match: isRunPermCmd, Output: []byte("GH_RUNPERM_OK\n")}}}
	if runtime.GOOS != "windows" {
		// Giả container phụ đã siết xong (chmod do container làm trên máy thật).
		if err := hostlink.EnsureDir(env.InstallDir); err != nil {
			t.Fatal(err)
		}
		for _, d := range []string{hostlink.Dir(env.InstallDir), hostlink.RequestDirPath(env.InstallDir)} {
			if err := os.Chmod(d, os.ModeSetgid|0o770); err != nil {
				t.Fatal(err)
			}
		}
	}
	mode, err := EnsureRunDirPerms(context.Background(), env, r)
	if runtime.GOOS == "windows" {
		if mode != hostlink.RunModeNA || len(r.Calls) != 0 {
			t.Fatalf("windows: %q, %d lệnh", mode, len(r.Calls))
		}
		return
	}
	if mode != hostlink.RunModeRestricted || err != nil {
		t.Fatalf("mode = %q, %v", mode, err)
	}
	if len(r.Calls) != 1 {
		t.Fatalf("phải đúng một lệnh docker, có %d", len(r.Calls))
	}
	args := strings.Join(r.Calls[0].Cmd.Args, " ")
	for _, want := range []string{
		"run --rm --network none --user 0:0",
		"-v " + hostlink.Dir(env.InstallDir) + ":/r",
		"--entrypoint /bin/sh " + testAPIImage + " -c",
		"chgrp 10001 /r /r/request",
		"chmod 2770 /r /r/request",
	} {
		if !strings.Contains(args, want) {
			t.Fatalf("lệnh container phụ thiếu %q: %s", want, args)
		}
	}
	info, err := hostlink.ReadInfo(env.InstallDir)
	if err != nil || info.RunMode != hostlink.RunModeRestricted {
		t.Fatalf("genh.json run_mode = %+v, %v", info, err)
	}
}

func TestEnsureRunDirPermsDockerErrorFallsBackOpen(t *testing.T) {
	env, _ := runPermEnv(t, 1000, nil)
	r := &fake.Runner{Responses: []fake.Response{{Match: isRunPermCmd, Err: errors.New("Cannot connect to the Docker daemon")}}}
	mode, err := EnsureRunDirPerms(context.Background(), env, r)
	if runtime.GOOS == "windows" {
		if mode != hostlink.RunModeNA {
			t.Fatalf("windows: %q", mode)
		}
		return
	}
	if mode != hostlink.RunModeOpen || err == nil {
		t.Fatalf("lỗi docker phải trả mode open + lỗi, có %q, %v", mode, err)
	}
	for _, d := range []string{hostlink.Dir(env.InstallDir), hostlink.RequestDirPath(env.InstallDir)} {
		fi, err := os.Stat(d)
		if err != nil || fi.Mode().Perm() != 0o777 {
			t.Fatalf("%s phải mở lại 0777: %v, %v", d, fi, err)
		}
	}
	if info, _ := hostlink.ReadInfo(env.InstallDir); info.RunMode != hostlink.RunModeOpen {
		t.Fatalf("genh.json run_mode = %q", info.RunMode)
	}
	if ok, text := RunDirPermsLine(mode); ok || !strings.Contains(text, "run/ vẫn mở cho mọi người dùng trên máy") {
		t.Fatalf("dòng cảnh báo = %v %q", ok, text)
	}
}

func TestEnsureRunDirPermsRootChownsDirectly(t *testing.T) {
	skipOnDarwin(t)
	type call struct {
		path     string
		uid, gid int
	}
	var chowned []call
	env, _ := runPermEnv(t, 0, func(p string, uid, gid int) error {
		chowned = append(chowned, call{p, uid, gid})
		return nil
	})
	r := &fake.Runner{}
	mode, err := EnsureRunDirPerms(context.Background(), env, r)
	if runtime.GOOS == "windows" {
		if mode != hostlink.RunModeNA {
			t.Fatalf("windows: %q", mode)
		}
		return
	}
	if mode != hostlink.RunModeRestricted || err != nil {
		t.Fatalf("root: %q, %v", mode, err)
	}
	// Sửa review v0.1.45: root vẫn hỏi container phụ (kiểm ảnh cũ) trước; container phụ lỗi → chown trực tiếp.
	if len(r.Calls) != 1 || r.Calls[0].Cmd.Args[0] != "run" {
		t.Fatalf("root phải thử container phụ đúng 1 lần, có %+v", r.Calls)
	}
	want := []call{
		{hostlink.Dir(env.InstallDir), -1, hostlink.APIGID},
		{hostlink.RequestDirPath(env.InstallDir), -1, hostlink.APIGID},
	}
	if len(chowned) != len(want) {
		t.Fatalf("Chown = %+v", chowned)
	}
	for i := range want {
		if chowned[i] != want[i] {
			t.Fatalf("Chown[%d] = %+v, muốn %+v", i, chowned[i], want[i])
		}
	}
	fi, _ := os.Stat(hostlink.Dir(env.InstallDir))
	if fi.Mode().Perm() != 0o770 || (runtime.GOOS == "linux" && fi.Mode()&os.ModeSetgid == 0) {
		t.Fatalf("run/ phải 2770, có %v", fi.Mode())
	}
}

// Không có image cố định (build:) → hỏi ảnh của container api đang chạy.
func TestEnsureRunDirPermsResolvesRunningImage(t *testing.T) {
	env, composePath := runPermEnv(t, 1000, nil)
	if err := os.WriteFile(composePath, []byte("services:\n  api:\n    build: { context: .. }\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	r := &fake.Runner{Responses: []fake.Response{
		{Match: func(c dockercli.Cmd) bool { return strings.Contains(strings.Join(c.Args, " "), "ps -q api") }, Output: []byte("abc123\n")},
		{Match: func(c dockercli.Cmd) bool { return len(c.Args) > 0 && c.Args[0] == "inspect" }, Output: []byte("sha256:deadbeef\n")},
		{Match: isRunPermCmd, Err: errors.New("thử")},
	}}
	_, _ = EnsureRunDirPerms(context.Background(), env, r)
	if runtime.GOOS == "windows" {
		return
	}
	var ran string
	for _, c := range r.Calls {
		if isRunPermCmd(c.Cmd) {
			ran = strings.Join(c.Cmd.Args, " ")
		}
	}
	if !strings.Contains(ran, "--entrypoint /bin/sh sha256:deadbeef -c") {
		t.Fatalf("container phụ phải dùng ảnh container api đang chạy: %q", ran)
	}
}
