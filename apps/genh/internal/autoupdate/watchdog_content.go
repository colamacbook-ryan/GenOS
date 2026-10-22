package autoupdate

import (
	"fmt"
	"strconv"
	"strings"
)

// ─── Lịch "Trực canh máy chủ" mỗi 12 phút (v0.1.44, F-6b) — phần SINH NỘI DUNG ─
//
// Cùng khuôn với lịch tuần bản sao ngoài máy (offsite_content.go): tên, marker,
// tệp riêng — bật/tắt độc lập, KHÔNG phụ thuộc lịch tự cập nhật đêm
// (--no-auto-update không tắt trực canh). Hàm THUẦN: test được cả 4 hệ trên Linux.

// WatchdogTaskName là tên lịch trực canh ở MỌI hệ điều hành (unit systemd, tên
// Task Scheduler; LaunchAgent dùng WatchdogLaunchdLabel).
const WatchdogTaskName = "gen-harness-watchdog"

// WatchdogLaunchdLabel là Label của LaunchAgent trực canh.
const WatchdogLaunchdLabel = "com.gen-harness.watchdog"

// WatchdogCrontabMarker đứng NGAY TRƯỚC dòng cron trực canh (fallback khi máy
// không có systemd --user) — marker riêng.
const WatchdogCrontabMarker = "# gen-harness-watchdog (genh) — KHONG sua tay"

// watchdogIntervalMin là nhịp trực canh (phút).
const watchdogIntervalMin = 12

// WatchdogJob là những gì lịch trực canh cần biết để gọi đúng bản cài.
type WatchdogJob struct {
	// InstallDir là gốc cài đặt — truyền qua --install-dir (và GEN_HARNESS_HOME).
	InstallDir string
	// Port là cổng HTTPS khi KHÁC mặc định (0 = mặc định, không truyền --port).
	Port int
	// Env là biến "KEY=VALUE" cần mang theo (ví dụ GENH_COMPOSE_FILE).
	Env []string
}

// args là đối số sau binary genh: `doctor --notify --quiet --install-dir <dir> [--port N]`.
func (j WatchdogJob) args() []string {
	a := []string{"doctor", "--notify", "--quiet"}
	if j.InstallDir != "" {
		a = append(a, "--install-dir", j.InstallDir)
	}
	if j.Port > 0 {
		a = append(a, "--port", strconv.Itoa(j.Port))
	}
	return a
}

func (j WatchdogJob) env() []string {
	out := []string{}
	if j.InstallDir != "" {
		out = append(out, "GEN_HARNESS_HOME="+j.InstallDir)
	}
	return append(out, j.Env...)
}

// SystemdWatchdogServiceUnit sinh "<WatchdogTaskName>.service": chạy MỘT lượt
// `genh doctor --notify --quiet …` (Nice=10 — nhường máy; TimeoutStartSec=300
// — một lượt tự giới hạn 4 phút), log nối vào logFile.
func SystemdWatchdogServiceUnit(genhPath, logFile string, job WatchdogJob) string {
	env := ""
	for _, kv := range job.env() {
		env += fmt.Sprintf("Environment=%s\n", quoteUnitArg(kv))
	}
	parts := []string{quoteUnitArg(genhPath)}
	for _, a := range job.args() {
		parts = append(parts, quoteUnitArg(a))
	}
	return fmt.Sprintf(`[Unit]
Description=Gen-Harness — truc canh may chu (genh doctor --notify)

[Service]
Type=oneshot
%sExecStart=%s
Nice=10
TimeoutStartSec=300
StandardOutput=append:%s
StandardError=append:%s
`, env, strings.Join(parts, " "), logFile, logFile)
}

// SystemdWatchdogTimerUnit sinh "<WatchdogTaskName>.timer": 5 phút sau khi bật
// máy rồi mỗi 12 phút sau lần chạy trước.
func SystemdWatchdogTimerUnit() string {
	return fmt.Sprintf(`[Unit]
Description=Gen-Harness — lich truc canh may chu moi %d phut

[Timer]
OnBootSec=5min
OnUnitActiveSec=%dmin
AccuracySec=1min

[Install]
WantedBy=timers.target
`, watchdogIntervalMin, watchdogIntervalMin)
}

// WatchdogCrontabLine sinh dòng crontab mỗi 12 phút. Phần sau 5 trường thời
// gian là một lệnh sh hợp lệ.
func WatchdogCrontabLine(genhPath, logFile string, job WatchdogJob) string {
	env := ""
	for _, kv := range job.env() {
		k, v, _ := strings.Cut(kv, "=")
		env += k + "=" + shellQuote(v) + " "
	}
	parts := []string{shellQuote(genhPath)}
	for _, a := range job.args() {
		parts = append(parts, shellQuote(a))
	}
	return fmt.Sprintf("*/%d * * * * %s%s >> %s 2>&1", watchdogIntervalMin, env, strings.Join(parts, " "), shellQuote(logFile))
}

// MergeWatchdogCrontab: như MergeCrontab nhưng cho marker trực canh — không đụng
// dòng tự cập nhật đêm, watcher hay lịch tuần.
func MergeWatchdogCrontab(existing, newLine string, removeOnly bool) string {
	return mergeCrontabMarked(existing, WatchdogCrontabMarker, newLine, removeOnly)
}

// WatchdogLaunchdPlist sinh plist LaunchAgent: StartInterval=720 giây.
func WatchdogLaunchdPlist(genhPath, logFile string, job WatchdogJob) string {
	var argXML, envXML strings.Builder
	argXML.WriteString("\t\t<string>" + xmlEscape(genhPath) + "</string>\n")
	for _, a := range job.args() {
		argXML.WriteString("\t\t<string>" + xmlEscape(a) + "</string>\n")
	}
	for _, kv := range job.env() {
		k, v, _ := strings.Cut(kv, "=")
		envXML.WriteString("\t\t<key>" + xmlEscape(k) + "</key>\n\t\t<string>" + xmlEscape(v) + "</string>\n")
	}
	return fmt.Sprintf(`<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>%s</string>
	<key>ProgramArguments</key>
	<array>
%s	</array>
	<key>EnvironmentVariables</key>
	<dict>
%s	</dict>
	<key>StartInterval</key>
	<integer>%d</integer>
	<key>StandardOutPath</key>
	<string>%s</string>
	<key>StandardErrorPath</key>
	<string>%s</string>
	<key>RunAtLoad</key>
	<false/>
</dict>
</plist>
`, WatchdogLaunchdLabel, argXML.String(), envXML.String(), watchdogIntervalMin*60, xmlEscape(logFile), xmlEscape(logFile))
}

// WatchdogSchtasksCreateArgs sinh args `schtasks /Create` (mỗi 12 phút, quyền
// người dùng thường, /F ghi đè). Task Scheduler không đặt được biến môi trường
// — --install-dir đủ để genh tìm đúng bản cài.
func WatchdogSchtasksCreateArgs(genhPath, logFile string, job WatchdogJob) []string {
	parts := []string{`"` + genhPath + `"`}
	for _, a := range job.args() {
		if strings.ContainsAny(a, " \t") {
			a = `"` + a + `"`
		}
		parts = append(parts, a)
	}
	tr := fmt.Sprintf(`cmd.exe /c "%s >> "%s" 2>&1"`, strings.Join(parts, " "), logFile)
	return []string{
		"/Create", "/TN", WatchdogTaskName,
		"/TR", tr,
		"/SC", "MINUTE",
		"/MO", strconv.Itoa(watchdogIntervalMin),
		"/RL", "LIMITED",
		"/F",
	}
}

// WatchdogSchtasksDeleteArgs: xoá task trực canh (/F không hỏi).
func WatchdogSchtasksDeleteArgs() []string {
	return []string{"/Delete", "/TN", WatchdogTaskName, "/F"}
}

// WatchdogSchtasksQueryArgs: hỏi task trực canh đã có chưa.
func WatchdogSchtasksQueryArgs() []string {
	return []string{"/Query", "/TN", WatchdogTaskName, "/FO", "LIST"}
}
