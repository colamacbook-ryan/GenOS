package ops

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/autoupdate"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/config"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
)

// AutoUpdateOptOutFile: <gốc cài đặt>/config/auto-update-disabled.json — Sếp đã
// CHỦ ĐỘNG tắt lịch tự cập nhật đêm (`genh auto-update disable` hoặc
// `genh install --no-auto-update`). Khuôn y WatchdogOptOutFile. Có tệp này thì
// genh KHÔNG tự lành (autoupdate.EnsureNightly) lịch đêm; `genh auto-update
// enable` và `genh install` không cờ xoá nó. Nằm trong config/ (chỉ genh ghi),
// KHÔNG trong run/ (api ghi được).
const AutoUpdateOptOutFile = "auto-update-disabled.json"

// AutoUpdateOptOutPath là đường dẫn tệp đánh dấu Sếp tắt lịch đêm.
func AutoUpdateOptOutPath(installDir string) string {
	return filepath.Join(installDir, "config", AutoUpdateOptOutFile)
}

// AutoUpdateOptedOut báo Sếp có đang chủ động tắt lịch tự cập nhật đêm không.
func AutoUpdateOptedOut(installDir string) bool {
	_, err := os.Lstat(AutoUpdateOptOutPath(installDir))
	return err == nil
}

// SetAutoUpdateOptOut ghi (on=true, {at} 0600) hoặc xoá (on=false — không có tệp
// không phải lỗi) đánh dấu Sếp tắt lịch đêm. Ghi lặp lại (đã có tệp) không đổi
// gì để giữ nguyên thời điểm Sếp tắt lần đầu.
func SetAutoUpdateOptOut(installDir string, on bool, at time.Time) error {
	path := AutoUpdateOptOutPath(installDir)
	if !on {
		if err := os.Remove(path); err != nil && !errors.Is(err, os.ErrNotExist) {
			return err
		}
		return nil
	}
	if AutoUpdateOptedOut(installDir) {
		return nil
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	b, _ := json.Marshal(map[string]string{"at": at.UTC().Format(time.RFC3339)})
	return os.WriteFile(path, append(b, '\n'), 0o600)
}

// NightlyDeps dựng autoupdate.Deps ĐỌC TRẠNG THÁI lịch đêm của bản cài (không cần
// đường dẫn genh): dùng cho lượt trực canh và `genh auto-update status`.
func NightlyDeps(installDir string) autoupdate.Deps {
	return autoupdate.Deps{
		InstallDir: installDir,
		LogFile:    filepath.Join(config.New(installDir).LogsDir(), "auto-update.log"),
	}
}

// NightlyStatusFrom chuyển autoupdate.Status thành run/nightly-status.json. Lịch
// thuộc bản cài KHÁC còn sống (st.OwnedByOther) thì với bản cài này coi như chưa
// bật — lịch đó cập nhật bản kia, không phải bản này — và ghi owned_by_other để
// Console không báo "đang tắt" mãi với lối ra là lệnh bị từ chối.
func NightlyStatusFrom(st autoupdate.Status, optedOut bool, watcher string) hostlink.NightlyStatus {
	ns := hostlink.NightlyStatus{
		Mechanism:      st.Mechanism,
		Enabled:        st.Enabled && !st.OwnedByOther,
		UnitPresent:    st.UnitPresent,
		OptedOut:       optedOut,
		OwnedByOther:   st.OwnedByOther,
		Linger:         st.Linger,
		RequestWatcher: watcher,
	}
	if st.Mechanism == autoupdate.ScheduleSystemd {
		active := st.Active == "active"
		ns.Active = &active
	}
	if !st.LastRun.IsZero() {
		ns.LastRunAt = st.LastRun.UTC().Format(time.RFC3339)
	}
	if !st.NextRun.IsZero() {
		ns.NextRunAt = st.NextRun.UTC().Format(time.RFC3339)
	}
	return ns
}

// WatcherInfoFrom chuyển tình trạng người gác (autoupdate.WatcherHealth) thành khối `watcher`
// của run/nightly-status.json (v0.1.54). Khoẻ ⇒ {state: ok, reason: "", hint: ""}.
func WatcherInfoFrom(h autoupdate.WatcherHealth) hostlink.NightlyWatcher {
	switch h.State {
	case autoupdate.WatcherHealthFallback:
		return hostlink.NightlyWatcher{State: hostlink.WatcherStateFallback, Reason: h.Reason, Hint: h.Hint()}
	case autoupdate.WatcherHealthFailed:
		return hostlink.NightlyWatcher{State: hostlink.WatcherStateFailed, Reason: h.Reason, Hint: h.Hint()}
	}
	return hostlink.NightlyWatcher{State: hostlink.WatcherStateOK}
}

// SaveNightlyStatus ghi run/nightly-status.json từ ảnh chụp st (giữ since/last_run_at/last_result
// đã có — xem hostlink.WriteNightlyStatus). Mốc lần chạy do lịch đêm tự ghi (RecordNightlyRun) tin
// hơn mốc systemd/log: chỉ lấy từ ảnh chụp khi genh chưa có mốc nào.
func SaveNightlyStatus(installDir string, st autoupdate.Status, optedOut bool, watcher string, health autoupdate.WatcherHealth) error {
	ns := NightlyStatusFrom(st, optedOut, watcher)
	ns.Watcher = WatcherInfoFrom(health)
	if prev, err := hostlink.ReadNightlyStatus(installDir); err == nil && prev.LastRunAt != "" {
		ns.LastRunAt = ""
	}
	return hostlink.WriteNightlyStatus(installDir, ns)
}

// RecordNightlyStatus đọc trạng thái lịch đêm + trình nhận yêu cầu và ghi
// run/nightly-status.json (SaveNightlyStatus). Lỗi đọc trạng thái ⇒ trả lỗi, không ghi gì.
func RecordNightlyStatus(ctx context.Context, installDir string, deps autoupdate.Deps) error {
	if deps.InstallDir == "" {
		deps.InstallDir = installDir
	}
	st, err := autoupdate.GetStatus(ctx, deps)
	if err != nil {
		return err
	}
	return SaveNightlyStatus(installDir, st, AutoUpdateOptedOut(installDir), autoupdate.RequestWatcherState(ctx, deps),
		autoupdate.RequestWatcherHealth(ctx, deps))
}
