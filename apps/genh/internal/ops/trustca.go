package ops

import (
	"context"
	"errors"
	"fmt"
	"io"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/install"
)

// TrustCADeps cho phép tiêm runner/hàm tin cậy giả khi test — nil dùng bản
// thật của internal/install (cùng logic Bước 8 của `genh install`).
type TrustCADeps struct {
	Runner       dockercli.Runner
	TrustBrowser func(ctx context.Context, certPath string) error
	TrustSystem  func(ctx context.Context, certPath string, interactive bool) error
}

// RunTrustCA trích lại CA nội bộ của Caddy, ghi ra <gốc>/config/caddy-root.crt
// rồi tin cậy lại vào kho trình duyệt (NSS, không cần sudo) và kho hệ điều
// hành (chỉ khi chạy được không cần hỏi — xem install.TrustSystemOS).
//
// interactive=false dùng cho `genh update` (có thể chạy nền qua watcher):
// không bao giờ bật hộp thoại hay hỏi mật khẩu. Chỉ trả lỗi khi không trích/
// ghi được CA; kho nào tin cậy thất bại thì chỉ in ra out.
func RunTrustCA(ctx context.Context, env *Env, interactive bool, deps TrustCADeps, out io.Writer) error {
	runner := deps.Runner
	if runner == nil {
		runner = dockercli.ExecRunner{}
	}
	trustBrowser := deps.TrustBrowser
	if trustBrowser == nil {
		trustBrowser = install.TrustBrowserOS
	}
	trustSystem := deps.TrustSystem
	if trustSystem == nil {
		trustSystem = install.TrustSystemOS
	}

	composePath, err := env.LocatePath()
	if err != nil {
		return err
	}
	bundle, err := env.LoadSecrets()
	if err != nil {
		return err
	}
	pem, err := install.ExtractCaddyRootCert(ctx, runner, composePath, EnvOverlay(bundle))
	if err != nil {
		return &OpError{
			Code: ErrCodeTrustCAFailed,
			What: "Không đọc được CA nội bộ từ container proxy",
			Why:  err.Error(),
			Next: "Chạy `genh status` (proxy phải đang chạy) rồi thử lại `genh trust-ca`.",
			Err:  err,
		}
	}
	certPath := install.CACertPath(env.InstallDir)
	if err := install.WriteCACert(certPath, pem); err != nil {
		return &OpError{
			Code: ErrCodeTrustCAFailed,
			What: "Không ghi được CA nội bộ ra đĩa",
			Why:  err.Error(),
			Next: "Kiểm quyền ghi thư mục " + env.ConfigDir() + " rồi thử lại.",
			Err:  err,
		}
	}
	_, _ = fmt.Fprintln(out, "CA nội bộ: "+certPath)

	browserErr := trustBrowser(ctx, certPath)
	switch {
	case browserErr == nil:
		_, _ = fmt.Fprintln(out, "Trình duyệt (Chrome/Firefox): đã tin cậy")
	case errors.Is(browserErr, install.ErrBrowserTrustUnsupported):
		// macOS/Windows: trình duyệt đọc kho hệ điều hành — xem dòng dưới.
	default:
		_, _ = fmt.Fprintf(out, "Trình duyệt (Chrome/Firefox): chưa tin cậy — %v\n", browserErr)
	}

	systemErr := trustSystem(ctx, certPath, interactive)
	switch {
	case systemErr == nil:
		_, _ = fmt.Fprintln(out, "Hệ điều hành: đã tin cậy")
	case errors.Is(systemErr, install.ErrTrustSkipped):
		_, _ = fmt.Fprintln(out, "Hệ điều hành: "+systemErr.Error()+" — chạy `genh trust-ca`")
	default:
		_, _ = fmt.Fprintf(out, "Hệ điều hành: chưa tin cậy — %v (tự cài %s nếu cần)\n", systemErr, certPath)
	}
	if browserErr == nil || systemErr == nil {
		_, _ = fmt.Fprintln(out, "Đóng hẳn rồi mở lại trình duyệt để hết cảnh báo \"Not secure\".")
	}
	return nil
}
