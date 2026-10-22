// Package machine chứa Bước 1 (Kiểm tra máy) của trình cài genh.
//
// Mỗi kiểm tra được tách thành một hàm thuần (pure function) nhận giá trị đã
// đo/dò sẵn và trả về kết quả — không tự đọc trạng thái máy bên trong. Việc
// đó thuộc các hàm "probe*" trong probe*.go, để phần logic quyết định
// (ngưỡng nào là OK/WARN/BAD) có thể unit-test độc lập bằng giá trị giả,
// không phụ thuộc máy đang chạy test.
package machine

import (
	"fmt"
	"time"
)

// Status là kết quả tổng quát của một kiểm tra, dùng chung với các bước
// khác trong internal/install (OK/WARN/BAD như token Nocturne).
type Status int

const (
	StatusOK Status = iota
	StatusWarn
	StatusFail
)

func (s Status) String() string {
	switch s {
	case StatusOK:
		return "ok"
	case StatusWarn:
		return "warn"
	case StatusFail:
		return "fail"
	default:
		return "unknown"
	}
}

// CheckResult là kết quả của một kiểm tra máy đơn lẻ.
type CheckResult struct {
	Name   string // tên hiển thị, tiếng Việt, ví dụ "RAM"
	Status Status
	Detail string // dòng mô tả ngắn, ví dụ "8 GB RAM (khuyến nghị 8 GB)"
}

const (
	minRAMBytes         uint64 = 4 * 1024 * 1024 * 1024
	recommendedRAMBytes uint64 = 8 * 1024 * 1024 * 1024
	// Đo thật (v0.1.10, amd64): 6 image ≈ 0,5 GB nén, ≈ 1,5 GB sau giải nén;
	// dữ liệu ban đầu vài chục MB. 5 GB đủ chỗ tải + giải nén + CSDL nhỏ;
	// dưới 10 GB chỉ cảnh báo vì backup/dữ liệu sẽ lớn dần.
	minDiskBytes         uint64 = 5 * 1024 * 1024 * 1024
	recommendedDiskBytes uint64 = 10 * 1024 * 1024 * 1024
	defaultPort                 = 8443
	maxClockDrift               = 5 * time.Minute
)

// MinRAMBytes/RecommendedRAMBytes/MinDiskBytes/DefaultPort xuất ra để các
// gói khác (install, tui) tham chiếu đúng ngưỡng khi hiển thị.
const (
	MinRAMBytes          = minRAMBytes
	RecommendedRAMBytes  = recommendedRAMBytes
	MinDiskBytes         = minDiskBytes
	RecommendedDiskBytes = recommendedDiskBytes
	DefaultPort          = defaultPort
	MaxClockDrift        = maxClockDrift
)

func gib(bytes uint64) float64 {
	return float64(bytes) / (1024 * 1024 * 1024)
}

// supportedPlatforms là các cặp GOOS/GOARCH mà genh phát hành binary theo
// docs/handoff/05-installer.md.
var supportedPlatforms = map[string]bool{
	"linux/amd64":   true,
	"linux/arm64":   true,
	"darwin/amd64":  true,
	"darwin/arm64":  true,
	"windows/amd64": true,
	"windows/arm64": true,
}

// CheckOS xác nhận hệ điều hành/kiến trúc hiện tại nằm trong danh sách nền
// tảng được hỗ trợ.
func CheckOS(goos, goarch string) CheckResult {
	key := goos + "/" + goarch
	if supportedPlatforms[key] {
		return CheckResult{
			Name:   "Hệ điều hành",
			Status: StatusOK,
			Detail: fmt.Sprintf("%s/%s", goos, goarch),
		}
	}
	return CheckResult{
		Name:   "Hệ điều hành",
		Status: StatusFail,
		Detail: fmt.Sprintf("%s/%s chưa được hỗ trợ", goos, goarch),
	}
}

// CheckRAM đánh giá tổng RAM vật lý: BAD nếu dưới 4 GB, WARN nếu dưới 8 GB
// (khuyến nghị), OK nếu từ 8 GB trở lên.
func CheckRAM(totalBytes uint64) CheckResult {
	detail := fmt.Sprintf("%.0f GB RAM", gib(totalBytes))
	switch {
	case totalBytes < minRAMBytes:
		return CheckResult{Name: "RAM", Status: StatusFail,
			Detail: detail + fmt.Sprintf(" (cần tối thiểu %.0f GB)", gib(minRAMBytes))}
	case totalBytes < recommendedRAMBytes:
		return CheckResult{Name: "RAM", Status: StatusWarn,
			Detail: detail + fmt.Sprintf(" (khuyến nghị %.0f GB)", gib(recommendedRAMBytes))}
	default:
		return CheckResult{Name: "RAM", Status: StatusOK, Detail: detail}
	}
}

// CheckDisk đánh giá dung lượng đĩa trống tại thư mục cài đặt: BAD nếu dưới
// 5 GB, WARN nếu dưới 10 GB (khuyến nghị), OK nếu từ 10 GB trở lên.
func CheckDisk(freeBytes uint64) CheckResult {
	detail := fmt.Sprintf("%.0f GB trống", gib(freeBytes))
	switch {
	case freeBytes < minDiskBytes:
		return CheckResult{Name: "Đĩa trống", Status: StatusFail,
			Detail: detail + fmt.Sprintf(" (cần tối thiểu %.0f GB)", gib(minDiskBytes))}
	case freeBytes < recommendedDiskBytes:
		return CheckResult{Name: "Đĩa trống", Status: StatusWarn,
			Detail: detail + fmt.Sprintf(" (khuyến nghị %.0f GB — dữ liệu và backup sẽ lớn dần)", gib(recommendedDiskBytes))}
	default:
		return CheckResult{Name: "Đĩa trống", Status: StatusOK, Detail: detail}
	}
}

// CheckPort đánh giá cổng cần cho proxy (mặc định 8443): BAD nếu đang bị
// tiến trình khác chiếm, kèm tên/PID nếu biết.
func CheckPort(port int, inUse bool, procName string, pid int) CheckResult {
	name := fmt.Sprintf("Cổng %d", port)
	if !inUse {
		return CheckResult{Name: name, Status: StatusOK, Detail: "rảnh"}
	}
	detail := "đang bị tiến trình khác dùng"
	if procName != "" || pid != 0 {
		detail = fmt.Sprintf("đang bị %s dùng (pid %d)", procName, pid)
	}
	return CheckResult{Name: name, Status: StatusFail, Detail: detail}
}

// CheckNetwork đánh giá kết nối mạng ra ngoài (cần để tải image, bản cập
// nhật…). reachable=false kèm lỗi thật sẽ báo BAD.
func CheckNetwork(reachable bool, err error) CheckResult {
	if reachable {
		return CheckResult{Name: "Kết nối mạng", Status: StatusOK, Detail: "sẵn sàng"}
	}
	detail := "không kết nối được"
	if err != nil {
		detail = fmt.Sprintf("không kết nối được (%s)", err.Error())
	}
	return CheckResult{Name: "Kết nối mạng", Status: StatusFail, Detail: detail}
}

// CheckClock đánh giá độ lệch đồng hồ hệ thống so với một mốc tham chiếu
// đáng tin cậy (ví dụ header Date từ máy chủ HTTPS). Lệch quá maxClockDrift
// (5 phút) sẽ khiến kiểm chứng TLS/chữ ký thất bại nên báo WARN.
func CheckClock(drift time.Duration) CheckResult {
	abs := drift
	if abs < 0 {
		abs = -abs
	}
	if abs > maxClockDrift {
		return CheckResult{Name: "Đồng hồ hệ thống", Status: StatusWarn,
			Detail: fmt.Sprintf("lệch %s so với mốc tham chiếu", abs.Round(time.Second))}
	}
	return CheckResult{Name: "Đồng hồ hệ thống", Status: StatusOK, Detail: "đúng giờ"}
}

// Overall gộp trạng thái xấu nhất trong danh sách kết quả — dùng để quyết
// định bước 1 kết thúc là OK/WARN/BAD.
func Overall(results []CheckResult) Status {
	worst := StatusOK
	for _, r := range results {
		if r.Status > worst {
			worst = r.Status
		}
	}
	return worst
}
