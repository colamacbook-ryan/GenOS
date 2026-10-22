package compose

import (
	"context"
	"testing"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
)

const psArrayJSON = `[
  {"Service":"db","State":"running","Health":"healthy"},
  {"Service":"redis","State":"running","Health":"healthy"},
  {"Service":"objects","State":"running","Health":"starting"}
]`

const psNDJSON = `{"Service":"db","State":"running","Health":"healthy"}
{"Service":"redis","State":"running","Health":""}
`

func TestParsePS_ArrayForm(t *testing.T) {
	got, err := ParsePS([]byte(psArrayJSON))
	if err != nil {
		t.Fatalf("ParsePS: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("số container = %d, muốn 3", len(got))
	}
	if got[2].Service != "objects" || got[2].Health != "starting" {
		t.Errorf("phần tử 2 = %+v", got[2])
	}
}

func TestParsePS_NDJSONForm(t *testing.T) {
	got, err := ParsePS([]byte(psNDJSON))
	if err != nil {
		t.Fatalf("ParsePS: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("số container = %d, muốn 2", len(got))
	}
}

func TestParsePS_Empty(t *testing.T) {
	got, err := ParsePS([]byte(""))
	if err != nil || got != nil {
		t.Errorf("ParsePS(rỗng) = (%v, %v), muốn (nil, nil)", got, err)
	}
}

func TestAllHealthy(t *testing.T) {
	statuses := []ContainerStatus{
		{Service: "db", State: "running", Health: "healthy"},
		{Service: "redis", State: "running", Health: ""}, // không có healthcheck nhưng đang chạy
		{Service: "objects", State: "running", Health: "starting"},
	}

	ok, detail := AllHealthy(statuses, []string{"db", "redis", "objects"})
	if ok {
		t.Error("ok phải false vì objects vẫn 'starting'")
	}
	if detail["objects"] != "starting" {
		t.Errorf("detail[objects] = %q, muốn starting", detail["objects"])
	}
	if detail["redis"] == "" {
		t.Error("detail[redis] không được rỗng")
	}

	ok, _ = AllHealthy(statuses, []string{"db", "redis"})
	if !ok {
		t.Error("ok phải true khi mọi service cần đều healthy/running")
	}

	ok, detail = AllHealthy(statuses, []string{"db", "khong-ton-tai"})
	if ok {
		t.Error("ok phải false khi thiếu container")
	}
	if detail["khong-ton-tai"] != "chưa thấy container" {
		t.Errorf("detail = %v", detail)
	}
}

func TestWaitHealthy_SucceedsAfterFewPolls(t *testing.T) {
	origInterval := PollInterval
	PollInterval = time.Millisecond
	defer func() { PollInterval = origInterval }()

	fr := &fake.Runner{}
	fr.Responses = []fake.Response{
		{
			Match: fake.MatchArgsContain("ps", "--format", "json"),
			OutputSeq: [][]byte{
				[]byte(`[{"Service":"db","State":"running","Health":"starting"}]`),
				[]byte(`[{"Service":"db","State":"running","Health":"starting"}]`),
				[]byte(`[{"Service":"db","State":"running","Health":"healthy"}]`),
			},
		},
	}

	var ticks []map[string]string
	err := WaitHealthy(context.Background(), fr, "/tmp/compose.yaml", nil, []string{"db"}, 5*time.Second,
		func(detail map[string]string) { ticks = append(ticks, detail) })
	if err != nil {
		t.Fatalf("WaitHealthy: %v", err)
	}
	if len(ticks) != 3 {
		t.Fatalf("số lần onTick = %d, muốn 3 (2 lần starting + 1 lần healthy)", len(ticks))
	}
	if ticks[2]["db"] != "healthy" {
		t.Errorf("tick cuối = %v, muốn healthy", ticks[2])
	}
}

func TestWaitHealthy_TimesOut(t *testing.T) {
	origInterval := PollInterval
	PollInterval = time.Millisecond
	defer func() { PollInterval = origInterval }()

	fr := &fake.Runner{Responses: []fake.Response{
		{
			Match:  fake.MatchArgsContain("ps", "--format", "json"),
			Output: []byte(`[{"Service":"db","State":"running","Health":"starting"}]`),
		},
	}}

	err := WaitHealthy(context.Background(), fr, "/tmp/compose.yaml", nil, []string{"db"}, 20*time.Millisecond, nil)
	if err == nil {
		t.Fatal("muốn lỗi hết thời gian chờ")
	}
}

func TestWaitHealthy_RespectsContextCancel(t *testing.T) {
	origInterval := PollInterval
	PollInterval = time.Second // đủ dài để đảm bảo context Done() thắng trước, không phải deadline logic
	defer func() { PollInterval = origInterval }()

	fr := &fake.Runner{Responses: []fake.Response{
		{
			Match:  fake.MatchArgsContain("ps", "--format", "json"),
			Output: []byte(`[{"Service":"db","State":"running","Health":"starting"}]`),
		},
	}}

	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	err := WaitHealthy(ctx, fr, "/tmp/compose.yaml", nil, []string{"db"}, time.Minute, nil)
	if err == nil {
		t.Fatal("muốn lỗi khi context đã huỷ")
	}
}
