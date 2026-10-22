package ops

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// F-22 (v0.1.38): phiên Claude Code chuyển sang volume riêng claude_state —
// `genh status` phải liệt kê dòng của volume này trong `docker system df -v`.
func TestVolumeLines_IncludesClaudeState(t *testing.T) {
	df := strings.Join([]string{
		"Local Volumes space usage:",
		"",
		"VOLUME NAME                    LINKS     SIZE",
		"gen-harness_agy_state          2         12MB",
		"gen-harness_claude_state       2         40kB",
		"other-project_data             1         1GB",
	}, "\n")
	got := strings.Join(volumeLines(df), "\n")
	for _, want := range []string{"VOLUME NAME", "gen-harness_agy_state", "gen-harness_claude_state"} {
		if !strings.Contains(got, want) {
			t.Errorf("thiếu %q trong %q", want, got)
		}
	}
	if strings.Contains(got, "other-project_data") {
		t.Errorf("không được lấy volume ngoài Gen-Harness: %q", got)
	}
}

// Mọi volume khai ở cuối deploy/compose.yaml đều nằm trong volumeBaseNames
// (thêm volume mới mà quên genh status thì test này đỏ).
func TestVolumeBaseNames_MatchComposeVolumes(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "deploy", "compose.yaml"))
	if err != nil {
		t.Fatalf("đọc deploy/compose.yaml: %v", err)
	}
	var doc struct {
		Volumes map[string]any `yaml:"volumes"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("parse compose.yaml: %v", err)
	}
	if _, ok := doc.Volumes["claude_state"]; !ok {
		t.Fatal("deploy/compose.yaml thiếu volume claude_state (F-22)")
	}
	known := map[string]bool{}
	for _, n := range volumeBaseNames {
		known[n] = true
	}
	for name := range doc.Volumes {
		if !known[name] {
			t.Errorf("volume %q của compose.yaml thiếu trong volumeBaseNames", name)
		}
	}
}
