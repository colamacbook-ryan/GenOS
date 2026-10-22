package tui

import (
	"os"

	"golang.org/x/term"
)

// IsTerminal báo true nếu f là một TTY thật. cmd/genh/main.go dùng nó để
// chọn giữa Model (TUI tương tác) và LineRenderer (chế độ dòng) — theo yêu
// cầu "Không có TTY (CI, pipe): tự chuyển sang chế độ dòng" trong
// docs/handoff/05-installer.md.
func IsTerminal(f *os.File) bool {
	return term.IsTerminal(int(f.Fd()))
}
