package autoupdate

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

var testRP = RequestPaths{InstallDir: "/home/u/.gen-harness", RequestDir: "/home/u/.gen-harness/run/request", RequestFile: "/home/u/.gen-harness/run/request/update.json",
	RestoreFile: "/home/u/.gen-harness/run/request/restore.json"}

func TestEnsureRequestWatcher_SystemdPathUnit(t *testing.T) {
	home := t.TempDir()
	runner := newFakeRunner()
	deps := Deps{Runner: runner, GenhPath: "/home/u/.gen-harness/bin/genh", LogFile: "/home/u/.gen-harness/logs/auto-update.log",
		HomeDir: home, GOOS: "linux", LookPath: func(string) (string, error) { return "/usr/bin/systemctl", nil }}
	got, err := EnsureRequestWatcher(context.Background(), deps, testRP)
	if err != nil || got != UpdaterSystemd {
		t.Fatalf("EnsureRequestWatcher = %q, %v", got, err)
	}
	dir := filepath.Join(home, ".config", "systemd", "user")
	path, _ := os.ReadFile(filepath.Join(dir, RequestTaskName+".path"))
	mustContain(t, string(path), "PathExists="+testRP.RequestFile)
	mustContain(t, string(path), "PathExists="+testRP.RestoreFile)
	mustContain(t, string(path), "Unit="+RequestTaskName+".service")
	svc, _ := os.ReadFile(filepath.Join(dir, RequestTaskName+".service"))
	mustContain(t, string(svc), "handle-requests --quiet")
	mustContain(t, string(svc), "Environment=GEN_HARNESS_HOME=/home/u/.gen-harness")
	// Cổng khác mặc định được truyền theo để bước kiểm /ready gọi đúng cổng.
	rp := RequestPaths{InstallDir: "/r", RequestDir: "/r/run/request", RequestFile: "/r/run/request/update.json", Port: 9443,
		Env: []string{"GENH_COMPOSE_FILE=/src/deploy/compose.yaml"}}
	unit := SystemdRequestServiceUnit("/g/genh", "/g/log", rp)
	mustContain(t, unit, "handle-requests --quiet --port 9443")
	// Biến môi trường của phiên cài (compose ngoài thư mục genh) đi theo watcher.
	mustContain(t, unit, "Environment=GENH_COMPOSE_FILE=/src/deploy/compose.yaml")
	plist := LaunchdRequestPlist("/g/genh", "/g/log", rp)
	mustContain(t, plist, "<string>--port</string>")
	mustContain(t, plist, "<key>GENH_COMPOSE_FILE</key>")
	mustContain(t, CrontabRequestLine("/g/genh", "/g/log", rp), "GENH_COMPOSE_FILE=/src/deploy/compose.yaml ")
	if !runner.calledWith("systemctl", "--user", "enable", "--now", RequestTaskName+".path") {
		t.Fatalf("phải enable --now path unit: %+v", runner.calls)
	}
}

func TestEnsureRequestWatcher_CronFallbackKeepsNightlyLine(t *testing.T) {
	runner := newFakeRunner()
	runner.errs["systemctl|--user|daemon-reload"] = errors.New("no user bus")
	runner.outputs["crontab|-l"] = []byte("0 1 * * * backup.sh\n" + CrontabMarker + "\n7 3 * * * genh update --yes --quiet\n")
	deps := Deps{Runner: runner, GenhPath: "/g/genh", LogFile: "/g/log", HomeDir: t.TempDir(), GOOS: "linux",
		LookPath: func(string) (string, error) { return "/usr/bin/systemctl", nil }}
	got, err := EnsureRequestWatcher(context.Background(), deps, testRP)
	if err != nil || got != UpdaterCron {
		t.Fatalf("EnsureRequestWatcher = %q, %v", got, err)
	}
	line := CrontabRequestLine("/g/genh", "/g/log", testRP)
	mustContain(t, line, "* * * * * { [ -f "+testRP.RequestFile+" ] || [ -f "+testRP.RestoreFile+" ]; } && GEN_HARNESS_HOME=")
	mustContain(t, line, "handle-requests")
	// Bản cài cũ không có RestoreFile: vẫn một điều kiện như trước.
	old := CrontabRequestLine("/g/genh", "/g/log", RequestPaths{RequestFile: "/r/update.json"})
	mustContain(t, old, "* * * * * [ -f /r/update.json ] && ")
	merged := mergeCrontabMarked(string(runner.outputs["crontab|-l"]), CrontabRequestMarker, line, false)
	for _, want := range []string{"backup.sh", CrontabMarker, CrontabRequestMarker, "handle-requests"} {
		mustContain(t, merged, want)
	}
	// Gỡ watcher không đụng dòng hằng đêm.
	removed := mergeCrontabMarked(merged, CrontabRequestMarker, "", true)
	if strings.Contains(removed, "handle-requests") || !strings.Contains(removed, CrontabMarker) {
		t.Fatalf("gỡ watcher phải giữ dòng hằng đêm:\n%s", removed)
	}
}

func TestEnsureRequestWatcher_LaunchdQueueDirectory(t *testing.T) {
	home := t.TempDir()
	runner := newFakeRunner()
	deps := Deps{Runner: runner, GenhPath: "/g/genh", LogFile: "/g/log", HomeDir: home, GOOS: "darwin"}
	got, err := EnsureRequestWatcher(context.Background(), deps, testRP)
	if err != nil || got != UpdaterLaunchd {
		t.Fatalf("EnsureRequestWatcher = %q, %v", got, err)
	}
	b, _ := os.ReadFile(filepath.Join(home, "Library", "LaunchAgents", "com.gen-harness.update-request.plist"))
	mustContain(t, string(b), "<key>QueueDirectories</key>")
	mustContain(t, string(b), testRP.RequestDir)
	mustContain(t, string(b), "<string>handle-requests</string>")
}

func TestEnsureRequestWatcher_WindowsUnsupported(t *testing.T) {
	_, err := EnsureRequestWatcher(context.Background(), Deps{Runner: newFakeRunner(), GenhPath: "g", HomeDir: t.TempDir(), GOOS: "windows"}, testRP)
	if err == nil {
		t.Fatal("Windows chưa hỗ trợ — phải trả lỗi để Console hiện lệnh tay")
	}
}

// v0.1.40: watcher nhận cả yêu cầu bản sao ngoài máy (run/request/offsite.json).
func TestRequestWatcher_OffsiteFile(t *testing.T) {
	rp := testRP
	rp.OffsiteFile = "/home/u/.gen-harness/run/request/offsite.json"
	path := SystemdRequestPathUnit(rp.files()...)
	mustContain(t, path, "PathExists="+rp.RequestFile)
	mustContain(t, path, "PathExists="+rp.RestoreFile)
	mustContain(t, path, "PathExists="+rp.OffsiteFile)
	line := CrontabRequestLine("/g/genh", "/g/log", rp)
	mustContain(t, line, "* * * * * { [ -f "+rp.RequestFile+" ] || [ -f "+rp.RestoreFile+" ] || [ -f "+rp.OffsiteFile+" ]; } && ")
	mustContain(t, line, "handle-requests")

	home := t.TempDir()
	deps := Deps{Runner: newFakeRunner(), GenhPath: "/g/genh", LogFile: "/g/log", HomeDir: home, GOOS: "linux",
		LookPath: func(string) (string, error) { return "/usr/bin/systemctl", nil }}
	if got, err := EnsureRequestWatcher(context.Background(), deps, rp); err != nil || got != UpdaterSystemd {
		t.Fatalf("EnsureRequestWatcher = %q, %v", got, err)
	}
	b, _ := os.ReadFile(filepath.Join(home, ".config", "systemd", "user", RequestTaskName+".path"))
	mustContain(t, string(b), "PathExists="+rp.OffsiteFile)
}

func TestSystemdRequestServiceUnit_KillModeMixed(t *testing.T) {
	unit := SystemdRequestServiceUnit("/g/genh", "/g/log", RequestPaths{InstallDir: "/r"})
	mustContain(t, unit, "KillMode=mixed")
	mustContain(t, unit, "TimeoutStopSec=900")
}

// v0.1.44: watcher nhận cả yêu cầu gói chẩn đoán (doctor.json) và "Gửi thử" (watchdog.json).
func TestRequestWatcher_DoctorVaWatchdogFile(t *testing.T) {
	rp := testRP
	rp.OffsiteFile = "/home/u/.gen-harness/run/request/offsite.json"
	rp.DoctorFile = "/home/u/.gen-harness/run/request/doctor.json"
	rp.WatchdogFile = "/home/u/.gen-harness/run/request/watchdog.json"
	path := SystemdRequestPathUnit(rp.files()...)
	mustContain(t, path, "PathExists="+rp.DoctorFile)
	mustContain(t, path, "PathExists="+rp.WatchdogFile)
	line := CrontabRequestLine("/g/genh", "/g/log", rp)
	mustContain(t, line, "[ -f "+rp.OffsiteFile+" ] || [ -f "+rp.DoctorFile+" ] || [ -f "+rp.WatchdogFile+" ]; } && ")
	// launchd QueueDirectories theo dõi cả thư mục request/ ⇒ đã bao hai tệp mới.
	mustContain(t, LaunchdRequestPlist("/g/genh", "/g/log", rp), "<string>"+rp.RequestDir+"</string>")

	home := t.TempDir()
	deps := Deps{Runner: newFakeRunner(), GenhPath: "/g/genh", LogFile: "/g/log", HomeDir: home, GOOS: "linux",
		LookPath: func(string) (string, error) { return "/usr/bin/systemctl", nil }}
	if got, err := EnsureRequestWatcher(context.Background(), deps, rp); err != nil || got != UpdaterSystemd {
		t.Fatalf("EnsureRequestWatcher = %q, %v", got, err)
	}
	b, _ := os.ReadFile(filepath.Join(home, ".config", "systemd", "user", RequestTaskName+".path"))
	mustContain(t, string(b), "PathExists="+rp.DoctorFile)
	mustContain(t, string(b), "PathExists="+rp.WatchdogFile)
}

// v0.1.53: .path/.service từng start-limit-hit (tệp yêu cầu không xoá được) thì `genh update`
// phải reset-failed TRƯỚC khi enable --now để chữa.
func TestEnsureRequestWatcher_ResetFailedTruocEnable(t *testing.T) {
	rr := &recRunner{}
	deps := Deps{Runner: rr, GenhPath: "/g/genh", LogFile: "/g/log", HomeDir: t.TempDir(), GOOS: "linux",
		LookPath: func(string) (string, error) { return "/usr/bin/systemctl", nil }}
	if got, err := EnsureRequestWatcher(context.Background(), deps, testRP); err != nil || got != UpdaterSystemd {
		t.Fatalf("EnsureRequestWatcher = %q, %v", got, err)
	}
	reset, enable := -1, -1
	for i, c := range rr.calls {
		switch {
		case strings.Contains(c, "reset-failed"):
			reset = i
			if !strings.Contains(c, RequestTaskName+".path") || !strings.Contains(c, RequestTaskName+".service") {
				t.Errorf("reset-failed phải gồm cả .path và .service: %q", c)
			}
		case strings.Contains(c, "enable --now "+RequestTaskName+".path"):
			enable = i
		}
	}
	if reset < 0 || enable < 0 || reset > enable {
		t.Fatalf("reset-failed (%d) phải chạy trước enable --now (%d): %v", reset, enable, rr.calls)
	}

	// reset-failed lỗi (chưa failed) không làm hỏng việc cài.
	rr = &recRunner{}
	rr.on("reset-failed", "", errExit1)
	deps.Runner = rr
	if got, err := EnsureRequestWatcher(context.Background(), deps, testRP); err != nil || got != UpdaterSystemd {
		t.Fatalf("reset-failed lỗi phải bị bỏ qua: %q, %v", got, err)
	}
}
