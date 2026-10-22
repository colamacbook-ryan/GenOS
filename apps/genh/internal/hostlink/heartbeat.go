package hostlink

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"
)

// HeartbeatFile là nhịp sống của tiến trình genh đang giữ khoá loại trừ
// (update/restore/import) — Console (container api) KHÔNG thấy PID máy chủ nên
// suy "tiến trình còn sống" từ tệp này + boot_id: nhịp cũ hơn vài phút, khác
// pid với update-status.json hoặc khác boot_id ⇒ lần chạy đã chết (máy tắt,
// bị kill) và trạng thái "running" là treo.
const HeartbeatFile = "genh-heartbeat.json"

// heartbeatEvery là nhịp ghi lại genh-heartbeat.json (biến để test đặt ngắn).
var heartbeatEvery = 30 * time.Second

// Heartbeat là nội dung run/genh-heartbeat.json (hợp đồng với apps/api — giữ
// đúng tên khoá).
type Heartbeat struct {
	PID       int    `json:"pid"`
	Op        string `json:"op"` // update | restore | import
	BootID    string `json:"boot_id"`
	StartedAt string `json:"started_at"`
	At        string `json:"at"`
}

// HeartbeatPath là đường dẫn tệp nhịp sống trong hộp thư.
func HeartbeatPath(installDir string) string { return filepath.Join(Dir(installDir), HeartbeatFile) }

// bootIDPath là nơi Linux công bố mã lần khởi động hiện tại (đổi mỗi lần bật máy).
const bootIDPath = "/proc/sys/kernel/random/boot_id"

// BootID trả mã lần khởi động hiện tại của máy (Linux); hệ điều hành khác hoặc
// không đọc được → "".
func BootID() string {
	if runtime.GOOS != "linux" {
		return ""
	}
	b, err := os.ReadFile(bootIDPath)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(b))
}

// StartHeartbeat ghi genh-heartbeat.json NGAY rồi mỗi heartbeatEvery cho tới
// khi stop() được gọi; stop() dừng nhịp, chờ lần ghi đang dở xong rồi XOÁ tệp
// (gọi nhiều lần vô hại). Chỉ tiến trình giữ khoá loại trừ gọi hàm này (tệp
// riêng — không tranh ghi với Start/Finish của update-status.json do tiến
// trình con ghi). Lỗi ghi bỏ qua: nhịp sống chỉ là tín hiệu cho Console, không
// bao giờ làm hỏng lần cập nhật/khôi phục.
func StartHeartbeat(installDir, op string) (stop func()) {
	_ = EnsureDir(installDir)
	hb := Heartbeat{PID: os.Getpid(), Op: op, BootID: BootID(), StartedAt: now()}
	path := HeartbeatPath(installDir)
	write := func() {
		hb.At = now()
		_ = writeJSON(path, hb)
	}
	write()

	done := make(chan struct{})
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		t := time.NewTicker(heartbeatEvery)
		defer t.Stop()
		for {
			select {
			case <-done:
				return
			case <-t.C:
				write()
			}
		}
	}()

	var once sync.Once
	return func() {
		once.Do(func() {
			close(done)
			wg.Wait()
			// Xoá cả khi đích là symlink ai đó cài vào run/ (Remove không đi theo).
			_ = os.Remove(path)
		})
	}
}

// ReadHeartbeat đọc genh-heartbeat.json AN TOÀN (readStateFile — run/ không
// tin cậy, chỉ dùng để HIỂN THỊ, ví dụ PID của tiến trình đang giữ khoá).
func ReadHeartbeat(installDir string) (Heartbeat, error) {
	var hb Heartbeat
	b, err := readStateFile(HeartbeatPath(installDir), false)
	if err != nil {
		return hb, err
	}
	return hb, json.Unmarshal(b, &hb)
}
