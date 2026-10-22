//go:build darwin

package runtime

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"runtime"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
)

// colimaVersion/limaVersion: bản tĩnh genh tự tải khi macOS chưa có
// Docker Desktop/OrbStack/Colima chạy sẵn, theo
// docs/handoff/05-installer.md hàng "macOS". Ghim cứng như dockerStaticVersion
// ở bản Linux — nâng cấp là thay đổi có chủ đích.
const (
	colimaVersion = "0.7.5"
	limaVersion   = "0.23.2"
)

// colimaAssetName/limaAssetName trả về tên tệp phát hành đúng kiến trúc —
// Colima/Lima đặt tên release theo "<goos>_<goarch>" (Lima) hoặc
// "Darwin-<arch>" (Colima), khác quy ước download.docker.com nên tách hàm
// riêng thay vì dùng chung dockerArchName của bản Linux.
func colimaAssetName(goarch string) string {
	arch := goarch
	if arch == "amd64" {
		arch = "x86_64"
	}
	return fmt.Sprintf("colima-Darwin-%s", arch)
}

func limaAssetName(goarch string) string {
	arch := goarch
	if arch == "amd64" {
		arch = "x86_64"
	}
	return fmt.Sprintf("lima-%s-Darwin-%s.tar.gz", limaVersion, arch)
}

// BootstrapOptions/Outcome: cùng hình dạng với bản Linux (xem
// bootstrap_linux.go) để internal/install/steps_runtime.go gọi giống nhau
// bất kể GOOS — chỉ một trong ba tệp bootstrap_*.go được biên dịch theo
// build tag, đúng cách internal/machine tách probeRAM/probeDiskFree.
type BootstrapOptions struct {
	RuntimeDir string
	GOARCH     string
	BaseURL    string // gốc tải Colima; trống = GitHub Releases thật

	HTTP   HTTPDoer
	Runner dockercli.Runner

	// AutoApprove: Owner đã đồng ý trước cho phép genh tạo VM Lima
	// "gen-harness" (4 CPU, 6GB RAM, 60GB đĩa — theo tài liệu). Không cần
	// sudo trên macOS (Lima chạy trong user space), nhưng vẫn hỏi trước vì
	// đây là một VM chiếm tài nguyên lâu dài trên máy Owner.
	AutoApprove bool

	OnProgress func(stage string, current, total int64)
}

type Outcome struct {
	Installed       bool
	ServiceStarted  bool
	BinDir          string
	ManualNextSteps string
}

// vmName là tên VM Lima/Colima genh tạo — tách khỏi các VM khác Owner có
// thể đã tự tạo bằng Colima của riêng họ.
const vmName = "gen-harness"

// Bootstrap tải Colima + Lima + docker CLI + compose plugin bản tĩnh vào
// opts.RuntimeDir, rồi (chỉ khi AutoApprove) tạo/khởi động VM "gen-harness".
//
// GIỚI HẠN: tệp này CHỈ biên dịch trên GOOS=darwin (xác nhận qua
// `GOOS=darwin GOARCH=arm64 go build ./...`) — không chạy được thật trong
// sandbox Linux của phiên này. Logic tải+kiểm checksum dùng chung
// download.go (đã test trên Linux vì không có gì đặc thù macOS); phần
// `colima start` chỉ được viết đúng theo tài liệu, chưa từng chạy thật.
func Bootstrap(ctx context.Context, opts BootstrapOptions) (*Outcome, error) {
	if opts.GOARCH == "" {
		opts.GOARCH = runtime.GOARCH
	}
	if opts.Runner == nil {
		opts.Runner = dockercli.ExecRunner{}
	}
	report := opts.OnProgress
	if report == nil {
		report = func(string, int64, int64) {}
	}

	binDir := filepath.Join(opts.RuntimeDir, "colima", "bin")
	marker := filepath.Join(opts.RuntimeDir, "colima", ".installed-"+colimaVersion)

	if _, err := os.Stat(marker); err != nil {
		if err := os.MkdirAll(binDir, 0o755); err != nil {
			return nil, err
		}

		// GHI CHÚ: cả hai bản phát hành (abiosoft/colima, lima-vm/lima) đều
		// xuất bản một tệp "checksums.txt" dạng sha256sum chuẩn cùng thư mục
		// release — cùng định dạng ParseChecksumsFile đã dùng cho Linux.
		// Tên asset chính xác không kiểm chứng được từ sandbox Linux này
		// (không có máy macOS/tài khoản GitHub API để liệt kê asset thật);
		// nếu bản phát hành đổi tên tệp checksum, downloadVerifiedDarwin trả
		// lỗi rõ ràng (GH-E023) thay vì âm thầm bỏ qua kiểm chứng.
		report("colima", 0, 0)
		colimaBin := filepath.Join(binDir, "colima")
		if err := downloadVerifiedDarwin(ctx, opts, colimaReleaseBase(opts.BaseURL)+"/checksums.txt",
			colimaDownloadURL(opts.BaseURL, opts.GOARCH), colimaBin, "colima", report); err != nil {
			return nil, err
		}
		if err := os.Chmod(colimaBin, 0o755); err != nil {
			return nil, err
		}

		report("lima", 0, 0)
		limaTar := filepath.Join(opts.RuntimeDir, "downloads", limaAssetName(opts.GOARCH))
		if err := downloadVerifiedDarwin(ctx, opts, limaReleaseBase(opts.BaseURL)+"/checksums.txt",
			limaDownloadURL(opts.BaseURL, opts.GOARCH), limaTar, "lima", report); err != nil {
			return nil, err
		}
		if err := ExtractTarGz(limaTar, filepath.Join(opts.RuntimeDir, "colima")); err != nil {
			return nil, fmt.Errorf("giải nén lima: %w", err)
		}

		if err := os.WriteFile(marker, []byte(colimaVersion+"\n"), 0o644); err != nil {
			return nil, err
		}
	}

	out := &Outcome{Installed: true, BinDir: binDir}

	colimaBin := filepath.Join(binDir, "colima")
	if !opts.AutoApprove {
		out.ManualNextSteps = fmt.Sprintf(
			"chạy: PATH=%s:$PATH %s start %s --cpu 4 --memory 6 --disk 60    (hoặc chạy lại `genh install --yes`)",
			binDir, colimaBin, vmName)
		return out, nil
	}

	_, err := opts.Runner.Output(ctx, dockercli.Cmd{
		Name: colimaBin,
		Args: []string{"start", vmName, "--cpu", "4", "--memory", "6", "--disk", "60"},
		Env:  []string{"PATH=" + binDir + ":" + os.Getenv("PATH")},
	})
	if err != nil {
		out.ManualNextSteps = fmt.Sprintf("tự chạy: PATH=%s:$PATH %s start %s --cpu 4 --memory 6 --disk 60", binDir, colimaBin, vmName)
		return out, fmt.Errorf("colima start %s: %w", vmName, err)
	}
	out.ServiceStarted = true
	return out, nil
}

func colimaReleaseBase(baseURL string) string {
	if baseURL != "" {
		return baseURL
	}
	return "https://github.com/abiosoft/colima/releases/download/v" + colimaVersion
}

func limaReleaseBase(baseURL string) string {
	if baseURL != "" {
		return baseURL
	}
	return "https://github.com/lima-vm/lima/releases/download/v" + limaVersion
}

func colimaDownloadURL(baseURL, goarch string) string {
	return colimaReleaseBase(baseURL) + "/" + colimaAssetName(goarch)
}

func limaDownloadURL(baseURL, goarch string) string {
	return limaReleaseBase(baseURL) + "/" + limaAssetName(goarch)
}

// downloadVerifiedDarwin tải fileURL, kiểm SHA-256 theo checksumsURL (định
// dạng sha256sum chuẩn — xem ParseChecksumsFile), dừng và xoá tệp nếu sai.
func downloadVerifiedDarwin(ctx context.Context, opts BootstrapOptions, checksumsURL, fileURL, destPath, label string, report func(string, int64, int64)) error {
	if _, err := os.Stat(destPath); err == nil {
		return nil
	}
	want, err := FetchExpectedChecksum(ctx, opts.HTTP, checksumsURL, filepath.Base(fileURL))
	if err != nil {
		return fmt.Errorf("lấy checksum cho %s: %w", label, err)
	}
	report(label, 0, 0)
	got, err := DownloadFile(ctx, opts.HTTP, fileURL, destPath)
	if err != nil {
		return fmt.Errorf("tải %s: %w", label, err)
	}
	if err := VerifyChecksum(got, want); err != nil {
		os.Remove(destPath)
		return fmt.Errorf("%s: %w", label, err)
	}
	return nil
}
