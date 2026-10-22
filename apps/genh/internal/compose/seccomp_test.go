package compose

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// Bản nhúng phải khớp deploy/browser/chromium-seccomp.json (F-85).
func TestEmbeddedSeccompMatchesRepo(t *testing.T) {
	repo, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "deploy", "browser", "chromium-seccomp.json"))
	if err != nil {
		t.Fatalf("đọc deploy/browser/chromium-seccomp.json: %v", err)
	}
	if !bytes.Equal(repo, embeddedSeccomp) {
		t.Fatal("embedded_chromium-seccomp.json lệch deploy/browser/chromium-seccomp.json — chép lại cho khớp")
	}
	var doc map[string]any
	if err := json.Unmarshal(embeddedSeccomp, &doc); err != nil {
		t.Fatalf("profile seccomp không phải JSON hợp lệ: %v", err)
	}
	if s, _ := doc["defaultAction"].(string); s == "" {
		t.Error("profile seccomp thiếu defaultAction")
	}
}

func TestLocate_WritesSeccompNextToEmbeddedCompose(t *testing.T) {
	installDir := isolateLocate(t)
	t.Setenv(SeccompEnv, "")
	path, err := Locate(installDir)
	if err != nil {
		t.Fatalf("Locate: %v", err)
	}
	want := filepath.Join(installDir, "deploy", "browser", "chromium-seccomp.json")
	got, err := os.ReadFile(want)
	if err != nil {
		t.Fatalf("không có browser/chromium-seccomp.json cạnh compose.yaml: %v", err)
	}
	if !bytes.Equal(got, embeddedSeccomp) {
		t.Error("profile seccomp ghi ra khác bản nhúng")
	}
	if env := os.Getenv(SeccompEnv); env != want || !filepath.IsAbs(env) {
		t.Errorf("%s = %q, cần đường dẫn tuyệt đối %q (compose %s)", SeccompEnv, env, want, path)
	}
}

// Docker tự tạo THƯ MỤC khi thiếu tệp bind; sync sửa được, bản Owner sửa tay được giữ ở .bak.
func TestSync_ReplacesDirAndBacksUpEdited(t *testing.T) {
	installDir := isolateLocate(t)
	t.Setenv(SeccompEnv, "")
	deployDir := filepath.Join(installDir, "deploy")
	p := filepath.Join(deployDir, "browser", "chromium-seccomp.json")
	if err := os.MkdirAll(p, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(deployDir, "compose.yaml"), embeddedComposeYAML, 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := LocateAndSync(installDir); err != nil {
		t.Fatalf("LocateAndSync: %v", err)
	}
	if info, err := os.Stat(p); err != nil || info.IsDir() {
		t.Fatalf("profile phải là tệp sau khi sửa (err=%v)", err)
	}
	custom := []byte("{}\n")
	if err := os.WriteFile(p, custom, 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := Locate(installDir); err != nil {
		t.Fatal(err)
	}
	if got, _ := os.ReadFile(p); !bytes.Equal(got, custom) {
		t.Fatal("Locate không được ghi đè profile Owner đã sửa")
	}
	if ok, _ := InSyncWithEmbedded(installDir, filepath.Join(deployDir, "compose.yaml")); ok {
		t.Error("profile lệch bản nhúng thì không được coi là đã khớp")
	}
	if _, err := LocateAndSync(installDir); err != nil {
		t.Fatal(err)
	}
	if got, _ := os.ReadFile(p); !bytes.Equal(got, embeddedSeccomp) {
		t.Error("LocateAndSync phải đồng bộ profile về bản nhúng")
	}
	if got, _ := os.ReadFile(p + ".bak"); !bytes.Equal(got, custom) {
		t.Error("LocateAndSync phải giữ bản đã sửa ở .bak")
	}
	if ok, err := InSyncWithEmbedded(installDir, filepath.Join(deployDir, "compose.yaml")); err != nil || !ok {
		t.Errorf("sau sync phải khớp (ok=%v err=%v)", ok, err)
	}
}

// Dịch vụ browser: cap_drop ALL, no-new-privileges, seccomp riêng, sandbox auto.
func TestDeployCompose_BrowserSandboxHardening(t *testing.T) {
	for name, data := range map[string][]byte{"deploy/compose.yaml": readRepoCompose(t), "embedded_compose.yaml": embeddedComposeYAML} {
		var doc struct {
			Services map[string]struct {
				CapDrop     []string       `yaml:"cap_drop"`
				SecurityOpt []string       `yaml:"security_opt"`
				ReadOnly    bool           `yaml:"read_only"`
				Environment map[string]any `yaml:"environment"`
			} `yaml:"services"`
		}
		if err := yaml.Unmarshal(data, &doc); err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		b, ok := doc.Services["browser"]
		if !ok {
			t.Fatalf("%s thiếu service browser", name)
		}
		if len(b.CapDrop) != 1 || b.CapDrop[0] != "ALL" {
			t.Errorf("%s: browser phải cap_drop [ALL], đang %v", name, b.CapDrop)
		}
		if !b.ReadOnly {
			t.Errorf("%s: browser phải read_only", name)
		}
		var nnp, sec bool
		for _, o := range b.SecurityOpt {
			nnp = nnp || o == "no-new-privileges:true"
			sec = sec || (strings.HasPrefix(o, "seccomp=") && strings.Contains(o, "chromium-seccomp.json"))
		}
		if !nnp || !sec {
			t.Errorf("%s: security_opt thiếu no-new-privileges hoặc seccomp=…chromium-seccomp.json: %v", name, b.SecurityOpt)
		}
		if v, _ := b.Environment["GH_BROWSER_SANDBOX"].(string); !strings.Contains(v, "GH_BROWSER_SANDBOX:-auto") {
			t.Errorf("%s: thiếu GH_BROWSER_SANDBOX mặc định auto (đang %q)", name, v)
		}
	}
}
