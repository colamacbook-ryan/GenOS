// Package install cài khung tiến trình cài đặt tổng thể của genh: 8 bước
// theo đúng bảng trọng số trong docs/handoff/05-installer.md, mỗi bước là
// một cài đặt của interface Step. Gói này KHÔNG tự vẽ TUI — nó phát ra
// Progress qua Reporter, và internal/tui (hoặc chế độ dòng không TTY) chịu
// trách nhiệm hiển thị.
//
// Cả 8 bước đều được cài đặt thật — xem Registry() trong registry.go.
package install

import (
	"context"
	"time"
)

// StepID định danh 8 bước cài đặt, đúng thứ tự trong bảng của tài liệu.
type StepID int

const (
	StepMachineCheck  StepID = iota + 1 // 1 · Kiểm tra máy
	StepRuntime                         // 2 · Chuẩn bị container runtime
	StepPullImages                      // 3 · Tải image
	StepSecrets                         // 4 · Sinh bí mật & cấu hình
	StepStartData                       // 5 · Khởi động dữ liệu
	StepMigrate                         // 6 · Tạo cấu trúc dữ liệu
	StepStartServices                   // 7 · Khởi động dịch vụ
	StepFinalize                        // 8 · Hoàn tất
)

// stepWeights là trọng số % đúng theo bảng "Các bước và trọng số tiến độ"
// trong docs/handoff/05-installer.md. Tổng = 100.
var stepWeights = map[StepID]float64{
	StepMachineCheck:  3,
	StepRuntime:       17,
	StepPullImages:    50,
	StepSecrets:       3,
	StepStartData:     8,
	StepMigrate:       9,
	StepStartServices: 6,
	StepFinalize:      4,
}

// Weight trả về trọng số % của một bước (0 nếu id không hợp lệ).
func (id StepID) Weight() float64 { return stepWeights[id] }

// Status là trạng thái hiển thị của một bước trong TUI: chờ/đang chạy/xong
// (OK)/cảnh báo (WARN, vẫn tiếp tục)/lỗi (BAD).
type Status int

const (
	StatusPending Status = iota
	StatusRunning
	StatusOK
	StatusWarn
	StatusError
	// StatusSkipped: bước tự nhận đã xong từ trước (ví dụ runtime hợp lệ có
	// sẵn) — hiển thị như OK nhưng Detail nói rõ lý do bỏ qua.
	StatusSkipped
)

func (s Status) String() string {
	switch s {
	case StatusPending:
		return "pending"
	case StatusRunning:
		return "running"
	case StatusOK:
		return "ok"
	case StatusWarn:
		return "warn"
	case StatusError:
		return "error"
	case StatusSkipped:
		return "skipped"
	default:
		return "unknown"
	}
}

// StepError là lỗi có cấu trúc theo đúng yêu cầu tài liệu: "chuyện gì xảy ra
// · vì sao · làm gì tiếp", kèm mã tra được (GH-E0xx).
type StepError struct {
	Code string // ví dụ "GH-E021"
	What string // chuyện gì xảy ra
	Why  string // vì sao
	Next string // làm gì tiếp (gợi ý khắc phục, có thể gồm cả lệnh)
	Err  error  // lỗi gốc, nếu có — dùng cho log/%w, không hiển thị thẳng trong TUI
}

func (e *StepError) Error() string {
	if e == nil {
		return ""
	}
	msg := e.What
	if e.Why != "" {
		msg += ": " + e.Why
	}
	if e.Code != "" {
		msg += " (" + e.Code + ")"
	}
	return msg
}

func (e *StepError) Unwrap() error { return e.Err }

// Progress là một lần cập nhật trạng thái mà một Step phát ra qua Reporter
// trong lúc chạy. TUI/chế độ dòng gộp các Progress này lại để vẽ khung theo
// mockup trong tài liệu (một dòng mỗi bước, dòng con tối đa 4 dòng cho bước
// đang chạy).
type Progress struct {
	StepID   StepID
	Status   Status
	Percent  float64  // 0..100, tiến độ NỘI BỘ của riêng bước này
	Detail   string   // dòng mô tả ngắn cùng hàng với tên bước, ví dụ "8 GB RAM · 112 GB trống"
	SubLines []string // tối đa 4 dòng con, chỉ có ý nghĩa khi Status == StatusRunning
	Elapsed  time.Duration
	Err      *StepError // khác nil khi Status == StatusError
}

// Reporter là cách một Step báo tiến độ ngược lại cho Runner/TUI. Một Step
// có thể gọi Report nhiều lần trong lúc chạy (ví dụ mỗi layer image tải
// xong); Runner tự thêm StepID nếu Step bỏ trống.
type Reporter interface {
	Report(Progress)
}

// ReporterFunc cho phép dùng một hàm thường làm Reporter, tiện cho test.
type ReporterFunc func(Progress)

func (f ReporterFunc) Report(p Progress) { f(p) }

// Env là ngữ cảnh dùng chung mà Runner truyền cho mọi Step: đường dẫn cài
// đặt, cờ dòng lệnh, và (khi có) bí mật đã sinh ở Bước 4 để các bước sau
// dùng lại (ví dụ Bước 5 cần mật khẩu DB để khởi động container db).
//
// Các trường của Env sẽ được phiên sau mở rộng khi cắm bước 2/3/5/6/7/8
// (ví dụ: client Docker, danh sách image cần tải, cổng đã chọn…) — Step chỉ
// nên đọc field nó cần, không giả định Env đứng yên.
type Env struct {
	// InstallDir là gốc cài đặt, mặc định ~/.gen-harness (xem
	// internal/config.DefaultRoot), có thể ghi đè để test hoặc bằng cờ CLI.
	InstallDir string

	// Port là cổng HTTPS cho proxy, mặc định 8443 (machine.DefaultPort).
	// Bước 7 dùng lại đúng giá trị này để kiểm /api/v1/ready qua đúng cổng
	// Owner đã chọn.
	Port int

	// Secrets là kết quả Bước 4 sau khi chạy xong (nil trước đó) — các bước
	// 5 trở đi đọc từ đây (type-assert về secretgen.Result).
	Secrets any

	// AutoApprove ứng với cờ `genh install --yes`: cho phép các bước cần
	// thay đổi hệ thống rộng mà bình thường phải hỏi trước — Bước 2 (cài
	// Docker rootless cần sudo cho newuidmap, hoặc bật WSL2 cần UAC) và
	// Bước 8 (tin cậy CA nội bộ vào kho hệ điều hành) — tự thực hiện thay
	// vì dừng lại chờ Owner tự chạy lệnh. false là mặc định an toàn: các
	// bước đó vẫn CHẠY (tải, giải nén, chuẩn bị) nhưng dừng trước thao tác
	// cần quyền rộng, báo rõ lệnh Owner tự chạy hoặc chạy lại kèm --yes.
	AutoApprove bool

	// ComposePath, nếu khác rỗng, ghi đè việc tự dò deploy/compose.yaml
	// (xem internal/compose.Locate) — dùng khi test hoặc khi Owner cài đặt
	// từ một bản sao repo không theo cấu trúc mặc định.
	ComposePath string

	// BrowserOpened được Bước 8 (Hoàn tất) đặt true nếu nó tự mở được trình
	// duyệt vào trình thiết lập Owner. cmd/genh đọc lại trường này sau khi
	// Runner.Run() trả về để điền tui.FinishInfo.BrowserOpened — cách này
	// tránh phải mở trình duyệt lần thứ hai từ main.go (Bước 8 đã mở đúng
	// một lần, main.go chỉ cần biết kết quả).
	BrowserOpened bool

	// FreshInstall (v0.1.46, F-21): true khi config/secrets.json CHƯA tồn tại
	// trước Bước 4 — cài mới thật sự. Bước 5 dùng để ghi .env cho cài mới là
	// 127.0.0.1/local (cài lại/tiếp tục bản dở giữ lựa chọn cũ hoặc lan_legacy).
	// Không dựa vào GeneratedNew: nó cũng true khi chỉ bổ sung trường thiếu.
	FreshInstall bool
}

// Step là đơn vị công việc của một trong 8 bước cài đặt. Mỗi Step biết
// StepID/tên hiển thị/trọng số của chính nó, và cách chạy.
type Step interface {
	// ID trả về StepID cố định của bước này.
	ID() StepID

	// Name là tên hiển thị tiếng Việt trong TUI, ví dụ "Kiểm tra máy".
	Name() string

	// Run thực hiện bước, báo tiến độ qua rep trong lúc chạy, và trả về lỗi
	// có cấu trúc (*StepError) nếu thất bại. Run phải idempotent: gọi lại
	// sau khi lỗi hoặc sau khi đã chạy xong không được phá dữ liệu/bí mật đã
	// có, và nên tự nhận ra việc đã xong để báo StatusSkipped/StatusOK ngay.
	Run(ctx context.Context, env *Env, rep Reporter) error
}
