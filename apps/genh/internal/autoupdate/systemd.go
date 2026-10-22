package autoupdate

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

// enableLinux thử systemd --user trước (dùng `systemctl --user
// daemon-reload` như phép thử máy CÓ systemd --user hoạt động thật — một số
// container/WSL có binary systemctl nhưng KHÔNG có user session/D-Bus, lệnh
// này thất bại rõ ràng trong trường hợp đó); fallback sang crontab người
// dùng nếu systemd --user không dùng được.
func enableLinux(ctx context.Context, deps Deps) (EnableResult, error) {
	home, err := deps.homeDir()
	if err != nil {
		return EnableResult{}, fmt.Errorf("không xác định được thư mục home: %w", err)
	}
	runner := deps.runner()
	job := deps.job()

	if systemdUserAvailable(ctx, runner, deps.lookPath()) {
		// Lịch dùng chung giữa các bản cài: đang thuộc bản cài khác còn sống thì không ghi đè.
		if err := deps.guardUnit(home, TaskName+".service"); err != nil {
			return EnableResult{}, err
		}
		dir := systemdUserDir(home)
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return EnableResult{}, fmt.Errorf("tạo %s: %w", dir, err)
		}
		svc := SystemdServiceUnit(deps.GenhPath, deps.LogFile, job)
		tim := SystemdTimerUnit()
		if err := os.WriteFile(serviceUnitPath(home), []byte(svc), 0o644); err != nil {
			return EnableResult{}, fmt.Errorf("ghi %s: %w", serviceUnitPath(home), err)
		}
		if err := os.WriteFile(timerUnitPath(home), []byte(tim), 0o644); err != nil {
			return EnableResult{}, fmt.Errorf("ghi %s: %w", timerUnitPath(home), err)
		}
		if _, err := runner.Output(ctx, "systemctl", []string{"--user", "daemon-reload"}); err != nil {
			return EnableResult{}, fmt.Errorf("systemctl --user daemon-reload: %w", err)
		}
		if _, err := runner.Output(ctx, "systemctl", []string{"--user", "enable", "--now", TaskName + ".timer"}); err != nil {
			return EnableResult{}, fmt.Errorf("systemctl --user enable --now: %w", err)
		}
		// Timer systemd --user chỉ chạy khi user đang có phiên đăng nhập, trừ
		// khi bật "linger" — máy chủ chạy suốt đêm không ai đăng nhập sẽ
		// không bao giờ tự cập nhật nếu thiếu bước này. Không bật được (polkit
		// đòi mật khẩu, ví dụ qua SSH) chỉ cảnh báo, không coi là lỗi — và v0.1.53
		// (F-95) hỏi lại `loginctl show-user` thay vì tin mã thoát của enable-linger.
		linger, warning := ensureLinger(ctx, runner, deps)
		return EnableResult{
			Msg:       "Đã bật tự cập nhật hằng đêm lúc ~03:00 (systemd --user timer) — tắt bằng `genh auto-update disable`",
			Mechanism: ScheduleSystemd,
			Linger:    linger,
			Warning:   warning,
		}, nil
	}

	// Fallback crontab.
	if err := deps.guardLinux(ctx, home, TaskName+".service", CrontabMarker); err != nil {
		return EnableResult{}, err
	}
	minute := parseMinuteFromClock(time.Now().Format("15:04:05"))
	line := CrontabLine(deps.GenhPath, deps.LogFile, minute, job)
	existing, _ := runner.Output(ctx, "crontab", []string{"-l"}) // lỗi (chưa có crontab nào) coi như rỗng
	merged := MergeCrontab(string(existing), line, false)
	if err := installCrontab(ctx, runner, merged); err != nil {
		return EnableResult{}, fmt.Errorf("cài crontab: %w", err)
	}
	return EnableResult{
		Msg:       "Đã bật tự cập nhật hằng đêm lúc ~03:00 (crontab — máy này không có systemd --user) — tắt bằng `genh auto-update disable`",
		Mechanism: ScheduleCron,
	}, nil
}

// ensureLinger chạy `loginctl enable-linger` rồi HỎI LẠI `loginctl show-user
// <uid> -p Linger --value` (F-95): enable-linger có thể thoát 0 mà linger vẫn
// tắt (hoặc thoát ≠0 vì đã bật sẵn). Trả trạng thái (yes|no|unknown) và câu
// cảnh báo (rỗng nếu ổn): cảnh báo khi linger=no, hoặc không hỏi được mà
// enable-linger cũng lỗi.
func ensureLinger(ctx context.Context, runner Runner, deps Deps) (state, warning string) {
	_, enableErr := runner.Output(ctx, "loginctl", []string{"enable-linger"})
	state = lingerState(ctx, runner, deps.uid())
	if state == "no" || (state == "unknown" && enableErr != nil) {
		warning = LingerWarning
	}
	return state, warning
}

// lingerState: `loginctl show-user <uid> -p Linger --value` → yes | no | unknown.
func lingerState(ctx context.Context, runner Runner, uid string) string {
	out, err := runner.Output(ctx, "loginctl", []string{"show-user", uid, "-p", "Linger", "--value"})
	if err != nil {
		return "unknown"
	}
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	switch strings.TrimSpace(lines[len(lines)-1]) {
	case "yes":
		return "yes"
	case "no":
		return "no"
	}
	return "unknown"
}

// refreshUnitsLinux bổ sung KillMode=mixed / TimeoutStopSec (v0.1.37 — unit
// chỉ được ghi lúc install/enable, máy cài từ bản cũ thiếu) và, từ v0.1.53
// (F-98), sửa ExecStart + thêm Environment=GEN_HARNESS_HOME vào unit lịch đêm
// ĐÃ CÀI rồi `systemctl --user daemon-reload`. Giữ nguyên mọi dòng khác — sửa tay
// của Owner (Environment=, Nice=, KillMode khác…). ExecStart chỉ được thay khi
// nó trỏ CÙNG binary deps.GenhPath hoặc binary đó không còn tồn tại; binary khác
// còn tồn tại (Owner cố ý chạy từ đường dẫn khác) thì giữ nguyên, không trỏ lịch
// đêm sang genh đang chạy lệnh này. Unit thuộc bản cài khác còn sống: không đụng
// ExecStart/Environment. Chưa có unit (lịch đêm tắt, hoặc dùng crontab) → không
// làm gì; KHÔNG enable/disable gì.
func refreshUnitsLinux(ctx context.Context, deps Deps) (RefreshReport, error) {
	home, err := deps.homeDir()
	if err != nil {
		return RefreshReport{}, fmt.Errorf("không xác định được thư mục home: %w", err)
	}
	path := serviceUnitPath(home)
	cur, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return RefreshReport{}, nil
		}
		return RefreshReport{}, fmt.Errorf("đọc %s: %w", path, err)
	}
	want, stop := patchServiceUnit(string(cur))
	var what []string
	if stop {
		what = append(what, "KillMode=mixed/TimeoutStopSec")
	}
	if job := deps.job(); job.InstallDir != "" {
		if _, other := OwnedByOther(home, TaskName+".service", job.InstallDir); !other {
			var changes []string
			want, changes = patchNightlyExec(want, deps.GenhPath, job)
			what = append(what, changes...)
		}
	}
	if len(what) == 0 {
		return RefreshReport{}, nil
	}
	if err := os.WriteFile(path, []byte(want), 0o644); err != nil {
		return RefreshReport{}, fmt.Errorf("ghi %s: %w", path, err)
	}
	if _, err := deps.runner().Output(ctx, "systemctl", []string{"--user", "daemon-reload"}); err != nil {
		return RefreshReport{Changed: true, What: what}, fmt.Errorf("systemctl --user daemon-reload: %w", err)
	}
	return RefreshReport{Changed: true, What: what}, nil
}

// stopDirectives: các dòng [Service] v0.1.37 cần có (xem SystemdServiceUnit).
var stopDirectives = []struct{ key, line string }{
	{"KillMode", "KillMode=mixed"},
	{"TimeoutStopSec", "TimeoutStopSec=900"},
}

// patchServiceUnit thêm các dòng stopDirectives CÒN THIẾU ngay sau dòng
// [Service] (khoá đã có — kể cả giá trị khác do Owner đặt — thì giữ nguyên).
// Không có mục [Service] → thêm mục đó ở cuối. changed=false nếu đủ cả.
func patchServiceUnit(cur string) (string, bool) {
	lines := strings.Split(cur, "\n")
	has := func(key string) bool {
		for _, l := range lines {
			if strings.HasPrefix(strings.TrimSpace(l), key+"=") {
				return true
			}
		}
		return false
	}
	var add []string
	for _, d := range stopDirectives {
		if !has(d.key) {
			add = append(add, d.line)
		}
	}
	if len(add) == 0 {
		return cur, false
	}
	for i, l := range lines {
		if strings.TrimSpace(l) == "[Service]" {
			out := append(append(append([]string{}, lines[:i+1]...), add...), lines[i+1:]...)
			return strings.Join(out, "\n"), true
		}
	}
	if cur != "" && !strings.HasSuffix(cur, "\n") {
		cur += "\n"
	}
	return cur + "\n[Service]\n" + strings.Join(add, "\n") + "\n", true
}

// patchNightlyExec đưa dòng ExecStart + Environment=GEN_HARNESS_HOME của unit
// lịch đêm về dạng Enable ghi (xem SystemdServiceUnit) — CHỈ khi ExecStart trỏ
// cùng binary genhPath hoặc binary đó không còn tồn tại. Chạy lần 2 không đổi
// gì (changes rỗng).
func patchNightlyExec(cur, genhPath string, job NightlyJob) (string, []string) {
	lines := strings.Split(cur, "\n")
	execIdx := -1
	for i, l := range lines {
		if strings.HasPrefix(strings.TrimSpace(l), "ExecStart=") {
			execIdx = i
			break
		}
	}
	if execIdx < 0 {
		return cur, nil
	}
	words := splitWords(strings.TrimPrefix(strings.TrimSpace(lines[execIdx]), "ExecStart="))
	if len(words) == 0 {
		return cur, nil
	}
	bin := words[0]
	if !samePath(bin, genhPath) {
		if _, err := os.Stat(bin); err == nil || !errors.Is(err, os.ErrNotExist) {
			return cur, nil // binary khác còn tồn tại (hoặc không kiểm được): giữ nguyên
		}
	}
	var changes []string
	if want := "ExecStart=" + job.execStart(genhPath); strings.TrimSpace(lines[execIdx]) != want {
		lines[execIdx] = want
		changes = append(changes, "ExecStart")
	}
	// Environment=GEN_HARNESS_HOME=<dir>
	wantEnv := "Environment=" + quoteUnitArg("GEN_HARNESS_HOME="+job.InstallDir)
	envIdx := -1
	for i, l := range lines {
		t := strings.TrimSpace(l)
		if !strings.HasPrefix(t, "Environment=") {
			continue
		}
		for _, w := range splitWords(strings.TrimPrefix(t, "Environment=")) {
			if strings.HasPrefix(w, "GEN_HARNESS_HOME=") {
				envIdx = i
			}
		}
	}
	switch {
	case envIdx < 0:
		lines = append(lines[:execIdx], append([]string{wantEnv}, lines[execIdx:]...)...)
		changes = append(changes, "Environment=GEN_HARNESS_HOME")
	default:
		t := strings.TrimSpace(lines[envIdx])
		words := splitWords(strings.TrimPrefix(t, "Environment="))
		// Chỉ sửa khi dòng này CHỈ khai GEN_HARNESS_HOME (không đụng biến khác của Owner).
		if len(words) == 1 && !samePath(strings.TrimPrefix(words[0], "GEN_HARNESS_HOME="), job.InstallDir) {
			lines[envIdx] = wantEnv
			changes = append(changes, "Environment=GEN_HARNESS_HOME")
		}
	}
	if len(changes) == 0 {
		return cur, nil
	}
	return strings.Join(lines, "\n"), changes
}

func disableLinux(ctx context.Context, deps Deps) (string, error) {
	home, err := deps.homeDir()
	if err != nil {
		return "", fmt.Errorf("không xác định được thư mục home: %w", err)
	}
	runner := deps.runner()

	// Lịch dùng chung: thuộc bản cài khác còn sống thì KHÔNG gỡ (F-98 — gỡ bản
	// cài phụ từng xoá luôn lịch đêm của bản chính).
	if other, yes := deps.ownerOnLinux(ctx, home, TaskName+".service", CrontabMarker); yes {
		return keptMessage("lịch tự cập nhật đêm", other), nil
	}

	// Tắt cả hai cơ chế, best-effort — Owner có thể đã bật bằng cơ chế này
	// rồi máy sau đó thay đổi (có/mất systemd --user), không muốn để sót.
	_, _ = runner.Output(ctx, "systemctl", []string{"--user", "disable", "--now", TaskName + ".timer"})
	_ = os.Remove(serviceUnitPath(home))
	_ = os.Remove(timerUnitPath(home))
	_, _ = runner.Output(ctx, "systemctl", []string{"--user", "daemon-reload"})

	existing, err := runner.Output(ctx, "crontab", []string{"-l"})
	if err == nil {
		merged := MergeCrontab(string(existing), "", true)
		_ = installCrontab(ctx, runner, merged)
	}

	return "Đã tắt tự cập nhật hằng đêm.", nil
}

// timerShowProps: thuộc tính hỏi `systemctl --user show` cho statusLinux.
const timerShowProps = "LoadState,UnitFileState,ActiveState,LastTriggerUSec,NextElapseUSecRealtime"

// parseShowProps đọc đầu ra "Khoá=Giá trị" từng dòng của `systemctl show`.
func parseShowProps(out []byte) map[string]string {
	props := map[string]string{}
	for _, line := range strings.Split(string(out), "\n") {
		k, v, ok := strings.Cut(strings.TrimSpace(line), "=")
		if !ok {
			continue
		}
		switch k {
		case "LoadState", "UnitFileState", "ActiveState", "LastTriggerUSec", "NextElapseUSecRealtime":
			props[k] = strings.TrimSpace(v)
		}
	}
	return props
}

// showTimer hỏi thuộc tính của gen-harness-update.timer. systemd >= 247 hiểu
// --timestamp=unix (mốc dạng "@giây"); cũ hơn thì hỏi lại không có cờ đó (mốc dạng
// "Mon 2006-01-02 15:04:05 MST"). Đọc stdout KỂ CẢ khi lệnh thoát ≠0. Không hỏi
// được gì (không có systemd --user) → nil.
func showTimer(ctx context.Context, runner Runner) map[string]string {
	base := []string{"--user", "show", TaskName + ".timer", "-p", timerShowProps}
	out, _ := runner.Output(ctx, "systemctl", append(append([]string{}, base...), "--timestamp=unix"))
	if p := parseShowProps(out); p["LoadState"] != "" {
		return p
	}
	out, _ = runner.Output(ctx, "systemctl", base)
	if p := parseShowProps(out); p["LoadState"] != "" {
		return p
	}
	// Bản systemd rất cũ / show lỗi: dò bằng is-enabled (thoát ≠0 khi disabled
	// nhưng stdout vẫn có trạng thái) + is-active.
	out, _ = runner.Output(ctx, "systemctl", []string{"--user", "is-enabled", TaskName + ".timer"})
	state := ""
	if f := strings.Fields(string(out)); len(f) > 0 {
		state = f[0]
	}
	switch state {
	case "enabled", "enabled-runtime", "disabled", "static", "masked", "masked-runtime", "linked", "linked-runtime", "indirect", "generated", "alias":
		p := map[string]string{"LoadState": "loaded", "UnitFileState": state}
		if a, _ := runner.Output(ctx, "systemctl", []string{"--user", "is-active", TaskName + ".timer"}); len(a) > 0 {
			if f := strings.Fields(string(a)); len(f) > 0 {
				p["ActiveState"] = f[0]
			}
		}
		return p
	}
	return nil
}

var systemdTimeLayouts = []string{
	"Mon 2006-01-02 15:04:05 MST",
	"Mon 2006-01-02 15:04:05",
	"2006-01-02 15:04:05 MST",
	"2006-01-02 15:04:05",
}

// parseSystemdTime đọc mốc giờ systemd in: "@giây" (--timestamp=unix) hoặc
// "Mon 2006-01-02 15:04:05 MST" (theo múi giờ máy, loc). Rỗng/n/a/0 ⇒ zero.
func parseSystemdTime(s string, loc *time.Location) time.Time {
	s = strings.TrimSpace(s)
	if s == "" || s == "n/a" || s == "0" || s == "@0" {
		return time.Time{}
	}
	if strings.HasPrefix(s, "@") {
		n, err := strconv.ParseInt(s[1:], 10, 64)
		if err != nil || n <= 0 {
			return time.Time{}
		}
		return time.Unix(n, 0)
	}
	if loc == nil {
		loc = time.Local
	}
	// Múi giờ dạng số ("+07", "-0330" — tzdata của nhiều nước dùng thay tên viết tắt):
	// Go đọc "MST" kiểu này thành múi giờ giả độ lệch 0 ⇒ tự đọc độ lệch.
	if i := strings.LastIndex(s, " "); i > 0 {
		if off, ok := parseNumericZone(s[i+1:]); ok {
			if t, err := time.ParseInLocation("Mon 2006-01-02 15:04:05", s[:i], time.FixedZone(s[i+1:], off)); err == nil {
				return t
			}
		}
	}
	for _, layout := range systemdTimeLayouts {
		if t, err := time.ParseInLocation(layout, s, loc); err == nil {
			return t
		}
	}
	// Tên múi giờ lạ (vd "+07"): bỏ từ cuối, đọc theo múi giờ máy.
	if i := strings.LastIndex(s, " "); i > 0 {
		if t, err := time.ParseInLocation("Mon 2006-01-02 15:04:05", s[:i], loc); err == nil {
			return t
		}
	}
	return time.Time{}
}

// parseNumericZone đọc "+07", "-03", "+0530", "+05:30" thành độ lệch giây.
func parseNumericZone(z string) (int, bool) {
	if len(z) < 3 || (z[0] != '+' && z[0] != '-') {
		return 0, false
	}
	digits := strings.ReplaceAll(z[1:], ":", "")
	if len(digits) != 2 && len(digits) != 4 {
		return 0, false
	}
	h, err := strconv.Atoi(digits[:2])
	if err != nil {
		return 0, false
	}
	m := 0
	if len(digits) == 4 {
		if m, err = strconv.Atoi(digits[2:]); err != nil {
			return 0, false
		}
	}
	off := h*3600 + m*60
	if z[0] == '-' {
		off = -off
	}
	return off, true
}

func statusLinux(ctx context.Context, deps Deps) (Status, error) {
	runner := deps.runner()
	home, _ := deps.homeDir()
	st := Status{Linger: lingerState(ctx, runner, deps.uid())}

	if p := showTimer(ctx, runner); p != nil && p["LoadState"] != "not-found" {
		st.Mechanism = ScheduleSystemd
		st.UnitPresent = true
		st.UnitFileState = p["UnitFileState"]
		st.Active = p["ActiveState"]
		st.LastRun = parseSystemdTime(p["LastTriggerUSec"], deps.location())
		st.NextRun = parseSystemdTime(p["NextElapseUSecRealtime"], deps.location())
		// KHÔNG lấy mtime của tệp stamp (~/.local/share/systemd/timers/stamp-*.timer) làm lần chạy:
		// với Persistent=true systemd TẠO tệp này ngay khi timer khởi động lần đầu (chưa chạy lần nào), nên
		// mtime = lúc bật lịch ⇒ máy vừa cài sẽ bị coi là "đã chạy mà log trống" (BẬT NHƯNG KHÔNG CHẠY).
		// Lần kích thật đã nằm trong LastTriggerUSec (systemd tự nạp lại từ stamp sau khi khởi động lại).
		st.Enabled = st.UnitFileState == "enabled" && st.Active == "active"
		st.Detail = fmt.Sprintf("systemd --user timer %s.timer: UnitFileState=%s, ActiveState=%s", TaskName, orUnknown(st.UnitFileState), orUnknown(st.Active))
		if home != "" {
			if dir, found := UnitInstallDir(home, TaskName+".service"); found {
				st.Owner = dir
				if self := deps.guardDir(); self != "" {
					_, st.OwnedByOther = otherAlive(dir, self)
				}
			}
		}
		return st, nil
	}

	// Không có unit systemd: thử crontab.
	if out, err := runner.Output(ctx, "crontab", []string{"-l"}); err == nil {
		if strings.Contains(string(out), CrontabMarker) {
			st.Mechanism = ScheduleCron
			st.UnitPresent = true
			st.Enabled = true
			st.Detail = "crontab: đã có dòng tự cập nhật"
			if fi, err := os.Stat(deps.LogFile); deps.LogFile != "" && err == nil {
				st.LastRun = fi.ModTime()
			}
			if dir, found := crontabInstallDir(string(out), CrontabMarker); found {
				st.Owner = dir
				if self := deps.guardDir(); self != "" {
					_, st.OwnedByOther = otherAlive(dir, self)
				}
			}
			return st, nil
		}
		st.Detail = "crontab: không có dòng tự cập nhật (và không có systemd --user timer)"
		return st, nil
	}
	st.Detail = "chưa bật (không có systemd --user timer lẫn dòng crontab)"
	return st, nil
}

func orUnknown(s string) string {
	if s == "" {
		return "không rõ"
	}
	return s
}

// systemdUserAvailable thử một lệnh vô hại (`systemctl --user
// daemon-reload`, an toàn để gọi lặp lại) — thành công nghĩa là session
// systemd --user thật sự hoạt động, không chỉ có binary trên PATH.
func systemdUserAvailable(ctx context.Context, runner Runner, lookPath func(string) (string, error)) bool {
	if _, err := lookPath("systemctl"); err != nil {
		return false
	}
	_, err := runner.Output(ctx, "systemctl", []string{"--user", "daemon-reload"})
	return err == nil
}

func installCrontab(ctx context.Context, runner Runner, content string) error {
	// crontab đọc crontab MỚI từ STDIN — Runner interface ở gói này (Output)
	// không có stdin, nên ghi content ra một tệp tạm rồi trỏ `crontab
	// <tệp>` (tương đương, `crontab -` cũng đọc từ stdin mà nhiều bản
	// crontab không hỗ trợ "-" như một quy ước rõ ràng — dùng tệp để chắc
	// chắn chạy được trên mọi biến thể cron phổ biến: cronie, Vixie cron…).
	tmp, err := os.CreateTemp("", "genh-crontab-*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.WriteString(content); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	_, err = runner.Output(ctx, "crontab", []string{tmp.Name()})
	return err
}
