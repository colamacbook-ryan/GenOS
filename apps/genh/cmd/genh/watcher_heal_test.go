package main

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/autoupdate"
)

// ─── v0.1.54: người gác yêu cầu (.path) tự chữa ─────────────────────────────

// watcherSim giả trạng thái .path + timer dự phòng cho test cmd (đi cùng cmdRunner.dyn).
type watcherSim struct {
	mu        sync.Mutex
	pathState string
	inotifyOK bool
	timerOn   bool
	// svcRunning: service mà .path kích đang chạy ⇒ systemd thật cho .path "active (running)" (không gác).
	svcRunning bool
}

func (w *watcherSim) dyn(line string, _ []string) (string, error, bool) {
	w.mu.Lock()
	defer w.mu.Unlock()
	switch {
	case strings.Contains(line, "is-active gen-harness-update-request.path"):
		if w.pathState == "active" {
			return "active\n", nil, true
		}
		return w.pathState + "\n", errors.New("exit status 3"), true
	case strings.Contains(line, "is-active gen-harness-update-request.timer"):
		if w.timerOn {
			return "active\n", nil, true
		}
		return "inactive\n", errors.New("exit status 3"), true
	case strings.Contains(line, "show gen-harness-update-request.path -p SubState"):
		switch {
		case w.pathState == "active" && w.svcRunning:
			return "SubState=running\n", nil, true
		case w.pathState == "active":
			return "SubState=waiting\n", nil, true
		}
		return "SubState=" + w.pathState + "\n", nil, true
	case strings.Contains(line, "show gen-harness-update-request.path"):
		if w.pathState == "failed" {
			return "Result=resources\n", nil, true
		}
		return "Result=success\n", nil, true
	case strings.Contains(line, "reset-failed"):
		if w.pathState == "failed" {
			w.pathState = "inactive"
		}
		return "", nil, true
	case strings.Contains(line, "restart gen-harness-update-request.path"):
		if w.svcRunning || w.inotifyOK {
			w.pathState = "active"
			return "", nil, true
		}
		w.pathState = "failed"
		return "", errors.New("exit status 1"), true
	case strings.Contains(line, "enable --now gen-harness-update-request.timer"):
		w.timerOn = true
		return "", nil, true
	case strings.Contains(line, "disable --now gen-harness-update-request.timer"):
		w.timerOn = false
		return "", nil, true
	}
	return "", nil, false
}

func (w *watcherSim) fixInotify() {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.inotifyOK = true
}

// useWatcherHost tiêm host giả có unit .path trên đĩa, hạn mức inotify = 128.
func useWatcherHost(t *testing.T, sim *watcherSim) (home string, rr *cmdRunner) {
	t.Helper()
	rr = healthyRules(&cmdRunner{})
	rr.dyn = sim.dyn
	home = t.TempDir()
	unitDir := filepath.Join(home, ".config", "systemd", "user")
	if err := os.MkdirAll(unitDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(unitDir, autoupdate.RequestTaskName+".path"), []byte("[Path]\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	old := hostInfoEnvFn
	hostInfoEnvFn = func() hostInfoEnv {
		he := hermeticHost(home, rr)
		he.Base.ReadFile = func(string) ([]byte, error) { return []byte("128\n"), nil }
		he.Base.InotifyHeadroom = func(int) bool { sim.mu.Lock(); defer sim.mu.Unlock(); return sim.inotifyOK }
		return he
	}
	t.Cleanup(func() { hostInfoEnvFn = old })
	return home, rr
}

// Hết hạn mức inotify: status chữa → dự phòng + nói thật; handle-requests im lặng; sửa sysctl rồi
// enable → .path sống, gỡ dự phòng; trạng thái JSON đi cùng.
func TestAutoUpdateCmd_NguoiGacInotify_DuPhongRoiPhucHoi(t *testing.T) {
	sim := &watcherSim{pathState: "failed"}
	home, rr := useWatcherHost(t, sim)
	dir := installDirAlive(t)
	timerFile := filepath.Join(home, ".config", "systemd", "user", autoupdate.RequestFallbackTimer)

	var code int
	out, _ := captureStd(t, func() { code = runAutoUpdate([]string{"status", "--install-dir", dir}) })
	want := "Người gác yêu cầu (.path) lỗi: hết hạn mức inotify — đang dùng dự phòng quét mỗi phút. " +
		"Sửa gốc: `sudo sysctl -w fs.inotify.max_user_instances=1024` rồi `genh auto-update enable`."
	if code != 0 || !strings.Contains(out, want) {
		t.Fatalf("status phải nói thật về dự phòng: code=%d out=%q", code, out)
	}
	if !sim.timerOn || rr.ran("reset-failed gen-harness-update-request.path") || rr.ran("restart gen-harness-update-request.path") {
		t.Fatalf("hết instance ⇒ bật timer dự phòng, KHÔNG reset-failed/restart (chỉ sống giả): %v", rr.calls)
	}
	ns := readNightly(t, dir)
	if ns.Watcher.State != "fallback" || ns.Watcher.Reason != "inotify" || !strings.Contains(ns.Watcher.Hint, "inotify") {
		t.Errorf("nightly-status.watcher = %+v", ns.Watcher)
	}

	// status lần 2: vẫn nói dự phòng, không cài lại timer.
	n := rr.count("enable --now gen-harness-update-request.timer")
	out, _ = captureStd(t, func() { code = runAutoUpdate([]string{"status", "--install-dir", dir}) })
	if !strings.Contains(out, want) || rr.count("enable --now gen-harness-update-request.timer") != n {
		t.Errorf("status lần 2: out=%q enable=%d→%d", out, n, rr.count("enable --now gen-harness-update-request.timer"))
	}

	// handle-requests (dự phòng gọi mỗi phút) chạy TRONG service mà .path kích: không in gì và KHÔNG
	// động vào .path/timer dự phòng (ở đó systemd báo .path "running" giả — E2E v0.1.54).
	rr.reset()
	sim.mu.Lock()
	sim.svcRunning = true
	sim.mu.Unlock()
	out, errOut := captureStd(t, func() { code = runHandleRequests([]string{"--quiet", "--install-dir", dir}) })
	if code != 0 || out != "" || errOut != "" {
		t.Errorf("handle-requests phải im lặng: code=%d out=%q err=%q", code, out, errOut)
	}
	for _, c := range rr.calls {
		if strings.Contains(c, "reset-failed") || strings.Contains(c, "restart") || strings.Contains(c, "enable") || strings.Contains(c, "disable") {
			t.Errorf("handle-requests không được chữa/gỡ người gác: %v", rr.calls)
			break
		}
	}
	if !sim.timerOn {
		t.Error("handle-requests không được gỡ timer dự phòng")
	}
	sim.mu.Lock()
	sim.svcRunning = false
	sim.mu.Unlock()

	// Sếp sửa sysctl → `genh auto-update enable` ⇒ .path sống, dự phòng gỡ.
	sim.fixInotify()
	out, _ = captureStd(t, func() { code = runAutoUpdate([]string{"enable", "--install-dir", dir}) })
	if code != 0 || !strings.Contains(out, "Người gác yêu cầu (.path) đã sống lại") {
		t.Fatalf("enable: code=%d out=%q", code, out)
	}
	if sim.pathState != "active" || sim.timerOn {
		t.Errorf("sau enable .path phải active, dự phòng tắt: path=%s timer=%v", sim.pathState, sim.timerOn)
	}
	if _, err := os.Stat(timerFile); !os.IsNotExist(err) {
		t.Errorf("tệp timer dự phòng phải bị gỡ: %v", err)
	}
	if ns := readNightly(t, dir); ns.Watcher.State != "ok" || ns.Watcher.Reason != "" || ns.Watcher.Hint != "" {
		t.Errorf("nightly-status.watcher sau enable = %+v", ns.Watcher)
	}
}

// Người gác khoẻ: status không nhắc gì về người gác, handle-requests không ghi gì cả.
func TestAutoUpdateCmd_NguoiGacKhoe_ImLang(t *testing.T) {
	sim := &watcherSim{pathState: "active"}
	_, rr := useWatcherHost(t, sim)
	dir := installDirAlive(t)
	var code int
	out, _ := captureStd(t, func() { code = runAutoUpdate([]string{"status", "--install-dir", dir}) })
	if code != 0 || strings.Contains(out, "Người gác") {
		t.Errorf("khoẻ ⇒ không nhắc người gác: code=%d out=%q", code, out)
	}
	rr.reset()
	out, errOut := captureStd(t, func() { code = runHandleRequests([]string{"--quiet", "--install-dir", dir}) })
	if code != 0 || out != "" || errOut != "" {
		t.Errorf("handle-requests khoẻ phải im lặng: %q %q", out, errOut)
	}
	for _, c := range rr.calls {
		if strings.Contains(c, "reset-failed") || strings.Contains(c, "restart") || strings.Contains(c, "enable") {
			t.Errorf("khoẻ không được ghi gì: %v", rr.calls)
			break
		}
	}
}
