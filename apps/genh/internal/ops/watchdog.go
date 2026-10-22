package ops

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/autoupdate"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/config"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/machine"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/notify"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/redact"
)

// ─── Trực canh máy chủ — `genh doctor --notify` (v0.1.44, F-6b) ─────────────
//
// Lịch mỗi 12 phút (internal/autoupdate/watchdog*.go) gọi RunWatchdog: đo sức
// khoẻ máy chủ KHÔNG cần api (docker + tệp trạng thái), tự khởi động lại dịch vụ
// chết (tối đa 1 lần/service/60 phút), gộp sự cố phía api (run/api-health.json)
// và báo Owner qua Telegram — CHỐNG SPAM: mỗi lượt tối đa 1 tin CẢNH BÁO (sự cố
// mới) + 1 tin ĐÃ ỔN (sự cố đã hết); sự cố còn mở không báo lại. Đây là đường
// cảnh báo sự cố DUY NHẤT (api không gửi Telegram cho sự cố).
//
// State chống spam ở <gốc cài đặt>/config/watchdog-state.json (0600, KHÔNG trong
// run/ — api ghi được run/). Kết quả cho Console: run/watchdog-status.json.

// WatchdogStateFile là tệp state chống spam trong config/.
const WatchdogStateFile = "watchdog-state.json"

const (
	watchdogStateSchema      = 1
	watchdogRunTimeout       = 4 * time.Minute
	watchdogBootGap          = 30 * time.Minute
	watchdogLogMaxBytes      = 5 << 20
	watchdogMaxPending       = 50
	watchdogMsgAlert         = "Gen-Harness · CẢNH BÁO"
	watchdogMsgResolved      = "Gen-Harness · ĐÃ ỔN"
	watchdogMsgTest          = "Gen-Harness · Tin thử từ trực canh máy chủ"
	watchdogMsgFooterNote    = "(Tin tự động từ trực canh máy chủ — mọi thao tác Sếp xác nhận trong Console.)"
	watchdogLocalConsoleLine = "Mở Console từ máy chủ Gen-Harness (chưa bật Truy cập từ xa nên điện thoại chưa mở được link)."
	watchdogConsolePath      = "/connections#telegram" // khối Trực canh máy chủ + sự cố đang mở
)

// WatchdogScheduler bật/tắt/hỏi lịch trực canh (mặc định: internal/autoupdate
// theo hệ điều hành — tiêm giả khi test).
type WatchdogScheduler interface {
	Enable(ctx context.Context) (msg, mechanism string, err error)
	Disable(ctx context.Context) (string, error)
	Status(ctx context.Context) (autoupdate.WatchdogSchedule, error)
}

type autoupdateWatchdogScheduler struct {
	deps autoupdate.Deps
	job  autoupdate.WatchdogJob
}

func (s autoupdateWatchdogScheduler) Enable(ctx context.Context) (string, string, error) {
	return autoupdate.EnableWatchdog(ctx, s.deps, s.job)
}

func (s autoupdateWatchdogScheduler) Disable(ctx context.Context) (string, error) {
	return autoupdate.DisableWatchdog(ctx, s.deps)
}

func (s autoupdateWatchdogScheduler) Status(ctx context.Context) (autoupdate.WatchdogSchedule, error) {
	return autoupdate.WatchdogScheduleStatus(ctx, s.deps)
}

// NewWatchdogScheduler dựng lịch trực canh thật cho bản cài env: `<genh> doctor
// --notify --quiet --install-dir <dir> [--port N]` mỗi 12 phút, log
// logs/watchdog.log, mang theo GENH_COMPOSE_FILE nếu phiên hiện tại có.
func NewWatchdogScheduler(env *Env) WatchdogScheduler {
	return NewWatchdogSchedulerWith(env, autoupdate.Deps{})
}

// NewWatchdogSchedulerWith như NewWatchdogScheduler nhưng mượn Runner/HomeDir/GOOS/
// LookPath (và GenhPath) của base — test tiêm lệnh hệ thống giả (v0.1.53).
func NewWatchdogSchedulerWith(env *Env, base autoupdate.Deps) WatchdogScheduler {
	genh := base.GenhPath
	if genh == "" {
		if exe, err := os.Executable(); err == nil {
			genh, _ = filepath.Abs(exe)
		}
	}
	job := autoupdate.WatchdogJob{InstallDir: env.InstallDir}
	if env.Port > 0 && env.Port != machine.DefaultPort {
		job.Port = env.Port
	}
	if v := os.Getenv(compose.EnvOverrideVar); v != "" {
		job.Env = append(job.Env, compose.EnvOverrideVar+"="+v)
	}
	deps := base
	deps.GenhPath, deps.LogFile, deps.InstallDir = genh, WatchdogLogPath(env.InstallDir), env.InstallDir
	return autoupdateWatchdogScheduler{deps: deps, job: job}
}

// WatchdogOptions là cờ của `genh doctor --notify`.
type WatchdogOptions struct {
	// Quiet: chỉ in dòng tóm tắt khi có thay đổi (lịch 12 phút dùng cờ này).
	Quiet bool
	// Test: gửi thêm tin thử (Owner bấm "Gửi thử" — run/request/watchdog.json).
	Test bool
}

// WatchdogDeps tiêm mọi I/O khi test — trường nil/rỗng dùng giá trị thật.
type WatchdogDeps struct {
	Runner      dockercli.Runner
	ReadyClient *http.Client
	// ReadyURL thay https://localhost:<port>/api/v1/ready (test).
	ReadyURL string
	Telegram *notify.Client
	Now      func() time.Time
	DiskFree func(path string) (uint64, error)
	Hostname func() (string, error)
	BootID   func() string
	Sleep    func(ctx context.Context, d time.Duration) error
	// Nightly làm mới run/nightly-status.json (nil = thật: RecordNightlyStatus) — test
	// tiêm giả để không hỏi systemctl/loginctl của máy chạy test.
	Nightly func(ctx context.Context, env *Env)
}

// refreshNightlyStatus (v0.1.53, F-99): mỗi lượt trực canh (12 phút) làm mới phần
// TRẠNG THÁI của run/nightly-status.json (lịch đêm bật/chạy không, linger, trình
// nhận yêu cầu) để Console thấy lịch đêm im lặng kể cả khi không ai chạy genh.
// KHÔNG đụng last_run_at/last_result (lịch đêm tự ghi). Lỗi bỏ qua.
func refreshNightlyStatus(ctx context.Context, env *Env, deps WatchdogDeps) {
	nctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	if deps.Nightly != nil {
		deps.Nightly(nctx, env)
		return
	}
	_ = RecordNightlyStatus(nctx, env.InstallDir, NightlyDeps(env.InstallDir))
}

func (d WatchdogDeps) now() time.Time {
	if d.Now != nil {
		return d.Now().UTC()
	}
	return time.Now().UTC()
}

// stateIncident là một sự cố trong watchdog-state.json.
type stateIncident struct {
	Key         string `json:"key"`
	Fingerprint string `json:"fingerprint"`
	Title       string `json:"title"`
	Body        string `json:"body"`
	Severity    string `json:"severity"`
	FirstSeen   string `json:"first_seen"`
	NotifiedAt  string `json:"notified_at"`
	ResolvedAt  string `json:"resolved_at,omitempty"`
}

// watchdogState là nội dung config/watchdog-state.json (không chứa bí mật).
type watchdogState struct {
	Schema            int                      `json:"schema"`
	BootID            string                   `json:"boot_id"`
	LastRunAt         string                   `json:"last_run_at"`
	Incidents         map[string]stateIncident `json:"incidents"`
	ResolvedPending   []stateIncident          `json:"resolved_pending"`
	Restarts          map[string]string        `json:"restarts"`
	BootNoticePending string                   `json:"boot_notice_pending,omitempty"`
	LastSentAt        string                   `json:"last_sent_at,omitempty"`
	LastTest          *hostlink.WatchdogTest   `json:"last_test,omitempty"`
}

// WatchdogStatePath là đường dẫn state chống spam.
func WatchdogStatePath(installDir string) string {
	return filepath.Join(installDir, "config", WatchdogStateFile)
}

func loadWatchdogState(installDir string) *watchdogState {
	st := &watchdogState{}
	if fi, err := os.Lstat(WatchdogStatePath(installDir)); err == nil && fi.Mode().IsRegular() && fi.Size() < 1<<20 {
		if b, err := os.ReadFile(WatchdogStatePath(installDir)); err == nil {
			if json.Unmarshal(b, st) != nil {
				st = &watchdogState{}
			}
		}
	}
	if st.Incidents == nil {
		st.Incidents = map[string]stateIncident{}
	}
	if st.Restarts == nil {
		st.Restarts = map[string]string{}
	}
	return st
}

// saveWatchdogState ghi nguyên tử (tệp tạm 0600 rồi rename).
func saveWatchdogState(installDir string, st *watchdogState) error {
	st.Schema = watchdogStateSchema
	if st.ResolvedPending == nil {
		st.ResolvedPending = []stateIncident{}
	}
	b, err := json.MarshalIndent(st, "", "  ")
	if err != nil {
		return err
	}
	path := WatchdogStatePath(installDir)
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	f, err := os.CreateTemp(filepath.Dir(path), WatchdogStateFile+".*.tmp")
	if err != nil {
		return err
	}
	tmp := f.Name()
	if _, err := f.Write(append(b, '\n')); err != nil {
		_ = f.Close()
		_ = os.Remove(tmp)
		return err
	}
	if err := f.Close(); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return nil
}

// WatchdogLogPath là logs/watchdog.log của bản cài.
func WatchdogLogPath(installDir string) string {
	return filepath.Join(config.New(installDir).LogsDir(), "watchdog.log")
}

// rotateWatchdogLog đổi tên logs/watchdog.log → .1 khi > 5 MB.
func rotateWatchdogLog(installDir string) {
	path := WatchdogLogPath(installDir)
	if fi, err := os.Lstat(path); err == nil && fi.Mode().IsRegular() && fi.Size() > watchdogLogMaxBytes {
		_ = os.Rename(path, path+".1")
	}
}

// RunWatchdog chạy MỘT lượt trực canh. Trả lỗi CHỈ khi cấu hình hỏng nghiêm
// trọng (chưa cài, không thấy compose.yaml) — sự cố của máy chủ đã báo qua
// Telegram/status, lịch không được "đỏ" vì chúng.
func RunWatchdog(ctx context.Context, env *Env, opts WatchdogOptions, deps WatchdogDeps, out io.Writer) error {
	if deps.Runner == nil {
		deps.Runner = dockercli.ExecRunner{}
	}
	if deps.Telegram == nil {
		deps.Telegram = notify.NewClient()
	}
	rotateWatchdogLog(env.InstallDir)

	// a) Khoá riêng — lượt khác đang chạy ⇒ thoát êm. "Gửi thử" (Owner bấm
	// trong Console, tệp yêu cầu đã bị tiêu thụ) thì CHỜ lượt kia xong, không
	// được mất tin thử thứ hai.
	var lock *hostlink.Lock
	var err error
	if opts.Test {
		lock, err = hostlink.AcquireWatchdogLockWait(ctx, env.InstallDir, watchdogRunTimeout+30*time.Second)
	} else {
		lock, err = hostlink.AcquireWatchdogLock(env.InstallDir)
	}
	if err != nil {
		if errors.Is(err, hostlink.ErrLockBusy) {
			if !opts.Quiet {
				_, _ = fmt.Fprintln(out, "Trực canh: lượt khác đang chạy — bỏ qua.")
			}
			return nil
		}
		return &OpError{Code: ErrCodeWatchdogFailed, What: "Không chạy được trực canh máy chủ", Why: err.Error(),
			Next: "Kiểm thư mục cài đặt (--install-dir) và quyền ghi của nó.", Err: err}
	}
	defer lock.Release()
	ctx, cancel := context.WithTimeout(ctx, watchdogRunTimeout)
	defer cancel()
	now := deps.now()
	refreshNightlyStatus(ctx, env, deps)
	PruneDiagnostics(env.InstallDir, now)

	st := loadWatchdogState(env.InstallDir)
	prevStatus, _ := hostlink.ReadWatchdogStatus(env.InstallDir)
	status := hostlink.WatchdogStatus{LastRunAt: now.Format(time.RFC3339), Schedule: prevStatus.Schedule}

	composePath, err := env.LocatePath()
	if err != nil {
		status.State = hostlink.WatchdogStateError
		status.Telegram = prevStatus.Telegram
		_ = hostlink.WriteWatchdogStatus(env.InstallDir, status)
		return err
	}
	bundle, err := env.LoadSecrets()
	if err != nil {
		status.State = hostlink.WatchdogStateError
		status.Telegram = prevStatus.Telegram
		_ = hostlink.WriteWatchdogStatus(env.InstallDir, status)
		return err
	}

	cfg, tgErr := notify.LoadTelegramConfig(env.InstallDir, composePath)
	red := redact.New(redact.SecretsFromInstall(env.InstallDir, composePath, cfg.Token)...)
	status.Telegram = telegramStateOf(tgErr)
	tgReady := tgErr == nil

	host := watchdogHostname(deps)
	healthForURL, _ := hostlink.ReadAPIHealth(env.InstallDir)
	consoleURL := watchdogConsoleURL(env, healthForURL.PublicURL)
	send := func(text string) error {
		sctx, scancel := context.WithTimeout(ctx, 15*time.Second)
		defer scancel()
		return deps.Telegram.Send(sctx, cfg, red.String(text))
	}
	var lines []string // tóm tắt cho out (đã che bí mật)
	changed := false
	recordSendErr := func(err error) {
		status.Telegram = hostlink.TelegramFailed
		var se *notify.SendError
		if errors.As(err, &se) {
			status.TelegramErrorCode = se.Code
		}
		lines = append(lines, "gửi Telegram lỗi: "+red.String(err.Error()))
		changed = true
	}

	// f) "Gửi thử" — việc Owner chủ động bấm: làm cả khi đang tạm dừng/bận.
	if opts.Test {
		t := &hostlink.WatchdogTest{At: now.Format(time.RFC3339)}
		switch {
		case errors.Is(tgErr, notify.ErrNotConfigured):
			t.ErrorCode = notify.CodeNotConfigured
		case tgErr != nil:
			t.ErrorCode = notify.CodeKeyMismatch
		default:
			if err := send(watchdogMessage(watchdogMsgTest, host, []string{"Kênh báo động của máy chủ hoạt động bình thường."}, consoleURL)); err != nil {
				var se *notify.SendError
				t.ErrorCode = notify.CodeUnreachable
				if errors.As(err, &se) {
					t.ErrorCode = se.Code
				}
				recordSendErr(err)
			} else {
				t.OK = true
				st.LastSentAt = now.Format(time.RFC3339)
			}
		}
		st.LastTest = t
		lines = append(lines, fmt.Sprintf("gửi thử: ok=%v %s", t.OK, t.ErrorCode))
		changed = true
	}

	// Nhịp khởi động lại máy: boot_id đổi và lần chạy trước cách > 30 phút.
	bootID := hostlink.BootID()
	if deps.BootID != nil {
		bootID = deps.BootID()
	}
	if bootID != "" && st.BootID != "" && bootID != st.BootID {
		if last, err := parseISO(st.LastRunAt); err == nil && now.Sub(last) > watchdogBootGap {
			st.BootNoticePending = "Máy chủ vừa khởi động lại (tắt khoảng " + humanDuration(now.Sub(last)) + ")."
		}
	}
	if bootID != "" {
		st.BootID = bootID
	}

	finish := func() error {
		st.LastRunAt = now.Format(time.RFC3339)
		status.LastSentAt = st.LastSentAt
		status.Test = st.LastTest
		status.Incidents = statusIncidents(st.Incidents)
		if status.State == "" {
			status.State = hostlink.WatchdogStateOK
			if len(status.Incidents) > 0 {
				status.State = hostlink.WatchdogStateIssues
			}
		}
		errState := saveWatchdogState(env.InstallDir, st)
		errStatus := hostlink.WriteWatchdogStatus(env.InstallDir, status)
		if changed || !opts.Quiet {
			summary := fmt.Sprintf("[%s] Trực canh: %s · %d sự cố đang mở · Telegram: %s", now.Format(time.RFC3339), status.State, len(status.Incidents), status.Telegram)
			if status.TelegramErrorCode != "" {
				summary += " (" + status.TelegramErrorCode + ")"
			}
			_, _ = fmt.Fprintln(out, red.String(summary))
			for _, l := range lines {
				_, _ = fmt.Fprintln(out, "  "+red.String(l))
			}
		}
		if errState != nil || errStatus != nil {
			err := errors.Join(errState, errStatus)
			return &OpError{Code: ErrCodeWatchdogFailed, What: "Trực canh không ghi được tệp trạng thái", Why: err.Error(),
				Next: "Kiểm quyền ghi thư mục config/ và run/ dưới thư mục cài đặt.", Err: err}
		}
		return nil
	}

	// a) Đang bận update/restore/import ⇒ không đo, không gửi.
	if hostlink.ExclusiveLockBusy(env.InstallDir) {
		status.State = hostlink.WatchdogStateSkippedBusy
		lines = append(lines, "đang cập nhật/khôi phục — lượt này không đo")
		return finish()
	}
	// Owner chủ động dừng (genh stop) ⇒ không tự khởi động lại, không báo.
	if OwnerPaused(env.InstallDir) {
		status.State = hostlink.WatchdogStatePaused
		return finish()
	}

	// b) Đo.
	run := &wdRun{env: env, deps: deps, runner: deps.Runner, composePath: composePath, envOverlay: EnvOverlay(bundle), now: now, state: st}
	m := run.measure(ctx)
	// c) Gộp sự cố phía api.
	mergeAPIAlerts(m)
	for k, inc := range m.found { // nội dung từ api/docker: che bí mật trước khi lưu/gửi
		inc.Title, inc.Body = red.String(inc.Title), red.String(inc.Body)
		m.found[k] = inc
	}
	for _, n := range m.notes {
		lines = append(lines, n)
	}
	if len(m.restarted) > 0 {
		changed = true
	}

	// d) Chống spam.
	newKeys, resolvedNow := applyIncidents(st, m, now)
	if len(newKeys) > 0 || len(resolvedNow) > 0 {
		changed = true
	}
	for _, k := range newKeys {
		lines = append(lines, "sự cố mới: "+k)
	}
	for _, k := range resolvedNow {
		lines = append(lines, "đã ổn: "+k)
	}
	// e) Telegram chưa cấu hình/tắt ⇒ không có ai để báo "đã ổn".
	if errors.Is(tgErr, notify.ErrNotConfigured) {
		st.ResolvedPending = nil
		st.BootNoticePending = ""
	}

	if tgReady {
		if st.BootNoticePending != "" {
			if err := send(watchdogMessage("Gen-Harness · "+st.BootNoticePending, host, nil, consoleURL)); err != nil {
				recordSendErr(err)
			} else {
				st.BootNoticePending = ""
				st.LastSentAt = now.Format(time.RFC3339)
			}
		}
		if alerts := unnotified(st.Incidents); len(alerts) > 0 && status.Telegram != hostlink.TelegramFailed {
			var body []string
			for _, a := range alerts {
				body = append(body, "• "+a.Title+": "+a.Body)
			}
			if err := send(watchdogMessage(watchdogMsgAlert, host, body, consoleURL)); err != nil {
				recordSendErr(err)
			} else {
				for _, a := range alerts {
					a.NotifiedAt = now.Format(time.RFC3339)
					st.Incidents[a.Key] = a
				}
				st.LastSentAt = now.Format(time.RFC3339)
				lines = append(lines, fmt.Sprintf("đã gửi CẢNH BÁO (%d sự cố)", len(alerts)))
				changed = true
			}
		}
		if len(st.ResolvedPending) > 0 && status.Telegram != hostlink.TelegramFailed {
			var body []string
			for _, r := range st.ResolvedPending {
				body = append(body, "• "+r.Title+": đã hết.")
			}
			if err := send(watchdogMessage(watchdogMsgResolved, host, body, consoleURL)); err != nil {
				recordSendErr(err)
			} else {
				lines = append(lines, fmt.Sprintf("đã gửi ĐÃ ỔN (%d sự cố)", len(st.ResolvedPending)))
				st.ResolvedPending = nil
				st.LastSentAt = now.Format(time.RFC3339)
				changed = true
			}
		}
	}
	return finish()
}

// applyIncidents cập nhật state theo số đo lượt này; trả khoá mới mở và khoá vừa hết.
func applyIncidents(st *watchdogState, m *wdMeasure, now time.Time) (newKeys, resolved []string) {
	nowS := now.Format(time.RFC3339)
	prev := st.Incidents
	// Sự cố "vừa hết, chưa báo ĐÃ ỔN" lại xuất hiện: rút khỏi hàng chờ, coi như
	// còn mở (cùng fingerprint ⇒ không báo lại).
	var pending []stateIncident
	for _, p := range st.ResolvedPending {
		if _, back := m.found[p.Key]; back {
			if _, still := prev[p.Key]; !still {
				p.ResolvedAt = ""
				prev[p.Key] = p
			}
			continue
		}
		pending = append(pending, p)
	}
	cur := map[string]stateIncident{}
	for k, inc := range m.found {
		si := stateIncident{Key: k, Fingerprint: inc.Fingerprint, Title: inc.Title, Body: inc.Body, Severity: inc.Severity, FirstSeen: nowS}
		p, ok := prev[k]
		if ok {
			si.FirstSeen = p.FirstSeen
			if p.Fingerprint == si.Fingerprint {
				si.NotifiedAt = p.NotifiedAt
			}
		}
		if !ok || p.Fingerprint != si.Fingerprint {
			newKeys = append(newKeys, k)
		}
		cur[k] = si
	}
	for k, p := range prev {
		if _, ok := cur[k]; ok {
			continue
		}
		// Không đo được (genh) và api-health không tươi ⇒ GIỮ NGUYÊN — không "đã ổn" giả.
		if !m.isMeasured(k) && !m.healthFresh {
			cur[k] = p
			continue
		}
		resolved = append(resolved, k)
		if p.NotifiedAt != "" {
			p.ResolvedAt = nowS
			pending = append(pending, p)
		}
	}
	if len(pending) > watchdogMaxPending {
		pending = pending[len(pending)-watchdogMaxPending:]
	}
	sort.Strings(newKeys)
	sort.Strings(resolved)
	sort.Slice(pending, func(i, j int) bool { return pending[i].Key < pending[j].Key })
	st.Incidents = cur
	st.ResolvedPending = pending
	return newKeys, resolved
}

// unnotified: sự cố đang mở chưa báo (bad trước, rồi theo khoá).
func unnotified(incs map[string]stateIncident) []stateIncident {
	var out []stateIncident
	for _, i := range incs {
		if i.NotifiedAt == "" {
			out = append(out, i)
		}
	}
	sortIncidents(out)
	return out
}

func sortIncidents(out []stateIncident) {
	sort.Slice(out, func(a, b int) bool {
		if (out[a].Severity == severityBad) != (out[b].Severity == severityBad) {
			return out[a].Severity == severityBad
		}
		return out[a].Key < out[b].Key
	})
}

func statusIncidents(incs map[string]stateIncident) []hostlink.WatchdogIncident {
	list := make([]stateIncident, 0, len(incs))
	for _, i := range incs {
		list = append(list, i)
	}
	sortIncidents(list)
	out := make([]hostlink.WatchdogIncident, 0, len(list))
	for _, i := range list {
		out = append(out, hostlink.WatchdogIncident{Key: i.Key, Severity: i.Severity, Title: i.Title, Since: i.FirstSeen})
	}
	return out
}

func telegramStateOf(err error) string {
	switch {
	case err == nil:
		return hostlink.TelegramOK
	case errors.Is(err, notify.ErrDisabled):
		return hostlink.TelegramDisabled
	case errors.Is(err, notify.ErrNotConfigured):
		return hostlink.TelegramNotConfigured
	case errors.Is(err, notify.ErrKeyMismatch):
		return hostlink.TelegramKeyMismatch
	default:
		return hostlink.TelegramFailed
	}
}

// watchdogMessage dựng tin văn bản thường (không emoji, không bí mật).
func watchdogMessage(header, host string, items []string, consoleURL string) string {
	var b strings.Builder
	b.WriteString(header + "\n")
	b.WriteString(host + "\n")
	for _, it := range items {
		b.WriteString(it + "\n")
	}
	if consoleURL == "" {
		b.WriteString("\n" + watchdogLocalConsoleLine + "\n")
	} else {
		b.WriteString("\nMở Console: " + consoleURL + watchdogConsolePath + "\n")
	}
	b.WriteString(watchdogMsgFooterNote)
	return b.String()
}

// watchdogConsoleURL trả địa chỉ Console để chèn vào tin Telegram, hoặc "" khi chưa có địa chỉ mở được từ ngoài máy chủ
// (rỗng, localhost, *.localhost, 127.0.0.0/8, ::1, 0.0.0.0, hỏng) — cùng luật với console_line của API (F092).
func watchdogConsoleURL(_ *Env, publicURL string) string {
	raw := strings.TrimSpace(publicURL)
	if isLocalConsoleURL(raw) {
		return ""
	}
	return strings.TrimRight(raw, "/")
}

func isLocalConsoleURL(raw string) bool {
	if raw == "" {
		return true
	}
	if !strings.Contains(raw, "//") {
		raw = "//" + raw
	}
	u, err := url.Parse(raw)
	if err != nil {
		return true
	}
	host := strings.TrimRight(strings.ToLower(u.Hostname()), ".")
	if host == "" || host == "localhost" || strings.HasSuffix(host, ".localhost") {
		return true
	}
	if ip := net.ParseIP(host); ip != nil {
		return ip.IsLoopback() || ip.IsUnspecified()
	}
	return false
}

func watchdogHostname(deps WatchdogDeps) string {
	hn := deps.Hostname
	if hn == nil {
		hn = os.Hostname
	}
	h, err := hn()
	h = shortText(strings.Map(func(r rune) rune {
		if r < ' ' || r == 0x7f {
			return -1
		}
		return r
	}, h), 64)
	if err != nil || h == "" {
		return "Máy chủ Gen-Harness"
	}
	return "Máy chủ: " + h
}

// humanDuration: "2 giờ 5 phút", "3 ngày 4 giờ", "45 phút".
func humanDuration(d time.Duration) string {
	if d < time.Minute {
		return "dưới 1 phút"
	}
	days := int(d.Hours()) / 24
	hours := int(d.Hours()) % 24
	mins := int(d.Minutes()) % 60
	switch {
	case days > 0:
		return fmt.Sprintf("%d ngày %d giờ", days, hours)
	case hours > 0:
		return fmt.Sprintf("%d giờ %d phút", hours, mins)
	default:
		return fmt.Sprintf("%d phút", mins)
	}
}
