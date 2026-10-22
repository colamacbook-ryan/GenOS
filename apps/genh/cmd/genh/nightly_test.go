package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/autoupdate"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/ops"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/selfupdate"
)

// TestMain cô lập MỌI test của cmd/genh khỏi máy dev: HOME là thư mục tạm, systemctl/crontab/
// loginctl/docker đều là Runner giả báo lỗi. Trước v0.1.53 các test đi qua publishHostInfo
// (runUpdate…) ghi unit systemd --user và gọi `systemctl --user enable --now` THẬT trên HOME của người
// chạy test — với v0.1.53 (tự lành) còn có thể BẬT lịch đêm trỏ vào binary test.
func TestMain(m *testing.M) {
	home, err := os.MkdirTemp("", "genh-cmd-home-*")
	if err != nil {
		panic(err)
	}
	_ = os.Setenv("HOME", home)
	_ = os.Setenv("USERPROFILE", home) // os.UserHomeDir trên Windows
	hostInfoEnvFn = func() hostInfoEnv { return hermeticHost(home, errRunner{}) }
	autostartDepsFn = func() ops.AutostartDeps { return ops.AutostartDeps{Runner: &fake.Runner{}} }
	code := m.Run()
	_ = os.RemoveAll(home)
	os.Exit(code)
}

// errRunner: mọi lệnh hệ thống đều lỗi (máy không có systemd --user/crontab).
type errRunner struct{}

func (errRunner) Output(_ context.Context, name string, _ []string) ([]byte, error) {
	return nil, errors.New("test: không có " + name)
}

// cmdRunner ghi lại mọi lệnh ("tên đối số…") và trả đầu ra giả theo luật đầu tiên khớp; dyn (nếu
// có) được hỏi trước để giả lập trạng thái đổi theo thời gian. Không luật nào khớp ⇒ thành công, rỗng.
type cmdRunner struct {
	mu    sync.Mutex
	calls []string
	rules []cmdRule
	dyn   func(line string, calls []string) (out string, err error, handled bool)
}

type cmdRule struct {
	match, out string
	err        error
}

func (r *cmdRunner) on(match, out string, err error) *cmdRunner {
	r.rules = append(r.rules, cmdRule{match, out, err})
	return r
}

func (r *cmdRunner) Output(_ context.Context, name string, args []string) ([]byte, error) {
	line := name + " " + strings.Join(args, " ")
	r.mu.Lock()
	defer r.mu.Unlock()
	r.calls = append(r.calls, line)
	if r.dyn != nil {
		if out, err, ok := r.dyn(line, r.calls); ok {
			return []byte(out), err
		}
	}
	for _, rule := range r.rules {
		if strings.Contains(line, rule.match) {
			return []byte(rule.out), rule.err
		}
	}
	return nil, nil
}

func (r *cmdRunner) ran(sub string) bool { return r.count(sub) > 0 }

func (r *cmdRunner) count(sub string) int {
	r.mu.Lock()
	defer r.mu.Unlock()
	n := 0
	for _, c := range r.calls {
		if strings.Contains(c, sub) {
			n++
		}
	}
	return n
}

func (r *cmdRunner) reset() {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.calls = nil
}

type noopOffsite struct{}

func (noopOffsite) Enable(context.Context) (string, string, error) { return "", "", nil }
func (noopOffsite) Disable(context.Context) (string, error)        { return "", nil }
func (noopOffsite) Status(context.Context) (autoupdate.OffsiteScheduleStatus, error) {
	return autoupdate.OffsiteScheduleStatus{}, nil
}

// hermeticHost dựng hostInfoEnv không chạm máy thật: Runner/HomeDir giả, systemctl "có" trên PATH.
func hermeticHost(home string, rr autoupdate.Runner) hostInfoEnv {
	return hostInfoEnv{
		Base: autoupdate.Deps{
			Runner: rr, HomeDir: home, GOOS: "linux", UID: "1000", GenhPath: "/g/bin/genh", Location: time.UTC,
			LookPath: func(string) (string, error) { return "/usr/bin/systemctl", nil },
		},
		Offsite: ops.OffsiteDeps{Scheduler: noopOffsite{}},
	}
}

// useHost tiêm hostInfoEnv cho test này; trả HOME giả.
func useHost(t *testing.T, rr autoupdate.Runner) string {
	t.Helper()
	home := t.TempDir()
	old := hostInfoEnvFn
	hostInfoEnvFn = func() hostInfoEnv { return hermeticHost(home, rr) }
	t.Cleanup(func() { hostInfoEnvFn = old })
	return home
}

func showProps(loadState, unitFileState, active, last, next string) string {
	return "LoadState=" + loadState + "\nUnitFileState=" + unitFileState + "\nActiveState=" + active +
		"\nLastTriggerUSec=" + last + "\nNextElapseUSecRealtime=" + next + "\n"
}

var errExit1 = errors.New("exit status 1")

// healthyRules: lịch đêm khoẻ (enabled + active + có lần kế tiếp), linger yes.
func healthyRules(rr *cmdRunner) *cmdRunner {
	return rr.
		on("show gen-harness-update.timer", showProps("loaded", "enabled", "active", "@1760000000", "@1760086400"), nil).
		on("loginctl show-user", "yes\n", nil).
		on("is-active gen-harness-update-request.path", "active\n", nil)
}

// statefulTimer: `systemctl --user show gen-harness-update.timer` theo lệnh enable/disable
// GẦN NHẤT đã chạy (bật ⇒ enabled + active + có lần kế tiếp; tắt ⇒ not-found). on = trạng thái
// trước mọi lệnh.
func statefulTimer(rr *cmdRunner, on bool) *cmdRunner {
	rr.dyn = func(line string, calls []string) (string, error, bool) {
		if !strings.Contains(line, "show gen-harness-update.timer") {
			return "", nil, false
		}
		state := on
		for _, c := range calls {
			switch {
			case strings.HasPrefix(c, "systemctl --user disable --now gen-harness-update.timer"):
				state = false
			case strings.HasPrefix(c, "systemctl --user enable --now gen-harness-update.timer"):
				state = true
			}
		}
		if state {
			return showProps("loaded", "enabled", "active", "@1760000000", "@1760086400"), nil, true
		}
		return showProps("not-found", "", "inactive", "", ""), nil, true
	}
	return rr
}

// installDirAlive: gốc cài đặt tạm có config/secrets.json (bản cài "còn sống").
func installDirAlive(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	p := filepath.Join(dir, "config", "secrets.json")
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	return dir
}

func readNightly(t *testing.T, dir string) hostlink.NightlyStatus {
	t.Helper()
	st, err := hostlink.ReadNightlyStatus(dir)
	if err != nil {
		t.Fatalf("đọc nightly-status.json: %v", err)
	}
	return st
}

// ─── F-93: tự lành + dấu opt-out ────────────────────────────────────────────

// Sếp đã `genh auto-update disable` / `install --no-auto-update` ⇒ publishHostInfo KHÔNG tự lành.
func TestPublishHostInfo_DauOptOut_KhongTuLanh(t *testing.T) {
	rr := &cmdRunner{}
	rr.on("show gen-harness-update.timer", showProps("loaded", "disabled", "inactive", "", ""), errExit1)
	rr.on("loginctl show-user", "yes\n", nil)
	home := useHost(t, rr)
	dir := installDirAlive(t)
	if err := ops.SetAutoUpdateOptOut(dir, true, time.Now()); err != nil {
		t.Fatal(err)
	}
	out, errOut := captureStd(t, func() { publishHostInfo(dir, 8443) })
	if rr.ran("enable --now gen-harness-update.timer") || rr.ran("--user enable --now gen-harness-update.timer") {
		t.Fatalf("Sếp đã tắt: KHÔNG được bật lại lịch đêm: %v", rr.calls)
	}
	if _, err := os.Stat(filepath.Join(home, ".config", "systemd", "user", autoupdate.TaskName+".service")); !os.IsNotExist(err) {
		t.Fatalf("không được ghi unit lịch đêm: %v", err)
	}
	if strings.Contains(out+errOut, "đã bật lại") {
		t.Errorf("không được báo đã bật lại: %q %q", out, errOut)
	}
	ns := readNightly(t, dir)
	if !ns.OptedOut || ns.Enabled || ns.Mechanism != "systemd" || !ns.UnitPresent {
		t.Errorf("nightly-status = %+v", ns)
	}
	if info, err := hostlink.ReadInfo(dir); err != nil || info.AutoUpdateEnabled == nil || *info.AutoUpdateEnabled {
		t.Errorf("genh.json phải ghi auto_update_enabled=false: %+v %v", info, err)
	}
	// Trình nhận yêu cầu + trực canh vẫn được cài (độc lập với lịch đêm).
	if !rr.ran("enable --now gen-harness-update-request.path") {
		t.Errorf("trình nhận yêu cầu vẫn phải được cài: %v", rr.calls)
	}

	// Xoá dấu (Sếp `genh auto-update enable`) ⇒ máy chưa có dấu mà lịch tắt được BẬT LẠI.
	if err := ops.SetAutoUpdateOptOut(dir, false, time.Now()); err != nil {
		t.Fatal(err)
	}
	out, _ = captureStd(t, func() { publishHostInfo(dir, 8443) })
	if !rr.ran("enable --now gen-harness-update.timer") {
		t.Fatalf("không có dấu ⇒ phải tự lành: %v", rr.calls)
	}
	if !strings.Contains(out, "genh: lịch tự cập nhật đêm đã bị tắt/mất — đã bật lại (~03:00). Muốn tắt hẳn: genh auto-update disable") {
		t.Errorf("thiếu dòng tự lành: %q", out)
	}
	svc, _ := os.ReadFile(filepath.Join(home, ".config", "systemd", "user", autoupdate.TaskName+".service"))
	if !strings.Contains(string(svc), "--install-dir "+dir) || !strings.Contains(string(svc), "Environment=GEN_HARNESS_HOME="+dir) {
		t.Errorf("unit phải mang --install-dir/GEN_HARNESS_HOME:\n%s", svc)
	}
	if ns := readNightly(t, dir); ns.OptedOut {
		t.Errorf("opted_out phải false: %+v", ns)
	}
}

func TestAutoUpdateCmd_DisableGhiDau_EnableXoaDau(t *testing.T) {
	rr := statefulTimer(healthyRules(&cmdRunner{}), true)
	useHost(t, rr)
	dir := installDirAlive(t)

	var code int
	out, _ := captureStd(t, func() { code = runAutoUpdate([]string{"disable", "--install-dir", dir}) })
	if code != 0 || !strings.Contains(out, "Đã tắt tự cập nhật hằng đêm.") {
		t.Fatalf("disable: code=%d out=%q", code, out)
	}
	if !ops.AutoUpdateOptedOut(dir) {
		t.Fatal("`auto-update disable` phải ghi dấu Sếp đã chủ động tắt")
	}
	fi, err := os.Stat(ops.AutoUpdateOptOutPath(dir))
	if err != nil {
		t.Fatalf("dấu: %v", err)
	}
	// Windows không có bit quyền kiểu Unix (Stat luôn báo 0666/0444) ⇒ chỉ kiểm 0600 ở Unix.
	if runtime.GOOS != "windows" && fi.Mode().Perm() != 0o600 {
		t.Fatalf("dấu phải 0600: %v", fi.Mode())
	}
	if info, _ := hostlink.ReadInfo(dir); info.AutoUpdateEnabled == nil || *info.AutoUpdateEnabled {
		t.Errorf("genh.json: %+v", info)
	}
	if !readNightly(t, dir).OptedOut {
		t.Error("nightly-status phải opted_out=true")
	}

	out, _ = captureStd(t, func() { code = runAutoUpdate([]string{"status", "--install-dir", dir}) })
	if code != 0 || !strings.Contains(out, "Tự cập nhật hằng đêm:") || !strings.Contains(out, "Lần kế tiếp:") {
		t.Fatalf("status: code=%d out=%q", code, out)
	}

	out, _ = captureStd(t, func() { code = runAutoUpdate([]string{"enable", "--install-dir", dir}) })
	if code != 0 || !strings.Contains(out, "Đã bật tự cập nhật hằng đêm lúc ~03:00") {
		t.Fatalf("enable: code=%d out=%q", code, out)
	}
	if ops.AutoUpdateOptedOut(dir) {
		t.Fatal("`auto-update enable` phải xoá dấu")
	}
	if info, _ := hostlink.ReadInfo(dir); info.AutoUpdateEnabled == nil || !*info.AutoUpdateEnabled {
		t.Errorf("genh.json sau enable: %+v", info)
	}
	// F-95: `auto-update enable` ghi autostart-status.json + nightly-status.json.
	if _, err := hostlink.ReadAutostartStatus(dir); err != nil {
		t.Errorf("enable phải ghi run/autostart-status.json: %v", err)
	}
	if readNightly(t, dir).OptedOut {
		t.Error("nightly-status sau enable: opted_out phải false")
	}
}

// `genh auto-update disable` mà lịch VẪN bật sau đó (Disable best-effort bỏ qua lỗi systemctl —
// vd chạy bằng tài khoản khác/không có phiên systemd --user): KHÔNG báo "Đã tắt", thoát 1, nói rõ
// lịch vẫn bật; dấu vẫn ghi (ý Sếp: không tự lành) và genh.json nói thật (lịch còn bật).
func TestAutoUpdateCmd_DisableHut_KhongBaoDaTat(t *testing.T) {
	rr := healthyRules(&cmdRunner{}) // show luôn báo enabled + active: tắt không ăn
	useHost(t, rr)
	dir := installDirAlive(t)
	var code int
	out, errOut := captureStd(t, func() { code = runAutoUpdate([]string{"disable", "--install-dir", dir}) })
	if code != 1 || strings.Contains(out, "Đã tắt") || !strings.Contains(errOut, disableStillOnLine) {
		t.Fatalf("tắt hụt: code=%d out=%q err=%q", code, out, errOut)
	}
	if !ops.AutoUpdateOptedOut(dir) {
		t.Error("dấu Sếp đã tắt vẫn phải ghi (không tự lành)")
	}
	if info, _ := hostlink.ReadInfo(dir); info.AutoUpdateEnabled == nil || !*info.AutoUpdateEnabled {
		t.Errorf("genh.json phải nói lịch còn bật: %+v", info)
	}
	if ns := readNightly(t, dir); !ns.OptedOut || !ns.Enabled {
		t.Errorf("nightly-status phải opted_out=true + enabled=true (Console: Sếp đã tắt nhưng lịch vẫn bật): %+v", ns)
	}
}

// `install --no-auto-update` ghi dấu VÀ tắt lịch đêm đang có của chính bản cài này (vd `install
// --force --no-auto-update` đè lên máy đã bật lịch); `install` không cờ xoá dấu rồi bật lịch
// (mang --install-dir/--port).
func TestApplyAutoUpdateChoice(t *testing.T) {
	rr := statefulTimer(healthyRules(&cmdRunner{}), true)
	home := useHost(t, rr)
	dir := installDirAlive(t)

	out, errOut := captureStd(t, func() { applyAutoUpdateChoice(dir, 9443, true) })
	if !ops.AutoUpdateOptedOut(dir) {
		t.Fatal("--no-auto-update phải ghi dấu")
	}
	if rr.ran("--user enable --now gen-harness-update.timer") {
		t.Fatal("--no-auto-update không được bật lịch")
	}
	if !rr.ran("--user disable --now gen-harness-update.timer") || !strings.Contains(out, "Đã tắt tự cập nhật hằng đêm.") {
		t.Fatalf("--no-auto-update trên máy đang có lịch phải tắt lịch đó: %v out=%q err=%q", rr.calls, out, errOut)
	}
	rr.reset()

	out, _ = captureStd(t, func() { applyAutoUpdateChoice(dir, 9443, false) })
	if ops.AutoUpdateOptedOut(dir) {
		t.Fatal("install không cờ phải xoá dấu")
	}
	if !rr.ran("enable --now gen-harness-update.timer") || !strings.Contains(out, "Đã bật tự cập nhật hằng đêm") {
		t.Fatalf("install không cờ phải bật lịch: %v %q", rr.calls, out)
	}
	svc, _ := os.ReadFile(filepath.Join(home, ".config", "systemd", "user", autoupdate.TaskName+".service"))
	if !strings.Contains(string(svc), "update --yes --quiet --install-dir "+dir+" --port 9443") {
		t.Errorf("lịch đêm phải mang --install-dir/--port:\n%s", svc)
	}
}

// `install --no-auto-update` trên máy CHƯA có lịch: chỉ ghi dấu, không gọi lệnh ghi nào
// (không disable/daemon-reload/ghi lại crontab của Sếp).
func TestApplyAutoUpdateChoice_NoAutoUpdate_MayMoiKhongDungGi(t *testing.T) {
	rr := statefulTimer(&cmdRunner{}, false)
	rr.on("crontab -l", "", errExit1)
	useHost(t, rr)
	dir := installDirAlive(t)
	out, _ := captureStd(t, func() { applyAutoUpdateChoice(dir, 8443, true) })
	if !ops.AutoUpdateOptedOut(dir) {
		t.Fatal("--no-auto-update phải ghi dấu")
	}
	for _, c := range rr.calls {
		if strings.Contains(c, " disable") || strings.Contains(c, "daemon-reload") || strings.HasPrefix(c, "crontab /") {
			t.Fatalf("máy chưa có lịch: không được gọi lệnh ghi %q (%v)", c, rr.calls)
		}
	}
	if strings.Contains(out, "Đã tắt") {
		t.Errorf("không có gì để tắt: %q", out)
	}
}

// Bản cài PHỤ `install --no-auto-update` không tắt lịch của bản chính còn sống.
func TestApplyAutoUpdateChoice_NoAutoUpdate_BanPhuKhongTatLichBanChinh(t *testing.T) {
	rr := statefulTimer(healthyRules(&cmdRunner{}), false)
	home := useHost(t, rr)
	mainDir, other := installDirAlive(t), installDirAlive(t)
	captureStd(t, func() { applyAutoUpdateChoice(mainDir, 8443, false) })
	timerBefore, _ := os.ReadFile(filepath.Join(home, ".config", "systemd", "user", autoupdate.TaskName+".timer"))
	rr.reset()
	captureStd(t, func() { applyAutoUpdateChoice(other, 8443, true) })
	if rr.ran("disable") {
		t.Fatalf("bản phụ không được tắt lịch của bản chính: %v", rr.calls)
	}
	if b, err := os.ReadFile(filepath.Join(home, ".config", "systemd", "user", autoupdate.TaskName+".timer")); err != nil || string(b) != string(timerBefore) {
		t.Errorf("timer của bản chính phải còn nguyên: %v", err)
	}
	if !ops.AutoUpdateOptedOut(other) || ops.AutoUpdateOptedOut(mainDir) {
		t.Error("dấu chỉ ghi cho bản phụ")
	}
}

// Bản cài PHỤ không đổi lịch của bản chính: cảnh báo đúng một dòng.
func TestApplyAutoUpdateChoice_BanPhuKhongDoiLich(t *testing.T) {
	rr := healthyRules(&cmdRunner{})
	home := useHost(t, rr)
	mainDir, other := installDirAlive(t), installDirAlive(t)
	captureStd(t, func() { applyAutoUpdateChoice(mainDir, 8443, false) })
	timerBefore, _ := os.ReadFile(filepath.Join(home, ".config", "systemd", "user", autoupdate.TaskName+".timer"))
	rr.reset()

	_, errOut := captureStd(t, func() { applyAutoUpdateChoice(other, 8443, false) })
	want := "genh: Máy này có bản cài khác đang giữ lịch đêm/nút Cập nhật ngay (" + mainDir + ") — bản cài " + other + " không đổi lịch."
	if !strings.Contains(errOut, want) {
		t.Fatalf("thiếu cảnh báo một dòng %q:\n%s", want, errOut)
	}
	if rr.ran("enable") || rr.ran("disable") {
		t.Errorf("bản phụ không được đổi lịch: %v", rr.calls)
	}
	svc, _ := os.ReadFile(filepath.Join(home, ".config", "systemd", "user", autoupdate.TaskName+".service"))
	if strings.Contains(string(svc), other) {
		t.Errorf("unit bị trỏ sang bản phụ:\n%s", svc)
	}
	if b, _ := os.ReadFile(filepath.Join(home, ".config", "systemd", "user", autoupdate.TaskName+".timer")); string(b) != string(timerBefore) {
		t.Error("timer bị đổi")
	}
}

// Phát lại chuỗi đêm 03/10 (nâng v0.1.37 → v0.1.44): lịch đêm đang chạy gọi `genh update` →
// publishHostInfo lần đầu bật trực canh (daemon-reload + enable --now từ BÊN TRONG
// gen-harness-update.service). KHÔNG lệnh nào được disable/stop/mask gen-harness-update.timer
// và tệp timer không đổi.
func TestReplayNight0310(t *testing.T) {
	rr := healthyRules(&cmdRunner{})
	home := useHost(t, rr)
	dir := installDirAlive(t)
	captureStd(t, func() { applyAutoUpdateChoice(dir, 8443, false) }) // máy Sếp đã bật lịch đêm từ trước
	unitDir := filepath.Join(home, ".config", "systemd", "user")
	timerBefore, err := os.ReadFile(filepath.Join(unitDir, autoupdate.TaskName+".timer"))
	if err != nil {
		t.Fatal(err)
	}
	svcBefore, _ := os.ReadFile(filepath.Join(unitDir, autoupdate.TaskName+".service"))
	rr.reset()

	out, _ := captureStd(t, func() { publishHostInfo(dir, 8443) })

	if !strings.Contains(out, "Đã bật trực canh máy chủ mỗi 12 phút") {
		t.Errorf("trực canh phải được bật lần đầu: %q", out)
	}
	if !rr.ran("enable --now gen-harness-watchdog.timer") || !rr.ran("enable --now gen-harness-update-request.path") {
		t.Errorf("thiếu enable trực canh/trình nhận yêu cầu: %v", rr.calls)
	}
	bad := regexp.MustCompile(`(disable|stop|mask|kill|revert|reset-failed).*gen-harness-update\.timer`)
	for _, c := range rr.calls {
		if bad.MatchString(c) {
			t.Fatalf("lệnh làm tắt gen-harness-update.timer: %q\ntoàn bộ: %v", c, rr.calls)
		}
	}
	if rr.ran("enable --now gen-harness-update.timer") {
		t.Errorf("lịch đêm khoẻ thì không enable lại: %v", rr.calls)
	}
	if b, _ := os.ReadFile(filepath.Join(unitDir, autoupdate.TaskName+".timer")); string(b) != string(timerBefore) {
		t.Fatal("tệp gen-harness-update.timer bị đổi")
	}
	if b, _ := os.ReadFile(filepath.Join(unitDir, autoupdate.TaskName+".service")); string(b) != string(svcBefore) {
		t.Fatal("tệp gen-harness-update.service bị đổi")
	}
	info, err := hostlink.ReadInfo(dir)
	if err != nil || info.AutoUpdateEnabled == nil || !*info.AutoUpdateEnabled || info.Updater != "systemd" {
		t.Fatalf("genh.json = %+v, %v", info, err)
	}
	ns := readNightly(t, dir)
	if !ns.Enabled || ns.Mechanism != "systemd" || ns.Active == nil || !*ns.Active || ns.RequestWatcher != "active" || ns.Linger != "yes" {
		t.Errorf("nightly-status = %+v", ns)
	}

	// Chạy lần 2: không đổi gì (idempotent) — không enable lịch đêm, tệp y nguyên.
	rr.reset()
	captureStd(t, func() { publishHostInfo(dir, 8443) })
	if rr.ran("enable --now gen-harness-update.timer") || rr.ran("gen-harness-update.timer") && rr.ran("--user disable") {
		t.Errorf("lần 2: %v", rr.calls)
	}
	if b, _ := os.ReadFile(filepath.Join(unitDir, autoupdate.TaskName+".timer")); string(b) != string(timerBefore) {
		t.Fatal("lần 2: tệp timer bị đổi")
	}
}

// H-b: daemon-reload/enable từ bên trong gen-harness-update.service làm timer MẤT lần chạy kế tiếp
// (active mà NextElapse rỗng) ⇒ publishHostInfo khởi động lại timer (tự lành), không enable/disable.
func TestReplayNight0310_HB_TimerMatLich_TuLanh(t *testing.T) {
	rr := &cmdRunner{}
	rr.on("loginctl show-user", "yes\n", nil)
	reloaded := false
	rr.dyn = func(line string, _ []string) (string, error, bool) {
		switch {
		case strings.Contains(line, "daemon-reload"):
			reloaded = true
		case strings.Contains(line, "show gen-harness-update.timer"):
			next := "@1760086400"
			if reloaded {
				next = "" // sau daemon-reload: timer active nhưng mất lần chạy kế tiếp
			}
			return showProps("loaded", "enabled", "active", "@1760000000", next), nil, true
		case strings.Contains(line, "restart gen-harness-update.timer"):
			reloaded = false // restart khôi phục lịch
		}
		return "", nil, false
	}
	useHost(t, rr)
	dir := installDirAlive(t)
	out, _ := captureStd(t, func() { publishHostInfo(dir, 8443) })
	if rr.count("restart gen-harness-update.timer") != 1 {
		t.Fatalf("phải restart timer đúng 1 lần: %v", rr.calls)
	}
	if !strings.Contains(out, autoupdate.RearmedMessage) {
		t.Errorf("thiếu dòng tự lành H-b: %q", out)
	}
	if rr.ran("disable") || rr.ran("--user stop") || rr.ran("mask") {
		t.Errorf("không được disable/stop/mask: %v", rr.calls)
	}
}

// ─── F-94: `genh auto-update status` trung thực ─────────────────────────────

func TestNightlyStatusText_DuNamTruongVaCanhBaoLog(t *testing.T) {
	now := time.Date(2026, 10, 10, 15, 0, 0, 0, time.UTC)
	log := filepath.Join(t.TempDir(), "auto-update.log")
	if err := os.WriteFile(log, []byte("x\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	st := autoupdate.Status{
		Enabled: true, Mechanism: autoupdate.ScheduleSystemd, UnitPresent: true, UnitFileState: "enabled", Active: "active",
		LastRun: time.Date(2026, 10, 10, 3, 7, 0, 0, time.UTC), NextRun: time.Date(2026, 10, 11, 3, 21, 0, 0, time.UTC),
		Linger: "yes", Detail: "systemd --user timer gen-harness-update.timer: UnitFileState=enabled, ActiveState=active",
	}
	chtimes := func(age time.Duration) (time.Time, error) {
		mt := now.Add(-age)
		if err := os.Chtimes(log, mt, mt); err != nil {
			t.Fatal(err)
		}
		fi, err := os.Stat(log)
		return fi.ModTime(), err
	}

	// Log mới (2 giờ) ⇒ BẬT, đủ 5 trường, KHÔNG cảnh báo.
	mod, err := chtimes(2 * time.Hour)
	txt := nightlyStatusText(st, mod, err, false, now)
	for _, want := range []string{
		"Tự cập nhật hằng đêm: BẬT\n",
		"Cơ chế: systemd --user timer (gen-harness-update.timer)",
		"Đã bật (enabled): có · Lịch đang chạy (active): có",
		"Lần chạy gần nhất: 10/10/2026 03:07 (giờ máy)",
		"Lần kế tiếp: 11/10/2026 03:21 (giờ máy)",
		"Linger: có (tiến trình nền chạy cả khi không ai đăng nhập)",
	} {
		if !strings.Contains(txt, want) {
			t.Errorf("thiếu %q:\n%s", want, txt)
		}
	}
	if strings.Contains(txt, "CẢNH BÁO") {
		t.Errorf("log 2 giờ không được cảnh báo:\n%s", txt)
	}

	// Log cũ 37 giờ ⇒ BẬT NHƯNG KHÔNG CHẠY + dòng CẢNH BÁO.
	mod, err = chtimes(37 * time.Hour)
	txt = nightlyStatusText(st, mod, err, false, now)
	for _, want := range []string{
		"Tự cập nhật hằng đêm: BẬT NHƯNG KHÔNG CHẠY",
		"CẢNH BÁO: logs/auto-update.log không có dòng mới hơn 36 giờ (lần ghi cuối 09/10/2026 02:00 (giờ máy)) — lịch đêm có thể không chạy. Bật lại: genh auto-update enable",
	} {
		if !strings.Contains(txt, want) {
			t.Errorf("thiếu %q:\n%s", want, txt)
		}
	}
	// Đúng 36 giờ chưa cảnh báo; quá chút là cảnh báo.
	mod, err = chtimes(36 * time.Hour)
	if txt := nightlyStatusText(st, mod, err, false, now); strings.Contains(txt, "CẢNH BÁO") {
		t.Errorf("đúng 36 giờ chưa cảnh báo:\n%s", txt)
	}
}

// Hồi quy (e2e-nightly-real, v0.1.53): vừa bật lịch (log chưa có) mà systemd đã có LastTriggerUSec gần đây
// (sau khởi động lại nó nạp mtime tệp stamp tạo lúc bật lịch) ⇒ vẫn là BẬT, không cảnh báo; chỉ khi lần kích đó
// cũ hơn 36 giờ mà log vẫn trống mới là BẬT NHƯNG KHÔNG CHẠY + CẢNH BÁO.
func TestNightlyStatusText_LogChuaCo_LanKichGanDayKhongCanhBao(t *testing.T) {
	now := time.Date(2026, 10, 10, 15, 0, 0, 0, time.UTC)
	st := autoupdate.Status{
		Enabled: true, Mechanism: autoupdate.ScheduleSystemd, UnitPresent: true, UnitFileState: "enabled", Active: "active",
		LastRun: now.Add(-time.Hour), NextRun: time.Date(2026, 10, 11, 3, 21, 0, 0, time.UTC), Linger: "yes",
	}
	txt := nightlyStatusText(st, time.Time{}, os.ErrNotExist, false, now)
	if !strings.Contains(txt, "Tự cập nhật hằng đêm: BẬT\n") || strings.Contains(txt, "NHƯNG KHÔNG CHẠY") || strings.Contains(txt, "CẢNH BÁO") {
		t.Errorf("vừa bật, log chưa có, lần kích 1 giờ trước ⇒ BẬT không cảnh báo:\n%s", txt)
	}
	st.LastRun = time.Time{}
	if txt := nightlyStatusText(st, time.Time{}, os.ErrNotExist, false, now); strings.Contains(txt, "CẢNH BÁO") ||
		!strings.Contains(txt, "Lần chạy gần nhất: chưa chạy lần nào") {
		t.Errorf("chưa kích lần nào ⇒ không cảnh báo:\n%s", txt)
	}
	st.LastRun = now.Add(-37 * time.Hour)
	txt = nightlyStatusText(st, time.Time{}, os.ErrNotExist, false, now)
	for _, want := range []string{"Tự cập nhật hằng đêm: BẬT NHƯNG KHÔNG CHẠY", "CẢNH BÁO: logs/auto-update.log không có dòng mới hơn 36 giờ (lần ghi cuối chưa có)"} {
		if !strings.Contains(txt, want) {
			t.Errorf("thiếu %q:\n%s", want, txt)
		}
	}
}

func TestNightlyStatusText_TatChuDongLingerVaHong(t *testing.T) {
	now := time.Date(2026, 10, 10, 15, 0, 0, 0, time.UTC)
	off := autoupdate.Status{Mechanism: autoupdate.ScheduleSystemd, UnitPresent: true, UnitFileState: "disabled", Active: "inactive", Linger: "no"}

	// Sếp đã chủ động tắt.
	txt := nightlyStatusText(off, time.Time{}, os.ErrNotExist, true, now)
	if !strings.Contains(txt, "Tự cập nhật hằng đêm: TẮT (Sếp đã chủ động tắt — bật lại: genh auto-update enable)") {
		t.Errorf("thiếu 'Sếp đã chủ động tắt':\n%s", txt)
	}
	// Linger tắt: câu sửa KHÔNG có dấu chấm sau lệnh.
	if !strings.Contains(txt, "Linger: KHÔNG — chạy một lần: sudo loginctl enable-linger $USER\n") {
		t.Errorf("thiếu dòng linger:\n%s", txt)
	}
	for _, want := range []string{"Đã bật (enabled): không · Lịch đang chạy (active): không", "Lần chạy gần nhất: chưa chạy lần nào", "Lần kế tiếp: không rõ"} {
		if !strings.Contains(txt, want) {
			t.Errorf("thiếu %q:\n%s", want, txt)
		}
	}
	// Không phải Sếp tắt (máy tự mất lịch) ⇒ "TẮT" trơn, không nói Sếp đã tắt.
	txt = nightlyStatusText(off, time.Time{}, os.ErrNotExist, false, now)
	if !strings.Contains(txt, "Tự cập nhật hằng đêm: TẮT\n") || strings.Contains(txt, "Sếp đã chủ động tắt") {
		t.Errorf("%s", txt)
	}

	// Unit enabled mà timer inactive/failed ⇒ BẬT NHƯNG KHÔNG CHẠY.
	broken := autoupdate.Status{Mechanism: autoupdate.ScheduleSystemd, UnitPresent: true, UnitFileState: "enabled", Active: "failed", Linger: "yes"}
	if txt := nightlyStatusText(broken, time.Time{}, os.ErrNotExist, false, now); !strings.Contains(txt, "BẬT NHƯNG KHÔNG CHẠY") ||
		!strings.Contains(txt, "Đã bật (enabled): có · Lịch đang chạy (active): không") {
		t.Errorf("%s", txt)
	}

	// Chưa có unit nào.
	none := autoupdate.Status{Linger: "unknown", Detail: "chưa bật (không có systemd --user timer lẫn dòng crontab)"}
	txt = nightlyStatusText(none, time.Time{}, os.ErrNotExist, false, now)
	for _, want := range []string{"TẮT", "Cơ chế: chưa có", "Linger: không rõ", "chưa bật (không có systemd --user timer lẫn dòng crontab)"} {
		if !strings.Contains(txt, want) {
			t.Errorf("thiếu %q:\n%s", want, txt)
		}
	}

	// Lịch đang thuộc bản cài KHÁC: với bản này là TẮT.
	owned := autoupdate.Status{Enabled: true, Mechanism: autoupdate.ScheduleSystemd, UnitPresent: true, UnitFileState: "enabled", Active: "active", OwnedByOther: true, Owner: "/x/main", Linger: "yes"}
	txt = nightlyStatusText(owned, time.Now(), nil, false, time.Now())
	if !strings.Contains(txt, "TẮT (lịch đêm của máy đang thuộc bản cài khác") || !strings.Contains(txt, "Lịch đang thuộc bản cài: /x/main") {
		t.Errorf("%s", txt)
	}
}

func TestPrintLingerWarning(t *testing.T) {
	var b strings.Builder
	printLingerWarning(&b, "", true)
	if b.Len() != 0 {
		t.Fatal("không cảnh báo thì không in gì")
	}
	// Không TTY (lịch đêm vào log): chữ thường, không mã màu.
	printLingerWarning(&b, autoupdate.LingerWarning, false)
	got := b.String()
	if strings.Contains(got, "\x1b") || strings.Contains(got, "CẢNH BÁO") || !strings.HasPrefix(got, "cảnh báo: linger đang TẮT") ||
		!strings.Contains(got, "sudo loginctl enable-linger $USER\n") || strings.Contains(got, "$USER.") {
		t.Fatalf("không TTY: %q", got)
	}
	// Có TTY: vẫn chứa đủ nội dung (màu do lipgloss quyết theo terminal).
	b.Reset()
	printLingerWarning(&b, autoupdate.LingerWarning, true)
	if !strings.Contains(b.String(), "CẢNH BÁO: linger đang TẮT") || !strings.Contains(b.String(), "sudo loginctl enable-linger $USER") {
		t.Fatalf("TTY: %q", b.String())
	}
}

// ─── F-96/F-98: runUpdate lịch đêm chọn bản 25 giờ, re-exec con ──────────────

func TestRunUpdateNightlyPicks25hRelease(t *testing.T) {
	now := time.Now().UTC().Truncate(time.Second)
	asset := selfupdate.AssetName(runtime.GOOS, runtime.GOARCH)
	bin53 := []byte("genh binary v0.1.53")
	mux := http.NewServeMux()
	meta := func(tag string, age time.Duration) map[string]any {
		return map[string]any{"tag_name": tag, "published_at": now.Add(-age).Format(time.RFC3339),
			"body": "notes\n" + selfupdate.PromotedMarker(now.Add(-age))}
	}
	var mu sync.Mutex
	var paths []string
	mux.HandleFunc("/repos/o/r/releases", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode([]map[string]any{meta("v0.1.54", time.Hour), meta("v0.1.53", 25*time.Hour)})
	})
	mux.HandleFunc("/o/r/releases/download/v0.1.53/checksums.txt", func(w http.ResponseWriter, _ *http.Request) {
		_, _ = fmt.Fprintf(w, "%s  %s\n", sha256Hex(bin53), asset)
	})
	mux.HandleFunc("/o/r/releases/download/v0.1.53/"+asset, func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write(bin53) })
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		paths = append(paths, r.URL.RequestURI())
		mu.Unlock()
		mux.ServeHTTP(w, r)
	}))
	defer srv.Close()

	dir := installDirAlive(t)
	exe := filepath.Join(t.TempDir(), "genh")
	if err := os.WriteFile(exe, []byte("genh cu"), 0o755); err != nil {
		t.Fatal(err)
	}
	oldVer, oldTweak, oldExec := version, selfUpdateTweak, reExecChild
	defer func() { version, selfUpdateTweak, reExecChild = oldVer, oldTweak, oldExec }()
	version = "v0.1.52"
	selfUpdateTweak = func(o *selfupdate.Options) {
		o.Owner, o.Repo = "o", "r"
		o.APIBase, o.DownloadBase = srv.URL, srv.URL
		o.ExecutablePath = exe
		o.Now = func() time.Time { return now }
	}
	var childArgs []string
	var childPath string
	reExecChild = func(_ context.Context, c *exec.Cmd) error {
		childArgs, childPath = c.Args, c.Path
		return nil
	}

	var code int
	out, _ := captureStd(t, func() { code = runUpdate([]string{"--yes", "--quiet", "--install-dir", dir}) })
	if code != 0 {
		t.Fatalf("thoát %d, muốn 0\n%s", code, out)
	}
	if b, _ := os.ReadFile(exe); string(b) != string(bin53) {
		t.Fatalf("binary phải là asset v0.1.53, được %q", b)
	}
	mu.Lock()
	defer mu.Unlock()
	for _, p := range paths {
		if strings.Contains(p, "download/v0.1.54") {
			t.Errorf("không được tải v0.1.54 (mới 1 giờ): %v", paths)
		}
	}
	if childPath != exe || len(childArgs) < 2 || childArgs[1] != "update" {
		t.Fatalf("con phải là `<genh mới> update …`: path=%q args=%v", childPath, childArgs)
	}
	joined := strings.Join(childArgs, " ")
	for _, want := range []string{"--yes", "--quiet", "--install-dir " + dir, "--self-updated"} {
		if !strings.Contains(joined, want) {
			t.Errorf("args con thiếu %q: %v", want, childArgs)
		}
	}
	// Lịch đêm ghi nightly-status: bắt đầu + kết quả done.
	ns := readNightly(t, dir)
	if ns.LastResult != hostlink.NightlyResultDone || ns.LastRunAt == "" {
		t.Errorf("nightly-status = %+v", ns)
	}
	// MỌI lối thoát của tiến trình ngoài cùng đều ghi autostart-status.
	if _, err := hostlink.ReadAutostartStatus(dir); err != nil {
		t.Errorf("runUpdate phải ghi run/autostart-status.json: %v", err)
	}
}

func sha256Hex(b []byte) string {
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

// ─── F-97: yêu cầu không xoá được / chờ khoá hết hạn ────────────────────────

func writeRequest(t *testing.T, path, body string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(body), 0o666); err != nil {
		t.Fatal(err)
	}
}

// Dựng "tệp" yêu cầu KHÔNG xoá được kể cả khi chạy bằng root: một thư mục không rỗng.
func writeUndeletable(t *testing.T, path string) {
	t.Helper()
	if err := os.MkdirAll(path, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(path, "giu-lai"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestRunUpdate_IfRequested_XoaLoi_GHE94C_KhongLamKhongGhiLai(t *testing.T) {
	dir := lockTestInstall(t)
	writeRequest(t, hostlink.RequestPath(dir), `{"requested_at":"2026-10-10T01:00:00Z","by":"owner"}`)
	old := consumeRequest
	defer func() { consumeRequest = old }()
	calls := 0
	consumeRequest = func(string) (bool, error) {
		calls++
		return false, fmt.Errorf("%w: %w", hostlink.ErrRequestUndeletable, syscall.EACCES)
	}

	run := func() (int, string, string) {
		var code int
		out, errOut := captureStd(t, func() {
			code = runUpdate([]string{"--yes", "--if-requested", "--no-self-update", "--install-dir", dir})
		})
		return code, out, errOut
	}
	code, _, errOut := run()
	if code != 0 {
		t.Fatalf("thoát %d, muốn 0", code)
	}
	st, err := hostlink.ReadStatus(dir)
	wantMsg := "Không xoá được yêu cầu cập nhật trong run/request — máy chủ không làm yêu cầu này để tránh chạy lặp. Kiểm quyền thư mục run/request trên máy chủ rồi bấm Thử lại (GH-E94C)"
	if err != nil || st.State != "failed" || st.Message != wantMsg || st.RequestedAt != "2026-10-10T01:00:00Z" {
		t.Fatalf("update-status = %+v, %v", st, err)
	}
	if !strings.Contains(errOut, "GH-E94C") || strings.Contains(st.Message, dir) {
		t.Errorf("lỗi gốc chỉ ở stderr, thông điệp Console không chứa đường dẫn: stderr=%q msg=%q", errOut, st.Message)
	}
	if calls != 1 {
		t.Errorf("consumeRequest gọi %d lần", calls)
	}
	if !hostlink.HasRequest(dir) {
		t.Error("tệp yêu cầu không xoá được vẫn còn")
	}
	raw1 := readFileStr(t, filepath.Join(hostlink.Dir(dir), hostlink.StatusFile))

	// Lần gọi sau (path unit kích lại) — cùng requested_at ⇒ thoát 0, KHÔNG ghi lại, không cố xoá nữa.
	time.Sleep(1100 * time.Millisecond)
	code, _, _ = run()
	if code != 0 {
		t.Fatalf("lần 2 thoát %d", code)
	}
	if raw2 := readFileStr(t, filepath.Join(hostlink.Dir(dir), hostlink.StatusFile)); raw2 != raw1 {
		t.Errorf("update-status.json bị ghi lại ở lần 2:\n%s\n---\n%s", raw1, raw2)
	}
	if calls != 1 {
		t.Errorf("lần 2 không được thử xoá/làm lại (consumeRequest=%d)", calls)
	}
}

// Lịch đêm/gõ tay gặp yêu cầu kẹt: chỉ cảnh báo, vẫn cập nhật như thường (không để một tệp kẹt chặn cập nhật).
func TestRunUpdate_KhongIfRequested_XoaLoi_VanChayCapNhat(t *testing.T) {
	dir := lockTestInstall(t)
	old := consumeRequest
	defer func() { consumeRequest = old }()
	consumeRequest = func(string) (bool, error) { return false, hostlink.ErrRequestUndeletable }
	var code int
	_, errOut := captureStd(t, func() { code = runUpdate([]string{"--no-self-update", "--install-dir", dir}) })
	if !strings.Contains(errOut, "GH-E94C") {
		t.Errorf("phải cảnh báo GH-E94C trên stderr: %q", errOut)
	}
	// RunUpdate thật chạy (bản cài giả không có docker) ⇒ không phải thoát 0 sớm kiểu 'không làm yêu cầu'.
	if st, _ := hostlink.ReadStatus(dir); st.State == "failed" && strings.Contains(st.Message, "GH-E94C") && code == 0 {
		t.Errorf("gõ tay không được dừng sớm vì tệp yêu cầu kẹt: code=%d %+v", code, st)
	}
}

// Chờ khoá đủ requestLockWait mà máy chủ vẫn bận ⇒ xoá yêu cầu + failed GH-E94A (không để path unit kích lặp mãi).
func TestRunUpdate_IfRequested_ChoKhoaHetHan_XoaYeuCauVaBaoGHE94A(t *testing.T) {
	old := requestLockWait
	requestLockWait = 400 * time.Millisecond
	defer func() { requestLockWait = old }()
	dir := lockTestInstall(t)
	held, err := hostlink.AcquireLock(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer held.Release()

	var code int
	out, _ := captureStd(t, func() {
		code = runUpdate([]string{"--yes", "--if-requested", "--no-self-update", "--install-dir", dir})
	})
	if code != 0 {
		t.Fatalf("thoát %d, muốn 0", code)
	}
	if hostlink.HasRequest(dir) {
		t.Fatal("hết hạn chờ khoá: tệp yêu cầu phải bị xoá")
	}
	st, err := hostlink.ReadStatus(dir)
	want := "Máy chủ bận một lần cập nhật/khôi phục khác quá 30 phút — chưa làm yêu cầu. Bấm Thử lại sau (GH-E94A)"
	if err != nil || st.State != "failed" || st.Message != want {
		t.Fatalf("update-status = %+v, %v", st, err)
	}
	if !strings.Contains(out, "đang có một lần cập nhật/khôi phục khác chạy") {
		t.Errorf("log: %q", out)
	}
}

func TestRunRestore_IfRequested_ChoKhoaHetHan_VaXoaLoi(t *testing.T) {
	old := requestLockWait
	requestLockWait = 300 * time.Millisecond
	defer func() { requestLockWait = old }()
	dir := lockTestInstall(t)
	key := "backups/20260929T010203Z-abcdef12.pgcustom.enc"
	writeRequest(t, hostlink.RestoreRequestPath(dir), `{"key":"`+key+`","requested_at":"2026-10-10T01:00:00Z"}`)
	held, err := hostlink.AcquireLock(dir)
	if err != nil {
		t.Fatal(err)
	}
	var code int
	captureStd(t, func() { code = runRestore([]string{"--if-requested", "--install-dir", dir}) })
	held.Release()
	if code != 0 || hostlink.HasRestoreRequest(dir) {
		t.Fatalf("hết hạn chờ khoá: code=%d còn tệp=%v", code, hostlink.HasRestoreRequest(dir))
	}
	st, err := hostlink.ReadRestoreStatus(dir)
	if err != nil || st.State != "failed" || !strings.Contains(st.Message, "GH-E94A") {
		t.Fatalf("restore-status = %+v, %v", st, err)
	}

	// Yêu cầu khôi phục KHÔNG xoá được (thư mục không rỗng) ⇒ failed GH-E94C, thoát 0, lần 2 không ghi lại.
	if err := os.RemoveAll(hostlink.RestoreRequestPath(dir)); err != nil {
		t.Fatal(err)
	}
	writeUndeletable(t, hostlink.RestoreRequestPath(dir))
	captureStd(t, func() { code = runRestore([]string{"--if-requested", "--install-dir", dir}) })
	st, _ = hostlink.ReadRestoreStatus(dir)
	if code != 0 || st.State != "failed" || !strings.Contains(st.Message, "GH-E94C") || !strings.Contains(st.Message, "yêu cầu khôi phục") {
		t.Fatalf("restore xoá lỗi: code=%d %+v", code, st)
	}
	raw1 := readFileStr(t, filepath.Join(hostlink.Dir(dir), hostlink.RestoreStatusFile))
	time.Sleep(1100 * time.Millisecond)
	captureStd(t, func() { code = runRestore([]string{"--if-requested", "--install-dir", dir}) })
	if raw2 := readFileStr(t, filepath.Join(hostlink.Dir(dir), hostlink.RestoreStatusFile)); code != 0 || raw2 != raw1 {
		t.Errorf("lần 2 không được ghi lại: code=%d\n%s\n---\n%s", code, raw1, raw2)
	}
}

// doctor / offsite / watchdog: tệp yêu cầu không xoá được ⇒ không làm, báo failed GH-E94C, thoát 0.
func TestHandleRequests_XoaLoi_DoctorOffsiteWatchdog(t *testing.T) {
	dir := lockTestInstall(t)
	_ = os.Remove(hostlink.RequestPath(dir)) // chỉ giữ yêu cầu đang thử
	run := func() int {
		var code int
		captureStd(t, func() { code = runHandleRequests([]string{"--quiet", "--install-dir", dir}) })
		return code
	}

	// doctor.json
	writeUndeletable(t, hostlink.DoctorRequestPath(dir))
	if code := run(); code != 0 {
		t.Fatalf("doctor: thoát %d", code)
	}
	ds, err := hostlink.ReadDoctorStatus(dir)
	if err != nil || ds.State != "failed" || ds.ErrorCode != "GH-E94C" || !strings.Contains(ds.Message, "yêu cầu gói chẩn đoán") {
		t.Fatalf("doctor-status = %+v, %v", ds, err)
	}
	raw1 := readFileStr(t, hostlink.DoctorStatusPath(dir))
	time.Sleep(1100 * time.Millisecond)
	run()
	if raw2 := readFileStr(t, hostlink.DoctorStatusPath(dir)); raw2 != raw1 {
		t.Errorf("doctor-status bị ghi lại ở lần 2:\n%s\n---\n%s", raw1, raw2)
	}
	if err := os.RemoveAll(hostlink.DoctorRequestPath(dir)); err != nil {
		t.Fatal(err)
	}

	// offsite.json
	writeUndeletable(t, hostlink.OffsiteRequestPath(dir))
	if code := run(); code != 0 {
		t.Fatalf("offsite: thoát %d", code)
	}
	os1, err := hostlink.ReadOffsiteStatus(dir)
	if err != nil || os1.State != hostlink.OffsiteStateFailed || os1.ErrorCode != "GH-E94C" {
		t.Fatalf("offsite-status = %+v, %v", os1, err)
	}
	rawO1 := readFileStr(t, hostlink.OffsiteStatusPath(dir))
	time.Sleep(1100 * time.Millisecond)
	if code := run(); code != 0 {
		t.Fatalf("offsite lần 2: thoát %d", code)
	}
	if rawO2 := readFileStr(t, hostlink.OffsiteStatusPath(dir)); rawO2 != rawO1 {
		t.Errorf("offsite-status bị ghi lại ở lần 2:\n%s\n---\n%s", rawO1, rawO2)
	}
	if err := os.RemoveAll(hostlink.OffsiteRequestPath(dir)); err != nil {
		t.Fatal(err)
	}

	// watchdog.json
	writeUndeletable(t, hostlink.WatchdogRequestPath(dir))
	if code := run(); code != 0 {
		t.Fatalf("watchdog: thoát %d", code)
	}
	ws, err := hostlink.ReadWatchdogStatus(dir)
	if err != nil || ws.Test == nil || ws.Test.OK || ws.Test.ErrorCode != "GH-E94C" {
		t.Fatalf("watchdog-status = %+v, %v", ws, err)
	}
}

// Không xác định được thư mục cài đặt (không --install-dir, không GEN_HARNESS_HOME, không HOME):
// in lỗi, thoát 1 — không panic.
func TestHandleRequests_KhongXacDinhDuocThuMucCaiDat(t *testing.T) {
	t.Setenv("GEN_HARNESS_HOME", "")
	t.Setenv("HOME", "")
	// Windows: config.DefaultRoot đọc LOCALAPPDATA rồi USERPROFILE (os.UserHomeDir), không đọc HOME.
	t.Setenv("LOCALAPPDATA", "")
	t.Setenv("USERPROFILE", "")
	var code int
	_, errOut := captureStd(t, func() { code = runHandleRequests([]string{"--quiet"}) })
	if code != 1 || !strings.Contains(errOut, "không xác định được thư mục cài đặt") {
		t.Fatalf("code=%d stderr=%q", code, errOut)
	}
}

// Lịch đêm chạy lỗi sớm (bản cài giả không có docker/bí mật) vẫn để lại nightly-status ĐÚNG: ảnh chụp
// lịch (đang bật) được ghi TRƯỚC khi chạy, kết quả "failed" ghi sau — không phải bản ghi "lịch tắt".
func TestRunUpdate_LichDem_GhiNightlyStatusKeCaKhiLoi(t *testing.T) {
	rr := healthyRules(&cmdRunner{})
	useHost(t, rr)
	dir := lockTestInstall(t)
	var code int
	captureStd(t, func() { code = runUpdate([]string{"--yes", "--quiet", "--no-self-update", "--install-dir", dir}) })
	if code == 0 {
		t.Fatalf("bản cài giả phải làm RunUpdate lỗi, thoát %d", code)
	}
	ns := readNightly(t, dir)
	if ns.LastResult != hostlink.NightlyResultFailed || ns.LastRunAt == "" {
		t.Fatalf("last_result/last_run_at: %+v", ns)
	}
	if !ns.Enabled || ns.Mechanism != "systemd" || ns.Since == "" {
		t.Errorf("phải có ảnh chụp lịch đang bật từ trước khi chạy: %+v", ns)
	}
	// Chạy tay (không --yes) KHÔNG ghi last_run_at/last_result của lịch đêm.
	before := readNightly(t, dir)
	time.Sleep(1100 * time.Millisecond)
	captureStd(t, func() { runUpdate([]string{"--no-self-update", "--install-dir", dir}) })
	if after := readNightly(t, dir); after.LastRunAt != before.LastRunAt || after.LastResult != before.LastResult {
		t.Errorf("chạy tay không được đổi kết quả lịch đêm: %+v -> %+v", before, after)
	}
	// Gõ tay `genh update --yes` trong terminal (stdout là TTY) cũng KHÔNG phải lần chạy của lịch:
	// tính vào thì timer hỏng vẫn hiện "Bình thường" thêm 36 giờ.
	old := stdoutIsTerminal
	stdoutIsTerminal = func() bool { return true }
	t.Cleanup(func() { stdoutIsTerminal = old })
	captureStd(t, func() { runUpdate([]string{"--yes", "--no-self-update", "--install-dir", dir}) })
	if after := readNightly(t, dir); after.LastRunAt != before.LastRunAt || after.LastResult != before.LastResult {
		t.Errorf("`genh update --yes` gõ tay không được đổi kết quả lịch đêm: %+v -> %+v", before, after)
	}
}
