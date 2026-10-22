package autoupdate

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// sysdFake giả systemd --user cho người gác yêu cầu (.path) và timer dự phòng, CÓ TRẠNG THÁI:
//   - pathState: trạng thái is-active của .path (active|failed|inactive);
//   - inotifyOK: restart .path có thành công không (false = hết hạn mức inotify ⇒ failed, Result=resources);
//   - timerOn: timer dự phòng đang bật (enable --now ⇒ true, disable --now ⇒ false).
type sysdFake struct {
	pathState string
	inotifyOK bool
	result    string // giá trị `show -p Result` khi .path failed (mặc định "resources")
	timerOn   bool
	enableErr error // lỗi khi enable --now timer dự phòng
	// svcRunning: service mà .path kích (genh handle-requests) đang chạy. systemd THẬT khi đó cho
	// `restart .path` "thành công" (active/running) mà không dựng watch inotify nào — dù hết instance.
	svcRunning bool
	// headroomSet/headroom: ghi đè phép thử inotify (mặc định = inotifyOK).
	headroomSet bool
	headroom    bool
	calls       []string
}

// hasHeadroom là phép thử inotify giả (Deps.InotifyHeadroom).
func (s *sysdFake) hasHeadroom(int) bool {
	if s.headroomSet {
		return s.headroom
	}
	return s.inotifyOK
}

func newSysd(pathState string, inotifyOK bool) *sysdFake {
	return &sysdFake{pathState: pathState, inotifyOK: inotifyOK, result: "resources"}
}

func (s *sysdFake) Output(_ context.Context, name string, args []string) ([]byte, error) {
	line := name + " " + strings.Join(args, " ")
	s.calls = append(s.calls, line)
	switch {
	case strings.Contains(line, "is-active "+RequestTaskName+".path"):
		if s.pathState == "active" {
			return []byte("active\n"), nil
		}
		return []byte(s.pathState + "\n"), errors.New("exit status 3")
	case strings.Contains(line, "is-active "+RequestFallbackTimer):
		if s.timerOn {
			return []byte("active\n"), nil
		}
		return []byte("inactive\n"), errors.New("exit status 3")
	case strings.Contains(line, "show "+RequestTaskName+".path -p SubState"):
		switch {
		case s.pathState == "active" && s.svcRunning:
			return []byte("SubState=running\n"), nil
		case s.pathState == "active":
			return []byte("SubState=waiting\n"), nil
		}
		return []byte("SubState=" + s.pathState + "\n"), nil
	case strings.Contains(line, "show "+RequestTaskName+".path"):
		if s.pathState == "failed" {
			return []byte("Result=" + s.result + "\n"), nil
		}
		return []byte("Result=success\n"), nil
	case strings.Contains(line, "reset-failed"):
		if s.pathState == "failed" {
			s.pathState = "inactive"
		}
	case strings.Contains(line, "restart "+RequestTaskName+".path"):
		if s.svcRunning || s.inotifyOK {
			s.pathState = "active"
			return nil, nil
		}
		s.pathState = "failed"
		return []byte("Job failed"), errors.New("exit status 1")
	case strings.Contains(line, "enable --now "+RequestFallbackTimer):
		if s.enableErr != nil {
			return nil, s.enableErr
		}
		s.timerOn = true
	case strings.Contains(line, "disable --now "+RequestFallbackTimer):
		s.timerOn = false
	case strings.Contains(line, "enable --now "+RequestTaskName+".path"):
		if s.svcRunning || s.inotifyOK {
			s.pathState = "active"
			return nil, nil
		}
		s.pathState = "failed"
		return []byte("Job failed"), errors.New("exit status 1")
	}
	return nil, nil
}

func (s *sysdFake) count(sub string) int {
	n := 0
	for _, c := range s.calls {
		if strings.Contains(c, sub) {
			n++
		}
	}
	return n
}

// writes: số lệnh GHI (đổi trạng thái) đã chạy — tất cả trừ is-active/show.
func (s *sysdFake) writes() int {
	n := 0
	for _, c := range s.calls {
		if !strings.Contains(c, "is-active") && !strings.Contains(c, " show ") {
			n++
		}
	}
	return n
}

func healDeps(t *testing.T, rr Runner, inotifyLimit string) (Deps, string) {
	t.Helper()
	home := t.TempDir()
	writeUnit(t, home, RequestTaskName+".path", SystemdRequestPathUnit(testRP.RequestFile))
	writeUnit(t, home, RequestTaskName+".service", SystemdRequestServiceUnit("/g/genh", "/g/log", testRP))
	deps := Deps{Runner: rr, HomeDir: home, GOOS: "linux", GenhPath: "/g/genh",
		LookPath: func(string) (string, error) { return "/usr/bin/systemctl", nil },
		InotifyHeadroom: func(n int) bool {
			if f, ok := rr.(*sysdFake); ok {
				return f.hasHeadroom(n)
			}
			return true
		},
		ReadFile: func(p string) ([]byte, error) {
			if p != InotifyInstancesProc {
				return nil, os.ErrNotExist
			}
			if inotifyLimit == "" {
				return nil, os.ErrNotExist
			}
			return []byte(inotifyLimit + "\n"), nil
		}}
	return deps, systemdUserDir(home)
}

// Khoẻ ⇒ không làm gì: chỉ MỘT lệnh đọc, không ghi tệp nào.
func TestHealRequestWatcher_Khoe_KhongLamGi(t *testing.T) {
	rr := newSysd("active", true)
	deps, dir := healDeps(t, rr, "128")
	h := HealRequestWatcher(context.Background(), deps)
	if !h.OK() || h.Changed || h.Healed || h.State != WatcherHealthOK {
		t.Fatalf("khoẻ phải ok, không đổi gì: %+v", h)
	}
	if rr.writes() != 0 || len(rr.calls) != 1 {
		t.Errorf("khoẻ chỉ được hỏi is-active một lần, không ghi: %v", rr.calls)
	}
	if fileExists(filepath.Join(dir, RequestFallbackTimer)) {
		t.Error("khoẻ không được có timer dự phòng")
	}
	if h.Line() != "" || h.Hint() != "" {
		t.Errorf("khoẻ không có dòng/gợi ý: %q %q", h.Line(), h.Hint())
	}
}

// Không phải Linux, hoặc chưa cài unit .path ⇒ không áp dụng, không gọi lệnh nào.
func TestHealRequestWatcher_KhongApDung(t *testing.T) {
	rr := newSysd("failed", false)
	deps, _ := healDeps(t, rr, "128")
	deps.GOOS = "darwin"
	if h := HealRequestWatcher(context.Background(), deps); !h.OK() || len(rr.calls) != 0 {
		t.Errorf("darwin: %+v %v", h, rr.calls)
	}
	deps.GOOS = "linux"
	deps.HomeDir = t.TempDir() // chưa có unit
	if h := HealRequestWatcher(context.Background(), deps); !h.OK() || len(rr.calls) != 0 {
		t.Errorf("chưa cài unit: %+v %v", h, rr.calls)
	}
	if h := RequestWatcherHealth(context.Background(), deps); !h.OK() || len(rr.calls) != 0 {
		t.Errorf("chỉ đọc, chưa cài unit: %+v %v", h, rr.calls)
	}
}

// failed → reset-failed + restart thành công ⇒ chữa xong, KHÔNG dựng dự phòng.
func TestHealRequestWatcher_FailedRoiRestartOk_KhongDuPhong(t *testing.T) {
	rr := newSysd("failed", true)
	deps, dir := healDeps(t, rr, "128")
	h := HealRequestWatcher(context.Background(), deps)
	if h.State != WatcherHealthOK || !h.Healed || !h.Changed {
		t.Fatalf("phải chữa xong: %+v", h)
	}
	if rr.count("reset-failed "+RequestTaskName+".path") != 1 || rr.count("restart "+RequestTaskName+".path") != 1 {
		t.Errorf("phải reset-failed rồi restart đúng một lần: %v", rr.calls)
	}
	if rr.count(RequestFallbackTimer) != 0 || fileExists(filepath.Join(dir, RequestFallbackTimer)) {
		t.Errorf("chữa được thì không dựng timer dự phòng: %v", rr.calls)
	}
	// reset-failed phải đi TRƯỚC restart.
	reset, restart := -1, -1
	for i, c := range rr.calls {
		if strings.Contains(c, "reset-failed") {
			reset = i
		}
		if strings.Contains(c, "restart") {
			restart = i
		}
	}
	if reset < 0 || restart < 0 || reset > restart {
		t.Errorf("reset-failed (%d) phải trước restart (%d)", reset, restart)
	}
}

// failed bền (hết hạn mức inotify) ⇒ cài + bật timer dự phòng, ghi lý do inotify, gợi ý có "inotify".
func TestHealRequestWatcher_FailedBen_CaiTimerDuPhong(t *testing.T) {
	rr := newSysd("failed", false)
	deps, dir := healDeps(t, rr, "128")
	h := HealRequestWatcher(context.Background(), deps)
	if h.State != WatcherHealthFallback || h.Reason != WatcherReasonInotify || !h.Changed || h.Healed {
		t.Fatalf("phải dự phòng vì inotify: %+v", h)
	}
	if !strings.Contains(h.Hint(), "inotify") || !strings.Contains(h.Hint(), "sysctl -w fs.inotify.max_user_instances=1024") ||
		!strings.Contains(h.Hint(), "genh auto-update enable") {
		t.Errorf("hint thiếu inotify/sysctl/enable: %q", h.Hint())
	}
	timer, err := os.ReadFile(filepath.Join(dir, RequestFallbackTimer))
	if err != nil {
		t.Fatalf("thiếu timer dự phòng: %v", err)
	}
	for _, want := range []string{"OnBootSec=1min", "OnUnitActiveSec=1min", "Unit=" + RequestTaskName + ".service",
		"WantedBy=timers.target", fallbackReasonMarker + "inotify"} {
		mustContain(t, string(timer), want)
	}
	if strings.Contains(string(timer), "Persistent") {
		t.Error("dự phòng không cần Persistent")
	}
	// drop-in gỡ giới hạn StartLimit để service được kích mỗi phút không bị từ chối sau 5 lần.
	drop, err := os.ReadFile(filepath.Join(dir, RequestTaskName+".service.d", "fallback.conf"))
	if err != nil {
		t.Fatalf("thiếu drop-in StartLimit: %v", err)
	}
	mustContain(t, string(drop), "StartLimitIntervalSec=0")
	if !rr.timerOn || rr.count("daemon-reload") == 0 || rr.count("enable --now "+RequestFallbackTimer) != 1 {
		t.Errorf("phải daemon-reload rồi enable --now timer dự phòng: %v", rr.calls)
	}
	line := h.Line()
	mustContain(t, line, "Người gác yêu cầu (.path) lỗi: hết hạn mức inotify — đang dùng dự phòng quét mỗi phút.")
	mustContain(t, line, "Sửa gốc: `sudo sysctl -w fs.inotify.max_user_instances=1024` rồi `genh auto-update enable`.")

	// Gọi lại khi dự phòng đã chạy: im lặng (Changed=false), không cài lại.
	before := rr.count("enable --now " + RequestFallbackTimer)
	h2 := HealRequestWatcher(context.Background(), deps)
	if h2.State != WatcherHealthFallback || h2.Changed || rr.count("enable --now "+RequestFallbackTimer) != before {
		t.Errorf("lần 2 phải im lặng và idempotent: %+v", h2)
	}

	// Bản CHỈ ĐỌC thấy đúng dự phòng + lý do và không ghi gì.
	rr.calls = nil
	ro := RequestWatcherHealth(context.Background(), deps)
	if ro.State != WatcherHealthFallback || ro.Reason != WatcherReasonInotify || rr.writes() != 0 {
		t.Errorf("chỉ đọc: %+v writes=%d %v", ro, rr.writes(), rr.calls)
	}
}

// Chạy lại khi đã sửa sysctl ⇒ .path sống, GỠ timer dự phòng + drop-in.
func TestHealRequestWatcher_DaSuaSysctl_GoDuPhong(t *testing.T) {
	rr := newSysd("failed", false)
	deps, dir := healDeps(t, rr, "128")
	if h := HealRequestWatcher(context.Background(), deps); h.State != WatcherHealthFallback {
		t.Fatalf("tiền đề: dự phòng: %+v", h)
	}
	rr.inotifyOK = true // Sếp đã chạy sudo sysctl -w …=1024
	h := HealRequestWatcher(context.Background(), deps)
	if h.State != WatcherHealthOK || !h.Healed || !h.Changed {
		t.Fatalf("phải chữa xong và gỡ dự phòng: %+v", h)
	}
	if rr.timerOn || fileExists(filepath.Join(dir, RequestFallbackTimer)) ||
		fileExists(filepath.Join(dir, RequestTaskName+".service.d", "fallback.conf")) {
		t.Errorf("timer dự phòng + drop-in phải bị gỡ (timerOn=%v)", rr.timerOn)
	}
	if rr.count("disable --now "+RequestFallbackTimer) != 1 {
		t.Errorf("phải disable --now timer dự phòng: %v", rr.calls)
	}
	if ro := RequestWatcherHealth(context.Background(), deps); !ro.OK() {
		t.Errorf("sau khi gỡ phải ok: %+v", ro)
	}

	// .path tự sống lại (Sếp restart tay) mà dự phòng còn sót ⇒ lần chạy kế gỡ.
	rr2 := newSysd("failed", false)
	deps2, dir2 := healDeps(t, rr2, "128")
	HealRequestWatcher(context.Background(), deps2)
	rr2.pathState = "active"
	if h := HealRequestWatcher(context.Background(), deps2); h.State != WatcherHealthOK || !h.Changed || fileExists(filepath.Join(dir2, RequestFallbackTimer)) {
		t.Errorf(".path đã active ⇒ gỡ dự phòng: %+v", h)
	}
}

// Chẩn đoán lý do: không đọc được hạn mức ⇒ "lỗi tài nguyên" chung; Result khác ⇒ other.
func TestHealRequestWatcher_ChanDoanLyDo(t *testing.T) {
	rr := newSysd("failed", false)
	deps, _ := healDeps(t, rr, "") // không đọc được /proc
	h := HealRequestWatcher(context.Background(), deps)
	if h.Reason != WatcherReasonResources {
		t.Fatalf("không đọc được hạn mức ⇒ resources: %+v", h)
	}
	mustContain(t, h.Line(), "lỗi tài nguyên")
	mustContain(t, h.Hint(), "inotify")

	rr = newSysd("failed", false)
	deps, _ = healDeps(t, rr, "65536") // hạn mức đã đủ lớn ⇒ không đổ lỗi cho instances
	if h := HealRequestWatcher(context.Background(), deps); h.Reason != WatcherReasonResources {
		t.Errorf("hạn mức đủ lớn ⇒ resources, không phải inotify: %+v", h)
	}

	rr = newSysd("failed", false)
	rr.result = "start-limit-hit"
	rr.headroomSet, rr.headroom = true, true // còn instance; restart hỏng vì lý do khác
	deps, _ = healDeps(t, rr, "128")
	h = HealRequestWatcher(context.Background(), deps)
	if h.State != WatcherHealthFallback || h.Reason != WatcherReasonOther {
		t.Fatalf("Result khác ⇒ other (vẫn có dự phòng): %+v", h)
	}
	if strings.Contains(h.Line(), "inotify") {
		t.Errorf("lý do khác không được nhắc inotify: %q", h.Line())
	}
}

// Không bật được timer dự phòng ⇒ failed (Console nói thẳng nút chưa có người nhận).
func TestHealRequestWatcher_DuPhongCungLoi_Failed(t *testing.T) {
	rr := newSysd("failed", false)
	rr.enableErr = errors.New("exit status 1")
	deps, _ := healDeps(t, rr, "128")
	h := HealRequestWatcher(context.Background(), deps)
	if h.State != WatcherHealthFailed || h.Reason != WatcherReasonInotify || h.Changed {
		t.Fatalf("phải failed: %+v", h)
	}
	mustContain(t, h.Line(), "chưa có người nhận")
	// Chỉ đọc: .path failed, chưa có dự phòng đang chạy ⇒ failed + lý do chẩn đoán.
	if ro := RequestWatcherHealth(context.Background(), deps); ro.State != WatcherHealthFailed || ro.Reason != WatcherReasonInotify {
		t.Errorf("chỉ đọc: %+v", ro)
	}
}

// EnsureRequestWatcher: enable --now .path lỗi (inotify) ⇒ tự chữa, dự phòng nhận thay ⇒ vẫn trả systemd.
func TestEnsureRequestWatcher_PathLoiInotify_DuPhong(t *testing.T) {
	rr := newSysd("inactive", false)
	home := t.TempDir()
	deps := Deps{Runner: rr, GenhPath: "/g/genh", LogFile: "/g/log", HomeDir: home, GOOS: "linux",
		LookPath:        func(string) (string, error) { return "/usr/bin/systemctl", nil },
		InotifyHeadroom: func(n int) bool { return rr.hasHeadroom(n) },
		ReadFile:        func(string) ([]byte, error) { return []byte("128\n"), nil }}
	got, err := EnsureRequestWatcher(context.Background(), deps, testRP)
	if err != nil || got != UpdaterSystemd {
		t.Fatalf("dự phòng nhận thay ⇒ vẫn systemd: %q, %v", got, err)
	}
	if !rr.timerOn || !fileExists(filepath.Join(systemdUserDir(home), RequestFallbackTimer)) {
		t.Errorf("phải bật timer dự phòng: %v", rr.calls)
	}
	// Dự phòng cũng hỏng ⇒ trả lỗi gốc để caller hiện lệnh tay.
	rr = newSysd("inactive", false)
	rr.enableErr = errors.New("exit status 1")
	deps.Runner, deps.HomeDir = rr, t.TempDir()
	if got, err := EnsureRequestWatcher(context.Background(), deps, testRP); err == nil {
		t.Errorf("cả dự phòng cũng hỏng phải trả lỗi, được %q", got)
	}
	// Sửa gốc rồi cài lại ⇒ .path sống, gỡ dự phòng cũ.
	rr = newSysd("inactive", false)
	deps.Runner, deps.HomeDir = rr, t.TempDir()
	if _, err := EnsureRequestWatcher(context.Background(), deps, testRP); err != nil {
		t.Fatal(err)
	}
	rr.inotifyOK = true
	if _, err := EnsureRequestWatcher(context.Background(), deps, testRP); err != nil {
		t.Fatal(err)
	}
	if rr.timerOn || fileExists(filepath.Join(systemdUserDir(deps.HomeDir), RequestFallbackTimer)) {
		t.Errorf("cài lại sau khi sửa gốc phải gỡ dự phòng: %v", rr.calls)
	}
}

// Gỡ watcher (genh auto-update disable / uninstall) gỡ luôn timer dự phòng.
func TestDisableRequestWatcher_GoCaDuPhong(t *testing.T) {
	rr := newSysd("failed", false)
	deps, dir := healDeps(t, rr, "128")
	HealRequestWatcher(context.Background(), deps)
	if !fileExists(filepath.Join(dir, RequestFallbackTimer)) {
		t.Fatal("tiền đề: có dự phòng")
	}
	DisableRequestWatcher(context.Background(), deps)
	if rr.timerOn || fileExists(filepath.Join(dir, RequestFallbackTimer)) || fileExists(filepath.Join(dir, RequestTaskName+".path")) {
		t.Errorf("phải gỡ .path + timer dự phòng: %v", rr.calls)
	}
}

func TestRequestFallbackTimerUnit_LyDoLa(t *testing.T) {
	mustContain(t, RequestFallbackTimerUnit("bậy; rm -rf /"), fallbackReasonMarker+WatcherReasonOther)
	if got := fallbackReasonFromFile(filepath.Join(t.TempDir(), "khong-co")); got != WatcherReasonOther {
		t.Errorf("tệp thiếu ⇒ other, được %q", got)
	}
}

// Hết instance (ca máy Sếp): KHÔNG reset-failed/restart (chỉ "sống giả"), Result=resources còn nguyên
// để chẩn đoán đúng lý do.
func TestHealRequestWatcher_HetInstance_KhongRestart(t *testing.T) {
	rr := newSysd("failed", false)
	deps, _ := healDeps(t, rr, "128")
	h := HealRequestWatcher(context.Background(), deps)
	if h.State != WatcherHealthFallback || h.Reason != WatcherReasonInotify {
		t.Fatalf("phải dự phòng vì inotify: %+v", h)
	}
	if rr.count("reset-failed") != 0 || rr.count("restart "+RequestTaskName+".path") != 0 {
		t.Errorf("hết instance thì không reset-failed/restart: %v", rr.calls)
	}
	if rr.pathState != "failed" {
		t.Errorf(".path phải còn failed, được %q", rr.pathState)
	}
}

// Hồi quy E2E v0.1.54: timer dự phòng kích service (genh handle-requests) và NGAY TRONG service đó
// heal chạy lại. systemd thật cho `restart .path` thành công (running, không watch) ⇒ bản cũ báo
// "đã sống lại" và GỠ dự phòng; service xong .path lại failed (resources) và không còn ai nhận yêu cầu.
func TestHealRequestWatcher_TrongService_KhongSongGia_GiuDuPhong(t *testing.T) {
	rr := newSysd("failed", false)
	deps, dir := healDeps(t, rr, "128")
	if h := HealRequestWatcher(context.Background(), deps); h.State != WatcherHealthFallback || !h.Changed {
		t.Fatalf("tiền đề: dự phòng vừa bật: %+v", h)
	}
	restarts := rr.count("restart " + RequestTaskName + ".path")

	rr.svcRunning = true // timer dự phòng vừa kích service; heal chạy bên trong nó
	h := HealRequestWatcher(context.Background(), deps)
	if h.State != WatcherHealthFallback || h.Changed || h.Healed || h.FallbackRemoved || h.Reason != WatcherReasonInotify {
		t.Fatalf("trong service mà vẫn hết instance ⇒ giữ dự phòng, im lặng: %+v", h)
	}
	if !rr.timerOn || !fileExists(filepath.Join(dir, RequestFallbackTimer)) ||
		fileExists(filepath.Join(dir, RequestTaskName+".service.d")) == false {
		t.Errorf("không được gỡ timer dự phòng/drop-in (timerOn=%v): %v", rr.timerOn, rr.calls)
	}
	if rr.count("restart "+RequestTaskName+".path") != restarts || rr.count("disable --now "+RequestFallbackTimer) != 0 {
		t.Errorf("không được restart .path hay disable timer khi hết instance: %v", rr.calls)
	}
	mustContain(t, timerFileText(t, dir), fallbackReasonMarker+"inotify") // lý do không bị ghi đè thành other

	// Sếp đã sysctl nhưng heal vẫn chạy trong service: .path "running" ⇒ chưa biết ⇒ GIỮ dự phòng.
	rr.inotifyOK = true
	h = HealRequestWatcher(context.Background(), deps)
	if h.State != WatcherHealthFallback || h.FallbackRemoved || !rr.timerOn || !fileExists(filepath.Join(dir, RequestFallbackTimer)) {
		t.Errorf(".path chỉ running (service đang chạy) ⇒ giữ dự phòng: %+v", h)
	}
	// Service xong, heal chạy ngoài service: .path waiting (gác thật) ⇒ gỡ dự phòng.
	rr.svcRunning = false
	h = HealRequestWatcher(context.Background(), deps)
	if h.State != WatcherHealthOK || !h.FallbackRemoved || rr.timerOn || fileExists(filepath.Join(dir, RequestFallbackTimer)) {
		t.Errorf(".path waiting ⇒ gỡ dự phòng: %+v", h)
	}
}

// .path "active" nhưng SubState=running (service đang chạy) ⇒ chưa biết có gác thật không ⇒ GIỮ dự phòng
// (kể cả khi còn instance); SubState=waiting (đang gác thật) ⇒ gỡ.
func TestHealRequestWatcher_ActiveRunning_ChuaGoDuPhong(t *testing.T) {
	rr := newSysd("failed", false)
	deps, dir := healDeps(t, rr, "128")
	HealRequestWatcher(context.Background(), deps)
	rr.pathState, rr.svcRunning = "active", true
	h := HealRequestWatcher(context.Background(), deps)
	if h.State != WatcherHealthFallback || h.FallbackRemoved || !fileExists(filepath.Join(dir, RequestFallbackTimer)) {
		t.Fatalf("running + hết instance ⇒ giữ dự phòng: %+v", h)
	}
	rr.inotifyOK = true // còn instance cũng không đổi: running vẫn là "chưa biết"
	if h := HealRequestWatcher(context.Background(), deps); h.State != WatcherHealthFallback || h.FallbackRemoved || rr.count("disable --now "+RequestFallbackTimer) != 0 {
		t.Fatalf("running + còn instance ⇒ vẫn giữ dự phòng: %+v", h)
	}
	rr.svcRunning = false // đang chờ thật
	h = HealRequestWatcher(context.Background(), deps)
	if h.State != WatcherHealthOK || !h.FallbackRemoved || fileExists(filepath.Join(dir, RequestFallbackTimer)) {
		t.Errorf("waiting ⇒ gỡ dự phòng: %+v", h)
	}
}

func timerFileText(t *testing.T, dir string) string {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(dir, RequestFallbackTimer))
	if err != nil {
		t.Fatalf("thiếu timer dự phòng: %v", err)
	}
	return string(b)
}

// Mỗi dòng Path*= là một inotify instance của .path.
func TestRequestPathSpecs(t *testing.T) {
	p := filepath.Join(t.TempDir(), "x.path")
	if err := os.WriteFile(p, []byte(SystemdRequestPathUnit(testRP.files()...)), 0o644); err != nil {
		t.Fatal(err)
	}
	if got, want := requestPathSpecs(p), len(testRP.files()); got != want || got < 2 {
		t.Errorf("số watch = %d, cần %d (≥ 2)", got, want)
	}
	if got := requestPathSpecs(filepath.Join(t.TempDir(), "khong-co")); got != 1 {
		t.Errorf("không đọc được ⇒ 1, được %d", got)
	}
}

// Phép thử inotify thật không làm rò instance (rò sẽ làm kết quả đổi khi gọi nhiều lần).
func TestProbeInotifyInstances_KhongRoRi(t *testing.T) {
	first := probeInotifyInstances(4)
	for i := 0; i < 400; i++ {
		if got := probeInotifyInstances(4); got != first {
			t.Fatalf("lần %d: kết quả đổi %v → %v — rò inotify instance", i, first, got)
		}
	}
}

// EnsureRequestWatcher chạy trong service yêu cầu (genh update do nút Console chạy): enable --now .path
// "thành công" giả (running) ⇒ KHÔNG được gỡ dự phòng; chạy ngoài service (waiting) ⇒ gỡ.
func TestEnsureRequestWatcher_TrongService_GiuDuPhong(t *testing.T) {
	rr := newSysd("inactive", false)
	home := t.TempDir()
	deps := Deps{Runner: rr, GenhPath: "/g/genh", LogFile: "/g/log", HomeDir: home, GOOS: "linux",
		LookPath:        func(string) (string, error) { return "/usr/bin/systemctl", nil },
		InotifyHeadroom: func(n int) bool { return rr.hasHeadroom(n) },
		ReadFile:        func(string) ([]byte, error) { return []byte("128\n"), nil }}
	if _, err := EnsureRequestWatcher(context.Background(), deps, testRP); err != nil || !rr.timerOn {
		t.Fatalf("tiền đề: dự phòng bật: %v %v", err, rr.calls)
	}
	rr.svcRunning = true
	if _, err := EnsureRequestWatcher(context.Background(), deps, testRP); err != nil {
		t.Fatal(err)
	}
	if !rr.timerOn || !fileExists(filepath.Join(systemdUserDir(home), RequestFallbackTimer)) {
		t.Errorf("cài/cập nhật chạy trong service (.path running) không được gỡ dự phòng: %v", rr.calls)
	}
	rr.svcRunning, rr.inotifyOK = false, true
	if _, err := EnsureRequestWatcher(context.Background(), deps, testRP); err != nil {
		t.Fatal(err)
	}
	if rr.timerOn || fileExists(filepath.Join(systemdUserDir(home), RequestFallbackTimer)) {
		t.Errorf("ngoài service, .path waiting ⇒ gỡ dự phòng: %v", rr.calls)
	}
}
