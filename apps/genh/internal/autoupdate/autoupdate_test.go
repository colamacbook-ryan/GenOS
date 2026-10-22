package autoupdate

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// fakeRunner ghi lại mọi lệnh được gọi, trả về output/lỗi định sẵn theo tên
// lệnh — dùng cho mọi test dispatch dưới đây (KHÔNG bao giờ gọi
// systemctl/launchctl/schtasks/crontab thật).
type fakeRunner struct {
	calls []call
	// outputs ánh xạ "name args..." (nối bằng "|") -> output giả.
	outputs map[string][]byte
	// errs ánh xạ cùng khoá -> lỗi giả.
	errs map[string]error
}

type call struct {
	name string
	args []string
}

func newFakeRunner() *fakeRunner {
	return &fakeRunner{outputs: map[string][]byte{}, errs: map[string]error{}}
}

func (f *fakeRunner) key(name string, args []string) string {
	return name + "|" + strings.Join(args, "|")
}

func (f *fakeRunner) Output(ctx context.Context, name string, args []string) ([]byte, error) {
	f.calls = append(f.calls, call{name: name, args: args})
	k := f.key(name, args)
	return f.outputs[k], f.errs[k]
}

func (f *fakeRunner) calledWith(name string, argsContains ...string) bool {
	for _, c := range f.calls {
		if c.name != name {
			continue
		}
		joined := strings.Join(c.args, " ")
		ok := true
		for _, want := range argsContains {
			if !strings.Contains(joined, want) {
				ok = false
				break
			}
		}
		if ok {
			return true
		}
	}
	return false
}

func TestEnableLinux_UsesSystemdWhenAvailable(t *testing.T) {
	home := t.TempDir()
	runner := newFakeRunner()
	// systemctl --user daemon-reload thành công -> systemd --user khả dụng.
	deps := Deps{
		Runner: runner, GenhPath: "/opt/genh/bin/genh", LogFile: "/opt/genh/logs/auto-update.log", HomeDir: home, GOOS: "linux",
		LookPath: func(string) (string, error) { return "/usr/bin/systemctl", nil },
	}

	res, err := Enable(context.Background(), deps)
	if err != nil {
		t.Fatalf("Enable: %v", err)
	}
	mustContain(t, res.Msg, "03:00")
	if res.Mechanism != ScheduleSystemd {
		t.Errorf("Mechanism = %q", res.Mechanism)
	}

	if !runner.calledWith("systemctl", "--user", "enable", "--now", TaskName+".timer") {
		t.Fatalf("phải gọi systemctl --user enable --now, các lệnh đã gọi: %+v", runner.calls)
	}
	svc, err := os.ReadFile(filepath.Join(home, ".config", "systemd", "user", TaskName+".service"))
	if err != nil {
		t.Fatalf("phải ghi được service unit: %v", err)
	}
	mustContain(t, string(svc), "/opt/genh/bin/genh update --yes --quiet")

	if _, err := os.Stat(filepath.Join(home, ".config", "systemd", "user", TaskName+".timer")); err != nil {
		t.Fatalf("phải ghi được timer unit: %v", err)
	}
}

func TestEnableLinux_FallsBackToCrontab_WhenNoSystemdUser(t *testing.T) {
	home := t.TempDir()
	runner := newFakeRunner()
	runner.errs["systemctl|--user|daemon-reload"] = errCommandFailed
	runner.outputs["crontab|-l"] = []byte("0 9 * * * some-other-job\n")

	deps := Deps{
		Runner: runner, GenhPath: "/opt/genh/bin/genh", LogFile: "/opt/genh/logs/auto-update.log", HomeDir: home, GOOS: "linux",
		// LookPath thành công (systemctl "có" trên PATH) NHƯNG daemon-reload
		// thất bại (giả session --user không hoạt động, kiểu container/WSL
		// không có D-Bus) — vẫn phải rơi về crontab.
		LookPath: func(string) (string, error) { return "/usr/bin/systemctl", nil },
	}
	res, err := Enable(context.Background(), deps)
	if err != nil {
		t.Fatalf("Enable: %v", err)
	}
	mustContain(t, res.Msg, "crontab")

	if !runner.calledWith("crontab") {
		t.Fatalf("phải gọi crontab để cài lịch, các lệnh đã gọi: %+v", runner.calls)
	}
	if runner.calledWith("systemctl", "enable") {
		t.Fatalf("KHÔNG được gọi systemctl enable khi systemd --user không khả dụng")
	}
}

func TestEnableLinux_FallsBackToCrontab_WhenNoSystemctlBinary(t *testing.T) {
	home := t.TempDir()
	runner := newFakeRunner()
	runner.outputs["crontab|-l"] = []byte("")

	deps := Deps{
		Runner: runner, GenhPath: "genh", LogFile: "/tmp/log", HomeDir: home, GOOS: "linux",
		LookPath: func(string) (string, error) { return "", os.ErrNotExist },
	}
	if _, err := Enable(context.Background(), deps); err != nil {
		t.Fatalf("Enable: %v", err)
	}
	if runner.calledWith("systemctl") {
		t.Fatalf("KHÔNG được gọi systemctl khi LookPath báo không có binary, các lệnh: %+v", runner.calls)
	}
	if !runner.calledWith("crontab") {
		t.Fatalf("phải rơi về crontab")
	}
}

func TestDisableLinux_RemovesUnitsAndCrontabLine(t *testing.T) {
	home := t.TempDir()
	dir := filepath.Join(home, ".config", "systemd", "user")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, TaskName+".service"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, TaskName+".timer"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	runner := newFakeRunner()
	runner.outputs["crontab|-l"] = []byte(CrontabMarker + "\n0 3 * * * genh update --yes --quiet\n0 9 * * * keep-me\n")

	deps := Deps{Runner: runner, GenhPath: "/opt/genh/bin/genh", LogFile: "/tmp/log", HomeDir: home, GOOS: "linux"}
	if _, err := Disable(context.Background(), deps); err != nil {
		t.Fatalf("Disable: %v", err)
	}

	if _, err := os.Stat(filepath.Join(dir, TaskName+".service")); !os.IsNotExist(err) {
		t.Fatalf("service unit phải bị xoá, err=%v", err)
	}
	if _, err := os.Stat(filepath.Join(dir, TaskName+".timer")); !os.IsNotExist(err) {
		t.Fatalf("timer unit phải bị xoá, err=%v", err)
	}
	if !runner.calledWith("crontab", "-l") {
		t.Fatalf("phải đọc crontab hiện tại để lọc dòng genh")
	}
}

func TestStatusLinux_ReportsEnabled(t *testing.T) {
	rr := &recRunner{}
	rr.on("show gen-harness-update.timer", "LoadState=loaded\nUnitFileState=enabled\nActiveState=active\nLastTriggerUSec=@1760000000\nNextElapseUSecRealtime=@1760086400\n", nil)
	rr.on("loginctl show-user", "yes\n", nil)

	st, err := GetStatus(context.Background(), Deps{Runner: rr, GOOS: "linux", UID: "1000", HomeDir: t.TempDir()})
	if err != nil {
		t.Fatalf("GetStatus: %v", err)
	}
	if !st.Enabled || st.Mechanism != ScheduleSystemd || !st.UnitPresent || st.Active != "active" || st.Linger != "yes" {
		t.Fatalf("muốn bật/systemd/active/linger yes, được %+v", st)
	}
	if !st.LastRun.Equal(time.Unix(1760000000, 0)) || !st.NextRun.Equal(time.Unix(1760086400, 0)) {
		t.Errorf("mốc giờ '@giây' sai: last=%v next=%v", st.LastRun, st.NextRun)
	}
}

func TestStatusLinux_FallsBackToCrontabCheck(t *testing.T) {
	rr := &recRunner{}
	rr.on("show gen-harness-update.timer", "LoadState=not-found\nUnitFileState=\nActiveState=inactive\n", nil)
	rr.on("crontab -l", CrontabMarker+"\n0 3 * * * genh update --yes --quiet\n", nil)

	st, err := GetStatus(context.Background(), Deps{Runner: rr, GOOS: "linux", HomeDir: t.TempDir()})
	if err != nil {
		t.Fatalf("GetStatus: %v", err)
	}
	if !st.Enabled || st.Mechanism != ScheduleCron || st.UnitPresent != true {
		t.Fatalf("muốn Enabled=true (thấy marker trong crontab), được %+v", st)
	}
}

func TestEnableDarwin_WritesPlistAndLoads(t *testing.T) {
	home := t.TempDir()
	runner := newFakeRunner()
	deps := Deps{Runner: runner, GenhPath: "/Users/o/.gen-harness/bin/genh", LogFile: "/Users/o/.gen-harness/logs/auto-update.log", HomeDir: home, GOOS: "darwin"}

	if _, err := Enable(context.Background(), deps); err != nil {
		t.Fatalf("Enable: %v", err)
	}
	if !runner.calledWith("launchctl", "load", "-w") {
		t.Fatalf("phải gọi launchctl load -w, các lệnh: %+v", runner.calls)
	}
	plistPath := filepath.Join(home, "Library", "LaunchAgents", "com.gen-harness.update.plist")
	data, err := os.ReadFile(plistPath)
	if err != nil {
		t.Fatalf("phải ghi được plist: %v", err)
	}
	mustContain(t, string(data), "/Users/o/.gen-harness/bin/genh")
}

func TestDisableDarwin_UnloadsAndRemovesPlist(t *testing.T) {
	home := t.TempDir()
	dir := filepath.Join(home, "Library", "LaunchAgents")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	plistPath := filepath.Join(dir, "com.gen-harness.update.plist")
	if err := os.WriteFile(plistPath, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	runner := newFakeRunner()
	deps := Deps{Runner: runner, HomeDir: home, GOOS: "darwin"}

	if _, err := Disable(context.Background(), deps); err != nil {
		t.Fatalf("Disable: %v", err)
	}
	if !runner.calledWith("launchctl", "unload") {
		t.Fatalf("phải gọi launchctl unload")
	}
	if _, err := os.Stat(plistPath); !os.IsNotExist(err) {
		t.Fatalf("plist phải bị xoá, err=%v", err)
	}
}

func TestEnableWindows_CallsSchtasksCreate(t *testing.T) {
	runner := newFakeRunner()
	deps := Deps{Runner: runner, GenhPath: `C:\Users\o\genh.exe`, LogFile: `C:\Users\o\log.txt`, GOOS: "windows"}

	if _, err := Enable(context.Background(), deps); err != nil {
		t.Fatalf("Enable: %v", err)
	}
	if !runner.calledWith("schtasks", "/Create", "/TN", TaskName) {
		t.Fatalf("phải gọi schtasks /Create, các lệnh: %+v", runner.calls)
	}
}

func TestDisableWindows_CallsSchtasksDelete(t *testing.T) {
	runner := newFakeRunner()
	if _, err := Disable(context.Background(), Deps{Runner: runner, GOOS: "windows"}); err != nil {
		t.Fatalf("Disable: %v", err)
	}
	if !runner.calledWith("schtasks", "/Delete", "/TN", TaskName) {
		t.Fatalf("phải gọi schtasks /Delete, các lệnh: %+v", runner.calls)
	}
}

func TestStatusWindows_NotCreated(t *testing.T) {
	runner := newFakeRunner()
	runner.errs["schtasks|/Query|/TN|"+TaskName+"|/FO|LIST|/V"] = errCommandFailed

	st, err := GetStatus(context.Background(), Deps{Runner: runner, GOOS: "windows"})
	if err != nil {
		t.Fatalf("GetStatus: %v", err)
	}
	if st.Enabled {
		t.Fatalf("muốn Enabled=false khi chưa tạo task, được %+v", st)
	}
}

var errCommandFailed = &fakeCmdError{"lệnh giả thất bại"}

type fakeCmdError struct{ msg string }

func (e *fakeCmdError) Error() string { return e.msg }

// v0.1.37: RefreshUnits ghi lại unit lịch đêm đã cài nếu khác bản hiện tại.
func TestRefreshUnits(t *testing.T) {
	home := t.TempDir()
	deps := func(r *fakeRunner) Deps {
		return Deps{Runner: r, GenhPath: "/g/genh", LogFile: "/g/log", HomeDir: home, GOOS: "linux"}
	}
	path := serviceUnitPath(home)

	// Chưa có unit (lịch đêm tắt / crontab) → không tạo, không gọi gì.
	r := newFakeRunner()
	changed, err := RefreshUnits(context.Background(), deps(r))
	if err != nil || changed || len(r.calls) != 0 {
		t.Fatalf("chưa có unit: changed=%v err=%v calls=%+v", changed, err, r.calls)
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatal("không được tạo unit khi chưa có")
	}

	// Unit cũ (thiếu KillMode) → CHỈ thêm dòng thiếu + daemon-reload, không
	// enable/disable; giữ sửa tay của Owner và ExecStart cũ (binary ở đường dẫn
	// khác — không trỏ lịch đêm sang genh đang chạy lệnh này).
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	old := "[Unit]\nDescription=cu\n\n[Service]\nType=oneshot\nExecStart=/opt/khac/genh update --yes --quiet\nEnvironment=HTTPS_PROXY=http://proxy:3128\nNice=10\n"
	if err := os.WriteFile(path, []byte(old), 0o644); err != nil {
		t.Fatal(err)
	}
	r = newFakeRunner()
	changed, err = RefreshUnits(context.Background(), deps(r))
	if err != nil || !changed {
		t.Fatalf("unit cũ: changed=%v err=%v", changed, err)
	}
	b, _ := os.ReadFile(path)
	wantPatched := "[Unit]\nDescription=cu\n\n[Service]\nKillMode=mixed\nTimeoutStopSec=900\nType=oneshot\nExecStart=/opt/khac/genh update --yes --quiet\nEnvironment=HTTPS_PROXY=http://proxy:3128\nNice=10\n"
	if string(b) != wantPatched {
		t.Fatalf("unit phải chỉ được thêm dòng thiếu:\n%s", b)
	}
	if len(r.calls) != 1 || !r.calledWith("systemctl", "--user daemon-reload") {
		t.Fatalf("chỉ được gọi daemon-reload: %+v", r.calls)
	}

	// Đã đúng → không gọi gì.
	r = newFakeRunner()
	changed, err = RefreshUnits(context.Background(), deps(r))
	if err != nil || changed || len(r.calls) != 0 {
		t.Fatalf("đã đúng: changed=%v err=%v calls=%+v", changed, err, r.calls)
	}

	// Owner tự đặt KillMode khác (đã có khoá) → giữ nguyên, chỉ thêm TimeoutStopSec.
	_ = os.WriteFile(path, []byte("[Service]\nKillMode=control-group\nExecStart=/g/genh update\n"), 0o644)
	if changed, err := RefreshUnits(context.Background(), deps(newFakeRunner())); err != nil || !changed {
		t.Fatalf("thiếu TimeoutStopSec: changed=%v err=%v", changed, err)
	}
	if b, _ := os.ReadFile(path); string(b) != "[Service]\nTimeoutStopSec=900\nKillMode=control-group\nExecStart=/g/genh update\n" {
		t.Fatalf("không được đổi KillMode Owner đặt:\n%s", b)
	}

	// Hệ điều hành khác Linux → không làm gì.
	_ = os.WriteFile(path, []byte(old), 0o644)
	r = newFakeRunner()
	d := deps(r)
	d.GOOS = "darwin"
	if changed, err := RefreshUnits(context.Background(), d); err != nil || changed || len(r.calls) != 0 {
		t.Fatalf("darwin: changed=%v err=%v", changed, err)
	}
}
