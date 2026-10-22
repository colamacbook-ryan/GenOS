//go:build windows

package runtime

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
)

// wslDistroName là tên bản phân phối WSL2 tối giản genh nhập vào — tách
// khỏi các distro khác Owner có thể đã có (Ubuntu, Debian…).
const wslDistroName = "gen-harness"

// BootstrapOptions/Outcome: cùng hình dạng với bootstrap_linux.go/
// bootstrap_darwin.go để steps_runtime.go gọi giống nhau bất kể GOOS.
type BootstrapOptions struct {
	RuntimeDir string
	// RootfsURL trỏ tới rootfs Alpine tối giản đóng gói kèm bản phát hành
	// genh (tài liệu: "nhập bản phân phối tối giản gen-harness (rootfs
	// Alpine đóng gói kèm bản phát hành) chứa Docker Engine"). Bản phát
	// hành thật (ngoài phạm vi phiên này — xem "CI phát hành") sẽ đính kèm
	// rootfs này vào GitHub Release; BaseURL trỏ tới URL thật đó.
	BaseURL string
	// RootfsChecksum là SHA-256 hex mong đợi của rootfs — genh ghim cứng
	// cùng bản phát hành đang chạy để không tải nhầm rootfs bị thay đổi.
	RootfsChecksum string

	HTTP   HTTPDoer
	Runner dockercli.Runner

	// AutoApprove: Owner đã đồng ý trước việc bật tính năng Windows
	// "Subsystem for Linux" (cần UAC một lần, có thể cần khởi động lại máy
	// — tài liệu: "cần UAC một lần, có thể cần khởi động lại"). Đây không
	// phải sudo nhưng vẫn là thay đổi hệ thống rộng, nên vẫn hỏi trước.
	AutoApprove bool

	OnProgress func(stage string, current, total int64)
}

type Outcome struct {
	Installed       bool // đã bật tính năng WSL2 (hoặc đã bật sẵn)
	ServiceStarted  bool // đã `wsl --import` xong bản phân phối gen-harness
	BinDir          string
	ManualNextSteps string
	// RestartRequired: bật tính năng WSL2 trên Windows đôi khi cần khởi
	// động lại trước khi `wsl --import` chạy được — tài liệu nói trình cài
	// "tự tiếp tục sau khi đăng nhập lại qua RunOnce". Việc đăng ký RunOnce
	// thật (khoá registry) KHÔNG được cài trong phiên này — xem giới hạn ở
	// docstring của Bootstrap.
	RestartRequired bool
}

// Bootstrap bật WSL2 (nếu chưa bật) rồi `wsl --import` một rootfs Alpine
// tối giản chứa Docker Engine vào wslDistroName, theo
// docs/handoff/05-installer.md hàng "Windows".
//
// GIỚI HẠN LỚN của bước này, ghi rõ để không hiểu nhầm là đã hoàn thiện:
//  1. Tệp này CHỈ biên dịch trên GOOS=windows (xác nhận qua
//     `GOOS=windows GOARCH=amd64 go build ./...`) — chưa từng chạy thật,
//     sandbox phiên này là Linux.
//  2. Chưa có rootfs Alpine + Docker Engine nào được đóng gói/phát hành
//     thật (thuộc "CI phát hành GitHub Actions", ngoài phạm vi phiên này) —
//     BootstrapOptions.BaseURL/RootfsChecksum phải được điền khi có.
//  3. Đăng ký RunOnce để tự tiếp tục sau khi khởi động lại máy là thao tác
//     registry Windows thật (HKCU\...\RunOnce) — CHƯA cài trong phiên này;
//     Outcome.RestartRequired chỉ báo hiệu cho `cmd/genh` in hướng dẫn thủ
//     công, chưa tự động chạy lại `genh install` sau khi đăng nhập lại.
func Bootstrap(ctx context.Context, opts BootstrapOptions) (*Outcome, error) {
	if opts.Runner == nil {
		opts.Runner = dockercli.ExecRunner{}
	}
	report := opts.OnProgress
	if report == nil {
		report = func(string, int64, int64) {}
	}

	out := &Outcome{}

	enabled, err := wslFeatureEnabled(ctx, opts.Runner)
	if err != nil {
		return nil, fmt.Errorf("kiểm tính năng WSL2: %w", err)
	}
	if !enabled {
		if !opts.AutoApprove {
			out.ManualNextSteps = "bật WSL2: dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart" +
				" rồi khởi động lại máy, hoặc chạy lại `genh install --yes` (sẽ cần UAC)"
			return out, nil
		}
		if err := enableWSLFeature(ctx, opts.Runner); err != nil {
			out.ManualNextSteps = "tự bật WSL2 qua Settings > Apps > Optional features > More Windows features > Windows Subsystem for Linux, rồi khởi động lại máy"
			return out, fmt.Errorf("bật tính năng WSL2: %w", err)
		}
		out.Installed = true
		out.RestartRequired = true
		out.ManualNextSteps = "đã bật WSL2 — khởi động lại máy rồi chạy lại `genh install` để tiếp tục"
		return out, nil
	}
	out.Installed = true

	if opts.BaseURL == "" || opts.RootfsChecksum == "" {
		out.ManualNextSteps = "WSL2 đã bật nhưng chưa có rootfs gen-harness để nhập — bản phát hành genh chưa đính kèm rootfs (xem giới hạn trong internal/runtime/bootstrap_windows.go), cài Docker Desktop thủ công để tiếp tục"
		return out, nil
	}

	rootfsPath := filepath.Join(opts.RuntimeDir, "wsl", "rootfs.tar")
	if _, statErr := os.Stat(rootfsPath); statErr != nil {
		if err := os.MkdirAll(filepath.Dir(rootfsPath), 0o755); err != nil {
			return nil, err
		}
		report("rootfs", 0, 0)
		got, err := DownloadFile(ctx, opts.HTTP, opts.BaseURL, rootfsPath)
		if err != nil {
			return nil, fmt.Errorf("tải rootfs: %w", err)
		}
		if err := VerifyChecksum(got, opts.RootfsChecksum); err != nil {
			os.Remove(rootfsPath)
			return nil, fmt.Errorf("rootfs: %w", err)
		}
	}

	importDir := filepath.Join(opts.RuntimeDir, "wsl", wslDistroName)
	if err := os.MkdirAll(importDir, 0o755); err != nil {
		return nil, err
	}

	if !opts.AutoApprove {
		out.ManualNextSteps = fmt.Sprintf("chạy: wsl --import %s %q %q --version 2    (hoặc chạy lại `genh install --yes`)", wslDistroName, importDir, rootfsPath)
		return out, nil
	}

	_, err = opts.Runner.Output(ctx, dockercli.Cmd{
		Name: "wsl.exe",
		Args: []string{"--import", wslDistroName, importDir, rootfsPath, "--version", "2"},
	})
	if err != nil {
		out.ManualNextSteps = fmt.Sprintf("tự chạy: wsl --import %s %q %q --version 2", wslDistroName, importDir, rootfsPath)
		return out, fmt.Errorf("wsl --import %s: %w", wslDistroName, err)
	}
	out.ServiceStarted = true
	out.BinDir = importDir
	return out, nil
}

// wslFeatureEnabled hỏi DISM tính năng Microsoft-Windows-Subsystem-Linux đã
// bật hay chưa — tách hàm để test được phần phân tích output qua Runner giả.
func wslFeatureEnabled(ctx context.Context, r dockercli.Runner) (bool, error) {
	out, err := r.Output(ctx, dockercli.Cmd{
		Name: "dism.exe",
		Args: []string{"/online", "/get-featureinfo", "/featurename:Microsoft-Windows-Subsystem-Linux"},
	})
	if err != nil {
		return false, err
	}
	return ParseDismFeatureState(string(out)) == "Enabled", nil
}

// ParseDismFeatureState đọc dòng "State : Enabled/Disabled" từ output của
// `dism.exe /get-featureinfo` — hàm thuần, test bằng output mẫu (không cần
// máy Windows/DISM thật).
func ParseDismFeatureState(output string) string {
	const marker = "State : "
	for _, line := range strings.Split(output, "\n") {
		line = strings.TrimRight(line, "\r")
		if strings.HasPrefix(strings.TrimLeft(line, " "), marker) {
			return strings.TrimSpace(strings.TrimPrefix(strings.TrimLeft(line, " "), marker))
		}
	}
	return ""
}

func enableWSLFeature(ctx context.Context, r dockercli.Runner) error {
	_, err := r.Output(ctx, dockercli.Cmd{
		Name: "dism.exe",
		Args: []string{"/online", "/enable-feature", "/featurename:Microsoft-Windows-Subsystem-Linux", "/all", "/norestart"},
	})
	return err
}
