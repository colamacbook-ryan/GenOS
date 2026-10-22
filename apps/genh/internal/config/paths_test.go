package config

import (
	"path/filepath"
	"testing"
)

// install.sh đặt genh vào $GEN_HARNESS_HOME/bin; genh phải cài dịch vụ vào
// cùng gốc đó, nếu không `genh status` sau cài báo "chưa cài".
func TestDefaultRoot_HonorsEnv(t *testing.T) {
	want := filepath.Join(t.TempDir(), "gh")
	t.Setenv(EnvRoot, want)
	got, err := DefaultRoot()
	if err != nil || got != want {
		t.Fatalf("DefaultRoot = %q, %v; muốn %q", got, err, want)
	}
}

func TestDefaultRoot_FallsBackWhenEnvEmpty(t *testing.T) {
	t.Setenv(EnvRoot, "")
	got, err := DefaultRoot()
	if err != nil || got == "" {
		t.Fatalf("DefaultRoot = %q, %v", got, err)
	}
}
