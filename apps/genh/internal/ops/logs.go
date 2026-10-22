package ops

import (
	"context"
	"io"
	"os"
	"os/exec"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
)

// LogsOptions là các cờ đã phân tích của `genh logs [dịch vụ...] [-f]`.
type LogsOptions struct {
	Services []string // rỗng = mọi service
	Follow   bool
}

// inheritRunner chạy một tiến trình con nối THẲNG Stdin/Stdout/Stderr của
// tiến trình gọi — cần cho `genh logs -f` (tail tương tác, người dùng bấm
// Ctrl-C để dừng) và cho log không -f (in gọn, có màu theo mức mà chính
// `docker compose logs` đã tô nếu terminal hỗ trợ, genh không cần tự phân
// tích/tô lại).
//
// dockercli.Runner (Output/Stream) GOM log lại thành []byte hoặc gọi lại
// onLine cho TỪNG dòng — không phù hợp cho tail -f: Output chờ tiến trình
// thoát (không bao giờ xảy ra với -f), còn Stream tuy stream được nhưng vẫn
// đi qua bufio.Scanner của genh rồi mới in lại (chậm hơn, mất khả năng
// Ctrl-C ngắt trực tiếp tiến trình con qua tín hiệu terminal). Vì vậy `genh
// logs` KHÔNG dùng dockercli.Runner mà tự gọi exec.CommandContext, đúng
// khuyến nghị "ưu tiên đơn giản đúng" của yêu cầu nhiệm vụ.
type inheritRunner interface {
	Run(ctx context.Context, name string, args []string, dir string, env []string, stdout, stderr io.Writer, stdin io.Reader) error
}

// execInheritRunner là inheritRunner thật, dùng os/exec.
type execInheritRunner struct{}

func (execInheritRunner) Run(ctx context.Context, name string, args []string, dir string, env []string, stdout, stderr io.Writer, stdin io.Reader) error {
	cmd := exec.CommandContext(ctx, name, args...)
	cmd.Dir = dir
	if len(env) > 0 {
		cmd.Env = append(os.Environ(), env...)
	}
	cmd.Stdout = stdout
	cmd.Stderr = stderr
	cmd.Stdin = stdin
	return cmd.Run()
}

// RunLogs chạy `docker compose logs [--follow] [--tail=200] [service...]`
// nối thẳng vào out/errOut/in của tiến trình gọi.
func RunLogs(ctx context.Context, env *Env, opts LogsOptions, runner inheritRunner, out, errOut io.Writer, in io.Reader) error {
	if runner == nil {
		runner = execInheritRunner{}
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

	sub := []string{"logs", "--tail=200"}
	if opts.Follow {
		sub = append(sub, "--follow")
	}
	sub = append(sub, opts.Services...)
	args := compose.BaseArgs(composePath, sub...)

	if err := runner.Run(ctx, "docker", args, composeDir(composePath), envOverlay, out, errOut, in); err != nil {
		return &OpError{
			Code: ErrCodeLogsFailed,
			What: "`docker compose logs` thất bại",
			Why:  err.Error(),
			Next: "Kiểm tên dịch vụ đúng (`genh status`) và Gen-Harness đang chạy.",
			Err:  err,
		}
	}
	return nil
}
