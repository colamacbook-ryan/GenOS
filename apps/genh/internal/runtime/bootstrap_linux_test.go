//go:build linux

package runtime

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
)

func TestDockerArchName(t *testing.T) {
	if got := dockerArchName("amd64"); got != "x86_64" {
		t.Errorf("amd64 -> %q, muốn x86_64", got)
	}
	if got := dockerArchName("arm64"); got != "aarch64" {
		t.Errorf("arm64 -> %q, muốn aarch64", got)
	}
}

func TestStaticDownloadURL(t *testing.T) {
	got := StaticDownloadURL("https://download.docker.com", "amd64", "27.3.1")
	want := "https://download.docker.com/linux/static/stable/x86_64/docker-27.3.1.tgz"
	if got != want {
		t.Errorf("StaticDownloadURL = %q, muốn %q", got, want)
	}
}

// TestPinnedChecksum_KnownVersion kiểm bảng dockerStaticChecksums thật (bản
// dockerStaticVersion hiện hành) có đủ checksum cho cả 2 kiến trúc release
// hỗ trợ và cả 2 gói (chính + rootless-extras) — hỏng bảng này là hỏng cài
// đặt Docker thật trên máy Owner, không cách nào test bằng server giả bù
// được.
func TestPinnedChecksum_KnownVersion(t *testing.T) {
	for _, goarch := range []string{"amd64", "arm64"} {
		for _, name := range []string{
			fmt.Sprintf("docker-%s.tgz", dockerStaticVersion),
			fmt.Sprintf("docker-rootless-extras-%s.tgz", dockerStaticVersion),
		} {
			got, err := PinnedChecksum(goarch, name)
			if err != nil {
				t.Errorf("PinnedChecksum(%q, %q): %v", goarch, name, err)
				continue
			}
			if len(got) != 64 {
				t.Errorf("PinnedChecksum(%q, %q) = %q, muốn chuỗi hex sha256 dài 64", goarch, name, got)
			}
		}
	}
}

func TestPinnedChecksum_UnknownFails(t *testing.T) {
	if _, err := PinnedChecksum("amd64", "docker-0.0.0.tgz"); err == nil {
		t.Error("muốn lỗi rõ ràng khi không có checksum ghim cho version lạ, thay vì âm thầm bỏ qua kiểm chứng")
	}
	if _, err := PinnedChecksum("riscv64", fmt.Sprintf("docker-%s.tgz", dockerStaticVersion)); err == nil {
		t.Error("muốn lỗi rõ ràng khi không có checksum ghim cho kiến trúc lạ")
	}
}

// withPinnedChecksums ghi đè tạm bảng checksum toàn cục cho một version+arch
// giả (dùng httptest.Server + tarball giả trong bộ nhớ), trả hàm khôi phục
// bảng thật — để các test Bootstrap dưới đây không phụ thuộc mạng thật lẫn
// không phải sửa binary docker thật vào git.
func withPinnedChecksums(t *testing.T, arch, version string, checksums map[string]string) {
	t.Helper()
	old := dockerStaticChecksums
	merged := map[string]map[string]string{}
	for k, v := range old {
		merged[k] = v
	}
	merged[arch] = checksums
	dockerStaticChecksums = merged
	t.Cleanup(func() { dockerStaticChecksums = old })
}

// fakeTarGz trả về nội dung .tar.gz hợp lệ, tối giản (chỉ đủ để
// ExtractTarGz chạy qua, không cần binary docker thật).
func fakeTarGz(t *testing.T) []byte {
	t.Helper()
	return buildTarGz(t, map[string]string{
		"docker/docker":                        "fake-docker-binary",
		"docker/dockerd":                       "fake-dockerd-binary",
		"docker/dockerd-rootless-setuptool.sh": "#!/bin/sh\necho ok\n",
	}, 0o755)
}

// newBootstrapTestServer dựng một httptest.Server đóng vai download.docker.com:
// phục vụ đúng 2 tarball (chính + rootless-extras), và ghi đè tạm
// dockerStaticChecksums (qua withPinnedChecksums) để khớp nội dung tarball
// giả — không tải SHA256SUMS nữa (tệp đó không tồn tại thật, xem comment ở
// dockerStaticChecksums trong bootstrap_linux.go).
func newBootstrapTestServer(t *testing.T) (*httptest.Server, string) {
	t.Helper()
	tarball := fakeTarGz(t)
	sum := sha256.Sum256(tarball)
	sumHex := hex.EncodeToString(sum[:])

	mainName := "docker-27.3.1.tgz"
	extrasName := "docker-rootless-extras-27.3.1.tgz"

	withPinnedChecksums(t, "x86_64", "27.3.1", map[string]string{
		mainName:   sumHex,
		extrasName: sumHex,
	})

	mux := http.NewServeMux()
	mux.HandleFunc("/linux/static/stable/x86_64/"+mainName, func(w http.ResponseWriter, r *http.Request) {
		w.Write(tarball)
	})
	mux.HandleFunc("/linux/static/stable/x86_64/"+extrasName, func(w http.ResponseWriter, r *http.Request) {
		w.Write(tarball)
	})
	srv := httptest.NewServer(mux)
	return srv, sumHex
}

func TestBootstrap_DownloadsExtractsAndStopsWithoutAutoApprove(t *testing.T) {
	srv, _ := newBootstrapTestServer(t)
	defer srv.Close()

	dir := t.TempDir()
	opts := BootstrapOptions{
		RuntimeDir:  filepath.Join(dir, "runtime"),
		GOARCH:      "amd64",
		BaseURL:     srv.URL,
		Version:     "27.3.1",
		HTTP:        srv.Client(),
		AutoApprove: false,
	}

	out, err := Bootstrap(context.Background(), opts)
	if err != nil {
		t.Fatalf("Bootstrap: %v", err)
	}
	if !out.Installed {
		t.Error("Installed phải true sau khi tải+giải nén xong")
	}
	if out.ServiceStarted {
		t.Error("ServiceStarted phải false khi AutoApprove=false")
	}
	if out.ManualNextSteps == "" {
		t.Error("phải có hướng dẫn thủ công khi chưa tự chạy setuptool")
	}
	if !strings.Contains(out.ManualNextSteps, "dockerd-rootless-setuptool.sh") {
		t.Errorf("hướng dẫn phải nhắc tới dockerd-rootless-setuptool.sh, được: %s", out.ManualNextSteps)
	}

	binPath := filepath.Join(opts.RuntimeDir, "docker", "docker")
	if _, err := os.Stat(binPath); err != nil {
		t.Errorf("docker binary phải được giải nén tại %s: %v", binPath, err)
	}
}

func TestBootstrap_Idempotent_SkipsRedownloadOnSecondRun(t *testing.T) {
	srv, _ := newBootstrapTestServer(t)
	defer srv.Close()

	dir := t.TempDir()
	opts := BootstrapOptions{
		RuntimeDir: filepath.Join(dir, "runtime"),
		GOARCH:     "amd64",
		BaseURL:    srv.URL,
		Version:    "27.3.1",
		HTTP:       srv.Client(),
	}

	if _, err := Bootstrap(context.Background(), opts); err != nil {
		t.Fatalf("Bootstrap lần 1: %v", err)
	}

	// Lần 2: tắt máy chủ HTTP giả — nếu Bootstrap cố tải lại sẽ lỗi ngay,
	// nên Run thành công chứng minh nó nhận ra đã cài xong (marker) và bỏ qua.
	srv.Close()

	out, err := Bootstrap(context.Background(), opts)
	if err != nil {
		t.Fatalf("Bootstrap lần 2 (idempotent) phải không lỗi dù mạng đã tắt: %v", err)
	}
	if !out.Installed {
		t.Error("Installed phải true ở lần chạy lại")
	}
}

func TestBootstrap_ChecksumMismatch_Fails(t *testing.T) {
	withPinnedChecksums(t, "x86_64", "27.3.1", map[string]string{
		"docker-27.3.1.tgz": "0000000000000000000000000000000000000000000000000000000000000000",
	})

	mux := http.NewServeMux()
	mux.HandleFunc("/linux/static/stable/x86_64/docker-27.3.1.tgz", func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("noi dung khong khop checksum"))
	})
	srv := httptest.NewServer(mux)
	defer srv.Close()

	dir := t.TempDir()
	_, err := Bootstrap(context.Background(), BootstrapOptions{
		RuntimeDir: filepath.Join(dir, "runtime"),
		GOARCH:     "amd64",
		BaseURL:    srv.URL,
		Version:    "27.3.1",
		HTTP:       srv.Client(),
	})
	if err == nil {
		t.Fatal("muốn lỗi khi checksum không khớp")
	}
	if !strings.Contains(err.Error(), "khớp") && !strings.Contains(err.Error(), "checksum") {
		t.Errorf("lỗi phải nói rõ về checksum, được: %v", err)
	}
}

func TestBootstrap_AutoApprove_RunsSetupTool(t *testing.T) {
	srv, _ := newBootstrapTestServer(t)
	defer srv.Close()

	dir := t.TempDir()
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("install"), Output: []byte("ok\n")},
	}}

	out, err := Bootstrap(context.Background(), BootstrapOptions{
		RuntimeDir:  filepath.Join(dir, "runtime"),
		GOARCH:      "amd64",
		BaseURL:     srv.URL,
		Version:     "27.3.1",
		HTTP:        srv.Client(),
		Runner:      fr,
		AutoApprove: true,
	})
	if err != nil {
		t.Fatalf("Bootstrap: %v", err)
	}
	if !out.ServiceStarted {
		t.Error("ServiceStarted phải true khi AutoApprove=true và setuptool chạy thành công")
	}
	if len(fr.Calls) != 1 {
		t.Fatalf("muốn đúng 1 lệnh gọi tới Runner, được %d", len(fr.Calls))
	}
	if !strings.HasSuffix(fr.Calls[0].Cmd.Name, "dockerd-rootless-setuptool.sh") {
		t.Errorf("lệnh gọi phải là dockerd-rootless-setuptool.sh, được %q", fr.Calls[0].Cmd.Name)
	}
}

func TestBootstrap_AutoApprove_SetupToolFails_ReturnsManualSteps(t *testing.T) {
	srv, _ := newBootstrapTestServer(t)
	defer srv.Close()

	dir := t.TempDir()
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("install"), Err: errors.New("newuidmap: setuid bit is not set")},
	}}

	out, err := Bootstrap(context.Background(), BootstrapOptions{
		RuntimeDir:  filepath.Join(dir, "runtime"),
		GOARCH:      "amd64",
		BaseURL:     srv.URL,
		Version:     "27.3.1",
		HTTP:        srv.Client(),
		Runner:      fr,
		AutoApprove: true,
	})
	if err == nil {
		t.Fatal("muốn lỗi khi setuptool thất bại")
	}
	if out == nil || out.ManualNextSteps == "" {
		t.Error("phải trả hướng dẫn thủ công kể cả khi lỗi")
	}
}

var _ dockercli.Runner = (*fake.Runner)(nil)
