// Package tui vẽ TUI cài đặt của genh theo đúng mockup trong
// docs/handoff/05-installer.md: khung 76 cột viền bo, thanh tiến độ tổng có
// %, danh sách bước có trạng thái, dòng con tối đa 4 dòng cho bước đang
// chạy. Khi không có TTY (CI, pipe), dùng LineRenderer thay vì Model —
// xem linerenderer.go.
package tui

import (
	"os"

	"github.com/charmbracelet/lipgloss"
	"github.com/muesli/termenv"
)

// FrameWidth là bề ngang khung theo đúng mockup (76 cột).
const FrameWidth = 76

// Màu lấy từ token Nocturne (packages/tokens/tokens.css):
//   - accent: --color-accent (#9184d9)
//   - OK/WARN/BAD: --color-ok / --color-warn / --color-bad (oklch, quy đổi
//     sang hex sRGB gần đúng để dùng trong terminal 24-bit)
const (
	ColorAccent = "#9184d9"
	ColorOK     = "#60c99a"
	ColorWarn   = "#f2c35e"
	ColorBad    = "#ff8782"
	ColorDim    = "8" // xám ANSI chuẩn, dùng cho chữ phụ/`·` chờ
)

// ColorEnabled quyết định có tô màu hay không: tắt khi NO_COLOR được đặt
// (https://no-color.org) hoặc renderer tự phát hiện terminal không hỗ trợ
// màu (renderer.ColorProfile() == termenv.Ascii — không TTY, TERM=dumb…).
func ColorEnabled(renderer *lipgloss.Renderer) bool {
	if os.Getenv("NO_COLOR") != "" {
		return false
	}
	return renderer.ColorProfile() != termenv.Ascii
}

// Styles gói các lipgloss.Style dùng trong Model.View(), dựng theo một
// renderer cụ thể (để test không phụ thuộc terminal thật — xem
// lipgloss.NewRenderer(io.Discard) trong model_test.go) và có tự tắt màu.
type Styles struct {
	Border  lipgloss.Style
	Title   lipgloss.Style
	Dim     lipgloss.Style
	Accent  lipgloss.Style
	OK      lipgloss.Style
	Warn    lipgloss.Style
	Bad     lipgloss.Style
	Pending lipgloss.Style
}

// NewStyles dựng bộ style cho renderer đã cho. color=false trả về style
// "trong suốt" (không mã ANSI nào), dùng khi NO_COLOR hoặc không phải TTY.
func NewStyles(renderer *lipgloss.Renderer, color bool) Styles {
	base := renderer.NewStyle()
	if !color {
		return Styles{
			Border:  base,
			Title:   base.Bold(true),
			Dim:     base,
			Accent:  base,
			OK:      base,
			Warn:    base,
			Bad:     base,
			Pending: base,
		}
	}
	return Styles{
		Border:  base.Foreground(lipgloss.Color(ColorAccent)),
		Title:   base.Foreground(lipgloss.Color(ColorAccent)).Bold(true),
		Dim:     base.Foreground(lipgloss.Color(ColorDim)),
		Accent:  base.Foreground(lipgloss.Color(ColorAccent)),
		OK:      base.Foreground(lipgloss.Color(ColorOK)),
		Warn:    base.Foreground(lipgloss.Color(ColorWarn)),
		Bad:     base.Foreground(lipgloss.Color(ColorBad)),
		Pending: base.Foreground(lipgloss.Color(ColorDim)),
	}
}
