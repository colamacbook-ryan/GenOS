package ops

import (
	"path/filepath"
	"strings"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/secretgen"
)

func TestRunResetSetup_AutoApprove_GeneratesNewToken(t *testing.T) {
	dir := t.TempDir()
	before, err := secretgen.Ensure(filepath.Join(dir, "config"))
	if err != nil {
		t.Fatalf("secretgen.Ensure: %v", err)
	}
	env := &Env{InstallDir: dir, Port: 18443}

	var out strings.Builder
	if err := RunResetSetup(env, ResetSetupOptions{AutoApprove: true}, strings.NewReader(""), &out); err != nil {
		t.Fatalf("RunResetSetup: %v", err)
	}

	after, err := secretgen.Load(filepath.Join(dir, "config"))
	if err != nil {
		t.Fatalf("secretgen.Load: %v", err)
	}
	if after.SetupToken == before.Bundle.SetupToken {
		t.Error("SetupToken phải đổi sau reset-setup")
	}
	if !strings.Contains(out.String(), after.SetupToken) {
		t.Errorf("output phải in mã mới %q, được %q", after.SetupToken, out.String())
	}
}

func TestRunResetSetup_NoConfirmation_Cancels_DoesNotChangeToken(t *testing.T) {
	dir := t.TempDir()
	before, err := secretgen.Ensure(filepath.Join(dir, "config"))
	if err != nil {
		t.Fatalf("secretgen.Ensure: %v", err)
	}
	env := &Env{InstallDir: dir, Port: 18443}

	err = RunResetSetup(env, ResetSetupOptions{}, strings.NewReader("n\n"), &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T", err)
	}
	if opErr.Code != ErrCodeResetSetupCancelled {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeResetSetupCancelled)
	}

	after, err := secretgen.Load(filepath.Join(dir, "config"))
	if err != nil {
		t.Fatalf("secretgen.Load: %v", err)
	}
	if after.SetupToken != before.Bundle.SetupToken {
		t.Error("SetupToken không được đổi khi huỷ xác nhận")
	}
}

func TestRunResetSetup_YesConfirmation_GeneratesNewToken(t *testing.T) {
	dir := t.TempDir()
	if _, err := secretgen.Ensure(filepath.Join(dir, "config")); err != nil {
		t.Fatalf("secretgen.Ensure: %v", err)
	}
	env := &Env{InstallDir: dir, Port: 18443}

	if err := RunResetSetup(env, ResetSetupOptions{}, strings.NewReader("yes\n"), &strings.Builder{}); err != nil {
		t.Fatalf("RunResetSetup: %v", err)
	}
}

func TestRunResetSetup_NotInstalled_ReturnsOpError(t *testing.T) {
	dir := t.TempDir()
	env := &Env{InstallDir: dir, Port: 18443}

	err := RunResetSetup(env, ResetSetupOptions{AutoApprove: true}, strings.NewReader(""), &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T", err)
	}
	if opErr.Code != ErrCodeResetSetupFailed {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeResetSetupFailed)
	}
}
