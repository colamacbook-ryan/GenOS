//go:build !windows

// Bit thực thi là bit quyền POSIX: trên Windows, os.FileMode của tệp thường
// không bao giờ có 0o111 (tệp chạy được nhờ đuôi .exe, không nhờ bit quyền),
// nên phần kiểm này tách khỏi TestExtractTarGz_ExtractsFiles (chạy trên mọi
// hệ điều hành) sang tệp chỉ biên dịch trên Linux/macOS.

package runtime

import (
	"os"
	"path/filepath"
	"testing"
)

// Binary docker/dockerd giải nén từ docker-<ver>.tgz phải chạy được ngay.
func TestExtractTarGz_PreservesExecBit(t *testing.T) {
	dir := t.TempDir()
	src := filepath.Join(dir, "in.tar.gz")
	data := buildTarGz(t, map[string]string{
		"docker/docker": "#!/bin/sh\necho fake-docker\n",
	}, 0o755)
	if err := os.WriteFile(src, data, 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	destDir := filepath.Join(dir, "out")
	if err := ExtractTarGz(src, destDir); err != nil {
		t.Fatalf("ExtractTarGz: %v", err)
	}

	binPath := filepath.Join(destDir, "docker", "docker")
	info, err := os.Stat(binPath)
	if err != nil {
		t.Fatalf("stat %s: %v", binPath, err)
	}
	if info.Mode().Perm()&0o100 == 0 {
		t.Error("bit thực thi phải được giữ lại sau khi giải nén")
	}
}
