package ops

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/machine"
)

// ─── Trực canh máy chủ — phần ĐO (v0.1.44, F-6b) ────────────────────────────
//
// KHÔNG gọi API nghiệp vụ: chỉ đọc tệp + hỏi docker; /api/v1/ready là phép thử
// sống duy nhất qua HTTP. Mỗi phép đo ghi lại "đã đo được" (measured) — khoá đã
// đo mà không thấy sự cố ⇒ đã hết; KHÔNG đo được ⇒ giữ nguyên trạng thái cũ.

// Khoá sự cố genh tự đo (chung không gian khoá với api — hợp đồng, giữ đúng chữ).
const (
	incAPIDown        = "api.down"
	incDockerDown     = "docker.down"
	incServicePrefix  = "service.unhealthy:"
	incUpdateFailed   = "update.failed"
	incDiskLow        = "disk.low"
	incBackupStale    = "backup.stale"
	incOffsiteStale   = "offsite.stale"
	incOffsiteFailed  = "offsite.failed"
	incWorkerSilent   = "worker.silent"
	incBridgeSilent   = "bridge.silent"
	severityBad       = "bad"
	severityWarn      = "warn"
	sourceGenh        = "genh"
	sourceAPI         = "api"
	heartbeatWorkerKy = "gh:worker:heartbeat"
	heartbeatBridgeKy = "gh:bridge:heartbeat"
)

// Ngưỡng (biến gói để test đặt được nếu cần).
var (
	watchdogCmdTimeout        = 30 * time.Second
	watchdogReadyRetry        = 10 * time.Second
	watchdogRestartEvery      = 60 * time.Minute
	watchdogAPIHealthFresh    = 10 * time.Minute
	watchdogWorkerSilentAfter = 10 * time.Minute
	watchdogBridgeGrace       = 2 * time.Minute
	watchdogDefaultBackupMax  = 36 * time.Hour
	watchdogOffsiteStaleAfter = 7*24*time.Hour + 12*time.Hour
	watchdogUpdateFailedFor   = 24 * time.Hour
)

// wdIncident là một sự cố đo được trong lượt này.
type wdIncident struct {
	Key         string
	Severity    string
	Title       string
	Body        string
	Fingerprint string
	Source      string
}

// wdMeasure là kết quả một lượt đo.
type wdMeasure struct {
	found          map[string]wdIncident
	measured       map[string]bool
	measuredPrefix []string
	restarted      []string
	health         *hostlink.APIHealth
	healthFresh    bool
	notes          []string // dòng log (đã che bí mật) cho out
}

func newMeasure() *wdMeasure {
	return &wdMeasure{found: map[string]wdIncident{}, measured: map[string]bool{}}
}

func (m *wdMeasure) add(inc wdIncident) {
	if inc.Fingerprint == "" {
		inc.Fingerprint = inc.Key
	}
	if inc.Source == "" {
		inc.Source = sourceGenh
	}
	m.found[inc.Key] = inc
}

// isMeasured: genh đã đo khoá này trong lượt (vắng ⇒ đã hết).
func (m *wdMeasure) isMeasured(key string) bool {
	if m.measured[key] {
		return true
	}
	for _, p := range m.measuredPrefix {
		if strings.HasPrefix(key, p) {
			return true
		}
	}
	return false
}

// psRow là phần cần của một dòng `docker compose ps --all --format json`.
type psRow struct {
	Service string `json:"Service"`
	State   string `json:"State"`
	Health  string `json:"Health"`
	Status  string `json:"Status"`
	Image   string `json:"Image"`
}

// parsePSRows đọc mảng JSON hoặc NDJSON (như compose.ParsePS) giữ thêm Status/Image.
func parsePSRows(data []byte) ([]psRow, error) {
	trimmed := strings.TrimSpace(string(data))
	if trimmed == "" {
		return nil, nil
	}
	if trimmed[0] == '[' {
		var rows []psRow
		if err := json.Unmarshal([]byte(trimmed), &rows); err != nil {
			return nil, err
		}
		return rows, nil
	}
	var rows []psRow
	for _, line := range strings.Split(trimmed, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var r psRow
		if err := json.Unmarshal([]byte(line), &r); err != nil {
			return nil, err
		}
		rows = append(rows, r)
	}
	return rows, nil
}

var upForRe = regexp.MustCompile(`(?i)^Up\s+(\d+|about an?|less than a)\s+(second|minute|hour|day|week|month|year)s?`)

// upFor đọc thời gian chạy từ Status kiểu "Up 5 minutes (healthy)" — ok=false
// nếu không phải "Up …".
func upFor(status string) (time.Duration, bool) {
	m := upForRe.FindStringSubmatch(strings.TrimSpace(status))
	if m == nil {
		return 0, false
	}
	n := 1
	switch q := strings.ToLower(m[1]); {
	case strings.HasPrefix(q, "less"):
		n = 0
	case strings.HasPrefix(q, "about"):
		n = 1
	default:
		n, _ = strconv.Atoi(q)
	}
	unit := map[string]time.Duration{"second": time.Second, "minute": time.Minute, "hour": time.Hour, "day": 24 * time.Hour,
		"week": 7 * 24 * time.Hour, "month": 30 * 24 * time.Hour, "year": 365 * 24 * time.Hour}[strings.ToLower(m[2])]
	return time.Duration(n) * unit, true
}

func (r psRow) running() bool { return strings.EqualFold(r.State, "running") }

func (r psRow) stopped() bool {
	s := strings.ToLower(r.State)
	return s == "exited" || s == "dead"
}

// wdRun gom ngữ cảnh một lượt đo.
type wdRun struct {
	env         *Env
	deps        WatchdogDeps
	runner      dockercli.Runner
	composePath string
	envOverlay  []string
	now         time.Time
	state       *watchdogState
}

func (w *wdRun) docker(ctx context.Context, args ...string) ([]byte, error) {
	cctx, cancel := context.WithTimeout(ctx, watchdogCmdTimeout)
	defer cancel()
	return w.runner.Output(cctx, dockercli.Cmd{Name: "docker", Args: compose.BaseArgs(w.composePath, args...), Env: w.envOverlay, Dir: composeDir(w.composePath)})
}

func (w *wdRun) hhmm() string { return w.now.Local().Format("15:04") }

// maybeRestart tự khởi động lại svc (tối đa 1 lần/service/60 phút — ghi trong
// state): exited/dead ⇒ `up -d --no-deps`, unhealthy ⇒ `restart`. Trả câu thêm
// vào nội dung tin ("" nếu không khởi động lại lượt này).
func (w *wdRun) maybeRestart(ctx context.Context, m *wdMeasure, svc string, exited bool) string {
	// Owner vừa `genh stop`/`genh uninstall` giữa lượt ⇒ không khởi động lại.
	if OwnerPaused(w.env.InstallDir) {
		return ""
	}
	if last, ok := w.state.Restarts[svc]; ok {
		if t, err := time.Parse(time.RFC3339, last); err == nil && w.now.Sub(t) < watchdogRestartEvery && w.now.Sub(t) > -time.Hour {
			return ""
		}
	}
	// Giữ genh.lock (không chờ) CHỈ quanh lệnh docker: update/restore/import có
	// thể lấy khoá sau lần kiểm đầu lượt (đo mất tới ~25 giây) — khi đó không
	// được dựng lại service bằng compose/env cũ giữa lúc đang dừng/migrate.
	lock, lerr := hostlink.AcquireLock(w.env.InstallDir)
	if lerr != nil {
		m.notes = append(m.notes, "đang cập nhật/khôi phục — không tự khởi động lại "+svc)
		return ""
	}
	defer lock.Release()
	w.state.Restarts[svc] = w.now.UTC().Format(time.RFC3339)
	var err error
	if exited {
		_, err = w.docker(ctx, "up", "-d", "--no-deps", svc)
	} else {
		_, err = w.docker(ctx, "restart", svc)
	}
	m.restarted = append(m.restarted, svc)
	if err != nil {
		m.notes = append(m.notes, fmt.Sprintf("tự khởi động lại %s thất bại: %v", svc, err))
		return fmt.Sprintf(" Đã thử tự khởi động lại %s lúc %s nhưng chưa được.", svc, w.hhmm())
	}
	m.notes = append(m.notes, "đã tự khởi động lại "+svc)
	return fmt.Sprintf(" Đã tự khởi động lại %s lúc %s.", svc, w.hhmm())
}

// probeReady: true khi /api/v1/ready trả 200.
func (w *wdRun) probeReady(ctx context.Context) bool {
	client := w.deps.ReadyClient
	if client == nil {
		client = insecureLocalClient(5 * time.Second)
	}
	url := w.deps.ReadyURL
	if url == "" {
		url = localURL(w.env.Port, readyPath)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return false
	}
	resp, err := client.Do(req)
	if err != nil {
		return false
	}
	_ = resp.Body.Close()
	return resp.StatusCode == http.StatusOK
}

// measure chạy mọi phép đo của một lượt.
func (w *wdRun) measure(ctx context.Context) *wdMeasure {
	m := newMeasure()

	// api-health.json của api (đọc trước: backup.stale dùng latest_backup_at).
	if h, err := hostlink.ReadAPIHealth(w.env.InstallDir); err == nil {
		m.health = &h
		if t, perr := parseISO(h.WrittenAt); perr == nil {
			age := w.now.Sub(t)
			m.healthFresh = age <= watchdogAPIHealthFresh && age >= -5*time.Minute
		}
	}

	// 1. Docker + dịch vụ.
	rows, dockerOK := w.checkServices(ctx, m)

	// 2. Cập nhật thất bại gần đây (update-status.json — genh tự ghi).
	w.checkUpdateFailed(m)

	// 3. Chỗ trống đĩa (như genh update).
	w.checkDisk(ctx, m, dockerOK)

	// 4. Nhịp worker/bridge qua redis.
	if dockerOK {
		w.checkHeartbeats(ctx, m, rows)
	}

	// 5. Bản sao lưu trong máy.
	w.checkBackup(ctx, m, rows, dockerOK)

	// 6. Bản sao ngoài máy.
	w.checkOffsite(m)
	return m
}

func findRow(rows []psRow, svc string) (psRow, bool) {
	for _, r := range rows {
		if r.Service == svc {
			return r, true
		}
	}
	return psRow{}, false
}

func (w *wdRun) checkServices(ctx context.Context, m *wdMeasure) ([]psRow, bool) {
	out, err := w.docker(ctx, "ps", "--all", "--format", "json")
	m.measured[incDockerDown] = true
	if err != nil {
		m.add(wdIncident{Key: incDockerDown, Severity: severityBad, Title: "Docker không chạy",
			Body: "Không hỏi được docker compose — mọi dịch vụ có thể đang dừng. Kiểm máy chủ (Docker có tự chạy lại khi bật máy không)."})
		m.notes = append(m.notes, "docker compose ps lỗi: "+err.Error())
		return nil, false
	}
	rows, err := parsePSRows(out)
	if err != nil {
		m.notes = append(m.notes, "không đọc được docker compose ps: "+err.Error())
		return nil, false
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].Service < rows[j].Service })

	// api: không chạy ⇒ api.down ngay; đang chạy ⇒ /ready 2 lần cách 10 giây.
	m.measured[incAPIDown] = true
	api, ok := findRow(rows, "api")
	switch {
	case !ok || !api.running():
		state := "chưa có container"
		if ok {
			state = api.State
		}
		// Không có container api ⇒ KHÔNG tự `up -d` (bản cài đã gỡ/chưa dựng —
		// tạo lại sẽ ra container/volume rỗng); chỉ báo cho Owner.
		note := " Không tự tạo lại container — chạy `genh start` nếu muốn bật lại."
		if ok {
			note = w.maybeRestart(ctx, m, "api", true)
		}
		m.add(wdIncident{Key: incAPIDown, Severity: severityBad, Title: "Máy chủ ứng dụng (api) không chạy",
			Body: "api đang ở trạng thái " + state + "." + note, Fingerprint: incAPIDown + "|stopped"})
	default:
		ready := w.probeReady(ctx)
		if !ready {
			if err := w.sleep(ctx, watchdogReadyRetry); err == nil {
				ready = w.probeReady(ctx)
			}
		}
		if !ready {
			note := ""
			if strings.EqualFold(api.Health, "unhealthy") {
				note = w.maybeRestart(ctx, m, "api", false)
			}
			m.add(wdIncident{Key: incAPIDown, Severity: severityBad, Title: "Máy chủ ứng dụng (api) không phản hồi",
				Body: readyPath + " không trả 200 sau 2 lần thử cách 10 giây." + note, Fingerprint: incAPIDown + "|not_ready"})
		}
	}

	// Các dịch vụ dài hạn khác (trừ migrate — chạy một lần).
	m.measuredPrefix = append(m.measuredPrefix, incServicePrefix)
	for _, r := range rows {
		if r.Service == "api" || r.Service == "migrate" || r.Service == "" {
			continue
		}
		switch {
		case r.stopped():
			note := w.maybeRestart(ctx, m, r.Service, true)
			m.add(wdIncident{Key: incServicePrefix + r.Service, Severity: severityBad, Title: "Dịch vụ " + r.Service + " đã dừng",
				Body: "Trạng thái: " + r.State + "." + note, Fingerprint: incServicePrefix + r.Service + "|stopped"})
		case strings.EqualFold(r.State, "restarting"):
			// Docker đang tự thử lại (restart: unless-stopped) ⇒ chỉ báo, không restart chồng.
			m.add(wdIncident{Key: incServicePrefix + r.Service, Severity: severityBad, Title: "Dịch vụ " + r.Service + " khởi động lại liên tục",
				Body:        "Docker đang tự khởi động lại " + r.Service + " nhiều lần (lỗi lặp). Xem `genh logs " + r.Service + "`.",
				Fingerprint: incServicePrefix + r.Service + "|restarting"})
		case strings.EqualFold(r.State, "created") || strings.EqualFold(r.State, "paused"):
			m.add(wdIncident{Key: incServicePrefix + r.Service, Severity: severityWarn, Title: "Dịch vụ " + r.Service + " chưa chạy",
				Body: "Trạng thái: " + r.State + ". Chạy `genh start` để bật lại.", Fingerprint: incServicePrefix + r.Service + "|" + strings.ToLower(r.State)})
		case strings.EqualFold(r.Health, "unhealthy"):
			note := w.maybeRestart(ctx, m, r.Service, false)
			m.add(wdIncident{Key: incServicePrefix + r.Service, Severity: severityBad, Title: "Dịch vụ " + r.Service + " không khoẻ",
				Body: "Kiểm tra sức khoẻ báo unhealthy." + note, Fingerprint: incServicePrefix + r.Service + "|unhealthy"})
		}
	}
	return rows, true
}

func (w *wdRun) sleep(ctx context.Context, d time.Duration) error {
	if w.deps.Sleep != nil {
		return w.deps.Sleep(ctx, d)
	}
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}

func (w *wdRun) checkUpdateFailed(m *wdMeasure) {
	m.measured[incUpdateFailed] = true
	st, err := hostlink.ReadStatus(w.env.InstallDir)
	if err != nil || st.State != "failed" {
		return
	}
	t, err := parseISO(st.FinishedAt)
	if err != nil || w.now.Sub(t) > watchdogUpdateFailedFor {
		return
	}
	body := "Lần cập nhật lúc " + t.Local().Format("15:04 02/01") + " không thành công."
	if msg := shortText(st.Message, 300); msg != "" {
		body += " " + msg
	}
	m.add(wdIncident{Key: incUpdateFailed, Severity: severityBad, Title: "Cập nhật thất bại", Body: body,
		Fingerprint: incUpdateFailed + "|" + st.FinishedAt})
}

func (w *wdRun) checkDisk(ctx context.Context, m *wdMeasure, dockerOK bool) {
	cctx, cancel := context.WithTimeout(ctx, watchdogCmdTimeout)
	defer cancel()
	var runner dockercli.Runner
	if dockerOK {
		runner = w.runner
	}
	free, path, err := checkDiskFree(cctx, runner, w.env, UpdateDeps{DiskFree: w.deps.DiskFree})
	if err != nil {
		m.notes = append(m.notes, "không đo được chỗ trống đĩa: "+err.Error())
		return
	}
	m.measured[incDiskLow] = true
	state := "ok"
	if free < machine.MinDiskBytes {
		state = "low"
		m.add(wdIncident{Key: incDiskLow, Severity: severityBad, Title: "Ổ đĩa sắp đầy",
			Body: fmt.Sprintf("Còn %s trống tại %s (cần tối thiểu %s) — cập nhật và sao lưu có thể lỗi.", formatGB(free), path, formatGB(machine.MinDiskBytes))})
	}
	_ = hostlink.WriteDiskStatus(w.env.InstallDir, hostlink.DiskStatus{State: state, FreeBytes: free, MinBytes: machine.MinDiskBytes, Path: path})
}

func (w *wdRun) checkHeartbeats(ctx context.Context, m *wdMeasure, rows []psRow) {
	redis, ok := findRow(rows, "redis")
	if !ok || !redis.running() {
		return
	}
	out, err := w.docker(ctx, "exec", "-T", "redis", "redis-cli", "--raw", "MGET", heartbeatWorkerKy, heartbeatBridgeKy)
	if err != nil {
		m.notes = append(m.notes, "không đọc được nhịp worker/bridge: "+err.Error())
		return
	}
	lines := strings.Split(strings.TrimRight(string(out), "\r\n"), "\n")
	for len(lines) < 2 {
		lines = append(lines, "")
	}
	workerBeat, bridgeBeat := strings.TrimSpace(lines[0]), strings.TrimSpace(lines[1])

	if worker, ok := findRow(rows, "worker"); ok && worker.running() {
		m.measured[incWorkerSilent] = true
		up, _ := upFor(worker.Status)
		if t, err := parseBeat(workerBeat); err == nil {
			if age := w.now.Sub(t); age > watchdogWorkerSilentAfter {
				m.add(wdIncident{Key: incWorkerSilent, Severity: severityBad, Title: "Tiến trình nền (worker) im lặng",
					Body: fmt.Sprintf("Nhịp gần nhất lúc %s (quá %d phút) — việc định kỳ (sao lưu, nhắc việc) có thể đang đứng.", t.Local().Format("15:04 02/01"), int(watchdogWorkerSilentAfter.Minutes()))})
			}
		} else if up > watchdogWorkerSilentAfter {
			m.add(wdIncident{Key: incWorkerSilent, Severity: severityBad, Title: "Tiến trình nền (worker) im lặng",
				Body: "Chưa thấy nhịp nào dù worker đã chạy hơn 10 phút."})
		}
	} else if !ok {
		m.measured[incWorkerSilent] = true
	}

	bridge, ok := findRow(rows, "bridge")
	switch {
	case !ok:
		m.measured[incBridgeSilent] = true
	case bridge.running():
		m.measured[incBridgeSilent] = true
		up, _ := upFor(bridge.Status)
		if bridgeBeat == "" && up > watchdogBridgeGrace {
			m.add(wdIncident{Key: incBridgeSilent, Severity: severityWarn, Title: "Cầu nối kênh (bridge) im lặng",
				Body: "bridge đang chạy nhưng không còn nhịp — tin nhắn kênh (Zalo…) có thể không đi/không về."})
		}
	}
}

var isoInTextRe = regexp.MustCompile(`\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?`)

func (w *wdRun) checkBackup(ctx context.Context, m *wdMeasure, rows []psRow, dockerOK bool) {
	limit := watchdogDefaultBackupMax
	var latest time.Time
	found := false
	if m.healthFresh && m.health != nil && m.health.LatestBackupAt != nil {
		if t, err := parseISO(*m.health.LatestBackupAt); err == nil {
			latest, found = t, true
			if m.health.BackupStaleLimitHours > 0 {
				limit = time.Duration(m.health.BackupStaleLimitHours) * time.Hour
			}
		}
	}
	if !found && dockerOK {
		if wk, ok := findRow(rows, "worker"); ok && wk.running() {
			out, err := w.docker(ctx, "exec", "-T", "worker", "sh", "-c", "python -m gh.backup list 2>&1")
			if err != nil {
				m.notes = append(m.notes, "không liệt kê được bản sao lưu: "+err.Error())
			} else {
				for _, s := range isoInTextRe.FindAllString(string(out), -1) {
					if t, err := parseISO(s); err == nil && (!found || t.After(latest)) {
						latest, found = t, true
					}
				}
			}
		}
	}
	if !found {
		return // không đo được ⇒ không mở/đóng
	}
	m.measured[incBackupStale] = true
	if age := w.now.Sub(latest); age > limit {
		m.add(wdIncident{Key: incBackupStale, Severity: severityWarn, Title: "Bản sao lưu trong máy đã cũ",
			Body: fmt.Sprintf("Bản gần nhất lúc %s — quá %d giờ chưa có bản mới.", latest.Local().Format("15:04 02/01"), int(limit.Hours()))})
	}
}

func (w *wdRun) checkOffsite(m *wdMeasure) {
	m.measured[incOffsiteStale] = true
	m.measured[incOffsiteFailed] = true
	st, err := hostlink.ReadOffsiteStatus(w.env.InstallDir)
	if err != nil || !st.Configured {
		return
	}
	if st.State == hostlink.OffsiteStateFailed {
		body := "Lần sao lưu ra ổ ngoài gần nhất không thành công"
		if st.ErrorCode != "" {
			body += " (" + shortText(st.ErrorCode, 20) + ")"
		}
		m.add(wdIncident{Key: incOffsiteFailed, Severity: severityWarn, Title: "Sao lưu ra ổ ngoài thất bại", Body: body + ".",
			Fingerprint: incOffsiteFailed + "|" + st.LastAttemptAt})
	}
	if t, err := parseISO(st.LastSuccessAt); err == nil && w.now.Sub(t) > watchdogOffsiteStaleAfter {
		m.add(wdIncident{Key: incOffsiteStale, Severity: severityWarn, Title: "Bản sao ngoài máy đã cũ",
			Body: "Bản sao ngoài máy gần nhất lúc " + t.Local().Format("02/01/2006") + " — cắm ổ USB/NAS để lịch Chủ nhật chạy được."})
	}
}

// mergeAPIAlerts gộp sự cố phía api (api-health còn tươi): khoá genh đã đo thì
// số đo của genh thắng.
func mergeAPIAlerts(m *wdMeasure) {
	if !m.healthFresh || m.health == nil {
		return
	}
	for _, a := range m.health.Alerts {
		if m.isMeasured(a.Key) {
			continue
		}
		if _, dup := m.found[a.Key]; dup {
			continue
		}
		m.add(wdIncident{Key: a.Key, Severity: a.Severity, Title: a.Title, Body: a.Body, Fingerprint: a.Fingerprint, Source: sourceAPI})
	}
}

// parseISO nhận RFC3339 (có/không phần lẻ giây) và dạng "YYYY-MM-DD HH:MM:SS".
func parseISO(s string) (time.Time, error) {
	s = strings.TrimSpace(s)
	if t, err := time.Parse(time.RFC3339Nano, s); err == nil {
		return t, nil
	}
	for _, layout := range []string{"2006-01-02T15:04:05.999999999", "2006-01-02 15:04:05.999999999Z07:00", "2006-01-02 15:04:05.999999999"} {
		if t, err := time.Parse(layout, s); err == nil {
			return t, nil
		}
	}
	return time.Time{}, fmt.Errorf("thời điểm không đọc được: %q", shortText(s, 40))
}

// parseBeat đọc nhịp ISO UTC (worker/bridge) hoặc số giây unix (bản cũ).
func parseBeat(s string) (time.Time, error) {
	if s == "" {
		return time.Time{}, fmt.Errorf("không có nhịp")
	}
	if t, err := parseISO(s); err == nil {
		return t, nil
	}
	f, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return time.Time{}, err
	}
	return time.Unix(int64(f), 0), nil
}

// shortText cắt chuỗi còn ≤ n rune, bỏ xuống dòng.
func shortText(s string, n int) string {
	s = strings.Join(strings.Fields(s), " ")
	rs := []rune(s)
	if len(rs) > n {
		return string(rs[:n-1]) + "…"
	}
	return s
}
