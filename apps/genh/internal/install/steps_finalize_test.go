package install

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
)

// fakeCAPEM là nội dung CA giả (không cần là PEM hợp lệ — extractCaddyRootCert
// chỉ kiểm nội dung không rỗng, và finalizeStep chỉ ghi lại y nguyên bytes).
const fakeCAPEM = "-----BEGIN CERTIFICATE-----\nFAKE-GEN-HARNESS-CA\n-----END CERTIFICATE-----\n"

// finalizeTestEnv dựng Env có sẵn bí mật (Bước 4) VÀ InstallDir riêng dưới
// t.TempDir() — finalizeStep cần InstallDir thật để ghi
// <InstallDir>/config/caddy-root.crt.
func finalizeTestEnv(t *testing.T) *Env {
	t.Helper()
	env := testEnvWithSecrets(t)
	env.InstallDir = t.TempDir()
	env.Port = 18443
	return env
}

// fakeExtractRunner dựng fake.Runner chỉ khớp đúng lệnh
// `docker compose exec -T proxy cat <caddyRootCertPath>` — dùng cho mọi test
// không cố tình giả lập lỗi trích xuất CA.
func fakeExtractRunner(pem string, err error) *fake.Runner {
	return &fake.Runner{Responses: []fake.Response{
		{
			Match:  fake.MatchArgsContain("exec", "-T", "proxy", "cat", caddyRootCertPath),
			Output: []byte(pem),
			Err:    err,
		},
	}}
}

func noopTrustCA(context.Context, string) error      { return nil }
func noopTrustBrowser(context.Context, string) error { return errBrowserTrustUnsupported }
func noopOpenBrowser(string) error                   { return nil }
func noopCreateShortcut(string) (string, error)      { return "/tmp/fake-shortcut", nil }

func TestFinalizeStep_AutoApproveFalse_DoesNotCallTrustCA_AndSucceeds(t *testing.T) {
	composePath := testComposePath(t)
	fr := fakeExtractRunner(fakeCAPEM, nil)

	trustCACalled := false
	step := finalizeStep{
		runner: fr,
		locate: func(string) (string, error) { return composePath, nil },
		trustCA: func(context.Context, string) error {
			trustCACalled = true
			return nil
		},
		trustBrowser:   noopTrustBrowser,
		openBrowser:    noopOpenBrowser,
		createShortcut: noopCreateShortcut,
	}

	env := finalizeTestEnv(t)
	env.AutoApprove = false

	var progresses []Progress
	rep := ReporterFunc(func(p Progress) { progresses = append(progresses, p) })

	if err := step.Run(context.Background(), env, rep); err != nil {
		t.Fatalf("Run: %v", err)
	}
	if trustCACalled {
		t.Error("AutoApprove=false không được tự gọi trustCA (thao tác cần quyền rộng)")
	}

	last := progresses[len(progresses)-1]
	if last.Status != StatusOK && last.Status != StatusWarn {
		t.Errorf("Status cuối = %v, muốn StatusOK hoặc StatusWarn (không phải lỗi)", last.Status)
	}
	if last.Percent != 100 {
		t.Errorf("Percent cuối = %v, muốn 100", last.Percent)
	}

	// Phải có ít nhất một Progress giải thích rõ chưa tin cậy CA + gợi ý --yes.
	sawGuidance := false
	certPath := filepath.Join(env.InstallDir, "config", "caddy-root.crt")
	for _, p := range progresses {
		if strings.Contains(p.Detail, "--yes") && strings.Contains(p.Detail, certPath) {
			sawGuidance = true
		}
	}
	if !sawGuidance {
		t.Errorf("phải có Progress hướng dẫn Owner chạy lại kèm --yes hoặc tự cài %s, progresses=%+v", certPath, progresses)
	}

	// CA trích xuất được phải ghi đúng nội dung ra đĩa.
	got, err := os.ReadFile(certPath)
	if err != nil {
		t.Fatalf("đọc lại %s: %v", certPath, err)
	}
	if string(got) != fakeCAPEM {
		t.Errorf("nội dung CA ghi ra đĩa = %q, muốn %q", got, fakeCAPEM)
	}
}

func TestFinalizeStep_ExtractCAFails_ReturnsStructuredError(t *testing.T) {
	composePath := testComposePath(t)
	fr := fakeExtractRunner("", errors.New("container proxy không chạy"))

	step := finalizeStep{
		runner:         fr,
		locate:         func(string) (string, error) { return composePath, nil },
		trustCA:        noopTrustCA,
		trustBrowser:   noopTrustBrowser,
		openBrowser:    noopOpenBrowser,
		createShortcut: noopCreateShortcut,
	}

	err := step.Run(context.Background(), finalizeTestEnv(t), ReporterFunc(func(Progress) {}))
	se, ok := err.(*StepError)
	if !ok {
		t.Fatalf("lỗi phải là *StepError, được %T", err)
	}
	if se.Code != ErrCodeTrustCAFailed {
		t.Errorf("Code = %q, muốn %q", se.Code, ErrCodeTrustCAFailed)
	}
}

func TestFinalizeStep_MissingSecrets_ReturnsStructuredError(t *testing.T) {
	composePath := testComposePath(t)
	fr := fakeExtractRunner(fakeCAPEM, nil)

	step := finalizeStep{
		runner:         fr,
		locate:         func(string) (string, error) { return composePath, nil },
		trustCA:        noopTrustCA,
		trustBrowser:   noopTrustBrowser,
		openBrowser:    noopOpenBrowser,
		createShortcut: noopCreateShortcut,
	}

	err := step.Run(context.Background(), &Env{}, ReporterFunc(func(Progress) {}))
	se, ok := err.(*StepError)
	if !ok {
		t.Fatalf("lỗi phải là *StepError, được %T", err)
	}
	if se.Code != ErrCodeTrustCAFailed {
		t.Errorf("Code = %q, muốn %q", se.Code, ErrCodeTrustCAFailed)
	}
	if len(fr.Calls) != 0 {
		t.Error("không được gọi docker khi chưa có bí mật")
	}
}

func TestFinalizeStep_ComposeNotFound_ReturnsStructuredError(t *testing.T) {
	step := finalizeStep{
		locate:         func(string) (string, error) { return "", errors.New("không thấy") },
		trustCA:        noopTrustCA,
		trustBrowser:   noopTrustBrowser,
		openBrowser:    noopOpenBrowser,
		createShortcut: noopCreateShortcut,
	}
	err := step.Run(context.Background(), finalizeTestEnv(t), ReporterFunc(func(Progress) {}))
	se, ok := err.(*StepError)
	if !ok {
		t.Fatalf("lỗi phải là *StepError, được %T", err)
	}
	if se.Code != ErrCodeComposeNotFound {
		t.Errorf("Code = %q, muốn %q", se.Code, ErrCodeComposeNotFound)
	}
}

func TestFinalizeStep_AutoApproveTrue_TrustSucceeds_FinalStatusOK(t *testing.T) {
	composePath := testComposePath(t)
	fr := fakeExtractRunner(fakeCAPEM, nil)

	step := finalizeStep{
		runner:         fr,
		locate:         func(string) (string, error) { return composePath, nil },
		trustCA:        noopTrustCA,
		trustBrowser:   noopTrustBrowser,
		openBrowser:    noopOpenBrowser,
		createShortcut: noopCreateShortcut,
	}

	env := finalizeTestEnv(t)
	env.AutoApprove = true

	var last Progress
	rep := ReporterFunc(func(p Progress) { last = p })

	if err := step.Run(context.Background(), env, rep); err != nil {
		t.Fatalf("Run: %v", err)
	}
	if last.Status != StatusOK {
		t.Errorf("Status cuối = %v, muốn StatusOK khi CA/lối tắt/trình duyệt đều thành công", last.Status)
	}
	if !env.BrowserOpened {
		t.Error("env.BrowserOpened phải true khi openBrowser giả trả nil")
	}
}

func TestFinalizeStep_AutoApproveTrue_TrustFails_IsWarnNotError(t *testing.T) {
	composePath := testComposePath(t)
	fr := fakeExtractRunner(fakeCAPEM, nil)

	step := finalizeStep{
		runner: fr,
		locate: func(string) (string, error) { return composePath, nil },
		trustCA: func(context.Context, string) error {
			return errors.New("sudo: a password is required")
		},
		trustBrowser:   noopTrustBrowser,
		openBrowser:    noopOpenBrowser,
		createShortcut: noopCreateShortcut,
	}

	env := finalizeTestEnv(t)
	env.AutoApprove = true

	var progresses []Progress
	rep := ReporterFunc(func(p Progress) { progresses = append(progresses, p) })

	if err := step.Run(context.Background(), env, rep); err != nil {
		t.Fatalf("Run không được trả lỗi khi tin cậy CA tự động thất bại (chỉ là warn): %v", err)
	}

	last := progresses[len(progresses)-1]
	if last.Status != StatusWarn {
		t.Errorf("Status cuối = %v, muốn StatusWarn khi tin cậy CA tự động thất bại", last.Status)
	}
}

func TestFinalizeStep_OpenBrowserFails_SetsBrowserOpenedFalse_NoError(t *testing.T) {
	composePath := testComposePath(t)
	fr := fakeExtractRunner(fakeCAPEM, nil)

	step := finalizeStep{
		runner:         fr,
		locate:         func(string) (string, error) { return composePath, nil },
		trustCA:        noopTrustCA,
		trustBrowser:   noopTrustBrowser,
		openBrowser:    func(string) error { return errors.New("xdg-open: not found") },
		createShortcut: noopCreateShortcut,
	}

	env := finalizeTestEnv(t)

	var last Progress
	rep := ReporterFunc(func(p Progress) { last = p })

	if err := step.Run(context.Background(), env, rep); err != nil {
		t.Fatalf("Run không được trả lỗi khi không mở được trình duyệt: %v", err)
	}
	if env.BrowserOpened {
		t.Error("env.BrowserOpened phải false khi openBrowser giả trả lỗi")
	}
	if last.Status != StatusWarn {
		t.Errorf("Status cuối = %v, muốn StatusWarn khi không mở được trình duyệt", last.Status)
	}
	if !strings.Contains(last.Detail, "mở tay") {
		t.Errorf("Detail cuối phải gợi ý mở tay, được %q", last.Detail)
	}
}

func TestFinalizeStep_ShortcutFails_IsWarnNotError(t *testing.T) {
	composePath := testComposePath(t)
	fr := fakeExtractRunner(fakeCAPEM, nil)

	step := finalizeStep{
		runner:       fr,
		locate:       func(string) (string, error) { return composePath, nil },
		trustCA:      noopTrustCA,
		trustBrowser: noopTrustBrowser,
		openBrowser:  noopOpenBrowser,
		createShortcut: func(string) (string, error) {
			return "", errors.New("không ghi được tệp .desktop")
		},
	}

	err := step.Run(context.Background(), finalizeTestEnv(t), ReporterFunc(func(Progress) {}))
	if err != nil {
		t.Fatalf("Run không được trả lỗi khi không tạo được lối tắt (%s là warn, không chặn): %v", ErrCodeShortcutFailed, err)
	}
}

// TestFinalizeStep_CreateShortcut_Linux_WritesRealDesktopFile dùng
// createShortcutOS THẬT (không tiêm giả) trên chính GOOS của máy chạy CI
// (linux) — ghi vào $HOME/.local/share/applications dưới một $HOME giả lập
// bằng t.Setenv, không đụng home thật của máy.
func TestFinalizeStep_CreateShortcut_Linux_WritesRealDesktopFile(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("chỉ kiểm createShortcutLinux thật trên GOOS=linux")
	}

	composePath := testComposePath(t)
	fr := fakeExtractRunner(fakeCAPEM, nil)

	fakeHome := t.TempDir()
	t.Setenv("HOME", fakeHome)

	step := finalizeStep{
		runner:       fr,
		locate:       func(string) (string, error) { return composePath, nil },
		trustCA:      noopTrustCA,
		trustBrowser: noopTrustBrowser,
		openBrowser:  noopOpenBrowser,
		// createShortcut để nil => dùng createShortcutOS thật.
	}

	env := finalizeTestEnv(t)

	if err := step.Run(context.Background(), env, ReporterFunc(func(Progress) {})); err != nil {
		t.Fatalf("Run: %v", err)
	}

	wantPath := filepath.Join(fakeHome, ".local", "share", "applications", "gen-harness.desktop")
	data, err := os.ReadFile(wantPath)
	if err != nil {
		t.Fatalf("đọc lối tắt tại %s: %v", wantPath, err)
	}
	content := string(data)
	if !strings.Contains(content, "[Desktop Entry]") {
		t.Errorf("nội dung .desktop thiếu [Desktop Entry]: %q", content)
	}
	setupURL := SetupURL(env, "") // token rỗng vì test không cần khớp chính xác — chỉ cần đúng host:port/setup
	hostPart := strings.SplitN(setupURL, "?", 2)[0]
	if !strings.Contains(content, hostPart) {
		t.Errorf("nội dung .desktop phải chứa URL trình thiết lập %q, được %q", hostPart, content)
	}
}

func TestSetupURL_BuildsExpectedFormat(t *testing.T) {
	env := &Env{Port: 8443}
	got := SetupURL(env, "K7QF-2MXD-9PLA")
	want := "https://localhost:8443/setup?token=K7QF-2MXD-9PLA"
	if got != want {
		t.Errorf("SetupURL = %q, muốn %q", got, want)
	}
}

func TestSetupURL_DefaultsPortWhenUnset(t *testing.T) {
	got := SetupURL(&Env{}, "tok")
	if !strings.HasPrefix(got, "https://localhost:8443/setup?token=") {
		t.Errorf("SetupURL phải dùng machine.DefaultPort khi Env.Port <= 0, được %q", got)
	}
}

// Không có --yes vẫn phải tin cậy CA cho trình duyệt (kho NSS của user, không
// cần sudo) — Owner cài bằng `curl … | sh` không bao giờ truyền --yes, và
// trước đây luôn bị cảnh báo "Not secure" trên Chrome.
func TestFinalizeStep_TrustsBrowserWithoutAutoApprove(t *testing.T) {
	composePath := testComposePath(t)
	called := false
	step := finalizeStep{
		runner:  fakeExtractRunner(fakeCAPEM, nil),
		locate:  func(string) (string, error) { return composePath, nil },
		trustCA: noopTrustCA,
		trustBrowser: func(context.Context, string) error {
			called = true
			return nil
		},
		openBrowser:    noopOpenBrowser,
		createShortcut: noopCreateShortcut,
	}
	env := finalizeTestEnv(t)
	env.AutoApprove = false
	var last Progress
	if err := step.Run(context.Background(), env, ReporterFunc(func(p Progress) { last = p })); err != nil {
		t.Fatalf("Run: %v", err)
	}
	if !called {
		t.Fatal("trustBrowser phải được gọi cả khi không có --yes")
	}
	if last.Status != StatusOK {
		t.Errorf("Status cuối = %v, muốn OK khi trình duyệt đã tin cậy", last.Status)
	}
}
