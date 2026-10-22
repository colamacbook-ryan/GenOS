//go:build !windows

// Bit quyền POSIX (0600) chỉ có nghĩa trên Linux/macOS: trên Windows,
// os.FileMode của tệp thường luôn là 0666 (hoặc 0444 nếu chỉ đọc) bất kể
// quyền đã xin khi tạo — quyền truy cập ở đó do ACL quyết định. Vì vậy phần
// kiểm quyền tách khỏi TestRunExport_HappyPath_StreamsStdoutToFile (chạy trên
// mọi hệ điều hành) sang tệp này.

package ops

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
)

// Gói xuất chứa toàn bộ dữ liệu + khoá đã mã hoá — chỉ chủ máy được đọc.
func TestRunExport_OutputFileIsOwnerOnly(t *testing.T) {
	withBundlePassword(t, "mat-khau-du-dai-123")
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	toPath := filepath.Join(t.TempDir(), "out.ghbundle")

	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("exec", "-T", "-e", bundlePasswordEnv, "api", "python", "-m", "gh.bundle", "export", "--out", "-"),
			RunIOStdout: []byte(bundleMagic + "fake-bytes")},
	}}

	var out strings.Builder
	if err := RunExport(context.Background(), env, toPath, ExportDeps{Runner: fr}, &out); err != nil {
		t.Fatalf("RunExport: %v", err)
	}
	info, err := os.Stat(toPath)
	if err != nil {
		t.Fatalf("stat %s: %v", toPath, err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Errorf("quyền tệp = %o, muốn 0600", info.Mode().Perm())
	}
}
