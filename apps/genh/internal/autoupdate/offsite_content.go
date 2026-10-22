package autoupdate

import (
	"fmt"
	"strconv"
	"strings"
)

// ─── Lịch tuần "bản sao ngoài máy" (v0.1.40, F-12) — phần SINH NỘI DUNG ─────
//
// Cùng bộ hẹn giờ với lịch tự cập nhật đêm (systemd --user timer / crontab /
// LaunchAgent / Task Scheduler) nhưng tên, marker và tệp riêng — bật/tắt độc
// lập. Hàm THUẦN như content.go: test được cả 3 hệ trên một máy Linux.

// OffsiteTaskName là tên lịch tuần ở MỌI hệ điều hành (unit systemd, tên Task
// Scheduler; LaunchAgent dùng OffsiteLaunchdLabel).
const OffsiteTaskName = "gen-harness-offsite"

// OffsiteLaunchdLabel là Label của LaunchAgent lịch tuần.
const OffsiteLaunchdLabel = "com.gen-harness.offsite"

// OffsiteCrontabMarker đứng NGAY TRƯỚC dòng cron lịch tuần (fallback khi máy
// không có systemd --user) — marker riêng, không đụng dòng tự cập nhật đêm hay
// watcher "Cập nhật ngay".
const OffsiteCrontabMarker = "# gen-harness-offsite (genh offsite) — KHONG sua tay"

// Các giá trị "schedule" trong run/offsite-status.json (hợp đồng với apps/api).
const (
	ScheduleSystemd  = "systemd"
	ScheduleCron     = "cron"
	ScheduleLaunchd  = "launchd"
	ScheduleSchtasks = "schtasks"
)

// OffsiteJob là những gì lịch tuần cần biết để gọi đúng bản cài.
type OffsiteJob struct {
	// InstallDir là gốc cài đặt — truyền qua --install-dir (và GEN_HARNESS_HOME).
	InstallDir string
	// Port là cổng HTTPS khi KHÁC mặc định (0 = mặc định, không truyền --port).
	Port int
	// Env là biến "KEY=VALUE" cần mang theo (ví dụ GENH_COMPOSE_FILE) — như
	// RequestPaths.Env: lịch chạy ngoài phiên shell của Owner.
	Env []string
}

// args là đối số sau binary genh: `offsite run --quiet --install-dir <dir> [--port N]`.
func (j OffsiteJob) args() []string {
	a := []string{"offsite", "run", "--quiet"}
	if j.InstallDir != "" {
		a = append(a, "--install-dir", j.InstallDir)
	}
	if j.Port > 0 {
		a = append(a, "--port", strconv.Itoa(j.Port))
	}
	return a
}

func (j OffsiteJob) env() []string {
	out := []string{}
	if j.InstallDir != "" {
		out = append(out, "GEN_HARNESS_HOME="+j.InstallDir)
	}
	return append(out, j.Env...)
}

// SystemdOffsiteServiceUnit sinh "<OffsiteTaskName>.service": chạy MỘT lần
// `genh offsite run --quiet …`, log nối vào logFile. KillMode=mixed +
// TimeoutStopSec=900 như SystemdServiceUnit (xuất gói lớn có thể lâu; dừng unit
// chỉ gửi SIGTERM cho genh chính).
func SystemdOffsiteServiceUnit(genhPath, logFile string, job OffsiteJob) string {
	env := ""
	for _, kv := range job.env() {
		env += fmt.Sprintf("Environment=%s\n", quoteUnitArg(kv))
	}
	parts := []string{quoteUnitArg(genhPath)}
	for _, a := range job.args() {
		parts = append(parts, quoteUnitArg(a))
	}
	return fmt.Sprintf(`[Unit]
Description=Gen-Harness — ban sao ngoai may hang tuan (genh offsite run)

[Service]
Type=oneshot
%sExecStart=%s
KillMode=mixed
TimeoutStopSec=900
StandardOutput=append:%s
StandardError=append:%s
`, env, strings.Join(parts, " "), logFile, logFile)
}

// SystemdOffsiteTimerUnit sinh "<OffsiteTaskName>.timer": mỗi Chủ nhật ~05:30
// giờ máy, Persistent=true bù lần lỡ khi máy tắt, lùi ngẫu nhiên tối đa 30 phút.
func SystemdOffsiteTimerUnit() string {
	return fmt.Sprintf(`[Unit]
Description=Gen-Harness — lich ban sao ngoai may moi Chu nhat (~05:30)

[Timer]
OnCalendar=Sun *-*-* 05:30:00
Persistent=true
RandomizedDelaySec=%d

[Install]
WantedBy=timers.target
`, randomizedDelaySec)
}

// offsiteMinute lẻ hoá phút chạy vào khoảng 30–59 (không có RandomizedDelaySec
// ở cron/launchd).
func offsiteMinute(minute int) int {
	return 30 + ((minute%30)+30)%30
}

// OffsiteCrontabLine sinh dòng crontab mỗi Chủ nhật lúc 05:<30–59>. Phần sau 5
// trường thời gian là một lệnh sh hợp lệ (E2E chạy đúng phần đó bằng `sh -c`).
func OffsiteCrontabLine(genhPath, logFile string, job OffsiteJob, minute int) string {
	env := ""
	for _, kv := range job.env() {
		k, v, _ := strings.Cut(kv, "=")
		env += k + "=" + shellQuote(v) + " "
	}
	parts := []string{shellQuote(genhPath)}
	for _, a := range job.args() {
		parts = append(parts, shellQuote(a))
	}
	return fmt.Sprintf("%d 5 * * 0 %s%s >> %s 2>&1", offsiteMinute(minute), env, strings.Join(parts, " "), shellQuote(logFile))
}

// MergeOffsiteCrontab: như MergeCrontab nhưng cho marker lịch tuần — không đụng
// dòng tự cập nhật đêm hay watcher.
func MergeOffsiteCrontab(existing, newLine string, removeOnly bool) string {
	return mergeCrontabMarked(existing, OffsiteCrontabMarker, newLine, removeOnly)
}

// xmlEscape thoát ký tự đặc biệt cho giá trị trong plist.
func xmlEscape(s string) string {
	r := strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;", "'", "&apos;")
	return r.Replace(s)
}

// OffsiteLaunchdPlist sinh plist LaunchAgent: Weekday=0 (Chủ nhật) Hour=5
// Minute=30–59 (lẻ hoá).
func OffsiteLaunchdPlist(genhPath, logFile string, job OffsiteJob, minute int) string {
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
	<key>StartCalendarInterval</key>
	<dict>
		<key>Weekday</key>
		<integer>0</integer>
		<key>Hour</key>
		<integer>5</integer>
		<key>Minute</key>
		<integer>%d</integer>
	</dict>
	<key>StandardOutPath</key>
	<string>%s</string>
	<key>StandardErrorPath</key>
	<string>%s</string>
	<key>RunAtLoad</key>
	<false/>
</dict>
</plist>
`, OffsiteLaunchdLabel, argXML.String(), envXML.String(), offsiteMinute(minute), xmlEscape(logFile), xmlEscape(logFile))
}

// OffsiteSchtasksCreateArgs sinh args `schtasks /Create` cho lịch tuần (Chủ nhật
// 05:30, quyền người dùng thường, /F ghi đè). Task Scheduler không đặt được
// biến môi trường — --install-dir đủ để genh tìm đúng bản cài.
func OffsiteSchtasksCreateArgs(genhPath, logFile string, job OffsiteJob) []string {
	parts := []string{`"` + genhPath + `"`}
	for _, a := range job.args() {
		if strings.ContainsAny(a, " \t") {
			a = `"` + a + `"`
		}
		parts = append(parts, a)
	}
	tr := fmt.Sprintf(`cmd.exe /c "%s >> "%s" 2>&1"`, strings.Join(parts, " "), logFile)
	return []string{
		"/Create", "/TN", OffsiteTaskName,
		"/TR", tr,
		"/SC", "WEEKLY",
		"/D", "SUN",
		"/ST", "05:30",
		"/RL", "LIMITED",
		"/F",
	}
}

// OffsiteSchtasksDeleteArgs: xoá task lịch tuần (/F không hỏi).
func OffsiteSchtasksDeleteArgs() []string {
	return []string{"/Delete", "/TN", OffsiteTaskName, "/F"}
}

// OffsiteSchtasksQueryArgs: hỏi task lịch tuần đã có chưa.
func OffsiteSchtasksQueryArgs() []string {
	return []string{"/Query", "/TN", OffsiteTaskName, "/FO", "LIST"}
}
