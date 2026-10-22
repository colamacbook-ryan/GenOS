package ops

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"os"
	"runtime"
	"strings"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/autoupdate"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/browseropen"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
)

// UninstallOptions là các cờ đã phân tích của `genh uninstall`.
type UninstallOptions struct {
	// KeepData (--keep-data): từ v0.1.40 mặc định đã GIỮ dữ liệu — cờ vẫn nhận
	// (không làm gì thêm) để script cũ không hỏng.
	KeepData bool
	// DeleteData (--delete-data, v0.1.40): MỚI xoá volume dữ liệu (`down
	// --volumes`). Không có AutoApprove thì Owner phải gõ đúng "XOÁ DỮ LIỆU".
	DeleteData bool
	// AutoApprove bỏ qua hỏi xác nhận — ứng với cờ CLI `genh uninstall --yes`
	// (thêm ở phiên e2e-install, cần cho kịch bản không tương tác: CI, script
	// tự động gỡ cài). Test gọi RunUninstall trực tiếp cũng dùng trường này để
	// không phải mô phỏng stdin cho từng test case.
	AutoApprove bool
	// Offsite gỡ lịch tuần bản sao ngoài máy (nil = lịch thật theo hệ điều hành).
	Offsite OffsiteScheduler
	// Watchdog gỡ lịch trực canh máy chủ (v0.1.44; nil = lịch thật theo hệ điều hành).
	Watchdog WatchdogScheduler
	// Now cho test (nil = time.Now) — để cảnh báo "Chưa có bản sao ngoài máy gần đây".
	Now func() time.Time
}

// deleteDataPhrase là cụm Owner phải gõ để xác nhận xoá dữ liệu khi không có --yes.
const deleteDataPhrase = "XOÁ DỮ LIỆU"

// ansiRed/ansiReset tô đỏ cảnh báo (terminal không hỗ trợ thì chỉ thấy ký tự thừa).
const (
	ansiRed   = "\033[1;31m"
	ansiReset = "\033[0m"
)

// confirmDeletePhrase đọc một dòng và so với deleteDataPhrase (chấp nhận cả cách
// viết "XÓA DỮ LIỆU" — cùng một chữ, khác chỗ đặt dấu).
func confirmDeletePhrase(in io.Reader) bool {
	scanner := bufio.NewScanner(in)
	if !scanner.Scan() {
		return false
	}
	answer := strings.TrimSpace(scanner.Text())
	return answer == deleteDataPhrase || answer == "XÓA DỮ LIỆU"
}

// RunUninstall gỡ container (+ volume CHỈ khi --delete-data, v0.1.40) + lối tắt
// desktop + dòng PATH mà install.sh/install.ps1 đã thêm + lịch tuần bản sao
// ngoài máy + lịch trực canh máy chủ (v0.1.44).
//
// GIỚI HẠN QUAN TRỌNG (đọc kỹ trước khi coi lệnh này "gỡ sạch"): tài liệu
// nói "Gỡ sạch container, runtime do genh cài, lối tắt, PATH". internal/
// install/steps_runtime.go (Bước 2) hiện KHÔNG ghi lại bất kỳ đánh dấu nào
// phân biệt "runtime genh tự cài" (Docker rootless/Colima/WSL rootfs) với
// "runtime đã có sẵn trên máy Owner từ trước" — không có marker file nào
// trong ~/.gen-harness/runtime để đọc lại. Tự đoán rồi gỡ nhầm Docker Engine/
// Colima của Owner có thể phá vỡ các phần mềm KHÁC đang dùng chung runtime
// đó, hậu quả nặng hơn nhiều so với việc để lại một runtime không dùng nữa.
// Vì vậy RunUninstall CHỈ gỡ những gì chắc chắn do chính genh quản lý
// (container/volume của compose.yaml, lối tắt, dòng PATH) — KHÔNG đụng tới
// ~/.gen-harness/runtime. Owner tự gỡ runtime nếu muốn (thông báo in ra khi
// chạy lệnh này).
func RunUninstall(ctx context.Context, env *Env, opts UninstallOptions, runner dockercli.Runner, in io.Reader, out io.Writer) error {
	if runner == nil {
		runner = dockercli.ExecRunner{}
	}

	if opts.DeleteData && opts.KeepData {
		return &OpError{
			Code: ErrCodeUninstallCancelled,
			What: "Không gỡ gì cả — --keep-data và --delete-data mâu thuẫn nhau",
			Next: "Chọn một: bỏ --delete-data để giữ dữ liệu (mặc định), hoặc bỏ --keep-data để xoá dữ liệu.",
		}
	}
	now := time.Now()
	if opts.Now != nil {
		now = opts.Now()
	}
	if opts.DeleteData && !OffsiteRecentSuccess(env.InstallDir, now) {
		_, _ = fmt.Fprintln(out, ansiRed+"CẢNH BÁO: Chưa có bản sao ngoài máy gần đây (7 ngày) — xoá dữ liệu là MẤT HẲN, không khôi phục được."+ansiReset)
		_, _ = fmt.Fprintln(out, ansiRed+"  Nên chạy `genh offsite run` (hoặc `genh export --to <tệp>`) và cất gói + Bộ khôi phục ra ngoài máy trước."+ansiReset)
	}

	if !opts.AutoApprove {
		_, _ = fmt.Fprintln(out, "Gỡ Gen-Harness khỏi máy này?")
		if opts.DeleteData {
			_, _ = fmt.Fprintln(out, "  --delete-data: TOÀN BỘ dữ liệu (CSDL, tệp đã tải lên) sẽ bị XOÁ VĨNH VIỄN.")
			_, _ = fmt.Fprintln(out, "Gõ đúng \""+deleteDataPhrase+"\" để xác nhận (gõ khác = huỷ):")
			if !confirmDeletePhrase(in) {
				return &OpError{
					Code: ErrCodeUninstallCancelled,
					What: "Đã huỷ — không gỡ gì cả, dữ liệu còn nguyên",
					Next: "Muốn xoá cả dữ liệu: chạy lại `genh uninstall --delete-data` và gõ đúng \"" + deleteDataPhrase + "\". Muốn giữ dữ liệu: `genh uninstall`.",
				}
			}
		} else {
			_, _ = fmt.Fprintln(out, "  Dữ liệu (CSDL, tệp) sẽ được GIỮ LẠI trong volume Docker (cài lại vào ĐÚNG thư mục cài này là thấy). Muốn xoá cả dữ liệu: --delete-data.")
			_, _ = fmt.Fprintln(out, "Tiếp tục? [y/N]")
			if !confirmYesNo(in) {
				return &OpError{
					Code: ErrCodeUninstallCancelled,
					What: "Đã huỷ — không gỡ gì cả",
					Next: "Chạy lại `genh uninstall` khi chắc chắn.",
				}
			}
		}
	}

	composePath, locErr := env.LocatePath()
	if locErr != nil {
		// Không có compose.yaml nghĩa là không có gì để `docker compose down`
		// — vẫn tiếp tục gỡ lối tắt/PATH thay vì dừng hẳn (idempotent, an
		// toàn để chạy lại `genh uninstall` nhiều lần).
		_, _ = fmt.Fprintln(out, "Không tìm thấy deploy/compose.yaml — bỏ qua `docker compose down` ("+locErr.Error()+").")
	} else {
		sub := []string{"down"}
		if opts.DeleteData {
			sub = append(sub, "--volumes")
		}
		downArgs := compose.BaseArgs(composePath, sub...)

		// Đánh dấu tạm dừng + chờ lượt trực canh đang chạy TRƯỚC khi down: lượt
		// rơi vào khe giữa down và gỡ lịch không được `up -d` lại api (tạo lại
		// container/volume rỗng sau khi gỡ). Đánh dấu nằm trong config/ — còn
		// lại sau khi gỡ, cài lại/`genh start` sẽ xoá.
		if _, perr := pauseWatchdog(ctx, env.InstallDir, out); perr != nil {
			_, _ = fmt.Fprintln(out, "Cảnh báo: không ghi được đánh dấu tạm dừng trực canh ("+perr.Error()+").")
		}

		var envOverlay []string
		if bundle, secErr := env.LoadSecrets(); secErr == nil {
			envOverlay = EnvOverlay(bundle)
		}
		if _, err := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: downArgs, Env: envOverlay, Dir: composeDir(composePath)}); err != nil {
			return &OpError{
				Code: ErrCodeUninstallFailed,
				What: "`docker compose down` thất bại",
				Why:  err.Error(),
				Next: "Xem log ở trên; có thể cần `docker compose down` tay rồi chạy lại `genh uninstall`.",
				Err:  err,
			}
		}
		_, _ = fmt.Fprintln(out, "Đã dừng và gỡ container"+volumesSuffix(!opts.DeleteData)+".")
		if !opts.DeleteData {
			_, _ = fmt.Fprintln(out, keepDataNote(env.InstallDir))
		}
	}

	// Gỡ lịch tuần bản sao ngoài máy (v0.1.40) — không để lịch gọi một bản cài đã gỡ.
	// v0.1.53 (F-98): lịch là CHUNG cho mọi bản cài của người dùng — đang thuộc bản
	// cài KHÁC còn sống (gỡ bản phụ) thì GIỮ NGUYÊN, không gỡ lịch của bản chính.
	if other, kept := scheduleKeptForOther(env, autoupdate.OffsiteTaskName); kept {
		_, _ = fmt.Fprintln(out, "Giữ nguyên lịch sao lưu ra ổ ngoài của bản cài "+other+" (không phải bản đang gỡ).")
	} else {
		offsite := opts.Offsite
		if offsite == nil {
			offsite = NewOffsiteScheduler(env)
		}
		octx, ocancel := context.WithTimeout(ctx, 20*time.Second)
		if msg, err := offsite.Disable(octx); err != nil {
			_, _ = fmt.Fprintln(out, "Không gỡ được lịch sao lưu ra ổ ngoài: "+err.Error())
		} else {
			_, _ = fmt.Fprintln(out, msg)
		}
		ocancel()
	}

	// Gỡ lịch trực canh máy chủ (v0.1.44) — không để lịch 12 phút gọi bản cài đã gỡ
	// (cũng giữ nguyên nếu thuộc bản cài khác còn sống, như trên).
	if other, kept := scheduleKeptForOther(env, autoupdate.WatchdogTaskName); kept {
		_, _ = fmt.Fprintln(out, "Giữ nguyên lịch trực canh máy chủ của bản cài "+other+" (không phải bản đang gỡ).")
	} else {
		watchdog := opts.Watchdog
		if watchdog == nil {
			watchdog = NewWatchdogScheduler(env)
		}
		wctx, wcancel := context.WithTimeout(ctx, 20*time.Second)
		if msg, err := watchdog.Disable(wctx); err != nil {
			_, _ = fmt.Fprintln(out, "Không gỡ được lịch trực canh máy chủ: "+err.Error())
		} else {
			_, _ = fmt.Fprintln(out, msg)
		}
		wcancel()
	}

	if path, err := browseropen.ShortcutPath(); err == nil {
		if rmErr := os.Remove(path); rmErr == nil {
			_, _ = fmt.Fprintln(out, "Đã gỡ lối tắt "+path)
		} else if !os.IsNotExist(rmErr) {
			_, _ = fmt.Fprintln(out, "Không gỡ được lối tắt "+path+": "+rmErr.Error())
		}
	}

	if removed, rcFile, rmErr := removeFromShellRC(); rmErr != nil {
		_, _ = fmt.Fprintln(out, "Không tự gỡ được PATH khỏi "+rcFile+": "+rmErr.Error()+" — tự xoá dòng \"# Gen-Harness (genh)\" trong tệp đó nếu cần.")
	} else if removed {
		_, _ = fmt.Fprintln(out, "Đã gỡ dòng PATH khỏi "+rcFile+" — mở phiên shell mới để có hiệu lực.")
	} else if rcFile != "" {
		_, _ = fmt.Fprintln(out, "Không thấy dòng PATH của Gen-Harness trong "+rcFile+" (có thể đã gỡ trước đó, hoặc PATH được thêm theo cách khác).")
	}

	_, _ = fmt.Fprintln(out, "\nGIỚI HẠN: genh uninstall KHÔNG tự gỡ Docker Engine/Colima/WSL kể cả khi genh đã tự cài ở Bước 2 (không có cách phân biệt an toàn với runtime sẵn có của Owner) — tự gỡ tay nếu không còn cần, xem ~/.gen-harness/runtime.")
	return nil
}

// scheduleKeptForOther: lịch systemd --user `taskName` (unit <taskName>.service) đang
// thuộc một bản cài KHÁC env.InstallDir mà bản đó còn sống (có config/secrets.json)
// ⇒ (thư mục bản kia, true) — RunUninstall không gỡ. Cron/LaunchAgent do chính
// autoupdate.Disable* kiểm (Deps.InstallDir).
func scheduleKeptForOther(env *Env, taskName string) (string, bool) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", false
	}
	return autoupdate.OwnedByOther(home, taskName+".service", env.InstallDir)
}

// keepDataNote: volume giữ lại (pg_data…) vẫn khoá bằng mật khẩu CŨ trong
// secrets/ + .env của thư mục cài. Cài lại vào thư mục khác (hoặc sau khi xoá
// thư mục cài) sẽ sinh mật khẩu MỚI ⇒ migrate lỗi xác thực với CSDL cũ.
func keepDataNote(installDir string) string {
	return "Lưu ý: dữ liệu giữ lại chỉ mở được bằng mật khẩu trong thư mục cài " + installDir +
		" (secrets/, .env) — cài lại phải dùng đúng thư mục này (--install-dir " + installDir + "), đừng xoá nó. " +
		"Muốn xoá thư mục cài thì xoá dữ liệu trước: genh uninstall --delete-data (hoặc docker volume rm các volume của Gen-Harness)."
}

func volumesSuffix(keepData bool) string {
	if keepData {
		return " (giữ nguyên dữ liệu trong volume Docker — xoá hẳn: genh uninstall --delete-data)"
	}
	return " và volume dữ liệu"
}

// removeFromShellRC gỡ đúng khối "# Gen-Harness (genh)\nexport PATH=..." mà
// install.sh đã thêm (xem hàm add_to_path trong install.sh) khỏi rc file
// tương ứng ($HOME/.zshrc, $HOME/.bashrc, hoặc $HOME/.profile mặc định —
// cùng thứ tự ưu tiên theo $SHELL mà install.sh dùng để CHỌN rc file lúc
// thêm, nên phải dò đúng thứ tự đó để gỡ đúng chỗ).
//
// GIỚI HẠN: chỉ xử lý Linux/macOS (rc file dạng POSIX shell, đúng những gì
// install.sh sửa). Trên Windows, install.ps1 sửa biến PATH của user qua
// registry ([Environment]::SetEnvironmentVariable('Path', ..., 'User')) —
// không phải một rc file — gỡ đúng chỗ đó cần gọi Windows API tương ứng,
// CHƯA triển khai ở đây (không có Windows thật để test trong môi trường
// viết code này) để tránh rủi ro ghi sai biến PATH hệ thống của Owner mà
// không kiểm chứng được.
func removeFromShellRC() (removed bool, rcFile string, err error) {
	if strings.EqualFold(runtime.GOOS, "windows") {
		return false, "", nil
	}

	home, err := os.UserHomeDir()
	if err != nil {
		return false, "", err
	}
	rcFile = home + "/.profile"
	switch {
	case strings.HasSuffix(os.Getenv("SHELL"), "/zsh"):
		rcFile = home + "/.zshrc"
	case strings.HasSuffix(os.Getenv("SHELL"), "/bash"):
		rcFile = home + "/.bashrc"
	}

	data, err := os.ReadFile(rcFile)
	if err != nil {
		if os.IsNotExist(err) {
			return false, rcFile, nil
		}
		return false, rcFile, err
	}

	lines := strings.Split(string(data), "\n")
	var kept []string
	skipNext := false
	found := false
	for _, l := range lines {
		if skipNext {
			skipNext = false
			found = true
			continue
		}
		if strings.TrimSpace(l) == "# Gen-Harness (genh)" {
			skipNext = true
			found = true
			continue
		}
		kept = append(kept, l)
	}
	if !found {
		return false, rcFile, nil
	}

	if err := os.WriteFile(rcFile, []byte(strings.Join(kept, "\n")), 0o644); err != nil {
		return false, rcFile, err
	}
	return true, rcFile, nil
}
