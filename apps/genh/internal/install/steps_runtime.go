package install

import (
	"context"
	"fmt"
	"net/http"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/config"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	genhruntime "github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/runtime"
)

// runtimeStep cài Bước 2 — Chuẩn bị container runtime (17%): dùng ngay nếu
// máy đã có Docker Engine ≥ 24 + Compose v2 hợp lệ (StatusSkipped); nếu
// chưa, tự tải+cài runtime theo nền tảng (internal/runtime.Bootstrap, khác
// cài đặt theo GOOS qua build tag — xem bootstrap_linux.go/_darwin.go/
// _windows.go).
type runtimeStep struct {
	// runner cho phép tiêm Runner giả khi test — nil dùng ExecRunner thật.
	runner dockercli.Runner
	// http cho phép tiêm HTTPDoer giả khi test (không đụng mạng thật để tải
	// Docker Engine tĩnh/Colima/Lima/rootfs WSL) — nil dùng http.DefaultClient.
	http genhruntime.HTTPDoer
}

func (runtimeStep) ID() StepID   { return StepRuntime }
func (runtimeStep) Name() string { return "Chuẩn bị container runtime" }

func (s runtimeStep) Run(ctx context.Context, env *Env, rep Reporter) error {
	runner := s.runner
	if runner == nil {
		runner = dockercli.ExecRunner{}
	}

	info := genhruntime.Detect(ctx, runner)
	if info.Ready() {
		rep.Report(Progress{
			Status:  StatusSkipped,
			Percent: 100,
			Detail:  fmt.Sprintf("Docker Engine %s (sẵn có)", info.EngineVersion),
		})
		return nil
	}

	rep.Report(Progress{
		Status:  StatusRunning,
		Percent: 5,
		Detail:  "chưa có runtime hợp lệ (" + info.Reason + ") — đang tự cài",
	})

	runtimeDir, dirErr := runtimeDirFor(env)
	if dirErr != nil {
		se := &StepError{
			Code: ErrCodeRuntimeUnsupportedOS,
			What: "Không xác định được thư mục cài runtime",
			Why:  dirErr.Error(),
			Next: "Đặt lại --install-dir rồi bấm r.",
			Err:  dirErr,
		}
		rep.Report(Progress{Status: StatusError, Percent: 100, Err: se})
		return se
	}

	autoApprove := env != nil && env.AutoApprove
	outcome, err := runBootstrap(ctx, runtimeDir, autoApprove, s.http, rep)
	if err != nil {
		se := &StepError{
			Code: ErrCodeRuntimeDownloadFailed,
			What: "Không tự cài được container runtime",
			Why:  err.Error(),
			Next: "Kiểm kết nối mạng rồi bấm r để thử lại; hoặc cài Docker thủ công theo docs/handoff/05-installer.md.",
			Err:  err,
		}
		if outcome != nil && outcome.ManualNextSteps != "" {
			se.Next = outcome.ManualNextSteps
		}
		rep.Report(Progress{Status: StatusError, Percent: 100, Err: se})
		return se
	}

	if outcome.ServiceStarted {
		rep.Report(Progress{Status: StatusOK, Percent: 100, Detail: "đã cài và khởi động runtime"})
		return nil
	}

	// Đã tải+giải nén xong nhưng chưa khởi động dịch vụ (AutoApprove=false,
	// hoặc nền tảng cần khởi động lại máy trước — ví dụ vừa bật WSL2).
	// Không phải lỗi chương trình, nhưng cài đặt CHƯA xong — Owner cần làm
	// thêm một bước, nên trả *StepError để Runner dừng lại đúng chỗ thay vì
	// các bước 3 trở đi chạy tiếp trên một runtime chưa thật sự sẵn sàng.
	se := &StepError{
		Code: ErrCodeRuntimeNeedsApproval,
		What: "Đã chuẩn bị xong container runtime nhưng chưa khởi động",
		Why:  "cần xác nhận thêm để tiếp tục (sudo/UAC/khởi động lại máy) — xem hướng dẫn bên dưới",
		Next: outcome.ManualNextSteps,
	}
	rep.Report(Progress{Status: StatusWarn, Percent: 100, Detail: outcome.ManualNextSteps, Err: se})
	return se
}

// runBootstrap dựng genhruntime.BootstrapOptions chỉ với các trường CHUNG
// cho cả ba bản GOOS (RuntimeDir/HTTP/Runner/AutoApprove/OnProgress) —
// BootstrapOptions của mỗi nền tảng có thêm trường riêng (GOARCH/Version ở
// Linux/macOS, RootfsChecksum ở Windows) tự nhận giá trị mặc định bên trong
// từng Bootstrap(), nên gói install không cần biết đang biên dịch cho GOOS
// nào — đúng tinh thần "chỉ một bản bootstrap_*.go được biên dịch theo build
// tag" của internal/runtime.
func runBootstrap(ctx context.Context, runtimeDir string, autoApprove bool, httpClient genhruntime.HTTPDoer, rep Reporter) (*genhruntime.Outcome, error) {
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	opts := genhruntime.BootstrapOptions{
		RuntimeDir:  runtimeDir,
		HTTP:        httpClient,
		AutoApprove: autoApprove,
		OnProgress: func(stage string, current, total int64) {
			pct := 10.0
			if total > 0 {
				pct = 10 + 80*float64(current)/float64(total)
			}
			rep.Report(Progress{
				Status:  StatusRunning,
				Percent: pct,
				Detail:  "đang tải " + stage,
			})
		},
	}
	return genhruntime.Bootstrap(ctx, opts)
}

func runtimeDirFor(env *Env) (string, error) {
	root := ""
	if env != nil {
		root = env.InstallDir
	}
	if root == "" {
		r, err := config.DefaultRoot()
		if err != nil {
			return "", err
		}
		root = r
	}
	return config.New(root).RuntimeDir(), nil
}
