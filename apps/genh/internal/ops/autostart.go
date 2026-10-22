package ops

import (
	"context"
	"fmt"
	"io"
	"os"
	"runtime"
	"strings"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/autoupdate"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
)

// autostartCmdTimeout giới hạn MỖI lệnh kiểm tự khởi động (docker info,
// systemctl, loginctl) — máy có systemd treo/D-Bus chết không được làm treo
// genh status/doctor.
const autostartCmdTimeout = 10 * time.Second

// AutostartDeps cho phép tiêm Runner giả/GOOS/UID khi test — rỗng dùng giá trị thật.
type AutostartDeps struct {
	Runner dockercli.Runner
	GOOS   string // "" = runtime.GOOS
	UID    string // "" = os.Getuid()
}

// CheckAutostart (v0.1.37, F-73) kiểm máy có tự chạy lại Gen-Harness sau khi
// bật/khởi động lại không:
//   - Docker: dịch vụ docker (system: `systemctl is-enabled docker.service`;
//     rootless: `systemctl --user is-enabled docker.service`) phải được bật;
//   - linger (systemd --user chạy khi chưa đăng nhập): bắt buộc nếu có lịch
//     đêm/watcher/trực canh/bản sao ngoài máy systemd --user HOẶC Docker rootless.
//
// macOS/Windows: linger không áp dụng. Docker CHỈ coi là "tự lo"
// (not_applicable, docker_mode=desktop) khi phát hiện được Docker Desktop
// (`docker info` OperatingSystem "Docker Desktop" — Desktop có "Start when you
// sign in"); runtime genh tự cài (Colima/Lima trên macOS, distro WSL trên
// Windows) KHÔNG có gì tự khởi động VM khi bật máy, và không đọc được Docker →
// "unknown" (Console hiện "Chưa rõ", không báo sai "Có"). KHÔNG BAO GIỜ trả lỗi:
// lệnh lỗi → "unknown" (status/doctor vẫn chạy tiếp).
func CheckAutostart(ctx context.Context, deps AutostartDeps) hostlink.AutostartStatus {
	goos := deps.GOOS
	if goos == "" {
		goos = runtime.GOOS
	}
	st := hostlink.AutostartStatus{OS: goos, CheckedAt: time.Now().UTC().Format(time.RFC3339)}
	runner := deps.Runner
	if runner == nil {
		runner = dockercli.ExecRunner{}
	}
	run := func(name string, args ...string) ([]byte, error) {
		cctx, cancel := context.WithTimeout(ctx, autostartCmdTimeout)
		defer cancel()
		return runner.Output(cctx, dockercli.Cmd{Name: name, Args: args})
	}
	if goos != "linux" {
		st.Linger = "not_applicable"
		st.DockerEnabled, st.DockerMode = "unknown", "unknown"
		if out, err := run("docker", "info", "--format", "{{.OperatingSystem}}"); err == nil && strings.Contains(string(out), "Docker Desktop") {
			st.DockerEnabled, st.DockerMode = "not_applicable", "desktop"
		}
		return st
	}
	uid := deps.UID
	if uid == "" {
		uid = fmt.Sprint(os.Getuid())
	}

	// Docker system hay rootless.
	st.DockerMode = "unknown"
	if out, err := run("docker", "info", "--format", "{{json .SecurityOptions}}"); err == nil {
		if strings.Contains(string(out), "rootless") {
			st.DockerMode = "rootless"
		} else {
			st.DockerMode = "system"
		}
	}

	// Docker có tự chạy khi bật máy không.
	switch st.DockerMode {
	case "rootless":
		st.DockerEnabled = normalizeIsEnabled(run("systemctl", "--user", "is-enabled", "docker.service"))
	case "system":
		st.DockerEnabled = normalizeIsEnabled(run("systemctl", "is-enabled", "docker.service"))
	default:
		// "unknown" (docker info lỗi/quá hạn): vẫn hỏi systemd hệ thống nhưng CHỈ
		// tin "yes" — máy chỉ dùng Docker rootless thường TẮT docker.service hệ
		// thống (dockerd-rootless-setuptool khuyên vậy): "no" ở đây không có
		// nghĩa Gen-Harness sẽ không tự lên, và khuyên `sudo systemctl enable
		// docker` là sai. Không có docker.service thì lệnh lỗi ⇒ "unknown".
		if normalizeIsEnabled(run("systemctl", "is-enabled", "docker.service")) == "yes" {
			st.DockerEnabled = "yes"
		} else {
			st.DockerEnabled = "unknown"
		}
	}

	// linger.
	st.Linger = "unknown"
	if out, err := run("loginctl", "show-user", uid, "--property=Linger", "--value"); err == nil {
		switch strings.TrimSpace(string(out)) {
		case "yes":
			st.Linger = "yes"
		case "no":
			st.Linger = "no"
		}
	}
	st.LingerRequired = st.DockerMode == "rootless"
	// v0.1.53 (F-95): cả trực canh (12 phút) và bản sao ngoài máy (tuần) cũng là timer
	// systemd --user — chỉ chạy khi chưa đăng nhập nếu bật linger.
	for _, unit := range []string{
		autoupdate.RequestTaskName + ".path", autoupdate.TaskName + ".timer",
		autoupdate.WatchdogTaskName + ".timer", autoupdate.OffsiteTaskName + ".timer",
	} {
		if st.LingerRequired {
			break
		}
		if normalizeIsEnabled(run("systemctl", "--user", "is-enabled", unit)) == "yes" {
			st.LingerRequired = true
		}
	}
	return st
}

// normalizeIsEnabled chuẩn hoá stdout của `systemctl is-enabled`: lệnh trả mã
// ≠0 khi "disabled"/"masked" nhưng stdout vẫn có trạng thái — đọc stdout trước,
// chỉ coi là "unknown" khi không nhận ra trạng thái nào (không có systemctl,
// không có unit, D-Bus lỗi…). enabled-runtime: mất sau khởi động lại ⇒ "no".
func normalizeIsEnabled(out []byte, _ error) string {
	state := ""
	if f := strings.Fields(string(out)); len(f) > 0 {
		state = f[0]
	}
	switch {
	case state == "enabled" || state == "static" || state == "alias" || state == "indirect" || state == "generated":
		return "yes"
	case state == "disabled" || state == "masked" || state == "enabled-runtime" || strings.HasPrefix(state, "linked"):
		return "no"
	default:
		return "unknown"
	}
}

// autostartLines là các dòng "Tự chạy lại khi bật máy" cho genh status/doctor:
// (Docker, linger) — mỗi dòng: nhãn, OK hay không, mô tả kèm lệnh sửa khi "no".
func autostartLines(st hostlink.AutostartStatus) []diagLine {
	if st.OS != "linux" {
		docker := diagLine{"Docker tự chạy", true, "Docker Desktop tự quản lý (bật \"Start Docker Desktop when you sign in\")"}
		if st.DockerEnabled != "not_applicable" {
			docker = diagLine{"Docker tự chạy", false, "không rõ — không phải Docker Desktop (Colima/WSL do genh cài không tự chạy khi bật máy): sau khi bật lại máy, chạy genh start"}
		}
		return []diagLine{docker, {"Linger (systemd --user)", true, "không áp dụng trên " + st.OS}}
	}
	var docker diagLine
	docker.Check = "Docker tự chạy"
	mode := st.DockerMode
	switch st.DockerEnabled {
	case "yes":
		docker.OK, docker.Info = true, "có ("+mode+")"
	case "no":
		fix := "sudo systemctl enable docker"
		if mode == "rootless" {
			fix = "systemctl --user enable docker"
		}
		docker.Info = "KHÔNG — sau khi khởi động lại máy, Gen-Harness sẽ không tự lên. Sửa: " + fix
	default:
		docker.Info = "không rõ (không hỏi được systemd)"
	}

	linger := diagLine{Check: "Linger (systemd --user)"}
	switch {
	case st.Linger == "yes":
		linger.OK, linger.Info = true, "có"
	case !st.LingerRequired:
		linger.OK, linger.Info = true, "không cần (không có lịch/dịch vụ systemd --user)"
		if st.Linger == "no" {
			linger.Info = "tắt — không cần (không có lịch/dịch vụ systemd --user)"
		}
	case st.Linger == "no":
		linger.Info = "KHÔNG — lịch tự cập nhật/nút Console (và Docker rootless) chỉ chạy khi bạn đang đăng nhập. Sửa: sudo loginctl enable-linger $USER"
	default:
		linger.Info = "không rõ (không hỏi được loginctl)"
	}
	return []diagLine{docker, linger}
}

// printAutostart in mục "Tự chạy lại khi bật máy:" (genh status).
func printAutostart(out io.Writer, st hostlink.AutostartStatus) {
	_, _ = fmt.Fprintln(out, "Tự chạy lại khi bật máy:")
	for _, l := range autostartLines(st) {
		_, _ = fmt.Fprintln(out, "  "+l.String())
	}
}
