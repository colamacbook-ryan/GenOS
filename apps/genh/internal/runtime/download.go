package runtime

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

// HTTPDoer là phần tối thiểu của *http.Client cần cho tải tệp — trừu tượng
// hoá để test bằng httptest.Server thật (không mock io.Reader tay), nhưng
// vẫn tiêm được client giả khi cần mô phỏng lỗi mạng.
type HTTPDoer interface {
	Do(req *http.Request) (*http.Response, error)
}

// DownloadFile tải url vào destPath, ghi qua tệp tạm rồi rename (không để
// lại tệp dở dang nếu bị ngắt giữa chừng — cùng cách secretgen ghi bí mật),
// trả về SHA-256 (hex) của nội dung đã tải để đối chiếu với VerifyChecksum.
func DownloadFile(ctx context.Context, client HTTPDoer, url, destPath string) (sha256Hex string, err error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return "", fmt.Errorf("dựng yêu cầu tải %s: %w", url, err)
	}

	resp, err := client.Do(req)
	if err != nil {
		return "", fmt.Errorf("tải %s: %w", url, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("tải %s: máy chủ trả %s", url, resp.Status)
	}

	if err := os.MkdirAll(filepath.Dir(destPath), 0o755); err != nil {
		return "", fmt.Errorf("tạo thư mục đích %s: %w", filepath.Dir(destPath), err)
	}

	tmp := destPath + ".part"
	f, err := os.Create(tmp)
	if err != nil {
		return "", fmt.Errorf("tạo tệp tạm %s: %w", tmp, err)
	}

	h := sha256.New()
	_, copyErr := io.Copy(io.MultiWriter(f, h), resp.Body)
	closeErr := f.Close()
	if copyErr != nil {
		os.Remove(tmp)
		return "", fmt.Errorf("ghi %s: %w", tmp, copyErr)
	}
	if closeErr != nil {
		os.Remove(tmp)
		return "", fmt.Errorf("đóng %s: %w", tmp, closeErr)
	}

	if err := os.Rename(tmp, destPath); err != nil {
		os.Remove(tmp)
		return "", fmt.Errorf("đổi tên %s -> %s: %w", tmp, destPath, err)
	}

	return hex.EncodeToString(h.Sum(nil)), nil
}

// FetchExpectedChecksum tải checksumsURL (định dạng sha256sum chuẩn, mỗi
// dòng "HASH  tên-tệp" — cùng định dạng install.sh đã dùng cho
// checksums.txt) và trả về HASH khớp đúng basename của filename. So khớp
// theo basename vì các bản phát hành hay ghi "./docker-27.3.1.tgz" hoặc
// đường dẫn đầy đủ tuỳ công cụ tạo checksum.
func FetchExpectedChecksum(ctx context.Context, client HTTPDoer, checksumsURL, filename string) (string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, checksumsURL, nil)
	if err != nil {
		return "", fmt.Errorf("dựng yêu cầu tải %s: %w", checksumsURL, err)
	}
	resp, err := client.Do(req)
	if err != nil {
		return "", fmt.Errorf("tải %s: %w", checksumsURL, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("tải %s: máy chủ trả %s", checksumsURL, resp.Status)
	}
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", fmt.Errorf("đọc %s: %w", checksumsURL, err)
	}
	return ParseChecksumsFile(string(body), filename)
}

// ParseChecksumsFile là phần thuần (test được không cần HTTP) của
// FetchExpectedChecksum: tìm HASH khớp basename trong nội dung tệp
// checksums dạng "HASH  tên-tệp" mỗi dòng.
func ParseChecksumsFile(content, filename string) (string, error) {
	want := filepath.Base(filename)
	for _, line := range strings.Split(content, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 2 {
			continue
		}
		name := strings.TrimPrefix(fields[len(fields)-1], "*")
		name = strings.TrimPrefix(name, "./")
		if filepath.Base(name) == want {
			return strings.ToLower(fields[0]), nil
		}
	}
	return "", fmt.Errorf("không tìm thấy %q trong tệp checksum", want)
}

// VerifyChecksum so khớp gotHex (đã tính khi tải) với want (hex, hoa/thường
// đều được) — dừng cài đặt ngay nếu sai, không bao giờ chạy binary tải sai.
func VerifyChecksum(gotHex, want string) error {
	got := strings.ToLower(strings.TrimSpace(gotHex))
	w := strings.ToLower(strings.TrimSpace(want))
	if w == "" {
		return fmt.Errorf("checksum mong đợi rỗng")
	}
	if got != w {
		return fmt.Errorf("SHA-256 không khớp (muốn %s, được %s)", w, got)
	}
	return nil
}

// maxExtractedBytes chặn giải nén vô hạn (tarball hỏng/độc hại khai bừa
// kích thước) — Docker Engine tĩnh đầy đủ nặng khoảng 80-120MB, 2GB là dư
// dả nhiều lần cho mọi phiên bản hợp lệ.
const maxExtractedBytes = 2 << 30 // 2 GiB

// ExtractTarGz giải nén src (.tar.gz) vào destDir, giữ nguyên quyền thực thi
// của từng mục — dùng cho gói Docker Engine tĩnh (chứa docker, dockerd,
// containerd, rootlesskit, docker-compose, dockerd-rootless-setuptool.sh…).
// Từ chối mọi mục cố ghi ra ngoài destDir (path traversal qua "../") hoặc
// symlink — phòng tarball độc hại, dù nguồn đã qua VerifyChecksum.
func ExtractTarGz(src, destDir string) error {
	f, err := os.Open(src)
	if err != nil {
		return fmt.Errorf("mở %s: %w", src, err)
	}
	defer f.Close()

	gz, err := gzip.NewReader(f)
	if err != nil {
		return fmt.Errorf("%s không phải gzip hợp lệ: %w", src, err)
	}
	defer gz.Close()

	if err := os.MkdirAll(destDir, 0o755); err != nil {
		return fmt.Errorf("tạo thư mục đích %s: %w", destDir, err)
	}

	tr := tar.NewReader(gz)
	var written int64
	for {
		hdr, err := tr.Next()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return fmt.Errorf("đọc tar %s: %w", src, err)
		}

		target, err := safeJoin(destDir, hdr.Name)
		if err != nil {
			return fmt.Errorf("%s: %w", src, err)
		}

		switch hdr.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
		case tar.TypeReg:
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return err
			}
			out, err := os.OpenFile(target, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, os.FileMode(hdr.Mode)&0o777|0o600)
			if err != nil {
				return err
			}
			n, err := io.CopyN(out, tr, hdr.Size+1)
			out.Close()
			written += n
			if err != nil && err != io.EOF {
				return fmt.Errorf("ghi %s: %w", target, err)
			}
			if n > hdr.Size {
				return fmt.Errorf("%s: kích thước mục %s lớn hơn khai báo trong header (nghi tarball hỏng)", src, hdr.Name)
			}
			if written > maxExtractedBytes {
				return fmt.Errorf("%s: giải nén vượt %d byte, dừng (nghi tarball hỏng/độc hại)", src, maxExtractedBytes)
			}
			if err := os.Chmod(target, os.FileMode(hdr.Mode)&0o777); err != nil {
				return err
			}
		case tar.TypeSymlink, tar.TypeLink:
			// Bỏ qua symlink/hardlink trong gói: docker tĩnh không cần đến
			// chúng để chạy (mọi binary đều là tệp thường), và bỏ qua tránh
			// hẳn rủi ro path traversal qua đích symlink.
			continue
		default:
			continue
		}
	}
}

// safeJoin nối destDir với name (đường dẫn tương đối bên trong tarball),
// từ chối nếu kết quả thoát ra ngoài destDir.
func safeJoin(destDir, name string) (string, error) {
	clean := filepath.Clean("/" + name) // chuẩn hoá "../../x" thành "/x"
	target := filepath.Join(destDir, clean)
	if target != destDir && !strings.HasPrefix(target, destDir+string(filepath.Separator)) {
		return "", fmt.Errorf("mục %q trong tarball thoát ra ngoài thư mục đích", name)
	}
	return target, nil
}
