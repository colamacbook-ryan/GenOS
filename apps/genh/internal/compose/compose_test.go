package compose

import (
	"bytes"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

const sampleCompose = `
name: gen-harness

x-app-env: &app-env
  GH_ENV: production

services:
  proxy:
    image: caddy:2-alpine
    environment: *app-env

  redis:
    image: redis:7-alpine

  api:
    build: { context: .., dockerfile: deploy/images/api.Dockerfile }
    environment: *app-env

  db:
    build: { context: .., dockerfile: deploy/images/db.Dockerfile }
`

func TestParse_ReadsImageAndBuildServices(t *testing.T) {
	f, err := Parse([]byte(sampleCompose))
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	if len(f.Services) != 4 {
		t.Fatalf("số service = %d, muốn 4", len(f.Services))
	}

	proxy := f.Services["proxy"]
	if proxy.Image != "caddy:2-alpine" {
		t.Errorf("proxy.Image = %q, muốn caddy:2-alpine", proxy.Image)
	}
	if proxy.Build {
		t.Error("proxy.Build phải false (dùng image: có sẵn)")
	}

	api := f.Services["api"]
	if api.Image != "" {
		t.Errorf("api.Image = %q, muốn rỗng (chỉ có build:)", api.Image)
	}
	if !api.Build {
		t.Error("api.Build phải true")
	}
}

func TestParse_ResolvesYAMLAnchors(t *testing.T) {
	// Nếu anchor/alias (*app-env) không được parser giải quyết, Unmarshal sẽ
	// lỗi hoặc bỏ qua service đó — Parse phải không lỗi và service vẫn xuất
	// hiện đầy đủ.
	f, err := Parse([]byte(sampleCompose))
	if err != nil {
		t.Fatalf("Parse với anchor/alias: %v", err)
	}
	if _, ok := f.Services["api"]; !ok {
		t.Error("service dùng alias *app-env phải parse được bình thường")
	}
}

func TestParse_InvalidYAML(t *testing.T) {
	_, err := Parse([]byte("services: [not-a-map"))
	if err == nil {
		t.Fatal("muốn lỗi với YAML hỏng")
	}
}

func TestLoad_ReadsFileFromDisk(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "compose.yaml")
	if err := os.WriteFile(path, []byte(sampleCompose), 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}
	f, err := Load(path)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if f.Path != path {
		t.Errorf("Path = %q, muốn %q", f.Path, path)
	}
	if len(f.Services) != 4 {
		t.Errorf("số service = %d, muốn 4", len(f.Services))
	}
}

func TestBaseArgs(t *testing.T) {
	got := BaseArgs("/tmp/compose.yaml", "up", "-d", "db")
	want := []string{"compose", "-f", "/tmp/compose.yaml", "up", "-d", "db"}
	if len(got) != len(want) {
		t.Fatalf("BaseArgs = %v, muốn %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("BaseArgs[%d] = %q, muốn %q", i, got[i], want[i])
		}
	}
}

// F-37: mọi dịch vụ phải giới hạn log (json-file, 3 tệp × 10 MB) — log không
// giới hạn làm đầy đĩa máy Owner. Đọc YAML thô (không qua Parse) để thấy
// khoá logging sau khi anchor *logging đã được giải.
func repoComposePath() string {
	return filepath.Join("..", "..", "..", "..", "deploy", "compose.yaml")
}

func readRepoCompose(t *testing.T) []byte {
	t.Helper()
	data, err := os.ReadFile(repoComposePath())
	if err != nil {
		t.Fatalf("đọc deploy/compose.yaml: %v", err)
	}
	return data
}

func composeServices(t *testing.T, data []byte) map[string]any {
	t.Helper()
	var doc map[string]any
	if err := yaml.Unmarshal(data, &doc); err != nil {
		t.Fatalf("yaml.Unmarshal: %v", err)
	}
	services, ok := doc["services"].(map[string]any)
	if !ok || len(services) == 0 {
		t.Fatal("compose không có khối services")
	}
	return services
}

func assertEveryServiceHasLogLimits(t *testing.T, label string, data []byte) {
	t.Helper()
	for name, raw := range composeServices(t, data) {
		svc, _ := raw.(map[string]any)
		logging, ok := svc["logging"].(map[string]any)
		if !ok {
			t.Errorf("%s: service %q thiếu logging: *logging (F-37)", label, name)
			continue
		}
		if got := logging["driver"]; got != "json-file" {
			t.Errorf("%s: service %q logging.driver = %v, muốn json-file", label, name, got)
		}
		opts, _ := logging["options"].(map[string]any)
		if got := opts["max-size"]; got != "10m" {
			t.Errorf("%s: service %q logging.options.max-size = %v, muốn \"10m\"", label, name, got)
		}
		if got := opts["max-file"]; got != "3" {
			t.Errorf("%s: service %q logging.options.max-file = %v, muốn \"3\"", label, name, got)
		}
	}
}

func TestDeployCompose_EveryServiceHasLogLimits(t *testing.T) {
	assertEveryServiceHasLogLimits(t, "deploy/compose.yaml", readRepoCompose(t))
}

func TestEmbeddedCompose_EveryServiceHasLogLimits(t *testing.T) {
	assertEveryServiceHasLogLimits(t, "embedded_compose.yaml", embeddedComposeYAML)
}

func TestEmbeddedComposeMatchesRepo(t *testing.T) {
	if !bytes.Equal(readRepoCompose(t), embeddedComposeYAML) {
		t.Fatal("apps/genh/internal/compose/embedded_compose.yaml lệch deploy/compose.yaml — chép lại cho khớp")
	}
}

// F-19 (v0.1.48): mọi ảnh dựng sẵn phải GHIM DIGEST (<tag>@sha256:<64 hex>); service không có image: thì
// bắt buộc có build: (bản phát hành bị release.yml ghi đè bằng compose.release.yaml toàn image: ghcr.io/...@sha256:).
var imagePinRE = regexp.MustCompile(`^[a-z0-9][a-z0-9._/:-]*@sha256:[0-9a-f]{64}$`)

func imagePinErrors(data []byte) []string {
	var doc map[string]any
	if err := yaml.Unmarshal(data, &doc); err != nil {
		return []string{"YAML không đọc được: " + err.Error()}
	}
	services, ok := doc["services"].(map[string]any)
	if !ok || len(services) == 0 {
		return []string{"compose không có khối services"}
	}
	var errs []string
	for name, raw := range services {
		svc, _ := raw.(map[string]any)
		image, hasImage := svc["image"]
		if !hasImage {
			if _, hasBuild := svc["build"]; !hasBuild {
				errs = append(errs, "service "+name+" thiếu cả image: lẫn build:")
			}
			continue
		}
		str, _ := image.(string)
		if !imagePinRE.MatchString(str) {
			errs = append(errs, "service "+name+" image "+str+" chưa ghim digest (<tag>@sha256:<64 hex>)")
		}
	}
	sort.Strings(errs)
	return errs
}

func assertEveryImagePinned(t *testing.T, label string, data []byte) {
	t.Helper()
	for _, e := range imagePinErrors(data) {
		t.Errorf("%s: %s (F-19)", label, e)
	}
}

func TestDeployCompose_EveryImagePinnedByDigest(t *testing.T) {
	assertEveryImagePinned(t, "deploy/compose.yaml", readRepoCompose(t))
}

func TestEmbeddedCompose_EveryImagePinnedByDigest(t *testing.T) {
	assertEveryImagePinned(t, "embedded_compose.yaml", embeddedComposeYAML)
}

func TestImagePinErrors_RejectsTagOnly(t *testing.T) {
	hex64 := strings.Repeat("ab", 32)
	tagOnly := "services:\n  proxy:\n    image: caddy:2-alpine\n"
	if len(imagePinErrors([]byte(tagOnly))) == 0 {
		t.Error("image: caddy:2-alpine (không digest) phải bị báo lỗi")
	}
	pinned := "services:\n  proxy:\n    image: caddy:2-alpine@sha256:" + hex64 + "\n"
	if errs := imagePinErrors([]byte(pinned)); len(errs) != 0 {
		t.Errorf("ảnh đã ghim digest không được báo lỗi: %v", errs)
	}
	release := "services:\n  api:\n    image: ghcr.io/o/gen-harness-api@sha256:" + hex64 + "\n"
	if errs := imagePinErrors([]byte(release)); len(errs) != 0 {
		t.Errorf("ca phát hành ghcr.io/...@sha256 không được báo lỗi: %v", errs)
	}
	build := "services:\n  api:\n    build: { context: .., dockerfile: deploy/images/api.Dockerfile }\n"
	if errs := imagePinErrors([]byte(build)); len(errs) != 0 {
		t.Errorf("service chỉ có build: không được báo lỗi: %v", errs)
	}
	neither := "services:\n  api:\n    restart: always\n"
	if len(imagePinErrors([]byte(neither))) == 0 {
		t.Error("service thiếu cả image lẫn build phải bị báo lỗi")
	}
	badDigest := "services:\n  api:\n    image: redis:7@sha256:abc\n"
	if len(imagePinErrors([]byte(badDigest))) == 0 {
		t.Error("digest không đủ 64 hex phải bị báo lỗi")
	}
}

// v0.1.46 (F-21/F-27): cổng chỉ nghe 127.0.0.1 theo mặc định; Caddyfile giữ
// localhost (ops.ProxyHost) cộng GH_SITE_ADDRESS. Canh cả bản repo lẫn bản nhúng.
func TestCompose_PortsBindAddrAndCaddySiteLine(t *testing.T) {
	const wantPorts = `ports: ["${GH_BIND_ADDR:-127.0.0.1}:${GH_PORT:-8443}:8443"]`
	const wantSite = `localhost:8443, {$GH_SITE_ADDRESS:127.0.0.1}:8443`
	repoCaddy, err := os.ReadFile(filepath.Join(filepath.Dir(repoComposePath()), "proxy", "Caddyfile"))
	if err != nil {
		t.Fatalf("đọc deploy/proxy/Caddyfile: %v", err)
	}
	cases := []struct {
		name    string
		compose []byte
		caddy   []byte
	}{
		{"repo", readRepoCompose(t), repoCaddy},
		{"nhúng", embeddedComposeYAML, embeddedCaddyfile},
	}
	for _, c := range cases {
		if !strings.Contains(string(c.compose), wantPorts) {
			t.Errorf("%s: compose thiếu dòng %s", c.name, wantPorts)
		}
		if !strings.Contains(string(c.compose), "GH_SITE_ADDRESS: ${GH_SITE_ADDRESS:-127.0.0.1}") {
			t.Errorf("%s: GH_SITE_ADDRESS mặc định phải là 127.0.0.1", c.name)
		}
		if !strings.Contains(string(c.caddy), wantSite) {
			t.Errorf("%s: Caddyfile thiếu %s", c.name, wantSite)
		}
	}
}

func TestDeployCompose_WebHealthcheckUsesHealthz(t *testing.T) {
	web, ok := composeServices(t, readRepoCompose(t))["web"].(map[string]any)
	if !ok {
		t.Fatal("deploy/compose.yaml thiếu service web")
	}
	hc, _ := web["healthcheck"].(map[string]any)
	test, _ := hc["test"].([]any)
	for _, part := range test {
		if s, ok := part.(string); ok && strings.Contains(s, "/healthz") {
			return
		}
	}
	t.Fatalf("healthcheck.test của web = %v, phải gọi /healthz", hc["test"])
}
