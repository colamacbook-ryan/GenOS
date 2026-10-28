package install

import (
	"context"
	"fmt"
	"path/filepath"
	"strings"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/machine"
)

// machineCheckStep cài Bước 1 — Kiểm tra máy (3%), gọi thẳng
// internal/machine.RunAll, vốn đã tách phần quyết định OK/WARN/BAD thành
// hàm thuần và test riêng.
type machineCheckStep struct {
	// runner/locate tiêm được khi test; nil dùng docker thật + compose.Locate.
	runner dockercli.Runner
	locate func(installDir string) (string, error)
}

func (machineCheckStep) ID() StepID   { return StepMachineCheck }
func (machineCheckStep) Name() string { return "Kiểm tra máy" }

func (s machineCheckStep) Run(ctx context.Context, env *Env, rep Reporter) error {
	port := 0
	if env != nil {
		port = env.Port
	}
	if port <= 0 {
		port = machine.DefaultPort
	}

	results := machine.RunAll(ctx, port)
	results = s.acceptOwnPort(ctx, env, port, results)
	detail := machineSummary(results)
	subLines := machineSubLines(results)

	switch machine.Overall(results) {
	case machine.StatusFail:
		se := &StepError{
			Code: firstFailureCode(results),
			What: "Máy chưa đạt yêu cầu tối thiểu để cài Gen-Harness",
			Why:  firstFailureDetail(results),
			Next: "Khắc phục mục trên rồi bấm r để kiểm tra lại.",
		}
		rep.Report(Progress{Status: StatusError, Percent: 100, Detail: detail, SubLines: subLines, Err: se})
		return se
	case machine.StatusWarn:
		rep.Report(Progress{Status: StatusWarn, Percent: 100, Detail: detail, SubLines: subLines})
		return nil
	default:
		rep.Report(Progress{Status: StatusOK, Percent: 100, Detail: detail, SubLines: subLines})
		return nil
	}
}

// machineSummary dựng dòng chi tiết cùng hàng với tên bước, giống mockup
// "8 GB RAM · 112 GB trống": ưu tiên hiển thị RAM + đĩa trống, hai mục
// người dùng quan tâm nhất khi vừa mở TUI.
func machineSummary(results []machine.CheckResult) string {
	var parts []string
	for _, r := range results {
		if r.Name == "RAM" || r.Name == "Đĩa trống" {
			parts = append(parts, r.Detail)
		}
	}
	return strings.Join(parts, " · ")
}

// machineSubLines liệt kê tối đa 4 dòng con — mọi kiểm tra không phải OK,
// để Owner thấy ngay cái gì cần chú ý mà không phải mở log.
func machineSubLines(results []machine.CheckResult) []string {
	var lines []string
	for _, r := range results {
		if r.Status == machine.StatusOK {
			continue
		}
		lines = append(lines, fmt.Sprintf("%s: %s", r.Name, r.Detail))
		if len(lines) == 4 {
			break
		}
	}
	return lines
}

func firstFailureDetail(results []machine.CheckResult) string {
	for _, r := range results {
		if r.Status == machine.StatusFail {
			return fmt.Sprintf("%s: %s", r.Name, r.Detail)
		}
	}
	return "kiểm tra máy thất bại"
}

func firstFailureCode(results []machine.CheckResult) string {
	for _, r := range results {
		if r.Status != machine.StatusFail {
			continue
		}
		switch r.Name {
		case "Hệ điều hành":
			return ErrCodeUnsupportedPlatform
		case "RAM":
			return ErrCodeInsufficientRAM
		case "Đĩa trống":
			return ErrCodeInsufficientDisk
		case "Kết nối mạng":
			return ErrCodeNetworkUnreachable
		default:
			if strings.HasPrefix(r.Name, "Cổng") {
				return ErrCodePortInUse
			}
		}
	}
	return ""
}

// composeConfigLabel là nhãn docker compose ghi đường dẫn compose.yaml đã dùng để tạo container.
const composeConfigLabel = "com.docker.compose.project.config_files"

// acceptOwnPort: cài dở rồi chạy lại thì cổng đang do CHÍNH stack của bản cài này giữ
// (container cùng compose.yaml trong install-dir). Khi đó kết quả "Cổng N" từ FAIL thành OK
// để cài tiếp (giữ dữ liệu); tiến trình lạ giữ cổng vẫn báo GH-E004.
func (s machineCheckStep) acceptOwnPort(ctx context.Context, env *Env, port int, results []machine.CheckResult) []machine.CheckResult {
	name := fmt.Sprintf("Cổng %d", port)
	idx := -1
	for i, r := range results {
		if r.Name == name && r.Status == machine.StatusFail {
			idx = i
		}
	}
	if idx < 0 || env == nil || !s.stackHoldsPort(ctx, env, port) {
		return results
	}
	out := append([]machine.CheckResult(nil), results...)
	out[idx] = machine.CheckResult{Name: name, Status: machine.StatusOK,
		Detail: "đang do chính Gen-Harness của bản cài này giữ — cài tiếp, giữ dữ liệu"}
	return out
}

// stackHoldsPort hỏi docker: có container nào publish cổng `port` mà nhãn compose trỏ đúng
// compose.yaml của install-dir này không. Mọi lỗi (docker chưa chạy…) ⇒ false (coi là tiến trình lạ).
func (s machineCheckStep) stackHoldsPort(ctx context.Context, env *Env, port int) bool {
	runner := s.runner
	if runner == nil {
		runner = dockercli.ExecRunner{}
	}
	composePath := env.ComposePath
	if composePath == "" {
		locate := s.locate
		if locate == nil {
			locate = compose.Locate
		}
		p, err := locate(env.InstallDir)
		if err != nil {
			return false
		}
		composePath = p
	}
	want, err := filepath.Abs(composePath)
	if err != nil {
		return false
	}
	out, err := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: []string{
		"ps", "--filter", fmt.Sprintf("publish=%d", port),
		"--format", `{{.Label "` + composeConfigLabel + `"}}`,
	}})
	if err != nil {
		return false
	}
	for _, line := range strings.Split(string(out), "\n") {
		// nhãn có thể liệt kê nhiều tệp, ngăn bởi dấu phẩy
		for _, f := range strings.Split(strings.TrimSpace(line), ",") {
			if f == "" {
				continue
			}
			if abs, err := filepath.Abs(f); err == nil && abs == want {
				return true
			}
		}
	}
	return false
}
