package pull

import (
	"sync"
	"testing"
)

func TestAggregator_PercentByRealBytes(t *testing.T) {
	a := NewAggregator([]string{"api", "web"})

	// api: 1 layer, 50/100 byte. web: 1 layer, 0/900 byte (chưa tải gì).
	a.Apply(Event{Image: "api", LayerID: "L1", Status: "Downloading", Current: 50, Total: 100})
	a.Apply(Event{Image: "web", LayerID: "L2", Status: "Downloading", Current: 0, Total: 900})

	// Tổng: 50/1000 = 5% — theo byte thật, KHÔNG chia đều 2 image (mà chia
	// đều sẽ ra (50/100 + 0/900)/2 = 25%, khác hẳn).
	if got := a.Percent(); got < 4.9 || got > 5.1 {
		t.Errorf("Percent() = %v, muốn ~5 (theo byte thật)", got)
	}

	if got := a.ImagePercent("api"); got < 49.9 || got > 50.1 {
		t.Errorf("ImagePercent(api) = %v, muốn ~50", got)
	}
	if got := a.ImagePercent("web"); got != 0 {
		t.Errorf("ImagePercent(web) = %v, muốn 0", got)
	}
}

func TestAggregator_AlreadyExists_CountsAsDoneNotZero(t *testing.T) {
	a := NewAggregator([]string{"redis"})
	a.Apply(Event{Image: "redis", LayerID: "L1", Status: "Already exists"})
	a.Apply(Event{Image: "redis", LayerID: "L2", Status: "Already exists"})

	if got := a.ImagePercent("redis"); got != 100 {
		t.Errorf("ImagePercent(redis) = %v, muốn 100 (mọi layer đã có sẵn)", got)
	}
}

func TestAggregator_PullComplete_ClampsCurrentToTotal(t *testing.T) {
	a := NewAggregator([]string{"db"})
	a.Apply(Event{Image: "db", LayerID: "L1", Status: "Downloading", Current: 40, Total: 100})
	a.Apply(Event{Image: "db", LayerID: "L1", Status: "Pull complete"})

	if got := a.ImagePercent("db"); got != 100 {
		t.Errorf("ImagePercent(db) sau Pull complete = %v, muốn 100", got)
	}
}

func TestAggregator_Errors(t *testing.T) {
	a := NewAggregator([]string{"bridge"})
	a.Apply(Event{Image: "bridge", ErrMsg: "pull access denied"})

	errs := a.Errors()
	if errs["bridge"] != "pull access denied" {
		t.Errorf("Errors() = %v", errs)
	}
}

func TestAggregator_Images_PreservesInputOrder(t *testing.T) {
	want := []string{"db", "redis", "objects", "proxy", "api", "web", "bridge"}
	a := NewAggregator(want)
	got := a.Images()
	if len(got) != len(want) {
		t.Fatalf("Images() len = %d, muốn %d", len(got), len(want))
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("Images()[%d] = %q, muốn %q", i, got[i], want[i])
		}
	}
}

func TestAggregator_ConcurrentApply_NoRace(t *testing.T) {
	images := []string{"a", "b", "c", "d"}
	a := NewAggregator(images)

	var wg sync.WaitGroup
	for _, img := range images {
		wg.Add(1)
		go func(img string) {
			defer wg.Done()
			for i := 0; i < 200; i++ {
				a.Apply(Event{Image: img, LayerID: "L", Status: "Downloading", Current: int64(i), Total: 200})
			}
		}(img)
	}
	wg.Wait()

	// Không cần khẳng định giá trị cuối chính xác (các goroutine ghi đè lẫn
	// nhau lên cùng LayerID "L" là dự định của test) — mục đích là chạy
	// `go test -race` sạch, không panic map ghi đồng thời.
	_ = a.Percent()
}
