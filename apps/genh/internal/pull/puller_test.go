package pull

import (
	"context"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
)

func TestParsePullLine_Downloading(t *testing.T) {
	line := `{"status":"Downloading","progressDetail":{"current":1234,"total":5678},"id":"a2abf6c4d29d"}`
	ev, ok := ParsePullLine("redis:7-alpine", line)
	if !ok {
		t.Fatal("ParsePullLine phải nhận diện được dòng JSON hợp lệ")
	}
	if ev.Status != "Downloading" || ev.Current != 1234 || ev.Total != 5678 || ev.LayerID != "a2abf6c4d29d" {
		t.Errorf("ev = %+v", ev)
	}
	if ev.Image != "redis:7-alpine" {
		t.Errorf("ev.Image = %q", ev.Image)
	}
}

func TestParsePullLine_Error(t *testing.T) {
	line := `{"error":"pull access denied for gh-not-a-real-image"}`
	ev, ok := ParsePullLine("x", line)
	if !ok {
		t.Fatal("dòng lỗi vẫn phải phân tích được")
	}
	if ev.ErrMsg == "" {
		t.Error("ErrMsg không được rỗng")
	}
}

func TestParsePullLine_NotJSON_Ignored(t *testing.T) {
	if _, ok := ParsePullLine("x", ""); ok {
		t.Error("dòng rỗng không phải JSON hợp lệ, phải ok=false")
	}
	if _, ok := ParsePullLine("x", "khong phai json"); ok {
		t.Error("dòng không phải JSON, phải ok=false")
	}
}

func TestCLIPuller_Pull_EmitsParsedEvents(t *testing.T) {
	fr := &fake.Runner{Responses: []fake.Response{
		{
			Match: fake.MatchArgsContain("pull", "redis:7-alpine"),
			Lines: []string{
				`{"status":"Pulling fs layer","id":"aaa"}`,
				`{"status":"Downloading","progressDetail":{"current":100,"total":1000},"id":"aaa"}`,
				`{"status":"Downloading","progressDetail":{"current":1000,"total":1000},"id":"aaa"}`,
				`{"status":"Pull complete","id":"aaa"}`,
				`{"status":"Downloaded newer image for redis:7-alpine"}`,
			},
		},
	}}

	p := CLIPuller{Runner: fr}
	var events []Event
	err := p.Pull(context.Background(), "redis:7-alpine", func(ev Event) { events = append(events, ev) })
	if err != nil {
		t.Fatalf("Pull: %v", err)
	}
	if len(events) != 5 {
		t.Fatalf("số event = %d, muốn 5", len(events))
	}
	if events[2].Current != 1000 || events[2].Total != 1000 {
		t.Errorf("event[2] = %+v", events[2])
	}
}
