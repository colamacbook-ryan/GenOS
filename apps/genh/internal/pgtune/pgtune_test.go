package pgtune

import "testing"

const gb = 1024 * 1024 * 1024

func TestCompute_4GB(t *testing.T) {
	p := Compute(4 * gb)
	if p.SharedBuffers != "1024MB" {
		t.Errorf("SharedBuffers = %q, muốn 1024MB (25%% của 4GB)", p.SharedBuffers)
	}
	if p.EffectiveCacheSize != "2457MB" {
		t.Errorf("EffectiveCacheSize = %q, muốn 2457MB (60%% của 4GB)", p.EffectiveCacheSize)
	}
	if p.WorkMem != "10MB" {
		t.Errorf("WorkMem = %q, muốn 10MB", p.WorkMem)
	}
	if p.MaintenanceWorkMem != "204MB" {
		t.Errorf("MaintenanceWorkMem = %q, muốn 204MB (5%% của 4GB)", p.MaintenanceWorkMem)
	}
}

func TestCompute_16GB(t *testing.T) {
	p := Compute(16 * gb)
	if p.SharedBuffers != "4096MB" {
		t.Errorf("SharedBuffers = %q, muốn 4096MB (25%% của 16GB = 4096, đúng trần)", p.SharedBuffers)
	}
	if p.EffectiveCacheSize != "9830MB" {
		t.Errorf("EffectiveCacheSize = %q, muốn 9830MB", p.EffectiveCacheSize)
	}
	if p.WorkMem != "40MB" {
		t.Errorf("WorkMem = %q, muốn 40MB", p.WorkMem)
	}
	if p.MaintenanceWorkMem != "819MB" {
		t.Errorf("MaintenanceWorkMem = %q, muốn 819MB", p.MaintenanceWorkMem)
	}
}

// TestCompute_64GB_HitsCaps kiểm các trần: shared_buffers không bao giờ vượt
// 4096MB, work_mem không vượt 64MB, maintenance_work_mem không vượt 1024MB —
// dù RAM rất lớn.
func TestCompute_64GB_HitsCaps(t *testing.T) {
	p := Compute(64 * gb)
	if p.SharedBuffers != "4096MB" {
		t.Errorf("SharedBuffers = %q, muốn 4096MB (trần)", p.SharedBuffers)
	}
	if p.WorkMem != "64MB" {
		t.Errorf("WorkMem = %q, muốn 64MB (trần)", p.WorkMem)
	}
	if p.MaintenanceWorkMem != "1024MB" {
		t.Errorf("MaintenanceWorkMem = %q, muốn 1024MB (trần)", p.MaintenanceWorkMem)
	}
}

// TestCompute_ZeroRAM_NeverPanicsOrGoesNegative kiểm biên: RAM=0 (không dò
// được, gọi thẳng Compute không qua DetectAndCompute) vẫn phải ra giá trị
// hợp lệ >= 1MB cho cả 4 tham số, không panic/chia cho 0 theo cách sai.
func TestCompute_ZeroRAM_NeverPanicsOrGoesNegative(t *testing.T) {
	p := Compute(0)
	for name, v := range map[string]string{
		"SharedBuffers":      p.SharedBuffers,
		"EffectiveCacheSize": p.EffectiveCacheSize,
		"WorkMem":            p.WorkMem,
		"MaintenanceWorkMem": p.MaintenanceWorkMem,
	} {
		if v != "1MB" {
			t.Errorf("%s = %q, muốn sàn 1MB khi RAM=0", name, v)
		}
	}
}

func TestDetectAndCompute_NeverReturnsZeroValues(t *testing.T) {
	// DetectAndCompute dò RAM máy thật đang chạy test (hoặc rơi về sàn
	// machine.MinRAMBytes nếu không dò được) — chỉ kiểm không rỗng/hợp lệ,
	// không so khớp con số chính xác (phụ thuộc máy chạy CI).
	p := DetectAndCompute()
	if p.SharedBuffers == "" || p.EffectiveCacheSize == "" || p.WorkMem == "" || p.MaintenanceWorkMem == "" {
		t.Errorf("DetectAndCompute() trả tham số rỗng: %+v", p)
	}
}

func TestEnvPairs_FormatsAllFour(t *testing.T) {
	p := Params{SharedBuffers: "1GB", EffectiveCacheSize: "2GB", WorkMem: "8MB", MaintenanceWorkMem: "128MB"}
	got := p.EnvPairs()
	want := []string{
		"GH_PG_SHARED_BUFFERS=1GB",
		"GH_PG_EFFECTIVE_CACHE_SIZE=2GB",
		"GH_PG_WORK_MEM=8MB",
		"GH_PG_MAINTENANCE_WORK_MEM=128MB",
	}
	if len(got) != len(want) {
		t.Fatalf("EnvPairs() = %v, muốn %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("EnvPairs()[%d] = %q, muốn %q", i, got[i], want[i])
		}
	}
}
