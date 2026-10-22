package autoupdate

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// ─── Người gác yêu cầu tự chữa (v0.1.54) ────────────────────────────────────
//
// Bài học máy thật của Sếp: unit `gen-harness-update-request.path` (systemd --user)
// rơi vào failed "Result: resources" vì hết hạn mức inotify
// (fs.inotify.max_user_instances mặc định 128, máy đang dùng 174); `reset-failed` +
// restart vẫn lỗi tới khi chạy `sudo sysctl -w fs.inotify.max_user_instances=1024`.
// Trong lúc đó nút "Cập nhật ngay" trên Console không có ai nhận.
//
// HealRequestWatcher: .path failed ⇒ `reset-failed` + `restart`; vẫn lỗi ⇒ cài + bật
// timer DỰ PHÒNG gen-harness-update-request.timer (mỗi phút kích cùng service) để yêu
// cầu tay vẫn được nhận, và ghi lý do. Chạy lại khi .path đã sống ⇒ gỡ dự phòng.
// RequestWatcherHealth là bản CHỈ ĐỌC (ghi vào run/nightly-status.json → api → Console).

// Trạng thái người gác (nightly-status.json › watcher.state).
const (
	WatcherHealthOK       = "ok"
	WatcherHealthFallback = "fallback"
	WatcherHealthFailed   = "failed"
)

// Mã lý do (nightly-status.json › watcher.reason).
const (
	WatcherReasonNone      = ""
	WatcherReasonInotify   = "inotify"
	WatcherReasonResources = "resources"
	WatcherReasonOther     = "other"
)

// RequestFallbackTimer là timer dự phòng quét yêu cầu mỗi phút.
const RequestFallbackTimer = RequestTaskName + ".timer"

// InotifyInstancesProc là hạn mức inotify theo người dùng — đọc để chẩn đoán.
const InotifyInstancesProc = "/proc/sys/fs/inotify/max_user_instances"

// InotifyFixCommand là lệnh sửa gốc Sếp chạy một lần trên máy chủ.
const InotifyFixCommand = "sudo sysctl -w fs.inotify.max_user_instances=1024"

// inotifyEnough: hạn mức từ mức này trở lên thì "resources" không còn là do thiếu instances.
const inotifyEnough = 1024

const (
	requestFallbackDropIn    = "fallback.conf"
	fallbackReasonMarker     = "# genh-fallback-reason: "
	fallbackStartLimitDropIn = "[Unit]\nStartLimitIntervalSec=0\n"
)

// WatcherHealth là tình trạng người gác yêu cầu (.path).
type WatcherHealth struct {
	// State: WatcherHealthOK | Fallback | Failed ("" = ok).
	State string
	// Reason: mã lý do khi State ≠ ok (WatcherReason*).
	Reason string
	// Healed: lần gọi này `.path` đã sống lại sau reset-failed + restart.
	Healed bool
	// FallbackRemoved: lần gọi này đã gỡ timer dự phòng (.path đã sống lại).
	FallbackRemoved bool
	// Changed: lần gọi này ĐỔI trạng thái (bật dự phòng / chữa xong / gỡ dự phòng) —
	// caller chỉ in một dòng khi Changed để log không bị lặp mỗi phút.
	Changed bool
}

// OK: người gác khoẻ (hoặc không áp dụng).
func (h WatcherHealth) OK() bool { return h.State == "" || h.State == WatcherHealthOK }

// Hint là câu gợi ý cách sửa gốc (tiếng Việt, chuỗi cố định — không ghép dữ liệu ngoài).
func (h WatcherHealth) Hint() string {
	switch h.Reason {
	case WatcherReasonInotify:
		return "Hết hạn mức inotify: chạy `" + InotifyFixCommand + "` rồi `genh auto-update enable`."
	case WatcherReasonResources:
		return "Lỗi tài nguyên hệ thống (thường là hạn mức inotify): thử `" + InotifyFixCommand + "` rồi `genh auto-update enable`."
	case WatcherReasonOther:
		return "Xem `systemctl --user status " + RequestTaskName + ".path`, sửa lỗi rồi chạy `genh auto-update enable`."
	}
	return ""
}

func (h WatcherHealth) reasonText() string {
	switch h.Reason {
	case WatcherReasonInotify:
		return "hết hạn mức inotify"
	case WatcherReasonResources:
		return "lỗi tài nguyên"
	}
	return "không khởi động được"
}

func (h WatcherHealth) fixText() string {
	switch h.Reason {
	case WatcherReasonInotify, WatcherReasonResources:
		return "Sửa gốc: `" + InotifyFixCommand + "` rồi `genh auto-update enable`."
	}
	return "Sửa gốc: xem `systemctl --user status " + RequestTaskName + ".path` rồi chạy `genh auto-update enable`."
}

// Line là một dòng cho `genh auto-update status` / log; rỗng khi khoẻ.
func (h WatcherHealth) Line() string {
	switch h.State {
	case WatcherHealthFallback:
		return "Người gác yêu cầu (.path) lỗi: " + h.reasonText() + " — đang dùng dự phòng quét mỗi phút. " + h.fixText()
	case WatcherHealthFailed:
		return "Người gác yêu cầu (.path) lỗi: " + h.reasonText() + " — nút Cập nhật ngay trên Console chưa có người nhận. " + h.fixText()
	}
	return ""
}

// validWatcherReason: chỉ nhận mã trong tập (tệp unit/nightly-status có thể bị sửa tay).
func validWatcherReason(r string) bool {
	switch r {
	case WatcherReasonInotify, WatcherReasonResources, WatcherReasonOther:
		return true
	}
	return false
}

// RequestFallbackTimerUnit là timer dự phòng: mỗi phút kích đúng service mà .path kích.
// Dòng chú thích lý do để RequestWatcherHealth (chỉ đọc) nói lại lý do mà không phải dò lại.
func RequestFallbackTimerUnit(reason string) string {
	if !validWatcherReason(reason) {
		reason = WatcherReasonOther
	}
	return fmt.Sprintf(`[Unit]
Description=Gen-Harness — du phong quet yeu cau cap nhat moi phut (khi .path loi)
%s%s

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
AccuracySec=10s
Unit=%s.service

[Install]
WantedBy=timers.target
`, fallbackReasonMarker, reason, RequestTaskName)
}

type watcherPaths struct {
	dir     string // thư mục unit systemd --user
	path    string // .path
	timer   string // timer dự phòng
	dropInD string // gen-harness-update-request.service.d
	dropIn  string // fallback.conf
}

func (deps Deps) watcherPaths() (watcherPaths, bool) {
	if deps.goos() != "linux" {
		return watcherPaths{}, false
	}
	home, err := deps.homeDir()
	if err != nil {
		return watcherPaths{}, false
	}
	dir := systemdUserDir(home)
	dropInD := filepath.Join(dir, RequestTaskName+".service.d")
	return watcherPaths{
		dir:     dir,
		path:    filepath.Join(dir, RequestTaskName+".path"),
		timer:   filepath.Join(dir, RequestFallbackTimer),
		dropInD: dropInD,
		dropIn:  filepath.Join(dropInD, requestFallbackDropIn),
	}, true
}

func fileExists(p string) bool {
	_, err := os.Lstat(p)
	return err == nil
}

// fallbackReasonFromFile đọc mã lý do từ dòng chú thích trong timer dự phòng.
func fallbackReasonFromFile(p string) string {
	b, err := os.ReadFile(p)
	if err != nil {
		return WatcherReasonOther
	}
	for _, line := range strings.Split(string(b), "\n") {
		if strings.HasPrefix(line, fallbackReasonMarker) {
			if r := strings.TrimSpace(strings.TrimPrefix(line, fallbackReasonMarker)); validWatcherReason(r) {
				return r
			}
		}
	}
	return WatcherReasonOther
}

func fallbackTimerActive(ctx context.Context, deps Deps) bool {
	out, _ := deps.runner().Output(ctx, "systemctl", []string{"--user", "is-active", RequestFallbackTimer})
	f := strings.Fields(string(out))
	return len(f) > 0 && f[0] == "active"
}

// inotifyLimit đọc /proc/sys/fs/inotify/max_user_instances (0 = không đọc được).
func inotifyLimit(deps Deps) int {
	read := deps.ReadFile
	if read == nil {
		read = os.ReadFile
	}
	b, err := read(InotifyInstancesProc)
	if err != nil {
		return 0
	}
	n, err := strconv.Atoi(strings.TrimSpace(string(b)))
	if err != nil || n < 0 {
		return 0
	}
	return n
}

// pathSpecKeys là các khoá của [Path] mà systemd dựng MỖI dòng một inotify instance riêng.
var pathSpecKeys = []string{"PathExists=", "PathExistsGlob=", "PathChanged=", "PathModified=", "DirectoryNotEmpty="}

// requestPathSpecs đếm số dòng Path*= của unit .path (mỗi dòng cần một inotify instance).
// Không đọc được ⇒ 1.
func requestPathSpecs(unitFile string) int {
	b, err := os.ReadFile(unitFile)
	if err != nil {
		return 1
	}
	n := 0
	for _, line := range strings.Split(string(b), "\n") {
		line = strings.TrimSpace(line)
		for _, k := range pathSpecKeys {
			if strings.HasPrefix(line, k) {
				n++
				break
			}
		}
	}
	if n < 1 {
		n = 1
	}
	return n
}

// inotifyHeadroom: người dùng này còn mở thêm được n inotify instance không (đúng nhu cầu của
// .path khi vào lại trạng thái chờ). Bằng chứng trực tiếp từ nhân, không phụ thuộc cách systemd báo.
func (d Deps) inotifyHeadroom(n int) bool {
	if d.InotifyHeadroom != nil {
		return d.InotifyHeadroom(n)
	}
	return probeInotifyInstances(n)
}

// requestWatcherSubState: SubState của .path ("waiting" = đang gác thật; "running" = service nó
// kích đang chạy). Không hỏi được ⇒ "".
func requestWatcherSubState(ctx context.Context, deps Deps) string {
	out, _ := deps.runner().Output(ctx, "systemctl", []string{"--user", "show", RequestTaskName + ".path", "-p", "SubState"})
	for _, line := range strings.Split(string(out), "\n") {
		if v, ok := strings.CutPrefix(strings.TrimSpace(line), "SubState="); ok {
			return strings.TrimSpace(v)
		}
	}
	return ""
}

// diagnoseWatcher: `.path` đang failed — vì sao? Result=resources + hạn mức inotify thấp
// ⇒ inotify; Result=resources mà không đọc được hạn mức (hoặc hạn mức đủ) ⇒ resources
// (lỗi tài nguyên chung); lý do khác ⇒ other. Chỉ đọc.
func diagnoseWatcher(ctx context.Context, deps Deps) string {
	out, _ := deps.runner().Output(ctx, "systemctl", []string{"--user", "show", RequestTaskName + ".path", "-p", "Result"})
	result := ""
	for _, line := range strings.Split(string(out), "\n") {
		if v, ok := strings.CutPrefix(strings.TrimSpace(line), "Result="); ok {
			result = strings.TrimSpace(v)
		}
	}
	if result != "resources" {
		return WatcherReasonOther
	}
	if n := inotifyLimit(deps); n > 0 && n < inotifyEnough {
		return WatcherReasonInotify
	}
	return WatcherReasonResources
}

// RequestWatcherHealth: tình trạng người gác, CHỈ ĐỌC (không ghi gì). Không phải Linux
// hoặc chưa cài unit .path (macOS/cron/Windows) ⇒ ok (không áp dụng).
func RequestWatcherHealth(ctx context.Context, deps Deps) WatcherHealth {
	ok := WatcherHealth{State: WatcherHealthOK}
	wp, applicable := deps.watcherPaths()
	if !applicable || !fileExists(wp.path) {
		return ok
	}
	state := RequestWatcherState(ctx, deps)
	switch state {
	case "failed", "inactive":
		if fileExists(wp.timer) && fallbackTimerActive(ctx, deps) {
			return WatcherHealth{State: WatcherHealthFallback, Reason: fallbackReasonFromFile(wp.timer)}
		}
		if state == "failed" {
			return WatcherHealth{State: WatcherHealthFailed, Reason: diagnoseWatcher(ctx, deps)}
		}
	}
	return ok
}

// HealRequestWatcher tự chữa người gác (xem đầu tệp). Khoẻ và không có dự phòng ⇒ chỉ MỘT
// lệnh đọc (is-active), không ghi gì, không in gì.
//
// Bài học systemd thật (E2E v0.1.54): `is-active`/`restart` KHÔNG chứng minh .path đang gác. Khi
// service mà .path kích (gen-harness-update-request.service — nơi `genh handle-requests` và cả
// `genh update` do nút Console chạy) đang chạy, systemd đưa .path vào "active (running)" mà KHÔNG
// dựng inotify nào ("If the triggered unit is already running, so are we"); service xong, .path vào
// lại chờ ⇒ hết instance ⇒ failed (resources) lần nữa. Nên: chỉ SubState=waiting mới là "đang gác
// thật" (mới gỡ dự phòng); "running" là chưa biết ⇒ GIỮ dự phòng. Hết inotify instance (phép thử
// trực tiếp) thì không reset-failed/restart — chỉ có thể "sống giả" và xoá mất Result chẩn đoán.
func HealRequestWatcher(ctx context.Context, deps Deps) WatcherHealth {
	ok := WatcherHealth{State: WatcherHealthOK}
	wp, applicable := deps.watcherPaths()
	if !applicable || !fileExists(wp.path) {
		return ok
	}
	runner := deps.runner()
	state := RequestWatcherState(ctx, deps)
	hasFallback := fileExists(wp.timer)
	specs := requestPathSpecs(wp.path)

	switch {
	case state == "active":
		if hasFallback { // đã sửa gốc (sysctl…) và .path sống lại ⇒ gỡ dự phòng — nhưng chỉ khi đang GÁC thật
			if requestWatcherSubState(ctx, deps) != "waiting" {
				return WatcherHealth{State: WatcherHealthFallback, Reason: fallbackReasonFromFile(wp.timer)}
			}
			removeFallback(ctx, deps, wp)
			ok.Changed, ok.FallbackRemoved = true, true
		}
		return ok
	case state == "failed", state == "inactive" && hasFallback:
		// chữa bên dưới
	default:
		if hasFallback && fallbackTimerActive(ctx, deps) {
			return WatcherHealth{State: WatcherHealthFallback, Reason: fallbackReasonFromFile(wp.timer)}
		}
		return ok
	}

	headroom := deps.inotifyHeadroom(specs)
	if headroom {
		_, _ = runner.Output(ctx, "systemctl", []string{"--user", "reset-failed", RequestTaskName + ".path", RequestTaskName + ".service"})
		_, _ = runner.Output(ctx, "systemctl", []string{"--user", "restart", RequestTaskName + ".path"})
		if RequestWatcherState(ctx, deps) == "active" {
			if !hasFallback {
				return WatcherHealth{State: WatcherHealthOK, Healed: true, Changed: true}
			}
			if requestWatcherSubState(ctx, deps) == "waiting" {
				removeFallback(ctx, deps, wp)
				return WatcherHealth{State: WatcherHealthOK, Healed: true, Changed: true, FallbackRemoved: true}
			}
			// "running": service đang chạy nên .path chưa gác thật ⇒ chưa biết, GIỮ dự phòng.
			return WatcherHealth{State: WatcherHealthFallback, Reason: fallbackReasonFromFile(wp.timer)}
		}
	}
	// Hết instance thì restart chỉ có thể "sống giả" (xem trên) ⇒ không thử; giữ nguyên failed + Result.

	// Vẫn lỗi ⇒ dự phòng quét mỗi phút.
	reason := diagnoseWatcher(ctx, deps)
	if !headroom && reason == WatcherReasonOther {
		reason = WatcherReasonInotify // nhân báo hết instance: bằng chứng trực tiếp
	}
	if hasFallback && fallbackTimerActive(ctx, deps) && fallbackReasonFromFile(wp.timer) == reason {
		return WatcherHealth{State: WatcherHealthFallback, Reason: reason} // đã có từ trước: im lặng
	}
	if err := installFallback(ctx, deps, wp, reason); err != nil {
		return WatcherHealth{State: WatcherHealthFailed, Reason: reason}
	}
	return WatcherHealth{State: WatcherHealthFallback, Reason: reason, Changed: true}
}

func installFallback(ctx context.Context, deps Deps, wp watcherPaths, reason string) error {
	if err := os.MkdirAll(wp.dropInD, 0o755); err != nil {
		return err
	}
	// Service được kích mỗi phút ⇒ gỡ giới hạn StartLimit 5 lần/300 giây (nếu không, lần thứ 6
	// bị từ chối và dự phòng tự chết sau ~5 phút). Chỉ có hiệu lực khi dự phòng đang bật.
	if err := os.WriteFile(wp.dropIn, []byte(fallbackStartLimitDropIn), 0o644); err != nil {
		return err
	}
	if err := os.WriteFile(wp.timer, []byte(RequestFallbackTimerUnit(reason)), 0o644); err != nil {
		return err
	}
	runner := deps.runner()
	if _, err := runner.Output(ctx, "systemctl", []string{"--user", "daemon-reload"}); err != nil {
		return err
	}
	if _, err := runner.Output(ctx, "systemctl", []string{"--user", "enable", "--now", RequestFallbackTimer}); err != nil {
		return err
	}
	return nil
}

// removeFallback gỡ timer dự phòng + drop-in (best-effort, idempotent).
func removeFallback(ctx context.Context, deps Deps, wp watcherPaths) {
	runner := deps.runner()
	_, _ = runner.Output(ctx, "systemctl", []string{"--user", "disable", "--now", RequestFallbackTimer})
	_ = os.Remove(wp.timer)
	_ = os.Remove(wp.dropIn)
	_ = os.Remove(wp.dropInD) // chỉ xoá được khi trống
	_, _ = runner.Output(ctx, "systemctl", []string{"--user", "daemon-reload"})
}
