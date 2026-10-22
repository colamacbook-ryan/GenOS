package autoupdate

import (
	"context"
	"fmt"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// enableDarwin ghi plist LaunchAgent rồi `launchctl load -w` — dùng cú pháp
// "load/unload" cổ điển (không phải "bootstrap/bootout gui/<uid>") vì nó
// hoạt động nhất quán trên mọi phiên bản macOS còn phổ biến mà không cần tự
// dò UID người dùng hiện tại.
func enableDarwin(ctx context.Context, deps Deps) (EnableResult, error) {
	home, err := deps.homeDir()
	if err != nil {
		return EnableResult{}, fmt.Errorf("không xác định được thư mục home: %w", err)
	}
	runner := deps.runner()

	dir := launchAgentDir(home)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return EnableResult{}, fmt.Errorf("tạo %s: %w", dir, err)
	}
	path := launchAgentPath(home)
	if err := deps.guardDarwin(path); err != nil {
		return EnableResult{}, err
	}

	// Gỡ bản cũ trước (nếu có) để `load` không báo "đã tải" khi Enable gọi
	// lại (ví dụ Owner đổi ý bật lại sau khi disable, hoặc genh install gọi
	// Enable lần hai) — bỏ qua lỗi (chưa từng load thì unload thất bại,
	// không sao).
	_, _ = runner.Output(ctx, "launchctl", []string{"unload", path})

	minute := parseMinuteFromClock(time.Now().Format("15:04:05"))
	plist := LaunchdPlist(deps.GenhPath, deps.LogFile, minute, deps.job())
	if err := os.WriteFile(path, []byte(plist), 0o644); err != nil {
		return EnableResult{}, fmt.Errorf("ghi %s: %w", path, err)
	}
	if _, err := runner.Output(ctx, "launchctl", []string{"load", "-w", path}); err != nil {
		return EnableResult{}, fmt.Errorf("launchctl load -w %s: %w", path, err)
	}
	return EnableResult{
		Msg:       "Đã bật tự cập nhật hằng đêm lúc ~03:00 (LaunchAgent) — tắt bằng `genh auto-update disable`",
		Mechanism: ScheduleLaunchd,
		Linger:    "not_applicable",
	}, nil
}

func disableDarwin(ctx context.Context, deps Deps) (string, error) {
	home, err := deps.homeDir()
	if err != nil {
		return "", fmt.Errorf("không xác định được thư mục home: %w", err)
	}
	runner := deps.runner()
	path := launchAgentPath(home)

	if err := deps.guardDarwin(path); err != nil {
		if other, ok := OwnerOf(err); ok {
			return keptMessage("lịch tự cập nhật đêm", other), nil
		}
	}
	_, _ = runner.Output(ctx, "launchctl", []string{"unload", path})
	_ = os.Remove(path)
	return "Đã tắt tự cập nhật hằng đêm.", nil
}

var plistMinuteRe = regexp.MustCompile(`(?s)<key>Minute</key>\s*<integer>(\d+)</integer>`)

func statusDarwin(ctx context.Context, deps Deps) (Status, error) {
	home, err := deps.homeDir()
	if err != nil {
		return Status{}, fmt.Errorf("không xác định được thư mục home: %w", err)
	}
	st := Status{Linger: "not_applicable"}
	b, err := os.ReadFile(launchAgentPath(home))
	if err != nil {
		st.Detail = "chưa bật (không có LaunchAgent)"
		return st, nil
	}
	st.Mechanism = ScheduleLaunchd
	st.UnitPresent = true
	if dir, ok := installDirOfText(string(b)); ok {
		st.Owner = dir
		if self := deps.guardDir(); self != "" {
			_, st.OwnedByOther = otherAlive(dir, self)
		}
	}
	if fi, err := os.Stat(deps.LogFile); deps.LogFile != "" && err == nil {
		st.LastRun = fi.ModTime()
	}
	if m := plistMinuteRe.FindStringSubmatch(string(b)); m != nil {
		if minute, err := strconv.Atoi(m[1]); err == nil {
			st.NextRun = nextDaily(deps.now(), 3, minute, deps.location())
		}
	}
	runner := deps.runner()
	out, err := runner.Output(ctx, "launchctl", []string{"list"})
	if err != nil {
		st.Active = "inactive"
		st.Detail = "có tệp LaunchAgent nhưng `launchctl list` lỗi: " + err.Error()
		return st, nil
	}
	if strings.Contains(string(out), launchAgentLabel()) {
		st.Enabled, st.Active = true, "active"
		st.Detail = "LaunchAgent: đã nạp"
		return st, nil
	}
	st.Active = "inactive"
	st.Detail = "có tệp LaunchAgent nhưng chưa nạp (launchctl load)"
	return st, nil
}

// nextDaily: lần kế tiếp (sau now) đồng hồ treo giờ hour:minute theo múi giờ loc.
func nextDaily(now time.Time, hour, minute int, loc *time.Location) time.Time {
	n := now.In(loc)
	t := time.Date(n.Year(), n.Month(), n.Day(), hour, minute, 0, 0, loc)
	if !t.After(n) {
		t = t.AddDate(0, 0, 1)
	}
	return t
}
