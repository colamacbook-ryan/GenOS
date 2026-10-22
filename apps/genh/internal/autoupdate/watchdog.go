package autoupdate

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// ─── Lịch "Trực canh máy chủ" (v0.1.44, F-6b) — phần GỌI THẬT ──────────────
//
// Khuôn y hệt EnableOffsite/DisableOffsite/OffsiteStatus. Deps.LogFile ở đây là
// logs/watchdog.log.

// WatchdogSchedule là kết quả WatchdogScheduleStatus: Mechanism là giá trị
// "schedule" ghi vào run/watchdog-status.json ("" khi chưa bật).
type WatchdogSchedule struct {
	Enabled   bool
	Mechanism string
	Detail    string
}

const watchdogEnabledMsg = "Đã bật trực canh máy chủ mỗi 12 phút (%s) — tắt bằng `genh watchdog disable`"

// EnableWatchdog bật (idempotent — gọi lại để làm mới unit) lịch trực canh, trả
// một dòng mô tả và cơ chế đã dùng (ScheduleSystemd/Cron/Launchd/Schtasks).
func EnableWatchdog(ctx context.Context, deps Deps, job WatchdogJob) (msg, mechanism string, err error) {
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
		return enableWatchdogLinux(ctx, deps, job)
	case "darwin":
		return enableWatchdogDarwin(ctx, deps, job)
	case "windows":
		if _, err := deps.runner().Output(ctx, "schtasks", WatchdogSchtasksCreateArgs(deps.GenhPath, deps.LogFile, job)); err != nil {
			return "", "", err
		}
		return fmt.Sprintf(watchdogEnabledMsg, "Task Scheduler"), ScheduleSchtasks, nil
	default:
		return "", "", fmt.Errorf("chưa hỗ trợ trực canh máy chủ trên %s", deps.goos())
	}
}

// DisableWatchdog gỡ lịch trực canh — KHÔNG lỗi nếu vốn chưa bật (idempotent).
func DisableWatchdog(ctx context.Context, deps Deps) (string, error) {
	runner := deps.runner()
	switch deps.goos() {
	case "linux":
		home, err := deps.homeDir()
		if err != nil {
			return "", fmt.Errorf("không xác định được thư mục home: %w", err)
		}
		// Lịch dùng chung giữa các bản cài: thuộc bản cài khác còn sống thì KHÔNG gỡ.
		if other, yes := deps.ownerOnLinux(ctx, home, WatchdogTaskName+".service", WatchdogCrontabMarker); yes {
			return keptMessage("lịch trực canh máy chủ", other), nil
		}
		_, _ = runner.Output(ctx, "systemctl", []string{"--user", "disable", "--now", WatchdogTaskName + ".timer"})
		_ = os.Remove(watchdogServicePath(home))
		_ = os.Remove(watchdogTimerPath(home))
		_, _ = runner.Output(ctx, "systemctl", []string{"--user", "daemon-reload"})
		removeWatchdogCrontab(ctx, runner)
	case "darwin":
		home, err := deps.homeDir()
		if err != nil {
			return "", fmt.Errorf("không xác định được thư mục home: %w", err)
		}
		path := watchdogPlistPath(home)
		if err := deps.guardDarwin(path); err != nil {
			if other, ok := OwnerOf(err); ok {
				return keptMessage("lịch trực canh máy chủ", other), nil
			}
		}
		_, _ = runner.Output(ctx, "launchctl", []string{"unload", path})
		_ = os.Remove(path)
	case "windows":
		_, _ = runner.Output(ctx, "schtasks", WatchdogSchtasksDeleteArgs())
	default:
		return "", fmt.Errorf("chưa hỗ trợ trực canh máy chủ trên %s", deps.goos())
	}
	return "Đã tắt trực canh máy chủ (không còn kiểm mỗi 12 phút, không gửi báo động).", nil
}

// WatchdogScheduleStatus báo lịch trực canh đang bật hay không, bằng cơ chế nào.
func WatchdogScheduleStatus(ctx context.Context, deps Deps) (WatchdogSchedule, error) {
	runner := deps.runner()
	switch deps.goos() {
	case "linux":
		if out, err := runner.Output(ctx, "systemctl", []string{"--user", "is-enabled", WatchdogTaskName + ".timer"}); err == nil {
			if strings.TrimSpace(string(out)) == "enabled" {
				return WatchdogSchedule{Enabled: true, Mechanism: ScheduleSystemd, Detail: "systemd --user timer: enabled"}, nil
			}
		}
		if out, err := runner.Output(ctx, "crontab", []string{"-l"}); err == nil && strings.Contains(string(out), WatchdogCrontabMarker) {
			return WatchdogSchedule{Enabled: true, Mechanism: ScheduleCron, Detail: "crontab: đã có dòng mỗi 12 phút"}, nil
		}
		return WatchdogSchedule{Detail: "chưa bật (không có systemd --user timer lẫn dòng crontab)"}, nil
	case "darwin":
		home, err := deps.homeDir()
		if err != nil {
			return WatchdogSchedule{}, fmt.Errorf("không xác định được thư mục home: %w", err)
		}
		if _, err := os.Stat(watchdogPlistPath(home)); err != nil {
			return WatchdogSchedule{Detail: "chưa bật (không có LaunchAgent)"}, nil
		}
		out, err := runner.Output(ctx, "launchctl", []string{"list"})
		if err == nil && strings.Contains(string(out), WatchdogLaunchdLabel) {
			return WatchdogSchedule{Enabled: true, Mechanism: ScheduleLaunchd, Detail: "LaunchAgent: đã nạp"}, nil
		}
		return WatchdogSchedule{Detail: "có tệp LaunchAgent nhưng chưa nạp (launchctl load)"}, nil
	case "windows":
		out, err := runner.Output(ctx, "schtasks", WatchdogSchtasksQueryArgs())
		if err != nil {
			return WatchdogSchedule{Detail: "chưa bật (không có Scheduled Task)"}, nil
		}
		text := string(out)
		if strings.Contains(text, "Disabled") {
			return WatchdogSchedule{Mechanism: ScheduleSchtasks, Detail: "Task Scheduler: " + firstLineContaining(text, "Status")}, nil
		}
		return WatchdogSchedule{Enabled: true, Mechanism: ScheduleSchtasks, Detail: "Task Scheduler: " + firstLineContaining(text, "Status")}, nil
	default:
		return WatchdogSchedule{}, fmt.Errorf("chưa hỗ trợ trực canh máy chủ trên %s", deps.goos())
	}
}

func watchdogServicePath(home string) string {
	return filepath.Join(systemdUserDir(home), WatchdogTaskName+".service")
}

func watchdogTimerPath(home string) string {
	return filepath.Join(systemdUserDir(home), WatchdogTaskName+".timer")
}

func watchdogPlistPath(home string) string {
	return filepath.Join(launchAgentDir(home), WatchdogLaunchdLabel+".plist")
}

func removeWatchdogCrontab(ctx context.Context, runner Runner) {
	existing, err := runner.Output(ctx, "crontab", []string{"-l"})
	if err != nil || !strings.Contains(string(existing), WatchdogCrontabMarker) {
		return
	}
	_ = installCrontab(ctx, runner, MergeWatchdogCrontab(string(existing), "", true))
}

func enableWatchdogLinux(ctx context.Context, deps Deps, job WatchdogJob) (string, string, error) {
	home, err := deps.homeDir()
	if err != nil {
		return "", "", fmt.Errorf("không xác định được thư mục home: %w", err)
	}
	runner := deps.runner()
	if systemdUserAvailable(ctx, runner, deps.lookPath()) {
		if err := deps.guardUnit(home, WatchdogTaskName+".service"); err != nil {
			return "", "", err
		}
		dir := systemdUserDir(home)
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return "", "", fmt.Errorf("tạo %s: %w", dir, err)
		}
		if err := os.WriteFile(watchdogServicePath(home), []byte(SystemdWatchdogServiceUnit(deps.GenhPath, deps.LogFile, job)), 0o644); err != nil {
			return "", "", fmt.Errorf("ghi %s: %w", watchdogServicePath(home), err)
		}
		if err := os.WriteFile(watchdogTimerPath(home), []byte(SystemdWatchdogTimerUnit()), 0o644); err != nil {
			return "", "", fmt.Errorf("ghi %s: %w", watchdogTimerPath(home), err)
		}
		if _, err := runner.Output(ctx, "systemctl", []string{"--user", "daemon-reload"}); err != nil {
			return "", "", fmt.Errorf("systemctl --user daemon-reload: %w", err)
		}
		if _, err := runner.Output(ctx, "systemctl", []string{"--user", "enable", "--now", WatchdogTaskName + ".timer"}); err != nil {
			return "", "", fmt.Errorf("systemctl --user enable --now: %w", err)
		}
		// Máy từng dùng crontab: bỏ dòng cũ để không chạy hai lần.
		removeWatchdogCrontab(ctx, runner)
		msg := fmt.Sprintf(watchdogEnabledMsg, "systemd --user timer")
		if _, warning := ensureLinger(ctx, runner, deps); warning != "" {
			msg += "\nCảnh báo: linger đang TẮT — trực canh chỉ chạy khi bạn đang đăng nhập. Chạy một lần: sudo loginctl enable-linger $USER"
		}
		return msg, ScheduleSystemd, nil
	}
	if err := deps.guardLinux(ctx, home, WatchdogTaskName+".service", WatchdogCrontabMarker); err != nil {
		return "", "", err
	}
	existing, _ := runner.Output(ctx, "crontab", []string{"-l"}) // chưa có crontab → rỗng
	merged := MergeWatchdogCrontab(string(existing), WatchdogCrontabLine(deps.GenhPath, deps.LogFile, job), false)
	if err := installCrontab(ctx, runner, merged); err != nil {
		return "", "", fmt.Errorf("cài crontab: %w", err)
	}
	return fmt.Sprintf(watchdogEnabledMsg, "crontab — máy này không có systemd --user"), ScheduleCron, nil
}

func enableWatchdogDarwin(ctx context.Context, deps Deps, job WatchdogJob) (string, string, error) {
	home, err := deps.homeDir()
	if err != nil {
		return "", "", fmt.Errorf("không xác định được thư mục home: %w", err)
	}
	runner := deps.runner()
	dir := launchAgentDir(home)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", "", fmt.Errorf("tạo %s: %w", dir, err)
	}
	path := watchdogPlistPath(home)
	if err := deps.guardDarwin(path); err != nil {
		return "", "", err
	}
	_, _ = runner.Output(ctx, "launchctl", []string{"unload", path})
	if err := os.WriteFile(path, []byte(WatchdogLaunchdPlist(deps.GenhPath, deps.LogFile, job)), 0o644); err != nil {
		return "", "", fmt.Errorf("ghi %s: %w", path, err)
	}
	if _, err := runner.Output(ctx, "launchctl", []string{"load", "-w", path}); err != nil {
		return "", "", fmt.Errorf("launchctl load -w %s: %w", path, err)
	}
	return fmt.Sprintf(watchdogEnabledMsg, "LaunchAgent"), ScheduleLaunchd, nil
}
