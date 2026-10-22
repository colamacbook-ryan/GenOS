package tui

import (
	"fmt"
	"strings"
	"time"
	"unicode/utf8"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/install"
)

// contentWidth là bề ngang phần nội dung bên trong hai viền dọc, tính sao
// cho tổng khung (viền + nội dung + viền) đúng FrameWidth (76 cột) như
// mockup trong docs/handoff/05-installer.md.
const contentWidth = FrameWidth - 2

// Kích thước cố định của các cột trong một dòng bước, theo đúng bố cục
// mockup: lề trái, biểu tượng trạng thái, tên bước, chi tiết, thời lượng.
const (
	rowLeftMargin  = 2
	rowIconWidth   = 1
	rowIconGap     = 2
	rowNameWidth   = 32
	rowElapsedW    = 6
	rowRightMargin = 2
	subLineIndent  = rowLeftMargin + rowIconWidth + rowIconGap + 2

	progressBarWidth = 52
)

var spinnerFrames = []string{"⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"}

// FinishInfo là dữ liệu cho màn "Hoàn tất" (cuối docs/handoff/05-installer.md
// mục "Màn kết thúc"). Phase A chỉ định nghĩa và vẽ được màn này — việc mở
// trình duyệt/tạo mã thiết lập thật thuộc Bước 8 (phiên sau).
type FinishInfo struct {
	Duration      time.Duration
	SetupURL      string
	SetupCode     string
	CodeExpiresIn time.Duration
	BrowserOpened bool
}

// Model là tea.Model của TUI cài đặt. Chỉ nên dựng qua NewModel (test) hoặc
// NewForTerminal (chương trình thật) — không literal trực tiếp để styles
// luôn được khởi tạo.
type Model struct {
	Version    string
	InstallDir string
	Snapshot   install.Snapshot
	Finished   *FinishInfo
	LogVisible bool
	Quitting   bool
	// Cancelled phân biệt "q" (Owner chủ động huỷ an toàn) với việc chương
	// trình tự thoát sau khi Runner xong (DoneMsg) — cả hai đều đặt
	// Quitting=true, nhưng chỉ Cancelled mới cần cmd/genh huỷ ctx của
	// Runner (dừng container đã tạo, giữ image đã tải, theo đúng mục "q
	// huỷ an toàn" trong tài liệu).
	Cancelled bool

	styles     Styles
	spinnerIdx int
}

// NewModel dựng Model với một bộ Styles cho trước — dùng trong test để
// không phụ thuộc terminal thật (xem model_test.go, dùng
// lipgloss.NewRenderer(io.Discard)).
func NewModel(styles Styles, version, installDir string) Model {
	return Model{Version: version, InstallDir: installDir, styles: styles}
}

// SnapshotMsg mang một install.Snapshot mới vào vòng lặp Bubble Tea — gửi
// qua (*tea.Program).Send từ goroutine chạy Runner.
type SnapshotMsg install.Snapshot

// FinishMsg báo cài đặt đã xong, chuyển Model sang màn "Hoàn tất".
type FinishMsg FinishInfo

// DoneMsg báo Runner.Run đã trả về (thành công hay lỗi, mà không phải một
// màn "Hoàn tất" đẹp — ví dụ khi còn bước chưa triển khai). Model chỉ thoát
// vòng lặp tương tác; cmd/genh in tóm tắt/hướng dẫn tiếp theo ra terminal
// sau khi chương trình Bubble Tea kết thúc.
type DoneMsg struct{ Err error }

type tickMsg time.Time

func tickCmd() tea.Cmd {
	return tea.Tick(120*time.Millisecond, func(t time.Time) tea.Msg { return tickMsg(t) })
}

func (m Model) Init() tea.Cmd { return tickCmd() }

func (m Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		switch msg.String() {
		case "q", "ctrl+c":
			m.Quitting = true
			m.Cancelled = true
			return m, tea.Quit
		case "l":
			m.LogVisible = !m.LogVisible
		}
	case DoneMsg:
		m.Quitting = true
		return m, tea.Quit
	case tickMsg:
		m.spinnerIdx = (m.spinnerIdx + 1) % len(spinnerFrames)
		return m, tickCmd()
	case SnapshotMsg:
		m.Snapshot = install.Snapshot(msg)
	case FinishMsg:
		f := FinishInfo(msg)
		m.Finished = &f
	}
	return m, nil
}

func (m Model) View() string {
	if m.Quitting {
		return ""
	}
	if m.Finished != nil {
		return m.renderFinish()
	}
	return m.renderProgress()
}

func (m Model) renderProgress() string {
	var lines []string
	lines = append(lines, m.headerLines()...)
	lines = append(lines, "")
	lines = append(lines, m.progressBarLine())
	lines = append(lines, "")

	for i, s := range m.Snapshot.Steps {
		lines = append(lines, m.stepLine(s))
		if s.Status == install.StatusRunning {
			for _, sl := range firstN(s.SubLines, 4) {
				lines = append(lines, subLine(sl))
			}
		}
		_ = i
	}

	lines = append(lines, "")
	lines = append(lines, withMargin("l  xem log     q  huỷ an toàn"))

	return m.frame(lines)
}

// withMargin thêm lề trái chuẩn (2 cột, khớp mockup "│  ...") cho các dòng
// không có icon riêng (tiêu đề, thanh tiến độ, chân trang).
func withMargin(s string) string { return strings.Repeat(" ", rowLeftMargin) + s }

func (m Model) headerLines() []string {
	title := fmt.Sprintf("GEN-HARNESS  ·  Genesis Harness OS  %s", m.Version)
	sub := fmt.Sprintf("Đang cài đặt vào %s", m.InstallDir)
	return []string{
		withMargin(m.styles.Title.Render(title)),
		withMargin(sub),
	}
}

func (m Model) progressBarLine() string {
	pct := m.Snapshot.OverallPct
	if pct < 0 {
		pct = 0
	}
	if pct > 100 {
		pct = 100
	}
	filled := int(pct/100*progressBarWidth + 0.5)
	bar := strings.Repeat("█", filled) + strings.Repeat("░", progressBarWidth-filled)

	lb := newLineBuilder()
	lb.plain(strings.Repeat(" ", rowLeftMargin))
	lb.styled(bar, m.styles.Accent)
	lb.plain(fmt.Sprintf("   %3.0f%%", pct))
	return lb.String()
}

func (m Model) stepLine(s install.StepState) string {
	lb := newLineBuilder()
	lb.plain(strings.Repeat(" ", rowLeftMargin))
	lb.styled(statusIconChar(s.Status, m.spinnerIdx), m.iconStyle(s.Status))
	lb.plain(strings.Repeat(" ", rowIconGap))
	lb.plain(padRight(truncate(s.Name, rowNameWidth-1), rowNameWidth))

	detailWidth := contentWidth - rowLeftMargin - rowIconWidth - rowIconGap - rowNameWidth - rowElapsedW - rowRightMargin
	if detailWidth < 0 {
		detailWidth = 0
	}
	lb.plain(padRight(truncate(s.Detail, detailWidth), detailWidth))

	elapsed := ""
	if s.Status != install.StatusPending {
		elapsed = formatElapsed(s.Elapsed)
	}
	lb.plain(padLeft(elapsed, rowElapsedW))
	return lb.String()
}

func subLine(text string) string {
	width := contentWidth - subLineIndent
	if width < 0 {
		width = 0
	}
	return strings.Repeat(" ", subLineIndent) + padRight(truncate(text, width), width)
}

// statusIconChar trả về ký tự biểu tượng theo đúng quy ước tài liệu: `·`
// chờ · spinner đang chạy · `✓` xong (OK/bỏ qua) · `!` cảnh báo · `✕` lỗi.
func statusIconChar(status install.Status, spinnerIdx int) string {
	switch status {
	case install.StatusRunning:
		return spinnerFrames[spinnerIdx%len(spinnerFrames)]
	case install.StatusOK, install.StatusSkipped:
		return "✓"
	case install.StatusWarn:
		return "!"
	case install.StatusError:
		return "✕"
	default:
		return "·"
	}
}

func (m Model) iconStyle(status install.Status) lipgloss.Style {
	switch status {
	case install.StatusOK, install.StatusSkipped:
		return m.styles.OK
	case install.StatusWarn:
		return m.styles.Warn
	case install.StatusError:
		return m.styles.Bad
	case install.StatusRunning:
		return m.styles.Accent
	default:
		return m.styles.Pending
	}
}

func firstN(s []string, n int) []string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}

func formatElapsed(d time.Duration) string {
	if d < 0 {
		d = 0
	}
	total := int(d.Round(time.Second).Seconds())
	m := total / 60
	s := total % 60
	return fmt.Sprintf("%d:%02d", m, s)
}

// runeWidth đo bề rộng hiển thị của chuỗi thuần văn bản (không mã ANSI).
// Ký tự tiếng Việt có dấu vẫn là 1 rune có độ rộng 1 cột, nên đếm rune là
// đủ — không cần xử lý ký tự rộng 2 cột (CJK) trong TUI này.
func runeWidth(s string) int { return utf8.RuneCountInString(s) }

func truncate(s string, max int) string {
	if max <= 0 {
		return ""
	}
	if runeWidth(s) <= max {
		return s
	}
	r := []rune(s)
	if max == 1 {
		return string(r[:1])
	}
	return string(r[:max-1]) + "…"
}

func padRight(s string, width int) string {
	w := runeWidth(s)
	if w >= width {
		return s
	}
	return s + strings.Repeat(" ", width-w)
}

func padLeft(s string, width int) string {
	w := runeWidth(s)
	if w >= width {
		return s
	}
	return strings.Repeat(" ", width-w) + s
}

func plain(s string) string { return s }
