package ops

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/autoupdate"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
)

// TestMain đặt HOME về thư mục tạm cho MỌI test của gói ops: RunUninstall đọc unit
// systemd --user (~/.config/systemd/user) để biết lịch thuộc bản cài nào, và gỡ lối tắt/
// dòng PATH trong HOME — test không được chạm HOME thật của máy dev.
func TestMain(m *testing.M) {
	home, err := os.MkdirTemp("", "genh-ops-home-*")
	if err != nil {
		panic(err)
	}
	_ = os.Setenv("HOME", home)
	_ = os.Setenv("USERPROFILE", home) // os.UserHomeDir trên Windows
	code := m.Run()
	_ = os.RemoveAll(home)
	os.Exit(code)
}

// fakeOffsiteScheduler ghi lại lời gọi bật/tắt lịch tuần (không đụng systemd/cron thật).
type fakeOffsiteScheduler struct {
	enabled   bool
	mechanism string
	enableErr error
	enables   int
	disables  int
}

func (f *fakeOffsiteScheduler) Enable(context.Context) (string, string, error) {
	f.enables++
	if f.enableErr != nil {
		return "", "", f.enableErr
	}
	f.enabled = true
	if f.mechanism == "" {
		f.mechanism = autoupdate.ScheduleCron
	}
	return "Đã bật lịch sao lưu ra ổ ngoài mỗi Chủ nhật ~05:30 (giả)", f.mechanism, nil
}

func (f *fakeOffsiteScheduler) Disable(context.Context) (string, error) {
	f.disables++
	f.enabled = false
	return "Đã tắt lịch sao lưu ra ổ ngoài hằng tuần.", nil
}

func (f *fakeOffsiteScheduler) Status(context.Context) (autoupdate.OffsiteScheduleStatus, error) {
	if !f.enabled {
		return autoupdate.OffsiteScheduleStatus{Detail: "chưa bật"}, nil
	}
	return autoupdate.OffsiteScheduleStatus{Enabled: true, Mechanism: f.mechanism, Detail: "giả"}, nil
}

// fakeWatchdogScheduler ghi lại lời gọi gỡ lịch trực canh (không đụng systemd/cron thật).
type fakeWatchdogScheduler struct {
	disables int
	err      error
}

func (f *fakeWatchdogScheduler) Enable(context.Context) (string, string, error) {
	return "Đã bật trực canh (giả)", autoupdate.ScheduleCron, nil
}

func (f *fakeWatchdogScheduler) Disable(context.Context) (string, error) {
	f.disables++
	if f.err != nil {
		return "", f.err
	}
	return "Đã tắt trực canh máy chủ (giả).", nil
}

func (f *fakeWatchdogScheduler) Status(context.Context) (autoupdate.WatchdogSchedule, error) {
	return autoupdate.WatchdogSchedule{}, nil
}

// v0.1.44 (F-6b): uninstall gỡ lịch trực canh cạnh lịch tuần offsite — lỗi chỉ cảnh báo.
func TestRunUninstall_GoLichTrucCanh(t *testing.T) {
	env := testEnv(t, testComposePath(t, ""))
	wd := &fakeWatchdogScheduler{}
	var out strings.Builder
	if err := RunUninstall(context.Background(), env, UninstallOptions{AutoApprove: true, Watchdog: wd, Offsite: &fakeOffsiteScheduler{}}, downRunner(), strings.NewReader(""), &out); err != nil {
		t.Fatal(err)
	}
	if wd.disables != 1 || !strings.Contains(out.String(), "Đã tắt trực canh máy chủ (giả).") {
		t.Fatalf("phải gỡ lịch trực canh: %d lần, out=%q", wd.disables, out.String())
	}
	wd2 := &fakeWatchdogScheduler{err: errors.New("crontab hỏng")}
	out.Reset()
	if err := RunUninstall(context.Background(), env, UninstallOptions{AutoApprove: true, Watchdog: wd2, Offsite: &fakeOffsiteScheduler{}}, downRunner(), strings.NewReader(""), &out); err != nil {
		t.Fatalf("gỡ lịch trực canh lỗi không được làm hỏng uninstall: %v", err)
	}
	if !strings.Contains(out.String(), "Không gỡ được lịch trực canh máy chủ") {
		t.Fatalf("thiếu cảnh báo: %q", out.String())
	}
}

// Đánh dấu tạm dừng phải có TRƯỚC `docker compose down` — lượt trực canh rơi
// vào khe giữa down và gỡ lịch không được `up -d` lại api.
func TestRunUninstall_TamDungTrucCanhTruocKhiDown(t *testing.T) {
	env := testEnv(t, testComposePath(t, ""))
	pausedAtDown := false
	fr := &fake.Runner{Responses: []fake.Response{{Match: func(cmd dockercli.Cmd) bool {
		if strings.Contains(strings.Join(cmd.Args, " "), "down") {
			pausedAtDown = OwnerPaused(env.InstallDir)
			return true
		}
		return false
	}}}}
	if err := RunUninstall(context.Background(), env, UninstallOptions{AutoApprove: true, Watchdog: &fakeWatchdogScheduler{}, Offsite: &fakeOffsiteScheduler{}}, fr, strings.NewReader(""), &strings.Builder{}); err != nil {
		t.Fatal(err)
	}
	if !pausedAtDown {
		t.Fatal("phải ghi paused-by-owner.json trước `docker compose down`")
	}
	if !OwnerPaused(env.InstallDir) {
		t.Fatal("sau khi gỡ, đánh dấu tạm dừng phải còn (lịch sót lại không dựng lại api)")
	}
}

func hasVolumes(args []string) bool {
	for _, a := range args {
		if a == "--volumes" {
			return true
		}
	}
	return false
}

func downRunner() *fake.Runner {
	return &fake.Runner{Responses: []fake.Response{{Match: fake.MatchArgsContain("down"), Output: []byte("")}}}
}

// v0.1.40: mặc định GIỮ dữ liệu — không có --volumes; gỡ luôn lịch tuần offsite.
func TestRunUninstall_MacDinh_GiuDuLieu_KhongVolumes(t *testing.T) {
	env := testEnv(t, testComposePath(t, ""))
	fr := downRunner()
	sched := &fakeOffsiteScheduler{enabled: true, mechanism: "cron"}
	var out strings.Builder
	if err := RunUninstall(context.Background(), env, UninstallOptions{AutoApprove: true, Watchdog: &fakeWatchdogScheduler{}, Offsite: sched}, fr, strings.NewReader(""), &out); err != nil {
		t.Fatalf("RunUninstall: %v", err)
	}
	if len(fr.Calls) != 1 || hasVolumes(fr.Calls[0].Cmd.Args) {
		t.Fatalf("mặc định KHÔNG được --volumes, calls=%+v", fr.Calls)
	}
	if sched.disables != 1 {
		t.Fatalf("uninstall phải gỡ lịch tuần offsite (Disable gọi %d lần)", sched.disables)
	}
	if strings.Contains(out.String(), "Chưa có bản sao ngoài máy gần đây") {
		t.Fatal("giữ dữ liệu thì không cần cảnh báo bản sao ngoài máy")
	}
	// Volume giữ lại khoá bằng mật khẩu trong thư mục cài ⇒ phải dặn cài lại đúng thư mục, xoá thư mục thì xoá dữ liệu trước.
	if !strings.Contains(out.String(), "cài lại phải dùng đúng thư mục này (--install-dir "+env.InstallDir+")") ||
		!strings.Contains(out.String(), "genh uninstall --delete-data") {
		t.Fatalf("thiếu lời dặn giữ thư mục cài khi giữ dữ liệu, out=%q", out.String())
	}
}

// --keep-data vẫn hợp lệ (script cũ) — giữ dữ liệu như mặc định.
func TestRunUninstall_KeepData_VanHopLe(t *testing.T) {
	env := testEnv(t, testComposePath(t, ""))
	fr := downRunner()
	if err := RunUninstall(context.Background(), env, UninstallOptions{KeepData: true, AutoApprove: true, Watchdog: &fakeWatchdogScheduler{}, Offsite: &fakeOffsiteScheduler{}}, fr, strings.NewReader(""), &strings.Builder{}); err != nil {
		t.Fatalf("RunUninstall: %v", err)
	}
	if hasVolumes(fr.Calls[0].Cmd.Args) {
		t.Errorf("không được truyền --volumes khi --keep-data, args=%v", fr.Calls[0].Cmd.Args)
	}
}

// --delete-data --yes (CI): có --volumes; chưa có bản sao ngoài máy gần đây → cảnh báo đỏ.
func TestRunUninstall_DeleteData_Yes_CoVolumesVaCanhBao(t *testing.T) {
	env := testEnv(t, testComposePath(t, ""))
	fr := downRunner()
	var out strings.Builder
	if err := RunUninstall(context.Background(), env, UninstallOptions{DeleteData: true, AutoApprove: true, Watchdog: &fakeWatchdogScheduler{}, Offsite: &fakeOffsiteScheduler{}}, fr, strings.NewReader(""), &out); err != nil {
		t.Fatalf("RunUninstall: %v", err)
	}
	if !hasVolumes(fr.Calls[0].Cmd.Args) {
		t.Errorf("--delete-data phải truyền --volumes, args=%v", fr.Calls[0].Cmd.Args)
	}
	if !strings.Contains(out.String(), "Chưa có bản sao ngoài máy gần đây") || !strings.Contains(out.String(), ansiRed) {
		t.Fatalf("thiếu cảnh báo đỏ:\n%s", out.String())
	}

	// Có bản sao thành công 2 ngày trước → không cảnh báo.
	now := time.Date(2026, 10, 4, 6, 0, 0, 0, time.UTC)
	if err := hostlink.WriteOffsiteStatus(env.InstallDir, hostlink.OffsiteStatus{State: "ok", LastSuccessAt: "2026-10-02T05:40:00Z"}); err != nil {
		t.Fatal(err)
	}
	out.Reset()
	if err := RunUninstall(context.Background(), env, UninstallOptions{DeleteData: true, AutoApprove: true, Watchdog: &fakeWatchdogScheduler{}, Offsite: &fakeOffsiteScheduler{}, Now: func() time.Time { return now }},
		downRunner(), strings.NewReader(""), &out); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(out.String(), "Chưa có bản sao ngoài máy gần đây") {
		t.Fatalf("có bản sao 2 ngày trước thì không cảnh báo:\n%s", out.String())
	}
	// Quá 7 ngày → cảnh báo lại.
	out.Reset()
	later := func() time.Time { return now.Add(9 * 24 * time.Hour) }
	if err := RunUninstall(context.Background(), env, UninstallOptions{DeleteData: true, AutoApprove: true, Watchdog: &fakeWatchdogScheduler{}, Offsite: &fakeOffsiteScheduler{}, Now: later},
		downRunner(), strings.NewReader(""), &out); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out.String(), "Chưa có bản sao ngoài máy gần đây") {
		t.Fatalf("bản sao 11 ngày trước phải cảnh báo:\n%s", out.String())
	}
}

// --delete-data KHÔNG --yes: phải gõ đúng "XOÁ DỮ LIỆU"; gõ "y" là huỷ.
func TestRunUninstall_DeleteData_PhaiGoDungCumTu(t *testing.T) {
	env := testEnv(t, testComposePath(t, ""))
	for _, typed := range []string{"y\n", "yes\n", "xoá dữ liệu\n", "\n"} {
		fr := &fake.Runner{}
		err := RunUninstall(context.Background(), env, UninstallOptions{DeleteData: true, Watchdog: &fakeWatchdogScheduler{}, Offsite: &fakeOffsiteScheduler{}}, fr, strings.NewReader(typed), &strings.Builder{})
		var opErr *OpError
		if !errors.As(err, &opErr) || opErr.Code != ErrCodeUninstallCancelled || len(fr.Calls) != 0 {
			t.Fatalf("gõ %q phải huỷ, không gọi docker: err=%v calls=%d", typed, err, len(fr.Calls))
		}
	}
	for _, typed := range []string{"XOÁ DỮ LIỆU\n", "  XÓA DỮ LIỆU  \n"} {
		fr := downRunner()
		if err := RunUninstall(context.Background(), env, UninstallOptions{DeleteData: true, Watchdog: &fakeWatchdogScheduler{}, Offsite: &fakeOffsiteScheduler{}}, fr, strings.NewReader(typed), &strings.Builder{}); err != nil {
			t.Fatalf("gõ %q phải chạy: %v", typed, err)
		}
		if !hasVolumes(fr.Calls[0].Cmd.Args) {
			t.Fatalf("đã xác nhận xoá dữ liệu phải có --volumes: %v", fr.Calls[0].Cmd.Args)
		}
	}
}

func TestRunUninstall_KeepVaDeleteMauThuan(t *testing.T) {
	env := testEnv(t, testComposePath(t, ""))
	fr := &fake.Runner{}
	err := RunUninstall(context.Background(), env, UninstallOptions{DeleteData: true, KeepData: true, AutoApprove: true, Watchdog: &fakeWatchdogScheduler{}, Offsite: &fakeOffsiteScheduler{}}, fr, strings.NewReader(""), &strings.Builder{})
	var opErr *OpError
	if !errors.As(err, &opErr) || len(fr.Calls) != 0 {
		t.Fatalf("hai cờ mâu thuẫn phải dừng trước khi gọi docker: %v", err)
	}
}

func TestRunUninstall_NoConfirmation_Cancels_DoesNotCallDown(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	fr := &fake.Runner{}
	sched := &fakeOffsiteScheduler{}

	err := RunUninstall(context.Background(), env, UninstallOptions{Watchdog: &fakeWatchdogScheduler{}, Offsite: sched}, fr, strings.NewReader("n\n"), &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T", err)
	}
	if opErr.Code != ErrCodeUninstallCancelled {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeUninstallCancelled)
	}
	if len(fr.Calls) != 0 || sched.disables != 0 {
		t.Error("không được gọi docker compose down / gỡ lịch khi huỷ xác nhận")
	}
	// Giữ dữ liệu (mặc định): "y" là đủ.
	fr2 := downRunner()
	if err := RunUninstall(context.Background(), env, UninstallOptions{Watchdog: &fakeWatchdogScheduler{}, Offsite: sched}, fr2, strings.NewReader("y\n"), &strings.Builder{}); err != nil {
		t.Fatalf("giữ dữ liệu + y phải chạy: %v", err)
	}
}

func TestRunUninstall_DownFails_ReturnsOpError(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	fr := &fake.Runner{Responses: []fake.Response{{Match: fake.MatchArgsContain("down"), Err: errors.New("boom")}}}

	err := RunUninstall(context.Background(), env, UninstallOptions{AutoApprove: true, Watchdog: &fakeWatchdogScheduler{}, Offsite: &fakeOffsiteScheduler{}}, fr, strings.NewReader(""), &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T", err)
	}
	if opErr.Code != ErrCodeUninstallFailed {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeUninstallFailed)
	}
}

func TestRunUninstall_RemovesShortcutAndPathLine_Linux(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("chỉ kiểm đường dẫn lối tắt/PATH thật trên GOOS=linux")
	}
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	fr := downRunner()

	fakeHome := t.TempDir()
	t.Setenv("HOME", fakeHome)
	t.Setenv("SHELL", "/bin/bash")

	// Lối tắt "đã có" từ một lần genh install trước đó.
	shortcutDir := filepath.Join(fakeHome, ".local", "share", "applications")
	if err := os.MkdirAll(shortcutDir, 0o755); err != nil {
		t.Fatalf("MkdirAll: %v", err)
	}
	shortcutPath := filepath.Join(shortcutDir, "gen-harness.desktop")
	if err := os.WriteFile(shortcutPath, []byte("[Desktop Entry]\n"), 0o644); err != nil {
		t.Fatalf("WriteFile shortcut: %v", err)
	}

	// .bashrc "đã có" dòng PATH mà install.sh thêm.
	rcPath := filepath.Join(fakeHome, ".bashrc")
	rcContent := "existing line 1\n\n# Gen-Harness (genh)\nexport PATH=\"" + fakeHome + "/.gen-harness/bin:$PATH\"\nexisting line 2\n"
	if err := os.WriteFile(rcPath, []byte(rcContent), 0o644); err != nil {
		t.Fatalf("WriteFile rc: %v", err)
	}

	var out strings.Builder
	if err := RunUninstall(context.Background(), env, UninstallOptions{AutoApprove: true, Watchdog: &fakeWatchdogScheduler{}, Offsite: &fakeOffsiteScheduler{}}, fr, strings.NewReader(""), &out); err != nil {
		t.Fatalf("RunUninstall: %v", err)
	}

	if _, err := os.Stat(shortcutPath); !os.IsNotExist(err) {
		t.Errorf("lối tắt %s phải bị xoá, err=%v", shortcutPath, err)
	}

	gotRC, err := os.ReadFile(rcPath)
	if err != nil {
		t.Fatalf("đọc lại %s: %v", rcPath, err)
	}
	if strings.Contains(string(gotRC), "Gen-Harness") {
		t.Errorf("rc file vẫn còn dòng Gen-Harness sau uninstall: %q", gotRC)
	}
	if !strings.Contains(string(gotRC), "existing line 1") || !strings.Contains(string(gotRC), "existing line 2") {
		t.Errorf("rc file phải giữ nguyên các dòng khác, được %q", gotRC)
	}
}

// v0.1.53 (F-98): lịch trực canh/bản sao ngoài máy là CHUNG cho mọi bản cài — gỡ bản cài PHỤ
// không được gỡ lịch của bản chính còn sống.
func TestRunUninstall_BanPhu_KhongGoLichCuaBanChinh(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)                 // os.UserHomeDir trên Windows đọc USERPROFILE, không đọc HOME
	mainEnv := testEnv(t, testComposePath(t, "")) // có config/secrets.json = còn sống
	otherEnv := testEnv(t, testComposePath(t, ""))
	unitDir := filepath.Join(home, ".config", "systemd", "user")
	if err := os.MkdirAll(unitDir, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{autoupdate.WatchdogTaskName, autoupdate.OffsiteTaskName} {
		unit := "[Service]\nEnvironment=GEN_HARNESS_HOME=" + mainEnv.InstallDir + "\nExecStart=/g/genh x --install-dir " + mainEnv.InstallDir + "\n"
		if err := os.WriteFile(filepath.Join(unitDir, name+".service"), []byte(unit), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	wd, off := &fakeWatchdogScheduler{}, &fakeOffsiteScheduler{}
	var out strings.Builder
	if err := RunUninstall(context.Background(), otherEnv, UninstallOptions{AutoApprove: true, Watchdog: wd, Offsite: off}, downRunner(), strings.NewReader(""), &out); err != nil {
		t.Fatalf("RunUninstall bản phụ: %v", err)
	}
	if wd.disables != 0 || off.disables != 0 {
		t.Fatalf("gỡ bản phụ KHÔNG được gọi Disable lịch của bản chính: watchdog=%d offsite=%d", wd.disables, off.disables)
	}
	for _, want := range []string{
		"Giữ nguyên lịch trực canh máy chủ của bản cài " + mainEnv.InstallDir + " (không phải bản đang gỡ).",
		"Giữ nguyên lịch sao lưu ra ổ ngoài của bản cài " + mainEnv.InstallDir + " (không phải bản đang gỡ).",
	} {
		if !strings.Contains(out.String(), want) {
			t.Errorf("thiếu dòng %q:\n%s", want, out.String())
		}
	}

	// Gỡ chính bản chính thì vẫn gỡ lịch của nó.
	wd, off = &fakeWatchdogScheduler{}, &fakeOffsiteScheduler{}
	if err := RunUninstall(context.Background(), mainEnv, UninstallOptions{AutoApprove: true, Watchdog: wd, Offsite: off}, downRunner(), strings.NewReader(""), &strings.Builder{}); err != nil {
		t.Fatal(err)
	}
	if wd.disables != 1 || off.disables != 1 {
		t.Fatalf("gỡ bản chính phải gỡ lịch của nó: watchdog=%d offsite=%d", wd.disables, off.disables)
	}

	// Bản chủ đã gỡ hẳn (không còn secrets.json) thì bản khác được gỡ.
	if err := os.Remove(filepath.Join(mainEnv.InstallDir, "config", "secrets.json")); err != nil {
		t.Fatal(err)
	}
	wd, off = &fakeWatchdogScheduler{}, &fakeOffsiteScheduler{}
	if err := RunUninstall(context.Background(), otherEnv, UninstallOptions{AutoApprove: true, Watchdog: wd, Offsite: off}, downRunner(), strings.NewReader(""), &strings.Builder{}); err != nil {
		t.Fatal(err)
	}
	if wd.disables != 1 || off.disables != 1 {
		t.Fatalf("chủ lịch đã gỡ ⇒ bản khác được gỡ: watchdog=%d offsite=%d", wd.disables, off.disables)
	}
}
