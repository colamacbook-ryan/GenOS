package ops

import (
	"context"
	"encoding/base64"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
)

const agyImg = "ghcr.io/o/gen-harness-api@sha256:" +
	"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

// Bản phát hành: api và agy-box cùng image đã ghim digest (chung api.Dockerfile).
const updateAgyComposeYAML = `name: gen-harness
services:
  proxy:
    image: caddy:2-alpine
  redis:
    image: redis:7-alpine
  api:
    image: ` + agyImg + `
  agy-box:
    image: ` + agyImg + `
  worker:
    image: ` + agyImg + `
  web:
    build: { context: .. }
  bridge:
    build: { context: .. }
  db:
    build: { context: .. }
`

// agy-box là service genh update biết để tải: có image ghim ⇒ vào danh sách tải.
func TestResolveUpdateServices_CoAgyBox(t *testing.T) {
	cf, err := compose.Parse([]byte(updateAgyComposeYAML))
	if err != nil {
		t.Fatal(err)
	}
	pullable, skipped := resolveUpdateServices(cf)
	has := func(list []string, name string) bool {
		for _, n := range list {
			if n == name {
				return true
			}
		}
		return false
	}
	if !has(pullable, "agy-box") || has(skipped, "agy-box") {
		t.Fatalf("agy-box phải nằm trong pullable: pullable=%v skipped=%v", pullable, skipped)
	}
	// Thứ tự: sau api (cùng ảnh ⇒ api tải trước, agy-box chỉ xác nhận "đã có").
	ia, ib := -1, -1
	for i, n := range pullable {
		switch n {
		case "api":
			ia = i
		case "agy-box":
			ib = i
		}
	}
	if ia < 0 || ib < ia {
		t.Fatalf("agy-box phải đứng sau api trong thứ tự tải: %v", pullable)
	}
}

// Máy cài từ v0.1.62 (chưa có gh_agy_key): `genh update` phải sinh khoá TRƯỚC `docker compose up`
// (compose mới mount secret này; thiếu tệp thì up lỗi và update rollback), tải agy-box,
// và lần cập nhật sau không đổi khoá.
func TestRunUpdate_TuSinhAgyKeyTruocUpVaTaiAgyBox(t *testing.T) {
	composePath := testComposePath(t, updateAgyComposeYAML)
	env := testEnv(t, composePath)
	// compose KHÔNG phải bản genh quản lý (deploy/compose.yaml) ⇒ đích cập nhật là chính tệp này,
	// không phải compose nhúng — để kiểm danh sách tải đúng nội dung compose bản phát hành.
	altDir := filepath.Join(env.InstallDir, "alt")
	if err := os.MkdirAll(altDir, 0o755); err != nil {
		t.Fatal(err)
	}
	altCompose := filepath.Join(altDir, "compose.yaml")
	if err := os.WriteFile(altCompose, []byte(updateAgyComposeYAML), 0o644); err != nil {
		t.Fatal(err)
	}
	env.locate = func(string) (string, error) { return altCompose, nil }
	secrets := filepath.Join(env.InstallDir, "secrets")
	if err := os.MkdirAll(secrets, 0o700); err != nil {
		t.Fatal(err)
	}
	// Bản cài v0.1.62: có khoá cũ, KHÔNG có gh_agy_key.
	for name, v := range map[string]string{"gh_master_key": "m", "gh_bridge_key": "cu-bridge", "gh_browser_key": "cu-browser"} {
		if err := os.WriteFile(filepath.Join(secrets, name), []byte(v), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	_, port := listenReadyServer(t, true)
	env.Port = port

	keyAtUp := ""
	capture := fake.Response{
		Match: func(c dockercli.Cmd) bool {
			if !matchFullUp(c) {
				return false
			}
			if keyAtUp == "" {
				b, _ := os.ReadFile(filepath.Join(secrets, "gh_agy_key"))
				keyAtUp = string(b)
			}
			return true
		},
		Output: []byte(""),
	}
	fr := updateFakeRunner(capture)
	if err := RunUpdate(context.Background(), env, UpdateOptions{Channel: "stable", Version: testVersion}, fastUpdateDeps(fr), &strings.Builder{}); err != nil {
		t.Fatalf("RunUpdate: %v", err)
	}
	if k, err := base64.StdEncoding.DecodeString(keyAtUp); err != nil || len(k) != 32 {
		t.Fatalf("tại lệnh up, gh_agy_key phải đã là 32 byte base64: %q (%v)", keyAtUp, err)
	}
	for name, want := range map[string]string{"gh_bridge_key": "cu-bridge", "gh_browser_key": "cu-browser", "gh_master_key": "m"} {
		if got, _ := os.ReadFile(filepath.Join(secrets, name)); string(got) != want {
			t.Errorf("%s bị đổi khi update: %q", name, got)
		}
	}

	pulled := false
	for _, c := range fr.Calls {
		if matchPull(c.Cmd) && strings.Contains(strings.Join(c.Cmd.Args, " "), "agy-box") {
			pulled = true
		}
	}
	if !pulled {
		t.Error("lệnh `docker compose pull` phải gồm agy-box")
	}

	// Cập nhật lần hai: khoá giữ nguyên.
	_, port2 := listenReadyServer(t, true)
	env.Port = port2
	if err := RunUpdate(context.Background(), env, UpdateOptions{Channel: "stable", Version: testVersion}, fastUpdateDeps(updateFakeRunner()), &strings.Builder{}); err != nil {
		t.Fatalf("RunUpdate lần 2: %v", err)
	}
	if again, _ := os.ReadFile(filepath.Join(secrets, "gh_agy_key")); string(again) != keyAtUp {
		t.Fatal("gh_agy_key đổi ở lần update thứ hai")
	}
}
