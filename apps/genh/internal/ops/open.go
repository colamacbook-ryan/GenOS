package ops

import (
	"fmt"
	"io"
	"net/url"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/browseropen"
)

// OpenDeps cho phép tiêm hàm mở trình duyệt giả khi test — nil dùng
// browseropen.Open thật.
type OpenDeps struct {
	OpenBrowser func(url string) error
}

// RunOpen mở Console (URL gốc, KHÔNG kèm token thiết lập — khác URL setup
// của Bước 8) trong trình duyệt hệ thống.
//
// Cố ý KHÔNG tự "cài" (sẽ tự sinh cả bộ bí mật mới nếu secrets.json chưa
// từng tồn tại — sai với "open" một bản cài chưa từng chạy `genh install`):
// kiểm thư mục cấu hình có bí mật thật trước (env.LoadSecrets, dùng
// secretgen.LoadFillingMissing — chỉ bổ sung TRƯỜNG THIẾU của một bộ bí mật
// đã có, không sinh mới cả bộ), báo lỗi rõ ràng nếu chưa cài.
func RunOpen(env *Env, deps OpenDeps, out io.Writer) error {
	if _, err := env.LoadSecrets(); err != nil {
		if opErr, ok := err.(*OpError); ok {
			opErr.Code = ErrCodeOpenFailed
			opErr.What = "Chưa cài Gen-Harness — chưa có gì để mở"
			opErr.Next = "Chạy `genh install` trước."
		}
		return err
	}

	consoleURL := ConsoleURL(env.Port)

	openBrowser := deps.OpenBrowser
	if openBrowser == nil {
		openBrowser = browseropen.Open
	}
	if err := openBrowser(consoleURL); err != nil {
		return &OpError{
			Code: ErrCodeOpenFailed,
			What: "Không mở được trình duyệt",
			Why:  err.Error(),
			Next: "Tự mở tay: " + consoleURL,
			Err:  err,
		}
	}

	_, _ = fmt.Fprintln(out, "Đã mở Console: "+consoleURL)
	return nil
}

// ConsoleURL dựng URL gốc của Console (https://localhost:<port>/) — KHÁC
// install.SetupURL (không có query ?token=, vì đây là mở Console bình
// thường sau khi đã thiết lập xong, không phải trình thiết lập lần đầu).
func ConsoleURL(port int) string {
	u := url.URL{Scheme: "https", Host: fmt.Sprintf("localhost:%d", ResolvePort(port)), Path: "/"}
	return u.String()
}
