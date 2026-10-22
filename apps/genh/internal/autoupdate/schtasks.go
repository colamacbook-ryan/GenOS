package autoupdate

import (
	"context"
	"regexp"
	"strings"
	"time"
)

// schtasksInstallDirRe: `--install-dir <dir>` (hoặc `"<dir có khoảng trắng>"`) trong lệnh
// của Task — SchtasksCreateArgs bọc đối số có khoảng trắng bằng "…".
var schtasksInstallDirRe = regexp.MustCompile(`--install-dir(?:=|\s+)(?:"([^"]+)"|([^\s"]+))`)

// schtasksOwner đọc bản cài chủ của Task đã cài từ đầu ra `schtasks /Query /FO LIST /V`
// (v0.1.53, F-98): dòng lệnh của Task (nhãn "Task To Run" — Windows bản địa hoá nhãn nên
// tìm theo chính lệnh `update --yes --quiet`) mang --install-dir; Task do genh trước v0.1.53
// tạo không có ⇒ bản cài mặc định. found=false khi không thấy dòng lệnh nào của genh.
func schtasksOwner(text string) (string, bool) {
	cmd := lineValue(text, "Task To Run")
	if !strings.Contains(cmd, "update --yes --quiet") {
		cmd = ""
		for _, line := range strings.Split(text, "\n") {
			if strings.Contains(line, "update --yes --quiet") {
				cmd = line
				break
			}
		}
	}
	if cmd == "" {
		return "", false
	}
	if m := schtasksInstallDirRe.FindStringSubmatch(cmd); m != nil {
		if m[1] != "" {
			return m[1], true
		}
		return m[2], true
	}
	return defaultOwner()
}

// windowsOwnedByOther: Task đang cài thuộc bản cài KHÁC còn sống (so với deps.guardDir()).
// Không hỏi được Task (chưa có/không đọc được) hoặc không bảo vệ (guardDir rỗng) ⇒ ("", false).
func (d Deps) windowsOwnedByOther(ctx context.Context) (string, bool) {
	self := d.guardDir()
	if self == "" {
		return "", false
	}
	out, err := d.runner().Output(ctx, "schtasks", SchtasksQueryArgs())
	if err != nil {
		return "", false
	}
	owner, found := schtasksOwner(string(out))
	if !found {
		return "", false
	}
	return otherAlive(owner, self)
}

// enableWindows tạo/ghi đè một Scheduled Task chạy `genhPath update --yes
// --quiet [--install-dir …]` hằng ngày lúc 03:00 — /RL LIMITED nên KHÔNG cần
// chạy PowerShell nâng quyền. Task thuộc bản cài khác còn sống ⇒
// *OwnedByOtherError, KHÔNG /F ghi đè (v0.1.53, F-98 — như Linux/macOS).
func enableWindows(ctx context.Context, deps Deps) (EnableResult, error) {
	runner := deps.runner()
	if other, yes := deps.windowsOwnedByOther(ctx); yes {
		return EnableResult{}, &OwnedByOtherError{Other: other, Self: deps.guardDir()}
	}
	if _, err := runner.Output(ctx, "schtasks", SchtasksCreateArgs(deps.GenhPath, deps.LogFile, deps.job())); err != nil {
		return EnableResult{}, err
	}
	return EnableResult{
		Msg:       "Đã bật tự cập nhật hằng đêm lúc ~03:00 (Task Scheduler) — tắt bằng `genh auto-update disable`",
		Mechanism: ScheduleSchtasks,
		Linger:    "not_applicable",
	}, nil
}

func disableWindows(ctx context.Context, deps Deps) (string, error) {
	runner := deps.runner()
	// Lịch dùng chung: Task của bản cài khác còn sống thì KHÔNG xoá (F-98 — `genh uninstall
	// --install-dir <bản phụ>` từng xoá luôn Task của bản chính).
	if other, yes := deps.windowsOwnedByOther(ctx); yes {
		return keptMessage("lịch tự cập nhật đêm", other), nil
	}
	// Bỏ qua lỗi "không tìm thấy task" (chưa từng bật) — idempotent.
	_, _ = runner.Output(ctx, "schtasks", SchtasksDeleteArgs())
	return "Đã tắt tự cập nhật hằng đêm.", nil
}

func statusWindows(ctx context.Context, deps Deps) (Status, error) {
	runner := deps.runner()
	st := Status{Linger: "not_applicable"}
	out, err := runner.Output(ctx, "schtasks", SchtasksQueryArgs())
	if err != nil {
		st.Detail = "chưa bật (không có Scheduled Task)"
		return st, nil
	}
	text := string(out)
	st.Mechanism = ScheduleSchtasks
	st.UnitPresent = true
	st.Enabled = strings.Contains(text, "Ready") || strings.Contains(text, "Running") || !strings.Contains(text, "Disabled")
	if st.Enabled {
		st.Active = "active"
	} else {
		st.Active = "inactive"
	}
	st.LastRun = schtasksTime(lineValue(text, "Last Run Time"))
	st.NextRun = schtasksTime(lineValue(text, "Next Run Time"))
	st.Detail = "Task Scheduler: " + firstLineContaining(text, "Status")
	if owner, found := schtasksOwner(text); found {
		st.Owner = owner
		if self := deps.guardDir(); self != "" {
			_, st.OwnedByOther = otherAlive(owner, self)
		}
	}
	return st, nil
}

// lineValue: giá trị sau "Tên:" của dòng đầu tiên bắt đầu bằng name trong đầu ra
// `schtasks /Query /FO LIST /V` ("" nếu không có).
func lineValue(text, name string) string {
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, name+":") {
			return strings.TrimSpace(strings.TrimPrefix(line, name+":"))
		}
	}
	return ""
}

var schtasksLayouts = []string{
	"1/2/2006 3:04:05 PM", "1/2/2006 15:04:05", "2/1/2006 15:04:05", "02/01/2006 15:04:05",
	"2006-01-02 15:04:05", "2006/01/02 15:04:05", "2.1.2006 15:04:05",
}

// schtasksTime đọc mốc giờ schtasks in theo vùng/ngôn ngữ của Windows (best-effort,
// "N/A" hay định dạng lạ ⇒ zero).
func schtasksTime(s string) time.Time {
	s = strings.TrimSpace(s)
	if s == "" || strings.EqualFold(s, "N/A") {
		return time.Time{}
	}
	for _, layout := range schtasksLayouts {
		if t, err := time.ParseInLocation(layout, s, time.Local); err == nil && t.Year() > 2000 {
			return t // "30/11/1999" = chưa từng chạy
		}
	}
	return time.Time{}
}

func firstLineContaining(text, needle string) string {
	for _, line := range strings.Split(text, "\n") {
		if strings.Contains(line, needle) {
			return strings.TrimSpace(line)
		}
	}
	return strings.TrimSpace(text)
}
