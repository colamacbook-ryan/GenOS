package ops

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/autoupdate"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
)

// Dấu "Sếp đã chủ động tắt lịch đêm" — khuôn y watchdog-disabled.json.
func TestAutoUpdateOptOut(t *testing.T) {
	dir := t.TempDir()
	if AutoUpdateOptedOut(dir) {
		t.Fatal("chưa có dấu")
	}
	// Xoá khi chưa có tệp không phải lỗi.
	if err := SetAutoUpdateOptOut(dir, false, time.Now()); err != nil {
		t.Fatalf("xoá khi chưa có: %v", err)
	}
	at := time.Date(2026, 10, 10, 3, 0, 0, 0, time.UTC)
	if err := SetAutoUpdateOptOut(dir, true, at); err != nil {
		t.Fatal(err)
	}
	path := AutoUpdateOptOutPath(dir)
	if path != filepath.Join(dir, "config", "auto-update-disabled.json") || !AutoUpdateOptedOut(dir) {
		t.Fatalf("path=%s opted=%v", path, AutoUpdateOptedOut(dir))
	}
	fi, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	// Windows không có bit quyền kiểu Unix ⇒ chỉ kiểm 0600 ở Unix.
	if runtime.GOOS != "windows" && fi.Mode().Perm() != 0o600 {
		t.Fatalf("quyền = %v (muốn 0600)", fi.Mode().Perm())
	}
	var m map[string]string
	raw, _ := os.ReadFile(path)
	if err := json.Unmarshal(raw, &m); err != nil || m["at"] != "2026-10-10T03:00:00Z" || len(m) != 1 {
		t.Fatalf("nội dung %s (muốn {at}): %v", raw, err)
	}
	// Ghi lại lần nữa không đổi thời điểm Sếp tắt lần đầu (idempotent).
	if err := SetAutoUpdateOptOut(dir, true, at.Add(48*time.Hour)); err != nil {
		t.Fatal(err)
	}
	if raw2, _ := os.ReadFile(path); string(raw2) != string(raw) {
		t.Fatalf("ghi lặp không được đổi tệp:\n%s\n%s", raw, raw2)
	}
	// Dấu nằm trong config/, KHÔNG trong run/ (api ghi được run/).
	if strings.Contains(path, string(filepath.Separator)+"run"+string(filepath.Separator)) {
		t.Errorf("dấu không được ở run/: %s", path)
	}
	if err := SetAutoUpdateOptOut(dir, false, at); err != nil || AutoUpdateOptedOut(dir) {
		t.Fatalf("enable phải xoá dấu: %v", err)
	}
}

type recAutoRunner struct{ out map[string]string }

func (r recAutoRunner) Output(_ context.Context, name string, args []string) ([]byte, error) {
	line := name + " " + strings.Join(args, " ")
	for k, v := range r.out {
		if strings.Contains(line, k) {
			return []byte(v), nil
		}
	}
	return nil, nil
}

func TestNightlyStatusFrom(t *testing.T) {
	last := time.Date(2026, 10, 9, 3, 7, 0, 0, time.UTC)
	next := time.Date(2026, 10, 10, 3, 21, 0, 0, time.UTC)
	ns := NightlyStatusFrom(autoupdate.Status{Enabled: true, Mechanism: "systemd", UnitPresent: true, Active: "active",
		LastRun: last, NextRun: next, Linger: "yes"}, false, "active")
	if !ns.Enabled || ns.Active == nil || !*ns.Active || ns.Mechanism != "systemd" || !ns.UnitPresent || ns.OptedOut ||
		ns.LastRunAt != "2026-10-09T03:07:00Z" || ns.NextRunAt != "2026-10-10T03:21:00Z" || ns.Linger != "yes" || ns.RequestWatcher != "active" {
		t.Fatalf("%+v", ns)
	}
	// Cron: không có khái niệm active ⇒ null.
	if ns := NightlyStatusFrom(autoupdate.Status{Enabled: true, Mechanism: "cron", UnitPresent: true}, false, "unknown"); ns.Active != nil {
		t.Errorf("cron: active phải nil: %+v", ns)
	}
	if ns.OwnedByOther {
		t.Errorf("lịch của chính bản này: owned_by_other phải false: %+v", ns)
	}
	// Lịch của bản cài KHÁC ⇒ với bản này coi như chưa bật, và ghi owned_by_other để Console
	// không báo "đang tắt" (enable ở bản này bị từ chối).
	if ns := NightlyStatusFrom(autoupdate.Status{Enabled: true, Mechanism: "systemd", UnitPresent: true, OwnedByOther: true, Active: "active"}, false, "active"); ns.Enabled || !ns.OwnedByOther {
		t.Errorf("lịch của bản khác không phải của bản này: %+v", ns)
	}
	// Unit enabled nhưng timer inactive ⇒ active=false.
	ns = NightlyStatusFrom(autoupdate.Status{Mechanism: "systemd", UnitPresent: true, UnitFileState: "enabled", Active: "inactive"}, true, "failed")
	if ns.Enabled || ns.Active == nil || *ns.Active || !ns.OptedOut || ns.RequestWatcher != "failed" {
		t.Errorf("%+v", ns)
	}
}

func TestRecordNightlyStatus_GhiRunNightlyStatusVaGiuKetQua(t *testing.T) {
	dir := t.TempDir()
	rr := recAutoRunner{out: map[string]string{
		"show gen-harness-update.timer":             "LoadState=loaded\nUnitFileState=enabled\nActiveState=active\nLastTriggerUSec=@1760000000\nNextElapseUSecRealtime=@1760086400\n",
		"loginctl show-user":                        "no\n",
		"is-active gen-harness-update-request.path": "failed\n",
	}}
	deps := autoupdate.Deps{Runner: rr, GOOS: "linux", HomeDir: t.TempDir(), UID: "1000"}
	if err := hostlink.RecordNightlyRun(dir, time.Date(2026, 10, 9, 3, 7, 5, 0, time.UTC), hostlink.NightlyResultDone); err != nil {
		t.Fatal(err)
	}
	if err := RecordNightlyStatus(context.Background(), dir, deps); err != nil {
		t.Fatal(err)
	}
	st, err := hostlink.ReadNightlyStatus(dir)
	if err != nil {
		t.Fatal(err)
	}
	if st.Mechanism != "systemd" || !st.Enabled || st.Linger != "no" || st.RequestWatcher != "failed" || st.OptedOut {
		t.Fatalf("%+v", st)
	}
	// Mốc lần chạy do lịch đêm tự ghi tin hơn mốc systemd: giữ nguyên, không bị đè.
	if st.LastRunAt != "2026-10-09T03:07:05Z" || st.LastResult != "done" {
		t.Fatalf("không được đụng last_run_at/last_result: %+v", st)
	}
	if st.NextRunAt != "2025-10-10T08:53:20Z" { // @1760086400
		t.Errorf("next_run_at = %q", st.NextRunAt)
	}

	// Sếp đã tắt ⇒ opted_out=true.
	if err := SetAutoUpdateOptOut(dir, true, time.Now()); err != nil {
		t.Fatal(err)
	}
	_ = RecordNightlyStatus(context.Background(), dir, deps)
	if st, _ := hostlink.ReadNightlyStatus(dir); !st.OptedOut {
		t.Errorf("opted_out phải true: %+v", st)
	}
}

// v0.1.54: khối watcher của run/nightly-status.json lấy từ tình trạng người gác (chỉ đọc).
func TestWatcherInfoFrom_VaRecordNightlyStatus(t *testing.T) {
	if w := WatcherInfoFrom(autoupdate.WatcherHealth{}); w != (hostlink.NightlyWatcher{State: "ok"}) {
		t.Errorf("khoẻ: %+v", w)
	}
	w := WatcherInfoFrom(autoupdate.WatcherHealth{State: autoupdate.WatcherHealthFallback, Reason: autoupdate.WatcherReasonInotify})
	if w.State != "fallback" || w.Reason != "inotify" || !strings.Contains(w.Hint, "inotify") || !strings.Contains(w.Hint, "sysctl") {
		t.Errorf("dự phòng: %+v", w)
	}

	// .path failed + có timer dự phòng đang chạy ⇒ nightly-status.json ghi watcher.state=fallback.
	dir := t.TempDir()
	home := t.TempDir()
	unitDir := filepath.Join(home, ".config", "systemd", "user")
	if err := os.MkdirAll(unitDir, 0o755); err != nil {
		t.Fatal(err)
	}
	_ = os.WriteFile(filepath.Join(unitDir, autoupdate.RequestTaskName+".path"), []byte("[Path]\n"), 0o644)
	_ = os.WriteFile(filepath.Join(unitDir, autoupdate.RequestFallbackTimer), []byte(autoupdate.RequestFallbackTimerUnit("inotify")), 0o644)
	rr := recAutoRunner{out: map[string]string{
		"show gen-harness-update.timer":              "LoadState=loaded\nUnitFileState=enabled\nActiveState=active\nLastTriggerUSec=@1760000000\nNextElapseUSecRealtime=@1760086400\n",
		"loginctl show-user":                         "yes\n",
		"is-active gen-harness-update-request.path":  "failed\n",
		"is-active gen-harness-update-request.timer": "active\n",
	}}
	deps := autoupdate.Deps{Runner: rr, GOOS: "linux", HomeDir: home, UID: "1000"}
	if err := RecordNightlyStatus(context.Background(), dir, deps); err != nil {
		t.Fatal(err)
	}
	st, err := hostlink.ReadNightlyStatus(dir)
	if err != nil {
		t.Fatal(err)
	}
	if st.Watcher.State != "fallback" || st.Watcher.Reason != "inotify" || st.RequestWatcher != "failed" {
		t.Fatalf("watcher = %+v, request_watcher = %q", st.Watcher, st.RequestWatcher)
	}
}
