package hostlink

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"
)

// ─── Trực canh máy chủ (v0.1.44, F-6b) ──────────────────────────────────────
//
//	run/telegram.json          ← api ghi (0644): cấu hình "Báo động & bản tin" — token
//	                             MÃ HOÁ phong bì GH1 bằng khoá master (genh giải bằng
//	                             secrets/gh_master_key, internal/notify)
//	run/api-health.json        ← api ghi mỗi lượt health watch (~60 giây): sự cố phía api
//	run/watchdog-status.json   ← genh ghi (nguyên tử) mỗi lượt `genh doctor --notify`
//	run/request/watchdog.json  ← api ghi khi Owner bấm "Gửi thử"; genh XOÁ trước khi làm
//
// State chống spam KHÔNG nằm ở đây mà ở <gốc cài đặt>/config/watchdog-state.json
// (0600, chỉ genh đọc) — api ghi được run/ nên mọi thứ đọc từ run/ đều KHÔNG tin cậy.

// Tên tệp trong run/ (hợp đồng với apps/api — giữ đúng chữ).
const (
	TelegramConfigFile  = "telegram.json"
	APIHealthFile       = "api-health.json"
	WatchdogStatusFile  = "watchdog-status.json"
	WatchdogRequestFile = "watchdog.json"
)

// WatchdogStatusSchema là phiên bản khuôn watchdog-status.json.
const WatchdogStatusSchema = 1

// WatchdogLockFile là khoá RIÊNG của trực canh (<gốc cài đặt>/watchdog.lock) —
// không phải khoá loại trừ genh.lock: hai lượt trực canh không chồng nhau, nhưng
// trực canh không chặn update/restore.
const WatchdogLockFile = "watchdog.lock"

// Giới hạn khi đọc api-health.json (api là nguồn KHÔNG tin cậy với genh).
const (
	maxAPIAlerts     = 30
	maxAlertTitle    = 160
	maxAlertBody     = 400
	maxAlertKey      = 120
	maxAlertShortStr = 64
	maxPublicURL     = 300
)

// TelegramConfigPath là đường dẫn run/telegram.json.
func TelegramConfigPath(installDir string) string {
	return filepath.Join(Dir(installDir), TelegramConfigFile)
}

// ReadTelegramConfigFile đọc run/telegram.json AN TOÀN (readStateFile — không
// theo symlink, ≤ 64 KiB). Không có tệp → lỗi bọc os.ErrNotExist.
func ReadTelegramConfigFile(installDir string) ([]byte, error) {
	return readStateFile(TelegramConfigPath(installDir), false)
}

// APIAlert là một sự cố phía api trong api-health.json (đã lọc).
type APIAlert struct {
	Key         string `json:"key"`
	Kind        string `json:"kind"`
	Severity    string `json:"severity"` // bad | warn
	Title       string `json:"title"`
	Body        string `json:"body"`
	Fingerprint string `json:"fingerprint"`
	RaisedAt    string `json:"raised_at"`
}

// APIHealth là nội dung run/api-health.json (hợp đồng với apps/api).
type APIHealth struct {
	Schema                int        `json:"schema"`
	WrittenAt             string     `json:"written_at"`
	Version               string     `json:"version"`
	PublicURL             string     `json:"public_url"`
	Alerts                []APIAlert `json:"alerts"`
	LatestBackupAt        *string    `json:"latest_backup_at"`
	BackupStaleLimitHours int        `json:"backup_stale_limit_hours"`
}

// APIHealthPath là đường dẫn run/api-health.json.
func APIHealthPath(installDir string) string { return filepath.Join(Dir(installDir), APIHealthFile) }

// ReadAPIHealth đọc run/api-health.json AN TOÀN và LỌC: bỏ ký tự điều khiển,
// cắt title ≤ 160 / body ≤ 400 rune, tối đa 30 alert, bỏ alert không có key,
// public_url chỉ nhận http(s) không khoảng trắng.
func ReadAPIHealth(installDir string) (APIHealth, error) {
	var h APIHealth
	b, err := readStateFile(APIHealthPath(installDir), false)
	if err != nil {
		return h, err
	}
	if err := json.Unmarshal(b, &h); err != nil {
		return APIHealth{}, err
	}
	h.WrittenAt = cleanText(h.WrittenAt, maxAlertShortStr)
	h.Version = cleanText(h.Version, maxAlertShortStr)
	h.PublicURL = cleanURL(h.PublicURL)
	if h.LatestBackupAt != nil {
		v := cleanText(*h.LatestBackupAt, maxAlertShortStr)
		h.LatestBackupAt = &v
	}
	if h.BackupStaleLimitHours < 0 || h.BackupStaleLimitHours > 24*365 {
		h.BackupStaleLimitHours = 0
	}
	alerts := make([]APIAlert, 0, len(h.Alerts))
	for _, a := range h.Alerts {
		if len(alerts) >= maxAPIAlerts {
			break
		}
		a.Key = cleanText(a.Key, maxAlertKey)
		if a.Key == "" {
			continue
		}
		a.Kind = cleanText(a.Kind, maxAlertShortStr)
		a.Severity = cleanText(a.Severity, maxAlertShortStr)
		if a.Severity != "bad" {
			a.Severity = "warn"
		}
		a.Title = cleanText(a.Title, maxAlertTitle)
		if a.Title == "" {
			a.Title = a.Key
		}
		a.Body = cleanText(a.Body, maxAlertBody)
		a.Fingerprint = cleanText(a.Fingerprint, maxAlertTitle)
		a.RaisedAt = cleanText(a.RaisedAt, maxAlertShortStr)
		alerts = append(alerts, a)
	}
	h.Alerts = alerts
	return h, nil
}

// cleanText bỏ ký tự điều khiển (đổi xuống dòng/tab thành khoảng trắng), gộp
// khoảng trắng thừa, cắt còn ≤ max rune (thêm "…").
func cleanText(s string, max int) string {
	if !utf8.ValidString(s) {
		s = strings.ToValidUTF8(s, "")
	}
	var b strings.Builder
	space := false
	for _, r := range s {
		if unicode.IsControl(r) || unicode.IsSpace(r) || r == ' ' || r == ' ' {
			if !space && b.Len() > 0 {
				b.WriteByte(' ')
			}
			space = true
			continue
		}
		if unicode.In(r, unicode.Cf) { // ký tự định dạng vô hình (bidi override…)
			continue
		}
		b.WriteRune(r)
		space = false
	}
	out := strings.TrimSpace(b.String())
	if utf8.RuneCountInString(out) > max {
		rs := []rune(out)
		out = strings.TrimSpace(string(rs[:max-1])) + "…"
	}
	return out
}

func cleanURL(s string) string {
	s = strings.TrimSpace(s)
	if len(s) > maxPublicURL || !(strings.HasPrefix(s, "https://") || strings.HasPrefix(s, "http://")) {
		return ""
	}
	for _, r := range s {
		if r <= ' ' || r == 0x7f || r == '"' || r == '<' || r == '>' || r > 0x7e {
			return ""
		}
	}
	return strings.TrimRight(s, "/")
}

// WatchdogIncident là một sự cố đang mở trong watchdog-status.json.
type WatchdogIncident struct {
	Key      string `json:"key"`
	Severity string `json:"severity"`
	Title    string `json:"title"`
	Since    string `json:"since"`
}

// WatchdogTest là kết quả "Gửi thử" gần nhất.
type WatchdogTest struct {
	At        string `json:"at"`
	OK        bool   `json:"ok"`
	ErrorCode string `json:"error_code"`
}

// Giá trị "state" của watchdog-status.json.
const (
	WatchdogStateOK          = "ok"
	WatchdogStateIssues      = "issues"
	WatchdogStatePaused      = "paused"
	WatchdogStateSkippedBusy = "skipped_busy"
	WatchdogStateError       = "error"
)

// Giá trị "telegram" của watchdog-status.json.
const (
	TelegramOK            = "ok"
	TelegramNotConfigured = "not_configured"
	TelegramDisabled      = "disabled"
	TelegramFailed        = "failed"
	TelegramKeyMismatch   = "key_mismatch"
)

// WatchdogStatus là nội dung run/watchdog-status.json — KHÔNG omitempty: api
// đọc đủ mọi khoá (chuỗi rỗng = chưa có; test null = chưa gửi thử).
type WatchdogStatus struct {
	Schema            int                `json:"schema"`
	LastRunAt         string             `json:"last_run_at"`
	State             string             `json:"state"`
	Incidents         []WatchdogIncident `json:"incidents"`
	Telegram          string             `json:"telegram"`
	TelegramErrorCode string             `json:"telegram_error_code"`
	LastSentAt        string             `json:"last_sent_at"`
	Schedule          string             `json:"schedule"` // systemd | cron | launchd | schtasks | ""
	Test              *WatchdogTest      `json:"test"`
}

// WatchdogStatusPath là đường dẫn run/watchdog-status.json.
func WatchdogStatusPath(installDir string) string {
	return filepath.Join(Dir(installDir), WatchdogStatusFile)
}

// WriteWatchdogStatus ghi nguyên tử run/watchdog-status.json (0644 để api đọc).
func WriteWatchdogStatus(installDir string, st WatchdogStatus) error {
	if err := EnsureDir(installDir); err != nil {
		return err
	}
	st.Schema = WatchdogStatusSchema
	if st.Incidents == nil {
		st.Incidents = []WatchdogIncident{}
	}
	return writeJSON(WatchdogStatusPath(installDir), st)
}

// ReadWatchdogStatus đọc run/watchdog-status.json AN TOÀN (readStateFile).
func ReadWatchdogStatus(installDir string) (WatchdogStatus, error) {
	var st WatchdogStatus
	b, err := readStateFile(WatchdogStatusPath(installDir), false)
	if err != nil {
		return st, err
	}
	return st, json.Unmarshal(b, &st)
}

// SetWatchdogSchedule chỉ đổi khoá "schedule" (giữ phần còn lại; chưa có tệp
// thì ghi mới với state rỗng).
func SetWatchdogSchedule(installDir, schedule string) error {
	st, err := ReadWatchdogStatus(installDir)
	if err != nil {
		st = WatchdogStatus{}
	}
	st.Schedule = schedule
	return WriteWatchdogStatus(installDir, st)
}

// WatchdogRequest là nội dung run/request/watchdog.json (KHÔNG tin cậy).
type WatchdogRequest struct {
	Schema      int    `json:"schema"`
	Action      string `json:"action"` // test
	RequestedAt string `json:"requested_at"`
}

// WatchdogRequestPath là đường dẫn run/request/watchdog.json.
func WatchdogRequestPath(installDir string) string {
	return filepath.Join(RequestDirPath(installDir), WatchdogRequestFile)
}

// HasWatchdogRequest: có yêu cầu "Gửi thử" không (Lstat — symlink cũng tính
// để genh dọn nó đi).
func HasWatchdogRequest(installDir string) bool {
	_, err := os.Lstat(WatchdogRequestPath(installDir))
	return err == nil
}

// ConsumeWatchdogRequest đọc AN TOÀN rồi XOÁ run/request/watchdog.json (xoá cả
// khi tệp hỏng/không an toàn — watcher không kích lặp). Chỉ nhận action "test".
func ConsumeWatchdogRequest(installDir string) (WatchdogRequest, error) {
	var r WatchdogRequest
	path := WatchdogRequestPath(installDir)
	b, err := readRequestFile(installDir, path)
	_ = os.Remove(path)
	if err != nil {
		return r, err
	}
	if err := json.Unmarshal(b, &r); err != nil {
		return r, err
	}
	if r.Action != "test" {
		return r, fmt.Errorf("yêu cầu trực canh không rõ action %q", cleanText(r.Action, 32))
	}
	return r, nil
}

// AcquireWatchdogLock thử lấy khoá riêng của trực canh NGAY (không chờ). Bận →
// *LockBusyError; lỗi khác trả nguyên.
func AcquireWatchdogLock(installDir string) (*Lock, error) {
	path := filepath.Join(installDir, WatchdogLockFile)
	f, err := openLockFile(path)
	if err != nil {
		return nil, fmt.Errorf("mở %s: %w", path, err)
	}
	busy, err := tryLock(f)
	if err != nil {
		_ = f.Close()
		return nil, fmt.Errorf("khoá %s: %w", path, err)
	}
	if busy {
		_ = f.Close()
		return nil, &LockBusyError{}
	}
	return &Lock{f: f}, nil
}

// AcquireWatchdogLockWait thử lấy khoá trực canh mỗi lockRetryEvery cho tới khi
// được, hết max (trả lỗi bận) hoặc ctx bị huỷ (trả ctx.Err()). Dùng khi việc
// Owner chủ động (genh stop/uninstall, "Gửi thử") phải chờ lượt đang chạy xong
// thay vì bỏ qua.
func AcquireWatchdogLockWait(ctx context.Context, installDir string, max time.Duration) (*Lock, error) {
	deadline := time.Now().Add(max)
	for {
		l, err := AcquireWatchdogLock(installDir)
		if err == nil || !errors.Is(err, ErrLockBusy) {
			return l, err
		}
		wait := time.Until(deadline)
		if wait <= 0 {
			return nil, err
		}
		if wait > lockRetryEvery {
			wait = lockRetryEvery
		}
		t := time.NewTimer(wait)
		select {
		case <-ctx.Done():
			t.Stop()
			return nil, ctx.Err()
		case <-t.C:
		}
	}
}

// ExclusiveLockBusy báo khoá loại trừ chung (genh.lock — update/restore/import)
// có đang bị giữ không: thử lấy rồi NHẢ NGAY (trực canh chỉ đọc, không giữ khoá
// trong lúc đo để không chặn lịch đêm).
func ExclusiveLockBusy(installDir string) bool {
	l, err := AcquireLock(installDir)
	if err != nil {
		return errors.Is(err, ErrLockBusy)
	}
	l.Release()
	return false
}
