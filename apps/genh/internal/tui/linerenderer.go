package tui

import (
	"fmt"
	"io"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/install"
)

// LineRenderer vẽ tiến độ ở chế độ dòng — dùng khi không có TTY (CI, pipe
// qua `| sh`, log ra tệp…), theo đúng docs/handoff/05-installer.md: "mỗi
// bước một dòng `[ 58%] Tải image 412/690 MB`". Không có màu, không animation,
// không giả định con trỏ điều khiển được vị trí dòng — mỗi lần Render chỉ
// ghi thêm một dòng mới (phù hợp khi output bị redirect vào tệp/log).
type LineRenderer struct {
	w io.Writer

	// lastLine tránh in trùng một dòng y hệt liên tiếp (ví dụ nhiều
	// Progress không đổi gì đáng kể) — vẫn cho phép in lại khi có thay đổi
	// thật (%, chi tiết, trạng thái).
	lastLine string
}

// NewLineRenderer dựng LineRenderer ghi vào w (thường là os.Stdout).
func NewLineRenderer(w io.Writer) *LineRenderer {
	return &LineRenderer{w: w}
}

// Line dựng đúng một dòng văn bản cho một Snapshot — hàm thuần, không ghi
// I/O, để test được nội dung chính xác không cần io.Writer giả.
func Line(s install.Snapshot) string {
	name := "—"
	detail := ""
	if s.CurrentIndex >= 0 && s.CurrentIndex < len(s.Steps) {
		cur := s.Steps[s.CurrentIndex]
		name = cur.Name
		detail = cur.Detail
	} else if n := lastNonPending(s.Steps); n != "" {
		name = n
	}

	line := fmt.Sprintf("[%3.0f%%] %s", clampPct(s.OverallPct), name)
	if detail != "" {
		line += " " + detail
	}
	return line
}

func lastNonPending(steps []install.StepState) string {
	for i := len(steps) - 1; i >= 0; i-- {
		if steps[i].Status != install.StatusPending {
			return steps[i].Name
		}
	}
	return ""
}

func clampPct(pct float64) float64 {
	if pct < 0 {
		return 0
	}
	if pct > 100 {
		return 100
	}
	return pct
}

// Observe cài install.Observer: in một dòng mỗi khi Snapshot đổi, bỏ qua
// nếu dòng y hệt lần trước (không xả log vô ích khi không có gì mới).
func (r *LineRenderer) Observe(s install.Snapshot) {
	line := Line(s)
	if line == r.lastLine {
		return
	}
	r.lastLine = line
	fmt.Fprintln(r.w, line)
}

// Finish in dòng kết thúc ở chế độ dòng — không có khung/URL đẹp như TUI,
// chỉ đủ để script/CI đọc được, đúng tinh thần "mã thoát" mà tài liệu nhắc
// tới cho chế độ không TTY.
func (r *LineRenderer) Finish(f FinishInfo) {
	fmt.Fprintf(r.w, "[100%%] Gen-Harness đã sẵn sàng (%s)\n", formatFinishDuration(f.Duration))
	if f.SetupURL != "" {
		fmt.Fprintf(r.w, "Mở trình thiết lập: %s\n", f.SetupURL)
	}
	if f.SetupCode != "" {
		fmt.Fprintf(r.w, "Mã thiết lập: %s\n", f.SetupCode)
	}
}

// FinishError in dòng lỗi ở chế độ dòng, đúng mẫu "chuyện gì · vì sao · làm
// gì tiếp" + mã GH-E0xx, không cần vẽ khung.
func (r *LineRenderer) FinishError(se *install.StepError) {
	if se == nil {
		return
	}
	fmt.Fprintf(r.w, "LỖI %s: %s\n", se.Code, se.What)
	if se.Why != "" {
		fmt.Fprintf(r.w, "  vì sao: %s\n", se.Why)
	}
	if se.Next != "" {
		fmt.Fprintf(r.w, "  làm gì tiếp: %s\n", se.Next)
	}
}
