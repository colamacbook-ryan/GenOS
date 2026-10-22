package ops

import (
	"errors"
	"strings"
	"testing"
)

func TestRunOpen_HappyPath_OpensConsoleURL(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)

	var openedURL string
	deps := OpenDeps{OpenBrowser: func(url string) error {
		openedURL = url
		return nil
	}}

	var out strings.Builder
	if err := RunOpen(env, deps, &out); err != nil {
		t.Fatalf("RunOpen: %v", err)
	}
	want := "https://localhost:18443/"
	if openedURL != want {
		t.Errorf("openedURL = %q, muốn %q", openedURL, want)
	}
	if !strings.Contains(out.String(), want) {
		t.Errorf("output phải chứa %q, được %q", want, out.String())
	}
	if strings.Contains(openedURL, "token=") {
		t.Errorf("URL mở Console không được kèm token thiết lập, được %q", openedURL)
	}
}

func TestRunOpen_NotInstalled_ReturnsOpError_DoesNotOpenBrowser(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnvNoSecrets(t, composePath)

	called := false
	deps := OpenDeps{OpenBrowser: func(string) error { called = true; return nil }}

	err := RunOpen(env, deps, &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T", err)
	}
	if opErr.Code != ErrCodeOpenFailed {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeOpenFailed)
	}
	if called {
		t.Error("không được mở trình duyệt khi chưa cài")
	}
}

func TestRunOpen_BrowserFails_ReturnsOpError(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)

	deps := OpenDeps{OpenBrowser: func(string) error { return errors.New("xdg-open: not found") }}

	err := RunOpen(env, deps, &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T", err)
	}
	if opErr.Code != ErrCodeOpenFailed {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeOpenFailed)
	}
}
