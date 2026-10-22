package tui

import (
	"io"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/install"
)

func keyMsg(r rune) tea.KeyMsg {
	return tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{r}}
}

// noColorStyles dựng Styles không tô màu (renderer ghi vào io.Discard nên
// tự động không có TTY thật) — dùng để test nội dung/bố cục mà không cần
// terminal thật, và để so khớp chuỗi không bị mã ANSI xen vào.
func noColorStyles() Styles {
	r := lipgloss.NewRenderer(io.Discard)
	return NewStyles(r, false)
}

func sampleSnapshot() install.Snapshot {
	steps := []install.StepState{
		{ID: install.StepMachineCheck, Name: "Kiểm tra máy", Weight: 3, Status: install.StatusOK, Percent: 100, Detail: "8 GB RAM · 112 GB trống", Elapsed: 2 * time.Second},
		{ID: install.StepRuntime, Name: "Chuẩn bị container runtime", Weight: 17, Status: install.StatusOK, Percent: 100, Detail: "Docker Engine 27.1 (sẵn có)", Elapsed: 1 * time.Second},
		{ID: install.StepPullImages, Name: "Tải image", Weight: 50, Status: install.StatusRunning, Percent: 60,
			Detail:   "412 / 690 MB · 18.4 MB/s",
			SubLines: []string{"api      86%", "bridge   54%", "db       xong"},
		},
		{ID: install.StepSecrets, Name: "Sinh bí mật & cấu hình", Weight: 3, Status: install.StatusPending},
		{ID: install.StepStartData, Name: "Khởi động dữ liệu", Weight: 8, Status: install.StatusPending},
		{ID: install.StepMigrate, Name: "Tạo cấu trúc dữ liệu", Weight: 9, Status: install.StatusPending},
		{ID: install.StepStartServices, Name: "Khởi động dịch vụ", Weight: 6, Status: install.StatusPending},
		{ID: install.StepFinalize, Name: "Hoàn tất", Weight: 4, Status: install.StatusPending},
	}
	return install.Snapshot{Steps: steps, OverallPct: 50, CurrentIndex: 2}
}

func TestView_FrameIs76ColumnsWide(t *testing.T) {
	m := NewModel(noColorStyles(), "v2.2.0", "~/.gen-harness")
	m.Snapshot = sampleSnapshot()

	out := m.View()
	for i, line := range strings.Split(out, "\n") {
		w := utf8.RuneCountInString(line)
		if w != FrameWidth {
			t.Errorf("dòng %d rộng %d cột, muốn %d: %q", i, w, FrameWidth, line)
		}
	}
}

func TestView_ContainsHeaderAndStepNames(t *testing.T) {
	m := NewModel(noColorStyles(), "v2.2.0", "~/.gen-harness")
	m.Snapshot = sampleSnapshot()
	out := m.View()

	for _, want := range []string{
		"GEN-HARNESS",
		"v2.2.0",
		"Đang cài đặt vào ~/.gen-harness",
		"Kiểm tra máy",
		"Chuẩn bị container runtime",
		"Tải image",
		"Sinh bí mật & cấu hình",
		"Hoàn tất",
		"l  xem log     q  huỷ an toàn",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("View() thiếu %q\n---\n%s", want, out)
		}
	}
}

func TestView_StatusIcons(t *testing.T) {
	m := NewModel(noColorStyles(), "v2.2.0", "~/.gen-harness")
	m.Snapshot = sampleSnapshot()
	out := m.View()

	// Hai bước OK phải có ✓, các bước Pending phải có ·.
	if strings.Count(out, "✓") < 2 {
		t.Errorf("phải có ít nhất 2 dấu ✓ (2 bước OK), out:\n%s", out)
	}
	if strings.Count(out, "·") < 5 {
		t.Errorf("phải có ít nhất 5 dấu · (5 bước pending), out:\n%s", out)
	}
}

func TestView_RunningStepShowsSubLinesMaxFour(t *testing.T) {
	snap := sampleSnapshot()
	snap.Steps[2].SubLines = []string{"a", "b", "c", "d", "e"} // 5 dòng, phải cắt còn 4
	m := NewModel(noColorStyles(), "v2.2.0", "~/.gen-harness")
	m.Snapshot = snap

	out := m.View()
	if !strings.Contains(out, "a") || !strings.Contains(out, "d") {
		t.Error("phải hiện tới dòng con thứ 4")
	}
	// dòng con thứ 5 ("e") không được xuất hiện dưới dạng một dòng con riêng
	lines := strings.Split(out, "\n")
	count := 0
	for _, l := range lines {
		trimmed := strings.TrimSpace(strings.Trim(l, "│"))
		if trimmed == "e" {
			count++
		}
	}
	if count != 0 {
		t.Error("dòng con thứ 5 không được hiển thị (giới hạn tối đa 4)")
	}
}

func TestView_PendingStepHasNoElapsedTime(t *testing.T) {
	m := NewModel(noColorStyles(), "v2.2.0", "~/.gen-harness")
	m.Snapshot = sampleSnapshot()
	out := m.View()

	for _, line := range strings.Split(out, "\n") {
		if strings.Contains(line, "Sinh bí mật & cấu hình") {
			if strings.Contains(line, "0:00") {
				t.Errorf("bước pending không được hiện thời lượng: %q", line)
			}
		}
	}
}

func TestView_QuittingRendersEmpty(t *testing.T) {
	m := NewModel(noColorStyles(), "v2.2.0", "~/.gen-harness")
	m.Quitting = true
	if out := m.View(); out != "" {
		t.Errorf("View() khi Quitting phải rỗng, được %q", out)
	}
}

func TestView_FinishScreen(t *testing.T) {
	m := NewModel(noColorStyles(), "v2.2.0", "~/.gen-harness")
	m.Finished = &FinishInfo{
		Duration:      4*time.Minute + 12*time.Second,
		SetupURL:      "https://localhost:8443/setup",
		SetupCode:     "K7QF-2MXD-9PLA",
		CodeExpiresIn: 24 * time.Hour,
		BrowserOpened: true,
	}
	out := m.View()

	for _, want := range []string{
		"Gen-Harness đã sẵn sàng",
		"4 phút 12s",
		"https://localhost:8443/setup",
		"K7QF-2MXD-9PLA",
		"genh status",
		"genh update",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("màn hoàn tất thiếu %q\n---\n%s", want, out)
		}
	}
	for i, line := range strings.Split(out, "\n") {
		w := utf8.RuneCountInString(line)
		if w != FrameWidth {
			t.Errorf("dòng %d màn hoàn tất rộng %d cột, muốn %d: %q", i, w, FrameWidth, line)
		}
	}
}

func TestUpdate_KeyQ_SetsQuitting(t *testing.T) {
	m := NewModel(noColorStyles(), "v2.2.0", "~/.gen-harness")
	next, cmd := m.Update(keyMsg('q'))
	nm := next.(Model)
	if !nm.Quitting {
		t.Error("nhấn q phải đặt Quitting=true")
	}
	if cmd == nil {
		t.Error("nhấn q phải trả về tea.Quit (cmd khác nil)")
	}
}

func TestUpdate_KeyL_TogglesLogVisible(t *testing.T) {
	m := NewModel(noColorStyles(), "v2.2.0", "~/.gen-harness")
	next, _ := m.Update(keyMsg('l'))
	nm := next.(Model)
	if !nm.LogVisible {
		t.Error("nhấn l lần đầu phải bật LogVisible")
	}
	next2, _ := nm.Update(keyMsg('l'))
	nm2 := next2.(Model)
	if nm2.LogVisible {
		t.Error("nhấn l lần hai phải tắt LogVisible")
	}
}

func TestUpdate_KeyQ_SetsCancelled(t *testing.T) {
	m := NewModel(noColorStyles(), "v2.2.0", "~/.gen-harness")
	next, _ := m.Update(keyMsg('q'))
	nm := next.(Model)
	if !nm.Cancelled {
		t.Error("nhấn q phải đặt Cancelled=true (để huỷ ctx Runner)")
	}
}

func TestUpdate_DoneMsg_QuitsWithoutCancelled(t *testing.T) {
	m := NewModel(noColorStyles(), "v2.2.0", "~/.gen-harness")
	next, cmd := m.Update(DoneMsg{Err: nil})
	nm := next.(Model)
	if !nm.Quitting {
		t.Error("DoneMsg phải đặt Quitting=true")
	}
	if nm.Cancelled {
		t.Error("DoneMsg (Runner tự xong) không phải là Owner huỷ, Cancelled phải là false")
	}
	if cmd == nil {
		t.Error("DoneMsg phải trả về tea.Quit (cmd khác nil)")
	}
}

func TestUpdate_SnapshotMsg_UpdatesSnapshot(t *testing.T) {
	m := NewModel(noColorStyles(), "v2.2.0", "~/.gen-harness")
	snap := sampleSnapshot()
	next, _ := m.Update(SnapshotMsg(snap))
	nm := next.(Model)
	if nm.Snapshot.OverallPct != snap.OverallPct {
		t.Errorf("Snapshot chưa được cập nhật: %+v", nm.Snapshot)
	}
}

func TestTruncateAndPad(t *testing.T) {
	if got := truncate("abcdef", 4); got != "abc…" {
		t.Errorf("truncate = %q, muốn %q", got, "abc…")
	}
	if got := truncate("ab", 10); got != "ab" {
		t.Errorf("truncate không cắt khi ngắn hơn max: %q", got)
	}
	if got := padRight("ab", 5); got != "ab   " {
		t.Errorf("padRight = %q", got)
	}
	if got := padLeft("ab", 5); got != "   ab" {
		t.Errorf("padLeft = %q", got)
	}
}
