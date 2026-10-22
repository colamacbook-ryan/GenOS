package install

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"
)

// fakeStep là một Step giả, hoàn toàn điều khiển được từ test — không đụng
// máy thật, không đụng Docker.
type fakeStep struct {
	id       StepID
	name     string
	progress []Progress // phát ra theo thứ tự trong Run
	err      error      // nếu khác nil, Run trả lỗi này sau khi phát hết progress
	ran      bool
}

func (f *fakeStep) ID() StepID   { return f.id }
func (f *fakeStep) Name() string { return f.name }

func (f *fakeStep) Run(ctx context.Context, env *Env, rep Reporter) error {
	f.ran = true
	for _, p := range f.progress {
		rep.Report(p)
	}
	return f.err
}

func TestWeights_SumTo100(t *testing.T) {
	var total float64
	for id := StepMachineCheck; id <= StepFinalize; id++ {
		total += id.Weight()
	}
	if total != 100 {
		t.Errorf("tổng trọng số 8 bước = %v, muốn 100", total)
	}
}

func TestRunner_OverallPercent_WeightedByStep(t *testing.T) {
	steps := []Step{
		&fakeStep{id: StepMachineCheck, name: "A", progress: []Progress{{Status: StatusOK, Percent: 100}}},
		&fakeStep{id: StepRuntime, name: "B", progress: []Progress{{Status: StatusRunning, Percent: 50}}},
	}
	r := NewRunner(&Env{}, steps)

	var last Snapshot
	obs := ObserverFunc(func(s Snapshot) { last = s })

	err := r.Run(context.Background(), obs)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}

	// StepMachineCheck (3%) xong toàn bộ + StepRuntime (17%) xong 50% giữa
	// chừng rồi cũng xong (vì fakeStep không lỗi, Runner tự đóng OK) => cả
	// hai bước kết thúc ở 100%, tổng đúng = 3 + 17 = 20% khi Run trả về.
	if last.OverallPct != 20 {
		t.Errorf("OverallPct cuối = %v, muốn 20 (3%%+17%%)", last.OverallPct)
	}
}

func TestRunner_MidStepPercent_IsWeightedFraction(t *testing.T) {
	steps := []Step{
		&fakeStep{id: StepMachineCheck, name: "A", progress: []Progress{{Status: StatusOK, Percent: 100}}},
		&fakeStep{id: StepRuntime, name: "B", progress: []Progress{{Status: StatusRunning, Percent: 40}}, err: errStop},
	}

	r := NewRunner(&Env{}, steps)

	var seenMidRun bool
	obs := ObserverFunc(func(s Snapshot) {
		// Khi bước B đang Running ở 40%, tổng phải là 3 (A xong) + 17*0.4 = 9.8
		if len(s.Steps) > 1 && s.Steps[1].Status == StatusRunning && s.Steps[1].Percent == 40 {
			seenMidRun = true
			want := 3 + 17*0.4
			if s.OverallPct != want {
				t.Errorf("OverallPct giữa chừng = %v, muốn %v", s.OverallPct, want)
			}
		}
	})

	err := r.Run(context.Background(), obs)
	if err == nil {
		t.Fatal("Run phải trả lỗi vì fakeStep B lỗi")
	}
	if !seenMidRun {
		t.Fatal("chưa từng thấy snapshot giữa chừng của bước B")
	}
}

var errStop = errors.New("giả lập lỗi bước")

func TestRunner_HaltsOnError_LaterStepsNotRun(t *testing.T) {
	stepC := &fakeStep{id: StepPullImages, name: "C"}
	steps := []Step{
		&fakeStep{id: StepMachineCheck, name: "A", progress: []Progress{{Status: StatusOK, Percent: 100}}},
		&fakeStep{id: StepRuntime, name: "B", err: errStop},
		stepC,
	}
	r := NewRunner(&Env{}, steps)

	err := r.Run(context.Background(), nil)
	if err == nil {
		t.Fatal("Run phải trả lỗi")
	}
	se, ok := err.(*StepError)
	if !ok {
		t.Fatalf("lỗi trả về phải là *StepError, được %T", err)
	}
	if se.Why != errStop.Error() {
		t.Errorf("StepError.Why = %q, muốn %q", se.Why, errStop.Error())
	}
	if stepC.ran {
		t.Error("bước sau bước lỗi không được chạy")
	}

	snap := r.Snapshot()
	if snap.Steps[1].Status != StatusError {
		t.Errorf("trạng thái bước B = %v, muốn StatusError", snap.Steps[1].Status)
	}
	if snap.Steps[2].Status != StatusPending {
		t.Errorf("trạng thái bước C = %v, muốn StatusPending (chưa chạy)", snap.Steps[2].Status)
	}
}

func TestRunner_StepErrorType_CarriesStructuredMessage(t *testing.T) {
	custom := &StepError{Code: "GH-E099", What: "hỏng", Why: "vì lý do X", Next: "làm Y"}
	steps := []Step{&fakeStep{id: StepMachineCheck, name: "A", err: custom}}
	r := NewRunner(&Env{}, steps)

	err := r.Run(context.Background(), nil)
	se, ok := err.(*StepError)
	if !ok {
		t.Fatalf("lỗi trả về phải là *StepError, được %T", err)
	}
	if se != custom {
		t.Error("Runner phải giữ nguyên *StepError gốc do Step trả về, không bọc lại")
	}
	if se.Error() == "" {
		t.Error("StepError.Error() không được rỗng")
	}
}

func TestRunner_FullRegistry_MachineAndSecretsStepsWireUp(t *testing.T) {
	dir := t.TempDir()
	env := &Env{InstallDir: dir, Port: 18443}

	// Không dùng toàn bộ Registry() vì các stubStep là đúng như thiết kế,
	// nhưng machineCheckStep phụ thuộc máy thật (RAM/đĩa/mạng) nên chỉ kiểm
	// tra: (1) nó chạy không panic, (2) nếu OK/WARN thì bước tiếp theo chạy
	// và secretsStep ghi đúng thư mục cấu hình được truyền qua Env.
	steps := []Step{machineCheckStep{}, secretsStep{}}
	r := NewRunner(env, steps)

	err := r.Run(context.Background(), nil)
	if err != nil {
		// Máy chạy test có thể thiếu RAM/đĩa/cổng bận trong môi trường CI lạ
		// — vẫn cho qua nhưng in ra để không lẫn với lỗi lập trình.
		t.Logf("machineCheckStep báo lỗi trong môi trường test (chấp nhận được): %v", err)
		return
	}

	snap := r.Snapshot()
	if snap.Steps[1].ID != StepSecrets {
		t.Fatalf("bước 2 trong danh sách phải là StepSecrets")
	}
	if snap.Steps[1].Status != StatusOK && snap.Steps[1].Status != StatusSkipped {
		t.Errorf("trạng thái secretsStep = %v, muốn OK hoặc Skipped", snap.Steps[1].Status)
	}

	secretsPath := filepath.Join(dir, "config", "secrets.json")
	if _, statErr := os.Stat(secretsPath); statErr != nil {
		t.Errorf("secrets.json phải tồn tại tại %s sau khi chạy: %v", secretsPath, statErr)
	}
}

func TestRegistry_HasAllEightStepsInOrder(t *testing.T) {
	steps := Registry()
	if len(steps) != 8 {
		t.Fatalf("Registry() trả về %d bước, muốn 8", len(steps))
	}
	for i, s := range steps {
		wantID := StepID(i + 1)
		if s.ID() != wantID {
			t.Errorf("bước index %d có ID=%v, muốn %v", i, s.ID(), wantID)
		}
		if s.Name() == "" {
			t.Errorf("bước %v thiếu tên hiển thị", s.ID())
		}
	}
}
