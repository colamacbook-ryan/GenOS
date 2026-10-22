// Lệnh genh — trình cài đặt/vận hành một-lệnh của Gen-Harness, theo
// docs/handoff/05-installer.md.
//
// `genh install` chạy thật cả 8 bước cài đặt (Kiểm tra máy … Hoàn tất — xem
// internal/install.Registry), hiển thị qua TUI khi có TTY hoặc chế độ dòng
// khi không có (CI, pipe), và kết thúc bằng màn "Hoàn tất" thật (URL/mã
// thiết lập/trạng thái mở trình duyệt lấy từ Bước 4 và Bước 8). Các lệnh vận
// hành (status/open/logs/update/backup/restore/doctor/reset-setup/reset-password/trust-ca/stop/
// start/uninstall — xem internal/ops) cũng đã triển khai thật ở phiên này.
package main

import (
	"bufio"
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/autoupdate"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/config"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/install"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/machine"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/ops"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/secretgen"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/selfupdate"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/tui"
)

// selfupdateOwner/selfupdateRepo là repo GitHub genh tự hỏi bản mới nhất —
// cùng REPO dùng bởi install.sh/install.ps1 (docs/handoff/05-installer.md).
const (
	selfupdateOwner = "Genesis-ryan-84-0567536339"
	selfupdateRepo  = "Gen-Harness"
)

// Biến gói CHỈ để test (sản xuất giữ nguyên giá trị mặc định): tiêm bước tự cập nhật
// binary, tiêm cách chạy tiến trình con sau khi tự thay binary, giả lỗi xoá yêu cầu.
var (
	// selfUpdateRun là selfupdate.Run; selfUpdateTweak (nil ở sản xuất) chỉnh Options
	// trước khi chạy (trỏ APIBase/DownloadBase vào httptest, ExecutablePath vào tệp tạm…).
	selfUpdateRun   = selfupdate.Run
	selfUpdateTweak func(*selfupdate.Options)
	// reExecChild chạy tiến trình con (genh bản mới) và chờ nó xong.
	reExecChild = func(ctx context.Context, c *exec.Cmd) error { return runChildForwardingSignal(ctx, c) }
	// consumeRequest là hostlink.ConsumeRequest (test giả lỗi xoá GH-E94C).
	consumeRequest = hostlink.ConsumeRequest
	// autostartDepsFn dựng ops.AutostartDeps khi ghi run/autostart-status.json
	// (test tiêm Runner giả để không hỏi docker/systemctl thật).
	autostartDepsFn = func() ops.AutostartDeps { return ops.AutostartDeps{} }
	// hostInfoEnvFn dựng phần phụ thuộc của publishHostInfo (Runner/HomeDir/… cho
	// systemctl, crontab, loginctl) — rỗng = thật; test tiêm Runner giả + HomeDir tạm.
	hostInfoEnvFn = func() hostInfoEnv { return hostInfoEnv{} }
)

// version được ghi đè lúc build phát hành thật qua:
//
//	go build -ldflags "-X main.version=v2.2.0"
//
// Chưa có pipeline phát hành trong phần A nên giữ "dev".
var version = "dev"

func main() {
	os.Exit(run(os.Args[1:]))
}

func run(args []string) int {
	if len(args) == 0 {
		printUsage(os.Stdout)
		return 2
	}

	switch args[0] {
	case "install":
		return runInstall(args[1:])
	case "status":
		return runStatus(args[1:])
	case "open":
		return runOpen(args[1:])
	case "logs":
		return runLogs(args[1:])
	case "update":
		return runUpdate(args[1:])
	case "auto-update":
		return runAutoUpdate(args[1:])
	case "backup":
		return runBackup(args[1:])
	case "restore":
		return runRestore(args[1:])
	case "handle-requests":
		return runHandleRequests(args[1:])
	case "doctor":
		return runDoctor(args[1:])
	case "reset-setup":
		return runResetSetup(args[1:])
	case "reset-password":
		return runResetPassword(args[1:])
	case "trust-ca":
		return runTrustCA(args[1:])
	case "remote", "set-address":
		return runRemote(args[1:])
	case "stop":
		return runStop(args[1:])
	case "start":
		return runStart(args[1:])
	case "uninstall":
		return runUninstall(args[1:])
	case "export":
		return runExport(args[1:])
	case "offsite":
		return runOffsite(args[1:])
	case "watchdog":
		return runWatchdogCmd(args[1:])
	case "import":
		return runImport(args[1:])
	case "version", "-v", "--version":
		fmt.Println("genh " + version)
		return 0
	case "help", "-h", "--help":
		printUsage(os.Stdout)
		return 0
	default:
		_, _ = fmt.Fprintf(os.Stderr, "genh: lệnh không rõ %q\n\n", args[0])
		printUsage(os.Stderr)
		return 2
	}
}

func printUsage(w *os.File) {
	_, _ = fmt.Fprint(w, `genh — trình cài đặt/vận hành một lệnh của Gen-Harness

Cách dùng:
  genh install [--port N] [--install-dir DIR] [--yes] [--force]   cài đặt, hoặc tiếp tục bản dở
                                          (máy đã cài xong -> dừng, gợi ý genh update; --force để cố tình cài lại)

Lệnh vận hành (cờ chung mọi lệnh dưới đây: --port N, --install-dir DIR):
  genh status                            bảng dịch vụ + healthy + phiên bản + dung lượng
  genh open                              mở Console trong trình duyệt
  genh logs [dịch vụ...] [-f]            log gọn (tail 200), -f để theo dõi liên tục
  genh update [--channel stable|beta] [--yes] [--quiet] [--no-self-update]
                                          tự tải genh mới nhất (kiểm checksum, re-exec bằng
                                          code mới) rồi mới backup tự động, migrate, khởi động
                                          lại theo thứ tự; lỗi ở bất kỳ bước nào → tự rollback.
                                          --yes (lịch đêm dùng cờ này): chỉ nhận bản genh đã là
                                          bản chính thức ≥ 24 giờ (thời gian chín; gõ tay không
                                          --yes hoặc "Cập nhật ngay" trong Console thì cài
                                          luôn) · --quiet: chỉ in dòng quan trọng ·
                                          --no-self-update: chỉ nâng cấp dịch vụ, không đụng
                                          binary genh. Mọi cách chạy: dịch vụ đã khớp bản genh
                                          này thì bỏ qua (không sao lưu, không tải) — dịch vụ
                                          đang dừng/lỗi thì dùng genh start
  genh auto-update enable|disable|status tự chạy "genh update --yes --quiet" mỗi đêm ~03:00
                                          (systemd timer/crontab, LaunchAgent, hoặc Task
                                          Scheduler tuỳ hệ điều hành) — mặc định đã BẬT sau
                                          "genh install" (tắt bằng --no-auto-update lúc cài,
                                          hoặc "genh auto-update disable" sau đó); vì có --yes,
                                          lịch đêm chỉ nhận bản đã là bản chính thức ≥ 24 giờ.
                                          Lịch đêm bị tắt/mất ngoài ý muốn thì mỗi lần genh chạy
                                          (update, lịch đêm) TỰ BẬT LẠI — trừ khi Sếp đã chủ động
                                          tắt ("genh auto-update disable" / --no-auto-update).
                                          status: bật/tắt thật, cơ chế, lần chạy gần nhất/kế tiếp,
                                          linger, cảnh báo khi log không có dòng mới > 36 giờ
  genh backup [--to path]                sao lưu vào ObjectStore nội bộ (--to: copy thêm ra host)
  genh restore <khoá>                    khôi phục một bản backup theo khoá: tự sao lưu an toàn,
                                          dừng api/worker, khôi phục, migrate, khởi động lại
                                          (lỗi → quay về bản an toàn; chưa nhận file host tuỳ ý)
  genh handle-requests                   (watcher gọi) làm yêu cầu Console để lại trong hộp thư
                                          run/request: update.json → cập nhật, restore.json → khôi phục,
                                          offsite.json → bản sao ngoài máy, doctor.json → gói chẩn đoán,
                                          watchdog.json → gửi thử báo động
                                          (thứ tự update > restore > offsite > doctor > watchdog)
  genh doctor [--out report.zip]         chẩn đoán runtime/cổng/chứng chỉ/dung lượng/đồng hồ/
                                          kết nối kênh, xuất báo cáo zip (đã lọc bí mật)
  genh doctor --notify [--quiet] [--test]
                                          trực canh máy chủ MỘT lượt (lịch 12 phút gọi): đo dịch vụ,
                                          tự khởi động lại dịch vụ chết (≤ 1 lần/giờ), báo Telegram
                                          ("Báo động & bản tin" trong Console) — chỉ báo sự cố MỚI và
                                          sự cố ĐÃ ỔN; --test gửi thêm một tin thử; không tạo zip
  genh doctor --if-requested             (watcher gọi) làm gói chẩn đoán Console yêu cầu
                                          (run/request/doctor.json) vào run/diagnostics/
  genh watchdog enable|disable|status    bật/tắt/xem lịch trực canh máy chủ mỗi 12 phút (mặc định
                                          BẬT sau install/update, kể cả khi --no-auto-update);
                                          status in cơ chế, lần chạy gần nhất, sự cố đang mở
  genh reset-setup [--yes]               sinh mã thiết lập mới (hỏi xác nhận trừ khi --yes)
  genh reset-password                    quên mật khẩu Owner: in email + mật khẩu tạm mới
                                          (giữ nguyên dữ liệu, đăng xuất các phiên cũ)
  genh remote [status]                   xem cách truy cập từ xa hiện tại (và 4 cách đổi); set-address = bí danh
  genh remote tailscale [--yes]          mở qua Tailscale (khuyên dùng — không mở cổng ra mạng)
  genh remote cloudflare --hostname <tên> [--yes]   mở qua Cloudflare Tunnel
  genh remote lan [--name <tên|IP>] [--yes]         mở cho mạng nội bộ (LAN) — cảnh báo, cần cài CA (= --lan)
  genh remote local [--yes]              chỉ máy này (= --local)
  genh trust-ca                          tin cậy lại CA nội bộ cho trình duyệt/hệ điều hành
                                          (hết cảnh báo "Not secure"; genh update tự làm)
  genh stop                              dừng toàn bộ dịch vụ (giữ dữ liệu) — trực canh tạm nghỉ
                                          (không tự khởi động lại, không báo động) tới khi genh start
  genh start                             khởi động lại toàn bộ dịch vụ
  genh uninstall [--delete-data] [--yes] gỡ container/lối tắt/PATH/lịch — mặc định GIỮ dữ liệu
                                          (volume Docker); --delete-data mới xoá dữ liệu (gõ
                                          "XOÁ DỮ LIỆU" hoặc kèm --yes); --keep-data vẫn nhận
  genh offsite set [--allow-same-disk] [--no-run] <thư mục>
                                          chọn nơi lưu bản sao ngoài máy (ổ USB/NAS đã mount),
                                          bật lịch mỗi Chủ nhật ~05:30 và xuất bản đầu tiên ngay
  genh offsite run [--quiet]             xuất + kiểm đọc lại một bản sao ngoài máy (giữ 4 bản)
  genh offsite status|disable            xem tình trạng / tắt lịch bản sao ngoài máy
  genh export --to <file>                xuất gói hồ sơ .ghbundle (hỏi mật khẩu ẩn 2 lần,
                                          hoặc biến GH_BUNDLE_PASSWORD cho script/test)
  genh import <file> [--yes]             nhập gói .ghbundle — GHI ĐÈ dữ liệu hiện tại (hỏi
                                          xác nhận trừ --yes), tự backup an toàn trước

  genh version                                   in phiên bản
  genh help                                      in hướng dẫn này

Trạng thái bản này: cả 8 bước cài đặt (kiểm tra máy, container runtime,
tải image, sinh bí mật, khởi động dữ liệu, migration, khởi động dịch vụ,
hoàn tất) VÀ toàn bộ lệnh vận hành ở trên đã triển khai thật — một số lệnh
có giới hạn đã biết, ghi rõ trong thông báo của chính lệnh đó (ví dụ genh
restore chưa nhận file host tuỳ ý, genh update chưa có pipeline phát hành
theo kênh — xem docs/handoff/05-installer.md).
`)
}

// signalContext là ngữ cảnh bị huỷ khi genh nhận tín hiệu dừng: Ctrl-C
// (os.Interrupt) HOẶC SIGTERM — systemd gửi SIGTERM khi máy tắt/khởi động lại
// hoặc `systemctl stop` (v0.1.37, F-34: trước đây chỉ bắt Ctrl-C nên SIGTERM
// giết genh ngay giữa lúc migrate, không kịp quay về bản cũ). syscall.SIGTERM có
// trên mọi hệ điều hành Go hỗ trợ (Windows: chỉ là hằng, không bao giờ tới).
//
// Nguyên nhân huỷ (context.Cause) nói tín hiệu nào: SIGTERM → ops.ErrShutdownSignal
// (máy tắt — rollback không bắt đầu khôi phục CSDL trong khung ~2 phút trước
// SIGKILL), Ctrl-C → ops.ErrInterruptSignal. Như signal.NotifyContext, sau tín
// hiệu đầu genh VẪN bắt tín hiệu (không chết vì tín hiệu thứ hai) cho tới stop.
func signalContext() (context.Context, context.CancelFunc) {
	ctx, cancel := context.WithCancelCause(context.Background())
	ch := make(chan os.Signal, 1)
	signal.Notify(ch, os.Interrupt, syscall.SIGTERM)
	done := make(chan struct{})
	go func() {
		select {
		case sig := <-ch:
			cancel(signalCause(sig))
		case <-done:
		}
	}()
	var once sync.Once
	return ctx, func() {
		once.Do(func() {
			signal.Stop(ch)
			close(done)
			cancel(context.Canceled)
		})
	}
}

// signalCause: nguyên nhân huỷ ngữ cảnh ứng với tín hiệu nhận được.
func signalCause(sig os.Signal) error {
	if sig == syscall.SIGTERM {
		return ops.ErrShutdownSignal
	}
	return ops.ErrInterruptSignal
}

// forwardSignal: tín hiệu chuyển cho tiến trình con theo nguyên nhân huỷ ctx —
// giữ đúng loại (Ctrl-C vẫn là SIGINT để con khôi phục như thường, SIGTERM vẫn
// là SIGTERM để con không bắt đầu khôi phục lúc máy đang tắt).
func forwardSignal(ctx context.Context) os.Signal {
	if errors.Is(context.Cause(ctx), ops.ErrInterruptSignal) {
		return os.Interrupt
	}
	return syscall.SIGTERM
}

// lockMode: cách lấy khoá loại trừ (<gốc cài đặt>/genh.lock) theo cách genh
// được gọi.
type lockMode int

const (
	// lockManual: Owner gõ tay — bận ⇒ in lý do ra stderr, thoát 1 (GH-E94A).
	lockManual lockMode = iota
	// lockScheduled: lịch đêm — bận ⇒ in một dòng ra stdout (vào log), thoát 0,
	// KHÔNG đụng hộp thư Console.
	lockScheduled
	// lockRequested: nút trong Console (watcher, --if-requested) — CHỜ tối đa
	// requestLockWait: thoát ngay khi tệp yêu cầu còn đó thì path unit systemd
	// sẽ kích lặp liên tục.
	lockRequested
)

// requestLockWait: thời gian tối đa --if-requested chờ lần khác nhả khoá (biến
// gói để test đặt ngắn).
var requestLockWait = 30 * time.Minute

// lockBusyLine: dòng log khi khoá bận (E2E grep "đang có một lần cập
// nhật/khôi phục khác chạy").
func lockBusyLine(err error) string {
	if pid := hostlink.BusyPID(err); pid > 0 {
		return fmt.Sprintf("genh: đang có một lần cập nhật/khôi phục khác chạy (PID %d) — lần này bỏ qua.", pid)
	}
	return "genh: đang có một lần cập nhật/khôi phục khác chạy — lần này bỏ qua."
}

// acquireOpLock lấy khoá loại trừ cho update/restore/import ở tiến trình NGOÀI
// CÙNG, TRƯỚC khi đụng hộp thư Console. ok=false ⇒ caller trả ngay exitCode.
// stillWanted (lockRequested): kiểm lại yêu cầu sau khi chờ được khoá — lần
// chạy trước (vd lịch đêm) có thể đã nuốt nó. Lỗi khác "bận" (không mở được
// genh.lock…) chỉ cảnh báo rồi chạy tiếp không khoá — không chặn bản vá vì một
// tệp khoá hỏng.
func acquireOpLock(ctx context.Context, installDir string, mode lockMode, stillWanted func() bool, stdout, stderr io.Writer) (lock *hostlink.Lock, exitCode int, ok bool) {
	return acquireOpLockEx(ctx, installDir, mode, stillWanted, nil, stdout, stderr)
}

// acquireOpLockEx như acquireOpLock; onExpired (chỉ lockRequested) chạy khi đã CHỜ đủ
// requestLockWait mà khoá vẫn bận (v0.1.53, F-97): yêu cầu từ Console không được để
// nằm lại hộp thư (path unit sẽ kích lặp) — caller xoá tệp yêu cầu và ghi failed
// GH-E94A. KHÔNG chạy khi "đã có tiến trình khác đang chờ" (người chờ kia làm).
func acquireOpLockEx(ctx context.Context, installDir string, mode lockMode, stillWanted func() bool, onExpired func(), stdout, stderr io.Writer) (lock *hostlink.Lock, exitCode int, ok bool) {
	lock, err := hostlink.AcquireLock(installDir)
	if mode == lockRequested && errors.Is(err, hostlink.ErrLockBusy) {
		// Chỉ MỘT người chờ cùng lúc (watcher crontab kích mỗi phút — xem
		// hostlink.WaitLockFile); người chờ đó sẽ làm yêu cầu, lần này thoát.
		waiter, werr := hostlink.AcquireWaitLock(installDir)
		if errors.Is(werr, hostlink.ErrLockBusy) {
			_, _ = fmt.Fprintln(stdout, "genh: đã có một tiến trình khác đang chờ để làm yêu cầu từ Console — lần này bỏ qua.")
			return nil, 0, false
		}
		_, _ = fmt.Fprintf(stdout, "genh: chờ lần cập nhật/khôi phục đang chạy xong (tối đa %s) rồi làm tiếp yêu cầu từ Console…\n", requestLockWait)
		lock, err = hostlink.AcquireLockWait(ctx, installDir, requestLockWait)
		waiter.Release()
	}
	switch {
	case err == nil:
	case errors.Is(err, hostlink.ErrLockBusy):
		if mode == lockManual {
			_, _ = fmt.Fprintln(stderr, lockBusyLine(err))
			_, _ = fmt.Fprint(stderr, (&ops.OpError{
				Code: ops.ErrCodeUpdateLocked,
				What: "Chưa chạy lệnh này — đang có một lần cập nhật/khôi phục khác",
				Why:  err.Error(),
				Next: "Đợi lần đó chạy xong (xem `genh status` hoặc logs/auto-update.log) rồi chạy lại lệnh này.",
			}).Report())
			return nil, 1, false
		}
		_, _ = fmt.Fprintln(stdout, lockBusyLine(err))
		if mode == lockRequested && onExpired != nil {
			onExpired()
		}
		return nil, 0, false
	case ctx.Err() != nil:
		_, _ = fmt.Fprintln(stderr, "genh: nhận tín hiệu dừng khi đang chờ lần cập nhật/khôi phục khác chạy xong — không làm gì.")
		return nil, 1, false
	default:
		_, _ = fmt.Fprintf(stderr, "genh: cảnh báo — không lấy được khoá loại trừ (%v); vẫn chạy tiếp.\n", err)
	}
	if mode == lockRequested && stillWanted != nil && !stillWanted() {
		lock.Release()
		return nil, 0, false
	}
	return lock, 0, true
}

// opsFlagSet dựng flag.FlagSet chung cho mọi lệnh vận hành (--port/
// --install-dir, cùng mặc định với runInstall) — trả về FlagSet CHƯA Parse
// (caller tự thêm cờ riêng của lệnh trước khi gọi fs.Parse) cùng 2 con trỏ
// để đọc lại sau Parse.
func opsFlagSet(name string) (fs *flag.FlagSet, port *int, installDir *string) {
	fs = flag.NewFlagSet(name, flag.ContinueOnError)
	port = fs.Int("port", machine.DefaultPort, "cổng HTTPS của proxy")
	installDir = fs.String("install-dir", "", "thư mục cài đặt (mặc định ~/.gen-harness)")
	return fs, port, installDir
}

// resolveOpsEnv dựng *ops.Env từ port/installDir đã Parse — in lỗi ra stderr
// và trả ok=false nếu không xác định được thư mục cài đặt mặc định.
func resolveOpsEnv(port int, installDir string) (*ops.Env, bool) {
	dir, err := ops.ResolveInstallDir(installDir)
	if err != nil {
		_, _ = fmt.Fprintf(os.Stderr, "genh: không xác định được thư mục cài đặt: %v\n", err)
		return nil, false
	}
	return &ops.Env{InstallDir: dir, Port: ops.ResolvePort(port)}, true
}

// reportOpErr in một lỗi vận hành theo đúng định dạng "chuyện gì · vì sao ·
// làm gì tiếp" (xem internal/ops.OpError) hoặc lỗi thường nếu không phải
// *ops.OpError (không nên xảy ra với các hàm ops.Run*, nhưng phòng hờ).
func reportOpErr(err error) {
	if opErr, ok := err.(*ops.OpError); ok {
		_, _ = fmt.Fprint(os.Stderr, opErr.Report())
		return
	}
	_, _ = fmt.Fprintf(os.Stderr, "genh: %v\n", err)
}

func runStatus(args []string) int {
	fs, port, installDir := opsFlagSet("status")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	env, ok := resolveOpsEnv(*port, *installDir)
	if !ok {
		return 1
	}
	if err := ops.RunStatus(context.Background(), env, version, ops.StatusDeps{}, os.Stdout); err != nil {
		reportOpErr(err)
		return 1
	}
	return 0
}

func runOpen(args []string) int {
	fs, port, installDir := opsFlagSet("open")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	env, ok := resolveOpsEnv(*port, *installDir)
	if !ok {
		return 1
	}
	if err := ops.RunOpen(env, ops.OpenDeps{}, os.Stdout); err != nil {
		reportOpErr(err)
		return 1
	}
	return 0
}

func runLogs(args []string) int {
	fs, port, installDir := opsFlagSet("logs")
	follow := fs.Bool("f", false, "theo dõi log liên tục (như tail -f)")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	env, ok := resolveOpsEnv(*port, *installDir)
	if !ok {
		return 1
	}
	ctx, stop := signalContext()
	defer stop()
	opts := ops.LogsOptions{Services: fs.Args(), Follow: *follow}
	if err := ops.RunLogs(ctx, env, opts, nil, os.Stdout, os.Stderr, os.Stdin); err != nil {
		reportOpErr(err)
		return 1
	}
	return 0
}

// stdoutIsTerminal: stdout là terminal ⇒ người gõ tay. Mọi lịch đêm (systemd
// StandardOutput=append:, crontab `>>`, LaunchAgent StandardOutPath, schtasks `>>`)
// đều ghi stdout vào logs/auto-update.log — nên đây là dấu phân biệt lần chạy của lịch
// với `genh update --yes` gõ tay, đúng cả với unit/dòng lịch do genh cũ ghi (không cần
// thêm cờ/biến vào lịch: genh cũ sau quay về bản cũ sẽ không hiểu cờ mới). Biến để test tiêm.
var stdoutIsTerminal = func() bool { return tui.IsTerminal(os.Stdout) }

// updateFlags là các cờ của `genh update` sau khi Parse.
type updateFlags struct {
	port         int
	installDir   string
	channel      string
	yes          bool
	quiet        bool
	noSelfUpdate bool
	selfUpdated  bool
	ifRequested  bool
}

// parseUpdateFlags parse cờ của `genh update` — tách khỏi runUpdate để test
// (cmd/genh/main_test.go) parse đúng bộ cờ thật, ví dụ cờ handle-requests
// chuyển sang (handleRequestUpdateArgs).
func parseUpdateFlags(args []string) (updateFlags, error) {
	fs, port, installDir := opsFlagSet("update")
	channel := fs.String("channel", "stable", "kênh cập nhật: stable hoặc beta")
	yes := fs.Bool("yes", false, "chạy không tương tác — dùng cho lịch tự động (genh auto-update); KHÔNG hỏi gì kể cả khi có TTY, và (không kèm --if-requested) chỉ tự cài bản genh đã là bản chính thức ≥ 24 giờ (thời gian chín) — muốn cài ngay thì bỏ --yes")
	quiet := fs.Bool("quiet", false, "chỉ in các dòng quan trọng (có bản mới/lỗi/xong) — bỏ log tiến độ từng bước")
	noSelfUpdate := fs.Bool("no-self-update", false, "bỏ qua tự cập nhật BINARY genh — chỉ chạy phần nâng cấp dịch vụ (backup/pull/migrate/restart) bằng bản genh hiện tại (như mọi cách chạy genh update: dịch vụ đã khớp bản genh này thì bỏ qua — dịch vụ dừng/lỗi thì dùng genh start)")
	selfUpdated := fs.Bool("self-updated", false, "cờ NỘI BỘ: tiến trình này vừa được re-exec ngay sau khi tự thay binary — KHÔNG dùng tay, chỉ genh tự đặt cho chính nó")
	ifRequested := fs.Bool("if-requested", false, "chỉ cập nhật nếu Owner vừa bấm \"Cập nhật ngay\" trong Console (watcher trên máy chủ gọi) — không có yêu cầu thì thoát ngay")
	if err := fs.Parse(args); err != nil {
		return updateFlags{}, err
	}
	return updateFlags{
		port: *port, installDir: *installDir, channel: *channel,
		yes: *yes, quiet: *quiet, noSelfUpdate: *noSelfUpdate,
		selfUpdated: *selfUpdated, ifRequested: *ifRequested,
	}, nil
}

func runUpdate(args []string) int {
	f, err := parseUpdateFlags(args)
	if err != nil {
		return 2
	}
	// --yes không đổi phần nâng cấp dịch vụ (RunUpdate không hỏi gì, kể cả có
	// TTY — đã rà soát internal/ops/update.go: không có prompt nào), nhưng
	// --yes KHÔNG kèm --if-requested ⇒ bước tự cập nhật binary áp thời gian
	// chín 24 giờ (selfUpdateMinAge) — xem lý do giữ --yes làm cờ kích hoạt ở
	// đó. Bản chưa đủ chín: không thay binary, phần dịch vụ vẫn chạy như khi
	// đã mới nhất, dòng kết nói rõ bản mới đang đợi (updateDoneLine).

	env, ok := resolveOpsEnv(f.port, f.installDir)
	if !ok {
		return 1
	}

	// Tín hiệu dừng (Ctrl-C/SIGTERM) bắt từ ĐẦU — kể cả lúc tự cập nhật binary
	// và lúc chờ tiến trình con (trySelfUpdateAndReExec chuyển tiếp cho con).
	ctx, stop := signalContext()
	defer stop()

	// Lịch đêm (--yes không --if-requested, tiến trình ngoài cùng, stdout KHÔNG phải
	// terminal): ghi run/nightly-status.json lúc BẮT ĐẦU (last_run_at) và lúc KẾT THÚC
	// (last_result) — v0.1.53, F-99. Mặc định "failed" cho mọi lối thoát bất ngờ; mỗi
	// nhánh đặt kết quả đúng của nó. Sếp gõ tay `genh update --yes` trong terminal không
	// phải lần chạy của lịch: tính vào thì timer hỏng vẫn hiện "Bình thường" thêm 36 giờ.
	nightly := f.yes && !f.ifRequested && !f.selfUpdated && !stdoutIsTerminal()
	nightlyResult := hostlink.NightlyResultFailed

	// Khoá loại trừ (v0.1.37, F-35): tiến trình NGOÀI CÙNG lấy khoá TRƯỚC khi đụng
	// hộp thư Console — hai genh update/restore chạy chồng nhau (lịch đêm + nút
	// Console + gõ tay) sẽ sao lưu/migrate/khôi phục giẫm lên nhau. Tiến trình
	// con --self-updated BỎ QUA (cha đang giữ khoá; fd khoá không kế thừa).
	if !f.selfUpdated {
		if f.ifRequested && !hostlink.HasRequest(env.InstallDir) {
			return 0
		}
		// Yêu cầu không xoá được và đã báo lỗi GH-E94C cho đúng yêu cầu này: không làm
		// lại, không chờ khoá (path unit có thể kích lặp tới giới hạn rồi dừng).
		if f.ifRequested && hostlink.UpdateUndeletableReported(env.InstallDir, ops.ErrCodeRequestUndeletable) {
			return 0
		}
		mode := lockManual
		switch {
		case f.ifRequested:
			mode = lockRequested
		case f.yes:
			mode = lockScheduled
		}
		onExpired := func() {
			// Chờ khoá đủ 30 phút mà máy chủ vẫn bận: xoá yêu cầu (path unit không kích
			// lặp mãi) và báo Console GH-E94A — Sếp bấm Thử lại sau.
			if f.ifRequested && hostlink.HasRequest(env.InstallDir) {
				if _, cerr := consumeRequest(env.InstallDir); cerr != nil {
					reportUndeletableUpdate(env.InstallDir, cerr)
					return
				}
				_ = hostlink.Start(env.InstallDir, version)
				_ = hostlink.Finish(env.InstallDir, "failed", "", ops.RequestBusyMessage)
			}
		}
		lock, code, ok := acquireOpLockEx(ctx, env.InstallDir, mode, func() bool { return hostlink.HasRequest(env.InstallDir) }, onExpired, os.Stdout, os.Stderr)
		if !ok {
			return code
		}
		defer lock.Release()
		stopBeat := hostlink.StartHeartbeat(env.InstallDir, "update")
		defer stopBeat()
		// v0.1.53 (F-95): MỌI lối thoát của tiến trình ngoài cùng làm mới
		// run/autostart-status.json (trước đây chỉ nhánh "xong"), để Console nhắc
		// linger/Docker kể cả khi cập nhật lỗi hoặc bỏ qua.
		defer refreshAutostartStatus(env.InstallDir)
		if nightly {
			// Chưa có nightly-status.json (lần đầu sau khi nâng cấp): ghi ảnh chụp trạng thái lịch
			// TRƯỚC, để lần chạy lỗi sớm không để lại bản ghi "lịch tắt" sai.
			if _, err := hostlink.ReadNightlyStatus(env.InstallDir); err != nil {
				bctx, bcancel := context.WithTimeout(context.Background(), 15*time.Second)
				base := hostInfoEnvFn().Base
				base.LogFile = filepath.Join(config.New(env.InstallDir).LogsDir(), "auto-update.log")
				_ = ops.RecordNightlyStatus(bctx, env.InstallDir, base)
				bcancel()
			}
			_ = hostlink.RecordNightlyRun(env.InstallDir, time.Now(), "")
			defer func() { _ = hostlink.RecordNightlyRun(env.InstallDir, time.Time{}, nightlyResult) }()
		}
	}

	// Hộp thư Console (internal/hostlink): tiến trình NGOÀI CÙNG (không phải bản
	// re-exec sau tự cập nhật) xoá yêu cầu "Cập nhật ngay" TRƯỚC khi chạy — để
	// watcher không kích lặp — rồi báo "running" cho Console hiện tiến trình.
	// Chụp nguyên hộp thư TRƯỚC khi báo "running": lịch đêm gặp bản bị chặn thì
	// trả về đúng như cũ (skipBlockedUpdate).
	//
	// Lịch đêm chạy ĐÚNG lúc Owner vừa bấm "Cập nhật ngay"/"Thử lại" (watcher
	// chưa kịp gọi): lịch đêm đã nuốt yêu cầu thì phải làm như --if-requested
	// (không bị chặn, không đợi chín) — nếu không yêu cầu mất không dấu vết.
	//
	// Không xoá được yêu cầu (quyền…): --if-requested KHÔNG làm yêu cầu (xoá-trước-khi-làm
	// là thứ chặn watcher kích lặp) — ghi failed GH-E94C, thoát 0 (v0.1.53, F-97). Lịch
	// đêm/gõ tay thì cảnh báo rồi cập nhật như thường (không để một tệp kẹt chặn cập nhật).
	var statusSnap []byte
	hadStatus, hadRequest := false, false
	if !f.selfUpdated {
		statusSnap, hadStatus = hostlink.SnapshotStatus(env.InstallDir)
		consumed, cerr := consumeRequest(env.InstallDir)
		if cerr != nil {
			reportUndeletableUpdate(env.InstallDir, cerr)
			if f.ifRequested {
				nightlyResult = hostlink.NightlyResultFailed
				return 0
			}
		}
		hadRequest = consumed
		_ = hostlink.Start(env.InstallDir, version)
	}
	requested := f.ifRequested || hadRequest
	minAge := selfUpdateMinAge(f.yes, requested)

	// Tự cập nhật BINARY genh TRƯỚC KHI đụng gì tới dịch vụ — xem
	// internal/selfupdate. Bỏ qua nếu: --no-self-update, HOẶC tiến trình
	// này đã là kết quả của một lần tự cập nhật (--self-updated, tránh lặp
	// vô hạn tự-tải-tự-re-exec nếu có gì đó luôn báo "mới hơn" sai).
	deferred := false
	if !f.noSelfUpdate && !f.selfUpdated {
		code, ok, d := trySelfUpdateAndReExec(ctx, childUpdateArgs(args, f.ifRequested, requested), f.quiet, minAge)
		deferred = d
		if ok {
			// Bản mới (tiến trình con) tự ghi kết quả; con chết giữa chừng thì
			// trạng thái vẫn "running" — báo lỗi thay nó để Console không chờ mãi.
			// Không chạy được con vì tín hiệu dừng tới ngay sau khi tải genh mới
			// (code == exitStoppedBeforeReExec): chưa đụng gì — GH-E94B.
			if st, err := hostlink.ReadStatus(env.InstallDir); code != 0 && err == nil && st.State == "running" {
				_ = hostlink.Finish(env.InstallDir, "failed", "", childFailedMessage(code == exitStoppedBeforeReExec, context.Cause(ctx)))
			}
			if code == exitStoppedBeforeReExec {
				return 1
			}
			if code == 0 {
				nightlyResult = hostlink.NightlyResultDone
			}
			return code
		}
	}

	// Có cần chạy phần nâng cấp dịch vụ không (F-10 bước 3, F-33 bước 5): lịch
	// đêm không thử lại bản đã rollback; dịch vụ đã khớp bản genh này thì không
	// sao lưu/tải ảnh vô ích mỗi đêm.
	blocked, _, _ := hostlink.ReadUpdateBlocked(env.InstallDir)
	inSync, _ := ops.UpdateNeeded(env) // lỗi → false: để RunUpdate tự báo lỗi đúng khuôn
	switch skip, kind := decideServiceUpdate(serviceUpdateInput{
		Scheduled:      f.yes && !requested,
		SelfUpdated:    f.selfUpdated,
		Version:        version,
		InSync:         inSync,
		BlockedVersion: blocked.Version,
	}); {
	case skip && kind == "blocked":
		// In ra stdout CẢ khi --quiet để vào logs/auto-update.log.
		skipBlockedUpdate(os.Stdout, env.InstallDir, version, blocked, statusSnap, hadStatus, f.selfUpdated)
		nightlyResult = hostlink.NightlyResultBlocked
		return 0
	case skip && kind == "up-to-date":
		fmt.Println(upToDateLine(version))
		_ = hostlink.Finish(env.InstallDir, "done", version, "")
		publishHostInfo(env.InstallDir, env.Port)
		if deferred {
			fmt.Println(updateDoneLine(true))
			nightlyResult = hostlink.NightlyResultDeferred
		} else {
			nightlyResult = hostlink.NightlyResultUpToDate
		}
		return 0
	}

	out := io.Writer(os.Stdout)
	if f.quiet {
		out = io.Discard
	}
	opts := ops.UpdateOptions{Channel: f.channel, Version: version}
	if err := ops.RunUpdate(ctx, env, opts, ops.UpdateDeps{}, out); err != nil {
		reportOpErr(err)
		_ = hostlink.Finish(env.InstallDir, "failed", version, consoleUpdateMessage(err))
		return 1
	}
	_ = hostlink.Finish(env.InstallDir, "done", version, "")
	// Cập nhật xong = dịch vụ đã khởi động lại ⇒ bỏ đánh dấu "Owner chủ động dừng".
	_ = ops.ClearOwnerPause(env.InstallDir)
	publishHostInfo(env.InstallDir, env.Port)
	nightlyResult = hostlink.NightlyResultDone
	if deferred {
		fmt.Println(deferredAfterRunLine)
	} else if f.quiet {
		fmt.Println(updateDoneLine(false))
	}
	return 0
}

// reportUndeletableUpdate xử lý yêu cầu "Cập nhật ngay" KHÔNG xoá được (GH-E94C):
// lỗi gốc in stderr/log, update-status ghi failed một lần cho mỗi yêu cầu (idempotent
// theo requested_at). Thông điệp Console không chứa đường dẫn hay lỗi gốc.
func reportUndeletableUpdate(installDir string, cause error) {
	_, _ = fmt.Fprintf(os.Stderr, "genh: không xoá được yêu cầu cập nhật trong run/request (%v) — không làm yêu cầu này để tránh chạy lặp (%s).\n", cause, ops.ErrCodeRequestUndeletable)
	_, _ = hostlink.FailUndeletableUpdate(installDir, version, ops.ErrCodeRequestUndeletable, ops.RequestUndeletableMessage("yêu cầu cập nhật"))
}

// refreshAutostartStatus làm mới run/autostart-status.json (Docker/linger có tự chạy
// lại khi bật máy không). Lỗi bỏ qua.
func refreshAutostartStatus(installDir string) {
	actx, acancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer acancel()
	_ = hostlink.WriteAutostartStatus(installDir, ops.CheckAutostart(actx, autostartDepsFn()))
}

// childUpdateArgs: args cho tiến trình re-exec sau khi tự thay binary. Tiến
// trình ngoài đã nuốt yêu cầu Console (lịch đêm trùng lúc Owner bấm) thì con
// không còn thấy yêu cầu — thêm --if-requested để con cũng không bị chặn.
func childUpdateArgs(args []string, ifRequested, requested bool) []string {
	if requested && !ifRequested {
		return append(append([]string{}, args...), "--if-requested")
	}
	return args
}

// serviceUpdateInput là dữ kiện để quyết định có chạy ops.RunUpdate không.
type serviceUpdateInput struct {
	Scheduled      bool   // lịch đêm: --yes không kèm --if-requested
	SelfUpdated    bool   // tiến trình vừa re-exec sau khi tự thay binary
	Version        string // main.version
	InSync         bool   // compose.yaml + Caddyfile đã khớp bản nhúng (ops.UpdateNeeded)
	BlockedVersion string // version trong run/update-blocked.json ("" nếu không có)
}

// decideServiceUpdate (hàm thuần) quyết định bỏ qua phần nâng cấp dịch vụ:
//  1. lịch đêm + bản đang chạy đúng là bản đã bị chặn (so khớp CHÍNH XÁC) →
//     "blocked" — "Cập nhật ngay" và gõ tay luôn được chạy;
//  2. không vừa tự cập nhật + dịch vụ đã khớp bản genh này → "up-to-date";
//  3. còn lại chạy RunUpdate (skip=false, kind="").
func decideServiceUpdate(in serviceUpdateInput) (skip bool, kind string) {
	if in.Scheduled && in.BlockedVersion != "" && in.BlockedVersion == in.Version {
		return true, "blocked"
	}
	if !in.SelfUpdated && in.InSync {
		return true, "up-to-date"
	}
	return false, ""
}

// blockedLine: dòng log khi lịch đêm bỏ qua bản đã bị chặn (E2E grep "lịch đêm
// không tự thử lại"). Quay về bản cũ đã THẤT BẠI (b.RollbackFailed) thì KHÔNG
// được nói "đã tự quay về bản cũ" — máy đang cần xử lý tay.
func blockedLine(v string, b hostlink.UpdateBlocked) string {
	if b.RollbackFailed {
		return "genh: bản " + v + " đã lỗi ở lần cập nhật trước và tự quay về bản cũ CŨNG THẤT BẠI — cần xử lý tay ngay" + backupHint(b) + ". Lịch đêm không tự thử lại bản này."
	}
	return "genh: bản " + v + " đã lỗi ở lần cập nhật trước và đã tự quay về bản cũ — lịch đêm không tự thử lại bản này. Có bản mới hơn sẽ tự cài; muốn thử lại ngay: bấm \"Cập nhật ngay\" trong Console hoặc chạy genh update."
}

// backupHint: cách xử lý tay khi quay về bản cũ thất bại. CSDL đã bị đụng (có
// BackupKey — genh chỉ ghi khoá khi đó) → khôi phục bản sao lưu rồi up -d. CSDL
// CHƯA bị đụng → TUYỆT ĐỐI không khôi phục (worker/bridge/api vẫn ghi sau lúc
// sao lưu — khôi phục sẽ xoá mất), chỉ up -d.
func backupHint(b hostlink.UpdateBlocked) string {
	if b.BackupKey != "" {
		return " (khôi phục bản sao lưu " + b.BackupKey + " rồi chạy docker compose up -d --remove-orphans — xem logs/auto-update.log)"
	}
	if !b.DBTouched {
		return " (CSDL chưa bị đụng — KHÔNG khôi phục bản sao lưu, chỉ chạy docker compose up -d --remove-orphans — xem logs/auto-update.log)"
	}
	return " (xem logs/auto-update.log)"
}

// blockedConsoleMessage: thông điệp hộp thư Console khi tiến trình re-exec (vừa
// tự thay binary) gặp bản bị chặn — tiến trình ngoài đã ghi "running" nên phải
// kết thúc nó. Tiến trình ngoài thì KHÔNG dùng hàm này (trả hộp thư về như cũ).
func blockedConsoleMessage(v string, b hostlink.UpdateBlocked) string {
	if b.RollbackFailed {
		return "Bản " + v + " đã lỗi ở lần cập nhật trước và tự quay về bản cũ CŨNG THẤT BẠI — cần xử lý tay ngay" + backupHint(b) + ". Lịch đêm không tự thử lại bản này. (" + ops.ErrCodeUpdateBlocked + ")"
	}
	return "Bản " + v + " đã lỗi ở lần cập nhật trước và đã tự quay về bản cũ — lịch đêm không tự thử lại bản này. Bấm \"Cập nhật ngay\" để thử lại. (" + ops.ErrCodeUpdateBlocked + ")"
}

// skipBlockedUpdate: lịch đêm gặp đúng bản đã bị chặn — chỉ in một dòng log và
// để NGUYÊN update-status.json như lần cập nhật lỗi để lại (statusSnap chụp
// trước Start): không làm mới finished_at mỗi đêm (thẻ đỏ của Console tự hết
// sau 24 giờ), không ghi đè thông điệp gốc (mã lỗi, lý do, bản sao lưu, hoặc
// cảnh báo quay về bản cũ thất bại). selfUpdated: tiến trình này không có ảnh
// chụp (tiến trình ngoài đã báo "running") → kết thúc bằng thông điệp chặn.
func skipBlockedUpdate(w io.Writer, installDir, v string, b hostlink.UpdateBlocked, statusSnap []byte, hadStatus, selfUpdated bool) {
	_, _ = fmt.Fprintln(w, blockedLine(v, b))
	if !selfUpdated {
		_ = hostlink.RestoreStatusSnapshot(installDir, statusSnap, hadStatus)
		return
	}
	_ = hostlink.Finish(installDir, "failed", v, blockedConsoleMessage(v, b))
}

// consoleUpdateMessage dựng thông điệp lỗi cho hộp thư Console từ lỗi của
// ops.RunUpdate: "<What> — <Next> (<mã>)" — Console chọn lời dẫn theo mã (vd
// GH-E948 ổ đĩa đầy: Owner PHẢI dọn đĩa) và hiện phần còn lại trong "Chi tiết
// kỹ thuật". Ổ đĩa đầy kèm Why (còn bao nhiêu GB / cần bao nhiêu). Bỏ dấu `
// (định dạng lệnh cho terminal, Console hiện chữ thường).
func consoleUpdateMessage(err error) string {
	opErr, ok := err.(*ops.OpError)
	if !ok {
		return err.Error()
	}
	msg := opErr.What
	if opErr.Code == ops.ErrCodeUpdateDiskLow && opErr.Why != "" {
		msg += " (" + opErr.Why + ")"
	}
	if opErr.Next != "" {
		msg += " — " + opErr.Next
	}
	if opErr.Code != "" {
		msg += " (" + opErr.Code + ")"
	}
	return strings.ReplaceAll(msg, "`", "")
}

// upToDateLine: dòng log khi dịch vụ đã đúng bản (E2E grep "không cần cập nhật").
func upToDateLine(v string) string {
	return "genh: dịch vụ đã ở đúng bản " + v + " — không cần cập nhật (không sao lưu, không tải ảnh). Dịch vụ đang dừng/lỗi thì chạy genh start."
}

// deferredAfterRunLine: RunUpdate ĐÃ chạy xong bằng bản genh hiện tại nhưng bản
// genh mới hơn đang bị thời gian chín hoãn — KHÔNG in "genh: cập nhật xong."
// (người đọc log sẽ tưởng bản mới đã cài).
const deferredAfterRunLine = "genh: dịch vụ đã nâng cấp theo bản genh hiện tại — bản genh mới đang đợi đủ 24 giờ (thời gian chín) mới tự cài."

// updateDoneLine là dòng kết vào logs/auto-update.log. Bản genh mới bị thời
// gian chín hoãn (deferred) và dịch vụ đã khớp bản hiện tại → không có gì để
// cập nhật; KHÔNG được in "genh: cập nhật xong." — chỉ in câu đó khi RunUpdate
// thật sự chạy xong.
func updateDoneLine(deferred bool) string {
	if deferred {
		return "genh: không có gì để cập nhật — bản genh mới đang đợi đủ 24 giờ (thời gian chín) mới tự cài."
	}
	return "genh: cập nhật xong."
}

// selfUpdateMinAge chọn thời gian chín (selfupdate.Options.MinAge) cho bước
// tự cập nhật binary theo cách `genh update` được gọi:
//   - timer đêm gọi `update --yes --quiet` (internal/autoupdate/content.go)
//     ⇒ --yes, không --if-requested ⇒ selfupdate.NightlyMinAge (24 giờ): bản
//     vừa lên bản chính thức chưa đủ 24 giờ thì đợi đêm sau;
//   - nút "Cập nhật ngay" trong Console đi qua `genh handle-requests` →
//     `update --yes --if-requested` (handleRequestUpdateArgs) ⇒ 0, không chặn;
//   - Owner gõ tay `genh update` (không --yes) ⇒ 0, không chặn.
//
// Cờ kích hoạt CỐ Ý là --yes chứ không phải một cờ nội bộ riêng (kiểu
// --scheduled): unit systemd/crontab/LaunchAgent/schtasks chỉ được ghi lúc
// `genh install`/`genh auto-update enable` — `genh update` không ghi lại —
// nên mọi máy đã cài đều đang chạy `update --yes --quiet`; đổi sang cờ mới
// sẽ làm các máy đó âm thầm mất cổng 24 giờ. Đổi lại: ai gõ tay
// `genh update --yes` cũng bị đợi; dòng lý do (selfupdate) nói rõ "chế độ
// --yes" và cách cài ngay (bỏ --yes / nút "Cập nhật ngay").
func selfUpdateMinAge(yes, ifRequested bool) time.Duration {
	if yes && !ifRequested {
		return selfupdate.NightlyMinAge
	}
	return 0
}

// trySelfUpdateAndReExec chạy internal/selfupdate.Run; nếu binary vừa được
// thay trên đĩa, RE-EXEC chính nó (cùng đường dẫn, giờ đã là bản MỚI) với
// đúng args gốc + "--self-updated", CHỜ tiến trình con chạy hết phần còn lại
// của `genh update` rồi trả (mã thoát của con, true). Trả (0, false) nếu
// không tự cập nhật gì (đã mới nhất/bản dev/lỗi mạng) — caller tiếp tục chạy
// phần nâng cấp dịch vụ bằng CHÍNH tiến trình hiện tại, không re-exec.
//
// Lý do BẮT BUỘC phải re-exec thay vì tự chạy tiếp trong cùng tiến trình:
// tiến trình đang chạy đã nạp SẴN code + compose.yaml nhúng của bản CŨ vào
// bộ nhớ — chỉ thay tệp trên đĩa không đổi gì tiến trình đang chạy đang
// dùng. Phần đồng bộ compose.yaml (ops.RunUpdate, bước 1.5) PHẢI chạy bằng
// code MỚI để lấy đúng compose.yaml nhúng của bản mới.
//
// minAge là thời gian chín truyền thẳng vào selfupdate.Options.MinAge (xem
// selfUpdateMinAge) — bản chưa đủ chín: Run trả Skipped/Deferred, hàm này
// trả (0, false, true): caller chạy phần dịch vụ như khi đã mới nhất nhưng
// biết là có bản mới đang đợi (dòng kết không được nói "cập nhật xong").
//
// ctx (signalContext của tiến trình ngoài): tự cập nhật dừng theo tín hiệu; khi
// đang chờ tiến trình con mà ctx bị huỷ (systemd KillMode=mixed chỉ gửi SIGTERM
// cho tiến trình CHÍNH) thì chuyển tiếp SIGTERM cho con và TIẾP TỤC chờ con
// quay về bản cũ xong — không Kill con.
func trySelfUpdateAndReExec(ctx context.Context, originalArgs []string, quiet bool, minAge time.Duration) (exitCode int, reExeced bool, deferred bool) {
	execPath, err := os.Executable()
	if err != nil {
		fmt.Fprintf(os.Stderr, "genh: không tự cập nhật binary được (không xác định được đường dẫn của chính nó): %v — tiếp tục với bản hiện tại.\n", err)
		return 0, false, false
	}
	execPath, _ = filepath.Abs(execPath)

	suOpts := selfupdate.Options{
		Owner: selfupdateOwner, Repo: selfupdateRepo,
		CurrentVersion: version,
		GOOS:           runtime.GOOS, GOARCH: runtime.GOARCH,
		ExecutablePath: execPath,
		Out:            os.Stdout,
		Quiet:          quiet,
		MinAge:         minAge,
	}
	if selfUpdateTweak != nil {
		selfUpdateTweak(&suOpts)
	}
	res, err := selfUpdateRun(ctx, suOpts)
	if err != nil {
		// Tải/kiểm checksum/thay binary thất bại: KHÔNG chặn `genh update`
		// — báo rõ rồi tiếp tục nâng cấp dịch vụ bằng binary hiện tại.
		fmt.Fprintf(os.Stderr, "genh: tự cập nhật binary thất bại (%v) — tiếp tục nâng cấp dịch vụ với bản genh hiện tại.\n", err)
		return 0, false, false
	}
	if !res.Updated {
		return 0, false, res.Deferred
	}

	newArgs := append(append([]string{}, originalArgs...), "--self-updated")
	child := exec.Command(suOpts.ExecutablePath, append([]string{"update"}, newArgs...)...)
	child.Stdin = os.Stdin
	child.Stdout = os.Stdout
	child.Stderr = os.Stderr
	if err := reExecChild(ctx, child); err != nil {
		if errors.Is(err, errStoppedBeforeReExec) {
			fmt.Fprintf(os.Stderr, "genh: nhận tín hiệu dừng ngay sau khi tải genh %s — chưa đụng gì tới dịch vụ; lần sau (lịch đêm hoặc genh update) sẽ làm tiếp.\n", res.To)
			return exitStoppedBeforeReExec, true, false
		}
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) {
			return exitErr.ExitCode(), true, false
		}
		fmt.Fprintf(os.Stderr, "genh: chạy lại genh %s sau tự cập nhật thất bại: %v\n", res.To, err)
		return 1, true, false
	}
	return 0, true, false
}

// exitStoppedBeforeReExec: mã nội bộ trySelfUpdateAndReExec trả khi KHÔNG chạy
// được genh bản mới vì tín hiệu dừng (errStoppedBeforeReExec) — caller ghi
// Console GH-E94B "chưa đụng gì" rồi thoát mã 1 (không lộ mã này ra ngoài).
const exitStoppedBeforeReExec = -2

// childFailedMessage: thông điệp Console khi tiến trình con (genh bản mới) không
// ghi được kết quả. stoppedBeforeChild: tín hiệu dừng tới trước khi chạy con —
// chưa đụng gì (GH-E94B, Console hiện thẻ "dừng giữa chừng — chưa đụng gì",
// không phải thẻ lỗi đỏ "đã tự quay về").
func childFailedMessage(stoppedBeforeChild bool, cause error) string {
	if stoppedBeforeChild {
		if cause == nil {
			cause = errStoppedBeforeReExec
		}
		return consoleUpdateMessage(ops.InterruptedBeforeTouch(cause))
	}
	return "Cập nhật dừng giữa chừng — xem logs/auto-update.log"
}

// errStoppedBeforeReExec: tín hiệu dừng tới trước khi kịp chạy genh bản mới.
var errStoppedBeforeReExec = errors.New("nhận tín hiệu dừng trước khi chạy genh bản mới")

// runChildForwardingSignal chạy child và chờ nó xong; trong lúc chờ, ctx bị huỷ
// (tín hiệu dừng tới tiến trình này) ⇒ chuyển tiếp SIGTERM cho con một lần rồi
// VẪN chờ (con tự quay về bản cũ — xem ops.rollbackAndWrap). Windows không gửi
// được SIGTERM/Interrupt cho tiến trình khác ⇒ chỉ ghi log (con cùng console
// vẫn nhận Ctrl-C của chính nó). ctx ĐÃ bị huỷ trước khi chạy ⇒ KHÔNG chạy con
// (con chưa kịp cài bộ bắt tín hiệu sẽ bị giết ngay, không ghi được kết quả) —
// trả errStoppedBeforeReExec.
func runChildForwardingSignal(ctx context.Context, child *exec.Cmd) error {
	if ctx.Err() != nil {
		return errStoppedBeforeReExec
	}
	if err := child.Start(); err != nil {
		return err
	}
	done := make(chan struct{})
	defer close(done)
	go func() {
		select {
		case <-done:
		case <-ctx.Done():
			if runtime.GOOS == "windows" {
				fmt.Fprintln(os.Stderr, "genh: nhận tín hiệu dừng — đang chờ genh bản mới chạy xong (Windows không chuyển tiếp được tín hiệu).")
				return
			}
			fmt.Fprintln(os.Stderr, "genh: nhận tín hiệu dừng — chuyển cho genh bản mới để quay về bản cũ rồi dừng…")
			_ = child.Process.Signal(forwardSignal(ctx))
		}
	}()
	return child.Wait()
}

func runAutoUpdate(args []string) int {
	if len(args) == 0 {
		_, _ = fmt.Fprintln(os.Stderr, "genh: cách dùng: genh auto-update enable|disable|status")
		return 2
	}
	fs, port, installDir := opsFlagSet("auto-update " + args[0])
	if err := fs.Parse(args[1:]); err != nil {
		return 2
	}
	env, ok := resolveOpsEnv(*port, *installDir)
	if !ok {
		return 1
	}

	execPath, err := os.Executable()
	if err != nil {
		fmt.Fprintf(os.Stderr, "genh: không xác định được đường dẫn của chính genh: %v\n", err)
		return 1
	}
	execPath, _ = filepath.Abs(execPath)
	logFile := filepath.Join(config.New(env.InstallDir).LogsDir(), "auto-update.log")

	ctx, stop := signalContext()
	defer stop()
	deps := hostInfoEnvFn().Base
	deps.GenhPath, deps.LogFile = execPath, logFile
	deps.InstallDir, deps.Nightly = env.InstallDir, nightlyJobFor(env.InstallDir, env.Port)

	switch args[0] {
	case "enable":
		if err := os.MkdirAll(filepath.Dir(logFile), 0o755); err != nil {
			fmt.Fprintf(os.Stderr, "genh: không tạo được thư mục log: %v\n", err)
			return 1
		}
		res, err := autoupdate.Enable(ctx, deps)
		if err != nil {
			if other, owned := autoupdate.OwnerOf(err); owned {
				fmt.Fprintln(os.Stderr, "genh: "+ownedByOtherLine(other, env.InstallDir))
				return 1
			}
			fmt.Fprintf(os.Stderr, "genh: bật tự cập nhật hằng đêm thất bại: %v\n", err)
			return 1
		}
		// Sếp chủ động bật lại: bỏ dấu "đã tắt" (tự lành lại được áp dụng).
		if err := ops.SetAutoUpdateOptOut(env.InstallDir, false, time.Now()); err != nil {
			fmt.Fprintf(os.Stderr, "genh: cảnh báo — không xoá được %s: %v\n", ops.AutoUpdateOptOutPath(env.InstallDir), err)
		}
		// v0.1.33: báo Console (genh.json) lịch đêm đã bật — lỗi ghi chỉ làm Console không hứa "Tự cài".
		_ = hostlink.SetAutoUpdate(env.InstallDir, version, true)
		fmt.Println(res.Msg)
		printLingerWarning(os.Stdout, res.Warning, tui.IsTerminal(os.Stdout))
		// v0.1.54: người gác yêu cầu (.path) lỗi ⇒ tự chữa (reset-failed + restart; vẫn lỗi ⇒ dự phòng
		// quét mỗi phút); đã sửa gốc (sysctl…) ⇒ .path sống lại, gỡ dự phòng. Im lặng khi khoẻ.
		healRequestWatcher(ctx, deps, os.Stdout)
		// v0.1.53 (F-95): làm mới autostart-status + nightly-status (linger vừa đổi).
		refreshAutostartStatus(env.InstallDir)
		_ = ops.RecordNightlyStatus(ctx, env.InstallDir, deps)
		return 0
	case "disable":
		msg, err := autoupdate.Disable(ctx, deps)
		if err != nil {
			fmt.Fprintf(os.Stderr, "genh: tắt tự cập nhật hằng đêm thất bại: %v\n", err)
			return 1
		}
		// Ghi nhớ lựa chọn của Sếp: genh KHÔNG tự bật lại (tự lành) lịch đêm này.
		if err := ops.SetAutoUpdateOptOut(env.InstallDir, true, time.Now()); err != nil {
			fmt.Fprintf(os.Stderr, "genh: cảnh báo — không ghi được %s: %v (lần cập nhật sau có thể tự bật lại lịch đêm)\n",
				ops.AutoUpdateOptOutPath(env.InstallDir), err)
		}
		// v0.1.53: Disable là best-effort (bỏ qua lỗi systemctl/crontab — vd chạy bằng tài
		// khoản khác hay không có phiên systemd --user) ⇒ HỎI LẠI trạng thái thật, không báo
		// "Đã tắt" khi lịch vẫn bật (Console cũng nói thật: "Sếp đã tắt nhưng lịch vẫn bật").
		stillOn := false
		if st, serr := autoupdate.GetStatus(ctx, deps); serr == nil && st.Enabled && !st.OwnedByOther {
			stillOn = true
		}
		_ = hostlink.SetAutoUpdate(env.InstallDir, version, stillOn)
		_ = ops.RecordNightlyStatus(ctx, env.InstallDir, deps)
		if stillOn {
			fmt.Fprintln(os.Stderr, disableStillOnLine)
			return 1
		}
		fmt.Println(msg)
		return 0
	case "status":
		st, err := autoupdate.GetStatus(ctx, deps)
		if err != nil {
			fmt.Fprintf(os.Stderr, "genh: kiểm trạng thái tự cập nhật thất bại: %v\n", err)
			return 1
		}
		var logMod time.Time
		fi, logErr := os.Stat(logFile)
		if logErr == nil {
			logMod = fi.ModTime()
		}
		fmt.Print(nightlyStatusText(st, logMod, logErr, ops.AutoUpdateOptedOut(env.InstallDir), time.Now()))
		// v0.1.54: tự chữa người gác yêu cầu rồi nói thật tình trạng của nó (dự phòng quét mỗi phút…).
		wh := autoupdate.HealRequestWatcher(ctx, deps)
		fmt.Print(requestWatcherStatusText(wh))
		if wh.Changed {
			_ = ops.RecordNightlyStatus(ctx, env.InstallDir, deps)
		}
		return 0
	default:
		_, _ = fmt.Fprintf(os.Stderr, "genh: lệnh con auto-update không rõ %q (dùng enable|disable|status)\n", args[0])
		return 2
	}
}

// healRequestWatcher chạy autoupdate.HealRequestWatcher (v0.1.54) và in MỘT dòng khi vừa đổi trạng
// thái (chữa xong / bật dự phòng / gỡ dự phòng) rồi làm mới run/nightly-status.json; im lặng khi
// khoẻ hoặc không đổi. Dùng ở `auto-update enable`; KHÔNG gọi từ `handle-requests` (chạy trong service
// mà .path kích ⇒ trạng thái .path không đáng tin). deps cần InstallDir (ghi nightly-status).
func healRequestWatcher(ctx context.Context, deps autoupdate.Deps, w io.Writer) autoupdate.WatcherHealth {
	h := autoupdate.HealRequestWatcher(ctx, deps)
	if !h.Changed {
		return h
	}
	if text := strings.TrimRight(requestWatcherStatusText(h), "\n"); text != "" {
		_, _ = fmt.Fprintln(w, "genh: "+strings.TrimPrefix(text, "  "))
	}
	if deps.InstallDir != "" {
		_ = ops.RecordNightlyStatus(ctx, deps.InstallDir, deps)
	}
	return h
}

// requestWatcherStatusText: phần "Người gác yêu cầu" của `genh auto-update status` (rỗng khi khoẻ
// và không có gì vừa đổi).
func requestWatcherStatusText(h autoupdate.WatcherHealth) string {
	switch {
	case h.Healed && h.FallbackRemoved:
		return "  Người gác yêu cầu (.path) đã sống lại (reset-failed + restart) — đã gỡ dự phòng quét mỗi phút.\n"
	case h.Healed:
		return "  Người gác yêu cầu (.path): vừa tự chữa xong (reset-failed + restart).\n"
	case h.State == autoupdate.WatcherHealthFallback || h.State == autoupdate.WatcherHealthFailed:
		return "  " + h.Line() + "\n"
	case h.FallbackRemoved: // .path tự sống lại, chỉ cần gỡ dự phòng
		return "  Người gác yêu cầu (.path) đã sống lại — đã gỡ dự phòng quét mỗi phút.\n"
	}
	return ""
}

// disableStillOnLine: `genh auto-update disable` xong mà lịch đêm vẫn bật (tắt hụt).
const disableStillOnLine = "genh: CHƯA tắt được — lịch tự cập nhật đêm vẫn đang bật (máy vẫn tự lên bản mới ~03:00). Chạy lại bằng đúng tài khoản đã bật lịch (Linux: trong phiên đăng nhập có systemd --user), rồi kiểm: genh auto-update status"

// nightlyJobFor dựng NightlyJob cho bản cài: --install-dir/--port (khi ≠ mặc định) và
// GENH_COMPOSE_FILE nếu phiên hiện tại có — lịch chạy ngoài phiên shell của Sếp.
func nightlyJobFor(installDir string, port int) autoupdate.NightlyJob {
	job := autoupdate.NightlyJob{InstallDir: installDir}
	if port > 0 && port != machine.DefaultPort {
		job.Port = port
	}
	if v := os.Getenv(compose.EnvOverrideVar); v != "" {
		job.Env = append(job.Env, compose.EnvOverrideVar+"="+v)
	}
	return job
}

// ownedByOtherLine: một dòng cảnh báo khi lịch đang thuộc bản cài khác còn sống.
func ownedByOtherLine(other, self string) string {
	return (&autoupdate.OwnedByOtherError{Other: other, Self: self}).Error()
}

// printLingerWarning in cảnh báo linger: có TTY thì tô đậm đỏ (lipgloss); không TTY
// (lịch đêm ghi vào logs/auto-update.log) thì chữ thường, không mã màu.
func printLingerWarning(w io.Writer, warning string, tty bool) {
	if warning == "" {
		return
	}
	if tty {
		style := lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("9"))
		_, _ = fmt.Fprintln(w, style.Render(warning))
		return
	}
	_, _ = fmt.Fprintln(w, strings.Replace(warning, "CẢNH BÁO:", "cảnh báo:", 1))
}

// nightlyStaleAfter: log lịch đêm không có dòng mới hơn ngưỡng này (36 giờ — thống nhất
// với Console, NIGHTLY_STALE_HOURS) thì lịch đêm coi như không chạy.
const nightlyStaleAfter = 36 * time.Hour

// nightlyMechanismText: tên cơ chế bằng tiếng Việt cho `genh auto-update status`.
func nightlyMechanismText(st autoupdate.Status) string {
	switch st.Mechanism {
	case autoupdate.ScheduleSystemd:
		return "systemd --user timer (" + autoupdate.TaskName + ".timer)"
	case autoupdate.ScheduleCron:
		return "crontab"
	case autoupdate.ScheduleLaunchd:
		return "LaunchAgent (macOS)"
	case autoupdate.ScheduleSchtasks:
		return "Task Scheduler (Windows)"
	}
	return "chưa có (không có unit systemd, dòng crontab, LaunchAgent hay Task nào)"
}

// nightlyStatusText (hàm thuần) dựng nội dung `genh auto-update status` (F-94): ĐỦ 5
// thông tin — bật/tắt thật, cơ chế, đã bật + đang chạy, lần chạy gần nhất/kế tiếp,
// linger — cùng cảnh báo khi logs/auto-update.log (logMod/logErr) không có dòng mới
// hơn 36 giờ. optedOut: Sếp đã chủ động tắt (`genh auto-update disable`).
func nightlyStatusText(st autoupdate.Status, logMod time.Time, logErr error, optedOut bool, now time.Time) string {
	loc := now.Location()
	yesNo := func(b bool) string {
		if b {
			return "có"
		}
		return "không"
	}
	fmtTime := func(t time.Time) string { return t.In(loc).Format("02/01/2006 15:04") + " (giờ máy)" }

	running := st.Enabled && !st.OwnedByOther
	// systemd: unit đã enable mà timer không active (failed/inactive) ≠ "BẬT".
	broken := st.Mechanism == autoupdate.ScheduleSystemd && st.UnitPresent && st.UnitFileState == "enabled" && st.Active != "active"
	logMissing := logErr != nil
	// Log chưa có VÀ chưa từng có lần chạy nào (vừa bật xong) chưa phải dấu hiệu hỏng. Log chưa có mà timer
	// đã kích: chỉ cảnh báo khi lần kích đó cũ hơn 36 giờ — sau khi khởi động lại, systemd nạp mtime tệp stamp
	// (tạo lúc bật lịch) vào LastTriggerUSec nên "đã kích" chưa chắc đã có lần chạy thật.
	logStale := running && ((!logMissing && now.Sub(logMod) > nightlyStaleAfter) ||
		(logMissing && !st.LastRun.IsZero() && now.Sub(st.LastRun) > nightlyStaleAfter))

	var b strings.Builder
	switch {
	case st.OwnedByOther:
		b.WriteString("Tự cập nhật hằng đêm: TẮT (lịch đêm của máy đang thuộc bản cài khác — bản cài này không đổi lịch)\n")
	case running && !logStale:
		b.WriteString("Tự cập nhật hằng đêm: BẬT\n")
	case running || broken:
		b.WriteString("Tự cập nhật hằng đêm: BẬT NHƯNG KHÔNG CHẠY\n")
	case optedOut:
		b.WriteString("Tự cập nhật hằng đêm: TẮT (Sếp đã chủ động tắt — bật lại: genh auto-update enable)\n")
	default:
		b.WriteString("Tự cập nhật hằng đêm: TẮT\n")
	}
	_, _ = fmt.Fprintf(&b, "  Cơ chế: %s\n", nightlyMechanismText(st))
	enabled, active := st.Enabled, st.Enabled
	if st.Mechanism == autoupdate.ScheduleSystemd {
		enabled, active = st.UnitFileState == "enabled", st.Active == "active"
	}
	_, _ = fmt.Fprintf(&b, "  Đã bật (enabled): %s · Lịch đang chạy (active): %s\n", yesNo(enabled), yesNo(active))
	switch {
	case !st.LastRun.IsZero():
		_, _ = fmt.Fprintf(&b, "  Lần chạy gần nhất: %s\n", fmtTime(st.LastRun))
	case !logMissing:
		_, _ = fmt.Fprintf(&b, "  Lần chạy gần nhất: %s\n", fmtTime(logMod))
	default:
		b.WriteString("  Lần chạy gần nhất: chưa chạy lần nào\n")
	}
	if st.NextRun.IsZero() {
		b.WriteString("  Lần kế tiếp: không rõ\n")
	} else {
		_, _ = fmt.Fprintf(&b, "  Lần kế tiếp: %s\n", fmtTime(st.NextRun))
	}
	switch st.Linger {
	case "yes":
		b.WriteString("  Linger: có (tiến trình nền chạy cả khi không ai đăng nhập)\n")
	case "no":
		b.WriteString("  Linger: KHÔNG — chạy một lần: sudo loginctl enable-linger $USER\n")
	case "not_applicable":
		b.WriteString("  Linger: không áp dụng trên hệ điều hành này\n")
	default:
		b.WriteString("  Linger: không rõ (không hỏi được loginctl)\n")
	}
	if st.Detail != "" {
		_, _ = fmt.Fprintf(&b, "  %s\n", st.Detail)
	}
	if st.Owner != "" && st.OwnedByOther {
		_, _ = fmt.Fprintf(&b, "  Lịch đang thuộc bản cài: %s\n", st.Owner)
	}
	if logStale {
		last := "chưa có"
		if !logMissing {
			last = fmtTime(logMod)
		}
		_, _ = fmt.Fprintf(&b, "CẢNH BÁO: logs/auto-update.log không có dòng mới hơn 36 giờ (lần ghi cuối %s) — lịch đêm có thể không chạy. Bật lại: genh auto-update enable\n", last)
	}
	return b.String()
}

func runBackup(args []string) int {
	fs, port, installDir := opsFlagSet("backup")
	to := fs.String("to", "", "copy thêm bản backup ra đường dẫn này trên host (tuỳ chọn)")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	env, ok := resolveOpsEnv(*port, *installDir)
	if !ok {
		return 1
	}
	ctx, stop := signalContext()
	defer stop()
	if err := ops.RunBackup(ctx, env, *to, nil, os.Stdout); err != nil {
		reportOpErr(err)
		return 1
	}
	return 0
}

func runRestore(args []string) int {
	fs, port, installDir := opsFlagSet("restore")
	ifRequested := fs.Bool("if-requested", false, "khôi phục bản Owner vừa chọn trong Console (request/restore.json — watcher gọi); không có yêu cầu thì thoát ngay")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if !*ifRequested && fs.NArg() != 1 {
		_, _ = fmt.Fprintln(os.Stderr, "genh: cách dùng: genh restore <khoá-backup>   (hoặc --if-requested)")
		return 2
	}
	env, ok := resolveOpsEnv(*port, *installDir)
	if !ok {
		return 1
	}
	ctx, stop := signalContext()
	defer stop()
	// Khoá loại trừ (v0.1.37): cùng khoá với update/import — --if-requested chờ
	// tối đa requestLockWait, gõ tay bận ⇒ thoát 1 (GH-E94A).
	mode := lockManual
	if *ifRequested {
		if !hostlink.HasRestoreRequest(env.InstallDir) {
			return 0
		}
		// Yêu cầu không xoá được và đã báo GH-E94C cho đúng yêu cầu này: không làm lại.
		if hostlink.RestoreUndeletableReported(env.InstallDir, ops.ErrCodeRequestUndeletable) {
			return 0
		}
		mode = lockRequested
	}
	onExpired := func() {
		// Chờ khoá đủ 30 phút mà máy chủ vẫn bận: xoá yêu cầu + báo Console GH-E94A.
		if !*ifRequested || !hostlink.HasRestoreRequest(env.InstallDir) {
			return
		}
		req, cerr := hostlink.ConsumeRestoreRequest(env.InstallDir)
		if errors.Is(cerr, hostlink.ErrRequestUndeletable) {
			reportUndeletableRestore(env.InstallDir, cerr)
			return
		}
		_ = hostlink.StartRestore(env.InstallDir, req.Key)
		_ = hostlink.FinishRestore(env.InstallDir, "failed", "", ops.RequestBusyMessage)
	}
	lock, code, ok := acquireOpLockEx(ctx, env.InstallDir, mode, func() bool { return hostlink.HasRestoreRequest(env.InstallDir) }, onExpired, os.Stdout, os.Stderr)
	if !ok {
		return code
	}
	defer lock.Release()
	stopBeat := hostlink.StartHeartbeat(env.InstallDir, "restore")
	defer stopBeat()
	if *ifRequested {
		handled, err := ops.RunRestoreRequest(ctx, env, ops.RestoreDeps{}, os.Stdout)
		if err != nil {
			// Tệp yêu cầu còn nguyên = KHÔNG xoá được (GH-E94C): không làm, thoát 0.
			if hostlink.HasRestoreRequest(env.InstallDir) {
				reportUndeletableRestore(env.InstallDir, err)
				return 0
			}
			reportOpErr(err)
			return 1
		}
		if handled {
			fmt.Println("genh: khôi phục xong.")
		}
		return 0
	}
	if _, err := ops.RunRestore(ctx, env, fs.Arg(0), ops.RestoreDeps{}, os.Stdout); err != nil {
		reportOpErr(err)
		return 1
	}
	return 0
}

// runHandleRequests là lệnh watcher trên máy chủ gọi (internal/autoupdate):
// đọc hộp thư run/request và chuyển sang đúng lệnh — cập nhật trước (tự sao
// lưu), khôi phục sau. Hộp thư trống thì thoát ngay. KHÔNG tự lấy khoá loại
// trừ: runUpdate/runRestore bên trong lấy (chờ tối đa requestLockWait).
func runHandleRequests(args []string) int {
	fs, port, installDir := opsFlagSet("handle-requests")
	quiet := fs.Bool("quiet", false, "chỉ in các dòng quan trọng")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	dir, err := ops.ResolveInstallDir(*installDir)
	if err != nil {
		// Không xác định được thư mục cài đặt (thiếu --install-dir, GEN_HARNESS_HOME và
		// HOME): nếu GEN_HARNESS_HOME trỏ tới một thư mục thật thì báo Console "failed"
		// thay vì để yêu cầu nằm im; không có thì chỉ in lỗi.
		if v := os.Getenv(config.EnvRoot); v != "" {
			if fi, serr := os.Stat(v); serr == nil && fi.IsDir() {
				_ = hostlink.Start(v, version)
				_ = hostlink.Finish(v, "failed", "", "Máy chủ không xác định được thư mục cài đặt để làm yêu cầu — xem logs/auto-update.log")
				_, _ = fmt.Fprintf(os.Stderr, "genh: không xác định được thư mục cài đặt: %v\n", err)
				return 1
			}
		}
		_, _ = fmt.Fprintf(os.Stderr, "genh: không xác định được thư mục cài đặt: %v\n", err)
		return 1
	}
	pass := []string{"--port", strconv.Itoa(*port), "--install-dir", dir}
	// v0.1.54: KHÔNG tự chữa người gác (.path) ở đây. handle-requests luôn chạy BÊN TRONG
	// gen-harness-update-request.service; khi service mà .path kích đang chạy, systemd đưa .path sang
	// "active (running)" mà không dựng inotify nào ⇒ reset-failed + restart báo "sống" giả và gỡ nhầm
	// timer dự phòng (E2E v0.1.54). Chữa/gỡ dự phòng chỉ ở `auto-update enable|status` và lần cài/cập nhật.
	switch hostlink.Pending(dir) {
	case "update":
		return runUpdate(append(handleRequestUpdateArgs(*quiet), pass...))
	case "restore":
		return runRestore(append([]string{"--if-requested"}, pass...))
	case "offsite":
		// Không xoá được tệp yêu cầu thì không làm (xem runOffsite) — dò trước khi bắt đầu.
		if offsiteUndeletableReported(dir) {
			return 0
		}
		if perr := hostlink.RequestDirWritable(dir); perr != nil {
			reportUndeletableOffsite(dir, perr)
			return 0
		}
		return runOffsite(append(handleRequestOffsiteArgs(*quiet), pass...))
	case "doctor":
		return runDoctor(append(handleRequestDoctorArgs(), pass...))
	case "watchdog":
		// Xoá yêu cầu TRƯỚC khi làm (watcher không kích lặp); tệp hỏng/action lạ ⇒ bỏ.
		// Đọc được mà KHÔNG xoá được (GH-E94C) ⇒ không làm, thoát 0.
		_, err := hostlink.ConsumeWatchdogRequest(dir)
		if hostlink.HasWatchdogRequest(dir) {
			reportUndeletableWatchdog(dir)
			return 0
		}
		if err != nil {
			_, _ = fmt.Fprintf(os.Stderr, "genh: bỏ yêu cầu gửi thử không hợp lệ: %v\n", err)
			return 0
		}
		return runDoctor(append(handleRequestWatchdogArgs(), pass...))
	default:
		return 0
	}
}

// reportUndeletableRestore / Offsite / Watchdog: yêu cầu Console KHÔNG xoá được (GH-E94C) —
// lỗi gốc in stderr/log, trạng thái failed ghi một lần (idempotent).
func reportUndeletableRestore(installDir string, cause error) {
	_, _ = fmt.Fprintf(os.Stderr, "genh: không xoá được yêu cầu khôi phục trong run/request (%v) — không làm yêu cầu này để tránh chạy lặp (%s).\n", cause, ops.ErrCodeRequestUndeletable)
	_, _ = hostlink.FailUndeletableRestore(installDir, ops.ErrCodeRequestUndeletable, ops.RequestUndeletableMessage("yêu cầu khôi phục"))
}

func reportUndeletableOffsite(installDir string, cause error) {
	_, _ = fmt.Fprintf(os.Stderr, "genh: không xoá được yêu cầu sao lưu ra ổ ngoài trong run/request (%v) — không làm yêu cầu này để tránh chạy lặp (%s).\n", cause, ops.ErrCodeRequestUndeletable)
	if prev, err := hostlink.ReadOffsiteStatus(installDir); err == nil && prev.State == hostlink.OffsiteStateFailed && prev.ErrorCode == ops.ErrCodeRequestUndeletable {
		return
	}
	st, err := hostlink.ReadOffsiteStatus(installDir)
	if err != nil {
		st = hostlink.OffsiteStatus{}
	}
	st.State, st.ErrorCode, st.LastAttemptAt = hostlink.OffsiteStateFailed, ops.ErrCodeRequestUndeletable, time.Now().UTC().Format(time.RFC3339)
	_ = hostlink.WriteOffsiteStatus(installDir, st)
}

// offsiteUndeletableReported: offsite-status đã là failed GH-E94C và tệp yêu cầu còn
// nằm đó từ trước lúc báo (cùng một yêu cầu) — không làm lại.
func offsiteUndeletableReported(installDir string) bool {
	st, err := hostlink.ReadOffsiteStatus(installDir)
	if err != nil || st.State != hostlink.OffsiteStateFailed || st.ErrorCode != ops.ErrCodeRequestUndeletable {
		return false
	}
	at, perr := time.Parse(time.RFC3339, st.LastAttemptAt)
	fi, serr := os.Lstat(hostlink.OffsiteRequestPath(installDir))
	return perr == nil && serr == nil && !fi.ModTime().Truncate(time.Second).After(at)
}

func reportUndeletableWatchdog(installDir string) {
	_, _ = fmt.Fprintf(os.Stderr, "genh: không xoá được yêu cầu gửi thử trong run/request — không làm yêu cầu này để tránh chạy lặp (%s).\n", ops.ErrCodeRequestUndeletable)
	st, err := hostlink.ReadWatchdogStatus(installDir)
	if err != nil {
		st = hostlink.WatchdogStatus{}
	}
	if st.Test != nil && !st.Test.OK && st.Test.ErrorCode == ops.ErrCodeRequestUndeletable {
		return
	}
	st.Test = &hostlink.WatchdogTest{At: time.Now().UTC().Format(time.RFC3339), OK: false, ErrorCode: ops.ErrCodeRequestUndeletable}
	_ = hostlink.WriteWatchdogStatus(installDir, st)
}

// handleRequestDoctorArgs: run/request/doctor.json → `genh doctor --if-requested`
// (gói chẩn đoán vào run/diagnostics/, genh tự xoá tệp yêu cầu trước khi làm).
func handleRequestDoctorArgs() []string { return []string{"--if-requested"} }

// handleRequestWatchdogArgs: run/request/watchdog.json ("Gửi thử") → một lượt
// trực canh kèm tin thử.
func handleRequestWatchdogArgs() []string { return []string{"--notify", "--test", "--quiet"} }

// handleRequestOffsiteArgs: cờ runHandleRequests chuyển cho runOffsite khi
// Console để lại run/request/offsite.json — lệnh con "run" + --if-requested:
// việc thật (set|run|disable) đọc từ tệp yêu cầu, không từ dòng lệnh.
func handleRequestOffsiteArgs(quiet bool) []string {
	a := []string{"run", "--if-requested"}
	if quiet {
		a = append(a, "--quiet")
	}
	return a
}

// offsiteFlags là cờ của `genh offsite <lệnh con>` sau khi Parse.
type offsiteFlags struct {
	sub           string
	port          int
	installDir    string
	allowSameDisk bool
	noRun         bool
	quiet         bool
	ifRequested   bool
	path          string
}

// parseOffsiteFlags phân tích `genh offsite set|run|status|disable [cờ…] [đường dẫn]`
// — MỌI cờ đứng trước đối số vị trí (package flag dừng ở đối số không-cờ đầu tiên).
func parseOffsiteFlags(args []string) (offsiteFlags, error) {
	if len(args) == 0 {
		return offsiteFlags{}, errors.New("thiếu lệnh con (set|run|status|disable)")
	}
	f := offsiteFlags{sub: args[0]}
	fs, port, installDir := opsFlagSet("offsite " + args[0])
	fs.SetOutput(io.Discard)
	var allow, noRun, quiet, ifReq *bool
	switch f.sub {
	case "set":
		allow = fs.Bool("allow-same-disk", false, "cho phép nơi lưu nằm CÙNG ổ với máy chủ (hỏng ổ là mất cả hai — chỉ CLI)")
		noRun = fs.Bool("no-run", false, "chỉ lưu nơi lưu + bật lịch, chưa xuất bản sao ngay")
		quiet = fs.Bool("quiet", false, "chỉ in dòng quan trọng")
	case "run":
		quiet = fs.Bool("quiet", false, "chỉ in dòng quan trọng (lịch tuần dùng cờ này)")
		ifReq = fs.Bool("if-requested", false, "làm yêu cầu Console trong run/request/offsite.json (watcher gọi); không có thì thoát ngay")
	case "status", "disable":
	default:
		return offsiteFlags{}, fmt.Errorf("lệnh con offsite không rõ %q (dùng set|run|status|disable)", f.sub)
	}
	if err := fs.Parse(args[1:]); err != nil {
		return offsiteFlags{}, err
	}
	f.port, f.installDir = *port, *installDir
	if allow != nil {
		f.allowSameDisk, f.noRun = *allow, *noRun
	}
	if quiet != nil {
		f.quiet = *quiet
	}
	if ifReq != nil {
		f.ifRequested = *ifReq
	}
	if f.sub == "set" {
		if fs.NArg() != 1 {
			return offsiteFlags{}, errors.New("cách dùng: genh offsite set [--allow-same-disk] [--no-run] <thư mục trên ổ USB/NAS>")
		}
		f.path = fs.Arg(0)
	} else if fs.NArg() != 0 {
		return offsiteFlags{}, fmt.Errorf("genh offsite %s không nhận đối số %q (cờ phải đứng trước)", f.sub, fs.Arg(0))
	}
	return f, nil
}

// offsiteExitCode: mã thoát cho lỗi của `genh offsite`. Lịch tuần (--quiet) và
// watcher (--if-requested) gặp khoá bận (GH-EB05) thoát 0 — tuần sau/lần bấm
// sau thử lại; mọi lỗi khác (kể cả chưa thấy ổ USB/NAS) thoát 1.
func offsiteExitCode(err error, f offsiteFlags) int {
	if err == nil {
		return 0
	}
	var oe *ops.OpError
	if errors.As(err, &oe) && oe.Code == ops.ErrCodeOffsiteBusy && (f.quiet || f.ifRequested) {
		return 0
	}
	return 1
}

// runOffsite: `genh offsite set|run|status|disable` (v0.1.40, F-12 — bản sao ngoài máy).
func runOffsite(args []string) int {
	f, err := parseOffsiteFlags(args)
	if err != nil {
		_, _ = fmt.Fprintf(os.Stderr, "genh: %v\n", err)
		_, _ = fmt.Fprintln(os.Stderr, "cách dùng: genh offsite set [--allow-same-disk] [--no-run] <thư mục> | run [--quiet] | status | disable")
		return 2
	}
	env, ok := resolveOpsEnv(f.port, f.installDir)
	if !ok {
		return 1
	}
	ctx, stop := signalContext()
	defer stop()
	deps := ops.OffsiteDeps{}
	switch f.sub {
	case "set":
		if f.allowSameDisk {
			_, _ = fmt.Fprintln(os.Stderr, "CẢNH BÁO: --allow-same-disk — "+ops.OffsiteSameDiskWarning+". Bạn tự quyết rủi ro này.")
		}
		err = ops.RunOffsiteSet(ctx, env, ops.OffsiteSetOptions{Path: f.path, AllowSameDisk: f.allowSameDisk, NoRun: f.noRun, Quiet: f.quiet}, deps, os.Stdout)
	case "run":
		if f.ifRequested {
			_, err = ops.RunOffsiteRequest(ctx, env, deps, os.Stdout)
			// KHÔNG xoá được tệp yêu cầu (GH-E94C): ops đã dừng TRƯỚC khi làm gì (như
			// ConsumeRequest của cập nhật) — báo một lần, thoát 0 (path unit không bị lỗi đỏ;
			// TriggerLimit chặn kích lặp).
			if errors.Is(err, hostlink.ErrRequestUndeletable) {
				reportUndeletableOffsite(env.InstallDir, err)
				return 0
			}
		} else {
			err = ops.RunOffsiteRun(ctx, env, ops.OffsiteRunOptions{Quiet: f.quiet}, deps, os.Stdout)
		}
	case "status":
		err = ops.RunOffsiteStatus(ctx, env, deps, os.Stdout)
	case "disable":
		err = ops.RunOffsiteDisable(ctx, env, deps, os.Stdout)
	}
	if err != nil {
		// Lịch tuần ghi stdout+stderr vào logs/offsite.log — báo lỗi đủ 3 dòng.
		reportOpErr(err)
	}
	return offsiteExitCode(err, f)
}

// handleRequestUpdateArgs là cờ runHandleRequests chuyển cho runUpdate khi
// Owner bấm "Cập nhật ngay": --yes (không tương tác) + --if-requested (chỉ
// chạy khi còn yêu cầu trong hộp thư) — có --if-requested nên
// selfUpdateMinAge trả 0, nút "Cập nhật ngay" KHÔNG bị thời gian chín chặn.
func handleRequestUpdateArgs(quiet bool) []string {
	upd := []string{"--yes", "--if-requested"}
	if quiet {
		upd = append(upd, "--quiet")
	}
	return upd
}

// doctorFlags là cờ của `genh doctor` sau khi Parse.
type doctorFlags struct {
	port        int
	installDir  string
	out         string
	notify      bool
	quiet       bool
	test        bool
	ifRequested bool
}

// parseDoctorFlags: `genh doctor [--out f] | --notify [--quiet] [--test] | --if-requested`.
func parseDoctorFlags(args []string) (doctorFlags, error) {
	fs, port, installDir := opsFlagSet("doctor")
	fs.SetOutput(io.Discard)
	out := fs.String("out", "genh-doctor-report.zip", "đường dẫn tệp báo cáo zip xuất ra")
	notify := fs.Bool("notify", false, "trực canh máy chủ một lượt: đo, tự khởi động lại dịch vụ chết, báo Telegram (không tạo zip)")
	quiet := fs.Bool("quiet", false, "kèm --notify: chỉ in khi có thay đổi (lịch 12 phút dùng cờ này)")
	test := fs.Bool("test", false, "kèm --notify: gửi thêm một tin thử qua Telegram")
	ifReq := fs.Bool("if-requested", false, "làm gói chẩn đoán Console yêu cầu (run/request/doctor.json — watcher gọi); không có thì thoát ngay")
	if err := fs.Parse(args); err != nil {
		return doctorFlags{}, err
	}
	if fs.NArg() != 0 {
		return doctorFlags{}, fmt.Errorf("genh doctor không nhận đối số %q", fs.Arg(0))
	}
	f := doctorFlags{port: *port, installDir: *installDir, out: *out, notify: *notify, quiet: *quiet, test: *test, ifRequested: *ifReq}
	if f.test && !f.notify {
		return doctorFlags{}, errors.New("--test chỉ dùng cùng --notify")
	}
	if f.notify && f.ifRequested {
		return doctorFlags{}, errors.New("--notify và --if-requested không dùng chung")
	}
	return f, nil
}

func runDoctor(args []string) int {
	f, err := parseDoctorFlags(args)
	if err != nil {
		_, _ = fmt.Fprintf(os.Stderr, "genh: %v\n", err)
		_, _ = fmt.Fprintln(os.Stderr, "cách dùng: genh doctor [--out report.zip] | --notify [--quiet] [--test] | --if-requested")
		return 2
	}
	env, ok := resolveOpsEnv(f.port, f.installDir)
	if !ok {
		return 1
	}
	ctx, stop := signalContext()
	defer stop()
	switch {
	case f.notify:
		// Mã thoát: luôn 0 trừ lỗi cấu hình nghiêm trọng — sự cố của máy chủ đã
		// báo qua Telegram/watchdog-status.json, timer không được "đỏ" vì chúng.
		if err := ops.RunWatchdog(ctx, env, ops.WatchdogOptions{Quiet: f.quiet, Test: f.test}, ops.WatchdogDeps{}, os.Stdout); err != nil {
			reportOpErr(err)
			return 1
		}
		return 0
	case f.ifRequested:
		if !hostlink.HasDoctorRequest(env.InstallDir) {
			return 0
		}
		if err := ops.RunDoctorRequest(ctx, env, ops.DoctorDeps{}, os.Stdout); err != nil {
			reportOpErr(err)
			return 1
		}
		return 0
	}
	if err := ops.RunDoctor(ctx, env, f.out, ops.DoctorDeps{}, os.Stdout); err != nil {
		reportOpErr(err)
		return 1
	}
	return 0
}

// runWatchdogCmd: `genh watchdog enable|disable|status` (v0.1.44, F-6b).
func runWatchdogCmd(args []string) int {
	if len(args) == 0 {
		_, _ = fmt.Fprintln(os.Stderr, "genh: cách dùng: genh watchdog enable|disable|status")
		return 2
	}
	fs, port, installDir := opsFlagSet("watchdog " + args[0])
	if err := fs.Parse(args[1:]); err != nil {
		return 2
	}
	env, ok := resolveOpsEnv(*port, *installDir)
	if !ok {
		return 1
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	sched := ops.NewWatchdogScheduler(env)
	switch args[0] {
	case "enable":
		msg, mech, err := sched.Enable(ctx)
		if err != nil {
			_, _ = fmt.Fprintf(os.Stderr, "genh: bật trực canh máy chủ thất bại: %v\n", err)
			return 1
		}
		if err := ops.SetWatchdogOptOut(env.InstallDir, false, time.Now()); err != nil {
			_, _ = fmt.Fprintf(os.Stderr, "genh: cảnh báo — không xoá được %s: %v (lần cập nhật sau vẫn coi như Owner đã tắt)\n",
				ops.WatchdogOptOutPath(env.InstallDir), err)
		}
		_ = hostlink.SetWatchdogSchedule(env.InstallDir, mech)
		fmt.Println(msg)
		return 0
	case "disable":
		msg, err := sched.Disable(ctx)
		if err != nil {
			_, _ = fmt.Fprintf(os.Stderr, "genh: tắt trực canh máy chủ thất bại: %v\n", err)
			return 1
		}
		// Ghi nhớ lựa chọn của Owner: install/update (kể cả lịch đêm) không bật lại.
		if err := ops.SetWatchdogOptOut(env.InstallDir, true, time.Now()); err != nil {
			_, _ = fmt.Fprintf(os.Stderr, "genh: cảnh báo — không ghi được %s: %v (lần cập nhật sau có thể bật lại trực canh)\n",
				ops.WatchdogOptOutPath(env.InstallDir), err)
		}
		_ = hostlink.SetWatchdogSchedule(env.InstallDir, "")
		fmt.Println(msg)
		return 0
	case "status":
		ss, err := sched.Status(ctx)
		if err != nil {
			_, _ = fmt.Fprintf(os.Stderr, "genh: kiểm lịch trực canh thất bại: %v\n", err)
			return 1
		}
		ws, werr := hostlink.ReadWatchdogStatus(env.InstallDir)
		fmt.Print(watchdogStatusText(ss, ws, werr, ops.WatchdogOptedOut(env.InstallDir)))
		return 0
	default:
		_, _ = fmt.Fprintf(os.Stderr, "genh: lệnh con watchdog không rõ %q (dùng enable|disable|status)\n", args[0])
		return 2
	}
}

// watchdogStatusText: cơ chế lịch + lần chạy gần nhất + sự cố đang mở.
func watchdogStatusText(ss autoupdate.WatchdogSchedule, ws hostlink.WatchdogStatus, werr error, optedOut bool) string {
	var b strings.Builder
	state := "TẮT"
	if ss.Enabled {
		state = "BẬT (" + ss.Mechanism + ")"
	} else if optedOut {
		state = "TẮT — Owner đã tắt bằng `genh watchdog disable` (cập nhật không tự bật lại; bật lại: genh watchdog enable)"
	}
	_, _ = fmt.Fprintf(&b, "Trực canh máy chủ: %s\n  %s\n", state, ss.Detail)
	if werr != nil || ws.LastRunAt == "" {
		b.WriteString("Lần chạy gần nhất: chưa có\n")
		return b.String()
	}
	_, _ = fmt.Fprintf(&b, "Lần chạy gần nhất: %s (%s)\n", ws.LastRunAt, ws.State)
	tg := ws.Telegram
	if ws.TelegramErrorCode != "" {
		tg += " — " + ws.TelegramErrorCode
	}
	_, _ = fmt.Fprintf(&b, "Telegram: %s\n", tg)
	if len(ws.Incidents) == 0 {
		b.WriteString("Sự cố đang mở: không có\n")
		return b.String()
	}
	_, _ = fmt.Fprintf(&b, "Sự cố đang mở (%d):\n", len(ws.Incidents))
	for _, i := range ws.Incidents {
		_, _ = fmt.Fprintf(&b, "  • %s — %s (từ %s)\n", i.Key, i.Title, i.Since)
	}
	return b.String()
}

func runResetSetup(args []string) int {
	fs, port, installDir := opsFlagSet("reset-setup")
	yes := fs.Bool("yes", false, "bỏ qua hỏi xác nhận")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	env, ok := resolveOpsEnv(*port, *installDir)
	if !ok {
		return 1
	}
	opts := ops.ResetSetupOptions{AutoApprove: *yes}
	if err := ops.RunResetSetup(env, opts, bufio.NewReader(os.Stdin), os.Stdout); err != nil {
		reportOpErr(err)
		return 1
	}
	return 0
}

func runResetPassword(args []string) int {
	fs, port, installDir := opsFlagSet("reset-password")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	env, ok := resolveOpsEnv(*port, *installDir)
	if !ok {
		return 1
	}
	ctx, stop := signalContext()
	defer stop()
	if err := ops.RunResetPassword(ctx, env, nil, os.Stdout); err != nil {
		reportOpErr(err)
		return 1
	}
	return 0
}

func runTrustCA(args []string) int {
	fs, port, installDir := opsFlagSet("trust-ca")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	env, ok := resolveOpsEnv(*port, *installDir)
	if !ok {
		return 1
	}
	ctx, stop := signalContext()
	defer stop()
	// interactive: Owner đang ngồi trước máy — macOS/Windows được phép bật hộp thoại xác nhận.
	if err := ops.RunTrustCA(ctx, env, true, ops.TrustCADeps{}, os.Stdout); err != nil {
		reportOpErr(err)
		return 1
	}
	return 0
}

// runRemote: `genh remote [status|tailscale|cloudflare|lan|local] [cờ]` (v0.1.46).
// Go flag dừng ở đối số đầu không phải cờ, nên tách lệnh con trước khi Parse.
func runRemote(args []string) int {
	sub := ""
	if len(args) > 0 && !strings.HasPrefix(args[0], "-") {
		sub, args = args[0], args[1:]
	}
	fs, port, installDir := opsFlagSet("remote")
	yes := fs.Bool("yes", false, "bỏ qua hỏi xác nhận")
	hostname := fs.String("hostname", "", "tên miền Cloudflare Tunnel (cloudflare)")
	name := fs.String("name", "", "tên máy hoặc IP trong mạng LAN (lan)")
	lan := fs.Bool("lan", false, "như `genh remote lan`")
	local := fs.Bool("local", false, "như `genh remote local`")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if *lan {
		if sub != "" && sub != "lan" || *local {
			_, _ = fmt.Fprintln(os.Stderr, "genh remote: chỉ chọn MỘT cách truy cập.")
			return 2
		}
		sub = "lan"
	}
	if *local {
		if sub != "" && sub != "local" {
			_, _ = fmt.Fprintln(os.Stderr, "genh remote: chỉ chọn MỘT cách truy cập.")
			return 2
		}
		sub = "local"
	}
	env, ok := resolveOpsEnv(*port, *installDir)
	if !ok {
		return 1
	}
	ctx, stop := signalContext()
	defer stop()
	renderer := lipgloss.NewRenderer(os.Stdout)
	red := renderer.NewStyle().Foreground(lipgloss.Color("9")).Bold(true)
	deps := ops.RemoteDeps{
		Interactive: tui.IsTerminal(os.Stdin) && tui.IsTerminal(os.Stdout),
		In:          bufio.NewReader(os.Stdin),
		Warn:        func(s string) string { return red.Render(s) },
	}
	opts := ops.RemoteOptions{Action: sub, Hostname: *hostname, Name: *name, Yes: *yes}
	if err := ops.RunRemote(ctx, env, opts, deps, os.Stdout); err != nil {
		reportOpErr(err)
		return ops.RemoteExitCode(err)
	}
	return 0
}

func runStop(args []string) int {
	fs, port, installDir := opsFlagSet("stop")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	env, ok := resolveOpsEnv(*port, *installDir)
	if !ok {
		return 1
	}
	if err := ops.RunStop(context.Background(), env, nil, os.Stdout); err != nil {
		reportOpErr(err)
		return 1
	}
	return 0
}

func runStart(args []string) int {
	fs, port, installDir := opsFlagSet("start")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	env, ok := resolveOpsEnv(*port, *installDir)
	if !ok {
		return 1
	}
	if err := ops.RunStart(context.Background(), env, nil, os.Stdout); err != nil {
		reportOpErr(err)
		return 1
	}
	return 0
}

func runUninstall(args []string) int {
	fs, port, installDir := opsFlagSet("uninstall")
	// v0.1.40 (F-12): mặc định GIỮ dữ liệu; --delete-data mới xoá volume.
	// --keep-data vẫn nhận (không làm gì thêm) cho script cũ.
	keepData := fs.Bool("keep-data", false, "giữ lại dữ liệu (đã là mặc định từ v0.1.40 — vẫn nhận cho script cũ)")
	deleteData := fs.Bool("delete-data", false, "XOÁ VĨNH VIỄN dữ liệu (volume Docker) — không có --yes thì phải gõ đúng \"XOÁ DỮ LIỆU\"")
	// --yes: cần cho kịch bản không tương tác (CI e2e, script) — trước phiên
	// này ops.UninstallOptions.AutoApprove chỉ dùng được từ test Go gọi thẳng
	// RunUninstall, CLI không có cách bật (xem comment cũ ở
	// internal/ops/uninstall.go). Đọc kỹ tài liệu (docs/handoff/05-installer.md
	// mục "Lệnh vận hành") trước khi đổi mô tả cờ ở đó.
	yes := fs.Bool("yes", false, "bỏ qua hỏi xác nhận (dùng cho script/CI không có TTY) — kèm --delete-data thì xoá dữ liệu không hỏi")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	env, ok := resolveOpsEnv(*port, *installDir)
	if !ok {
		return 1
	}
	opts := ops.UninstallOptions{KeepData: *keepData, DeleteData: *deleteData, AutoApprove: *yes}
	if err := ops.RunUninstall(context.Background(), env, opts, nil, os.Stdin, os.Stdout); err != nil {
		reportOpErr(err)
		return 1
	}
	// Gỡ luôn lịch tự cập nhật hằng đêm + watcher "Cập nhật ngay" — không để lại
	// dòng cron/unit systemd gọi một bản cài đã gỡ (lịch tuần bản sao ngoài máy
	// do ops.RunUninstall gỡ).
	//
	// v0.1.53 (F-98): lịch là CHUNG cho mọi bản cài của người dùng — Deps.InstallDir
	// là bản cài ĐANG gỡ; lịch/watcher đang thuộc bản cài KHÁC còn sống thì giữ nguyên
	// (trước đây `genh uninstall --install-dir <bản phụ>` xoá luôn lịch đêm và watcher
	// của bản chính).
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	deps := hostInfoEnvFn().Base
	deps.InstallDir = env.InstallDir
	if msg := autoupdate.DisableRequestWatcher(ctx, deps); msg != "" {
		fmt.Println(msg)
	}
	if msg, _ := autoupdate.Disable(ctx, deps); strings.HasPrefix(msg, "Giữ nguyên") {
		fmt.Println(msg)
	}
	return 0
}

func runExport(args []string) int {
	fs, port, installDir := opsFlagSet("export")
	to := fs.String("to", "", "đường dẫn tệp .ghbundle sẽ ghi ra (bắt buộc)")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if *to == "" {
		_, _ = fmt.Fprintln(os.Stderr, "genh: cách dùng: genh export --to <file>")
		return 2
	}
	env, ok := resolveOpsEnv(*port, *installDir)
	if !ok {
		return 1
	}
	ctx, stop := signalContext()
	defer stop()
	if err := ops.RunExport(ctx, env, *to, ops.ExportDeps{}, os.Stdout); err != nil {
		reportOpErr(err)
		return 1
	}
	return 0
}

func runImport(args []string) int {
	fs, port, installDir := opsFlagSet("import")
	yes := fs.Bool("yes", false, "bỏ qua hỏi xác nhận GHI ĐÈ dữ liệu hiện tại")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if fs.NArg() != 1 {
		_, _ = fmt.Fprintln(os.Stderr, "genh: cách dùng: genh import <file> [--yes]")
		return 2
	}
	env, ok := resolveOpsEnv(*port, *installDir)
	if !ok {
		return 1
	}
	ctx, stop := signalContext()
	defer stop()
	// Khoá loại trừ (v0.1.37): import ghi đè CSDL — không chạy chồng update/restore.
	lock, code, ok := acquireOpLock(ctx, env.InstallDir, lockManual, nil, os.Stdout, os.Stderr)
	if !ok {
		return code
	}
	defer lock.Release()
	stopBeat := hostlink.StartHeartbeat(env.InstallDir, "import")
	defer stopBeat()
	opts := ops.ImportOptions{AutoApprove: *yes}
	if err := ops.RunImport(ctx, env, fs.Arg(0), opts, ops.ImportDeps{}, os.Stdin, os.Stdout); err != nil {
		reportOpErr(err)
		return 1
	}
	return 0
}

func runInstall(args []string) int {
	fs := flag.NewFlagSet("install", flag.ContinueOnError)
	port := fs.Int("port", machine.DefaultPort, "cổng HTTPS cho proxy")
	installDir := fs.String("install-dir", "", "thư mục cài đặt (mặc định ~/.gen-harness)")
	yes := fs.Bool("yes", false, "đồng ý trước cho các thao tác hệ thống rộng (sudo cho Docker rootless, UAC cho WSL2, tin cậy CA nội bộ)")
	force := fs.Bool("force", false, "bỏ qua kiểm tra máy đã cài — CHẠY LẠI cả 8 bước dù đã có bản cài hoàn chỉnh (dùng khi lần cài trước hỏng hẳn, cần dựng lại từ đầu; bình thường hãy dùng `genh update`)")
	noAutoUpdate := fs.Bool("no-auto-update", false, "không tự bật lịch cập nhật hằng đêm (mặc định BẬT sau khi cài xong — xem `genh auto-update`)")
	if err := fs.Parse(args); err != nil {
		return 2
	}

	dir := *installDir
	if dir == "" {
		d, err := config.DefaultRoot()
		if err != nil {
			_, _ = fmt.Fprintf(os.Stderr, "genh: không xác định được thư mục cài đặt: %v\n", err)
			return 1
		}
		dir = d
	}

	// SỬA LỖI (docs/reports/HANDOFF-v0.1.1.md mục "Lỗi cần sửa" #2 của
	// v0.1.3): `genh install` trên một máy ĐÃ CÀI HOÀN CHỈNH sẽ chạy lại cả 8
	// Bước, dựng lại container VÀ BỎ QUA backup/di trú dữ liệu mà chỉ `genh
	// update` mới có — dừng SỚM ở đây, trước khi đụng gì, trừ khi Owner đã tự
	// xác nhận bằng --force.
	if !*force && install.DetectExistingInstall(context.Background(), dockercli.ExecRunner{}, dir) {
		se := &install.StepError{
			Code: install.ErrCodeAlreadyInstalled,
			What: "Gen-Harness ĐÃ CÀI XONG tại " + dir + " — DỪNG LẠI, chưa đụng gì",
			Why:  "phát hiện secrets.json và container \"api\" đã tồn tại — chạy lại `genh install` sẽ dựng lại container, BỎ QUA backup tự động + di trú dữ liệu mà chỉ `genh update` mới làm.",
			Next: "Chạy `genh update` để nâng cấp an toàn (có backup + rollback tự động). Nếu lần cài trước thật sự CHƯA XONG (Owner biết rõ), chạy lại kèm `genh install --force`.",
		}
		fmt.Println()
		fmt.Printf("Lỗi %s: %s\n", se.Code, se.What)
		fmt.Printf("  vì sao: %s\n", se.Why)
		fmt.Printf("  làm gì tiếp: %s\n", se.Next)
		return 1
	}

	env := &install.Env{InstallDir: dir, Port: *port, AutoApprove: *yes}
	runner := install.NewRunner(env, install.Registry())

	ctx, stop := signalContext()
	defer stop()

	var runErr error
	if tui.IsTerminal(os.Stdout) {
		runErr = runInteractive(ctx, runner, env)
	} else {
		runErr = runLineMode(ctx, runner, env)
	}

	printSummary(runner.Snapshot(), runErr)

	if runErr != nil {
		return 1
	}

	// Bước "Hoàn tất" của người cài đặt không rành code THẬT SỰ chỉ hoàn tất
	// khi máy KHÔNG cần họ làm gì thêm để có bản mới sau này — nên bật lịch
	// tự cập nhật hằng đêm ngay tại đây, mặc định, trừ khi có --no-auto-update.
	// Bật lỗi CHỈ cảnh báo (fmt.Fprintln ra stderr), KHÔNG BAO GIỜ làm hỏng
	// một lần cài đặt vừa xong thành công — Owner vẫn dùng được Gen-Harness
	// bình thường, chỉ là phải tự chạy `genh update` tay hoặc `genh
	// auto-update enable` lại sau.
	//
	// v0.1.53 (F-93): --no-auto-update ghi dấu "Sếp đã chủ động tắt" (genh không tự bật
	// lại lịch đêm); cài không cờ xoá dấu đó rồi bật.
	applyAutoUpdateChoice(dir, *port, *noAutoUpdate)
	_ = ops.ClearOwnerPause(dir)
	publishHostInfo(dir, *port)

	return 0
}

// applyAutoUpdateChoice áp lựa chọn lịch đêm của `genh install`: --no-auto-update ghi dấu
// "Sếp đã chủ động tắt" (genh không tự lành lại) VÀ tắt lịch đêm đang có của chính bản
// cài này (`install --force --no-auto-update` đè lên máy đã bật lịch — trước đây dấu nói
// "tắt" mà lịch vẫn chạy 03:00); không cờ xoá dấu đó rồi bật lịch.
func applyAutoUpdateChoice(dir string, port int, noAutoUpdate bool) {
	if noAutoUpdate {
		if err := ops.SetAutoUpdateOptOut(dir, true, time.Now()); err != nil {
			fmt.Fprintf(os.Stderr, "genh: cảnh báo — không ghi được %s: %v (lần cập nhật sau có thể tự bật lại lịch đêm)\n", ops.AutoUpdateOptOutPath(dir), err)
		}
		disableAutoUpdateAfterInstall(dir, port)
		return
	}
	if err := ops.SetAutoUpdateOptOut(dir, false, time.Now()); err != nil {
		fmt.Fprintf(os.Stderr, "genh: cảnh báo — không xoá được %s: %v\n", ops.AutoUpdateOptOutPath(dir), err)
	}
	enableAutoUpdateAfterInstall(dir, port)
}

// enableAutoUpdateAfterInstall bật internal/autoupdate ngay sau khi cài xong
// — xem ghi chú ở nơi gọi. In đúng MỘT dòng rõ ràng khi thành công (yêu cầu
// của phiên v0.1.5), hoặc một dòng cảnh báo ngắn khi thất bại. Lịch đêm mang
// --install-dir/--port/GENH_COMPOSE_FILE của bản cài này (v0.1.53, F-98); đang thuộc
// bản cài KHÁC còn sống thì không đổi lịch, chỉ cảnh báo một dòng.
func enableAutoUpdateAfterInstall(installDir string, port int) {
	he := hostInfoEnvFn()
	execPath := he.Base.GenhPath
	if execPath == "" {
		p, err := os.Executable()
		if err != nil {
			fmt.Fprintf(os.Stderr, "genh: không bật được tự cập nhật hằng đêm (không xác định được đường dẫn genh): %v — chạy tay `genh auto-update enable` sau.\n", err)
			return
		}
		execPath, _ = filepath.Abs(p)
	}
	logFile := filepath.Join(config.New(installDir).LogsDir(), "auto-update.log")
	if err := os.MkdirAll(filepath.Dir(logFile), 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "genh: không bật được tự cập nhật hằng đêm (không tạo được thư mục log): %v — chạy tay `genh auto-update enable` sau.\n", err)
		return
	}

	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	deps := he.Base
	deps.GenhPath, deps.LogFile = execPath, logFile
	deps.InstallDir, deps.Nightly = installDir, nightlyJobFor(installDir, port)
	res, err := autoupdate.Enable(ctx, deps)
	if err != nil {
		if other, owned := autoupdate.OwnerOf(err); owned {
			fmt.Fprintln(os.Stderr, "genh: "+ownedByOtherLine(other, installDir))
			return
		}
		fmt.Fprintf(os.Stderr, "genh: không bật được tự cập nhật hằng đêm tự động (%v) — chạy tay `genh auto-update enable`, hoặc bỏ qua nếu không cần.\n", err)
		return
	}
	fmt.Println(res.Msg)
	printLingerWarning(os.Stdout, res.Warning, tui.IsTerminal(os.Stdout))
}

// disableAutoUpdateAfterInstall tắt lịch đêm ĐANG CÓ của bản cài này cho `genh install
// --no-auto-update` (v0.1.53). Chưa có lịch nào ⇒ không gọi lệnh ghi nào (cài mới không
// đụng crontab/systemd); lịch thuộc bản cài KHÁC còn sống ⇒ không đụng (Disable cũng tự
// giữ nguyên). Lỗi/tắt hụt chỉ cảnh báo — không làm hỏng lần cài vừa xong.
func disableAutoUpdateAfterInstall(installDir string, port int) {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	deps := hostInfoEnvFn().Base
	deps.LogFile = filepath.Join(config.New(installDir).LogsDir(), "auto-update.log")
	deps.InstallDir, deps.Nightly = installDir, nightlyJobFor(installDir, port)
	st, err := autoupdate.GetStatus(ctx, deps)
	if err != nil || !st.UnitPresent || st.OwnedByOther {
		return
	}
	msg, err := autoupdate.Disable(ctx, deps)
	if err != nil {
		fmt.Fprintf(os.Stderr, "genh: cảnh báo — không tắt được lịch tự cập nhật đêm đang có (%v) — chạy tay `genh auto-update disable`.\n", err)
		return
	}
	if st, err := autoupdate.GetStatus(ctx, deps); err == nil && st.Enabled && !st.OwnedByOther {
		fmt.Fprintln(os.Stderr, disableStillOnLine)
		return
	}
	fmt.Println(msg)
}

// hostInfoEnv là phần phụ thuộc của publishHostInfo: Base mang Runner/HomeDir/GOOS/
// LookPath (và GenhPath) cho systemctl/crontab/loginctl — rỗng = thật; Offsite/Watchdog
// tiêm lịch giả. Test tiêm qua hostInfoEnvFn.
type hostInfoEnv struct {
	Base     autoupdate.Deps
	Offsite  ops.OffsiteDeps
	Watchdog ops.WatchdogScheduler
}

// publishHostInfo cài (idempotent) watcher nhận yêu cầu "Cập nhật ngay" từ
// Console rồi ghi phiên bản + cơ chế vào hộp thư (run/genh.json) cho Console
// đọc. Lỗi chỉ làm nút trong Console hiện lệnh tay thay vì bấm được — không
// bao giờ làm hỏng install/update vừa xong.
func publishHostInfo(installDir string, port int) {
	publishHostInfoWith(installDir, port, hostInfoEnvFn())
}

func publishHostInfoWith(installDir string, port int, he hostInfoEnv) {
	updater := ""
	var autoUpdate *bool
	execPath := he.Base.GenhPath
	if execPath == "" {
		if p, err := os.Executable(); err == nil {
			execPath, _ = filepath.Abs(p)
		}
	}
	if execPath != "" {
		logFile := filepath.Join(config.New(installDir).LogsDir(), "auto-update.log")
		_ = os.MkdirAll(filepath.Dir(logFile), 0o755)
		ctx, cancel := context.WithTimeout(context.Background(), 40*time.Second)
		defer cancel()
		rp := autoupdate.RequestPaths{InstallDir: installDir, RequestDir: hostlink.RequestDirPath(installDir), RequestFile: hostlink.RequestPath(installDir),
			RestoreFile: hostlink.RestoreRequestPath(installDir), OffsiteFile: hostlink.OffsiteRequestPath(installDir),
			DoctorFile: hostlink.DoctorRequestPath(installDir), WatchdogFile: hostlink.WatchdogRequestPath(installDir)}
		if port != machine.DefaultPort {
			rp.Port = port
		}
		if v := os.Getenv(compose.EnvOverrideVar); v != "" {
			rp.Env = append(rp.Env, compose.EnvOverrideVar+"="+v)
		}
		deps := he.Base
		deps.GenhPath, deps.LogFile = execPath, logFile
		deps.InstallDir, deps.Nightly = installDir, nightlyJobFor(installDir, port)
		warnedOwner := false
		warnOwner := func(err error) bool {
			other, owned := autoupdate.OwnerOf(err)
			if owned && !warnedOwner {
				warnedOwner = true
				fmt.Fprintln(os.Stderr, "genh: "+ownedByOtherLine(other, installDir))
			}
			return owned
		}
		optedOut := ops.AutoUpdateOptedOut(installDir)
		// v0.1.37: unit lịch đêm chỉ được ghi lúc install/enable — máy cài từ bản
		// cũ cần ghi lại để có KillMode=mixed/TimeoutStopSec (không bật/tắt gì). Từ
		// v0.1.53 cả ExecStart --install-dir/--port + Environment=GEN_HARNESS_HOME.
		if rep, err := autoupdate.RefreshUnitsReport(ctx, deps); err != nil {
			fmt.Fprintf(os.Stderr, "genh: cảnh báo — không làm mới được unit lịch tự cập nhật: %v\n", err)
		} else if rep.Changed {
			fmt.Println("genh: đã làm mới unit lịch tự cập nhật (" + strings.Join(rep.What, ", ") + ") — các dòng khác giữ nguyên.")
		}
		// v0.1.53 (F-93): lịch đêm bị tắt/mất thì TỰ LÀNH (bật lại) — trừ khi Sếp đã chủ
		// động tắt. TRƯỚC GetStatus bên dưới để genh.json phản ánh trạng thái sau khi lành.
		healNightly := func() {
			if optedOut {
				return
			}
			healed, msg, err := autoupdate.EnsureNightly(ctx, deps)
			switch {
			case err != nil:
				if !warnOwner(err) {
					fmt.Fprintf(os.Stderr, "genh: cảnh báo — không tự bật lại được lịch tự cập nhật đêm: %v (thử: genh auto-update enable)\n", err)
				}
			case healed:
				// Dòng đầu là thông báo tự lành; dòng sau (nếu có) là cảnh báo linger — nổi bật khi
				// có TTY, chữ thường khi vào log (lịch đêm).
				lines := strings.SplitN(msg, "\n", 2)
				fmt.Println(lines[0])
				if len(lines) == 2 {
					printLingerWarning(os.Stdout, lines[1], tui.IsTerminal(os.Stdout))
				}
			}
		}
		healNightly()
		if u, err := autoupdate.EnsureRequestWatcher(ctx, deps, rp); err == nil {
			updater = u
		} else {
			warnOwner(err)
		}
		// v0.1.40: bản sao ngoài máy đang bật → ghi lại lịch tuần (idempotent) cho
		// khớp bản genh này (máy cập nhật từ bản cũ). Chưa bật thì không làm gì.
		if _, err := ops.RefreshOffsiteSchedule(ctx, &ops.Env{InstallDir: installDir, Port: port}, he.Offsite); err != nil {
			if !warnOwner(err) {
				fmt.Fprintf(os.Stderr, "genh: cảnh báo — không làm mới được lịch sao lưu ra ổ ngoài: %v\n", err)
			}
		}
		// v0.1.44 (F-6b): lịch trực canh máy chủ mỗi 12 phút — idempotent, KHÔNG phụ
		// thuộc --no-auto-update (trực canh không đổi gì trên máy ngoài tự khởi động
		// lại dịch vụ đã chết). Lỗi chỉ cảnh báo.
		enableWatchdogScheduleWith(ctx, installDir, he.watchdog(installDir, port), warnOwner)
		// Các lệnh daemon-reload/enable ở trên (nhất là khi chạy TỪ BÊN TRONG
		// gen-harness-update.service lúc lịch đêm) có thể làm timer mất lịch: kiểm lại
		// sau cùng (idempotent — khoẻ thì không ghi gì).
		healNightly()
		// v0.1.33: Console chỉ hứa "Tự cài đêm …" khi lịch đêm thật sự đang bật.
		if st, err := autoupdate.GetStatus(ctx, deps); err == nil {
			enabled := st.Enabled && !st.OwnedByOther
			autoUpdate = &enabled
			// v0.1.53 (F-99): run/nightly-status.json — Console thấy lịch đêm có thật sự
			// bật/chạy không (kể cả khi im lặng nhiều ngày).
			wh := autoupdate.RequestWatcherHealth(ctx, deps)
			if !wh.OK() { // v0.1.54: install/update xong mà người gác vẫn lỗi ⇒ nói một dòng (đã tự chữa/dự phòng)
				fmt.Fprintln(os.Stderr, "genh: "+wh.Line())
			}
			_ = ops.SaveNightlyStatus(installDir, st, optedOut, autoupdate.RequestWatcherState(ctx, deps), wh)
		}
	}
	_ = hostlink.WriteInfo(installDir, version, updater, autoUpdate)

	// v0.1.37 (F-73): làm mới run/autostart-status.json để Console nhắc Owner
	// khi Docker/linger không tự chạy lại sau khởi động — lịch đêm cũng gọi tới
	// đây nên Owner không phải chạy `genh status`. Lỗi bỏ qua.
	refreshAutostartStatus(installDir)
}

// watchdog trả lịch trực canh: đã tiêm thì dùng, không thì dựng thật (có Runner/HomeDir
// của Base để test không chạm máy thật).
func (he hostInfoEnv) watchdog(installDir string, port int) ops.WatchdogScheduler {
	if he.Watchdog != nil {
		return he.Watchdog
	}
	env := &ops.Env{InstallDir: installDir, Port: port}
	if he.Base.Runner != nil || he.Base.HomeDir != "" || he.Base.GOOS != "" || he.Base.LookPath != nil {
		return ops.NewWatchdogSchedulerWith(env, he.Base)
	}
	return ops.NewWatchdogScheduler(env)
}

// enableWatchdogSchedule bật (idempotent) lịch trực canh và ghi cơ chế vào
// run/watchdog-status.json ("schedule") — in một dòng khi lần đầu bật. Owner đã
// `genh watchdog disable` (config/watchdog-disabled.json) ⇒ không làm gì.
func enableWatchdogSchedule(ctx context.Context, installDir string, port int) {
	enableWatchdogScheduleWith(ctx, installDir, ops.NewWatchdogScheduler(&ops.Env{InstallDir: installDir, Port: port}), nil)
}

// enableWatchdogScheduleWith như enableWatchdogSchedule với lịch tiêm sẵn; warnOwner
// (nil được) in cảnh báo "bản cài khác đang giữ lịch" một lần và báo lỗi đó đã xử lý.
func enableWatchdogScheduleWith(ctx context.Context, installDir string, sched ops.WatchdogScheduler, warnOwner func(error) bool) {
	if ops.WatchdogOptedOut(installDir) { // Owner đã chủ động tắt — không ghi đè lựa chọn đó
		return
	}
	msg, mech, err := sched.Enable(ctx)
	if err != nil {
		if warnOwner != nil && warnOwner(err) {
			return
		}
		if other, owned := autoupdate.OwnerOf(err); owned {
			fmt.Fprintln(os.Stderr, "genh: "+ownedByOtherLine(other, installDir))
			return
		}
		fmt.Fprintf(os.Stderr, "genh: cảnh báo — không bật được trực canh máy chủ: %v (thử lại: genh watchdog enable)\n", err)
		return
	}
	prev, perr := hostlink.ReadWatchdogStatus(installDir)
	if perr != nil || prev.Schedule != mech {
		_ = hostlink.SetWatchdogSchedule(installDir, mech)
		fmt.Println(msg)
	}
}

// programObserver chuyển install.Snapshot thành tui.SnapshotMsg gửi vào
// vòng lặp Bubble Tea — Runner không biết gì về TUI, chỉ biết Reporter.
type programObserver struct{ p *tea.Program }

func (o programObserver) Observe(s install.Snapshot) { o.p.Send(tui.SnapshotMsg(s)) }

func runInteractive(ctx context.Context, runner *install.Runner, env *install.Env) error {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()

	renderer := lipgloss.NewRenderer(os.Stdout)
	styles := tui.NewStyles(renderer, tui.ColorEnabled(renderer))
	model := tui.NewModel(styles, version, env.InstallDir)

	p := tea.NewProgram(model, tea.WithAltScreen())

	errCh := make(chan error, 1)
	go func() {
		runErr := runner.Run(ctx, programObserver{p: p})
		errCh <- runErr
		if runErr == nil {
			// Cài đặt xong thật: chuyển Model sang màn "Hoàn tất" (mockup cuối
			// docs/handoff/05-installer.md) thay vì thoát ngay — Owner tự bấm q
			// khi đã xem/copy xong URL và mã thiết lập.
			p.Send(tui.FinishMsg(buildFinishInfo(env, runner)))
		} else {
			p.Send(tui.DoneMsg{Err: runErr})
		}
	}()

	finalModel, progErr := p.Run()
	if fm, ok := finalModel.(tui.Model); ok && fm.Cancelled {
		// Owner bấm q: huỷ an toàn — dừng ctx để Step đang chạy có cơ hội
		// dọn dẹp (dừng container đã tạo, giữ image đã tải), rồi chờ nó trả
		// về thay vì bỏ mặc goroutine.
		cancel()
	}
	runErr := <-errCh

	if progErr != nil {
		return progErr
	}
	return runErr
}

func runLineMode(ctx context.Context, runner *install.Runner, env *install.Env) error {
	lr := tui.NewLineRenderer(os.Stdout)
	err := runner.Run(ctx, lr)
	if err == nil {
		lr.Finish(buildFinishInfo(env, runner))
		return nil
	}
	if se, ok := err.(*install.StepError); ok {
		lr.FinishError(se)
	}
	return err
}

// buildFinishInfo dựng tui.FinishInfo từ dữ liệu THẬT sau khi runner.Run()
// trả về thành công: thời lượng từ chính Runner, URL/mã thiết lập từ bí mật
// Bước 4 (env.Secrets, còn sống vì env là cùng một *install.Env đã truyền
// vào NewRunner), và BrowserOpened từ kết quả Bước 8 (đã tự mở trình duyệt
// đúng một lần — xem finalizeStep trong internal/install).
func buildFinishInfo(env *install.Env, runner *install.Runner) tui.FinishInfo {
	info := tui.FinishInfo{Duration: runner.Snapshot().Elapsed}

	res, ok := env.Secrets.(secretgen.Result)
	if !ok {
		return info
	}

	info.SetupURL = install.SetupURL(env, res.Bundle.SetupToken)
	info.SetupCode = res.Bundle.SetupToken
	if !res.Bundle.CreatedAt.IsZero() {
		info.CodeExpiresIn = time.Until(res.Bundle.CreatedAt.Add(24 * time.Hour))
	}
	info.BrowserOpened = env.BrowserOpened
	return info
}

func printSummary(snap install.Snapshot, runErr error) {
	fmt.Println()
	fmt.Printf("Tổng tiến độ: %.0f%%\n", snap.OverallPct)

	for _, s := range snap.Steps {
		fmt.Printf("  %-8s %s", statusLabel(s.Status), s.Name)
		if s.Detail != "" {
			fmt.Printf(" — %s", s.Detail)
		}
		fmt.Println()
	}

	if runErr != nil {
		fmt.Println()
		if se, ok := runErr.(*install.StepError); ok {
			fmt.Printf("Lỗi %s: %s\n", se.Code, se.What)
			if se.Why != "" {
				fmt.Printf("  vì sao: %s\n", se.Why)
			}
			if se.Next != "" {
				fmt.Printf("  làm gì tiếp: %s\n", se.Next)
			}
		} else {
			fmt.Printf("Lỗi: %v\n", runErr)
		}
	}
}

func statusLabel(s install.Status) string {
	switch s {
	case install.StatusOK:
		return "[OK]"
	case install.StatusSkipped:
		return "[=]"
	case install.StatusWarn:
		return "[!]"
	case install.StatusError:
		return "[X]"
	case install.StatusRunning:
		return "[..]"
	default:
		return "[ ]"
	}
}
