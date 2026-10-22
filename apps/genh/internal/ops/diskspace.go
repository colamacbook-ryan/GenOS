package ops

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strings"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/machine"
)

// checkDiskFree đo chỗ trống trước khi `genh update` tải bản mới (F-11):
//   - tại gốc cài đặt (env.InstallDir — bản sao lưu, compose, log nằm ở đây);
//   - VÀ tại thư mục gốc của Docker (`docker info --format {{.DockerRootDir}}`
//     — nơi ảnh mới được tải về) nếu đường đó tồn tại trên máy chủ (Docker gốc
//     trên Linux). Docker Desktop (macOS/Windows) trả đường bên trong máy ảo,
//     os.Stat lỗi → bỏ qua, chỉ đo gốc cài đặt.
//
// Trả số NHỎ hơn trong các lần đo được cùng đường tương ứng. Lỗi đo cả hai →
// err (bên gọi chỉ in cảnh báo rồi đi tiếp, không chặn cập nhật).
func checkDiskFree(ctx context.Context, runner dockercli.Runner, env *Env, deps UpdateDeps) (free uint64, path string, err error) {
	diskFree := deps.DiskFree
	if diskFree == nil {
		diskFree = machine.ProbeDiskFree
	}

	var errs []error
	found := false
	consider := func(p string) {
		f, e := diskFree(p)
		if e != nil {
			errs = append(errs, fmt.Errorf("%s: %w", p, e))
			return
		}
		if !found || f < free {
			free, path, found = f, p, true
		}
	}

	if env.InstallDir != "" {
		consider(env.InstallDir)
	}
	if runner != nil {
		out, e := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: []string{"info", "--format", "{{.DockerRootDir}}"}})
		if e == nil {
			root := strings.TrimSpace(string(out))
			if root != "" {
				if info, statErr := os.Stat(root); statErr == nil && info.IsDir() {
					consider(root)
				}
			}
		}
	}

	if !found {
		if len(errs) == 0 {
			return 0, "", errors.New("không có đường dẫn nào để đo")
		}
		return 0, "", errors.Join(errs...)
	}
	return free, path, nil
}

// formatGB in số byte theo GB (1 chữ số thập phân) cho thông điệp tiếng Việt.
func formatGB(b uint64) string {
	return fmt.Sprintf("%.1f GB", float64(b)/(1<<30))
}
