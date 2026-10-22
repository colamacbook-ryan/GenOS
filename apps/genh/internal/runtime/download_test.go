package runtime

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestParseChecksumsFile(t *testing.T) {
	content := "aaaa111  docker-27.3.1.tgz\n" +
		"bbbb222 *docker-rootless-extras-27.3.1.tgz\n" +
		"cccc333  ./nested/docker-other.tgz\n"

	cases := []struct {
		filename string
		want     string
		wantErr  bool
	}{
		{"docker-27.3.1.tgz", "aaaa111", false},
		{"docker-rootless-extras-27.3.1.tgz", "bbbb222", false},
		{"nested/docker-other.tgz", "cccc333", false},
		{"khong-co.tgz", "", true},
	}
	for _, c := range cases {
		got, err := ParseChecksumsFile(content, c.filename)
		if c.wantErr {
			if err == nil {
				t.Errorf("ParseChecksumsFile(%q) muốn lỗi, không có", c.filename)
			}
			continue
		}
		if err != nil {
			t.Fatalf("ParseChecksumsFile(%q): %v", c.filename, err)
		}
		if got != c.want {
			t.Errorf("ParseChecksumsFile(%q) = %q, muốn %q", c.filename, got, c.want)
		}
	}
}

func TestVerifyChecksum(t *testing.T) {
	if err := VerifyChecksum("AbCd", "abcd"); err != nil {
		t.Errorf("khớp không phân biệt hoa/thường: %v", err)
	}
	if err := VerifyChecksum("abcd", "xyz"); err == nil {
		t.Error("muốn lỗi khi không khớp")
	}
	if err := VerifyChecksum("abcd", ""); err == nil {
		t.Error("muốn lỗi khi checksum mong đợi rỗng")
	}
}

func TestDownloadFile_VerifiesContentAndChecksum(t *testing.T) {
	body := []byte("noi dung gia lap cua goi docker tinh")
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write(body)
	}))
	defer srv.Close()

	dir := t.TempDir()
	dest := filepath.Join(dir, "sub", "docker.tgz")

	got, err := DownloadFile(context.Background(), srv.Client(), srv.URL, dest)
	if err != nil {
		t.Fatalf("DownloadFile: %v", err)
	}

	want := sha256.Sum256(body)
	if got != hex.EncodeToString(want[:]) {
		t.Errorf("sha256 = %s, muốn %s", got, hex.EncodeToString(want[:]))
	}

	data, err := os.ReadFile(dest)
	if err != nil {
		t.Fatalf("đọc tệp đã tải: %v", err)
	}
	if !bytes.Equal(data, body) {
		t.Error("nội dung tệp đã tải không khớp")
	}

	if _, err := os.Stat(dest + ".part"); err == nil {
		t.Error("tệp tạm .part không được để lại sau khi tải xong")
	}
}

func TestDownloadFile_NonOKStatus(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
	}))
	defer srv.Close()

	dest := filepath.Join(t.TempDir(), "x.tgz")
	if _, err := DownloadFile(context.Background(), srv.Client(), srv.URL, dest); err == nil {
		t.Fatal("muốn lỗi khi máy chủ trả 404")
	}
	if _, err := os.Stat(dest); err == nil {
		t.Error("không được tạo tệp đích khi tải thất bại")
	}
}

func TestFetchExpectedChecksum_EndToEnd(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("deadbeef  docker-27.3.1.tgz\n"))
	}))
	defer srv.Close()

	got, err := FetchExpectedChecksum(context.Background(), srv.Client(), srv.URL, "docker-27.3.1.tgz")
	if err != nil {
		t.Fatalf("FetchExpectedChecksum: %v", err)
	}
	if got != "deadbeef" {
		t.Errorf("got = %q, muốn deadbeef", got)
	}
}

// buildTarGz dựng một .tar.gz tối giản trong bộ nhớ, dùng cho test
// ExtractTarGz — không phụ thuộc tarball Docker thật.
func buildTarGz(t *testing.T, files map[string]string, mode int64) []byte {
	t.Helper()
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	for name, content := range files {
		hdr := &tar.Header{Name: name, Mode: mode, Size: int64(len(content))}
		if err := tw.WriteHeader(hdr); err != nil {
			t.Fatalf("WriteHeader: %v", err)
		}
		if _, err := tw.Write([]byte(content)); err != nil {
			t.Fatalf("Write: %v", err)
		}
	}
	if err := tw.Close(); err != nil {
		t.Fatalf("tw.Close: %v", err)
	}
	if err := gz.Close(); err != nil {
		t.Fatalf("gz.Close: %v", err)
	}
	return buf.Bytes()
}

// Bit thực thi sau khi giải nén: kiểm ở download_unix_test.go (bit quyền
// POSIX — Windows không có bit thực thi trong os.FileMode).
func TestExtractTarGz_ExtractsFiles(t *testing.T) {
	dir := t.TempDir()
	src := filepath.Join(dir, "in.tar.gz")
	data := buildTarGz(t, map[string]string{
		"docker/docker":  "#!/bin/sh\necho fake-docker\n",
		"docker/LICENSE": "MIT",
	}, 0o755)
	if err := os.WriteFile(src, data, 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	destDir := filepath.Join(dir, "out")
	if err := ExtractTarGz(src, destDir); err != nil {
		t.Fatalf("ExtractTarGz: %v", err)
	}

	binPath := filepath.Join(destDir, "docker", "docker")
	if _, err := os.Stat(binPath); err != nil {
		t.Fatalf("stat %s: %v", binPath, err)
	}

	content, err := os.ReadFile(filepath.Join(destDir, "docker", "LICENSE"))
	if err != nil {
		t.Fatalf("đọc LICENSE: %v", err)
	}
	if string(content) != "MIT" {
		t.Errorf("nội dung LICENSE = %q, muốn MIT", content)
	}
}

func TestExtractTarGz_NeutralizesPathTraversal(t *testing.T) {
	dir := t.TempDir()
	src := filepath.Join(dir, "evil.tar.gz")
	data := buildTarGz(t, map[string]string{
		"../../etc/passwd": "root:x:0:0",
	}, 0o644)
	if err := os.WriteFile(src, data, 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	destDir := filepath.Join(dir, "out")
	if err := ExtractTarGz(src, destDir); err != nil {
		t.Fatalf("ExtractTarGz: %v", err)
	}

	// "../../etc/passwd" phải bị kẹp lại bên trong destDir (không thoát ra
	// ngoài, ví dụ ghi đè /etc/passwd thật) — kiểm bằng cách xác nhận không
	// có tệp nào bị tạo ra bên ngoài destDir và một bản an toàn nằm bên
	// trong destDir.
	outsidePath := filepath.Join(dir, "etc", "passwd")
	if _, err := os.Stat(outsidePath); err == nil {
		t.Fatalf("tệp bị ghi ra ngoài destDir tại %s — path traversal không được chặn", outsidePath)
	}

	insidePath := filepath.Join(destDir, "etc", "passwd")
	if _, err := os.Stat(insidePath); err != nil {
		t.Fatalf("muốn tệp được kẹp an toàn vào bên trong destDir tại %s: %v", insidePath, err)
	}
}
