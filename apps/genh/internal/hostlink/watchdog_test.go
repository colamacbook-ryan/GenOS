package hostlink

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
	"unicode/utf8"
)

func TestReadAPIHealth_LocVaGioiHan(t *testing.T) {
	root := t.TempDir()
	if err := EnsureDir(root); err != nil {
		t.Fatal(err)
	}
	if _, err := ReadAPIHealth(root); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("chưa có tệp phải ErrNotExist: %v", err)
	}
	alerts := []map[string]any{}
	for i := 0; i < 40; i++ {
		alerts = append(alerts, map[string]any{
			"key": fmt.Sprintf("channel.down:%d", i), "kind": "channel", "severity": "bad",
			"title": strings.Repeat("Tiêu đề dài ", 40) + "\x1b[31m\n", "body": "dòng 1\ndòng 2‮" + strings.Repeat("x", 600),
			"fingerprint": "fp", "raised_at": "2026-10-03T00:00:00Z",
		})
	}
	alerts = append([]map[string]any{{"key": "", "title": "không key"}, {"key": "model.auth_expired:7", "severity": "lạ", "title": ""}}, alerts...)
	latest := "2026-10-02T00:00:00Z"
	raw, _ := json.Marshal(map[string]any{
		"schema": 1, "written_at": "2026-10-03T00:00:00Z", "version": "v0.1.44", "public_url": "https://gh.example.com/",
		"alerts": alerts, "latest_backup_at": latest, "backup_stale_limit_hours": 36,
	})
	if err := os.WriteFile(APIHealthPath(root), raw, 0o666); err != nil {
		t.Fatal(err)
	}
	h, err := ReadAPIHealth(root)
	if err != nil {
		t.Fatal(err)
	}
	if len(h.Alerts) != maxAPIAlerts {
		t.Fatalf("tối đa %d alert, được %d", maxAPIAlerts, len(h.Alerts))
	}
	if h.Alerts[0].Key != "model.auth_expired:7" || h.Alerts[0].Severity != "warn" || h.Alerts[0].Title != "model.auth_expired:7" {
		t.Fatalf("alert đầu (bỏ alert không key; severity lạ → warn; title rỗng → key) = %+v", h.Alerts[0])
	}
	a := h.Alerts[1]
	if utf8.RuneCountInString(a.Title) > maxAlertTitle || utf8.RuneCountInString(a.Body) > maxAlertBody {
		t.Fatalf("chưa cắt: title %d, body %d", utf8.RuneCountInString(a.Title), utf8.RuneCountInString(a.Body))
	}
	for _, s := range []string{a.Title, a.Body} {
		if strings.ContainsAny(s, "\x1b\n\r‮") {
			t.Fatalf("còn ký tự điều khiển: %q", s)
		}
	}
	if h.PublicURL != "https://gh.example.com" || h.LatestBackupAt == nil || *h.LatestBackupAt != latest || h.BackupStaleLimitHours != 36 {
		t.Fatalf("api-health = %+v", h)
	}
	// public_url lạ (javascript:, khoảng trắng) bị bỏ.
	raw2, _ := json.Marshal(map[string]any{"schema": 1, "public_url": "javascript:alert(1)"})
	_ = os.WriteFile(APIHealthPath(root), raw2, 0o666)
	if h, _ := ReadAPIHealth(root); h.PublicURL != "" {
		t.Fatalf("public_url không phải http(s) phải bị bỏ: %q", h.PublicURL)
	}
}

func TestReadAPIHealth_SymlinkBiTuChoi(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink cần quyền đặc biệt trên Windows")
	}
	root := t.TempDir()
	_ = EnsureDir(root)
	secret := filepath.Join(t.TempDir(), "id_rsa")
	_ = os.WriteFile(secret, []byte(`{"schema":1,"public_url":"https://bimat"}`), 0o600)
	if err := os.Symlink(secret, APIHealthPath(root)); err != nil {
		t.Fatal(err)
	}
	if _, err := ReadAPIHealth(root); err == nil {
		t.Fatal("symlink phải bị từ chối")
	}
	if err := os.Symlink(secret, TelegramConfigPath(root)); err != nil {
		t.Fatal(err)
	}
	if _, err := ReadTelegramConfigFile(root); err == nil {
		t.Fatal("telegram.json symlink phải bị từ chối")
	}
}

func TestWatchdogStatus_GhiDungKhoaHopDong(t *testing.T) {
	root := t.TempDir()
	if err := WriteWatchdogStatus(root, WatchdogStatus{LastRunAt: "2026-10-03T00:00:00Z", State: WatchdogStateOK, Telegram: TelegramNotConfigured}); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(WatchdogStatusPath(root))
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"schema", "last_run_at", "state", "incidents", "telegram", "telegram_error_code", "last_sent_at", "schedule", "test"} {
		if _, ok := m[k]; !ok {
			t.Errorf("thiếu khoá %q: %s", k, b)
		}
	}
	if inc, ok := m["incidents"].([]any); !ok || len(inc) != 0 {
		t.Fatalf("incidents phải là mảng rỗng (không null): %s", b)
	}
	if m["test"] != nil {
		t.Fatalf("test phải null khi chưa gửi thử: %s", b)
	}
	if err := SetWatchdogSchedule(root, "cron"); err != nil {
		t.Fatal(err)
	}
	st, err := ReadWatchdogStatus(root)
	if err != nil || st.Schedule != "cron" || st.State != WatchdogStateOK || st.Schema != 1 {
		t.Fatalf("SetWatchdogSchedule phải giữ phần còn lại: %+v, %v", st, err)
	}
}

func TestWatchdogRequest_ConsumeXoaTep(t *testing.T) {
	root := t.TempDir()
	_ = EnsureDir(root)
	if HasWatchdogRequest(root) {
		t.Fatal("chưa có yêu cầu")
	}
	_ = os.WriteFile(WatchdogRequestPath(root), []byte(`{"schema":1,"action":"test","requested_at":"x"}`), 0o666)
	if !HasWatchdogRequest(root) {
		t.Fatal("phải thấy yêu cầu")
	}
	r, err := ConsumeWatchdogRequest(root)
	if err != nil || r.Action != "test" || HasWatchdogRequest(root) {
		t.Fatalf("Consume = %+v, %v (phải xoá tệp)", r, err)
	}
	_ = os.WriteFile(WatchdogRequestPath(root), []byte(`{"action":"rm -rf"}`), 0o666)
	if _, err := ConsumeWatchdogRequest(root); err == nil || HasWatchdogRequest(root) {
		t.Fatal("action lạ phải lỗi và tệp bị xoá")
	}
}

func TestWatchdogLock_KhongChongNhau(t *testing.T) {
	root := t.TempDir()
	l, err := AcquireWatchdogLock(root)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := AcquireWatchdogLock(root); !errors.Is(err, ErrLockBusy) {
		t.Fatalf("lượt thứ hai phải bận: %v", err)
	}
	l.Release()
	if ExclusiveLockBusy(root) {
		t.Fatal("chưa ai giữ genh.lock")
	}
	ex, err := AcquireLock(root)
	if err != nil {
		t.Fatal(err)
	}
	defer ex.Release()
	if !ExclusiveLockBusy(root) {
		t.Fatal("genh.lock đang bị giữ phải báo bận")
	}
}

func TestAcquireWatchdogLockWait_ChoLuotDangChay(t *testing.T) {
	old := lockRetryEvery
	lockRetryEvery = 10 * time.Millisecond
	defer func() { lockRetryEvery = old }()
	root := t.TempDir()
	held, err := AcquireWatchdogLock(root)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := AcquireWatchdogLockWait(context.Background(), root, 30*time.Millisecond); !errors.Is(err, ErrLockBusy) {
		t.Fatalf("hết hạn chờ phải trả bận: %v", err)
	}
	go func() {
		time.Sleep(40 * time.Millisecond)
		held.Release()
	}()
	l, err := AcquireWatchdogLockWait(context.Background(), root, 5*time.Second)
	if err != nil {
		t.Fatalf("lượt kia nhả khoá thì phải lấy được: %v", err)
	}
	l.Release()
}
