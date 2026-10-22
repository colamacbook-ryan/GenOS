package install

import (
	"context"
	"crypto/tls"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/machine"
)

// startupServices là các service Bước 7 khởi động, đúng tên VÀ đúng thứ tự
// liệt kê trong docs/handoff/05-installer.md — tên trùng khớp 1-1 với
// deploy/compose.yaml (api, worker, bridge, web, proxy đều là tên service
// thật, không cần đổi). v0.1.29 thêm browser-egress + browser (Gen đọc mạng
// xã hội — container riêng, không DB, không khoá master). v0.1.63 thêm agy-box (hộp
// riêng Antigravity cho agent ngoại giao: không DB, không khoá master, chỉ gh_agy_key). Gói sửa review v0.1.63 (P2-2)
// thêm agy-egress — proxy lối ra của hộp (cùng ảnh api; hộp chỉ ra Internet qua nó), lên trước hộp.
var startupServices = []string{"api", "worker", "agy-egress", "agy-box", "bridge", "browser-egress", "browser", "web", "proxy"}

// optionalStartupServices là các dịch vụ THÊM SAU bản đầu: compose.yaml đang dùng không khai thì bỏ khỏi `up -d` thay vì
// để docker chết "no such service". Máy thật không bao giờ gặp (genh và compose nhúng cùng một bản), nhưng genh build từ
// mã mới mà nhúng compose.release.yaml của bản chính thức cũ (E2E chế độ pr, bản hỏng cố ý; GENH_COMPOSE_FILE trỏ bản
// cũ) thì có — cài đặt chết ở Bước 7 trước khi kịp chạy phép kiểm. Dịch vụ lõi (api, worker, bridge, web, proxy) thiếu
// thì vẫn để docker báo lỗi đúng chỗ, không âm thầm bỏ.
var optionalStartupServices = map[string]bool{"agy-egress": true, "agy-box": true, "browser-egress": true, "browser": true}

// startupServicesFor lọc startupServices theo compose.yaml đã đọc: bỏ dịch vụ tuỳ chọn không được khai. Không đọc được
// compose hoặc compose không khai dịch vụ nào → giữ nguyên danh sách đầy đủ (để docker tự báo lỗi). skipped: tên bị bỏ.
func startupServicesFor(composePath string) (services, skipped []string) {
	cf, err := compose.Load(composePath)
	if err != nil || len(cf.Services) == 0 {
		return append([]string(nil), startupServices...), nil
	}
	for _, name := range startupServices {
		if _, ok := cf.Services[name]; !ok && optionalStartupServices[name] {
			skipped = append(skipped, name)
			continue
		}
		services = append(services, name)
	}
	return services, skipped
}

// readyPath là endpoint readiness thật của apps/api — xem
// apps/api/gh/shell/routes.py (@router.get("/ready"), mount ở
// apps/api/gh/app.py với prefix "/api/v1") — TRẢ 200 khi cả db lẫn redis
// đều "ok", 503 nếu không, đúng khớp với "/api/v1/ready" mà
// docs/handoff/05-installer.md nói tắt là "/api/ready" và step.go (Env.Port)
// đã ghi chú đúng đường dẫn đầy đủ.
const readyPath = "/api/v1/ready"

// defaultServicesTimeout: api cần chờ migrate xong (đã chạy ở Bước 6) rồi
// mới healthy, worker/bridge/web có healthcheck riêng, proxy chờ cả
// api lẫn web healthy trước khi tự nó "running" — cộng dồn thời gian khởi
// động container + healthcheck đầu tiên của từng service, 3 phút dư dả cho
// máy chậm/lần đầu.
const defaultServicesTimeout = 3 * time.Minute

// defaultReadyPollInterval là khoảng nghỉ giữa hai lần gọi readyPath.
const defaultReadyPollInterval = 2 * time.Second

// servicesStep cài Bước 7 — Khởi động dịch vụ (6%): `docker compose up -d`
// cho startupServices rồi chờ readyPath trả 200 qua HTTPS ở cổng Env.Port.
type servicesStep struct {
	// runner cho phép tiêm dockercli.Runner giả khi test — nil dùng ExecRunner thật.
	runner dockercli.Runner
	// locate cho phép tiêm compose.LocateAndSync giả khi test — nil dùng
	// compose.LocateAndSync (genh install CÓ trách nhiệm đồng bộ compose.yaml
	// nhúng, khác Locate thường dùng ở internal/ops — xem doc-comment
	// compose.LocateAndSync).
	locate func(installDir string) (string, error)
	// client cho phép tiêm *http.Client giả (ví dụ trỏ vào httptest.Server)
	// khi test — nil dùng client TLS mặc định (xem lý do InsecureSkipVerify
	// ở newInsecureReadyClient).
	client    *http.Client
	timeout   time.Duration
	pollEvery time.Duration
	// ensureRunPerms siết hộp thư run/ (v0.1.45) SAU `docker compose up` — nil
	// dùng ensureRunDirPerms (hostlink.EnsureRunPerms + ghi run_mode vào genh.json).
	ensureRunPerms func(ctx context.Context, runner dockercli.Runner, installDir, composePath string, envOverlay []string) (string, error)
}

// ensureRunDirPerms siết hộp thư run/ về 2770 nhóm 10001 (ảnh api vừa lên đã có
// gid 10001) — như ops.EnsureRunDirPerms (internal/ops import install nên không
// gọi ngược được). Thất bại → run/ mở 0777 như cũ, mode "open" + lỗi.
func ensureRunDirPerms(ctx context.Context, runner dockercli.Runner, installDir, composePath string, envOverlay []string) (string, error) {
	if installDir == "" {
		return hostlink.RunModeNA, nil
	}
	spec := hostlink.RunPermSpec{
		InstallDir: installDir,
		PsArgs:     compose.BaseArgs(composePath, "ps", "-q", "api"),
		Env:        envOverlay,
		Dir:        filepath.Dir(composePath),
		Runner:     runner,
	}
	if data, err := os.ReadFile(composePath); err == nil {
		if cf, err := compose.Parse(data); err == nil {
			if img := cf.Services["api"].Image; !strings.Contains(img, "$") {
				spec.Image = img
			}
		}
	}
	mode, err := hostlink.EnsureRunPerms(ctx, spec)
	_ = hostlink.SetRunMode(installDir, mode)
	return mode, err
}

func (servicesStep) ID() StepID   { return StepStartServices }
func (servicesStep) Name() string { return "Khởi động dịch vụ" }

func (s servicesStep) Run(ctx context.Context, env *Env, rep Reporter) error {
	runner := s.runner
	if runner == nil {
		runner = dockercli.ExecRunner{}
	}
	locate := s.locate
	if locate == nil {
		locate = compose.LocateAndSync
	}
	timeout := s.timeout
	if timeout <= 0 {
		timeout = defaultServicesTimeout
	}
	pollEvery := s.pollEvery
	if pollEvery <= 0 {
		pollEvery = defaultReadyPollInterval
	}

	installDir := ""
	if env != nil {
		installDir = env.InstallDir
	}
	composePath, err := locate(installDir)
	if err != nil {
		se := &StepError{
			Code: ErrCodeComposeNotFound,
			What: "Không tìm thấy deploy/compose.yaml",
			Why:  err.Error(),
			Next: "Đặt biến GENH_COMPOSE_FILE trỏ tới compose.yaml rồi bấm r.",
			Err:  err,
		}
		rep.Report(Progress{Status: StatusError, Percent: 100, Err: se})
		return se
	}

	res, err := secretsResult(env)
	if err != nil {
		se := &StepError{
			Code: ErrCodeServicesUpFailed,
			What: "Chưa có bí mật để khởi động dịch vụ",
			Why:  err.Error(),
			Next: "Chạy lại `genh install` từ đầu (Bước 4 phải chạy trước Bước 7).",
			Err:  err,
		}
		rep.Report(Progress{Status: StatusError, Percent: 100, Err: se})
		return se
	}
	envOverlay, err := secretsEnvOverlay(env)
	if err != nil {
		se := &StepError{
			Code: ErrCodeServicesUpFailed,
			What: "Chưa có bí mật để khởi động dịch vụ",
			Why:  err.Error(),
			Next: "Chạy lại `genh install` từ đầu (Bước 4 phải chạy trước Bước 7).",
			Err:  err,
		}
		rep.Report(Progress{Status: StatusError, Percent: 100, Err: se})
		return se
	}

	// ensureComposeSecretFiles (steps_migrate.go) đã chạy ở Bước 6 trong một
	// lượt `genh install` bình thường — gọi lại ở đây rẻ và idempotent
	// (không ghi đè gì đã có), phòng trường hợp Owner chỉ thử lại riêng Bước
	// 7 sau khi Bước 6 đã xong ở một lần chạy trước.
	if err := ensureComposeSecretFiles(composePath, res); err != nil {
		se := &StepError{
			Code: ErrCodeServicesUpFailed,
			What: "Không chuẩn bị được khoá bí mật cho docker compose (gh_master_key/gh_bridge_key)",
			Why:  err.Error(),
			Next: "Kiểm quyền ghi thư mục cài đặt rồi bấm r.",
			Err:  err,
		}
		rep.Report(Progress{Status: StatusError, Percent: 100, Err: se})
		return se
	}

	services, skipped := startupServicesFor(composePath)
	detail := "khởi động " + strings.Join(services, ", ")
	if len(skipped) > 0 {
		detail += " (compose.yaml này chưa khai " + strings.Join(skipped, ", ") + " — bỏ qua)"
	}
	rep.Report(Progress{Status: StatusRunning, Percent: 5, Detail: detail})

	upArgs := compose.BaseArgs(composePath, append([]string{"up", "-d"}, services...)...)
	if _, err := runner.Output(ctx, dockercli.Cmd{
		Name: "docker", Args: upArgs, Env: envOverlay, Dir: filepath.Dir(composePath),
	}); err != nil {
		se := &StepError{
			Code: ErrCodeServicesUpFailed,
			What: "`docker compose up -d` cho api/worker/agy-egress/agy-box/bridge/web/proxy thất bại",
			Why:  err.Error(),
			Next: "Xem log ở trên rồi bấm r để thử lại — dữ liệu đã có không bị mất.",
			Err:  err,
		}
		rep.Report(Progress{Status: StatusError, Percent: 100, Err: se})
		return se
	}

	// v0.1.45: ảnh api mới (nhóm gid 10001) đã lên — siết hộp thư run/ về 2770.
	// Không siết được thì run/ mở như cũ, chỉ cảnh báo (không chặn cài đặt).
	ensurePerms := s.ensureRunPerms
	if ensurePerms == nil {
		ensurePerms = ensureRunDirPerms
	}
	if mode, err := ensurePerms(ctx, runner, installDir, composePath, envOverlay); mode == hostlink.RunModeOpen {
		detail := "Cảnh báo: run/ vẫn mở cho mọi người dùng trên máy — `genh doctor` thử siết lại"
		if err != nil {
			detail += " (" + err.Error() + ")"
		}
		rep.Report(Progress{Status: StatusRunning, Percent: 15, Detail: detail})
	}

	rep.Report(Progress{Status: StatusRunning, Percent: 20, Detail: "đang chờ " + readyPath})

	client := s.client
	if client == nil {
		client = newInsecureReadyClient()
	}

	port := 0
	if env != nil {
		port = env.Port
	}
	if port <= 0 {
		port = machine.DefaultPort
	}
	url := fmt.Sprintf("https://localhost:%d%s", port, readyPath) // "localhost" chứ không phải IP — xem ops.ProxyHost

	waitErr := waitServiceReady(ctx, client, url, timeout, pollEvery, func(pct float64, attempt int) {
		rep.Report(Progress{
			Status:  StatusRunning,
			Percent: pct,
			Detail:  fmt.Sprintf("đang chờ %s (lần %d)", readyPath, attempt),
		})
	})
	if waitErr != nil {
		se := &StepError{
			Code: ErrCodeServiceNotReady,
			What: readyPath + " không trả 200 trước khi hết thời gian chờ",
			Why:  waitErr.Error(),
			Next: "Xem `docker compose logs api worker agy-egress agy-box bridge web proxy` rồi bấm r để thử lại.",
			Err:  waitErr,
		}
		rep.Report(Progress{Status: StatusError, Percent: 100, Err: se})
		return se
	}

	rep.Report(Progress{Status: StatusOK, Percent: 100, Detail: "api, worker, agy-box, bridge, web, proxy đã sẵn sàng"})
	return nil
}

// newInsecureReadyClient dựng http.Client cho việc thăm dò readyPath.
//
// LƯU Ý vì sao InsecureSkipVerify: deploy/proxy/Caddyfile dùng chỉ thị `tls
// internal` — Caddy TỰ quản lý một CA nội bộ RIÊNG (sinh và giữ trong volume
// caddy_data lúc container proxy khởi động lần đầu), KHÁC với CA mà
// secretgen.EnsureCA sinh ở Bước 4 (secretgen.Result.CACertPEM). Hai CA này
// không liên quan tới nhau, nên không thể dùng CACertPEM để verify chứng chỉ
// mà proxy thật sự trình ra ở :8443 — muốn verify đúng phải trích xuất CA
// nội bộ của Caddy từ trong volume (ví dụ qua `docker compose exec proxy cat
// /data/caddy/pki/authorities/local/root.crt`), việc này để lại cho khi cần
// (không cần thiết cho một lượt gọi readiness qua loopback trong lúc cài,
// không mang theo bí mật gì). InsecureSkipVerify vì vậy là lựa chọn có chủ
// đích, không phải bỏ sót — Bước 8 (Hoàn tất, chưa làm ở phiên này) mới là
// nơi hỏi Owner tin cậy CA để trình duyệt không cảnh báo.
func newInsecureReadyClient() *http.Client {
	return &http.Client{
		Timeout: 5 * time.Second,
		Transport: &http.Transport{
			TLSClientConfig: &tls.Config{InsecureSkipVerify: true}, //nolint:gosec // xem ghi chú hàm
		},
	}
}

// waitServiceReady gọi GET url lặp lại cho tới khi nhận 200, hoặc hết
// timeout. onTick, nếu khác nil, được gọi sau mỗi lần thử (kể cả lần thất
// bại) để Step báo tiến độ qua Reporter — percent tăng dần theo tỉ lệ thời
// gian đã trôi qua trên timeout (tiến độ thật của việc CHỜ, không phải suy
// đoán readyPath còn bao lâu nữa mới sẵn sàng — không có cách nào biết
// trước điều đó).
func waitServiceReady(ctx context.Context, client *http.Client, url string, timeout, pollEvery time.Duration, onTick func(pct float64, attempt int)) error {
	deadline := time.Now().Add(timeout)
	attempt := 0

	for {
		attempt++
		if probeReady(ctx, client, url) {
			return nil
		}

		if time.Now().After(deadline) {
			return fmt.Errorf("hết thời gian chờ %s", timeout)
		}

		if onTick != nil {
			onTick(waitReadyPercent(deadline, timeout), attempt)
		}

		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(pollEvery):
		}
	}
}

// waitReadyPercent ánh xạ thời gian còn lại tới deadline thành % nội bộ
// 20..95 của Bước 7 (20 là mốc Run đặt ngay sau khi `up -d` xong, trước khi
// vào vòng chờ — xem Run).
func waitReadyPercent(deadline time.Time, timeout time.Duration) float64 {
	remaining := time.Until(deadline)
	if remaining < 0 {
		remaining = 0
	}
	elapsedFrac := 1 - remaining.Seconds()/timeout.Seconds()
	pct := 20 + 75*elapsedFrac
	if pct < 20 {
		pct = 20
	}
	if pct > 95 {
		pct = 95
	}
	return pct
}

func probeReady(ctx context.Context, client *http.Client, url string) bool {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return false
	}
	resp, err := client.Do(req)
	if err != nil {
		return false
	}
	defer func() { _ = resp.Body.Close() }()
	return resp.StatusCode == http.StatusOK
}
