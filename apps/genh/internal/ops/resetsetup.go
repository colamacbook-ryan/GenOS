package ops

import (
	"bufio"
	"fmt"
	"io"
	"strings"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/secretgen"
)

// ResetSetupOptions là các cờ đã phân tích của `genh reset-setup`.
type ResetSetupOptions struct {
	// AutoApprove ứng với cờ --yes, nhất quán với `genh install --yes` — bỏ
	// qua hỏi xác nhận.
	AutoApprove bool
}

// confirmYesNo đọc một dòng từ in, trả true nếu là "y"/"yes" (không phân
// biệt hoa/thường, khoảng trắng đầu/cuối bị cắt).
func confirmYesNo(in io.Reader) bool {
	scanner := bufio.NewScanner(in)
	if !scanner.Scan() {
		return false
	}
	answer := strings.ToLower(strings.TrimSpace(scanner.Text()))
	return answer == "y" || answer == "yes"
}

// RunResetSetup sinh mã thiết lập MỚI (secretgen.RegenerateSetupToken), làm
// mã cũ hết hiệu lực — hỏi xác nhận trước (đọc "y"/"yes" từ in) TRỪ KHI
// opts.AutoApprove.
func RunResetSetup(env *Env, opts ResetSetupOptions, in io.Reader, out io.Writer) error {
	if !opts.AutoApprove {
		_, _ = fmt.Fprintln(out, "Sinh mã thiết lập mới sẽ làm mã hiện tại KHÔNG còn dùng được. Tiếp tục? [y/N]")
		if !confirmYesNo(in) {
			return &OpError{
				Code: ErrCodeResetSetupCancelled,
				What: "Đã huỷ — không sinh mã thiết lập mới",
				Next: "Chạy lại `genh reset-setup` (hoặc kèm --yes) khi muốn thật sự sinh mã mới.",
			}
		}
	}

	newToken, err := secretgen.RegenerateSetupToken(env.ConfigDir())
	if err != nil {
		return &OpError{
			Code: ErrCodeResetSetupFailed,
			What: "Không sinh được mã thiết lập mới",
			Why:  err.Error(),
			Next: "Kiểm Gen-Harness đã cài (`genh install`) và quyền ghi thư mục cấu hình.",
			Err:  err,
		}
	}

	newURL := SetupURLWithToken(env.Port, newToken)
	_, _ = fmt.Fprintln(out, "Mã cũ không còn dùng được.")
	_, _ = fmt.Fprintln(out, "Mã thiết lập mới: "+newToken)
	_, _ = fmt.Fprintln(out, "Mở: "+newURL)
	return nil
}

// SetupURLWithToken dựng URL trình thiết lập (https://localhost:<port>/setup?token=…)
// — cùng định dạng install.SetupURL, viết lại ở đây (nhỏ, không đáng import
// package install chỉ cho một hàm) để ops không phụ thuộc install.Env.
func SetupURLWithToken(port int, token string) string {
	return fmt.Sprintf("https://localhost:%d/setup?token=%s", ResolvePort(port), token)
}
