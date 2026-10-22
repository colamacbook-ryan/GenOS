package ops

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"strings"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
)

// resetOwnerCmd chạy CLI phía api (apps/api/gh/auth/reset_owner.py): đặt mật
// khẩu tạm cho Owner, thu hồi phiên cũ, gỡ khoá PIN, ghi Action Log, in JSON
// {"email","temp_password"} ra stdout. Mã thoát 2 = chưa có Owner.
var resetOwnerCmd = []string{"python", "-m", "gh.auth.reset_owner"}

// RunResetPassword đặt lại mật khẩu Owner khi Owner quên — KHÔNG đụng dữ liệu
// nào khác. Chạy trong container api đang sống (`exec`), api không chạy thì
// dùng container một lần (`run --rm --no-deps`) — cùng cách `genh backup`.
func RunResetPassword(ctx context.Context, env *Env, runner dockercli.Runner, out io.Writer) error {
	if runner == nil {
		runner = dockercli.ExecRunner{}
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
	dir := composeDir(composePath)

	run := func(oneOff bool) ([]byte, error) {
		args := apiCommandArgs(composePath, oneOff, resetOwnerCmd...)
		return runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: args, Env: envOverlay, Dir: dir})
	}
	stdout, err := run(false)
	if isServiceNotRunning(err) {
		stdout, err = run(true)
	}
	if err != nil {
		if strings.Contains(err.Error(), "Chưa có tài khoản Owner") {
			return &OpError{
				Code: ErrCodeResetPasswordFailed,
				What: "Chưa có tài khoản Owner để đặt lại mật khẩu",
				Why:  "trình thiết lập chưa qua bước 2 (tạo Owner)",
				Next: "Mở " + ConsoleURL(env.Port) + " và làm tiếp trình thiết lập.",
				Err:  err,
			}
		}
		return &OpError{
			Code: ErrCodeResetPasswordFailed,
			What: "Không đặt lại được mật khẩu Owner",
			Why:  err.Error(),
			Next: "Chạy `genh status` (db và api phải healthy) rồi thử lại `genh reset-password`.",
			Err:  err,
		}
	}

	var res struct {
		Email        string `json:"email"`
		TempPassword string `json:"temp_password"`
	}
	if perr := parseLastJSONLine(stdout, &res); perr != nil || res.Email == "" || res.TempPassword == "" {
		why := "thiếu email/mật khẩu trong kết quả"
		if perr != nil {
			why = perr.Error()
		}
		return &OpError{
			Code: ErrCodeResetPasswordFailed,
			What: "Đã chạy đặt lại mật khẩu nhưng không đọc được kết quả",
			Why:  why,
			Next: "Chạy lại `genh reset-password` — mỗi lần chạy sinh một mật khẩu tạm mới.",
			Err:  perr,
		}
	}

	_, _ = fmt.Fprintln(out, "Đã đặt lại mật khẩu Owner (dữ liệu giữ nguyên, các phiên đăng nhập cũ đã bị đăng xuất).")
	_, _ = fmt.Fprintln(out, "Email đăng nhập: "+res.Email)
	_, _ = fmt.Fprintln(out, "Mật khẩu tạm: "+res.TempPassword)
	_, _ = fmt.Fprintln(out, "Đăng nhập tại "+localURL(env.Port, "/login")+" — Console sẽ yêu cầu đặt mật khẩu mới ngay khi đăng nhập.")
	return nil
}

// parseLastJSONLine đọc dòng không rỗng CUỐI CÙNG của stdout làm JSON — phòng
// trường hợp có dòng log lọt ra stdout trước đó.
func parseLastJSONLine(stdout []byte, v any) error {
	lines := strings.Split(strings.TrimSpace(string(stdout)), "\n")
	last := strings.TrimSpace(lines[len(lines)-1])
	if last == "" {
		return fmt.Errorf("output rỗng")
	}
	return json.Unmarshal([]byte(last), v)
}
