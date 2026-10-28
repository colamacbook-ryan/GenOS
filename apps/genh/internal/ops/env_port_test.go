package ops

import (
	"os"
	"path/filepath"
	"testing"
)

func TestResolvePortFor(t *testing.T) {
	t.Setenv("GENH_COMPOSE_FILE", "")
	dir := t.TempDir()
	if got := ResolvePortFor(dir, 0); got != 8443 {
		t.Errorf("chưa có .env: %d", got)
	}
	_ = os.MkdirAll(filepath.Join(dir, "deploy"), 0o755)
	_ = os.WriteFile(filepath.Join(dir, "deploy", ".env"), []byte("GH_PORT=18478\n"), 0o644)
	if got := ResolvePortFor(dir, 0); got != 18478 {
		t.Errorf("không cờ ⇒ lấy GH_PORT, nhận %d", got)
	}
	if got := ResolvePortFor(dir, 8443); got != 8443 {
		t.Errorf("--port 8443 tường minh phải thắng GH_PORT, nhận %d", got)
	}
	if got := ResolvePortFor(dir, 9000); got != 9000 {
		t.Errorf("cờ tường minh phải thắng, nhận %d", got)
	}
}
