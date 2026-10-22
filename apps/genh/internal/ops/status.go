package ops

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"sort"
	"strings"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/access"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
)

// readyPath là endpoint readiness thật của apps/api (xem apps/api/gh/shell/
// routes.py @router.get("/ready"), mount dưới prefix "/api/v1") — cùng
// đường dẫn servicesStep (internal/install/steps_services.go) đã dùng ở
// Bước 7, viết lại hằng số ở đây vì đó là hằng không xuất của package khác.
const readyPath = "/api/v1/ready"

// volumeBaseNames là tên các Docker volume khai trong deploy/compose.yaml
// (mục "volumes:" ở cuối tệp) — dùng để lọc dòng liên quan trong `docker
// system df -v` cho genh status/doctor. Compose v2 đặt tên volume thật là
// "<tên project>_<tên khai>" (project = "gen-harness", theo khoá "name:" ở
// đầu compose.yaml) trừ khi ghi đè bằng "name:" trên từng volume (không
// dùng ở đây) — nên lọc theo CHỨA (Contains) tên khai, không so khớp tuyệt
// đối, để không phụ thuộc tiền tố project chính xác.
// F-22 (v0.1.38): claude_state — phiên Claude Code tách khỏi agy_state.
var volumeBaseNames = []string{"caddy_data", "pg_data", "redis_data", "gh_objects", "agy_state", "claude_state"}

// StatusDeps cho phép tiêm dockercli.Runner/http.Client giả khi test — các
// trường nil dùng cài đặt thật (ExecRunner, client TLS bỏ qua xác thực CA
// nội bộ Caddy — xem insecureLocalClient).
type StatusDeps struct {
	Runner dockercli.Runner
	Client *http.Client
	// GOOS/UID cho phần "Tự chạy lại khi bật máy" (CheckAutostart) — rỗng dùng
	// runtime.GOOS / os.Getuid().
	GOOS string
	UID  string
}

// RunStatus in bảng dịch vụ + kết quả /api/v1/ready + phiên bản + dung
// lượng dữ liệu ra out — đúng mục "genh status" trong docs/handoff/
// 05-installer.md.
func RunStatus(ctx context.Context, env *Env, version string, deps StatusDeps, out io.Writer) error {
	runner := deps.Runner
	if runner == nil {
		runner = dockercli.ExecRunner{}
	}
	client := deps.Client
	if client == nil {
		client = insecureLocalClient(5 * time.Second)
	}

	composePath, err := env.LocatePath()
	if err != nil {
		return err
	}
	bundle, err := env.LoadSecrets()
	if err != nil {
		return err
	}
	envOverlay := EnvOverlay(bundle)

	psArgs := compose.BaseArgs(composePath, "ps", "--format", "json")
	psOut, err := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: psArgs, Env: envOverlay, Dir: composeDir(composePath)})
	if err != nil {
		return &OpError{
			Code: ErrCodeStatusFailed,
			What: "`docker compose ps` thất bại",
			Why:  err.Error(),
			Next: "Kiểm Gen-Harness đã cài và runtime container đang chạy (`genh start`).",
			Err:  err,
		}
	}
	statuses, err := compose.ParsePS(psOut)
	if err != nil {
		return &OpError{
			Code: ErrCodeStatusFailed,
			What: "Không phân tích được kết quả `docker compose ps`",
			Why:  err.Error(),
			Next: "Thử lại; nếu vẫn lỗi, báo kèm `docker compose ps --format json` chạy tay.",
			Err:  err,
		}
	}
	sort.Slice(statuses, func(i, j int) bool { return statuses[i].Service < statuses[j].Service })

	_, _ = fmt.Fprintf(out, "Gen-Harness %s\n\n", version)
	// v0.1.46 (F-21/F-27): cách truy cập hiện tại + ghi lại run/network-status.json.
	if acc, aerr := access.Read(composePath); aerr == nil && acc.Mode != "" {
		printAccessLine(out, acc, env.Port)
		_ = writeNetworkStatus(env, acc, out)
		_, _ = fmt.Fprintln(out)
	}
	_, _ = fmt.Fprintf(out, "%-10s %-10s %s\n", "DỊCH VỤ", "TRẠNG THÁI", "HEALTH")
	for _, s := range statuses {
		health := s.Health
		if health == "" {
			health = "—"
		}
		_, _ = fmt.Fprintf(out, "%-10s %-10s %s\n", s.Service, s.State, health)
	}

	_, _ = fmt.Fprintln(out)
	readyOut, readyErr := probeReadyBody(ctx, client, localURL(env.Port, readyPath))
	if readyErr != nil {
		_, _ = fmt.Fprintf(out, "/api/v1/ready: không gọi được (%v)\n", readyErr)
	} else {
		_, _ = fmt.Fprintf(out, "/api/v1/ready:\n")
		keys := make([]string, 0, len(readyOut))
		for k := range readyOut {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			_, _ = fmt.Fprintf(out, "  %-8s %s\n", k, readyOut[k])
		}
	}

	_, _ = fmt.Fprintln(out)
	dfArgs := []string{"system", "df", "-v"}
	dfOut, dfErr := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: dfArgs})
	if dfErr != nil {
		_, _ = fmt.Fprintf(out, "Dung lượng dữ liệu: không đọc được (%v)\n", dfErr)
	} else {
		_, _ = fmt.Fprintln(out, "Dung lượng dữ liệu (docker system df -v, lọc theo volume Gen-Harness):")
		for _, line := range volumeLines(string(dfOut)) {
			_, _ = fmt.Fprintln(out, "  "+line)
		}
	}

	// v0.1.37 (F-73): máy có tự chạy lại Gen-Harness sau khi bật không — in kèm
	// lệnh sửa, rồi ghi run/autostart-status.json cho Console (lỗi ghi chỉ cảnh báo).
	_, _ = fmt.Fprintln(out)
	as := CheckAutostart(ctx, AutostartDeps{Runner: runner, GOOS: deps.GOOS, UID: deps.UID})
	printAutostart(out, as)
	writeAutostartStatus(env, as, out)

	return nil
}

// writeAutostartStatus ghi run/autostart-status.json (bỏ qua khi không có gốc
// cài đặt); lỗi chỉ in cảnh báo — không làm hỏng status/doctor.
func writeAutostartStatus(env *Env, as hostlink.AutostartStatus, out io.Writer) {
	if env.InstallDir == "" {
		return
	}
	if err := hostlink.WriteAutostartStatus(env.InstallDir, as); err != nil {
		_, _ = fmt.Fprintf(out, "  (cảnh báo: không ghi được %s — %v)\n", hostlink.AutostartStatusFile, err)
	}
}

// probeReadyBody gọi GET url và giải mã thân JSON dạng {"db":"ok",...} —
// khác probeReady của Bước 7 (chỉ cần bool), status cần in ra chi tiết từng
// thành phần.
func probeReadyBody(ctx context.Context, client *http.Client, url string) (map[string]string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer func() { _ = resp.Body.Close() }()

	var out map[string]string
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, fmt.Errorf("giải mã JSON: %w", err)
	}
	return out, nil
}

// volumeLines lọc các dòng `docker system df -v` liên quan tới volume của
// Gen-Harness (xem volumeBaseNames) — giữ nguyên dòng tiêu đề (bắt đầu bằng
// "VOLUME NAME") nếu có, để bảng vẫn có cột.
func volumeLines(dfOut string) []string {
	var out []string
	for _, line := range strings.Split(dfOut, "\n") {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" {
			continue
		}
		if strings.HasPrefix(trimmed, "VOLUME NAME") {
			out = append(out, line)
			continue
		}
		for _, name := range volumeBaseNames {
			if strings.Contains(line, name) {
				out = append(out, line)
				break
			}
		}
	}
	return out
}
