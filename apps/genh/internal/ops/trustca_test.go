package ops

import (
	"context"
	"errors"
	"os"
	"strings"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/install"
)

const fakeCAPEM = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"

func TestRunTrustCA_WritesCertAndTrustsBothStores(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("exec", "-T", "proxy", "cat"), Output: []byte(fakeCAPEM)},
	}}
	var gotBrowser, gotSystem string
	var gotInteractive bool
	deps := TrustCADeps{
		Runner:       fr,
		TrustBrowser: func(_ context.Context, p string) error { gotBrowser = p; return nil },
		TrustSystem: func(_ context.Context, p string, interactive bool) error {
			gotSystem, gotInteractive = p, interactive
			return nil
		},
	}
	var out strings.Builder
	if err := RunTrustCA(context.Background(), env, true, deps, &out); err != nil {
		t.Fatalf("RunTrustCA: %v", err)
	}
	certPath := install.CACertPath(env.InstallDir)
	data, err := os.ReadFile(certPath)
	if err != nil || string(data) != fakeCAPEM {
		t.Fatalf("CA ghi ra %s = %q (%v)", certPath, data, err)
	}
	if gotBrowser != certPath || gotSystem != certPath || !gotInteractive {
		t.Errorf("browser=%q system=%q interactive=%v", gotBrowser, gotSystem, gotInteractive)
	}
	for _, want := range []string{"Trình duyệt (Chrome/Firefox): đã tin cậy", "Hệ điều hành: đã tin cậy", "mở lại trình duyệt"} {
		if !strings.Contains(out.String(), want) {
			t.Errorf("output thiếu %q: %q", want, out.String())
		}
	}
}

func TestRunTrustCA_StoreFailuresAreNotErrors(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("exec", "-T", "proxy", "cat"), Output: []byte(fakeCAPEM)},
	}}
	deps := TrustCADeps{
		Runner:       fr,
		TrustBrowser: func(context.Context, string) error { return errors.New("thiếu certutil") },
		TrustSystem:  func(context.Context, string, bool) error { return install.ErrTrustSkipped },
	}
	var out strings.Builder
	if err := RunTrustCA(context.Background(), env, false, deps, &out); err != nil {
		t.Fatalf("RunTrustCA phải best-effort, được %v", err)
	}
	if !strings.Contains(out.String(), "thiếu certutil") || !strings.Contains(out.String(), "genh trust-ca") {
		t.Errorf("output = %q", out.String())
	}
}

func TestRunTrustCA_ExtractFails_ReturnsOpError(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("exec", "-T", "proxy", "cat"), Err: errors.New("proxy down")},
	}}
	called := false
	deps := TrustCADeps{Runner: fr, TrustBrowser: func(context.Context, string) error { called = true; return nil },
		TrustSystem: func(context.Context, string, bool) error { called = true; return nil }}
	err := RunTrustCA(context.Background(), env, false, deps, &strings.Builder{})
	var opErr *OpError
	if !errors.As(err, &opErr) || opErr.Code != ErrCodeTrustCAFailed {
		t.Fatalf("muốn OpError %s, được %v", ErrCodeTrustCAFailed, err)
	}
	if called {
		t.Error("không được tin cậy gì khi chưa trích được CA")
	}
}
