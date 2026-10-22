package hostlink

import (
	"encoding/json"
	"path/filepath"
	"time"
)

// ─── Trạng thái lịch tự cập nhật đêm (v0.1.53, F-99) ────────────────────────
//
//	run/nightly-status.json  ← genh ghi (nguyên tử) mỗi lần publishHostInfo
//	                           (install/update), mỗi lần lịch đêm chạy (đầu + cuối) và mỗi
//	                           lượt trực canh 12 phút — để Console thấy lịch đêm im lặng kể cả
//	                           khi không ai chạy genh. Không chứa lệnh hay bí mật.
//
// Hợp đồng với apps/api (giữ đúng tên khoá, kiểu và tập giá trị):
//
//	{"schema":1, "mechanism":"systemd"|"cron"|"launchd"|"schtasks"|"",
//	 "enabled":bool, "active":bool|null, "unit_present":bool, "opted_out":bool,
//	 "owned_by_other":bool,
//	 "since":RFC3339|"", "last_run_at":RFC3339|"",
//	 "last_result":"done"|"failed"|"deferred"|"up_to_date"|"blocked"|"",
//	 "next_run_at":RFC3339|"", "linger":"yes"|"no"|"unknown"|"not_applicable",
//	 "request_watcher":"active"|"failed"|"inactive"|"unknown",
//	 "watcher":{"state":"ok"|"fallback"|"failed","reason":""|"inotify"|"resources"|"other","hint":"…"},
//	 "checked_at":RFC3339}
//
// v0.1.54: `watcher` (người gác yêu cầu .path tự chữa) — thêm tương thích ngược: thiếu khoá = ok.
// fallback = .path lỗi (vd hết hạn mức inotify) nhưng timer dự phòng quét mỗi phút đang nhận thay;
// failed = .path lỗi và chưa bật được dự phòng. hint là câu cố định genh sinh (tiếng Việt).

// NightlyStatusFile là tên tệp trong run/.
const NightlyStatusFile = "nightly-status.json"

// NightlyStatusSchema là phiên bản khuôn nightly-status.json.
const NightlyStatusSchema = 1

// Các giá trị last_result.
const (
	NightlyResultDone     = "done"
	NightlyResultFailed   = "failed"
	NightlyResultDeferred = "deferred"
	NightlyResultUpToDate = "up_to_date"
	NightlyResultBlocked  = "blocked"
)

// Các giá trị request_watcher.
const (
	WatcherActive   = "active"
	WatcherFailed   = "failed"
	WatcherInactive = "inactive"
	WatcherUnknown  = "unknown"
)

// Các giá trị watcher.state / watcher.reason (v0.1.54).
const (
	WatcherStateOK       = "ok"
	WatcherStateFallback = "fallback"
	WatcherStateFailed   = "failed"

	WatcherReasonInotify   = "inotify"
	WatcherReasonResources = "resources"
	WatcherReasonOther     = "other"
)

// NightlyWatcher là khối `watcher` (v0.1.54): người gác yêu cầu `.path` có tự chữa được không.
type NightlyWatcher struct {
	State  string `json:"state"`
	Reason string `json:"reason"`
	Hint   string `json:"hint"`
}

// NightlyStatus là nội dung run/nightly-status.json — KHÔNG omitempty: api đọc đủ
// mọi khoá (chuỗi rỗng = chưa có; active null = không áp dụng/không rõ).
type NightlyStatus struct {
	Schema      int    `json:"schema"`
	Mechanism   string `json:"mechanism"`
	Enabled     bool   `json:"enabled"`
	Active      *bool  `json:"active"`
	UnitPresent bool   `json:"unit_present"`
	OptedOut    bool   `json:"opted_out"`
	// OwnedByOther: lịch đêm DÙNG CHUNG của máy thuộc một bản cài KHÁC còn sống —
	// với bản cài này Enabled=false nhưng không phải "đang tắt" (`genh auto-update
	// enable` ở đây bị từ chối): Console báo xám, không cảnh báo.
	OwnedByOther   bool   `json:"owned_by_other"`
	Since          string `json:"since"`
	LastRunAt      string `json:"last_run_at"`
	LastResult     string `json:"last_result"`
	NextRunAt      string `json:"next_run_at"`
	Linger         string `json:"linger"`
	RequestWatcher string `json:"request_watcher"`
	// Watcher (v0.1.54): người gác yêu cầu tự chữa — thiếu khoá = ok.
	Watcher   NightlyWatcher `json:"watcher"`
	CheckedAt string         `json:"checked_at"`
}

// NightlyStatusPath là đường dẫn run/nightly-status.json.
func NightlyStatusPath(installDir string) string {
	return filepath.Join(Dir(installDir), NightlyStatusFile)
}

var (
	validNightlyResults = map[string]bool{
		"": true, NightlyResultDone: true, NightlyResultFailed: true, NightlyResultDeferred: true,
		NightlyResultUpToDate: true, NightlyResultBlocked: true,
	}
	validLingers   = map[string]bool{"yes": true, "no": true, "unknown": true, "not_applicable": true}
	validWatchers  = map[string]bool{WatcherActive: true, WatcherFailed: true, WatcherInactive: true, WatcherUnknown: true}
	validMechanism = map[string]bool{"": true, "systemd": true, "cron": true, "launchd": true, "schtasks": true}
)

// ReadNightlyStatus đọc run/nightly-status.json AN TOÀN (readStateFile) và LỌC: api
// ghi được run/ nên mốc giờ sai dạng/giá trị ngoài tập bị bỏ (không đưa vào bản ghi
// kế tiếp).
func ReadNightlyStatus(installDir string) (NightlyStatus, error) {
	var st NightlyStatus
	b, err := readStateFile(NightlyStatusPath(installDir), false)
	if err != nil {
		return st, err
	}
	if err := json.Unmarshal(b, &st); err != nil {
		return NightlyStatus{}, err
	}
	for _, p := range []*string{&st.Since, &st.LastRunAt, &st.NextRunAt, &st.CheckedAt} {
		if _, err := time.Parse(time.RFC3339, *p); err != nil {
			*p = ""
		}
	}
	if !validNightlyResults[st.LastResult] {
		st.LastResult = ""
	}
	if !validMechanism[st.Mechanism] {
		st.Mechanism = ""
	}
	if !validLingers[st.Linger] {
		st.Linger = "unknown"
	}
	if !validWatchers[st.RequestWatcher] {
		st.RequestWatcher = WatcherUnknown
	}
	st.Watcher = cleanNightlyWatcher(st.Watcher)
	return st, nil
}

// cleanNightlyWatcher lọc khối watcher: state ngoài tập ⇒ ok; reason ngoài tập ⇒ ""; hint chỉ
// giữ khi không ok và ≤ 400 ký tự (api ghi được run/ nên không tin giá trị đọc vào).
func cleanNightlyWatcher(w NightlyWatcher) NightlyWatcher {
	switch w.State {
	case WatcherStateFallback, WatcherStateFailed:
	default:
		return NightlyWatcher{State: WatcherStateOK}
	}
	switch w.Reason {
	case WatcherReasonInotify, WatcherReasonResources, WatcherReasonOther:
	default:
		w.Reason = ""
	}
	if len(w.Hint) > 400 {
		w.Hint = ""
	}
	return w
}

// WriteNightlyStatus ghi nguyên tử run/nightly-status.json (0644 để api đọc). Các
// trường LƯU QUA các lần ghi được giữ từ bản ghi trước khi st để trống:
//   - since = lần đầu genh thấy lịch được bật: đã có thì giữ; tắt (Enabled=false)
//     thì xoá; chưa có mà đang bật thì đặt bằng bây giờ;
//   - last_run_at/last_result: st để trống thì giữ (publishHostInfo và trực canh chỉ
//     làm mới phần trạng thái, không đụng kết quả lần chạy).
//
// Schema và checked_at luôn do hàm này đặt.
func WriteNightlyStatus(installDir string, st NightlyStatus) error {
	if err := EnsureDir(installDir); err != nil {
		return err
	}
	old, _ := ReadNightlyStatus(installDir)
	st.Schema = NightlyStatusSchema
	switch {
	case !st.Enabled:
		st.Since = ""
	case st.Since != "":
	case old.Since != "":
		st.Since = old.Since
	default:
		st.Since = now()
	}
	if st.LastRunAt == "" {
		st.LastRunAt = old.LastRunAt
		if st.LastResult == "" {
			st.LastResult = old.LastResult
		}
	}
	if st.Linger == "" {
		st.Linger = "unknown"
	}
	if st.RequestWatcher == "" {
		st.RequestWatcher = WatcherUnknown
	}
	st.Watcher = cleanNightlyWatcher(st.Watcher)
	st.CheckedAt = now()
	return writeJSON(NightlyStatusPath(installDir), st)
}

// RecordNightlyRun ghi kết quả một lần lịch đêm chạy, GIỮ phần trạng thái lịch đã
// ghi: result == "" là LÚC BẮT ĐẦU (last_run_at = at, last_result xoá về "" — đang
// chạy); result khác rỗng là LÚC KẾT THÚC (last_result = result; at khác zero
// thì cập nhật cả last_run_at). result ngoài tập hợp lệ bị bỏ qua.
func RecordNightlyRun(installDir string, at time.Time, result string) error {
	if !validNightlyResults[result] {
		return nil
	}
	if err := EnsureDir(installDir); err != nil {
		return err
	}
	st, err := ReadNightlyStatus(installDir)
	if err != nil {
		st = NightlyStatus{Linger: "unknown", RequestWatcher: WatcherUnknown, Watcher: NightlyWatcher{State: WatcherStateOK}}
	}
	st.Schema = NightlyStatusSchema
	if !at.IsZero() {
		st.LastRunAt = at.UTC().Format(time.RFC3339)
	}
	st.LastResult = result
	st.CheckedAt = now()
	return writeJSON(NightlyStatusPath(installDir), st)
}
