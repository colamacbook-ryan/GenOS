package tui

import (
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/lipgloss"
)

// finishBodyIndent là lề trái của các dòng thân trong màn "Hoàn tất" — bằng
// đúng lề+icon+khoảng cách của một dòng bước (2+1+2=5), để chữ thẳng hàng
// dưới tên bước như mockup.
const finishBodyIndent = rowLeftMargin + rowIconWidth + rowIconGap

// lineBuilder dựng một dòng nội dung bên trong khung: theo dõi bề rộng hiển
// thị thật (không tính mã ANSI) để pad/canh lề đúng, cho phép chèn các đoạn
// tô màu riêng (icon, thanh tiến độ…) xen giữa văn bản thường mà không làm
// lệch cột — vì mã ANSI không chiếm bề rộng hiển thị trên terminal.
type lineBuilder struct {
	parts []string
	width int
}

func newLineBuilder() *lineBuilder { return &lineBuilder{} }

func (lb *lineBuilder) plain(s string) *lineBuilder {
	lb.parts = append(lb.parts, s)
	lb.width += runeWidth(s)
	return lb
}

func (lb *lineBuilder) styled(s string, style lipgloss.Style) *lineBuilder {
	lb.parts = append(lb.parts, style.Render(s))
	lb.width += runeWidth(s)
	return lb
}

func (lb *lineBuilder) String() string { return strings.Join(lb.parts, "") }

// frame bọc danh sách dòng nội dung (đã tính sẵn lề trái nếu cần) trong
// khung viền bo 76 cột, mỗi dòng được pad/cắt đúng contentWidth.
func (m Model) frame(lines []string) string {
	var b strings.Builder

	b.WriteString(m.styles.Border.Render("╭" + strings.Repeat("─", contentWidth) + "╮"))
	b.WriteByte('\n')

	for _, l := range lines {
		b.WriteString(m.styles.Border.Render("│"))
		b.WriteString(fitLine(l, contentWidth))
		b.WriteString(m.styles.Border.Render("│"))
		b.WriteByte('\n')
	}

	b.WriteString(m.styles.Border.Render("╰" + strings.Repeat("─", contentWidth) + "╯"))
	return b.String()
}

// fitLine pad một dòng (có thể chứa mã ANSI đã chèn qua lineBuilder) tới
// đúng contentWidth cột hiển thị. Cắt bớt chỉ áp dụng cho dòng thuần văn
// bản (không mã màu) — các dòng dựng qua lineBuilder tự chịu trách nhiệm
// không vượt quá contentWidth bằng padRight/truncate ở nơi gọi.
func fitLine(s string, width int) string {
	w := lineDisplayWidth(s)
	if w < width {
		return s + strings.Repeat(" ", width-w)
	}
	if w > width && !strings.Contains(s, "\x1b[") {
		return truncate(s, width)
	}
	return s
}

// lineDisplayWidth đo bề rộng hiển thị bỏ qua mã thoát ANSI (dùng khi dòng
// đã được lineBuilder tô màu từng đoạn).
func lineDisplayWidth(s string) int {
	inEscape := false
	n := 0
	for _, r := range s {
		switch {
		case inEscape:
			if r == 'm' {
				inEscape = false
			}
		case r == '\x1b':
			inEscape = true
		default:
			n++
		}
	}
	return n
}

func (m Model) renderFinish() string {
	f := m.Finished
	var lines []string

	lb := newLineBuilder()
	lb.plain(strings.Repeat(" ", rowLeftMargin))
	lb.styled("✓", m.styles.OK)
	lb.plain(strings.Repeat(" ", rowIconGap) + "Gen-Harness đã sẵn sàng")
	durationText := formatFinishDuration(f.Duration)
	pad := contentWidth - rowRightMargin - lb.width - runeWidth(durationText)
	if pad > 0 {
		lb.plain(strings.Repeat(" ", pad))
	}
	lb.plain(durationText)
	lines = append(lines, lb.String())
	lines = append(lines, "")

	indent := strings.Repeat(" ", finishBodyIndent)
	if f.SetupURL != "" {
		lines = append(lines, plain(indent+"Mở trình thiết lập:  "+f.SetupURL))
	}
	if f.SetupCode != "" {
		expiry := ""
		if f.CodeExpiresIn > 0 {
			expiry = fmt.Sprintf("    (hết hạn sau %s)", formatHours(f.CodeExpiresIn))
		}
		lines = append(lines, plain(indent+"Mã thiết lập:        "+f.SetupCode+expiry))
	}
	lines = append(lines, "")

	if f.BrowserOpened {
		lines = append(lines, plain(indent+"Đã mở trình duyệt. Nếu chưa thấy, bấm vào đường dẫn trên."))
	} else {
		lines = append(lines, plain(indent+"Mở đường dẫn trên trong trình duyệt để tiếp tục."))
	}
	lines = append(lines, "")

	lines = append(lines, plain(indent+"genh status    trạng thái        genh logs     xem log"))
	lines = append(lines, plain(indent+"genh update    cập nhật          genh backup   sao lưu"))

	return m.frame(lines)
}

func formatFinishDuration(d time.Duration) string {
	total := int(d.Round(time.Second).Seconds())
	m := total / 60
	s := total % 60
	if m == 0 {
		return fmt.Sprintf("%ds", s)
	}
	return fmt.Sprintf("%d phút %ds", m, s)
}

func formatHours(d time.Duration) string {
	h := int(d.Round(time.Hour).Hours())
	if h <= 1 {
		return "1 giờ"
	}
	return fmt.Sprintf("%d giờ", h)
}
