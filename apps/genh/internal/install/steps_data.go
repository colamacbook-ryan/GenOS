package install

import (
	"context"
	"fmt"
	"path/filepath"
	"sort"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/access"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/pgtune"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/secretgen"
)

// dataServices là 2 service Bước 5 khởi động và chờ healthy (đã bỏ "objects"
// — MinIO không còn trong compose.yaml, xem docs/reports/HANDOFF-v0.1.1.md).
var dataServices = []string{"db", "redis"}

// defaultDataTimeout là thời gian tối đa chờ db/redis healthy — compose.yaml
// khai healthcheck db tối đa 10×10s=100s, nên 3 phút dư dả cho cả trường hợp
// máy chậm/lần đầu khởi tạo dữ liệu.
const defaultDataTimeout = 3 * time.Minute

// dataStep cài Bước 5 — Khởi động dữ liệu (8%): `docker compose up -d db
// redis objects` rồi chờ cả ba healthy qua compose.WaitHealthy.
type dataStep struct {
	runner  dockercli.Runner
	locate  func(installDir string) (string, error)
	timeout time.Duration
}

func (dataStep) ID() StepID   { return StepStartData }
func (dataStep) Name() string { return "Khởi động dữ liệu" }

func (s dataStep) Run(ctx context.Context, env *Env, rep Reporter) error {
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
		timeout = defaultDataTimeout
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

	// v0.1.46 (F-21): .env phải có GH_BIND_ADDR TRƯỚC `docker compose up` đầu
	// tiên (compose.yaml mới nghe 127.0.0.1 theo mặc định). Cài mới → chỉ máy
	// này; cài lại/tiếp tục → giữ lựa chọn cũ, thiếu thì lan_legacy (QD-12).
	var accessErr error
	if env != nil && env.FreshInstall {
		_, _, accessErr = access.EnsureFresh(composePath)
	} else {
		_, _, accessErr = access.Ensure(composePath, false)
	}
	if accessErr != nil {
		se := &StepError{
			Code: ErrCodeSecretsWriteFailed,
			What: "Không ghi được cấu hình truy cập (.env)",
			Why:  accessErr.Error(),
			Next: "Kiểm quyền ghi thư mục " + filepath.Dir(composePath) + " rồi bấm r.",
			Err:  accessErr,
		}
		rep.Report(Progress{Status: StatusError, Percent: 100, Err: se})
		return se
	}

	envOverlay, err := secretsEnvOverlay(env)
	if err != nil {
		se := &StepError{
			Code: ErrCodeComposeUpFailed,
			What: "Chưa có bí mật để khởi động dữ liệu",
			Why:  err.Error(),
			Next: "Chạy lại `genh install` từ đầu (Bước 4 phải chạy trước Bước 5).",
			Err:  err,
		}
		rep.Report(Progress{Status: StatusError, Percent: 100, Err: se})
		return se
	}

	rep.Report(Progress{Status: StatusRunning, Percent: 5, Detail: "khởi động db, redis"})

	upArgs := compose.BaseArgs(composePath, append([]string{"up", "-d"}, dataServices...)...)
	if _, err := runner.Output(ctx, dockercli.Cmd{
		Name: "docker", Args: upArgs, Env: envOverlay, Dir: filepath.Dir(composePath),
	}); err != nil {
		se := &StepError{
			Code: ErrCodeComposeUpFailed,
			What: "`docker compose up -d` cho db/redis thất bại",
			Why:  err.Error(),
			Next: "Xem log ở trên rồi bấm r để thử lại — dữ liệu đã có không bị mất.",
			Err:  err,
		}
		rep.Report(Progress{Status: StatusError, Percent: 100, Err: se})
		return se
	}

	rep.Report(Progress{Status: StatusRunning, Percent: 20, Detail: "đang chờ healthy"})

	waitErr := compose.WaitHealthy(ctx, runner, composePath, envOverlay, dataServices, timeout, func(detail map[string]string) {
		rep.Report(Progress{
			Status:   StatusRunning,
			Percent:  dataHealthPercent(detail),
			Detail:   "đang chờ healthy",
			SubLines: healthSubLines(detail),
		})
	})
	if waitErr != nil {
		se := &StepError{
			Code: ErrCodeDataNotHealthy,
			What: "db/redis chưa healthy trước khi hết thời gian chờ",
			Why:  waitErr.Error(),
			Next: "Xem `docker compose logs db redis` rồi bấm r để thử lại.",
			Err:  waitErr,
		}
		rep.Report(Progress{Status: StatusError, Percent: 100, Err: se})
		return se
	}

	rep.Report(Progress{Status: StatusOK, Percent: 100, Detail: "db, redis healthy"})
	return nil
}

// secretsResult đọc Env.Secrets (đặt ở Bước 4, type-assert về
// secretgen.Result) — dùng chung cho mọi Step từ Bước 5 trở đi cần đọc bí
// mật đã sinh (Bước 5 chỉ cần env overlay qua secretsEnvOverlay; Bước 6 cần
// cả Bundle.MasterKey để ghi tệp Docker secret gh_master_key — xem
// ensureComposeSecretFiles trong steps_migrate.go).
func secretsResult(env *Env) (secretgen.Result, error) {
	if env == nil || env.Secrets == nil {
		return secretgen.Result{}, fmt.Errorf("Env.Secrets rỗng — Bước 4 (Sinh bí mật) chưa chạy")
	}
	res, ok := env.Secrets.(secretgen.Result)
	if !ok {
		return secretgen.Result{}, fmt.Errorf("Env.Secrets có kiểu %T không mong đợi (muốn secretgen.Result)", env.Secrets)
	}
	return res, nil
}

// secretsEnvOverlay đọc Env.Secrets (đặt ở Bước 4) và dựng các biến môi
// trường compose.yaml cần để dựng dữ liệu — POSTGRES_PASSWORD/
// GH_APP_DB_PASSWORD là bắt buộc (compose.yaml dùng
// ${VAR:?đặt VAR trong .env}), không có sẽ khiến `docker compose up` tự
// thất bại ngay với thông điệp rõ ràng từ chính Compose. GH_PG_* (tinh
// chỉnh Postgres) được tính từ RAM đo được ở Bước 1 — cùng logic
// ops.EnvOverlay, viết lại ở đây vì cùng lý do (khác package).
func secretsEnvOverlay(env *Env) ([]string, error) {
	res, err := secretsResult(env)
	if err != nil {
		return nil, err
	}
	overlay := []string{
		"POSTGRES_PASSWORD=" + res.Bundle.DBPassword,
		"GH_APP_DB_PASSWORD=" + res.Bundle.AppDBPassword,
		"GH_SETUP_TOKEN=" + res.Bundle.SetupToken,
	}
	return append(overlay, pgtune.DetectAndCompute().EnvPairs()...), nil
}

// dataHealthPercent ánh xạ số service đã healthy trong detail thành %
// nội bộ 20..95 của Bước 5 — 100% chỉ đặt khi WaitHealthy trả nil (Run
// tự báo StatusOK sau đó), tránh nhảy lên 100% giữa chừng rồi lùi lại nếu
// một service chuyển từ "healthy" về "starting" (không nên xảy ra nhưng
// không loại trừ).
func dataHealthPercent(detail map[string]string) float64 {
	if len(detail) == 0 {
		return 20
	}
	healthy := 0
	for _, v := range detail {
		if v == "healthy" || v == "đang chạy (không khai báo healthcheck)" {
			healthy++
		}
	}
	pct := 20 + 75*float64(healthy)/float64(len(detail))
	if pct > 95 {
		pct = 95
	}
	return pct
}

// healthSubLines dựng tối đa 4 dòng con "service: trạng thái", sắp theo tên
// để hiển thị ổn định (map không có thứ tự cố định).
func healthSubLines(detail map[string]string) []string {
	names := make([]string, 0, len(detail))
	for k := range detail {
		names = append(names, k)
	}
	sort.Strings(names)

	var lines []string
	for _, name := range names {
		lines = append(lines, fmt.Sprintf("%-10s %s", name, detail[name]))
		if len(lines) == 4 {
			break
		}
	}
	return lines
}
