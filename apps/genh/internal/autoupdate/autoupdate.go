// Package autoupdate cấu hình `genh update --yes --quiet` chạy TỰ ĐỘNG mỗi
// đêm — mục tiêu (owner không rành code): sau khi `genh install` xong, KHÔNG
// phải làm gì để có bản mới; `genh auto-update enable|disable|status` chỉ
// dành cho ai muốn tự tắt/kiểm tra.
//
// Cơ chế theo hệ điều hành:
//   - Linux: systemd --user timer chạy hằng ngày (~03:00 giờ máy), fallback
//     sang crontab người dùng nếu máy không có systemd --user hoạt động
//     (container tối giản, WSL không có systemd…).
//   - macOS: LaunchAgent (~/Library/LaunchAgents).
//   - Windows: Task Scheduler (schtasks).
//
// v0.1.53: tên unit/Label/marker là CHUNG cho mọi bản cài của người dùng nên mỗi lịch
// ghi rõ bản cài chủ (owner.go) và bản cài khác không gỡ/ghi đè lịch của bản còn sống;
// lịch đêm bị tắt/mất thì tự lành (nightly.go) trừ khi Sếp đã chủ động tắt.
//
// Phần SINH NỘI DUNG tệp/lệnh (unit systemd, dòng crontab, plist,
// args schtasks) là hàm THUẦN trong content.go — test được trên MỌI hệ điều
// hành (không cần build tag), không gọi tiến trình con nào. Phần GỌI THẬT
// systemctl/launchctl/schtasks/crontab đi qua Runner (tiêm giả khi test),
// và tự rẽ nhánh theo runtime.GOOS — KHÔNG dùng build tag theo tệp, để một
// máy Linux (môi trường viết/CI của repo này) vẫn biên dịch VÀ chạy được test
// của cả 3 nhánh macOS/Windows/Linux (chỉ là chúng không bao giờ thật sự gọi
// launchctl/schtasks trên máy Linux — Runner giả đứng ra nhận lệnh thay).
package autoupdate

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"time"
)

// TaskName là tên định danh dùng ở MỌI hệ điều hành (tên service/timer
// systemd, Label LaunchAgent, tên Task Scheduler) — một tên duy nhất để dễ
// tìm/tắt tay nếu cần.
const TaskName = "gen-harness-update"

// Runner thực thi một lệnh hệ thống (systemctl/launchctl/schtasks/crontab)
// — interface RIÊNG của gói này (không dùng chung dockercli.Runner: đây là
// lệnh hệ điều hành, không phải docker/docker compose, khác Cmd hoàn toàn).
type Runner interface {
	// Output chạy name+args, trả về stdout+stderr gộp (để thông báo lỗi có
	// ngữ cảnh) và lỗi nếu tiến trình thoát khác 0 hoặc không khởi chạy
	// được (ví dụ không có lệnh đó trên PATH).
	Output(ctx context.Context, name string, args []string) ([]byte, error)
}

// ExecRunner là Runner thật, gọi os/exec.
type ExecRunner struct{}

func (ExecRunner) Output(ctx context.Context, name string, args []string) ([]byte, error) {
	cmd := exec.CommandContext(ctx, name, args...)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return out, fmt.Errorf("%s %v: %w — %s", name, args, err, trimTail(string(out), 2000))
	}
	return out, nil
}

func trimTail(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return "…" + s[len(s)-n:]
}

// Deps cho phép tiêm Runner giả + đường dẫn genh/thư mục log/thư mục home
// khi test — nil ở mọi trường dùng giá trị thật.
type Deps struct {
	Runner Runner
	// GenhPath là đường dẫn tuyệt đối tới binary genh cần chạy mỗi đêm —
	// bắt buộc phải khác rỗng (caller — cmd/genh — luôn tự biết os.Executable()).
	GenhPath string
	// LogFile là tệp genh ghi log của lần chạy tự động vào (thường
	// config.Paths.LogsDir()/auto-update.log).
	LogFile string
	// HomeDir cho phép tiêm $HOME giả khi test (nơi ghi unit systemd/plist)
	// — "" dùng os.UserHomeDir().
	HomeDir string
	// GOOS cho phép tiêm giả khi test — "" dùng runtime.GOOS.
	GOOS string
	// LookPath cho phép tiêm giả khi test (tránh phụ thuộc PATH thật của
	// máy chạy test) — nil dùng exec.LookPath thật. Chỉ enableLinux dùng,
	// để kiểm máy có binary "systemctl" hay không TRƯỚC KHI thử gọi nó (một
	// máy Linux có thể sẵn PATH có systemctl nhưng KHÔNG có session
	// --user hoạt động — đó là lý do daemon-reload là phép thử thật sự,
	// LookPath chỉ là bước lọc nhanh trước).
	LookPath func(string) (string, error)

	// InstallDir (v0.1.53, F-98) là bản cài ĐANG thao tác. Lịch/unit là CHUNG cho
	// mọi bản cài của người dùng: khác rỗng thì Enable*/Disable* kiểm lịch đã cài
	// có thuộc một bản cài KHÁC còn sống không (owner.go) — có thì không gỡ,
	// không ghi đè. Rỗng = không bảo vệ (hành vi cũ).
	InstallDir string
	// Nightly là những gì lịch tự cập nhật đêm cần (cổng, bản cài, biến môi
	// trường) — xem NightlyJob. Nightly.InstallDir rỗng thì lấy InstallDir.
	Nightly NightlyJob
	// UID cho `loginctl show-user` — "" dùng os.Getuid().
	UID string
	// Now cho test (tính "lần kế tiếp") — nil dùng time.Now.
	Now func() time.Time
	// ReadFile cho phép tiêm giả khi test việc đọc /proc/sys/fs/inotify/max_user_instances
	// (chẩn đoán người gác yêu cầu — watcher_heal.go) — nil dùng os.ReadFile.
	ReadFile func(string) ([]byte, error)
	// InotifyHeadroom cho phép tiêm giả việc "còn đủ n inotify instance cho .path dựng n watch không"
	// (watcher_heal.go) — nil dùng phép thử thật (mở thử n instance rồi đóng ngay).
	InotifyHeadroom func(n int) bool
	// Location để đọc mốc giờ systemd in theo múi giờ máy (systemd < 247 không
	// có --timestamp=unix) — nil dùng time.Local.
	Location *time.Location
}

func (d Deps) job() NightlyJob {
	j := d.Nightly
	if j.InstallDir == "" {
		j.InstallDir = d.InstallDir
	}
	return j
}

func (d Deps) now() time.Time {
	if d.Now != nil {
		return d.Now()
	}
	return time.Now()
}

func (d Deps) location() *time.Location {
	if d.Location != nil {
		return d.Location
	}
	return time.Local
}

func (d Deps) uid() string {
	if d.UID != "" {
		return d.UID
	}
	return strconv.Itoa(os.Getuid())
}

func (d Deps) runner() Runner {
	if d.Runner != nil {
		return d.Runner
	}
	return ExecRunner{}
}

func (d Deps) goos() string {
	if d.GOOS != "" {
		return d.GOOS
	}
	return runtime.GOOS
}

func (d Deps) lookPath() func(string) (string, error) {
	if d.LookPath != nil {
		return d.LookPath
	}
	return exec.LookPath
}

func (d Deps) homeDir() (string, error) {
	if d.HomeDir != "" {
		return d.HomeDir, nil
	}
	return os.UserHomeDir()
}

// Status là kết quả của GetStatus(): trạng thái TRUNG THỰC của lịch tự cập nhật
// đêm (v0.1.53, F-94) — không chỉ "BẬT/TẮT" mà còn đang chạy hay không, lần chạy
// gần nhất/kế tiếp, linger.
type Status struct {
	// Enabled = lịch thật sự sẽ chạy: systemd UnitFileState=enabled VÀ
	// ActiveState=active; cron/launchd/schtasks: có dòng/đã nạp/chưa Disabled.
	Enabled bool
	// Mechanism: systemd | cron | launchd | schtasks | "" (không có lịch nào).
	Mechanism string
	// UnitPresent: có unit/dòng lịch nào đã cài (systemd: LoadState≠not-found).
	UnitPresent bool
	// UnitFileState (systemd): enabled | disabled | masked | static | linked… ("" nếu không rõ).
	UnitFileState string
	// Active (systemd): active | inactive | failed | … ("" nếu không áp dụng/không rõ).
	Active string
	// LastRun/NextRun: zero nếu không rõ.
	LastRun, NextRun time.Time
	// Linger: yes | no | unknown | not_applicable.
	Linger string
	// Owner là bản cài chủ của lịch (nếu biết); OwnedByOther: bản cài chủ khác
	// Deps.InstallDir và còn sống — lịch này KHÔNG phải của bản đang hỏi.
	Owner        string
	OwnedByOther bool
	// Detail mô tả cơ chế/trạng thái bằng tiếng Việt cho `genh auto-update status`.
	Detail string
}

// EnableResult là kết quả Enable.
type EnableResult struct {
	// Msg là dòng mô tả NGẮN ("Đã bật tự cập nhật hằng đêm lúc ~03:00 …").
	Msg string
	// Mechanism: systemd | cron | launchd | schtasks.
	Mechanism string
	// Linger (chỉ systemd --user): yes | no | unknown; "" ở cơ chế khác.
	Linger string
	// Warning: cảnh báo linger (LingerWarning) khi linger đang tắt/không xác nhận
	// được — "" nếu không có. cmd in nổi bật khi stdout là TTY.
	Warning string
}

// Text là Msg + (nếu có) cảnh báo ở dòng kế.
func (r EnableResult) Text() string {
	if r.Warning == "" {
		return r.Msg
	}
	return r.Msg + "\n" + r.Warning
}

// LingerWarning là cảnh báo khi linger (systemd --user chạy cả khi không ai
// đăng nhập) đang tắt. Lệnh sửa KHÔNG có dấu chấm ngay sau.
const LingerWarning = "CẢNH BÁO: linger đang TẮT — lịch tự cập nhật đêm và nút Cập nhật ngay chỉ chạy khi có người đăng nhập máy. Chạy một lần: sudo loginctl enable-linger $USER"

// Enable bật lịch tự động theo đúng hệ điều hành hiện tại — kết quả có một dòng
// mô tả NGẮN cho `genh install`/`genh auto-update enable` in ra (đúng yêu
// cầu: 1 dòng rõ ràng "Đã bật tự cập nhật hằng đêm…"). Lịch đang thuộc bản cài
// khác còn sống (Deps.InstallDir) ⇒ lỗi *OwnedByOtherError, không ghi đè.
func Enable(ctx context.Context, deps Deps) (EnableResult, error) {
	if deps.GenhPath == "" {
		return EnableResult{}, fmt.Errorf("thiếu đường dẫn binary genh")
	}
	switch deps.goos() {
	case "linux":
		return enableLinux(ctx, deps)
	case "darwin":
		return enableDarwin(ctx, deps)
	case "windows":
		return enableWindows(ctx, deps)
	default:
		return EnableResult{}, fmt.Errorf("chưa hỗ trợ tự cập nhật theo lịch trên %s", deps.goos())
	}
}

// Disable tắt lịch tự động — KHÔNG lỗi nếu vốn chưa bật (idempotent). Lịch
// thuộc bản cài khác còn sống (Deps.InstallDir) thì KHÔNG gỡ, trả câu "Giữ
// nguyên lịch … của bản cài <dir> (không phải bản đang gỡ)."
func Disable(ctx context.Context, deps Deps) (string, error) {
	switch deps.goos() {
	case "linux":
		return disableLinux(ctx, deps)
	case "darwin":
		return disableDarwin(ctx, deps)
	case "windows":
		return disableWindows(ctx, deps)
	default:
		return "", fmt.Errorf("chưa hỗ trợ tự cập nhật theo lịch trên %s", deps.goos())
	}
}

// GetStatus báo lịch tự động đang bật hay tắt, và bằng cơ chế nào.
func GetStatus(ctx context.Context, deps Deps) (Status, error) {
	switch deps.goos() {
	case "linux":
		return statusLinux(ctx, deps)
	case "darwin":
		return statusDarwin(ctx, deps)
	case "windows":
		return statusWindows(ctx, deps)
	default:
		return Status{}, fmt.Errorf("chưa hỗ trợ tự cập nhật theo lịch trên %s", deps.goos())
	}
}

// RefreshReport là kết quả RefreshUnitsReport: Changed = đã ghi lại unit; What =
// những gì đã thêm/đổi (cho dòng thông báo).
type RefreshReport struct {
	Changed bool
	What    []string
}

// RefreshUnits (v0.1.37) cập nhật unit lịch đêm ĐÃ CÀI cho khớp bản genh này
// (unit chỉ được ghi lúc install/enable — máy cài từ bản cũ sẽ thiếu
// KillMode=mixed/TimeoutStopSec, và từ v0.1.53 cả --install-dir/--port/
// Environment=GEN_HARNESS_HOME). Chỉ Linux (systemd --user) làm việc; hệ điều
// hành khác trả (false, nil). Không bật/tắt lịch. Trả true nếu đã ghi lại.
func RefreshUnits(ctx context.Context, deps Deps) (bool, error) {
	r, err := RefreshUnitsReport(ctx, deps)
	return r.Changed, err
}

// RefreshUnitsReport như RefreshUnits nhưng nói rõ đã đổi những gì.
func RefreshUnitsReport(ctx context.Context, deps Deps) (RefreshReport, error) {
	if deps.goos() != "linux" || deps.GenhPath == "" {
		return RefreshReport{}, nil
	}
	return refreshUnitsLinux(ctx, deps)
}

// systemdUserDir trả về ~/.config/systemd/user (KHÔNG tạo — caller tự
// MkdirAll khi cần ghi).
func systemdUserDir(home string) string {
	return filepath.Join(home, ".config", "systemd", "user")
}

func serviceUnitPath(home string) string {
	return filepath.Join(systemdUserDir(home), TaskName+".service")
}
func timerUnitPath(home string) string { return filepath.Join(systemdUserDir(home), TaskName+".timer") }

func launchAgentDir(home string) string {
	return filepath.Join(home, "Library", "LaunchAgents")
}

func launchAgentLabel() string { return "com.gen-harness.update" }

func launchAgentPath(home string) string {
	return filepath.Join(launchAgentDir(home), launchAgentLabel()+".plist")
}
