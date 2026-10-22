package install

import (
	"context"
	"fmt"
	"strings"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/machine"
)

// machineCheckStep cài Bước 1 — Kiểm tra máy (3%), gọi thẳng
// internal/machine.RunAll, vốn đã tách phần quyết định OK/WARN/BAD thành
// hàm thuần và test riêng.
type machineCheckStep struct{}

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
