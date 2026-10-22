package autoupdate

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// ─── Lịch tuần "bản sao ngoài máy" (v0.1.40, F-12) — phần GỌI THẬT ─────────
//
// Cùng khuôn với Enable/Disable/GetStatus của lịch đêm: rẽ nhánh theo
// Deps.GOOS, mọi lệnh hệ thống đi qua Deps.Runner (tiêm giả khi test).
// Deps.LogFile ở đây là logs/offsite.log.

// OffsiteScheduleStatus là kết quả OffsiteStatus: Mechanism là giá trị
// "schedule" ghi vào run/offsite-status.json ("" khi chưa bật).
type OffsiteScheduleStatus struct {
	Enabled   bool
	Mechanism string
	Detail    string
}

const offsiteEnabledMsg = "Đã bật lịch sao lưu ra ổ ngoài mỗi Chủ nhật ~05:30 (%s) — tắt bằng `genh offsite disable`"

// EnableOffsite bật (idempotent — gọi lại để làm mới unit) lịch tuần, trả một
// dòng mô tả và cơ chế đã dùng (ScheduleSystemd/Cron/Launchd/Schtasks).
func EnableOffsite(ctx context.Context, deps Deps, job OffsiteJob) (msg, mechanism string, err error) {
	if deps.GenhPath == "" {
		return "", "", fmt.Errorf("thiếu đường dẫn binary genh")
	}
	if deps.InstallDir == "" {
		deps.InstallDir = job.InstallDir
	}
	if deps.LogFile != "" {
		_ = os.MkdirAll(filepath.Dir(deps.LogFile), 0o755)
	}
	switch deps.goos() {
	case "linux":
		return enableOffsiteLinux(ctx, deps, job)
	case "darwin":
		return enableOffsiteDarwin(ctx, deps, job)
	case "windows":
		if _, err := deps.runner().Output(ctx, "schtasks", OffsiteSchtasksCreateArgs(deps.GenhPath, deps.LogFile, job)); err != nil {
			return "", "", err
		}
		return fmt.Sprintf(offsiteEnabledMsg, "Task Scheduler"), ScheduleSchtasks, nil
	default:
		return "", "", fmt.Errorf("chưa hỗ trợ lịch sao lưu ra ổ ngoài trên %s", deps.goos())
	}
}

// DisableOffsite gỡ lịch tuần — KHÔNG lỗi nếu vốn chưa bật (idempotent).
func DisableOffsite(ctx context.Context, deps Deps) (string, error) {
	runner := deps.runner()
	switch deps.goos() {
	case "linux":
		home, err := deps.homeDir()
		if err != nil {
			return "", fmt.Errorf("không xác định được thư mục home: %w", err)
		}
		// Lịch dùng chung giữa các bản cài: thuộc bản cài khác còn sống thì KHÔNG gỡ.
		if other, yes := deps.ownerOnLinux(ctx, home, OffsiteTaskName+".service", OffsiteCrontabMarker); yes {
			return keptMessage("lịch sao lưu ra ổ ngoài", other), nil
		}
		// Gỡ cả hai cơ chế, best-effort (máy có thể đã đổi có/mất systemd --user).
		_, _ = runner.Output(ctx, "systemctl", []string{"--user", "disable", "--now", OffsiteTaskName + ".timer"})
		_ = os.Remove(offsiteServicePath(home))
		_ = os.Remove(offsiteTimerPath(home))
		_, _ = runner.Output(ctx, "systemctl", []string{"--user", "daemon-reload"})
		removeOffsiteCrontab(ctx, runner)
	case "darwin":
		home, err := deps.homeDir()
		if err != nil {
			return "", fmt.Errorf("không xác định được thư mục home: %w", err)
		}
		path := offsitePlistPath(home)
		if err := deps.guardDarwin(path); err != nil {
			if other, ok := OwnerOf(err); ok {
				return keptMessage("lịch sao lưu ra ổ ngoài", other), nil
			}
		}
		_, _ = runner.Output(ctx, "launchctl", []string{"unload", path})
		_ = os.Remove(path)
	case "windows":
		_, _ = runner.Output(ctx, "schtasks", OffsiteSchtasksDeleteArgs())
	default:
		return "", fmt.Errorf("chưa hỗ trợ lịch sao lưu ra ổ ngoài trên %s", deps.goos())
	}
	return "Đã tắt lịch sao lưu ra ổ ngoài hằng tuần.", nil
}

// OffsiteStatus báo lịch tuần đang bật hay không, bằng cơ chế nào.
func OffsiteStatus(ctx context.Context, deps Deps) (OffsiteScheduleStatus, error) {
	runner := deps.runner()
	switch deps.goos() {
	case "linux":
		if out, err := runner.Output(ctx, "systemctl", []string{"--user", "is-enabled", OffsiteTaskName + ".timer"}); err == nil {
			state := strings.TrimSpace(string(out))
			if state == "enabled" {
				return OffsiteScheduleStatus{Enabled: true, Mechanism: ScheduleSystemd, Detail: "systemd --user timer: enabled"}, nil
			}
		}
		if out, err := runner.Output(ctx, "crontab", []string{"-l"}); err == nil && strings.Contains(string(out), OffsiteCrontabMarker) {
			return OffsiteScheduleStatus{Enabled: true, Mechanism: ScheduleCron, Detail: "crontab: đã có dòng lịch tuần"}, nil
		}
		return OffsiteScheduleStatus{Detail: "chưa bật (không có systemd --user timer lẫn dòng crontab)"}, nil
	case "darwin":
		home, err := deps.homeDir()
		if err != nil {
			return OffsiteScheduleStatus{}, fmt.Errorf("không xác định được thư mục home: %w", err)
		}
		if _, err := os.Stat(offsitePlistPath(home)); err != nil {
			return OffsiteScheduleStatus{Detail: "chưa bật (không có LaunchAgent)"}, nil
		}
		out, err := runner.Output(ctx, "launchctl", []string{"list"})
		if err == nil && strings.Contains(string(out), OffsiteLaunchdLabel) {
			return OffsiteScheduleStatus{Enabled: true, Mechanism: ScheduleLaunchd, Detail: "LaunchAgent: đã nạp"}, nil
		}
		return OffsiteScheduleStatus{Detail: "có tệp LaunchAgent nhưng chưa nạp (launchctl load)"}, nil
	case "windows":
		out, err := runner.Output(ctx, "schtasks", OffsiteSchtasksQueryArgs())
		if err != nil {
			return OffsiteScheduleStatus{Detail: "chưa bật (không có Scheduled Task)"}, nil
		}
		text := string(out)
		if strings.Contains(text, "Disabled") {
			return OffsiteScheduleStatus{Mechanism: ScheduleSchtasks, Detail: "Task Scheduler: " + firstLineContaining(text, "Status")}, nil
		}
		return OffsiteScheduleStatus{Enabled: true, Mechanism: ScheduleSchtasks, Detail: "Task Scheduler: " + firstLineContaining(text, "Status")}, nil
	default:
		return OffsiteScheduleStatus{}, fmt.Errorf("chưa hỗ trợ lịch sao lưu ra ổ ngoài trên %s", deps.goos())
	}
}

func offsiteServicePath(home string) string {
	return filepath.Join(systemdUserDir(home), OffsiteTaskName+".service")
}

func offsiteTimerPath(home string) string {
	return filepath.Join(systemdUserDir(home), OffsiteTaskName+".timer")
}

func offsitePlistPath(home string) string {
	return filepath.Join(launchAgentDir(home), OffsiteLaunchdLabel+".plist")
}

func removeOffsiteCrontab(ctx context.Context, runner Runner) {
	existing, err := runner.Output(ctx, "crontab", []string{"-l"})
	if err != nil || !strings.Contains(string(existing), OffsiteCrontabMarker) {
		return
	}
	_ = installCrontab(ctx, runner, MergeOffsiteCrontab(string(existing), "", true))
}

func enableOffsiteLinux(ctx context.Context, deps Deps, job OffsiteJob) (string, string, error) {
	home, err := deps.homeDir()
	if err != nil {
		return "", "", fmt.Errorf("không xác định được thư mục home: %w", err)
	}
	runner := deps.runner()
	if systemdUserAvailable(ctx, runner, deps.lookPath()) {
		if err := deps.guardUnit(home, OffsiteTaskName+".service"); err != nil {
			return "", "", err
		}
		dir := systemdUserDir(home)
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return "", "", fmt.Errorf("tạo %s: %w", dir, err)
		}
		if err := os.WriteFile(offsiteServicePath(home), []byte(SystemdOffsiteServiceUnit(deps.GenhPath, deps.LogFile, job)), 0o644); err != nil {
			return "", "", fmt.Errorf("ghi %s: %w", offsiteServicePath(home), err)
		}
		if err := os.WriteFile(offsiteTimerPath(home), []byte(SystemdOffsiteTimerUnit()), 0o644); err != nil {
			return "", "", fmt.Errorf("ghi %s: %w", offsiteTimerPath(home), err)
		}
		if _, err := runner.Output(ctx, "systemctl", []string{"--user", "daemon-reload"}); err != nil {
			return "", "", fmt.Errorf("systemctl --user daemon-reload: %w", err)
		}
		if _, err := runner.Output(ctx, "systemctl", []string{"--user", "enable", "--now", OffsiteTaskName + ".timer"}); err != nil {
			return "", "", fmt.Errorf("systemctl --user enable --now: %w", err)
		}
		// Máy từng dùng crontab (trước khi có systemd --user): bỏ dòng cũ để không chạy hai lần.
		removeOffsiteCrontab(ctx, runner)
		msg := fmt.Sprintf(offsiteEnabledMsg, "systemd --user timer")
		// Như enableLinux: không có linger thì timer chỉ chạy khi đang đăng nhập.
		if _, warning := ensureLinger(ctx, runner, deps); warning != "" {
			msg += "\nCảnh báo: linger đang TẮT — lịch chỉ chạy khi bạn đang đăng nhập. Chạy một lần: sudo loginctl enable-linger $USER"
		}
		return msg, ScheduleSystemd, nil
	}
	if err := deps.guardLinux(ctx, home, OffsiteTaskName+".service", OffsiteCrontabMarker); err != nil {
		return "", "", err
	}
	minute := parseMinuteFromClock(time.Now().Format("15:04:05"))
	existing, _ := runner.Output(ctx, "crontab", []string{"-l"}) // chưa có crontab → rỗng
	merged := MergeOffsiteCrontab(string(existing), OffsiteCrontabLine(deps.GenhPath, deps.LogFile, job, minute), false)
	if err := installCrontab(ctx, runner, merged); err != nil {
		return "", "", fmt.Errorf("cài crontab: %w", err)
	}
	return fmt.Sprintf(offsiteEnabledMsg, "crontab — máy này không có systemd --user"), ScheduleCron, nil
}

func enableOffsiteDarwin(ctx context.Context, deps Deps, job OffsiteJob) (string, string, error) {
	home, err := deps.homeDir()
	if err != nil {
		return "", "", fmt.Errorf("không xác định được thư mục home: %w", err)
	}
	runner := deps.runner()
	dir := launchAgentDir(home)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", "", fmt.Errorf("tạo %s: %w", dir, err)
	}
	path := offsitePlistPath(home)
	if err := deps.guardDarwin(path); err != nil {
		return "", "", err
	}
	_, _ = runner.Output(ctx, "launchctl", []string{"unload", path})
	minute := parseMinuteFromClock(time.Now().Format("15:04:05"))
	if err := os.WriteFile(path, []byte(OffsiteLaunchdPlist(deps.GenhPath, deps.LogFile, job, minute)), 0o644); err != nil {
		return "", "", fmt.Errorf("ghi %s: %w", path, err)
	}
	if _, err := runner.Output(ctx, "launchctl", []string{"load", "-w", path}); err != nil {
		return "", "", fmt.Errorf("launchctl load -w %s: %w", path, err)
	}
	return fmt.Sprintf(offsiteEnabledMsg, "LaunchAgent"), ScheduleLaunchd, nil
}
