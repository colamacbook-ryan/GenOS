package ops

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
)

// OwnerPauseFile (v0.1.44, F-6b): <gốc cài đặt>/config/paused-by-owner.json —
// Owner CHỦ ĐỘNG dừng (`genh stop`) ⇒ trực canh máy chủ không tự khởi động lại
// dịch vụ, không gửi báo động. `genh start` (và update/install thành công) xoá.
// Nằm trong config/ (chỉ genh ghi), KHÔNG trong run/ (api ghi được).
const OwnerPauseFile = "paused-by-owner.json"

// OwnerPausePath là đường dẫn tệp đánh dấu tạm dừng.
func OwnerPausePath(installDir string) string {
	return filepath.Join(installDir, "config", OwnerPauseFile)
}

// OwnerPaused báo Owner có đang chủ động dừng không.
func OwnerPaused(installDir string) bool {
	_, err := os.Lstat(OwnerPausePath(installDir))
	return err == nil
}

// WriteOwnerPause ghi {at} vào config/paused-by-owner.json (0600).
func WriteOwnerPause(installDir string, at time.Time) error {
	path := OwnerPausePath(installDir)
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	b, _ := json.Marshal(map[string]string{"at": at.UTC().Format(time.RFC3339)})
	return os.WriteFile(path, append(b, '\n'), 0o600)
}

// ClearOwnerPause xoá đánh dấu tạm dừng (không có tệp không phải lỗi).
func ClearOwnerPause(installDir string) error {
	err := os.Remove(OwnerPausePath(installDir))
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return nil
}

// WatchdogOptOutFile: <gốc cài đặt>/config/watchdog-disabled.json — Owner đã
// CHỦ ĐỘNG tắt trực canh máy chủ (`genh watchdog disable`). install/update (kể
// cả lịch đêm) KHÔNG bật lại lịch khi có tệp này; `genh watchdog enable` xoá.
const WatchdogOptOutFile = "watchdog-disabled.json"

// WatchdogOptOutPath là đường dẫn tệp đánh dấu Owner tắt trực canh.
func WatchdogOptOutPath(installDir string) string {
	return filepath.Join(installDir, "config", WatchdogOptOutFile)
}

// WatchdogOptedOut báo Owner có đang chủ động tắt trực canh không.
func WatchdogOptedOut(installDir string) bool {
	_, err := os.Lstat(WatchdogOptOutPath(installDir))
	return err == nil
}

// SetWatchdogOptOut ghi (on=true, {at} 0600) hoặc xoá (on=false — không có tệp
// không phải lỗi) đánh dấu Owner tắt trực canh.
func SetWatchdogOptOut(installDir string, on bool, at time.Time) error {
	path := WatchdogOptOutPath(installDir)
	if !on {
		if err := os.Remove(path); err != nil && !errors.Is(err, os.ErrNotExist) {
			return err
		}
		return nil
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	b, _ := json.Marshal(map[string]string{"at": at.UTC().Format(time.RFC3339)})
	return os.WriteFile(path, append(b, '\n'), 0o600)
}

// watchdogQuiesceWait: genh stop/uninstall chờ tối đa chừng này cho lượt trực
// canh ĐANG chạy (bắt đầu trước khi có đánh dấu tạm dừng) xong hẳn.
var watchdogQuiesceWait = watchdogRunTimeout + 30*time.Second

// pauseWatchdog ghi đánh dấu "Owner chủ động dừng" TRƯỚC khi dừng/gỡ container
// rồi chờ lượt trực canh đang chạy (nếu có) xong — lượt mới thấy đánh dấu nên
// không đo, không `up -d` lại; lượt cũ không còn giữa chừng. Trả wasPaused (đã
// có đánh dấu từ trước) để người gọi biết có nên xoá lại khi thất bại.
func pauseWatchdog(ctx context.Context, installDir string, out io.Writer) (wasPaused bool, err error) {
	wasPaused = OwnerPaused(installDir)
	if err := WriteOwnerPause(installDir, time.Now()); err != nil {
		return wasPaused, err
	}
	lock, lerr := hostlink.AcquireWatchdogLockWait(ctx, installDir, watchdogQuiesceWait)
	if lerr != nil {
		_, _ = fmt.Fprintln(out, "Cảnh báo: chưa chờ được lượt trực canh đang chạy ("+lerr.Error()+") — tiếp tục dừng.")
		return wasPaused, nil
	}
	lock.Release()
	return wasPaused, nil
}

// RunStop ghi đánh dấu "Owner chủ động dừng" (trực canh không tự khởi động
// lại, không báo động) và chờ lượt trực canh đang chạy xong, RỒI MỚI chạy
// `docker compose stop` (KHÔNG rebuild, KHÔNG xoá gì). Stop lỗi ⇒ xoá lại đánh
// dấu (nếu trước đó chưa có) để trực canh tiếp tục canh.
func RunStop(ctx context.Context, env *Env, runner dockercli.Runner, out io.Writer) error {
	if runner == nil {
		runner = dockercli.ExecRunner{}
	}
	composePath, err := env.LocatePath()
	if err != nil {
		return err
	}
	bundle, err := env.LoadSecrets()
	if err != nil {
		return err
	}
	wasPaused, perr := pauseWatchdog(ctx, env.InstallDir, out)
	if perr != nil {
		_, _ = fmt.Fprintln(out, "Cảnh báo: không ghi được đánh dấu tạm dừng ("+perr.Error()+") — trực canh máy chủ có thể tự khởi động lại dịch vụ.")
	}
	args := compose.BaseArgs(composePath, "stop")
	if _, err := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: args, Env: EnvOverlay(bundle), Dir: composeDir(composePath)}); err != nil {
		if !wasPaused {
			_ = ClearOwnerPause(env.InstallDir)
		}
		return &OpError{
			Code: ErrCodeStopFailed,
			What: "`docker compose stop` thất bại",
			Why:  err.Error(),
			Next: "Xem log ở trên rồi thử lại — dữ liệu không bị mất (chỉ dừng container).",
			Err:  err,
		}
	}
	_, _ = fmt.Fprintln(out, "Đã dừng Gen-Harness (dữ liệu giữ nguyên). Trực canh máy chủ tạm nghỉ cho tới khi `genh start`.")
	return nil
}

// RunStart chạy `docker compose up -d` (KHÔNG rebuild, KHÔNG migrate lại)
// cho toàn bộ service, rồi xoá đánh dấu tạm dừng (trực canh chạy lại).
func RunStart(ctx context.Context, env *Env, runner dockercli.Runner, out io.Writer) error {
	if runner == nil {
		runner = dockercli.ExecRunner{}
	}
	composePath, err := env.LocatePath()
	if err != nil {
		return err
	}
	bundle, err := env.LoadSecrets()
	if err != nil {
		return err
	}
	args := compose.BaseArgs(composePath, "up", "-d")
	if _, err := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: args, Env: EnvOverlay(bundle), Dir: composeDir(composePath)}); err != nil {
		return &OpError{
			Code: ErrCodeStartFailed,
			What: "`docker compose up -d` thất bại",
			Why:  err.Error(),
			Next: "Xem log ở trên rồi thử lại (`genh logs`).",
			Err:  err,
		}
	}
	// v0.1.45: siết hộp thư run/ (2770 nhóm 10001) sau khi api đã lên.
	reportRunDirPerms(ctx, runner, env.InstallDir, composePath, EnvOverlay(bundle), out)
	if err := ClearOwnerPause(env.InstallDir); err != nil {
		_, _ = fmt.Fprintln(out, "Cảnh báo: không xoá được "+OwnerPausePath(env.InstallDir)+": "+err.Error())
	}
	_, _ = fmt.Fprintln(out, "Đã khởi động Gen-Harness. Xem `genh status` để kiểm healthy.")
	return nil
}
