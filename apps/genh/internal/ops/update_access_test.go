package ops

import (
	"context"
	"os"
	"strings"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/access"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
)

// F-21: máy cũ (không có .env) PHẢI được ghi GH_BIND_ADDR=0.0.0.0 + lan_legacy
// TRƯỚC lệnh `docker compose up` đầu tiên của lần cập nhật (compose.yaml mới
// nghe 127.0.0.1 theo mặc định — không ghi trước là âm thầm đóng cổng LAN).
func TestRunUpdate_WritesBindAddrBeforeFirstUp(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)
	if err := hostlink.EnsureDir(env.InstallDir); err != nil {
		t.Fatal(err)
	}
	_, port := listenReadyServer(t, true)
	env.Port = port

	envAtUp, seenUp := "", false
	capture := fake.Response{
		Match: func(c dockercli.Cmd) bool {
			if !matchUp(c) {
				return false
			}
			if !seenUp {
				seenUp = true
				b, _ := os.ReadFile(access.EnvPath(composePath))
				envAtUp = string(b)
			}
			return true
		},
		Output: []byte(""),
	}
	fr := updateFakeRunner(capture)

	if err := RunUpdate(context.Background(), env, UpdateOptions{Channel: "stable", Version: testVersion}, fastUpdateDeps(fr), &strings.Builder{}); err != nil {
		t.Fatalf("RunUpdate: %v", err)
	}
	if !seenUp {
		t.Fatal("không thấy lệnh up")
	}
	if !strings.Contains(envAtUp, "GH_BIND_ADDR=0.0.0.0\n") || !strings.Contains(envAtUp, "GH_ACCESS_MODE=lan_legacy\n") {
		t.Errorf(".env tại thời điểm up đầu tiên = %q", envAtUp)
	}
	ns, err := hostlink.ReadNetworkStatus(env.InstallDir)
	if err != nil {
		t.Fatalf("ReadNetworkStatus: %v", err)
	}
	if ns.Schema != 1 || ns.Mode != "lan_legacy" || ns.BindAddr != "0.0.0.0" {
		t.Errorf("network-status.json = %+v", ns)
	}
}

// Chủ máy đã chọn cách truy cập thì cập nhật KHÔNG đổi lại.
func TestRunUpdate_KeepsOwnerAccessChoice(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)
	_, port := listenReadyServer(t, true)
	env.Port = port
	want := access.State{Mode: access.ModeLocal, BindAddr: access.BindLocal, PublicURL: "https://localhost:8443"}
	if err := access.Write(composePath, want); err != nil {
		t.Fatal(err)
	}
	if err := RunUpdate(context.Background(), env, UpdateOptions{Version: testVersion}, fastUpdateDeps(updateFakeRunner()), &strings.Builder{}); err != nil {
		t.Fatalf("RunUpdate: %v", err)
	}
	got, _ := access.Read(composePath)
	if got != want {
		t.Errorf("cấu hình bị đổi: %+v", got)
	}
}
