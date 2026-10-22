package tui

import (
	"bytes"
	"strings"
	"testing"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/install"
)

// Các test trong tệp này mô phỏng đúng chế độ "không có TTY (CI, pipe)" mà
// docs/handoff/05-installer.md yêu cầu: mỗi bước một dòng
// "[ 58%] Tải image 412/690 MB", không cần TTY thật — chỉ cần gọi các hàm
// thuần/ghi vào bytes.Buffer.

func TestLine_Format_MatchesDocExample(t *testing.T) {
	snap := install.Snapshot{
		OverallPct:   58,
		CurrentIndex: 2,
		Steps: []install.StepState{
			{Name: "Kiểm tra máy", Status: install.StatusOK},
			{Name: "Chuẩn bị container runtime", Status: install.StatusOK},
			{Name: "Tải image", Status: install.StatusRunning, Detail: "412/690 MB"},
		},
	}

	got := Line(snap)
	want := "[ 58%] Tải image 412/690 MB"
	if got != want {
		t.Errorf("Line() = %q, muốn %q", got, want)
	}
}

func TestLine_NoCurrentStep_UsesLastNonPending(t *testing.T) {
	snap := install.Snapshot{
		OverallPct:   100,
		CurrentIndex: -1,
		Steps: []install.StepState{
			{Name: "Kiểm tra máy", Status: install.StatusOK},
			{Name: "Hoàn tất", Status: install.StatusOK},
		},
	}
	got := Line(snap)
	if !strings.HasPrefix(got, "[100%] Hoàn tất") {
		t.Errorf("Line() = %q, muốn bắt đầu bằng \"[100%%] Hoàn tất\"", got)
	}
}

func TestLine_ClampsPercent(t *testing.T) {
	over := Line(install.Snapshot{OverallPct: 142, CurrentIndex: -1})
	if !strings.HasPrefix(over, "[100%]") {
		t.Errorf("Line() với %%>100 = %q, muốn kẹp về 100%%", over)
	}
	under := Line(install.Snapshot{OverallPct: -5, CurrentIndex: -1})
	if !strings.HasPrefix(under, "[  0%]") {
		t.Errorf("Line() với %% âm = %q, muốn kẹp về 0%%", under)
	}
}

func TestLineRenderer_Observe_WritesOneLinePerChange(t *testing.T) {
	var buf bytes.Buffer
	r := NewLineRenderer(&buf)

	r.Observe(install.Snapshot{OverallPct: 10, CurrentIndex: 0, Steps: []install.StepState{{Name: "Kiểm tra máy", Status: install.StatusRunning}}})
	r.Observe(install.Snapshot{OverallPct: 20, CurrentIndex: 0, Steps: []install.StepState{{Name: "Kiểm tra máy", Status: install.StatusRunning}}})

	lines := strings.Split(strings.TrimRight(buf.String(), "\n"), "\n")
	if len(lines) != 2 {
		t.Fatalf("phải ghi 2 dòng, được %d: %q", len(lines), buf.String())
	}
	if !strings.HasPrefix(lines[0], "[ 10%]") || !strings.HasPrefix(lines[1], "[ 20%]") {
		t.Errorf("nội dung dòng không đúng: %q", lines)
	}
}

func TestLineRenderer_Observe_SkipsDuplicateLine(t *testing.T) {
	var buf bytes.Buffer
	r := NewLineRenderer(&buf)

	snap := install.Snapshot{OverallPct: 50, CurrentIndex: 0, Steps: []install.StepState{{Name: "Tải image", Status: install.StatusRunning}}}
	r.Observe(snap)
	r.Observe(snap) // y hệt lần trước — không ghi thêm dòng

	lines := strings.Split(strings.TrimRight(buf.String(), "\n"), "\n")
	if len(lines) != 1 {
		t.Errorf("dòng trùng lặp không được ghi lại, được %d dòng: %q", len(lines), buf.String())
	}
}

func TestLineRenderer_Finish(t *testing.T) {
	var buf bytes.Buffer
	r := NewLineRenderer(&buf)
	r.Finish(FinishInfo{
		Duration:  4*time.Minute + 12*time.Second,
		SetupURL:  "https://localhost:8443/setup",
		SetupCode: "K7QF-2MXD-9PLA",
	})

	out := buf.String()
	for _, want := range []string{"[100%]", "4 phút 12s", "https://localhost:8443/setup", "K7QF-2MXD-9PLA"} {
		if !strings.Contains(out, want) {
			t.Errorf("Finish() thiếu %q trong:\n%s", want, out)
		}
	}
}

func TestLineRenderer_FinishError(t *testing.T) {
	var buf bytes.Buffer
	r := NewLineRenderer(&buf)
	r.FinishError(&install.StepError{
		Code: "GH-E021",
		What: "Cổng 8443 đang bị tiến trình khác dùng",
		Why:  "pid 4412, nginx",
		Next: "genh install --port 9443",
	})

	out := buf.String()
	for _, want := range []string{"GH-E021", "Cổng 8443 đang bị tiến trình khác dùng", "pid 4412, nginx", "genh install --port 9443"} {
		if !strings.Contains(out, want) {
			t.Errorf("FinishError() thiếu %q trong:\n%s", want, out)
		}
	}
}
