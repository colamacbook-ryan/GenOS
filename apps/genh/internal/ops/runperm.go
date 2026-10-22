package ops

import (
	"context"
	"io"
	"os"
	"strings"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
)

// Điểm tiêm cho test (v0.1.45): GOOS ("" = runtime.GOOS), uid tiến trình, Chown.
var (
	runPermGOOS   = ""
	runPermGetuid = os.Getuid
	runPermChown  = os.Chown
)

// EnsureRunDirPerms siết quyền hộp thư run/ (v0.1.45) về 2770 nhóm 10001 — chỉ
// genh và container api ghi được (hostlink.EnsureRunPerms). GỌI SAU `docker
// compose up` (ảnh api mới đã có nhóm gid 10001). Linux: một container phụ bằng
// ảnh api (--network none, --user 0:0) kiểm ảnh có gid 10001 rồi siết (ảnh cũ ⇒
// mở 0777); genh chạy root mà không có docker/ảnh hoặc container phụ lỗi → Chown
// trực tiếp. Thất bại → chmod 0777 như cũ, mode "open" kèm lỗi (bên gọi in
// cảnh báo, KHÔNG làm hỏng thao tác chính). macOS/Windows: "n/a". Ghi run_mode
// vào genh.json.
func EnsureRunDirPerms(ctx context.Context, env *Env, runner dockercli.Runner) (mode string, err error) {
	if env == nil || env.InstallDir == "" {
		return hostlink.RunModeNA, nil
	}
	composePath, _ := env.LocatePath()
	var overlay []string
	if composePath != "" {
		if b, lerr := env.LoadSecrets(); lerr == nil {
			overlay = EnvOverlay(b)
		}
	}
	return ensureRunDirPermsAt(ctx, runner, env.InstallDir, composePath, overlay)
}

// ensureRunDirPermsAt là EnsureRunDirPerms khi bên gọi đã có compose.yaml +
// biến môi trường (update/rollback, start).
func ensureRunDirPermsAt(ctx context.Context, runner dockercli.Runner, installDir, composePath string, overlay []string) (string, error) {
	if installDir == "" {
		return hostlink.RunModeNA, nil
	}
	if runner == nil {
		runner = dockercli.ExecRunner{}
	}
	spec := hostlink.RunPermSpec{
		InstallDir: installDir,
		Env:        overlay,
		Runner:     runner,
		GOOS:       runPermGOOS,
		Getuid:     runPermGetuid,
		Chown:      runPermChown,
	}
	if composePath != "" {
		spec.Image = APIImageFromCompose(composePath)
		spec.PsArgs = compose.BaseArgs(composePath, "ps", "-q", "api")
		spec.Dir = composeDir(composePath)
	}
	mode, err := hostlink.EnsureRunPerms(ctx, spec)
	_ = hostlink.SetRunMode(installDir, mode)
	return mode, err
}

// APIImageFromCompose trả "image:" của service api trong compose.yaml (như
// images.go đọc ảnh) — rỗng khi dùng "build:" hoặc còn biến ${…} chưa nội suy
// (khi đó hỏi docker ảnh của container api đang chạy).
func APIImageFromCompose(composePath string) string {
	data, err := os.ReadFile(composePath)
	if err != nil {
		return ""
	}
	cf, err := compose.Parse(data)
	if err != nil {
		return ""
	}
	img := cf.Services["api"].Image
	if strings.Contains(img, "$") {
		return ""
	}
	return img
}

// RunDirPermsLine là dòng trạng thái hộp thư run/ (genh doctor, start, update).
func RunDirPermsLine(mode string) (ok bool, text string) {
	switch mode {
	case hostlink.RunModeRestricted:
		return true, "Hộp thư run/: chỉ genh và api ghi được"
	case hostlink.RunModeNA:
		return true, "Hộp thư run/: Docker Desktop tự quản quyền (macOS/Windows)"
	default:
		return false, "Cảnh báo: run/ vẫn mở cho mọi người dùng trên máy"
	}
}

// reportRunDirPerms siết quyền run/ rồi in cảnh báo nếu không siết được (không
// trả lỗi — chỉ là lớp bảo vệ thêm, không làm hỏng thao tác chính).
func reportRunDirPerms(ctx context.Context, runner dockercli.Runner, installDir, composePath string, overlay []string, out io.Writer) string {
	mode, err := ensureRunDirPermsAt(ctx, runner, installDir, composePath, overlay)
	if ok, text := RunDirPermsLine(mode); !ok && out != nil {
		msg := "     (" + text
		if err != nil {
			msg += " — " + err.Error()
		}
		_, _ = io.WriteString(out, msg+"; `genh doctor` thử siết lại)\n")
	}
	return mode
}
