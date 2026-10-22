// Package pgtune tính 4 tham số tinh chỉnh Postgres (shared_buffers,
// effective_cache_size, work_mem, maintenance_work_mem) từ tổng RAM máy đo
// được ở Bước 1 (xem internal/machine.DetectRAM) — hàm thuần, không đụng
// đĩa/mạng, test bằng giá trị RAM giả (xem pgtune_test.go).
//
// genh ghi các giá trị này vào biến môi trường GH_PG_* trong overlay truyền
// cho MỌI lệnh `docker compose` (internal/ops.EnvOverlay, internal/install/
// steps_data.go secretsEnvOverlay) — deploy/compose.yaml đọc lại qua
// "${GH_PG_SHARED_BUFFERS:-256MB}"... trong command: của service db, có mặc
// định an toàn riêng nếu biến trống (ví dụ chạy `docker compose` tay, không
// qua genh).
package pgtune

import (
	"fmt"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/machine"
)

// Params là 4 tham số, định dạng sẵn dạng chuỗi Postgres hiểu được (ví dụ
// "512MB") — gán thẳng cho GH_PG_SHARED_BUFFERS/GH_PG_EFFECTIVE_CACHE_SIZE/
// GH_PG_WORK_MEM/GH_PG_MAINTENANCE_WORK_MEM.
type Params struct {
	SharedBuffers      string
	EffectiveCacheSize string
	WorkMem            string
	MaintenanceWorkMem string
}

const oneMB uint64 = 1024 * 1024

// assumedMaxConnections dùng để chia work_mem — đúng mặc định
// max_connections=100 của image Postgres chính thức (không đổi ở
// deploy/images/db.Dockerfile), work_mem KHÔNG nên ăn theo % RAM trực tiếp
// như shared_buffers/effective_cache_size vì nó nhân với số phép sắp
// xếp/hash đồng thời (mỗi kết nối có thể dùng nhiều work_mem cùng lúc).
const assumedMaxConnections = 100

// Compute tính Params từ tổng RAM máy (byte) theo các tỉ lệ khuyến nghị phổ
// biến của Postgres (PGTune, Postgres wiki "Tuning Your PostgreSQL Server"):
//   - shared_buffers      ≈ 25% RAM, trần 4096MB — vượt trần không giúp
//     thêm nhiều, Postgres đã dựa vào OS page cache qua effective_cache_size.
//   - effective_cache_size ≈ 60% RAM (giữa khoảng khuyến nghị 50–75%).
//   - work_mem            ≈ (25% RAM) / assumedMaxConnections, trần 64MB.
//   - maintenance_work_mem ≈ 5% RAM, trần 1024MB.
//
// RAM càng thấp (gần machine.MinRAMBytes = 4GB) các tham số càng nhỏ tương
// ứng — không đặt sàn riêng ngoài sàn tự nhiên 1MB của clampMB, vì
// compose.yaml đã có mặc định an toàn riêng khi GH_PG_* trống (xem
// deploy/compose.yaml, service db).
func Compute(totalRAMBytes uint64) Params {
	sharedBuffers := clampMB(totalRAMBytes/4, 1, 4096)
	effectiveCache := clampMB(totalRAMBytes*3/5, 1, 1<<20)
	workMem := clampMB(totalRAMBytes/4/assumedMaxConnections, 1, 64)
	maintenanceWorkMem := clampMB(totalRAMBytes/20, 1, 1024)

	return Params{
		SharedBuffers:      fmt.Sprintf("%dMB", sharedBuffers),
		EffectiveCacheSize: fmt.Sprintf("%dMB", effectiveCache),
		WorkMem:            fmt.Sprintf("%dMB", workMem),
		MaintenanceWorkMem: fmt.Sprintf("%dMB", maintenanceWorkMem),
	}
}

// clampMB đổi bytes ra MB (chia nguyên) rồi kẹp trong [minMB, maxMB].
func clampMB(bytes, minMB, maxMB uint64) uint64 {
	v := bytes / oneMB
	if v < minMB {
		v = minMB
	}
	if v > maxMB {
		v = maxMB
	}
	return v
}

// DetectAndCompute dò RAM máy thật qua machine.DetectRAM rồi Compute — dùng
// ở cả internal/ops.EnvOverlay và internal/install/steps_data.go
// secretsEnvOverlay để xây GH_PG_* mỗi khi dựng overlay môi trường cho
// `docker compose`. Không dò được RAM (hiếm — xem machine.DetectRAM) thì
// dùng sàn machine.MinRAMBytes: vẫn ra tham số hợp lệ, nhỏ hơn thực tế máy
// (an toàn hơn là tham số lớn hơn RAM thật).
func DetectAndCompute() Params {
	ram, err := machine.DetectRAM()
	if err != nil || ram == 0 {
		ram = machine.MinRAMBytes
	}
	return Compute(ram)
}

// EnvPairs dựng 4 biến môi trường GH_PG_* dạng "KEY=value" sẵn để nối vào
// []string Env của dockercli.Cmd — tiện dùng chung ở cả internal/ops và
// internal/install, tránh lặp lại 4 dòng fmt.Sprintf ở cả hai nơi gọi.
func (p Params) EnvPairs() []string {
	return []string{
		"GH_PG_SHARED_BUFFERS=" + p.SharedBuffers,
		"GH_PG_EFFECTIVE_CACHE_SIZE=" + p.EffectiveCacheSize,
		"GH_PG_WORK_MEM=" + p.WorkMem,
		"GH_PG_MAINTENANCE_WORK_MEM=" + p.MaintenanceWorkMem,
	}
}
