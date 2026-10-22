package machine

import (
	"context"
	"fmt"
	"net"
	"net/http"
	"runtime"
	"time"
)

// probeRAM và probeDiskFree được cài theo GOOS trong probe_ram_*.go /
// probe_disk_*.go (build tag), vì cách đọc RAM/đĩa trống thật sự khác nhau
// giữa Linux, macOS và Windows.

// DetectRAM xuất ra probeRAM cho các gói khác (internal/ops, internal/install)
// dùng lại đúng phép dò RAM thật của Bước 1 khi tính tham số tinh chỉnh
// Postgres (xem internal/pgtune) — tách khỏi RunAll vì các gói đó không cần
// (và không nên chạy lại) toàn bộ 6 kiểm tra máy, chỉ cần mỗi con số RAM.
func DetectRAM() (uint64, error) {
	return probeRAM()
}

// ProbeDiskFree trả dung lượng trống (byte) của ổ chứa path — `genh update`
// kiểm trước khi tải bản mới (so với MinDiskBytes, xem internal/ops/diskspace.go).
func ProbeDiskFree(path string) (uint64, error) { return probeDiskFree(path) }

// probePort dò cổng TCP có đang rảnh hay không bằng cách thử lắng nghe.
// Không xác định được tên/PID tiến trình đang chiếm cổng bằng thư viện
// chuẩn một cách cross-platform, nên chỉ báo "đang bị dùng" chung chung;
// đây là hành vi tối thiểu hợp lệ theo tài liệu (chi tiết pid/tên là "nếu
// biết").
func probePort(port int) (inUse bool, procName string, pid int) {
	ln, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", port))
	if err != nil {
		return true, "", 0
	}
	_ = ln.Close()
	return false, "", 0
}

// probeNetwork thử một kết nối TCP ngắn ra ngoài để xác nhận có mạng.
func probeNetwork(ctx context.Context) (bool, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodHead, "https://github.com", nil)
	if err != nil {
		return false, err
	}
	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return false, err
	}
	defer resp.Body.Close()
	return true, nil
}

// probeClockDrift lấy header Date từ một máy chủ HTTPS đáng tin cậy và so
// với đồng hồ cục bộ để ước lượng độ lệch. Nếu không lấy được (mất mạng),
// trả về drift=0 và không coi là lỗi — bước Kiểm tra mạng đã báo riêng.
func probeClockDrift(ctx context.Context) (time.Duration, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodHead, "https://github.com", nil)
	if err != nil {
		return 0, err
	}
	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	dateHeader := resp.Header.Get("Date")
	if dateHeader == "" {
		return 0, fmt.Errorf("máy chủ không trả header Date")
	}
	remote, err := http.ParseTime(dateHeader)
	if err != nil {
		return 0, err
	}
	return time.Since(remote), nil
}

// RunAll thực hiện toàn bộ kiểm tra máy (Bước 1) bằng dữ liệu dò thật của
// máy đang chạy, trả về kết quả theo đúng thứ tự hiển thị trong TUI.
func RunAll(ctx context.Context, port int) []CheckResult {
	if port <= 0 {
		port = DefaultPort
	}

	results := make([]CheckResult, 0, 6)
	results = append(results, CheckOS(runtime.GOOS, runtime.GOARCH))

	if ram, err := probeRAM(); err == nil {
		results = append(results, CheckRAM(ram))
	} else {
		results = append(results, CheckResult{Name: "RAM", Status: StatusWarn,
			Detail: "không dò được RAM (" + err.Error() + ")"})
	}

	if free, err := probeDiskFree("."); err == nil {
		results = append(results, CheckDisk(free))
	} else {
		results = append(results, CheckResult{Name: "Đĩa trống", Status: StatusWarn,
			Detail: "không dò được dung lượng đĩa (" + err.Error() + ")"})
	}

	inUse, proc, pid := probePort(port)
	results = append(results, CheckPort(port, inUse, proc, pid))

	reachable, netErr := probeNetwork(ctx)
	results = append(results, CheckNetwork(reachable, netErr))

	drift, err := probeClockDrift(ctx)
	if err != nil {
		// Không lấy được mốc tham chiếu (thường vì mất mạng) — không chặn
		// cài đặt vì bước mạng đã báo riêng; coi như đồng hồ OK.
		results = append(results, CheckResult{Name: "Đồng hồ hệ thống", Status: StatusOK, Detail: "chưa kiểm được (không có mốc tham chiếu)"})
	} else {
		results = append(results, CheckClock(drift))
	}

	return results
}
