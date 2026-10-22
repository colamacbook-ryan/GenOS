package ops

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/notify"
)

const (
	wdToken     = "123456789:AAFakeTokenForTestOnly_abcdefghijkl"
	wdMasterHex = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
)

// wdFakeRunner trả kết quả docker theo trạng thái test đặt trước mỗi lượt.
type wdFakeRunner struct {
	mu        sync.Mutex
	ps        string
	psErr     error
	mget      string
	mgetErr   error
	backup    string
	backupErr error
	calls     []string
	onPS      func()
}

func (r *wdFakeRunner) Output(_ context.Context, cmd dockercli.Cmd) ([]byte, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	joined := strings.Join(cmd.Args, " ")
	r.calls = append(r.calls, joined)
	switch {
	case strings.Contains(joined, "ps --all --format json"):
		if r.onPS != nil {
			r.onPS()
		}
		return []byte(r.ps), r.psErr
	case strings.Contains(joined, "redis-cli"):
		return []byte(r.mget), r.mgetErr
	case strings.Contains(joined, "gh.backup list"):
		return []byte(r.backup), r.backupErr
	case len(cmd.Args) > 0 && cmd.Args[0] == "info":
		return nil, errors.New("docker info không có trong test")
	}
	return nil, nil
}

func (r *wdFakeRunner) Stream(context.Context, dockercli.Cmd, func(string)) error { return nil }
func (r *wdFakeRunner) RunIO(context.Context, dockercli.Cmd, io.Reader, io.Writer) error {
	return nil
}

func (r *wdFakeRunner) count(sub string) int {
	r.mu.Lock()
	defer r.mu.Unlock()
	n := 0
	for _, c := range r.calls {
		if strings.Contains(c, sub) {
			n++
		}
	}
	return n
}

// tgFake là Bot API giả: đếm POST sendMessage, trả mã theo hàng đợi (mặc định 200).
type tgFake struct {
	mu       sync.Mutex
	srv      *httptest.Server
	texts    []string
	paths    []string
	statuses []int
}

func newTGFake(t *testing.T) *tgFake {
	f := &tgFake{}
	f.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		defer f.mu.Unlock()
		var p map[string]any
		_ = json.NewDecoder(r.Body).Decode(&p)
		f.paths = append(f.paths, r.URL.Path)
		code := 200
		if len(f.statuses) > 0 {
			code, f.statuses = f.statuses[0], f.statuses[1:]
		}
		w.WriteHeader(code)
		if code == 200 {
			txt, _ := p["text"].(string)
			f.texts = append(f.texts, txt)
			_, _ = w.Write([]byte(`{"ok":true}`))
			return
		}
		_, _ = w.Write([]byte(`{"ok":false,"error_code":500,"description":"Internal"}`))
	}))
	t.Cleanup(f.srv.Close)
	return f
}

func (f *tgFake) sent() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]string(nil), f.texts...)
}

func (f *tgFake) posts() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.paths)
}

type wdHarness struct {
	t        *testing.T
	env      *Env
	compose  string
	now      time.Time
	boot     string
	runner   *wdFakeRunner
	tg       *tgFake
	readyOK  bool
	readyURL string
	deadURL  string
	diskFree uint64
	out      strings.Builder
	// nightly đếm số lần RunWatchdog làm mới run/nightly-status.json (hook giả — test
	// không hỏi systemctl/loginctl của máy chạy test).
	nightly int
}

func newWDHarness(t *testing.T, withTelegram bool) *wdHarness {
	t.Helper()
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	ready := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write([]byte(`{}`)) }))
	t.Cleanup(ready.Close)
	dead := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	deadURL := dead.URL
	dead.Close() // connection refused
	h := &wdHarness{t: t, env: env, compose: composePath, now: time.Date(2026, 10, 3, 10, 0, 0, 0, time.UTC), boot: "boot-a",
		runner: &wdFakeRunner{}, tg: newTGFake(t), readyOK: true, readyURL: ready.URL, deadURL: deadURL, diskFree: 100 << 30}
	sec := filepath.Join(env.InstallDir, "secrets")
	if err := os.MkdirAll(sec, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(sec, "gh_master_key"), []byte(wdMasterHex), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := hostlink.EnsureDir(env.InstallDir); err != nil {
		t.Fatal(err)
	}
	if withTelegram {
		h.writeTelegram(wdMasterHex)
	}
	h.healthy()
	return h
}

func (h *wdHarness) writeTelegram(keyHex string) {
	mk, err := notify.DecodeMasterKey(keyHex)
	if err != nil {
		h.t.Fatal(err)
	}
	blob, err := notify.SealEnvelope(mk, []byte(`{"token":"`+wdToken+`","chat_id":"987654321"}`), []byte(notify.TelegramAAD))
	if err != nil {
		h.t.Fatal(err)
	}
	b, _ := json.Marshal(map[string]any{"schema": 1, "enabled": true, "enc": base64.StdEncoding.EncodeToString(blob), "briefing": true, "reminders": true})
	if err := os.WriteFile(hostlink.TelegramConfigPath(h.env.InstallDir), b, 0o644); err != nil {
		h.t.Fatal(err)
	}
}

func psJSON(rows ...psRow) string {
	b, _ := json.Marshal(rows)
	return string(b)
}

var healthyRows = []psRow{
	{Service: "api", State: "running", Health: "healthy", Status: "Up 2 hours (healthy)"},
	{Service: "bridge", State: "running", Status: "Up 2 hours"},
	{Service: "db", State: "running", Health: "healthy", Status: "Up 2 hours (healthy)"},
	{Service: "migrate", State: "exited", Status: "Exited (0) 2 hours ago"},
	{Service: "proxy", State: "running", Status: "Up 2 hours"},
	{Service: "redis", State: "running", Health: "healthy", Status: "Up 2 hours (healthy)"},
	{Service: "worker", State: "running", Status: "Up 2 hours"},
}

func rowsWith(over ...psRow) []psRow {
	out := append([]psRow(nil), healthyRows...)
	for _, o := range over {
		for i := range out {
			if out[i].Service == o.Service {
				out[i] = o
			}
		}
	}
	return out
}

// healthy: mọi dịch vụ chạy, nhịp worker/bridge mới, bản sao lưu 1 giờ trước.
func (h *wdHarness) healthy() {
	h.runner.ps = psJSON(healthyRows...)
	h.runner.psErr = nil
	h.readyOK = true
	beat := h.now.Format(time.RFC3339)
	h.runner.mget = beat + "\n" + beat + "\n"
	h.runner.backup = "INFO:gh.backup:" + h.now.Add(-time.Hour).Format("2006-01-02T15:04:05.000000+00:00") + "  backups/x.enc  10 byte  CSDL=gh\n"
}

func (h *wdHarness) advance(d time.Duration) {
	h.now = h.now.Add(d)
	beat := h.now.Format(time.RFC3339)
	if h.runner.mget != "" && !strings.HasPrefix(h.runner.mget, "2025") {
		h.runner.mget = beat + "\n" + beat + "\n"
	}
	h.runner.backup = "INFO:gh.backup:" + h.now.Add(-time.Hour).Format("2006-01-02T15:04:05.000000+00:00") + "  backups/x.enc  10 byte  CSDL=gh\n"
}

func (h *wdHarness) run(opts WatchdogOptions) error {
	url := h.readyURL
	if !h.readyOK {
		url = h.deadURL
	}
	deps := WatchdogDeps{
		Runner:      h.runner,
		ReadyClient: &http.Client{Timeout: 2 * time.Second},
		ReadyURL:    url,
		Telegram:    &notify.Client{BaseURL: h.tg.srv.URL, HTTP: h.tg.srv.Client()},
		Now:         func() time.Time { return h.now },
		DiskFree:    func(string) (uint64, error) { return h.diskFree, nil },
		Hostname:    func() (string, error) { return "may-chu-test", nil },
		BootID:      func() string { return h.boot },
		Sleep:       func(context.Context, time.Duration) error { return nil },
		Nightly:     func(context.Context, *Env) { h.nightly++ },
	}
	return RunWatchdog(context.Background(), h.env, opts, deps, &h.out)
}

func (h *wdHarness) mustRun(opts WatchdogOptions) {
	h.t.Helper()
	if err := h.run(opts); err != nil {
		h.t.Fatalf("RunWatchdog: %v", err)
	}
}

func (h *wdHarness) status() hostlink.WatchdogStatus {
	h.t.Helper()
	st, err := hostlink.ReadWatchdogStatus(h.env.InstallDir)
	if err != nil {
		h.t.Fatal(err)
	}
	return st
}

func (h *wdHarness) writeAPIHealth(writtenAt time.Time, alerts []hostlink.APIAlert, latestBackup *time.Time, limit int) {
	h.t.Helper()
	v := map[string]any{"schema": 1, "written_at": writtenAt.Format(time.RFC3339), "version": "v0.1.44", "public_url": "https://gh.example.vn",
		"alerts": alerts, "backup_stale_limit_hours": limit}
	if latestBackup != nil {
		v["latest_backup_at"] = latestBackup.Format(time.RFC3339)
	} else {
		v["latest_backup_at"] = nil
	}
	b, _ := json.Marshal(v)
	if err := os.WriteFile(hostlink.APIHealthPath(h.env.InstallDir), b, 0o644); err != nil {
		h.t.Fatal(err)
	}
}

func hasIncident(st hostlink.WatchdogStatus, key string) bool {
	for _, i := range st.Incidents {
		if i.Key == key {
			return true
		}
	}
	return false
}

func TestWatchdog_APIDownSendsExactlyOnce(t *testing.T) {
	h := newWDHarness(t, true)
	// Lượt 1: api exited, /ready từ chối kết nối.
	h.runner.ps = psJSON(rowsWith(psRow{Service: "api", State: "exited", Status: "Exited (1) 3 minutes ago"})...)
	h.readyOK = false
	h.mustRun(WatchdogOptions{Quiet: true})
	sent := h.tg.sent()
	if len(sent) != 1 || h.tg.posts() != 1 {
		t.Fatalf("lượt 1 phải đúng 1 tin, được %d: %q", h.tg.posts(), sent)
	}
	for _, want := range []string{"Gen-Harness · CẢNH BÁO", "Máy chủ: may-chu-test", "• Máy chủ ứng dụng (api) không chạy", "Đã tự khởi động lại api lúc",
		"Mở Console từ máy chủ Gen-Harness (chưa bật Truy cập từ xa", "mọi thao tác Sếp xác nhận trong Console"} {
		if !strings.Contains(sent[0], want) {
			t.Errorf("tin thiếu %q:\n%s", want, sent[0])
		}
	}
	// F092: chưa có public_url ⇒ không chèn link localhost vào tin (điện thoại không mở được).
	if strings.Contains(sent[0], "localhost") || strings.Contains(sent[0], "https://") {
		t.Errorf("tin không được chèn link localhost:\n%s", sent[0])
	}
	if h.tg.paths[0] != "/bot"+wdToken+"/sendMessage" {
		t.Fatalf("path %q", h.tg.paths[0])
	}
	if n := h.runner.count("up -d --no-deps api"); n != 1 {
		t.Fatalf("phải tự khởi động lại api đúng 1 lần, được %d", n)
	}
	st := h.status()
	if st.State != hostlink.WatchdogStateIssues || !hasIncident(st, "api.down") || st.Telegram != hostlink.TelegramOK || st.LastSentAt == "" {
		t.Fatalf("status lượt 1 = %+v", st)
	}

	// Lượt 2 (12 phút sau, api vẫn chết): không gửi, không khởi động lại (< 60 phút).
	h.advance(12 * time.Minute)
	h.mustRun(WatchdogOptions{Quiet: true})
	if h.tg.posts() != 1 {
		t.Fatalf("lượt 2 không được gửi thêm, tổng %d", h.tg.posts())
	}
	if n := h.runner.count("up -d --no-deps api"); n != 1 {
		t.Fatalf("lượt 2 không được khởi động lại lần nữa, tổng %d", n)
	}

	// Lượt 3: api healthy, /ready 200 ⇒ đúng 1 tin ĐÃ ỔN.
	h.advance(12 * time.Minute)
	h.healthy()
	h.mustRun(WatchdogOptions{Quiet: true})
	sent = h.tg.sent()
	if len(sent) != 2 || !strings.Contains(sent[1], "Gen-Harness · ĐÃ ỔN") || !strings.Contains(sent[1], "Máy chủ ứng dụng (api) không chạy: đã hết.") {
		t.Fatalf("lượt 3 phải 1 tin ĐÃ ỔN: %q", sent)
	}
	if st := h.status(); st.State != hostlink.WatchdogStateOK || len(st.Incidents) != 0 {
		t.Fatalf("status lượt 3 = %+v", st)
	}

	// Lượt 4: không gì mới ⇒ 0 tin.
	h.advance(12 * time.Minute)
	h.mustRun(WatchdogOptions{Quiet: true})
	if h.tg.posts() != 2 {
		t.Fatalf("lượt 4 không được gửi, tổng %d", h.tg.posts())
	}
	// Sau 60 phút api chết lại ⇒ được khởi động lại lần nữa.
	h.advance(61 * time.Minute)
	h.runner.ps = psJSON(rowsWith(psRow{Service: "api", State: "exited"})...)
	h.mustRun(WatchdogOptions{Quiet: true})
	if n := h.runner.count("up -d --no-deps api"); n != 2 {
		t.Fatalf("sau 60 phút được khởi động lại, tổng %d", n)
	}
}

func TestWatchdog_NhieuSuCoMotTin_VaKhongBaoLai(t *testing.T) {
	h := newWDHarness(t, true)
	h.runner.ps = psJSON(rowsWith(
		psRow{Service: "worker", State: "exited", Status: "Exited (137)"},
		psRow{Service: "db", State: "running", Health: "unhealthy", Status: "Up 1 hour (unhealthy)"},
	)...)
	h.diskFree = 1 << 20
	h.mustRun(WatchdogOptions{Quiet: true})
	sent := h.tg.sent()
	if len(sent) != 1 {
		t.Fatalf("nhiều sự cố cùng lượt vẫn 1 tin, được %d", len(sent))
	}
	for _, want := range []string{"Dịch vụ worker đã dừng", "Dịch vụ db không khoẻ", "Ổ đĩa sắp đầy", "Đã tự khởi động lại worker lúc", "Đã tự khởi động lại db lúc"} {
		if !strings.Contains(sent[0], want) {
			t.Errorf("thiếu %q:\n%s", want, sent[0])
		}
	}
	if h.runner.count("up -d --no-deps worker") != 1 || h.runner.count("restart db") != 1 {
		t.Fatalf("worker exited ⇒ up -d --no-deps, db unhealthy ⇒ restart: %v", h.runner.calls)
	}
	if h.runner.count("migrate") != 0 {
		t.Fatal("migrate (chạy một lần) không được tự khởi động lại")
	}
	ds, err := hostlink.ReadDiskStatus(h.env.InstallDir)
	if err != nil || ds.State != "low" {
		t.Fatalf("disk-status.json phải ghi low: %+v, %v", ds, err)
	}
	// Lượt sau: worker chạy lại nhưng im lặng (sự cố MỚI), db vẫn unhealthy, đĩa
	// vẫn đầy (đã báo ⇒ không báo lại) ⇒ 1 CẢNH BÁO chỉ có sự cố mới + 1 ĐÃ ỔN.
	h.advance(12 * time.Minute)
	h.runner.ps = psJSON(rowsWith(psRow{Service: "db", State: "running", Health: "unhealthy", Status: "Up 1 hour (unhealthy)"})...)
	h.runner.mget = "2025-01-01T00:00:00Z\n" + h.now.Format(time.RFC3339) + "\n"
	h.mustRun(WatchdogOptions{Quiet: true})
	sent = h.tg.sent()
	if len(sent) != 3 || !strings.Contains(sent[1], "Tiến trình nền (worker) im lặng") || strings.Contains(sent[1], "Ổ đĩa") || strings.Contains(sent[1], "db") {
		t.Fatalf("lượt 2 chỉ báo sự cố mới: %q", sent)
	}
	if !strings.Contains(sent[2], "ĐÃ ỔN") || !strings.Contains(sent[2], "Dịch vụ worker đã dừng: đã hết.") {
		t.Fatalf("lượt 2 phải báo worker đã ổn: %q", sent[2])
	}
	if h.runner.count("restart db") != 1 {
		t.Fatal("db vẫn unhealthy nhưng < 60 phút: không khởi động lại lần nữa")
	}
}

func TestWatchdog_GuiLoi5xx_LuotSauGuiLai(t *testing.T) {
	h := newWDHarness(t, true)
	h.tg.statuses = []int{500}
	h.runner.ps = psJSON(rowsWith(psRow{Service: "api", State: "exited"})...)
	h.mustRun(WatchdogOptions{Quiet: true})
	if len(h.tg.sent()) != 0 || h.tg.posts() != 1 {
		t.Fatalf("lượt 1 gửi lỗi: posts %d", h.tg.posts())
	}
	st := h.status()
	if st.Telegram != hostlink.TelegramFailed || st.TelegramErrorCode != notify.CodeUnreachable {
		t.Fatalf("status = %+v", st)
	}
	h.advance(12 * time.Minute)
	h.mustRun(WatchdogOptions{Quiet: true})
	sent := h.tg.sent()
	if len(sent) != 1 || !strings.Contains(sent[0], "CẢNH BÁO") {
		t.Fatalf("lượt sau phải gửi lại: %q", sent)
	}
	if st := h.status(); st.Telegram != hostlink.TelegramOK || st.TelegramErrorCode != "" {
		t.Fatalf("status sau khi gửi được = %+v", st)
	}
}

func TestWatchdog_APIHealthCu_KhongDaOnGia(t *testing.T) {
	h := newWDHarness(t, true)
	zalo := hostlink.APIAlert{Key: "channel.down:zalo", Kind: "channel", Severity: "bad", Title: "Kênh Zalo mất kết nối", Body: "Quét lại QR trong Console.", Fingerprint: "zalo-1"}
	h.writeAPIHealth(h.now.Add(-time.Minute), []hostlink.APIAlert{zalo}, nil, 0)
	h.mustRun(WatchdogOptions{Quiet: true})
	if s := h.tg.sent(); len(s) != 1 || !strings.Contains(s[0], "Kênh Zalo mất kết nối: Quét lại QR") {
		t.Fatalf("phải báo sự cố từ api: %q", s)
	}
	if !strings.Contains(h.tg.sent()[0], "Mở Console: https://gh.example.vn/connections#telegram") {
		t.Fatalf("Console URL phải lấy public_url: %s", h.tg.sent()[0])
	}
	// api chết ⇒ api-health không còn tươi ⇒ channel.down:zalo GIỮ NGUYÊN.
	h.advance(30 * time.Minute)
	h.runner.ps = psJSON(rowsWith(psRow{Service: "api", State: "exited"})...)
	h.mustRun(WatchdogOptions{Quiet: true})
	sent := h.tg.sent()
	if len(sent) != 2 || strings.Contains(sent[1], "ĐÃ ỔN") || !strings.Contains(sent[1], "api") {
		t.Fatalf("api-health cũ không được 'đã ổn' giả: %q", sent)
	}
	if st := h.status(); !hasIncident(st, "channel.down:zalo") {
		t.Fatalf("channel.down:zalo phải còn mở: %+v", st.Incidents)
	}
	// api sống lại, api-health tươi không còn alert ⇒ một tin ĐÃ ỔN gộp cả hai.
	h.advance(12 * time.Minute)
	h.healthy()
	h.writeAPIHealth(h.now, nil, nil, 0)
	h.mustRun(WatchdogOptions{Quiet: true})
	sent = h.tg.sent()
	if len(sent) != 3 || !strings.Contains(sent[2], "ĐÃ ỔN") || !strings.Contains(sent[2], "Kênh Zalo") || !strings.Contains(sent[2], "api") {
		t.Fatalf("phải 1 tin ĐÃ ỔN gộp: %q", sent)
	}
}

func TestWatchdog_KhoaTrungApiGenh_MotDong(t *testing.T) {
	h := newWDHarness(t, true)
	old := h.now.Add(-50 * time.Hour)
	dup := hostlink.APIAlert{Key: "backup.stale", Severity: "warn", Title: "Sao lưu cũ (api)", Body: "từ api"}
	h.writeAPIHealth(h.now, []hostlink.APIAlert{dup}, &old, 36)
	h.mustRun(WatchdogOptions{Quiet: true})
	sent := h.tg.sent()
	if len(sent) != 1 {
		t.Fatalf("%q", sent)
	}
	if strings.Count(sent[0], "• ") != 1 || !strings.Contains(sent[0], "Bản sao lưu trong máy đã cũ") || strings.Contains(sent[0], "từ api") {
		t.Fatalf("khoá trùng ⇒ 1 dòng, số đo genh thắng:\n%s", sent[0])
	}
	if h.runner.count("gh.backup list") != 0 {
		t.Fatal("api-health tươi có latest_backup_at thì không cần hỏi worker")
	}
}

func TestWatchdog_BackupStale_TuFallbackWorker(t *testing.T) {
	h := newWDHarness(t, true)
	h.runner.backup = "INFO:gh.backup:" + h.now.Add(-40*time.Hour).Format("2006-01-02T15:04:05.000000+00:00") + "  backups/a.enc  1 byte  CSDL=gh\n" +
		"INFO:gh.backup:" + h.now.Add(-80*time.Hour).Format("2006-01-02T15:04:05+00:00") + "  backups/b.enc  1 byte  CSDL=gh\n"
	h.mustRun(WatchdogOptions{Quiet: true})
	if s := h.tg.sent(); len(s) != 1 || !strings.Contains(s[0], "Bản sao lưu trong máy đã cũ") || !strings.Contains(s[0], "quá 36 giờ") {
		t.Fatalf("fallback worker (mặc định 36 giờ): %q", s)
	}
	if h.runner.count("exec -T worker sh -c python -m gh.backup list 2>&1") != 1 {
		t.Fatalf("phải hỏi worker: %v", h.runner.calls)
	}
	// Không đo được (worker lỗi) ⇒ không đóng.
	h.advance(12 * time.Minute)
	h.runner.backupErr = errors.New("worker bận")
	h.mustRun(WatchdogOptions{Quiet: true})
	if !hasIncident(h.status(), "backup.stale") || len(h.tg.sent()) != 1 {
		t.Fatal("không đo được thì giữ nguyên, không 'đã ổn'")
	}
}

func TestWatchdog_TamDungVaBan(t *testing.T) {
	h := newWDHarness(t, true)
	h.runner.ps = psJSON(rowsWith(psRow{Service: "api", State: "exited"})...)
	if err := WriteOwnerPause(h.env.InstallDir, h.now); err != nil {
		t.Fatal(err)
	}
	h.mustRun(WatchdogOptions{Quiet: true})
	if h.tg.posts() != 0 || h.runner.count("up -d") != 0 || h.runner.count("restart") != 0 || h.runner.count("ps --all") != 0 {
		t.Fatalf("tạm dừng ⇒ 0 tin, 0 restart, không đo: %v", h.runner.calls)
	}
	if st := h.status(); st.State != hostlink.WatchdogStatePaused {
		t.Fatalf("state = %q", st.State)
	}
	_ = ClearOwnerPause(h.env.InstallDir)

	lock, err := hostlink.AcquireLock(h.env.InstallDir)
	if err != nil {
		t.Fatal(err)
	}
	h.mustRun(WatchdogOptions{Quiet: true})
	lock.Release()
	if st := h.status(); st.State != hostlink.WatchdogStateSkippedBusy {
		t.Fatalf("khoá loại trừ bận ⇒ skipped_busy, được %q", st.State)
	}
	if h.tg.posts() != 0 || h.runner.count("ps --all") != 0 {
		t.Fatal("bận ⇒ không đo, không gửi")
	}

	// Lượt khác đang giữ watchdog.lock ⇒ thoát êm, không ghi gì.
	wl, err := hostlink.AcquireWatchdogLock(h.env.InstallDir)
	if err != nil {
		t.Fatal(err)
	}
	before := h.status().LastRunAt
	h.advance(time.Minute)
	h.mustRun(WatchdogOptions{Quiet: true})
	wl.Release()
	if h.status().LastRunAt != before {
		t.Fatal("lượt trùng không được ghi status")
	}
}

func TestWatchdog_TelegramChuaCauHinh_VaKeyMismatch(t *testing.T) {
	h := newWDHarness(t, false)
	h.runner.ps = psJSON(rowsWith(psRow{Service: "api", State: "exited"})...)
	if err := h.run(WatchdogOptions{Quiet: true}); err != nil {
		t.Fatalf("chưa cấu hình Telegram vẫn phải nil: %v", err)
	}
	st := h.status()
	if st.Telegram != hostlink.TelegramNotConfigured || !hasIncident(st, "api.down") || h.tg.posts() != 0 {
		t.Fatalf("status = %+v", st)
	}
	if h.runner.count("up -d --no-deps api") != 1 {
		t.Fatal("chưa cấu hình Telegram vẫn tự khởi động lại")
	}

	h2 := newWDHarness(t, false)
	h2.writeTelegram(strings.Repeat("ab", 32)) // mã hoá bằng khoá master KHÁC
	h2.mustRun(WatchdogOptions{Quiet: true, Test: true})
	st = h2.status()
	if st.Telegram != hostlink.TelegramKeyMismatch || h2.tg.posts() != 0 {
		t.Fatalf("key_mismatch: %+v", st)
	}
	if st.Test == nil || st.Test.OK || st.Test.ErrorCode != notify.CodeKeyMismatch {
		t.Fatalf("gửi thử khi khoá lệch: %+v", st.Test)
	}
	// Tắt (enabled=false) ⇒ disabled.
	_ = os.WriteFile(hostlink.TelegramConfigPath(h2.env.InstallDir), []byte(`{"schema":1,"enabled":false}`), 0o644)
	h2.mustRun(WatchdogOptions{Quiet: true})
	if st := h2.status(); st.Telegram != hostlink.TelegramDisabled {
		t.Fatalf("enabled=false ⇒ disabled, được %q", st.Telegram)
	}
}

func TestWatchdog_GuiThu(t *testing.T) {
	h := newWDHarness(t, true)
	h.mustRun(WatchdogOptions{Quiet: true, Test: true})
	sent := h.tg.sent()
	if len(sent) != 1 || !strings.HasPrefix(sent[0], "Gen-Harness · Tin thử từ trực canh máy chủ\n") {
		t.Fatalf("tin thử: %q", sent)
	}
	st := h.status()
	if st.Test == nil || !st.Test.OK || st.Test.ErrorCode != "" || st.Test.At == "" {
		t.Fatalf("test = %+v", st.Test)
	}
	// Lượt thường sau đó vẫn giữ kết quả gửi thử.
	h.advance(12 * time.Minute)
	h.mustRun(WatchdogOptions{Quiet: true})
	if st := h.status(); st.Test == nil || !st.Test.OK {
		t.Fatalf("test phải được giữ: %+v", st.Test)
	}
}

func TestWatchdog_BootIDDoi_TinKhoiDongLai(t *testing.T) {
	h := newWDHarness(t, true)
	h.mustRun(WatchdogOptions{Quiet: true})
	h.advance(2*time.Hour + 5*time.Minute)
	h.boot = "boot-b"
	h.mustRun(WatchdogOptions{Quiet: true})
	sent := h.tg.sent()
	if len(sent) != 1 || !strings.Contains(sent[0], "Máy chủ vừa khởi động lại (tắt khoảng 2 giờ 5 phút)") {
		t.Fatalf("tin khởi động lại: %q", sent)
	}
	// boot_id đổi nhưng lần trước mới 12 phút ⇒ không báo.
	h.advance(12 * time.Minute)
	h.boot = "boot-c"
	h.mustRun(WatchdogOptions{Quiet: true})
	if len(h.tg.sent()) != 1 {
		t.Fatal("khoảng nghỉ < 30 phút không báo khởi động lại")
	}
}

func TestWatchdog_DockerDown(t *testing.T) {
	h := newWDHarness(t, true)
	h.runner.psErr = errors.New("Cannot connect to the Docker daemon")
	h.mustRun(WatchdogOptions{Quiet: true})
	sent := h.tg.sent()
	if len(sent) != 1 || !strings.Contains(sent[0], "Docker không chạy") {
		t.Fatalf("%q", sent)
	}
	st := h.status()
	if !hasIncident(st, "docker.down") || hasIncident(st, "api.down") {
		t.Fatalf("docker lỗi ⇒ docker.down (không thêm api.down): %+v", st.Incidents)
	}
	if h.runner.count("redis-cli") != 0 {
		t.Fatal("docker lỗi thì không hỏi redis")
	}
}

func TestWatchdog_WorkerSilentVaBridgeSilent(t *testing.T) {
	h := newWDHarness(t, true)
	old := h.now.Add(-25 * time.Minute).Format("2006-01-02T15:04:05.000000Z")
	h.runner.mget = old + "\n\n" // bridge mất khoá (TTL 45 giây)
	h.mustRun(WatchdogOptions{Quiet: true})
	sent := h.tg.sent()
	if len(sent) != 1 || !strings.Contains(sent[0], "Tiến trình nền (worker) im lặng") || !strings.Contains(sent[0], "Cầu nối kênh (bridge) im lặng") {
		t.Fatalf("%q", sent)
	}
	if h.runner.count("exec -T redis redis-cli --raw MGET gh:worker:heartbeat gh:bridge:heartbeat") != 1 {
		t.Fatalf("lệnh MGET: %v", h.runner.calls)
	}
	// bridge vừa khởi động (< 2 phút) ⇒ chưa coi là im lặng.
	h2 := newWDHarness(t, true)
	h2.runner.ps = psJSON(rowsWith(psRow{Service: "bridge", State: "running", Status: "Up 30 seconds"})...)
	h2.runner.mget = h2.now.Format(time.RFC3339) + "\n\n"
	h2.mustRun(WatchdogOptions{Quiet: true})
	if h2.tg.posts() != 0 {
		t.Fatalf("bridge mới chạy không báo: %q", h2.tg.sent())
	}
}

func TestWatchdog_OffsiteStaleVaFailed(t *testing.T) {
	h := newWDHarness(t, true)
	err := hostlink.WriteOffsiteStatus(h.env.InstallDir, hostlink.OffsiteStatus{Configured: true, State: hostlink.OffsiteStateFailed, ErrorCode: "GH-EB01",
		LastAttemptAt: h.now.Add(-time.Hour).Format(time.RFC3339), LastSuccessAt: h.now.Add(-8 * 24 * time.Hour).Format(time.RFC3339)})
	if err != nil {
		t.Fatal(err)
	}
	h.mustRun(WatchdogOptions{Quiet: true})
	sent := h.tg.sent()
	if len(sent) != 1 || !strings.Contains(sent[0], "Bản sao ngoài máy đã cũ") || !strings.Contains(sent[0], "Sao lưu ra ổ ngoài thất bại: ") || !strings.Contains(sent[0], "(GH-EB01)") {
		t.Fatalf("%q", sent)
	}
}

func TestWatchdog_UpdateFailed24h(t *testing.T) {
	h := newWDHarness(t, true)
	if err := hostlink.Start(h.env.InstallDir, "v0.1.43"); err != nil {
		t.Fatal(err)
	}
	if err := hostlink.Finish(h.env.InstallDir, "failed", "v0.1.44", "Bản mới lỗi ở bước migrate (GH-E942) — đã tự quay về."); err != nil {
		t.Fatal(err)
	}
	// Finish dùng giờ thật — đặt "bây giờ" của test ngay sau đó.
	h.now = time.Now().UTC().Add(time.Hour)
	h.healthy()
	h.mustRun(WatchdogOptions{Quiet: true})
	if s := h.tg.sent(); len(s) != 1 || !strings.Contains(s[0], "Cập nhật thất bại") || !strings.Contains(s[0], "GH-E942") {
		t.Fatalf("%q", s)
	}
	h.advance(25 * time.Hour)
	h.mustRun(WatchdogOptions{Quiet: true})
	if hasIncident(h.status(), "update.failed") {
		t.Fatal("quá 24 giờ thì update.failed tự hết")
	}
}

// watchdog-status.json đúng khoá hợp đồng; token không lọt vào out/status/state/log.
func TestWatchdog_StatusHopDong_VaKhongLoToken(t *testing.T) {
	h := newWDHarness(t, true)
	h.tg.statuses = []int{500}
	h.runner.ps = psJSON(rowsWith(psRow{Service: "api", State: "exited"})...)
	h.mustRun(WatchdogOptions{Quiet: false, Test: true})
	raw, err := os.ReadFile(hostlink.WatchdogStatusPath(h.env.InstallDir))
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"schema", "last_run_at", "state", "incidents", "telegram", "telegram_error_code", "last_sent_at", "schedule", "test"} {
		if _, ok := m[k]; !ok {
			t.Errorf("watchdog-status.json thiếu %q", k)
		}
	}
	inc := m["incidents"].([]any)[0].(map[string]any)
	for _, k := range []string{"key", "severity", "title", "since"} {
		if _, ok := inc[k]; !ok {
			t.Errorf("incident thiếu %q", k)
		}
	}
	test := m["test"].(map[string]any)
	for _, k := range []string{"at", "ok", "error_code"} {
		if _, ok := test[k]; !ok {
			t.Errorf("test thiếu %q", k)
		}
	}
	state, _ := os.ReadFile(WatchdogStatePath(h.env.InstallDir))
	if fi, _ := os.Stat(WatchdogStatePath(h.env.InstallDir)); runtime.GOOS != "windows" && fi.Mode().Perm() != 0o600 {
		t.Fatalf("watchdog-state.json phải 0600, được %v", fi.Mode().Perm())
	}
	var sm map[string]any
	if err := json.Unmarshal(state, &sm); err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"schema", "boot_id", "last_run_at", "incidents", "resolved_pending", "restarts"} {
		if _, ok := sm[k]; !ok {
			t.Errorf("watchdog-state.json thiếu %q", k)
		}
	}
	logs := ""
	if b, err := os.ReadFile(WatchdogLogPath(h.env.InstallDir)); err == nil {
		logs = string(b)
	}
	for name, s := range map[string]string{"out": h.out.String(), "status": string(raw), "state": string(state), "log": logs} {
		if strings.Contains(s, wdToken) || strings.Contains(s, "AAFakeToken") {
			t.Errorf("%s lộ token Telegram", name)
		}
	}
	if !strings.Contains(h.out.String(), "Trực canh:") {
		t.Fatalf("out phải có dòng tóm tắt: %s", h.out.String())
	}
}

func TestWatchdog_XoayLog(t *testing.T) {
	h := newWDHarness(t, false)
	path := WatchdogLogPath(h.env.InstallDir)
	_ = os.MkdirAll(filepath.Dir(path), 0o755)
	if err := os.WriteFile(path, make([]byte, watchdogLogMaxBytes+1), 0o644); err != nil {
		t.Fatal(err)
	}
	h.mustRun(WatchdogOptions{Quiet: true})
	if _, err := os.Stat(path + ".1"); err != nil {
		t.Fatalf("log > 5 MB phải đổi tên .1: %v", err)
	}
}

func TestWatchdog_QuietChiInKhiCoThayDoi(t *testing.T) {
	h := newWDHarness(t, true)
	h.mustRun(WatchdogOptions{Quiet: true})
	if h.out.Len() != 0 {
		t.Fatalf("quiet + không đổi ⇒ không in: %q", h.out.String())
	}
	h.runner.ps = psJSON(rowsWith(psRow{Service: "api", State: "exited"})...)
	h.mustRun(WatchdogOptions{Quiet: true})
	if !strings.Contains(h.out.String(), "sự cố mới: api.down") {
		t.Fatalf("có thay đổi phải in: %q", h.out.String())
	}
}

func TestUpFor(t *testing.T) {
	cases := map[string]time.Duration{"Up 5 minutes (healthy)": 5 * time.Minute, "Up About a minute": time.Minute, "Up About an hour": time.Hour,
		"Up Less than a second": 0, "Up 2 hours": 2 * time.Hour, "Up 3 days": 72 * time.Hour}
	for in, want := range cases {
		if got, ok := upFor(in); !ok || got != want {
			t.Errorf("upFor(%q) = %v, %v", in, got, ok)
		}
	}
	if _, ok := upFor("Exited (0) 2 hours ago"); ok {
		t.Error("Exited không phải Up")
	}
}

// Không có container api (đã gỡ) ⇒ không tự `up -d` (tạo lại container/volume
// rỗng); chỉ báo.
func TestWatchdog_KhongCoContainerAPI_KhongTaoLai(t *testing.T) {
	h := newWDHarness(t, true)
	var rows []psRow
	for _, r := range healthyRows {
		if r.Service != "api" {
			rows = append(rows, r)
		}
	}
	h.runner.ps = psJSON(rows...)
	h.readyOK = false
	h.mustRun(WatchdogOptions{Quiet: true})
	if n := h.runner.count("up -d"); n != 0 {
		t.Fatalf("không được tạo lại api: %v", h.runner.calls)
	}
	if !hasIncident(h.status(), "api.down") {
		t.Fatal("vẫn phải báo api.down")
	}
	if sent := h.tg.sent(); len(sent) != 1 || !strings.Contains(sent[0], "genh start") {
		t.Fatalf("tin phải dặn chạy genh start: %q", sent)
	}
}

// Owner `genh stop` giữa lượt (sau khi lượt đã qua kiểm tạm dừng) ⇒ không restart.
func TestWatchdog_TamDungGiuaLuot_KhongRestart(t *testing.T) {
	h := newWDHarness(t, true)
	h.runner.ps = psJSON(rowsWith(psRow{Service: "api", State: "exited"}, psRow{Service: "worker", State: "exited"})...)
	h.readyOK = false
	h.runner.onPS = func() { _ = WriteOwnerPause(h.env.InstallDir, h.now) }
	h.mustRun(WatchdogOptions{Quiet: true})
	if n := h.runner.count("up -d"); n != 0 {
		t.Fatalf("đã tạm dừng thì không được up -d: %v", h.runner.calls)
	}
}

// update/restore/import lấy genh.lock SAU lần kiểm đầu lượt ⇒ không restart
// (không dựng lại service bằng compose/env cũ giữa lúc đang cập nhật).
func TestWatchdog_KhoaBanGiuaLuot_KhongRestart(t *testing.T) {
	h := newWDHarness(t, true)
	h.runner.ps = psJSON(rowsWith(psRow{Service: "worker", State: "exited"},
		psRow{Service: "db", State: "running", Health: "unhealthy", Status: "Up 1 hour (unhealthy)"})...)
	var held *hostlink.Lock
	h.runner.onPS = func() {
		if held == nil {
			l, err := hostlink.AcquireLock(h.env.InstallDir)
			if err != nil {
				t.Errorf("lấy genh.lock: %v", err)
				return
			}
			held = l
		}
	}
	h.mustRun(WatchdogOptions{Quiet: true})
	if held != nil {
		held.Release()
	}
	if h.runner.count("up -d") != 0 || h.runner.count("restart db") != 0 {
		t.Fatalf("genh.lock đang bị giữ thì không được restart/up -d: %v", h.runner.calls)
	}
	if st := loadWatchdogState(h.env.InstallDir); len(st.Restarts) != 0 {
		t.Fatalf("không restart thì không ghi mốc restart (lượt sau còn thử được): %v", st.Restarts)
	}
}

// restarting/created/paused cũng là sự cố; restarting không restart chồng.
func TestWatchdog_RestartingCreated_LaSuCo(t *testing.T) {
	h := newWDHarness(t, true)
	h.runner.ps = psJSON(rowsWith(psRow{Service: "worker", State: "restarting", Status: "Restarting (1) 5 seconds ago"},
		psRow{Service: "bridge", State: "created", Status: "Created"})...)
	h.mustRun(WatchdogOptions{Quiet: true})
	st := h.status()
	if !hasIncident(st, "service.unhealthy:worker") || !hasIncident(st, "service.unhealthy:bridge") {
		t.Fatalf("thiếu sự cố restarting/created: %+v", st.Incidents)
	}
	if h.runner.count("restart worker") != 0 || h.runner.count("up -d") != 0 {
		t.Fatalf("không restart chồng khi docker đang tự thử lại: %v", h.runner.calls)
	}
}

// "Gửi thử" khi lượt định kỳ đang giữ khoá ⇒ chờ rồi gửi, không mất tin thử.
func TestWatchdog_GuiThu_ChoLuotDangChay(t *testing.T) {
	h := newWDHarness(t, true)
	held, err := hostlink.AcquireWatchdogLock(h.env.InstallDir)
	if err != nil {
		t.Fatal(err)
	}
	go func() {
		time.Sleep(100 * time.Millisecond)
		held.Release()
	}()
	h.mustRun(WatchdogOptions{Quiet: true, Test: true})
	if st := h.status(); st.Test == nil || !st.Test.OK {
		t.Fatalf("gửi thử phải chờ khoá rồi gửi: %+v", st.Test)
	}
}

// v0.1.53 (F-99): mỗi lượt trực canh (12 phút) làm mới run/nightly-status.json để Console thấy lịch
// đêm im lặng kể cả khi không ai chạy genh. Lượt bị bỏ qua vì khoá riêng bận thì không làm mới.
func TestWatchdog_LamMoiNightlyStatus(t *testing.T) {
	h := newWDHarness(t, false)
	h.mustRun(WatchdogOptions{Quiet: true})
	if h.nightly != 1 {
		t.Fatalf("mỗi lượt phải làm mới nightly-status đúng 1 lần, được %d", h.nightly)
	}
	h.advance(12 * time.Minute)
	h.mustRun(WatchdogOptions{Quiet: true})
	if h.nightly != 2 {
		t.Fatalf("lượt 2: %d", h.nightly)
	}
	lock, err := hostlink.AcquireWatchdogLock(h.env.InstallDir)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Release()
	h.mustRun(WatchdogOptions{Quiet: true})
	if h.nightly != 2 {
		t.Fatalf("khoá bận ⇒ lượt bị bỏ qua, không làm mới: %d", h.nightly)
	}
}

func TestWatchdogConsoleURL_ChiLinkKhiMoDuocTuNgoai(t *testing.T) {
	env := testEnv(t, filepath.Join(t.TempDir(), "compose.yml"))
	for _, c := range []struct{ public, want string }{
		{"https://gh.example.vn", "https://gh.example.vn"},
		{"https://gh.example.vn/", "https://gh.example.vn"},
		{"http://203.0.113.5:8443", "http://203.0.113.5:8443"},
		{"", ""},
		{"  ", ""},
		{"https://localhost:8443", ""},
		{"https://LOCALHOST.:8443/", ""},
		{"https://gh.localhost", ""},
		{"http://127.0.0.1:8080", ""},
		{"http://127.5.6.7", ""},
		{"https://[::1]:8443", ""},
		{"http://0.0.0.0:8443", ""},
		{"localhost:8443", ""},
		{"://hỏng", ""},
	} {
		if got := watchdogConsoleURL(env, c.public); got != c.want {
			t.Errorf("watchdogConsoleURL(%q) = %q, muốn %q", c.public, got, c.want)
		}
	}
}

func TestWatchdogMessage_KhongLinkKhiConsoleURLRong(t *testing.T) {
	local := watchdogMessage("Gen-Harness · CẢNH BÁO", "Máy chủ: x", []string{"• a"}, "")
	if strings.Contains(local, "https://") || strings.Contains(local, "Mở Console:") ||
		!strings.Contains(local, "Mở Console từ máy chủ Gen-Harness (chưa bật Truy cập từ xa") {
		t.Errorf("tin local sai:\n%s", local)
	}
	remote := watchdogMessage("Gen-Harness · CẢNH BÁO", "Máy chủ: x", nil, "https://gh.example.vn")
	if !strings.Contains(remote, "Mở Console: https://gh.example.vn/connections#telegram") {
		t.Errorf("tin có địa chỉ ngoài phải có link:\n%s", remote)
	}
}
