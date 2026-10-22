package install

import (
	"context"
	"fmt"
	"sync"
	"time"
)

// StepState là trạng thái đầy đủ, hiện tại của một bước — cái mà
// TUI/chế độ dòng thật sự vẽ ra màn hình.
type StepState struct {
	ID       StepID
	Name     string
	Weight   float64
	Status   Status
	Percent  float64 // 0..100, nội bộ bước này
	Detail   string
	SubLines []string
	Elapsed  time.Duration
	Err      *StepError
}

// Snapshot là trạng thái của toàn bộ 8 bước tại một thời điểm, đã tính sẵn
// % tổng có trọng số — đúng những gì mockup TUI cần để vẽ thanh tiến độ và
// danh sách bước trong một lần.
type Snapshot struct {
	Steps        []StepState
	OverallPct   float64
	Elapsed      time.Duration
	CurrentIndex int // index trong Steps của bước đang StatusRunning, -1 nếu không có
}

// Observer nhận Snapshot mỗi khi trạng thái đổi. TUI và chế độ dòng đều cài
// interface này.
type Observer interface {
	Observe(Snapshot)
}

// ObserverFunc cho phép dùng một hàm thường làm Observer.
type ObserverFunc func(Snapshot)

func (f ObserverFunc) Observe(s Snapshot) { f(s) }

// noopObserver dùng khi Runner.Run được gọi mà không cần theo dõi (ví dụ
// trong test chỉ quan tâm giá trị trả về cuối).
type noopObserver struct{}

func (noopObserver) Observe(Snapshot) {}

// Runner chạy tuần tự các Step theo đúng thứ tự StepID, gộp Progress của
// từng bước thành Snapshot và tính % tổng có trọng số.
type Runner struct {
	steps []Step
	env   *Env

	mu     sync.Mutex
	states []StepState
	start  time.Time
}

// NewRunner tạo Runner cho danh sách steps (thường là Registry()) và Env
// dùng chung. Thứ tự của steps quyết định thứ tự chạy và thứ tự hiển thị.
func NewRunner(env *Env, steps []Step) *Runner {
	states := make([]StepState, len(steps))
	for i, s := range steps {
		states[i] = StepState{
			ID:     s.ID(),
			Name:   s.Name(),
			Weight: s.ID().Weight(),
			Status: StatusPending,
		}
	}
	return &Runner{steps: steps, env: env, states: states}
}

// Run thực hiện từng Step theo thứ tự, dừng lại ngay khi một Step trả lỗi
// (để Owner có thể sửa rồi chạy lại genh install — mọi Step phải idempotent
// nên phần đã xong không bị làm lại). obs có thể là nil.
func (r *Runner) Run(ctx context.Context, obs Observer) error {
	if obs == nil {
		obs = noopObserver{}
	}
	r.start = time.Now()

	for i, step := range r.steps {
		if err := ctx.Err(); err != nil {
			return err
		}

		r.setStatus(i, StatusRunning, "")
		obs.Observe(r.snapshot())

		stepStart := time.Now()
		id := step.ID()
		reporter := ReporterFunc(func(p Progress) {
			p.StepID = id
			p.Elapsed = time.Since(stepStart)
			r.applyProgress(i, p)
			obs.Observe(r.snapshot())
		})

		err := step.Run(ctx, r.env, reporter)

		r.mu.Lock()
		r.states[i].Elapsed = time.Since(stepStart)
		finalStatus := r.states[i].Status
		r.mu.Unlock()

		if err != nil {
			se := asStepError(err)
			r.mu.Lock()
			r.states[i].Status = StatusError
			r.states[i].Err = se
			r.mu.Unlock()
			obs.Observe(r.snapshot())
			return se
		}

		// Nếu Step không tự báo trạng thái cuối (StatusOK/Warn/Skipped) qua
		// Report, coi như xong OK để tránh bước "đứng hình" ở Running.
		if finalStatus == StatusRunning || finalStatus == StatusPending {
			r.setStatus(i, StatusOK, r.states[i].Detail)
		}
		r.mu.Lock()
		r.states[i].Percent = 100
		r.mu.Unlock()
		obs.Observe(r.snapshot())
	}

	return nil
}

func asStepError(err error) *StepError {
	if se, ok := err.(*StepError); ok {
		return se
	}
	return &StepError{What: "Bước cài đặt thất bại", Why: err.Error(), Err: err}
}

func (r *Runner) setStatus(i int, status Status, detail string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.states[i].Status = status
	if detail != "" {
		r.states[i].Detail = detail
	}
}

func (r *Runner) applyProgress(i int, p Progress) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.states[i].Status = p.Status
	r.states[i].Percent = p.Percent
	if p.Detail != "" {
		r.states[i].Detail = p.Detail
	}
	r.states[i].SubLines = p.SubLines
	r.states[i].Elapsed = p.Elapsed
	r.states[i].Err = p.Err
}

// snapshot tính % tổng có trọng số: bước đã xong/cảnh báo/bỏ qua tính đủ
// trọng số của nó, bước đang chạy tính theo tỉ lệ Percent nội bộ, bước
// chờ/lỗi tính 0 — đúng ý "chạy lại tiếp tục từ bước dở" của tài liệu.
func (r *Runner) snapshot() Snapshot {
	r.mu.Lock()
	defer r.mu.Unlock()

	steps := make([]StepState, len(r.states))
	copy(steps, r.states)

	var overall float64
	currentIndex := -1
	for i, s := range steps {
		switch s.Status {
		case StatusOK, StatusWarn, StatusSkipped:
			overall += s.Weight
		case StatusRunning:
			currentIndex = i
			pct := s.Percent
			if pct < 0 {
				pct = 0
			}
			if pct > 100 {
				pct = 100
			}
			overall += s.Weight * pct / 100
		}
	}

	return Snapshot{
		Steps:        steps,
		OverallPct:   overall,
		Elapsed:      time.Since(r.start),
		CurrentIndex: currentIndex,
	}
}

// Snapshot trả về trạng thái hiện tại — dùng khi cần đọc trạng thái ngoài
// vòng lặp Observe (ví dụ `genh install` bị ngắt rồi in trạng thái dở dang).
func (r *Runner) Snapshot() Snapshot { return r.snapshot() }

// String tiện cho log/debug: "[ 58%] Tải image".
func (s Snapshot) String() string {
	name := "—"
	if s.CurrentIndex >= 0 && s.CurrentIndex < len(s.Steps) {
		name = s.Steps[s.CurrentIndex].Name
	}
	return fmt.Sprintf("[%3.0f%%] %s", s.OverallPct, name)
}
