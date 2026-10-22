package install

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"sync"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/pull"
)

const testComposeYAML = `
services:
  proxy:
    image: caddy:2-alpine
  redis:
    image: redis:7-alpine
  db:
    build: { context: .. }
  api:
    build: { context: .. }
  web:
    build: { context: .. }
  bridge:
    build: { context: .. }
`

// fakePuller là pull.Puller giả — phát Event định sẵn theo image, không
// đụng Docker/mạng thật (đúng yêu cầu: Step 3 test được bằng Puller giả).
type fakePuller struct {
	mu       sync.Mutex
	events   map[string][]pull.Event // image -> chuỗi Event phát ra
	failWith map[string]error        // image -> lỗi Pull() trả về (nil = thành công)
	called   []string
}

func (p *fakePuller) Pull(ctx context.Context, image string, onEvent func(pull.Event)) error {
	p.mu.Lock()
	p.called = append(p.called, image)
	events := p.events[image]
	err := p.failWith[image]
	p.mu.Unlock()

	for _, ev := range events {
		onEvent(ev)
	}
	return err
}

func writeTestCompose(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "compose.yaml")
	if err := os.WriteFile(path, []byte(testComposeYAML), 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}
	return path
}

func TestPullStep_PullsOnlyServicesWithImage(t *testing.T) {
	composePath := writeTestCompose(t)

	fp := &fakePuller{events: map[string][]pull.Event{
		"caddy:2-alpine": {
			{Image: "caddy:2-alpine", LayerID: "L1", Status: "Downloading", Current: 10, Total: 10},
			{Image: "caddy:2-alpine", LayerID: "L1", Status: "Pull complete"},
		},
		"redis:7-alpine": {
			{Image: "redis:7-alpine", LayerID: "L1", Status: "Already exists"},
		},
	}}

	step := pullStep{puller: fp, locate: func(string) (string, error) { return composePath, nil }}

	var progresses []Progress
	rep := ReporterFunc(func(p Progress) { progresses = append(progresses, p) })

	if err := step.Run(context.Background(), &Env{}, rep); err != nil {
		t.Fatalf("Run: %v", err)
	}

	if len(fp.called) != 2 {
		t.Fatalf("số image được Pull = %d, muốn 2 (chỉ service có image:), gọi: %v", len(fp.called), fp.called)
	}

	last := progresses[len(progresses)-1]
	if last.Status != StatusOK {
		t.Errorf("trạng thái cuối = %v, muốn StatusOK", last.Status)
	}
	if last.Percent != 100 {
		t.Errorf("Percent cuối = %v, muốn 100", last.Percent)
	}
}

func TestPullStep_NoImagesAvailable_WarnsAndDoesNotFail(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "compose.yaml")
	if err := os.WriteFile(path, []byte("services:\n  api:\n    build: { context: .. }\n"), 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	fp := &fakePuller{}
	step := pullStep{puller: fp, locate: func(string) (string, error) { return path, nil }}

	var last Progress
	rep := ReporterFunc(func(p Progress) { last = p })

	if err := step.Run(context.Background(), &Env{}, rep); err != nil {
		t.Fatalf("Run không được lỗi khi không có image nào để tải: %v", err)
	}
	if last.Status != StatusWarn {
		t.Errorf("Status = %v, muốn StatusWarn", last.Status)
	}
	if len(fp.called) != 0 {
		t.Errorf("không được gọi Pull khi không có image nào: %v", fp.called)
	}
}

func TestPullStep_ComposeNotFound_ReturnsStructuredError(t *testing.T) {
	step := pullStep{locate: func(string) (string, error) { return "", errors.New("không thấy") }}

	var last Progress
	rep := ReporterFunc(func(p Progress) { last = p })

	err := step.Run(context.Background(), &Env{}, rep)
	se, ok := err.(*StepError)
	if !ok {
		t.Fatalf("lỗi phải là *StepError, được %T", err)
	}
	if se.Code != ErrCodeComposeNotFound {
		t.Errorf("Code = %q, muốn %q", se.Code, ErrCodeComposeNotFound)
	}
	if last.Status != StatusError {
		t.Errorf("Status cuối = %v, muốn StatusError", last.Status)
	}
}

func TestPullStep_OnePullFails_ReturnsStructuredErrorButOthersStillRan(t *testing.T) {
	composePath := writeTestCompose(t)

	fp := &fakePuller{
		events: map[string][]pull.Event{
			"redis:7-alpine": {{Image: "redis:7-alpine", LayerID: "L1", Status: "Already exists"}},
		},
		failWith: map[string]error{
			"caddy:2-alpine": errors.New("pull access denied"),
		},
	}

	step := pullStep{puller: fp, locate: func(string) (string, error) { return composePath, nil }}
	err := step.Run(context.Background(), &Env{}, ReporterFunc(func(Progress) {}))

	se, ok := err.(*StepError)
	if !ok {
		t.Fatalf("lỗi phải là *StepError, được %T", err)
	}
	if se.Code != ErrCodePullFailed {
		t.Errorf("Code = %q, muốn %q", se.Code, ErrCodePullFailed)
	}
	if len(fp.called) != 2 {
		t.Errorf("mọi image vẫn phải được thử tải song song dù một cái lỗi, gọi: %v", fp.called)
	}
}

func TestShortImageName(t *testing.T) {
	cases := map[string]string{
		"redis:7-alpine":                 "redis",
		"ghcr.io/org/api:v2.2.0":         "api",
		"minio/minio:latest":             "minio",
		"caddy:2-alpine@sha256:abcd1234": "caddy",
	}
	for in, want := range cases {
		if got := shortImageName(in); got != want {
			t.Errorf("shortImageName(%q) = %q, muốn %q", in, got, want)
		}
	}
}

// v0.1.63 (H3): agy-box nằm trong danh sách tải. Bản phát hành ghim agy-box CÙNG digest với
// api (chung api.Dockerfile) ⇒ một ảnh chỉ được tải MỘT lần (tải đôi làm % tiến độ sai).
func TestPullStep_AgyBoxCungAnhVoiApi_TaiMotLan(t *testing.T) {
	const apiImg = "ghcr.io/o/gen-harness-api@sha256:" +
		"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	dir := t.TempDir()
	path := filepath.Join(dir, "compose.yaml")
	yaml := "services:\n  api:\n    image: " + apiImg + "\n  agy-box:\n    image: " + apiImg + "\n  redis:\n    image: redis:7-alpine\n"
	if err := os.WriteFile(path, []byte(yaml), 0o644); err != nil {
		t.Fatal(err)
	}
	fp := &fakePuller{}
	step := pullStep{puller: fp, locate: func(string) (string, error) { return path, nil }}
	if err := step.Run(context.Background(), &Env{}, ReporterFunc(func(Progress) {})); err != nil {
		t.Fatalf("Run: %v", err)
	}
	n := 0
	for _, img := range fp.called {
		if img == apiImg {
			n++
		}
	}
	if n != 1 || len(fp.called) != 2 {
		t.Fatalf("ảnh api phải tải đúng 1 lần, tổng 2 ảnh; gọi: %v", fp.called)
	}
}

func TestPullStep_AgyBoxAnhRieng_DuocTai(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "compose.yaml")
	if err := os.WriteFile(path, []byte("services:\n  agy-box:\n    image: ghcr.io/o/box:1\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	fp := &fakePuller{}
	step := pullStep{puller: fp, locate: func(string) (string, error) { return path, nil }}
	if err := step.Run(context.Background(), &Env{}, ReporterFunc(func(Progress) {})); err != nil {
		t.Fatalf("Run: %v", err)
	}
	if len(fp.called) != 1 || fp.called[0] != "ghcr.io/o/box:1" {
		t.Fatalf("agy-box phải nằm trong pullServiceOrder; gọi: %v", fp.called)
	}
}

// Gói sửa review v0.1.63 (P2-2): proxy lối ra agy-egress (cùng ảnh api) cũng nằm trong danh sách tải.
func TestPullStep_AgyEgressAnhRieng_DuocTai(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "compose.yaml")
	if err := os.WriteFile(path, []byte("services:\n  agy-egress:\n    image: ghcr.io/o/egress:1\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	fp := &fakePuller{}
	step := pullStep{puller: fp, locate: func(string) (string, error) { return path, nil }}
	if err := step.Run(context.Background(), &Env{}, ReporterFunc(func(Progress) {})); err != nil {
		t.Fatalf("Run: %v", err)
	}
	if len(fp.called) != 1 || fp.called[0] != "ghcr.io/o/egress:1" {
		t.Fatalf("agy-egress phải nằm trong pullServiceOrder; gọi: %v", fp.called)
	}
}
